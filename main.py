import argparse
import copy
import csv
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.request
from collections import defaultdict, namedtuple
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

from arduino_bridge import ArduinoBridge
from inspection_guidance import inspection_advice

# --------------------------------------------------------------------------- #
# Application Product Identity
# --------------------------------------------------------------------------- #
APP_NAME = "PPE Checker"
APP_SUBTITLE = "PPE inspection and reporting"
APP_VERSION = "v2.7"

# Official SH17 YOLOv9-e weights (saved locally as sh17_yolov9e.pt)
MODEL_NAME = "sh17_yolov9e.pt"
MODEL_URL = "https://github.com/ahmadmughees/SH17dataset/releases/download/v1/yolo9e.pt"

# --------------------------------------------------------------------------- #
# PPE Registry (SH17 class names & safety equipment metadata)
# --------------------------------------------------------------------------- #
PPE = {
    "helmet":   {"label": "Hard Hat Helmet",  "icon": "⛑", "names": {"helmet"}},
    "vest":     {"label": "Safety Vest",      "icon": "🦺", "names": {"safety vest"}},
    "mask":     {"label": "Protective Mask",  "icon": "😷", "names": {"face mask medical", "face mask", "facemask", "mask"}},
    "glasses":  {"label": "Safety Goggles",   "icon": "👓", "names": {"glasses"}},
    "gloves":   {"label": "Work Gloves",      "icon": "🧤", "names": {"gloves"}},
    "earmuffs": {"label": "Sound Earmuffs",   "icon": "🎧", "names": {"ear mufs", "ear muffs"}},
}
PERSON_NAMES = {"person"}

# Per-person, per-item states
WORN, MISSING, PENDING = "worn", "missing", "pending"

# Industry Policy Presets
POLICY_PRESETS = {
    "all": {
        "title": "Full Gear (All 6)",
        "keys": {"helmet", "vest", "mask", "glasses", "gloves", "earmuffs"},
    },
    "construction": {
        "title": "Construction",
        "keys": {"helmet", "vest", "glasses", "gloves"},
    },
    "plant": {
        "title": "Factory Floor",
        "keys": {"vest", "glasses", "gloves", "earmuffs"},
    },
    "cleanroom": {
        "title": "Cleanroom / Lab",
        "keys": {"mask", "glasses", "gloves"},
    },
}

# --------------------------------------------------------------------------- #
# LEGO® Brick Inspired Color Palette (Tactile Plastic & Studs)
# --------------------------------------------------------------------------- #
class C:
    # Dual-theme tuples: (Light Mode, Dark Mode)
    BG = ("#F1F5F9", "#13171B")               # Baseplate background
    SURFACE = ("#FFFFFF", "#1B2026")          # Primary brick card surface
    SURFACE_2 = ("#F8FAFC", "#242A32")        # Secondary inner container
    SURFACE_3 = ("#EDF2F7", "#2E3640")        # Raised action element
    SURFACE_HOVER = ("#E2E8F0", "#38424E")
    BORDER = ("#CBD5E1", "#353D49")           # Brick borders & seams
    BORDER_LIGHT = ("#E2E8F0", "#475262")
    BORDER_ACCENT = ("#D97706", "#FFD700")

    # Authentic Primary Brick Colors
    BRICK_RED = ("#DC2626", "#D11013")        # Classic Signal Red
    BRICK_RED_HOVER = ("#B91C1C", "#B00D10")
    BRICK_RED_BG = ("#FEE2E2", "#4A0C0E")
    BRICK_RED_LIGHT = ("#EF4444", "#FF4B4E")

    BRICK_YELLOW = ("#D97706", "#FFD700")     # Classic Brick Yellow
    BRICK_YELLOW_HOVER = ("#B45309", "#E6C200")
    BRICK_YELLOW_BG = ("#FEF3C7", "#4A3E05")
    BRICK_YELLOW_LIGHT = ("#F59E0B", "#FFE866")

    BRICK_BLUE = ("#2563EB", "#0055BF")       # Classic Blue Brick
    BRICK_BLUE_HOVER = ("#1D4ED8", "#004396")
    BRICK_BLUE_BG = ("#DBEAFE", "#0A254A")
    BRICK_BLUE_LIGHT = ("#3B82F6", "#3385FF")

    BRICK_GREEN = ("#16A34A", "#00852B")      # Baseplate Green
    BRICK_GREEN_HOVER = ("#15803D", "#006E24")
    BRICK_GREEN_BG = ("#DCFCE7", "#0B381A")
    BRICK_GREEN_LIGHT = ("#22C55E", "#2ED165")

    BRICK_ORANGE = ("#EA580C", "#FF7E00")

    # Typography
    TEXT = ("#0F172A", "#FFFFFF")
    MUTED = ("#64748B", "#A8B4C4")
    DIM = ("#94A3B8", "#6B798C")

    # Semantic mappings
    ACCENT = BRICK_BLUE
    ACCENT_HOVER = BRICK_BLUE_HOVER
    ACCENT_LIGHT = BRICK_BLUE_LIGHT
    ACCENT_CYAN = ("#0891B2", "#00C2E0")

    OK = BRICK_GREEN
    OK_BG = BRICK_GREEN_BG
    OK_LIGHT = BRICK_GREEN_LIGHT
    OK_BORDER = ("#86EFAC", "#147035")

    BAD = BRICK_RED
    BAD_BG = BRICK_RED_BG
    BAD_LIGHT = BRICK_RED_LIGHT
    BAD_BORDER = ("#FECACA", "#8F1013")

    WARN = BRICK_YELLOW
    WARN_BG = BRICK_YELLOW_BG
    WARN_LIGHT = BRICK_YELLOW_LIGHT
    WARN_BORDER = ("#FDE68A", "#997F00")


class BaseplatePattern:
    """Generates authentic tactile LEGO studded baseplate patterns for Dark & Light modes."""
    @staticmethod
    def create_tile(size=32, stud_r=6, mode="dark"):
        if mode == "dark":
            bg = (19, 23, 27)
            stud_fill = (27, 34, 42)
            stud_hi = (40, 50, 62)
            stud_sh = (10, 13, 16)
        else:
            bg = (241, 245, 249)
            stud_fill = (226, 232, 240)
            stud_hi = (255, 255, 255)
            stud_sh = (203, 213, 225)

        tile = Image.new("RGB", (size, size), bg)
        d = ImageDraw.Draw(tile)
        cx, cy = size // 2, size // 2
        # Embossed shadow ring
        d.ellipse([cx - stud_r - 1, cy - stud_r - 1, cx + stud_r + 1, cy + stud_r + 1], fill=stud_sh)
        # Stud body
        d.ellipse([cx - stud_r, cy - stud_r, cx + stud_r, cy + stud_r], fill=stud_fill)
        # 3D Highlight arc
        d.arc([cx - stud_r + 1, cy - stud_r + 1, cx + stud_r - 1, cy + stud_r - 1], 180, 360, fill=stud_hi, width=1)
        return tile

    @classmethod
    def generate_bg(cls, width, height, mode="dark", size=32):
        tile = cls.create_tile(size=size, mode=mode)
        img = Image.new("RGB", (width, height))
        for x in range(0, width, size):
            for y in range(0, height, size):
                img.paste(tile, (x, y))
        return img


# OpenCV colours (BGR) for the camera feed overlay
OK_BGR = (43, 133, 0)        # Brick Green #00852B
BAD_BGR = (19, 16, 209)      # Brick Red #D11013
WARN_BGR = (0, 215, 255)     # Brick Yellow #FFD700
BLUE_BGR = (191, 85, 0)      # Brick Blue #0055BF
PPE_BOX_BGR = (0, 165, 255)  # Brick Orange

ROW_ICON = {
    WORN: ("✔", C.OK),
    MISSING: ("✖", C.BAD),
    PENDING: ("⏳", C.WARN),
}
ROW_TEXT = {
    WORN: ("EQUIPPED", C.OK),
    MISSING: ("MISSING", C.BAD),
    PENDING: ("CHECKING", C.WARN),
}

FONTS = None


def init_fonts():
    kw = {"family": "Segoe UI"} if sys.platform == "win32" else {}
    return SimpleNamespace(
        brand=ctk.CTkFont(size=20, weight="bold", **kw),
        brand_sub=ctk.CTkFont(size=11, weight="bold", **kw),
        h1=ctk.CTkFont(size=18, weight="bold", **kw),
        h2=ctk.CTkFont(size=15, weight="bold", **kw),
        card_title=ctk.CTkFont(size=15, weight="bold", **kw),
        kpi_val=ctk.CTkFont(size=22, weight="bold", **kw),
        kpi_lbl=ctk.CTkFont(size=11, weight="bold", **kw),
        body=ctk.CTkFont(size=13, **kw),
        body_bold=ctk.CTkFont(size=13, weight="bold", **kw),
        small=ctk.CTkFont(size=11, **kw),
        badge=ctk.CTkFont(size=11, weight="bold", **kw),
        btn=ctk.CTkFont(size=12, weight="bold", **kw),
        icon=ctk.CTkFont(size=14, weight="bold", **kw),
        splash_title=ctk.CTkFont(size=38, weight="bold", **kw),
        splash_sub=ctk.CTkFont(size=14, **kw),
        splash_btn=ctk.CTkFont(size=16, weight="bold", **kw),
    )


# --------------------------------------------------------------------------- #
# Tracking & math helpers
# --------------------------------------------------------------------------- #
STAY_RATIO = 0.6

BYTETRACK_YAML = """\
# ByteTrack tracker configuration (Ultralytics)
tracker_type: bytetrack
track_high_thresh: 0.05
track_low_thresh: 0.01
new_track_thresh: 0.05
track_buffer: 45
match_thresh: 0.8
fuse_score: True
"""


def ensure_tracker_file(path="bytetrack_ppe.yaml"):
    p = Path(path)
    p.write_text(BYTETRACK_YAML, encoding="utf-8")
    return str(p)


def norm(value):
    return " ".join(str(value).lower().replace("_", " ").replace("-", " ").split())


def get_name(names, index):
    if isinstance(names, dict):
        return str(names.get(index, index))
    return str(names[index]) if 0 <= index < len(names) else str(index)


NAME_TO_PPE = {norm(alias): key for key, spec in PPE.items() for alias in spec["names"]}

PPE_KEYWORDS = (("mask", "mask"), ("muff", "earmuffs"), ("mufs", "earmuffs"), ("vest", "vest"),
                ("glove", "gloves"), ("glass", "glasses"), ("helmet", "helmet"))


def ppe_key(label):
    n = norm(label)
    key = NAME_TO_PPE.get(n)
    if key:
        return key
    for word, k in PPE_KEYWORDS:
        if word in n:
            return k
    return None


def is_person(label):
    return norm(label) in PERSON_NAMES


def iou(box_a, box_b):
    ax1, ay1, ax2, ay2 = map(float, box_a)
    bx1, by1, bx2, by2 = map(float, box_b)
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def containment(box_inner, box_outer):
    ix1, iy1, ix2, iy2 = map(float, box_inner)
    ox1, oy1, ox2, oy2 = map(float, box_outer)
    iw = max(0.0, min(ix2, ox2) - max(ix1, ox1))
    ih = max(0.0, min(iy2, oy2) - max(iy1, oy1))
    area = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    return (iw * ih) / area if area > 0 else 0.0


def count_distinct_gloves(items_list):
    """Count actual distinct hands wearing gloves, preventing duplicate bounding boxes on a single hand."""
    if len(items_list) < 2:
        return len(items_list)
    distinct = []
    for _, item in items_list:
        b = item["box"]
        cx = (b[0] + b[2]) / 2.0
        cy = (b[1] + b[3]) / 2.0
        w = b[2] - b[0]
        h = b[3] - b[1]
        min_sep = max(40, min(w, h) * 0.7)
        if any(abs(cx - ocx) < min_sep and abs(cy - ocy) < min_sep for ocx, ocy in distinct):
            continue
        distinct.append((cx, cy))
    return len(distinct)


def suppress_overlaps(items, iou_thresh=0.55, contain_thresh=0.80):
    items = sorted(items, key=lambda d: d["confidence"], reverse=True)
    kept = []
    for cand in items:
        drop = False
        for k in kept:
            if (iou(cand["box"], k["box"]) > iou_thresh
                    or containment(cand["box"], k["box"]) >= contain_thresh
                    or containment(k["box"], cand["box"]) >= contain_thresh):
                drop = True
                break
        if not drop:
            kept.append(cand)
    return kept


def overlap_fraction(person_box, item_box):
    x1, y1, x2, y2 = map(int, person_box)
    a, b, c, d = map(int, item_box)
    iw = max(0, min(x2, c) - max(x1, a))
    ih = max(0, min(y2, d) - max(y1, b))
    return (iw * ih) / max(1, (c - a) * (d - b))


def parse_detections(result, names):
    detections = []
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return detections
    xyxy = boxes.xyxy.cpu().numpy().astype(int)
    classes = boxes.cls.cpu().numpy().astype(int)
    confs = boxes.conf.cpu().numpy()
    ids = boxes.id.int().cpu().tolist() if boxes.id is not None else [None] * len(xyxy)
    for box, cls_id, conf, track_id in zip(xyxy, classes, confs, ids):
        detections.append({
            "box": box,
            "label": get_name(names, int(cls_id)),
            "confidence": float(conf),
            "track_id": track_id,
        })
    return detections


