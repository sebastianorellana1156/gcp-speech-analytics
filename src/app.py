"""
Interfaz de usuario Streamlit para el MVP Speech Analytics Bancario.

Orquesta el pipeline completo de procesamiento de llamadas bancarias:
  1. Selector de audio + reproductor
  2. Teatro del backend (progreso visual paso a paso)
  3. Panel de resultados (transcripción + insights)
  4. Botón de reseteo de base de datos .

Los resultados persisten en st.session_state hasta el reseteo explícito.
"""

import os
import time

import streamlit as st

from gcp_services import upload_audio_to_gcs, transcribe_audio, redact_pii, extract_insights, list_audios_from_gcs, get_audio_bytes_from_gcs
from bq_client import insert_call_record, get_top_records

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
GCP_PROJECT_ID  = os.getenv("GCP_PROJECT_ID")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
BQ_DATASET_ID   = os.getenv("BQ_DATASET_ID",   "call_analytics_dataset")
BQ_TABLE_ID     = os.getenv("BQ_TABLE_ID",     "call_transcriptions")
GEMINI_MODEL    = os.getenv("GEMINI_MODEL",    "gemini-2.5-flash")

AUDIO_DIR = os.path.join(os.path.dirname(__file__), "sample_audios")

@st.cache_data(ttl=300)
def cached_list_audios(bucket_name):
    return list_audios_from_gcs(bucket_name)

try:
    AUDIO_OPTIONS = cached_list_audios(GCS_BUCKET_NAME)
except Exception as e:
    st.error(f"Error listando audios: {e}")
    AUDIO_OPTIONS = []

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
# Header e Introducción
# ---------------------------------------------------------------------------
st.markdown("""
<div class="main-header" style="margin-bottom: 0.5rem;">
    <h1>🎙️ Speech Analytics — Banca GCP</h1>
    <p style="font-size: 1.05rem; color: #a5b4fc;">Pipeline <i>Serverless</i> de Inteligencia Artificial para el análisis automatizado de llamadas bancarias.</p>
</div>
""", unsafe_allow_html=True)

with st.container(border=True):
    st.markdown("""
    ### 📖 Acerca de este Proyecto
    Este MVP demuestra una arquitectura completa en **Google Cloud Platform** enfocada en potenciar auditorías de call centers, asegurando la privacidad de los clientes y entregando métricas de negocio accionables, todo en tiempo real.
    
    ### ⚙️ Flujo y Arquitectura End-to-End
    1. **☁️ Cloud Storage**: Repositorio seguro donde aterrizan las llamadas teléfonicas entrantes (`.wav`).
    2. **🗣️ Speech-to-Text V1**: Transcribe el audio con alta precisión y separa los locutores (Diarización: Cliente vs Agente).
    3. **🔒 Cloud DLP**: Detecta y enmascara automáticamente Información Sensible o PII (RUTs, tarjetas de crédito, correos electrónicos).
    4. **🧠 Vertex AI (Gemini 2.5 Flash)**: Ingiere el texto ya anonimizado y estructura KPIs (Intención, Sentimiento, Churn, Resumen).
    5. **🗄️ BigQuery**: Almacena de forma estructurada el payload final y las métricas de rendimiento para su explotación vía herramientas de inteligencia de negocios (BI).
    """)

st.markdown("<br>", unsafe_allow_html=True)

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
        format_func=lambda x: x.replace(".wav", "").replace("_", " ").replace("Llamada", "📞 Llamada"),
    )

with col_info:
    if selected_audio:
        st.metric("GCS Bucket", GCS_BUCKET_NAME)
        st.caption(f"📂 Archivo: {selected_audio}")
    else:
        st.warning("⚠️ No hay audios en el bucket.")

