# app/views_admin.py
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from datetime import datetime,date, timedelta, time
from io import StringIO
import csv
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select, func
from pathlib import Path  
from .printing import print_kitchen_receipt, print_category_receipt
import secrets   
from .db import get_session
from .models import Kitchen, Product, Order, OrderLine, Ticket, Category
from .paths import STATIC_DIR, UPLOADS_DIR
from sqlalchemy import func, desc, update, delete


from .db import get_session
from .models import Product, Category, Kitchen
from .models_customizations import ProductPrompt

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# === le tue route admin esistenti RESTANO UGUALI ===
# (index, add/update product, categorie, cucine, close day, ecc.)

# --- STORICO ORDINI (admin) -----------------------------------------------
from datetime import datetime, date, time, timedelta
from fastapi import Query
from typing import Optional
from sqlmodel import select

@router.get("/admin/orders", response_class=HTMLResponse)
def admin_orders(
    request: Request,
    start: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="YYYY-MM-DD"),
    start_time: Optional[str] = Query(None, description="HH:MM"),
    end_time: Optional[str] = Query(None, description="HH:MM"),
    limit: int = Query(300, ge=1, le=2000),
):
    """
    Storico ordini con filtro data+ora. Default: oggi 00:00 -> oggi 23:59:59.
    """
    # --- parse range ---
    today = date.today()
    start_d = datetime.strptime(start, "%Y-%m-%d").date() if start else today
    end_d   = datetime.strptime(end,   "%Y-%m-%d").date() if end   else today

    start_t = datetime.strptime(start_time, "%H:%M").time() if start_time else time(0, 0, 0)
    end_t   = datetime.strptime(end_time,   "%H:%M").time() if end_time   else time(23, 59, 59)

    dt_from = datetime.combine(start_d, start_t)
    dt_to_ex = datetime.combine(end_d, end_t) + timedelta(seconds=1)  # esclusivo

    with get_session() as session:
        # Ordini nel range
        orders = session.exec(
            select(Order)
            .where(Order.created_at >= dt_from, Order.created_at < dt_to_ex)
            .order_by(Order.created_at.desc())
            .limit(limit)
        ).all()

        order_ids = [int(o.id) for o in orders]
        items_by_order: dict[int, list[tuple[str, int]]] = {}
        ticket_labels_by_order: dict[int, str] = {}

        if order_ids:
            # Righe ordine
            lines = session.exec(
                select(OrderLine).where(OrderLine.order_id.in_(order_ids))
            ).all()

            # Mappa prodotti
            prod_ids = list({int(l.product_id) for l in lines})
            products_map = {int(p.id): p for p in session.exec(select(Product).where(Product.id.in_(prod_ids))).all()} if prod_ids else {}

            # Riepilogo per ordine (compresso per nome)
            tmp_rows: dict[int, list[tuple[str, int]]] = {}
            for l in lines:
                pid = int(l.product_id)
                oid = int(l.order_id)
                name = products_map.get(pid).name if products_map.get(pid) else f"Prod #{pid}"
                tmp_rows.setdefault(oid, []).append((name, int(l.qty or 0)))

            for oid, rows in tmp_rows.items():
                acc: dict[str, int] = {}
                for nm, q in rows:
                    acc[nm] = acc.get(nm, 0) + q
                items_by_order[oid] = sorted(acc.items(), key=lambda x: x[0].lower())

            # Ticket -> etichette "PREFISSO-SEQ", senza JOIN (robusto)
            tks = session.exec(
                select(Ticket).where(Ticket.order_id.in_(order_ids))
            ).all()
            kitchens_map = {int(k.id): k for k in session.exec(select(Kitchen)).all()}

            # Accumula come tuple (prefix, seq) per ordinare e deduplicare
            tk_acc: dict[int, set[tuple[str, int]]] = {}
            for tk in tks:
                if tk.pickup_seq is None:
                    continue
                k = kitchens_map.get(int(tk.kitchen_id)) if tk.kitchen_id is not None else None
                if not k:
                    continue
                pref = (k.prefix or "").upper().strip()
                seq  = int(tk.pickup_seq)
                if not pref:
                    continue
                oid = int(tk.order_id)
                tk_acc.setdefault(oid, set()).add((pref, seq))

            for oid, pairs in tk_acc.items():
                ordered = sorted(pairs, key=lambda p: (p[0], p[1]))  # per prefisso, poi seq
                labels = [f"{p}-{s}" for (p, s) in ordered]
                ticket_labels_by_order[oid] = ", ".join(labels)

    return templates.TemplateResponse(
        "admin_orders.html",
        {
            "request": request,
            "orders": orders,
            "items_by_order": items_by_order,
            "start": start_d.strftime("%Y-%m-%d"),
            "end": end_d.strftime("%Y-%m-%d"),
            "start_time": start_t.strftime("%H:%M"),
            "end_time": end_t.strftime("%H:%M"),
            "limit": limit,
            "total_count": len(orders),
            "ticket_labels_by_order": ticket_labels_by_order,  # 👈 stringa già pronta
        },
    )


