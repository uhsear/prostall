r"""Render an ArcGIS Pro Performance Analyser report as one self-contained
HTML page.

A pure function over the document the analyser returns: no ArcGIS import, no
network, no template engine, no external asset. That matters twice over. The
report is opened from a file:// URL on a workstation that may be offline or
air-gapped, so a CDN link would be a broken page. And the report is full of
machine-supplied strings - account names, folder paths off a share, network
adapter descriptions, Defender exclusion paths, event-log text - so every one
of them is escaped on the way out. A folder on a file server whose name
contains markup must not become script in the operator's browser.

Every number on the page comes from the kpi mapping a check carried, never
from a sentence parsed back into a number. A gauge that had to read its value
out of prose would be a number this renderer invented on the way to the page,
which is the same defect this analyser exists to prevent, moved downstream.

    import arcgis_pro_performance_analyser as engine, html_report
    doc = engine.run(engine.Env(), {...})
    open(path, "w", encoding="utf-8").write(html_report.render_html(doc))
"""

from __future__ import print_function

import json
import math
import os

try:                                  # html.escape is stdlib on every Pro Python
    from html import escape as _esc
except ImportError:                   # pragma: no cover - Python 2 fallback
    from cgi import escape as _cgi_escape

    def _esc(text, quote=True):
        return _cgi_escape("%s" % text, quote)

# The five statuses, and nothing else: the same contract the analyser enforces.
STATUS_CLASSES = {
    "Pass": "st-pass",
    "Warn": "st-warn",
    "Fail": "st-fail",
    "Info": "st-info",
    "Skipped": "st-skipped",
}
STATUS_ORDER = ("Fail", "Warn", "Pass", "Info", "Skipped")
STATUS_MEANING = {
    "Pass": "Measured, and it is what it should be.",
    "Warn": "Measured, and it is a contributing cause.",
    "Fail": "Measured, and it is a cause.",
    "Info": "Read, and it decides nothing.",
    "Skipped": "Not read at all. The probe threw, produced nothing, or was "
               "never asked to run.",
}
STATUS_FR = {
    "Pass": "Conforme", "Warn": "Avertissement", "Fail": "Anomalie",
    "Info": "Informatif", "Skipped": "Non mesuré",
}

EXIT_MEANING = {
    0: "Nothing to act on.",
    1: "Measured findings, none of them bearing on lost edits.",
    2: "At least one finding bearing on lost edits.",
    3: "No finding, and nothing was determined either.",
    64: "A usage error, or the report could not be written.",
}

# The symptom key the analyser writes into its impact lines. Reproduced here so
# the flags on the page agree with the letters in the text.
SYMPTOM_FALLBACK = (
    ("S1", "le projet met des minutes a s'ouvrir au lieu de quelques "
           "secondes"),
    ("S2", "la session se fige plusieurs minutes en pleine modification, "
           "parfois jusqu'a une fermeture forcee"),
    ("S3", "des modifications enregistrees ont disparu sans message apres "
           "un tel figeage (le plus grave)"),
    ("S4", "une valeur recalculee met a jour la table mais la carte ne se "
           "redessine pas"),
    ("S5", "un copier ou coller d'entite ne fait rien pendant plusieurs "
           "secondes, et les tentatives suivantes le dupliquent"),
    ("S6", "ArcGIS Pro se ferme ou le projet plante pendant son ouverture"),
)

# What each finding costs the score. A finding that bears on lost edits costs
# more than one that only costs time, because that is the order the report
# puts them in.
SCORE_FAIL = 12
SCORE_FAIL_DATA = 18
SCORE_WARN = 6
SCORE_WARN_DATA = 9


def esc(value):
    """Escape one value for an HTML text node or a quoted attribute."""
    return _esc("" if value is None else "%s" % value, quote=True)


