from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=8, max_length=1000)


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: str = Field(max_length=24)
    currency: Literal["USD", "EUR", "GBP"]
    description: str = Field(min_length=3, max_length=127)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value):
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("Use a valid decimal amount.") from exc
        if not amount.is_finite() or not Decimal("0") < amount <= Decimal("1000"):
            raise ValueError("Sandbox amounts must be greater than 0 and at most 1000.")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Amounts must have at most two decimal places.")
        return format(amount, ".2f")

    @field_validator("description")
    @classmethod
    def validate_description(cls, value):
        value = " ".join(value.split())
        if len(value) < 3 or any(ord(char) < 32 for char in value):
            raise ValueError("Provide a clear payment description.")
        return value


class ApprovalRequest(BaseModel):
    approved: Literal[True]
    acknowledge_risk: bool = False