# ---------- NUOVO: customizzazioni prodotto ----------
@router.get("/admin/sales", response_class=HTMLResponse)
def admin_sales(request: Request):
    qs = request.query_params

    # --- leggi parametri ---
    start_date_s = qs.get("start_date")  # "YYYY-MM-DD"
    end_date_s   = qs.get("end_date")    # "YYYY-MM-DD"
    start_time_s = qs.get("start_time")  # "HH:MM"
    end_time_s   = qs.get("end_time")    # "HH:MM"

    # default: oggi 00:00 → oggi 23:59:59
    today = date.today()
    start_d = date.fromisoformat(start_date_s) if start_date_s else today
    end_d   = date.fromisoformat(end_date_s)   if end_date_s   else today

    # default orari
    start_t = time.fromisoformat(start_time_s) if start_time_s else time(0, 0, 0)
    # chiudiamo il range in modo inclusivo sull'ultimo secondo
    end_t   = time.fromisoformat(end_time_s)   if end_time_s   else time(23, 59, 59)

    start_dt = datetime.combine(start_d, start_t)
    end_dt   = datetime.combine(end_d, end_t)

    # per SQL conviene usare un upper-bound esclusivo:
    end_dt_exclusive = end_dt + timedelta(seconds=1)

    with get_session() as session:
        # totale incassi nel range
        total_cents = session.exec(
            select(func.coalesce(func.sum(Order.total_cents), 0))
            .where(Order.created_at >= start_dt)
            .where(Order.created_at < end_dt_exclusive)
        ).one()

        # venduto per prodotto (somma quantità)
        rows = session.exec(
            select(
                Product.name.label("product"),
                func.coalesce(func.sum(OrderLine.qty), 0).label("qty"),
                func.coalesce(func.sum(OrderLine.qty * Product.price_cents), 0).label("gross_cents"),
            )
            .join(Order, Order.id == OrderLine.order_id)
            .join(Product, Product.id == OrderLine.product_id)
            .where(Order.created_at >= start_dt)
            .where(Order.created_at < end_dt_exclusive)
            .group_by(Product.id, Product.name)
            .order_by(desc("qty"))
        ).all()

    # render
    return templates.TemplateResponse(
        "admin_sales.html",  # o la tua admin.html se incorpori lì la sezione
        {
            "request": request,
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "start_time": start_t.strftime("%H:%M"),
            "end_time": end_t.strftime("%H:%M"),
            "total_cents": total_cents or 0,
            "rows": rows,
        }
    )
    
@router.get("/admin/product/{product_id}/prompts", response_class=HTMLResponse)
def admin_product_prompts(product_id: int, request: Request):
    with get_session() as session:
        p = session.get(Product, product_id)
        if not p:
            return HTMLResponse("Prodotto non trovato", status_code=404)
        prompts = session.exec(
            select(ProductPrompt).where(ProductPrompt.product_id == product_id)
        ).all()
    return templates.TemplateResponse(
        "admin_prompts.html",
        {"request": request, "product": p, "prompts": prompts}
    )

