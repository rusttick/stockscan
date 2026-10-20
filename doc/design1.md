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

- Supported: CPython 3.11, 3.12, 3.13. Reference version: 3.13 (the one
  developed and tested with first).
- The app always runs in its own virtual environment (`venv`, part of the
  standard library), never in the system Python.

### Dependencies

All open source and AGPL-compatible:

| Package | Use | License |
|---|---|---|
| `numpy` | arrays | BSD-3 |
| `opencv-python-headless` | computer vision (no GUI libraries needed) | Apache-2.0 |
| `Pillow` | image I/O, thumbnails | MIT-CMU (HPND) |
| `pillow-heif` | iPhone HEIC photos (optional at run time) | BSD-3 (bundles LGPL libheif) |
| `PyYAML` | config and result files | MIT |
| `Flask` (+ its deps Jinja2, Werkzeug, …) | web app | BSD-3 |
| `waitress` | web server, pure Python | ZPL-2.1 |
| `pytest` (dev only) | tests | MIT |

`pyproject.toml` declares **compatible version ranges** (e.g.
`Flask>=3.0,<4`). There are no hash-locked requirement files: the goal is
an install that works by hand with plain `pip`, not a bit-exact
environment. The versions actually installed are printed by
`stockscan selftest` and shown on the dashboard, so they can be read back
when something behaves differently at the site.

### Install and use

No install or run scripts. `README.md` documents the steps by hand, for
Linux/macOS and Windows shells. In outline:

1. Create a venv in the repository folder with a supported Python
   (`python3.13 -m venv .venv`, or `py -3.13 -m venv .venv` on Windows) and
   activate it.
2. `pip install .`
3. `stockscan selftest`
4. `stockscan serve --data DIR`, then open `http://localhost:3000`.

The data directory should be outside the repository folder. Upgrading to a
new release: download it, then repeat steps 2–3 in the same venv (or a fresh
one).

### Offline install

If the site computer cannot reach PyPI, download the wheels at home on a
machine with internet access (`pip download --only-binary=:all: --platform …
--python-version 3.13 -d wheels . "setuptools>=77"`, plus `colorama` for
Windows, because pip resolves platform-only dependencies for the machine it
runs on), copy the folder over, and install with
`pip install --no-index --find-links wheels .`. The README has the exact
commands.

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

### Testing on other platforms

No CI service. The test suite and `stockscan selftest` run at home on macOS
before a release; on Windows and Linux the site's `stockscan selftest`
(field test T0) is the platform check.

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
| `stockscan dictionary check [--mc N]` | Re-verify the marker dictionary; print its guarantees |

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

OpenCV's predefined dictionaries stop at 1000 IDs, and the larger ones lose
error tolerance quickly (measured: `DICT_6X6_1000` has minimum Hamming
distance 9, OpenCV's own generator gives 7×7 with 4000 IDs at distance 13).
Covering every surface needs about 10 000 IDs, with room to double. So
stockscan ships its own dictionary:

- **`SS8x8_d17_20000`**: 20 000 markers of 8 × 8 cells (10 × 10 with the
  black border), minimum Hamming distance **17** over every pair of markers
  and every rotation, and between each marker and its own rotations. The
  file is `src/stockscan/data/SS8x8_d17_20000.txt` (hex codes, SHA-256
  pinned in code).
- Generated by a seeded greedy search (`stockscan.markers`). The search is
  **prefix-stable**: the first k markers never depend on how many are
  generated, so the dictionary can be extended later without renumbering
  anything already printed. Generation also rejects nearly uniform patterns
  (fraction of white cells 30–70 %, enough cell-to-cell transitions).
- The decoder corrects up to **r = 6** wrong cells. Then:
  - exact: up to 6 wrong cells are always read correctly (ID and rotation);
    up to 10 wrong cells are never read as a different marker (they are
    rejected instead). Checked exhaustively at every self-test.
  - probabilistic: a random pattern is accepted as some marker with
    probability 3.6 × 10⁻⁷; with 5 % of cells misread independently,
    96 % of markers are read, 4 % rejected, 3 × 10⁻¹¹ misidentified.
    These formulas assume random patterns / independent cell errors (real
    glare and blur are not independent); the exact guarantees do not.
  - `stockscan dictionary check [--mc N]` prints all of this, re-verifies
    the file exhaustively and optionally cross-checks the formulas by
    Monte Carlo. The tests check the formulas against simulation on small
    dictionaries, where misidentification is frequent enough to measure.
- OpenCV's decoder compares a candidate with every marker in turn (~30 ms
  per candidate at 20 000 markers). stockscan lets OpenCV find the
  candidate quads, samples their cells the same way OpenCV does, and
  decodes all candidates at once; tests check that IDs and corners equal
  OpenCV's own result.
- **One ID pool.** Every printed object (front row, board, calibration
  board, size-test patch) is a *segment* with a serial number and a
  contiguous block of IDs, allocated in order from 0. The app records what
  it has issued and never issues an ID twice. A board of 10 cm squares on
  a 60 × 90 cm poster uses about 20 IDs; a front row of 8 cm markers on
  11 × 17 in paper uses 4.

