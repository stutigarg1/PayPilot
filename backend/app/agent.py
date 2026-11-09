import json
import re
from datetime import datetime, timezone
from threading import RLock

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, insert, select

from .db import model_calls
from .schemas import Intent

model_lock = RLock()


def demo_parse(prompt: str) -> Intent:
    match = re.fullmatch(
        r"\s*(?:collect|charge|request)\s+"
        r"(?P<currency>USD|EUR|GBP)\s+(?P<amount>\d+(?:\.\d{1,2})?)"
        r"\s+for\s+(?P<description>.{3,127}?)\s*",
        prompt, re.IGNORECASE,
    )
    if not match:
        raise HTTPException(
            422, 'Demo parser: use "Collect USD 25.00 for design consultation". '
            "Supported currencies: USD, EUR, GBP. Configure Gemini for flexible phrasing.",
        )
    values = match.groupdict()
    if re.search(r"\b(?:USD|EUR|GBP)\s+\d", values["description"], re.IGNORECASE):
        raise HTTPException(422, "Prepare one payment at a time; a second amount was detected.")
    values["currency"] = values["currency"].upper()
    try:
        return Intent(**values)
    except ValidationError as exc:
        raise HTTPException(422, "Use an amount from 0.01 to 1000 with two decimal places.") from exc


def parse_intent(prompt, owner, engine, settings):
    if not settings.gemini_api_key:
        return demo_parse(prompt), "rule-based"
    # One server worker: serialize quota reservations before making external calls.
    with model_lock, engine.begin() as conn:
        day = datetime.now(timezone.utc).date().isoformat()
        own_count = conn.scalar(select(func.count()).select_from(model_calls).where(
            model_calls.c.owner == owner, model_calls.c.day == day,
        ))
        global_count = conn.scalar(select(func.count()).select_from(model_calls).where(
            model_calls.c.day == day,
        ))
        if own_count >= settings.model_daily_limit or global_count >= settings.model_global_daily_limit:
            raise HTTPException(429, "Daily model-call allowance reached. Try again tomorrow.")
        conn.execute(insert(model_calls).values(owner=owner, day=day))
    schema = Intent.model_json_schema()
    try:
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent",
            headers={"x-goog-api-key": settings.gemini_api_key},
            json={
                "systemInstruction": {"parts": [{"text":
                    "Extract one merchant checkout payment from the user's synthetic request. "
                    "Only collecting a buyer payment for goods/services is supported, not payouts, "
                    "transfers, refunds, subscriptions or multiple payments. "
                    "Never invent missing amount, currency or description. "
                    "For ambiguity or unsupported requests return null. "
                    "You cannot approve, create or execute payments."
                }]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": {"anyOf": [schema, {"type": "null"}]},
                    "maxOutputTokens": settings.model_max_output_tokens,
                    "temperature": 0,
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return Intent.model_validate(json.loads(text)), "gemini"
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise HTTPException(
            422, "Could not extract a complete payment. Specify one amount, currency "
            "(USD/EUR/GBP), and purpose; or check Gemini configuration and quota.",
        ) from exc
