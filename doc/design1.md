# Design 1: First Field Release

See [problem.md](problem.md) and [research.md](research.md).

## Goal

Build, in one evening, a release that can be installed at the site the next
day and answer the first practical questions:

1. Does the software install and run there?
2. Can the cameras we have be calibrated well enough?
3. What target (marker) size and printing/mounting method is detected
   reliably at working distances, in site lighting, hand-held?
4. With targets along the shelf front, is the plane geometry accurate enough
   for ±0.1 in cross-sections?
5. How well does a first automatic detector find end faces, and how does it
   fail?

It does **not** measure length, keep an inventory, or compare inventories.
Rear photos are collected from day one, though, so later releases can be
tested on them.

The release is a **local web app**: run it on a desktop computer, open it in
a browser, upload photos into photo sets, run analyses, and view the reports.

## Development loop

```
home: code + synthetic tests ──► GitHub (public, AGPL) ──► site: install, run field tests
  ▲                                                                  │
  └──────────── person reads back one-line numeric summaries ◄───────┘
```

- Site photos, hand measurements, configuration and reports **never leave
  the site** and are **never committed**. They live in a data directory
  outside the repository. A `.gitignore` excludes image files and the
  default data directory name as a safeguard.
- Each analysis produces one summary line, e.g.
  `SS 0.1.0 T5M n=12 w_med=0.02 w_max=0.06 h_med=0.03 h_max=0.07`.
  It is shown at the top of the report and collected on the dashboard for
  easy reading back. Only these lines (plus verbal descriptions of
  failures) come back.
- Site data accumulates in the data directory. Every new release can re-run
  all earlier photo sets ("Re-run all"), so the site data becomes a
  regression suite without ever leaving the site.
- Releases are tagged (`v0.1.0`, `v0.1.1`, …); the version is in every
  summary line and in the page footer.

## Installation and running

### Platforms

Linux, macOS and Windows, x86-64 and ARM64 (Apple Silicon). Every
dependency must ship prebuilt wheels for all of these, so nothing is ever
compiled at install time.

### Python

- Supported: CPython 3.11, 3.12, 3.13. Reference version: 3.12 (the one the
  lock file is generated and tested with first).
- The app always runs in its own virtual environment (`venv`, part of the
  standard library), never in the system Python.

### Dependencies, pinned

All open source and AGPL-compatible:

| Package | Use | License |
|---|---|---|
| `numpy` | arrays | BSD-3 |
| `opencv-python-headless` | computer vision (no GUI libraries needed) | Apache-2.0 |
| `Pillow` | image I/O, PDF output, thumbnails | MIT-CMU (HPND) |
| `pillow-heif` | iPhone HEIC photos | BSD-3 (bundles LGPL libheif) |
| `PyYAML` | config and result files | MIT |
| `Flask` (+ its deps Jinja2, Werkzeug, …) | web app | BSD-3 |
| `waitress` | web server, pure Python | ZPL-2.1 |
| `pytest` (dev only) | tests | MIT |

Pinning works in two layers:

1. `pyproject.toml` declares the package and **compatible ranges** (e.g.
   `Flask>=3.0,<4`), so the code states what it needs.
2. `requirements.lock` pins **every package, including transitive
   dependencies, to an exact version with SHA-256 hashes** for all
   platforms. It is generated with `pip-compile --generate-hashes` (from
   pip-tools) and committed. `requirements-dev.lock` adds the test tools.
   Installs use `pip install --require-hashes -r requirements.lock`, so the
   site gets exactly the tested versions or the install fails loudly.

Updating dependencies is a deliberate step: regenerate the lock files, run
the tests on all platforms, tag a release.

### Install and use

No install or run scripts. `README.md` documents the steps by hand, for
Linux/macOS and Windows shells, once the app is built. In outline:

1. Create a venv in the repository folder with a supported Python
   (`python3.12 -m venv .venv`, or `py -3.12 -m venv .venv` on Windows) and
   activate it.
2. `pip install --require-hashes -r requirements.lock`
3. `pip install --no-deps .`
4. `stockscan selftest`
5. `stockscan serve --data DIR`, then open `http://localhost:3000`.

The data directory should be outside the repository folder. Upgrading to a
new release: download it, then repeat steps 2–4 in the same venv (or a fresh
one).

### Offline install

