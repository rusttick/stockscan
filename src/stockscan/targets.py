"""Printable targets: marker IDs, segment geometry, pages.

All targets use the shipped 8x8 dictionary (stockscan.markers, 20 000 IDs,
minimum Hamming distance 17).  A *segment* is one rigid printed object with
its own serial number and a contiguous block of marker IDs taken from one
pool, so any photo identifies the segments it sees:

  front     a single row of markers, no chessboard (front face of a shelf beam;
            the reference plane for cross-sections)
  board     a ChArUco board for shelf surfaces, uprights, end panels
  calib     a ChArUco calibration board
  sizetest  ChArUco patches of several square sizes (finding the smallest
            reliable size)

Coordinates of a segment: origin at the top-left corner of its first
square / marker, x to the right, y down, z into the target, in inches.
"Up" is -y (the printed TOP arrow).  For ChArUco segments this matches
OpenCV's board frame.

All lengths can be given with a unit ("10cm", "80 mm", "3.15in"); internally
everything is inches.  Pages are built as lists of drawing primitives from the
segment positions stored in the print record, then written as vector PDF or
rasterized (tests and synthetic scenes) from the same primitives.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
import zlib
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import yaml

from . import markers

DICT_NAME = markers.DEFAULT_NAME
N_IDS = markers.DEFAULT_PARAMS["count"]
MARKER_RATIO = 0.75  # ChArUco marker side / square side
CALIB_MAX_MARKERS = 150
SIZETEST_SQUARES = (4, 3)

ITEMS = {
    "front": "Front marker row (shelf beam face)",
    "board": "ChArUco board (surfaces, uprights, panels)",
    "calib": "Calibration board",
    "sizetest": "Size-test sheet",
}

MM = 1 / 25.4
PAGE_SIZES = {
    "letter": (8.5, 11.0),
    "tabloid": (11.0, 17.0),
    "arch-c": (18.0, 24.0),
    "arch-d": (24.0, 36.0),
    "arch-e": (36.0, 48.0),
    "a4": (210 * MM, 297 * MM),
    "a3": (297 * MM, 420 * MM),
    "a2": (420 * MM, 594 * MM),
    "a1": (594 * MM, 841 * MM),
    "a0": (841 * MM, 1189 * MM),
}

# Page bands (inches) reserved inside the margin.
TITLE_BAND = 0.35
LEFT_BAND = 0.55
BOTTOM_BAND = 0.85
TICK_W = 0.012
LABEL_H = 0.3
GAP = 0.4  # between blocks on a page
OVERLAP = 0.003  # black cells overlap the black cell below by this much (in)

_UNITS = {"in": 1.0, "inch": 1.0, '"': 1.0, "cm": 10 * MM, "mm": MM}
_LEN_RE = re.compile(r'^\s*([0-9]*\.?[0-9]+)\s*(in|inch|"|cm|mm)?\s*$', re.I)


def parse_length(value, default_unit: str = "in") -> float:
    """'10cm', '80 mm', '3.15in', '3.15"' or a number (default_unit) -> inches."""
    if isinstance(value, (int, float)):
        return float(value) * _UNITS[default_unit]
    m = _LEN_RE.match(str(value))
    if not m:
        raise ValueError(f"length {value!r}: use a number with in, cm or mm, e.g. 10cm")
    v = float(m.group(1)) * _UNITS[(m.group(2) or default_unit).lower()]
    if v <= 0:
        raise ValueError(f"length {value!r} must be positive")
    return v


def parse_lengths(text) -> list[float]:
    if isinstance(text, (list, tuple)):
        return [parse_length(v) for v in text]
    return [parse_length(v) for v in re.split(r"[,\s;]+(?=[0-9.])", str(text).strip()) if v.strip()]


def fmt_length(inches: float) -> str:
    """Both units, e.g. '10.0 cm / 3.937 in'."""
    return f"{inches * 2.54:.1f} cm / {inches:.3f} in"


def parse_page_size(text: str) -> tuple[float, float]:
    """'letter', 'a1', '11x17', '60x90cm', '24 x 36 in' -> (width, height) inches, portrait."""
    key = str(text).strip().lower().replace(" ", "")
    if key in PAGE_SIZES:
        w, h = PAGE_SIZES[key]
    else:
        m = re.match(r"^([0-9.]+)x([0-9.]+)(in|cm|mm)?$", key)
        if not m:
            raise ValueError(f"page size {text!r}: use {', '.join(PAGE_SIZES)} or WxH, e.g. 11x17 or 60x90cm")
        f = _UNITS[m.group(3) or "in"]
        try:
            w, h = float(m.group(1)) * f, float(m.group(2)) * f
        except ValueError:
            raise ValueError(f"page size {text!r}: use WxH, e.g. 11x17") from None
    if not (2 <= w <= 100 and 2 <= h <= 100):
        raise ValueError(f"page size {text!r} out of range (2 to 100 in)")
    return (min(w, h), max(w, h))


@lru_cache(maxsize=1)
def dictionary():
    return markers.default_opencv()


# ---------------------------------------------------------------- segments


@dataclass
class Segment:
    serial: str
    kind: str  # front, board, calib, sizetest
    squares_x: int  # charuco: squares across; row: number of markers
    squares_y: int  # charuco: squares down; row: 1
    square_in: float  # charuco: square side; row: marker pitch (marker + gap)
    marker_in: float
    first_id: int
    print_serial: str = ""
    page: int = 0
    x_in: float = 0.0  # top-left on the page
    y_in: float = 0.0
    layout: str = "charuco"  # or "row"

    @property
    def is_row(self) -> bool:
        return self.layout == "row"

    @property
    def n_markers(self) -> int:
        return self.squares_x if self.is_row else (self.squares_x * self.squares_y) // 2

    @property
    def ids(self) -> range:
        return range(self.first_id, self.first_id + self.n_markers)

    @property
    def gap_in(self) -> float:
        return self.square_in - self.marker_in if self.is_row else 0.0

    @property
    def width_in(self) -> float:
        if self.is_row:
            return self.squares_x * self.square_in - self.gap_in
        return self.squares_x * self.square_in

    @property
    def height_in(self) -> float:
        return self.marker_in if self.is_row else self.squares_y * self.square_in

    @property
    def n_corners(self) -> int:
        """Points available for pose: ChArUco corners, or 4 per marker for a row."""
        return 4 * self.n_markers if self.is_row else (self.squares_x - 1) * (self.squares_y - 1)

    def describe(self) -> str:
        last = self.first_id + self.n_markers - 1
        if self.is_row:
            return (f"{self.n_markers} markers {fmt_length(self.marker_in)}, gap {fmt_length(self.gap_in)}, "
                    f"ids {self.first_id}-{last}")
        return (f"{self.squares_x}x{self.squares_y} squares {fmt_length(self.square_in)}, "
                f"ids {self.first_id}-{last}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def charuco_board(seg: Segment):
    if seg.is_row:
        raise ValueError(f"{seg.serial} is a marker row, not a ChArUco board")
    return _board(seg.squares_x, seg.squares_y, seg.square_in, seg.marker_in, seg.first_id)


@lru_cache(maxsize=512)
def _board(sx, sy, square, marker, first_id):
    n = (sx * sy) // 2
    return cv2.aruco.CharucoBoard((sx, sy), square, marker, dictionary(),
                                  np.arange(first_id, first_id + n, dtype=np.int32))


def object_points(seg: Segment, scale: tuple[float, float] = (1.0, 1.0)) -> np.ndarray:
    """(N, 3) points in segment inches, print-scale corrected, indexed by corner id.

    ChArUco: the inner chessboard corners.  Row: marker k has points 4k .. 4k+3,
    its corners in OpenCV's order (top-left, top-right, bottom-right, bottom-left).
    """
    if seg.is_row:
        m, p = seg.marker_in, seg.square_in
        k = np.arange(seg.n_markers, dtype=np.float64)[:, None]
        cx = np.array([0, m, m, 0], float)[None, :] + k * p
        cy = np.broadcast_to(np.array([0, 0, m, m], float), cx.shape)
        pts = np.column_stack([cx.ravel(), cy.ravel(), np.zeros(cx.size)])
    else:
        pts = np.array(charuco_board(seg).getChessboardCorners(), dtype=np.float64).reshape(-1, 3)
    pts[:, 0] *= scale[0]
    pts[:, 1] *= scale[1]
    return pts


# ---------------------------------------------------------------- registry


class Registry:
    """Which prints, segments and IDs have been generated (DATA/targets/registry.yaml)."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self.data = {"dictionary": DICT_NAME, "next_serial": 1, "next_print": 1, "next_id": 0, "prints": []}
        if path is not None and path.exists():
            with open(path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if loaded.get("dictionary", DICT_NAME) != DICT_NAME:
                raise ValueError(f"{path}: targets were made with dictionary {loaded.get('dictionary')}, "
                                 f"this version uses {DICT_NAME}")
            self.data.update(loaded)

    def save(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, sort_keys=False)
        tmp.replace(self.path)

    @property
    def prints(self) -> list[dict]:
        return self.data["prints"]

    def get_print(self, serial: str) -> dict | None:
        for p in self.prints:
            if p["serial"] == serial:
                return p
        return None

    def segments(self, kind: str | None = None) -> list[Segment]:
        segs = [Segment.from_dict(s) for p in self.prints for s in p["segments"]]
        return [s for s in segs if kind is None or s.kind == kind]

    def find_segment(self, serial: str) -> Segment | None:
        for s in self.segments():
            if s.serial == serial:
                return s
        return None

    def id_map(self) -> dict[int, Segment]:
        """Marker ID -> segment."""
        return {i: seg for seg in self.segments() for i in seg.ids}

    def ids_left(self) -> int:
        return N_IDS - self.data["next_id"]

    def _new_serial(self) -> str:
        n = self.data["next_serial"]
        self.data["next_serial"] = n + 1
        return f"S{n:04d}"

    def _new_print(self) -> str:
        n = self.data["next_print"]
        self.data["next_print"] = n + 1
        return f"P{n:04d}"

    def _take_ids(self, n: int) -> int:
        first = self.data["next_id"]
        if first + n > N_IDS:
            raise ValueError(f"marker IDs exhausted: need {n}, {N_IDS - first} left in {DICT_NAME}")
        self.data["next_id"] = first + n
        return first

    def set_first_serial(self, n: int):
        if n < self.data["next_serial"]:
            raise ValueError(f"serial S{n:04d} already used; next free is S{self.data['next_serial']:04d}")
        self.data["next_serial"] = n


# ---------------------------------------------------------------- page layout

Prim = tuple  # ("rect", x, y, w, h) | ("text", x, y, size_pt, s) | ("line", x1, y1, x2, y2, w_pt)


@dataclass
class Page:
    width: float
    height: float
    prims: list = field(default_factory=list)

    def rect(self, x, y, w, h):
        self.prims.append(("rect", x, y, w, h))

    def text(self, x, y, size_pt, s):
        self.prims.append(("text", x, y, size_pt, s))

    def line(self, x1, y1, x2, y2, w_pt=0.5):
        self.prims.append(("line", x1, y1, x2, y2, w_pt))


def content_box(w, h, margin):
    """(x0, y0, x1, y1) of the area left for targets."""
    x0 = margin + LEFT_BAND
    y0 = margin + TITLE_BAND
    x1 = w - margin
    y1 = h - margin - BOTTOM_BAND
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise ValueError("page too small for the margin")
    return x0, y0, x1, y1


def scale_bar_lengths(w, h, margin) -> dict:
    x0, y0, x1, y1 = content_box(w, h, margin)
    return {"x_in": int(math.floor(x1 - x0)), "y_in": int(math.floor(y1 - y0))}


def _scale_bars(page: Page, margin: float):
    w, h = page.width, page.height
    x0, y0, _, _ = content_box(w, h, margin)
    bars = scale_bar_lengths(w, h, margin)
    lx, ly = bars["x_in"], bars["y_in"]
    every = 1 if lx <= 12 else 2 if lx <= 30 else 5
    by = h - margin - BOTTOM_BAND + 0.35
    page.rect(x0, by, lx, TICK_W)
    for i in range(lx + 1):
        tall = 0.18 if i in (0, lx) else 0.1
        page.rect(x0 + i - TICK_W / 2, by - tall, TICK_W, tall)
        if i in (0, lx) or i % every == 0:
            page.text(x0 + i - 0.04, by - 0.2, 6, str(i))
    page.text(x0, by + 0.22, 8,
              f"PRINT AT 100% / ACTUAL SIZE. Measure bar 0 to {lx} in (horizontal) and 0 to {ly} in "
              f"(vertical, left); enter both on the Settings page.")
    every = 1 if ly <= 12 else 2 if ly <= 30 else 5
    bx = margin + 0.3
    page.rect(bx, y0, TICK_W, ly)
    for i in range(ly + 1):
        tall = 0.18 if i in (0, ly) else 0.1
        page.rect(bx, y0 + i - TICK_W / 2, tall, TICK_W)
        if i in (0, ly) or i % every == 0:
            page.text(bx - 0.25, y0 + i + 0.03, 6, str(i))


def _marker_cells(page: Page, mid: int, x: float, y: float, side: float):
    """Black cells of marker `mid` (with its 1-cell border), upright, top-left at (x, y)."""
    nb = markers.default().n + 2
    bits = cv2.aruco.generateImageMarker(dictionary(), int(mid), nb, borderBits=1)
    cell = side / nb
    black = bits < 128
    for r in range(nb):
        row = black[r]
        c = 0
        while c < nb:
            if not row[c]:
                c += 1
                continue
            c0 = c
            while c < nb and row[c]:
                c += 1
            # overlap into a black row below, so viewers and printers show no hairline seam
            down = OVERLAP if r + 1 < nb and black[r + 1, c0:c].all() else 0.0
            page.rect(x + c0 * cell, y + r * cell, (c - c0) * cell, cell + down)


def _draw_segment(page: Page, seg: Segment):
    x, y = seg.x_in, seg.y_in
    if seg.is_row:
        for k, mid in enumerate(seg.ids):
            _marker_cells(page, mid, x + k * seg.square_in, y, seg.marker_in)
        page.text(x, y + seg.marker_in + 0.22, 9, f"^ TOP ^   {seg.serial}   {seg.describe()}")
        return
    board = charuco_board(seg)
    obj = [np.array(c, dtype=np.float64).reshape(4, 3) for c in board.getObjPoints()]
    ids = np.array(board.getIds()).ravel()
    sq = seg.square_in
    white = {(int(c[:, 0].mean() // sq), int(c[:, 1].mean() // sq)) for c in obj}
    for j in range(seg.squares_y):
        i = 0
        while i < seg.squares_x:
            if (i, j) in white:
                i += 1
                continue
            i0 = i
            while i < seg.squares_x and (i, j) not in white:
                i += 1
            page.rect(x + i0 * sq, y + j * sq, (i - i0) * sq, sq)
    for c, mid in zip(obj, ids):
        _marker_cells(page, int(mid), x + c[0, 0], y + c[0, 1], c[1, 0] - c[0, 0])
    if seg.kind == "sizetest":
        page.text(x, y - 0.1, 8, f"{fmt_length(sq)} squares   {seg.serial}")
    else:
        what = "CALIBRATION BOARD " if seg.kind == "calib" else "TOP ^   "
        page.text(x, y - 0.1, 9, f"{what}{seg.serial}   {seg.describe()}")


def render(rec: dict) -> list[Page]:
    """Pages of a print from its record (segment positions are stored in it)."""
    w, h, margin = rec["page_w_in"], rec["page_h_in"], rec["margin_in"]
    pages = [Page(w, h) for _ in range(rec["pages"])]
    for k, page in enumerate(pages):
        _scale_bars(page, margin)
        page.text(margin + LEFT_BAND, margin + 0.22, 9,
                  f"stockscan {ITEMS[rec['item']].upper()}  {rec['serial']}  page {k + 1}/{len(pages)}  {DICT_NAME}")
    for s in rec["segments"]:
        _draw_segment(pages[s["page"]], Segment.from_dict(s))
    return pages


pages_for_print = render


def _pack_pages(blocks: list[tuple[float, float]], W: float, H: float, gap: float = GAP):
    """Row packing over as many pages as needed: [(page, x, y)] for (w, h) blocks."""
    out = []
    page, x, y, row_h = 0, 0.0, 0.0, 0.0
    for w, h in blocks:
        if w > W + 1e-9 or h > H + 1e-9:
            return None
        if x > 0 and x + w > W + 1e-9:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        if y + h > H + 1e-9:
            page, x, y, row_h = page + 1, 0.0, 0.0, 0.0
        out.append((page, x, y))
        x += w + gap
        row_h = max(row_h, h)
    return out


def _place(page_size, margin, blocks, prefer_landscape):
    """Choose the orientation needing fewer pages; returns (w, h, positions, n_pages)."""
    best = None
    for land in ((True, False) if prefer_landscape else (False, True)):
        w, h = (page_size[1], page_size[0]) if land else page_size
        x0, y0, x1, y1 = content_box(w, h, margin)
        pos = _pack_pages(blocks, x1 - x0, y1 - y0)
        if pos is None:
            continue
        n = pos[-1][0] + 1 if pos else 1
        if best is None or n < best[3]:
            best = (w, h, [(p, x0 + x, y0 + y) for p, x, y in pos], n)
    if best is None:
        raise ValueError("does not fit on this page size; use a larger page or a smaller size")
    return best


def _fit_squares(page_size, margin, sq, rows, cols, extra_h=LABEL_H):
    """Squares across/down for a board filling the page (or the given rows/cols)."""
    best = None
    for land in (False, True):
        w, h = (page_size[1], page_size[0]) if land else page_size
        x0, y0, x1, y1 = content_box(w, h, margin)
        nx = cols or int((x1 - x0 + 1e-9) // sq)
        ny = rows or int((y1 - y0 - extra_h + 1e-9) // sq)
        if nx * sq <= x1 - x0 + 1e-9 and ny * sq + extra_h <= y1 - y0 + 1e-9:
            if best is None or nx * ny > best[0] * best[1]:
                best = (nx, ny)
    if best is None:
        raise ValueError("board does not fit on this page size")
    return best


def generate(registry: Registry, out_dir: Path, item: str, page_size: str = "letter",
             margin: float = 0.5, square=None, count: int = 1, rows: int | None = None,
             cols: int | None = None, gap=None, sizes=None, marker_ratio: float = MARKER_RATIO,
             first_serial: int | None = None) -> dict:
    """Lay out, register and write one print (PDF + geometry YAML). Returns the print record.

    square: ChArUco square size (board, calib) or marker size (front), with unit.
    gap: space between front-row markers.  sizes: size-test square sizes.
    rows / cols: board size in squares, or markers per front row (cols); default: fill the page.
    """
    if item not in ITEMS:
        raise ValueError(f"unknown target item {item!r}")
    size = parse_page_size(page_size)
    margin = float(margin)
    if not (0.0 <= margin <= 3.0):
        raise ValueError("margin must be between 0 and 3 in")
    if not (1 <= int(count) <= 200):
        raise ValueError("count must be 1 to 200")
    count = int(count)
    rows = int(rows) if rows else None
    cols = int(cols) if cols else None
    if not (0.3 <= marker_ratio <= 0.9):
        raise ValueError("marker ratio must be 0.3 to 0.9")
    if first_serial is not None:
        registry.set_first_serial(first_serial)

    segs: list[Segment] = []
    if item in ("board", "calib"):
        sq = parse_length(square if square is not None else ("10cm" if item == "board" else "1.2in"))
        nx, ny = _fit_squares(size, margin, sq, rows, cols)
        if item == "calib":
            while nx * ny // 2 > CALIB_MAX_MARKERS:
                nx, ny = (nx - 1, ny) if nx >= ny else (nx, ny - 1)
        if nx < 2 or ny < 2:
            raise ValueError("a ChArUco board needs at least 2 x 2 squares; use a smaller square size")
        blocks = [(nx * sq, ny * sq + LABEL_H)] * count
        w, h, pos, n_pages = _place(size, margin, blocks, prefer_landscape=False)
        need = (nx * ny // 2) * count
        if need > registry.ids_left():
            raise ValueError(f"not enough marker IDs: need {need}, {registry.ids_left()} left")
        for (pg, x, y) in pos:
            segs.append(Segment(registry._new_serial(), item, nx, ny, sq, round(sq * marker_ratio, 6),
                                registry._take_ids(nx * ny // 2), "", pg, x, y + LABEL_H))
    elif item == "front":
        m = parse_length(square if square is not None else "8cm")
        g = parse_length(gap if gap is not None else "2cm")
        pitch = m + g
        best = None
        for land in (True, False):
            w_, h_ = (size[1], size[0]) if land else size
            x0, _, x1, _ = content_box(w_, h_, margin)
            n = cols or int((x1 - x0 + g + 1e-9) // pitch)
            if best is None or n > best:
                best = n
        n = best
        if n < 2:
            raise ValueError("fewer than 2 markers fit in a row; use a larger page or smaller markers")
        blocks = [(n * pitch - g, m + LABEL_H + 0.05)] * count
        w, h, pos, n_pages = _place(size, margin, blocks, prefer_landscape=True)
        if n * count > registry.ids_left():
            raise ValueError(f"not enough marker IDs: need {n * count}, {registry.ids_left()} left")
        for (pg, x, y) in pos:
            segs.append(Segment(registry._new_serial(), "front", n, 1, pitch, m, registry._take_ids(n),
                                "", pg, x, y, layout="row"))
    else:  # sizetest
        sx, sy = SIZETEST_SQUARES
        sq_list = parse_lengths(sizes if sizes is not None else "2cm 3cm 4cm 5cm 7cm")
        if not sq_list:
            raise ValueError("give at least one size-test square size")
        sq_list = sorted(sq_list, reverse=True)
        blocks = [(sx * s, sy * s + LABEL_H) for s in sq_list]
        w, h, pos, n_pages = _place(size, margin, blocks, prefer_landscape=False)
        for s, (pg, x, y) in zip(sq_list, pos):
            segs.append(Segment(registry._new_serial(), "sizetest", sx, sy, s, round(s * marker_ratio, 6),
                                registry._take_ids(sx * sy // 2), "", pg, x, y + LABEL_H))

    pserial = registry._new_print()
    for s in segs:
        s.print_serial = pserial
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{pserial}-{item}-{str(page_size).lower().replace(' ', '')}"
    name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
    rec = {
        "serial": pserial,
        "item": item,
        "page_size": str(page_size),
        "page_w_in": round(w, 6),
        "page_h_in": round(h, 6),
        "margin_in": margin,
        "pages": n_pages,
        "scale_bar_in": scale_bar_lengths(w, h, margin),
        "dictionary": DICT_NAME,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "file": f"{name}.pdf",
        "segments": [s.to_dict() for s in segs],
    }
    write_pdf(render(rec), out_dir / rec["file"])
    with open(out_dir / f"{name}.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(rec, f, sort_keys=False)
    registry.prints.append(rec)
    registry.save()
    return rec


# ---------------------------------------------------------------- output


def _pdf_escape(s: str) -> str:
    s = s.encode("ascii", "replace").decode("ascii")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(pages: list[Page], path: Path):
    """Minimal vector PDF: filled rectangles, lines, Helvetica text."""
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    catalog = add(b"")  # placeholder
    pages_obj = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    kids = []
    for p in pages:
        H = p.height * 72
        ops = ["0 g 0 G"]
        rects = []
        for prim in p.prims:
            if prim[0] == "rect":
                _, x, y, w, h = prim
                rects.append(f"{x * 72:.4f} {H - (y + h) * 72:.4f} {w * 72:.4f} {h * 72:.4f} re")
        for i in range(0, len(rects), 200):
            ops.append("\n".join(rects[i:i + 200]) + "\nf")
        for prim in p.prims:
            if prim[0] == "line":
                _, x1, y1, x2, y2, wpt = prim
                ops.append(f"{wpt:.3f} w {x1 * 72:.4f} {H - y1 * 72:.4f} m {x2 * 72:.4f} {H - y2 * 72:.4f} l S")
            elif prim[0] == "text":
                _, x, y, size, s = prim
                ops.append(f"BT /F1 {size} Tf {x * 72:.3f} {H - y * 72:.3f} Td ({_pdf_escape(s)}) Tj ET")
        data = zlib.compress("\n".join(ops).encode("ascii"))
        content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(add(
            (f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox [0 0 {p.width * 72:.4f} {H:.4f}] "
             f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>").encode("ascii")
        ))
    objs[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode("ascii")
    objs[pages_obj - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    ).encode("ascii")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, b in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + b + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii")
    for o in offsets:
        out += f"{o:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    path.write_bytes(bytes(out))


def rasterize(page: Page, dpi: float = 200, text: bool = True) -> np.ndarray:
    """Render a page to a uint8 grayscale image (white paper, black ink)."""
    W, H = int(round(page.width * dpi)), int(round(page.height * dpi))
    img = np.full((H, W), 255, np.uint8)
    for prim in page.prims:
        if prim[0] == "rect":
            _, x, y, w, h = prim
            x0, y0 = int(round(x * dpi)), int(round(y * dpi))
            x1, y1 = int(round((x + w) * dpi)), int(round((y + h) * dpi))
            img[max(y0, 0):max(y1, y0 + 1), max(x0, 0):max(x1, x0 + 1)] = 0
        elif prim[0] == "line":
            _, x1, y1, x2, y2, wpt = prim
            t = max(1, int(round(wpt / 72 * dpi)))
            cv2.line(img, (int(x1 * dpi), int(y1 * dpi)), (int(x2 * dpi), int(y2 * dpi)), 0, t)
        elif prim[0] == "text" and text:
            _, x, y, size, s = prim
            scale = size / 72 * dpi / 22
            cv2.putText(img, s.encode("ascii", "replace").decode(), (int(x * dpi), int(y * dpi)),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, 0, max(1, int(scale * 1.5)), cv2.LINE_AA)
    return img
