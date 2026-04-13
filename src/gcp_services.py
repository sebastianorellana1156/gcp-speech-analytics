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
from google.cloud import speech
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


def list_audios_from_gcs(bucket_name: str) -> list[str]:
    """
    Lista todos los audios (.wav) disponibles en el bucket de GCS.
    """
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blobs = bucket.list_blobs()
        return [blob.name for blob in blobs if blob.name.endswith('.wav')]
    except Exception as e:
        raise RuntimeError(f"Error listando audios desde GCS ({bucket_name}): {e}") from e


def get_audio_bytes_from_gcs(bucket_name: str, blob_name: str) -> bytes:
    """
    Descarga el audio como bytes para reproducirlo en memoria.
    """
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        return blob.download_as_bytes()
    except Exception as e:
        raise RuntimeError(f"Error descargando audio de GCS ({bucket_name}/{blob_name}): {e}") from e


# ---------------------------------------------------------------------------
# 2. Transcripción con Speech-to-Text v1 (Estable para MVPs)
# ---------------------------------------------------------------------------
def transcribe_audio(gcs_uri: str, project_id: str) -> dict:
    """
    Transcribe un archivo de audio desde GCS usando Cloud Speech-to-Text V1.
    Habilita diarización nativa para identificar Agente y Cliente.
    """
    try:
        print(f"⏳ Iniciando transcripción V1 para: {gcs_uri}")
        client = speech.SpeechClient()
        audio = speech.RecognitionAudio(uri=gcs_uri)

        # Configuración V1 optimizada con contextos extraídos de los diálogos
        config = speech.RecognitionConfig(
            language_code="es-CL",
            model="telephony",
            sample_rate_hertz=16000,
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
            speech_contexts=[
                speech.SpeechContext(
                    phrases=[
                        # Términos críticos para DLP
                        "su RUT", "mi RUT es", "RUT", "email es", "correo es", "arroba", "punto com", "dígitos",
                        # Vocabulario bancario
                        "cartola", "movimientos", "saldo disponible", "crédito de consumo", 
                        "tasa de interés", "seguro de desgravamen", "sucursal", "comisiones", 
                        "comisión de mantención", "bloquear mi tarjeta", "cuenta corriente",
                        "fraude", "área de fraudes", "transferencia", "ejecutivo de crédito",
                        # Nombres propios
                        "Banco Andino", "Carlos", "Sofía", "Rodrigo", "Daniela", "Miguel",
                        # Frases comunes
                        "¿en qué le puedo ayudar?", "¿en qué le puedo orientar?", 
                        "verificar su identidad", "cinco días hábiles", "forma remota"
                    ],
                    boost=15.0  
                )
            ],
            diarization_config=speech.SpeakerDiarizationConfig(
                enable_speaker_diarization=True,
                min_speaker_count=2,
                max_speaker_count=2,
            ),
        )

        # Usamos long_running_recognize por si el audio supera el minuto de duración
        operation = client.long_running_recognize(config=config, audio=audio)
        
        print("⏳ Procesando el audio en la nube... (Esto puede tardar unos segundos)")
        response = operation.result(timeout=600) # Espera hasta 10 min

        if not response.results:
            raise RuntimeError("Speech-to-Text V1 retornó resultado vacío.")

        all_words = []
        confidence_scores = []

        # 1. Extraer el nivel de confianza general
        for result in response.results:
            if result.alternatives:
                alt = result.alternatives[0]
                confidence_scores.append(alt.confidence if alt.confidence else 0.0)
                
        # 2. En V1, los tags de diarización vienen agrupados en el ÚLTIMO resultado
        last_result = response.results[-1]
        if last_result.alternatives:
            for word_info in last_result.alternatives[0].words:
                palabra_limpia = word_info.word
                
                # Forzar corrección manual de "rot" o "root" a "RUT"
                if palabra_limpia.lower().replace(".", "").replace(",", "") in ["rot", "root"]:
                    palabra_limpia = "RUT"

                all_words.append({
                    "word": palabra_limpia,
                    "speaker_tag": str(word_info.speaker_tag),
                })

        if not all_words:
            raise RuntimeError("No se detectaron palabras o hablantes en la transcripción.")

        # Reutilizamos tu excelente función para agrupar los segmentos
        segments = _group_words_into_segments(all_words)
        
        avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0

        print(f"✅ Transcripción completada. Confianza promedio: {avg_confidence:.2f}")

        return {
            "segments": segments,
            "confidence_score": round(avg_confidence, 4),
        }

    except Exception as e:
        raise RuntimeError(f"Error en Speech-to-Text V1 para {gcs_uri}: {e}") from e


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
    REPLACEMENT_MAP = {
        "CHILE_CDI_NUMBER":   "[RUT_CENSURADO]",
        "CREDIT_CARD_NUMBER": "[TARJETA_CENSURADA]",
        "EMAIL_ADDRESS":      "[EMAIL_CENSURADO]",
        "PHONE_NUMBER":       "[TELEFONO_CENSURADO]",
        "CHILE_RUT_CUSTOM":   "[RUT_CENSURADO]",  # detector propio
    }

    try:
        dlp_client = dlp_v2.DlpServiceClient()
        parent = f"projects/{project_id}/locations/global"

        # ── Info types nativos de GCP ──────────────────────────────────────
        native_info_types = [
            dlp_v2.InfoType(name=name)
            for name in REPLACEMENT_MAP.keys()
            if name != "CHILE_RUT_CUSTOM"
        ]

        # ── Detector personalizado con regex para RUT chileno ──────────────
        # Cubre: 12.345.678-5 | 12345678-5 | 123456785
        custom_rut_detector = dlp_v2.CustomInfoType(
            info_type=dlp_v2.InfoType(name="CHILE_RUT_CUSTOM"),
            regex=dlp_v2.CustomInfoType.Regex(
                pattern=r"\b\d{1,2}[.\d]{0,9}\d-[\dkK]\b"
            ),
            likelihood=dlp_v2.Likelihood.VERY_LIKELY,  # forzamos confianza alta
        )

        # ── FIX PRINCIPAL: bajar umbral a POSSIBLE ─────────────────────────
        inspect_config = dlp_v2.InspectConfig(
            info_types=native_info_types,
            custom_info_types=[custom_rut_detector],
            min_likelihood=dlp_v2.Likelihood.POSSIBLE,  # umbral mas permisivo
            include_quote=True,
        )

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

        # Reemplazar tokens genéricos de DLP → tokens en español
        for info_type, token in REPLACEMENT_MAP.items():
            redacted_text = redacted_text.replace(f"[{info_type}]", token)

        # Contar hallazgos
        inspect_request = dlp_v2.InspectContentRequest(
            parent=parent,
            inspect_config=inspect_config,
            item=dlp_v2.ContentItem(value=text),
        )
        inspect_response = dlp_client.inspect_content(request=inspect_request)
        findings_list = inspect_response.result.findings
        findings_count = len(findings_list)

        findings_details = []
        for f in findings_list:
            findings_details.append({
                "info_type": f.info_type.name,
                "quote": f.quote
            })

        return {
            "redacted_text": redacted_text,
            "findings_count": findings_count,
            "findings_details": findings_details,
        }

    except Exception as e:
        raise RuntimeError(f"Error en Cloud DLP al redactar PII: {e}") from e


# ---------------------------------------------------------------------------
# 4. Extracción de insights con Vertex AI / Gemini
# ---------------------------------------------------------------------------
def extract_insights(
    transcript_redacted: str,
    project_id: str,
    model: str = "gemini-2.5-flash",
) -> dict:
    """
    Extrae métricas de negocio de una transcripción usando Vertex AI (Gemini).

    Solicita a Gemini un JSON estructurado con intención, sentimiento, riesgo de
    churn y resumen. Limpia automáticamente markdown fences (```json ... ```)
    antes de parsear la respuesta.

    Args:
        transcript_redacted: Transcripción con PII ya enmascarada.
        project_id: ID del proyecto GCP.
        model: Nombre del modelo Gemini a usar (default: gemini-2.5-flash).

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
