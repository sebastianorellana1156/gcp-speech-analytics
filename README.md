# 🎙️ Speech Analytics — Banca GCP

Pipeline completo de análisis de llamadas bancarias sobre Google Cloud Platform. Extrae insights de negocio seguros (PII enmascarada) desde grabaciones de voz, usando IA generativa y almacenamiento en BigQuery. Construido como MVP técnico para demostrar capacidades como Ingeniero de IA y Cloud.

---

## 🏗️ Arquitectura

```
[Audio .wav]
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CLOUD RUN (Streamlit)                         │
│                                                                  │
│  1. Upload ──────────► Cloud Storage (GCS)                      │
│  2. Transcribe ──────► Speech-to-Text v2 (chirp, es-CL)        │
│                         + Speaker Diarization (Agente/Cliente)  │
│  3. Redact PII ──────► Cloud DLP                                │
│                         (CHILE_RUT, CREDIT_CARD, EMAIL, PHONE)  │
│  4. Extract Insights ► Vertex AI / Gemini 1.5 Flash             │
│                         (intención, sentimiento, churn, resumen)│
│  5. Store ───────────► BigQuery (particionado por mes)          │
└─────────────────────────────────────────────────────────────────┘

Auth: Workload Identity Federation (sin archivos JSON)
IAM:  Service Account con roles mínimos necesarios
IaC:  Terraform (GCS, BQ, Artifact Registry, SA, Cloud Run)
```

**Servicios GCP utilizados:**

| Servicio | Rol en el proyecto |
|---|---|
| **Cloud Run** | Hosting de la app Streamlit (contenedor Docker) |
| **Cloud Storage** | Almacenamiento temporal de archivos de audio |
| **Speech-to-Text v2** | Transcripción con diarización de hablantes en es-CL |
| **Cloud DLP** | Detección y enmascaramiento de PII (RUT, tarjeta, email) |
| **Vertex AI (Gemini)** | Extracción de intención, sentimiento, churn y resumen |
| **BigQuery** | Almacén analítico particionado por mes |
| **Artifact Registry** | Repositorio Docker para la imagen del servicio |
| **Workload Identity** | Autenticación segura sin claves JSON |

---

## 🗄️ Esquema de Datos BigQuery

Tabla: `call_analytics_dataset.call_transcriptions` (particionada por mes en `created_at`)

| Columna | Tipo | Descripción |
|---|---|---|
| `call_id` | STRING (REQUIRED) | UUID único generado por el pipeline |
| `timestamp` | TIMESTAMP (REQUIRED) | Momento simulado de la llamada |
| `audio_filename` | STRING | Nombre del archivo de audio procesado |
| `transcript_redacted` | STRING | Transcripción completa con PII enmascarada |
| `call_intent` | STRING | Intención detectada por Gemini |
| `customer_sentiment` | STRING | Positivo, Neutro o Negativo |
| `churn_risk` | BOOL | Riesgo de abandono detectado |
| `summary` | STRING | Resumen de 2-3 oraciones generado por Gemini |
| `processing_duration_seconds` | FLOAT | Duración total del pipeline en segundos |
| `speech_confidence_score` | FLOAT | Score de confianza STT (0.0–1.0) |
| `dlp_findings_count` | INTEGER | Cantidad de hallazgos PII censurados |
| `gemini_model_used` | STRING | Modelo Gemini utilizado (ej: gemini-1.5-flash) |
| `pipeline_status` | STRING | SUCCESS o PARTIAL_FAILURE |
| `created_at` | TIMESTAMP (REQUIRED) | Timestamp de inserción (campo de particionamiento) |
| `updated_at` | TIMESTAMP | Timestamp de última actualización |

---

## 📋 Prerrequisitos

