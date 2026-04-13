"""
Módulo de integración con servicios GCP para el MVP Speech Analytics.

Provee las siguientes funciones del pipeline de procesamiento de llamadas:
  - upload_audio_to_gcs: Sube audio a Cloud Storage
  - transcribe_audio: Transcribe con Speech-to-Text v2 y diarización
  - redact_pii: Enmascara PII con Cloud DLP
  - extract_insights: Extrae métricas de negocio con Vertex AI / Gemini

Todas las funciones incluyen manejo de errores con mensajes descriptivos.
"""

import json
import os
import re

from google.cloud import storage
from google.cloud import speech_v2
from google.cloud.speech_v2.types import cloud_speech
from google.cloud import dlp_v2
import vertexai
from vertexai.generative_models import GenerativeModel


# ---------------------------------------------------------------------------
# 1. Upload a Cloud Storage
# ---------------------------------------------------------------------------
def upload_audio_to_gcs(local_path: str, bucket_name: str) -> str:
    """
    Sube un archivo de audio local al bucket de Cloud Storage.

    Args:
        local_path: Ruta absoluta o relativa al archivo de audio local.
        bucket_name: Nombre del bucket GCS de destino.

    Returns:
        URI de GCS en formato gs://bucket_name/filename.

    Raises:
        FileNotFoundError: Si el archivo local no existe.
        RuntimeError: Si falla la subida al bucket.
    """
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Archivo de audio no encontrado: {local_path}")

    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob_name = os.path.basename(local_path)
        blob = bucket.blob(blob_name)

        blob.upload_from_filename(local_path)
        gcs_uri = f"gs://{bucket_name}/{blob_name}"
        return gcs_uri
    except Exception as e:
        raise RuntimeError(f"Error subiendo audio a GCS ({bucket_name}): {e}") from e


# ---------------------------------------------------------------------------
# 2. Transcripción con Speech-to-Text v2
# ---------------------------------------------------------------------------
def transcribe_audio(gcs_uri: str, project_id: str) -> dict:
    """
    Transcribe un archivo de audio desde GCS usando Cloud Speech-to-Text v2.

    Utiliza el modelo 'chirp' con fallback a 'latest_long'. Habilita diarización
    de hablante para identificar agente (Speaker 1) y cliente (Speaker 2).

    Args:
        gcs_uri: URI del archivo de audio en GCS (gs://bucket/file.wav).
        project_id: ID del proyecto GCP.

    Returns:
        Diccionario con:
            - segments (list): Lista de dicts {speaker: str, text: str}
              donde speaker es 'Agente' o 'Cliente'.
            - confidence_score (float): Promedio de confidence scores (0.0–1.0).

    Raises:
        RuntimeError: Si la transcripción falla o retorna resultado vacío.
    """
    try:
        client = speech_v2.SpeechClient()
        recognizer_name = f"projects/{project_id}/locations/global/recognizers/_"

        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=["es-CL"],
            model="chirp",
            features=cloud_speech.RecognitionFeatures(
                enable_word_time_offsets=True,
                diarization_config=cloud_speech.SpeakerDiarizationConfig(
                    min_speaker_count=2,
                    max_speaker_count=2,
                ),
            ),
        )

        request = cloud_speech.RecognizeRequest(
            recognizer=recognizer_name,
            config=config,
            uri=gcs_uri,
        )

        response = client.recognize(request=request)

        if not response.results:
            raise RuntimeError("Speech-to-Text retornó resultado vacío.")

        # Extraer palabras con info de speaker del último resultado (más completo)
        all_words = []
        confidence_scores = []

        for result in response.results:
            if result.alternatives:
                alt = result.alternatives[0]
                confidence_scores.append(alt.confidence if alt.confidence else 0.0)
                for word in alt.words:
                    all_words.append({
                        "word": word.word,
                        "speaker_tag": word.speaker_label if hasattr(word, "speaker_label") else str(word.speaker_tag),
                    })

        if not all_words:
            raise RuntimeError("No se encontraron palabras en la transcripción.")

        # Agrupar palabras consecutivas del mismo hablante en segmentos
        segments = _group_words_into_segments(all_words)

        # El primer hablante detectado se asume como Agente
        avg_confidence = (
            sum(confidence_scores) / len(confidence_scores)
            if confidence_scores else 0.0
        )

        return {
            "segments": segments,
            "confidence_score": round(avg_confidence, 4),
        }

    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Error en Speech-to-Text para {gcs_uri}: {e}") from e


