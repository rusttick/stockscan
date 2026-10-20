"""Command line: serve / selftest / rerun."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

from . import __version__

DEFAULT_PORT = 3000


def default_data_dir() -> Path:
    return Path.home() / "stockscan-data"


def addresses(port: int) -> list[str]:
    """URLs this computer can probably be reached at."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # the address of the default route; sends no packets
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    ips.discard("127.0.0.1")
    return [f"http://{ip}:{port}/" for ip in sorted(ips)]


def cmd_serve(args) -> int:
    from waitress import serve

    from .web.app import create_app

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    data = Path(args.data).expanduser()
    local_only = args.host in ("127.0.0.1", "localhost", "::1")
    warning = ""
    if not local_only and not args.password:
        warning = ("Reachable from the network WITHOUT a password: anyone on the network can upload and delete. "
                   "Use --password, or --host 127.0.0.1 for this computer only.")
    app = create_app(data, password=args.password, network_warning=warning)
    print(f"stockscan {__version__}")
    print(f"data directory: {app.config['DD'].root}")
    print(f"open: http://localhost:{args.port}/")
    if not local_only:
        for url in addresses(args.port):
            print(f"  or: {url}  (from other devices, if the network allows)")
    if warning:
        print("WARNING: " + warning)
    print("Ctrl+C to stop.")
    serve(app, host=args.host, port=args.port, threads=8, max_request_body_size=2 * 1024 ** 3,
          channel_timeout=300, ident=f"stockscan/{__version__}")
    return 0


def cmd_selftest(args) -> int:
    from . import selftest

    line = selftest.run(include_web=not args.no_web)
    a, b = line.split("pass=")[1].split("/")
    return 0 if a == b else 1


def cmd_rerun(args) -> int:
    from . import analyses
    from .store import DataDir

    dd = DataDir(Path(args.data).expanduser())
    lines = analyses.rerun_all(dd)
    print(f"# {len(lines)} reports")
    return 0


def cmd_dictionary_check(args) -> int:
    import numpy as np

    from . import markers
    from .report import summary_line

    md = markers.MarkerDictionary.load(Path(args.file)) if args.file else markers.default()
    r = markers.CORRECTION_BITS if args.r is None else args.r
    print(f"{md.name}: {len(md)} markers, {md.n}x{md.n} cells, seed {md.seed}, sha256 {md.digest()}")
    if not args.file:
        print("checksum matches the pinned value: yes")
    ver = markers.verify(md)
    print(f"exhaustive check: min distance between markers (all rotations) {ver.min_pair_distance}, "
          f"to own rotations {ver.min_self_distance}, duplicates {ver.duplicates}")
    g = markers.guarantees(md, ver, r, flip_ps=(0.01, 0.02, 0.05, 0.1, 0.15))
    print(f"\nexact, with a decoder correcting up to r = {r} cells (t = {g['t']}):")
    print(f"  up to {g['always_correct_up_to']} wrong cells: always identified correctly (ID and rotation)")
    print(f"  up to {g['never_misidentified_up_to']} wrong cells: never identified as another marker or rotation")
    print("\nprobabilistic (assumptions: uniformly random patterns / independent cell errors):")
    print(f"  random pattern accepted as some marker: {g['random_false_positive']:.2e}")
    print("  cell error rate   correct     rejected    misidentified")
    for pr, v in g["flip_noise"].items():
        print(f"  {pr:>14.2f}   {v['correct']:.6f}   {v['rejected']:.3e}   {v['misid']:.3e}")
    if args.mc:
        rng = np.random.default_rng(1)
        print(f"\nMonte Carlo, {args.mc} trials per line (99.9 % intervals):")
        for pr in (0.05, 0.1, 0.15):
            mc = markers.monte_carlo(md, r, pr, args.mc, rng)
            f = g["flip_noise"][pr]
            for key in ("correct", "rejected", "misid"):
                lo, hi = markers.wilson(mc[key], mc["trials"])
                ok = lo <= f[key] <= hi
                print(f"  p={pr:.2f} {key:9s} observed {mc[key] / mc['trials']:.3e} [{lo:.2e}, {hi:.2e}] "
                      f"formula {f[key]:.3e} {'ok' if ok else 'OUTSIDE'}")
        fp = markers.random_false_positives(md, r, args.mc, rng)
        print(f"  random patterns accepted: {fp} of {args.mc} (expected {g['random_false_positive'] * args.mc:.2g})")
    print()
    print(summary_line("DICT", {"name": md.name, "n": len(md), "d": ver.min_distance, "r": r,
                                "safe": g["never_misidentified_up_to"], "fp": f"{g['random_false_positive']:.1e}",
                                "sha": md.digest()[:8]}))
    return 0 if ver.min_distance >= md.min_distance and ver.duplicates == 0 else 1


def cmd_dictionary_generate(args) -> int:
    from . import markers

    md, st = markers.generate(args.n, args.d, args.count, seed=args.seed)
    md.save(Path(args.out))
    ver = markers.verify(md)
    print(f"wrote {args.out}: {len(md)} markers, verified min distance {ver.min_distance}, "
          f"{st.candidates} candidates tried, sha256 {md.digest()}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="stockscan", description="Photo inventory of machine shop metal stock.")
    p.add_argument("--version", action="version", version=f"stockscan {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--data", default=str(default_data_dir()), help="data directory (default: %(default)s)")
    s.add_argument("--host", default="0.0.0.0", help="address to listen on; 127.0.0.1 = this computer only")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--password", default=None, help="require this password (HTTP basic auth, any user name)")
    s.set_defaults(fn=cmd_serve)
    t = sub.add_parser("selftest", help="synthetic tests; check the install")
    t.add_argument("--no-web", action="store_true", help="skip the web app check")
    t.set_defaults(fn=cmd_selftest)
    r = sub.add_parser("rerun", help="re-run all photo sets with this version; print all summary lines")
    r.add_argument("--data", default=str(default_data_dir()), help="data directory (default: %(default)s)")
    r.set_defaults(fn=cmd_rerun)
    d = sub.add_parser("dictionary", help="check the marker dictionary and print its guarantees")
    dsub = d.add_subparsers(dest="dcmd", required=True)
    dc = dsub.add_parser("check", help="exhaustive check and guarantees (default: the shipped dictionary)")
    dc.add_argument("--file", help="a dictionary file instead of the shipped one")
    dc.add_argument("--r", type=int, help="decoder correction radius (default: the one detection uses)")
    dc.add_argument("--mc", type=int, default=0, metavar="N", help="also run N Monte Carlo trials per check")
    dc.set_defaults(fn=cmd_dictionary_check)
    dg = dsub.add_parser("generate", help="generate a dictionary (prefix-stable: same seed keeps existing IDs)")
    dg.add_argument("--n", type=int, default=8)
    dg.add_argument("--d", type=int, default=17)
    dg.add_argument("--count", type=int, default=20000)
    dg.add_argument("--seed", type=int, default=1)
    dg.add_argument("--out", required=True)
    dg.set_defaults(fn=cmd_dictionary_generate)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