def _num(value):
    """A float, or None. Never a guess at a string that is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _kpi(check, key):
    """One number a check carried, or None.

    None for a check that is not in the document at all. A report from an
    older format, or one written by a caller that filtered the checks, must
    render with fewer gauges rather than fail to render at all.
    """
    if not isinstance(check, dict):
        return None
    return _num((check.get("kpi") or {}).get(key))


def _multiline(text, css="observed"):
    out = []
    for line in ("%s" % (text or "")).split("\n"):
        if line.strip():
            out.append('<div class="%s">%s</div>' % (css, esc(line)))
    return "".join(out)


def _embedded_json(document):
    """The report as JSON, safe inside a <script> element.

    Content inside a script element is raw text to the HTML parser, so the
    three characters that matter are escaped as \\uXXXX. That keeps the block
    valid JSON and keeps a machine string out of the page as literal markup.
    """
    blob = json.dumps(document, indent=1, sort_keys=True, ensure_ascii=True)
    return (blob.replace("&", "\\u0026").replace("<", "\\u003c")
                .replace(">", "\\u003e").replace(u"\u2028", "\\u2028")
                .replace(u"\u2029", "\\u2029"))


# =============================================================================
# THE SCORE AND THE GAUGES
# =============================================================================

def health_score(document):
    """(score 0-100 or None, determined, total).

    None when nothing was determined. A machine this tool could not read is
    not a machine that scored 100, and drawing a green gauge over a run that
    measured nothing would be the report's own version of the defect this tool
    was rebuilt around.

    Only verdicts move the score. Info and Skipped move the coverage figure
    instead, because they say the tool did not look, not that the machine is
    fine.
    """
    checks = document.get("checks") or []
    total = len(checks)
    determined = len([c for c in checks
                      if c.get("status") in ("Pass", "Warn", "Fail")])
    if not determined:
        return None, determined, total
    score = 100.0
    for check in checks:
        status = check.get("status")
        data = bool(check.get("dataloss"))
        if status == "Fail":
            score -= SCORE_FAIL_DATA if data else SCORE_FAIL
        elif status == "Warn":
            score -= SCORE_WARN_DATA if data else SCORE_WARN
    return max(0, int(round(score))), determined, total


def _tone(pct):
    """Green, amber or red, on a percentage where higher is better."""
    if pct is None:
        return "none"
    if pct >= 66:
        return "good"
    if pct >= 33:
        return "warn"
    return "bad"


def gauge(label, pct, caption, tone=None, sub=None):
    """One radial gauge as inline SVG. pct is 0-100, higher is better."""
    pct = None if pct is None else max(0.0, min(100.0, pct))
    tone = tone or _tone(pct)
    circumference = 2 * math.pi * 46
    if pct is None:
        offset = circumference
        shown = "n/d"
    else:
        offset = circumference * (1.0 - pct / 100.0)
        shown = "%g" % round(pct)
    parts = ['<div class="gauge tone-%s">' % tone,
             '<svg viewBox="0 0 120 120" role="img" aria-label="%s: %s">'
             % (esc(label), esc(shown)),
             '<circle class="trk" cx="60" cy="60" r="46"/>',
             '<circle class="fil" cx="60" cy="60" r="46" '
             'stroke-dasharray="%.2f" stroke-dashoffset="%.2f" '
             'transform="rotate(-90 60 60)"/>' % (circumference, offset),
             '<text class="val" x="60" y="60">%s</text>' % esc(shown),
             '<text class="unit" x="60" y="78">%%</text>',
             "</svg>",
             '<div class="glabel">%s</div>' % esc(label)]
    if caption:
        parts.append('<div class="gcaption">%s</div>' % esc(caption))
    if sub:
        parts.append('<div class="gsub">%s</div>' % esc(sub))
    parts.append("</div>")
    return "".join(parts)


def _ratio_gauge(label, value, limit, unit, invert, check_id):
    """A gauge from a measured number against a threshold this tool named.

    invert is True when a bigger number is worse - a latency, a cache size -
    and False when a bigger number is better, so the colour means the same
    thing on every gauge on the page.
    """
    value = _num(value)
    limit = _num(limit)
    if value is None or not limit:
        return None
    pct = (100.0 * value / limit) if not invert else (100.0 * (limit / value)
                                                      if value else 100.0)
    caption = "%s %s / %s %s" % (("%g" % value), unit, ("%g" % limit), unit)
    tone = "good" if pct >= 66 else ("warn" if pct >= 33 else "bad")
    if not invert:
        tone = "good" if pct >= 100 else ("warn" if pct >= 66 else "bad")
    return gauge(label, pct, caption, tone, sub="réf. %s" % check_id)


def gauges(document):
    """The gauge row, built only from numbers the checks actually carried."""
    checks = document.get("checks") or []
    by_id = dict((c.get("id"), c) for c in checks)
    score, determined, total = health_score(document)
    out = [gauge("Santé globale", None if score is None else float(score),
                 "%d verdict(s) sur %d contrôles" % (determined, total),
                 sub="0 = tout est en cause")]
    coverage = (100.0 * determined / total) if total else None
    out.append(gauge("Couverture de mesure", coverage,
                     "%d contrôle(s) ont rendu un verdict" % determined,
                     sub="le reste n'a pas pu être lu"))
    memory = _kpi(by_id.get("PF-12"), "free_pct")
    if memory is not None:
        out.append(gauge("Mémoire libre", memory,
                         "%.1f Go libres sur %.1f Go"
                         % (_kpi(by_id["PF-12"], "free_gb") or 0,
                            _kpi(by_id["PF-12"], "total_gb") or 0),
                         sub="réf. PF-12"))
    disk = _kpi(by_id.get("PF-13"), "free_pct_worst")
    if disk is not None:
        out.append(gauge("Disque libre (pire volume)", disk,
                         "%.1f Go libres"
                         % (_kpi(by_id["PF-13"], "free_gb_worst") or 0),
                         sub="réf. PF-13"))
    share = _ratio_gauge("Latence du partage",
                         _kpi(by_id.get("PF-03"), "worst_ms"),
                         _kpi(by_id.get("PF-03"), "fail_ms") or 1500,
                         "ms", True, "PF-03")
    if share:
        out.append(share)
    link = _ratio_gauge("Liaison réseau", _kpi(by_id.get("PF-21"), "worst_mbps"),
                        _kpi(by_id.get("PF-21"), "min_mbps") or 1000,
                        "Mbit/s", False, "PF-21")
    if link:
        out.append(link)
    cache_bytes = _kpi(by_id.get("PF-06"), "bytes")
    if cache_bytes is not None:
        ceiling = 2 * 1024.0 ** 3
        pct = 100.0 * (ceiling / cache_bytes) if cache_bytes else 100.0
        out.append(gauge("Cache ESRI", min(pct, 100.0),
                         "%.2f Go, %.0f%% orphelins"
                         % (cache_bytes / 1024.0 ** 3,
                            _kpi(by_id["PF-06"], "stale_pct") or 0),
                         "good" if pct >= 100 else
                         ("warn" if pct >= 33 else "bad"), sub="réf. PF-06"))
    return out


# =============================================================================
# THE FLAGS
# =============================================================================

def symptom_flags(document):
    """{symptom: [check ids]} for the findings that name that symptom.

    Derived from the impact text, which is where the analyser already writes the
    symptom letter. Nothing is inferred: a check whose impact line names no
    symptom flags none.
    """
    flags = {}
    for check in document.get("checks") or []:
        if check.get("status") not in ("Fail", "Warn"):
            continue
        text = check.get("impact") or ""
        for code, _label in (document.get("symptoms") or SYMPTOM_FALLBACK):
            if code in text:
                flags.setdefault(code, []).append(check.get("id"))
    return flags


def _flags_section(document):
    symptoms = document.get("symptoms") or SYMPTOM_FALLBACK
    flags = symptom_flags(document)
    doc_by_id = dict((c.get("id"), c) for c in document.get("checks") or [])
    rows = []
    for code, label in symptoms:
        ids = flags.get(code) or []
        tone = "bad" if ids else "calm"
        badge = ('<span class="flag hit">%d</span>' % len(ids)) if ids \
            else '<span class="flag none">0</span>'
        links = " ".join(
            '<a href="#check-%s">%s <span class="ref">%s</span></a>'
            % (esc(cid), esc(display_name(doc_by_id.get(cid) or {})), esc(cid))
                         for cid in ids) or "aucun contrôle en anomalie"
        rows.append('<div class="flagrow %s"><div class="flaghead">%s '
                    '<b>%s</b></div><div class="flagtext">%s</div>'
                    '<div class="flaglinks">%s</div></div>'
                    % (tone, badge, esc(code), esc(label), links))
    return ('<section id="symptomes"><h2>Drapeaux par sympt&ocirc;me</h2>'
            '<p class="lead">Chaque drapeau compte les contr&ocirc;les en '
            'anomalie dont la ligne d&rsquo;impact nomme ce sympt&ocirc;me. '
            'Un drapeau &agrave; z&eacute;ro ne veut pas dire que le '
            'sympt&ocirc;me est &eacute;cart&eacute; : il veut dire '
            'qu&rsquo;aucun contr&ocirc;le en anomalie ne le porte.</p>'
            '%s</section>' % "".join(rows))


# =============================================================================
# THE PAGE
# =============================================================================

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#15181d;--mut:#5c6672;--line:#dfe3e8;
--good:#1a7f37;--warn:#9a6700;--bad:#b42318;--info:#0b5cad;--skip:#5c6672;
--accent:#0b5cad}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 "Segoe UI",system-ui,-apple-system,Arial,sans-serif}
.wrap{max-width:1160px;margin:0 auto;padding:0 20px 90px}
header.top{padding:26px 0 14px;border-bottom:2px solid var(--line);margin-bottom:0}
header.top h1{font-size:24px;margin:0 0 6px}
header.top .sub{color:var(--mut);font-size:13px;margin:2px 0}
header.top .sub b{color:var(--ink);font-weight:600}
nav.toc{position:sticky;top:0;z-index:20;background:rgba(245,246,248,.96);
backdrop-filter:blur(6px);border-bottom:1px solid var(--line);
padding:10px 0;margin-bottom:22px}
nav.toc ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px}
nav.toc a{text-decoration:none;color:var(--ink);font-size:13px;font-weight:600;
padding:5px 11px;border:1px solid var(--line);border-radius:999px;background:#fff;
display:inline-flex;align-items:center;gap:6px}
nav.toc a:hover{border-color:var(--accent);color:var(--accent)}
nav.toc .n{font-size:11px;background:#eceff2;border-radius:999px;padding:0 6px;
color:var(--mut);font-weight:700}
nav.toc a.hot{border-color:var(--bad);color:var(--bad)}
nav.toc a.hot .n{background:#fde7e5;color:var(--bad)}
section{margin:26px 0}
section>h2{font-size:13px;letter-spacing:.09em;text-transform:uppercase;
color:var(--mut);margin:0 0 10px;border-bottom:1px solid var(--line);
padding-bottom:6px}
.lead{color:var(--mut);font-size:13px;margin:0 0 14px;max-width:78ch}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
gap:10px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:12px 14px}
.kpi .v{font-size:27px;font-weight:700;line-height:1.1}
.kpi .l{font-size:11px;letter-spacing:.06em;text-transform:uppercase;
color:var(--mut);margin-top:3px}
.kpi .s{font-size:12px;color:var(--mut);margin-top:4px}
.kpi.bad .v{color:var(--bad)}.kpi.warn .v{color:var(--warn)}
.kpi.good .v{color:var(--good)}.kpi.info .v{color:var(--info)}
.gauges{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));
gap:12px;margin:14px 0}
.gauge{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:12px 8px 10px;text-align:center}
.gauge svg{width:104px;height:104px}
.gauge .trk{fill:none;stroke:#e6e9ee;stroke-width:11}
.gauge .fil{fill:none;stroke-width:11;stroke-linecap:round}
.gauge.tone-good .fil{stroke:var(--good)}
.gauge.tone-warn .fil{stroke:var(--warn)}
.gauge.tone-bad .fil{stroke:var(--bad)}
.gauge.tone-none .fil{stroke:#c8ced6}
.gauge .val{font-size:25px;font-weight:700;text-anchor:middle;fill:var(--ink)}
.gauge .unit{font-size:11px;text-anchor:middle;fill:var(--mut)}
.gauge .glabel{font-size:12px;font-weight:600;margin-top:4px}
.gauge .gcaption{font-size:11px;color:var(--mut);margin-top:2px}
.gauge .gsub{font-size:10px;color:var(--mut);opacity:.8}
.banner{margin:16px 0;padding:15px 17px;border-radius:10px;border:1px solid}
.banner.act{border-color:var(--bad);background:#fff5f4}
.banner.calm{border-color:var(--good);background:#f2fbf5}
.banner.undone{border-color:var(--info);background:#f3f8ff}
.banner h3{font-size:16px;margin:0 0 8px}
.banner ul{margin:0;padding-left:20px}
.banner li{margin:3px 0}
.flagrow{display:grid;grid-template-columns:64px 1fr;gap:4px 12px;
background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:11px 14px;margin:8px 0}
.flagrow.bad{border-left:4px solid var(--bad)}
.flagrow.calm{border-left:4px solid #c8ced6}
.flaghead{grid-column:2;font-size:14px}
.flag{display:inline-flex;align-items:center;justify-content:center;
grid-row:1/3;align-self:center;width:44px;height:44px;border-radius:50%;
font-weight:700;font-size:16px}
.flag.hit{background:#fde7e5;color:var(--bad)}
.flag.none{background:#eceff2;color:var(--mut)}
.flagtext{grid-column:2;font-size:13px;color:var(--mut)}
.flaglinks{grid-column:2;font-size:12px}
.flaglinks a{color:var(--accent);text-decoration:none;margin-right:6px}
.flaglinks a:hover{text-decoration:underline}
.cat{font-size:12px;letter-spacing:.07em;text-transform:uppercase;
color:var(--mut);margin:20px 0 6px;font-weight:700}
.check{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin:10px 0}
.check.is-fail{border-left:4px solid var(--bad)}
.check.is-warn{border-left:4px solid var(--warn)}
.check.is-pass{border-left:4px solid var(--good)}
.check.is-info{border-left:4px solid var(--info)}
.check.is-skipped{border-left:4px solid var(--skip)}
.chead{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.chead .ttl{font-weight:600;font-size:16px;flex:1 1 340px;line-height:1.35}
.ref{font:600 11px ui-monospace,Consolas,monospace;color:var(--mut);
border:1px solid var(--line);border-radius:999px;padding:1px 7px;
background:#f2f4f7;white-space:nowrap}
.flaglinks a .ref{margin-left:2px}
.chead .cat2{font-size:11px;color:var(--mut);border:1px solid var(--line);
border-radius:4px;padding:1px 6px;margin-left:auto}
.badge{font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
border-radius:4px;padding:2px 7px}
.st-pass{background:#e6f4ea;color:var(--good)}
.st-warn{background:#fdf3dc;color:var(--warn)}
.st-fail{background:#fde7e5;color:var(--bad)}
.st-info{background:#e7f0fb;color:var(--info)}
.st-skipped{background:#eceff2;color:var(--skip)}
.dl{font-size:10px;font-weight:700;letter-spacing:.06em;background:#fde7e5;
color:var(--bad);border-radius:4px;padding:2px 6px}
.blk{margin-top:10px}
.blk .h{font-size:11px;letter-spacing:.07em;text-transform:uppercase;
color:var(--mut);font-weight:700;margin-bottom:3px}
.observed{white-space:pre-wrap;word-break:break-word;font:13px/1.55 ui-monospace,
Consolas,"Courier New",monospace}
.sol{background:#f2fbf5;border:1px solid #bfe3c8;border-radius:8px;
padding:9px 11px;font-size:13px;margin-top:8px}
.check.is-fail .sol,.check.is-warn .sol{background:#f7fbf8}
.impact{font-size:13px;color:var(--mut)}
.why{font-size:13px;color:var(--ink)}
.steps{margin:6px 0 0;padding-left:1.45rem;font-size:13px}
.steps li{margin:.4rem 0;padding-left:.15rem}
.steps li::marker{font-weight:700;color:var(--accent)}
.verify{margin-top:8px;padding:8px 10px;border-left:3px solid var(--info);
background:#f3f8ff;font-size:13px}
.guide{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:8px;
margin:12px 0}
.guide>div{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:10px 12px;font-size:13px}
.guide b{display:block;color:var(--accent);margin-bottom:3px}
.srclist{margin:0;padding-left:1.1rem;font-size:13px}
.srclist li{margin:.15rem 0}
.srclist a{color:#0b5cad;text-decoration:none;word-break:break-word}
.srclist a:hover{text-decoration:underline}
details{margin-top:8px}
details summary{cursor:pointer;font-size:12px;color:var(--mut)}
.kpichips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{font-size:11px;background:#eceff2;border-radius:4px;padding:2px 7px;
font-family:ui-monospace,Consolas,monospace}
.fixes li,.notes li{margin:4px 0}
code{background:#eceff2;border-radius:4px;padding:1px 5px;
font:12px ui-monospace,Consolas,monospace}
pre.undo{background:#15181d;color:#f3f5f7;padding:11px 13px;border-radius:8px;
overflow-x:auto;font:12px ui-monospace,Consolas,monospace;white-space:pre-wrap}
.key{margin:14px 0;padding:13px 16px;background:#fff;border:1px solid var(--line);
border-radius:10px;font-size:13px}
.key dl{display:grid;grid-template-columns:96px 1fr;gap:4px 12px;margin:8px 0 0}
.key dt{font-weight:700}.key dd{margin:0;color:var(--mut)}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0 4px}
.controls input{flex:1 1 220px;padding:8px 11px;border:1px solid var(--line);
border-radius:8px;font:inherit;background:#fff}
.controls button{padding:7px 12px;border:1px solid var(--line);border-radius:8px;
background:#fff;font:inherit;cursor:pointer;font-size:13px}
.controls button[aria-pressed=true]{background:var(--ink);color:#fff;
border-color:var(--ink)}
footer{margin-top:30px;color:var(--mut);font-size:12px;border-top:1px solid
var(--line);padding-top:13px}
.backtop{position:fixed;right:18px;bottom:18px;background:var(--ink);color:#fff;
text-decoration:none;border-radius:999px;padding:9px 14px;font-size:13px;
opacity:.85}
[hidden]{display:none!important}
@media print{body{background:#fff}nav.toc,.controls,.backtop{display:none}
.check{break-inside:avoid;border:1px solid #ccc}details{display:none}
.gauge svg{width:80px;height:80px}}
@media (prefers-color-scheme:dark){
:root{--bg:#0f1216;--card:#171b21;--ink:#e8eaed;--mut:#9aa4b0;--line:#2a313a;
--good:#4ac26b;--warn:#e3b341;--bad:#f2726a;--info:#6fa8dc;--accent:#6fa8dc}
nav.toc{background:rgba(15,18,22,.96)}
nav.toc a,.controls input,.controls button,.kpi,.gauge,.check,.flagrow,.key
{background:#171b21;color:var(--ink)}
nav.toc .n,.chip{background:#232931}
nav.toc a.hot{border-color:var(--bad);color:var(--bad)}
nav.toc a.hot .n{background:#3d1512;color:var(--bad)}
.gauge .trk{stroke:#2a313a}
.gauge.tone-none .fil{stroke:#39414b}
.st-pass{background:#12321c;color:var(--good)}
.st-warn{background:#3a2e10;color:var(--warn)}
.st-fail{background:#3d1512;color:var(--bad)}
.st-info{background:#12283d;color:var(--info)}
.st-skipped{background:#232931;color:var(--skip)}
.dl{background:#3d1512;color:var(--bad)}
.flag.hit{background:#3d1512;color:var(--bad)}
.flag.none{background:#232931;color:var(--mut)}
.banner.act{background:#25100e;border-color:var(--bad)}
.banner.calm{background:#0f2417;border-color:var(--good)}
.banner.undone{background:#0e1c2c;border-color:var(--info)}
.sol{background:#132318;border-color:#24462e}
.verify{background:#0e1c2c}
.guide>div{background:#171b21}
code,.chip{background:#232931}}
"""

