# app/receipts/models_receipts.py
from __future__ import annotations
from typing import Optional
from sqlmodel import SQLModel, Field
from datetime import datetime

class Printer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    host: str
    port: int = 9100
    enabled: bool = True
    width_chars: int = 32
    logo_path: Optional[str] = None

class ReceiptTemplate(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    engine: str = "jinja"      # al momento solo jinja
    body: str                  # contenuto del template
    cut: bool = True

class ReceiptRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    mode: str                  # 'kds' | 'product_set'
    # FK verso tabelle esistenti nel tuo progetto
    kitchen_id: Optional[int] = Field(default=None, foreign_key="kitchen.id")
    printer_id: int = Field(foreign_key="printer.id")
    template_id: int = Field(foreign_key="receipttemplate.id")
    copies: int = 1
    priority: int = 100
    consume_lines: bool = True
    enabled: bool = True

class ReceiptRuleProduct(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    rule_id: int = Field(foreign_key="receiptrule.id")
    product_id: int = Field(foreign_key="product.id")



class PrintedReceipt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    order_id: Optional[int] = Field(default=None, index=True)
    rule_id: Optional[int] = Field(default=None, index=True)
    template_id: Optional[int] = Field(default=None, index=True)
    printer_id: int = Field(index=True)
    kitchen_id: Optional[int] = Field(default=None, index=True)

    body: str
    cut: bool = True
    status: str = "ok"            # "ok" | "error"
    error_text: Optional[str] = None
    summary: Optional[str] = None # prima riga utile per elenco