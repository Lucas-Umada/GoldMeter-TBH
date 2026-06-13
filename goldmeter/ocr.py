"""OCR pipeline: preprocessing tuned for small pixel-font game text,
tesseract invocation, and parsing of gold amounts / stage labels."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps


def _find_tesseract() -> None:
    """On Windows, locate tesseract.exe when it isn't on PATH.

    The UB-Mannheim/winget installer defaults to Program Files and does not
    add itself to PATH.
    """
    if shutil.which("tesseract"):
        return
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
    ]
    for base in candidates:
        exe = base / "Tesseract-OCR" / "tesseract.exe"
        if exe.is_file():
            pytesseract.pytesseract.tesseract_cmd = str(exe)
            return


if os.name == "nt":
    _find_tesseract()


def tesseract_available() -> bool:
    """True when the tesseract binary pytesseract will call actually exists."""
    cmd = pytesseract.pytesseract.tesseract_cmd
    return shutil.which(cmd) is not None or Path(cmd).is_file()

GOLD_WHITELIST = "0123456789.,KMBTkmbt"
STAGE_WHITELIST = "0123456789-"

_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}

_GOLD_RE = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*([kmbt])?", re.IGNORECASE
)
_STAGE_RE = re.compile(r"(\d{1,3})\s*-\s*(\d{1,3})|(\d{1,4})")


def _otsu_threshold(gray: Image.Image) -> int:
    """Classic Otsu's method over the grayscale histogram."""
    hist = gray.histogram()
    total = sum(hist)
    if total == 0:
        return 128
    sum_all = sum(i * h for i, h in enumerate(hist))
    sum_bg = 0.0
    weight_bg = 0
    best_thresh, best_var = 128, -1.0
    for i in range(256):
        weight_bg += hist[i]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += i * hist[i]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_all - sum_bg) / weight_fg
        var_between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var_between > best_var:
            best_var = var_between
            best_thresh = i
    return best_thresh


def preprocess(img: Image.Image, scale: int = 4) -> Image.Image:
    """Grayscale → upscale → autocontrast → binarize → dark text on white.

    Game HUD text is tiny and often light-on-dark with outlines; tesseract
    wants large dark glyphs on a white background.
    """
    gray = ImageOps.grayscale(img)
    if scale > 1:
        gray = gray.resize((gray.width * scale, gray.height * scale), Image.LANCZOS)
    gray = ImageOps.autocontrast(gray)
    thresh = _otsu_threshold(gray)
    binary = gray.point(lambda p: 255 if p > thresh else 0, mode="L")
    # If most pixels are dark, the text is the light part — invert so the
    # glyphs end up dark on a white background.
    if sum(binary.histogram()[128:]) < (binary.width * binary.height) / 2:
        binary = ImageOps.invert(binary)
    # Breathing room around the glyphs helps tesseract's line detection.
    return ImageOps.expand(binary, border=10 * max(1, scale // 2), fill=255)


def ocr_line(img: Image.Image, whitelist: str) -> str:
    """Run tesseract on a preprocessed single-line crop."""
    cfg = f"--psm 7 -c tessedit_char_whitelist={whitelist}"
    return pytesseract.image_to_string(img, config=cfg).strip()


def parse_gold(text: str) -> float | None:
    """Parse a gold amount like '12,345', '1.23M', '987' → float.

    Returns None when nothing number-like is present.
    """
    text = text.strip().replace(" ", "")
    if not text:
        return None
    m = _GOLD_RE.search(text)
    if not m:
        return None
    number, suffix = m.group(1), m.group(2)
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number):
        # Thousands separators: 1,234,567 or 1.234.567
        value = float(re.sub(r"[.,]", "", number))
    else:
        value = float(number.replace(",", "."))
    if suffix:
        value *= _SUFFIX[suffix.lower()]
    return value


def parse_stage(text: str) -> str | None:
    """Parse a stage label like '2-6' (act-stage) or a bare number '12'."""
    text = text.strip()
    if not text:
        return None
    m = _STAGE_RE.search(text)
    if not m:
        return None
    if m.group(1) is not None:
        return f"{int(m.group(1))}-{int(m.group(2))}"
    return str(int(m.group(3)))


def read_gold(img: Image.Image, scale: int = 4) -> tuple[float | None, str]:
    """OCR a gold-counter crop. Returns (value, raw_text)."""
    raw = ocr_line(preprocess(img, scale), GOLD_WHITELIST)
    return parse_gold(raw), raw


def read_stage(img: Image.Image, scale: int = 4) -> tuple[str | None, str]:
    """OCR a stage-label crop. Returns (stage, raw_text)."""
    raw = ocr_line(preprocess(img, scale), STAGE_WHITELIST)
    return parse_stage(raw), raw