JS = """
(function(){
  var cards = Array.prototype.slice.call(document.querySelectorAll('.check'));
  var search = document.getElementById('q');
  var mode = 'all';
  function apply(){
    var needle = (search.value || '').toLowerCase();
    var shown = 0;
    cards.forEach(function(c){
      var okMode = mode === 'all' ||
        (mode === 'findings' ? c.dataset.finding === '1' :
         mode === 'dataloss' ? c.dataset.dataloss === '1' :
         mode === 'performance' ? c.dataset.dataloss !== '1' :
         c.dataset.status === mode);
      var okText = !needle || c.textContent.toLowerCase().indexOf(needle) !== -1;
      var ok = okMode && okText;
      c.hidden = !ok;
      if (ok) { shown++; }
    });
    document.getElementById('shown').textContent = shown;
    Array.prototype.forEach.call(
      document.querySelectorAll('[data-catblock]'), function(s){
        s.hidden = s.querySelectorAll('.check:not([hidden])').length === 0;
      });
  }
  Array.prototype.forEach.call(document.querySelectorAll('[data-mode]'),
    function(b){
      b.addEventListener('click', function(){
        mode = b.getAttribute('data-mode');
        document.querySelectorAll('[data-mode]').forEach(function(o){
          o.setAttribute('aria-pressed', o === b ? 'true' : 'false');
        });
        apply();
      });
    });
  search.addEventListener('input', apply);
  apply();
})();
"""