- [gcloud CLI](https://cloud.google.com/sdk/docs/install) instalado y autenticado
- [Terraform](https://developer.hashicorp.com/terraform/downloads) >= 1.5
- [Docker](https://www.docker.com/) instalado
- Python 3.10+
- Proyecto GCP con **billing habilitado**

---

## ⚡ APIs de GCP a Habilitar

```bash
gcloud services enable \
  speech.googleapis.com \
  dlp.googleapis.com \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  texttospeech.googleapis.com
```

---

## 🚀 Despliegue Paso a Paso

### 1. Autenticación

```bash
gcloud auth application-default login
gcloud config set project gcp-speech-analytics
```

### 2. Habilitar APIs

```bash
gcloud services enable \
  speech.googleapis.com dlp.googleapis.com aiplatform.googleapis.com \
  bigquery.googleapis.com storage.googleapis.com run.googleapis.com \
  artifactregistry.googleapis.com iam.googleapis.com texttospeech.googleapis.com
```

### 3. Infraestructura con Terraform

```bash
cd terraform
terraform init
terraform apply -var="project_id=gcp-speech-analytics"
```

### 4. Generar Audios de Muestra

> [!NOTE]
> **Nota para Cloud Shell:** Si usas Cloud Shell, es posible que la API de Text-to-Speech requiera configurar un proyecto de cuota para las credenciales locales de ADC. Si obtienes un error `403`, ejecuta primero `gcloud auth application-default set-quota-project gcp-speech-analytics` o simplemente omite este paso; el despliegue Docker funcionará igual sin los audios locales.

```bash
# Requiere autenticación GCP activa
python sample_audios/generate_sample_audios.py
```

Los 5 archivos `.wav` quedarán en `sample_audios/`. Están excluidos del git por el `.gitignore`.

### 5. Ejecutar Localmente

```bash
export GCP_PROJECT_ID=gcp-speech-analytics
export GCS_BUCKET_NAME=speech-analytics-gcp-speech-analytics
export BQ_DATASET_ID=call_analytics_dataset
export BQ_TABLE_ID=call_transcriptions
export GEMINI_MODEL=gemini-1.5-flash

cd src
streamlit run app.py
# Abrir http://localhost:8501
```

### 6. Build y Push de la Imagen Docker

Asegúrate de ejecutar estos comandos desde la **raíz del proyecto**, no desde adentro de la carpeta `src` ni `terraform`:

```bash
# Configurar Docker para Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev

# Build (el punto . al final y el uso de -f indica que tome todo el contexto de la raíz)
docker build -t us-central1-docker.pkg.dev/gcp-speech-analytics/speech-analytics-repo/speech-analytics-app:latest -f src/Dockerfile .

# Push
docker push us-central1-docker.pkg.dev/gcp-speech-analytics/speech-analytics-repo/speech-analytics-app:latest
```

### 7. Deploy en Cloud Run

```bash
gcloud run deploy speech-analytics-app \
  --image us-central1-docker.pkg.dev/gcp-speech-analytics/speech-analytics-repo/speech-analytics-app:latest \
  --region us-central1 \
  --platform managed \
  --allow-unauthenticated \
  --service-account speech-analytics-sa@gcp-speech-analytics.iam.gserviceaccount.com \
  --set-env-vars GCP_PROJECT_ID=gcp-speech-analytics,GCS_BUCKET_NAME=speech-analytics-gcp-speech-analytics,BQ_DATASET_ID=call_analytics_dataset,BQ_TABLE_ID=call_transcriptions,GEMINI_MODEL=gemini-1.5-flash \
  --memory 2Gi \
  --cpu 2 \
  --port 8080
```

---

## 🔧 Variables de Entorno

| Variable | Descripción | Ejemplo |
|---|---|---|
| `GCP_PROJECT_ID` | ID del proyecto GCP | `gcp-speech-analytics` |
| `GCS_BUCKET_NAME` | Nombre del bucket GCS para audios | `speech-analytics-gcp-speech-analytics` |
| `BQ_DATASET_ID` | Dataset de BigQuery | `call_analytics_dataset` |
| `BQ_TABLE_ID` | Tabla de BigQuery | `call_transcriptions` |
| `GEMINI_MODEL` | Modelo Gemini a utilizar | `gemini-1.5-flash` |

---

## 📁 Estructura del Proyecto

```
/
├── terraform/
│   ├── main.tf          # GCS, BQ, Artifact Registry, SA, IAM, Cloud Run
│   └── variables.tf     # Variables con defaults
├── src/
│   ├── app.py           # Interfaz Streamlit (4 bloques de UI)
│   ├── gcp_services.py  # Upload GCS, STT v2, DLP, Gemini
│   ├── bq_client.py     # Insert y truncate BigQuery
│   └── Dockerfile       # Imagen python:3.10-slim
├── sample_audios/
│   └── generate_sample_audios.py  # TTS: 5 escenarios bancarios es-CL
├── .gitignore
├── README.md
└── requirements.txt
```

---

## 🏷️ Historial de Commits

```
1. chore: initial project structure and .gitignore
2. infra: add terraform for GCS, BQ, Artifact Registry, IAM and Workload Identity
3. chore: add Dockerfile and requirements.txt
4. feat(sample_audios): add TTS script to generate 5 banking call samples in es-CL
5. feat(bq_client): implement BigQuery insert and truncate module
6. feat(gcp_services): implement Speech-to-Text v2 with speaker diarization
7. feat(gcp_services): add DLP redaction pipeline for CHILE_RUT, CREDIT_CARD and EMAIL
8. feat(gcp_services): add Vertex AI insight extraction with Gemini
9. feat(app): build Streamlit UI with session state and progress tracking
10. docs: complete README with architecture and deployment guide
```

---

## 🎯 Escenarios de Llamadas de Muestra

| Archivo | Escenario | Sentimiento | Churn Risk |
|---|---|---|---|
| `Llamada_01_ReclamoFraude.wav` | Cliente reporta cargo no reconocido | Negativo | ✅ Sí |
| `Llamada_02_ConsultaSaldo.wav` | Consulta de saldo y cartola | Neutro | ❌ No |
| `Llamada_03_SolicitudPrestamo.wav` | Solicitud de crédito de consumo | Positivo | ❌ No |
| `Llamada_04_BloqueoTarjeta.wav` | Bloqueo urgente por robo | Negativo | ❌ No |
| `Llamada_05_CancelacionCuenta.wav` | Cierre de cuenta por comisiones | Negativo | ✅ Sí |
