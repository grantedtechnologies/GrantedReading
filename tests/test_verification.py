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
        "school": "Lincoln Elementary",
        "country": "United States",
        "state": "Pennsylvania",
        "city": "Philadelphia",
        "district": "Metro District",
        "membership": "standard",
    }
    body.update(overrides)
    return body


def test_signup_requires_country_state_and_city(monkeypatch):
    created = []
    monkeypatch.setattr(app_module.db, "find_teacher_by_email", lambda email: None)
    monkeypatch.setattr(app_module.db, "create_teacher", lambda *args: created.append(args))
    body = _signup_body()
    body["country"] = ""
    with _client(monkeypatch) as client:
        response = client.post("/signup", json=body)
    assert response.status_code == 400
    assert "country" in response.get_json()["error"].lower()
    assert created == []


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

    def refresh(teacher_id, first_name, last_name, password, school, district, country, state, city):
        calls.append((teacher_id, first_name, last_name, password, school, district, country, state, city))
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
    assert calls == [(
        9, "Ada", "Lovelace", "Pass1!", "Lincoln Elementary", "Metro District",
        "United States", "Pennsylvania", "Philadelphia",
    )]


def test_unknown_pilot_code_does_not_create_an_account(monkeypatch):
    created = []
    monkeypatch.setattr(app_module.db, "find_teacher_by_email", lambda email: None)
    monkeypatch.setattr(app_module.db, "find_pilot_by_code", lambda code: None)
    monkeypatch.setattr(app_module.db, "create_teacher", lambda *args: created.append(args))
    monkeypatch.setattr(app_module.db, "create_teacher_with_pilot", lambda *args: created.append(args))
    with _client(monkeypatch) as client:
        response = client.post("/signup", json=_signup_body(membership="pilot", pilot_code="NOPE"))
    assert response.status_code == 400
    assert "does not exist" in response.get_json()["error"].lower()
    assert created == []


def test_expired_pilot_code_does_not_create_an_account(monkeypatch):
    created = []
    from datetime import datetime

    monkeypatch.setattr(app_module.db, "find_teacher_by_email", lambda email: None)
    monkeypatch.setattr(
        app_module.db,
        "find_pilot_by_code",
        lambda code: {
            "pilotid": 2,
            "name": "Spring",
            "startTime": datetime(2026, 1, 1),
            "endTime": datetime(2026, 6, 1),
        },
    )
    monkeypatch.setattr(app_module.db, "create_teacher_with_pilot", lambda *args: created.append(args))
    with _client(monkeypatch) as client:
        response = client.post("/signup", json=_signup_body(membership="pilot", pilot_code="SPRING2026"))
    assert response.status_code == 400
    assert "no longer active" in response.get_json()["error"].lower()
    assert created == []


def test_live_pilot_skips_payment_and_sends_confirmation(monkeypatch):
    from datetime import datetime, timedelta, timezone

    created = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    monkeypatch.setattr(app_module.db, "find_teacher_by_email", lambda email: None)
    monkeypatch.setattr(
        app_module.db,
        "find_pilot_by_code",
        lambda code: {
            "pilotid": 1,
            "name": "Riverside",
            "startTime": now - timedelta(days=1),
            "endTime": now + timedelta(days=30),
            "district_name": "Metro District",
            "district_state": "Pennsylvania",
        },
    )
    monkeypatch.setattr(
        app_module.db,
        "create_teacher_with_pilot",
        lambda *args: created.append(args) or {
            "teacher_id": 9,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@x.test",
            "verified": 0,
        },
    )
    monkeypatch.setattr(app_module, "_issue_verification", lambda *args: True)
    with _client(monkeypatch) as client:
        response = client.post("/signup", json=_signup_body(membership="pilot", pilot_code="RIVERSIDE2026"))
    data = response.get_json()
    assert response.status_code == 200
    assert data["email_sent"] is True
    assert data["needs_verification"] is True
    assert "checkout_url" not in data
    assert created and created[0][-1]["pilotid"] == 1


