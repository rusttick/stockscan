"""Analyses run on a photo set; each writes a report folder and one summary line."""

from __future__ import annotations

import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml

from . import __version__, calib, detect, faces, imageio, pose, rectify, targets, truth
from .report import Report, fmt, summary_line
from .store import DataDir

# name -> (title, test code, set types it applies to; None = any)
ANALYSES = {
    "inspect": ("Inspect", "IN", None),
    "calibrate": ("Calibrate camera", "T2", {"calibration"}),
    "size_test": ("Size test", "T3", {"size_test"}),
    "plane_check": ("Plane check", "T4", {"plane_check"}),
    "manual": ("Manual cross-section", "T5M", {"front", "back"}),
    "faces": ("Automatic cross-section", "T5A", {"front"}),
    "compare": ("Compare with another set (upload check)", "T8", {"other"}),
}


def applicable(set_type: str) -> list[str]:
    return [k for k, (_, _, types) in ANALYSES.items() if types is None or set_type in types]


def test_code(analysis: str, set_type: str) -> str:
    if analysis == "inspect" and set_type == "back":
        return "T6"
    return ANALYSES[analysis][1]


class AnalysisError(Exception):
    """A problem the operator can fix (shown without a traceback)."""


# ---------------------------------------------------------------- context


class Ctx:
    def __init__(self, dd: DataDir, set_id: str, params: dict | None = None):
        self.dd = dd
        self.set_id = set_id
        self.params = params or {}
        self.site = dd.site()
        self.registry = dd.registry()
        self.info = dd.get_set(set_id)
        self.id_map = self.registry.id_map()
        self._cals: dict[str, calib.Calibration | None] = {}

    def scale(self, seg: targets.Segment) -> tuple[float, float]:
        return self.site.print_scale(self.registry.get_print(seg.print_serial))

    def calibration(self, meta: imageio.PhotoMeta) -> calib.Calibration | None:
        key = meta.camera_key
        if key not in self._cals:
            self._cals[key] = calib.find(self.dd.cameras_dir, key)
        return self._cals[key]

    def camera(self, meta: imageio.PhotoMeta):
        """(K, dist, calibration or None)."""
        cal = self.calibration(meta)
        if cal is not None:
            return cal.K, cal.dist, cal
        K, dist = pose.approx_intrinsics(meta.width, meta.height, meta.focal35, meta.focal_mm)
        return K, dist, None


@dataclass
class Photo:
    name: str
    path: Path
    meta: imageio.PhotoMeta
    gray: np.ndarray
    K: np.ndarray
    dist: np.ndarray
    cal: calib.Calibration | None
    det: detect.Detection


def load_photo(ctx: Ctx, name: str, kinds=None) -> Photo:
    path = ctx.dd.photo_path(ctx.set_id, name)
    meta = imageio.read_meta(path)
    gray = imageio.load_gray(path)
    K, dist, cal = ctx.camera(meta)
    # with a calibration, corner interpolation can use the lens model
    det = detect.detect(gray, ctx.id_map, K if cal else None, dist if cal else None, kinds)
    return Photo(name, path, meta, gray, K, dist, cal, det)


def best_of(ph: Photo, kind: str):
    """The detected segment of this kind with the most points."""
    best = None
    for sd in ph.det.by_kind(kind):
        if best is None or sd.n_corners > best.n_corners:
            best = sd
    return best


def front_pose(ctx: Ctx, ph: Photo):
    """The front marker row with the most points, and its pose."""
    best = best_of(ph, "front")
    if best is None:
        return None, None
    return best, pose.segment_pose(best, ph.K, ph.dist, ctx.scale(best.segment))


def face_plane(ctx: Ctx, ph: Photo):
    sd, pz = front_pose(ctx, ph)
    if pz is None:
        return None, None
    return sd, rectify.FacePlane(pz, ph.K, ph.dist, float(ctx.site["face_offset_in"]))


def straight_on(ctx: Ctx, ph: Photo, sd, plane, image=None) -> rectify.Rectified:
    seg = sd.segment
    yr = rectify.face_band(seg.height_in, float(ctx.site["above_in"]))
    ppi = float(ctx.site["px_per_in"])
    limit = (-36.0, seg.width_in + 36.0)
    xr = rectify.visible_x_range(plane, (ph.meta.width, ph.meta.height), yr, limit)
    if xr is None:
        raise AnalysisError("the face area above the front row is not in the photo")
    max_w = (rectify.MAX_SIDE_PX - 2) / ppi
    if xr[1] - xr[0] > max_w:
        mid = seg.width_in / 2
        xr = (max(xr[0], mid - max_w / 2), min(xr[1], mid + max_w / 2))
    return rectify.rectify(ph.gray if image is None else image, plane, xr, yr, ppi)


