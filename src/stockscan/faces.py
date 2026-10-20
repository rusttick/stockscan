"""Automatic end-face detection in the straight-on image (shape only, never color).

1. Shelf line: the strongest horizontal edge in a band above the strip.
2. Side boundaries / seams: peaks of the horizontal gradient in a thin band
   just above the shelf line, where every face is present.
3. Top edge: between neighbouring boundaries, the strongest vertical-gradient
   edge in each column; the median over columns, which must agree.
4. Faces smaller than the minimum trackable size are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .rectify import Rectified


@dataclass
class Params:
    shelf_search_in: tuple[float, float] = (-3.0, -0.3)  # y band searched for the shelf line
    seam_band_in: tuple[float, float] = (0.06, 0.3)  # above the shelf line
    min_size_in: float = 0.2
    max_height_in: float = 12.0
    min_boundary_gap_in: float = 0.12
    top_tolerance_in: float = 0.06  # max spread of per-column top edges
    smooth_px: float = 1.2


@dataclass
class Face:
    x0: float  # plane inches
    x1: float
    y_top: float
    y_bottom: float
    spread_in: float
    strength: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y_bottom - self.y_top


@dataclass
class Candidate:
    x0: float
    x1: float
    reason: str
    y_top: float | None = None


@dataclass
class Result:
    shelf_y: float | None
    boundaries: list[float] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)
    rejected: list[Candidate] = field(default_factory=list)


def _peaks(p: np.ndarray, min_dist: int, thresh: float) -> list[float]:
    """Local maxima above thresh, at least min_dist apart, with sub-pixel refinement."""
    order = np.argsort(p)[::-1]
    taken = np.zeros(len(p), bool)
    out = []
    for i in order:
        if p[i] < thresh:
            break
        if taken[max(0, i - min_dist):i + min_dist + 1].any():
            continue
        if 0 < i < len(p) - 1 and p[i] >= p[i - 1] and p[i] >= p[i + 1]:
            a, b, c = p[i - 1], p[i], p[i + 1]
            den = a - 2 * b + c
            off = 0.5 * (a - c) / den if den != 0 else 0.0
            out.append(i + float(np.clip(off, -0.5, 0.5)))
            taken[i] = True
    return sorted(out)


def _merge_seams(cols: list[float], signed: np.ndarray, prof: np.ndarray, ppi: float, P: Params) -> list[float]:
    """A falling edge followed closely by a rising edge is a dark seam between
    two touching pieces: replace the pair by its centre.  Then drop boundaries
    closer than min_boundary_gap_in, keeping the stronger."""
    out, i = [], 0
    while i < len(cols):
        a = cols[i]
        if i + 1 < len(cols):
            b = cols[i + 1]
            if (b - a) <= 0.1 * ppi and signed[int(round(a))] < 0 < signed[int(round(b))]:
                out.append((a + b) / 2)
                i += 2
                continue
        out.append(a)
        i += 1
    min_gap = P.min_boundary_gap_in * ppi
    kept: list[float] = []
    for c in out:
        if kept and c - kept[-1] < min_gap:
            if prof[int(round(c))] > prof[int(round(kept[-1]))]:
                kept[-1] = c
            continue
        kept.append(c)
    return kept


def _subpix_argmax(col: np.ndarray) -> tuple[float, float]:
    i = int(np.argmax(col))
    if 0 < i < len(col) - 1:
        a, b, c = col[i - 1], col[i], col[i + 1]
        den = a - 2 * b + c
        off = 0.5 * (a - c) / den if den != 0 else 0.0
        return i + float(np.clip(off, -0.5, 0.5)), float(b)
    return float(i), float(col[i])


def detect(rect: Rectified, params: Params | None = None) -> Result:
    P = params or Params()
    img = rect.image.astype(np.float32)
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    img = cv2.GaussianBlur(img, (0, 0), P.smooth_px)
    ppi = rect.ppi
    gy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3) / 8
    gx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3) / 8
    H, W = img.shape
    valid = rect.image.reshape(H, W, -1)[..., 0] > 0  # outside the photo is black

    def row_of(y):
        return int(round((y - rect.y0) * ppi - 0.5))

    # 1. shelf line
    r0, r1 = max(1, row_of(P.shelf_search_in[0])), min(H - 2, row_of(P.shelf_search_in[1]))
    if r1 <= r0:
        return Result(None)
    band = np.abs(gy[r0:r1])
    score = np.percentile(band, 75, axis=1)
    shelf_row, _ = _subpix_argmax(score)
    shelf_row += r0
    shelf_y = rect.y0 + (shelf_row + 0.5) / ppi
    res = Result(shelf_y)

    # 2. boundaries from the horizontal gradient just above the shelf line
    s0 = max(0, int(round(shelf_row - P.seam_band_in[1] * ppi)))
    s1 = max(s0 + 1, int(round(shelf_row - P.seam_band_in[0] * ppi)))
    signed = gx[s0:s1].mean(axis=0)
    prof = np.abs(gx[s0:s1]).mean(axis=0)
    cover = valid[s0:s1].all(axis=0)
    prof[~cover] = 0
    noise = np.median(prof) + 1e-3
    thresh = max(4 * noise, 0.15 * np.percentile(prof, 99.5))
    cols = _merge_seams(_peaks(prof, max(2, int(0.025 * ppi)), thresh), signed, prof, ppi, P)
    res.boundaries = [rect.x0 + (c + 0.5) / ppi for c in cols]

    # 3. top edge between neighbouring boundaries
    top_lo = max(0, int(round(shelf_row - P.max_height_in * ppi)))
    for a, b in zip(cols[:-1], cols[1:]):
        xa, xb = rect.x0 + (a + 0.5) / ppi, rect.x0 + (b + 0.5) / ppi
        if xb - xa < P.min_size_in:
            res.rejected.append(Candidate(xa, xb, "too narrow"))
            continue
        ca = int(np.ceil(a + 0.2 * (b - a)))
        cb = int(np.floor(b - 0.2 * (b - a)))
        if cb <= ca:
            cb = ca + 1
        stop = int(round(shelf_row - P.min_size_in * 0.5 * ppi))
        if stop <= top_lo + 2:
            res.rejected.append(Candidate(xa, xb, "no room above shelf"))
            continue
        tops, strengths = [], []
        for c in range(ca, cb):
            col = np.abs(gy[top_lo:stop, c])
            r, s = _subpix_argmax(col)
            tops.append(top_lo + r)
            strengths.append(s)
        tops, strengths = np.array(tops), np.array(strengths)
        top = float(np.median(tops))
        spread = float(np.percentile(np.abs(tops - top), 80)) / ppi
        strength = float(np.median(strengths))
        y_top = rect.y0 + (top + 0.5) / ppi
        if strength < thresh:
            res.rejected.append(Candidate(xa, xb, "no top edge", y_top))
        elif spread > P.top_tolerance_in:
            res.rejected.append(Candidate(xa, xb, f"top edge inconsistent ({spread:.2f} in)", y_top))
        elif shelf_y - y_top < P.min_size_in:
            res.rejected.append(Candidate(xa, xb, "too short", y_top))
        else:
            res.faces.append(Face(xa, xb, y_top, shelf_y, spread, strength))
    return res


def near_standard(v: float, step: float = 0.125) -> float:
    return round(v / step) * step


def draw(rect: Rectified, res: Result) -> np.ndarray:
    img = rect.image
    out = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img.copy()
    ppi = rect.ppi

    def px(x, y):
        c = rect.to_px(np.array([[x, y]]))[0]
        return int(round(c[0])), int(round(c[1]))

    H = out.shape[0]
    if res.shelf_y is not None:
        _, r = px(0, res.shelf_y)
        cv2.line(out, (0, r), (out.shape[1] - 1, r), (255, 160, 0), 1)
    for x in res.boundaries:
        c, _ = px(x, 0)
        cv2.line(out, (c, 0), (c, H - 1), (200, 200, 0), 1)
    for cand in res.rejected:
        if cand.y_top is not None and res.shelf_y is not None:
            cv2.rectangle(out, px(cand.x0, cand.y_top), px(cand.x1, res.shelf_y), (0, 0, 255), 1)
    for k, f in enumerate(res.faces, 1):
        cv2.rectangle(out, px(f.x0, f.y_top), px(f.x1, f.y_bottom), (0, 220, 0), 2)
        org = px(f.x0, f.y_top - 0.08)
        cv2.putText(out, f"{k}: {f.width:.2f}x{f.height:.2f}", org, cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.4, ppi / 250), (0, 160, 255), 1, cv2.LINE_AA)
    return out
