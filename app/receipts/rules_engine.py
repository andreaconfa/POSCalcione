# app/receipts/rules_engine.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from sqlmodel import select

from ..models import Order, OrderLine, Product, Kitchen
from .models_receipts import (
    ReceiptRule,
    ReceiptRuleProduct,
    ReceiptTemplate,
    Printer,
    PrintedReceipt,
)
from .printing_service import render_jinja, print_text


@dataclass
class LineData:
    id: int
    product_id: int
    product_name: str
    qty: int
    unit_price_cents: int | None
    kitchen_id: Optional[int]
    pickup_seq: Optional[int]
    options: List[str]
    notes: Optional[str]


def _load_lines(session, order_id: int) -> Dict[int, LineData]:
    q = session.exec(
        select(OrderLine, Product)
        .where(OrderLine.order_id == order_id)
        .join(Product, Product.id == OrderLine.product_id)
    ).all()

    out: Dict[int, LineData] = {}
    for ol, prod in q:
        qty = getattr(ol, "qty", 0) or 0
        kitchen_id = getattr(ol, "kitchen_id", None)
        pickup_seq = getattr(ol, "pickup_seq", None)

        unit_price = getattr(ol, "price_cents", None)
        if unit_price is None:
            unit_price = getattr(prod, "price_cents", None)

        notes = getattr(ol, "note", None) or getattr(ol, "notes", None)
        options: List[str] = []  # se vorrai, popola da ol.options

        out[ol.id] = LineData(
            id=ol.id,
            product_id=prod.id,
            product_name=getattr(prod, "name", f"Prod {prod.id}"),
            qty=qty,
            unit_price_cents=unit_price,
            kitchen_id=kitchen_id,
            pickup_seq=pickup_seq,
            options=options,
            notes=notes,
        )
    return out


def _first_non_empty_line(s: str) -> str:
    for ln in s.splitlines():
        t = ln.strip()
        if t:
            return t[:120]
    return ""


def _ctx_for_rule(
    order: Order,
    k: Optional[Kitchen],
    lines_payload: List[dict],
    rule: ReceiptRule,
    printer: Optional[Printer],
):
    return {
        "now": datetime.now(),
        "event_name": "CALCIONE",
        "order": {
            "id": order.id,
            "datetime": getattr(order, "created_at", None),
            "total_cents": getattr(order, "total_cents", None),
            "paid_method": getattr(order, "paid_method", None),
        },
        "kitchen": (
            {
                "id": k.id,
                "name": k.name,
                "prefix": k.prefix,
                "pickup_seq": (lines_payload[0].get("pickup_seq") if lines_payload else None),
            }
            if k
            else None
        ),
        "lines": lines_payload,
        "rule": {"id": rule.id, "name": rule.name, "mode": rule.mode},
        # NEW: logo della stampante disponibile nel template via [[BITMAP:{{ printer_logo }}|w=384]]
        "printer_logo": getattr(printer, "logo_path", None),
    }


def apply_receipt_rules(session, order_id: int):
    order = session.get(Order, order_id)
    if not order:
        return

    lines = _load_lines(session, order_id)
    remaining = {lid: ld.qty for lid, ld in lines.items()}

    rules: List[ReceiptRule] = session.exec(
        select(ReceiptRule)
        .where(ReceiptRule.enabled == True)  # noqa: E712
        .order_by(ReceiptRule.priority)
    ).all()

    for rule in rules:
        matched_ids: List[int] = []

        if rule.mode == "kds":
            for lid, ld in lines.items():
                if remaining[lid] > 0 and ld.kitchen_id == rule.kitchen_id:
                    matched_ids.append(lid)

        elif rule.mode == "product_set":
            prod_ids = {
                rp.product_id
                for rp in session.exec(
                    select(ReceiptRuleProduct).where(ReceiptRuleProduct.rule_id == rule.id)
                ).all()
            }
            for lid, ld in lines.items():
                if remaining[lid] > 0 and ld.product_id in prod_ids:
                    matched_ids.append(lid)
        else:
            # modalità non riconosciuta
            continue

        if not matched_ids:
            continue

        # Aggregazione linee per il payload del template
        payload: List[dict] = []
        for lid in matched_ids:
            if remaining[lid] <= 0:
                continue
            ld = lines[lid]
            payload.append(
                {
                    "name": ld.product_name,
                    "qty": remaining[lid],
                    "unit_price_cents": ld.unit_price_cents,
                    "options": ld.options,
                    "notes": ld.notes,
                    "pickup_seq": ld.pickup_seq,
                }
            )

        tpl: Optional[ReceiptTemplate] = session.get(ReceiptTemplate, rule.template_id)
        prn: Optional[Printer] = session.get(Printer, rule.printer_id)
        kit: Optional[Kitchen] = session.get(Kitchen, rule.kitchen_id) if rule.mode == "kds" else None

        if not tpl or not prn:
            continue

        ctx = _ctx_for_rule(order, kit, payload, rule, prn)
        text = render_jinja(tpl.body, ctx)
        copies = max(1, int(getattr(rule, "copies", 1) or 1))

        # Stampa + LOG una riga per copia
        for _ in range(copies):
            status = "ok"
            err = None
            if prn and prn.enabled:
                try:
                    print_text(prn.host, prn.port, text, do_cut=bool(tpl.cut))
                except Exception as e:
                    status = "error"
                    err = str(e)[:500]
            else:
                status = "error"
                err = "Printer not enabled or missing"

            log = PrintedReceipt(
                order_id=order.id,
                rule_id=rule.id,
                template_id=tpl.id,
                printer_id=prn.id,
                kitchen_id=(kit.id if kit else None),
                body=text,
                cut=bool(tpl.cut),
                status=status,
                error_text=err,
                summary=_first_non_empty_line(text),
            )
            session.add(log)
            session.commit()

        # Consuma le linee se richiesto
        if rule.consume_lines:
            for lid in matched_ids:
                remaining[lid] = 0

    # opzionale: se restano linee non stampate, potresti loggarle per diagnosi
    # leftover = {lid: qty for lid, qty in remaining.items() if qty > 0}
    # if leftover: ...
