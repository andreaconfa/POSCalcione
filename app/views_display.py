# app/views_display.py
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from .db import get_session
from .models import Kitchen, Ticket, Order, OrderLine

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/display", response_class=HTMLResponse)
def display_all_page(request: Request):
    # sottomenu + vista "tutti"
    with get_session() as session:
        kitchens = session.exec(select(Kitchen).order_by(Kitchen.name)).all()
    return templates.TemplateResponse("display.html", {
        "request": request,
        "mode": "all",
        "kitchen": None,
        "kitchens": kitchens
    })

@router.get("/display/{prefix}", response_class=HTMLResponse)
def display_one_page(prefix: str, request: Request):
    prefix = prefix.upper()
    with get_session() as session:
        kitchens = session.exec(select(Kitchen).order_by(Kitchen.name)).all()
        kitchen = session.exec(select(Kitchen).where(Kitchen.prefix == prefix)).first()
        if not kitchen:
            raise HTTPException(404, f"Nessuna postazione {prefix}")
    return templates.TemplateResponse("display.html", {
        "request": request,
        "mode": "one",
        "kitchen": kitchen,
        "kitchens": kitchens
    })

@router.get("/display/fragment", response_class=HTMLResponse)
def display_all_fragment(request: Request):
    # Ritorna numeri pronti per tutte le postazioni (raggruppati per prefix)
    with get_session() as session:
        kitchens = session.exec(select(Kitchen).order_by(Kitchen.name)).all()
        by_prefix = {k.prefix: [] for k in kitchens}  # 👈 pannelli vuoti pre-creati

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
            by_prefix[k.prefix].append(t.pickup_seq)

    return templates.TemplateResponse(
        "display_fragment.html",
        {
            "request": request,
            "mode": "all",
            "by_prefix": by_prefix,
            "prefix": None,
        },
    )
    
# app/views_display.py (aggiunte)
@router.get("/display-menu", response_class=HTMLResponse)
def display_menu(request: Request):
    with get_session() as session:
        kitchens = session.exec(select(Kitchen).order_by(Kitchen.name)).all()
    return templates.TemplateResponse("display_menu.html", {
        "request": request,
        "kitchens": kitchens
    })

# schermo pulito: TUTTE le postazioni
@router.get("/screen", response_class=HTMLResponse)
def screen_all(request: Request):
    with get_session() as session:
        kitchens = {k.id: k for k in session.exec(select(Kitchen)).all()}
        ready = session.exec(
            select(Ticket)
            .where(Ticket.status == "ready")
            .order_by(Ticket.pickup_seq.desc())
        ).all()
        by_prefix = {}
        for t in ready:
            k = kitchens.get(t.kitchen_id)
            if not k:
                continue
            by_prefix.setdefault(k.prefix.upper(), []).append(t.pickup_seq)

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
    return templates.TemplateResponse("display_screen_split.html", {
        "request": request,
        "prefix": prefix
    })
    
@router.get("/screen_full/{prefix}", response_class=HTMLResponse)
def display_screen_full(request: Request, prefix: str):
    # Torna la schermata dei numeri pura, filtrata SOLO per quella postazione
    return templates.TemplateResponse(
        "display_screen.html",
        {
            "request": request,
            "mode": "one",      # ⬅️ FIX: importantissimo
            "prefix": prefix,   # ⬅️ usato dal JS per filtrare eventi WS
        },
    )

    
@router.get("/display/{prefix}/fragment", response_class=HTMLResponse)
def display_one_fragment(prefix: str, request: Request):
    prefix = prefix.upper()
    with get_session() as session:
        kitchen = session.exec(select(Kitchen).where(Kitchen.prefix == prefix)).first()
        if not kitchen:
            return HTMLResponse("Postazione non trovata", status_code=404)
        ready = session.exec(
            select(Ticket).where(Ticket.kitchen_id == kitchen.id, Ticket.status == "ready").order_by(Ticket.pickup_seq.desc())
        ).all()
        seqs = [t.pickup_seq for t in ready]
    return templates.TemplateResponse("display_fragment.html", {
        "request": request,
        "mode": "one",
        "by_prefix": None,
        "prefix": prefix,
        "seqs": seqs
    })
