from pathlib import Path
from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.websockets import WebSocketDisconnect
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles
from . import routes_api_playlist, routes_admin_media  
from .views_display import router as display_router 
from .routes_kds_summary import router as kds_summary_router

import os

from .db import create_db_and_tables, seed_if_empty
from . import views_pos, views_kds, views_display, views_admin
from .ws import manager
from .paths import STATIC_DIR, TEMPLATES_DIR, UPLOADS_DIR

# ✅ importa il router ma NON chiamare include_router prima di creare l'app
from .routers import views_receipts

# ✅ crea l'app PRIMA di includere i router
app = FastAPI(title="Calcione POS — Responsive")
class CachingStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            # 1 anno, risorsa immutabile: il browser non richiede più al server
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
        
# Static e templates
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", CachingStaticFiles(directory=str(UPLOADS_DIR)), name="uploads")  # <-- NEW
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


        
@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    seed_if_empty()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

@app.get("/health", response_class=PlainTextResponse)
def health():
    return "OK"

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ✅ include di tutti i router DOPO la creazione dell'app
app.include_router(views_pos.router)
app.include_router(views_kds.router)
app.include_router(views_display.router)
app.include_router(views_admin.router)
app.include_router(views_receipts.router)  
app.include_router(routes_api_playlist.router)
app.include_router(routes_admin_media.router)
app.include_router(display_router)   
app.include_router(kds_summary_router)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    ico_path = "app/static/favicon.ico"
    if os.path.exists(ico_path):
        return FileResponse(ico_path, media_type="image/x-icon")
    return RedirectResponse(url="/static/favicon.svg", status_code=302)