@router.post("/admin/product/{product_id}/prompts/save")
def admin_product_prompts_save(
    product_id: int,
    prompt_ids: list[str] = Form(default=[]),
    names: list[str] = Form(default=[]),
    kinds: list[str] = Form(default=[]),
    requireds: list[str] = Form(default=[]),     # "on" quando spuntato
    choices_csvs: list[str] = Form(default=[]),  # "A; B; C"
    deltas: list[str] = Form(default=[]),        # centesimi
):
    rows = []
    L = max(len(names), len(kinds), len(requireds), len(choices_csvs), len(deltas), len(prompt_ids))
    for i in range(L):
        nm = (names[i] if i < len(names) else "").strip()
        if not nm:
            continue
        kd = (kinds[i] if i < len(kinds) else "single").strip()
        req_bool = ((requireds[i] if i < len(requireds) else "") == "on")
        chs = (choices_csvs[i] if i < len(choices_csvs) else "").strip()
        ch_list = [c.strip() for c in chs.split(";") if c.strip()] if chs else None
        try:
            dc = int(deltas[i]) if i < len(deltas) and (deltas[i] or "").strip() else 0
        except Exception:
            dc = 0
        rows.append((nm, kd, req_bool, ch_list, dc))

    with get_session() as session:
        session.exec(
            ProductPrompt.__table__.delete().where(ProductPrompt.product_id == product_id)
        )
        for (nm, kd, req, ch_list, dc) in rows:
            session.add(ProductPrompt(
                product_id=product_id,
                name=nm, kind=kd, required=req,
                choices=ch_list, delta_cents=dc,
            ))
        session.commit()

    return RedirectResponse(url="/admin?ok=1", status_code=303)


def _int_or_none(v):
    if v in (None, "", "null", "None"):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
        
@router.post("/admin/print-test")
def admin_print_test():
    """
    Stampa 1 scontrino demo per ogni cucina (Casetta/Esterno, ecc.)
    + 1 scontrino demo per 'categoria senza routing'.
    Non modifica DB, non incrementa numerazioni reali.
    """
    # Piccole righe fittizie (usa 2-3 prodotti reali se esistono)
    with get_session() as session:
        products = session.exec(select(Product).limit(3)).all()
        if products:
            demo_lines = [(p.name, 1) for p in products]
        else:
            demo_lines = [("Prodotto demo A", 1), ("Prodotto demo B", 2)]

        kitchens = session.exec(select(Kitchen)).all()
        # Scontrino di test per ogni cucina: usa seq fittizia 999
        for k in kitchens:
            try:
                print_kitchen_receipt(k.prefix, 999, demo_lines)
            except Exception as e:
                print(f"[ADMIN][PRINT-TEST] Kitchen {k.name} errore: {e}")

        # Scontrino per 'categoria senza routing'
        cat = session.exec(
            select(Category).where(Category.kitchen_id.is_(None))
        ).first()
        cat_name = cat.name if cat else "Senza categoria"
        try:
            print_category_receipt(cat_name, demo_lines)
        except Exception as e:
            print(f"[ADMIN][PRINT-TEST] Categoria NR errore: {e}")

    return RedirectResponse(url="/admin?printok=1", status_code=303)
    