If the site computer cannot reach PyPI, each GitHub release can include a
wheel bundle per platform (`wheels-<os>-<arch>-py312.zip`), downloaded at
home with `pip download --only-binary=:all: --platform … --python-version
3.12 -r requirements.lock`, and installed with `pip install --no-index
--find-links <unzipped folder> --require-hashes -r requirements.lock`.
Built only for the platforms actually needed.

### Portability rules for the code

- Paths only through `pathlib`; no hard-coded separators; no shell calls.
- File names the app creates use only `[A-Za-z0-9._-]` (no `:` in
  timestamps, which Windows forbids); photo-name comparisons are
  case-insensitive (Windows and macOS file systems are).
- Files are opened with explicit encoding (UTF-8) and binary mode for
  images.
- No platform-specific dependencies; no compiled code of our own.
- On Windows, the first network-reachable start triggers the firewall
  prompt; the README says to allow it on private networks.

### Continuous integration

GitHub Actions (free for public repositories) runs the test suite and the
self-test on Linux, macOS and Windows for each supported Python version on
every push, installing from the lock file with the same commands the
README gives. A release is only tagged when all of them pass. This is how
Windows and Linux get tested without having those machines at home.

### Network exposure

- By default the server listens on `0.0.0.0` (all network interfaces), so
  other devices on the local network can reach it, e.g. a phone at
  `http://<desktop-ip>:3000/`. Plain HTTP, no certificates, no DNS needed.
  Whether the site network allows this is something to try, not something
  to depend on. On startup the server prints the addresses it can be
  reached at.
- `--host 127.0.0.1` restricts it to this computer only.
- On the network, anyone who can reach it can upload and delete.
  `--password` enables a single shared password (HTTP basic auth). The
  server prints a warning when it is reachable from the network without
  one.

## Web app

### Pages

| Page | Content |
|---|---|
| **Dashboard** | Photo sets (newest first), recent analyses with their summary lines, a copy-all box of today's summary lines, version, data directory, warnings (e.g. no camera calibration yet) |
| **New set** | Name, type (calibration, size test, plane check, front, back, other), optional notes |
| **Set** | Upload area, photo thumbnails with per-photo metadata (camera, lens, size, sharpness), delete photo, hand-measurement table, notes, buttons for the analyses that apply to the set type, list of past reports for this set |
| **Report** | The HTML report of one analysis run: summary line first, then per-photo and per-piece details with annotated images |
| **Measure** | Manual measurement: click face corners on the straight-on image (see below) |
| **Targets** | Form to generate printable PDFs (item, page size, square size, number of segments, first serial); download links to previous ones |
| **Cameras** | Camera calibrations: camera/lens/image size, error, which set produced it, date |
| **Settings** | Print batches (measured scale bars), segment labels, default units; stored in `site.yaml` |
| **Self-test** | Runs the synthetic test suite, shows the result |

Plain server-rendered HTML (Jinja2 templates, part of Flask) with a little
vanilla JavaScript for uploads, job status and the measuring tool. No
JavaScript framework, no build step, no external resources (works with no
internet at the site).

### Uploading photos

- A drop zone plus a file picker with `multiple` and `accept="image/*"`. On
  a phone this offers the photo library, the camera, or files.
- Files are uploaded **one per request**, a few in parallel, with per-file
  progress and automatic retry, so one failed file does not lose the batch.
- Each file is stored under its original name; a content hash rejects
  duplicates (re-uploading the same photos is harmless).
- **Folder drop-in also works:** photos copied directly into a set's folder
  (e.g. from an SD card) are picked up by a "Rescan folder" button.
- Upload limit per file: 100 MB (configurable).
- Phones may convert or resize photos when uploading through a browser and
  may drop some metadata. The set page shows each photo's pixel size and
  EXIF camera/lens so this can be checked (field test T8).

### Running analyses

- Analyses run in a background worker thread, one at a time, so the page
  stays responsive. The set page shows queued/running/done/failed and links
  to the report when done.
- Each run writes a report folder:
  `reports/<set>/<analysis>-<timestamp>/index.html` plus images and a
  machine-readable `result.yaml`. Old reports are kept.
- Errors are shown on the page with the traceback (useful verbal feedback).

### Manual measurement in the browser

