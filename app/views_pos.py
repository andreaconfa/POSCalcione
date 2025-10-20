from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select
from .db import get_session
from .models import Product, Order, OrderLine, Kitchen, Ticket
from fastapi.templating import Jinja2Templates
from .ws import manager 
from .models import Product, Order, OrderLine, Kitchen, Ticket, Category
from .printing import print_kitchen_receipt, print_category_receipt
from datetime import datetime
from fastapi.responses import JSONResponse
from .models_customizations import ProductPrompt, OrderLineOption
from urllib.parse import quote
from app.receipts.rules_engine import apply_receipt_rules
from sqlalchemy import func, desc
from .receipts.models_receipts import PrintedReceipt, Printer
from .receipts.printing_service import print_text
from flask import Blueprint, request, jsonify
from typing import Any, Dict, Optional, List 
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

def _get_last_order(session):
    return session.exec(select(Order).order_by(desc(Order.id))).first()

def _fetch_order_from_your_store(order_id: str) -> Optional[Dict[str, Any]]:
    try:
        oid = int(str(order_id).strip())
    except Exception:
        return None

    with get_session() as session:
        order = session.get(Order, oid)
        if not order:
            return None

        # Order lines + product
        rows = session.exec(
            select(OrderLine, Product)
            .where(OrderLine.order_id == order.id)
            .join(Product, Product.id == OrderLine.product_id)
        ).all()
        if not rows:
            return {"id": order.id, "items": [], "total_cents": int(order.total_cents or 0)}

        # raccogliamo tutte le opzioni in un colpo solo
        ol_ids = [ol.id for ol, _ in rows]
        opts_rows = session.exec(
            select(OrderLineOption).where(OrderLineOption.orderline_id.in_(ol_ids))
        ).all()
        opts_by_ol: Dict[int, List[OrderLineOption]] = {}
        for r in opts_rows:
            opts_by_ol.setdefault(r.orderline_id, []).append(r)

        items: List[Dict[str, Any]] = []
        running_total = 0

        for ol, prod in rows:
            base = int(getattr(prod, "price_cents", 0) or 0)
            ol_opts = opts_by_ol.get(ol.id, [])

            # somma dei delta
            delta = sum(int(getattr(o, "price_delta_cents", 0) or 0) for o in ol_opts)
            unit_price_cents = max(0, base + delta)
            qty = int(getattr(ol, "qty", 0) or 0)

            running_total += unit_price_cents * qty

            items.append({
                "product_id": int(prod.id),
                "name": getattr(prod, "name", f"Prod {prod.id}"),
                "qty": qty,
                "unit_price_cents": unit_price_cents,
                "options": [
                    {
                        "name": getattr(o, "prompt_name", "") or "",
                        "value": getattr(o, "value", "") or "",
                        "delta": int(getattr(o, "price_delta_cents", 0) or 0),
                    }
                    for o in ol_opts
                ],
            })

        total_cents = int(order.total_cents or 0)
        if total_cents <= 0:
            total_cents = running_total

        return {
            "id": order.id,
            "items": items,
            "total_cents": total_cents,
        }

def _to_cents(val: Any) -> Optional[int]:
    """Converte numeri/str in centesimi; None se non interpretabile."""
    if val is None:
        return None
    try:
        # se stringa con virgola
        if isinstance(val, str):
            v = val.strip().replace("€", "").replace(",", ".")
            return int(round(float(v) * 100))
        # se numero
        return int(round(float(val) * 100))
    except Exception:
        return None


