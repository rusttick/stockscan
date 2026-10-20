"""ChArUco camera calibration, coverage map, calibration files."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml

MIN_CORNERS = 12


@dataclass
class Calibration:
    camera_key: str
    width: int
    height: int
    K: np.ndarray
    dist: np.ndarray
    rms_px: float
    n_images: int
    source_set: str = ""
    created: str = ""
    file: str = ""

    @property
    def fx(self) -> float:
        return float(self.K[0, 0])

    def to_dict(self) -> dict:
        return {
            "camera_key": self.camera_key,
            "width": self.width,
            "height": self.height,
            "K": np.round(self.K, 6).tolist(),
            "dist": np.round(self.dist.ravel(), 8).tolist(),
            "rms_px": round(self.rms_px, 4),
            "n_images": self.n_images,
            "source_set": self.source_set,
            "created": self.created,
        }

    @classmethod
    def from_dict(cls, d: dict, file: str = "") -> "Calibration":
        return cls(d["camera_key"], int(d["width"]), int(d["height"]), np.array(d["K"], float),
                   np.array(d["dist"], float), float(d["rms_px"]), int(d["n_images"]),
                   d.get("source_set", ""), d.get("created", ""), file)


@dataclass
class CalibResult:
    K: np.ndarray
    dist: np.ndarray
    rms_px: float
    per_view_px: list[float]
    used: list[int]  # indices of the views used


def calibrate(views: list[tuple[np.ndarray, np.ndarray]], image_size: tuple[int, int]) -> CalibResult:
    """views: (object points (N, 3), image points (N, 2)) per photo; image_size: (w, h)."""
    used = [i for i, (o, _) in enumerate(views) if len(o) >= MIN_CORNERS]
    if len(used) < 3:
        raise ValueError(f"need at least 3 photos with {MIN_CORNERS}+ corners, have {len(used)}")
    objs = [np.asarray(views[i][0], np.float32).reshape(-1, 1, 3) for i in used]
    imgs = [np.asarray(views[i][1], np.float32).reshape(-1, 1, 2) for i in used]
    rms, K, dist, _, _, _, _, per_view = cv2.calibrateCameraExtended(objs, imgs, tuple(image_size), None, None)
    return CalibResult(K, dist.ravel(), float(rms), [float(e) for e in np.ravel(per_view)], used)


def coverage(points: list[np.ndarray], image_size: tuple[int, int], grid=(8, 6)) -> np.ndarray:
    """Corner counts per cell of a grid over the image (rows, cols)."""
    w, h = image_size
    cnt = np.zeros((grid[1], grid[0]), int)
    for p in points:
        p = np.asarray(p).reshape(-1, 2)
        cx = np.clip((p[:, 0] / w * grid[0]).astype(int), 0, grid[0] - 1)
        cy = np.clip((p[:, 1] / h * grid[1]).astype(int), 0, grid[1] - 1)
        np.add.at(cnt, (cy, cx), 1)
    return cnt


def safe_key(key: str) -> str:
    out = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")[:80]


def save(cal: Calibration, cameras_dir: Path) -> Path:
    cameras_dir.mkdir(parents=True, exist_ok=True)
    if not cal.created:
        cal.created = _dt.datetime.now().isoformat(timespec="seconds")
    stamp = cal.created.replace(":", "").replace("-", "").replace("T", "-")[:15]
    path = cameras_dir / f"{safe_key(cal.camera_key)}-{stamp}.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cal.to_dict(), f, sort_keys=False)
    cal.file = path.name
    return path


def load_all(cameras_dir: Path) -> list[Calibration]:
    out = []
    if not cameras_dir.exists():
        return out
    for p in sorted(cameras_dir.glob("*.yaml")):
        try:
            with open(p, encoding="utf-8") as f:
                out.append(Calibration.from_dict(yaml.safe_load(f), p.name))
        except Exception:
            continue
    out.sort(key=lambda c: c.created)
    return out


def find(cameras_dir: Path, camera_key: str) -> Calibration | None:
    """Newest calibration for exactly this camera / lens / image size."""
    match = [c for c in load_all(cameras_dir) if c.camera_key == camera_key]
    return match[-1] if match else None


# ---------------------------------------------------------------- tolerant matching
#
# Phones and transfers do things to photos.  A calibration is still usable when
# the photo was only scaled (shared / "small" sizes), rotated without keeping
# the EXIF orientation (some re-encoders), or digitally zoomed (a centred crop
# scaled back up).  Each case maps the calibrated camera exactly to the photo.
# A different lens (macro / wide switching) or a crop of another shape cannot
# be mapped and is not matched.


def key_parts(key: str) -> tuple[str, str, tuple[int, int] | None]:
    parts = [p.strip() for p in key.split("|")]
    size = None
    if len(parts) == 3 and "x" in parts[2]:
        try:
            w, h = parts[2].split("x")
            size = (int(w), int(h))
        except ValueError:
            pass
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "", size)


def scale_K(K: np.ndarray, s: float) -> np.ndarray:
    """Camera matrix for the image resized by factor s (pixel centres preserved)."""
    K2 = K.astype(np.float64).copy()
    K2[0, 0] *= s
    K2[1, 1] *= s
    K2[0, 1] *= s
    K2[0, 2] = (K[0, 2] + 0.5) * s - 0.5
    K2[1, 2] = (K[1, 2] + 0.5) * s - 0.5
    return K2


def rotate_K(K: np.ndarray, dist: np.ndarray, size: tuple[int, int], k: int):
    """Camera for the image rotated like np.rot90(img, k) (k quarter turns
    counter-clockwise).  size is (w, h) before rotation.
    Returns (K, dist, new size)."""
    K2 = K.astype(np.float64).copy()
    d = np.zeros(5)
    dd = np.asarray(dist, np.float64).ravel()[:5]
    d[:len(dd)] = dd
    w, h = size
    for _ in range(k % 4):
        # pixel (u, v) -> (v, w - 1 - u); normalized (x, y) -> (y, -x)
        fx, fy, cx, cy = K2[0, 0], K2[1, 1], K2[0, 2], K2[1, 2]
        K2 = np.array([[fy, 0, cy], [0, fx, w - 1 - cx], [0, 0, 1]], np.float64)
        d = np.array([d[0], d[1], -d[3], d[2], d[4]])  # p1' = -p2, p2' = p1
        w, h = h, w
    return K2, d, (w, h)


def zoom_K(K: np.ndarray, size: tuple[int, int], z: float) -> np.ndarray:
    """Camera for a centred digital zoom by z (crop 1/z, scaled back to full size)."""
    w, h = size
    K2 = K.astype(np.float64).copy()
    K2[0, 0] *= z
    K2[1, 1] *= z
    K2[0, 1] *= z
    K2[0, 2] = (K[0, 2] - (w - 1) / 2) * z + (w - 1) / 2
    K2[1, 2] = (K[1, 2] - (h - 1) / 2) * z + (h - 1) / 2
    return K2


@dataclass
class Match:
    cal: Calibration
    candidates: list  # [(K, dist, label)]; more than one when the rotation direction is unknown
    how: str  # "exact", "scaled x0.5", "rotated 90 deg", "digital zoom 2x", "set camera hint", ...

    @property
    def exact(self) -> bool:
        return self.how == "exact"


def _same_shape(a: tuple[int, int], b: tuple[int, int], tol: float = 0.005) -> bool:
    return abs(a[0] * b[1] - a[1] * b[0]) <= tol * a[0] * b[1]


def adapt(cal: Calibration, size: tuple[int, int], zoom: float | None = None) -> tuple[list, str] | None:
    """Map a calibration onto a photo of this stored size; None if impossible."""
    W, H = size
    cw, ch = cal.width, cal.height
    notes = []
    if _same_shape((W, H), (cw, ch)):
        s = W / cw
        cands = [(scale_K(cal.K, s) if abs(s - 1) > 1e-9 else cal.K.copy(), np.asarray(cal.dist, float).ravel(), "")]
        if abs(s - 1) > 1e-9:
            notes.append(f"scaled x{s:.3g}")
    elif _same_shape((W, H), (ch, cw)):
        s = W / ch
        cands = []
        for k, label in ((1, "rotated 90 deg"), (3, "rotated 270 deg")):
            K2, d2, _ = rotate_K(cal.K, cal.dist, (cw, ch), k)
            cands.append((scale_K(K2, s) if abs(s - 1) > 1e-9 else K2, d2, label))
        notes.append("pixels rotated without EXIF orientation")
        if abs(s - 1) > 1e-9:
            notes.append(f"scaled x{s:.3g}")
    else:
        return None
    if zoom and zoom > 1.001:
        cands = [(zoom_K(K, (W, H), zoom), d, lab) for K, d, lab in cands]
        notes.append(f"digital zoom {zoom:g}x")
    return cands, (", ".join(notes) or "exact")


def match(cameras_dir: Path, meta, hint: str | None = None, cals: list[Calibration] | None = None) -> Match | None:
    """Best calibration for a photo (imageio.PhotoMeta), adapted to it.

    Same camera and lens are required (from EXIF).  Photos without camera
    information use the set's camera hint (a calibration file name), if given.
    Prefers the exact image size, then the newest.
    """
    cals = load_all(cameras_dir) if cals is None else cals
    zoom = getattr(meta, "zoom", None)
    size = (meta.width, meta.height)
    if meta.camera == "unknown camera":
        pool = [c for c in cals if hint and c.file == hint]
        prefix = "set camera hint"
    else:
        pool = [c for c in cals if key_parts(c.camera_key)[:2] == (meta.camera, meta.lens_name)]
        prefix = ""
    pool.sort(key=lambda c: ((c.width, c.height) == size, c.created), reverse=True)
    for c in pool:
        r = adapt(c, size, zoom)
        if r is not None:
            cands, how = r
            if prefix:
                how = prefix if how == "exact" else f"{prefix}, {how}"
            return Match(c, cands, how)
    return None
