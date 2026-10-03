"""Stripe subscription billing: one paid plan on hosted Checkout and the
hosted Billing Portal. No publishable key or Stripe.js is involved; the
browser is only ever redirected to URLs Stripe returns.
"""
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import stripe

import cache
import db

log = logging.getLogger(__name__)

PLAN_NAME = "Pro"
PLAN_PRICE_CENTS = 599
PLAN_CURRENCY = "usd"
PLAN_INTERVAL = "month"
# Fixed IDs make scripts/setup_stripe_product.py safe to re-run: it looks the
# product up by id and the price up by lookup key before creating anything.
PLAN_PRODUCT_ID = "granted_pro"
PLAN_LOOKUP_KEY = "granted_pro_monthly"
PORTAL_CONFIG_METADATA = {"app": "granted-reading"}

# past_due is not Pro: the card failed and Stripe is retrying. Add it here to
# give a grace period while those retries run.
PRO_STATUSES = frozenset({"active", "trialing"})

HANDLED_EVENTS = frozenset({
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
})

_client = None


class BillingNotConfigured(RuntimeError):
    pass


def _env(name):
    return (os.getenv(name) or "").strip().strip("'\"")


def client():
    global _client
    if _client is None:
        key = _env("STRIPE_SECRET_KEY")
        if not key:
            raise BillingNotConfigured("STRIPE_SECRET_KEY is not set.")
        _client = stripe.StripeClient(key)
    return _client


def price_id():
    value = _env("STRIPE_PRICE_ID")
    if not value:
        raise BillingNotConfigured(
            "STRIPE_PRICE_ID is not set. Run scripts/setup_stripe_product.py."
        )
    return value


def is_teacher_pro(teacher_id):
    """True when the teacher's most recent subscription has a Pro status.

    The status is whatever the last webhook wrote. currentPeriodEnd is not
    compared against the clock; Stripe says when a subscription lapses.
    """
    subscription = db.latest_subscription(teacher_id)
    return subscription is not None and subscription["status"] in PRO_STATUSES


def create_checkout_session(teacher, base_url, success_url=None, cancel_url=None):
    base = base_url.rstrip("/")
    teacher_ref = str(teacher["teacher_id"])
    params = {
        "mode": "subscription",
        "line_items": [{"price": price_id(), "quantity": 1}],
        "client_reference_id": teacher_ref,
        "metadata": {"teacherid": teacher_ref},
        # Subscription events carry the subscription's metadata, not the
        # session's, so this lets them find the teacher on their own.
        "subscription_data": {"metadata": {"teacherid": teacher_ref}},
        "success_url": success_url or f"{base}/account?billing=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": cancel_url or f"{base}/account?billing=canceled",
    }
    previous = db.latest_subscription(teacher["teacher_id"])
    if previous:
        params["customer"] = previous["stripe_customer_id"]
    else:
        params["customer_email"] = teacher["email"]
    return client().v1.checkout.sessions.create(params=params).url


def record_checkout(session_id, expected_teacher_id=None):
    """Save the subscription from a completed Checkout Session.

    Returns the teacher id when the session is paid. When expected_teacher_id
    is set, a session for someone else returns None and is not saved.
    """
    checkout = client().v1.checkout.sessions.retrieve(session_id).to_dict()
    if checkout.get("status") != "complete" or checkout.get("mode") != "subscription":
        return None
    teacher_id = _teacher_id(checkout.get("client_reference_id"), checkout.get("metadata"))
    if teacher_id is None:
        return None
    if expected_teacher_id is not None and teacher_id != int(expected_teacher_id):
        return None
    subscription_id = _object_id(checkout.get("subscription"))
    if not subscription_id:
        return None
    subscription = _current_subscription(subscription_id, None)
    db.save_subscription(_subscription_row(subscription, teacher_id))
    return teacher_id


def sync_teacher_from_stripe(teacher):
    """Save this teacher's newest Stripe subscription, if Stripe has one.

    Used when Checkout returns without a session id, so a paid plan is
    recorded even if the webhook has not arrived yet.
    """
    teacher_id = int(teacher["teacher_id"])
    subscription = _latest_remote_subscription(teacher_id, teacher.get("email"))
    if subscription is None:
        return None
    db.save_subscription(_subscription_row(subscription, teacher_id))
    return teacher_id


