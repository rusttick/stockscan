"""Marker dictionary: generation, exact guarantees, and the probability formulas.

Small dictionaries keep this fast; the same code paths are used for the
shipped dictionary (see stockscan.markers and `stockscan dictionary check`).
"""

import itertools

import cv2
import numpy as np
import pytest

from stockscan import markers as M


@pytest.fixture(scope="module")
def small():
    """5x5, d >= 7: t = 3."""
    md, _ = M.generate(5, 7, 40, seed=3)
    return md


def brute_force(md):
    """Independent, slow reference: pure Python over bit matrices."""
    mats = [md.bits(i) for i in range(len(md))]
    rots = [[np.rot90(m, r) for r in range(4)] for m in mats]
    hist = [0] * (md.nbits + 1)
    min_pair = min_self = md.nbits
    for i, a in enumerate(mats):
        for j in range(len(mats)):
            for r in range(4):
                if i == j and r == 0:
                    continue
                dist = int((a != rots[j][r]).sum())
                hist[dist] += 1
                if i == j:
                    min_self = min(min_self, dist)
                else:
                    min_pair = min(min_pair, dist)
    return min_pair, min_self, np.array(hist)


# ---------------------------------------------------------------- bits and files


def test_pack_unpack_rotations():
    rng = np.random.default_rng(0)
    for n in (4, 5, 7, 8):
        b = rng.integers(0, 2, (20, n, n)).astype(np.uint8)
        c = M.pack(b)
        assert np.array_equal(M.unpack(c, n), b)
        R = M.rotations(c, n)
        for r in range(4):
            assert np.array_equal(M.unpack(R[:, r], n), np.rot90(b, r, axes=(1, 2)))


def test_popcount_fallback_matches():
    x = np.random.default_rng(1).integers(0, 2 ** 63, 1000, dtype=np.uint64)
    ref = np.array([bin(int(v)).count("1") for v in x])
    assert np.array_equal(M.popcount(x), ref)


def test_save_load_roundtrip_and_checksum(small, tmp_path):
    p = tmp_path / "d.txt"
    small.save(p)
    back = M.MarkerDictionary.load(p)
    assert np.array_equal(back.codes, small.codes) and back.n == 5 and back.min_distance == 7
    lines = p.read_text().splitlines()
    lines[-1] = format(int(lines[-1], 16) ^ 1, "07x")
    p.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="checksum"):
        M.MarkerDictionary.load(p)
    (tmp_path / "x.txt").write_text("hello\n")
    with pytest.raises(ValueError):
        M.MarkerDictionary.load(tmp_path / "x.txt")


# ---------------------------------------------------------------- generation


def test_generation_is_deterministic_and_prefix_stable():
    a, _ = M.generate(6, 9, 60, seed=11)
    b, _ = M.generate(6, 9, 60, seed=11)
    c, _ = M.generate(6, 9, 120, seed=11)
    d, _ = M.generate(6, 9, 60, seed=11, batch=97)  # batch size does not change the result
    assert np.array_equal(a.codes, b.codes)
    assert np.array_equal(c.codes[:60], a.codes)
    assert np.array_equal(d.codes, a.codes)
    e, _ = M.generate(6, 9, 60, seed=12)
    assert not np.array_equal(e.codes, a.codes)


def test_generation_meets_constraints(small):
    ver = M.verify(small)
    assert ver.min_distance >= 7 and ver.duplicates == 0
    b = np.stack([small.bits(i) for i in range(len(small))])
    assert M.pattern_ok(b, 0.3, 0.7, 0.4).all()


def test_impossible_target_fails_loudly():
    with pytest.raises(ValueError, match="only"):
        M.generate(4, 9, 50, seed=1, max_candidates=5000)


# ---------------------------------------------------------------- exact verification


def test_verify_matches_brute_force(small):
    ver = M.verify(small, chunk=7)  # odd chunk size exercises the chunking
    mp, ms, hist = brute_force(small)
    assert (ver.min_pair_distance, ver.min_self_distance) == (mp, ms)
    assert np.array_equal(ver.histogram, hist)
    assert ver.histogram.sum() == len(small) * (4 * len(small) - 1)


def test_verify_detects_a_bad_dictionary(small):
    bad = M.MarkerDictionary(5, small.codes.copy(), 7)
    bad.codes[5] = M.rotations(bad.codes[2:3], 5)[0, 1] ^ np.uint64(1)  # 1 bit from a rotation of marker 2
    ver = M.verify(bad)
    assert ver.min_pair_distance == 1


# ---------------------------------------------------------------- exact guarantees, exhaustively


def all_error_masks(nbits, k):
    for pos in itertools.combinations(range(nbits), k):
        yield sum(1 << p for p in pos)


def test_every_error_up_to_r_is_corrected(small):
    """Exhaustive: every marker, every rotation, every error pattern of weight <= r."""
    ver = M.verify(small)
    r = (ver.min_distance - 1) // 2
    R = M.rotations(small.codes, 5)
    masks = np.array([m for k in range(r + 1) for m in all_error_masks(25, k)], np.uint64)
    for i in range(len(small)):
        for rot in range(4):
            ids, rots = M.decode(small, R[i, rot] ^ masks, r, R)
            assert (ids == i).all() and (rots == rot).all()


def test_never_misidentified_up_to_d_minus_r_minus_1(small):
    """Exhaustive over every marker and every error pattern of weight r+1 .. d-r-1."""
    ver = M.verify(small)
    d = ver.min_distance
    r = 2  # a cautious decoder: then up to d - 3 errors are never misread
    R = M.rotations(small.codes, 5)
    masks = np.array([m for k in range(r + 1, d - r) for m in all_error_masks(25, k)], np.uint64)
    for i in range(0, len(small), 3):
        ids, rots = M.decode(small, R[i, 0] ^ masks, r, R)
        assert ((ids == -1) | ((ids == i) & (rots == 0))).all()


