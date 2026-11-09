from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

BASE_URL = "https://api-m.sandbox.paypal.com"


class PayPal:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configured(self):
        return bool(self.settings.paypal_client_id and self.settings.paypal_client_secret)

    def request(self, method, path, *, body=None, request_id=None):
        if not self.configured:
            raise HTTPException(503, "Configure PayPal Sandbox credentials to enable checkout.")
        try:
            with httpx.Client(timeout=30) as client:
                token = client.post(
                    f"{BASE_URL}/v1/oauth2/token",
                    auth=(self.settings.paypal_client_id, self.settings.paypal_client_secret),
                    data={"grant_type": "client_credentials"},
                )
                token.raise_for_status()
                headers = {
                    "Authorization": f"Bearer {token.json()['access_token']}",
                    "Content-Type": "application/json",
                    "Prefer": "return=representation",
                }
                if request_id:
                    headers["PayPal-Request-Id"] = request_id
                response = client.request(
                    method, f"{BASE_URL}{path}", headers=headers, json=body,
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise HTTPException(
                502, "PayPal Sandbox could not confirm the operation. No success is assumed. "
                "Retry this same payment to reconcile its status.",
            ) from exc

    def create(self, payment):
        return self.request(
            "POST", "/v2/checkout/orders",
            request_id=f"c-{payment['id']}",
            body={
                "intent": "CAPTURE",
                "purchase_units": [{
                    "reference_id": payment["id"],
                    "custom_id": payment["id"],
                    "description": payment["description"],
                    "amount": {"currency_code": payment["currency"], "value": payment["amount"]},
                }],
                "payment_source": {"paypal": {"experience_context": {
                    "brand_name": "PayPilot Sandbox",
                    "user_action": "PAY_NOW",
                    "shipping_preference": "NO_SHIPPING",
                    "return_url": f"{self.settings.frontend_url}/?payment={payment['id']}&checkout=return",
                    "cancel_url": f"{self.settings.frontend_url}/?payment={payment['id']}&checkout=cancel",
                }}},
            },
        )

    def get(self, order_id):
        return self.request("GET", f"/v2/checkout/orders/{order_id}")

    def capture(self, payment):
        return self.request(
            "POST", f"/v2/checkout/orders/{payment['order_id']}/capture",
            body={}, request_id=f"p-{payment['id']}",
        )


def approval_link(order):
    for link in order.get("links", []):
        if link.get("rel") in {"payer-action", "approve"}:
            url = link.get("href", "")
            parsed = urlparse(url)
            if (parsed.scheme == "https"
                    and parsed.hostname == "www.sandbox.paypal.com"
                    and parsed.port in {None, 443}
                    and not parsed.username and not parsed.password):
                return url
    raise HTTPException(502, "PayPal did not provide a trusted Sandbox approval link.")


def verify_capture(order, payment):
    try:
        units = order["purchase_units"]
        if order["id"] != payment["order_id"] or order["status"] != "COMPLETED" or len(units) != 1:
            raise ValueError("Order is not complete.")
        unit = units[0]
        captures = unit["payments"]["captures"]
        if unit.get("reference_id") != payment["id"] or len(captures) != 1:
            raise ValueError("Payment reference does not match.")
        capture = captures[0]
        if (
            capture["status"] != "COMPLETED"
            or capture["amount"]["currency_code"] != payment["currency"]
            or Decimal(capture["amount"]["value"]) != Decimal(payment["amount"])
            or not capture["id"]
        ):
            raise ValueError("Capture does not match the reviewed amount.")
        return capture["id"]
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation) as exc:
        raise HTTPException(409, "Capture is not verified. Retry to reconcile; no receipt issued.") from exc