def _latest_remote_subscription(teacher_id, email):
    found = []
    searched = client().v1.subscriptions.search(params={
        "query": f"metadata['teacherid']:'{int(teacher_id)}'",
        "limit": 10,
    }).to_dict()
    found.extend(searched.get("data") or [])
    if email:
        customers = client().v1.customers.list(params={"email": email, "limit": 10}).to_dict()
        for customer in customers.get("data") or []:
            listed = client().v1.subscriptions.list(params={
                "customer": customer["id"],
                "status": "all",
                "limit": 10,
            }).to_dict()
            for subscription in listed.get("data") or []:
                metadata_teacher = (subscription.get("metadata") or {}).get("teacherid")
                if str(metadata_teacher) == str(teacher_id):
                    found.append(subscription)
    if not found:
        return None
    newest = max(found, key=lambda row: row.get("created") or 0)
    return _current_subscription(newest["id"], newest)


def create_portal_session(customer_id, return_url):
    session = client().v1.billing_portal.sessions.create(
        params={"customer": customer_id, "return_url": return_url},
    )
    return session.url


def parse_event(payload, signature):
    """Verify the Stripe-Signature header and return the event as a dict.

    Raises stripe.SignatureVerificationError or ValueError for anything that
    was not signed with STRIPE_WEBHOOK.
    """
    secret = _env("STRIPE_WEBHOOK")
    if not secret:
        raise BillingNotConfigured("STRIPE_WEBHOOK is not set.")
    return stripe.Webhook.construct_event(payload, signature, secret).to_dict()


def handle_event(event):
    """Apply one verified event. Returns a short outcome string for the log.

    The event id is claimed in Redis first. A second delivery of the same id
    returns "duplicate". If handling raises, the claim is released so Stripe's
    retry is not skipped.
    """
    event_type = event["type"]
    if event_type not in HANDLED_EVENTS:
        return "ignored"
    if not cache.claim_webhook_event(event["id"]):
        return "duplicate"
    try:
        return _apply_event(event)
    except Exception:
        cache.release_webhook_event(event["id"])
        raise


def _apply_event(event):
    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "invoice.payment_failed":
        log.warning(
            "Stripe invoice %s payment failed for customer %s (subscription %s)",
            obj.get("id"), obj.get("customer"), _invoice_subscription_id(obj),
        )
        return "recorded"

    if event_type == "checkout.session.completed":
        if obj.get("mode") != "subscription" or not obj.get("subscription"):
            return "recorded"
        teacher_id = _teacher_id(obj.get("client_reference_id"), obj.get("metadata"))
        subscription = _current_subscription(_object_id(obj["subscription"]), None)
    else:
        subscription = _current_subscription(obj["id"], obj)
        teacher_id = _teacher_id(None, subscription.get("metadata"))

    row = _subscription_row(subscription, teacher_id)
    if event_type == "customer.subscription.deleted":
        row["status"] = "canceled"
        row["canceled_at"] = row["canceled_at"] or _utc_now()
    outcome = db.save_subscription(row)
    if outcome == "no_row":
        log.warning(
            "Stripe %s for subscription %s has no teacher to attach it to",
            event_type, row["subscription_id"],
        )
    return outcome


def _current_subscription(subscription_id, fallback):
    # Events can arrive out of order. Reading the subscription now means an
    # older delivery never overwrites a newer status.
    try:
        return client().v1.subscriptions.retrieve(subscription_id).to_dict()
    except stripe.InvalidRequestError:
        if fallback is None:
            raise
        return fallback


def _subscription_row(subscription, teacher_id):
    items = (subscription.get("items") or {}).get("data") or [{}]
    item = items[0]
    # Newer API versions keep the billing period on each item.
    period_end = subscription.get("current_period_end") or item.get("current_period_end")
    status = subscription["status"]
    canceled_at = None
    if status == "canceled":
        canceled_at = _from_timestamp(
            subscription.get("ended_at") or subscription.get("canceled_at")
        )
    return {
        "teacher_id": teacher_id,
        "customer_id": _object_id(subscription["customer"]),
        "subscription_id": subscription["id"],
        "price": _price_amount(item.get("price")),
        "status": status,
        "current_period_end": _from_timestamp(period_end),
        "created_at": _from_timestamp(subscription.get("created")) or _utc_now(),
        "canceled_at": canceled_at,
    }


def _price_amount(price):
    """Dollar amount stored on the subscription. Stripe sends cents."""
    cents = price.get("unit_amount") if isinstance(price, dict) else None
    if cents is None:
        cents = PLAN_PRICE_CENTS
    return (Decimal(cents) / Decimal(100)).quantize(Decimal("0.01"))


def _teacher_id(reference, metadata):
    for raw in (reference, (metadata or {}).get("teacherid")):
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _invoice_subscription_id(invoice):
    if invoice.get("subscription"):
        return _object_id(invoice["subscription"])
    details = ((invoice.get("parent") or {}).get("subscription_details") or {})
    return details.get("subscription")


def _object_id(value):
    return value.get("id") if isinstance(value, dict) else value


def _from_timestamp(value):
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)
