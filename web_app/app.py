"""Flask front end for style_match: pick a photo, browse live low-res
previews of each photographer's look, then develop the full-size version.

Runs entirely in memory -- nothing is written to disk, so it's safe on
Render's ephemeral filesystem.
"""
import io
import json
import math
import os
import sys

from flask import Flask, abort, render_template, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from style_match.apply import apply_profile  # noqa: E402
from style_match.profile import LookProfile  # noqa: E402

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

GRADE_STRENGTH = 0.85

# Render's free instance has 512MB RAM total, and scikit-image's LAB color
# conversion allocates several full-resolution float64 arrays at once --
# a modest few-megapixel photo can exhaust that on its own. Downscale before
# processing; a web download doesn't need full camera resolution anyway.
MAX_DIMENSION = 1200

# Low-res renders for the live look browser. Small on purpose: eight of these
# run back to back on a very small CPU, and they only need to be recognizable.
PREVIEW_DIMENSION = 512

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB, plenty for a phone photo

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


def read_photo(max_dimension: int) -> Image.Image:
    """Validate and decode the uploaded photo, fix orientation, cap its size."""
    upload = request.files.get("photo")
    if upload is None or upload.filename == "":
        abort(400, "No photo uploaded.")

    try:
        image = Image.open(upload.stream)
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        abort(400, "That doesn't look like a valid image file.")

    image = ImageOps.exif_transpose(image)
    if max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return image


def read_style() -> str:
    style_key = request.form.get("style", "")
    if style_key not in STYLES:
        abort(400, "Unknown style.")
    return style_key


def jpeg_response(image: Image.Image, quality: int, **send_kwargs):
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    response = send_file(buf, mimetype="image/jpeg", **send_kwargs)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", styles=STYLE_CARDS, default_strength=int(GRADE_STRENGTH * 100))


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
    """Small, full-strength render. The page blends it with the original
    client-side, so the intensity slider needs no further requests."""
    style_key = read_style()
    image = read_photo(PREVIEW_DIMENSION)
    graded = apply_profile(image, load_profile(style_key), strength=1.0)
    return jpeg_response(graded, quality=85)


@app.route("/grade", methods=["POST"])
def grade():
    style_key = read_style()

    try:
        strength = float(request.form.get("strength", GRADE_STRENGTH))
    except ValueError:
        abort(400, "Invalid strength.")
    if not 0.0 <= strength <= 1.0:
        abort(400, "Strength must be between 0 and 1.")

    image = read_photo(MAX_DIMENSION)
    graded = apply_profile(image, load_profile(style_key), strength=strength)
    return jpeg_response(
        graded,
        quality=92,
        as_attachment=True,
        download_name=f"{style_key}_graded.jpg",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