The straight-on image of the face plane is shown on a canvas. The operator
clicks each face's top-left and bottom-right corners, left to right. Each
first click opens a zoomed view (loupe) around the point for a precise
second click. Mouse wheel / pinch zooms and pans. Clicks are saved to the
set, so measuring can be resumed, and the result becomes a report like any
other analysis. Works from a phone or tablet too.

### Hand measurements

Entered in a table on the set page (index, width, height, length, note) or
uploaded as `truth.csv`. Stored as `truth.csv` in the set folder:

```
index,width_in,height_in,length_in,note
1,1.002,2.004,,
2,0.252,9.98,,plate
```

`index` is the left-to-right order in the photographed area; length may be
empty.

### Command line

The web app is the main interface. A small CLI remains for things that must
work without a browser:

| Command | Purpose |
|---|---|
| `stockscan serve [--data DIR] [--host H] [--port P] [--password PW]` | Run the web app |
| `stockscan selftest` | Synthetic tests; check the install before starting the server |
| `stockscan rerun [--data DIR]` | Re-run all sets; print all summary lines |

## Data directory (at the site only)

```
DATA/
  site.yaml                     # print batches, segment labels, settings
  cameras/                      # camera calibrations (YAML)
  targets/                      # generated PDFs + segment geometry YAML
  sets/
    2026-10-20_calib_phone1/
      set.yaml                  # type, notes, created, camera hint
      photos/                   # uploaded or copied photos
      truth.csv                 # hand measurements (optional)
      clicks.yaml               # manual measurement clicks (optional)
  reports/
    2026-10-20_calib_phone1/calibrate-20261020-0912/index.html
  cache/                        # thumbnails, straight-on images; safe to delete
```

Everything is plain files, so it can be browsed, backed up or copied by hand.
A database comes later with the inventory itself.

## Targets

### Dictionary and IDs

- OpenCV predefined dictionary `DICT_5X5_1000` (1000 IDs). 5×5 bits keeps
  the cells larger than 6×6 for a given printed size.
- IDs 0–99 are reserved for the calibration board and the size-test sheet.
- From ID 100 up, IDs are allocated in blocks to **strip segments**. Each
  segment has a serial number printed on it in human-readable form.
- The block size depends on the segment's layout (a 3 × 21 strip uses 31
  IDs), so the ID budget depends on page size: about 29 long strips, or
  about 45 short ones. That is enough for this phase; a larger scheme is a
  later design decision (see research.md §5).
- The app records which serials and IDs have been generated, so it never
  issues the same IDs twice.

### Printed items (Targets page)

All are generated as PDFs at exact physical size. **Page size is an
input:** letter, tabloid, A4, A3, or custom `WxH` in inches (e.g. `11x17`),
plus margin (default 0.5 in). Layouts are computed from the page size:
boards get as many squares as fit, and strips get as long as the page's long
side allows and are stacked several per page. Every page has a **scale bar**
with tick marks a known distance apart (the longest round length that fits)
and the text "PRINT AT 100% / ACTUAL SIZE".

| Item | Content | Purpose |
|---|---|---|
| Calibration board | ChArUco, as many squares of the chosen size (default 1.2 in) as fit the page | Camera calibration; geometry reference card |
| Size-test sheet | ChArUco patches with squares of 3/8, 1/2, 3/4, 1 in | Find the smallest reliable square size |
| Strip segments | ChArUco, 3 squares tall × as many as fit the page's long side; square size 1/2 in or 3/4 in | Mounted along shelf edges |

Example: on 11 × 17 in paper a strip of 3/4 in squares is 21 squares
(15.75 in) long, and four strips fit on a page.

Strips are 3 squares tall so they have 2 rows of ChArUco corners; a single
row would leave the plane's tilt poorly constrained.

Each generated segment's geometry (square size, rows, columns, first ID) is
printed on the page as text and saved as YAML in `targets/`, so the software
never has to guess how a segment was printed.

### Printing and mounting rules

1. Print at 100 % ("actual size", no "fit to page"), matte paper.
2. Measure each page's scale bar with a steel rule or calipers and enter it
   on the Settings page (per print batch). The software corrects the scale.
3. Mount **flat and rigid**: spray adhesive onto a flat batten, board or
   aluminum flat bar. Paper on a curved or bumpy surface breaks the
   geometry.
4. The calibration board must be flat (glue it to a stiff board).
5. Covers: test both with and without a clear cover (glare risk).

