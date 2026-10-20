"""Flask web app: the main user interface."""

from __future__ import annotations

import datetime as _dt
import hmac
import tempfile
from pathlib import Path

import numpy as np
import yaml
from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file, send_from_directory,
                   url_for)

from .. import __version__, analyses, calib, imageio, report, store, targets, truth
from ..jobs import Job, JobQueue
from ..store import SET_TYPES, DataDir


def package_versions() -> dict:
    from importlib.metadata import PackageNotFoundError, version

    out = {"python": __import__("platform").python_version()}
    for p in ("numpy", "opencv-python-headless", "Pillow", "pillow-heif", "PyYAML", "Flask", "waitress"):
        try:
            out[p] = version(p)
        except PackageNotFoundError:
            out[p] = "missing"
    return out


def create_app(data_dir: Path, password: str | None = None, network_warning: str = "") -> Flask:
    app = Flask(__name__)
    dd = DataDir(Path(data_dir))
    app.config["DD"] = dd
    app.config["MAX_CONTENT_LENGTH"] = int(float(dd.site()["upload_limit_mb"]) * 1024 * 1024) + (1 << 20)
    versions = package_versions()

    def runner(job: Job):
        if job.analysis == "selftest":
            from .. import selftest

            out_lines: list[str] = []
            line = selftest.run(echo=out_lines.append, include_web=False)
            (dd.root / "selftest.txt").write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            return "", line
        return analyses.run(dd, job.set_id, job.analysis, job.params)

    jobs = JobQueue(runner)
    app.config["JOBS"] = jobs

    @app.before_request
    def _auth():
        if not password:
            return None
        a = request.authorization
        if a is not None and a.password is not None and hmac.compare_digest(a.password.encode(), password.encode()):
            return None
        return ("Password required", 401, {"WWW-Authenticate": 'Basic realm="stockscan"'})

    @app.context_processor
    def _globals():
        return {"version": __version__, "data_dir": str(dd.root), "set_types": SET_TYPES,
                "versions": versions}

    def get_set_or_404(sid):
        try:
            return dd.get_set(sid)
        except KeyError:
            abort(404)

    # ------------------------------------------------------------ dashboard

    @app.route("/")
    def dashboard():
        sets = dd.list_sets()
        reps = dd.list_reports()
        today = _dt.date.today().isoformat()
        lines = [r["summary"] for r in reps if r["finished"].startswith(today) and r["summary"]][::-1]
        t1 = t1_line(dd)
        if t1:
            lines.insert(0, t1)
        st = dd.root / "selftest.txt"
        if st.exists() and _dt.date.fromtimestamp(st.stat().st_mtime).isoformat() == today:
            last = [ln for ln in st.read_text(encoding="utf-8").splitlines() if ln.startswith("SS ")]
            if last:
                lines.insert(0, last[-1])
        warnings = []
        if network_warning:
            warnings.append(network_warning)
        if not calib.load_all(dd.cameras_dir):
            warnings.append("No camera calibration yet: run Calibrate camera on a calibration set (test T2).")
        if not dd.registry().prints:
            warnings.append("No targets generated yet (Targets page).")
        counts = {s.id: len(dd.photos(s.id)) for s in sets}
        return render_template("dashboard.html", sets=sets, reports=reps[:30], lines=lines, warnings=warnings,
                               counts=counts, jobs=jobs.list()[:10])

    # ------------------------------------------------------------ sets

    @app.route("/sets/new", methods=["GET", "POST"])
    def new_set():
        if request.method == "POST":
            name = request.form.get("name", "").strip() or "set"
            type_ = request.form.get("type", "other")
            if type_ not in SET_TYPES:
                abort(400)
            sid = dd.create_set(name, type_, request.form.get("notes", ""))
            return redirect(url_for("set_page", sid=sid))
        return render_template("new_set.html", preset=request.args.get("type", ""))

    @app.route("/sets/<sid>")
    def set_page(sid):
        info = get_set_or_404(sid)
        photos = [photo_info(dd, sid, n) for n in dd.photos(sid)]
        cal_keys = {c.camera_key for c in calib.load_all(dd.cameras_dir)}
        for p in photos:
            p["calibrated"] = p.get("key") in cal_keys
        rows = truth.read(dd.truth_path(sid)) if dd.truth_path(sid).exists() else []
        avail = [(a, analyses.ANALYSES[a][0], analyses.test_code(a, info.type)) for a in analyses.applicable(info.type)]
        others = [s for s in dd.list_sets() if s.id != sid]
        return render_template("set.html", s=info, photos=photos, truth=rows, analyses=avail,
                               reports=dd.list_reports(sid), jobs=jobs.list(sid), others=others,
                               clicks=dd.clicks(sid), limit_mb=dd.site()["upload_limit_mb"],
                               error=request.args.get("error", ""), msg=request.args.get("msg", ""))

    @app.route("/sets/<sid>/notes", methods=["POST"])
    def set_notes(sid):
        get_set_or_404(sid)
        dd.update_set(sid, notes=request.form.get("notes", ""))
        return redirect(url_for("set_page", sid=sid))

    @app.route("/sets/<sid>/upload", methods=["POST"])
    def upload(sid):
        get_set_or_404(sid)
        f = request.files.get("file")
        if f is None or not f.filename:
            return jsonify({"status": "rejected", "error": "no file"}), 400
        tmpdir = dd.cache_dir / "upload"
        tmpdir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=tmpdir, delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            f.save(tmp_path)
            status, name = dd.add_photo(sid, f.filename, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        if status == "rejected":
            return jsonify({"status": status, "name": name, "error": "not an image file type"}), 415
        if status == "added":
            try:
                photo_info(dd, sid, name)
            except Exception:
                pass
        return jsonify({"status": status, "name": name})

    @app.route("/sets/<sid>/rescan", methods=["POST"])
    def rescan(sid):
        get_set_or_404(sid)
        r = dd.rescan(sid)
        return redirect(url_for("set_page", sid=sid,
                                msg=f"{r['photos']} photos; renamed {r['renamed']}, "
                                    f"duplicates removed {r['duplicates_removed']}"))

    @app.route("/sets/<sid>/photos/<name>")
    def photo_file(sid, name):
        get_set_or_404(sid)
        try:
            return send_file(dd.photo_path(sid, name))
        except KeyError:
            abort(404)

    @app.route("/sets/<sid>/thumbs/<name>")
    def thumb(sid, name):
        get_set_or_404(sid)
        try:
            return send_file(dd.thumbnail(sid, name), mimetype="image/jpeg")
        except KeyError:
            abort(404)
        except Exception:
            abort(415)

    @app.route("/sets/<sid>/photos/<name>/delete", methods=["POST"])
    def delete_photo(sid, name):
        get_set_or_404(sid)
        try:
            dd.delete_photo(sid, name)
        except KeyError:
            abort(404)
        return redirect(url_for("set_page", sid=sid))

    @app.route("/sets/<sid>/truth", methods=["POST"])
    def save_truth(sid):
        get_set_or_404(sid)
        try:
            up = request.files.get("csv")
            if up and up.filename:
                rows = truth.parse(up.read().decode("utf-8-sig"))
            else:
                rows = []
                idx = request.form.getlist("index")
                ws, hs = request.form.getlist("width"), request.form.getlist("height")
                ls, ns = request.form.getlist("length"), request.form.getlist("note")
                for k in range(len(ws)):
                    if not ws[k].strip() and not hs[k].strip():
                        continue
                    rows.append(truth.TruthRow(
                        int(idx[k]) if k < len(idx) and idx[k].strip() else len(rows) + 1,
                        float(ws[k]), float(hs[k]),
                        float(ls[k]) if k < len(ls) and ls[k].strip() else None,
                        ns[k].strip() if k < len(ns) else ""))
                rows.sort(key=lambda r: r.index)
        except (ValueError, IndexError) as e:
            return redirect(url_for("set_page", sid=sid, error=f"hand measurements not saved: {e}"))
        truth.write(dd.truth_path(sid), rows)
        return redirect(url_for("set_page", sid=sid, msg=f"{len(rows)} hand measurements saved"))

    @app.route("/sets/<sid>/truth.csv")
    def truth_csv(sid):
        get_set_or_404(sid)
        p = dd.truth_path(sid)
        if not p.exists():
            abort(404)
        return send_file(p, mimetype="text/csv", as_attachment=True, download_name="truth.csv")

    @app.route("/sets/<sid>/run/<analysis>", methods=["POST"])
    def run_analysis(sid, analysis):
        info = get_set_or_404(sid)
        if analysis not in analyses.applicable(info.type):
            abort(400)
        params = {}
        if analysis == "compare":
            other = request.form.get("other", "")
            if not other:
                return redirect(url_for("set_page", sid=sid, error="choose a set to compare with"))
            params["other"] = other
        jobs.submit(sid, analysis, params)
        return redirect(url_for("set_page", sid=sid))

    @app.route("/jobs.json")
    def jobs_json():
        sid = request.args.get("set")
        return jsonify([j.to_dict() for j in jobs.list(sid)])

    # ------------------------------------------------------------ measure

    @app.route("/sets/<sid>/measure")
    def measure(sid):
        info = get_set_or_404(sid)
        names = dd.photos(sid)
        photo = request.args.get("photo") or (names[0] if names else "")
        clicks = dd.clicks(sid)
        return render_template("measure.html", s=info, names=names, photo=photo, clicks=clicks)

    @app.route("/sets/<sid>/measure/<name>/image.jpg")
    def measure_image(sid, name):
        get_set_or_404(sid)
        try:
            v = analyses.measure_view(dd, sid, name)
        except (KeyError, analyses.AnalysisError):
            abort(404)
        return send_file(v["image"], mimetype="image/jpeg", max_age=0)

    @app.route("/sets/<sid>/measure/<name>/data", methods=["GET", "POST"])
    def measure_data(sid, name):
        get_set_or_404(sid)
        try:
            v = analyses.measure_view(dd, sid, name)
        except KeyError:
            abort(404)
        except analyses.AnalysisError as e:
            return jsonify({"error": str(e)}), 422
        rect = analyses.rect_from_meta(v["meta"])
        plane = v["plane"]
        if request.method == "POST":
            data = request.get_json(force=True, silent=True) or {}
            faces_px = data.get("faces", [])
            out = []
            for f in faces_px:
                try:
                    c1, r1, c2, r2 = (float(x) for x in f)
                except (TypeError, ValueError):
                    abort(400)
                xy = rect.to_plane(np.array([[min(c1, c2), min(r1, r2)], [max(c1, c2), max(r1, r2)]]))
                uv = plane.to_image(xy)
                out.append({"tl": np.round(uv[0], 3).tolist(), "br": np.round(uv[1], 3).tolist()})
            dd.save_clicks(sid, name, out)
        faces_px = []
        for c in dd.clicks(sid).get(name, []):
            xy, ok = plane.to_plane(np.array([c["tl"], c["br"]], float))
            if ok.all():
                faces_px.append(np.round(rect.to_px(xy).ravel(), 2).tolist())
        return jsonify({"image": url_for("measure_image", sid=sid, name=name), "meta": v["meta"],
                        "front": v["front"], "calibrated": v["calibrated"], "faces": faces_px})

    # ------------------------------------------------------------ reports

    @app.route("/reports/<sid>/<run>/")
    def report_index(sid, run):
        try:
            p = dd.report_path(sid, run)
        except KeyError:
            abort(404)
        return send_from_directory(p, "index.html")

    @app.route("/reports/<sid>/<run>/<path:fname>")
    def report_file(sid, run, fname):
        try:
            p = dd.report_path(sid, run)
        except KeyError:
            abort(404)
        return send_from_directory(p, fname)

    # ------------------------------------------------------------ targets

    @app.route("/targets", methods=["GET", "POST"])
    def targets_page():
        reg = dd.registry()
        sizes = dd.site()["target_sizes"]
        error = ""
        form = {}
        if request.method == "POST":
            form = request.form.to_dict()
            f = request.form
            item = f.get("item", "front")
            page = f.get("page_custom", "").strip() if f.get("page") == "custom" else f.get("page", "letter")
            try:
                first = f.get("first_serial", "").strip().upper().lstrip("S")
                key = {"front": "front_marker", "board": "board_square", "calib": "calib_square"}.get(item)
                square = f.get(f"square_{item}", "").strip() or (sizes.get(key) if key else None)
                targets.generate(
                    reg, dd.targets_dir, item, page or "letter",
                    margin=targets.parse_length(f.get("margin") or "0.5in"),
                    square=square,
                    gap=f.get("gap", "").strip() or sizes.get("front_gap"),
                    sizes=f.get("sizes", "").strip() or sizes.get("sizetest_squares"),
                    rows=f.get("rows", "").strip() or None,
                    cols=f.get("cols_b" if item == "board" else "cols", "").strip() or None,
                    count=int(f.get("count") or 1),
                    marker_ratio=float(sizes.get("marker_ratio", targets.MARKER_RATIO)),
                    first_serial=int(first) if first else None)
                return redirect(url_for("targets_page"))
            except ValueError as e:
                error = str(e)
                reg = dd.registry()
        return render_template("targets.html", prints=reg.prints[::-1], ids_left=reg.ids_left(), n_ids=targets.N_IDS,
                               next_serial=reg.data["next_serial"], error=error, pages=targets.PAGE_SIZES,
                               items=targets.ITEMS, sizes=sizes, form=form, dict_name=targets.DICT_NAME,
                               fmt_length=targets.fmt_length)

    @app.route("/targets/file/<fname>")
    def target_file(fname):
        if store.safe_name(fname) != fname:
            abort(404)
        return send_from_directory(dd.targets_dir, fname, as_attachment=fname.endswith(".pdf"))

    # ------------------------------------------------------------ cameras

    @app.route("/cameras")
    def cameras():
        return render_template("cameras.html", cals=calib.load_all(dd.cameras_dir)[::-1])

    @app.route("/cameras/<fname>/delete", methods=["POST"])
    def delete_camera(fname):
        if store.safe_name(fname) != fname or not fname.endswith(".yaml"):
            abort(404)
        (dd.cameras_dir / fname).unlink(missing_ok=True)
        return redirect(url_for("cameras"))

    # ------------------------------------------------------------ settings

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        site = dd.site()
        reg = dd.registry()
        error = ""
        if request.method == "POST":
            f = request.form
            try:
                for k in ("px_per_in", "face_offset_in", "above_in", "blur_threshold", "upload_limit_mb"):
                    if f.get(k, "").strip():
                        site[k] = float(f[k])
                if not (20 <= site["px_per_in"] <= 400):
                    raise ValueError("straight-on scale must be 20 to 400 px/in")
                ts = dict(site["target_sizes"])
                for k in ("front_marker", "front_gap", "board_square", "calib_square"):
                    v = f.get(f"ts_{k}", "").strip()
                    if v:
                        targets.parse_length(v)  # validate
                        ts[k] = v
                v = f.get("ts_sizetest_squares", "").strip()
                if v:
                    if not targets.parse_lengths(v):
                        raise ValueError("size-test squares: give at least one size")
                    ts["sizetest_squares"] = v
                v = f.get("ts_marker_ratio", "").strip()
                if v:
                    if not 0.3 <= float(v) <= 0.9:
                        raise ValueError("marker ratio must be 0.3 to 0.9")
                    ts["marker_ratio"] = float(v)
                site["target_sizes"] = ts
                scales = {}
                for p in reg.prints:
                    xs, ys = f.get(f"x_{p['serial']}", "").strip(), f.get(f"y_{p['serial']}", "").strip()
                    if xs or ys:
                        scales[p["serial"]] = {"x_measured": round(targets.parse_length(xs), 6) if xs else None,
                                               "y_measured": round(targets.parse_length(ys), 6) if ys else None}
                site["print_scales"] = scales
                labels = {}
                for s in reg.segments():
                    v = f.get(f"label_{s.serial}", "").strip()
                    if v:
                        labels[s.serial] = v
                site["segment_labels"] = labels
                site.save()
                app.config["MAX_CONTENT_LENGTH"] = int(float(site["upload_limit_mb"]) * 1024 * 1024) + (1 << 20)
                return redirect(url_for("settings"))
            except ValueError as e:
                error = f"not saved: {e}"
        mounted = [s for s in reg.segments() if s.kind in ("front", "board")]
        return render_template("settings.html", site=site, prints=reg.prints, mounted=mounted,
                               t1=t1_line(dd), error=error)

    # ------------------------------------------------------------ self-test

    @app.route("/selftest", methods=["GET", "POST"])
    def selftest_page():
        if request.method == "POST":
            jobs.submit("", "selftest")
            return redirect(url_for("selftest_page"))
        p = dd.root / "selftest.txt"
        out = p.read_text(encoding="utf-8") if p.exists() else ""
        return render_template("selftest.html", output=out, jobs=[j for j in jobs.list("") if j.analysis == "selftest"])

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"status": "rejected", "error": "file too large (see Settings)"}), 413

    return app


