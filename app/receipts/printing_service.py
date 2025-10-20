# app/receipts/printing_service.py
from __future__ import annotations
import os, time, traceback
from typing import Any, Dict, Optional
from jinja2 import Environment, BaseLoader
from escpos.printer import Network
from PIL import Image, ImageOps

# ========= Debug =========
DEBUG = os.environ.get("PRINT_DEBUG", "").strip() in ("1", "true", "TRUE", "yes", "on")

def dbg(*args):
    if DEBUG:
        print("[PRINT][DBG]", *args, flush=True)

def err(*args):
    print("[PRINT][ERR]", *args, flush=True)

# ========= Fallback Printer =========
class _NullPrinter:
    def __getattr__(self, _):
        return lambda *a, **k: None
    def close(self):
        pass

# ========= Connect =========
def _connect(host: str, port: int):
    dbg(f"Connecting to printer host={host} port={port} ...")
    t0 = time.time()
    try:
        # timeout breve per evitare hang
        p = Network(host, port, timeout=5)  # timeout supportato da python-escpos
        dbg(f"Connected in {time.time()-t0:.3f}s")
        return p
    except Exception as e:
        err("Network connect failed:", repr(e))
        dbg(traceback.format_exc())
        return _NullPrinter()

# ========= Template =========
def render_jinja(body: str, ctx: Dict[str, Any]) -> str:
    env = Environment(loader=BaseLoader(), autoescape=False, trim_blocks=True, lstrip_blocks=True)
    return env.from_string(body).render(**ctx)

# ========= RAW helpers =========
def _escpos_align_raw(p, align: str):
    n = 0 if align == "left" else (1 if align == "center" else 2)
    try:
        p._raw(bytes([0x1B, 0x61, n]))
        dbg(f"_raw align -> {align} ({n})")
    except Exception as e:
        err("_escpos_align_raw failed:", repr(e))
        dbg(traceback.format_exc())

def _img_to_raster_bytes(img: Image.Image, max_w: int = 384, invert: bool = False, threshold: int = 200, bg: str = "white"):
    """
    Converte un'immagine in raster ESC/POS (GS v 0) 1-bit.
    Ritorna (w_bytes, h, data_bytes).
    """
    dbg(f"Bitmap src size: {img.width}x{img.height}, max_w={max_w}, invert={invert}, th={threshold}, bg={bg}")

    # Gestione alpha (PNG con trasparenza)
    if img.mode in ("RGBA", "LA"):
        base = Image.new("RGBA", img.size, (255, 255, 255, 0))
        base.paste(img, mask=img.split()[-1])
        img = base.convert("RGB")
    elif img.mode == "P":  # palette -> RGB
        img = img.convert("RGB")

    # Sfondo per trasparenza (post-conversione)
    if bg.lower() == "white":
        bg_color = (255, 255, 255)
    else:
        bg_color = (0, 0, 0)
    if img.mode != "RGB":
        img = img.convert("RGB")
    bg_im = Image.new("RGB", img.size, bg_color)
    bg_im.paste(img)
    img = bg_im

    # Scala mantenendo proporzioni
    if img.width > max_w:
        ratio = max_w / float(img.width)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        dbg(f"Resizing to {new_w}x{new_h}")
        img = img.resize((new_w, new_h))

    # Binarizza
    img = img.convert("L")
    img = img.point(lambda x: 0 if x < threshold else 255, mode="1")
    if invert:
        img = ImageOps.invert(img.convert("L")).convert("1")

    w, h = img.width, img.height
    padded_w = (w + 7) & ~7
    w_bytes = padded_w // 8

    dbg(f"Binarized size: {w}x{h}, padded_w={padded_w}, w_bytes={w_bytes}")

    # Pack bit (1 = nero)
    pixels = img.load()
    out = bytearray(w_bytes * h)
    for y in range(h):
        off = y * w_bytes
        byte = 0
        bitcount = 0
        for x in range(padded_w):
            v = 0
            if x < w:
                v = 1 if pixels[x, y] == 0 else 0  # 0=nero -> 1
            byte = (byte << 1) | v
            bitcount += 1
            if bitcount == 8:
                out[off] = byte
                off += 1
                byte = 0
                bitcount = 0
    return w_bytes, h, bytes(out)

