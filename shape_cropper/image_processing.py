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


@dataclass(frozen=True)
class WorkbookProcessedCrop(ProcessedCrop):
    answer_bbox: BBox | None
    answer_image: Image.Image | None
    problem_number_image: Image.Image
    red_pixel_count: int


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


def process_crop(page_image: Image.Image, bbox: BBox, refine_bounds: bool = False, make_transparent: bool = True) -> ProcessedCrop:
    """Crop a user selection and clean it for saving."""
    page_image = ImageOps.exif_transpose(page_image).convert("RGB")
    page_width, page_height = page_image.size
    original = clip_bbox(bbox, page_width, page_height)
    if original[2] - original[0] < 4 or original[3] - original[1] < 4:
        raise ValueError("Crop area is too small.")

    if not refine_bounds:
        cropped = page_image.crop(original)
        return ProcessedCrop(
            image=enhance_for_saving(cropped, make_transparent),
            original_bbox=original,
            refined_bbox=original,
        )

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
        image=enhance_for_saving(refined_image, make_transparent),
        original_bbox=original,
        refined_bbox=refined,
    )


def process_workbook_crop(page_image: Image.Image, bbox: BBox, make_transparent: bool = True) -> WorkbookProcessedCrop:
    """Crop a workbook problem, remove red answer marks, and prepare OCR regions."""
    page_image = ImageOps.exif_transpose(page_image).convert("RGB")
    page_width, page_height = page_image.size
    original = clip_bbox(bbox, page_width, page_height)
    if original[2] - original[0] < 4 or original[3] - original[1] < 4:
        raise ValueError("Crop area is too small.")

    cropped = page_image.crop(original)
    red_mask_raw = find_red_answer_mask(cropped, expand=False)
    red_mask = _expand_mask(red_mask_raw, iterations=2)
    answer_bbox = mask_bbox(red_mask)
    answer_image = None
    if answer_bbox is not None:
        number_bbox = answer_number_bbox(red_mask_raw, answer_bbox)
        answer_image = cropped.crop(expand_bbox(number_bbox or answer_bbox, cropped.width, cropped.height, 8))

    cleaned = remove_masked_pixels(cropped, red_mask)
    number_crop = crop_problem_number_region(cropped)
    return WorkbookProcessedCrop(
        image=enhance_for_saving(cleaned, make_transparent),
        original_bbox=original,
        refined_bbox=original,
        answer_bbox=answer_bbox,
        answer_image=answer_image,
        problem_number_image=number_crop,
        red_pixel_count=int(red_mask.sum()),
    )


def find_red_answer_mask(image: Image.Image, *, expand: bool = True) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    mask = (
        (red >= 105)
        & (red - green >= 28)
        & (red - blue >= 18)
        & (red >= (green * 1.18))
        & (red >= (blue * 1.12))
    )
    if expand:
        return _expand_mask(mask, iterations=2)
    return mask


def mask_bbox(mask: np.ndarray) -> BBox | None:
    ys, xs = np.where(mask)
    if xs.size < 8:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def answer_number_bbox(mask: np.ndarray, answer_bbox: BBox) -> BBox | None:
    x0, y0, x1, y1 = answer_bbox
    local = mask[y0:y1, x0:x1]
    if local.size == 0:
        return None

    counts = local.sum(axis=0)
    active = np.where(counts > 0)[0]
    if active.size == 0:
        return None

    merge_gap = max(3, int(local.shape[1] * 0.035))
    split_gap = max(4, int(local.shape[1] * 0.05))
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

    included = [segments[0]]
    for segment in segments[1:]:
        gap = segment[0] - included[-1][1] - 1
        if gap > split_gap:
            break
        included.append(segment)

    left = included[0][0]
    right = included[-1][1] + 1
    focused = local[:, left:right]
    ys, xs = np.where(focused)
    if xs.size == 0:
        return None
    return x0 + left + int(xs.min()), y0 + int(ys.min()), x0 + left + int(xs.max()) + 1, y0 + int(ys.max()) + 1


def remove_masked_pixels(image: Image.Image, mask: np.ndarray) -> Image.Image:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
    if mask.any():
        rgb[mask] = 255
    return Image.fromarray(rgb, mode="RGB")