@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    with get_session() as session:
        kitchens = session.exec(select(Kitchen).order_by(Kitchen.prefix.asc())).all()
        products = session.exec(select(Product).order_by(Product.name)).all()
        categories = session.exec(select(Category).order_by(Category.name)).all()

        # carica customizzazioni per tutti i prodotti
        prompts_by_product = {}
        if products:
            pids = [p.id for p in products]
            rows = session.exec(
                select(ProductPrompt).where(ProductPrompt.product_id.in_(pids))
            ).all()
            for r in rows:
                choices_csv = "; ".join(r.choices or []) if r.choices else ""
                prompts_by_product.setdefault(r.product_id, []).append(type("X", (), {
                    "id": r.id,
                    "name": r.name,
                    "kind": r.kind,
                    "required": bool(r.required),
                    "choices_csv": choices_csv,
                    "price_delta_cents": int(r.delta_cents or 0),
                }))

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "kitchens": kitchens,
            "products": products,
            "categories": categories,
            "prompts_by_product": prompts_by_product,
        },
    )
 
# ------- Categorie -------
@router.post("/admin/category/add")
def admin_category_add(name: str = Form(...), kitchen_id: str | None = Form(None)):
    with get_session() as session:
        session.add(Category(name=name.strip(), kitchen_id=_int_or_none(kitchen_id)))
        session.commit()
    return RedirectResponse(url="/admin?cat_added=1", status_code=303)

@router.post("/admin/category/delete")
def admin_category_delete(category_id: int = Form(...)):
    with get_session() as session:
        cat = session.get(Category, category_id)
        if cat:
            session.delete(cat)
            session.commit()
    return RedirectResponse(url="/admin?cat_deleted=1", status_code=303)

@router.post("/admin/category/update-kitchen")
def admin_category_update_kitchen(category_id: int = Form(...), kitchen_id: str | None = Form(None)):
    with get_session() as session:
        cat = session.get(Category, category_id)
        if cat:
            cat.kitchen_id = _int_or_none(kitchen_id)
            session.add(cat); session.commit()
    return RedirectResponse(url="/admin?cat_updated=1", status_code=303)
 
@router.post("/admin/category/update", include_in_schema=False)
def admin_category_update(
    request: Request,
    category_id: int = Form(...),
    kitchen_id: str = Form(""),
    color_hex: str = Form("#0ea5e9"),
):
    # kitchen_id può essere vuoto -> None
    k_id = int(kitchen_id) if (kitchen_id or "").strip() else None
    color = (color_hex or "").strip() or "#0ea5e9"
    with get_session() as session:
        c = session.exec(select(Category).where(Category.id == category_id)).first()
        if not c:
            return RedirectResponse("/admin?cat_updated=0", status_code=303)
        c.kitchen_id = k_id
        c.color_hex = color if color.startswith("#") and len(color) in (4, 7) else "#0ea5e9"
        session.add(c)
        session.commit()
    return RedirectResponse("/admin?cat_updated=1", status_code=303) 
# -------- Prodotti --------

@router.post("/admin/product/add")
def admin_product_add(
    name: str = Form(...),
    price_eur: float = Form(...),
    kitchen_id: str | None = Form(None),
    category_id: str | None = Form(None),
    image_url: str | None = Form(None),
    image_file: UploadFile | None = File(None),  # << nuovo
):
    price_cents = int(round(price_eur * 100))
    # se carico un file, ha priorità rispetto a image_url
    uploaded = _save_image_file(image_file)
    final_image = uploaded or (image_url.strip() if image_url else None)

    with get_session() as session:
        session.add(Product(
            name=name.strip(),
            price_cents=price_cents,
            kitchen_id=_int_or_none(kitchen_id),
            category_id=_int_or_none(category_id),
            image_url=final_image,
        ))
        session.commit()
    return RedirectResponse(url="/admin?ok=1", status_code=303)
