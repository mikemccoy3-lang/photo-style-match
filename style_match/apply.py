"""Apply a look profile to a target image.

This is content-adaptive: color transfer is computed relative to the
target photo's own a/b statistics (Reinhard-style transfer), and the L
(luminance) channel is histogram-matched to the reference's averaged tone
curve rather than just linearly rescaled. That's what a static LUT can't
do -- the correction each pixel gets depends on the whole image it's in.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from skimage import color

from .profile import LookProfile, N_HIST_BINS


def _match_l_to_profile(l: np.ndarray, profile: LookProfile) -> np.ndarray:
    """Histogram-match the L channel to the profile's averaged CDF."""
    target_cdf = np.asarray(profile.l_hist)
    bin_edges = np.linspace(0, 100, N_HIST_BINS + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    src_hist, _ = np.histogram(l, bins=N_HIST_BINS, range=(0, 100), density=True)
    src_cdf = np.cumsum(src_hist)
    src_cdf /= src_cdf[-1]

    # Find each pixel's percentile under the source CDF, then map that
    # percentile to the L value it corresponds to under the target CDF.
    src_percentile = np.interp(l, bin_centers, src_cdf)
    matched = np.interp(src_percentile, target_cdf, bin_centers)
    return matched


def apply_profile(image: Image.Image, profile: LookProfile, strength: float = 0.85) -> Image.Image:
    rgb = np.asarray(image.convert("RGB")).astype(np.float64) / 255.0
    lab = color.rgb2lab(rgb)
    l, a, b = lab[..., 0], lab[..., 1].copy(), lab[..., 2].copy()

    for i, channel in enumerate((a, b), start=1):
        src_mean, src_std = channel.mean(), channel.std() or 1.0
        ref_mean, ref_std = profile.lab_mean[i], profile.lab_std[i]
        channel[:] = (channel - src_mean) / src_std * ref_std + ref_mean

    l_matched = _match_l_to_profile(l, profile)

    graded_lab = np.stack([l_matched, a, b], axis=-1)
    graded_rgb = np.clip(color.lab2rgb(graded_lab), 0, 1)

    blended = np.clip(rgb * (1 - strength) + graded_rgb * strength, 0, 1)
    return Image.fromarray((blended * 255).astype(np.uint8))
