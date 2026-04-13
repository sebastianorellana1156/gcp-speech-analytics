terraform {
  backend "local" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  required_version = ">= 1.5"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ============================================================
# Google Cloud Storage — Bucket temporal para audios
# ============================================================
resource "google_storage_bucket" "audio_bucket" {
  name                        = var.gcs_bucket_name
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true

  labels = {
    project = "speech-analytics"
    env     = "demo"
  }
}

# ============================================================
# BigQuery — Dataset
# ============================================================
resource "google_bigquery_dataset" "call_analytics" {
  dataset_id                 = var.bq_dataset_id
  location                   = var.region
  description                = "Dataset para análisis de llamadas del call center bancario"
  delete_contents_on_destroy = true

  labels = {
    project = "speech-analytics"
  }
}

# ============================================================
# BigQuery — Tabla call_transcriptions (schema de sección 6)
# ============================================================
resource "google_bigquery_table" "call_transcriptions" {
  dataset_id          = google_bigquery_dataset.call_analytics.dataset_id
  table_id            = var.bq_table_id
  deletion_protection = false

  description = "Tabla de transcripciones y métricas de llamadas bancarias procesadas"

  # Particionamiento mensual por created_at
  time_partitioning {
    type  = "MONTH"
    field = "created_at"
  }

  labels = {
    project = "speech-analytics"
  }

  schema = jsonencode([
    # --- Datos de la llamada ---
    {
      name        = "call_id"
      type        = "STRING"
      mode        = "REQUIRED"
      description = "Identificador único UUID generado en el pipeline"
    },
    {
      name        = "timestamp"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Momento en que ocurrió la llamada (simulado como hora de procesamiento en el MVP)"
    },
    {
      name        = "audio_filename"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Nombre del archivo de audio procesado"
    },

    # --- Contenido procesado ---
    {
      name        = "transcript_redacted"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Transcripción completa con PII enmascarada"
    },
    {
      name        = "call_intent"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Intención de la llamada detectada por Gemini"
    },
    {
      name        = "customer_sentiment"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Sentimiento del cliente: Positivo, Neutro o Negativo"
    },
    {
      name        = "churn_risk"
      type        = "BOOL"
      mode        = "NULLABLE"
      description = "Riesgo de abandono detectado por Gemini"
    },
    {
      name        = "summary"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Resumen de 2-3 oraciones generado por Gemini"
    },

    # --- Métricas del pipeline ---
    {
      name        = "processing_duration_seconds"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Segundos totales del pipeline completo (upload → insert)"
    },
    {
      name        = "speech_confidence_score"
      type        = "FLOAT"
      mode        = "NULLABLE"
      description = "Score promedio de confianza de Speech-to-Text (0.0 a 1.0)"
    },
    {
      name        = "dlp_findings_count"
      type        = "INTEGER"
      mode        = "NULLABLE"
      description = "Cantidad de hallazgos PII censurados por Cloud DLP"
    },
    {
      name        = "gemini_model_used"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Nombre exacto del modelo Gemini utilizado (ej: gemini-1.5-flash)"
    },
    {
      name        = "pipeline_status"
      type        = "STRING"
      mode        = "NULLABLE"
      description = "Estado final del pipeline: SUCCESS o PARTIAL_FAILURE"
    },

    # --- Auditoría ---
    {
      name        = "created_at"
      type        = "TIMESTAMP"
      mode        = "REQUIRED"
      description = "Momento exacto de inserción del registro en BigQuery (campo de particionamiento)"
    },
    {
      name        = "updated_at"
      type        = "TIMESTAMP"
      mode        = "NULLABLE"
      description = "Momento de última modificación del registro"
    }
  ])

  depends_on = [google_bigquery_dataset.call_analytics]
}

# ============================================================
# Artifact Registry — Repositorio Docker
# ============================================================
resource "google_artifact_registry_repository" "docker_repo" {
  location      = var.region
  repository_id = var.artifact_registry_repo
  description   = "Repositorio Docker para la imagen del servicio de speech analytics"
  format        = "DOCKER"

  labels = {
    project = "speech-analytics"
  }
}

# ============================================================
# Service Account
# ============================================================
resource "google_service_account" "app_sa" {
  account_id   = var.service_account_name
  display_name = "Speech Analytics Service Account"
  description  = "SA para el servicio Cloud Run de speech analytics con acceso a GCS, STT, DLP, Vertex AI y BigQuery"
}

# ============================================================
# IAM — Roles para la Service Account (sección 2 del spec)
# ============================================================
locals {
  sa_roles = [
    "roles/speech.client",
    "roles/dlp.user",
    "roles/aiplatform.user",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/storage.objectAdmin",
    "roles/run.invoker",
  ]
}

resource "google_project_iam_member" "sa_roles" {
  for_each = toset(local.sa_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# ============================================================
# Workload Identity — Para Cloud Run (permite que el servicio
# use la SA sin archivos de clave JSON)
# ============================================================
resource "google_service_account_iam_member" "workload_identity_cloudrun" {
  service_account_id = google_service_account.app_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.app_sa.email}"
}

# ============================================================
# Cloud Run — Servicio principal (imagen placeholder)
# Se actualiza con imagen real en el paso de deploy
# ============================================================
resource "google_cloud_run_v2_service" "app" {
  name     = var.cloud_run_service_name
  location = var.region

  template {
    service_account = google_service_account.app_sa.email

    containers {
      # Imagen placeholder — se reemplaza en el deploy real
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}/${var.cloud_run_service_name}:latest"

      ports {
        container_port = 8080
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCS_BUCKET_NAME"
        value = var.gcs_bucket_name
      }
      env {
        name  = "BQ_DATASET_ID"
        value = var.bq_dataset_id
      }
      env {
        name  = "BQ_TABLE_ID"
        value = var.bq_table_id
      }
      env {
        name  = "GEMINI_MODEL"
        value = "gemini-1.5-flash"
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }
    }

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
  }

  depends_on = [
    google_artifact_registry_repository.docker_repo,
    google_service_account.app_sa,
    google_project_iam_member.sa_roles,
  ]
}

# ============================================================
# Cloud Run — IAM: allow_unauthenticated = true (demo pública)
# ============================================================
resource "google_cloud_run_v2_service_iam_member" "allow_unauthenticated" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ============================================================
# Outputs útiles
# ============================================================
output "cloud_run_url" {
  description = "URL pública del servicio Cloud Run"
  value       = google_cloud_run_v2_service.app.uri
}

output "gcs_bucket_name" {
  description = "Nombre del bucket GCS para audios"
  value       = google_storage_bucket.audio_bucket.name
}

output "artifact_registry_url" {
  description = "URL del repositorio Docker en Artifact Registry"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}"
}

output "service_account_email" {
  description = "Email de la Service Account de la aplicación"
  value       = google_service_account.app_sa.email
}
