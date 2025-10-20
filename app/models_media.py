from __future__ import annotations
from typing import Optional
from enum import Enum
from datetime import datetime
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, Integer  # ⬅️ NECESSARIO per sa_column

class MediaType(str, Enum):
    image = "image"
    video = "video"

class MediaAsset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    media_type: MediaType         # "image" | "video"
    url: str
    filename: str
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    duration_ms: Optional[int] = None  # default per immagini o override video
    mute: bool = True                  # per i video

class Playlist(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)  # es: "screen_C"
    version: int = Field(default=1)

class PlaylistItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    playlist_id: int = Field(foreign_key="playlist.id")
    media_id: int = Field(foreign_key="mediaasset.id")

    # Mappa l’attributo Python 'position' sulla colonna SQL esistente 'order_index' (NOT NULL)
    position: int = Field(
        default=0,
        sa_column=Column("order_index", Integer, nullable=False)
    )

    override_duration_ms: Optional[int] = None
