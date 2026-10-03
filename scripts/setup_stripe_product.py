"""Create the Pro product, its monthly price, and a Billing Portal setup in
whichever Stripe mode STRIPE_SECRET_KEY belongs to. Safe to re-run: the
product has a fixed id and the price a fixed lookup key, so an existing one is
reused instead of duplicated. The price id is written to .env as
STRIPE_PRICE_ID.

    PYTHONPATH=. ./venv/bin/python scripts/setup_stripe_product.py

Run it again with a live key when moving off the sandbox, then copy the new
STRIPE_PRICE_ID to the deployed service.
"""
import os
import sys

import stripe
from dotenv import load_dotenv, set_key

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
load_dotenv(ENV_PATH, override=True)

import billing  # noqa: E402


def ensure_product(client):
    try:
        product = client.v1.products.retrieve(billing.PLAN_PRODUCT_ID)
    except stripe.InvalidRequestError as exc:
        if exc.code != "resource_missing":
            raise
        product = client.v1.products.create(params={
            "id": billing.PLAN_PRODUCT_ID,
            "name": billing.PLAN_NAME,
        })
        return product, "created"
    if product.name != billing.PLAN_NAME or not product.active:
        product = client.v1.products.update(billing.PLAN_PRODUCT_ID, params={
            "name": billing.PLAN_NAME,
            "active": True,
        })
        return product, "updated"
    return product, "found"


def _price_matches(price):
    return (
        price.unit_amount == billing.PLAN_PRICE_CENTS
        and price.currency == billing.PLAN_CURRENCY
        and price.recurring is not None
        and price.recurring.interval == billing.PLAN_INTERVAL
        and price.product == billing.PLAN_PRODUCT_ID
        and price.active
    )


def ensure_price(client):
    existing = client.v1.prices.list(params={
        "lookup_keys": [billing.PLAN_LOOKUP_KEY],
        "limit": 1,
    }).data
    if existing and _price_matches(existing[0]):
        return existing[0], "found"
    # Prices cannot be edited. A changed amount or interval gets a new price,
    # and transfer_lookup_key moves the lookup key onto it. Subscribers on the
    # old price stay on it.
    price = client.v1.prices.create(params={
        "product": billing.PLAN_PRODUCT_ID,
        "unit_amount": billing.PLAN_PRICE_CENTS,
        "currency": billing.PLAN_CURRENCY,
        "recurring": {"interval": billing.PLAN_INTERVAL},
        "lookup_key": billing.PLAN_LOOKUP_KEY,
        "transfer_lookup_key": True,
        "nickname": f"{billing.PLAN_NAME} monthly",
    })
    return price, "replaced" if existing else "created"


def ensure_portal_configuration(client):
    defaults = client.v1.billing_portal.configurations.list(params={
        "is_default": True,
        "limit": 1,
    }).data
    if defaults:
        return defaults[0], "found default"
    for config in client.v1.billing_portal.configurations.list(params={
        "active": True,
        "limit": 100,
    }).auto_paging_iter():
        if (config.metadata or {}).get("app") == billing.PORTAL_CONFIG_METADATA["app"]:
            return config, "found"
    config = client.v1.billing_portal.configurations.create(params={
        "metadata": billing.PORTAL_CONFIG_METADATA,
        "features": {
            "customer_update": {"enabled": True, "allowed_updates": ["email", "address"]},
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
        },
    })
    return config, "created"


def main():
    client = billing.client()
    product, product_state = ensure_product(client)
    price, price_state = ensure_price(client)
    portal, portal_state = ensure_portal_configuration(client)

    set_key(ENV_PATH, "STRIPE_PRICE_ID", price.id, quote_mode="never")

    mode = "live" if price.livemode else "test"
    amount = price.unit_amount / 100
    print(f"Stripe mode: {mode}")
    print(f"Product {product.id} ({product.name}): {product_state}")
    print(f"Price {price.id} (${amount:.2f} {price.currency.upper()}/{price.recurring.interval}): {price_state}")
    print(f"Billing Portal configuration {portal.id}: {portal_state} (default={portal.is_default})")
    print(f"Wrote STRIPE_PRICE_ID={price.id} to .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