def open_camera(index, attempts=4):
    indices_to_try = [index] + [i for i in (0, 1) if i != index]
    for idx in indices_to_try:
        for _ in range(attempts):
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(idx)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(idx)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return cap
            cap.release()
            time.sleep(0.3)
    raise RuntimeError(
        f"Cannot connect to Camera Device {index} or fallback cameras. Verify webcam connection or close other camera apps.")


def open_folder(path):
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def put_latest(q, item):
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        q.put_nowait(item)


def download_file(url, dest, on_progress=None, should_stop=None, timeout=30):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "ppe-detector-app"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp, part.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if should_stop is not None and should_stop():
                    raise InterruptedError("download cancelled")
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
        if total and done != total:
            raise IOError(f"incomplete download ({done} of {total} bytes)")
        if done < 1_000_000:
            raise IOError(f"downloaded file is too small to be a model ({done} bytes)")
        part.replace(dest)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    return dest


def reset_tracker(model):
    predictor = getattr(model, "predictor", None)
    for tracker in getattr(predictor, "trackers", None) or []:
        try:
            tracker.reset()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Shared settings
# --------------------------------------------------------------------------- #
Snapshot = namedtuple("Snapshot", "person ppe draw_ppe latch_equipped")


class Settings:
    def __init__(self, enabled_ppe, person=True, draw_ppe=False, latch_equipped=True):
        self._lock = threading.Lock()
        self._person = person
        self._ppe = {key: key in enabled_ppe for key in PPE}
        self._draw_ppe = draw_ppe
        self._latch_equipped = latch_equipped

    def set_person(self, on):
        with self._lock:
            self._person = bool(on)

    def set_ppe(self, key, on):
        with self._lock:
            self._ppe[key] = bool(on)

    def set_draw_ppe(self, on):
        with self._lock:
            self._draw_ppe = bool(on)

    def set_latch_equipped(self, on):
        with self._lock:
            self._latch_equipped = bool(on)

    def snapshot(self):
        with self._lock:
            return Snapshot(
                self._person,
                frozenset(k for k, on in self._ppe.items() if on),
                self._draw_ppe,
                self._latch_equipped,
            )


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #
@dataclass
class PersonState:
    track_id: int
    box: Tuple[int, int, int, int]
    items: Dict[str, str]
    logged_at: Optional[str]
    comments: Dict[str, str] = field(default_factory=dict)

    @property
    def missing(self) -> List[str]:
        return [k for k, s in self.items.items() if s == MISSING]

    @property
    def compliance_score(self) -> float:
        if not self.items:
            return 1.0
        worn_count = sum(1 for s in self.items.values() if s == WORN)
        return worn_count / len(self.items)

    def rows(self) -> List[Tuple[str, str]]:
        return [(PPE[k]["label"], self.comments.get(k, s)) for k, s in self.items.items()]


@dataclass
class FrameResult:
    raw: np.ndarray
    view: np.ndarray
    people: List[PersonState]
    person_enabled: bool
    active: List[str]
    fps: float = 30.0


# --------------------------------------------------------------------------- #
# Report Generator
# --------------------------------------------------------------------------- #
CROP_PADDING = 0.20
REPORT_MIN_H, REPORT_MAX_H = 620, 940
PANEL_W = 470

THEMES = {
    "light": dict(bg=(255, 255, 255), surface=(245, 247, 250), text=(20, 24, 30), muted=(100, 116, 139),
                  line=(226, 232, 240), ok=(0, 133, 43), bad=(209, 16, 19),
                  bad_bg=(254, 235, 236), ok_bg=(235, 248, 238), neutral=(148, 163, 184)),
    "dark": dict(bg=(20, 24, 30), surface=(28, 34, 42), text=(255, 255, 255), muted=(168, 180, 196),
                 line=(45, 54, 68), ok=(0, 168, 68), bad=(209, 16, 19),
                 bad_bg=(74, 12, 14), ok_bg=(11, 56, 26), neutral=(107, 121, 140)),
}
LOG_STATE_TEXT = {WORN: "EQUIPPED", MISSING: "MISSING", PENDING: "UNCHECKED"}


@lru_cache(maxsize=None)
def _font(size, bold=False):
    names = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if bold
             else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _text(draw, xy, text, font, fill, anchor):
    try:
        draw.text(xy, text, font=font, fill=fill, anchor=anchor)
    except ValueError:
        draw.text(xy, text, font=font, fill=fill)


def _draw_icon(draw, cx, cy, state, th):
    r = 14
    is_ok = (state == WORN or state == "EQUIPPED")
    is_bad = (state == MISSING or "NO GLOVE" in str(state) or state == "MISSING")
    color = th["ok"] if is_ok else (th["bad"] if is_bad else th["neutral"])
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    w = (255, 255, 255)
    if is_ok:
        draw.line([(cx - 5, cy), (cx - 2, cy + 4), (cx + 5, cy - 4)], fill=w, width=3, joint="curve")
    elif is_bad:
        draw.line([(cx - 5, cy - 5), (cx + 5, cy + 5)], fill=w, width=3)
        draw.line([(cx - 5, cy + 5), (cx + 5, cy - 5)], fill=w, width=3)
    else:
        draw.line([(cx - 5, cy), (cx + 5, cy)], fill=w, width=3)


def build_report_image(frame, box, track_id, rows, theme="dark"):
    th = THEMES[theme]
    fh, fw = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box)
    pad_x, pad_y = int((x2 - x1) * CROP_PADDING), int((y2 - y1) * CROP_PADDING)
    crop = frame[max(0, y1 - pad_y):min(fh, y2 + pad_y), max(0, x1 - pad_x):min(fw, x2 + pad_x)]
    if crop.size == 0:
        crop = frame
    ch = crop.shape[0]
    target_h = int(min(max(ch, REPORT_MIN_H), REPORT_MAX_H))
    interp = cv2.INTER_CUBIC if target_h > ch else cv2.INTER_AREA
    crop = cv2.resize(crop, (max(1, round(crop.shape[1] * target_h / ch)), target_h), interpolation=interp)
    photo = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))

    head_h, row_h, foot_h = 165, 52, 90
    height = max(target_h, head_h + row_h * len(rows) + foot_h)
    x0 = photo.width
    width = x0 + PANEL_W
    canvas = Image.new("RGB", (width, height), th["bg"])
    canvas.paste(photo, (0, (height - target_h) // 2))
    d = ImageDraw.Draw(canvas)

    pad = 32
    n_missing = sum(1 for _, s in rows if s == MISSING or "NO GLOVE" in str(s))

    # Top red stripe
    d.rectangle([x0, 0, width, 8], fill=(209, 16, 19))
    d.line([(x0, 0), (x0, height)], fill=th["line"], width=2)

    # Clean Header
    _text(d, (x0 + pad, 36), "PPE CHECKER  |  SAFETY AUDIT REPORT", _font(12, True), (255, 215, 0), "lm")
    _text(d, (x0 + pad, 72), f"WORKER #{track_id:02d}", _font(30, True), th["text"], "lm")
    _text(d, (x0 + pad, 104), f"INSPECTION TIME: {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}", _font(12), th["muted"], "lm")

    # Severity pill
    if not rows:
        pill = "NOT ASSESSED - NO PPE POLICY"
        bg_col, txt_col = th["bg"], th["muted"]
    elif n_missing:
        pill = f"⚠  {n_missing} MISSING GEAR VIOLATION{'S' if n_missing > 1 else ''}"
        bg_col, txt_col = th["bad_bg"], th["bad"]
    else:
        pill = "✔  100% COMPLIANT & EQUIPPED"
        bg_col, txt_col = th["ok_bg"], th["ok"]

    pill_font = _font(13, True)
    tw = d.textlength(pill, font=pill_font)
    d.rounded_rectangle([x0 + pad, 120, x0 + pad + tw + 28, 150], radius=10, fill=bg_col)
    _text(d, (x0 + pad + 14, 135), pill, pill_font, txt_col, "lm")

    # Checklist
    y = head_h + 10
    d.line([(x0 + pad, y - 6), (width - pad, y - 6)], fill=th["line"], width=1)
    for label, state in rows:
        cy = y + row_h // 2
        is_bad = (state == MISSING or "NO GLOVE" in str(state) or state == "MISSING")
        is_ok = (state == WORN or state == "EQUIPPED")
        row_bg = th["bad_bg"] if is_bad else th["surface"]
        d.rounded_rectangle([x0 + 16, y + 3, width - 16, y + row_h - 5], radius=8, fill=row_bg)
        _draw_icon(d, x0 + pad + 14, cy, state, th)
        _text(d, (x0 + pad + 42, cy), label, _font(17, True), th["text"], "lm")
        color = th["ok"] if is_ok else (th["bad"] if is_bad else th["muted"])
        status_str = LOG_STATE_TEXT.get(state, str(state))
        _text(d, (width - pad, cy), status_str, _font(13, True), color, "rm")
        y += row_h

    # Footer
    d.line([(x0 + pad, height - 52), (width - pad, height - 52)], fill=th["line"], width=1)
    _text(d, (x0 + pad, height - 30),
          "Automated Compliance Engine  ·  PPE Checker",
          _font(12), th["muted"], "lm")
    return canvas


class LogWriter:
    """Saves violation logs and tracks session history."""

    def __init__(self, root, theme="dark"):
        self.root = Path(root)
        self.image_dir = self.root / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.root / "logs.csv"
        self.theme = theme
        self.count = 0
        self._last = {}
        self._lock = threading.Lock()
        if not self.csv_path.exists():
            with self.csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(["timestamp", "worker_id", "status", "missing_gear", "image_path"])

    def reset_memory(self):
        with self._lock:
            self._last.clear()

    def logged_for(self, track_id, missing_keys):
        with self._lock:
            prev = self._last.get(track_id)
        if prev and prev[0] == frozenset(missing_keys):
            return prev[2]
        return None

    def save(self, frame, track_id, box, rows, missing_keys):
        stamp = datetime.now()
        image = build_report_image(frame, box, track_id, rows, self.theme)
        path = self.image_dir / f"{stamp:%Y%m%d_%H%M%S}_worker{track_id:02d}.jpg"
        image.save(str(path), quality=94)
        missing_labels = [PPE[k]["label"] for k in missing_keys]
        with self._lock:
            self._last[track_id] = (frozenset(missing_keys), time.monotonic(), stamp.strftime("%H:%M:%S"))
            self.count += 1
            with self.csv_path.open("a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow([
                    stamp.isoformat(timespec="seconds"),
                    f"Worker #{track_id:02d}",
                    "NOT ASSESSED" if not rows else "VIOLATION" if missing_keys else "COMPLIANT",
                    "; ".join(missing_labels) if missing_keys else "None",
                    str(path)
                ])
        return path


# --------------------------------------------------------------------------- #
# Video HUD Overlay
# --------------------------------------------------------------------------- #
def draw_hud_person(view, box, track_id, n_missing, is_checking, scale, glove_comment=None):
    """Draws tactical corner studs and rounded brick tags on workers."""
    x1, y1, x2, y2 = (int(v) for v in box)
    h, w = view.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)

    if glove_comment == "1 HAND HAS NO GLOVE":
        main_color = BAD_BGR   # Brick Red
        stud_color = WARN_BGR  # Yellow stud
        status_txt = f"WORKER #{track_id:02d} | ⚠ 1 HAND HAS NO GLOVE"
    elif n_missing > 0:
        main_color = BAD_BGR   # Brick Red
        stud_color = WARN_BGR  # Yellow stud
        status_txt = f"WORKER #{track_id:02d} | ⚠ {n_missing} MISSING GEAR"
    elif glove_comment == "ONLY 1 HAND SHOWN WITH GLOVES":
        main_color = WARN_BGR  # Brick Yellow
        stud_color = (255, 255, 255)
        status_txt = f"WORKER #{track_id:02d} | ⏳ ONLY 1 HAND SHOWN"
    elif is_checking:
        main_color = WARN_BGR  # Brick Yellow
        stud_color = (255, 255, 255)
        status_txt = f"WORKER #{track_id:02d} | ⏳ SCANNING GEAR"
    else:
        main_color = OK_BGR    # Brick Green
        stud_color = (255, 255, 255)
        status_txt = f"WORKER #{track_id:02d} | ✔ 100% EQUIPPED"

    # Sleek brick outline
    cv2.rectangle(view, (x1, y1), (x2, y2), main_color, max(2, int(2 * scale)), cv2.LINE_AA)

    # Corner studs (circular bumps on the corners)
    stud_r = max(4, int(5 * scale))
    cv2.circle(view, (x1 + stud_r + 2, y1 + stud_r + 2), stud_r, stud_color, -1)
    cv2.circle(view, (x2 - stud_r - 2, y1 + stud_r + 2), stud_r, stud_color, -1)
    cv2.circle(view, (x1 + stud_r + 2, y2 - stud_r - 2), stud_r, stud_color, -1)
    cv2.circle(view, (x2 - stud_r - 2, y2 - stud_r - 2), stud_r, stud_color, -1)

    # Tag positioning: keep within frame and avoid obscuring head/camera HUD
    font, fs, th = cv2.FONT_HERSHEY_SIMPLEX, 0.44 * scale, max(1, int(scale))
    (tw, tht), base = cv2.getTextSize(status_txt, font, fs, th)
    tag_pad = 6
    tag_h = tht + base + tag_pad * 2
    tag_w = tw + tag_pad * 3

    top = y1 - tag_h - 4
    if top < 48:
        # If too close to camera HUD at top, draw cleanly inside top edge of box or below box
        if y2 + tag_h + 8 < h:
            top = y2 + 6
        else:
            top = max(10, y1 + 8)

    # Translucent brick background
    overlay = view.copy()
    cv2.rectangle(overlay, (x1, top), (x1 + tag_w, top + tag_h), (19, 23, 27), -1)
    cv2.addWeighted(overlay, 0.88, view, 0.12, 0, view)

    # Border & side stud
    cv2.rectangle(view, (x1, top), (x1 + tag_w, top + tag_h), main_color, 2, cv2.LINE_AA)
    cv2.rectangle(view, (x1, top), (x1 + 6, top + tag_h), main_color, -1)
    cv2.putText(view, status_txt, (x1 + tag_pad + 6, top + tht + tag_pad),
                font, fs, (255, 255, 255), th, cv2.LINE_AA)