def display(img_bgr: np.ndarray, meta: imageio.PhotoMeta) -> np.ndarray:
    return imageio.orient(img_bgr, meta.orientation)


def cal_note(ph: Photo) -> str:
    return f"calibrated ({ph.cal.file})" if ph.cal else "NOT calibrated (rough lens model from EXIF)"


def _photo_list(ctx: Ctx) -> list[str]:
    names = ctx.dd.photos(ctx.set_id)
    if not names:
        raise AnalysisError("the set has no photos")
    return names


# ---------------------------------------------------------------- inspect


def run_inspect(ctx: Ctx, rep: Report) -> tuple[dict, dict]:
    names = _photo_list(ctx)
    blur_t = float(ctx.site["blur_threshold"])
    rows, seg_rows, agree_rows, images = [], [], [], []
    n_cal = n_seg = n_blur = 0
    sharp = []
    for name in names:
        try:
            ph = load_photo(ctx, name)
        except Exception as e:
            rows.append([name, (f"cannot read: {e}", "bad")] + [""] * 8)
            continue
        s = imageio.sharpness(ph.gray)
        sharp.append(s)
        clip = imageio.clipped_fraction(ph.gray)
        n_cal += ph.cal is not None
        n_blur += s < blur_t
        poses = {}
        for sd in ph.det.segments.values():
            pz = pose.segment_pose(sd, ph.K, ph.dist, ctx.scale(sd.segment))
            poses[sd.segment.serial] = pz
            seg_rows.append([name, sd.segment.serial, sd.segment.kind, ctx.site.label(sd.segment.serial),
                             sd.n_markers, f"{sd.n_corners}/{sd.segment.n_corners}",
                             fmt(pz.distance_in / 12, 2) if pz else "-", fmt(pz.angle_deg, 1) if pz else "-",
                             fmt(pz.rms_px, 2) if pz else "-"])
        fronts = [k for k, v in poses.items() if v is not None and ctx.registry.find_segment(k).kind == "front"]
        n_seg += bool(fronts)
        for i, a in enumerate(fronts):
            for b in fronts[i + 1:]:
                ag = pose.plane_agreement(poses[a], poses[b])
                agree_rows.append([name, a, b, fmt(ag["angle_deg"], 2), fmt(ag["offset_in"], 3)])
        m = ph.meta
        rows.append([name, m.camera, m.lens_name, f"{m.width}x{m.height}",
                     fmt(m.exposure_s, 4) if m.exposure_s else "-", m.iso or "-",
                     (fmt(s, 0), "bad" if s < blur_t else ""), fmt(clip * 100, 2),
                     ph.det.n_markers, len(ph.det.segments), "yes" if ph.cal else ("no", "warn")])
        if ph.det.unknown_ids:
            rep_unknown = ", ".join(map(str, ph.det.unknown_ids[:20]))
            rows[-1][0] = f"{name} (unknown IDs: {rep_unknown})"
        images.append((detect.draw(cv2.cvtColor(ph.gray, cv2.COLOR_GRAY2BGR), ph.det), ph.meta, name))
    rep.h2("Photos")
    rep.p(f"Blurry means sharpness below {blur_t:g} (Settings).")
    rep.table(["photo", "camera", "lens", "pixels", "exposure s", "ISO", "sharpness", "clipped %",
               "markers", "segments", "calibrated"], rows)
    rep.h2("Segments seen")
    if seg_rows:
        rep.table(["photo", "segment", "kind", "label", "markers", "corners", "distance ft", "angle deg", "rms px"],
                  seg_rows, left=4)
    else:
        rep.p("No known segments found. Generate targets on the Targets page first.", "bad")
    if agree_rows:
        rep.h2("Plane agreement between front rows seen together")
        rep.p("Angle between the rows' planes, and how far the second row's centre is from the first's plane.")
        rep.table(["photo", "row a", "row b", "angle deg", "offset in"], agree_rows, left=3)
    rep.h2("Annotated photos")
    for img, meta, name in images:
        rep.image(display(img, meta), name, max_side=1200)
    fields = {"photos": len(names), "cal": n_cal, "seg_found": n_seg, "blur": n_blur,
              "sharp_med": float(np.median(sharp)) if sharp else None}
    return fields, {"photos": rows}


