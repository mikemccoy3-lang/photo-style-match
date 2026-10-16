"""Flask front end for style_match: pick photos, browse live low-res previews
of each photographer's look (or your own), then develop at full size.

Runs entirely in memory -- nothing is written to disk, so it's safe on
Render's ephemeral filesystem.

Grading goes through `style_match.transform`: statistics are measured on a
small copy of the photo, every setting is baked into a 3D color lookup
table, and Pillow applies that table to the full-size photo. That keeps a
12-megapixel photo comfortably inside the free instance's 512MB of RAM.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
from dataclasses import asdict

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from style_match import transform  # noqa: E402
from style_match.profile import LookProfile, build_profile_from_images  # noqa: E402
from style_match.screen import screen_references  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(BASE_DIR, "..", "profiles")

# Whitelist of valid styles -- never build the profile path from raw user input.
STYLES = {
    "larry-chen": "Larry Chen",
    "peter-mckinnon": "Peter McKinnon",
    "krispy-media": "Krispy Media",
    "amy-shore": "Amy Shore",
    "camden-thrasher": "Camden Thrasher",
    "dino-dalle-carbonare": "Dino Dalle Carbonare",
    "easton-chang": "Easton Chang",
    "jamey-price": "Jamey Price",
}
CUSTOM = "custom"  # the user's own look, sent from their browser as a profile

DEFAULT_STRENGTH = 0.85
# How much of a photographer's color balance to borrow. Full color matching
# rotates paint hues by ~15-20 degrees and mutes them (a blue car turns
# purple); half keeps paint within ~5-9 degrees while still picking up the look.
DEFAULT_COLOR = 0.5

# Largest photo we will develop. 12.6 megapixels adds only ~100MB of memory
# through the lookup-table path, so it fits the free instance.
MAX_DIMENSION = 4096

# Low-res renders for the live look browser. Small on purpose: eight of these
# run back to back on a very small CPU, and they only need to be recognizable.
PREVIEW_DIMENSION = 512

MAX_PROFILE_JSON_BYTES = 20_000
MAX_REFERENCE_PHOTOS = 15
MIN_REFERENCE_PHOTOS = 3

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 24 * 1024 * 1024  # a 12MP JPEG is a few MB

_profile_cache: dict[str, LookProfile] = {}


def load_profile(style_key: str) -> LookProfile:
    if style_key not in _profile_cache:
        path = os.path.join(PROFILES_DIR, style_key, "profile.json")
        _profile_cache[style_key] = LookProfile.load(path)
    return _profile_cache[style_key]


def describe_look(profile: LookProfile) -> list[str]:
    """Plain-English traits measured from a profile's statistics.

    Derived from the reference photos' numbers (average lightness, contrast
    spread, color cast, saturation spread, black point) so the labels stay
    honest -- nothing here is a claim about the photographer beyond what the
    reference set measured.
    """
    mean_l, mean_a, mean_b = profile.lab_mean
    std_l, std_a, std_b = profile.lab_std
    chroma_spread = math.hypot(std_a, std_b)
    tags = []

    if mean_l < 32:
        tags.append("Dark")
    elif mean_l < 38:
        tags.append("Moody")
    elif mean_l < 43:
        tags.append("Balanced")
    else:
        tags.append("Bright")

    if std_l >= 25.5:
        tags.append("Punchy")
    elif std_l < 22:
        tags.append("Soft")

    if profile.black_point > 8:
        tags.append("Matte")

    if mean_b < 0:
        tags.append("Cool")
    elif mean_b > 6:
        tags.append("Warm")
    elif abs(mean_a) < 1.5 and mean_b < 4.5:
        tags.append("Neutral")

    if chroma_spread > 17:
        tags.append("Vivid")
    elif chroma_spread < 12.5:
        tags.append("Muted")

    return tags[:4]


STYLE_CARDS = [
    {"slug": slug, "name": name, "tags": describe_look(load_profile(slug))}
    for slug, name in STYLES.items()
]

# Build the lookup lattice once at startup instead of on the first request.
transform._grid(transform.LUT_SIZE)


# ---------- request helpers ----------

def decode_photo(upload, max_dimension: int) -> Image.Image:
    try:
        image = Image.open(upload.stream)
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        abort(400, "That doesn't look like a valid image file.")

    image = ImageOps.exif_transpose(image)
    if max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return image


def read_photo(max_dimension: int) -> Image.Image:
    upload = request.files.get("photo")
    if upload is None or upload.filename == "":
        abort(400, "No photo uploaded.")
    return decode_photo(upload, max_dimension)


def read_unit_float(field: str, default: float) -> float:
    raw = request.form.get(field)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        abort(400, f"Invalid {field}.")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        abort(400, f"{field} must be between 0 and 1.")
    return value


def resolve_look(style_field: str, optional: bool = False) -> LookProfile | None:
    style_key = request.form.get(style_field, "")
    if optional and style_key == "":
        return None
    if style_key == CUSTOM:
        raw = request.form.get("profile", "")
        if not raw or len(raw) > MAX_PROFILE_JSON_BYTES:
            abort(400, "Missing or oversized custom profile.")
        try:
            return LookProfile.from_untrusted(json.loads(raw))
        except (ValueError, TypeError):
            abort(400, "Invalid custom profile.")
    if style_key not in STYLES:
        abort(400, "Unknown style.")
    return load_profile(style_key)


def jpeg_response(image: Image.Image, quality: int, **send_kwargs):
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    response = send_file(buf, mimetype="image/jpeg", **send_kwargs)
    response.headers["Cache-Control"] = "no-store"
    return response


# ---------- routes ----------

@app.route("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        styles=STYLE_CARDS,
        default_strength=int(DEFAULT_STRENGTH * 100),
        default_color=int(DEFAULT_COLOR * 100),
        max_reference=MAX_REFERENCE_PHOTOS,
        min_reference=MIN_REFERENCE_PHOTOS,
    )


@app.route("/healthz", methods=["GET"])
def healthz():
    return "ok", 200, {"Cache-Control": "no-store"}


@app.route("/manifest.webmanifest", methods=["GET"])
def manifest():
    """Makes the site installable to a phone home screen as a standalone app."""
    data = {
        "name": "Photo Style Match",
        "short_name": "Style Match",
        "description": "Grade a photo to match a photographer's look.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0c0d0f",
        "theme_color": "#0c0d0f",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return app.response_class(json.dumps(data), mimetype="application/manifest+json")


@app.route("/preview", methods=["POST"])
def preview():
    """Small, full-strength renders of one look, stacked in one JPEG.

    Top half: tone only (the photo's own colors). Bottom half: tone plus the
    photographer's color. The page blends these with the original for its
    intensity and color sliders, so no slider move needs another request.
    """
    profile = resolve_look("style")
    image = read_photo(PREVIEW_DIMENSION)
    tone, full = transform.preview_pair(image, profile)
    stacked = Image.new("RGB", (image.width, image.height * 2))
    stacked.paste(tone, (0, 0))
    stacked.paste(full, (0, image.height))
    return jpeg_response(stacked, quality=88)


@app.route("/grade", methods=["POST"])
def grade():
    primary = resolve_look("style")
    secondary = resolve_look("style2", optional=True)
    strength = read_unit_float("strength", DEFAULT_STRENGTH)
    color_amount = read_unit_float("color", DEFAULT_COLOR)
    mix = read_unit_float("mix", 0.5)

    if secondary is None:
        looks = [(primary, 1.0)]
    else:
        looks = [(primary, 1.0 - mix), (secondary, mix)]

    image = read_photo(MAX_DIMENSION)
    graded = transform.develop(image, looks, strength, color_amount)
    return jpeg_response(
        graded,
        quality=92,
        as_attachment=True,
        download_name="style-match.jpg",
    )


@app.route("/build-look", methods=["POST"])
def build_look():
    """Build a personal look from the user's own best photos.

    Nothing is stored server-side: the profile (a few KB of numbers) goes back
    to the browser, which keeps it and sends it along with later requests.
    """
    uploads = [f for f in request.files.getlist("photos") if f.filename]
    if len(uploads) < MIN_REFERENCE_PHOTOS:
        abort(400, f"Add at least {MIN_REFERENCE_PHOTOS} photos.")
    if len(uploads) > MAX_REFERENCE_PHOTOS:
        abort(400, f"Use at most {MAX_REFERENCE_PHOTOS} photos.")

    images = [decode_photo(f, PREVIEW_DIMENSION) for f in uploads]
    usable, notes = screen_references(images)
    if len(usable) < MIN_REFERENCE_PHOTOS:
        return (
            jsonify(
                error=f"Only {len(usable)} of your {len(images)} photos could be used "
                f"(need {MIN_REFERENCE_PHOTOS}). Add more color photos that aren't duplicates.",
                notes=notes,
            ),
            422,
        )

    profile = build_profile_from_images(usable)
    payload = asdict(profile)
    payload.pop("source_images", None)
    return jsonify(profile=payload, tags=describe_look(profile), used=len(usable), total=len(images), notes=notes)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
