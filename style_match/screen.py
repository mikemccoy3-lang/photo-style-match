"""Clean up reference photos before they define a look.

Screenshots and social-media downloads often carry things that would skew a
look's statistics: white or black bars, black-and-white frames (no color to
learn), duplicates, tiny thumbnails. Bars get trimmed off; the rest are
skipped with a reason so the user can see what happened.
"""
from __future__ import annotations

import warnings

import numpy as np
from PIL import Image
from skimage import color

MIN_SIDE = 200
MIN_CONTENT_FRACTION = 0.4
BW_CHROMA = 1.5
DUPLICATE_CORRELATION = 0.995


def _bar_mask(means: np.ndarray, stds: np.ndarray) -> np.ndarray:
    """True for rows/columns that are one flat near-white or near-black color."""
    return (stds < 0.02) & ((means > 0.93) | (means < 0.02))


def _leading(mask: np.ndarray) -> int:
    n = 0
    for flag in mask:
        if not flag:
            break
        n += 1
    return n


def trim_bars(image: Image.Image) -> tuple[Image.Image, bool]:
    """Crop flat white/black bars off the edges. Returns (image, trimmed?)."""
    a = np.asarray(image.convert("RGB")).astype(np.float64) / 255.0
    h, w = a.shape[:2]

    rows = _bar_mask(a.mean(axis=(1, 2)), a.std(axis=(1, 2)))
    top, bottom = _leading(rows), _leading(rows[::-1])
    if top + bottom >= h:
        return image, False

    inner = a[top:h - bottom]
    cols = _bar_mask(inner.mean(axis=(0, 2)), inner.std(axis=(0, 2)))
    left, right = _leading(cols), _leading(cols[::-1])
    if left + right >= w:
        return image, False

    if not (top or bottom or left or right):
        return image, False
    return image.crop((left, top, w - right, h - bottom)), True


def _mean_chroma(image: Image.Image) -> float:
    small = image.convert("RGB").copy()
    small.thumbnail((128, 128))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lab = color.rgb2lab(np.asarray(small).astype(np.float64) / 255.0)
    return float(np.hypot(lab[..., 1], lab[..., 2]).mean())


def _fingerprint(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L").resize((16, 16), Image.Resampling.BILINEAR)).astype(np.float64).ravel()


def screen_references(images: list[Image.Image]) -> tuple[list[Image.Image], list[dict]]:
    """Return (usable images, notes). Each note: {"index", "action", "reason"}."""
    kept: list[Image.Image] = []
    prints: list[np.ndarray] = []
    notes: list[dict] = []

    for index, original in enumerate(images):
        image, trimmed = trim_bars(original)
        if trimmed:
            if image.width * image.height < MIN_CONTENT_FRACTION * original.width * original.height:
                notes.append({"index": index, "action": "skipped", "reason": "mostly bars or blank space"})
                continue
            notes.append({"index": index, "action": "trimmed", "reason": "removed bars"})

        if min(image.size) < MIN_SIDE:
            notes.append({"index": index, "action": "skipped", "reason": "too small"})
            continue
        if _mean_chroma(image) < BW_CHROMA:
            notes.append({"index": index, "action": "skipped", "reason": "black-and-white"})
            continue

        fp = _fingerprint(image)
        if any(
            np.std(fp) > 0 and np.std(p) > 0 and np.corrcoef(fp, p)[0, 1] > DUPLICATE_CORRELATION
            for p in prints
        ):
            notes.append({"index": index, "action": "skipped", "reason": "duplicate"})
            continue

        prints.append(fp)
        kept.append(image)

    return kept, notes