if selected_audio:
    try:
        audio_bytes = get_audio_bytes_from_gcs(GCS_BUCKET_NAME, selected_audio)
        st.audio(audio_bytes, format="audio/wav")
    except Exception as e:
        st.error(f"Error cargando audio desde GCS: {e}")

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
    if not selected_audio:
        st.error("❌ No se ha seleccionado ningún audio del bucket.")
        st.stop()

    st.session_state.processed = False
    st.session_state.error_message = None
    pipeline_start = time.time()

    with st.status("🔄 Procesando llamada en GCP...", expanded=True) as status:
        try:
            # ── Paso 1: Ubicar audio en GCS ──────────────────────────────────────
            st.write("⏳ Obteniendo referencia del audio en Cloud Storage...")
            gcs_uri = f"gs://{GCS_BUCKET_NAME}/{selected_audio}"
            st.write(f"✅ Usando audio desde: {gcs_uri}")

            # ── Paso 2: Transcripción STT v2 ──────────────────────────────
            st.write("⏳ Transcribiendo audio (Cloud Speech-to-Text)...")
            stt_result = transcribe_audio(gcs_uri, GCP_PROJECT_ID)
            segments         = stt_result["segments"]
            confidence_score = stt_result["confidence_score"]
            st.write(f"✅ Transcripción completada (confianza: {confidence_score * 100:.1f}%)")

            # ── Paso 3: Redacción PII con DLP ─────────────────────────────
            st.write("⏳ Enmascarando datos sensibles (Cloud DLP)...")
            # Unir con saltos de línea para poder reconstruir los segmentos visuales
            full_transcript = "\\n".join(
                f"[{seg['speaker']}]: {seg['text']}" for seg in segments
            )
            dlp_result       = redact_pii(full_transcript, GCP_PROJECT_ID)
            redacted_text    = dlp_result["redacted_text"]
            findings_count   = dlp_result["findings_count"]
            findings_details = dlp_result.get("findings_details", [])
            
            # Reconstruir los segmentos para la UI a partir del texto censurado
            redacted_segments = []
            for line in redacted_text.split("\\n"):
                if line.startswith("[Agente]:"):
                    redacted_segments.append({"speaker": "Agente", "text": line.replace("[Agente]:", "").strip()})
                elif line.startswith("[Cliente]:"):
                    redacted_segments.append({"speaker": "Cliente", "text": line.replace("[Cliente]:", "").strip()})
                else:
                    if redacted_segments:
                        redacted_segments[-1]["text"] += " " + line

            st.write(f"✅ {findings_count} hallazgos PII enmascarados.")

            # ── Paso 4: Gemini insights ───────────────────────────────────
            st.write("⏳ Extrayendo insights con LLM (Vertex AI / Gemini)...")
            insights = extract_insights(redacted_text, GCP_PROJECT_ID, GEMINI_MODEL)
            st.write("✅ Insights extraídos.")

            # ── Paso 5: Insert BigQuery ───────────────────────────────────
            st.write("⏳ Guardando en BigQuery...")
            processing_duration = round(time.time() - pipeline_start, 2)

            record = {
                "audio_filename":               selected_audio,
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
            
            # Limpiar caché de BigQuery para que la tabla de abajo se actualice inmediatamente
            if "cached_get_top_records" in globals():
                cached_get_top_records.clear()

            st.write(f"⏱️ Tiempo total de procesamiento: {processing_duration:.2f} segundos")

            # Guardar en session_state
            st.session_state.processed           = True
            st.session_state.transcript_segments = redacted_segments
            st.session_state.findings_details    = findings_details
            st.session_state.insights            = insights
            st.session_state.audio_filename      = selected_audio
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
        st.markdown("### 💬 Transcripción")
        transcript_container = st.container(height=480)

        PII_TOKENS = [
            "[RUT_CENSURADO]",
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

        # Hallazgos PII (Lo censurado)
        findings_count = metrics.get("dlp_findings_count", 0)
        findings_details = st.session_state.get("findings_details", [])
        
        if findings_details:
            badges_html = ""
            for detail in findings_details:
                itype = detail["info_type"].replace("CHILE_CDI_NUMBER", "RUT").replace("CHILE_RUT_CUSTOM", "RUT").replace("CREDIT_CARD_NUMBER", "TARJETA").replace("EMAIL_ADDRESS", "EMAIL").replace("PHONE_NUMBER", "TELÉFONO")
                quote = detail["quote"]
                badges_html += f"<div style='margin-bottom: 3px;'><span class='pii-badge'>{itype}</span> <span style='font-family: monospace; color: #f87171;'>{quote}</span></div>"
            
            pii_content = f"<div style='margin-bottom: 4px; font-size: 0.85rem; color: #94a3b8;'>Interceptados ({findings_count}):</div>{badges_html}"
        else:
            pii_content = '<span style="color: #64748b;">Ninguno</span>'

        st.markdown(f"""
        <div class="insight-card">
            <div class="insight-label">🔒 PII Interceptada (Datos Originales)</div>
            <div class="insight-value">{pii_content}</div>
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
# BLOQUE 5 — Historial de BigQuery
# ===========================================================================
st.markdown("---")
st.subheader("🗄️ Últimos Registros en BigQuery (Top 10)")

@st.cache_data(ttl=60)
def cached_get_top_records(project_id, dataset_id, table_id):
    return get_top_records(project_id, dataset_id, table_id)

col_table_header, col_refresh = st.columns([4, 1])
with col_refresh:
    if st.button("🔄 Actualizar", use_container_width=True):
        cached_get_top_records.clear() # Fuerza a borrar la caché

try:
    records = cached_get_top_records(GCP_PROJECT_ID, BQ_DATASET_ID, BQ_TABLE_ID)
    if records:
        st.dataframe(
            records,
            use_container_width=True,
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Fecha", format="DD/MM/YYYY HH:mm:ss"),
                "audio_filename": "Archivo",
                "call_intent": "Intención",
                "customer_sentiment": "Sentimiento",
                "churn_risk": st.column_config.CheckboxColumn("Riesgo Churn"),
                "processing_duration_seconds": st.column_config.NumberColumn("Duración (s)", format="%.1f")
            },
            hide_index=True
        )
    else:
        st.info("La base de datos está vacía. Procesa un audio para ver resultados.")
except Exception as e:
    st.warning(f"No se pudieron cargar los registros. Comienza procesando una llamada. Detalle: {e}")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("""
<div style="text-align:center; color:#374151; font-size:0.8rem; margin-top:3rem; padding-top:1.5rem;
    border-top: 1px solid rgba(255,255,255,0.05);">
    Speech Analytics MVP · GCP · Cloud Run · BigQuery · Vertex AI · Cloud Speech-to-Text v2
</div>
""", unsafe_allow_html=True)