def t1_line(dd: DataDir) -> str:
    """Summary line of the measured scale bars (field test T1)."""
    site, reg = dd.site(), dd.registry()
    parts = []
    for p in reg.prints:
        m = site["print_scales"].get(p["serial"]) or {}
        nom = p.get("scale_bar_in") or {}
        if m.get("x_measured") or m.get("y_measured"):
            parts.append(f"{p['serial']}={report.fmt(m.get('x_measured'))}/{nom.get('x_in')},"
                         f"{report.fmt(m.get('y_measured'))}/{nom.get('y_in')}")
    return "SS " + __version__ + " T1 " + " ".join(parts) if parts else ""


def photo_info(dd: DataDir, sid: str, name: str) -> dict:
    """Per-photo metadata and sharpness, cached by modification time."""
    src = dd.photo_path(sid, name)
    cp = dd.cache_dir / "info" / sid / f"{name}.yaml"
    mtime = src.stat().st_mtime
    if cp.exists():
        try:
            with open(cp, encoding="utf-8") as f:
                d = yaml.safe_load(f) or {}
            if d.get("mtime") == mtime and d.get("version") == __version__:
                return d
        except Exception:
            pass
    d = {"name": name, "mtime": mtime, "version": __version__, "bytes": src.stat().st_size}
    try:
        m = imageio.read_meta(src)
        d.update({"camera": m.camera, "lens": m.lens_name, "size": f"{m.width}x{m.height}",
                  "key": m.camera_key, "datetime": m.datetime})
        d["sharpness"] = round(imageio.sharpness(imageio.load_gray(src)), 1)
    except Exception as e:
        d["error"] = f"{type(e).__name__}: {e}"
    cp.parent.mkdir(parents=True, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        yaml.safe_dump(d, f)
    return d