### Printed items (Targets page)

All are generated as vector PDFs at exact physical size. **Page size is an
input:** letter, tabloid, A4–A0, ARCH C/D/E, or custom `WxH` with a unit
(`11x17`, `60x90cm`), plus margin. **All sizes are configurable** and accept
a unit (`10cm`, `80mm`, `3.15in`); the Settings page holds the defaults
used by the Targets page. Several rows or boards are packed per page, over
as many pages as needed. Every page has horizontal and vertical **scale
bars** and the text "PRINT AT 100% / ACTUAL SIZE".

| Item | Content | Default size | Purpose |
|---|---|---|---|
| Front marker row | One row of plain markers, no chessboard, as many as fit the page (or a given number) | 8 cm markers, 2 cm gap | Front face of the shelf beam: the face plane for cross-sections |
| ChArUco board | As many squares as fit (or given rows × columns) | 10 cm squares | Shelf surfaces, uprights, end panels |
| Calibration board | ChArUco, as many squares as fit (at most 150 markers) | 1.2 in squares | Camera calibration; plane check |
| Size-test sheet | ChArUco patches, 4 × 3 squares each | 2, 3, 4, 5, 7 cm | Find the smallest reliable size |

A marker row has no chessboard corners: its pose uses the four sub-pixel
corners of each marker, which lie on two parallel lines and so constrain the
plane's tilt. ChArUco markers are 75 % of the square (configurable).

Each segment's geometry (layout, size, rows, columns, first ID, position on
the page) is printed on the page as text and saved as YAML in `targets/`,
and the PDF is drawn from that same record, so the software never has to
guess how a segment was printed.

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

The end faces are assumed to lie in the plane of the front marker row,
shifted by an optional configured offset. The region above the row is resampled into
a straight-on image at a fixed scale (default 100 px/in, i.e. 0.01 in/px).
Measurements in this image are plain pixel distances divided by 100.

### Size test

Detection rate for each square size of the size-test sheet, per photo, with
the photo's distance and angle to the sheet. Summary: smallest size detected
in ≥ 95 % of photos, per distance band.

### Plane check

The operator holds or props the calibration board against the front ends of
the stock (in the face plane), with a front marker row in view. The software
finds the camera pose **from the front row only**, rectifies, then detects the
board's corners in the straight-on image and compares their spacing with the
known spacing. The result is the geometry error by itself, with no face
detection involved. Expected: < 0.5 % scale error, i.e. < 0.015 in on a
3 in face.

### Automatic face detection (stretch)

Uses shape only, never color:

1. Find the shelf line (bottom of the faces) just above the front row.
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

- A front marker row and rectangles of known size in one plane, viewed by a
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
| **T4** Geometry | Mount one front marker row on a shelf beam face. Plane-check set with the calibration board against the stock ends; 5–10 photos from different positions | `SS … T4 n=… scale_med=…% scale_max=…%` | < 0.5 % |
| **T5M** Manual cross-section | Front set: choose a section with 10–20 pieces including the smallest and some plate. Hand-measure width and height left to right into the table. Photograph from 2–3 positions. Measure manually. | `SS … T5M n=… w_med w_max h_med h_max` | max ≤ 0.05 in |
| **T5A** Automatic cross-section | Same set, automatic analysis | `SS … T5A found=…/… w_med w_max h_med h_max` | informative; target ≥ 80 % found, ≤ 0.1 in |
| **T6** Back photos | Mount a front marker row on the back beam of the same shelf; back set from the back; Inspect | `SS … T6 photos=… seg_found=… blur=…` | collected for later phases |
| **T7** Re-run | "Re-run all" | all summary lines | no crashes |
| **T8** Phone upload | Only if a phone can reach the desktop: upload a few photos from the phone's browser, compare pixel size and EXIF with the same photos copied by cable | `SS … T8 same_size=… exif_lens=…` | full resolution, lens recorded |

Useful verbal feedback beyond the numbers: what the failures look like in
the report (e.g. "seams between equal-height bars missed", "glare on
cover"), and anything awkward about taking or uploading the photos.

## Repository layout

```
LICENSE                 AGPL-3.0
pyproject.toml          package metadata, compatible dependency ranges
README.md               includes install and usage (written once built)
.gitignore
doc/                    problem.md, research.md, design*.md
examples/site.example.yaml
src/stockscan/
  cli.py                serve / selftest / rerun
  config.py             data directory, site.yaml, camera calibration files
  store.py              photo sets, photos, truth.csv, clicks, reports on disk
  jobs.py               background worker, job status
  imageio.py            loading, EXIF, orientation, thumbnails
  markers.py            marker dictionary: generation, verification, guarantees
  data/SS8x8_d17_20000.txt   the shipped dictionary (pinned checksum)
  targets.py            ID pool, segment geometry, page layout, PDF generation
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