def crop_problem_number_region(image: Image.Image) -> Image.Image:
    width, height = image.size
    crop_width = min(width, max(100, int(width * 0.24)))
    crop_height = min(height, max(150, int(height * 0.50)))
    search = image.crop((0, 0, crop_width, crop_height))
    circle_bbox = find_number_badge_bbox(search)
    if circle_bbox is None:
        return search
    return search.crop(expand_bbox(circle_bbox, search.width, search.height, 5))


def find_number_badge_bbox(image: Image.Image) -> BBox | None:
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    mask = gray < 225
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    candidates: list[tuple[int, int, int, int, int]] = []

    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            count = 0
            min_x = max_x = x
            min_y = max_y = y
            while stack:
                cy, cx = stack.pop()
                count += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny = cy + dy
                        nx = cx + dx
                        if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            stack.append((ny, nx))

            component_width = max_x - min_x + 1
            component_height = max_y - min_y + 1
            if component_width < 20 or component_height < 20:
                continue
            if component_width > 58 or component_height > 58:
                continue
            aspect = component_width / max(1, component_height)
            if 0.65 <= aspect <= 1.45 and count >= 160 and min_x <= int(width * 0.35):
                candidates.append((count, min_x, min_y, max_x + 1, max_y + 1))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[1], item[2], -item[0]))
    _count, left, top, right, bottom = candidates[0]
    return left, top, right, bottom


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


def enhance_for_saving(image: Image.Image, make_transparent: bool = True) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.2)
    cleaned = ImageEnhance.Sharpness(gray).enhance(1.15)
    if make_transparent:
        return make_white_background_transparent(cleaned)
    return cleaned


def make_white_background_transparent(image: Image.Image) -> Image.Image:
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    ink_cut = _adaptive_ink_cut(gray)
    ink_mask = gray <= ink_cut
    ink_mask = _remove_dense_ink_interiors(ink_mask)
    ink_mask = _remove_tiny_components(ink_mask)

    alpha = np.zeros(gray.shape, dtype=np.uint8)
    alpha[ink_mask] = 255

    soft_mask = (gray <= ink_cut + 28) & _dilate_mask(ink_mask)
    soft_alpha = np.clip((ink_cut + 28 - gray[soft_mask]) * 9, 0, 220).astype(np.uint8)
    alpha[soft_mask] = np.maximum(alpha[soft_mask], soft_alpha)

    black = np.zeros_like(gray, dtype=np.uint8)
    rgba = np.dstack([black, black, black, alpha])
    return Image.fromarray(rgba, mode="RGBA")


def _adaptive_ink_cut(gray: np.ndarray) -> int:
    dark_percentile = int(np.percentile(gray, 4))
    return max(42, min(72, dark_percentile + 10))


def _dilate_mask(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, mode="constant", constant_values=False)
    result = np.zeros_like(mask, dtype=bool)
    for y_offset in range(3):
        for x_offset in range(3):
            result |= padded[y_offset : y_offset + mask.shape[0], x_offset : x_offset + mask.shape[1]]
    return result


def _expand_mask(mask: np.ndarray, iterations: int) -> np.ndarray:
    expanded = mask.astype(bool)
    for _ in range(iterations):
        expanded = _dilate_mask(expanded)
    return expanded


def _remove_dense_ink_interiors(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 2, mode="constant", constant_values=False)
    neighbor_count = np.zeros(mask.shape, dtype=np.uint8)
    for y_offset in range(5):
        for x_offset in range(5):
            neighbor_count += padded[
                y_offset : y_offset + mask.shape[0],
                x_offset : x_offset + mask.shape[1],
            ].astype(np.uint8)
    return mask & (neighbor_count < 18)


def _remove_tiny_components(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    cleaned = np.zeros_like(mask, dtype=bool)

    for y in range(height):
        for x in range(width):
            if not mask[y, x] or visited[y, x]:
                continue

            stack = [(y, x)]
            visited[y, x] = True
            pixels: list[tuple[int, int]] = []
            min_x = max_x = x
            min_y = max_y = y

            while stack:
                cy, cx = stack.pop()
                pixels.append((cy, cx))
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)

                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny = cy + dy
                        nx = cx + dx
                        if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            stack.append((ny, nx))

            component_width = max_x - min_x + 1
            component_height = max_y - min_y + 1
            if len(pixels) >= 12 or component_width >= 9 or component_height >= 9:
                for py, px in pixels:
                    cleaned[py, px] = True

    return cleaned


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
