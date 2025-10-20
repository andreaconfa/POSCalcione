# app/summary.py
from typing import List, Optional, Dict, Any, Set

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sqlalchemy import or_, and_, case
from sqlalchemy.sql import func
from sqlmodel import select

from .db import get_db
# ⬇️ ADATTA questi import ai tuoi modelli reali
from .models import Kitchen, Order, OrderLine, Product  # noqa: F401

router = APIRouter(prefix="/kds", tags=["kds"])

# Stati considerati "attivi" (da preparare/in preparazione)
DEFAULT_INCLUDE_STATES: Set[str] = {
    "queue", "queued", "inqueue", "in_queue", "waiting",
    "preparing", "prepping",
}

# Stati "finiti" da escludere sempre (anche se c'è colonna stato)
DEFAULT_EXCLUDE_STATES: Set[str] = {
    "ready", "completed", "done", "delivered", "served", "closed",
    "canceled", "cancelled", "void", "voided",
    # varianti italiane comuni
    "pronto", "consegnato", "chiuso", "annullato", "evaso", "finito",
}

class ProductSummary(BaseModel):
    name: str
    total_qty: int

def _first_attr(model, names: List[str]):
    for n in names:
        if hasattr(model, n):
            return getattr(model, n)
    return None

def _norm_csv(csv: Optional[str]) -> Optional[Set[str]]:
    if not csv:
        return None
    out: Set[str] = set()
    for p in csv.split(","):
        s = (p or "").strip().lower().replace(" ", "").replace("-", "_")
        if s:
            out.add(s)
    return out or None

