"""Pydantic models used to validate write operations before anything leaves the system."""
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator

class InventoryUpdate(BaseModel):
    sku: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9\-_.]+$")
    new_on_hand: float = Field(ge=0, le=10_000_000)
    reason: str = Field(min_length=5, max_length=300)
    approval_id: int = Field(gt=0)

class SlackMessage(BaseModel):
    channel: str = Field(min_length=2, max_length=80, pattern=r"^[#@A-Za-z0-9._\-]+$")
    text: str = Field(min_length=3, max_length=3500)
    approval_id: int = Field(gt=0)

    @field_validator("text")
    @classmethod
    def no_secrets(cls, v: str) -> str:
        if "xoxb-" in v or "xoxp-" in v:
            raise ValueError("Message appears to contain a Slack token.")
        return v
