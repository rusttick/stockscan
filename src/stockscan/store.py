"""Data directory on disk: photo sets, photos, hand measurements, reports.

Layout (all plain files):

    DATA/site.yaml
    DATA/cameras/        camera calibrations
    DATA/targets/        generated PDFs, geometry YAML, registry.yaml
    DATA/sets/<set>/     set.yaml, photos/, truth.csv, clicks.yaml
    DATA/reports/<set>/<analysis>-<timestamp>/
    DATA/cache/          thumbnails, straight-on images, hashes; safe to delete
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import imageio
from .config import Site
from .targets import Registry

SET_TYPES = {
    "calibration": "Calibration",
    "size_test": "Size test",
    "plane_check": "Plane check",
    "front": "Front",
    "back": "Back",
    "other": "Other",
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str, default: str = "file") -> str:
    """Reduce a name to [A-Za-z0-9._-], without path parts or leading dots."""
    name = name.replace("\\", "/").split("/")[-1]
    name = _SAFE.sub("_", name).strip("._")
    return name[:120] or default


def timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class SetInfo:
    id: str
    name: str
    type: str
    notes: str
    created: str
    extra: dict

    @property
    def type_label(self) -> str:
        return SET_TYPES.get(self.type, self.type)


class DataDir:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self._lock = threading.RLock()
        for sub in ("cameras", "targets", "sets", "reports", "cache"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ paths

    @property
    def site_path(self) -> Path:
        return self.root / "site.yaml"

    @property
    def cameras_dir(self) -> Path:
        return self.root / "cameras"

    @property
    def targets_dir(self) -> Path:
        return self.root / "targets"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    def site(self) -> Site:
        return Site(self.site_path)

    def registry(self) -> Registry:
        return Registry(self.targets_dir / "registry.yaml")

    def set_dir(self, set_id: str) -> Path:
        if safe_name(set_id) != set_id:
            raise KeyError(set_id)
        return self.root / "sets" / set_id

    def photos_dir(self, set_id: str) -> Path:
        return self.set_dir(set_id) / "photos"

    def reports_dir(self, set_id: str) -> Path:
        return self.root / "reports" / safe_name(set_id)

    # ------------------------------------------------------------ sets

    def create_set(self, name: str, type_: str, notes: str = "", extra: dict | None = None) -> str:
        if type_ not in SET_TYPES:
            raise ValueError(f"unknown set type {type_!r}")
        base = f"{_dt.date.today().isoformat()}_{type_}_{safe_name(name, 'set')}"
        with self._lock:
            sid, n = base, 2
            existing = {p.name.lower() for p in (self.root / "sets").iterdir()}
            while sid.lower() in existing:
                sid = f"{base}-{n}"
                n += 1
            d = self.root / "sets" / sid
            (d / "photos").mkdir(parents=True)
            info = {"name": name, "type": type_, "notes": notes,
                    "created": _dt.datetime.now().isoformat(timespec="seconds")}
            info.update(extra or {})
            self._write_yaml(d / "set.yaml", info)
        return sid

    def get_set(self, set_id: str) -> SetInfo:
        d = self.set_dir(set_id)
        p = d / "set.yaml"
        if not p.exists():
            raise KeyError(set_id)
        data = self._read_yaml(p) or {}
        extra = {k: v for k, v in data.items() if k not in ("name", "type", "notes", "created")}
        return SetInfo(set_id, str(data.get("name", set_id)), str(data.get("type", "other")),
                       str(data.get("notes", "") or ""), str(data.get("created", "")), extra)

    def update_set(self, set_id: str, **fields):
        p = self.set_dir(set_id) / "set.yaml"
        with self._lock:
            data = self._read_yaml(p) or {}
            data.update(fields)
            self._write_yaml(p, data)

    def list_sets(self) -> list[SetInfo]:
        out = []
        for d in (self.root / "sets").iterdir():
            if d.is_dir() and (d / "set.yaml").exists() and safe_name(d.name) == d.name:
                try:
                    out.append(self.get_set(d.name))
                except Exception:
                    continue
        out.sort(key=lambda s: s.created, reverse=True)
        return out

    # ------------------------------------------------------------ photos

    def photos(self, set_id: str) -> list[str]:
        d = self.photos_dir(set_id)
        if not d.exists():
            return []
        names = [p.name for p in d.iterdir() if p.is_file() and imageio.is_image_name(p.name)]
        return sorted(names, key=str.lower)

    def photo_path(self, set_id: str, name: str) -> Path:
        for n in self.photos(set_id):
            if n == name:
                return self.photos_dir(set_id) / n
        raise KeyError(name)

    def _hash_index_path(self, set_id: str) -> Path:
        return self.cache_dir / "hashes" / f"{set_id}.yaml"

    def photo_hashes(self, set_id: str) -> dict[str, str]:
        """name -> sha256, cached by size and modification time."""
        with self._lock:
            ip = self._hash_index_path(set_id)
            index = (self._read_yaml(ip) or {}) if ip.exists() else {}
            out, changed = {}, False
            for n in self.photos(set_id):
                p = self.photos_dir(set_id) / n
                st = p.stat()
                rec = index.get(n)
                if not rec or rec.get("size") != st.st_size or rec.get("mtime") != st.st_mtime:
                    rec = {"size": st.st_size, "mtime": st.st_mtime, "sha256": sha256_file(p)}
                    index[n] = rec
                    changed = True
                out[n] = rec["sha256"]
            for n in list(index):
                if n not in out:
                    del index[n]
                    changed = True
            if changed:
                ip.parent.mkdir(parents=True, exist_ok=True)
                self._write_yaml(ip, index)
            return out

    def add_photo(self, set_id: str, original_name: str, tmp_path: Path) -> tuple[str, str]:
        """Move an uploaded file into the set. Returns (status, stored name);
        status is 'added', 'duplicate' or 'rejected'."""
        name = safe_name(original_name, "photo.jpg")
        if not imageio.is_image_name(name):
            tmp_path.unlink(missing_ok=True)
            return "rejected", name
        digest = sha256_file(tmp_path)
        with self._lock:
            hashes = self.photo_hashes(set_id)
            for n, h in hashes.items():
                if h == digest:
                    tmp_path.unlink(missing_ok=True)
                    return "duplicate", n
            lower = {n.lower() for n in hashes}
            stem, ext = Path(name).stem, Path(name).suffix
            cand, k = name, 2
            while cand.lower() in lower:
                cand = f"{stem}-{k}{ext}"
                k += 1
            dst = self.photos_dir(set_id) / cand
            shutil.move(str(tmp_path), dst)
            self.photo_hashes(set_id)
        return "added", cand

    def delete_photo(self, set_id: str, name: str):
        p = self.photo_path(set_id, name)
        with self._lock:
            p.unlink()
            for sub in ("thumbs", "rect"):
                for c in (self.cache_dir / sub / set_id).glob(f"{name}.*"):
                    c.unlink(missing_ok=True)
            self.photo_hashes(set_id)

    def rescan(self, set_id: str) -> dict:
        """Pick up photos copied into the folder by hand; rename unsafe names;
        remove exact duplicates."""
        d = self.photos_dir(set_id)
        renamed = removed = 0
        with self._lock:
            for p in sorted(d.iterdir()):
                if p.is_file() and imageio.is_image_name(p.name) and safe_name(p.name) != p.name:
                    target = safe_name(p.name)
                    k = 2
                    taken = {q.name.lower() for q in d.iterdir()}
                    while target.lower() in taken:
                        target = f"{Path(safe_name(p.name)).stem}-{k}{Path(p.name).suffix}"
                        k += 1
                    p.rename(d / target)
                    renamed += 1
            seen = {}
            for n, h in sorted(self.photo_hashes(set_id).items(), key=lambda kv: kv[0].lower()):
                if h in seen:
                    (d / n).unlink()
                    removed += 1
                else:
                    seen[h] = n
            hashes = self.photo_hashes(set_id)
        return {"photos": len(hashes), "renamed": renamed, "duplicates_removed": removed}

    def thumbnail(self, set_id: str, name: str) -> Path:
        src = self.photo_path(set_id, name)
        dst = self.cache_dir / "thumbs" / set_id / f"{name}.jpg"
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            imageio.make_thumbnail(src, dst)
        return dst

    # ------------------------------------------------------------ truth and clicks

    def truth_path(self, set_id: str) -> Path:
        return self.set_dir(set_id) / "truth.csv"

    def clicks(self, set_id: str) -> dict:
        p = self.set_dir(set_id) / "clicks.yaml"
        return (self._read_yaml(p) or {}) if p.exists() else {}

    def save_clicks(self, set_id: str, photo: str, faces: list):
        p = self.set_dir(set_id) / "clicks.yaml"
        with self._lock:
            data = (self._read_yaml(p) or {}) if p.exists() else {}
            if faces:
                data[photo] = faces
            else:
                data.pop(photo, None)
            self._write_yaml(p, data)

    # ------------------------------------------------------------ reports

    def new_report_dir(self, set_id: str, analysis: str) -> Path:
        base = self.reports_dir(set_id)
        with self._lock:
            base.mkdir(parents=True, exist_ok=True)
            name = f"{safe_name(analysis)}-{timestamp()}"
            cand, k = name, 2
            while (base / cand).exists():
                cand = f"{name}-{k}"
                k += 1
            (base / cand).mkdir()
        return base / cand

    def list_reports(self, set_id: str | None = None) -> list[dict]:
        """Report summaries, newest first."""
        roots = [self.reports_dir(set_id)] if set_id else [p for p in (self.root / "reports").iterdir() if p.is_dir()]
        out = []
        for r in roots:
            if not r.exists():
                continue
            for d in r.iterdir():
                res = d / "result.yaml"
                if d.is_dir() and res.exists():
                    try:
                        data = self._read_yaml(res) or {}
                    except Exception:
                        continue
                    out.append({
                        "set": r.name,
                        "run": d.name,
                        "analysis": data.get("analysis", ""),
                        "status": data.get("status", ""),
                        "summary": data.get("summary", ""),
                        "finished": data.get("finished", ""),
                        "version": data.get("version", ""),
                    })
        out.sort(key=lambda x: x["finished"], reverse=True)
        return out

    def report_path(self, set_id: str, run: str) -> Path:
        if safe_name(run) != run:
            raise KeyError(run)
        p = self.reports_dir(set_id) / run
        if not p.is_dir():
            raise KeyError(run)
        return p

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _read_yaml(p: Path):
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f)

    @staticmethod
    def _write_yaml(p: Path, data):
        tmp = p.with_name(p.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=False)
        tmp.replace(p)
