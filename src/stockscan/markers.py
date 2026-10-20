"""Custom marker dictionaries with verified error-correction guarantees.

OpenCV's predefined dictionaries stop at 1000 IDs.  This module generates
larger ones for n x n bit markers by a deterministic greedy search, saves
them as plain text, and *proves* their properties instead of assuming them.

Guarantees, in two kinds:

Exact (checked exhaustively over every pair of markers and every rotation):
  d   minimum Hamming distance between any marker and any rotation of any
      other marker, and between a marker and its own 90/180/270 degree
      rotations (so the orientation is never ambiguous).
  With a decoder that accepts patterns within r bit errors of a marker
  (r <= t = (d - 1) // 2):
  * any pattern with <= r wrong bits is identified correctly (ID and rotation);
  * any pattern with <= d - r - 1 wrong bits is never identified as a
    different marker or rotation (it is either correct or rejected).

Probabilistic (formulas, with their assumptions, validated by Monte Carlo):
  * random-pattern false positives: a uniformly random n*n bit pattern
    is accepted with probability exactly 4 N V(n*n, r) / 2^(n*n), where
    V is the Hamming ball volume (exact because the balls are disjoint
    when r <= t);
  * outcome under independent bit flips with probability p: correct,
    rejected or misidentified, exact for that model (from the full
    distance distribution; the radius-r balls are disjoint).
  Real errors (blur, glare, occlusion) are not independent bit flips; the
  exact guarantees above do not depend on that assumption, these two do.

Greedy generation is prefix-stable: the first k markers of a dictionary
generated with a given seed and parameters do not depend on how many are
generated, so a dictionary can later be extended without renumbering.

Bit convention: bit i of a code is cell (i // n, i % n), row-major; 1 is a
white cell, as in OpenCV.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FORMAT = "stockscan-marker-dictionary-1"


# ---------------------------------------------------------------- bit helpers


def _weights(n: int) -> np.ndarray:
    return np.uint64(1) << np.arange(n * n, dtype=np.uint64)


def pack(bits: np.ndarray) -> np.ndarray:
    """(..., n, n) 0/1 -> (...,) uint64."""
    n = bits.shape[-1]
    flat = bits.reshape(bits.shape[:-2] + (n * n,)).astype(np.uint64)
    return np.bitwise_or.reduce(flat * _weights(n), axis=-1)


def unpack(codes: np.ndarray, n: int) -> np.ndarray:
    """(...,) uint64 -> (..., n, n) uint8."""
    codes = np.asarray(codes, np.uint64)
    b = (codes[..., None] >> np.arange(n * n, dtype=np.uint64)) & np.uint64(1)
    return b.astype(np.uint8).reshape(codes.shape + (n, n))


def rotations(codes: np.ndarray, n: int) -> np.ndarray:
    """(N,) -> (N, 4): the code rotated by 0, 90, 180, 270 degrees (np.rot90 sense)."""
    b = unpack(codes, n)
    return np.stack([pack(np.rot90(b, r, axes=(-2, -1))) for r in range(4)], axis=-1)


def popcount(x: np.ndarray) -> np.ndarray:
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x)
    # numpy < 2.0
    x = x.astype(np.uint64)
    x = x - ((x >> np.uint64(1)) & np.uint64(0x5555555555555555))
    x = (x & np.uint64(0x3333333333333333)) + ((x >> np.uint64(2)) & np.uint64(0x3333333333333333))
    x = (x + (x >> np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
    return ((x * np.uint64(0x0101010101010101)) >> np.uint64(56)).astype(np.uint8)


def ball_volume(nbits: int, r: int) -> int:
    return sum(math.comb(nbits, i) for i in range(r + 1))


# ---------------------------------------------------------------- dictionary


@dataclass
class MarkerDictionary:
    n: int  # bits per side
    codes: np.ndarray  # (N,) uint64, rotation 0
    min_distance: int  # design target; verify() proves the actual value
    seed: int = 0
    params: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.codes)

    @property
    def nbits(self) -> int:
        return self.n * self.n

    @property
    def name(self) -> str:
        return f"SS{self.n}x{self.n}_d{self.min_distance}_{len(self)}"

    def bits(self, i: int) -> np.ndarray:
        return unpack(self.codes[i], self.n)

    def digest(self) -> str:
        return hashlib.sha256(self.codes.astype("<u8").tobytes()).hexdigest()

    def prefix(self, k: int) -> "MarkerDictionary":
        return MarkerDictionary(self.n, self.codes[:k].copy(), self.min_distance, self.seed, dict(self.params))

    def to_opencv(self, max_correction_bits: int | None = None):
        """An OpenCV aruco Dictionary with these markers.

        OpenCV corrects up to floor(maxCorrectionBits * errorCorrectionRate)
        bits; max_correction_bits defaults to t = (d - 1) // 2.
        """
        import cv2

        t = (self.min_distance - 1) // 2 if max_correction_bits is None else max_correction_bits
        bl = np.vstack([cv2.aruco.Dictionary.getByteListFromBits(self.bits(i)) for i in range(len(self))])
        return cv2.aruco.Dictionary(bl, self.n, int(t))

    # ------------------------------------------------------------ files

    def save(self, path: Path):
        lines = [
            f"# {FORMAT}",
            f"# n: {self.n}",
            f"# count: {len(self)}",
            f"# min_distance: {self.min_distance}",
            f"# seed: {self.seed}",
            f"# params: {' '.join(f'{k}={v}' for k, v in sorted(self.params.items()))}",
            f"# sha256: {self.digest()}",
            "# one marker per line: hex code, bit i = cell (i // n, i % n), 1 = white",
        ]
        width = (self.nbits + 3) // 4
        lines += [f"{int(c):0{width}x}" for c in self.codes]
        Path(path).write_text("\n".join(lines) + "\n", encoding="ascii")

    @classmethod
    def load(cls, path: Path) -> "MarkerDictionary":
        text = Path(path).read_text(encoding="ascii")
        if not text.startswith(f"# {FORMAT}\n"):
            raise ValueError(f"{path}: not a {FORMAT} file")
        head, codes = {}, []
        for ln in text.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if ln.startswith("#"):
                if ":" in ln:
                    k, v = ln[1:].split(":", 1)
                    head[k.strip()] = v.strip()
                continue
            codes.append(int(ln, 16))
        params = dict(p.split("=", 1) for p in head.get("params", "").split() if "=" in p)
        md = cls(int(head["n"]), np.array(codes, np.uint64), int(head["min_distance"]), int(head.get("seed", 0)),
                 params)
        if len(md) != int(head["count"]):
            raise ValueError(f"{path}: {len(md)} codes, header says {head['count']}")
        if head.get("sha256") and head["sha256"] != md.digest():
            raise ValueError(f"{path}: checksum mismatch (file damaged or edited)")
        return md


# ---------------------------------------------------------------- generation


def pattern_ok(bits: np.ndarray, min_white: float, max_white: float, min_transitions: float) -> np.ndarray:
    """Reject nearly uniform markers, which look like ordinary dark or light patches.

    bits: (B, n, n). min_transitions is a fraction of the 2 n (n - 1)
    neighbouring cell pairs that must differ.
    """
    n = bits.shape[-1]
    frac = bits.mean(axis=(-2, -1))
    trans = (bits[..., :, 1:] != bits[..., :, :-1]).sum(axis=(-2, -1)) + \
            (bits[..., 1:, :] != bits[..., :-1, :]).sum(axis=(-2, -1))
    return (frac >= min_white) & (frac <= max_white) & (trans >= min_transitions * 2 * n * (n - 1))


@dataclass
class GenerationStats:
    candidates: int = 0
    rejected_pattern: int = 0
    rejected_self: int = 0
    rejected_distance: int = 0


def generate(n: int, min_distance: int, count: int, seed: int = 1, *, min_white: float = 0.3,
             max_white: float = 0.7, min_transitions: float = 0.4, max_candidates: int | None = None,
             batch: int = 2048) -> tuple[MarkerDictionary, GenerationStats]:
    """Greedy random search: accept each candidate whose distance to every accepted
    marker (all rotations) and to its own rotations is at least min_distance.

    Candidates are examined strictly one after another in the order the seeded
    generator produces them, so the result is prefix-stable and reproducible.
    Stops at `count` markers or after `max_candidates` (raising ValueError).
    """
    if not (3 <= n <= 8):
        raise ValueError("n must be 3 to 8 (codes are stored in 64 bits)")
    rng = np.random.default_rng(seed)
    acc = np.zeros(0, np.uint64)  # every rotation of every accepted marker
    accepted: list[np.uint64] = []
    st = GenerationStats()
    max_candidates = max_candidates or 200 * count + 100000
    d = min_distance
    while len(accepted) < count:
        if st.candidates >= max_candidates:
            raise ValueError(f"only {len(accepted)} markers of {n}x{n} at distance {d} after "
                             f"{st.candidates} candidates")
        b = (rng.random((batch, n, n)) < 0.5).astype(np.uint8)
        good = pattern_ok(b, min_white, max_white, min_transitions)
        R = rotations(pack(b), n)  # (B, 4)
        selfd = np.min(popcount(R[:, :1] ^ R[:, 1:]), axis=1)
        # distance of every candidate to all markers accepted before this batch
        dm = np.full(batch, 255, np.int64)
        for s in range(0, len(acc), 1 << 16):
            dm = np.minimum(dm, popcount(R[:, :1] ^ acc[None, s:s + (1 << 16)]).min(axis=1))
        new = np.zeros(0, np.uint64)
        for i in range(batch):
            st.candidates += 1
            if not good[i]:
                st.rejected_pattern += 1
            elif selfd[i] < d:
                st.rejected_self += 1
            elif dm[i] < d or (len(new) and popcount(R[i, 0] ^ new).min() < d):
                st.rejected_distance += 1
            else:
                accepted.append(R[i, 0])
                new = np.concatenate([new, R[i]])
                if len(accepted) == count:
                    break
            if st.candidates >= max_candidates:
                break
        acc = np.concatenate([acc, new])
    params = {"min_white": min_white, "max_white": max_white, "min_transitions": min_transitions,
              "batch": batch}
    return MarkerDictionary(n, np.array(accepted, np.uint64), d, seed, params), st


# ---------------------------------------------------------------- verification


@dataclass
class Verification:
    count: int
    min_pair_distance: int  # over all pairs, all rotations
    min_self_distance: int  # marker vs its own rotations
    histogram: np.ndarray  # histogram[k] = ordered (marker, other marker or own rotation) pairs at distance k
    duplicates: int

    @property
    def min_distance(self) -> int:
        return min(self.min_pair_distance, self.min_self_distance)


def verify(md: MarkerDictionary, chunk: int = 256) -> Verification:
    """Exhaustive: every marker against every rotation of every marker."""
    n, N, nb = md.n, len(md), md.nbits
    R = rotations(md.codes, n)  # (N, 4)
    flat = R.reshape(-1)
    hist = np.zeros(nb + 1, np.int64)
    min_pair = nb
    for s in range(0, N, chunk):
        e = min(N, s + chunk)
        rows, cols = np.arange(e - s), np.arange(s, e)
        D = popcount(md.codes[s:e, None] ^ flat[None, :]).reshape(e - s, N, 4)  # uint8
        own = D[rows, cols].copy()  # (rows, 4): distances to its own rotations
        D[rows, cols] = 255  # exclude the marker itself from the pair statistics
        min_pair = min(min_pair, int(D.min()))
        hist += np.bincount(D.ravel(), minlength=256)[:nb + 1]
        hist += np.bincount(own[:, 1:].ravel(), minlength=nb + 1)[:nb + 1]  # wrong orientation is an error too
    selfd = int(popcount(R[:, :1] ^ R[:, 1:]).min()) if N else nb
    dup = N - len(np.unique(md.codes))
    return Verification(N, int(min_pair), selfd, hist, dup)


# ---------------------------------------------------------------- guarantees


def _binom_pmf(k, nn, p):
    return math.comb(nn, k) * p ** k * (1 - p) ** (nn - k)


def p_land_within(D: int, nbits: int, r: int, p: float) -> float:
    """P(sent word + independent flips lands within r of a word at distance D)."""
    tot = 0.0
    for k in range(D + 1):  # flips inside the D differing positions
        pk = _binom_pmf(k, D, p)
        for l in range(nbits - D + 1):  # flips outside them
            if D - k + l <= r:
                tot += pk * _binom_pmf(l, nbits - D, p)
            else:
                break
    return tot


def guarantees(md: MarkerDictionary, ver: Verification, r: int,
               flip_ps: tuple[float, ...] = (0.01, 0.02, 0.05, 0.1)) -> dict:
    """Guarantees for a decoder accepting up to r bit errors."""
    d = ver.min_distance
    t = (d - 1) // 2
    if r > t:
        raise ValueError(f"correction radius {r} exceeds t = {t}: decoding would be ambiguous")
    nb, N = md.nbits, len(md)
    fp = 4 * N * ball_volume(nb, r) / 2.0 ** nb
    per_p = {}
    for p in flip_ps:
        correct = sum(_binom_pmf(k, nb, p) for k in range(r + 1))
        mis = sum(int(c) * p_land_within(D, nb, r, p) for D, c in enumerate(ver.histogram) if c) / N
        # exact under the model: the radius-r balls are disjoint, so the events add up
        per_p[p] = {"correct": correct, "misid": mis, "rejected": max(0.0, 1 - correct - mis)}
    return {
        "markers": N, "bits": nb, "d": d, "t": t, "r": r,
        "always_correct_up_to": r,
        "never_misidentified_up_to": d - r - 1,
        "random_false_positive": fp,
        "flip_noise": per_p,
    }


def decode(md: MarkerDictionary, patterns: np.ndarray, r: int, R: np.ndarray | None = None):
    """Nearest-marker decoder (reference): patterns (M,) uint64 ->
    (id or -1, rotation or -1). Accepts the closest marker+rotation within r."""
    R = rotations(md.codes, md.n) if R is None else R
    flat = R.reshape(-1)
    ids = np.full(len(patterns), -1, np.int64)
    rots = np.full(len(patterns), -1, np.int64)
    for s in range(0, len(patterns), 256):
        D = popcount(patterns[s:s + 256, None] ^ flat[None, :])
        j = D.argmin(axis=1)
        ok = D[np.arange(len(j)), j] <= r
        # pattern = marker rotated by k  <=>  pattern matches rotation k of the marker
        ids[s:s + 256] = np.where(ok, j // 4, -1)
        rots[s:s + 256] = np.where(ok, j % 4, -1)
    return ids, rots


def flip_bits(codes: np.ndarray, nbits: int, k: int, rng: np.random.Generator) -> np.ndarray:
    """Flip exactly k distinct random bits in each code."""
    M = len(codes)
    pos = np.argsort(rng.random((M, nbits)), axis=1)[:, :k].astype(np.uint64)
    mask = np.bitwise_or.reduce(np.uint64(1) << pos, axis=1) if k else np.zeros(M, np.uint64)
    return codes ^ mask


def monte_carlo(md: MarkerDictionary, r: int, p: float, trials: int, rng: np.random.Generator) -> dict:
    """Independent bit flips with probability p: counts of correct / rejected / misidentified."""
    R = rotations(md.codes, md.n)
    idx = rng.integers(0, len(md), trials)
    rot = rng.integers(0, 4, trials)
    sent = R[idx, rot]
    flips = rng.random((trials, md.nbits)) < p
    mask = pack(flips.reshape(trials, md.n, md.n).astype(np.uint8))
    ids, rots = decode(md, sent ^ mask, r, R)
    correct = int(np.sum((ids == idx) & (rots == rot)))
    rejected = int(np.sum(ids < 0))
    return {"trials": trials, "correct": correct, "rejected": rejected, "misid": trials - correct - rejected}


def random_false_positives(md: MarkerDictionary, r: int, trials: int, rng: np.random.Generator) -> int:
    pats = rng.integers(0, 2, (trials, md.n, md.n)).astype(np.uint8)
    ids, _ = decode(md, pack(pats), r)
    return int(np.sum(ids >= 0))


def wilson(k: int, n: int, z: float = 3.29) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (z = 3.29: 99.9 %)."""
    if n == 0:
        return (0.0, 1.0)
    ph = k / n
    den = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / den
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