def _extract_unit_price_cents(item: Dict[str, Any]) -> int:
    """
    Prova diversi alias per il prezzo unitario.
    Se non li trova, tenta: line_total_cents / qty.
    """
    # 1) campi già in cents
    for k in ("unit_price_cents", "price_cents", "cents"):
        v = item.get(k)
        if isinstance(v, int) and v >= 0:
            return v

    # 2) campi in euro (float/str)
    for k in ("unit_price", "price", "unit", "unitary"):
        cents = _to_cents(item.get(k))
        if cents is not None and cents >= 0:
            return cents

    # 3) ricava dai totali riga
    qty = item.get("qty") or 0
    if qty:
        # prima prova *_cents
        for k in ("line_total_cents", "total_cents"):
            v = item.get(k)
            if isinstance(v, int) and v >= 0:
                return int(v // qty)  # floor per sicurezza
        # poi prova totali in euro
        for k in ("line_total", "total"):
            cents = _to_cents(item.get(k))
            if cents is not None and cents >= 0:
                return int(cents // qty)

    # 4) fallback hard 0
    return 0


@router.get("/pos/util/order_summary")
def pos_order_summary(order_id: str = "") -> Dict[str, Any]:
    if not order_id:
        return {"ok": False, "error": "order_id mancante"}

    data = _fetch_order_from_your_store(order_id)
    if not data:
        # anche se non trovi l’ordine, restituisci un payload valido
        return {"ok": True, "order": {"id": str(order_id), "items": [], "total_cents": 0}}

    raw_items: List[Dict[str, Any]] = data.get("items") or []

    # Normalizza items: assicura name, qty, unit_price_cents, options
    items: List[Dict[str, Any]] = []
    running_total = 0
    for it in raw_items:
        qty = int(it.get("qty") or 0)
        upc = _extract_unit_price_cents(it)
        running_total += upc * qty
        items.append({
            "name": it.get("name", ""),
            "qty": qty,
            "unit_price_cents": upc,
            "options": it.get("options") or [],
            # se vuoi far arrivare anche product_id alla modale:
            **({"product_id": it.get("product_id")} if it.get("product_id") is not None else {})
        })

    # Preferisci total_cents fornito dal backend, altrimenti calcolato
    total_cents = data.get("total_cents")
    if not isinstance(total_cents, int):
        total_cents = running_total

    return {
        "ok": True,
        "order": {
            "id": str(data.get("id") or order_id),
            "items": items,
            "total_cents": int(total_cents or 0),
        }
    }
    
@router.get("/pos", response_class=HTMLResponse)
def pos_page(request: Request):
    with get_session() as session:
        products = session.exec(
            select(Product).order_by(func.lower(Product.name))  # <-- ordinamento A→Z
        ).all()
        kitchens_list = session.exec(select(Kitchen)).all()
        kitchens_map = {k.id: k for k in kitchens_list}
        categories = {c.id: c for c in session.exec(select(Category)).all()}
    return templates.TemplateResponse(
        "pos.html",
        {
            "request": request,
            "products": products,
            "kitchens": kitchens_map,
            "kitchens_map": kitchens_map,
            "categories": categories,
        },
    )
@router.post("/pos/util/reprint_order")
def pos_reprint_order(order_id: int = Form(...), session=Depends(get_session)):
    rows = session.exec(
        select(PrintedReceipt).where(PrintedReceipt.order_id == order_id).order_by(PrintedReceipt.id)
    ).all()
    if not rows:
        return JSONResponse({"ok": False, "error": "Nessuno scontrino per questo ordine"})

    ok = 0
    fail = 0
    for r in rows:
        prn = session.get(Printer, r.printer_id)
        if not prn or not prn.enabled:
            fail += 1
            continue
        try:
            print_text(prn.host, prn.port, r.body, do_cut=r.cut)
            ok += 1
        except Exception:
            fail += 1

    return JSONResponse({"ok": True, "reprinted": ok, "failed": fail, "order_id": order_id})


@router.get("/pos/util/orders")
def pos_orders(limit: int = 20, session=Depends(get_session)):
    # ultimi N ordini (cap a 100)
    orders = session.exec(
        select(Order).order_by(desc(Order.id)).limit(max(1, min(limit, 10)))
    ).all()
    if not orders:
        return JSONResponse({"ok": True, "orders": []})

    ids = [o.id for o in orders]

    # log scontrini per conteggi OK/ERR
    logs = session.exec(
        select(PrintedReceipt).where(PrintedReceipt.order_id.in_(ids)).order_by(PrintedReceipt.id)
    ).all()
    logs_by_order = {}
    for r in logs:
        logs_by_order.setdefault(r.order_id, []).append(r)

    # righe prodotti per riepilogo
    rows = session.exec(
        select(OrderLine, Product)
        .where(OrderLine.order_id.in_(ids))
        .join(Product, Product.id == OrderLine.product_id)
    ).all()
    items_by_order: dict[int, dict[str, int]] = {}
    for ol, prod in rows:
        m = items_by_order.setdefault(ol.order_id, {})
        name = getattr(prod, "name", f"Prod {prod.id}")
        qty = getattr(ol, "qty", 0) or 0
        m[name] = m.get(name, 0) + qty

    data = []
    for o in orders:
        lst = logs_by_order.get(o.id, [])
        ok = sum(1 for x in lst if x.status == "ok")
        err = sum(1 for x in lst if x.status != "ok")
        items_map = items_by_order.get(o.id, {})
        items = [{"name": n, "qty": q} for n, q in items_map.items()]
        data.append({
            "order_id": o.id,
            "created_at": getattr(o, "created_at", None).strftime("%d/%m/%Y %H:%M:%S") if getattr(o, "created_at", None) else "",
            "receipts_total": len(lst),
            "receipts_ok": ok,
            "receipts_err": err,
            "items": items,  # <-- riepilogo prodotti
        })

    return JSONResponse({"ok": True, "orders": data})

@router.get("/pos/util/last_receipts")
def pos_last_receipts(session=Depends(get_session)):
    order = _get_last_order(session)
    if not order:
        return JSONResponse({"ok": True, "order_id": None, "receipts": []})

    rows = session.exec(
        select(PrintedReceipt).where(PrintedReceipt.order_id == order.id).order_by(PrintedReceipt.id)
    ).all()

    # Mappa id->nome stampante per comodità
    pmap = {p.id: p.name for p in session.exec(select(Printer)).all()}

    data = []
    for r in rows:
        data.append({
            "log_id": r.id,
            "created_at": r.created_at.strftime("%d/%m/%Y %H:%M:%S"),
            "printer_id": r.printer_id,
            "printer_name": pmap.get(r.printer_id, f"#{r.printer_id}"),
            "status": r.status,
            "summary": r.summary or "",
        })
    return JSONResponse({"ok": True, "order_id": order.id, "receipts": data})

@router.post("/pos/util/reprint_one")
def pos_reprint_one(log_id: int = Form(...), session=Depends(get_session)):
    rec = session.get(PrintedReceipt, log_id)
    if not rec:
        return JSONResponse({"ok": False, "error": "Log non trovato"})

    prn = session.get(Printer, rec.printer_id)
    if not prn or not prn.enabled:
        return JSONResponse({"ok": False, "error": "Stampante non disponibile"})

    try:
        print_text(prn.host, prn.port, rec.body, do_cut=rec.cut)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]})
