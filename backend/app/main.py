import hashlib
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import RLock
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import insert, select, text, update

from .agent import parse_intent
from .config import get_settings
from .db import make_engine, metadata, payments
from .paypal import PayPal, approval_link, verify_capture
from .schemas import ApprovalRequest, PromptRequest


def now():
    return datetime.now(timezone.utc).isoformat()


def create_app(settings=None, engine=None, paypal=None):
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)
    paypal = paypal or PayPal(settings)
    payment_lock = RLock()

    @asynccontextmanager
    async def lifespan(app):
        with engine.begin() as conn:
            metadata.create_all(conn)
            if engine.dialect.name == "postgresql":
                # The backend connects as table owner; browser roles get no policies.
                for table in (payments, metadata.tables["paypilot_model_calls"]):
                    conn.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))
        yield
        engine.dispose()

    app = FastAPI(title="PayPilot Sandbox API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=[settings.frontend_url],
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if settings.auth_mode == "local":
            from starlette.responses import JSONResponse
            host = request.client.host if request.client else ""
            origin = request.headers.get("origin")
            request_host = request.url.hostname
            if (host not in {"127.0.0.1", "::1", "testclient"}
                    or request_host not in {"localhost", "127.0.0.1", "::1", "testserver"}):
                return JSONResponse({"detail": "Local mode only accepts loopback connections."}, 403)
            if request.method != "GET" and origin not in {None, settings.frontend_url}:
                return JSONResponse({"detail": "Untrusted browser origin."}, 403)
        return await call_next(request)

    def owner(request: Request):
        if settings.auth_mode == "local":
            return "local-demo"
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Sign in to continue.")
        try:
            response = httpx.get(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
                headers={"apikey": settings.supabase_anon_key, "Authorization": authorization},
                timeout=10,
            )
            response.raise_for_status()
            return str(response.json()["id"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise HTTPException(401, "Session expired. Sign in again.") from exc

    def find(payment_id, user):
        with engine.connect() as conn:
            row = conn.execute(select(payments).where(
                payments.c.id == payment_id, payments.c.owner == user,
            )).mappings().first()
        if not row:
            raise HTTPException(404, "Payment not found.")
        return dict(row)

    def duplicate(payment):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with engine.connect() as conn:
            return conn.scalar(select(payments.c.id).where(
                payments.c.owner == payment["owner"],
                payments.c.id != payment["id"],
                payments.c.fingerprint == payment["fingerprint"],
                payments.c.created_at >= cutoff,
                payments.c.status.in_(["CREATING", "CREATED", "CAPTURE_PENDING", "COMPLETED"]),
            ).limit(1))

    def public(payment):
        result = {key: value for key, value in payment.items() if key not in {"owner", "fingerprint"}}
        risks = []
        if Decimal(payment["amount"]) >= Decimal("250"):
            risks.append("High-value Sandbox payment: review the amount carefully.")
        duplicate_id = duplicate(payment)
        if duplicate_id:
            risks.append("A matching payment was started in the last 24 hours. Resolve it first.")
        result["risks"] = risks
        result["duplicate"] = bool(duplicate_id)
        return result

    def save(payment_id, **values):
        with engine.begin() as conn:
            conn.execute(update(payments).where(payments.c.id == payment_id).values(**values))

    @app.get("/api/health")
    def health():
        return {
            "status": "ok", "environment": "sandbox", "auth_mode": settings.auth_mode,
            "parser": "gemini" if settings.gemini_api_key else "rule-based",
            "paypal_configured": paypal.configured,
        }

    @app.post("/api/plans", status_code=201)
    def plan(body: PromptRequest, user=Depends(owner)):
        intent, parser = parse_intent(body.prompt, user, engine, settings)
        normalized = re.sub(r"[\W_]+", " ", intent.description.casefold()).strip()
        fingerprint = hashlib.sha256(
            f"{intent.amount}|{intent.currency}|{normalized}".encode()
        ).hexdigest()
        payment = {
            "id": str(uuid4()), "owner": user, **intent.model_dump(),
            "fingerprint": fingerprint, "status": "DRAFT", "created_at": now(),
            "order_id": None, "approval_url": None, "capture_id": None, "completed_at": None,
        }
        with engine.begin() as conn:
            conn.execute(insert(payments).values(**payment))
        return {**public(payment), "parser": parser}

    @app.get("/api/payments")
    def history(user=Depends(owner)):
        with engine.connect() as conn:
            rows = conn.execute(select(payments).where(
                payments.c.owner == user,
            ).order_by(payments.c.created_at.desc()).limit(100)).mappings().all()
        return [public(dict(row)) for row in rows]

    @app.get("/api/summary")
    def summary(user=Depends(owner)):
        with engine.connect() as conn:
            rows = conn.execute(select(
                payments.c.currency, payments.c.amount,
            ).where(payments.c.owner == user, payments.c.status == "COMPLETED")).all()
        totals = {}
        for currency, amount in rows:
            totals[currency] = totals.get(currency, Decimal("0")) + Decimal(amount)
        return {
            "completed_count": len(rows),
            "totals": {currency: format(amount, ".2f") for currency, amount in totals.items()},
            "label": "Verified Sandbox collections (not payouts or bank balances)",
        }

    @app.get("/api/payments/{payment_id}")
    def get_payment(payment_id: str, user=Depends(owner)):
        return public(find(payment_id, user))

    @app.post("/api/payments/{payment_id}/checkout")
    def checkout(payment_id: str, body: ApprovalRequest, user=Depends(owner)):
        with payment_lock:
            payment = find(payment_id, user)
            if payment["status"] in {"CREATED", "CAPTURE_PENDING", "COMPLETED"}:
                return public(payment)
            if not paypal.configured:
                raise HTTPException(503, "Configure PayPal Sandbox credentials to enable checkout.")
            if duplicate(payment):
                raise HTTPException(409, "Duplicate payment blocked. Resume the existing payment.")
            if Decimal(payment["amount"]) >= Decimal("250") and not body.acknowledge_risk:
                raise HTTPException(409, "Acknowledge the high-value warning before checkout.")
            save(payment_id, status="CREATING")
            order = paypal.create(payment)
            url = approval_link(order)
            order_id = order.get("id", "")
            if not re.fullmatch(r"[A-Za-z0-9]{1,64}", order_id):
                raise HTTPException(502, "PayPal returned an invalid order identifier.")
            save(payment_id, status="CREATED", order_id=order_id, approval_url=url)
            return public(find(payment_id, user))

    @app.post("/api/payments/{payment_id}/capture")
    def capture(payment_id: str, user=Depends(owner)):
        with payment_lock:
            payment = find(payment_id, user)
            if payment["status"] == "COMPLETED":
                return public(payment)
            if not payment["order_id"]:
                raise HTTPException(409, "Create and approve checkout first.")
            order = paypal.get(payment["order_id"])
            if order.get("id") != payment["order_id"]:
                raise HTTPException(409, "PayPal order does not match.")
            if order.get("status") == "APPROVED":
                save(payment_id, status="CAPTURE_PENDING")
                order = paypal.capture(payment)
            capture_id = verify_capture(order, payment)
            save(payment_id, status="COMPLETED", capture_id=capture_id, completed_at=now())
            return public(find(payment_id, user))

    @app.get("/api/payments/{payment_id}/receipt")
    def receipt(payment_id: str, user=Depends(owner)):
        payment = find(payment_id, user)
        if payment["status"] != "COMPLETED":
            raise HTTPException(409, "A receipt is available only after verified capture.")
        return {
            "environment": "PAYPAL SANDBOX - NO REAL MONEY",
            "payment_id": payment["id"], "order_id": payment["order_id"],
            "capture_id": payment["capture_id"], "amount": payment["amount"],
            "currency": payment["currency"], "description": payment["description"],
            "completed_at": payment["completed_at"],
        }

    return app


app = create_app()
