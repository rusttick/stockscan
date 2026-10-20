"""Marker and ChArUco detection, grouped into printed segments."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from . import markers, targets
from .targets import Segment


@dataclass
class SegmentDetection:
    segment: Segment
    marker_ids: np.ndarray  # (M,)
    marker_corners: list  # M arrays of (4, 2)
    corner_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))  # (N,)
    corners: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))  # (N, 2) px

    @property
    def n_markers(self) -> int:
        return len(self.marker_ids)

    @property
    def n_corners(self) -> int:
        return len(self.corner_ids)


@dataclass
class Detection:
    n_markers: int
    unknown_ids: list[int]
    segments: dict[str, SegmentDetection]  # by serial

    def by_kind(self, kind: str) -> list[SegmentDetection]:
        return [d for d in self.segments.values() if d.segment.kind == kind]


def detector_parameters():
    p = cv2.aruco.DetectorParameters()
    p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    p.cornerRefinementWinSize = 5
    # accept small markers; the size test finds the real limit
    p.minMarkerPerimeterRate = 0.01
    # the dictionary carries maxCorrectionBits = markers.CORRECTION_BITS; use all of it
    p.errorCorrectionRate = 1.0
    return p


def detect_markers_opencv(gray: np.ndarray):
    """OpenCV's own pipeline with the full dictionary (reference; slow with 20 000 IDs)."""
    det = cv2.aruco.ArucoDetector(targets.dictionary(), detector_parameters())
    corners, ids, _ = det.detectMarkers(gray)
    if ids is None:
        return [], np.zeros(0, np.int32)
    return [c.reshape(4, 2) for c in corners], ids.ravel().astype(np.int32)


# OpenCV decodes each candidate by comparing it with every marker in turn, which
# takes ~30 ms per candidate with 20 000 markers.  Instead OpenCV only finds the
# candidate quads (using a one-marker probe dictionary, so every quad comes back
# as "rejected"); the cells are sampled exactly as OpenCV's _extractBits does and
# all candidates are decoded at once against all rotations of all markers.
# The decision rule is the same: the marker within CORRECTION_BITS cells, unique
# because the dictionary's minimum distance is > 2 * CORRECTION_BITS.

_probe = None


def _probe_detector():
    global _probe
    if _probe is None:
        n = markers.default().n
        rng = np.random.default_rng(12345)
        bits = (rng.random((n, n)) < 0.5).astype(np.uint8)
        d = cv2.aruco.Dictionary(cv2.aruco.Dictionary.getByteListFromBits(bits), n, 0)
        p = detector_parameters()
        p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_NONE
        # report every candidate: OpenCV otherwise groups close candidates (inner and
        # outer border contours) and tries the alternatives itself when one fails
        p.minMarkerDistanceRate = 0.0
        _probe = cv2.aruco.ArucoDetector(d, p)
    return _probe