def _counts(document):
    checks = document.get("checks") or []
    given = document.get("counts") or {}
    out = []
    for status in STATUS_ORDER:
        out.append((status, given[status] if status in given
                    else len([c for c in checks if c.get("status") == status])))
    return out


def _kpi_cards(document):
    checks = document.get("checks") or []
    tally = dict(_counts(document))
    score, determined, total = health_score(document)
    act = document.get("act_first") or []
    cards = [
        ("Santé globale", "n/d" if score is None else score,
         "0&ndash;100, plus c'est haut mieux c'est",
         "bad" if score is not None and score < 34
         else ("warn" if score is not None and score < 67 else "good")),
        ("Anomalies", tally.get("Fail", 0), "mesur&eacute;es et en cause",
         "bad" if tally.get("Fail") else "good"),
        ("Avertissements", tally.get("Warn", 0), "causes contributives",
         "warn" if tally.get("Warn") else "good"),
        ("Perte de donn&eacute;es", len(act),
         "&agrave; traiter en premier", "bad" if act else "good"),
        ("Conformes", tally.get("Pass", 0), "mesur&eacute;s et corrects",
         "info"),
        ("Non d&eacute;termin&eacute;s", tally.get("Info", 0)
         + tally.get("Skipped", 0),
         "lus sans verdict, ou non lus", "info"),
        ("Couverture", "%d%%" % round(100.0 * determined / total) if total
         else "n/d", "%d verdict(s) sur %d" % (determined, total),
         "warn" if total and determined * 2 < total else "good"),
    ]
    out = []
    for label, value, sub, tone in cards:
        out.append('<div class="kpi %s"><div class="v">%s</div>'
                   '<div class="l">%s</div><div class="s">%s</div></div>'
                   % (tone, esc(value), label, sub))
    return '<div class="kpis">%s</div>' % "".join(out)


