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
