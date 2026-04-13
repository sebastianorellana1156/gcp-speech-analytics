variable "project_id" {
  description = "GCP Project ID (required)"
  type        = string
  # No default — must be provided explicitly
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "gcs_bucket_name" {
  description = "GCS bucket name for audio uploads (must be globally unique)"
  type        = string
  default     = "speech-analytics-gcp-speech-analytics"
}

variable "bq_dataset_id" {
  description = "BigQuery dataset ID"
  type        = string
  default     = "call_analytics_dataset"
}

variable "bq_table_id" {
  description = "BigQuery table ID for call transcriptions"
  type        = string
  default     = "call_transcriptions"
}

variable "artifact_registry_repo" {
  description = "Artifact Registry Docker repository name"
  type        = string
  default     = "speech-analytics-repo"
}

variable "cloud_run_service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "speech-analytics-app"
}

variable "service_account_name" {
  description = "Service Account name for the application"
  type        = string
  default     = "speech-analytics-sa"
}
