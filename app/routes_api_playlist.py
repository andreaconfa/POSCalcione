
from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import select, Session
from .db import get_session
from .models_media import Playlist, PlaylistItem, MediaAsset

router = APIRouter(prefix="/api/playlist", tags=["playlist"])

@router.get("/by-screen/{plname}")
def get_playlist(plname: str, db: Session = Depends(get_session)):
    pl = db.exec(select(Playlist).where(Playlist.name == plname)).first()
    if not pl:
        return {"version": 0, "items": []}

    rows = db.exec(
        select(PlaylistItem, MediaAsset)
        .where(PlaylistItem.playlist_id == pl.id)
        .join(MediaAsset, MediaAsset.id == PlaylistItem.media_id)
        .order_by(PlaylistItem.position.asc())
    ).all()

    items = []
    for it, m in rows:
        items.append({
            "type": m.media_type,   # "image" | "video"
            "url": m.url,
            "mute": bool(m.mute),
            "duration_ms": it.override_duration_ms or m.duration_ms or None,
        })
    return {"version": pl.version, "items": items}