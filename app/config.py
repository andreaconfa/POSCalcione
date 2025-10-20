# app/config.py
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

@dataclass
class PrinterConfig:
    enabled: bool = False
    backend: str = "network"  # per ora solo network
    host: str = "127.0.0.1"
    port: int = 9100
    logo_path: str = "app/static/logo.png"

@dataclass
class AppConfig:
    # ⚠️ Usare default_factory per oggetti mutabili
    printer: PrinterConfig = field(default_factory=PrinterConfig)

def _merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            dst[k] = _merge(dst[k], v)
        else:
            dst[k] = v
    return dst

def load_config() -> AppConfig:
    # default
    data = {
        "printer": {
            "enabled": False,
            "backend": "network",
            "host": "127.0.0.1",
            "port": 9100,
            "logo_path": "app/static/logo.png",
        }
    }
    if CONFIG_FILE.exists():
        try:
            file_data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            data = _merge(data, file_data or {})
        except Exception:
            # file malformato → mantieni default
            pass

    p = data["printer"]
    return AppConfig(
        printer=PrinterConfig(
            enabled=bool(p.get("enabled", False)),
            backend=str(p.get("backend", "network")),
            host=str(p.get("host", "127.0.0.1")),
            port=int(p.get("port", 9100)),
            logo_path=str(p.get("logo_path", "app/static/logo.png")),
        )
    )

# istanza singleton caricata a import
CONFIG = load_config()
