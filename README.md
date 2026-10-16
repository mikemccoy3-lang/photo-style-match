# photo-style-match

Match your own photos to a reference photographer's color/tone style (e.g.
Larry Chen's automotive work). Drop in a handful of reference images, build a
"look profile," then apply it to any new photo.

## How it works

1. **`build-profile`** reads your reference images, converts them to LAB
   color space, and averages their color statistics (mean/std of a/b) and
   luminance tone curve (a histogram of the L channel) into a single JSON
   profile.
2. **`apply`** grades a new photo against that profile. It's
   content-adaptive: color correction is scaled relative to *your photo's
   own* color statistics, and contrast/tone is matched via histogram
   matching to the reference's tone curve -- not a fixed transform, so it
   reacts differently to a bright midday shot vs. a moody dusk shot the way
   a single static LUT can't.
3. **`export-lut`** optionally bakes an approximation of the profile into a
   portable `.cube` LUT for Lightroom/Premiere/DaVinci. It's a good fast
   starting point, but less accurate than running `apply` per photo, since a
   LUT has no way to know a photo's own exposure/contrast going in.

## Setup

```bash
pip install -r requirements.txt
```

## Folder structure

Each photographer/style gets its own folder under `profiles/`:

```
profiles/
  larry-chen/
    references/   # their photos -- used to build the look profile
    profile.json  # built from references/
    inbox/        # drop your own photos here to be graded
    graded/       # output lands here
  peter-mckinnon/
    references/
    profile.json
    inbox/
    graded/
```

## Usage

**Add a new photographer:** create `profiles/<name>/references/`, drop in
5-10 of their photos (varied lighting -- daylight, dusk, studio), then build
the profile:

```bash
python -m style_match.cli build-profile profiles/<name>/references/ --out profiles/<name>/profile.json
```

**Grade your own photos (the everyday workflow):** drag photos into
`profiles/<name>/inbox/`, then run:

```bash
python -m style_match.cli process profiles/<name>
```

Every image in `inbox/` gets graded and saved to `graded/` as
`<name>_graded.<ext>`. Re-run any time -- it just re-processes whatever is
sitting in `inbox/`.

**One-off apply** (skip the inbox/graded convention, name paths yourself):

```bash
python -m style_match.cli apply --profile profiles/<name>/profile.json \
  --input path/to/photo.jpg --output output/photo_graded.jpg --strength 0.85
```

**Optional: bake a `.cube` LUT** for Lightroom/Premiere:

```bash
python -m style_match.cli export-lut --profile profiles/<name>/profile.json --out <name>.cube
```

Run all commands from the project root (`photo-style-match/`) so the
`style_match` package is importable.

`--strength` (0-1, default 0.85, applies to both `process` and `apply`)
blends the graded result back toward the original -- lower it if the
correction feels too aggressive on a given photo.

## Notes

- More/varied reference images (different lighting conditions) make a more
  robust profile. Start with 5-10 that represent the range of shots you want
  to emulate.
- This matches *color and tonal* style (contrast, white balance, saturation,
  shadow/highlight color cast). It does not replicate composition, lighting
  setup, or lens choices -- those are shot on location, not in post.

## Web app (`web_app/`)

A Flask front end so you can grade a photo from your phone without touching
the CLI. The flow:

1. **Choose a photo.** The browser shrinks it to 512px and shows it in a
   viewfinder.
2. **Browse looks.** The small copy is sent to `POST /preview` once per
   photographer (one at a time, selected look first). Each returns a
   full-strength graded preview, shown as a filmstrip of *your own photo*
   in every style. Tap a thumbnail to put that look in the viewfinder.
   Press and hold the photo to see the original.
3. **Intensity slider.** Blends the preview with the original on a canvas
   in the browser, so it's instant and costs no server calls. The same
   value is sent as `strength` when you develop.
4. **Develop full-size.** Sends a 1200px copy to `POST /grade` with the
   chosen style and strength, then shows the result with a Save button
   (share sheet on phones, download on desktop).

Each look also shows plain-English traits (Dark, Punchy, Matte, Cool...)
computed from the profile's own statistics by `describe_look()` in
`web_app/app.py`, so the labels stay tied to what was actually measured.

It reuses the same `style_match` engine and reads whichever
`profiles/<name>/profile.json` files exist -- add a new photographer via
the CLI workflow above and it shows up automatically once you add it to the
`STYLES` dict in `web_app/app.py`.

Everything runs in memory (no disk writes), so it's safe to deploy on a
host with an ephemeral filesystem like Render's free tier.

**Run it locally:**

```bash
pip install -r web_app/requirements.txt
cd web_app && python3 app.py
```

Visit `http://127.0.0.1:5000` (or whatever `$PORT` you set).

**Deploy to Render (free tier):**

1. Push this repo to GitHub.
2. On [dashboard.render.com](https://dashboard.render.com): **New +** →
   **Web Service** → connect the repo. Render auto-detects `render.yaml`
   at the project root; if it doesn't, set manually:
   - Build Command: `pip install -r web_app/requirements.txt`
   - Start Command: `gunicorn --chdir web_app --bind 0.0.0.0:$PORT app:app`
   - Instance Type: Free
3. Deploy. Open the resulting `*.onrender.com` URL from your phone (Add to
   Home Screen for an app-like icon).

Free-tier services spin down after 15 minutes idle and take ~30-50s to
wake on the next request -- fine for occasional use, just not instant on a
cold start.

**Adding a photographer:** create `profiles/<slug>/references/`, drop in
8-15 of their color photos (varied lighting; skip black-and-white frames,
white-bordered or letterboxed frames, stitched carousel panels, and
near-black abstracts -- they skew the color/tone statistics), run
`build-profile`, add the slug and display name to the `STYLES` dict in
`web_app/app.py`, and commit `profile.json`. Currently eight styles: Larry
Chen, Peter McKinnon, Krispy Media, Amy Shore, Camden Thrasher, Dino Dalle
Carbonare, Easton Chang, Jamey Price.

**Install on your phone:** the site ships a web manifest and app icons
(`web_app/static/`), so "Add to Home Screen" gives a standalone app icon with
no browser bar. iPhone: Safari → Share → Add to Home Screen. Android: Chrome
→ ⋮ → Add to Home screen / Install app. Results use a **Save photo** button
(share sheet on phones, plain download on desktop) because file downloads
are unreliable inside an installed iOS home-screen app. Icons are generated
by a one-off Pillow script; regenerate them if you want a new look.

**Memory note:** Render's free instance has 512MB RAM total. scikit-image's
LAB color conversion allocates several full-resolution float64 arrays at
once, which on top of the Flask/gunicorn/numpy/scikit-image baseline
footprint can exceed that even for a modest few-megapixel photo. The web
app downscales every upload to 1200px on the long edge before grading
(`MAX_DIMENSION` in `web_app/app.py`) to stay well under the limit -- raise
it only if you move to a paid instance with more RAM.
