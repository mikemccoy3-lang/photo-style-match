"""Build a color/tone 'look profile' from a set of reference images."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

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


def _load_lab(path: Path) -> np.ndarray:
    img = Image.open(path).convert("RGB")
    rgb = np.asarray(img).astype(np.float64) / 255.0
    return color.rgb2lab(rgb)


def build_profile(reference_paths: list[Path]) -> LookProfile:
    if not reference_paths:
        raise ValueError("Need at least one reference image")

    means, stds, hists, blacks, whites = [], [], [], [], []

    for p in reference_paths:
        lab = _load_lab(p)
        l, a, b = lab[..., 0], lab[..., 1], lab[..., 2]

        means.append([l.mean(), a.mean(), b.mean()])
        stds.append([l.std(), a.std(), b.std()])
        blacks.append(np.percentile(l, 1))
        whites.append(np.percentile(l, 99))

        hist, _ = np.histogram(l, bins=N_HIST_BINS, range=(0, 100), density=True)
        cdf = np.cumsum(hist)
        cdf /= cdf[-1]
        hists.append(cdf)

    return LookProfile(
        lab_mean=np.mean(means, axis=0).tolist(),
        lab_std=np.mean(stds, axis=0).tolist(),
        l_hist=np.mean(hists, axis=0).tolist(),
        black_point=float(np.mean(blacks)),
        white_point=float(np.mean(whites)),
        source_images=[str(p) for p in reference_paths],
    )
