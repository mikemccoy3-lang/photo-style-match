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

A Flask front end so you can grade photos from your phone without touching
the CLI. The flow:

1. **Choose photos.** One photo, or a set of up to 12. The browser shrinks
   one to 512px and shows it in a sticky viewfinder (it stays pinned while
   you drag sliders). With a set, tap any thumbnail to preview looks on
   that photo.
2. **Browse looks.** The small copy goes to `POST /preview` once per look
   (selected look first). Each response is a stacked JPEG: tone-only on
   top, tone + color below. They show as a filmstrip of *your own photo* in
   every look. Tap one to put it in the viewfinder; press and hold the
   photo to see the original.
3. **Intensity and Color match sliders.** Both blend the previews with the
   original on a canvas in the browser, so they are instant and cost no
   server calls. *Color match* is the "keep my colors" control: 0% borrows
   only the photographer's tone and contrast, 100% also moves your color
   balance toward theirs. The default is 50%, because at 100% saturated
   paint rotated ~15-20 degrees in hue and lost 30-60% of its saturation
   (a blue car turned purple); at 50% the drift is ~5-9 degrees.
4. **Mix two looks.** Pick look A and look B and blend them with a slider.
   The page composites weighted layers; the server bakes the same weights
   into one table.
5. **Develop full-size.** Sends up to 4096px per photo to `POST /grade`
   (style, optional style2 + mix, strength, color), one at a time with
   progress and a Cancel button for sets, then shows the result with a Save
   button (share sheet on phones, download on desktop).
6. **My Look.** Build a personal look from 3-15 of your own color photos
   (`POST /build-look`). References are screened the same way I screen
   photographers' sets: white/black bars are trimmed, black-and-white frames,
   duplicates and tiny images are skipped, and the page reports what it did.
   The resulting profile (a few KB of numbers) is kept in the browser's
   localStorage and sent with later requests as `style=custom` plus a
   `profile` field, so nothing about you is stored on the server. It is
   validated strictly on arrival (`LookProfile.from_untrusted`).

The page also remembers your last look, intensity and color match.

Each look shows plain-English traits (Dark, Punchy, Matte, Cool...) computed
from the profile's own statistics by `describe_look()` in `web_app/app.py`,
so the labels stay tied to what was actually measured.

It reads whichever `profiles/<name>/profile.json` files exist -- add a new
photographer via the CLI workflow above and it shows up automatically once
you add it to the `STYLES` dict in `web_app/app.py`.

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

**How the engine stays inside 512MB:** `style_match/apply.py` is the exact
reference pipeline (used by the CLI), but it allocates several full-size
float64 arrays and crashed the free instance on a 2.8MP photo. The web app
uses `style_match/transform.py` instead. A look is a fixed function of each
pixel's color once the photo's own statistics are known, so it measures
those on a 512px copy, evaluates the look on a 65x65x65 grid of colors,
bakes intensity / color match / mixing into that grid, and lets Pillow apply
the grid to the full-size photo (`ImageFilter.Color3DLUT`, trilinear). Against
the exact pipeline the difference averages ~1.3 levels out of 255. A 12.6MP
photo adds ~100MB at peak and takes ~0.4s locally, so `MAX_DIMENSION` is
4096. Tests in `tests/` check that match, that preview blending equals the
final render, that mixing is linear, and the profile validation and
reference screening (`python3 -m unittest discover -s tests`).

**Staying awake:** Render's free instance sleeps after ~15 idle minutes.
`.github/workflows/keepalive.yml` pings `/healthz` every 10 minutes so the
first load is instant. One always-on free service uses ~744 of the 750 free
monthly hours. GitHub pauses scheduled workflows after 60 days with no repo
activity (any push wakes it). Delete the file to turn it off. `render.yaml`
also sets `--timeout 120` because the free CPU is slow.
