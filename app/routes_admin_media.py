# app/router_admin_media.py
from __future__ import annotations

from typing import Annotated
from pathlib import Path
import shutil, uuid

from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete
from sqlmodel import Session, select

from .db import get_session_dep
from .models_media import MediaAsset, MediaType, Playlist, PlaylistItem

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")
UPLOAD_DIR = Path("app/static/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Dipendenza tipizzata per la sessione
SessionDep = Annotated[Session, Depends(get_session_dep)]

@router.get("/admin/media", response_class=HTMLResponse)
def media_admin(request: Request, session: SessionDep, screen: str = "C"):
    assets = session.exec(select(MediaAsset).order_by(MediaAsset.id.desc())).all()
    plname = f"screen_{screen}"
    pl = session.exec(select(Playlist).where(Playlist.name == plname)).first()

    vm_items = []
    if pl:
        rows = session.exec(
            select(PlaylistItem, MediaAsset)
            .where(PlaylistItem.playlist_id == pl.id)
            .join(MediaAsset, MediaAsset.id == PlaylistItem.media_id)
            .order_by(PlaylistItem.position.asc())
        ).all()
        for it, m in rows:
            vm_items.append({
                "media_id": it.media_id,
                "url": m.url,
                "media_type": m.media_type,
                "filename": m.filename,
                "override_duration_ms": it.override_duration_ms,
                "default_duration_ms": m.duration_ms,
            })

    return templates.TemplateResponse("admin_media.html", {
        "request": request,
        "assets": assets,
        "vm_items": vm_items,
        "screen": screen,
    })

@router.post("/admin/media/delete")
def delete_media(
    session: SessionDep,
    media_id: int = Form(...),
    screen: str = Form("C"),
):
    m = session.get(MediaAsset, media_id)
    if not m:
        return RedirectResponse(url=f"/admin/media?screen={screen}", status_code=303)

    # 1) playlist toccate
    rows = session.exec(
        select(PlaylistItem.playlist_id).where(PlaylistItem.media_id == media_id)
    ).all()

    touched = set()
    for r in rows:
        if isinstance(r, (tuple, list)):
            touched.add(r[0])
        else:
            touched.add(r)

    # 2) elimina riferimenti
    session.exec(delete(PlaylistItem).where(PlaylistItem.media_id == media_id))

    # 3) bump versione playlist impattate
    if touched:
        pls = session.exec(select(Playlist).where(Playlist.id.in_(touched))).all()
        for pl in pls:
            pl.version = (pl.version or 0) + 1
            session.add(pl)

    # 4) cancella file fisico se sotto /static
    try:
        if (m.url or "").startswith("/static/"):
            fpath = Path("app") / m.url.lstrip("/")
            if fpath.exists():
                fpath.unlink()
    except Exception:
        pass

    # 5) elimina asset e commit
    session.delete(m)
    session.commit()

    return RedirectResponse(url=f"/admin/media?screen={screen}", status_code=303)

@router.post("/admin/media/upload")
async def upload_media(
    session: SessionDep,
    file: UploadFile = File(...),
    duration_ms: str | None = Form(None),
    mute: str | None = Form(None),
):
    # normalizza i campi del form
    dur_val = int(duration_ms) if duration_ms and duration_ms.strip() != "" else None
    mute_val = False if (mute in (None, "", "false", "0", "off")) else True

    ext = (Path(file.filename).suffix or "").lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov", ".webm", ".mkv"]:
        raise HTTPException(400, "Formato non supportato")

    name = f"{uuid.uuid4().hex}{ext}"
    dest = UPLOAD_DIR / name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    media_type = MediaType.video if ext in [".mp4", ".mov", ".webm", ".mkv"] else MediaType.image
    asset = MediaAsset(
        filename=file.filename,
        url=f"/static/uploads/{name}",
        media_type=media_type,
        duration_ms=dur_val,
        mute=mute_val,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)

    return RedirectResponse(url="/admin/media", status_code=303)

@router.post("/admin/playlist/set")
def playlist_set(
    session: SessionDep,
    screen: str = Form(...),
    items_spec: str = Form(""),
):
    plname = f"screen_{screen}"

    pl = session.exec(select(Playlist).where(Playlist.name == plname)).first()
    if not pl:
        pl = Playlist(name=plname, version=1)
        session.add(pl)
        session.commit()
        session.refresh(pl)

    # Pulisci items di QUELLA playlist
    session.exec(delete(PlaylistItem).where(PlaylistItem.playlist_id == pl.id))

    # Ricrea da items_spec: "mediaId:pos:overrideMs;..."
    items = []
    for part in filter(None, (items_spec or "").split(";")):
        mid_str, pos_str, ov_str = (part.split(":") + ["", "", ""])[:3]
        try:
            mid = int(mid_str); pos = int(pos_str)
        except ValueError:
            continue
        ov = int(ov_str) if ov_str.strip().isdigit() else None
        items.append(PlaylistItem(
            playlist_id=pl.id,
            media_id=mid,
            position=pos,
            override_duration_ms=ov
        ))

    for it in items:
        session.add(it)

    pl.version = (pl.version or 0) + 1
    session.add(pl)
    session.commit()

    return RedirectResponse(url=f"/admin/media?screen={screen}", status_code=303)
