from copy import deepcopy
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import Settings
from app.db import make_engine, model_calls
from app.main import create_app
from app.paypal import PayPal, approval_link
from app.schemas import Intent


class FakePayPal:
    configured = True

    def __init__(self):
        self.orders = {}
        self.creates = 0
        self.captures = 0
        self.reject_create = False
        self.uncertain_capture = False
        self.mismatch = False

    def create(self, payment):
        self.creates += 1
        if self.reject_create:
            raise HTTPException(502, "Uncertain order creation.")
        order_id = f"ORDER{payment['id'].replace('-', '')}"
        self.orders.setdefault(order_id, {
            "id": order_id, "status": "CREATED",
            "purchase_units": [{
                "reference_id": payment["id"],
                "payments": {"captures": [{
                    "id": f"CAP{payment['id'].replace('-', '')}",
                    "status": "COMPLETED",
                    "amount": {"currency_code": payment["currency"], "value": payment["amount"]},
                }]},
            }],
            "links": [{"rel": "payer-action",
                       "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}"}],
        })
        return deepcopy(self.orders[order_id])

    def get(self, order_id):
        result = deepcopy(self.orders[order_id])
        if self.mismatch:
            result["purchase_units"][0]["payments"]["captures"][0]["amount"]["value"] = "999.00"
        return result

    def approve(self, order_id):
        self.orders[order_id]["status"] = "APPROVED"

    def capture(self, payment):
        self.captures += 1
        order = self.orders[payment["order_id"]]
        order["status"] = "COMPLETED"
        if self.uncertain_capture:
            raise HTTPException(502, "Uncertain capture response.")
        return self.get(payment["order_id"])