def draw_camera_hud(view, fps_val=30.0):
    """Draws a live camera HUD directly on the frame."""
    h, w = view.shape[:2]

    # Top-Left Telemetry Pill
    pill_w, pill_h = 245, 30
    overlay = view.copy()
    cv2.rectangle(overlay, (12, 12), (12 + pill_w, 12 + pill_h), (19, 23, 27), -1)
    cv2.addWeighted(overlay, 0.85, view, 0.15, 0, view)
    cv2.rectangle(view, (12, 12), (12 + pill_w, 12 + pill_h), (209, 16, 19), 2, cv2.LINE_AA)

    # Yellow Stud Dot
    cv2.circle(view, (26, 27), 5, WARN_BGR, -1)
    cv2.putText(view, "VISION CAM 01 | LIVE STREAM", (38, 31),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)

    # Top-Right Engine Status
    r_w = 210
    cv2.rectangle(overlay, (w - r_w - 12, 12), (w - 12, 12 + pill_h), (19, 23, 27), -1)
    cv2.addWeighted(overlay, 0.85, view, 0.15, 0, view)
    cv2.rectangle(view, (w - r_w - 12, 12), (w - 12, 12 + pill_h), (0, 85, 191), 2, cv2.LINE_AA)
    cv2.putText(view, f"AI CORE: YOLO  |  {fps_val:.1f} FPS", (w - r_w - 2, 31),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, (255, 215, 0), 1, cv2.LINE_AA)