## Camera settings

- **Phone:** save JPEG (iPhone: Settings → Camera → Formats → Most
  Compatible), main 1× lens only, no zoom, automatic macro switching off
  (iPhone: Settings → Camera → Macro Control, then keep macro off in the
  camera app), no portrait mode. Flash off unless a test says otherwise.
- **Dedicated camera:** JPEG, zoom at one fixed setting (e.g. widest), no
  digital zoom.
- The software reads camera make, model, lens and image size from the photo
  metadata (EXIF), picks the matching calibration, and warns when a photo
  does not match (e.g. the phone switched lenses, or the upload was resized).

## Analyses

| Analysis | Set type | Priority tonight |
|---|---|---|
| Inspect (quality + target detection) | any | must |
| Calibrate camera | calibration | must |
| Size test | size test | must |
| Plane check | plane check | must |
| Manual cross-section | front | must |
| Automatic cross-section | front | stretch |

### Image loading

Read JPEG (and HEIC if `pillow-heif` is installed), apply EXIF orientation,
read EXIF camera/lens/focal length/exposure. Internal length unit is the
inch.

### Inspect

Per photo: camera/lens/size, exposure, sharpness (variance of the
Laplacian), clipped highlights, markers and segments found, ChArUco corners
per segment, distance and angle to each segment (when calibrated). Annotated
thumbnails.

### Camera calibration

Detect ChArUco corners on each photo; drop photos with fewer than 12
corners; run OpenCV calibration (pinhole + 5-term distortion). Report RMS
reprojection error, per-photo error, and a coverage map showing whether
corners were seen across the whole frame (edges and corners of the image
matter for distortion). Calibration saved as YAML keyed by
make/model/lens/image size.

### Target detection and pose

Detect markers with OpenCV's ArUco detector (sub-pixel corner refinement),
group them into segments by ID block, interpolate ChArUco corners per
segment, and solve the camera pose per segment (`solvePnP`) using the
calibration and the segment's print-scale correction. Report corners
found, reprojection error and distance to each segment.

v0 has no marker map: each segment is used on its own. When one photo sees
several segments that are supposed to be in one plane, the report shows how
well their planes agree. This is a free early check on mounting quality.

### Rectification (removing perspective)

The end faces are assumed to lie in the plane of the front strip, shifted by
an optional configured offset. The region above the strip is resampled into
a straight-on image at a fixed scale (default 100 px/in, i.e. 0.01 in/px).
Measurements in this image are plain pixel distances divided by 100.

### Size test

Detection rate for each square size of the size-test sheet, per photo, with
the photo's distance and angle to the sheet. Summary: smallest size detected
in ≥ 95 % of photos, per distance band.

### Plane check

The operator holds or props the calibration board against the front ends of
the stock (in the face plane), with a strip segment in view. The software
finds the camera pose **from the strip only**, rectifies, then detects the
board's corners in the straight-on image and compares their spacing with the
known spacing. The result is the geometry error by itself, with no face
detection involved. Expected: < 0.5 % scale error, i.e. < 0.015 in on a
3 in face.

### Automatic face detection (stretch)

Uses shape only, never color:

1. Find the shelf line (bottom of the faces) just above the strip.
2. Find vertical boundaries (sides of faces, seams) from the horizontal
   gradient in a band just above the shelf line, where every face is
   present.
3. Between neighboring boundaries, find the top edge from the vertical
   gradient, column by column; take the median; require consistency across
   the columns.
4. Accept a face when the top edge is consistent and the size is at least
   the minimum trackable size; reject the rest.
5. Annotate "near standard size" (nearest 1/8 in) as information only.

The report overlays every detected face, boundary and rejected candidate so
the operator can describe failures.

### Comparison with hand measurements

Detected or clicked faces are matched to `truth.csv` rows by an
order-preserving alignment (so a missed or extra face does not shift every
match after it). The report gives per-piece errors; the summary line gives
counts, medians and maxima.

## Synthetic tests (home and site)

The self-test renders synthetic scenes with known ground truth:

- A strip segment and rectangles of known size in one plane, viewed by a
  virtual camera from random positions, with blur and noise.
- Calibration board views for a known virtual camera.

It checks that calibration recovers the camera, pose and rectification
recover sizes within 0.02 in, the automatic detector finds the synthetic
faces, and the web app starts and accepts an upload (Flask test client).
These run in seconds and prove the install works.

