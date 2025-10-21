# app/routers/views_receipts.py
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, List, Annotated

from fastapi import APIRouter, Depends, Form, Request, Query, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlmodel import select, delete, desc, Session

from ..paths import UPLOADS_DIR
from ..db import get_session_dep
from ..models import Product, Kitchen, Order
from ..receipts.models_receipts import (
    Printer, ReceiptTemplate, ReceiptRule, ReceiptRuleProduct, PrintedReceipt
)
from ..receipts.rules_engine import apply_receipt_rules
from ..receipts.printing_service import print_text

SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# ---- Dipendenza tipizzata per la sessione ----
SessionDep = Annotated[Session, Depends(get_session_dep)]

# ---------- Helpers ----------
def to_int(val, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(val)
    except Exception:
        return default

def to_bool(val) -> bool:
    return str(val).lower() in ("1", "true", "on", "yes")

def safe_filename(name: str) -> str:
    name = os.path.basename(name)
    name = SAFE_RE.sub("_", name)
    return name[:120]

# ---------- PAGE ----------
# RISTAMPA DA LOG
@router.post("/admin/receipts/logs/reprint")
def reprint_from_log(session: SessionDep, log_id: int = Form(...)):
    rec = session.get(PrintedReceipt, log_id)
    if not rec:
        return RedirectResponse(url="/admin/receipts/logs", status_code=303)

    printer = session.get(Printer, rec.printer_id)
    if not printer or not printer.enabled:
        return RedirectResponse(url="/admin/receipts/logs", status_code=303)

    try:
        print_text(printer.host, printer.port, rec.body, do_cut=rec.cut)
    except Exception:
        pass

    return RedirectResponse(url="/admin/receipts/logs", status_code=303)

# ELIMINA LOG (opzionale)
@router.post("/admin/receipts/logs/delete")
def delete_log(session: SessionDep, log_id: int = Form(...)):
    rec = session.get(PrintedReceipt, log_id)
    if rec:
        session.delete(rec)
        session.commit()
    return RedirectResponse(url="/admin/receipts/logs", status_code=303)

@router.get("/admin/receipts/logs", response_class=HTMLResponse)
def logs_page(
    request: Request,
    session: SessionDep,
    status: Optional[str] = Query(None),
    printer_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
):
    offset = (page - 1) * limit

    base = select(PrintedReceipt)
    if status in ("ok", "error"):
        base = base.where(PrintedReceipt.status == status)
    if printer_id:
        base = base.where(PrintedReceipt.printer_id == printer_id)
    if q:
        like = f"%{q}%"
        base = base.where(
            (PrintedReceipt.summary.ilike(like)) | (PrintedReceipt.body.ilike(like))
        )

    # conteggio robusto
    cnt_stmt = select(func.count()).select_from(base.subquery())
    cnt_res = session.exec(cnt_stmt).one_or_none()
    if cnt_res is None:
        total = 0
    elif isinstance(cnt_res, (tuple, list)):
        total = int(cnt_res[0] or 0)
    else:
        total = int(cnt_res or 0)

    rows = session.exec(
        base.order_by(desc(PrintedReceipt.created_at)).offset(offset).limit(limit)
    ).all()
    printers = session.exec(select(Printer).order_by(Printer.name)).all()

    return templates.TemplateResponse("admin_receipts_logs.html", {
        "request": request,
        "rows": rows,
        "printers": printers,
        "page": page, "limit": limit, "total": total,
        "status": status or "", "printer_id": printer_id or "", "q": q or "",
    })

@router.get("/admin/receipts", response_class=HTMLResponse)
def receipts_page(request: Request, session: SessionDep):
    rules = session.exec(select(ReceiptRule).order_by(ReceiptRule.priority)).all()
    printers = session.exec(select(Printer)).all()
    templates_list = session.exec(select(ReceiptTemplate)).all()
    kitchens = session.exec(select(Kitchen)).all()
    products = session.exec(select(Product)).all()

    logos_dir = UPLOADS_DIR / "logos"
    logos_dir.mkdir(parents=True, exist_ok=True)
    logo_files: List[str] = []
    for p in logos_dir.glob("*"):
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp"}:
            logo_files.append(p.name)

    # mappa rule_id -> [product_id,...]
    rps = session.exec(select(ReceiptRuleProduct)).all()
    rule_products = {}
    for rp in rps:
        rule_products.setdefault(rp.rule_id, []).append(rp.product_id)

    return templates.TemplateResponse("admin_receipts.html", {
        "request": request,
        "rules": rules,
        "printers": printers,
        "templates": templates_list,
        "kitchens": kitchens,
        "products": products,
        "rule_products": rule_products,
        "logo_files": logo_files,
    })

@router.post("/admin/receipts/printer/upload_logo")
def upload_printer_logo(
    session: SessionDep,
    printer_id: int = Form(...),
    file: UploadFile = File(...),
):
    p = session.get(Printer, printer_id)
    if not p:
        return RedirectResponse(url="/admin/receipts", status_code=303)

    ext = Path(file.filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".bmp"}:
        return RedirectResponse(url="/admin/receipts", status_code=303)

    logos_dir = UPLOADS_DIR / "logos"
    logos_dir.mkdir(parents=True, exist_ok=True)
    safe_name = safe_filename(file.filename)
    dest = logos_dir / safe_name

    with dest.open("wb") as f:
        f.write(file.file.read())

    p.logo_path = str(dest)  # percorso locale per PIL
    session.add(p)
    session.commit()

    return RedirectResponse(url="/admin/receipts", status_code=303)

# ---------- RULES ----------
@router.post("/admin/receipts/rule/create")
def create_rule(
    session: SessionDep,
    name: str = Form(...),
    mode: str = Form(...),                    # 'kds' | 'product_set'
    kitchen_id: str = Form(""),
    printer_id: str = Form(...),
    template_id: str = Form(...),
    copies: str = Form("1"),
    priority: str = Form("100"),
    consume_lines: str = Form("on"),
    enabled: str = Form("on"),
    product_ids: str = Form(""),
):
    k_id = to_int(kitchen_id, None)
    prn_id = to_int(printer_id)
    tpl_id = to_int(template_id)
    copies_i = to_int(copies, 1) or 1
    priority_i = to_int(priority, 100)
    consume_b = to_bool(consume_lines)
    enabled_b = to_bool(enabled)

    rule = ReceiptRule(
        name=name, mode=mode, kitchen_id=k_id,
        printer_id=prn_id, template_id=tpl_id,
        copies=copies_i, priority=priority_i,
        consume_lines=consume_b, enabled=enabled_b,
    )
    session.add(rule); session.commit(); session.refresh(rule)

    if mode == "product_set" and product_ids:
        ids = [to_int(x) for x in product_ids.split(",") if x.strip().isdigit()]
        for pid in ids:
            session.add(ReceiptRuleProduct(rule_id=rule.id, product_id=pid))
        session.commit()

    return RedirectResponse(url="/admin/receipts", status_code=303)

@router.post("/admin/receipts/rule/update")
def update_rule(
    session: SessionDep,
    rule_id: int = Form(...),
    name: str = Form(...),
    mode: str = Form(...),
    kitchen_id: str = Form(""),
    printer_id: str = Form(...),
    template_id: str = Form(...),
    copies: str = Form("1"),
    priority: str = Form("100"),
    consume_lines: str = Form("on"),
    enabled: str = Form("on"),
    product_ids: str = Form(""),
):
    rule = session.get(ReceiptRule, rule_id)
    if not rule:
        return RedirectResponse(url="/admin/receipts", status_code=303)

    k_id = to_int(kitchen_id, None)
    prn_id = to_int(printer_id)
    tpl_id = to_int(template_id)
    copies_i = to_int(copies, 1) or 1
    priority_i = to_int(priority, 100)
    consume_b = to_bool(consume_lines)
    enabled_b = to_bool(enabled)

    rule.name = name
    rule.mode = mode
    rule.kitchen_id = k_id
    rule.printer_id = prn_id
    rule.template_id = tpl_id
    rule.copies = copies_i
    rule.priority = priority_i
    rule.consume_lines = consume_b
    rule.enabled = enabled_b
    session.add(rule); session.commit()

    if mode == "product_set":
        session.exec(delete(ReceiptRuleProduct).where(ReceiptRuleProduct.rule_id == rule_id))
        if product_ids:
            ids = [to_int(x) for x in product_ids.split(",") if x.strip().isdigit()]
            for pid in ids:
                if pid is not None:
                    session.add(ReceiptRuleProduct(rule_id=rule_id, product_id=pid))
        session.commit()
    else:
        session.exec(delete(ReceiptRuleProduct).where(ReceiptRuleProduct.rule_id == rule_id))
        session.commit()

    return RedirectResponse(url="/admin/receipts", status_code=303)

@router.post("/admin/receipts/rule/delete")
def delete_rule(session: SessionDep, rule_id: int = Form(...)):
    session.exec(delete(ReceiptRuleProduct).where(ReceiptRuleProduct.rule_id == rule_id))
    r = session.get(ReceiptRule, rule_id)
    if r:
        session.delete(r)
    session.commit()
    return RedirectResponse(url="/admin/receipts", status_code=303)

@router.post("/admin/receipts/rule/reorder")
def reorder_rules(session: SessionDep, order: str = Form(...)):
    ids = [to_int(x) for x in order.split(",") if x.strip().isdigit()]
    pr = 10
    for rid in ids:
        r = session.get(ReceiptRule, rid)
        if r:
            r.priority = pr
            session.add(r)
            pr += 10
    session.commit()
    return RedirectResponse(url="/admin/receipts", status_code=303)

# ---------- TEMPLATES ----------
@router.post("/admin/receipts/template/create")
def create_template(session: SessionDep, name: str = Form(...), body: str = Form(...), cut: str = Form("on")):
    t = ReceiptTemplate(name=name, body=body, cut=to_bool(cut))
    session.add(t); session.commit()
    return RedirectResponse(url="/admin/receipts", status_code=303)

@router.post("/admin/receipts/template/update")
def update_template(session: SessionDep, tpl_id: int = Form(...), name: str = Form(...), body: str = Form(...), cut: str = Form("on")):
    t = session.get(ReceiptTemplate, tpl_id)
    if t:
        t.name = name
        t.body = body
        t.cut = to_bool(cut)
        session.add(t); session.commit()
    return RedirectResponse(url="/admin/receipts", status_code=303)

@router.post("/admin/receipts/template/delete")
def delete_template(session: SessionDep, tpl_id: int = Form(...)):
    # Se referenziato da regole con FK attive, potrebbe fallire.
    t = session.get(ReceiptTemplate, tpl_id)
    if t:
        session.delete(t); session.commit()
    return RedirectResponse(url="/admin/receipts", status_code=303)

# ---------- PRINTERS ----------
@router.post("/admin/receipts/printer/create")
def create_printer(
    session: SessionDep,
    name: str = Form(...), host: str = Form(...), port: str = Form("9100"),
    enabled: str = Form("on"), width_chars: str = Form("32"),
):
    p = Printer(
        name=name, host=host, port=to_int(port, 9100) or 9100,
        enabled=to_bool(enabled), width_chars=to_int(width_chars, 32) or 32
    )
    session.add(p); session.commit()
    return RedirectResponse(url="/admin/receipts", status_code=303)

@router.post("/admin/receipts/printer/update")
def update_printer(
    session: SessionDep,
    printer_id: int = Form(...),
    name: str = Form(...), host: str = Form(...), port: str = Form("9100"),
    enabled: str = Form("on"), width_chars: str = Form("32"),
    selected_logo: str = Form(""),
    remove_logo: str = Form(""),
):
    p = session.get(Printer, printer_id)
    if p:
        p.name = name
        p.host = host
        p.port = to_int(port, 9100) or 9100
        p.enabled = to_bool(enabled)
        p.width_chars = to_int(width_chars, 32) or 32

        logos_dir = UPLOADS_DIR / "logos"
        if remove_logo.lower() in ("on", "true", "1"):
            p.logo_path = None
        elif selected_logo:
            p.logo_path = str((logos_dir / safe_filename(selected_logo)).resolve())

        session.add(p); session.commit()
    return RedirectResponse(url="/admin/receipts", status_code=303)

@router.post("/admin/receipts/printer/delete")
def delete_printer(session: SessionDep, printer_id: int = Form(...)):
    p = session.get(Printer, printer_id)
    if p:
        session.delete(p); session.commit()
    return RedirectResponse(url="/admin/receipts", status_code=303)

# ---------- PREVIEW ----------
@router.post("/admin/receipts/preview")
def preview_rule(session: SessionDep, order_id: int = Form(...), rule_id: Optional[int] = Form(None)):
    # per semplicità usiamo l'applicazione delle regole normali (stampa reale)
    apply_receipt_rules(session, order_id)
    return RedirectResponse(url="/admin/receipts", status_code=303)
