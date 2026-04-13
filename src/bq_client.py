"""
Módulo de cliente BigQuery para el MVP Speech Analytics.

Provee funciones para insertar registros de llamadas procesadas y truncar la tabla,
siguiendo el esquema exacto definido en la especificación del proyecto (sección 6).
"""

import uuid
from datetime import datetime, timezone

from google.cloud import bigquery


def insert_call_record(
    record: dict,
    project_id: str,
    dataset_id: str,
    table_id: str,
) -> None:
    """
    Inserta un registro completo de llamada procesada en BigQuery.

    Construye la fila con todos los campos del esquema (sección 6), generando
    automáticamente call_id (UUID4), timestamps de auditoría y campos de metadata.

    Args:
        record: Diccionario con los datos procesados del pipeline. Espera las
                siguientes claves (todas opcionales excepto audio_filename):
                - audio_filename (str)
                - transcript_redacted (str)
                - call_intent (str)
                - customer_sentiment (str)
                - churn_risk (bool)
                - summary (str)
                - processing_duration_seconds (float)
                - speech_confidence_score (float)
                - dlp_findings_count (int)
                - gemini_model_used (str)
                - pipeline_status (str) — 'SUCCESS' o 'PARTIAL_FAILURE'
        project_id: ID del proyecto GCP.
        dataset_id: ID del dataset de BigQuery.
        table_id: ID de la tabla de BigQuery.

    Raises:
        RuntimeError: Si BigQuery reporta errores durante la inserción.
    """
    client = bigquery.Client(project=project_id)
    table_ref = f"{project_id}.{dataset_id}.{table_id}"

    now_utc = datetime.now(timezone.utc).isoformat()

    row = {
        # --- Datos de la llamada ---
        "call_id":          str(uuid.uuid4()),
        "timestamp":        now_utc,
        "audio_filename":   record.get("audio_filename"),

        # --- Contenido procesado ---
        "transcript_redacted":  record.get("transcript_redacted"),
        "call_intent":          record.get("call_intent"),
        "customer_sentiment":   record.get("customer_sentiment"),
        "churn_risk":           record.get("churn_risk"),
        "summary":              record.get("summary"),

        # --- Métricas del pipeline ---
        "processing_duration_seconds":  record.get("processing_duration_seconds"),
        "speech_confidence_score":      record.get("speech_confidence_score"),
        "dlp_findings_count":           record.get("dlp_findings_count"),
        "gemini_model_used":            record.get("gemini_model_used"),
        "pipeline_status":              record.get("pipeline_status", "SUCCESS"),

        # --- Auditoría ---
        "created_at":   now_utc,
        "updated_at":   now_utc,
    }

    errors = client.insert_rows_json(table_ref, [row])

    if errors:
        raise RuntimeError(
            f"Error insertando registro en BigQuery ({table_ref}): {errors}"
        )


def truncate_table(project_id: str, dataset_id: str, table_id: str) -> str:
    """
    Elimina todos los registros de la tabla call_transcriptions.

    Ejecuta un DELETE FROM ... WHERE TRUE para limpiar la tabla completa.
    Útil para el botón de reseteo de la interfaz Streamlit.

    Args:
        project_id: ID del proyecto GCP.
        dataset_id: ID del dataset de BigQuery.
        table_id: ID de la tabla a truncar.

    Returns:
        Mensaje de confirmación con el número de filas eliminadas.

    Raises:
        RuntimeError: Si la consulta falla en BigQuery.
    """
    client = bigquery.Client(project=project_id)

    query = f"DELETE FROM `{project_id}.{dataset_id}.{table_id}` WHERE TRUE"

    try:
        query_job = client.query(query)
        query_job.result()  # Espera a que la operación termine
        return f"✅ Tabla {table_id} vaciada correctamente."
    except Exception as e:
        raise RuntimeError(f"Error truncando tabla {table_id}: {e}") from e

def get_top_records(project_id: str, dataset_id: str, table_id: str, limit: int = 10) -> list[dict]:
    """
    Obtiene los registros más recientes de BigQuery.
    """
    client = bigquery.Client(project=project_id)
    query = f"""
        SELECT *
        FROM `{project_id}.{dataset_id}.{table_id}`
        ORDER BY timestamp DESC
        LIMIT {limit}
    """
    try:
        query_job = client.query(query)
        return [dict(row) for row in query_job.result()]
    except Exception as e:
        raise RuntimeError(f"Error obteniendo registros: {e}") from e
