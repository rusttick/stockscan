# Research: Options and Recommended Development Path

See [problem.md](problem.md) for the problem statement.

## 1. The problem splits in two

**Cross-section (width × height, ±0.1 in).** Every piece's front end is
roughly in one vertical plane (the shelf front). A plane photographed together
with reference targets lying in (or near) that plane can be measured from a
single photo by removing the perspective (a planar homography / known camera
pose). Back-of-envelope: a shelf-width photo of 4000 px across a few feet gives
roughly 0.01–0.02 in/px, so ±0.1 in is 5–10 px of margin. This is the easy
part.

**Length (±0.25 in).** The far end of each piece is not visible from the front,
and far ends are at different depths. Most of the design effort is in how to
locate the far ends. Because the shelves can be photographed from the back,
the far ends can be seen directly.

## 2. Building blocks (all open source)

| Block | Options | Notes |
|---|---|---|
| Fiducial targets | ArUco, ChArUco, AprilTag (OpenCV `aruco` module, Apache-2.0) | ChArUco interpolates chessboard corners between markers and is much more precise than plain ArUco corners [1]. Works when partially occluded. |
| Camera calibration | OpenCV `calibrateCamera` with a ChArUco board | One-time per camera / lens / resolution. |
| Marker maps | Self-surveying maps of many markers (MarkerMapper / UcoSLAM, Muñoz-Salinas et al. [2]); or our own bundle adjustment | Lets markers be added incrementally without surveying them by hand. |
| Classical segmentation | OpenCV edges, line fitting, projections | Deterministic, fast, no training data. |
| Learned segmentation | SAM 2 (Apache-2.0) [5], YOLO instance segmentation (Ultralytics, AGPL-3.0) | Bar-end detection in bundled stock is a studied problem [3][4]. Fallback for difficult cases. Needs validation for measurement accuracy. |
| Multi-view 3D | COLMAP (BSD), OpenMVS (AGPL), Meshroom/AliceVision (MPL-2.0) | General reconstruction; heavy; struggles on low-texture, reflective metal. |
| Phone depth sensors | iPhone LiDAR, ARCore depth | About ±1 cm on small objects [7][8][9]; the AR frameworks are not open source. |
| Image I/O | OpenCV, Pillow, pillow-heif (iPhone HEIC) | Or set the phone to save JPEG. |

All of these are compatible with releasing this project under AGPL-3.0.

## 3. Cross-section options

| # | Option | Assessment |
|---|---|---|
| C1 | **Targets on the shelf front + perspective removal + classical CV.** Find seams between pieces and each piece's top edge in the rectified image. | **Recommended primary.** Simple, deterministic, needs no training data. Uses shape only, so paint, labels and dirt are irrelevant. |
| C2 | C1 + learned segmentation (SAM 2 / YOLO) | Fallback for touching pieces with faint seams, leaning plates, clutter. Add when C1's failures are understood. |
| C3 | Snap to standard stock sizes | Useful hint only: since any dimension can be cut, sizes are not guaranteed standard. Use as a soft prior / "near standard size" annotation, not a correction. |

Precision notes:

- If a piece's front end is not exactly in the target plane, its measured size
  is off by (offset / camera distance). For example, 0.5 in of misalignment
  at 30 in from the camera is a 1.7 % error, about 0.05 in on a 3 in face.
  Acceptable at first; eliminated once each face's depth is measured (§4).
- A long, thin strip of targets constrains the plane poorly in the short
  direction. Use strips at least 3 squares tall (2 rows of ChArUco corners)
  and a calibrated camera (solve for camera pose, not a free homography).

## 4. Length options

