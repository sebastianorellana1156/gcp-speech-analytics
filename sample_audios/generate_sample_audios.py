"""
Script de generación de audios de muestra para el MVP Speech Analytics.

Genera 5 archivos .wav de llamadas bancarias ficticias en español chileno (es-CL)
usando la API de Google Cloud Text-to-Speech. Cada llamada incluye PII simulada
(RUT, tarjeta de crédito, email) que será detectada y enmascarada por Cloud DLP.

Uso:
    export GCP_PROJECT_ID=gcp-speech-analytics
    python sample_audios/generate_sample_audios.py
"""

import os
import struct
import wave
from google.cloud import texttospeech

# ---------------------------------------------------------------------------
# Configuración de voces
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Guiones de los 5 escenarios bancarios (mínimo 6 turnos alternados cada uno)
# ---------------------------------------------------------------------------
LLAMADAS = {
    "Llamada_01_ReclamoFraude": [
        ("agente",  "Banco Andino, le habla Carlos, ¿en qué le puedo ayudar?"),
        ("cliente", "Buenas tardes, mire, necesito reportar un cargo que no reconozco en mi tarjeta. Aparece un cobro de ciento cincuenta mil pesos de una tienda que no conozco."),
        ("agente",  "Entiendo su preocupación. Para verificar su identidad, ¿me puede indicar su RUT?"),
        ("cliente", "Sí, mi RUT es doce punto trescientos cuarenta y cinco punto seiscientos setenta y ocho guión nueve."),
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
        ("cliente", "Claro, once punto doscientos veintidós punto trescientos treinta y tres guión k."),
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
        ("cliente", "Sí, mi RUT es quince punto cuatrocientos cincuenta y seis punto setecientos ochenta y nueve guión dos."),
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
        ("cliente", "Dieciséis punto quinientos sesenta y siete punto ochocientos noventa guión tres. ¡Rápido por favor, tengo miedo que la usen!"),
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
        ("cliente", "Diecisiete punto seiscientos setenta y ocho punto novecientos uno guión cinco. Y también mándeme la confirmación al email: maria punto gonzalez arroba hotmail punto com."),
        ("agente",  "Entendido, María. Veo que tiene una comisión de mantención de ocho mil pesos mensuales. ¿Ese es el principal motivo?"),
        ("cliente", "Sí, y ya encontré otro banco que no cobra comisión de mantención. Ya tomé la decisión."),
        ("agente",  "Entiendo su postura. Como alternativa, puedo ofrecerle la exención total de la comisión de mantención durante doce meses si decide quedarse."),
        ("cliente", "Gracias por la oferta, Miguel, pero ya lo decidí. Quiero cerrar la cuenta hoy."),
        ("agente",  "Está bien, respeto su decisión. Para proceder con el cierre necesitaré que se acerque a una sucursal con su cédula de identidad. ¿desea que le indique la más cercana?"),
        ("cliente", "No gracias, ya sé donde queda. Que tenga buen día."),
    ],
}

OUTPUT_DIR = os.path.join(os.path.dirname(__file__))


def synthesize_turn(client: texttospeech.TextToSpeechClient, text: str, speaker: str) -> bytes:
    """
    Sintetiza un turno de diálogo en audio PCM usando Text-to-Speech.

    Args:
        client: Cliente de la API Text-to-Speech.
        text: Texto a sintetizar.
        speaker: 'agente' o 'cliente', determina la voz a usar.

    Returns:
        Bytes de audio PCM LINEAR16 a 16kHz.
    """
    voice = VOICE_AGENTE if speaker == "agente" else VOICE_CLIENTE
    synthesis_input = texttospeech.SynthesisInput(text=text)

    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=AUDIO_CONFIG,
    )
    return response.audio_content


def add_silence(duration_ms: int = 500, sample_rate: int = 16000) -> bytes:
    """
    Genera bytes de silencio PCM para pausas entre turnos.

    Args:
        duration_ms: Duración del silencio en milisegundos.
        sample_rate: Tasa de muestreo en Hz.

    Returns:
        Bytes de silencio PCM.
    """
    num_samples = int(sample_rate * duration_ms / 1000)
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


def concatenate_audio_segments(segments: list[bytes]) -> bytes:
    """
    Concatena múltiples segmentos de audio PCM en uno solo.

    Args:
        segments: Lista de bytes PCM a concatenar.

    Returns:
        Bytes PCM concatenados.
    """
    return b"".join(segments)


def save_as_wav(audio_data: bytes, output_path: str, sample_rate: int = 16000) -> None:
    """
    Guarda bytes de audio PCM como archivo .wav.

    Args:
        audio_data: Audio en formato PCM LINEAR16.
        output_path: Ruta completa del archivo de salida.
        sample_rate: Tasa de muestreo (debe coincidir con la síntesis).
    """
    with wave.open(output_path, "wb") as wav_file:
        wav_file.setnchannels(1)        # Mono
        wav_file.setsampwidth(2)        # 16-bit PCM = 2 bytes por muestra
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_data)


def generate_call_audio(
    client: texttospeech.TextToSpeechClient,
    call_name: str,
    turns: list[tuple[str, str]],
) -> None:
    """
    Genera el archivo .wav completo de una llamada bancaria.

    Sintetiza cada turno de diálogo con la voz correspondiente (agente/cliente),
    los concatena con pausas de silencio entre turnos y guarda el archivo.

    Args:
        client: Cliente de la API Text-to-Speech.
        call_name: Nombre del escenario (se usa como nombre de archivo).
        turns: Lista de tuplas (speaker, texto) con el guión de la llamada.
    """
    print(f"\n🎙️  Generando: {call_name}")
    segments = []
    silence = add_silence(duration_ms=600)

    for i, (speaker, text) in enumerate(turns):
        print(f"   Turno {i + 1}/{len(turns)} [{speaker}]: {text[:60]}...")
        audio_bytes = synthesize_turn(client, text, speaker)
        # La API devuelve el header WAV en el primer chunk — lo quitamos para concatenar
        # Los bytes de TTS LINEAR16 incluyen header WAV, necesitamos solo PCM
        # Leemos el WAV en memoria para extraer solo los frames PCM
        import io
        wav_buffer = io.BytesIO(audio_bytes)
        with wave.open(wav_buffer, "rb") as w:
            segments.append(w.readframes(w.getnframes()))
        segments.append(silence)

    combined_pcm = concatenate_audio_segments(segments)
    output_path = os.path.join(OUTPUT_DIR, f"{call_name}.wav")
    save_as_wav(combined_pcm, output_path)
    print(f"   ✅ Guardado: {output_path}")


def main() -> None:
    """
    Punto de entrada principal. Genera los 5 audios de llamadas bancarias.

    Requiere que la variable de entorno GCP_PROJECT_ID esté configurada
    y que la autenticación de GCP esté activa (gcloud auth application-default login).
    """
    print("🚀 Iniciando generación de audios de muestra")
    print(f"   Output dir: {OUTPUT_DIR}")
    print(f"   Escenarios: {len(LLAMADAS)}")

    client = texttospeech.TextToSpeechClient()

    for call_name, turns in LLAMADAS.items():
        try:
            generate_call_audio(client, call_name, turns)
        except Exception as e:
            print(f"❌ Error generando {call_name}: {e}")
            raise

    print("\n🎉 Todos los audios generados exitosamente.")
    print("   Los archivos .wav están en el directorio sample_audios/")
    print("   Recuerda: los archivos .wav son ignorados por .gitignore")


if __name__ == "__main__":
    main()
