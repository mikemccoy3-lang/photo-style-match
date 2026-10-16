"""Fast, memory-light look transfer built on a 3D color lookup table.

`apply.apply_profile` runs the whole LAB pipeline on every pixel, which needs
several full-size float64 arrays and is too heavy for a 512MB server at
photo resolution. But once a photo's own statistics are measured (its
lightness curve and a/b mean and spread), the look is just a fixed function
of each pixel's RGB. So:

  1. measure the photo's statistics on a small copy (cheap),
  2. evaluate the look on a grid of RGB values (65^3 points, cheap),
  3. bake intensity, color-match and look-mixing settings into the grid,
  4. hand the grid to Pillow, which applies it to the full-size photo in C
     with trilinear interpolation (fast, and memory is just the image).

Because every blend is a weighted sum of RGB values, the baked grid is
linear in its parts, so previews built from separate tables blend exactly
the way the final single table does.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageFilter
from skimage import color

from .profile import LookProfile, N_HIST_BINS

LUT_SIZE = 65          # final renders
PREVIEW_LUT_SIZE = 33  # thumbnails: ~8x less work, ~0.5/255 average difference
STATS_DIMENSION = 512

_BIN_EDGES = np.linspace(0, 100, N_HIST_BINS + 1)
_BIN_CENTERS = (_BIN_EDGES[:-1] + _BIN_EDGES[1:]) / 2


@dataclass(frozen=True)
class SourceStats:
    """What the look needs to know about the photo it is being applied to."""

    l_cdf: np.ndarray
    a_mean: float
    a_std: float
    b_mean: float
    b_std: float


def source_stats(image: Image.Image) -> SourceStats:
    small = image.convert("RGB")
    if max(small.size) > STATS_DIMENSION:
        small = small.copy()
        small.thumbnail((STATS_DIMENSION, STATS_DIMENSION), Image.Resampling.LANCZOS)
    lab = color.rgb2lab(np.asarray(small).astype(np.float64) / 255.0)
    l, a, b = lab[..., 0], lab[..., 1], lab[..., 2]

    hist, _ = np.histogram(l, bins=N_HIST_BINS, range=(0, 100), density=True)
    cdf = np.cumsum(hist)
    cdf /= cdf[-1]
    return SourceStats(cdf, float(a.mean()), float(a.std()) or 1.0, float(b.mean()), float(b.std()) or 1.0)


@lru_cache(maxsize=2)
def _grid(size: int) -> tuple[np.ndarray, np.ndarray]:
    """RGB lattice (red varies fastest, as Pillow expects) and its LAB values."""
    axis = np.linspace(0, 1, size)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    rgb = np.stack([r.ravel(), g.ravel(), b.ravel()], axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lab = color.rgb2lab(rgb.reshape(1, -1, 3)).reshape(-1, 3)
    return rgb, lab


def _look_nodes(stats: SourceStats, profile: LookProfile, size: int) -> tuple[np.ndarray, np.ndarray]:
    """The look on every lattice point: (tone only, tone + color), both RGB.

    "Tone only" remaps lightness to the photographer's tone curve and leaves
    the photo's own colors alone. "Tone + color" also moves the photo's color
    balance and saturation spread toward the photographer's.
    """
    _, lab = _grid(size)
    percentile = np.interp(lab[:, 0], _BIN_CENTERS, stats.l_cdf)
    l2 = np.interp(percentile, np.asarray(profile.l_hist), _BIN_CENTERS)

    a2 = (lab[:, 1] - stats.a_mean) / stats.a_std * profile.lab_std[1] + profile.lab_mean[1]
    b2 = (lab[:, 2] - stats.b_mean) / stats.b_std * profile.lab_std[2] + profile.lab_mean[2]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tone = color.lab2rgb(np.stack([l2, lab[:, 1], lab[:, 2]], axis=1).reshape(1, -1, 3)).reshape(-1, 3)
        full = color.lab2rgb(np.stack([l2, a2, b2], axis=1).reshape(1, -1, 3)).reshape(-1, 3)
    return np.clip(tone, 0, 1), np.clip(full, 0, 1)


def build_table(
    stats: SourceStats,
    looks: list[tuple[LookProfile, float]],
    strength: float,
    color_amount: float,
    size: int = LUT_SIZE,
) -> np.ndarray:
    """Bake every setting into one lattice of output RGB values.

    looks: (profile, weight) pairs whose weights sum to 1 (one pair for a
        single look, two to mix looks).
    strength: how far to move from the original photo (0 = untouched).
    color_amount: 0 keeps the photo's own colors and only borrows tone,
        1 also borrows the photographer's color balance.
    """
    rgb, _ = _grid(size)
    out = (1.0 - strength) * rgb
    for profile, weight in looks:
        if weight <= 0:
            continue
        tone, full = _look_nodes(stats, profile, size)
        out = out + strength * weight * ((1.0 - color_amount) * tone + color_amount * full)
    return np.clip(out, 0.0, 1.0)


def apply_table(image: Image.Image, table: np.ndarray, size: int = LUT_SIZE) -> Image.Image:
    lut = ImageFilter.Color3DLUT(size, table.astype(np.float32).reshape(-1), channels=3)
    return image.convert("RGB").filter(lut)


def develop(
    image: Image.Image,
    looks: list[tuple[LookProfile, float]],
    strength: float,
    color_amount: float,
) -> Image.Image:
    """Grade a photo of any size: stats from a small copy, LUT on the full one."""
    table = build_table(source_stats(image), looks, strength, color_amount)
    return apply_table(image, table)


def preview_pair(image: Image.Image, profile: LookProfile) -> tuple[Image.Image, Image.Image]:
    """Full-strength (tone only, tone + color) renders of a small photo.

    The browser blends these with the original for its intensity and color
    sliders, so one request per look covers every slider position. Uses the
    coarser preview lattice: a 512px thumbnail doesn't need 65^3 precision
    and the free server's CPU is small.
    """
    stats = source_stats(image)
    tone_nodes, full_nodes = _look_nodes(stats, profile, PREVIEW_LUT_SIZE)
    return apply_table(image, tone_nodes, PREVIEW_LUT_SIZE), apply_table(image, full_nodes, PREVIEW_LUT_SIZE)