# ---------------------------------------------------------------- calibrate


def run_calibrate(ctx: Ctx, rep: Report) -> tuple[dict, dict]:
    names = _photo_list(ctx)
    if not ctx.registry.segments("calib"):
        raise AnalysisError("no calibration board generated yet (Targets page)")
    per = []
    boards_used = {}
    for name in names:
        try:
            path = ctx.dd.photo_path(ctx.set_id, name)
            meta = imageio.read_meta(path)
            gray = imageio.load_gray(path)
        except Exception as e:
            per.append({"name": name, "error": str(e)})
            continue
        det = detect.detect(gray, ctx.id_map, kinds=("calib",))
        sd = max(det.segments.values(), key=lambda g: g.n_corners, default=None)
        if sd is None:
            per.append({"name": name, "meta": meta, "obj": np.zeros((0, 3)), "img": np.zeros((0, 2)), "n": 0})
            continue
        board = sd.segment
        boards_used[board.serial] = board
        obj = targets.object_points(board, ctx.scale(board))[sd.corner_ids]
        per.append({"name": name, "meta": meta, "obj": obj, "img": sd.corners, "n": sd.n_corners})
    keys = Counter(p["meta"].camera_key for p in per if "meta" in p and p["n"] >= calib.MIN_CORNERS)
    if not keys:
        raise AnalysisError(f"no photo has {calib.MIN_CORNERS} or more board corners")
    key = keys.most_common(1)[0][0]
    group = [p for p in per if "meta" in p and p["meta"].camera_key == key]
    meta0 = group[0]["meta"]
    size = (meta0.width, meta0.height)
    res = calib.calibrate([(p["obj"], p["img"]) for p in group], size)
    err = {group[i]["name"]: e for i, e in zip(res.used, res.per_view_px)}
    rows = []
    for p in per:
        if "error" in p:
            rows.append([p["name"], "", (p["error"], "bad"), ""])
            continue
        if p["meta"].camera_key != key:
            rows.append([p["name"], p["n"], ("other camera/lens/size: " + p["meta"].camera_key, "warn"), ""])
            continue
        e = err.get(p["name"])
        rows.append([p["name"], p["n"], "used" if e is not None else (f"skipped (< {calib.MIN_CORNERS} corners)", "warn"),
                     ("" if e is None else (fmt(e, 3), "bad" if e > 1.0 else ""))])
    cov = calib.coverage([p["img"] for p in group], size)
    cells = int((cov > 0).sum())
    K, d = res.K, res.dist
    rep.h2("Result")
    rep.kv({
        "camera": key,
        "photos used": f"{len(res.used)} of {len(names)}",
        "RMS reprojection error": f"{res.rms_px:.3f} px",
        "fx, fy": f"{K[0, 0]:.1f}, {K[1, 1]:.1f} px",
        "cx, cy": f"{K[0, 2]:.1f}, {K[1, 2]:.1f} px (image centre {(size[0] - 1) / 2:.1f}, {(size[1] - 1) / 2:.1f})",
        "distortion k1 k2 p1 p2 k3": " ".join(f"{v:.5f}" for v in d[:5]),
        "coverage": f"{cells} of {cov.size} grid cells have corners",
    })
    if meta0.focal35:
        Ka, _ = pose.approx_intrinsics(size[0], size[1], meta0.focal35)
        rep.p(f"EXIF 35 mm-equivalent focal length suggests fx = {Ka[0, 0]:.0f} px "
              f"({(K[0, 0] / Ka[0, 0] - 1) * 100:+.1f} %).")
    rep.p("Boards seen: " + "; ".join(f"{b.serial} ({b.describe()}, print scale "
                                      f"{ctx.scale(b)[0]:.4f} x {ctx.scale(b)[1]:.4f})" for b in boards_used.values()))
    rep.h2("Per photo")
    rep.table(["photo", "corners", "status", "error px"], rows)
    rep.h2("Coverage")
    rep.p("Corners seen in each part of the frame (stored orientation). Empty cells near the edges "
          "and corners make the distortion estimate weaker.")
    rep.image(_coverage_image(cov, [p["img"] for p in group], size), "coverage", max_side=1000)
    saved = ""
    if ctx.params.get("save", True):
        cal = calib.Calibration(key, size[0], size[1], K, d, res.rms_px, len(res.used), ctx.set_id)
        saved = calib.save(cal, ctx.dd.cameras_dir).name
        rep.p(f"Saved as cameras/{saved}; used for photos from this camera, lens and image size.", "ok")
    else:
        rep.p("Re-run: calibration not saved.")
    cam = meta0.model or meta0.camera
    fields = {"cam": cam, "imgs": f"{len(res.used)}/{len(names)}", "rms": res.rms_px,
              "fx": round(float(K[0, 0]), 1), "cover": f"{cells}/{cov.size}"}
    return fields, {"camera_key": key, "K": K, "dist": d, "rms_px": res.rms_px, "saved": saved}


