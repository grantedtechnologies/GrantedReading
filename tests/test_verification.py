"""Unverified accounts stay pending, and a failed send is reported."""
import app as app_module


def _client(monkeypatch):
    monkeypatch.setattr(app_module.db, "list_worksheets_for_teacher", lambda teacher_id: [])
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _signup_body(**overrides):
    body = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@x.test",
        "password": "Pass1!",
        "password_confirm": "Pass1!",
        "membership": "standard",
    }
    body.update(overrides)
    return body


def test_signup_reports_when_the_confirmation_email_does_not_send(monkeypatch):
    monkeypatch.setattr(app_module.db, "find_teacher_by_email", lambda email: None)
    monkeypatch.setattr(
        app_module.db,
        "create_teacher",
        lambda *args: {
            "teacher_id": 9,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@x.test",
            "verified": 0,
        },
    )
    monkeypatch.setattr(app_module, "_issue_verification", lambda *args: False)
    with _client(monkeypatch) as client:
        response = client.post("/signup", json=_signup_body())
    data = response.get_json()
    assert response.status_code == 503
    assert data["email_sent"] is False
    assert "could not send" in data["error"].lower()


def test_unverified_signup_can_be_retried(monkeypatch):
    existing = {
        "teacher_id": 9,
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@x.test",
        "verified": 0,
        "password": "Old1!",
    }
    refreshed = dict(existing)
    calls = []

    def refresh(teacher_id, first_name, last_name, password):
        calls.append((teacher_id, first_name, last_name, password))
        refreshed["password"] = password
        return refreshed

    monkeypatch.setattr(app_module.db, "find_teacher_by_email", lambda email: existing)
    monkeypatch.setattr(app_module.db, "refresh_unverified_teacher", refresh)
    monkeypatch.setattr(app_module, "_issue_verification", lambda *args: True)
    with _client(monkeypatch) as client:
        response = client.post("/signup", json=_signup_body())
    data = response.get_json()
    assert response.status_code == 200
    assert data["email_sent"] is True
    assert calls == [(9, "Ada", "Lovelace", "Pass1!")]


def test_verified_email_cannot_be_signed_up_again(monkeypatch):
    monkeypatch.setattr(
        app_module.db,
        "find_teacher_by_email",
        lambda email: {
            "teacher_id": 9,
            "email": "ada@x.test",
            "verified": 1,
            "password": "Pass1!",
        },
    )
    with _client(monkeypatch) as client:
        response = client.post("/signup", json=_signup_body())
    assert response.status_code == 409
    assert "already in use" in response.get_json()["error"].lower()


def test_login_reports_when_the_confirmation_email_does_not_send(monkeypatch):
    monkeypatch.setattr(
        app_module.db,
        "find_teacher_by_email",
        lambda email: {
            "teacher_id": 9,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@x.test",
            "password": "Pass1!",
            "verified": 0,
        },
    )
    monkeypatch.setattr(app_module, "_issue_verification", lambda *args: False)
    with _client(monkeypatch) as client:
        response = client.post("/login", json={"email": "ada@x.test", "password": "Pass1!"})
    data = response.get_json()
    assert response.status_code == 503
    assert data["email_sent"] is False
    assert "could not send" in data["error"].lower()


def test_issue_verification_returns_false_when_mailtrap_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("mailtrap down")

    monkeypatch.setattr(app_module.requests, "post", boom)
    monkeypatch.setitem(app_module.app.config, "MAIL_PASSWORD", "token")
    with app_module.app.app_context():
        sent = app_module._issue_verification(
            {"email": "ada@x.test", "first_name": "Ada"},
            "http://127.0.0.1:5000/",
        )
    assert sent is False


def test_verification_email_posts_to_mailtrap_api(monkeypatch):
    captured = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        return FakeResponse()

    monkeypatch.setattr(app_module.requests, "post", fake_post)
    monkeypatch.setitem(app_module.app.config, "MAIL_PASSWORD", "token")
    monkeypatch.setitem(
        app_module.app.config,
        "MAIL_DEFAULT_SENDER",
        "Granted Reading <from@example.com>",
    )
    with app_module.app.app_context():
        sent = app_module._issue_verification(
            {"email": "ada@x.test", "first_name": "Ada"},
            "http://127.0.0.1:5000/",
        )
    assert sent is True
    assert captured[0]["url"] == "https://send.api.mailtrap.io/api/send"
    assert captured[0]["headers"]["Authorization"] == "Bearer token"
    assert captured[0]["timeout"] == 15
    payload = captured[0]["json"]
    assert payload["from"] == {"email": "from@example.com", "name": "Granted Reading"}
    assert payload["to"] == [{"email": "ada@x.test"}]
    assert "Confirm your Granted Reading" in payload["subject"]
    assert "http://127.0.0.1:5000/verify?token=" in payload["text"]
