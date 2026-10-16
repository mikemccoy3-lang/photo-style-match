"""Build a color/tone 'look profile' from a set of reference images."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image
from skimage import color

N_HIST_BINS = 256


@dataclass
class LookProfile:
    lab_mean: list[float]   # [L, a, b] averaged across references
    lab_std: list[float]    # [L, a, b] averaged across references
    l_hist: list[float]     # averaged cumulative histogram of L, 256 bins over 0-100
    black_point: float      # averaged 1st percentile of L
    white_point: float      # averaged 99th percentile of L
    source_images: list[str]

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    @staticmethod
    def load(path: Path) -> "LookProfile":
        data = json.loads(Path(path).read_text())
        return LookProfile(**data)

    @staticmethod
    def from_untrusted(data: object) -> "LookProfile":
        """Validate a profile that arrived from outside (e.g. a browser).

        A profile is only numbers, but they feed array math, so check shapes
        and ranges strictly and rebuild a clean object instead of trusting
        whatever was sent. Raises ValueError on anything off.
        """
        if not isinstance(data, dict):
            raise ValueError("profile must be an object")

        def numbers(key: str, length: int, lo: float, hi: float) -> list[float]:
            raw = data.get(key)
            if not isinstance(raw, list) or len(raw) != length:
                raise ValueError(f"{key} must be a list of {length} numbers")
            out = []
            for v in raw:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise ValueError(f"{key} has a non-numeric value")
                if not lo <= v <= hi:
                    raise ValueError(f"{key} value out of range")
                out.append(float(v))
            return out

        lab_mean = numbers("lab_mean", 3, -200.0, 200.0)
        lab_std = numbers("lab_std", 3, 0.0, 200.0)
        if not 0.0 <= lab_mean[0] <= 100.0:
            raise ValueError("lab_mean lightness out of range")
        hist = numbers("l_hist", N_HIST_BINS, 0.0, 1.0)
        hist = np.maximum.accumulate(np.asarray(hist)).tolist()  # a CDF never decreases

        def scalar(key: str) -> float:
            v = data.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0.0 <= v <= 100.0:
                raise ValueError(f"{key} must be a number between 0 and 100")
            return float(v)

        return LookProfile(lab_mean, lab_std, hist, scalar("black_point"), scalar("white_point"), [])


def _lab_of(img: Image.Image) -> np.ndarray:
    rgb = np.asarray(img.convert("RGB")).astype(np.float64) / 255.0
    return color.rgb2lab(rgb)


def _load_lab(path: Path) -> np.ndarray:
    return _lab_of(Image.open(path))


def _profile_from_labs(labs: Iterable[np.ndarray], names: list[str]) -> LookProfile:
    means, stds, hists, blacks, whites = [], [], [], [], []

    for lab in labs:
        l, a, b = lab[..., 0], lab[..., 1], lab[..., 2]

        means.append([l.mean(), a.mean(), b.mean()])
        stds.append([l.std(), a.std(), b.std()])
        blacks.append(np.percentile(l, 1))
        whites.append(np.percentile(l, 99))

        hist, _ = np.histogram(l, bins=N_HIST_BINS, range=(0, 100), density=True)
        cdf = np.cumsum(hist)
        cdf /= cdf[-1]
        hists.append(cdf)

    if not means:
        raise ValueError("Need at least one reference image")

    return LookProfile(
        lab_mean=np.mean(means, axis=0).tolist(),
        lab_std=np.mean(stds, axis=0).tolist(),
        l_hist=np.mean(hists, axis=0).tolist(),
        black_point=float(np.mean(blacks)),
        white_point=float(np.mean(whites)),
        source_images=names,
    )


def build_profile(reference_paths: list[Path]) -> LookProfile:
    if not reference_paths:
        raise ValueError("Need at least one reference image")
    return _profile_from_labs((_load_lab(p) for p in reference_paths), [str(p) for p in reference_paths])


def build_profile_from_images(images: list[Image.Image]) -> LookProfile:
    """Same as build_profile, for images already in memory (no file paths)."""
    return _profile_from_labs((_lab_of(im) for im in images), [])
