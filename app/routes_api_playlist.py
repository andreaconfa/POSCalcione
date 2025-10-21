# app/routes_api_playlist.py
from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import select, Session

from .db import get_session_dep
from .models_media import Playlist, PlaylistItem, MediaAsset

router = APIRouter(prefix="/api/playlist", tags=["playlist"])

SessionDep = Annotated[Session, Depends(get_session_dep)]

@router.get("/by-screen/{plname}")
def get_playlist(plname: str, db: SessionDep):
    pl = db.exec(select(Playlist).where(Playlist.name == plname)).first()
    if not pl:
        return JSONResponse({"version": 0, "items": []}, headers={"Cache-Control": "no-store, max-age=0"})

    rows = db.exec(
        select(PlaylistItem, MediaAsset)
        .where(PlaylistItem.playlist_id == pl.id)
        .join(MediaAsset, MediaAsset.id == PlaylistItem.media_id)
        .order_by(PlaylistItem.position.asc())
    ).all()

    items = [
        {
            "type": m.media_type,   # "image" | "video"
            "url": m.url,
            "mute": bool(m.mute),
            "duration_ms": it.override_duration_ms or m.duration_ms or None,
        }
        for it, m in rows
    ]

    return JSONResponse(
        {"version": pl.version, "items": items},
        headers={"Cache-Control": "no-store, max-age=0"}
    )
