import pytest

from stockscan import report, truth
from stockscan.jobs import JobQueue


def test_parse_truth():
    rows = truth.parse("index,width_in,height_in,length_in,note\n2,0.25,10,,plate\n1,1,2,30.5,\n\n")
    assert [r.index for r in rows] == [1, 2] and rows[0].length == 30.5 and rows[1].note == "plate"
    with pytest.raises(ValueError):
        truth.parse("a,b\n1,2\n")
    with pytest.raises(ValueError):
        truth.parse("width_in,height_in\n1,\n")


def test_align_missing_and_extra():
    t = [(1, 2), (2, 1), (0.5, 0.5), (3, 3), (0.25, 4)]
    m = [(1.01, 2.0), (0.5, 0.52), (3.02, 2.98), (5, 5), (0.26, 4.01)]  # (2,1) missed, (5,5) extra
    pairs = truth.align(m, t)
    matched = {(i, j) for i, j in pairs if i is not None and j is not None}
    assert matched == {(0, 0), (1, 2), (2, 3), (4, 4)}
    assert (None, 1) in pairs and (3, None) in pairs
    st = truth.stats([(0.01, 0), (0, 0.02), (0.02, -0.02), (0.01, 0.01)], 1, 1)
    assert st["n"] == 4 and abs(st["w_max"] - 0.02) < 1e-12 and st["miss"] == 1


def test_summary_line():
    line = report.summary_line("T5M", {"n": 12, "w_med": 0.0201, "w_max": 0.06, "cam": "iPhone 13", "x": None})
    assert line == "SS 0.1.0 T5M n=12 w_med=0.02 w_max=0.06 cam=iPhone_13 x=na"
    ver, test, fields = report.parse_summary(line)
    assert ver == "0.1.0" and test == "T5M" and fields["n"] == "12"
    with pytest.raises(ValueError):
        report.parse_summary("hello")


def test_job_queue():
    def runner(job):
        if job.analysis == "boom":
            raise RuntimeError("bad")
        return "run1", "SS x"

    q = JobQueue(runner)
    ok, bad = q.submit("s", "fine"), q.submit("s", "boom")
    q.wait(ok, 5)
    q.wait(bad, 5)
    assert ok.status == "done" and ok.report == "run1"
    assert bad.status == "failed" and "RuntimeError: bad" in bad.error
    assert [j.id for j in q.list("s")] == [bad.id, ok.id]
    q.stop()