def _print_bitmap_raw(p, path: str, align: str = "left", max_w: int = 384, invert: bool = False, threshold: int = 200, bg: str = "white"):
    dbg(f"_print_bitmap_raw path={path}")
    if not os.path.isabs(path):
        dbg("WARNING: path is not absolute; current cwd:", os.getcwd())
    if not os.path.exists(path):
        err(f"Bitmap file not found: {path}")
        return
    try:
        img = Image.open(path)
        dbg(f"Opened image: mode={img.mode}, size={img.size}")
    except Exception as e:
        err("Pillow open failed:", repr(e))
        dbg(traceback.format_exc())
        return

    try:
        t0 = time.time()
        w_bytes, h, data = _img_to_raster_bytes(img, max_w=max_w, invert=invert, threshold=threshold, bg=bg)
        dbg(f"Raster conversion took {time.time()-t0:.3f}s, bytes={len(data)}, rows={h}, rowbytes={w_bytes}")
        xL, xH = w_bytes & 0xFF, (w_bytes >> 8) & 0xFF
        yL, yH = h & 0xFF, (h >> 8) & 0xFF
        _escpos_align_raw(p, align)
        header = bytes([0x1D, 0x76, 0x30, 0x00, xL, xH, yL, yH])
        dbg(f"Sending GS v 0 header ({len(header)} bytes) + data ({len(data)} bytes)")
        p._raw(header + data)
        p.text("\n")
        dbg("Bitmap sent OK")
    except Exception as e:
        err("Bitmap raw print failed:", repr(e))
        dbg(traceback.format_exc())