def _group_words_into_segments(words: list[dict]) -> list[dict]:
    """
    Agrupa palabras consecutivas del mismo hablante en segmentos de texto.

    El primer speaker_tag detectado se mapea a 'Agente', el resto a 'Cliente'.

    Args:
        words: Lista de dicts con 'word' y 'speaker_tag'.

    Returns:
        Lista de dicts {speaker, text} con segmentos agrupados.
    """
    if not words:
        return []

    # Detectar el primer speaker_tag para asignarlo como Agente
    first_speaker = words[0].get("speaker_tag", "1")
    speaker_map = {}

    segments = []
    current_speaker_tag = None
    current_words = []

    for word_info in words:
        tag = word_info.get("speaker_tag", "1")

        if tag not in speaker_map:
            if not speaker_map:
                speaker_map[tag] = "Agente"
            else:
                speaker_map[tag] = "Cliente"

        if tag != current_speaker_tag:
            if current_words:
                segments.append({
                    "speaker": speaker_map.get(current_speaker_tag, "Cliente"),
                    "text": " ".join(current_words).strip(),
                })
            current_speaker_tag = tag
            current_words = [word_info["word"]]
        else:
            current_words.append(word_info["word"])

    # Último segmento pendiente
    if current_words:
        segments.append({
            "speaker": speaker_map.get(current_speaker_tag, "Cliente"),
            "text": " ".join(current_words).strip(),
        })

    return segments


# ---------------------------------------------------------------------------
# 3. Redacción de PII con Cloud DLP
# ---------------------------------------------------------------------------
def redact_pii(text: str, project_id: str) -> dict:
    """
    Detecta y enmascara datos personales sensibles (PII) en el texto usando Cloud DLP.

    Info types detectados: CHILE_RUT, CREDIT_CARD_NUMBER, EMAIL_ADDRESS, PHONE_NUMBER.
    Cada tipo es reemplazado por un token descriptivo en español.

    Args:
        text: Texto con posible PII (transcripción sin enmascarar).
        project_id: ID del proyecto GCP.

    Returns:
        Diccionario con:
            - redacted_text (str): Texto con PII reemplazada por tokens.
            - findings_count (int): Número total de hallazgos PII detectados.

    Raises:
        RuntimeError: Si la llamada a DLP falla.
    """
    # Mapeo de info type → token descriptivo en español
    REPLACEMENT_MAP = {
        "CHILE_RUT":          "[CHILE_RUT_CENSURADO]",
        "CREDIT_CARD_NUMBER": "[TARJETA_CENSURADA]",
        "EMAIL_ADDRESS":      "[EMAIL_CENSURADO]",
        "PHONE_NUMBER":       "[TELEFONO_CENSURADO]",
    }

    try:
        dlp_client = dlp_v2.DlpServiceClient()
        parent = f"projects/{project_id}/locations/global"

        info_types = [
            dlp_v2.InfoType(name=info_type)
            for info_type in REPLACEMENT_MAP.keys()
        ]

        inspect_config = dlp_v2.InspectConfig(info_types=info_types)

        # Construir transformaciones: una por cada info type
        transformations = [
            dlp_v2.PrimitiveTransformation(
                replace_with_info_type_config=dlp_v2.ReplaceWithInfoTypeConfig()
            )
        ]

        # Usamos deidentify para reemplazar con info type names
        deidentify_config = dlp_v2.DeidentifyConfig(
            info_type_transformations=dlp_v2.InfoTypeTransformations(
                transformations=[
                    dlp_v2.InfoTypeTransformations.InfoTypeTransformation(
                        primitive_transformation=dlp_v2.PrimitiveTransformation(
                            replace_with_info_type_config=dlp_v2.ReplaceWithInfoTypeConfig()
                        )
                    )
                ]
            )
        )

        item = dlp_v2.ContentItem(value=text)

        request = dlp_v2.DeidentifyContentRequest(
            parent=parent,
            deidentify_config=deidentify_config,
            inspect_config=inspect_config,
            item=item,
        )

        response = dlp_client.deidentify_content(request=request)
        redacted_text = response.item.value

        # Reemplazar los tokens genéricos de DLP por nuestros tokens en español
        for info_type, token in REPLACEMENT_MAP.items():
            redacted_text = redacted_text.replace(f"[{info_type}]", token)

        # Contar hallazgos totales inspeccionando el texto original
        inspect_request = dlp_v2.InspectContentRequest(
            parent=parent,
            inspect_config=inspect_config,
            item=dlp_v2.ContentItem(value=text),
        )
        inspect_response = dlp_client.inspect_content(request=inspect_request)
        findings_count = len(inspect_response.result.findings)

        return {
            "redacted_text": redacted_text,
            "findings_count": findings_count,
        }

    except Exception as e:
        raise RuntimeError(f"Error en Cloud DLP al redactar PII: {e}") from e