def test_guarantee_is_tight(small):
    """At d - r errors a misidentification is possible: the bound cannot be improved."""
    ver = M.verify(small)
    d, r = ver.min_distance, 2
    R = M.rotations(small.codes, 5)
    flat = R.reshape(-1)
    D = M.popcount(small.codes[:, None] ^ flat[None, :]).reshape(len(small), len(small), 4)
    D[np.arange(len(small)), np.arange(len(small)), 0] = 99
    i, j, rot = np.unravel_index(np.argmin(D), D.shape)
    assert D[i, j, rot] == d
    diff = int(small.codes[i] ^ R[j, rot])
    pos = [b for b in range(25) if diff >> b & 1][:d - r]
    received = small.codes[i] ^ np.uint64(sum(1 << b for b in pos))  # d - r errors toward the neighbour
    ids, _ = M.decode(small, np.array([received], np.uint64), r, R)
    assert ids[0] == j


# ---------------------------------------------------------------- probability formulas vs Monte Carlo


def test_ball_volume():
    assert M.ball_volume(25, 0) == 1 and M.ball_volume(25, 1) == 26
    assert M.ball_volume(10, 10) == 1024


def test_random_false_positive_formula(small):
    """Measurable here (5x5, r = 3); the same formula is exact for any r <= t."""
    ver = M.verify(small)
    r = 3
    g = M.guarantees(small, ver, r)
    trials = 200_000
    fp = M.random_false_positives(small, r, trials, np.random.default_rng(5))
    lo, hi = M.wilson(fp, trials)
    assert lo <= g["random_false_positive"] <= hi, (fp / trials, g["random_false_positive"])


@pytest.mark.parametrize("p", [0.08, 0.15])
def test_flip_noise_formula(small, p):
    ver = M.verify(small)
    r = 2
    g = M.guarantees(small, ver, r, flip_ps=(p,))["flip_noise"][p]
    mc = M.monte_carlo(small, r, p, 200_000, np.random.default_rng(7))
    for key in ("correct", "rejected", "misid"):
        lo, hi = M.wilson(mc[key], mc["trials"])
        assert lo <= g[key] <= hi, (key, mc[key] / mc["trials"], g[key])
    assert mc["misid"] > 0  # the test can actually see misidentifications at this noise


def test_radius_above_t_rejected(small):
    ver = M.verify(small)
    with pytest.raises(ValueError):
        M.guarantees(small, ver, (ver.min_distance - 1) // 2 + 1)


def test_wilson():
    lo, hi = M.wilson(50, 100)
    assert lo < 0.5 < hi and M.wilson(0, 100)[0] == 0.0
    lo, hi = M.wilson(10, 10 ** 6)
    assert lo < 1e-5 < hi and hi < 4e-5
    # coverage: the 99.9 % interval contains the true rate in nearly all repeated experiments
    rng = np.random.default_rng(0)
    p, n = 0.003, 20_000
    miss = sum(not (M.wilson(int(k), n)[0] <= p <= M.wilson(int(k), n)[1]) for k in rng.binomial(n, p, 2000))
    assert miss <= 10


# ---------------------------------------------------------------- OpenCV agrees


def test_opencv_identify_agrees(small):
    """OpenCV's own decoder, with maxCorrectionBits = t and rate 1: same ID as ours."""
    ver = M.verify(small)
    t = (ver.min_distance - 1) // 2
    cvd = small.to_opencv(t)
    rng = np.random.default_rng(9)
    rot_map = {}
    for i in range(len(small)):
        for rot in range(4):
            b = np.rot90(small.bits(i), rot).copy()
            ok, idx, cvrot = cvd.identify(b, 1.0)
            assert ok and idx == i
            rot_map.setdefault(rot, set()).add(cvrot)
            for k in range(1, t + 1):  # k errors, k <= t: still identified
                e = b.copy().ravel()
                e[rng.choice(25, k, replace=False)] ^= 1
                ok, idx, _ = cvd.identify(e.reshape(5, 5), 1.0)
                assert ok and idx == i
    assert all(len(v) == 1 for v in rot_map.values()) and len({next(iter(v)) for v in rot_map.values()}) == 4


def test_opencv_detects_rendered_markers():
    """Render markers, detect with the custom dictionary: every ID right, nothing else found."""
    md, _ = M.generate(7, 11, 48, seed=2)
    cvd = md.to_opencv()
    params = cv2.aruco.DetectorParameters()
    det = cv2.aruco.ArucoDetector(cvd, params)
    cell, quiet = 8, 2
    side = (md.n + 2 + 2 * quiet) * cell
    sheet = np.full((6 * side, 8 * side), 255, np.uint8)
    for k in range(len(md)):
        img = cv2.aruco.generateImageMarker(cvd, k, (md.n + 2) * cell)
        r, c = divmod(k, 8)
        sheet[r * side + quiet * cell:r * side + quiet * cell + img.shape[0],
              c * side + quiet * cell:c * side + quiet * cell + img.shape[1]] = img
    sheet = cv2.GaussianBlur(sheet, (0, 0), 1.0)
    _, ids, _ = det.detectMarkers(sheet)
    assert sorted(ids.ravel().tolist()) == list(range(len(md)))
    # random textures: nothing should be detected
    rng = np.random.default_rng(4)
    found = 0
    for _ in range(10):
        noise = cv2.resize((rng.random((60, 80)) < 0.5).astype(np.uint8) * 255, (800, 600),
                           interpolation=cv2.INTER_NEAREST)
        _, ids, _ = det.detectMarkers(noise)
        found += 0 if ids is None else len(ids)
    assert found == 0
