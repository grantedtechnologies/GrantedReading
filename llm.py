"""Azure Foundry LLM client — text and JSON only (no Flux, no leveling)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading

from azure.ai.projects import AIProjectClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ServiceRequestError
from azure.identity import ClientSecretCredential
from dotenv import load_dotenv
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

load_dotenv(override=True)

logger = logging.getLogger(__name__)

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "").rstrip("/")
LLM_AGENT_NAME = os.getenv("LLM_AGENT_NAME", "")
LLM_MODEL = (os.getenv("LLM_MODEL") or "").strip()

FOUNDRY_AUTH_VARS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")

FOUNDRY_DOWN = (
    "Azure Foundry (the writing assistant) is not responding. "
    "It may be down. Try again in a few minutes."
)
FLUX_DOWN = (
    "Azure image generation is not responding. "
    "The picture service may be down. Try again in a few minutes."
)
STORAGE_DOWN = (
    "Azure storage is not responding. "
    "Saved worksheets may be unavailable. Try again in a few minutes."
)
GENERIC_RETRY = "Something went wrong. Try again in a few minutes."

_RESOURCE_DOWN = {
    "foundry": FOUNDRY_DOWN,
    "flux": FLUX_DOWN,
    "storage": STORAGE_DOWN,
}
_RESOURCE_AUTH = {
    "foundry": (
        "Could not sign in to Azure Foundry. "
        "Ask an admin to check the writing assistant credentials."
    ),
    "flux": (
        "Could not sign in to Azure image generation. "
        "Ask an admin to check the picture service credentials."
    ),
    "storage": (
        "Could not sign in to Azure storage. "
        "Ask an admin to check the storage connection."
    ),
}
_RESOURCE_TIMEOUT = {
    "foundry": "Azure Foundry took too long to respond. Try again in a few minutes.",
    "flux": "Azure image generation took too long to respond. Try again in a few minutes.",
    "storage": "Azure storage took too long to respond. Try again in a few minutes.",
}
_RESOURCE_BUSY = "Azure is busy right now. Wait a minute and try again."
_RESOURCE_NOT_FOUND = {
    "foundry": (
        "Azure Foundry could not find the writing assistant. "
        "Ask an admin to check the Foundry project."
    ),
    "flux": (
        "Azure could not find the image model. "
        "Ask an admin to check image generation setup."
    ),
    "storage": "That worksheet file was not found in Azure storage.",
}


def _llm_endpoint():
    endpoint = (os.getenv("LLM_ENDPOINT") or LLM_ENDPOINT).rstrip("/")
    last = endpoint.rsplit("/", 1)[-1]
    if not endpoint or last in ("projects", "api"):
        raise RuntimeError(
            "LLM_ENDPOINT must include the Foundry project name, for example "
            "https://<resource>.services.ai.azure.com/api/projects/<project-name>."
        )
    return endpoint


def require_foundry_config():
    missing = [
        name for name in (*FOUNDRY_AUTH_VARS, "LLM_ENDPOINT")
        if not (os.environ.get(name) or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )
    _llm_endpoint()


def foundry_credential():
    require_foundry_config()
    return ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"].strip(),
        client_id=os.environ["AZURE_CLIENT_ID"].strip(),
        client_secret=os.environ["AZURE_CLIENT_SECRET"].strip(),
    )


def ensure_ai_configured():
    """Require Foundry + Flux env so worksheet generation can finish end-to-end."""
    missing = []
    for name in FOUNDRY_AUTH_VARS:
        if not (os.environ.get(name) or "").strip():
            missing.append(name)
    if not (os.getenv("FLUX_API_KEY") or "").strip():
        missing.append("FLUX_API_KEY")
    if not (os.environ.get("LLM_ENDPOINT") or LLM_ENDPOINT).strip():
        missing.append("LLM_ENDPOINT")
    if not LLM_AGENT_NAME:
        missing.append("LLM_AGENT_NAME")
    if not (os.getenv("FLUX_API_URL") or "").strip():
        missing.append("FLUX_API_URL")
    if not (os.getenv("FLUX_MODEL") or "").strip():
        missing.append("FLUX_MODEL")
    if missing:
        logger.error("AI is not configured; missing %s", ", ".join(missing))
        raise RuntimeError(
            "The writing assistant is not set up yet. Ask an admin to finish Azure setup."
        )
    _llm_endpoint()


def _error_text(exc) -> str:
    if exc is None:
        return ""
    if isinstance(exc, dict):
        nested = exc.get("error")
        if isinstance(nested, dict):
            return str(nested.get("message") or nested.get("code") or "")
        if isinstance(nested, str):
            return nested
        return str(exc.get("message") or "")
    if isinstance(exc, str):
        return exc
    return str(exc or "")


def _error_status(exc):
    if isinstance(exc, dict):
        nested = exc.get("error") if isinstance(exc.get("error"), dict) else {}
        for key in ("status", "status_code", "statusCode", "code"):
            value = exc.get(key)
            if value is None:
                value = nested.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _looks_like_dump(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    if len(stripped) > 280:
        return True
    lowered = stripped.lower()
    if stripped[:1] in "{[":
        return True
    if "traceback" in lowered or "httpresponseerror" in lowered:
        return True
    if "requestid" in lowered or "request id" in lowered:
        return True
    if "aadsts" in lowered or "error_description" in lowered:
        return True
    if '"error"' in lowered or "'error'" in lowered:
        return True
    if "has no attribute" in lowered or "object is not" in lowered:
        return True
    return False


def _looks_technical(text: str) -> bool:
    lowered = (text or "").lower()
    return any(
        needle in lowered
        for needle in (
            "has no attribute",
            "object is not",
            "nonetype",
            "keyerror",
            "typeerror",
            "indexerror",
            "attributeerror",
        )
    )


def user_error_message(exc, resource="foundry") -> str:
    """One short sentence for the teacher. Never includes JSON or Azure dumps."""
    kind = resource if resource in _RESOURCE_DOWN else "foundry"
    text = _error_text(exc)
    status = _error_status(exc)
    if isinstance(exc, dict):
        try:
            lowered = json.dumps(exc).lower()
        except TypeError:
            lowered = text.lower()
    else:
        lowered = text.lower()
        known_azure = (
            ClientAuthenticationError,
            HttpResponseError,
            ServiceRequestError,
            RequestsConnectionError,
            RequestsTimeout,
            TimeoutError,
            ConnectionError,
        )
        if (
            isinstance(exc, Exception)
            and not isinstance(exc, known_azure)
            and text
            and not _looks_like_dump(text)
            and not _looks_technical(text)
        ):
            return text

    if (
        isinstance(exc, ClientAuthenticationError)
        or status in (401, 403)
        or "unauthorized" in lowered
        or "authentication" in lowered
        or "aadsts" in lowered
        or "invalid_client" in lowered
        or "access denied" in lowered
        or "not authorized" in lowered
    ):
        return _RESOURCE_AUTH[kind]

    if (
        isinstance(exc, (TimeoutError, RequestsTimeout))
        or status in (408, 504)
        or "timed out" in lowered
        or "timeout" in lowered
    ):
        return _RESOURCE_TIMEOUT[kind]

    if status == 429 or "too many requests" in lowered or "rate limit" in lowered:
        return _RESOURCE_BUSY

    if status == 404 or "deploymentnotfound" in lowered or "resourcenotfound" in lowered:
        return _RESOURCE_NOT_FOUND[kind]

    if (
        isinstance(exc, (HttpResponseError, ServiceRequestError, RequestsConnectionError))
        or status in (500, 502, 503)
        or "connection" in lowered
        or "temporarily unavailable" in lowered
        or "service unavailable" in lowered
        or "foundry" in lowered
        or "azure" in lowered
        or "flux" in lowered
        or "blob" in lowered
    ):
        return _RESOURCE_DOWN[kind]

    return GENERIC_RETRY


_client_lock = threading.Lock()
_project_client = None
_openai_client = None
_structured_client = None
_structured_model = None


def _get_openai_client():
    global _project_client, _openai_client
    if _openai_client is not None:
        return _project_client, _openai_client

    with _client_lock:
        if _openai_client is not None:
            return _project_client, _openai_client

        endpoint = _llm_endpoint()
        logger.info("Azure project endpoint: %s", endpoint)
        _project_client = AIProjectClient(
            endpoint=endpoint,
            credential=foundry_credential(),
            allow_preview=True,
        )
        _openai_client = _project_client.get_openai_client(
            agent_name=LLM_AGENT_NAME,
            timeout=None,
        )
        return _project_client, _openai_client


def _get_structured_client():
    global _structured_client
    project, _ = _get_openai_client()
    if _structured_client is not None:
        return _structured_client
    with _client_lock:
        if _structured_client is None:
            _structured_client = project.get_openai_client(timeout=None)
        return _structured_client


def _structured_model_name():
    global _structured_model
    if LLM_MODEL:
        return LLM_MODEL
    if _structured_model:
        return _structured_model
    with _client_lock:
        if _structured_model:
            return _structured_model
        project, _ = _get_openai_client()
        details = project.agents.get(agent_name=LLM_AGENT_NAME)
        payload = details.as_dict() if hasattr(details, "as_dict") else dict(details)
        model = (
            payload.get("versions", {})
            .get("latest", {})
            .get("definition", {})
            .get("model")
        )
        if not model:
            raise RuntimeError(
                "Could not find the writing model on the Foundry agent. "
                "Set LLM_MODEL to the deployment name."
            )
        _structured_model = str(model)
        logger.info("Worksheet JSON model: %s", _structured_model)
        return _structured_model


def generate_text(prompt, system=None):
    """Free-text model call (rewrites, open-ended writing)."""
    ensure_ai_configured()
    content = prompt
    if system:
        content = f"{system.strip()}\n\n{prompt}"
    try:
        _, openai_client = _get_openai_client()
        response = openai_client.responses.create(input=content)
    except Exception as exc:
        raise RuntimeError(user_error_message(exc, "foundry")) from exc
    text = response.output_text
    if text is None or not str(text).strip():
        raise RuntimeError(FOUNDRY_DOWN)
    return text


def _parse_json_object(raw_text) -> dict | None:
    if raw_text is None or not str(raw_text).strip():
        return None
    text = str(raw_text).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def generate_json(prompt, schema, *, name="response", system=None) -> dict:
    """Enforced JSON via chat.completions response_format (strict schema)."""
    ensure_ai_configured()
    messages = [
        {
            "role": "system",
            "content": system
            or "Return only valid JSON that matches the required schema.",
        },
        {"role": "user", "content": prompt},
    ]
    try:
        client = _get_structured_client()
        response = client.chat.completions.create(
            model=_structured_model_name(),
            messages=messages,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": name,
                    "strict": True,
                    "schema": schema,
                },
            },
        )
    except Exception as exc:
        raise RuntimeError(user_error_message(exc, "foundry")) from exc
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise RuntimeError(FOUNDRY_DOWN) from exc
    data = _parse_json_object(content)
    if data is not None:
        return data
    raise RuntimeError(
        "The writing assistant returned a response that could not be read. "
        "Try generating again."
    )