def _banner(document):
    checks = document.get("checks") or []
    by_id = dict((c.get("id"), c) for c in checks)
    act = document.get("act_first") or []
    if act:
        items = "".join(
            '<li><b><a href="#check-%s">%s</a></b> '
            '<span class="ref">%s</span></li>'
            % (esc(cid), esc(display_name(by_id.get(cid) or {})), esc(cid))
            for cid in act)
        return ('<div class="banner act"><h3>&#9873; &Agrave; traiter en '
                'premier &mdash; ces trouvailles portent sur les modifications '
                'perdues</h3><ul>%s</ul></div>' % items)
    verdicts = [c for c in checks if c.get("status") in ("Fail", "Warn")]
    if verdicts and document.get("analysis_type") == "project":
        return ('<div class="banner undone"><h3>Anomalies d&eacute;tect&eacute;es '
                'dans le projet s&eacute;lectionn&eacute;</h3><p>%d contr&ocirc;le(s) '
                '&agrave; traiter. Lisez PR-01 et PR-02 en premier pour la '
                'corruption, puis PR-05 si Pro plante en rouvrant une vue. '
                'Chaque carte donne les &eacute;tapes et la v&eacute;rification.'
                '</p></div>' % len(verdicts))
    if verdicts:
        return ('<div class="banner undone"><h3>Findings de performance '
                'seulement</h3><p>%d trouvaille(s), aucune ne porte sur des '
                'modifications perdues.</p></div>' % len(verdicts))
    score, determined, total = health_score(document)
    if score is None:
        return ('<div class="banner undone"><h3>Rien n&rsquo;a pu &ecirc;tre '
                'd&eacute;termin&eacute;</h3><p>Aucune trouvaille, et rien '
                'n&rsquo;a &eacute;t&eacute; mesur&eacute; non plus. Lancez le '
                'diagnostic dans la session de l&rsquo;&eacute;ditrice, sur '
                'Windows. C&rsquo;est le code de sortie 3, pas une machine '
                'saine.</p></div>')
    return ('<div class="banner calm"><h3>Rien &agrave; traiter</h3>'
            '<p>Tous les contr&ocirc;les qui ont pu &ecirc;tre lus sont '
            'conformes.</p></div>')


