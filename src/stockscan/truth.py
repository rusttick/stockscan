"""Hand measurements (truth.csv), alignment with measured faces, error statistics."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FIELDS = ["index", "width_in", "height_in", "length_in", "note"]


@dataclass
class TruthRow:
    index: int
    width: float
    height: float
    length: float | None = None
    note: str = ""


def _num(s) -> float | None:
    s = str(s or "").strip()
    if not s:
        return None
    return float(s)


def parse(text: str) -> list[TruthRow]:
    rows = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None:
        return rows
    missing = {"width_in", "height_in"} - {f.strip() for f in reader.fieldnames}
    if missing:
        raise ValueError(f"truth.csv needs columns {', '.join(FIELDS)}; missing {', '.join(sorted(missing))}")
    for k, r in enumerate(reader, 1):
        r = {(key or "").strip(): v for key, v in r.items()}
        w, h = _num(r.get("width_in")), _num(r.get("height_in"))
        if w is None and h is None:
            continue
        if w is None or h is None:
            raise ValueError(f"row {k}: width and height are both required")
        idx = r.get("index")
        rows.append(TruthRow(int(float(idx)) if str(idx or "").strip() else len(rows) + 1,
                             w, h, _num(r.get("length_in")), (r.get("note") or "").strip()))
    rows.sort(key=lambda t: t.index)
    return rows


def read(path: Path) -> list[TruthRow]:
    if not path.exists():
        return []
    return parse(path.read_text(encoding="utf-8"))


def to_csv(rows: list[TruthRow]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(FIELDS)
    for r in rows:
        w.writerow([r.index, f"{r.width:g}", f"{r.height:g}", "" if r.length is None else f"{r.length:g}", r.note])
    return out.getvalue()


def write(path: Path, rows: list[TruthRow]):
    path.write_text(to_csv(rows), encoding="utf-8")


def align(measured: list[tuple[float, float]], truth: list[tuple[float, float]],
          gap: float = 0.6) -> list[tuple[int | None, int | None]]:
    """Order-preserving alignment (both lists left to right).

    Matching costs |dw| + |dh|; skipping an item on either side costs `gap`.
    Returns (measured index or None, truth index or None) pairs in order.
    """
    n, m = len(measured), len(truth)
    D = np.zeros((n + 1, m + 1))
    D[:, 0] = np.arange(n + 1) * gap
    D[0, :] = np.arange(m + 1) * gap
    back = np.zeros((n + 1, m + 1), np.int8)  # 0 match, 1 skip measured, 2 skip truth
    back[1:, 0] = 1
    back[0, 1:] = 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            c = abs(measured[i - 1][0] - truth[j - 1][0]) + abs(measured[i - 1][1] - truth[j - 1][1])
            opts = (D[i - 1, j - 1] + c, D[i - 1, j] + gap, D[i, j - 1] + gap)
            k = int(np.argmin(opts))
            D[i, j], back[i, j] = opts[k], k
    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        k = back[i, j]
        if k == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif k == 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    return pairs[::-1]


def stats(errors: list[tuple[float, float]], n_missed: int = 0, n_extra: int = 0) -> dict:
    """errors: (width error, height error) of matched faces."""
    e = np.abs(np.array(errors, float).reshape(-1, 2))
    if len(e) == 0:
        return {"n": 0, "w_med": None, "w_max": None, "h_med": None, "h_max": None,
                "miss": n_missed, "extra": n_extra}
    return {
        "n": len(e),
        "w_med": float(np.median(e[:, 0])),
        "w_max": float(e[:, 0].max()),
        "h_med": float(np.median(e[:, 1])),
        "h_max": float(e[:, 1].max()),
        "miss": n_missed,
        "extra": n_extra,
    }