| # | Option | How | Assessment |
|---|---|---|---|
| L1 | **Locate both end faces in 3D from several photos** | Photos from the front and from the back, each with targets in view so the camera position is known in the shelf's coordinate frame. Each end face is a rectangle whose width, height and lateral position are known from the front; its depth is found by comparing 2–3 photos from different positions. Length = back-face depth − front-face depth. | **Recommended.** Back-of-envelope: two photos 24 in apart, 5 ft from the face, give about 20 px per inch of depth, so 0.25 in ≈ 5 px. Comes with a built-in check: front and back cross-sections must match. Also measures how well each front end is aligned, and later the angle of angled cuts. |
| L1a | Single rear photo + shelf surface as reference | The bottom edge of a back face touches the shelf surface; with the shelf plane known from targets, one photo gives its depth. | **First prototype / extra constraint for L1.** Weaker for faces far from the camera (about 2 px per 0.25 in at 5 ft with the camera 10 in above the shelf). |
| L2 | Oblique view along the top surfaces | Camera above the front edge looks back along the bars; depth scale on shelf sides / underside of the shelf above. | Possible but limited by the space between shelves; taller neighbors hide shorter pieces. Not needed when the back is accessible. |
| L3 | Mirror periscope | Angled mirror strip under the shelf above. | Fallback for shelves with no back access. Adds calibration and cleaning. |
| L4 | Full photogrammetry (COLMAP etc.) | Reconstruct the whole shelf in 3D. | Most general, most fragile: plain metal has little texture and reflects light, and processing is slow. Published smartphone photogrammetry results on metal parts are reported in relative terms (within 15 % of a laser scan) [6], far from 0.25 in. Research option only. |
| L5 | Phone LiDAR / AR depth | | **Rejected:** about ±1 cm (0.4 in) [7][8][9], worse on reflective surfaces; proprietary frameworks. |
| L6 | Monocular learned depth | | **Rejected:** far from the needed accuracy. |
| L7 | Laser line / structured light | | **Rejected:** end faces are only a few inches tall, which makes the triangulation geometry poor. |
| L8 | Track changes instead of re-measuring | Identify pieces day to day; only re-measure what changed. | **Rejected as primary:** any dimension can be cut and paint is unreliable, so every piece must be measured every time. Day-to-day matching is still used to produce the change report. |

### Hard case: thin plate stored on edge

A short plate between longer neighbors has its back face at the bottom of a
slot as wide as the plate is thick. It is only visible from almost directly
behind; moving the camera sideways is blocked by the neighbors, moving it up
and down is not. Approach: photos straight behind at two or more heights and
closer range; mark the result low-confidence when the face is poorly seen; fall
back to a hand measurement for those pieces.

## 5. Targets: start with a few, keep adding

Design rules so the system works with few targets and gets better with more:

1. **Printed target segments.** Each printed page (strip or board) is a rigid
   object with a printed serial number and its own range of marker IDs.
2. **Unique IDs everywhere.** Any photo identifies which segments it sees,
   so photos never need to be labelled by hand.
3. **Site configuration maps segments to locations** (kept at the site only).
4. **Self-surveying map.** Segment positions relative to each other are
   estimated from photos that see more than one segment, and refined with
   every inventory. A newly added segment is located automatically the first
   time it is photographed next to known ones. No tape-measure survey.
5. **Every measurement carries a confidence.** Fewer targets → larger
   uncertainty and more "check by hand" flags, never silently wrong numbers.

Suggested placement levels, each improving on the previous:

| Level | Targets | Capability |
|---|---|---|
| 1 | Strip along each shelf front edge | Cross-sections. No lengths. |
| 2 | + strip along the back edge, + one hand-measured front-to-back distance | Lengths via L1a. Good near the back, rough far from it. |
| 3 | + vertical strips on the shelf uprights (visible from front and back) | Front and back tied together without hand measurement; L1 multi-view lengths. |
| 4 | + side margins of the shelf surface, underside of the shelf above, end panels | More targets in every photo, so camera positions are better, plate slots can be measured, angled cuts can be measured, sloppy photos are tolerated. |

ID budget: OpenCV's largest predefined dictionaries have 1000 IDs, too few
for full coverage. Decided in design1.md ("Dictionary and IDs"): a custom
8 × 8 dictionary of 20 000 markers with minimum Hamming distance 17,
prefix-stable so it can be extended without renumbering.

Glare: clear polycarbonate covers can reflect light sources into the camera and
make targets undetectable. Test covered vs. uncovered and matte film; consider
cross-polarization (polarizer film on lights and lens).

## 6. Capture and processing model

- **Local web app.** A small web app runs on a desktop computer. Photos are
  uploaded through the browser (many at once) or copied into a set's
  folder; analyses run in the background and produce HTML reports viewed in
  the same app, each with a one-line numeric summary. Plain files on disk,
  no external services, no certificates.
- **Coverage check after upload.** Even without on-phone feedback, the report
  says which shelf areas were not covered or were blurry, so the operator can
  go back and retake while still on site.