@pytest.fixture
def workspace(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    paypal = FakePayPal()
    settings = Settings(_env_file=None)
    with TestClient(create_app(settings, engine, paypal)) as client:
        yield client, paypal


def plan(client, prompt="Collect USD 25.00 for design consultation"):
    response = client.post("/api/plans", json={"prompt": prompt})
    assert response.status_code == 201, response.text
    return response.json()


def checkout(client, payment, **body):
    return client.post(f"/api/payments/{payment['id']}/checkout",
                       json={"approved": True, **body})


def test_complete_workflow_requires_buyer_approval_and_is_idempotent(workspace):
    client, paypal = workspace
    payment = plan(client)
    assert payment["amount"] == "25.00"
    assert client.get(f"/api/payments/{payment['id']}/receipt").status_code == 409
    assert client.post(f"/api/payments/{payment['id']}/checkout",
                       json={"approved": False}).status_code == 422
    created = checkout(client, payment).json()
    assert created["status"] == "CREATED"
    assert checkout(client, payment).status_code == 200
    assert paypal.creates == 1
    capture_url = f"/api/payments/{payment['id']}/capture"
    assert client.post(capture_url).status_code == 409
    assert paypal.captures == 0
    paypal.approve(created["order_id"])
    result = client.post(capture_url)
    assert result.status_code == 200
    assert result.json()["status"] == "COMPLETED"
    assert client.post(capture_url).status_code == 200
    assert paypal.captures == 1
    receipt = client.get(f"/api/payments/{payment['id']}/receipt").json()
    assert receipt["capture_id"]
    assert receipt["environment"] == "PAYPAL SANDBOX - NO REAL MONEY"
    assert client.get("/api/summary").json()["totals"] == {"USD": "25.00"}


def test_duplicate_blocks_normalized_description_and_preserves_original(workspace):
    client, paypal = workspace
    original = plan(client)
    checkout(client, original)
    duplicate = plan(client, "Collect USD 25 for DESIGN   consultation!")
    assert duplicate["duplicate"]
    assert checkout(client, duplicate).status_code == 409
    assert paypal.creates == 1
    assert checkout(client, original).status_code == 200


def test_high_value_warning_requires_acknowledgement(workspace):
    client, _ = workspace
    payment = plan(client, "Collect EUR 350 for website development")
    assert payment["risks"]
    assert checkout(client, payment).status_code == 409
    assert checkout(client, payment, acknowledge_risk=True).status_code == 200


@pytest.mark.parametrize("prompt", [
    "Send USD 25 to someone",
    "Collect USD -25 for consultation",
    "Collect USD 25.123 for consultation",
    "Collect INR 25 for consultation",
    "Collect USD 1001 for consultation",
    "Collect USD 0 for consultation",
    "Collect USD 25 for consultation and collect EUR 50 for hosting",
])
def test_ambiguous_or_unsupported_requests_are_not_executed(workspace, prompt):
    client, paypal = workspace
    response = client.post("/api/plans", json={"prompt": prompt})
    assert response.status_code == 422
    assert paypal.creates == 0


def test_uncertain_capture_is_reconciled_without_second_capture(workspace):
    client, paypal = workspace
    payment = plan(client)
    created = checkout(client, payment).json()
    paypal.approve(created["order_id"])
    paypal.uncertain_capture = True
    capture_url = f"/api/payments/{payment['id']}/capture"
    assert client.post(capture_url).status_code == 502
    assert client.get(f"/api/payments/{payment['id']}/receipt").status_code == 409
    assert client.post(capture_url).json()["status"] == "COMPLETED"
    assert paypal.captures == 1


def test_mismatched_capture_never_issues_receipt(workspace):
    client, paypal = workspace
    payment = plan(client)
    created = checkout(client, payment).json()
    paypal.approve(created["order_id"])
    paypal.mismatch = True
    assert client.post(f"/api/payments/{payment['id']}/capture").status_code == 409
    assert client.get(f"/api/payments/{payment['id']}/receipt").status_code == 409
    assert client.get("/api/summary").json()["completed_count"] == 0


def test_unconfigured_paypal_does_not_fake_success(workspace):
    client, paypal = workspace
    paypal.configured = False
    payment = plan(client)
    assert checkout(client, payment).status_code == 503
    assert client.get(f"/api/payments/{payment['id']}").json()["status"] == "DRAFT"


def test_local_mode_rejects_remote_clients_origins_and_rebinding(workspace):
    client, _ = workspace
    assert client.post("/api/plans", json={"prompt": "Collect USD 25 for design"},
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/payments", headers={"Host": "evil.example"}).status_code == 403


def test_production_cannot_use_local_auth():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production")


def test_supabase_ownership_is_enforced(tmp_path):
    settings = Settings(_env_file=None, auth_mode="supabase",
                        supabase_url="https://example.supabase.co", supabase_anon_key="test-key")
    engine = make_engine(f"sqlite:///{tmp_path / 'auth.db'}")

    def user_response(url, *, headers, **kwargs):
        return httpx.Response(200, json={"id": headers["Authorization"]},
                              request=httpx.Request("GET", url))

    with patch("app.main.httpx.get", side_effect=user_response):
        with TestClient(create_app(settings, engine, FakePayPal())) as client:
            assert client.get("/api/payments").status_code == 401
            client.headers["Authorization"] = "Bearer user-a"
            payment = plan(client)
            client.headers["Authorization"] = "Bearer user-b"
            assert client.get("/api/payments").json() == []
            assert client.get(f"/api/payments/{payment['id']}").status_code == 404
            assert checkout(client, payment).status_code == 404
            assert client.post(f"/api/payments/{payment['id']}/capture").status_code == 404
            assert client.get(f"/api/payments/{payment['id']}/receipt").status_code == 404


def test_gemini_calls_are_budgeted_before_network(tmp_path):
    settings = Settings(_env_file=None, gemini_api_key="test-key", model_daily_limit=1)
    engine = make_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    response = httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text":
            '{"amount":"12","currency":"GBP","description":"workshop"}'
        }]}}]}, request=httpx.Request("POST", "https://example.test"),
    )
    with TestClient(create_app(settings, engine, FakePayPal())) as client:
        with patch("app.agent.httpx.post", return_value=response) as model:
            assert plan(client, "Collect twelve pounds for a workshop")["parser"] == "gemini"
            assert client.post("/api/plans", json={
                "prompt": "Collect twelve pounds for a workshop",
            }).status_code == 429
            assert model.call_count == 1
            with engine.connect() as conn:
                assert conn.scalar(select(func.count()).select_from(model_calls)) == 1


