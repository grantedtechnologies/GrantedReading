"""Signed Stripe webhook payloads for E2E (test-mode keys only)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

import requests
import stripe


def stripe_secret() -> str:
    return (os.getenv("STRIPE_SECRET_KEY") or "").strip()


def is_configured() -> bool:
    """True when STRIPE_SECRET_KEY looks like a real test key (not a placeholder)."""
    key = stripe_secret()
    if not key.startswith("sk_test_"):
        return False
    placeholder_bits = ("replace_me", "changeme", "your_key", "xxx", "placeholder")
    lowered = key.lower()
    return not any(bit in lowered for bit in placeholder_bits)


def webhook_secret() -> str:
    return (os.getenv("STRIPE_WEBHOOK") or "").strip()


def signed_headers(body: bytes, secret: str | None = None) -> dict[str, str]:
    secret = secret or webhook_secret()
    timestamp = int(time.time())
    signature = hmac.new(
        secret.encode(),
        f"{timestamp}.{body.decode('utf-8')}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "Stripe-Signature": f"t={timestamp},v1={signature}",
    }


def subscription_created_event(
    *,
    teacher_id: int,
    customer_id: str = "cus_e2e_test",
    subscription_id: str | None = None,
    price_id: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    price_id = price_id or (os.getenv("STRIPE_PRICE_ID") or "price_e2e")
    now = int(time.time())
    subscription_id = subscription_id or f"sub_e2e_{teacher_id}_{now}"
    return {
        "id": event_id or f"evt_e2e_{teacher_id}_{now}",
        "object": "event",
        "type": "customer.subscription.created",
        "data": {
            "object": {
                "id": subscription_id,
                "object": "subscription",
                "customer": customer_id,
                "status": "active",
                "created": now,
                "current_period_end": now + 30 * 24 * 3600,
                "metadata": {"teacherid": str(teacher_id)},
                "items": {
                    "data": [
                        {
                            "current_period_end": now + 30 * 24 * 3600,
                            "price": {
                                "id": price_id,
                                "unit_amount": 599,
                            },
                        }
                    ]
                },
            }
        },
    }


def post_signed_event(base_url: str, event: dict[str, Any]):
    body = json.dumps(event, separators=(",", ":")).encode("utf-8")
    return requests.post(
        f"{base_url.rstrip('/')}/webhook",
        data=body,
        headers=signed_headers(body),
        timeout=20,
    )


def cleanup_stripe_customer(customer_id: str | None) -> None:
    if not customer_id or not str(customer_id).startswith("cus_"):
        return
    try:
        client = stripe.StripeClient(stripe_secret())
        client.v1.customers.delete(customer_id)
    except Exception:
        return