def _coverage_image(cov, points, size):
    w, h = size
    W = 1000
    H = int(round(W * h / w))
    img = np.full((H, W, 3), 255, np.uint8)
    gy, gx = cov.shape
    mx = max(1, cov.max())
    for r in range(gy):
        for c in range(gx):
            v = cov[r, c] / mx
            color = (255, 255, 255) if cov[r, c] == 0 else (int(255 * (1 - v)), int(255 - 120 * v), 255 - int(40 * v))
            x0, y0 = int(c * W / gx), int(r * H / gy)
            x1, y1 = int((c + 1) * W / gx), int((r + 1) * H / gy)
            cv2.rectangle(img, (x0, y0), (x1, y1), color, -1)
            cv2.rectangle(img, (x0, y0), (x1, y1), (180, 180, 180), 1)
            cv2.putText(img, str(cov[r, c]), (x0 + 6, y0 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 60, 60), 1)
    for p in points:
        for u, v in np.asarray(p).reshape(-1, 2):
            cv2.circle(img, (int(u / w * W), int(v / h * H)), 2, (200, 0, 0), -1)
    return img


# ---------------------------------------------------------------- size test

BANDS = [(3, "2ft"), (5, "4ft"), (7, "6ft"), (1e9, "8ft")]
OBLIQUE_DEG = 25.0


def _cm(inches: float) -> str:
    return f"{round(inches * 2.54, 1):g}cm"


def run_size_test(ctx: Ctx, rep: Report) -> tuple[dict, dict]:
    names = _photo_list(ctx)
    patches = ctx.registry.segments("sizetest")
    if not patches:
        raise AnalysisError("no size-test sheet generated yet (Targets page)")
    sizes = sorted({round(p.square_in, 6) for p in patches})
    rows, records = [], []
    n_nodet = 0
    any_uncal = False
    for name in names:
        try:
            ph = load_photo(ctx, name, kinds=("sizetest",))
        except Exception as e:
            rows.append([name, (str(e), "bad")])
            continue
        any_uncal |= ph.cal is None
        found = {s: 0 for s in sizes}
        best = None
        for sd in ph.det.segments.values():
            s = round(sd.segment.square_in, 6)
            found[s] = max(found[s], sd.n_corners)
            if sd.n_corners >= pose.MIN_CORNERS and (best is None or s > best[0]):
                pz = pose.segment_pose(sd, ph.K, ph.dist, ctx.scale(sd.segment))
                if pz:
                    best = (s, pz)
        if best is None:
            n_nodet += 1
            rows.append([name, "-", "-", "-"] + [f"{found[s]}/6" for s in sizes])
            continue
        d_ft, ang = best[1].distance_in / 12, best[1].angle_deg
        band = next(lbl for lim, lbl in BANDS if d_ft < lim)
        view = "O" if ang >= OBLIQUE_DEG else "S"
        det_ok = {s: found[s] >= 4 for s in sizes}
        records.append({"photo": name, "distance_ft": d_ft, "angle_deg": ang, "band": band, "view": view,
                        "detected": det_ok})
        rows.append([name, fmt(d_ft, 1), fmt(ang, 0), f"{band} {view}"] +
                    [(f"{found[s]}/6", "" if det_ok[s] else "bad") for s in sizes])
    rep.p("A patch counts as detected when at least 4 of its 6 ChArUco corners are found. "
          f"Distance and angle are to the largest patch found. S = straight (< {OBLIQUE_DEG:g} deg), O = oblique.")
    if any_uncal:
        rep.p("Some photos have no camera calibration: distances are rough.", "warn")
    rep.h2("Per photo")
    rep.table(["photo", "distance ft", "angle deg", "band"] + [f"{_cm(s)} ({s:.3g} in)" for s in sizes], rows,
              left=1)
    rep.h2("Detection rate by distance band")
    fields = {"n": len(names), "nodet": n_nodet}
    rate_rows = []
    for _, band in BANDS:
        vals = []
        for view in ("S", "O"):
            rs = [r for r in records if r["band"] == band and r["view"] == view]
            if not rs:
                vals.append("na")
                continue
            smallest = "none"
            for s in sizes:
                rate = np.mean([r["detected"][s] for r in rs])
                rate_rows.append([band, view, len(rs), _cm(s), fmt(rate * 100, 0)])
                if smallest == "none" and rate >= 0.95:
                    smallest = _cm(s)
            vals.append(smallest)
        if vals != ["na", "na"]:
            fields[f"b{band}"] = "/".join(vals)
    rep.table(["band", "view", "photos", "square", "detected %"], rate_rows, left=2)
    rep.p("Summary fields bNft = smallest square size detected in at least 95 % of photos, straight/oblique.")
    return fields, {"records": records}


