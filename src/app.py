"""
Interfaz de usuario Streamlit para el MVP Speech Analytics Bancario.

Orquesta el pipeline completo de procesamiento de llamadas bancarias:
  1. Selector de audio + reproductor
  2. Teatro del backend (progreso visual paso a paso)
  3. Panel de resultados (transcripción + insights)
  4. Botón de reseteo de base de datos

Los resultados persisten en st.session_state hasta el reseteo explícito.
"""

import os
import time

import streamlit as st

from gcp_services import upload_audio_to_gcs, transcribe_audio, redact_pii, extract_insights
from bq_client import insert_call_record, truncate_table

# ---------------------------------------------------------------------------
# Configuración de la página
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Speech Analytics | Banca GCP",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Variables de entorno
# ---------------------------------------------------------------------------
GCP_PROJECT_ID  = os.getenv("GCP_PROJECT_ID", "gcp-speech-analytics")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "speech-analytics-gcp-speech-analytics")
BQ_DATASET_ID   = os.getenv("BQ_DATASET_ID",   "call_analytics_dataset")
BQ_TABLE_ID     = os.getenv("BQ_TABLE_ID",     "call_transcriptions")
GEMINI_MODEL    = os.getenv("GEMINI_MODEL",    "gemini-1.5-flash")

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "sample_audios")

AUDIO_OPTIONS = [
    "Llamada_01_ReclamoFraude",
    "Llamada_02_ConsultaSaldo",
    "Llamada_03_SolicitudPrestamo",
    "Llamada_04_BloqueoTarjeta",
    "Llamada_05_CancelacionCuenta",
]