# --------------------------------------------------------------------------- #
# High-Speed Inter-Frame Motion Tracker (30 FPS Box Follower)
# --------------------------------------------------------------------------- #
class LiveBoxTracker:
    """Ultra-fast (0.15ms) template-matching tracker that bridges YOLO CPU latency.

    Tracks moving person and PPE bounding boxes at the full 30 FPS camera rate
    between YOLO neural inference cycles, following head and body motion instantly.
    """

    def __init__(self):
        self.templates = {}

    def has_templates(self):
        return bool(self.templates)

    def update_detections(self, frame, boxes):
        if frame is None or not boxes:
            self.templates.clear()
            return
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self.templates.clear()
        h_img, w_img = gray.shape[:2]
        for i, b in enumerate(boxes):
            x1, y1, x2, y2 = [int(v) for v in b]
            w, h = x2 - x1, y2 - y1
            if w >= 16 and h >= 16 and x1 >= 0 and y1 >= 0 and x2 <= w_img and y2 <= h_img:
                self.templates[i] = {
                    "tpl": gray[y1:y2, x1:x2].copy(),
                    "size": (w, h),
                    "box": [x1, y1, x2, y2],
                }

    def track(self, curr_frame):
        if not self.templates or curr_frame is None:
            return {}
        gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
        h_img, w_img = gray.shape[:2]
        margin = 50
        updated = {}
        for i, data in self.templates.items():
            tpl = data["tpl"]
            w, h = data["size"]
            x1, y1, x2, y2 = data["box"]
            sx1 = max(0, x1 - margin)
            sy1 = max(0, y1 - margin)
            sx2 = min(w_img, x2 + margin)
            sy2 = min(h_img, y2 + margin)
            if (sx2 - sx1) <= w or (sy2 - sy1) <= h:
                updated[i] = data["box"]
                continue
            search_region = gray[sy1:sy2, sx1:sx2]
            res = cv2.matchTemplate(search_region, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val >= 0.35:
                nx1 = sx1 + max_loc[0]
                ny1 = sy1 + max_loc[1]
                new_box = [nx1, ny1, nx1 + w, ny1 + h]
                data["box"] = new_box
                updated[i] = new_box
            else:
                updated[i] = data["box"]
        return updated


# --------------------------------------------------------------------------- #
# Detector worker thread
# --------------------------------------------------------------------------- #
class DetectionWorker(threading.Thread):
    def __init__(self, args, settings, logger, frames_q, msgs_q, model_cache):
        super().__init__(daemon=True, name="detector")
        self.args = args
        self.settings = settings
        self.logger = logger
        self.frames = frames_q
        self.msgs = msgs_q
        self.model_cache = model_cache
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        try:
            self._load_and_run()
        except Exception as exc:
            traceback.print_exc()
            self.msgs.put(("error", f"{type(exc).__name__}: {exc}"))

    def _download_model(self, url, dest):
        print("[INFO] Downloading neural weights from", url)
        last = [0.0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] < 0.2:
                return
            last[0] = now
            if total:
                text = f"Downloading {dest.name}  {done * 100.0 / total:.0f}%  ({done / 1e6:.1f} / {total / 1e6:.1f} MB)"
            else:
                text = f"Downloading {dest.name}  {done / 1e6:.1f} MB"
            self.msgs.put(("status", text))

        try:
            download_file(url, dest, progress, self.stop_event.is_set)
        except InterruptedError:
            return False
        except Exception as exc:
            raise RuntimeError(
                f"Could not download model.\n{exc}\n\nDownload manually from:\n{url}\nand place at:\n{dest.resolve()}"
            ) from exc
        return True

    def _load_model(self):
        from ultralytics import YOLO

        model_path = Path(self.args.model)
        if not model_path.is_file():
            url = self.args.model_url or (MODEL_URL if model_path.name == MODEL_NAME else None)
            if self.args.no_download or not url:
                raise FileNotFoundError(f"Model not found: {model_path.resolve()}")
            self.msgs.put(("status", f"Downloading {model_path.name}..."))
            if not self._download_model(url, model_path):
                return None
        self.msgs.put(("status", "Initializing Neural Vision Engine..."))
        return YOLO(str(model_path))

    def _load_and_run(self):
        try:
            import torch
            torch.set_num_threads(min(8, os.cpu_count() or 4))
        except Exception:
            pass
        model = self.model_cache.get("model")
        if model is None:
            model = self._load_model()
            if model is None or self.stop_event.is_set():
                return
            self.model_cache["model"] = model
        else:
            reset_tracker(model)

        labels = [get_name(model.names, i) for i in range(len(model.names))]
        print("[INFO] Model classes:", labels)
        if not any(is_person(n) for n in labels):
            raise RuntimeError("The model has no 'person' class.")
        available = {ppe_key(n) for n in labels} - {None}
        print("[INFO] PPE matched:", sorted(available))
        self.msgs.put(("ready", available))

        tracker_file = ensure_tracker_file()
        self.msgs.put(("status", "Connecting to live video camera..."))
        cap = open_camera(self.args.camera)
        try:
            if not self.stop_event.is_set():
                self._frame_loop(cap, model, tracker_file, available)
        finally:
            cap.release()

    def _analyse(self, frame, snap, available, model, tracker_file, ctx):
        args = self.args
        active = [k for k in PPE if k in snap.ppe and k in available]
        now = time.monotonic()

        absent, held, seen = ctx.absent, ctx.held, ctx.seen
        last_worn, last_state = ctx.last_worn, ctx.last_state

        def enter_conf(label):
            if is_person(label):
                return args.person_conf
            lbl_norm = norm(label)
            if lbl_norm in ("hands", "hand"):
                return 0.10
            return args.class_conf.get(ppe_key(label), args.confidence)

        floor = max(0.05, min([args.confidence, args.person_conf]
                               + list(args.class_conf.values())) * STAY_RATIO)
        kw = dict(source=frame, persist=True, tracker=tracker_file,
                  conf=floor, imgsz=args.imgsz, verbose=False)
        if args.device:
            kw["device"] = args.device
        result = model.track(**kw)[0]

        detections = []
        for d in parse_detections(result, model.names):
            need = enter_conf(d["label"])
            if is_person(d["label"]):
                tid = d["track_id"]
                known = tid is not None and now - seen.get(tid, -1e9) < args.hold_seconds
                if d["confidence"] >= (need * STAY_RATIO if known else need):
                    detections.append(d)
            elif d["confidence"] >= need * STAY_RATIO:
                d["strong"] = d["confidence"] >= need
                detections.append(d)

        # Collect genuine head and face detections in the CURRENT frame with high confidence
        head_detections = [
            d for d in detections
            if (norm(d["label"]) in ("head", "face") and d.get("confidence", 0) >= 0.40)
            or (norm(d["label"]) == "helmet" and d.get("confidence", 0) >= 0.50)
        ]

        people_raw = []
        if snap.person:
            for d in detections:
                if not is_person(d["label"]):
                    continue
                pbox = d["box"]
                px1, py1, px2, py2 = pbox
                pw = max(1, px2 - px1)
                ph = max(1, py2 - py1)
                if ph < 80:  # Must have height of at least 80px
                    continue

                # STRICT RULE: A worker MUST have an actual head/face visible in the frame!
                # If only torso, chest, shoulders, or hands are visible (head off-screen), REJECT.
                has_head = False
                for hd in head_detections:
                    hb = hd["box"]
                    hcx = (hb[0] + hb[2]) / 2.0
                    hcy = (hb[1] + hb[3]) / 2.0
                    hh = max(1, hb[3] - hb[1])

                    # Head height must be substantial (at least 30px, not a tiny speck)
                    if hh < 30:
                        continue

                    # If the person box hits the ceiling (py1 <= 5), the head must be clearly inside the frame (hcy >= 25)
                    if py1 <= 5 and hcy < 25:
                        continue

                    # The head center MUST be horizontally inside the person box, and in the UPPER 35% of the body
                    if px1 <= hcx <= px2 and py1 <= hcy <= (py1 + 0.35 * ph):
                        has_head = True
                        break
                    # Or strong containment in upper portion
                    if overlap_fraction(pbox, hb) >= 0.60 and hcy <= (py1 + 0.40 * ph):
                        has_head = True
                        break

                if has_head:
                    people_raw.append(d)
        ppe_raw = []
        bare_hands_raw = []
        for d in detections:
            lbl = norm(d["label"])
            if lbl in ("hands", "hand"):
                bare_hands_raw.append(d)
            key = ppe_key(d["label"])
            if key in active:
                d["ppe"] = key
                ppe_raw.append(d)

        people = suppress_overlaps(people_raw, iou_thresh=args.person_iou)
        bare_hands = suppress_overlaps(bare_hands_raw, iou_thresh=args.ppe_iou)
        ppe_items = []
        for key in active:
            ppe_items.extend(suppress_overlaps(
                [d for d in ppe_raw if d["ppe"] == key], iou_thresh=args.ppe_iou))

        item_owner = {}
        for pi, person in enumerate(people):
            for ii, item in enumerate(ppe_items):
                frac = overlap_fraction(person["box"], item["box"])
                if frac >= 0.45 and (ii not in item_owner or frac > item_owner[ii][1]):
                    item_owner[ii] = (pi, frac)

        worker_bare_hands = defaultdict(list)
        for bh in bare_hands:
            for pi, person in enumerate(people):
                if overlap_fraction(person["box"], bh["box"]) >= 0.35:
                    worker_bare_hands[pi].append(bh)

        shown = {ii for ii, item in enumerate(ppe_items) if item["strong"]}

        live_now = {p_["track_id"] for p_ in people if p_["track_id"] is not None}
        for person in people:
            tid = person["track_id"]
            if tid is None or tid in seen:
                continue
            nbox = tuple(int(v) for v in person["box"])
            for old, ghost in sorted(last_state.items(), key=lambda kv: -seen.get(kv[0], 0.0)):
                if old in live_now or now - seen.get(old, -1e9) >= args.hold_seconds:
                    continue
                if (iou(ghost.box, nbox) > 0.30 or containment(ghost.box, nbox) >= 0.6
                        or containment(nbox, ghost.box) >= 0.6):
                    for key in active:
                        for table in (held, last_worn, absent):
                            if (old, key) in table:
                                table[(tid, key)] = table[(old, key)]
                    break

        states = []
        for pi, person in enumerate(people):
            tid = person["track_id"]
            if tid is None:
                continue
            box = tuple(int(v) for v in person["box"])

            owned_by_key = defaultdict(list)
            for ii, (owner, _) in item_owner.items():
                if owner == pi:
                    item = ppe_items[ii]
                    owned_by_key[item["ppe"]].append((ii, item))

            glove_boxes = [item["box"] for _, item in owned_by_key.get("gloves", [])]
            valid_bare_hands = [
                bh for bh in worker_bare_hands.get(pi, [])
                if not any(iou(bh["box"], gb) > 0.20 for gb in glove_boxes)
            ]
            num_bare = count_distinct_gloves([(0, bh) for bh in valid_bare_hands])
            num_gloves = count_distinct_gloves(owned_by_key.get("gloves", []))

            present = set()
            for key, items_list in owned_by_key.items():
                if key == "gloves":
                    for ii, item in items_list:
                        shown.add(ii)
                    if num_gloves >= 2:
                        present.add("gloves")
                else:
                    for ii, item in items_list:
                        if item["strong"] or held.get((tid, item["ppe"])) == WORN:
                            present.add(key)
                            shown.add(ii)

            items = {}
            comments = {}
            for key in active:
                k = (tid, key)
                is_present = (key in present)
                is_latched = snap.latch_equipped and (held.get(k) == WORN)

                if key == "gloves":
                    if is_present or (is_latched and not (num_gloves == 1 and num_bare >= 1)):
                        absent[k] = 0
                        last_worn[k] = now
                        state = WORN
                        comment = "EQUIPPED"
                    elif num_gloves == 1 and num_bare >= 1:
                        absent[k] += 1
                        state = MISSING
                        comment = "1 HAND HAS NO GLOVE"
                    elif num_gloves == 1 and num_bare == 0:
                        state = PENDING
                        comment = "ONLY 1 HAND SHOWN WITH GLOVES"
                    elif num_gloves == 0 and num_bare >= 1:
                        absent[k] += 1
                        state = MISSING
                        comment = "NO GLOVES DETECTED"
                    elif held.get(k) == WORN and now - last_worn.get(k, -1e9) < args.hold_seconds:
                        state = WORN
                        comment = "EQUIPPED"
                    else:
                        absent[k] += 1
                        if absent[k] >= args.miss_frames:
                            state = MISSING
                            comment = "MISSING"
                        else:
                            state = WORN if held.get(k) == WORN else PENDING
                            comment = ROW_TEXT[state][0]
                else:
                    if is_present or is_latched:
                        absent[k] = 0
                        last_worn[k] = now
                        state = WORN
                        comment = "EQUIPPED"
                    elif held.get(k) == WORN and now - last_worn.get(k, -1e9) < args.hold_seconds:
                        state = WORN
                        comment = "EQUIPPED"
                    else:
                        absent[k] += 1
                        if absent[k] >= args.miss_frames:
                            state = MISSING
                            comment = "MISSING"
                        else:
                            state = WORN if held.get(k) == WORN else PENDING
                            comment = ROW_TEXT[state][0]

                held[k] = state
                items[key] = state
                comments[key] = comment

            missing_keys = [k for k, s_ in items.items() if s_ == MISSING]

            seen[tid] = now
            logged_at = self.logger.logged_for(tid, missing_keys) if missing_keys else None
            state_obj = PersonState(tid, box, items, logged_at, comments)
            states.append(state_obj)
            last_state[tid] = state_obj

        if snap.person and states:
            live_ids = {st.track_id for st in states}
            for tid, ghost in sorted(last_state.items(), key=lambda kv: -seen.get(kv[0], 0.0)):
                if tid in live_ids or now - seen.get(tid, -1e9) >= 0.35:
                    continue
                if any(iou(ghost.box, st.box) > 0.30
                       or containment(ghost.box, st.box) >= 0.6
                       or containment(st.box, ghost.box) >= 0.6 for st in states):
                    continue
                states.append(ghost)

        ppe_shown = [ppe_items[ii] for ii in sorted(shown)]

        ctx.n += 1
        if ctx.n % 60 == 0:
            stale = {t for t, last in seen.items() if now - last > 10.0}
            for t in stale:
                seen.pop(t, None)
                last_state.pop(t, None)
                if hasattr(ctx, "verified_tracks"):
                    ctx.verified_tracks.discard(t)
            for d in (absent, held, last_worn):
                for k in [k for k in d if k[0] in stale]:
                    d.pop(k, None)
        return states, ppe_shown, active

    def _frame_loop(self, cap, model, tracker_file, available):
        lock = threading.Lock()
        wake = threading.Event()
        done = threading.Event()
        slot = {"frame": None}
        latest = {"a": None, "frame": None, "is_new": False}
        failure = []

        def infer():
            ctx = SimpleNamespace(absent=defaultdict(int), held={}, seen={}, last_worn={},
                                  last_state={}, verified_tracks=set(), n=0)
            try:
                while not done.is_set() and not self.stop_event.is_set():
                    if not wake.wait(0.1):
                        continue
                    wake.clear()
                    if done.is_set() or self.stop_event.is_set():
                        break
                    with lock:
                        frame = slot["frame"]
                    if frame is None:
                        continue
                    snap = self.settings.snapshot()
                    states, ppe_items, active = self._analyse(
                        frame, snap, available, model, tracker_file, ctx)
                    with lock:
                        latest["a"] = (states, ppe_items, active)
                        latest["frame"] = frame
                        latest["is_new"] = True
            except Exception as exc:
                traceback.print_exc()
                failure.append(exc)

        worker = threading.Thread(target=infer, daemon=True, name="inference")
        worker.start()

        fps_calc = {"last_t": time.time(), "fps": 30.0}
        tracker = LiveBoxTracker()
        curr_states = []
        curr_ppe_items = []
        try:
            while not self.stop_event.is_set():
                if failure:
                    raise failure[0]
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Camera video stream was disconnected.")

                now_t = time.time()
                dt = now_t - fps_calc["last_t"]
                fps_calc["last_t"] = now_t
                if dt > 0:
                    fps_calc["fps"] = 0.9 * fps_calc["fps"] + 0.1 * (1.0 / dt)

                with lock:
                    slot["frame"] = frame
                wake.set()

                with lock:
                    a = latest["a"]
                    raw = latest["frame"]
                    is_new = latest["is_new"]
                    if is_new:
                        latest["is_new"] = False

                snap = self.settings.snapshot()
                active = [k for k in PPE if k in snap.ppe and k in available]

                if a is not None:
                    states, ppe_items, active = a
                    if is_new and raw is not None:
                        curr_states = [copy.copy(p) for p in states]
                        curr_ppe_items = [dict(item) for item in ppe_items]
                        boxes = [list(p.box) for p in curr_states] + [list(item["box"]) for item in curr_ppe_items]
                        tracker.update_detections(raw, boxes)
                        if frame is not raw and tracker.has_templates():
                            tracked = tracker.track(frame)
                            n_p = len(curr_states)
                            for pi in range(n_p):
                                if pi in tracked:
                                    curr_states[pi].box = tuple(int(v) for v in tracked[pi])
                            for ii in range(len(curr_ppe_items)):
                                b_idx = n_p + ii
                                if b_idx in tracked:
                                    curr_ppe_items[ii]["box"] = tracked[b_idx]
                    elif tracker.has_templates():
                        tracked = tracker.track(frame)
                        n_p = len(curr_states)
                        for pi in range(n_p):
                            if pi in tracked:
                                curr_states[pi].box = tuple(int(v) for v in tracked[pi])
                        for ii in range(len(curr_ppe_items)):
                            b_idx = n_p + ii
                            if b_idx in tracked:
                                curr_ppe_items[ii]["box"] = tracked[b_idx]
                    else:
                        curr_states = [copy.copy(p) for p in states]
                        curr_ppe_items = [dict(item) for item in ppe_items]

                height, width = frame.shape[:2]
                scale = max(0.6, min(width, height) / 720.0)
                view = frame.copy()

                if snap.draw_ppe or not snap.person:
                    for item in curr_ppe_items:
                        x1, y1, x2, y2 = (int(v) for v in item["box"])
                        cv2.rectangle(view, (x1, y1), (x2, y2), PPE_BOX_BGR, 2, cv2.LINE_AA)
                        tag = f"{PPE[item['ppe']]['label']} {item['confidence']:.2f}"
                        cv2.putText(view, tag, (x1, max(12, y1 - 4)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.40 * scale, PPE_BOX_BGR, 1, cv2.LINE_AA)

                for p in curr_states:
                    is_chk = PENDING in p.items.values()
                    g_comment = getattr(p, "comments", {}).get("gloves")
                    if p.items:
                        draw_hud_person(view, p.box, p.track_id, len(p.missing), is_chk, scale, g_comment)
                    else:
                        x1, y1, x2, y2 = p.box
                        cv2.rectangle(view, (x1, y1), (x2, y2), (160, 160, 160), 2)
                        cv2.putText(view, f"WORKER #{p.track_id:02d} | NOT ASSESSED", (x1, max(20, y1 - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

                draw_camera_hud(view, fps_calc["fps"])

                put_latest(self.frames, FrameResult(
                    frame, view, curr_states, snap.person, active, fps_calc["fps"]))
        finally:
            done.set()
            wake.set()
            worker.join(timeout=15)


# --------------------------------------------------------------------------- #
# Tactile Brick Worker Profile Card
# --------------------------------------------------------------------------- #
class PersonCard(ctk.CTkFrame):
    """Tactile brick card for each detected worker with top stud accents."""

    def __init__(self, master, track_id, on_capture):
        super().__init__(master, fg_color=C.SURFACE_2, corner_radius=10,
                         border_width=2, border_color=C.BORDER)
        self.track_id = track_id
        self._sig = None
        self._keys = ()
        self._rows = {}
        self.grid_columnconfigure(0, weight=1)

        # 4 Brick studs along the top edge of the card
        studs_bar = ctk.CTkFrame(self, fg_color="transparent", height=8)
        studs_bar.grid(row=0, column=0, sticky="w", padx=16, pady=(6, 0))
        for i in range(4):
            stud = ctk.CTkFrame(studs_bar, width=14, height=5, corner_radius=2, fg_color=C.BORDER_LIGHT)
            stud.grid(row=0, column=i, padx=3)

        # Header bar
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=1, column=0, sticky="ew", padx=14, pady=(6, 6))
        head.grid_columnconfigure(0, weight=1)

        id_holder = ctk.CTkFrame(head, fg_color="transparent")
        id_holder.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(id_holder, text="👷", font=ctk.CTkFont(size=18)).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkLabel(id_holder, text=f"WORKER #{track_id:02d}", font=FONTS.card_title,
                     text_color=C.BRICK_YELLOW).grid(row=0, column=1)

        self.badge = ctk.CTkLabel(head, text="", font=FONTS.badge, corner_radius=6, height=24, padx=10)
        self.badge.grid(row=0, column=1, sticky="e")

        # Compliance Meter Gauge
        bar_frame = ctk.CTkFrame(self, fg_color="transparent")
        bar_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(2, 6))
        bar_frame.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(bar_frame, height=8, corner_radius=4, fg_color=C.SURFACE_3)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.score_label = ctk.CTkLabel(bar_frame, text="Gear Equipped: 100%", font=FONTS.small, text_color=C.MUTED)
        self.score_label.grid(row=1, column=0, sticky="w", pady=(3, 0))

        # Checklist Body (Baseplate container)
        self.body = ctk.CTkFrame(self, fg_color=C.SURFACE, corner_radius=8, border_width=1, border_color=C.BORDER)
        self.body.grid(row=3, column=0, sticky="ew", padx=14, pady=4)
        self.body.grid_columnconfigure(1, weight=1)

        # Card Footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", padx=14, pady=(8, 12))
        foot.grid_columnconfigure(0, weight=1)

        self.note = ctk.CTkLabel(foot, text="", font=FONTS.small, text_color=C.MUTED, anchor="w")
        self.note.grid(row=0, column=0, sticky="w")

        # Chunky Red Brick button
        self.button = ctk.CTkButton(
            foot, text="🖨️ Print / Capture", width=175, height=32, corner_radius=8, font=FONTS.btn,
            fg_color=C.BRICK_RED, hover_color=C.BRICK_RED_HOVER, text_color="#FFFFFF",
            text_color_disabled=C.DIM, command=lambda: on_capture(self.track_id))
        self.button.grid(row=0, column=1, sticky="e")

    def _build_rows(self, keys):
        for child in self.body.winfo_children():
            child.destroy()
        self._rows = {}
        for i, key in enumerate(keys):
            icon_char = PPE[key].get("icon", "•")
            icon = ctk.CTkLabel(self.body, text=icon_char, width=22, font=FONTS.icon)
            name = ctk.CTkLabel(self.body, text=PPE[key]["label"], font=FONTS.body_bold,
                                text_color=C.TEXT, anchor="w")
            status = ctk.CTkLabel(self.body, text="", font=FONTS.small, anchor="e", padx=6)
            icon.grid(row=i, column=0, pady=3, padx=(10, 6))
            name.grid(row=i, column=1, sticky="w", pady=3)
            status.grid(row=i, column=2, sticky="e", pady=3, padx=(0, 10))
            self._rows[key] = [icon, status, None]
        self._keys = tuple(keys)

    def update_person(self, person, keys):
        missing = person.missing
        n = len(missing)
        states = set(person.items.values())
        glove_comment = getattr(person, "comments", {}).get("gloves")

        if not keys:
            badge = ("NO RULES", C.MUTED, C.SURFACE_3)
            score_col = C.MUTED
        elif glove_comment == "1 HAND HAS NO GLOVE":
            badge = ("⚠  1 HAND HAS NO GLOVE", ("#FFFFFF", "#FFFFFF"), C.BRICK_RED)
            score_col = C.BRICK_RED
        elif n:
            badge = (f"⚠  {n} VIOLATION{'S' if n > 1 else ''}", ("#FFFFFF", "#FFFFFF"), C.BRICK_RED)
            score_col = C.BRICK_RED
        elif glove_comment == "ONLY 1 HAND SHOWN WITH GLOVES":
            badge = ("⏳  ONLY 1 HAND SHOWN", ("#000000", "#000000"), C.BRICK_YELLOW)
            score_col = C.BRICK_YELLOW
        elif PENDING in states:
            badge = ("⏳  SCANNING", ("#000000", "#000000"), C.BRICK_YELLOW)
            score_col = C.BRICK_YELLOW
        else:
            badge = ("✔  100% EQUIPPED", ("#FFFFFF", "#FFFFFF"), C.BRICK_GREEN)
            score_col = C.BRICK_GREEN

        score_pct = int(person.compliance_score * 100)
        self.progress_bar.set(person.compliance_score if keys else 0)
        self.progress_bar.configure(progress_color=score_col)
        self.score_label.configure(
            text=(f"Safety Gear: {score_pct}% Equipped  ({len(keys) - n}/{len(keys)} worn)" if keys else "Not assessed - no PPE policy selected"),
            text_color=score_col
        )

        note, note_color = "Ready for inspection", C.MUTED
        if person.logged_at:
            note, note_color = f"✔ Evidence Logged: {person.logged_at}", C.BRICK_YELLOW
        elif glove_comment == "1 HAND HAS NO GLOVE":
            note, note_color = "⚠ Violation: 1 hand has no glove!", C.BRICK_RED_LIGHT
        elif glove_comment == "ONLY 1 HAND SHOWN WITH GLOVES":
            note, note_color = "⏳ Only 1 hand shown with gloves · Show second hand", C.BRICK_YELLOW_LIGHT
        elif n:
            note, note_color = f"⚠ {n} Missing Items", C.BRICK_RED_LIGHT

        can_capture = n > 0

        sig = (tuple(keys), tuple(person.items.items()), tuple(getattr(person, "comments", {}).items()), badge, note, can_capture)
        if sig == self._sig:
            return
        self._sig = sig

        if tuple(keys) != self._keys:
            self._build_rows(keys)

        for key, state in person.items.items():
            row = self._rows[key]
            comment = getattr(person, "comments", {}).get(key)
            if key == "gloves" and comment:
                if comment == "1 HAND HAS NO GLOVE":
                    status_text = "1 HAND HAS NO GLOVE"
                    status_color = C.BAD
                elif comment == "ONLY 1 HAND SHOWN WITH GLOVES":
                    status_text = "ONLY 1 HAND SHOWN"
                    status_color = C.WARN
                elif comment == "NO GLOVES DETECTED":
                    status_text = "NO GLOVES DETECTED"
                    status_color = C.BAD
                elif comment == "EQUIPPED":
                    status_text = "EQUIPPED"
                    status_color = C.OK
                else:
                    status_text, status_color = ROW_TEXT.get(state, (comment, C.MUTED))
            else:
                status_text, status_color = ROW_TEXT[state]

            if row[2] != (state, comment):
                row[1].configure(text=status_text, text_color=status_color)
                row[2] = (state, comment)

        self.badge.configure(text=badge[0], text_color=badge[1], fg_color=badge[2])
        self.configure(border_color=C.BORDER if not keys else C.BRICK_RED if n else (C.BRICK_GREEN if not PENDING in states else C.BORDER))
        # Allow capturing/printing report card for both 100% compliant workers and violators
        btn_text = "Print / Capture Not Assessed" if not keys else "🖨️ Print / Capture Violation" if n else "🖨️ Print / Capture Pass"
        btn_color = C.SURFACE_3 if not keys else C.BRICK_RED if n else C.BRICK_GREEN
        btn_hover = C.BRICK_RED_HOVER if n else C.BRICK_GREEN_HOVER
        self.button.configure(text=btn_text, state="normal",
                              fg_color=btn_color, hover_color=btn_hover)


# --------------------------------------------------------------------------- #
# Main Application Window
# --------------------------------------------------------------------------- #
class App(ctk.CTk):
    GONE_GRACE = 1.5

    def __init__(self, args):
        super().__init__(fg_color=C.BG)
        global FONTS
        FONTS = init_fonts()
        self.args = args
        self.title(f"{APP_NAME} — {APP_SUBTITLE}")
        self.geometry("1420x860")
        self.minsize(1180, 720)

        # Theme appearance initialization
        self._current_theme = "light"
        ctk.set_appearance_mode("Light")
        self._bg_photo = None
        self._last_bg_size = (0, 0)
        self._resize_timer = None

        # 0. Background Tactile Studded Baseplate Canvas (underneath all cards)
        self.bg_canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg="#13171B")
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lower(self.bg_canvas)
        self.bind("<Configure>", self._on_window_configure)

        enabled = set(PPE) if "all" in args.ppe else (set() if "none" in args.ppe else set(args.ppe))
        self.settings = Settings(enabled, draw_ppe=args.draw_ppe, latch_equipped=not getattr(args, "no_latch", False))
        snap0 = self.settings.snapshot()

        self.applied = {"person": snap0.person}
        self.applied.update({k: k in snap0.ppe for k in PPE})
        self.model_cache = {}
        self._restarting = False
        self._wait_id = None
        self.logger = LogWriter(args.log_dir, theme=args.log_theme)
        self.frames = queue.Queue(maxsize=1)
        self.msgs = queue.Queue()
        self.latest = None
        self._inspection_idle = False
        self._advice_label = None
        self.cards = {}
        self.last_seen = {}
        self._photo = None
        self._poll_id = None
        self._clock_id = None
        self._have_frame = False
        self._empty_text = None

        ard_port = getattr(args, "arduino_port", "auto")
        self.arduino = ArduinoBridge(port=ard_port) if ard_port.lower() != "none" else None
        self._last_arduino_state = "IDLE"
        self._last_arduino_detail = ""
        self._last_worker_seen_time = 0.0
        self._fail_first_seen_time = None
        self._last_fail_detail_time = 0.0

        # Start with LEGO loading screen
        self._on_loading_screen = True
        self._loading_photo = None
        self._raw_welcome_img = None

        self._build_ui()
        self._build_loading_screen()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.worker = DetectionWorker(args, self.settings, self.logger, self.frames, self.msgs,
                                      self.model_cache)
        self.worker.start()
        self._poll()
        self._update_clock()
        self.after(50, lambda: self._update_bg_canvas(force=True))
        self.after(50, lambda: self._update_loading_bg())

    # -------------------------------------------------------- Baseplate Background & Loading Screen
    def _on_window_configure(self, event=None):
        if event and event.widget != self:
            return
        if self._resize_timer is not None:
            self.after_cancel(self._resize_timer)
        self._resize_timer = self.after(100, self._on_resize_debounced)

    def _on_resize_debounced(self):
        self._update_bg_canvas()
        if getattr(self, "_on_loading_screen", False):
            self._update_loading_bg()

    def _update_loading_bg(self):
        if not getattr(self, "_on_loading_screen", False) or not hasattr(self, "loading_bg_canvas"):
            return
        try:
            w = max(200, self.winfo_width())
            h = max(200, self.winfo_height())
            if not getattr(self, "_raw_welcome_img", None):
                bg_path = Path(__file__).resolve().parent / "assets" / "lego_welcome_bg.jpg"
                if bg_path.exists():
                    self._raw_welcome_img = Image.open(bg_path).convert("RGBA")
                else:
                    self._raw_welcome_img = None
            if self._raw_welcome_img:
                img_w, img_h = self._raw_welcome_img.size
                scale = max(w / img_w, h / img_h)
                nw, nh = max(1, int(img_w * scale)), max(1, int(img_h * scale))
                resized = self._raw_welcome_img.resize((nw, nh), Image.Resampling.LANCZOS)
                left = (nw - w) // 2
                top = (nh - h) // 2
                cropped = resized.crop((left, top, left + w, top + h))

                # Subtle dark translucent overlay so hero typography pops cleanly
                overlay = Image.new("RGBA", (w, h), (11, 14, 20, 115))
                tinted = Image.alpha_composite(cropped, overlay)
                self._loading_photo = ImageTk.PhotoImage(tinted)
                self.loading_bg_canvas.delete("all")
                self.loading_bg_canvas.create_image(0, 0, image=self._loading_photo, anchor="nw")
        except Exception:
            pass

    def _build_loading_screen(self):
        self.loading_frame = ctk.CTkFrame(self, fg_color="#0B0E14", corner_radius=0)
        self.loading_frame.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Misc.lift(self.loading_frame)

        self.loading_bg_canvas = tk.Canvas(self.loading_frame, highlightthickness=0, bd=0, bg="#0B0E14")
        self.loading_bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)

        # Center Hero Glassmorphic Card
        hero = ctk.CTkFrame(
            self.loading_frame,
            fg_color=("#181F2A", "#0E131B"),
            corner_radius=22,
            border_width=2,
            border_color=C.BORDER,
            width=580,
        )
        hero.place(relx=0.5, rely=0.5, anchor="center")
        tk.Misc.lift(hero)

        content = ctk.CTkFrame(hero, fg_color="transparent")
        content.pack(padx=42, pady=34, fill="both", expand=True)

        # 4 Mini Brick Studs
        studs = ctk.CTkFrame(content, fg_color="transparent", height=10)
        studs.pack(pady=(0, 12))
        for col_i, col_val in enumerate([C.BRICK_RED, C.BRICK_BLUE, C.BRICK_YELLOW, C.BRICK_GREEN]):
            ctk.CTkFrame(studs, width=20, height=8, corner_radius=3, fg_color=col_val).grid(row=0, column=col_i, padx=3)

        # Top Station Category Pill
        badge_pill = ctk.CTkFrame(content, fg_color=C.SURFACE_3, corner_radius=14, border_width=1, border_color=C.BORDER)
        badge_pill.pack(pady=(0, 10))
        ctk.CTkLabel(badge_pill, text="🛡️  SAFETY COMPLIANCE STATION", font=FONTS.badge, text_color=C.BRICK_YELLOW).pack(padx=18, pady=5)

        # Main Title & Subtitle
        ctk.CTkLabel(content, text="PPE CHECKER", font=FONTS.splash_title, text_color=C.BRICK_YELLOW).pack(pady=(0, 4))
        ctk.CTkLabel(content, text="Computer Vision Personal Protective Equipment Audit",
                     font=FONTS.splash_sub, text_color=C.TEXT).pack(pady=(0, 16))

        # Thin Divider
        ctk.CTkFrame(content, height=1, fg_color=C.BORDER).pack(fill="x", pady=(0, 18))

        # Feature highlights row
        feats = ctk.CTkFrame(content, fg_color="transparent")
        feats.pack(fill="x", pady=(0, 24))
        feats.grid_columnconfigure((0, 1, 2), weight=1)

        feat_items = [
            ("📷", "AI Vision", "Real-Time YOLO"),
            ("⚡", "Safety Station", "LEDs & Tone Alert"),
            ("🖨️", "Audit Receipts", "Thermal Card Print"),
        ]
        for idx, (icon, title, desc) in enumerate(feat_items):
            box = ctk.CTkFrame(feats, fg_color=C.SURFACE_2, corner_radius=12, border_width=1, border_color=C.BORDER)
            box.grid(row=0, column=idx, padx=5, sticky="nsew")
            ctk.CTkLabel(box, text=icon, font=FONTS.icon).pack(pady=(10, 2))
            ctk.CTkLabel(box, text=title, font=FONTS.body_bold, text_color=C.TEXT).pack()
            ctk.CTkLabel(box, text=desc, font=FONTS.small, text_color=C.MUTED).pack(pady=(0, 10))

        # Prominent Start Action Button
        start_btn = ctk.CTkButton(
            content,
            text="START INSPECTION  ▶",
            width=340,
            height=52,
            corner_radius=12,
            font=FONTS.splash_btn,
            fg_color=C.BRICK_GREEN,
            hover_color=C.BRICK_GREEN_HOVER,
            text_color="#FFFFFF",
            command=self._enter_detection_board,
        )
        start_btn.pack(pady=(2, 6))

        ctk.CTkLabel(
            content,
            text="Press Start to proceed to detection board",
            font=FONTS.small,
            text_color=C.MUTED,
        ).pack(pady=(0, 14))

        # Bottom System Readiness Pill
        status_box = ctk.CTkFrame(content, fg_color="transparent")
        status_box.pack()
        ctk.CTkLabel(
            status_box,
            text="● Vision Engine Ready  ·  Camera Online",
            font=FONTS.small,
            text_color=C.OK,
        ).pack()

    def _enter_detection_board(self):
        self._on_loading_screen = False
        if hasattr(self, "loading_frame"):
            self.loading_frame.place_forget()
        self._toast("Welcome to PPE Checker. Inspection station active!")

    def _show_loading_screen(self):
        self._on_loading_screen = True
        if hasattr(self, "loading_frame"):
            self.loading_frame.place(x=0, y=0, relwidth=1, relheight=1)
            tk.Misc.lift(self.loading_frame)
            self._update_loading_bg()

    def _update_bg_canvas(self, force=False):
        self._resize_timer = None
        try:
            w = max(200, self.winfo_width())
            h = max(200, self.winfo_height())
            # Snap dimensions to 64px increments to cache background tiles efficiently
            cw = ((w + 63) // 64) * 64
            ch = ((h + 63) // 64) * 64
            key = (cw, ch, self._current_theme)
            if force or key != self._last_bg_size:
                img = BaseplatePattern.generate_bg(cw, ch, mode=self._current_theme)
                self._bg_photo = ImageTk.PhotoImage(img)
                self._last_bg_size = key
                self.bg_canvas.delete("all")
                self.bg_canvas.create_image(0, 0, image=self._bg_photo, anchor="nw")
                bg_color = "#13171B" if self._current_theme == "dark" else "#F1F5F9"
                self.bg_canvas.configure(bg=bg_color)
        except Exception:
            pass

    # ---------------------------------------------------------------- Layout
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=300)
        self.grid_columnconfigure(2, weight=0, minsize=380)
        self.grid_rowconfigure(2, weight=1)

        # 1. Executive Top Header with Brick Badge, Live Pill, Theme Switcher & Vault
        top_bar = ctk.CTkFrame(self, fg_color=C.SURFACE, corner_radius=0, height=62, border_width=1, border_color=C.BORDER)
        top_bar.grid(row=0, column=0, columnspan=3, sticky="ew")
        top_bar.grid_columnconfigure(1, weight=1)

        brand_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        brand_frame.grid(row=0, column=0, sticky="w", padx=24, pady=8)

        # 4 Mini Brick Studs in classic LEGO primary colors
        studs_row = ctk.CTkFrame(brand_frame, fg_color="transparent", height=8)
        studs_row.grid(row=0, column=0, sticky="w", pady=(0, 2))
        for col_i, col_val in enumerate([C.BRICK_RED, C.BRICK_BLUE, C.BRICK_YELLOW, C.BRICK_GREEN]):
            ctk.CTkFrame(studs_row, width=14, height=5, corner_radius=2, fg_color=col_val).grid(row=0, column=col_i, padx=2)

        ctk.CTkLabel(brand_frame, text=APP_NAME.upper(), font=FONTS.brand, text_color=C.BRICK_YELLOW).grid(row=1, column=0, sticky="w")
        ctk.CTkLabel(brand_frame, text=f"{APP_SUBTITLE}  ·  {APP_VERSION}",
                     font=FONTS.small, text_color=C.MUTED).grid(row=2, column=0, sticky="w")

        # Center Status Pill: Live camera indicator & clock
        center_box = ctk.CTkFrame(top_bar, fg_color="transparent")
        center_box.grid(row=1, column=0, sticky="w", padx=24, pady=(0, 2))
        center_box.grid_columnconfigure(0, weight=1)

        center_pill = ctk.CTkFrame(center_box, fg_color=C.SURFACE_2, corner_radius=18, border_width=1, border_color=C.BORDER)
        center_pill.grid(row=0, column=0)

        self.live_stream_badge = ctk.CTkLabel(center_pill, text="● STARTING", font=FONTS.small, text_color=C.OK)
        self.live_stream_badge.grid(row=0, column=0, padx=(14, 8), pady=6)

        ctk.CTkLabel(center_pill, text="|", font=FONTS.small, text_color=C.DIM).grid(row=0, column=1)

        self.clock_label = ctk.CTkLabel(center_pill, text="", font=FONTS.body_bold, text_color=C.TEXT)
        self.clock_label.grid(row=0, column=2, padx=(8, 14), pady=6)

        # Top Right Controls: Home + Ready/Scan + Arduino Status + Theme + Vault
        ctrl_box = ctk.CTkFrame(top_bar, fg_color="transparent")
        ctrl_box.grid(row=0, column=1, sticky="e", padx=24, pady=8)

        self.welcome_screen_btn = ctk.CTkButton(
            ctrl_box, text="Home", width=85, height=34, corner_radius=8, font=FONTS.btn,
            fg_color=C.SURFACE_3, hover_color=C.SURFACE_HOVER, text_color=C.TEXT,
            border_width=1, border_color=C.BORDER, command=self._show_loading_screen)
        self.welcome_screen_btn.grid(row=0, column=0, padx=(0, 10))

        self.ready_btn = ctk.CTkButton(
            ctrl_box, text="New inspection", width=140, height=34, corner_radius=8, font=FONTS.btn,
            fg_color=C.BRICK_GREEN, hover_color=C.BRICK_GREEN_HOVER, text_color="#FFFFFF",
            command=self._reset_station_idle)
        self.ready_btn.grid(row=0, column=1, padx=(0, 10))

        self.arduino_btn = ctk.CTkButton(
            ctrl_box, text="[--] Arduino: Auto", width=145, height=34, corner_radius=8, font=FONTS.btn,
            fg_color=C.SURFACE_3, hover_color=C.SURFACE_HOVER, text_color=C.TEXT,
            border_width=1, border_color=C.BORDER, command=self._reconnect_arduino)
        self.arduino_btn.grid(row=0, column=2, padx=(0, 10))

        self.theme_btn = ctk.CTkButton(
            ctrl_box, text="Dark mode", width=120, height=34, corner_radius=8, font=FONTS.btn,
            fg_color=C.SURFACE_3, hover_color=C.SURFACE_HOVER, text_color=C.TEXT,
            border_width=1, border_color=C.BORDER, command=self._toggle_theme)
        self.theme_btn.grid(row=0, column=3, padx=(0, 10))

        vault_btn = ctk.CTkButton(
            ctrl_box, text="Saved reports", width=135, height=34, corner_radius=8, font=FONTS.btn,
            fg_color=C.BRICK_BLUE, hover_color=C.BRICK_BLUE_HOVER, text_color="#FFFFFF",
            command=self._open_logs)
        vault_btn.grid(row=0, column=4)

        # 2. Executive 3D Brick KPI Strip (Cards with visible studs!)
        kpi_strip = ctk.CTkFrame(self, fg_color="transparent")
        kpi_strip.grid(row=1, column=0, columnspan=3, sticky="ew", padx=24, pady=(10, 8))
        for col in range(4):
            kpi_strip.grid_columnconfigure(col, weight=1, uniform="kpi")

        self.kpi_workers = self._create_kpi_card(
            kpi_strip, 0, "👷 ACTIVE WORKERS", "0 Workers", C.BRICK_BLUE, "In Inspection Zone")
        self.kpi_compliance = self._create_kpi_card(
            kpi_strip, 1, "🛡️ SAFETY SCORE", "--", C.BRICK_GREEN, "Zone Compliance")
        self.kpi_violations = self._create_kpi_card(
            kpi_strip, 2, "⚠️ TOTAL VIOLATIONS", "0 Violations", C.BRICK_RED, "Immediate Attention")
        self.kpi_logs = self._create_kpi_card(
            kpi_strip, 3, "📋 AUDIT LOGS SAVED", "0 Captured", C.BRICK_YELLOW, "Session Vault")

        # 3. Left Section: Live Camera Viewport & Streamlined Policy Dock
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=2, column=0, sticky="nsew", padx=(16, 8), pady=(0, 18))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(0, weight=1)

        # Video Frame Container (Baseplate bordered)
        feed_card = ctk.CTkFrame(left, fg_color=C.SURFACE, corner_radius=12, border_width=2, border_color=C.BORDER)
        feed_card.grid(row=0, column=0, sticky="nsew")
        feed_card.grid_columnconfigure(0, weight=1)
        feed_card.grid_rowconfigure(0, weight=1)

        self.feed_holder = tk.Frame(feed_card, bg="#0D1014", highlightthickness=0)
        self.feed_holder.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))

        self.feed = tk.Label(self.feed_holder, bg="#0D1014", fg="#A8B4C4", text="Connecting to vision feed...",
                             font=("Segoe UI", 13), compound="center", bd=0)
        self.feed.place(relx=0.5, rely=0.5, anchor="center")

        self.retry_btn = ctk.CTkButton(
            self.feed_holder, text="Re-initialize Camera", width=160, height=36, corner_radius=8, font=FONTS.btn,
            fg_color=C.BRICK_RED, hover_color=C.BRICK_RED_HOVER, bg_color="#0D1014",
            command=lambda: self._restart_worker("Reconnecting to camera..."))

        # Feed Toolbar
        bar = ctk.CTkFrame(feed_card, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 8))
        bar.grid_columnconfigure(1, weight=1)

        self.live_label = ctk.CTkLabel(bar, text="● CONNECTING", font=FONTS.small, text_color=C.WARN)
        self.live_label.grid(row=0, column=0, sticky="w")

        self.profile_label = ctk.CTkLabel(bar, text="Standard: Full Gear (All 6)", font=FONTS.small, text_color=C.MUTED)
        self.profile_label.grid(row=1, column=0, columnspan=3, sticky="w")

        sw_bar = ctk.CTkFrame(bar, fg_color="transparent")
        sw_bar.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.latch_switch = self._make_switch(sw_bar, "Remember gear", self._on_latch)
        self.latch_switch.pack(side="left", padx=(0, 14))
        if self.settings.snapshot().latch_equipped:
            self.latch_switch.select()

        self.boxes_switch = self._make_switch(sw_bar, "Gear outlines", self._on_boxes)
        self.boxes_switch.pack(side="left")
        if self.settings.snapshot().draw_ppe:
            self.boxes_switch.select()

        # Streamlined Safety Policy Dock (Spacious 2-Row Control Deck)
        policy_card = ctk.CTkFrame(self, fg_color=C.SURFACE, corner_radius=12, border_width=2, border_color=C.BORDER, width=300)
        policy_card.grid(row=2, column=1, sticky="nsew", padx=8, pady=(0, 18))
        policy_card.grid_propagate(False)
        policy_card.grid_rowconfigure(1, weight=1)
        policy_card.grid_columnconfigure(0, weight=1)

        # Policy Header: Title, Live Status Tag, Presets, and Action Buttons
        p_head = ctk.CTkFrame(policy_card, fg_color="transparent")
        p_head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        p_head.grid_columnconfigure(1, weight=1)

        # Title & Status Label
        t_box = ctk.CTkFrame(p_head, fg_color="transparent")
        t_box.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(t_box, text="Inspection policy", font=FONTS.h2, text_color=C.TEXT).grid(row=0, column=0, sticky="w")
        self.pending_label = ctk.CTkLabel(
            t_box, text="All 6 PPE policies enforced · Real-time vision audit",
            font=FONTS.small, text_color=C.OK, wraplength=260, justify="left")
        self.pending_label.grid(row=1, column=0, sticky="w", pady=(2, 0))

        # Preset & Action Buttons
        p_actions = ctk.CTkFrame(p_head, fg_color="transparent")
        p_actions.grid(row=1, column=0, sticky="w", pady=(12, 4))

        preset_names = {v["title"]: k for k, v in POLICY_PRESETS.items()}
        ctk.CTkOptionMenu(p_actions, values=list(preset_names), width=150,
                          fg_color=C.BRICK_BLUE, button_color=C.BRICK_BLUE_HOVER,
                          command=lambda name: self._apply_preset(preset_names[name])).grid(row=0, column=0, padx=4)
        ctk.CTkButton(p_actions, text="Clear", width=55, height=28,
                      fg_color=C.SURFACE_3, text_color=C.TEXT, hover_color=C.SURFACE_HOVER,
                      command=self._clear_all_policies).grid(row=0, column=1, padx=4)

        # PPE Toggle Grid: Spacious 4-Column x 2-Row Control Tiles
        grid = ctk.CTkScrollableFrame(policy_card, fg_color=C.SURFACE_2, corner_radius=10, border_width=1, border_color=C.BORDER)
        grid.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 12))
        for col_idx in range(1):
            grid.grid_columnconfigure(col_idx, weight=1, uniform="gear_tile")

        self.switches = {}
        self.switch_labels = {}
        entries = [("person", "👷", "Worker Detection")] + [(k, v["icon"], v["label"]) for k, v in PPE.items()]
        for i, (key, icon, label) in enumerate(entries):
            row_idx, col_idx = i, 0
            tile = ctk.CTkFrame(grid, fg_color=C.SURFACE_3, corner_radius=8, border_width=1, border_color=C.BORDER)
            tile.grid(row=row_idx, column=col_idx, sticky="nsew", padx=2, pady=4)
            tile.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(tile, text=icon, font=ctk.CTkFont(size=20)).grid(row=0, column=0, padx=(6, 4), pady=10)
            lbl = ctk.CTkLabel(tile, text=label, font=FONTS.btn, text_color=C.TEXT, anchor="w")
            lbl.grid(row=0, column=1, sticky="w", pady=10)
            self.switch_labels[key] = lbl

            sw = ctk.CTkSwitch(
                tile, text="", command=lambda k=key: self._on_switch_toggle(k),
                progress_color=C.BRICK_YELLOW, fg_color=C.SURFACE_2, button_color="#FFFFFF",
                button_hover_color=C.BRICK_YELLOW_LIGHT, width=44, switch_width=44, switch_height=22,
                text_color_disabled=C.DIM)
            sw.grid(row=0, column=2, padx=(4, 6), pady=10)
            if self.applied[key]:
                sw.select()
            self.switches[key] = sw

        # 8th Tile: Active Vision Engine status tile
        status_tile = ctk.CTkFrame(grid, fg_color=C.SURFACE_3, corner_radius=8, border_width=1, border_color=C.BORDER)
        status_tile.grid(row=7, column=0, sticky="nsew", padx=2, pady=4)
        status_tile.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(status_tile, text="🛡️", font=ctk.CTkFont(size=20)).grid(row=0, column=0, padx=(6, 4), pady=10)
        ctk.CTkLabel(status_tile, text="Vision Engine", font=FONTS.btn, text_color=C.TEXT, anchor="w").grid(row=0, column=1, sticky="w", pady=10)
        status_chip = ctk.CTkLabel(
            status_tile, text="AI VISION", font=FONTS.badge, text_color=C.OK,
            fg_color=C.SURFACE_2, corner_radius=6, height=24, padx=8)
        status_chip.grid(row=0, column=2, padx=(4, 6), pady=10)

        # 4. Right Section: Personnel Safety Audit Stream
        right = ctk.CTkFrame(self, fg_color=C.SURFACE, corner_radius=12, border_width=2, border_color=C.BORDER, width=380)
        right.grid(row=2, column=2, sticky="nsew", padx=(8, 16), pady=(0, 18))
        right.grid_propagate(False)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)

        # Studs on right card top
        studs_top = ctk.CTkFrame(right, fg_color="transparent", height=8)
        studs_top.grid(row=0, column=0, sticky="w", padx=16, pady=(6, 0))
        for i in range(4):
            ctk.CTkFrame(studs_top, width=14, height=5, corner_radius=2, fg_color=C.BORDER_LIGHT).grid(row=0, column=i, padx=3)

        # Right Header
        rh = ctk.CTkFrame(right, fg_color="transparent")
        rh.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 6))
        rh.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(rh, text="Worker inspections", font=FONTS.h2, text_color=C.TEXT).grid(row=0, column=0, sticky="w")

        rh_actions = ctk.CTkFrame(rh, fg_color="transparent")
        rh_actions.grid(row=1, column=0, sticky="w", pady=(6, 0))

        ctk.CTkButton(
            rh_actions, text="New inspection", width=110, height=26, corner_radius=6, font=FONTS.small,
            fg_color=C.SURFACE_3, hover_color=C.SURFACE_HOVER, text_color=C.TEXT,
            border_width=1, border_color=C.BORDER, command=self._reset_station_idle
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            rh_actions, text="Printer setup", width=95, height=26, corner_radius=6, font=FONTS.small,
            fg_color=C.SURFACE_3, hover_color=C.SURFACE_HOVER, text_color=C.TEXT,
            border_width=1, border_color=C.BORDER, command=self._printer_setup
        ).pack(side="left", padx=(0, 8))

        self.summary_badge = ctk.CTkLabel(rh_actions, text="0 In View", font=FONTS.badge, fg_color=C.SURFACE_2,
                                          corner_radius=6, height=24, padx=8, text_color=C.MUTED)
        self.summary_badge.pack(side="left")

        # Scrollable Cards List
        self.list = ctk.CTkScrollableFrame(
            right, fg_color="transparent", scrollbar_button_color=C.BORDER,
            scrollbar_button_hover_color=C.MUTED)
        self.list.grid(row=2, column=0, sticky="nsew", padx=(10, 6), pady=(0, 6))
        self.list.grid_columnconfigure(0, weight=1)

        self.empty = ctk.CTkLabel(self.list, text="", font=FONTS.body, text_color=C.MUTED, justify="center")
        self.empty.grid(row=0, column=0, pady=80)

        # Right Bottom Activity Card
        foot = ctk.CTkFrame(right, fg_color=C.SURFACE_2, corner_radius=8, border_width=1, border_color=C.BORDER)
        foot.grid(row=3, column=0, sticky="ew", padx=14, pady=(6, 12))
        foot.grid_columnconfigure(0, weight=1)

        self.toast = ctk.CTkLabel(foot, text="Waiting for camera", font=FONTS.small,
                                  text_color=C.MUTED, anchor="w", justify="left", wraplength=230)
        self.toast.grid(row=0, column=0, sticky="w", padx=12, pady=10)

        ctk.CTkButton(foot, text="Open Logs", width=86, height=28, corner_radius=6, font=FONTS.btn,
                      fg_color=C.SURFACE_3, hover_color=C.SURFACE_HOVER, text_color=C.TEXT,
                      command=self._open_logs).grid(row=0, column=1, padx=10, pady=8)

        self._set_empty(person_enabled=self.settings.snapshot().person)
        self._relayout()
        self._update_policy_status()

    def _create_kpi_card(self, parent, col, title, initial_val, accent_color, subtitle=""):
        # Container holding top studs and the brick card body
        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.grid(row=0, column=col, sticky="ew", padx=5)
        container.grid_columnconfigure(0, weight=1)

        # Row of 4 raised brick studs
        studs_bar = ctk.CTkFrame(container, fg_color="transparent", height=8)
        studs_bar.grid(row=0, column=0, pady=(0, 2))
        for i in range(4):
            stud = ctk.CTkFrame(studs_bar, width=16, height=6, corner_radius=3, fg_color=accent_color)
            stud.grid(row=0, column=i, padx=5)

        # Main brick card body
        card = ctk.CTkFrame(container, fg_color=C.SURFACE, corner_radius=10, border_width=2, border_color=accent_color)
        card.grid(row=1, column=0, sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        lbl = ctk.CTkLabel(card, text=title, font=FONTS.kpi_lbl, text_color=C.MUTED)
        lbl.grid(row=0, column=0, sticky="w", padx=14, pady=(4, 0))

        val = ctk.CTkLabel(card, text=initial_val, font=FONTS.kpi_val, text_color=accent_color)
        val.grid(row=1, column=0, sticky="w", padx=14, pady=(2, 2))

        sub = ctk.CTkLabel(card, text=subtitle, font=FONTS.small, text_color=C.DIM)
        sub.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 2))
        return val

    def _make_switch(self, master, text, command):
        return ctk.CTkSwitch(
            master, text=text, command=command, font=FONTS.body, text_color=C.TEXT,
            progress_color=C.BRICK_YELLOW, fg_color=C.SURFACE_3, button_color="#FFFFFF",
            button_hover_color=C.BRICK_YELLOW_LIGHT, switch_width=40, switch_height=20,
            text_color_disabled=C.DIM)

    # -------------------------------------------------------------- Handlers
    def _toggle_theme(self):
        self._current_theme = "light" if self._current_theme == "dark" else "dark"
        ctk.set_appearance_mode(self._current_theme.capitalize())
        is_light = (self._current_theme == "light")
        self.theme_btn.configure(
            text="Dark mode" if is_light else "Light mode",
            fg_color=C.SURFACE_3,
            hover_color=C.SURFACE_HOVER
        )
        self.logger.theme = self._current_theme
        self._update_bg_canvas(force=True)
        feed_bg = "#1E293B" if is_light else "#0D1014"
        feed_fg = "#64748B" if is_light else "#A8B4C4"
        self.feed_holder.configure(bg=feed_bg)
        self.feed.configure(bg=feed_bg, fg=feed_fg)
        self._toast(f"Switched theme to {self._current_theme.capitalize()} Mode")

    def _on_switch_toggle(self, key):
        val = (self.switches[key].get() == 1)
        if key == "person":
            self.settings.set_person(val)
        else:
            self.settings.set_ppe(key, val)
        self.applied[key] = val
        self._update_policy_status()

    def _update_policy_status(self):
        active_count = sum(1 for k in PPE if self.applied.get(k, False))
        if active_count == 0:
            txt = "No PPE items active  ·  Only presence monitoring"
            col = C.MUTED
        elif active_count == len(PPE):
            txt = f"All {len(PPE)} PPE policies enforced  ·  Live"
            col = C.OK
        else:
            txt = f"Active Policies: {active_count}/6 PPE items enforced  ·  Live"
            col = C.OK
        self.pending_label.configure(text=txt, text_color=col)

    def _clear_all_policies(self):
        for key, sw in self.switches.items():
            if key != "person":
                sw.deselect()
                self.settings.set_ppe(key, False)
                self.applied[key] = False
        self._update_policy_status()
        self.profile_label.configure(text="Standard: Custom (No PPE)")
        self._toast("Cleared all PPE policy rules · No gear violations will be flagged")

    def _enforce_all_policies(self):
        for key, sw in self.switches.items():
            sw.select()
            if key == "person":
                self.settings.set_person(True)
            else:
                self.settings.set_ppe(key, True)
            self.applied[key] = True
        self._update_policy_status()
        self.profile_label.configure(text="Standard: Full Gear (All 6)")
        self._toast("Enforced all 6 PPE safety policies live")

    def _apply_preset(self, preset_key):
        pdata = POLICY_PRESETS.get(preset_key)
        if not pdata:
            return
        keys = set(pdata["keys"])
        for key, sw in self.switches.items():
            if key == "person":
                sw.select()
                self.settings.set_person(True)
                self.applied[key] = True
            elif key in keys:
                sw.select()
                self.settings.set_ppe(key, True)
                self.applied[key] = True
            else:
                sw.deselect()
                self.settings.set_ppe(key, False)
                self.applied[key] = False
        active_count = len(keys)
        self.pending_label.configure(
            text=f"Active Preset: {pdata['title']} ({active_count}/6 items) · Applied live",
            text_color=C.OK)
        self.profile_label.configure(text=f"Standard: {pdata['title']} ({active_count} items)")
        self._toast(f"Switched inspection profile to: {pdata['title']}")

    def _clear_cards(self):
        for card in self.cards.values():
            card.destroy()
        self.cards.clear()
        self.last_seen.clear()
        self._relayout()

    def _restart_worker(self, text):
        if self._inspection_idle:
            self._toast("Policy saved. Click New inspection to start detection.")
            return
        if self._restarting:
            return
        self._restarting = True
        self._clear_cards()
        self.latest = None
        self._have_frame = False
        self.retry_btn.place_forget()
        self.feed.configure(image="", text=text, fg=C.MUTED[1])
        self.live_label.configure(text="● RESTARTING", text_color=C.WARN)
        self.summary_badge.configure(text="Restarting", text_color=C.MUTED)
        self.worker.stop()
        self._wait_for_worker()

    def _wait_for_worker(self):
        if self.worker.is_alive():
            self._wait_id = self.after(50, self._wait_for_worker)
            return
        self._wait_id = None
        for q in (self.frames, self.msgs):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        self.logger.reset_memory()
        self.worker = DetectionWorker(self.args, self.settings, self.logger, self.frames, self.msgs,
                                      self.model_cache)
        self.worker.start()
        self._restarting = False
        self._toast("Camera restarted with updated safety policies")

    def _on_boxes(self):
        self.settings.set_draw_ppe(self.boxes_switch.get() == 1)

    def _on_latch(self):
        on = (self.latch_switch.get() == 1)
        self.settings.set_latch_equipped(on)
        if on:
            self._toast("Permanent Latch ON: Once gear is verified, it stays equipped for worker")
        else:
            self._toast("Permanent Latch OFF: Dynamic equipment tracking (times out after 3s)")

    def _reset_latch(self):
        self._restart_worker("Resetting worker inspection latch...")
        self._toast("Worker equipment states reset")

    def _open_logs(self):
        try:
            open_folder(self.logger.root.resolve())
        except Exception as exc:
            self._toast(f"Could not open directory: {exc}")

    def _reconnect_arduino(self):
        if not self.arduino:
            self._toast("Arduino integration disabled (--arduino-port none)")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Connect safety station")
        dialog.geometry("500x280")
        dialog.transient(self)
        ctk.CTkLabel(dialog, text="Arduino connection", font=FONTS.h1).pack(pady=(20, 8))
        detail = self.arduino.last_error or "Select the USB port shown in Arduino IDE. Close Serial Monitor first."
        ctk.CTkLabel(dialog, text=detail, wraplength=450).pack(padx=20, pady=8)
        ports = self.arduino.list_ports()
        choices = {f"{port} - {desc}": port for port, desc in ports if "bluetooth" not in desc.lower()}
        if not choices:
            ctk.CTkLabel(dialog, text="No USB serial port found. Check the USB cable.").pack(pady=12)
            return
        selected = ctk.CTkOptionMenu(dialog, values=list(choices), width=400)
        selected.pack(pady=12)
        def connect():
            port = choices[selected.get()]
            self.arduino.set_port(port)
            self._toast(f"Connecting to {port} at 9600 baud...")
            dialog.destroy()
        ctk.CTkButton(dialog, text="Connect", command=connect).pack(pady=12)

    def _reset_station_idle(self):
        """Reset the safety inspection station to Ready/Idle: turns off alarms and readies for next worker."""
        self._last_arduino_state = "IDLE"
        self._last_arduino_detail = ""
        self._last_worker_seen_time = 0.0
        self._fail_first_seen_time = None
        self._last_fail_detail_time = 0.0
        if self.arduino:
            self.arduino.send_alert("IDLE", force=True)
        self._inspection_idle = False
        if self._advice_label is not None:
            self._advice_label.destroy()
            self._advice_label = None
        self._restart_worker("Starting a new inspection...")

    def _printer_setup(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Printer setup")
        dialog.geometry("430x300")
        dialog.transient(self)
        ctk.CTkLabel(dialog, text="Make every receipt readable", font=FONTS.h1).pack(pady=(24, 12))
        ctk.CTkLabel(dialog, text="T7-S printer and USB connection: 9600 baud.\nUses the proven Adafruit printer driver.\nUpload the updated Arduino sketch first.",
                     wraplength=380, justify="left").pack(padx=24, pady=8)
        rate = ctk.CTkOptionMenu(dialog, values=["9600"])
        rate.pack(pady=8)
        def apply():
            if self.arduino and self.arduino.is_connected():
                self.arduino.set_printer_baud(int(rate.get()))
                self.arduino.test_print()
                self._toast("Test queued. Check the printed paper.")
                dialog.destroy()
            else:
                self._toast("Connect the station before testing.")
        ctk.CTkButton(dialog, text="Print test receipt", command=apply).pack(pady=16)

    def _test_printer(self):
        """Send a test receipt to the thermal printer via Arduino."""
        if self.arduino:
            self.arduino.test_print()
            self._toast("Sent test print command to thermal printer")
        else:
            self._toast("Arduino not connected")

    def _toast(self, message: str):
        """Displays toast status message on UI and console without crashing."""
        print(f"[STATUS] {message}")
        if hasattr(self, "toast") and self.toast:
            try:
                self.toast.configure(text=message)
            except Exception:
                pass

    def capture_log(self, track_id):
        if self._inspection_idle or self._restarting:
            self._toast("Click New inspection before capturing again.")
            return
        res = self.latest
        person = next((p for p in res.people if p.track_id == track_id), None) if res else None
        if person is None:
            self._toast(f"Worker #{track_id:02d} not found in frame")
            return
        if any(value not in (WORN, MISSING) for value in person.items.values()):
            self._toast("Wait for the PPE check to finish before capturing.")
            return
        # Stop detection immediately; retain the captured frame until New inspection.
        self._inspection_idle = True
        self.worker.stop()
        self.live_stream_badge.configure(text="IDLE", text_color=C.WARN)
        self.live_label.configure(text="IDLE - Click New inspection to resume", text_color=C.WARN)
        for card in self.cards.values():
            card.button.configure(state="disabled")
        self.summary_badge.configure(text="Inspection captured", text_color=C.MUTED)
        advice = inspection_advice(person.items)
        self._advice_label = ctk.CTkLabel(self.list, text="Worker #{:02d}\n\n{}".format(track_id, "\n".join(advice)),
                                        wraplength=310, justify="left", font=FONTS.body,
                                        text_color=C.TEXT)
        self._advice_label.grid(row=len(self.cards) + 1, column=0, sticky="ew", padx=8, pady=16)
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            path = self.logger.save(res.raw, person.track_id, person.box, person.rows(), person.missing)
            report = [APP_NAME, f"Worker #{track_id:02d}", now_str]
            report.extend(f"{label}: {state}" for label, state in person.rows())
            report.extend(["", *advice])
            path.with_suffix(".txt").write_text("\n".join(report), encoding="utf-8")
            self.kpi_logs.configure(text=f"{self.logger.count} Captured")
            print_status = "Printer offline; report saved only."
            if self.arduino and self.arduino.is_connected():
                self.arduino.send_print_report(track_id=person.track_id,
                    status=("NOT_ASSESSED" if not person.items else "FAIL" if person.missing else "PASS"),
                    score_pct=int(person.compliance_score * 100), logged_at=now_str, rows=person.rows())
                print_status = "Receipt queued."
                self.arduino.send_alert("IDLE", force=True)
                self._last_arduino_state = "IDLE"
                self._last_arduino_detail = ""
                self._fail_first_seen_time = None
            self._toast(f"{print_status} Detection idle. Click New inspection.")
        except Exception as exc:
            self._toast(f"Capture error: {exc}. Detection idle; click New inspection to retry.")

    # -------------------------------------------------------------- Loop
    def _update_clock(self):
        now_str = datetime.now().strftime("%Y-%m-%d  %I:%M:%S %p")
        self.clock_label.configure(text=now_str)
        if self.arduino and hasattr(self, "arduino_btn"):
            self.arduino_btn.configure(text=self.arduino.get_status_label())
        self._clock_id = self.after(1000, self._update_clock)

    def _poll(self):
        try:
            self._drain_messages()
            try:
                res = self.frames.get_nowait()
            except queue.Empty:
                res = None
            if res is not None and not self._restarting and not self._inspection_idle:
                self._render(res)
        except Exception:
            traceback.print_exc()
        finally:
            self._poll_id = self.after(12, self._poll)

    def _drain_messages(self):
        while True:
            try:
                kind, payload = self.msgs.get_nowait()
            except queue.Empty:
                return
            if self._restarting or self._inspection_idle:
                continue
            if kind == "status":
                if not self._have_frame:
                    self.feed.configure(image="", text=payload, fg=C.MUTED[1])
                    self.live_label.configure(text="● " + payload.rstrip("."), text_color=C.WARN)
            elif kind == "ready":
                self._apply_available(payload)
            elif kind == "error":
                self._have_frame = False
                self.feed.configure(image="", text=payload, fg=C.BAD[1], justify="center")
                self.live_label.configure(text="● CAMERA STOPPED", text_color=C.BAD)
                self.live_stream_badge.configure(text="● OFFLINE", text_color=C.BAD)
                self.retry_btn.place(relx=0.5, rely=1.0, y=-24, anchor="s")

    def _apply_available(self, available):
        for key, sw in self.switches.items():
            if key != "person" and key not in available:
                sw.deselect()
                self.settings.set_ppe(key, False)
                self.applied[key] = False
                sw.configure(state="disabled")
                if key in self.switch_labels:
                    self.switch_labels[key].configure(text=f"{PPE[key]['label']} (n/a)", text_color=C.DIM)
        self._update_policy_status()

    def _render(self, res):
        self.live_stream_badge.configure(text="● LIVE", text_color=C.OK)
        self.latest = res
        self._have_frame = True
        self._show_frame(res.view)
        self.live_label.configure(text="● LIVE STREAM", text_color=C.OK)
        self._update_people(res)

    def _show_frame(self, bgr):
        cw, ch = self.feed_holder.winfo_width(), self.feed_holder.winfo_height()
        if cw < 10 or ch < 10:
            return
        h, w = bgr.shape[:2]
        scale = min(cw / w, ch / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        rgb = cv2.cvtColor(cv2.resize(bgr, (nw, nh), interpolation=interp), cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.feed.configure(image=self._photo, text="")

    def _update_people(self, res):
        now = time.monotonic()
        layout_changed = False

        for person in res.people:
            card = self.cards.get(person.track_id)
            if card is None:
                card = PersonCard(self.list, person.track_id, self.capture_log)
                self.cards[person.track_id] = card
                layout_changed = True
            self.last_seen[person.track_id] = now
            card.update_person(person, res.active)

        present = {p.track_id for p in res.people}
        for tid in list(self.cards):
            if tid not in present and now - self.last_seen.get(tid, 0) > self.GONE_GRACE:
                self.cards.pop(tid).destroy()
                self.last_seen.pop(tid, None)
                layout_changed = True

        self._set_empty(person_enabled=res.person_enabled)
        if layout_changed:
            self._relayout()

        n = len(res.people)
        # Calculate TOTAL item violations across all detected people
        total_violations = sum(len(p.missing) for p in res.people)

        # Update KPI Ribbon
        self.kpi_workers.configure(text=f"{n} {'Worker' if n == 1 else 'Workers'}")
        if n == 0:
            comp_rate = 100
            comp_color = C.OK
        else:
            # Average equipment compliance rate across detected workers
            avg_score = sum(p.compliance_score for p in res.people) / n
            comp_rate = int(round(avg_score * 100))
            comp_color = C.OK if comp_rate >= 90 else (C.WARN if comp_rate >= 40 else C.BAD)

        self.kpi_compliance.configure(text=f"{comp_rate}%" if res.active and n else "--", text_color=comp_color if res.active and n else C.MUTED)

        # Display accurate total violations matching the cards on the right
        self.kpi_violations.configure(
            text=f"{total_violations} {'Violation' if total_violations == 1 else 'Violations'}",
            text_color=C.BAD if total_violations > 0 else C.MUTED)
        self.kpi_logs.configure(text=f"{self.logger.count} Captured")

        # Automatic Arduino Hardware Alert (LEDs + Buzzer - Synchronized with live detection)
        if self.arduino and not getattr(self, "_inspection_idle", False) and not getattr(self, "_on_loading_screen", False):
            now = time.monotonic()
            if n == 0 or not res.active:
                # Debounce empty view: wait 1.2s before declaring station IDLE
                # to prevent 1-frame tracker flickers from resetting station and re-triggering alarms
                if now - getattr(self, "_last_worker_seen_time", 0.0) > 1.2:
                    if self._last_arduino_state != "IDLE":
                        self.arduino.send_alert("IDLE")
                        self._last_arduino_state = "IDLE"
                        self._last_arduino_detail = ""
                    self._fail_first_seen_time = None
            else:
                self._last_worker_seen_time = now
                viol_count = sum(len(p.missing) for p in res.people)
                all_passed = (
                    viol_count == 0 and
                    len(res.people) > 0 and
                    all(p.compliance_score >= 1.0 for p in res.people)
                )

                if all_passed:
                    self._fail_first_seen_time = None
                    if self._last_arduino_state != "PASS":
                        self.arduino.send_alert("PASS", detail="100% EQUIPPED", force=True)
                        self._last_arduino_state = "PASS"
                        self._last_arduino_detail = "100% EQUIPPED"
                elif viol_count > 0:
                    if getattr(self, "_fail_first_seen_time", None) is None:
                        self._fail_first_seen_time = now

                    # 0.25s grace period on initial transition from IDLE/PASS to avoid 1-frame YOLO startup jitter
                    if (now - self._fail_first_seen_time >= 0.25) or (self._last_arduino_state == "FAIL"):
                        fail_detail = f"{viol_count} VIOLATION(S)"
                        if self._last_arduino_state != "FAIL":
                            self.arduino.send_alert("FAIL", detail=fail_detail, force=True)
                            self._last_arduino_state = "FAIL"
                            self._last_arduino_detail = fail_detail
                            self._last_fail_detail_time = now
                        elif (fail_detail != getattr(self, "_last_arduino_detail", "")) and (now - getattr(self, "_last_fail_detail_time", 0.0) >= 1.0):
                            self.arduino.send_alert("FAIL", detail=fail_detail, force=True)
                            self._last_arduino_detail = fail_detail
                            self._last_fail_detail_time = now

        # Update Sidebar Header
        if not res.person_enabled:
            self.summary_badge.configure(text="Detection Off", text_color=C.MUTED, fg_color=C.SURFACE_2)
        else:
            badge_text = f"{n} in view  ·  {total_violations} violation{'s' if total_violations != 1 else ''}"
            badge_color = C.BAD if total_violations > 0 else C.OK
            badge_bg = C.BAD_BG if total_violations > 0 else C.OK_BG
            self.summary_badge.configure(text=badge_text, text_color=badge_color, fg_color=badge_bg)

    def _set_empty(self, person_enabled):
        text = ("No workers detected in view\nWorkers will appear here dynamically when detected."
                if person_enabled else
                "Person tracking is disabled\nEnable person detection in the policy deck below.")
        if text != self._empty_text:
            self._empty_text = text
            self.empty.configure(text=text)

    def _relayout(self):
        for i, tid in enumerate(sorted(self.cards)):
            self.cards[tid].grid(row=i, column=0, sticky="ew", padx=4, pady=(0, 10))
        if self.cards:
            self.empty.grid_remove()
        else:
            self.empty.grid(row=0, column=0, pady=80)

    def on_close(self):
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
        if self._wait_id is not None:
            self.after_cancel(self._wait_id)
        if self._clock_id is not None:
            self.after_cancel(self._clock_id)
        if self.arduino:
            self.arduino.stop()
        self.worker.stop()
        self.worker.join(timeout=3)
        self.destroy()


# --------------------------------------------------------------------------- #
# CLI Parser & Entry Point
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=f"{APP_NAME} — {APP_SUBTITLE}")
    p.add_argument("--model", default="models/sh17_yolov9e.pt")
    p.add_argument("--model-url", default=None,
                   help="Where to download the model from if it is missing.")
    p.add_argument("--no-download", action="store_true",
                   help="Never download the model automatically.")
    p.add_argument("--camera", type=int, default=0,
                   help="Camera device index (default: 0).")
    p.add_argument("--confidence", type=float, default=0.30)
    p.add_argument("--imgsz", type=int, default=384,
                   help="Inference size. Lower = faster on CPU (320/384/416/480/640). Default: 384.")
    p.add_argument("--device", default=None,
                   help="Inference device: cpu or 0 (NVIDIA GPU). Default: auto.")
    p.add_argument("--hold-seconds", type=float, default=3.0,
                   help="Persistence: seconds a person or worn item is kept after brief detection gaps.")
    p.add_argument("--person-conf", type=float, default=0.40,
                   help="Confidence threshold for person detection.")
    p.add_argument("--class-conf", nargs="+", default=["gloves=0.10", "earmuffs=0.05", "mask=0.10", "helmet=0.15", "glasses=0.15"], metavar="ITEM=VALUE",
                   help="Per-PPE confidence overrides, e.g. gloves=0.20 glasses=0.25.")
    p.add_argument("--person-iou", type=float, default=0.55,
                   help="IoU threshold for person box deduplication.")
    p.add_argument("--ppe-iou", type=float, default=0.55,
                   help="IoU threshold for PPE item deduplication.")
    p.add_argument("--ppe", nargs="+", default=["all"], choices=list(PPE) + ["all", "none"],
                   metavar="ITEM", help="PPE enforced at startup.")
    p.add_argument("--miss-frames", type=int, default=0,
                   help="Detections an item must be absent before marking as missing.")
    p.add_argument("--log-dir", default="logs",
                   help="Directory where CSV records and evidence images are saved.")
    p.add_argument("--log-theme", choices=["light", "dark"], default="dark",
                   help="Checklist theme for saved audit reports.")
    p.add_argument("--draw-ppe", action="store_true", help="Start with individual PPE boxes shown.")
    p.add_argument("--no-latch", action="store_true", help="Start with permanent latch mode disabled.")
    p.add_argument("--arduino-port", default="auto",
                   help="Arduino COM port (e.g. COM3 or auto). Use 'none' to disable Arduino.")
    args = p.parse_args(argv)

    overrides = {}
    for item in args.class_conf:
        if item.lower() == "none":
            continue
        key, _, value = item.partition("=")
        if key not in PPE:
            p.error(f"--class-conf: unknown item '{key}' (choose from {', '.join(PPE)})")
        try:
            overrides[key] = float(value)
        except ValueError:
            p.error(f"--class-conf: '{item}' requires a numeric value, e.g. {key}=0.2")
    args.class_conf = overrides
    return args


def main():
    args = parse_args()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    App(args).mainloop()


if __name__ == "__main__":
    main()