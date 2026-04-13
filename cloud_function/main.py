import os
import struct
import wave
import io
import json
import functions_framework
from google.cloud import texttospeech
from google.cloud import storage

# Configuración de voces
VOICE_AGENTE = texttospeech.VoiceSelectionParams(
    language_code="es-US",
    name="es-US-Neural2-B",  # Voz masculina de alta calidad
    ssml_gender=texttospeech.SsmlVoiceGender.MALE,
)
VOICE_CLIENTE = texttospeech.VoiceSelectionParams(
    language_code="es-US",
    name="es-US-Neural2-A",  # Voz femenina de alta calidad
    ssml_gender=texttospeech.SsmlVoiceGender.FEMALE,
)
AUDIO_CONFIG = texttospeech.AudioConfig(
    audio_encoding=texttospeech.AudioEncoding.LINEAR16,
    sample_rate_hertz=16000,
)

# Guiones
LLAMADAS = {
    "Llamada_01_ReclamoFraude": [
        ("agente",  "Banco Andino, le habla Carlos, ¿en qué le puedo ayudar?"),
        ("cliente", "Buenas tardes, mire, necesito reportar un cargo que no reconozco en mi tarjeta. Aparece un cobro de ciento cincuenta mil pesos de una tienda que no conozco."),
        ("agente",  "Entiendo su preocupación. Para verificar su identidad, ¿me puede indicar su RUT?"),
        ("cliente", "Sí, mi RUT es doce punto trescientos cuarenta y cinco punto seiscientos setenta y ocho guión cinco."),
        ("agente",  "Perfecto. Y el número de tarjeta, los últimos cuatro dígitos por favor."),
        ("cliente", "Los últimos cuatro son cuatro cuatro siete uno. Oiga, y el cargo fue ayer en la noche, yo estaba en mi casa."),
        ("agente",  "Comprendo. Estoy revisando en el sistema ahora mismo. Efectivamente veo una transacción por ciento cincuenta mil pesos que parece sospechosa. Voy a proceder a bloquear la tarjeta de forma preventiva."),
        ("cliente", "Sí, por favor bloquéela. Esto es un fraude, ¡quiero que me devuelvan ese dinero!"),
        ("agente",  "Ya queda bloqueada su tarjeta. El caso será derivado al área de fraudes. En un plazo de cinco días hábiles recibirá una respuesta. Le llegará un mensaje de confirmación al número registrado."),
        ("cliente", "Está bien, pero me tiene que llamar el área de fraudes, esto no puede quedar así."),
    ],
    "Llamada_02_ConsultaSaldo": [
        ("agente",  "Banco Andino, le habla Sofía, ¿en qué le puedo orientar?"),
        ("cliente", "Hola Sofía, quiero saber mi saldo disponible y también los últimos movimientos de mi cuenta corriente."),
        ("agente",  "Con gusto. Para verificar su identidad, ¿me dice su RUT por favor?"),
        ("cliente", "Claro, once punto doscientos veintidós punto trescientos treinta y tres guión nueve."),
        ("agente",  "Muchas gracias. Veo que su saldo disponible es de ochocientos cuarenta y dos mil pesos. ¿Desea que le detalle los últimos cinco movimientos?"),
        ("cliente", "Sí, y además mándeme la cartola completa de este mes al correo. Mi email es juan punto perez arroba gmail punto com."),
        ("agente",  "Anotado. Le enviaré la cartola a ese correo en los próximos minutos. En cuanto a los movimientos: el treinta de marzo un pago en supermercado por cuarenta y cinco mil, el veintinueve una transferencia recibida de doscientos mil..."),
        ("cliente", "Está bien, con esos dos me basta. Gracias, Sofía."),
        ("agente",  "Con mucho gusto. La cartola estará en su correo electrónico a la brevedad. ¿Hay algo más en que pueda ayudarle?"),
        ("cliente", "No, eso es todo. Hasta luego."),
    ],
    "Llamada_03_SolicitudPrestamo": [
        ("agente",  "Banco Andino, habla Rodrigo. Buenos días, ¿en qué le puedo ayudar?"),
        ("cliente", "Buenos días. Estoy interesado en solicitar un crédito de consumo. Quiero saber montos, plazos y tasa de interés."),
        ("agente",  "Perfecto, con gusto lo oriento. ¿Para verificar su perfil podría indicarme su RUT?"),
        ("cliente", "Sí, mi RUT es quince punto cuatrocientos cincuenta y seis punto setecientos ochenta y nueve guión cinco."),
        ("agente",  "Gracias. Revisando su historial crediticio... veo que califica para créditos desde cinco millones hasta veinte millones de pesos. Los plazos van de doce a sesenta meses, con una tasa de interés mensual del uno coma dos por ciento."),
        ("cliente", "Me interesa algo alrededor de ocho millones a cuarenta y ocho meses. ¿Cuánto quedaría la cuota?"),
        ("agente",  "Para ocho millones a cuarenta y ocho meses, la cuota mensual aproximada sería de doscientos cincuenta mil pesos. Esto incluye seguro de desgravamen."),
        ("cliente", "Suena bien. ¿Puedo solicitarlo hoy mismo o tengo que ir a la sucursal?"),
        ("agente",  "Podemos iniciar el proceso de forma remota. Le agendar una reunión con un ejecutivo de crédito para mañana en la tarde si le parece."),
        ("cliente", "Perfecto, quedo a las cinco de la tarde entonces. Muchas gracias, Rodrigo."),
    ],
    "Llamada_04_BloqueoTarjeta": [
        ("agente",  "Banco Andino urgencias, le habla Daniela, ¿en qué le puedo ayudar?"),
        ("cliente", "Necesito bloquear mi tarjeta de débito ahora mismo, me acaban de robar la billetera."),
        ("agente",  "Entendido, procedo de inmediato. Necesito verificar su identidad. ¿Su RUT por favor?"),
        ("cliente", "Dieciséis punto quinientos sesenta y siete punto ochocientos noventa guión siete. ¡Rápido por favor, tengo miedo que la usen!"),
        ("agente",  "Estoy bloqueando ahora mismo. ¿El número de tarjeta completo lo recuerda, o tiene una foto de la cartola?"),
        ("cliente", "Sí, anótelo: cuatro cinco cuatro cinco, guión, seis siete ocho nueve, guión, cero uno dos tres, guión, cuatro cinco seis siete."),
        ("agente",  "Listo, la tarjeta quedó bloqueada en este instante. Ninguna transacción posterior al robo será procesada."),
        ("cliente", "Gracias a Dios. ¿Cómo hago para reponer la tarjeta?"),
        ("agente",  "Puede solicitarla en cualquier sucursal con su cédula de identidad. En cinco días hábiles recibirá la nueva. También puede coordinar el envío a domicilio por nuestra app."),
        ("cliente", "Voy a ir a la sucursal entonces. Gracias, Daniela, me salvó."),
    ],
    "Llamada_05_CancelacionCuenta": [
        ("agente",  "Banco Andino, le habla Miguel. ¿En qué le puedo ayudar?"),
        ("cliente", "Llamo para cerrar mi cuenta corriente. Estoy muy disconforme con las comisiones que me cobran cada mes."),
        ("agente",  "Lamento escuchar eso. ¿Me podría indicar su RUT para consultar su cuenta?"),
        ("cliente", "Diecisiete punto seiscientos setenta y ocho punto novecientos uno guión ocho. Y también mándeme la confirmación al email: maria punto gonzalez arroba hotmail punto com."),
        ("agente",  "Entendido, Miguel. Veo que tiene una comisión de mantención de ocho mil pesos mensuales. ¿Ese es el principal motivo?"),
        ("cliente", "Sí, y ya encontré otro banco que no cobra comisión de mantención. Ya tomé la decisión."),
        ("agente",  "Entiendo su postura. Como alternativa, puedo ofrecerle la exención total de la comisión de mantención durante doce meses si decide quedarse."),
        ("cliente", "Gracias por la oferta, Miguel, pero ya lo decidí. Quiero cerrar la cuenta hoy."),
        ("agente",  "Está bien, respeto su decisión. Para proceder con el cierre necesitaré que se acerque a una sucursal con su cédula de identidad. ¿desea que le indique la más cercana?"),
        ("cliente", "No gracias, ya sé donde queda. Que tenga buen día."),
    ],
}