def _reading_guide(document):
    """A short, explicit reading order before the technical cards."""
    project = document.get("analysis_type") == "project"
    intro = ("Ce rapport analyse uniquement le fichier .aprx indiqué dans "
             "l'en-tête, à partir d'une copie instantanée. Le projet actif "
             "n'est ni ouvert ni modifié par ce diagnostic." if project else
             "Chaque anomalie ci-dessous est fondée sur une valeur réellement "
             "lue sur ce poste. Info et Non mesuré ne sont pas des preuves de "
             "panne.")
    return ('<section id="mode-emploi"><h2>Comment lire et corriger</h2>'
            '<p class="lead">%s</p><div class="guide">'
            '<div><b>1. Lire la mesure</b>La ligne « problème détecté » '
            'indique les faits et les chemins concernés.</div>'
            '<div><b>2. Comprendre l&rsquo;impact</b>Elle est suivie de '
            'l&rsquo;explication et des effets possibles dans ArcGIS Pro, '
            'sans confondre corrélation et cause certaine.</div>'
            '<div><b>3. Agir dans l&rsquo;ordre</b>Suivez les étapes '
            'numérotées. Testez une correction à la fois sur une copie.</div>'
            '<div><b>4. Vérifier le résultat</b>Rejouez le même scénario '
            'puis relancez le contrôle indiqué avant de reprendre la '
            'production.</div></div></section>' % esc(intro))


def display_name(check):
    """The French name of a check: the heading, not the code.

    The report used to print the code as the heading and the check's own name
    in English beside it, so a French operator had to decode both before
    knowing what had been tested. The name leads now and the code stays as a
    small reference, because a support case and a JSON diff still need one
    identifier that survives a re-wording.
    """
    return (check.get("title_fr") or check.get("title") or "")


def _check_card(check):
    status = check.get("status", "")
    cls = STATUS_CLASSES.get(status, "st-skipped")
    finding = status in ("Fail", "Warn")
    parts = ['<article class="check is-%s" id="check-%s" data-status="%s" '
             'data-finding="%s" data-dataloss="%s">'
             % (cls.replace("st-", ""), esc(check.get("id")), esc(status),
                "1" if finding else "0",
                "1" if check.get("dataloss") else "0")]
    parts.append('<div class="chead">'
                 '<span class="ttl">%s</span>'
                 '<span class="badge %s">%s</span>%s'
                 '<span class="ref" title="r&eacute;f&eacute;rence '
                 'technique du contr&ocirc;le">%s</span>'
                 '<span class="cat2">%s</span></div>'
                 % (esc(display_name(check)), cls,
                    esc(STATUS_FR.get(status, status)),
                    '<span class="dl">perte de donn&eacute;es</span>'
                    if check.get("dataloss") else "",
                    esc(check.get("id")), esc(check.get("category"))))

    parts.append('<div class="blk"><div class="h">%s</div>%s</div>'
                 % ("Probl&egrave;me d&eacute;tect&eacute; &mdash; valeurs "
                    "mesur&eacute;es" if finding else "R&eacute;sultat du "
                    "contr&ocirc;le", _multiline(check.get("observed"))))

    explanation = check.get("explanation")
    impact_fr = check.get("impact_fr")
    steps = check.get("steps") or []
    verification = check.get("verification")
    # Pass needs no remediation panel. Info and Skipped do: their actions tell
    # the reader how to obtain the missing measurement instead of pretending
    # that a cause was found.
    show_actions = finding or status in ("Info", "Skipped")
    if explanation and show_actions:
        parts.append('<div class="blk"><div class="h">Pourquoi ce '
                     'r&eacute;sultat est important</div>'
                     '<div class="why">%s</div></div>' % esc(explanation))
    if show_actions and (impact_fr or check.get("impact")):
        parts.append('<div class="blk"><div class="h">Impact concret sur '
                     'ArcGIS Pro</div><div class="impact">%s</div></div>'
                     % esc(impact_fr or check.get("impact")))
    if steps and show_actions:
        items = "".join("<li>%s</li>" % esc(step) for step in steps)
        parts.append('<div class="sol"><div class="h">%s</div>'
                     '<ol class="steps">%s</ol></div>'
                     % ("Actions &agrave; prendre, dans l&rsquo;ordre"
                        if finding else "Comment lever le point bloquant",
                        items))
    elif finding and check.get("fix"):
        # Compatibility for a document produced before format 4 or for a
        # caller-defined check that has no entry in the guidance table.
        parts.append('<div class="sol"><div class="h">Correction / '
                     'optimisation</div>%s</div>' % esc(check.get("fix")))
    if verification and show_actions:
        parts.append('<div class="verify"><b>V&eacute;rification apr&egrave;s '
                     'correction :</b> %s</div>' % esc(verification))

    # Sources are rendered for EVERY status, not only Warn/Fail: an Info
    # check is still a check whose reference someone may want to read.
    # These are navigation links, not resources - the page downloads
    # nothing from these hosts, and the report's own CSP still forbids it.
    sources = check.get("sources") or []
    if sources:
        items = "".join(
            '<li><a href="%s" rel="noopener noreferrer">%s</a></li>'
            % (esc(url), esc(label)) for label, url in sources)
        parts.append('<div class="blk"><div class="h">Sources</div>'
                     '<ul class="srclist">%s</ul></div>' % items)

    kpi = check.get("kpi") or {}
    if kpi:
        chips = "".join('<span class="chip">%s = %s</span>'
                        % (esc(k), esc("%g" % v))
                        for k, v in sorted(kpi.items())
                        if _num(v) is not None)
        if chips:
            parts.append('<div class="kpichips">%s</div>' % chips)

    extras = []
    if check.get("title") and check.get("title") != display_name(check):
        extras.append('<div class="blk"><div class="h">Intitul&eacute; '
                      'd&rsquo;origine (r&eacute;f&eacute;rence support)'
                      '</div><div class="impact">%s</div></div>'
                      % esc(check.get("title")))
    if check.get("expected"):
        extras.append('<div class="blk"><div class="h">Valeur ou &eacute;tat '
                      'attendu</div><div class="impact">%s</div></div>'
                      % esc(check.get("expected")))
    if check.get("impact") and impact_fr:
        extras.append('<div class="blk"><div class="h">Impact technique '
                      'd&rsquo;origine</div><div class="impact">%s</div></div>'
                      % esc(check.get("impact")))
    if check.get("fix") and (steps or not finding):
        extras.append('<div class="blk"><div class="h">Correction / '
                      'optimisation &mdash; d&eacute;tails techniques'
                      '</div><div class="impact">%s</div></div>'
                      % esc(check.get("fix")))
    if check.get("detail"):
        extras.append('<div class="blk"><div class="h">Preuve compl&eacute;mentaire'
                      '</div>%s</div>' % _multiline(check.get("detail")))
    if check.get("autofixable"):
        extras.append('<div class="blk"><div class="h">Automatisable</div>'
                      '<div class="impact">oui &mdash; relancez avec les '
                      'correctifs activ&eacute;s</div></div>')
    if extras:
        parts.append('<details><summary>attendu, preuve et d&eacute;tails '
                     'techniques</summary>%s</details>' % "".join(extras))
    parts.append("</article>")
    return "".join(parts)


