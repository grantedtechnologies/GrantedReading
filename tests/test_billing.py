"""Pro status, webhook signature checks, and duplicate-event handling."""
import hashlib
import hmac
import json
import time

import pytest

import billing
import cache
import db

TEST_SECRET = "whsec_test_billing_suite"


def _subscription(status):
    return None if status is None else {"status": status, "stripe_customer_id": "cus_1"}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, False),
        ("active", True),
        ("trialing", True),
        ("canceled", False),
        ("past_due", False),
        ("incomplete", False),
        ("unpaid", False),
    ],
)
def test_is_paid_pro_follows_an_active_stripe_row(monkeypatch, status, expected):
    monkeypatch.setattr(db, "paid_pro_exists", lambda teacher_id: expected)
    monkeypatch.setattr(db, "active_pilot_exists", lambda teacher_id: False)
    assert billing.is_paid_pro(7) is expected
    assert billing.has_pro_access(7) is expected


def test_has_pro_access_is_true_for_a_live_pilot(monkeypatch):
    monkeypatch.setattr(db, "paid_pro_exists", lambda teacher_id: False)
    monkeypatch.setattr(db, "active_pilot_exists", lambda teacher_id: True)
    assert billing.is_paid_pro(7) is False
    assert billing.has_pro_access(7) is True


def _signed(payload, secret=TEST_SECRET, timestamp=None):
    timestamp = int(timestamp or time.time())
    signature = hmac.new(
        secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def _event_payload(event_id="evt_test_1", event_type="customer.subscription.updated"):
    return json.dumps({
        "id": event_id,
        "object": "event",
        "type": event_type,
        "data": {"object": {"id": "sub_1", "object": "subscription"}},
    })


@pytest.fixture
def client(monkeypatch):
    import app as app_module

    # app loads .env with override=True on import, so set the secret after.
    monkeypatch.setenv("STRIPE_WEBHOOK", TEST_SECRET)
    handled = []
    monkeypatch.setattr(billing, "handle_event", lambda event: handled.append(event) or "saved")
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        test_client.handled = handled
        yield test_client


def _post_webhook(client, payload, signature):
    return client.post(
        "/webhook",
        data=payload,
        headers={"Stripe-Signature": signature, "Content-Type": "application/json"},
    )


def test_webhook_accepts_a_correctly_signed_event(client):
    payload = _event_payload()
    response = _post_webhook(client, payload, _signed(payload))
    assert response.status_code == 200
    assert [event["id"] for event in client.handled] == ["evt_test_1"]


@pytest.mark.parametrize(
    "signature",
    [
        "",
        "t=1,v1=deadbeef",
        _signed(_event_payload(), secret="whsec_someone_else"),
        _signed(_event_payload(), timestamp=time.time() - 3600),
    ],
    ids=["missing", "garbage", "wrong-secret", "too-old"],
)
def test_webhook_rejects_unverified_requests(client, signature):
    response = _post_webhook(client, _event_payload(), signature)
    assert response.status_code == 400
    assert client.handled == []


def test_webhook_rejects_a_tampered_body(client):
    signature = _signed(_event_payload())
    tampered = _event_payload(event_type="customer.subscription.deleted")
    response = _post_webhook(client, tampered, signature)
    assert response.status_code == 400
    assert client.handled == []


def test_checkout_refuses_a_teacher_who_is_already_pro(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_verified_teacher", lambda: {"teacher_id": 3, "email": "t@x.test"})
    monkeypatch.setattr(billing, "is_paid_pro", lambda teacher_id: True)
    monkeypatch.setattr(
        billing, "create_checkout_session", lambda *a: pytest.fail("must not start a second subscription")
    )
    response = client.post("/billing/create-checkout-session", json={})
    assert response.status_code == 409


def test_a_claimed_event_is_skipped_before_calling_stripe(monkeypatch):
    monkeypatch.setattr(db, "apply_stripe_event", lambda *args, **kwargs: "duplicate")

    def fail(*args, **kwargs):
        raise AssertionError("a duplicate event must not be applied again")

    monkeypatch.setattr(billing, "_current_subscription", fail)
    event = json.loads(_event_payload())
    # handle_event still reads the Stripe object before claiming, so stub that.
    monkeypatch.setattr(billing, "_current_subscription", lambda *args, **kwargs: {
        "id": "sub_1",
        "customer": "cus_1",
        "status": "active",
        "created": 1,
        "items": {"data": [{}]},
        "metadata": {},
    })
    assert billing.handle_event(event) == "duplicate"


def test_a_failed_event_does_not_keep_its_claim(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("stripe down")

    monkeypatch.setattr(billing, "_current_subscription", fail)
    with pytest.raises(RuntimeError, match="stripe down"):
        billing.handle_event(json.loads(_event_payload()))


def test_unhandled_event_types_are_ignored(monkeypatch):
    monkeypatch.setattr(db, "apply_stripe_event", lambda *args, **kwargs: pytest.fail("not needed"))
    event = json.loads(_event_payload(event_type="customer.created"))
    assert billing.handle_event(event) == "ignored"


def test_subscription_row_reads_the_period_from_the_item():
    row = billing._subscription_row(
        {
            "id": "sub_1",
            "customer": "cus_1",
            "status": "active",
            "created": 1_790_000_000,
            "items": {"data": [{"current_period_end": 1_792_592_000, "price": {"unit_amount": 599}}]},
        },
        teacher_id=3,
    )
    assert row["teacher_id"] == 3
    assert row["price"] == billing._price_amount({"unit_amount": 599})
    assert str(row["price"]) == "5.99"
    assert row["current_period_end"].year == 2026
    assert row["canceled_at"] is None


def test_record_checkout_saves_a_completed_session(monkeypatch):
    saved = {}

    def retrieve(session_id, params=None):
        assert session_id == "cs_test"
        return type("Result", (), {
            "to_dict": lambda self: {
                "status": "complete",
                "mode": "subscription",
                "client_reference_id": "4",
                "metadata": {"teacherid": "4"},
                "subscription": "sub_9",
            },
        })()

    class Sessions:
        pass

    Sessions.retrieve = staticmethod(retrieve)

    class Checkout:
        sessions = Sessions()

    class V1:
        checkout = Checkout()

    monkeypatch.setattr(billing, "client", lambda: type("Client", (), {"v1": V1()})())
    monkeypatch.setattr(billing, "_current_subscription", lambda subscription_id, fallback: {
        "id": subscription_id,
        "customer": "cus_9",
        "status": "active",
        "created": 1_790_000_000,
        "metadata": {"teacherid": "4"},
        "items": {"data": [{"price": {"unit_amount": 599}, "current_period_end": 1_792_592_000}]},
    })
    monkeypatch.setattr(db, "save_subscription", lambda row: saved.update(row) or "saved")

    assert billing.record_checkout("cs_test", expected_teacher_id=4) == 4
    assert saved["subscription_id"] == "sub_9"
    assert saved["teacher_id"] == 4
    assert saved["status"] == "active"
    assert billing.record_checkout("cs_test", expected_teacher_id=9) is None


def test_teacher_id_falls_back_to_metadata():
    assert billing._teacher_id(None, {"teacherid": "12"}) == 12
    assert billing._teacher_id("5", {"teacherid": "12"}) == 5
    assert billing._teacher_id(None, {}) is None