# ---------------------------------------------------------------- plane check


def run_plane_check(ctx: Ctx, rep: Report) -> tuple[dict, dict]:
    names = _photo_list(ctx)
    if not ctx.registry.segments("calib"):
        raise AnalysisError("no calibration board generated yet (Targets page)")
    rows, errs, recs = [], [], []
    n_cal = 0
    rep.p("The camera pose comes from the front marker row only. The board's corners are mapped from the photo onto "
          "the face plane and compared with their printed positions (an affine fit gives the scale along "
          f"each board axis). Face offset: {float(ctx.site['face_offset_in']):g} in.")
    for name in names:
        try:
            ph = load_photo(ctx, name)
        except Exception as e:
            rows.append([name, (str(e), "bad")])
            continue
        n_cal += ph.cal is not None
        sd, plane = face_plane(ctx, ph)
        bd = best_of(ph, "calib")
        if plane is None:
            rows.append([name, ("no front row pose", "bad")] + [""] * 7)
            continue
        if bd is None or bd.n_corners < 6:
            rows.append([name, sd.segment.serial, ("board not found", "bad")] + [""] * 6)
            continue
        xy, ok = plane.to_plane(bd.corners)
        known = targets.object_points(bd.segment, ctx.scale(bd.segment))[bd.corner_ids, :2]
        A, b, rms = _affine_fit(known[ok], xy[ok])
        sx, sy = float(np.linalg.norm(A[:, 0])), float(np.linalg.norm(A[:, 1]))
        e = max(abs(sx - 1), abs(sy - 1)) * 100
        errs.append(e)
        recs.append({"photo": name, "sx": sx, "sy": sy, "rms_in": rms, "err_pct": e,
                     "distance_in": plane.pose.distance_in, "angle_deg": plane.pose.angle_deg})
        rows.append([name, sd.segment.serial, bd.n_corners, fmt(plane.pose.distance_in / 12, 2),
                     fmt(plane.pose.angle_deg, 1), fmt((sx - 1) * 100, 3), fmt((sy - 1) * 100, 3),
                     fmt(rms, 4), (fmt(e * 0.03, 3), "bad" if e >= 0.5 else "ok")])
        if len(recs) <= 12:
            try:
                rect = straight_on(ctx, ph, sd, plane)
                vis = cv2.cvtColor(rect.image, cv2.COLOR_GRAY2BGR)
                for p in rect.to_px(xy[ok]):
                    cv2.circle(vis, (int(round(p[0])), int(round(p[1]))), 4, (0, 0, 255), 1)
                rep.image(vis, f"{name}: straight-on, board corners (red)", max_side=1600)
            except AnalysisError:
                pass
    rep.h2("Per photo")
    rep.table(["photo", "front row", "board corners", "distance ft", "angle deg", "x scale err %", "y scale err %",
               "residual in", "err on 3 in"], rows)
    rep.p("Pass: scale error below 0.5 % (0.015 in on a 3 in face).")
    fields = {"n": len(errs),
              "scale_med": f"{np.median(errs):.2f}%" if errs else None,
              "scale_max": f"{np.max(errs):.2f}%" if errs else None,
              "cal": f"{n_cal}/{len(names)}"}
    return fields, {"photos": recs}


def _affine_fit(src: np.ndarray, dst: np.ndarray):
    X = np.column_stack([src, np.ones(len(src))])
    sol, *_ = np.linalg.lstsq(X, dst, rcond=None)
    A, b = sol[:2].T, sol[2]
    res = dst - (src @ A.T + b)
    return A, b, float(np.sqrt(np.mean(np.sum(res ** 2, axis=1))))


# ---------------------------------------------------------------- cross-sections


