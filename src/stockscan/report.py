"""HTML reports, summary lines and result.yaml.

A summary line is `SS <version> <test> k=v k=v ...`: short, numeric, easy to
read aloud and retype.  It is the only thing that leaves the site.
"""

from __future__ import annotations

import datetime as _dt
import html
from pathlib import Path

import numpy as np
import yaml

from . import __version__, imageio


def fmt(v, nd: int = 3) -> str:
    if v is None:
        return "na"
    if isinstance(v, (bool, np.bool_)):
        return "1" if v else "0"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        if not np.isfinite(v):
            return "na"
        s = f"{float(v):.{nd}f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    s = str(v).strip().replace(" ", "_")
    return s or "na"


def summary_line(test: str, fields: dict, version: str = __version__) -> str:
    parts = ["SS", version, test] + [f"{k}={fmt(v)}" for k, v in fields.items()]
    return " ".join(parts)


def parse_summary(line: str) -> tuple[str, str, dict]:
    parts = line.split()
    if len(parts) < 3 or parts[0] != "SS":
        raise ValueError(f"not a summary line: {line!r}")
    fields = {}
    for p in parts[3:]:
        if "=" not in p:
            raise ValueError(f"bad field {p!r}")
        k, v = p.split("=", 1)
        fields[k] = v
    return parts[1], parts[2], fields


CSS = """
body{font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;color:#1d2125;background:#fafafa}
main{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:8px 0}h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid #ddd}
.summary{font:15px ui-monospace,Menlo,Consolas,monospace;background:#fff8d6;border:1px solid #e5d58a;padding:10px;
 white-space:pre-wrap;word-break:break-all}
table{border-collapse:collapse;margin:8px 0;background:#fff}td,th{border:1px solid #ddd;padding:3px 7px;text-align:right}
th{background:#f0f0f0}td.l,th.l{text-align:left}
.bad{color:#b00020;font-weight:600}.ok{color:#0a7a2f}.warn{color:#9a6700}
figure{display:inline-block;margin:6px;vertical-align:top;max-width:100%}
figure img{max-width:100%;border:1px solid #ccc}figcaption{font-size:12px;color:#555}
pre{background:#fff;border:1px solid #ddd;padding:8px;overflow:auto}
a{color:#0b57d0}
"""


class Report:
    def __init__(self, out_dir: Path, title: str, analysis: str, set_id: str):
        self.dir = Path(out_dir)
        self.title = title
        self.analysis = analysis
        self.set_id = set_id
        self.parts: list[str] = []
        self.summary = ""
        self.started = _dt.datetime.now().isoformat(timespec="seconds")
        self._n_img = 0

    def h2(self, text: str):
        self.parts.append(f"<h2>{html.escape(text)}</h2>")

    def p(self, text: str, cls: str = ""):
        c = f' class="{cls}"' if cls else ""
        self.parts.append(f"<p{c}>{html.escape(text)}</p>")

    def pre(self, text: str):
        self.parts.append(f"<pre>{html.escape(text)}</pre>")

    def kv(self, d: dict):
        rows = "".join(f"<tr><th class=l>{html.escape(str(k))}</th><td class=l>{html.escape(str(v))}</td></tr>"
                       for k, v in d.items())
        self.parts.append(f"<table>{rows}</table>")

    def table(self, headers: list[str], rows: list[list], left: int = 1):
        """left: number of leading columns aligned left. Cells may be (text, css class)."""
        def cell(v, i, tag):
            cls = ""
            if isinstance(v, tuple):
                v, cls = v
            cl = " ".join(c for c in (cls, "l" if i < left else "") if c)
            ca = f' class="{cl}"' if cl else ""
            if isinstance(v, float):
                v = fmt(v)
            return f"<{tag}{ca}>{html.escape(str(v))}</{tag}>"

        head = "".join(cell(h, i, "th") for i, h in enumerate(headers))
        body = "".join("<tr>" + "".join(cell(v, i, "td") for i, v in enumerate(r)) + "</tr>" for r in rows)
        self.parts.append(f"<table><tr>{head}</tr>{body}</table>")

    def image(self, img: np.ndarray, caption: str = "", max_side: int = 1600, name: str | None = None) -> str:
        self._n_img += 1
        fname = name or f"img{self._n_img:03d}.jpg"
        imageio.save_jpeg(img, self.dir / fname, max_side=max_side)
        self.parts.append(
            f'<figure><a href="{fname}"><img src="{fname}" loading="lazy"></a>'
            f"<figcaption>{html.escape(caption)}</figcaption></figure>"
        )
        return fname

    def write(self, summary: str, result: dict, status: str = "done") -> Path:
        self.summary = summary
        finished = _dt.datetime.now().isoformat(timespec="seconds")
        res = {
            "analysis": self.analysis,
            "set": self.set_id,
            "status": status,
            "version": __version__,
            "summary": summary,
            "started": self.started,
            "finished": finished,
        }
        res.update(_plain(result))
        with open(self.dir / "result.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(res, f, sort_keys=False)
        body = "\n".join(self.parts)
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(self.title)}</title><style>{CSS}</style></head>
<body><main>
<p><a href="/sets/{html.escape(self.set_id)}">&larr; set {html.escape(self.set_id)}</a></p>
<h1>{html.escape(self.title)}</h1>
<div class="summary">{html.escape(summary)}</div>
<p>stockscan {__version__} &middot; set {html.escape(self.set_id)} &middot; {finished}</p>
{body}
</main></body></html>
"""
        (self.dir / "index.html").write_text(page, encoding="utf-8")
        return self.dir / "index.html"


def _plain(v):
    """Convert numpy values to plain Python for YAML."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, np.ndarray):
        return _plain(v.tolist())
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v