## Field tests for the first day

Run in this order; stop where time runs out. For each, read back the
summary line.

| Test | Procedure | Read back | Pass |
|---|---|---|---|
| **T0** Install + self-test | Install per README, `stockscan selftest`, then `stockscan serve` and open the dashboard | `SS … T0 pass=a/b` | all pass, dashboard opens |
| **T1** Print scale | Generate and print all items; measure each scale bar; enter on Settings | the measured lengths | within 1 % of nominal (the error is corrected anyway) |
| **T2** Calibrate | Calibration set: 15–25 photos of the flat calibration board, varied angles, board filling different parts of the frame, at about working distance | `SS … T2 cam=… imgs=… rms=… fx=…` | rms < 1.0 px |
| **T3** Target size | Tape the size-test sheet where the front ends are. Photograph straight-on and at about 45°, from 2, 4, 6 and 8 ft. Repeat with a clear cover over it if covers are planned. | detection rate per size and distance | choose the smallest size that is ≥ 95 % at working distance |
| **T4** Geometry | Mount one strip segment along a shelf front. Plane-check set with the calibration board against the stock ends; 5–10 photos from different positions | `SS … T4 n=… scale_med=…% scale_max=…%` | < 0.5 % |
| **T5M** Manual cross-section | Front set: choose a section with 10–20 pieces including the smallest and some plate. Hand-measure width and height left to right into the table. Photograph from 2–3 positions. Measure manually. | `SS … T5M n=… w_med w_max h_med h_max` | max ≤ 0.05 in |
| **T5A** Automatic cross-section | Same set, automatic analysis | `SS … T5A found=…/… w_med w_max h_med h_max` | informative; target ≥ 80 % found, ≤ 0.1 in |
| **T6** Back photos | Mount a strip segment along the back edge of the same shelf; back set from the back; Inspect | `SS … T6 photos=… seg_found=… blur=…` | collected for later phases |
| **T7** Re-run | "Re-run all" | all summary lines | no crashes |
| **T8** Phone upload | Only if a phone can reach the desktop: upload a few photos from the phone's browser, compare pixel size and EXIF with the same photos copied by cable | `SS … T8 same_size=… exif_lens=…` | full resolution, lens recorded |

Useful verbal feedback beyond the numbers: what the failures look like in
the report (e.g. "seams between equal-height bars missed", "glare on
cover"), and anything awkward about taking or uploading the photos.

## Repository layout

```
LICENSE                 AGPL-3.0
pyproject.toml          package metadata, compatible dependency ranges
requirements.lock       exact pins + hashes (pip-compile output)
requirements-dev.lock   + test tools
README.md               includes install and usage (written once built)
.gitignore
.github/workflows/ci.yml   tests on Linux, macOS, Windows × Python 3.11–3.13
doc/                    problem.md, research.md, design*.md
examples/site.example.yaml
src/stockscan/
  cli.py                serve / selftest / rerun
  config.py             data directory, site.yaml, camera calibration files
  store.py              photo sets, photos, truth.csv, clicks, reports on disk
  jobs.py               background worker, job status
  imageio.py            loading, EXIF, orientation, thumbnails
  targets.py            dictionary, ID blocks, segment geometry, PDF generation
  detect.py             marker / ChArUco detection, grouping into segments
  calib.py              camera calibration
  pose.py               camera pose per segment, plane agreement
  rectify.py            straight-on resampling of the face plane
  faces.py              automatic face detection
  truth.py              alignment with hand measurements, error statistics
  analyses.py           inspect, calibrate, size test, plane check, measure
  report.py             HTML reports and summary lines
  synth.py              synthetic scenes for tests
  web/
    app.py              Flask routes
    templates/          Jinja2 pages
    static/             CSS, upload.js, measure.js
tests/                  pytest suite (synthetic only)
```

## Deliberately left for later

Marker map and self-surveying; multi-view face location; lengths; front/back
pairing; angled-cut detection; inventory database and change report;
learned segmentation; coverage guidance during capture; user accounts.

## Open questions

1. Desktop: operating system and CPU (x86-64 / ARM)? Is a supported
   Python (3.11–3.13) installed or installable? Can it install packages
   from PyPI, or is an offline wheel bundle needed?
2. Which camera(s) will be tested first?
