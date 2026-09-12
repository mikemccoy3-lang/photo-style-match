"""Flask front end for style_match: upload a photo, pick a photographer
style, get the graded image back as a download.

Runs entirely in memory -- nothing is written to disk, so it's safe on
Render's ephemeral filesystem.
"""
import io
import os
import sys

from flask import Flask, abort, render_template, request, send_file
from PIL import Image, UnidentifiedImageError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from style_match.apply import apply_profile  # noqa: E402
from style_match.profile import LookProfile  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(BASE_DIR, "..", "profiles")

# Whitelist of valid styles -- never build the profile path from raw user input.
STYLES = {
    "larry-chen": "Larry Chen",
    "peter-mckinnon": "Peter McKinnon",
}

GRADE_STRENGTH = 0.85

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB, plenty for a phone photo

_profile_cache: dict[str, LookProfile] = {}


def load_profile(style_key: str) -> LookProfile:
    if style_key not in _profile_cache:
        path = os.path.join(PROFILES_DIR, style_key, "profile.json")
        _profile_cache[style_key] = LookProfile.load(path)
    return _profile_cache[style_key]


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", styles=STYLES)


@app.route("/grade", methods=["POST"])
def grade():
    style_key = request.form.get("style", "")
    if style_key not in STYLES:
        abort(400, "Unknown style.")

    upload = request.files.get("photo")
    if upload is None or upload.filename == "":
        abort(400, "No photo uploaded.")

    try:
        image = Image.open(upload.stream)
        image.load()
    except UnidentifiedImageError:
        abort(400, "That doesn't look like a valid image file.")

    profile = load_profile(style_key)
    graded = apply_profile(image, profile, strength=GRADE_STRENGTH)

    buf = io.BytesIO()
    graded.convert("RGB").save(buf, format="JPEG", quality=92)
    buf.seek(0)

    return send_file(
        buf,
        mimetype="image/jpeg",
        as_attachment=True,
        download_name=f"{style_key}_graded.jpg",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
