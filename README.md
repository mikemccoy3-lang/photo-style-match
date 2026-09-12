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