@router.post("/pos/util/reprint_last_all")
def pos_reprint_last_all(session=Depends(get_session)):
    order = _get_last_order(session)
    if not order:
        return JSONResponse({"ok": False, "error": "Nessun ordine trovato"})

    rows = session.exec(
        select(PrintedReceipt).where(PrintedReceipt.order_id == order.id).order_by(PrintedReceipt.id)
    ).all()
    if not rows:
        return JSONResponse({"ok": False, "error": "Nessun scontrino per l'ultimo ordine"})

    ok = 0
    fail = 0
    for r in rows:
        prn = session.get(Printer, r.printer_id)
        if not prn or not prn.enabled:
            fail += 1
            continue
        try:
            print_text(prn.host, prn.port, r.body, do_cut=r.cut)
            ok += 1
        except Exception:
            fail += 1

    return JSONResponse({"ok": True, "reprinted": ok, "failed": fail, "order_id": order.id})

@router.get("/api/products/{product_id}/prompts")
def api_product_prompts(product_id: int):
    with get_session() as session:
        rows = session.exec(
            select(ProductPrompt).where(ProductPrompt.product_id == product_id)
        ).all()
        out = []
        for r in rows:
            out.append({
                "name": r.name,
                "kind": r.kind,
                "required": bool(r.required),
                "choices": r.choices or [],
                "delta": int(r.delta_cents or 0),
            })
        return JSONResponse(out)