def _truth_rows(ctx: Ctx):
    rows = truth.read(ctx.dd.truth_path(ctx.set_id))
    return rows


def _compare_rows(rep: Report, name: str, measured: list[tuple[float, float]], rows_truth, errors, counts):
    """Align one photo's measurements with truth; append to errors/counts; add a table."""
    t = [(r.width, r.height) for r in rows_truth]
    table = []
    if t:
        pairs = truth.align(measured, t)
        for i, j in pairs:
            if i is not None and j is not None:
                dw, dh = measured[i][0] - t[j][0], measured[i][1] - t[j][1]
                errors.append((dw, dh))
                counts["matched"] += 1
                bad = "bad" if max(abs(dw), abs(dh)) > 0.1 else ""
                table.append([i + 1, fmt(measured[i][0]), fmt(measured[i][1]), rows_truth[j].index,
                              fmt(t[j][0]), fmt(t[j][1]), (fmt(dw), bad), (fmt(dh), bad),
                              f"{faces.near_standard(measured[i][0]):g} x {faces.near_standard(measured[i][1]):g}"])
            elif i is not None:
                counts["extra"] += 1
                table.append([i + 1, fmt(measured[i][0]), fmt(measured[i][1]), ("extra", "warn"), "", "", "", "", ""])
            else:
                counts["miss"] += 1
                table.append(["", "", "", rows_truth[j].index, fmt(t[j][0]), fmt(t[j][1]), ("missed", "warn"), "", ""])
    else:
        for i, (w, h) in enumerate(measured):
            table.append([i + 1, fmt(w), fmt(h), "", "", "", "", "",
                          f"{faces.near_standard(w):g} x {faces.near_standard(h):g}"])
    rep.h2(name)
    rep.table(["#", "width", "height", "truth #", "truth w", "truth h", "dw", "dh", "near std (info)"], table, left=0)


def clicks_to_faces(plane: rectify.FacePlane, clicks: list) -> list[tuple[float, float, float]]:
    """Clicked (image px) corner pairs -> (x left, width, height) in plane inches, left to right."""
    out = []
    for c in clicks:
        xy, ok = plane.to_plane(np.array([c["tl"], c["br"]], float))
        if not ok.all():
            continue
        (x1, y1), (x2, y2) = xy
        out.append((min(x1, x2), abs(x2 - x1), abs(y2 - y1)))
    return sorted(out)


def run_manual(ctx: Ctx, rep: Report) -> tuple[dict, dict]:
    clicks = ctx.dd.clicks(ctx.set_id)
    names = [n for n in ctx.dd.photos(ctx.set_id) if clicks.get(n)]
    if not names:
        raise AnalysisError("no faces clicked yet (Measure page)")
    rows_truth = _truth_rows(ctx)
    errors, counts = [], Counter(matched=0, miss=0, extra=0)
    n_meas = 0
    for name in names:
        ph = load_photo(ctx, name)
        sd, plane = face_plane(ctx, ph)
        if plane is None:
            rep.p(f"{name}: no front row pose; skipped", "bad")
            continue
        fs = clicks_to_faces(plane, clicks[name])
        n_meas += len(fs)
        _compare_rows(rep, f"{name} ({cal_note(ph)}, front row {sd.segment.serial})",
                      [(w, h) for _, w, h in fs], rows_truth, errors, counts)
        try:
            rect = straight_on(ctx, ph, sd, plane)
            vis = cv2.cvtColor(rect.image, cv2.COLOR_GRAY2BGR)
            for c in clicks[name]:
                xy, _ = plane.to_plane(np.array([c["tl"], c["br"]], float))
                p = np.round(rect.to_px(xy)).astype(int)
                cv2.rectangle(vis, tuple(p[0]), tuple(p[1]), (0, 200, 0), 2)
            rep.image(vis, f"{name}: clicked faces", max_side=2400)
        except AnalysisError:
            pass
    st = truth.stats(errors, counts["miss"], counts["extra"])
    if not rows_truth:
        rep.p("No hand measurements (truth.csv) for this set: nothing to compare with.", "warn")
        return {"n": n_meas, "truth": 0, "photos": len(names)}, {}
    fields = {"n": st["n"], "w_med": st["w_med"], "w_max": st["w_max"], "h_med": st["h_med"], "h_max": st["h_max"],
              "miss": st["miss"], "extra": st["extra"], "photos": len(names)}
    return fields, {"stats": st}