def test_pilot_code_rejects_a_teacher_outside_the_district(monkeypatch):
    from datetime import datetime, timedelta, timezone

    created = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    monkeypatch.setattr(app_module.db, "find_teacher_by_email", lambda email: None)
    monkeypatch.setattr(
        app_module.db,
        "find_pilot_by_code",
        lambda code: {
            "pilotid": 1,
            "name": "Riverside",
            "startTime": now - timedelta(days=1),
            "endTime": now + timedelta(days=30),
            "district_name": "Riverside Unified",
            "district_state": "California",
            "district_state_code": "CA",
        },
    )
    monkeypatch.setattr(app_module.db, "create_teacher_with_pilot", lambda *args: created.append(args))
    with _client(monkeypatch) as client:
        response = client.post("/signup", json=_signup_body(membership="pilot", pilot_code="RIVERSIDE2026"))
    assert response.status_code == 400
    assert "Riverside Unified" in response.get_json()["error"]
    assert created == []


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
    assert payload["category"] == "email_verification"


def test_unknown_email_still_says_a_reset_was_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(app_module.db, "find_teacher_by_email", lambda email: None)
    monkeypatch.setattr(app_module, "_issue_password_reset", lambda *args: sent.append(args))
    with _client(monkeypatch) as client:
        response = client.post("/forgot-password", json={"email": "missing@x.test"})
    assert response.status_code == 200
    assert response.get_json()["email_sent"] is True
    assert sent == []


def test_reset_email_uses_its_own_link(monkeypatch):
    captured = []

    class FakeResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.append(json)
        return FakeResponse()

    monkeypatch.setattr(app_module.requests, "post", fake_post)
    monkeypatch.setitem(app_module.app.config, "MAIL_PASSWORD", "token")
    monkeypatch.setattr(
        app_module.db,
        "find_teacher_by_email",
        lambda email: {
            "teacher_id": 9,
            "first_name": "Ada",
            "email": "ada@x.test",
            "verified": 1,
        },
    )
    with _client(monkeypatch) as client:
        response = client.post("/forgot-password", json={"email": "ada@x.test"})
    assert response.status_code == 200
    payload = captured[0]
    assert payload["category"] == "password_reset"
    assert "/reset?token=" in payload["text"]
    assert "/verify?token=" not in payload["text"]
    token = payload["text"].split("/reset?token=", 1)[1].split()[0]
    assert app_module.confirm_token(token) is None
    assert app_module.read_reset_token(token) == "ada@x.test"


def test_reset_saves_the_new_password(monkeypatch):
    saved = []
    token = app_module.generate_reset_token("ada@x.test")
    monkeypatch.setattr(
        app_module.db,
        "find_teacher_by_email",
        lambda email: {"teacher_id": 9, "email": email, "verified": 1},
    )
    monkeypatch.setattr(
        app_module.db,
        "set_teacher_password",
        lambda teacher_id, password: saved.append((teacher_id, password)),
    )
    with _client(monkeypatch) as client:
        response = client.post("/reset", json={
            "token": token,
            "password": "Newpass1!",
            "password_confirm": "Newpass1!",
        })
    assert response.status_code == 200
    assert saved == [(9, "Newpass1!")]


def test_confirmation_token_cannot_reset_a_password(monkeypatch):
    saved = []
    token = app_module.generate_confirmation_token("ada@x.test")
    monkeypatch.setattr(app_module.db, "set_teacher_password", lambda *args: saved.append(args))
    with _client(monkeypatch) as client:
        response = client.post("/reset", json={
            "token": token,
            "password": "Newpass1!",
            "password_confirm": "Newpass1!",
        })
    assert response.status_code == 400
    assert "invalid or expired" in response.get_json()["error"].lower()
    assert saved == []
