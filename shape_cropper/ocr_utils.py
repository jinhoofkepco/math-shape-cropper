from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageOps


TESSERACT_CANDIDATES = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
)


@lru_cache(maxsize=1)
def _load_pytesseract():
    try:
        import pytesseract
    except Exception as exc:  # pragma: no cover - depends on local install
        return None, f"pytesseract 없음: {exc}"

    for candidate in TESSERACT_CANDIDATES:
        if candidate.exists():
            pytesseract.pytesseract.tesseract_cmd = str(candidate)
            break
    return pytesseract, ""


def ocr_status() -> str:
    pytesseract, error = _load_pytesseract()
    if pytesseract is None:
        return error
    try:
        version = pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - depends on local install
        return f"Tesseract 실행 불가: {exc}"
    return f"Tesseract OCR 사용 가능 ({version})"


def recognize_problem_number(image: Image.Image | None) -> str:
    text = _ocr_digits(image, allow_decimal=False)
    match = re.search(r"\d{1,4}", text)
    if match is None:
        return ""
    return str(int(match.group()))


def recognize_answer(image: Image.Image | None) -> str:
    text = _ocr_digits(image, allow_decimal=True, allow_fraction=True)
    return normalize_answer_text(text)


def normalize_answer_text(text: str) -> str:
    cleaned = (
        text.replace(",", "")
        .replace("O", "0")
        .replace("o", "0")
        .replace("l", "1")
        .replace("I", "1")
        .strip()
    )
    fraction = re.search(r"\d+\s*/\s*\d+", cleaned)
    if fraction:
        return re.sub(r"\s+", "", fraction.group())

    number = re.search(r"\d+(?:\.\d+)?", cleaned)
    return number.group() if number else ""


def _ocr_digits(
    image: Image.Image | None,
    *,
    allow_decimal: bool,
    allow_fraction: bool = False,
) -> str:
    if image is None:
        return ""

    pytesseract, _error = _load_pytesseract()
    if pytesseract is None:
        return ""

    prepared = _prepare_for_digit_ocr(image)
    whitelist = "0123456789"
    if allow_decimal:
        whitelist += ".,"
    if allow_fraction:
        whitelist += "/"

    config_base = f"--oem 3 -c tessedit_char_whitelist={whitelist}"
    results: list[str] = []
    for psm in (7, 8, 13, 6, 11):
        try:
            text = pytesseract.image_to_string(
                prepared,
                lang="eng",
                config=f"{config_base} --psm {psm}",
            )
        except Exception:
            continue
        if text.strip():
            results.append(text.strip())

    return " ".join(results)


def _prepare_for_digit_ocr(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    scale = 4 if max(gray.size) < 160 else 3
    gray = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)
    gray = ImageOps.autocontrast(gray, cutoff=2)
    binary = gray.point(lambda value: 0 if value < 150 else 255)
    return _remove_border_noise(binary)


def _remove_border_noise(image: Image.Image) -> Image.Image:
    pixels = np.asarray(image.convert("L"), dtype=np.uint8)
    mask = pixels < 128
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    cleaned = np.zeros_like(mask, dtype=bool)
    border_margin = max(3, min(width, height) // 30)

    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            pixels_in_component: list[tuple[int, int]] = []
            touches_border = False
            while stack:
                cy, cx = stack.pop()
                pixels_in_component.append((cy, cx))
                if (
                    cx < border_margin
                    or cy < border_margin
                    or cx >= width - border_margin
                    or cy >= height - border_margin
                ):
                    touches_border = True
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny = cy + dy
                        nx = cx + dx
                        if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            stack.append((ny, nx))

            if touches_border or len(pixels_in_component) < 12:
                continue
            for py, px in pixels_in_component:
                cleaned[py, px] = True

    result = np.full(mask.shape, 255, dtype=np.uint8)
    result[cleaned] = 0
    return Image.fromarray(result, mode="L")