def run_faces(ctx: Ctx, rep: Report) -> tuple[dict, dict]:
    names = _photo_list(ctx)
    rows_truth = _truth_rows(ctx)
    errors, counts = [], Counter(matched=0, miss=0, extra=0)
    n_found = n_photos = 0
    rep.p("Shape only: shelf line (orange), side boundaries and seams (cyan), accepted faces (green), "
          "rejected candidates (red). 'near std' is the nearest 1/8 in, information only.")
    for name in names:
        try:
            ph = load_photo(ctx, name)
        except Exception as e:
            rep.p(f"{name}: cannot read: {e}", "bad")
            continue
        sd, plane = face_plane(ctx, ph)
        if plane is None:
            rep.p(f"{name}: no front row pose; skipped", "bad")
            continue
        try:
            rect = straight_on(ctx, ph, sd, plane)
        except AnalysisError as e:
            rep.p(f"{name}: {e}", "bad")
            continue
        n_photos += 1
        res = faces.detect(rect)
        n_found += len(res.faces)
        _compare_rows(rep, f"{name} ({cal_note(ph)}, front row {sd.segment.serial})",
                      [(f.width, f.height) for f in res.faces], rows_truth, errors, counts)
        if res.rejected:
            rep.p("Rejected: " + "; ".join(f"x {c.x0:.2f}-{c.x1:.2f}: {c.reason}" for c in res.rejected[:30]))
        rep.image(faces.draw(rect, res), f"{name}: automatic faces", max_side=2400)
    if n_photos == 0:
        raise AnalysisError("no photo with a usable front marker row")
    if not rows_truth:
        rep.p("No hand measurements (truth.csv) for this set: nothing to compare with.", "warn")
        return {"found": n_found, "truth": 0, "photos": n_photos}, {}
    st = truth.stats(errors, counts["miss"], counts["extra"])
    fields = {"found": f"{counts['matched']}/{len(rows_truth) * n_photos}", "w_med": st["w_med"], "w_max": st["w_max"],
              "h_med": st["h_med"], "h_max": st["h_max"], "extra": st["extra"], "photos": n_photos}
    return fields, {"stats": st}


# ---------------------------------------------------------------- compare (upload check)


def run_compare(ctx: Ctx, rep: Report) -> tuple[dict, dict]:
    other = ctx.params.get("other") or ctx.info.extra.get("compare_with")
    if not other:
        raise AnalysisError("choose the set to compare with (the same photos copied by cable)")
    ctx.dd.update_set(ctx.set_id, compare_with=other)
    names = _photo_list(ctx)
    ref = {}
    for n in ctx.dd.photos(other):
        try:
            m = imageio.read_meta(ctx.dd.photo_path(other, n))
        except Exception:
            continue
        ref[n] = m
    by_time = {m.datetime: n for n, m in ref.items() if m.datetime}
    by_stem = {Path(n).stem.lower(): n for n in ref}
    rows = []
    matched = same = lens = 0
    for n in names:
        m = imageio.read_meta(ctx.dd.photo_path(ctx.set_id, n))
        lens += bool(m.lens)
        r = by_time.get(m.datetime) if m.datetime else None
        r = r or by_stem.get(Path(n).stem.lower())
        if r is None:
            rows.append([n, f"{m.width}x{m.height}", m.lens or "-", ("no match", "warn"), "", ""])
            continue
        matched += 1
        rm = ref[r]
        ok = (m.width, m.height) == (rm.width, rm.height) or m.display_size == rm.display_size
        same += ok
        rows.append([n, f"{m.width}x{m.height}", m.lens or "-", r, f"{rm.width}x{rm.height}",
                     ("same" if ok else "DIFFERENT", "ok" if ok else "bad")])
    rep.p(f"Compared with set {other}, matching photos by EXIF time, then by file name.")
    rep.table(["photo", "pixels", "lens (EXIF)", "matched photo", "its pixels", "size"], rows)
    return {"n": len(names), "matched": matched, "same_size": f"{same}/{matched}",
            "exif_lens": f"{lens}/{len(names)}"}, {}


RUNNERS = {
    "inspect": run_inspect,
    "calibrate": run_calibrate,
    "size_test": run_size_test,
    "plane_check": run_plane_check,
    "manual": run_manual,
    "faces": run_faces,
    "compare": run_compare,
}


