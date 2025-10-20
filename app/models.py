# app/models.py
from __future__ import annotations

from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime

class Kitchen(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    prefix: str
    next_seq: int = 1


class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    kitchen_id: Optional[int] = Field(default=None, foreign_key="kitchen.id")
    color_hex: Optional[str] = Field(default="#0ea5e9", max_length=7)  # es. "#ff8800"


class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    price_cents: int = 0
    kitchen_id: Optional[int] = Field(default=None, foreign_key="kitchen.id")
    category_id: Optional[int] = Field(default=None, foreign_key="category.id")
    image_url: Optional[str] = None
    # ⚠️ NESSUNA relationship qui: usiamo solo FK e query per product_id


class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    paid_method: str = "cash"
    total_cents: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class Ticket(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kitchen_id: int = Field(foreign_key="kitchen.id")
    order_id: int = Field(foreign_key="order.id")
    pickup_seq: int
    status: str = "queued"


class OrderLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="order.id")
    product_id: int = Field(foreign_key="product.id")
    qty: int = 1
    kitchen_id: Optional[int] = Field(default=None, foreign_key="kitchen.id")
    pickup_seq: Optional[int] = None

