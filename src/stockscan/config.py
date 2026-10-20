"""Site settings (DATA/site.yaml).

The site file is kept at the site only.  It holds print-scale measurements,
segment labels and processing settings; the repository ships only
examples/site.example.yaml.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULTS = {
    "px_per_in": 100.0,  # scale of straight-on images
    "face_offset_in": 0.0,  # stock ends stand this far in front of the front marker row
    "above_in": 10.0,  # straight-on area above the front marker row
    "upload_limit_mb": 100,
    "blur_threshold": 60.0,  # sharpness below this is reported as blurry
    "print_scales": {},  # print serial -> {x_measured: in, y_measured: in}
    "segment_labels": {},  # segment serial -> free text
    # default target sizes on the Targets page; any length with in, cm or mm
    "target_sizes": {
        "front_marker": "8cm",  # marker side in the front row (beam face)
        "front_gap": "2cm",  # space between front-row markers
        "board_square": "10cm",  # ChArUco square on surfaces, uprights, panels
        "calib_square": "1.2in",  # calibration board square
        "sizetest_squares": "2cm 3cm 4cm 5cm 7cm",
        "marker_ratio": 0.75,  # ChArUco marker side / square side
    },
}


class Site:
    def __init__(self, path: Path):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            for k, v in loaded.items():
                self.data[k] = v
        for k in ("print_scales", "segment_labels"):
            if not isinstance(self.data.get(k), dict):
                self.data[k] = {}
        sizes = copy.deepcopy(DEFAULTS["target_sizes"])
        if isinstance(self.data.get("target_sizes"), dict):
            sizes.update(self.data["target_sizes"])
        self.data["target_sizes"] = sizes

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, sort_keys=False)
        tmp.replace(self.path)

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value

    def print_scale(self, print_rec: dict | None) -> tuple[float, float]:
        """(x, y) factors: measured / nominal scale-bar length for a print."""
        if not print_rec:
            return (1.0, 1.0)
        m = self.data["print_scales"].get(print_rec["serial"]) or {}
        nom = print_rec.get("scale_bar_in") or {}
        sx = sy = 1.0
        try:
            if m.get("x_measured") and nom.get("x_in"):
                sx = float(m["x_measured"]) / float(nom["x_in"])
            if m.get("y_measured") and nom.get("y_in"):
                sy = float(m["y_measured"]) / float(nom["y_in"])
            elif m.get("x_measured"):
                sy = sx
            if not m.get("x_measured") and m.get("y_measured"):
                sx = sy
        except (TypeError, ValueError, ZeroDivisionError):
            return (1.0, 1.0)
        return (sx, sy)

    def label(self, serial: str) -> str:
        return str(self.data["segment_labels"].get(serial, ""))
