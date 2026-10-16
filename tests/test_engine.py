"""Tests for the lookup-table engine, profile validation, and reference screening.

Run from the project root:  python3 -m unittest discover -s tests -v
"""
import copy
import json
import sys
import unittest
import warnings
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from style_match import transform  # noqa: E402
from style_match.apply import apply_profile  # noqa: E402
from style_match.profile import LookProfile  # noqa: E402
from style_match.screen import screen_references, trim_bars  # noqa: E402

warnings.filterwarnings("ignore")


def profile(name: str) -> LookProfile:
    return LookProfile.load(ROOT / "profiles" / name / "profile.json")


def synthetic_photo(seed: int = 1, size=(240, 160)) -> Image.Image:
    """Smooth, colorful, photo-ish image (blurred noise plus a gradient)."""
    rng = np.random.default_rng(seed)
    h, w = size[1], size[0]
    noise = rng.random((h // 8 + 2, w // 8 + 2, 3))
    big = np.asarray(
        Image.fromarray((noise * 255).astype(np.uint8)).resize((w, h), Image.Resampling.BICUBIC)
    ).astype(float) / 255
    ramp = np.linspace(0.1, 0.9, w)[None, :, None]
    arr = np.clip(0.55 * big + 0.45 * ramp, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def diff(a: Image.Image, b: Image.Image) -> np.ndarray:
    return np.abs(np.asarray(a).astype(int) - np.asarray(b).astype(int))


class LutMatchesExactPipeline(unittest.TestCase):
    def test_close_to_apply_profile_for_every_profile(self):
        photo = synthetic_photo()
        for name in ("larry-chen", "camden-thrasher", "amy-shore"):
            with self.subTest(profile=name):
                prof = profile(name)
                exact = apply_profile(photo, prof, strength=1.0)
                fast = transform.develop(photo, [(prof, 1.0)], 1.0, 1.0)
                d = diff(exact, fast)
                self.assertLess(d.mean(), 2.5)
                self.assertLess(np.percentile(d, 99), 12)

    def test_zero_strength_is_identity(self):
        photo = synthetic_photo()
        out = transform.develop(photo, [(profile("larry-chen"), 1.0)], 0.0, 1.0)
        self.assertLessEqual(diff(photo, out).max(), 1)


class SlidersBehave(unittest.TestCase):
    def test_preview_blend_equals_final_render(self):
        """The page blends tone/full previews client-side; the server bakes the
        same weights into one table. They must agree."""
        photo = synthetic_photo(2)
        prof = profile("peter-mckinnon")
        s, c = 0.7, 0.4
        tone, full = transform.preview_pair(photo, prof)
        o = np.asarray(photo).astype(float)
        blended = (1 - s) * o + s * ((1 - c) * np.asarray(tone) + c * np.asarray(full))
        final = np.asarray(transform.develop(photo, [(prof, 1.0)], s, c)).astype(float)
        # previews use a coarser 33^3 lattice than the final 65^3 one, so allow
        # a little interpolation difference, but the average must stay tiny
        err = np.abs(blended - final)
        self.assertLess(err.mean(), 1.0)
        self.assertLess(err.max(), 6.0)

    def test_color_zero_keeps_original_chroma_better_than_color_one(self):
        from skimage import color as skcolor

        photo = synthetic_photo(3)
        prof = profile("camden-thrasher")  # strong cool cast
        base = skcolor.rgb2lab(np.asarray(photo) / 255.0)

        def chroma_shift(c):
            out = skcolor.rgb2lab(np.asarray(transform.develop(photo, [(prof, 1.0)], 1.0, c)) / 255.0)
            return np.abs(out[..., 1:] - base[..., 1:]).mean()

        self.assertLess(chroma_shift(0.0), chroma_shift(1.0))

    def test_mixing_is_the_average_of_the_two_looks(self):
        photo = synthetic_photo(4)
        a, b = profile("larry-chen"), profile("jamey-price")
        mixed = np.asarray(transform.develop(photo, [(a, 0.5), (b, 0.5)], 1.0, 1.0)).astype(float)
        only_a = np.asarray(transform.develop(photo, [(a, 1.0)], 1.0, 1.0)).astype(float)
        only_b = np.asarray(transform.develop(photo, [(b, 1.0)], 1.0, 1.0)).astype(float)
        self.assertLess(np.abs(mixed - (only_a + only_b) / 2).max(), 2.0)

    def test_mix_weight_extremes_match_single_looks(self):
        photo = synthetic_photo(5)
        a, b = profile("larry-chen"), profile("krispy-media")
        only_b = transform.develop(photo, [(b, 1.0)], 0.85, 0.5)
        mixed = transform.develop(photo, [(a, 0.0), (b, 1.0)], 0.85, 0.5)
        self.assertLessEqual(diff(only_b, mixed).max(), 1)

    def test_handles_non_rgb_modes_and_large_sizes(self):
        prof = profile("larry-chen")
        rgba = synthetic_photo().convert("RGBA")
        self.assertEqual(transform.develop(rgba, [(prof, 1.0)], 0.85, 0.5).mode, "RGB")
        big = synthetic_photo(size=(3000, 2000))
        self.assertEqual(transform.develop(big, [(prof, 1.0)], 0.85, 0.5).size, (3000, 2000))


class ProfileValidation(unittest.TestCase):
    def good(self) -> dict:
        d = json.loads((ROOT / "profiles" / "larry-chen" / "profile.json").read_text())
        d.pop("source_images", None)
        return d

    def test_accepts_a_real_profile_and_drops_extras(self):
        d = self.good()
        d["evil"] = "x"
        p = LookProfile.from_untrusted(d)
        self.assertEqual(len(p.l_hist), 256)
        self.assertEqual(p.source_images, [])

    def test_rejects_malformed_profiles(self):
        bad_cases = {
            "not a dict": [1, 2, 3],
            "short mean": {**self.good(), "lab_mean": [1, 2]},
            "nan": {**self.good(), "lab_std": [float("nan"), 1, 1]},
            "bool": {**self.good(), "lab_mean": [True, 1, 1]},
            "string": {**self.good(), "lab_mean": ["a", 1, 1]},
            "huge": {**self.good(), "lab_mean": [50, 1e9, 1]},
            "negative std": {**self.good(), "lab_std": [-1, 1, 1]},
            "hist wrong length": {**self.good(), "l_hist": [0.5] * 10},
            "hist out of range": {**self.good(), "l_hist": [2.0] * 256},
            "missing black": {k: v for k, v in self.good().items() if k != "black_point"},
            "lightness out of range": {**self.good(), "lab_mean": [150, 0, 0]},
        }
        for label, data in bad_cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                LookProfile.from_untrusted(data)

    def test_non_monotonic_histogram_is_repaired(self):
        d = copy.deepcopy(self.good())
        d["l_hist"][100] = 0.0
        p = LookProfile.from_untrusted(d)
        self.assertTrue(all(b >= a for a, b in zip(p.l_hist, p.l_hist[1:])))


class ReferenceScreening(unittest.TestCase):
    def test_trims_white_and_black_bars(self):
        photo = synthetic_photo(size=(300, 200))
        framed = Image.new("RGB", (400, 320), (255, 255, 255))
        framed.paste(photo, (50, 60))
        trimmed, did = trim_bars(framed)
        self.assertTrue(did)
        self.assertAlmostEqual(trimmed.width, 300, delta=3)
        self.assertAlmostEqual(trimmed.height, 200, delta=3)

        letterbox = Image.new("RGB", (300, 300), (0, 0, 0))
        letterbox.paste(photo, (0, 50))
        trimmed, did = trim_bars(letterbox)
        self.assertTrue(did)
        self.assertAlmostEqual(trimmed.height, 200, delta=3)

    def test_leaves_normal_photos_alone(self):
        photo = synthetic_photo()
        same, did = trim_bars(photo)
        self.assertFalse(did)
        self.assertEqual(same.size, photo.size)

    def test_skips_black_and_white_duplicates_and_tiny_images(self):
        color_a = synthetic_photo(10, (320, 240))
        color_b = synthetic_photo(11, (320, 240))
        bw = color_a.convert("L").convert("RGB")
        tiny = synthetic_photo(12, (100, 80))
        kept, notes = screen_references([color_a, color_b, bw, color_a.copy(), tiny])
        self.assertEqual(len(kept), 2)
        reasons = {n["index"]: n["reason"] for n in notes if n["action"] == "skipped"}
        self.assertEqual(reasons[2], "black-and-white")
        self.assertEqual(reasons[3], "duplicate")
        self.assertEqual(reasons[4], "too small")

    def test_mostly_bars_is_skipped(self):
        photo = synthetic_photo(size=(300, 40))
        tall = Image.new("RGB", (300, 300), (255, 255, 255))
        tall.paste(photo, (0, 130))
        kept, notes = screen_references([tall])
        self.assertEqual(kept, [])
        self.assertEqual(notes[0]["reason"], "mostly bars or blank space")


if __name__ == "__main__":
    unittest.main()
