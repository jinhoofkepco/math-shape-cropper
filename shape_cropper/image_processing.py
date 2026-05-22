from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageEnhance, ImageOps


BBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class ProcessedCrop:
    image: Image.Image
    original_bbox: BBox
    refined_bbox: BBox


def normalize_bbox(bbox: BBox) -> BBox:
    x0, y0, x1, y1 = bbox
    left, right = sorted((int(round(x0)), int(round(x1))))
    top, bottom = sorted((int(round(y0)), int(round(y1))))
    return left, top, right, bottom


def clip_bbox(bbox: BBox, width: int, height: int) -> BBox:
    x0, y0, x1, y1 = normalize_bbox(bbox)
    x0 = max(0, min(width, x0))
    x1 = max(0, min(width, x1))
    y0 = max(0, min(height, y0))
    y1 = max(0, min(height, y1))
    return x0, y0, x1, y1


def expand_bbox(bbox: BBox, width: int, height: int, margin: int) -> BBox:
    x0, y0, x1, y1 = bbox
    return clip_bbox((x0 - margin, y0 - margin, x1 + margin, y1 + margin), width, height)


def process_crop(page_image: Image.Image, bbox: BBox) -> ProcessedCrop:
    """Crop a rough user selection, refine it around dark figure pixels, and clean it."""
    page_image = ImageOps.exif_transpose(page_image).convert("RGB")
    page_width, page_height = page_image.size
    original = clip_bbox(bbox, page_width, page_height)
    if original[2] - original[0] < 4 or original[3] - original[1] < 4:
        raise ValueError("Crop area is too small.")

    selection_width = original[2] - original[0]
    selection_height = original[3] - original[1]
    search_margin = max(8, int(max(selection_width, selection_height) * 0.04))
    search_box = expand_bbox(original, page_width, page_height, search_margin)
    search_crop = page_image.crop(search_box)

    local_bbox = find_foreground_bbox(search_crop)
    if local_bbox is None:
        refined = original
    else:
        padding = max(8, int(max(local_bbox[2] - local_bbox[0], local_bbox[3] - local_bbox[1]) * 0.035))
        refined = (
            search_box[0] + local_bbox[0] - padding,
            search_box[1] + local_bbox[1] - padding,
            search_box[0] + local_bbox[2] + padding,
            search_box[1] + local_bbox[3] + padding,
        )
        refined = clip_bbox(refined, page_width, page_height)

    refined_image = page_image.crop(refined)
    return ProcessedCrop(
        image=enhance_for_saving(refined_image),
        original_bbox=original,
        refined_bbox=refined,
    )


def find_foreground_bbox(image: Image.Image) -> BBox | None:
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    if gray.size == 0:
        return None

    threshold = _otsu_threshold(gray)
    threshold = min(225, max(70, threshold + 10))
    mask = gray <= threshold

    dark_pixels = int(mask.sum())
    min_pixels = max(12, int(gray.size * 0.001))
    if dark_pixels < min_pixels:
        return None

    row_range = _dense_projection_range(mask, axis=1)
    col_range = _dense_projection_range(mask, axis=0)
    if row_range is None or col_range is None:
        ys, xs = np.where(mask)
        if xs.size < min_pixels:
            return None

        trim = 0.75 if xs.size > 200 else 0.0
        x0, x1 = np.percentile(xs, [trim, 100 - trim])
        y0, y1 = np.percentile(ys, [trim, 100 - trim])

        left = max(0, int(np.floor(x0)))
        top = max(0, int(np.floor(y0)))
        right = min(image.width, int(np.ceil(x1)) + 1)
        bottom = min(image.height, int(np.ceil(y1)) + 1)
    else:
        top, bottom = row_range
        left, right = col_range
        focused = mask[top:bottom, left:right]
        ys, xs = np.where(focused)
        if xs.size < min_pixels:
            return None
        left = left + int(xs.min())
        right = left + int(xs.max() - xs.min()) + 1
        top = top + int(ys.min())
        bottom = top + int(ys.max() - ys.min()) + 1

    if right - left < 4 or bottom - top < 4:
        return None
    return left, top, right, bottom


def enhance_for_saving(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    arr = np.asarray(gray, dtype=np.uint8).copy()

    bright_cut = int(np.percentile(arr, 88))
    white_cut = max(205, min(245, bright_cut + 8))
    arr[arr >= white_cut] = 255

    cleaned = Image.fromarray(arr, mode="L")
    cleaned = ImageEnhance.Contrast(cleaned).enhance(1.35)
    cleaned = ImageEnhance.Sharpness(cleaned).enhance(1.15)
    return cleaned.convert("RGB")


def sanitize_name(value: str, fallback: str) -> str:
    value = value.strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value)
    value = value.strip(" ._")
    return value or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _otsu_threshold(gray: np.ndarray) -> int:
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = gray.size
    if total == 0:
        return 127

    cumulative = np.cumsum(hist)
    cumulative_mean = np.cumsum(hist * np.arange(256))
    global_mean = cumulative_mean[-1]

    denominator = cumulative * (total - cumulative)
    valid = denominator > 0
    variance = np.zeros(256, dtype=np.float64)
    variance[valid] = ((global_mean * cumulative[valid] - cumulative_mean[valid] * total) ** 2) / denominator[valid]
    return int(np.argmax(variance))


def _dense_projection_range(mask: np.ndarray, axis: int) -> tuple[int, int] | None:
    counts = mask.sum(axis=axis)
    length = counts.size
    cross_length = mask.shape[axis]
    min_count = max(2, int(cross_length * 0.004))
    active = np.where(counts >= min_count)[0]
    if active.size == 0:
        return None

    merge_gap = max(3, int(length * 0.01))
    segments: list[tuple[int, int]] = []
    start = int(active[0])
    previous = int(active[0])
    for value in active[1:]:
        value = int(value)
        if value - previous <= merge_gap + 1:
            previous = value
            continue
        segments.append((start, previous))
        start = previous = value
    segments.append((start, previous))

    if not segments:
        return None

    def segment_weight(segment: tuple[int, int]) -> int:
        return int(counts[segment[0] : segment[1] + 1].sum())

    best_index = max(range(len(segments)), key=lambda idx: segment_weight(segments[idx]))
    range_start, range_end = segments[best_index]
    best_weight = max(1, segment_weight(segments[best_index]))
    range_size = max(1, range_end - range_start + 1)
    proximity = max(8, int(range_size * 0.22), int(length * 0.025))
    tight_gap = max(8, int(length * 0.015))
    min_neighbor_weight = max(8, int(best_weight * 0.03))

    significant_weight = max(20, int(best_weight * 0.08))
    included = {
        idx
        for idx, segment in enumerate(segments)
        if segment_weight(segment) >= significant_weight
    }
    included.add(best_index)

    changed = True
    while changed:
        changed = False
        for idx, segment in enumerate(segments):
            if idx in included:
                continue
            nearest_gap = min(_segment_gap(segment, segments[included_idx]) for included_idx in included)
            if nearest_gap <= proximity and (
                nearest_gap <= tight_gap or segment_weight(segment) >= min_neighbor_weight
            ):
                included.add(idx)
                changed = True

    range_start = min(segments[idx][0] for idx in included)
    range_end = max(segments[idx][1] for idx in included)

    return max(0, range_start), min(length, range_end + 1)


def _segment_gap(first: tuple[int, int], second: tuple[int, int]) -> int:
    if first[1] < second[0]:
        return second[0] - first[1]
    if second[1] < first[0]:
        return first[0] - second[1]
    return 0