@pytest.mark.parametrize("url", [
    "https://www.paypal.com/checkoutnow",
    "https://www.sandbox.paypal.com.evil.example/checkoutnow",
    "http://www.sandbox.paypal.com/checkoutnow",
    "https://user@www.sandbox.paypal.com/checkoutnow",
])
def test_only_trusted_sandbox_approval_urls_are_accepted(url):
    with pytest.raises(HTTPException):
        approval_link({"links": [{"rel": "approve", "href": url}]})


def test_paypal_adapter_uses_sandbox_and_stable_request_ids():
    adapter = PayPal(Settings(_env_file=None))
    payment = {
        "id": "00000000-0000-0000-0000-000000000001", "amount": "25.00",
        "currency": "USD", "description": "design consultation", "order_id": "ORDER1",
    }
    with patch.object(adapter, "request", return_value={}) as request:
        adapter.create(payment)
        first = request.call_args.kwargs
        adapter.create(payment)
        assert request.call_args.kwargs["request_id"] == first["request_id"]
        assert len(first["request_id"]) <= 38
        assert first["body"]["purchase_units"][0]["amount"]["value"] == "25.00"
        adapter.capture(payment)
        assert request.call_args.kwargs["request_id"] != first["request_id"]


def test_real_adapter_transport_never_uses_live_endpoint():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "sandbox-token"})
        return httpx.Response(201, json={"id": "ORDER1"})

    client_class = httpx.Client
    settings = Settings(_env_file=None, paypal_client_id="test-id", paypal_client_secret="test-secret")
    adapter = PayPal(settings)
    with patch("app.paypal.httpx.Client", side_effect=lambda **kwargs: client_class(
        transport=httpx.MockTransport(handler), **kwargs,
    )):
        adapter.request("POST", "/v2/checkout/orders", body={}, request_id="stable-request")
    assert all(request.url.host == "api-m.sandbox.paypal.com" for request in seen)
    assert seen[-1].headers["PayPal-Request-Id"] == "stable-request"
    assert seen[-1].headers["Authorization"] == "Bearer sandbox-token"


def test_failed_create_reserves_duplicate_and_retries_same_plan(workspace):
    client, paypal = workspace
    original = plan(client)
    paypal.reject_create = True
    assert checkout(client, original).status_code == 502
    duplicate = plan(client)
    assert checkout(client, duplicate).status_code == 409
    paypal.reject_create = False
    assert checkout(client, original).json()["status"] == "CREATED"


def test_history_persists_across_server_restarts(tmp_path):
    url = f"sqlite:///{tmp_path / 'persistent.db'}"
    settings = Settings(_env_file=None)
    with TestClient(create_app(settings, make_engine(url), FakePayPal())) as client:
        payment = plan(client)
    with TestClient(create_app(settings, make_engine(url), FakePayPal())) as client:
        history = client.get("/api/payments").json()
        assert [row["id"] for row in history] == [payment["id"]]


def test_gemini_can_decline_ambiguous_request(tmp_path):
    settings = Settings(_env_file=None, gemini_api_key="test-key")
    engine = make_engine(f"sqlite:///{tmp_path / 'ambiguous.db'}")
    response = httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": "null"}]}}]},
        request=httpx.Request("POST", "https://example.test"),
    )
    with TestClient(create_app(settings, engine, FakePayPal())) as client:
        with patch("app.agent.httpx.post", return_value=response):
            assert client.post("/api/plans", json={"prompt": "Please collect something"}).status_code == 422
            assert client.get("/api/payments").json() == []


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0", "1000.01", "0.001"])
def test_amounts_are_validated_independently_of_the_model(amount):
    with pytest.raises(ValidationError):
        Intent(amount=amount, currency="USD", description="consultation")
