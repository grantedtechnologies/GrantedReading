"""Teacher-facing Azure errors stay short and never include JSON dumps."""
from types import SimpleNamespace

from azure.core.exceptions import HttpResponseError
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

import ai


def test_human_sentences_pass_through():
    text = "Please select a reading level."
    assert ai.user_error_message(ValueError(text)) == text


def test_json_dump_is_not_returned():
    raw = '{"error":{"code":"DeploymentNotFound","message":"flux exploded","requestId":"abc"}}'
    msg = ai.user_error_message(RuntimeError(raw), "flux")
    assert "{" not in msg
    assert "requestId" not in msg
    assert "exploded" not in msg
    assert "picture" in msg.lower() or "image" in msg.lower()


def test_foundry_http_failure_mentions_writing_assistant():
    exc = HttpResponseError(message='(ServiceUnavailable) {"error":{"code":"ServiceUnavailable"}}')
    exc.status_code = 503
    msg = ai.user_error_message(exc, "foundry")
    assert "{" not in msg
    assert "Foundry" in msg
    assert "down" in msg.lower()


def test_flux_dict_auth_failure_mentions_credentials():
    msg = ai.user_error_message(
        {"error": {"code": "Unauthorized", "message": "invalid api key"}, "status_code": 401},
        "flux",
    )
    assert "{" not in msg
    assert "sign in" in msg.lower()
    assert "image" in msg.lower()


def test_storage_timeout():
    msg = ai.user_error_message(RequestsTimeout("timed out"), "storage")
    assert "{" not in msg
    assert "storage" in msg.lower()
    assert "too long" in msg.lower()


def test_connection_error_says_foundry_may_be_down():
    msg = ai.user_error_message(RequestsConnectionError("connection refused"), "foundry")
    assert "Foundry" in msg
    assert "down" in msg.lower()


def test_process_ram_mb_is_a_whole_number():
    mb = ai.process_ram_mb()
    assert isinstance(mb, int)
    assert mb >= 1


def test_busy_is_not_a_dump():
    exc = SimpleNamespace(status_code=429)
    msg = ai.user_error_message(exc, "foundry")
    assert "busy" in msg.lower()
    assert "{" not in msg


def test_generate_route_hides_azure_json(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module.db, "find_teacher_by_id", lambda teacher_id: {
        "teacher_id": 1,
        "first_name": "A",
        "last_name": "B",
        "verified": True,
    })
    monkeypatch.setattr(app_module.db, "list_worksheets_for_teacher", lambda teacher_id: [])
    monkeypatch.setattr(app_module.billing, "is_teacher_pro", lambda teacher_id: True)

    def boom(*_args, **_kwargs):
        raise RuntimeError(
            'HttpResponseError: Azure Foundry {"error":{"code":"ServiceUnavailable","message":"nope"}}'
        )

    monkeypatch.setattr(app_module.ai, "generate_full_worksheet", boom)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["teacher_id"] = 1
        response = client.post("/generate", json={
            "grade": "3",
            "reading_level": "3",
            "interests": "dogs",
        })

    data = response.get_json()
    assert response.status_code == 500
    assert "raw_model_output" not in data
    assert "{" not in data["error"]
    assert "nope" not in data["error"]
    assert "Foundry" in data["error"]
