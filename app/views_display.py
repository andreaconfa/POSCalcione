# app/views_display.py
from __future__ import annotations

from datetime import datetime
from time import time
from typing import Annotated

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select, Session

from .db import get_session_dep
from .models import Kitchen, Ticket, Order, OrderLine

# Dipendenza tipizzata
SessionDep = Annotated[Session, Depends(get_session_dep)]

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# ------- piccola cache per Kitchen by prefix (riduce query durante il polling) -------
_KCACHE: dict[str, dict[str, object]] = {}
_KCACHE_TTL = 5.0  # secondi

def _kitchen_by_prefix(prefix: str, session: Session) -> Kitchen | None:
    key = prefix.upper().strip()
    now = time()
    hit = _KCACHE.get(key)
    if hit and (now - hit["t"] < _KCACHE_TTL):
        return hit["v"]  # type: ignore
    k = session.exec(select(Kitchen).where(Kitchen.prefix == key)).first()
    _KCACHE[key] = {"v": k, "t": now}
    return k

# -------------------------------------------------------------------------------------

@router.get("/display", response_class=HTMLResponse)
def display_all_page(request: Request, session: SessionDep):
    # sottomenu + vista "tutti"
    kitchens = session.exec(select(Kitchen).order_by(Kitchen.name)).all()
    return templates.TemplateResponse(
        "display.html",
        {"request": request, "mode": "all", "kitchen": None, "kitchens": kitchens},
    )

@router.get("/display/{prefix}", response_class=HTMLResponse)
def display_one_page(prefix: str, request: Request, session: SessionDep):
    prefix = prefix.upper()
    kitchens = session.exec(select(Kitchen).order_by(Kitchen.name)).all()
    kitchen = _kitchen_by_prefix(prefix, session)
    if not kitchen:
        raise HTTPException(404, f"Nessuna postazione {prefix}")
    return templates.TemplateResponse(
        "display.html",
        {"request": request, "mode": "one", "kitchen": kitchen, "kitchens": kitchens},
    )

@router.get("/display/fragment", response_class=HTMLResponse)
def display_all_fragment(request: Request, session: SessionDep):
    # Ritorna numeri pronti per tutte le postazioni (raggruppati per prefix)
    kitchens = session.exec(select(Kitchen).order_by(Kitchen.name)).all()
    by_prefix: dict[str, list[int]] = {k.prefix: [] for k in kitchens}  # pannelli vuoti pre-creati

    ready = session.exec(
        select(Ticket)
        .where(Ticket.status == "ready")
        .order_by(Ticket.pickup_seq.desc())
    ).all()

    k_by_id = {k.id: k for k in kitchens}
    for t in ready:
        k = k_by_id.get(t.kitchen_id)
        if not k:
            continue
        by_prefix[k.prefix].append(int(t.pickup_seq))

    return templates.TemplateResponse(
        "display_fragment.html",
        {"request": request, "mode": "all", "by_prefix": by_prefix, "prefix": None},
    )

# --- menu (lista postazioni) ------------------------------------------------

@router.get("/display-menu", response_class=HTMLResponse)
def display_menu(request: Request, session: SessionDep):
    kitchens = session.exec(select(Kitchen).order_by(Kitchen.name)).all()
    return templates.TemplateResponse("display_menu.html", {"request": request, "kitchens": kitchens})

# --- schermi ---------------------------------------------------------------

@router.get("/screen", response_class=HTMLResponse)
def screen_all(request: Request, session: SessionDep):
    kitchens = {k.id: k for k in session.exec(select(Kitchen)).all()}
    ready = session.exec(
        select(Ticket)
        .where(Ticket.status == "ready")
        .order_by(Ticket.pickup_seq.desc())
    ).all()

    by_prefix: dict[str, list[int]] = {}
    for t in ready:
        k = kitchens.get(t.kitchen_id)
        if not k:
            continue
        by_prefix.setdefault(k.prefix.upper(), []).append(int(t.pickup_seq))

    return templates.TemplateResponse(
        "display_screen.html",
        {
            "request": request,
            "mode": "all",
            "prefix": None,
            # bootstrap iniziale:
            "initial_mode": "all",
            "initial_by_prefix": by_prefix,
            "initial_prefix": None,
            "initial_seqs": None,
        },
    )

@router.get("/screen/{prefix}", response_class=HTMLResponse)
def display_screen_one(request: Request, prefix: str):
    # usa sempre il layout split (1/3 numeri + 2/3 playlist)
    return templates.TemplateResponse(
        "display_screen_split.html",
        {"request": request, "prefix": prefix},
    )

@router.get("/screen_full/{prefix}", response_class=HTMLResponse)
def display_screen_full(request: Request, prefix: str):
    # Torna la schermata dei numeri pura, filtrata SOLO per quella postazione
    return templates.TemplateResponse(
        "display_screen.html",
        {
            "request": request,
            "mode": "one",      # ⬅️ importante
            "prefix": prefix,   # ⬅️ usato dal JS per filtrare eventi WS
        },
    )

# --- frammento per singola postazione --------------------------------------

@router.get("/display/{prefix}/fragment", response_class=HTMLResponse)
def display_one_fragment(prefix: str, request: Request, session: SessionDep):
    prefix = prefix.upper()
    kitchen = _kitchen_by_prefix(prefix, session)
    if not kitchen:
        return HTMLResponse("Postazione non trovata", status_code=404)

    ready = session.exec(
        select(Ticket)
        .where(Ticket.kitchen_id == kitchen.id, Ticket.status == "ready")
        .order_by(Ticket.pickup_seq.desc())
    ).all()
    seqs = [int(t.pickup_seq) for t in ready]

    return templates.TemplateResponse(
        "display_fragment.html",
        {"request": request, "mode": "one", "by_prefix": None, "prefix": prefix, "seqs": seqs},
    )