# ---------------------------------------------------------------------------
# 4. Extracción de insights con Vertex AI / Gemini
# ---------------------------------------------------------------------------
def extract_insights(
    transcript_redacted: str,
    project_id: str,
    model: str = "gemini-1.5-flash",
) -> dict:
    """
    Extrae métricas de negocio de una transcripción usando Vertex AI (Gemini).

    Solicita a Gemini un JSON estructurado con intención, sentimiento, riesgo de
    churn y resumen. Limpia automáticamente markdown fences (```json ... ```)
    antes de parsear la respuesta.

    Args:
        transcript_redacted: Transcripción con PII ya enmascarada.
        project_id: ID del proyecto GCP.
        model: Nombre del modelo Gemini a usar (default: gemini-1.5-flash).

    Returns:
        Diccionario con:
            - call_intent (str): Descripción de la intención de la llamada.
            - customer_sentiment (str): 'Positivo', 'Neutro' o 'Negativo'.
            - churn_risk (bool): True si hay riesgo de abandono detectado.
            - summary (str): Resumen de 2-3 oraciones de la llamada.

    Raises:
        RuntimeError: Si Gemini falla o retorna JSON inválido.
    """
    try:
        vertexai.init(project=project_id, location="us-central1")
        gemini = GenerativeModel(model)

        prompt = f"""Analiza la siguiente transcripción de una llamada bancaria y devuelve ÚNICAMENTE un objeto JSON válido, sin texto adicional y sin bloques de código markdown.

El JSON debe tener exactamente esta estructura:
{{
  "call_intent": "descripción concisa de la intención principal de la llamada",
  "customer_sentiment": "Positivo" | "Neutro" | "Negativo",
  "churn_risk": true | false,
  "summary": "resumen objetivo de 2 a 3 oraciones de lo que ocurrió en la llamada"
}}

Reglas:
- customer_sentiment SOLO puede ser: "Positivo", "Neutro" o "Negativo"
- churn_risk es true solo si el cliente muestra intención clara de abandonar el banco
- summary debe ser en español y en tercera persona
- NO incluyas bloques de código, backticks ni markdown

TRANSCRIPCIÓN:
{transcript_redacted}

JSON:"""

        response = gemini.generate_content(prompt)
        raw_text = response.text.strip()

        # Limpieza obligatoria: remover markdown fences si la respuesta los incluye
        raw_text = re.sub(r"```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"```\s*$", "", raw_text)
        raw_text = raw_text.strip()

        insights = json.loads(raw_text)

        # Validar que churn_risk sea bool
        if isinstance(insights.get("churn_risk"), str):
            insights["churn_risk"] = insights["churn_risk"].lower() == "true"

        return insights

    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Gemini retornó JSON inválido: {e}. Respuesta: {raw_text[:300]}"
        ) from e
    except Exception as e:
        raise RuntimeError(f"Error en Vertex AI / Gemini ({model}): {e}") from e