@router.post("/admin/product/update")
def admin_product_update(
    product_id: int = Form(...),
    name: str = Form(...),
    price_eur: float = Form(...),
    kitchen_id: str | None = Form(None),
    category_id: str | None = Form(None),
    image_url: str | None = Form(None),
    image_file: UploadFile | None = File(None),
):
    price_cents = int(round(float(price_eur) * 100))
    with get_session() as session:
        p = session.get(Product, product_id)
        if not p:
            raise HTTPException(status_code=404, detail="Prodotto non trovato")

        p.name = name.strip()
        p.price_cents = price_cents
        p.kitchen_id = _int_or_none(kitchen_id)
        p.category_id = _int_or_none(category_id)

        uploaded = _save_image_file(image_file)
        if uploaded:
            p.image_url = uploaded
        else:
            # Se non ho caricato file:
            # - se è stato passato un image_url -> aggiorno con quello (anche vuoto -> None)
            # - se image_url è None -> lascio invariato
            if image_url is not None:
                p.image_url = (image_url.strip() or None)

        session.add(p)
        session.commit()

    return RedirectResponse(url="/admin?updated=1", status_code=303)

    
@router.post("/admin/product/delete")
def admin_product_delete(product_id: int = Form(...)):
    with get_session() as session:
        p = session.get(Product, product_id)
        if p:
            session.delete(p)
            session.commit()
    return RedirectResponse(url="/admin?deleted=1", status_code=303)
    