- **On-phone use when possible.** If the phone can reach the desktop over
  plain HTTP, the same upload page works from the phone; capture guidance on
  the phone comes later.
- **Any camera.** Calibrations are keyed by camera model, lens, and image
  size from the photo metadata (EXIF); phones that switch lenses on their own
  (e.g. macro mode) are detected and flagged.

## 7. Recommended development path

| Phase | Content | Exit criterion |
|---|---|---|
| 0 | Tooling: installable package, synthetic self-tests, target generator, camera calibration, photo inspection report | Calibration error < 1 px; targets detected reliably at working distance in site lighting |
| 1 | Cross-section from front photos (Level 1 targets). Manual-click measuring mode to test the geometry on its own; automatic seam/top-edge detection | Manual mode within ±0.05 in; automatic within ±0.1 in on a hand-measured sample |
| 2 | Lengths via single rear photo + shelf plane (Level 2) | ±0.25 in on pieces near the back; known degradation further away |
| 3 | Marker map + multi-view end-face location (Level 3), front/back pairing and cross-check | ±0.25 in length on typical stock; front-alignment measured |
| 4 | Inventory database, day-to-day matching, change report | Correct change report on staged changes |
| 5 | Angled cut detection → measurement; thin-plate handling; learned segmentation fallback | |
| 6 | Capture UX: coverage hints, optional on-phone upload page | |

Each phase ships with field tests that run at the site and print a one-line
numeric summary (see design documents). Site photos and hand measurements
stay at the site and form a growing regression set that every new release is
re-run against.

## 8. Main risks

1. **Seams between touching pieces of the same height** may be faint in
   some lighting. Mitigation: lighting experiments, multi-view, learned
   segmentation.
2. **Back faces of short pieces between long neighbors** (especially thin
   plate). Mitigation: views straight behind at several heights; confidence
   flags; hand fallback.
3. **Glare** from polished/sawn faces and from target covers. Mitigation:
   diffuse lighting, matte covers, polarizers.
4. **Hand-held photo quality** (blur, auto lens switching). Mitigation:
   automatic quality checks in the report.
5. **Slow iteration** (one cycle per day, no data returned). Mitigation:
   synthetic tests at home; field tests designed to isolate one question
   each; manual-measure modes to separate geometry errors from detection
   errors.

## Sources

1. [OpenCV ChArUco detection tutorial](https://docs.opencv.org/3.1.0/df/d4a/tutorial_charuco_detection.html)
2. [Muñoz-Salinas et al., Mapping and Localization from Planar Markers (arXiv 1606.00151)](https://arxiv.org/pdf/1606.00151)
3. [Automated Steel Bar Counting and Center Localization with CNNs (arXiv 1906.00891)](https://arxiv.org/pdf/1906.00891)
4. [Dense rebar end-face segmentation based on YOLACT_REBAR (SPIE)](https://remotesensing.spiedigitallibrary.org/conference-proceedings-of-spie/13089/130890G/Dense-rebar-end-face-segmentation-based-on-YOLACT_REBAR-instance-segmentation/10.1117/12.3020960.full)
5. [SAM 2 repository (Apache-2.0)](https://github.com/facebookresearch/segment-anything-2)
6. [Probing smartphone-based photogrammetry for part profiling in WA-DED (BAM, 2025)](https://opus4.kobv.de/opus4-bam/frontdoor/index/index/year/2025/docId/65084)
7. [Luetzenburg et al., iPhone 12 Pro LiDAR evaluation (Scientific Reports 2021)](https://doaj.org/article/05f6da2bb22e4343bec5db6fcf0ecf88)
8. [Zaczek-Peplinska & Kowalska, iPhone 13 Pro LiDAR vs. TLS (FIG 2022)](https://fig.net/resources/proceedings/fig_proceedings/fig2022/ppt/ts03g/TS03G_zaczek-peplinska_kowalska_11458_ppt.pdf)
9. [Hamdan et al., iPhone LiDAR apps evaluation (EGU 2026)](https://meetingorganizer.copernicus.org/EGU26/EGU26-2752.html)
10. [A mobile vision-based gap and flush measuring system using ArUco markers](https://pure.skku.edu/en/publications/a-mobile-vision-based-system-for-gap-and-flush-measuring-between-/)