@router.get("/{prefix}/summary", response_model=List[ProductSummary])
def kds_summary(
    prefix: str,
    session = Depends(get_db),
    debug: int = Query(0, ge=0, le=1, description="1 per output diagnostico"),
    states: Optional[str] = Query(None, description="Stati 'attivi' da includere (CSV)"),
    exclude_states: Optional[str] = Query(None, description="Stati da escludere (CSV)"),
):
    """
    Riepilogo prodotti per la cucina:
    - Conta solo righe base (no opzioni) se i campi lo permettono.
    - Somma 'quantità da fare': remaining_qty → qty-prepared_qty → qty.
    - Filtra *sempre* fuori gli ordini finiti (ready/delivered/closed/canceled...).
    - Se c'è colonna stato, include solo gli 'stati attivi' (queue/preparing...).
    - Filtra per cucina: Order.kitchen_id o Product.kitchen_id.
    """
    # 1) Kitchen (case-insensitive)
    kitchen = session.exec(
        select(Kitchen).where(func.lower(Kitchen.prefix) == prefix.lower())
    ).first()
    if not kitchen:
        if debug:
            return JSONResponse({"prefix": prefix.upper(), "error": "kitchen_not_found", "rows": []})
        raise HTTPException(status_code=404, detail=f"Kitchen '{prefix}' non trovata")

    # 2) Qty "da fare"
    if hasattr(OrderLine, "remaining_qty"):
        qty_expr = func.coalesce(getattr(OrderLine, "remaining_qty"), OrderLine.qty, 0)
    elif hasattr(OrderLine, "prepared_qty"):
        qty_expr = case(
            ((OrderLine.qty - getattr(OrderLine, "prepared_qty")) > 0,
             (OrderLine.qty - getattr(OrderLine, "prepared_qty"))),
            else_=0
        )
    else:
        qty_expr = func.coalesce(OrderLine.qty, 0)

    # 3) Stato: colonne possibili
    status_col = _first_attr(Order, ["status", "state", "order_status", "kds_status"])
    include_states: Set[str] = _norm_csv(states) or set(DEFAULT_INCLUDE_STATES)
    ban_states: Set[str] = _norm_csv(exclude_states) or set(DEFAULT_EXCLUDE_STATES)

    # Normalizzatore SQL di stato (se esiste colonna)
    def _status_norm(col):
        # minuscolo + toglie spazi e '-'
        return func.lower(func.replace(func.replace(col, " ", ""), "-", "_"))

    # 4) Filtro "NO FINITI" (sempre attivo)
    always_exclude_filter = True
    if status_col is not None:
        status_norm = _status_norm(status_col)
        always_exclude_filter = ~status_norm.in_(list(ban_states))  # NOT IN stati finiti

    # 5) Filtro "INCLUSI ATTIVI" (solo se hai colonna stato)
    include_active_filter = True
    if status_col is not None:
        status_norm = _status_norm(status_col)
        include_active_filter = status_norm.in_(list(include_states))

    # 6) Filtro "attivi" euristico (in assenza di colonna stato) — esclude ready/delivered/closed/canceled via campi tipici
    active_fields_filter = True
    if status_col is None:
        clauses = []
        # timestamp fine ciclo
        for fname in ["delivered_at", "completed_at", "closed_at", "canceled_at", "cancelled_at", "voided_at", "served_at"]:
            if hasattr(Order, fname):
                clauses.append(getattr(Order, fname).is_(None))
        # ready_at (se vuoi escludere anche i "già pronti")
        if hasattr(Order, "ready_at"):
            clauses.append(getattr(Order, "ready_at").is_(None))
        # flag booleani
        for fname in ["is_delivered", "is_ready", "is_closed", "is_cancelled", "is_canceled", "voided", "served"]:
            if hasattr(Order, fname):
                clauses.append(func.coalesce(getattr(Order, fname), False) == False)
        active_fields_filter = and_(*clauses) if clauses else True

    # 7) Esclusione opzioni/personalizzazioni
    option_filters = []
    if hasattr(OrderLine, "parent_line_id"):
        option_filters.append(getattr(OrderLine, "parent_line_id") == None)  # noqa: E711
    if hasattr(OrderLine, "is_option"):
        option_filters.append(func.coalesce(getattr(OrderLine, "is_option"), False) == False)
    base_line_filter = and_(*option_filters) if option_filters else True

    # 8) SELECT base
    base_stmt = (
        select(
            Product.name.label("name"),
            func.sum(qty_expr).label("total_qty"),
        )
        .select_from(OrderLine)
        .join(Order, Order.id == OrderLine.order_id)
        .join(Product, Product.id == OrderLine.product_id)
        .where(
            func.coalesce(qty_expr, 0) > 0,
            base_line_filter,
            always_exclude_filter,  # ← escludi sempre “finiti”
            include_active_filter,  # ← se esiste colonna stato, limita a “attivi”
            active_fields_filter,   # ← se NON esiste colonna stato, usa euristica
        )
        .group_by(Product.name)
    )

    rows: List[Any] = []
    matched_by: Optional[str] = None

    # 9) Filtri cucina
    if hasattr(Order, "kitchen_id"):
        stmt1 = base_stmt.where(Order.kitchen_id == kitchen.id) \
                         .order_by(func.sum(qty_expr).desc(), Product.name.asc())
        rows = session.exec(stmt1).all()
        if rows:
            matched_by = "order.kitchen_id"

    if not rows and hasattr(Product, "kitchen_id"):
        stmt2 = base_stmt.where(Product.kitchen_id == kitchen.id) \
                         .order_by(func.sum(qty_expr).desc(), Product.name.asc())
        rows = session.exec(stmt2).all()
        if rows:
            matched_by = "product.kitchen_id"

    if not rows and hasattr(Order, "kitchen_id") and hasattr(Product, "kitchen_id"):
        stmt3 = base_stmt.where(or_(Order.kitchen_id == kitchen.id, Product.kitchen_id == kitchen.id)) \
                         .order_by(func.sum(qty_expr).desc(), Product.name.asc())
        rows = session.exec(stmt3).all()
        if rows:
            matched_by = "either"

    # 10) Debug
    if debug:
        status_counts: Dict[str, int] = {}
        if status_col is not None:
            status_norm = _status_norm(status_col)
            sc_base = (
                select(status_norm.label("st"), func.count(func.distinct(Order.id)))
                .select_from(OrderLine)
                .join(Order, Order.id == OrderLine.order_id)
                .join(Product, Product.id == OrderLine.product_id)
                .where(
                    or_(
                        (hasattr(Order, "kitchen_id") and (Order.kitchen_id == kitchen.id)),
                        (hasattr(Product, "kitchen_id") and (Product.kitchen_id == kitchen.id)),
                    ),
                    base_line_filter,
                )
                .group_by(status_norm)
            )
            for st, cnt in session.exec(sc_base):
                status_counts[st] = int(cnt)

        return JSONResponse({
            "prefix": prefix.upper(),
            "kitchen_id": int(kitchen.id),
            "kitchen_name": getattr(kitchen, "name", prefix.upper()),
            "include_states": sorted(list(include_states)),
            "exclude_states": sorted(list(ban_states)),
            "matched_by": matched_by or "none",
            "status_counts": status_counts,
            "rows": [{"name": n, "total_qty": int(q or 0)} for (n, q) in rows],
        })

    # 11) Output
    return [{"name": n, "total_qty": int(q or 0)} for (n, q) in rows]
