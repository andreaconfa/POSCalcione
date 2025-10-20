# app/views_kds.py
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from .db import get_session
from .ws import manager  # se già presente nel progetto originale
from .models import Kitchen, Ticket, Order, OrderLine, Product
from .models_customizations import OrderLineOption  # <-- per leggere le opzioni

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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


@router.get("/kds/{prefix}", response_class=HTMLResponse)
def kds_page(prefix: str, request: Request):
    """Pagina host KDS (carica il fragment via fetch)."""
    with get_session() as session:
        k = session.exec(select(Kitchen).where(Kitchen.prefix == prefix.upper())).first()
        if not k:
            raise HTTPException(status_code=404, detail="Postazione non trovata")
    return templates.TemplateResponse(
        "kds.html",
        {"request": request, "kitchen": k, "prefix": k.prefix, "kitchen_name": k.name},
    )


    
# Pagina elenco: bottoni che puntano a /kds/{prefix}
@router.get("/kds", response_class=HTMLResponse)
def kds_index(request: Request):
    with get_session() as s:
        kitchens = s.exec(select(Kitchen).order_by(Kitchen.prefix.asc())).all()
    return templates.TemplateResponse("kds_index.html", {
        "request": request,
        "kitchens": kitchens,
    })

# Rotta richiesta: /kds/{PREFISSO} → apre il KDS "di cucina"
@router.get("/kds/{prefix}")
def kds_open(prefix: str):
    # Se il tuo KDS vive su /display/{prefix}, reindirizziamo lì:
    return RedirectResponse(url=f"/display/{prefix}", status_code=307)    

@router.get("/kds/select", response_class=HTMLResponse)
def kds_select_redirect():
    return RedirectResponse(url="/kds", status_code=303)
    
@router.get("/kds/{prefix}/fragment", response_class=HTMLResponse)
def kds_fragment(prefix: str, request: Request):
    """Ritorna solo il corpo (cards) – usato dal polling e dai poke WS."""
    with get_session() as session:
        k = session.exec(select(Kitchen).where(Kitchen.prefix == prefix.upper())).first()
        if not k:
            return HTMLResponse("<div class='tag'>Nessuna postazione trovata</div>", status_code=404)

        # Ticket visibili (come l’originale): niente delivered
        tickets = session.exec(
            select(Ticket)
            .where(
                Ticket.kitchen_id == k.id,
                Ticket.status.in_(("queued", "prepping", "ready")),
            )
            .order_by(Ticket.pickup_seq)
        ).all()

        # Cache prodotti per nome
        prod_map = {p.id: p for p in session.exec(select(Product)).all()}

        view = []
        for t in tickets:
            # 🔒 Filtro corretto: stesse condizioni con cui sono state create le orderlines per quel ticket
            lines = session.exec(
                select(OrderLine).where(
                    OrderLine.kitchen_id == k.id,
                    OrderLine.pickup_seq == t.pickup_seq,
                    OrderLine.order_id == t.order_id,  # <-- CRITICO per evitare “linee fantasma”
                )
            ).all()

            # Opzioni per riga (se presenti)
            line_ids = [ln.id for ln in lines]
            opts_by_line: dict[int, list[OrderLineOption]] = {}
            if line_ids:
                all_opts = session.exec(
                    select(OrderLineOption).where(OrderLineOption.orderline_id.in_(line_ids))
                ).all()
                for o in all_opts:
                    opts_by_line.setdefault(o.orderline_id, []).append(o)

            items = []
            for ln in lines:
                p = prod_map.get(ln.product_id)
                if not p:
                    continue
                item = {"name": p.name, "qty": ln.qty}
                line_opts = opts_by_line.get(ln.id) or []
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

    # NB: passiamo 'prefix' esattamente come l’originale si aspettava
    resp = templates.TemplateResponse(
        "kds_fragment.html",
        {"request": request, "prefix": k.prefix, "kitchen_name": k.name, "tickets": view},
    )
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp



from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

def _state_response(request: Request, prefix: str, payload: dict):
    # Se arrivi dal fetch JS, rispondi JSON. Altrimenti fai redirect gentile alla pagina KDS
    if request.headers.get("X-Fetch") == "1":
        return JSONResponse(payload)
    return RedirectResponse(url=f"/kds/{prefix}", status_code=303)

@router.post("/kds/{prefix}/{seq}/preparing")
async def kds_preparing(request: Request, prefix: str, seq: int):
    prefix = (prefix or "").upper()
    with get_session() as session:
        k = session.exec(select(Kitchen).where(Kitchen.prefix == prefix)).first()
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

    # — WS: aggiorna KDS + ping display per eventuale riallineamento
    try:
        await manager.broadcast_json({"type": "ticket_update", "prefix": prefix, "seq": seq, "status": "prepping"})
        await manager.broadcast_json({"type": "display_refresh", "prefix": prefix})
    except Exception as _e:
        pass

    return _state_response(request, prefix, {"ok": True, "status": "prepping"})

@router.post("/kds/{prefix}/{seq}/ready")
async def kds_ready(request: Request, prefix: str, seq: int):
    prefix = (prefix or "").upper()
    with get_session() as session:
        k = session.exec(select(Kitchen).where(Kitchen.prefix == prefix)).first()
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

    # — WS: aggiorna KDS + TRIGGER per DISPLAY (numero grande)
    # notifica sia update che "ready" (per popup display)
    await manager.broadcast_json({
        "type": "ticket_update",
        "prefix": prefix,
        "seq": seq,
        "status": "ready"
    })
    await manager.broadcast_json({
        "type": "ticket_ready",
        "prefix": prefix,
        "seq": seq,
        # se vuoi anche il nome cucina per l’overlay:
        # "kitchen_name": "Casetta" if px=="C" else ("Esterno" if px=="E" else f"Postazione {px}")
    })

    return _state_response(request, prefix, {"ok": True, "status": "ready"})

@router.post("/kds/{prefix}/{seq}/delivered")
async def kds_delivered(request: Request, prefix: str, seq: int):
    prefix = (prefix or "").upper()
    with get_session() as session:
        k = session.exec(select(Kitchen).where(Kitchen.prefix == prefix)).first()
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

    # — WS: aggiorna KDS + notifica display per rimuovere/pulire
    try:
        await manager.broadcast_json({"type": "ticket_update", "prefix": prefix, "seq": seq, "status": "delivered"})
        await manager.broadcast_json({"type": "ticket_delivered", "prefix": prefix, "seq": seq})
    except Exception:
        pass

    return _state_response(request, prefix, {"ok": True, "status": "delivered"})