def _categories(document):
    """[(category, [checks])] in the order the checks were recorded."""
    order = []
    for check in document.get("checks") or []:
        name = check.get("category") or "Other"
        if name not in order:
            order.append(name)
    return [(name, [c for c in document["checks"] if c.get("category") == name])
            for name in order]


def _checks_section(document, wanted, anchor, heading):
    blocks = []
    total = 0
    for category, checks in _categories(document):
        keep = [c for c in checks if bool(c.get("dataloss")) is wanted]
        if not keep:
            continue
        total += len(keep)
        findings = len([c for c in keep
                        if c.get("status") in ("Fail", "Warn")])
        blocks.append('<div data-catblock><div class="cat">%s%s</div>%s</div>'
                      % (esc(category),
                         " &middot; %d anomalie(s)" % findings if findings
                         else "",
                         "".join(_check_card(c) for c in keep)))
    if not blocks:
        return "", 0
    return ('<section id="%s"><h2>%s</h2>%s</section>'
            % (esc(anchor), esc(heading), "".join(blocks))), total


def _fixes_section(document):
    parts = []
    notes = document.get("notes") or []
    if notes:
        parts.append('<section id="correctifs"><h2>Portes des correctifs</h2>'
                     '<p class="lead">&Eacute;valu&eacute;es &agrave; chaque '
                     'lancement, m&ecirc;me sans les appliquer : un '
                     'correctif ne tourne que si son contr&ocirc;le est '
                     'r&eacute;ellement en anomalie.</p><ul class="notes">')
        for note in notes:
            parts.append("<li><code>%s</code></li>" % esc(note))
        parts.append("</ul>")
    applied = document.get("applied") or []
    if applied:
        parts.append("<h3>Appliqu&eacute;s sur ce lancement</h3>")
        for record in applied:
            parts.append(
                '<div class="check is-warn"><div class="chead">'
                '<span class="id">%s</span><span class="badge st-warn">'
                'appliqu&eacute;</span><span class="ttl">%s</span></div>'
                '<div class="blk"><div class="h">R&eacute;sultat</div>'
                '<div class="observed">%s</div></div>'
                '<div class="blk"><div class="h">Annulation</div></div>'
                '<pre class="undo">%s</pre></div>'
                % (esc(record.get("id")), esc(record.get("what")),
                   esc(record.get("result")), esc(record.get("undo"))))
    if parts:
        parts.append("</section>")
    return "".join(parts)


def _legend():
    rows = "".join("<dt>%s</dt><dd>%s &mdash; %s</dd>"
                   % (esc(s), esc(STATUS_FR[s]), esc(STATUS_MEANING[s]))
                   for s in STATUS_ORDER)
    return ('<section id="legende"><h2>L&eacute;gende</h2>'
            '<div class="key"><b>Seuls Pass, Warn et Fail sont des verdicts.'
            '</b> Info et Skipped n&rsquo;en sont pas : un contr&ocirc;le qui '
            'dit n&rsquo;avoir rien mesur&eacute; ne peut pas en porter un, et '
            'le constructeur d&rsquo;enregistrement le refuse.'
            '<dl>%s</dl></div></section>' % rows)


def _json_section(document):
    return ('<section id="json"><h2>Le m&ecirc;me rapport en JSON</h2>'
            '<p class="lead">&Agrave; comparer avec un poste reconnu rapide. '
            'Chaque horodatage est en UTC, donc deux machines dans des fuseaux '
            'diff&eacute;rents ne se contredisent sur aucune ligne '
            'dat&eacute;e. Le rapport contient le nom du compte, des chemins '
            'locaux, des noms de fichiers de connexion et des chemins '
            'd&rsquo;exclusion : relisez-le avant de l&rsquo;envoyer.</p>'
            '<details><summary>afficher le document JSON</summary>'
            '<pre class="undo">%s</pre></details></section>'
            % esc(json.dumps(document, indent=1, sort_keys=True)))