# ---------------------------------------------------------------- the shipped dictionary

DEFAULT_NAME = "SS8x8_d17_20000"
DEFAULT_SHA256 = "8c1e221daa0ff98823c39af5f33b91138e7ff0fc152c3c61622a29e4dd74ed91"
DEFAULT_PARAMS = {"n": 8, "min_distance": 17, "count": 20000, "seed": 1}
# Decoder correction radius: up to CORRECTION_BITS wrong cells are corrected;
# up to d - CORRECTION_BITS - 1 = 10 wrong cells are never misread as another marker.
CORRECTION_BITS = 6

_default: MarkerDictionary | None = None
_default_cv = None
_default_rot = None


def default_path() -> Path:
    return Path(__file__).parent / "data" / f"{DEFAULT_NAME}.txt"


def default() -> MarkerDictionary:
    """The dictionary all targets use (checked against the pinned checksum)."""
    global _default
    if _default is None:
        md = MarkerDictionary.load(default_path())
        if md.digest() != DEFAULT_SHA256:
            raise ValueError(f"{default_path()}: not the expected dictionary (checksum differs)")
        _default = md
    return _default


def default_opencv():
    """OpenCV form of the default dictionary, correcting up to CORRECTION_BITS (use errorCorrectionRate 1)."""
    global _default_cv
    if _default_cv is None:
        _default_cv = default().to_opencv(CORRECTION_BITS)
    return _default_cv


def default_rotations() -> np.ndarray:
    """(N, 4) all rotations of the default dictionary (for decode)."""
    global _default_rot
    if _default_rot is None:
        _default_rot = rotations(default().codes, default().n)
    return _default_rot