# ---------------------------------------------------------------------------
# Inicialización del session_state
# ---------------------------------------------------------------------------
DEFAULTS = {
    "processed":           False,
    "transcript_segments": [],
    "insights":            {},
    "audio_filename":      "",
    "pipeline_metrics":    {},
    "error_message":       None,
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ---------------------------------------------------------------------------
# Estilos CSS custom
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Fondo oscuro elegante */
    .stApp {
        background: linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%);
        color: #e8e8f0;
    }

    /* Header principal */
    .main-header {
        text-align: center;
        padding: 2rem 0 1rem 0;
        border-bottom: 1px solid rgba(100, 200, 255, 0.2);
        margin-bottom: 2rem;
    }
    .main-header h1 {
        background: linear-gradient(90deg, #64c8ff, #a78bfa, #f472b6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        font-size: 2.4rem;
        font-weight: 800;
        letter-spacing: -0.5px;
    }
    .main-header p {
        color: #94a3b8;
        font-size: 1rem;
    }

    /* Badges de PII */
    .pii-badge {
        display: inline-block;
        background-color: #ff4b4b;
        color: white;
        font-size: 0.72rem;
        font-weight: 700;
        padding: 2px 7px;
        border-radius: 4px;
        margin: 0 2px;
        font-family: monospace;
        letter-spacing: 0.3px;
    }

    /* Chat bubbles — Agente (izquierda) */
    .chat-bubble-agente {
        background: rgba(59, 130, 246, 0.15);
        border-left: 3px solid #3b82f6;
        border-radius: 0 12px 12px 0;
        padding: 10px 14px;
        margin: 8px 20% 8px 0;
        font-size: 0.92rem;
        line-height: 1.5;
    }
    .chat-speaker-agente {
        font-size: 0.75rem;
        color: #60a5fa;
        font-weight: 700;
        margin-bottom: 4px;
        letter-spacing: 0.5px;
    }

    /* Chat bubbles — Cliente (derecha) */
    .chat-bubble-cliente {
        background: rgba(107, 114, 128, 0.15);
        border-right: 3px solid #6b7280;
        border-radius: 12px 0 0 12px;
        padding: 10px 14px;
        margin: 8px 0 8px 20%;
        font-size: 0.92rem;
        line-height: 1.5;
        text-align: right;
    }
    .chat-speaker-cliente {
        font-size: 0.75rem;
        color: #9ca3af;
        font-weight: 700;
        margin-bottom: 4px;
        letter-spacing: 0.5px;
        text-align: right;
    }

    /* Card de insights */
    .insight-card {
        background: rgba(255, 255, 255, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 12px;
    }
    .insight-label {
        font-size: 0.78rem;
        color: #64748b;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.6px;
        margin-bottom: 6px;
    }
    .insight-value {
        font-size: 1rem;
        color: #e2e8f0;
        font-weight: 500;
    }

    /* Sentiment badges */
    .sentiment-positivo { color: #4ade80; font-weight: 700; }
    .sentiment-neutro    { color: #facc15; font-weight: 700; }
    .sentiment-negativo  { color: #f87171; font-weight: 700; }

    /* Churn risk badges */
    .churn-alto  { color: #f87171; font-weight: 700; font-size: 1.05rem; }
    .churn-bajo  { color: #4ade80; font-weight: 700; font-size: 1.05rem; }

    /* Sección de audio */
    .audio-section {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 16px;
        padding: 24px;
        margin-bottom: 24px;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown("""
<div class="main-header">
    <h1>🎙️ Speech Analytics — Banca GCP</h1>
    <p>Pipeline de IA para análisis de llamadas bancarias · Cloud Speech-to-Text v2 · DLP · Vertex AI · BigQuery</p>
</div>
""", unsafe_allow_html=True)

# ===========================================================================
# BLOQUE 1 — Selector de audio y reproductor
# ===========================================================================
st.markdown('<div class="audio-section">', unsafe_allow_html=True)
st.subheader("🎵 Seleccionar Llamada")

col_sel, col_info = st.columns([2, 1])
with col_sel:
    selected_audio = st.selectbox(
        "Escenario de llamada bancaria:",
        options=AUDIO_OPTIONS,
        format_func=lambda x: x.replace("_", " ").replace("Llamada", "📞 Llamada"),
    )

audio_path = os.path.join(AUDIO_DIR, f"{selected_audio}.wav")

with col_info:
    if os.path.exists(audio_path):
        size_kb = os.path.getsize(audio_path) / 1024
        st.metric("Archivo", f"{selected_audio}.wav")
        st.caption(f"📂 Tamaño: {size_kb:.1f} KB")
    else:
        st.warning("⚠️ Audio no generado aún. Ejecuta `generate_sample_audios.py` primero.")

if os.path.exists(audio_path):
    st.audio(audio_path, format="audio/wav")

st.markdown("</div>", unsafe_allow_html=True)

# Botón principal
col_btn, col_space = st.columns([1, 3])
with col_btn:
    process_clicked = st.button(
        "▶ Procesar Llamada en GCP",
        type="primary",
        use_container_width=True,
    )

# ===========================================================================
# BLOQUE 2 — Teatro del Backend (solo si se presionó el botón)
# ===========================================================================
if process_clicked:
    if not os.path.exists(audio_path):
        st.error("❌ No se encontró el archivo de audio. Genera los audios de muestra primero.")
        st.stop()

    st.session_state.processed = False
    st.session_state.error_message = None
    pipeline_start = time.time()

    with st.status("🔄 Procesando llamada en GCP...", expanded=True) as status:
        try:
            # ── Paso 1: Upload a GCS ──────────────────────────────────────
            st.write("⏳ Subiendo audio a Cloud Storage...")
            gcs_uri = upload_audio_to_gcs(audio_path, GCS_BUCKET_NAME)
            st.write("✅ Audio subido a Cloud Storage.")

            # ── Paso 2: Transcripción STT v2 ──────────────────────────────
            st.write("⏳ Transcribiendo audio (Cloud Speech-to-Text)...")
            stt_result = transcribe_audio(gcs_uri, GCP_PROJECT_ID)
            segments         = stt_result["segments"]
            confidence_score = stt_result["confidence_score"]
            st.write(f"✅ Transcripción completada (confianza: {confidence_score * 100:.1f}%)")

            # ── Paso 3: Redacción PII con DLP ─────────────────────────────
            st.write("⏳ Enmascarando datos sensibles (Cloud DLP)...")
            full_transcript = " ".join(
                f"[{seg['speaker']}]: {seg['text']}" for seg in segments
            )
            dlp_result       = redact_pii(full_transcript, GCP_PROJECT_ID)
            redacted_text    = dlp_result["redacted_text"]
            findings_count   = dlp_result["findings_count"]
            st.write(f"✅ {findings_count} hallazgos PII enmascarados.")

            # ── Paso 4: Gemini insights ───────────────────────────────────
            st.write("⏳ Extrayendo insights con LLM (Vertex AI / Gemini)...")
            insights = extract_insights(redacted_text, GCP_PROJECT_ID, GEMINI_MODEL)
            st.write("✅ Insights extraídos.")

            # ── Paso 5: Insert BigQuery ───────────────────────────────────
            st.write("⏳ Guardando en BigQuery...")
            processing_duration = round(time.time() - pipeline_start, 2)

            record = {
                "audio_filename":               f"{selected_audio}.wav",
                "transcript_redacted":          redacted_text,
                "call_intent":                  insights.get("call_intent"),
                "customer_sentiment":           insights.get("customer_sentiment"),
                "churn_risk":                   insights.get("churn_risk"),
                "summary":                      insights.get("summary"),
                "processing_duration_seconds":  processing_duration,
                "speech_confidence_score":      confidence_score,
                "dlp_findings_count":           findings_count,
                "gemini_model_used":            GEMINI_MODEL,
                "pipeline_status":              "SUCCESS",
            }
            insert_call_record(record, GCP_PROJECT_ID, BQ_DATASET_ID, BQ_TABLE_ID)
            st.write("✅ ¡Datos guardados en BigQuery!")
            st.write(f"⏱️ Tiempo total de procesamiento: {processing_duration:.2f} segundos")

            # Guardar en session_state
            st.session_state.processed           = True
            st.session_state.transcript_segments = segments
            st.session_state.insights            = insights
            st.session_state.audio_filename      = f"{selected_audio}.wav"
            st.session_state.pipeline_metrics    = {
                "speech_confidence_score":      confidence_score,
                "dlp_findings_count":           findings_count,
                "gemini_model_used":            GEMINI_MODEL,
                "processing_duration_seconds":  processing_duration,
            }
            status.update(label="✅ Pipeline completado exitosamente.", state="complete")

        except Exception as e:
            error_msg = str(e)
            st.session_state.error_message = error_msg
            st.error(f"❌ Error en el pipeline: {error_msg}")
            status.update(label="❌ Error en el pipeline.", state="error")

# ===========================================================================
# BLOQUE 3 — Resultados (solo si hay datos en session_state)
# ===========================================================================
if st.session_state.processed:
    metrics = st.session_state.pipeline_metrics
    insights = st.session_state.insights

    st.markdown("---")
    st.subheader("📈 Métricas del Pipeline")

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric(
            "🎯 Confianza STT",
            f"{metrics.get('speech_confidence_score', 0) * 100:.1f}%",
        )
    with m2:
        st.metric(
            "🔒 Hallazgos PII",
            metrics.get("dlp_findings_count", 0),
        )
    with m3:
        st.metric(
            "🤖 Modelo IA",
            metrics.get("gemini_model_used", "—"),
        )
    with m4:
        st.metric(
            "⏱️ Duración",
            f"{metrics.get('processing_duration_seconds', 0):.2f}s",
        )

    st.markdown("---")
    col_transcript, col_insights = st.columns([1.2, 1])

    # ── Columna izquierda: Transcripción ─────────────────────────────────
    with col_transcript:
        st.markdown("### 💬 Transcripción (PII enmascarada)")
        transcript_container = st.container(height=480)

        PII_TOKENS = [
            "[CHILE_RUT_CENSURADO]",
            "[TARJETA_CENSURADA]",
            "[EMAIL_CENSURADO]",
            "[TELEFONO_CENSURADO]",
        ]

        def highlight_pii(text: str) -> str:
            """Reemplaza tokens PII por badges rojos visuales."""
            for token in PII_TOKENS:
                badge = f'<span class="pii-badge">{token}</span>'
                text = text.replace(token, badge)
            return text

        with transcript_container:
            for seg in st.session_state.transcript_segments:
                speaker = seg.get("speaker", "Agente")
                text    = highlight_pii(seg.get("text", ""))

                if speaker == "Agente":
                    st.markdown(f"""
                    <div class="chat-bubble-agente">
                        <div class="chat-speaker-agente">🎧 AGENTE</div>
                        {text}
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div class="chat-bubble-cliente">
                        <div class="chat-speaker-cliente">👤 CLIENTE</div>
                        {text}
                    </div>
                    """, unsafe_allow_html=True)

    # ── Columna derecha: Insights ─────────────────────────────────────────
    with col_insights:
        st.markdown("### 📊 Análisis de la Llamada")

        # Intención
        st.markdown(f"""
        <div class="insight-card">
            <div class="insight-label">🎯 Intención de Llamada</div>
            <div class="insight-value">{insights.get("call_intent", "—")}</div>
        </div>
        """, unsafe_allow_html=True)

        # Sentimiento
        sentiment = insights.get("customer_sentiment", "Neutro")
        sentiment_css = {
            "Positivo": "sentiment-positivo",
            "Neutro":   "sentiment-neutro",
            "Negativo": "sentiment-negativo",
        }.get(sentiment, "sentiment-neutro")
        sentiment_emoji = {"Positivo": "😊", "Neutro": "😐", "Negativo": "😟"}.get(sentiment, "😐")

        st.markdown(f"""
        <div class="insight-card">
            <div class="insight-label">😊 Sentimiento del Cliente</div>
            <div class="insight-value">
                <span class="{sentiment_css}">{sentiment_emoji} {sentiment}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Churn risk
        churn_risk = insights.get("churn_risk", False)
        churn_html = (
            '<span class="churn-alto">⚠️ Alto Riesgo de Abandono</span>'
            if churn_risk else
            '<span class="churn-bajo">✅ Sin Riesgo de Abandono</span>'
        )
        st.markdown(f"""
        <div class="insight-card">
            <div class="insight-label">🚨 Riesgo de Abandono (Churn)</div>
            <div class="insight-value">{churn_html}</div>
        </div>
        """, unsafe_allow_html=True)

        # Resumen
        st.markdown(f"""
        <div class="insight-card">
            <div class="insight-label">📝 Resumen</div>
            <div class="insight-value">{insights.get("summary", "—")}</div>
        </div>
        """, unsafe_allow_html=True)

        # Confirmación BQ
        audio_fn = st.session_state.audio_filename
        st.markdown(f"""
        <div class="insight-card">
            <div class="insight-label">🗄️ BigQuery</div>
            <div class="insight-value" style="color: #4ade80;">
                ✅ Registro guardado en BigQuery<br>
                <span style="font-size:0.82rem; color:#64748b;">{audio_fn}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

# ===========================================================================
# BLOQUE 4 — Botón de reseteo
# ===========================================================================
st.markdown("---")
st.subheader("🗑️ Resetear")
col_reset, col_reset_space = st.columns([1, 4])
with col_reset:
    if st.button(
        "🗑️ Borrar base de datos y reiniciar",
        type="secondary",
        use_container_width=True,
    ):
        with st.spinner("Eliminando registros de BigQuery..."):
            try:
                msg = truncate_table(GCP_PROJECT_ID, BQ_DATASET_ID, BQ_TABLE_ID)
                # Limpiar todo el session_state
                for key, value in DEFAULTS.items():
                    st.session_state[key] = value
                st.success(f"{msg} La sesión ha sido reiniciada.")
            except Exception as e:
                st.error(f"❌ Error al resetear: {e}")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("""
<div style="text-align:center; color:#374151; font-size:0.8rem; margin-top:3rem; padding-top:1.5rem;
    border-top: 1px solid rgba(255,255,255,0.05);">
    Speech Analytics MVP · GCP · Cloud Run · BigQuery · Vertex AI · Cloud Speech-to-Text v2
</div>
""", unsafe_allow_html=True)