def extract_bits(gray: np.ndarray, quads: np.ndarray, n: int, params=None) -> tuple[np.ndarray, np.ndarray]:
    """Sample (n+2) x (n+2) cells of each candidate quad (M, 4, 2) as OpenCV does.

    Returns (inner bits (M, n, n) uint8, border ok (M,) bool)."""
    p = params or detector_parameters()
    cell = p.perspectiveRemovePixelPerCell
    nb = n + 2
    S = nb * cell
    m = int(p.perspectiveRemoveIgnoredMarginPerCell * cell)
    dst = np.float32([[0, 0], [S - 1, 0], [S - 1, S - 1], [0, S - 1]])
    max_border_err = int(n * n * p.maxErroneousBitsInBorderRate)
    bits = np.zeros((len(quads), n, n), np.uint8)
    ok = np.zeros(len(quads), bool)
    for k, q in enumerate(quads):
        H = cv2.getPerspectiveTransform(np.asarray(q, np.float32), dst)
        w = cv2.warpPerspective(gray, H, (S, S), flags=cv2.INTER_NEAREST)
        inner = w[cell:S - cell, cell:S - cell]
        mean, std = cv2.meanStdDev(inner)
        if std[0, 0] < p.minOtsuStdDev:
            cells = np.full((nb, nb), mean[0, 0] > 127, np.uint8)
        else:
            _, th = cv2.threshold(w, 125, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            blocks = th.reshape(nb, cell, nb, cell)[:, m:cell - m, :, m:cell - m]
            cells = (np.count_nonzero(blocks, axis=(1, 3)) > (cell - 2 * m) ** 2 / 2).astype(np.uint8)
        border = np.concatenate([cells[0], cells[-1], cells[1:-1, 0], cells[1:-1, -1]])
        ok[k] = int(border.sum()) <= max_border_err
        bits[k] = cells[1:-1, 1:-1]
    return bits, ok


def detect_markers(gray: np.ndarray):
    """Markers of the shipped dictionary: (list of (4, 2) corners, ids)."""
    md = markers.default()
    params = detector_parameters()
    acc, _, rej = _probe_detector().detectMarkers(gray)
    quads = [np.asarray(c, np.float64).reshape(4, 2) for c in list(acc) + list(rej)]
    if not quads:
        return [], np.zeros(0, np.int32)
    quads = np.stack(quads)
    bits, ok = extract_bits(gray, quads, md.n, params)
    ids, rots = markers.decode(md, markers.pack(bits), markers.CORRECTION_BITS, markers.default_rotations())
    keep = ok & (ids >= 0)
    best: dict[int, tuple[float, np.ndarray]] = {}
    for q, i, r in zip(quads[keep], ids[keep], rots[keep]):
        # extracted = marker rotated r times (np.rot90): its top-left cell lies at quad corner -r
        c = np.roll(q, int(r), axis=0)
        area = abs(cv2.contourArea(c.astype(np.float32)))
        if int(i) not in best or area > best[int(i)][0]:
            best[int(i)] = (area, c)
    if not best:
        return [], np.zeros(0, np.int32)
    order = sorted(best)
    crit = (cv2.TERM_CRITERIA_MAX_ITER | cv2.TERM_CRITERIA_EPS, params.cornerRefinementMaxIterations,
            params.cornerRefinementMinAccuracy)
    out = []
    for i in order:
        c = best[i][1].astype(np.float32).reshape(4, 1, 2)
        cv2.cornerSubPix(gray, c, (_refine_win(c, md.n, params),) * 2, (-1, -1), crit)
        out.append(c.reshape(4, 2).astype(np.float64))
    return out, np.array(order, np.int32)


def _refine_win(c: np.ndarray, n: int, p) -> int:
    """OpenCV's sub-pixel window: min(cornerRefinementWinSize, module size * relative rate)."""
    q = c.reshape(4, 2)
    module = float(np.mean(np.linalg.norm(q - np.roll(q, 1, axis=0), axis=1))) / (n + 2)
    rel = getattr(p, "relativeCornerRefinmentWinSize", None)
    if rel is None:  # OpenCV before 4.9: fixed window
        return p.cornerRefinementWinSize
    return max(1, min(p.cornerRefinementWinSize, int(np.floor(module * rel + 0.5))))


def charuco_corners(gray: np.ndarray, seg: Segment, marker_corners, marker_ids,
                    K: np.ndarray | None = None, dist: np.ndarray | None = None):
    """Interpolate the ChArUco corners of one segment from its markers."""
    params = cv2.aruco.CharucoParameters()
    if K is not None:
        params.cameraMatrix = K
        params.distCoeffs = dist if dist is not None else np.zeros(5)
    params.minMarkers = 1
    cd = cv2.aruco.CharucoDetector(targets.charuco_board(seg), params, detector_parameters())
    mc = [c.reshape(1, 4, 2).astype(np.float32) for c in marker_corners]
    mi = np.asarray(marker_ids, np.int32).reshape(-1, 1)
    ccorners, cids, _, _ = cd.detectBoard(gray, markerCorners=mc, markerIds=mi)
    if cids is None or len(cids) == 0:
        return np.zeros(0, np.int32), np.zeros((0, 2))
    return cids.ravel().astype(np.int32), ccorners.reshape(-1, 2).astype(np.float64)


def detect(gray: np.ndarray, id_map: dict[int, Segment], K=None, dist=None,
           kinds: tuple[str, ...] | None = None) -> Detection:
    corners, ids = detect_markers(gray)
    groups: dict[str, SegmentDetection] = {}
    unknown = []
    for c, i in zip(corners, ids):
        seg = id_map.get(int(i))
        if seg is None:
            unknown.append(int(i))
            continue
        if kinds is not None and seg.kind not in kinds:
            continue
        g = groups.get(seg.serial)
        if g is None:
            g = groups[seg.serial] = SegmentDetection(seg, np.zeros(0, np.int32), [])
        g.marker_ids = np.append(g.marker_ids, i)
        g.marker_corners.append(c)
    for g in groups.values():
        if g.segment.is_row:
            g.corner_ids, g.corners = row_corners(g.segment, g.marker_corners, g.marker_ids)
        else:
            g.corner_ids, g.corners = charuco_corners(gray, g.segment, g.marker_corners, g.marker_ids, K, dist)
    return Detection(len(ids), sorted(set(unknown)), groups)


def row_corners(seg: Segment, marker_corners, marker_ids):
    """A marker row has no chessboard: its points are the (sub-pixel) marker corners."""
    if not len(marker_ids):
        return np.zeros(0, np.int32), np.zeros((0, 2))
    ids = np.concatenate([(int(m) - seg.first_id) * 4 + np.arange(4) for m in marker_ids]).astype(np.int32)
    pts = np.vstack([np.asarray(c, np.float64).reshape(4, 2) for c in marker_corners])
    return ids, pts


def draw(img_bgr: np.ndarray, det: Detection) -> np.ndarray:
    """Annotate markers (green) and ChArUco corners (red) with segment serials."""
    out = img_bgr.copy()
    lw = max(1, round(max(out.shape[:2]) / 1500))
    for g in det.segments.values():
        for c in g.marker_corners:
            cv2.polylines(out, [np.round(c).astype(np.int32)], True, (0, 200, 0), lw)
        for p in g.corners:
            cv2.circle(out, (int(round(p[0])), int(round(p[1]))), 2 * lw + 1, (0, 0, 255), lw)
        if g.marker_corners:
            allc = np.vstack(g.marker_corners)
            x, y = allc[:, 0].min(), allc[:, 1].min()
            cv2.putText(out, g.segment.serial, (int(x), max(15, int(y) - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6 * lw, (255, 0, 255), lw + 1, cv2.LINE_AA)
    return out
