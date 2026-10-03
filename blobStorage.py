import os

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError, ServiceRequestError
from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv

load_dotenv(override=True)

_container = None
STORAGE_DOWN = (
    "Azure storage is not responding. "
    "Saved worksheets may be unavailable. Try again in a few minutes."
)


def _env_text(name):
    return (os.getenv(name) or "").strip().strip("'\"")


def _on_railway():
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT")
        or os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_SERVICE_ID")
    )


def container_name():
    # Railway sets RAILWAY_ENVIRONMENT on the deployed service, so that
    # process uses the production container. A laptop uses the dev one.
    # AZURE_STORAGE_CONTAINER_NAME still works when the matching name is unset.
    if _on_railway():
        name = _env_text("AZURE_STORAGE_PROD_CONTAINER")
    else:
        name = _env_text("AZURE_STORAGE_DEV_CONTAINER")
    return name or _env_text("AZURE_STORAGE_CONTAINER_NAME")


def _service_client():
    connection_string = _env_text("AZURE_STORAGE_CONNECTION_STRING")
    if not connection_string:
        raise RuntimeError("Worksheet storage is not configured.")
    return BlobServiceClient.from_connection_string(connection_string)


def client_for(name):
    if not name:
        raise RuntimeError("Worksheet storage is not configured.")
    return _service_client().get_container_client(name)


def _container_client():
    global _container
    if _container is None:
        _container = client_for(container_name())
    return _container


def worksheet_key(teacher_id, sheet_id):
    return f"teacher_{teacher_id}/{sheet_id}.pdf"


def _blob_error(exc):
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status in (401, 403) or "not authorized" in text or "unauthorized" in text:
        return (
            "Could not sign in to Azure storage. "
            "Ask an admin to check the storage connection."
        )
    if status in (408, 504) or "timed out" in text or "timeout" in text:
        return "Azure storage took too long to respond. Try again in a few minutes."
    return STORAGE_DOWN


def upload_pdf(key, pdf_bytes):
    # overwrite=True: a retried save for the same sheet writes the same blob
    # instead of failing, so the key stays one-to-one with the row.
    try:
        _container_client().upload_blob(
            key,
            pdf_bytes,
            overwrite=True,
            content_settings=ContentSettings(content_type="application/pdf"),
        )
    except Exception as exc:
        raise RuntimeError(_blob_error(exc)) from exc


def download_pdf(key):
    try:
        return _container_client().download_blob(key).readall()
    except ResourceNotFoundError:
        raise
    except Exception as exc:
        raise RuntimeError(_blob_error(exc)) from exc


def delete_pdf(key):
    try:
        _container_client().delete_blob(key)
    except ResourceNotFoundError:
        pass
    except (HttpResponseError, ServiceRequestError) as exc:
        raise RuntimeError(_blob_error(exc)) from exc