def synthesize_turn(client: texttospeech.TextToSpeechClient, text: str, speaker: str) -> bytes:
    voice = VOICE_AGENTE if speaker == "agente" else VOICE_CLIENTE
    synthesis_input = texttospeech.SynthesisInput(text=text)
    response = client.synthesize_speech(
        input=synthesis_input, voice=voice, audio_config=AUDIO_CONFIG
    )
    return response.audio_content

def add_silence(duration_ms: int = 500, sample_rate: int = 16000) -> bytes:
    num_samples = int(sample_rate * duration_ms / 1000)
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))

def upload_wav_to_gcs(bucket, audio_data: bytes, call_name: str, sample_rate: int = 16000) -> str:
    # Escribir el WAV en memoria
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_data)
    
    # Subir a Google Cloud Storage
    blob_name = f"{call_name}.wav"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(wav_buffer.getvalue(), content_type="audio/wav")
    return f"gs://{bucket.name}/{blob_name}"

@functions_framework.http
def generate_audios_http(request):
    """HTTP Cloud Function para generar los audios y subirlos a GCS."""
    bucket_name = os.environ.get("GCS_BUCKET_NAME")
    if not bucket_name:
        return json.dumps({"error": "La variable de entorno GCS_BUCKET_NAME no está configurada"}), 500

    storage_client = storage.Client()
    tts_client = texttospeech.TextToSpeechClient()
    bucket = storage_client.bucket(bucket_name)

    resultados = []
    
    for call_name, turns in LLAMADAS.items():
        segments = []
        silence = add_silence(duration_ms=600)
        
        for speaker, text in turns:
            audio_bytes = synthesize_turn(tts_client, text, speaker)
            wav_buffer = io.BytesIO(audio_bytes)
            with wave.open(wav_buffer, "rb") as w:
                segments.append(w.readframes(w.getnframes()))
            segments.append(silence)
        
        combined_pcm = b"".join(segments)
        
        # Subir directo al bucket
        gcs_uri = upload_wav_to_gcs(bucket, combined_pcm, call_name)
        resultados.append({"call_name": call_name, "gcs_uri": gcs_uri})

    return json.dumps({
        "status": "success",
        "message": f"Se han generado y subido {len(resultados)} audios al bucket {bucket_name}",
        "files": resultados
    }), 200, {"Content-Type": "application/json"}