def _save_image_file(image_file: UploadFile | None) -> str | None:
    """Salva il file immagine su /static/uploads e ritorna l'URL /static/uploads/xxx oppure None."""
    if not image_file or not image_file.filename:
        return None
    if not (image_file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Il file caricato non è un'immagine")

    suffix = Path(image_file.filename).suffix.lower() or ".png"
    name = f"{secrets.token_hex(8)}{suffix}"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / name
    with dest.open("wb") as f:
        f.write(image_file.file.read())
    return f"/static/uploads/{name}"
    

# -------- Postazioni (Kitchen) CRUD --------
@router.post("/admin/kitchen/add")
def admin_kitchen_add(name: str = Form(...), prefix: str = Form(...)):
    name = (name or "").strip()
    prefix = (prefix or "").strip().upper()
    if not name or not prefix:
        return RedirectResponse(url="/admin?kit_msg=invalid", status_code=303)
    with get_session() as session:
        # evita duplicati per prefix
        exists = session.exec(select(Kitchen).where(Kitchen.prefix == prefix)).first()
        if exists:
            # aggiorna il nome se cambia
            exists.name = name
            session.add(exists)
            session.commit()
            return RedirectResponse(url="/admin?kit_msg=updated", status_code=303)
        k = Kitchen(name=name, prefix=prefix, next_seq=1)
        session.add(k)
        session.commit()
    return RedirectResponse(url="/admin?kit_msg=created", status_code=303)

@router.post("/admin/kitchen/delete")
def delete_kitchen(kitchen_id: int = Form(...)):
    with get_session() as session:
        k = session.get(Kitchen, kitchen_id)
        if not k:
            return RedirectResponse("/admin?kdel_missing=1", status_code=303)

        # 1) Scollega prodotti/categorie (se ancora presenti)
        session.exec(update(Product).where(Product.kitchen_id == kitchen_id).values(kitchen_id=None))
        session.exec(update(Category).where(Category.kitchen_id == kitchen_id).values(kitchen_id=None))

        # 2) Cancella ticket 'delivered'
        session.exec(delete(Ticket).where(Ticket.kitchen_id == kitchen_id, Ticket.status == "delivered"))

        # 3) Verifica se restano ticket non consegnati
        remaining = session.exec(
            select(Ticket.id).where(Ticket.kitchen_id == kitchen_id)
        ).first()

        if remaining:
            # Ci sono ancora ticket attivi -> blocco l'eliminazione
            session.commit()
            return RedirectResponse("/admin?kdel_block_active=1", status_code=303)

        # 4) Ora posso eliminare la kitchen
        session.delete(k)
        session.commit()

    return RedirectResponse("/admin?kdel_ok=1", status_code=303)

# -------- Numerazioni --------
@router.post("/admin/seq/reset")
def admin_seq_reset(kitchen_id: int = Form(...)):
    with get_session() as session:
        k = session.get(Kitchen, kitchen_id)
        if k:
            k.next_seq = 1
            session.add(k)
            session.commit()
    return RedirectResponse(url="/admin?seq_reset=1", status_code=303)

@router.post("/admin/seq/align")
def admin_seq_align(kitchen_id: int = Form(...)):
    """Imposta next_seq a (max(pickup_seq) + 1) per quella cucina."""
    with get_session() as session:
        k = session.get(Kitchen, kitchen_id)
        if k:
            max_seq = session.exec(
                select(func.max(Ticket.pickup_seq)).where(Ticket.kitchen_id == k.id)
            ).one()
            k.next_seq = (max_seq or 0) + 1
            session.add(k)
            session.commit()
    return RedirectResponse(url="/admin?seq_align=1", status_code=303)

# -------- Utilità --------
@router.post("/admin/close_day")
def admin_close_day():
    """Tutti i ticket -> delivered e azzero numerazioni."""
    with get_session() as session:
        tickets = session.exec(select(Ticket).where(Ticket.status != "delivered")).all()
        for t in tickets:
            t.status = "delivered"  # type: ignore
            session.add(t)
        for k in session.exec(select(Kitchen)).all():
            k.next_seq = 1
            session.add(k)
        session.commit()
    return RedirectResponse(url="/admin?closed=1", status_code=303)

@router.post("/admin/clear_delivered")
def admin_clear_delivered():
    """Opzionale: pulisce tutti i ticket marcati delivered (mantiene i dati ordini)."""
    with get_session() as session:
        delivered = session.exec(select(Ticket).where(Ticket.status == "delivered")).all()
        for t in delivered:
            session.delete(t)
        session.commit()
    return RedirectResponse(url="/admin?cleared=1", status_code=303)

# -------- Export CSV --------
@router.get("/admin/export.csv", response_class=PlainTextResponse)
def admin_export_csv(
    start: date = Query(..., description="YYYY-MM-DD"),
    end: date = Query(..., description="YYYY-MM-DD (incluso)")
):
    """Export ordini + righe in CSV per range date (UTC)."""
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())

    out = StringIO()
    w = csv.writer(out)
    w.writerow(["order_id", "created_at_utc", "paid_method", "total_cents", "product", "qty", "price_cents", "kitchen_prefix"])

    with get_session() as session:
        orders = session.exec(
            select(Order).where(Order.created_at >= start_dt, Order.created_at <= end_dt)
        ).all()
        # Precarica ausiliari
        kitchens = {k.id: k for k in session.exec(select(Kitchen)).all()}
        products = {p.id: p for p in session.exec(select(Product)).all()}

        for o in orders:
            lines = session.exec(select(OrderLine).where(OrderLine.order_id == o.id)).all()
            for ln in lines:
                p = products.get(ln.product_id)
                k = kitchens.get(ln.kitchen_id)
                w.writerow([
                    o.id,
                    o.created_at.isoformat(),
                    o.paid_method,
                    o.total_cents,
                    p.name if p else "",
                    ln.qty,
                    p.price_cents if p else "",
                    k.prefix if k else "",
                ])

    csv_data = out.getvalue()
    headers = {
        "Content-Disposition": f'attachment; filename="export_{start}_{end}.csv"'
    }
    return PlainTextResponse(csv_data, headers=headers)

@router.post("/admin/kitchen/unlink_pc")
def unlink_products_categories(kitchen_id: int = Form(...)):
    """
    Scollega prodotti e categorie dalla postazione (kitchen_id = NULL).
    I ticket restano da spostare o la postazione non si può eliminare.
    """
    with get_session() as session:
        k = session.get(Kitchen, kitchen_id)
        if not k:
            return RedirectResponse("/admin?kfix_err_missing=1", status_code=303)

        session.exec(update(Product).where(Product.kitchen_id == kitchen_id).values(kitchen_id=None))
        session.exec(update(Category).where(Category.kitchen_id == kitchen_id).values(kitchen_id=None))
        session.commit()

    return RedirectResponse("/admin?kfix_unlinked=1", status_code=303)