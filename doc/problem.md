# Problem Statement

## Context

A shop keeps metal bar and plate stock on open shelving. Today the
inventory is taken by hand: someone measures every piece and updates a
record. This is slow and error prone.

The goal is to replace the hand measurement with photographs: someone takes
a series of photos of the shelves, the photos are processed, and the system
produces an inventory of every piece with its dimensions and a comparison
against the previous inventory.

## How the stock is stored

- Pieces lie horizontally on shelves, side by side. They are not stacked on
  top of each other.
- Thin plate may be stored on edge, side by side, like books on a bookshelf.
- One end of every piece is pushed up against the front of the shelf, so the
  front ends are roughly aligned. Alignment is by hand and not exact.
- Pieces may touch their neighbors. Where they touch, the seam between them
  is visible (shelf surfaces are not perfectly flat, edges are not sharp).
- Different materials are kept on separate, labelled shelves or shelf
  sections. The system does not need to identify material.
- Shelves are accessible and visible from the front, the back, and the
  sides, so both ends of each piece can be photographed.

## What must be measured

For every piece:

| Dimension | Seen from | Required precision |
|---|---|---|
| Width (cross-section) | end of the piece | about ±0.1 in |
| Height (cross-section) | end of the piece | about ±0.1 in |
| Length | the hard one: the far end is not visible from the front | about ±0.25 in |

The design envelope, from smallest to largest:

- Smallest piece worth tracking: on the order of 1/2 in × 1/2 in in
  cross-section and a few inches long.
- Typical pieces: rectangular bar from about an inch to a few inches on a
  side.
- Longest pieces: a few feet.
- Thin plate (for example 1/4 in thick) standing on edge.

Exact sizes, counts and shelf layouts are site configuration and are not
part of this repository.

## Scope by phase

- **First:** rectangular cross-sections with square-cut ends.
- **First version may flag** ends that are not square (angled cuts) for a
  human to check. These are infrequent.
- **Later:** angled cuts are legal and must be measured, not just flagged.
- **Later / manual for now:** round stock.

## Things that change between inventories

- Pieces are added, removed, and moved.
- Pieces are cut. Any dimension can change: length, width, or height.
  Usually the unpainted end is the one cut, but this is not a rule.
- Ends may be painted (color coding). Pieces may come back with no paint,
  or with partially removed paint. **Painted and unpainted pieces must both
  be measured, and the system must not depend on paint or color.**
- Pieces may carry paper labels from the supplier. The system must tolerate
  labels (not be confused by them) but must not look for or require them.
- Machine-readable codes (QR, DataMatrix) may be added to pieces later as an
  optional aid. The system must work without them.

## Constraints

### Capture

- The only measuring device is a camera: a smartphone, or possibly a
  dedicated consumer digital camera writing to an SD card.
- Capture is **hand-held**. Nothing may be mounted in a way that gets in the
  way of using the shelves. Fixtures, sleds, or mounted cameras may be
  considered later but the system must be built for hand-held capture.
- A clip-on polarizing filter is acceptable if reflections are a problem.
- A few extra minutes of photo taking per inventory is acceptable; accuracy
  and reliability matter more than capture speed.

### The shelves

- Shelves may be marked up freely: printed fiducial targets (e.g. ChArUco
  strips/boards), paint, printed posters covered with clear polycarbonate,
  reference features.
- Markings will be **added incrementally**. The system must work with few
  markings (with reduced accuracy or coverage) and get better as more are
  added. It must never require all markings to be present.
- Lighting may be changed using inexpensive commodity parts (e.g. LED
  strips).

### Software

- All software and libraries must be open source.
- This project is published under the **AGPL-3.0**.
- Language choice is open; prototype in Python.

### Development and deployment

- Development happens away from the site. Code is published to a public
  repository and installed at the site from there.
- **Data flows one way only:** repository → site. No photos, logs, or data
  come back from the site. Results are relayed by a person, so tests must
  print short, numeric summaries that are easy to read out and retype.
- Consequently, iteration is slow (about one cycle per day). Each release
  must come with self-checks and field tests the site operator can run.
- The system runs as a web app on an ordinary desktop computer at the site,
  used from a browser on that computer. Photos are uploaded through the
  browser or copied into the app's folders (e.g. from an SD card).
- The site network may not let a phone reach the desktop (no DNS or TLS
  certificates can be relied on). Plain-HTTP access from a phone is a bonus
  when it works, never a requirement. On-the-spot feedback on the phone is
  a later, optional feature.
- **No site-specific information is ever committed to the repository:** no
  photos, shelf dimensions, layouts, quantities, materials, or process
  details. Site configuration lives in files kept at the site, outside the
  repository.

## Output

- A per-piece inventory: location (shelf, position), width, height, length,
  and a confidence for each measurement.
- A comparison with the previous inventory: unchanged, cut (which
  dimension), moved, removed, added.
- Flags for anything a person should check: low confidence, angled cut,
  front/back mismatch, area not covered by photos.