def render_html(document, verbose=False, exit_code=None, meta=None):
    """The whole report as one HTML string.

    verbose widens the cards. meta is an optional dict of extra header lines
    the caller knows and the document does not, such as the ArcGIS Pro release
    the toolbox ran under.
    """
    checks = document.get("checks") or []
    code = exit_code if exit_code is not None else document.get("exit_code")
    dl_block, dl_total = _checks_section(document, True, "perte-donnees",
                                         "Perte de donn&eacute;es")
    perf_heading = ("Analyse du projet s&eacute;lectionn&eacute;"
                    if document.get("analysis_type") == "project" else
                    "Performance")
    perf_block, perf_total = _checks_section(document, False, "performance",
                                             perf_heading)
    act = len(document.get("act_first") or [])

    report_title = ("Diagnostic d'un projet .aprx externe"
                    if document.get("analysis_type") == "project" else
                    "Diagnostic de poste ArcGIS Pro")
    head = ['<header class="top"><h1>%s</h1>' % esc(report_title),
            '<div class="sub">ArcGIS Pro Performance Analyser &mdash; format de rapport %s &middot; '
            'relev&eacute; le <b>%s UTC</b> &middot; compte <b>%s</b> &middot; '
            '&eacute;lev&eacute; <b>%s</b></div>'
            % (esc(document.get("report_version")), esc(document.get("taken")),
               esc(document.get("user")), esc(document.get("elevated")))]
    if code is not None:
        head.append('<div class="sub">code de sortie <b>%s</b> &mdash; %s'
                    '</div>' % (esc(code), esc(EXIT_MEANING.get(code, "?"))))
    for name, value in sorted((meta or {}).items()):
        head.append('<div class="sub">%s <b>%s</b></div>'
                    % (esc(name), esc(value)))
    head.append("</header>")

    toc = ['<nav class="toc"><ul>',
           '<li><a href="#synthese">Synth&egrave;se</a></li>',
           '<li><a href="#mode-emploi">Mode d&rsquo;emploi</a></li>',
           '<li><a href="#jauges">Jauges</a></li>',
           '<li><a href="#symptomes">Drapeaux</a></li>',
           '<li><a class="%s" href="#perte-donnees">Perte de donn&eacute;es'
           '<span class="n">%d</span></a></li>'
           % ("hot" if act else "", dl_total),
           '<li><a href="#performance">%s<span class="n">%d</span>'
           '</a></li>' % ("Projet" if document.get("analysis_type") == "project"
                          else "Performance", perf_total),
           '<li><a href="#correctifs">Correctifs</a></li>',
           '<li><a href="#legende">L&eacute;gende</a></li>',
           '<li><a href="#json">JSON</a></li>',
           "</ul></nav>"]

    controls = ['<div class="controls">',
                '<input id="q" type="search" placeholder="Filtrer par id, '
                'titre ou texte&hellip;" aria-label="Filtrer">',
                '<button data-mode="all" aria-pressed="true">Tous</button>',
                '<button data-mode="findings" aria-pressed="false">Anomalies '
                'seulement</button>',
                '<button data-mode="dataloss" aria-pressed="false">Perte de '
                'donn&eacute;es</button>',
                '<button data-mode="performance" aria-pressed="false">'
                'Performance</button>']
    for status in STATUS_ORDER:
        controls.append('<button data-mode="%s" aria-pressed="false">%s'
                        '</button>' % (esc(status), esc(status)))
    controls.append('<span class="chip">affich&eacute;s <b id="shown">%d</b>'
                    '</span></div>' % len(checks))

    body = ["".join(head), "".join(toc), '<div class="wrap">',
            '<section id="synthese"><h2>Synth&egrave;se</h2>',
            _banner(document), _kpi_cards(document), "</section>",
            _reading_guide(document),
            '<section id="jauges"><h2>Jauges</h2><p class="lead">Chaque jauge '
            'est dessin&eacute;e &agrave; partir d&rsquo;un nombre que le '
            'contr&ocirc;le a mesur&eacute;, jamais d&rsquo;une phrase '
            'relue. Une jauge absente veut dire que le contr&ocirc;le '
            'n&rsquo;a pas rendu ce nombre.</p><div class="gauges">%s</div>'
            '</section>' % "".join(gauges(document)),
            _flags_section(document),
            "".join(controls),
            dl_block, perf_block,
            _fixes_section(document), _legend(), _json_section(document),
            '<footer>G&eacute;n&eacute;r&eacute; par ArcGIS Pro Performance Analyser. '
            'Lecture seule '
            'sauf si les correctifs ont &eacute;t&eacute; activ&eacute;s '
            'explicitement. Aucun h&ocirc;te n&rsquo;est contact&eacute; en '
            'dehors de ceux nomm&eacute;s au lancement. Rien ici ne supprime '
            'quoi que ce soit.</footer>',
            "</div>", '<a class="backtop" href="#synthese">&#8593; haut</a>']

    return ("<!DOCTYPE html>\n<html lang=\"fr\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, "
            "initial-scale=1\">\n"
            "<meta name=\"referrer\" content=\"no-referrer\">\n"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src "
            "'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "img-src data:\">\n"
            "<title>Diagnostic ArcGIS Pro &mdash; %s</title>\n"
            "<style>%s</style>\n</head>\n<body>\n%s\n"
            "<script type=\"application/json\" id=\"performance-report\">%s"
            "</script>\n<script>%s</script>\n</body>\n</html>\n"
            % (esc(document.get("taken")), CSS, "".join(body),
               _embedded_json(document), JS))


def write_html(document, path, verbose=False, exit_code=None, meta=None):
    """Render and write the report. Returns the path that was written."""
    folder = os.path.dirname(os.path.abspath(path))
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    handle = open(path, "wb")
    try:
        handle.write(render_html(document, verbose, exit_code, meta)
                     .encode("utf-8"))
    finally:
        handle.close()
    return path
