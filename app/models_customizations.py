# app/models_customizations.py
from __future__ import annotations

from typing import Optional
from sqlmodel import SQLModel, Field
from sqlalchemy import JSON


class ProductPrompt(SQLModel, table=True):
    __tablename__ = "product_prompt"
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    name: str                      # es. "Salse"
    kind: str = "single"           # "boolean" | "single" | "multi"
    required: bool = False
    choices: Optional[list[str]] = Field(default=None, sa_type=JSON)
    delta_cents: int = 0
    # ⚠️ NESSUNA relationship qui: si lavora per product_id


class OrderLineOption(SQLModel, table=True):
    __tablename__ = "orderline_option"
    id: Optional[int] = Field(default=None, primary_key=True)
    orderline_id: int = Field(foreign_key="orderline.id", index=True)
    prompt_name: str
    value: str
    price_delta_cents: int = 0
