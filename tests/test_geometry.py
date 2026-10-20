import cv2
import numpy as np

from stockscan import pose, rectify, selftest, synth


def test_undistort_inverts_projection():
    rng = np.random.default_rng(0)
    xy = rng.uniform(-0.6, 0.5, (200, 2))
    uv, _ = cv2.projectPoints(np.column_stack([xy, np.ones(200)]), np.zeros(3), np.zeros(3), synth.K_TRUE, synth.DIST_TRUE)
    assert np.abs(pose.undistort_normalized(uv.reshape(-1, 2), synth.K_TRUE, synth.DIST_TRUE) - xy).max() < 1e-9


def test_plane_roundtrip():
    R, t = synth.look_at([3, -4, -30], [5, 0, 0])
    pz = pose.Pose(R, t, 0.0, 0, np.zeros(3))
    plane = rectify.FacePlane(pz, synth.K_TRUE, synth.DIST_TRUE, offset_in=0.4)
    xy = np.array([[0, 0], [10, -3], [-2, 1.5]], float)
    back, ok = plane.to_plane(plane.to_image(xy))
    assert ok.all() and np.abs(back - xy).max() < 1e-7


def test_pose(tmp_path):
    selftest.check_pose(tmp_path)


def test_calibration(tmp_path):
    selftest.check_calibration(tmp_path)


def test_rectify_and_faces(tmp_path):
    selftest.check_rectify(tmp_path)


def test_approx_intrinsics():
    K, d = pose.approx_intrinsics(4032, 3024, focal35=26)
    assert abs(K[0, 0] - 26 / 43.27 * 5040) < 1 and not d.any()