def run(dd: DataDir, set_id: str, analysis: str, params: dict | None = None) -> tuple[str, str]:
    """Run one analysis; always writes a report folder. Returns (run name, summary line)."""
    if analysis not in RUNNERS:
        raise ValueError(f"unknown analysis {analysis!r}")
    info = dd.get_set(set_id)
    code = test_code(analysis, info.type)
    out = dd.new_report_dir(set_id, analysis)
    rep = Report(out, f"{ANALYSES[analysis][0]}: {info.name}", analysis, set_id)
    try:
        ctx = Ctx(dd, set_id, params)
        fields, result = RUNNERS[analysis](ctx, rep)
        line = summary_line(code, fields)
        rep.write(line, {"fields": fields, "result": result, "params": params or {}})
        return out.name, line
    except AnalysisError as e:
        line = summary_line(code, {"error": type(e).__name__})
        rep.parts.insert(0, f'<p class="bad">{_esc(str(e))}</p>')
        rep.write(line, {"error": str(e)}, status="failed")
        raise
    except Exception as e:
        line = summary_line(code, {"error": type(e).__name__})
        rep.parts.insert(0, f"<pre>{_esc(traceback.format_exc())}</pre>")
        rep.write(line, {"error": f"{type(e).__name__}: {e}"}, status="failed")
        raise


def _esc(s: str) -> str:
    import html

    return html.escape(s)


# ---------------------------------------------------------------- straight-on view for the Measure page


def measure_view(dd: DataDir, set_id: str, photo: str) -> dict:
    """Straight-on image of one photo (cached) and the data to map clicks.

    Returns {"image": Path, "meta": rect meta, "plane": FacePlane, "front": serial, "calibrated": bool}.
    """
    ctx = Ctx(dd, set_id)
    src = dd.photo_path(set_id, photo)
    cdir = dd.cache_dir / "rect" / set_id
    img_p, meta_p = cdir / f"{photo}.jpg", cdir / f"{photo}.yaml"
    sig = {"version": __version__, "dict": targets.DICT_NAME, "mtime": src.stat().st_mtime, "ppi": float(ctx.site["px_per_in"]),
           "offset": float(ctx.site["face_offset_in"]), "above": float(ctx.site["above_in"])}
    if img_p.exists() and meta_p.exists():
        with open(meta_p, encoding="utf-8") as f:
            m = yaml.safe_load(f) or {}
        if m.get("sig") == sig:
            pz = pose.Pose(np.array(m["R"]), np.array(m["t"]), m["rms"], m["n"], np.array(m["centroid"]))
            plane = rectify.FacePlane(pz, np.array(m["K"]), np.array(m["dist"]), m["offset"])
            return {"image": img_p, "meta": m["rect"], "plane": plane, "front": m["front"],
                    "calibrated": m["calibrated"]}
    ph = load_photo(ctx, photo)
    sd, plane = face_plane(ctx, ph)
    if plane is None:
        raise AnalysisError("no front marker row with enough markers in this photo")
    rect = straight_on(ctx, ph, sd, plane)
    imageio.save_jpeg(rect.image, img_p, quality=92)
    pz = plane.pose
    m = {"sig": sig, "rect": rect.meta(), "R": pz.R.tolist(), "t": pz.t.tolist(), "rms": pz.rms_px, "n": pz.n,
         "centroid": pz.centroid.tolist(), "K": ph.K.tolist(), "dist": np.ravel(ph.dist).tolist(),
         "offset": plane.offset_in, "front": sd.segment.serial, "calibrated": ph.cal is not None}
    with open(meta_p, "w", encoding="utf-8") as f:
        yaml.safe_dump(m, f)
    return {"image": img_p, "meta": m["rect"], "plane": plane, "front": m["front"], "calibrated": m["calibrated"]}


def rect_from_meta(meta: dict) -> rectify.Rectified:
    return rectify.Rectified(np.zeros((1, 1), np.uint8), meta["x0"], meta["y0"], meta["ppi"])


def rerun_all(dd: DataDir, echo=print) -> list[str]:
    """Re-run every applicable analysis on every set with this version."""
    lines = []
    for s in sorted(dd.list_sets(), key=lambda s: s.created):
        for a in applicable(s.type):
            if a == "manual" and not dd.clicks(s.id):
                continue
            if a == "compare" and not s.extra.get("compare_with"):
                continue
            params = {"save": False} if a == "calibrate" else {}
            try:
                _, line = run(dd, s.id, a, params)
            except Exception as e:
                line = summary_line(test_code(a, s.type), {"error": type(e).__name__})
                line += f"  # {e}"
            line = f"{line}  [{s.id}]"
            lines.append(line)
            echo(line)
    return lines
