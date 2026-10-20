import pytest
import yaml

from stockscan import analyses, report


def run(dd, sid, a, params=None):
    run_name, line = analyses.run(dd, sid, a, params)
    return dd.report_path(sid, run_name), report.parse_summary(line)


def test_calibrate_and_inspect(populated):
    dd, sets = populated
    path, (_, code, f) = run(dd, sets["calibration"], "calibrate")
    assert code == "T2" and float(f["rms"]) < 0.5 and abs(float(f["fx"]) - 1800) < 18
    assert (path / "index.html").exists()
    res = yaml.safe_load((path / "result.yaml").read_text())
    assert res["status"] == "done" and res["summary"].startswith("SS ")
    _, (_, code, f) = run(dd, sets["front"], "inspect")
    assert code == "IN" and f["cal"] == "2" and f["seg_found"] == "2"


def test_plane_check(populated):
    dd, sets = populated
    _, (_, code, f) = run(dd, sets["plane_check"], "plane_check")
    assert code == "T4" and float(f["scale_max"].rstrip("%")) < 0.5


def test_manual_and_faces(populated):
    dd, sets = populated
    _, (_, code, f) = run(dd, sets["front"], "manual")
    assert code == "T5M" and float(f["w_max"]) < 0.02 and float(f["h_max"]) < 0.02 and f["miss"] == "0"
    _, (_, code, f) = run(dd, sets["front"], "faces")
    a, b = f["found"].split("/")
    assert code == "T5A" and a == b and float(f["w_max"]) < 0.05


def test_size_test(populated):
    dd, sets = populated
    _, (_, code, f) = run(dd, sets["size_test"], "size_test")
    assert code == "T3" and f["nodet"] == "0" and "b2ft" in f


def test_failure_writes_report(populated):
    dd, _ = populated
    sid = dd.create_set("empty", "calibration")
    with pytest.raises(analyses.AnalysisError):
        analyses.run(dd, sid, "calibrate")
    rep = dd.list_reports(sid)[0]
    assert rep["status"] == "failed" and "error=AnalysisError" in rep["summary"]


def test_rerun(populated):
    dd, _ = populated
    lines = analyses.rerun_all(dd, echo=lambda _l: None)
    assert lines and all(ln.startswith("SS ") for ln in lines)
    assert not any("error=" in ln for ln in lines if "_empty" not in ln)
