# Tasks

Tracks the build of [design1.md](design1.md) (release v0.1.0).
Status: `[ ]` to do, `[~]` in progress, `[x]` done, `[-]` dropped.

## v0.1.0: first field release

| # | Status | Task | Depends on | Done when |
|---|---|---|---|---|
| 1 | [x] | **Scaffold:** `pyproject.toml` (compatible ranges, no lock files), `.gitignore` (images, data dirs, `.venv`), `src/stockscan/` package, `examples/site.example.yaml` | — | Clean venv installs with `pip install .`; `stockscan --help` works |
| 2 | [x] | **CLI:** `serve`, `selftest`, `rerun` entry points; startup prints reachable addresses; warning when network-reachable without `--password` | 1 | Commands run (stubs where later tasks fill in) |
| 3 | [x] | **imageio:** load JPEG/HEIC, EXIF orientation, EXIF camera/lens/focal/exposure, thumbnails, sharpness and clipping metrics | 1 | Unit tests on generated images with EXIF |
| 4 | [x] | **targets:** `DICT_5X5_1000`, reserved IDs 0–99, ID-block allocation registry, segment geometry YAML; PDFs for calibration board, size-test sheet, strip segments; page size input (letter/tabloid/a4/a3/`WxH`), margin, scale bar, printed labels | 1 | PDFs have exact physical size; markers re-detected from rasterized PDF pages in tests |
| 5 | [x] | **detect + pose:** ArUco detection with sub-pixel refinement, grouping by segment, ChArUco corner interpolation, `solvePnP` per segment with print-scale correction, plane agreement between segments | 4 | Synthetic tests: pose and corner errors within tolerance |
| 6 | [x] | **calib:** ChArUco camera calibration, per-photo error, coverage map, calibration YAML keyed by make/model/lens/size, matching photos to calibrations | 5 | Synthetic test recovers the virtual camera |
| 7 | [x] | **synth:** render synthetic scenes (board views; strip + rectangles in a plane) with blur/noise and known ground truth | 4 | Used by tests 5, 6, 10, 12 |
| 8 | [x] | **store:** data directory layout, `site.yaml`, sets (`set.yaml`, photos, `truth.csv`, `clicks.yaml`), content-hash dedupe, case-insensitive names, rescan folder, report folders, safe file names | 1 | Unit tests on a temp data dir |
| 9 | [x] | **jobs:** single background worker, queue, status, error capture with traceback | 8 | Unit test runs a dummy job end to end |
| 10 | [x] | **rectify:** straight-on resampling of the face plane at fixed px/in with configurable offset | 5 | Synthetic: rectangle sizes within 0.02 in |
| 11 | [x] | **report:** HTML report writer, summary-line format (`SS <ver> <test> k=v …`), `result.yaml` | 8 | Reports render; summary lines parse |
| 12 | [x] | **analyses:** inspect, calibrate, size test, plane check | 3, 5, 6, 9, 10, 11 | Each runs on synthetic sets and writes a report + summary line |
| 13 | [x] | **truth:** `truth.csv` read/write, order-preserving alignment, error statistics | 8 | Unit tests incl. missing/extra faces |
| 14 | [x] | **web app:** Flask + waitress; dashboard (with copy-all summary lines), new set, set page, report viewer, targets page, cameras, settings, self-test page; optional basic-auth password; no external resources | 8, 9, 11 | Flask test-client tests for every page |
| 15 | [~] | **upload:** drop zone + multi-file picker, one file per request, parallel with progress and retry, dedupe, size limit | 14 | Test-client upload test; manual check in a browser |
| 16 | [~] | **manual measure:** canvas page with zoom/pan, loupe for precise clicks, clicks saved per set, result as an analysis report compared with truth | 10, 13, 14 | Manual check in a browser; clicks → measurements unit-tested |
| 17 | [x] | **faces (stretch):** automatic shelf line / seams / top edges, overlays of accepted and rejected candidates, "near standard size" note | 10, 13 | Synthetic faces found and measured within 0.05 in |
| 18 | [x] | **rerun:** re-run all sets with the current version; print all summary lines | 12 | Works on a data dir with several set types |
| 19 | [x] | **selftest:** packaged synthetic suite runnable without pytest knowledge; prints `SS <ver> T0 pass=a/b` | 7, 12, 14 | Runs in under a minute |
| 20 | [-] | **CI:** dropped by the owner; selftest at the site (T0) is the platform check | — | — |
| 21 | [x] | **README:** install and usage for Linux/macOS and Windows shells, offline install, firewall note, field-test guide pointer | all | Steps verified in a fresh venv here |
| 22 | [~] | **Release check:** no workplace details anywhere in the repo; no images or data committed; version set | all | Reviewed by the owner |
| 23 | [x] | **Marker dictionary:** `markers.py` seeded prefix-stable greedy generator, file format with checksum, exhaustive verification, exact and probabilistic guarantees, Monte Carlo validation; shipped `SS8x8_d17_20000`; `stockscan dictionary check/generate` | — | Tests prove the exact guarantees exhaustively on small dictionaries and match the formulas to simulation; self-test re-verifies the shipped file |
| 24 | [x] | **Fast decoding:** OpenCV finds candidates, stockscan samples cells like OpenCV and decodes in batch | 23 | Same IDs and corners as OpenCV's decoder on synthetic images; ~40x faster |
| 25 | [x] | **Targets on the new dictionary:** one ID pool; front marker row (no chessboard); ChArUco boards for surfaces; size-test sizes configurable; lengths with units; poster page sizes; multi-page packing; default sizes on the Settings page | 23 | Re-detection, geometry and web tests pass |
| 26 | [x] | **Tolerate phone/transfer behaviour:** HEIC decoder rotation undone; calibrations adapted to resized, rotated (no EXIF orientation) and digitally zoomed photos; per-set camera hint for photos without EXIF; Inspect warnings (no EXIF, lens change, zoom, mixed sizes) | 6 | Synthetic variants all measured within 0.02 in (0.04 at half size); transforms checked against OpenCV projection |

## Later (see research.md §7)

- Marker map and self-surveying of target segments
- Multi-view location of end faces; lengths; front/back pairing
- Angled-cut detection, then measurement
- Inventory database; day-to-day matching; change report
- Learned segmentation fallback
- Capture guidance on the phone