# ========= Tag support =========
# [[C]] [[L]] [[R]] [[B]] [[NOB]] [[DW]] [[DH]] [[BIG]] [[SIZE:WxH]] [[FONT:A|B]]
# [[BR]] [[CUT]] [[NORM]] [[RAWHEX:...]] [[RAWSIZE:WxH]]
# [[LOGO:/path|w=384|invert|bg=white|th=200]]
# [[BITMAP:/path|w=384|invert|bg=white|th=200]]
def print_text(host: str, port: int, text: str, do_cut: bool = True):
    dbg("print_text start -> do_cut:", do_cut)
    p = _connect(host, port)

    def set_style_soft(align="left", w=1, h=1, bold=False, font="a"):
        try:
            p.set(align=align, width=w, height=h, bold=bold, font=font)
            dbg(f"set align={align} w={w} h={h} bold={bold} font={font}")
        except Exception as e:
            err("set_style_soft failed:", repr(e))
            dbg(traceback.format_exc())

    default = dict(align="left", w=1, h=1, bold=False, font="a")

    try:
        for idx, raw in enumerate(text.splitlines()):
            line = raw.rstrip("\r")
            dbg(f"[line {idx:03d}] RAW: {repr(line)}")
            st = dict(**default)
            raw_size_used = False

            # --- parse tags ---
            while True:
                if not line.startswith("[["):
                    break
                end = line.find("]]")
                if end == -1:
                    break
                tag = line[2:end].strip()
                line = line[end + 2:].lstrip()
                U = tag.upper()
                dbg(f"  TAG: {U}")

                if U == "C": st["align"] = "center"
                elif U == "L": st["align"] = "left"
                elif U == "R": st["align"] = "right"
                elif U == "B": st["bold"] = True
                elif U == "NOB": st["bold"] = False
                elif U == "DW": st["w"] = max(st["w"], 2)
                elif U == "DH": st["h"] = max(st["h"], 2)
                elif U == "BIG": st.update(dict(w=2, h=2, bold=True))
                elif U.startswith("SIZE:"):
                    try:
                        _, rest = tag.split(":", 1); w, h = rest.lower().split("x")
                        st["w"] = max(1, min(8, int(w))); st["h"] = max(1, min(8, int(h)))
                    except Exception as e:
                        err("SIZE parse failed:", repr(e))
                        dbg(traceback.format_exc())
                elif U.startswith("FONT:"):
                    st["font"] = "b" if tag.split(":",1)[1].strip().lower()=="b" else "a"
                elif U == "NORM":
                    st = dict(**default)
                elif U == "BR":
                    try:
                        p.text("\n")
                        dbg("  BR -> newline")
                    except Exception as e:
                        err("newline failed:", repr(e))
                        dbg(traceback.format_exc())
                    line = ""
                elif U == "CUT":
                    try:
                        p.text("\n\n")
                        p.cut()
                        dbg("  CUT sent")
                    except Exception as e:
                        err("CUT failed:", repr(e))
                        dbg(traceback.format_exc())
                    line = ""
                elif U.startswith("RAWSIZE:"):
                    try:
                        _, rest = tag.split(":", 1); w, h = rest.lower().split("x")
                        w = max(1, min(8, int(w))); h = max(1, min(8, int(h)))
                        n = (w - 1) + ((h - 1) << 4)
                        p._raw(bytes([0x1D, 0x21, n])); raw_size_used = True
                        dbg(f"  RAWSIZE {w}x{h} -> sent n={n}")
                    except Exception as e:
                        err("RAWSIZE failed:", repr(e))
                        dbg(traceback.format_exc())
                elif U.startswith("RAWHEX:"):
                    hexpart = tag.split(":",1)[1].strip().replace(" ","")
                    try:
                        data = bytes.fromhex(hexpart)
                        p._raw(data)
                        dbg(f"  RAWHEX len={len(data)}")
                        if len(data) == 3 and data[0]==0x1D and data[1]==0x21:
                            raw_size_used = True
                    except Exception as e:
                        err("RAWHEX failed:", repr(e))
                        dbg(traceback.format_exc())
                elif U.startswith("LOGO") or U.startswith("BITMAP"):
                    # LOGO/BITMAP:/path|w=384|invert|bg=white|th=200
                    args = tag.split(":", 1)[1].strip() if ":" in tag else ""
                    if args:
                        parts = [a.strip() for a in args.split("|") if a.strip()]
                        path = parts[0]
                        max_w = 384
                        inv = False
                        bg = "white"
                        th = 200
                        for op in parts[1:]:
                            opl = op.lower()
                            if opl.startswith("w="):
                                try: max_w = int(op.split("=",1)[1])
                                except: pass
                            elif opl in ("invert","inverse","inv"):
                                inv = True
                            elif opl.startswith("bg="):
                                bg = op.split("=",1)[1]
                            elif opl.startswith("th="):
                                try: th = int(op.split("=",1)[1])
                                except: pass
                        _print_bitmap_raw(p, path, align=st["align"], max_w=max_w, invert=inv, threshold=th, bg=bg)
                # end TAG
            # --- end parse tags ---

            # applica stile per la riga e stampa eventuale testo residuo
            try:
                if raw_size_used:
                    p.set(align=st["align"], bold=st["bold"], font=st["font"])
                else:
                    set_style_soft(align=st["align"], w=st["w"], h=st["h"], bold=st["bold"], font=st["font"])
            except Exception as e:
                err("set after tags failed:", repr(e))
                dbg(traceback.format_exc())

            if line:
                try:
                    p.text(line + "\n")
                    dbg(f"  TEXT -> {repr(line)}")
                except Exception as e:
                    err("text send failed:", repr(e))
                    dbg(traceback.format_exc())

        if do_cut:
            try:
                p.text("\n\n")  # feed prima di taglio
                p.cut()
                dbg("Final CUT done")
            except Exception as e:
                err("final CUT failed:", repr(e))
                dbg(traceback.format_exc())

    finally:
        try:
            p.close()
            dbg("Printer closed")
        except Exception as e:
            err("close failed:", repr(e))
            dbg(traceback.format_exc())
