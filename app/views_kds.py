# app/views_kds.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Dict, List

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select, Session

from .db import get_session_dep
from .ws import manager
from .models import Kitchen, Ticket, Order, OrderLine, Product
from .models_customizations import OrderLineOption

# Dipendenza tipizzata
SessionDep = Annotated[Session, Depends(get_session_dep)]

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# --- util -------------------------------------------------------------------

def _age_human(dt: datetime | None) -> str:
    if not dt:
        return ""
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        sec = int((datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return ""
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

# Micro-cache Kitchen by prefix (riduce query durante polling)
from time import time
_KCACHE: Dict[str, dict] = {}
_KCACHE_TTL = 5.0  # secondi

def _kitchen_by_prefix(prefix: str, session: Session) -> Kitchen | None:
    key = (prefix or "").upper().strip()
    now = time()
    hit = _KCACHE.get(key)
    if hit and (now - hit["t"] < _KCACHE_TTL):
        return hit["v"]  # type: ignore
    k = session.exec(select(Kitchen).where(Kitchen.prefix == key)).first()
    _KCACHE[key] = {"v": k, "t": now}
    return k

# --- pagine -----------------------------------------------------------------

@router.get("/kds", response_class=HTMLResponse)
def kds_index(request: Request, session: SessionDep):
    kitchens = session.exec(select(Kitchen).order_by(Kitchen.prefix.asc())).all()
    return templates.TemplateResponse("kds_index.html", {"request": request, "kitchens": kitchens})

@router.get("/kds/{prefix}", response_class=HTMLResponse)
def kds_page(prefix: str, request: Request, session: SessionDep):
    """Pagina host KDS (carica il fragment via fetch)."""
    k = _kitchen_by_prefix(prefix, session)
    if not k:
        raise HTTPException(status_code=404, detail="Postazione non trovata")
    return templates.TemplateResponse(
        "kds.html",
        {"request": request, "kitchen": k, "prefix": k.prefix, "kitchen_name": k.name},
    )

# (se vuoi tenere un redirect “compat” spostalo su un path diverso)
@router.get("/kds_redirect/{prefix}")
def kds_open_redirect(prefix: str):
    return RedirectResponse(url=f"/display/{prefix}", status_code=307)

@router.get("/kds/select", response_class=HTMLResponse)
def kds_select_redirect():
    return RedirectResponse(url="/kds", status_code=303)

# --- fragment (polling/refresh) --------------------------------------------

@router.get("/kds/{prefix}/fragment", response_class=HTMLResponse)
def kds_fragment(request: Request, session: SessionDep, prefix: str):
    """Ritorna solo il corpo (cards) – usato dal polling e dai poke WS."""
    px = (prefix or "").upper()

    k = session.exec(select(Kitchen).where(Kitchen.prefix == px)).first()
    if not k:
        return HTMLResponse("<div class='tag'>Nessuna postazione trovata</div>", status_code=404)

    # 👇 estrai SUBITO scalari e usa solo questi (no ORM object dopo)
    k_id = int(k.id)
    k_prefix = (k.prefix or "").upper()
    k_name = k.name or ""

    tickets = session.exec(
        select(Ticket)
        .where(
            Ticket.kitchen_id == k_id,
            Ticket.status.in_(("queued", "prepping", "ready")),
        )
        .order_by(Ticket.pickup_seq)
    ).all()

    # Cache prodotti per nome (mappa scalare -> scalare)
    prod_map = {int(p.id): p.name for p in session.exec(select(Product)).all()}

    # Prepara le righe
    view = []
    for t in tickets:
        lines = session.exec(
            select(OrderLine).where(
                OrderLine.kitchen_id == k_id,
                OrderLine.pickup_seq == t.pickup_seq,
                OrderLine.order_id == t.order_id,
            )
        ).all()

        line_ids = [int(ln.id) for ln in lines]
        opts_by_line: dict[int, list[OrderLineOption]] = {}
        if line_ids:
            all_opts = session.exec(
                select(OrderLineOption).where(OrderLineOption.orderline_id.in_(line_ids))
            ).all()
            for o in all_opts:
                opts_by_line.setdefault(int(o.orderline_id), []).append(o)

        items = []
        for ln in lines:
            pname = prod_map.get(int(ln.product_id))
            if not pname:
                continue
            item = {"name": pname, "qty": int(ln.qty or 0)}
            line_opts = opts_by_line.get(int(ln.id)) or []
            if line_opts:
                item["options"] = [
                    {"name": (o.prompt_name or ""), "value": (o.value or "")}
                    for o in line_opts
                ]
            items.append(item)

        created = None
        if t.order_id:
            order = session.get(Order, t.order_id)
            created = getattr(order, "created_at", None)

        view.append({
            "seq": t.pickup_seq,
            "status": t.status,
            "age": _age_human(created),
            "items": items,
        })

    resp = templates.TemplateResponse(
        "kds_fragment.html",
        {"request": request, "prefix": k_prefix, "kitchen_name": k_name, "tickets": view},
    )
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# --- helpers ----------------------------------------------------------------

def _state_response(request: Request, prefix: str, payload: dict):
    # Se arrivi dal fetch JS, rispondi JSON. Altrimenti redirect gentile alla pagina KDS
    if request.headers.get("X-Fetch") == "1":
        return JSONResponse(payload)
    return RedirectResponse(url=f"/kds/{prefix}", status_code=303)

# --- azioni KDS -------------------------------------------------------------

@router.post("/kds/{prefix}/{seq}/preparing")
async def kds_preparing(request: Request, prefix: str, seq: int, session: SessionDep):
    px = (prefix or "").upper()
    k = _kitchen_by_prefix(px, session)
    if not k:
        raise HTTPException(status_code=404, detail="Cucina non trovata")

    tk = session.exec(
        select(Ticket).where(Ticket.kitchen_id == k.id, Ticket.pickup_seq == seq)
    ).first()
    if not tk:
        raise HTTPException(status_code=404, detail="Ticket non trovato")
    if tk.status == "delivered":
        raise HTTPException(status_code=409, detail="Ticket già consegnato")

    tk.status = "prepping"
    session.add(tk)
    session.commit()

    try:
        await manager.broadcast_json({"type": "ticket_update", "prefix": px, "seq": seq, "status": "prepping"})
        await manager.broadcast_json({"type": "display_refresh", "prefix": px})
    except Exception:
        pass

    return _state_response(request, px, {"ok": True, "status": "prepping"})

@router.post("/kds/{prefix}/{seq}/ready")
async def kds_ready(request: Request, prefix: str, seq: int, session: SessionDep):
    px = (prefix or "").upper()
    k = _kitchen_by_prefix(px, session)
    if not k:
        raise HTTPException(status_code=404, detail="Cucina non trovata")

    tk = session.exec(
        select(Ticket).where(Ticket.kitchen_id == k.id, Ticket.pickup_seq == seq)
    ).first()
    if not tk:
        raise HTTPException(status_code=404, detail="Ticket non trovato")
    if tk.status == "delivered":
        raise HTTPException(status_code=409, detail="Ticket già consegnato")

    tk.status = "ready"
    session.add(tk)
    session.commit()

    try:
        await manager.broadcast_json({"type": "ticket_update", "prefix": px, "seq": seq, "status": "ready"})
        await manager.broadcast_json({"type": "ticket_ready", "prefix": px, "seq": seq})
    except Exception:
        pass

    return _state_response(request, px, {"ok": True, "status": "ready"})

@router.post("/kds/{prefix}/{seq}/delivered")
async def kds_delivered(request: Request, prefix: str, seq: int, session: SessionDep):
    px = (prefix or "").upper()
    k = _kitchen_by_prefix(px, session)
    if not k:
        raise HTTPException(status_code=404, detail="Cucina non trovata")

    tk = session.exec(
        select(Ticket).where(Ticket.kitchen_id == k.id, Ticket.pickup_seq == seq)
    ).first()
    if not tk:
        raise HTTPException(status_code=404, detail="Ticket non trovato")

    tk.status = "delivered"
    session.add(tk)
    session.commit()

    try:
        await manager.broadcast_json({"type": "ticket_update", "prefix": px, "seq": seq, "status": "delivered"})
        await manager.broadcast_json({"type": "ticket_delivered", "prefix": px, "seq": seq})
    except Exception:
        pass

    return _state_response(request, px, {"ok": True, "status": "delivered"})