@router.post("/pos/checkout")
async def pos_checkout(request: Request, paid_method: str = Form("cash")):
    form = await request.form()

    # ========= NUOVO: prova a leggere cart_json (carrello con opzioni) =========
    cart_lines: list[dict] = []
    cart_json = form.get("cart_json")
    if cart_json:
        try:
            import json
            parsed = json.loads(cart_json)
            if isinstance(parsed, list):
                # atteso: [{product_id, name, qty, unit_price_cents, options:[{name,value,delta}]}]
                cart_lines = parsed
        except Exception as e:
            print(f"[POS][WARN] cart_json parse error: {e}")

    # ========= LEGACY: qty_* (fallback) =========
    items: list[tuple[int, int]] = []  # [(pid, qty)] usato per routing/print
    if cart_lines:
        # aggrega per prodotto per mantenere invariata la logica di routing/print
        agg: dict[int, int] = {}
        for cl in cart_lines:
            try:
                pid = int(cl.get("product_id"))
                qty = int(cl.get("qty") or 1)
            except Exception:
                continue
            agg[pid] = agg.get(pid, 0) + qty
        items = list(agg.items())
    else:
        # comportamento originale
        for key, val in form.items():
            if key.startswith("qty_"):
                try:
                    pid = int(key.split("_", 1)[1])
                    qty = int(val or "0")
                except ValueError:
                    continue
                if qty > 0:
                    items.append((pid, qty))

    if not items:
        return RedirectResponse(url="/pos", status_code=303)

    prefixes: list[str] = []
    # job di stampa costruiti PRIMA di chiudere la sessione e usati DOPO
    kitchen_print_jobs: list[tuple[str, int, list[tuple[str, int]]]] = []  # (prefix, seq, [(name, qty)])
    category_print_jobs: list[tuple[str, list[tuple[str, int]]]] = []      # (category_name, [(name, qty)])
    # numeri per notifica UI POS (riempiti dentro la sessione)
    nums_for_ui: list[str] = []

    with get_session() as session:
        # cache di riferimento
        pid_list = [pid for pid, _ in items]
        prods = {p.id: p for p in session.exec(select(Product).where(Product.id.in_(pid_list))).all()}
        kitchens = {k.id: k for k in session.exec(select(Kitchen)).all()}
        categories = {c.id: c for c in session.exec(select(Category)).all()}

        order = Order(
            paid_method=paid_method,
            created_at=datetime.utcnow(),
            total_cents=0  # <-- valore iniziale per rispettare NOT NULL
        )
        session.add(order)
        session.flush()  # ottieni order.id

        # risolve la kitchen del prodotto o None
        def resolve_kitchen_id(p: Product) -> int | None:
            if p.kitchen_id:
                return p.kitchen_id
            if p.category_id:
                cat = categories.get(p.category_id)
                if cat and cat.kitchen_id is not None:
                    return cat.kitchen_id
            return None  # no routing

        # cucine coinvolte
        kitchens_involved: set[int] = set()
        for pid, _ in items:
            p = prods.get(pid)
            if not p:
                continue
            k_id = resolve_kitchen_id(p)
            if k_id is not None:
                kitchens_involved.add(k_id)

        # numerazione per cucina
        assigned_seq: dict[int, int] = {}
        for k_id in kitchens_involved:
            k = kitchens[k_id]
            assigned_seq[k_id] = k.next_seq
            if k.prefix not in prefixes:
                prefixes.append(k.prefix)

        # --> prepara anche i numeri per il toast del POS (es. ["C-12","E-5"])
        nums_for_ui = [f"{kitchens[k_id].prefix.upper()}-{seq}" for k_id, seq in assigned_seq.items()]

        # ticket per cucina
        for k_id, seq in assigned_seq.items():
            session.add(Ticket(kitchen_id=k_id, order_id=order.id, pickup_seq=seq))

        # ========= RIGHE ORDINE + TOTALE =========
        total = 0

        if cart_lines:
            # NUOVO: una OrderLine per OGNI riga del carrello (preserva opzioni)
            for cl in cart_lines:
                try:
                    pid = int(cl.get("product_id"))
                    qty = int(cl.get("qty") or 1)
                except Exception:
                    continue
                p = prods.get(pid)
                if not p:
                    continue

                unit_price = cl.get("unit_price_cents")
                try:
                    unit_price = int(unit_price) if unit_price is not None else None
                except Exception:
                    unit_price = None
                options = cl.get("options") or []
                if unit_price is None:
                    unit_price = int(p.price_cents or 0) + sum(int(o.get("delta") or 0) for o in options)

                total += unit_price * qty

                k_id = resolve_kitchen_id(p)
                seq = assigned_seq.get(k_id) if k_id is not None else None
                ol = OrderLine(
                    order_id=order.id,
                    product_id=p.id,
                    qty=qty,
                    kitchen_id=k_id,     # può essere NULL se no routing
                    pickup_seq=seq       # può essere NULL se no routing
                )
                session.add(ol)
                session.flush()  # id per le opzioni

                # salva le opzioni della riga
                for opt in options:
                    session.add(OrderLineOption(
                        orderline_id=ol.id,
                        prompt_name=str(opt.get("name") or ""),
                        value=str(opt.get("value") or ""),
                        price_delta_cents=int(opt.get("delta") or 0),
                    ))
        else:
            # ORIGINALE: una OrderLine per (pid, qty) senza opzioni
            for pid, qty in items:
                p = prods.get(pid)
                if not p:
                    continue
                total += p.price_cents * qty
                k_id = resolve_kitchen_id(p)
                seq = assigned_seq.get(k_id) if k_id is not None else None
                session.add(OrderLine(
                    order_id=order.id,
                    product_id=p.id,
                    qty=qty,
                    kitchen_id=k_id,
                    pickup_seq=seq
                ))

        order.total_cents = total
        session.add(order)

        # incrementa le sequenze per le cucine effettivamente usate
        for k_id in kitchens_involved:
            kitchens[k_id].next_seq += 1
            session.add(kitchens[k_id])
        
        apply_receipt_rules(session, order.id)

        session.commit()

        # Notifica dettagliata per ogni KDS (compat con tuo display-client)
        created_by_prefix = {}  # es: {"C":[101,102], "E":[103]}
        tks = session.exec(select(Ticket).where(Ticket.order_id == order.id)).all()
        kitchens_now = {k.id: k for k in session.exec(select(Kitchen)).all()}
        for tk in tks:
            k = kitchens_now.get(tk.kitchen_id)
            if not k:
                continue
            px = k.prefix.upper()
            created_by_prefix.setdefault(px, []).append(tk.id)

    # ----------- STAMPA (fuori dalla sessione) -----------
    try:
        for prefix, seq, lines in kitchen_print_jobs:
            print_kitchen_receipt(prefix, seq, lines)
        for cat_name, lines in category_print_jobs:
            print_category_receipt(cat_name, lines)
    except Exception as e:
        print(f"[PRINT][WARN] stampa fallita: {e}")

    # notifica WS (solo se ci sono cucine coinvolte)
    if prefixes:
        await manager.broadcast_json({"type": "tickets_created", "kitchens": prefixes})

    # redirect con numeri per toast POS
    nums_param = quote(",".join(nums_for_ui)) if nums_for_ui else ""
    return RedirectResponse(
        url=f"/pos?ok=1&order_id={order.id}&total_cents={int(order.total_cents or 0)}&nums={nums_param}",
        status_code=303
    )
