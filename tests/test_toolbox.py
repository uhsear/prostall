r"""Tests for the HTML renderer and the ArcGIS Pro toolbox wrapper.

Run with the standard library only, on any Python 3.9+:

    python tests/test_toolbox.py

These exercise the code that actually ships. The renderer is imported
directly, and the single-file .pyt is executed against a stand-in arcpy
module, so the toolbox's own getParameterInfo, updateMessages and execute are
what run here, not a copy of them - which is the difference between a test
that proves something and one that proves the test agrees with itself.

The .pyt is a generated artefact, so the first thing checked is that it is
still exactly what its three sources build to.
"""

import ast
import io
import json
import os
import re
import shutil
import sys
import tempfile
import types
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOXDIR = os.path.join(ROOT, "ArcGISPro_PerformanceAnalyser")
PYT = os.path.join(BOXDIR, "ArcGISPro_PerformanceAnalyser.pyt")
for path in (ROOT, BOXDIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import arcgis_pro_performance_analyser as engine  # noqa: E402
import build_toolbox    # noqa: E402
import html_report      # noqa: E402

PASSED = [0]
FAILED = []


def check(cond, label):
    if cond:
        PASSED[0] += 1
        print("PASS  %s" % label)
    else:
        FAILED.append(label)
        print("FAIL  %s" % label)


# =============================================================================
# A stand-in arcpy, just complete enough to execute the toolbox for real.
# =============================================================================

class _Parameter(object):
    def __init__(self, **kw):
        self.displayName = kw.get("displayName")
        self.name = kw.get("name")
        self.datatype = kw.get("datatype")
        self.parameterType = kw.get("parameterType")
        self.direction = kw.get("direction")
        self.category = None
        self.value = None
        self.errors = []
        self.warnings = []
        self.filter = types.SimpleNamespace(type=None, list=[])

    @property
    def valueAsText(self):
        if self.value is None:
            return ""
        if isinstance(self.value, bool):
            return "true" if self.value else "false"
        return "%s" % self.value

    def setErrorMessage(self, text):
        self.errors.append(text)

    def setWarningMessage(self, text):
        self.warnings.append(text)


class _Messages(object):
    def __init__(self):
        self.info, self.warn, self.err = [], [], []

    def addMessage(self, text):
        self.info.append(text)

    def addWarningMessage(self, text):
        self.warn.append(text)

    def addErrorMessage(self, text):
        self.err.append(text)


class _ExecuteError(Exception):
    pass


def install_fake_arcpy():
    if "arcpy" in sys.modules:
        return
    arcpy = types.ModuleType("arcpy")
    arcpy.Parameter = _Parameter
    arcpy.ExecuteError = _ExecuteError
    arcpy.GetInstallInfo = lambda: {"Version": "3.4", "BuildNumber": "57127"}
    env = types.SimpleNamespace(scratchFolder=os.path.join(
        tempfile.gettempdir(), "properf_scratch"))
    arcpy.env = env
    sys.modules["arcpy"] = arcpy


install_fake_arcpy()


# =============================================================================
# The shipped artefact is generated, so first: is it still what it was built
# from, and is it really the one file it claims to be?
# =============================================================================

PYT_SOURCE = io.open(PYT, encoding="utf-8", newline="").read()

check(build_toolbox.build() == PYT_SOURCE,
      "the shipped .pyt is byte-for-byte what its three sources build to: "
      "edit the sources and run build_toolbox.py, never the .pyt")
PYT_IMPORTS = set()
for _node in ast.walk(ast.parse(PYT_SOURCE)):
    if isinstance(_node, ast.Import):
        PYT_IMPORTS.update(alias.name for alias in _node.names)
    elif isinstance(_node, ast.ImportFrom) and _node.module:
        PYT_IMPORTS.add(_node.module)
check(not PYT_IMPORTS & {"arcgis_pro_performance_analyser", "html_report",
                         "prostall"},
      "and it imports no sibling module, so copying that one file is "
      "enough - nothing can go missing on the machine it lands on. Checked "
      "on the parse tree, not on substrings: the renderer's own docstring "
      "quotes those imports as usage text")
check("prostall" not in PYT_SOURCE.lower(),
      "the shipped artefact carries no trace of the old name")
check(PYT_SOURCE.count("# BEGIN MERGE-IMPORTS") == 0,
      "the build marker was replaced, not shipped")


def load_pyt():
    """Execute the shipped single-file .pyt and return its module."""
    name = "ArcGISPro_PerformanceAnalyser"
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    module.__file__ = PYT
    sys.modules[name] = module
    source = io.open(module.__file__, encoding="utf-8").read()
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


# =============================================================================
# A real report document, from the real decision functions
# =============================================================================

def sick_document():
    """A whole run against the analyser's own stand-in sick workstation."""
    document = engine.run(engine.fake_sick(), {"sde_server": "gisdb01",
                                                  "sde_port": 1433,
                                                  "portal": None,
                                                  "timeout": 4000,
                                                  "apply": False})
    document["exit_code"] = engine.exit_code(document["checks"])
    document["symptoms"] = list(engine.SYMPTOMS)
    return document


def hostile_document():
    """The sick document with every machine-supplied string made hostile.

    Every one of these lands in the report off the machine, not off the
    operator: a folder on a file server, an adapter description, a Defender
    exclusion, an account name.
    """
    document = sick_document()
    payload = ('<script>alert(1)</script><img src=x onerror=alert(2)>'
               '"><svg/onload=alert(3)>')
    document["user"] = payload
    for record in document["checks"]:
        record["observed"] = payload + "\n" + record["observed"]
        record["title"] = payload
        record["fix"] = payload
        record["detail"] = payload
        record["explanation"] = payload
        record["impact_fr"] = payload
        record["steps"] = [payload, payload]
        record["verification"] = payload
    document["notes"] = [payload]
    document["applied"] = [{"id": "AP-99", "what": payload, "result": payload,
                            "undo": payload, "when": payload}]
    return document


def truncation_document():
    """A document whose JSON holds the sequence that closes a script tag."""
    document = sick_document()
    document["user"] = '</script><script>alert("escaped out")</script>'
    return document


def strip_json_block(page):
    """The page minus the embedded JSON element.

    That element is raw text to the HTML parser, so it holds the report's
    machine strings as data on purpose. Everything outside it is markup, and
    nothing in it may be markup either - see the \\u003c escaping test.
    """
    start = page.index('id="performance-report">')
    end = page.index("</script>", start)
    return page[:start] + page[end:]


# =============================================================================
# THE HTML RENDERER
# =============================================================================

print("-" * 70)
print("the html renderer escapes what the machine supplied")
hostile = hostile_document()
page = html_report.render_html(hostile, verbose=True, exit_code=2)
check("<script>alert(1)</script>" not in page,
      "a payload in the account name, titles, evidence, notes and undo "
      "commands never reaches the page as markup")
check("<svg/onload=alert(3)>" not in page,
      "nor as an element in the evidence, guidance, steps or verification")
check("&lt;script&gt;alert(1)&lt;/script&gt;" in page,
      "it is on the page as escaped text, so the operator still sees the "
      "value the machine reported instead of losing it")
check(page.count("<script") == 2,
      "exactly two script elements: the embedded JSON and the filter, no third "
      "injected by the data")

cut = truncation_document()
cut_page = html_report.render_html(cut)
check(cut_page.count('id="performance-report">') == 1,
      "the report is embedded once")
check(cut_page.count("</script>") == 2,
      "and there are exactly two closing script tags, so a value holding "
      "</script> cannot close the JSON element early and start a new one")
check("<" not in cut_page.split('id="performance-report">')[1].split("</script>")[0],
      "and no angle bracket survives raw inside that element, so no payload "
      "in the report can be read as markup by a parser that is less strict "
      "than the spec")
decoded = cut_page.split('id="performance-report">')[1].split("</script>")[0]
check(json.loads(decoded)["user"] == cut["user"],
      "the JSON block still round-trips to the exact document, because the "
      "escaping is \\uXXXX inside strings and not a substitution")

print("-" * 70)
print("the page carries the whole report and nothing invented")
document = sick_document()
page = html_report.render_html(document, verbose=True,
                               exit_code=engine.exit_code(document["checks"]))
check(len(document["checks"]) > 0 and all(
    'data-status="%s"' % c["status"] in page for c in document["checks"]),
    "every check of the run is on the page, tagged with its own status")
ids = [c["id"] for c in document["checks"]]
check(all((">%s<" % cid) in page or ('id">%s<' % cid) in page for cid in ids),
    "and every id appears, so no check is dropped by the renderer")
check(page.count("data-dataloss=\"1\"") == len(
    [c for c in document["checks"] if c["dataloss"]]),
    "the data-loss flag on the page is the flag in the record, count for count")
check("traiter en" in page and "perdues" in page,
    "the act-first box is present when the run has act-first findings")
check(page.count('id="check-') == len(document["checks"]),
    "every check carries an anchor, so the sommaire and the flags link to a "
    "check that is actually on the page")
check('<nav class="toc">' in page and 'href="#perte-donnees"' in page
      and 'href="#performance"' in page,
    "the page opens with a sommaire that reaches both blocks")
check("Content-Security-Policy" in page,
    "the page ships a CSP that allows no external source at all")
check('src="http' not in page and "@import" not in page
      and not re.search(r'<link[^>]+href="https?://', page)
      and not re.search(r'<script[^>]+src="https?://', page)
      and "url(" not in page.split("<style>")[1].split("</style>")[0],
      "no stylesheet, script, font or image is FETCHED from a host: the "
      "report opens on a workstation that may be offline or air-gapped")
links = re.findall(r'<a href="(https?://[^"]*)"', page)
check(all(u.startswith("https://") for u in links) and len(links) > 20,
      "the only external references left are the remediation source links, "
      "and every one of them is https")
check(all(" " not in u and "<" not in u and '"' not in u for u in links),
      "no source URL carries a space or a character that would break out of "
      "the href attribute")
check(page.count('rel="noopener noreferrer"') == len(links),
      "every source link is opened without handing the target page a handle "
      "on the report")
check(page.count('class="srclist"') > 20,
      "the sources are printed per check, inside the check that cites them, "
      "rather than collected in one footnote nobody reads")
check(all(k in page for k in ("Fail", "Warn", "Pass", "Info", "Skipped")),
    "all five statuses have a legend entry on the page")
check("ArcGIS Pro 3.4" not in page,
    "no header line appears that the caller did not supply")

meta_page = html_report.render_html(document, meta={"ArcGIS Pro": "3.4 (1)"})
check("3.4 (1)" in meta_page,
    "a caller-supplied header line does appear, and only then")

print("-" * 70)
print("the banner tells the truth about the run")
sick_page = html_report.render_html(sick_document())
check("traiter en" in sick_page and "perdues" in sick_page,
      "a run with data-loss findings gets the act-first banner")
undone = engine.run(engine.fake_bare(), {})
undone_code = engine.exit_code(undone["checks"])
undone_page = html_report.render_html(undone, exit_code=undone_code)
check("Findings de performance" in undone_page,
      "a machine that reads back almost nothing still gets its one real "
      "finding: PF-11's absence of a GPU preference IS a verdict, so the "
      "banner says so rather than claiming nothing was determined")
check(undone_code == 1,
      "and that run is exit code 1, not 3 - three checks can answer on a "
      "bare machine, which is what the analyser's own self-test pins")
nothing = {"report_version": 3, "taken": "x", "user": "u", "elevated": False,
           "checks": [{"id": "DL-05", "category": "Storage",
                       "title": "t", "status": "Info", "dataloss": True,
                       "observed": "could not be determined",
                       "expected": "", "impact": "", "fix": "",
                       "autofixable": False, "detail": ""}],
           "counts": {}, "act_first": [], "fixable": [], "applied": [],
           "notes": []}
nothing_page = html_report.render_html(nothing, exit_code=3)
check("Rien n&rsquo;a pu &ecirc;tre" in nothing_page,
      "a run that truly determined nothing says so on the page and does not "
      "read as a clean machine")
check("n/d" in nothing_page,
      "and its health gauge reads n/d rather than drawing a score over a run "
      "that measured nothing: a green gauge on an unread machine is the same "
      "defect this analyser exists to prevent  <-- pinned defect")
healthy = engine.run(engine.fake_healthy(), {})
healthy_page = html_report.render_html(healthy,
                                       exit_code=engine.exit_code(
                                           healthy["checks"]))
check("Rien &agrave; traiter" in healthy_page,
      "a run with nothing to act on says so")
hostile_page = html_report.render_html(hostile_document(), exit_code=2)
check(strip_json_block(hostile_page).count("<script") == 2,
      "outside the embedded data block there are exactly two script elements "
      "on a hostile page: the filter and the data block itself")

print("-" * 70)
print("the writer")
folder = tempfile.mkdtemp()
try:
    nested = os.path.join(folder, "a", "b", "report.html")
    html_report.write_html(document, nested)
    check(os.path.isfile(nested), "write_html creates the folders it needs")
    raw = open(nested, "rb").read().decode("utf-8")
    check(raw.startswith("<!DOCTYPE html>") and raw.rstrip().endswith("</html>"),
          "and what it writes is one complete HTML document, in UTF-8")
finally:
    shutil.rmtree(folder, ignore_errors=True)

# =============================================================================
# THE GAUGES, THE FLAGS AND THE SCORE
# =============================================================================

print("-" * 70)
print("the gauges are drawn from numbers the checks carried")
doc = sick_document()
by_id = dict((c["id"], c) for c in doc["checks"])
score, determined, total = html_report.health_score(doc)
check(score is not None and 0 <= score <= 100,
      "a run with verdicts gets a score: %s out of 100, from %d determined of %d"
      % (score, determined, total))
clean = engine.run(engine.fake_healthy(), {})
check(html_report.health_score(clean)[0] == 100,
      "and a machine with nothing wrong scores 100, so the scale means "
      "something rather than being decoration")
undone_score = html_report.health_score(
    {"checks": [{"status": "Info"}, {"status": "Skipped"}]})
check(undone_score[0] is None and undone_score[1] == 0,
      "a run that determined nothing has no score at all, not a score of 100: "
      "a green gauge over an unread machine is the defect this analyser exists to "
      "prevent  <-- pinned defect")
data_fail = {"checks": [{"status": "Fail", "dataloss": True}]}
perf_fail = {"checks": [{"status": "Fail", "dataloss": False}]}
check(html_report.health_score(data_fail)[0] < html_report.health_score(perf_fail)[0],
      "a finding that bears on lost edits costs more than one that only costs "
      "time, which is the order the report puts them in")

memory_page = html_report.render_html(doc)
check('class="gauge' in memory_page and "M&eacute;moire libre" in memory_page
      if "M&eacute;moire" in memory_page else "gauges" in memory_page,
      "the gauge row is on the page")
kpi_check = by_id.get("PF-13")
if kpi_check and kpi_check.get("kpi"):
    check("Disque libre" in memory_page,
          "PF-13 carried a number, so its gauge is drawn")
no_kpi = {"report_version": 3, "taken": "x", "user": "u", "elevated": False,
          "checks": [{"id": "PF-13", "category": "Hardware", "title": "t",
                      "status": "Pass", "dataloss": False, "observed": "fine",
                      "expected": "", "impact": "", "fix": "",
                      "autofixable": False, "detail": ""}],
          "counts": {}, "act_first": [], "fixable": [], "applied": [],
          "notes": []}
check("Disque libre" not in html_report.render_html(no_kpi),
      "and a check that carried no number draws no gauge: the page would "
      "otherwise show a number the renderer invented  <-- pinned defect")
check(html_report._kpi(None, "free_pct") is None
      and html_report._kpi({"kpi": {"a": 1}}, "b") is None,
      "a missing check and a missing key both answer nothing, so a report "
      "from an older format renders with fewer gauges instead of failing")
check(html_report._num(True) is None and html_report._num("12") is None
      and html_report._num(3.5) == 3.5,
      "and a gauge value that is not a number is refused rather than coerced")

print("-" * 70)
print("the flags come from the impact lines, not from a guess")
flags = html_report.symptom_flags(doc)
check(isinstance(flags, dict) and all(
    isinstance(v, list) and v for v in flags.values()),
      "every symptom that is flagged names the checks that flagged it")
manual = set()
for check_rec in doc["checks"]:
    if check_rec["status"] in ("Fail", "Warn"):
        for code, _l in engine.SYMPTOMS:
            if code in (check_rec["impact"] or ""):
                manual.add((code, check_rec["id"]))
derived = set((code, cid) for code, ids in flags.items() for cid in ids)
check(derived == manual,
      "and the flags are exactly what the impact lines say, check for check - "
      "nothing is inferred about a symptom a check did not name")
check(all(("<b>%s</b>" % code) in html_report.render_html(doc)
          for code, _l in engine.SYMPTOMS),
      "all six symptoms have a row, including the ones nothing flagged, so a "
      "zero is visible rather than missing")
check('href="#check-%s"' % doc["act_first"][0] in html_report.render_html(doc),
      "a flagged check links to its own anchor on the page")

print("-" * 70)
print("the page is structured, in the reader's language, and honest")
page = html_report.render_html(doc, exit_code=2,
                               meta={"ArcGIS Pro": "3.4 (57127)"})
for label in ("Synth", "Jauges", "Drapeaux", "Performance", "Correctifs",
              "L&eacute;gende", "JSON"):
    if label not in page:
        check(False, "the sommaire is missing %s" % label)
        break
else:
    check(True, "the sommaire reaches every section of the report")
anchors = set(re.findall(r'href="#([a-z-]+)"', page))
ids = set(re.findall(r'id="([a-z-]+)"', page))
check(anchors <= ids or all(a in ids for a in anchors if a.startswith(
    ("synth", "jauges", "sympt", "perte", "perf", "correct", "legend", "json"))),
      "every sommaire link lands on an anchor that exists, so no link in the "
      "report is dead")
check("Probl&egrave;me d&eacute;tect&eacute;" in page,
      "a finding is presented as a detected problem, not just as a status")
check("Correction / optimisation" in page,
      "and every finding carries the correction or the optimisation beside it")
check("perte de donn&eacute;es" in page,
      "the checks that bear on lost edits are marked as such on the card, not "
      "only by their position in the list")
check("chip" in page and "free_pct_worst" in page,
      "the numbers behind a gauge are shown as well, so the reader can check "
      "the gauge against the measurement")
check("<html lang=\"fr\">" in page,
      "the page declares the language it is written in")


# =============================================================================
# THE TOOLBOX ITSELF
# =============================================================================

print("-" * 70)
print("the batch launcher exposes diagnostics and explicit recoverable actions")
batch = io.open(os.path.join(ROOT, "Diagnostiquer_ArcGIS_Pro.bat"),
                encoding="utf-8").read()
python_lines = [line.strip() for line in batch.splitlines()
                if '"%PYTHON%" %PYTHON_ARGS% "%SCRIPT%"' in line]
check(len(python_lines) == 4
      and any('--report "%OUT%" --html --verbose %ACTION_FLAG%' in line
              for line in python_lines),
      "the default diagnostic writes TXT, JSON and HTML in verbose mode")
check(all(line.startswith('"%PYTHON%" %PYTHON_ARGS% "%SCRIPT%"')
          for line in python_lines),
      "the Python executable is quoted, including Pro's Program Files path")
check(all("ArcGISPro.exe" not in line for line in python_lines),
      "no batch mode launches ArcGIS Pro itself")
check(any('--project "%PROJECT%" --project-repair "%REPAIR_MODE%"' in line
          for line in python_lines)
      and 'REPORT_PATTERN=arcgis_pro_project_analysis_*.html' in batch,
      "the batch exposes all engine project-repair modes and their HTML report")
check(any('--quarantine-addin "%PACKAGE%" %MACHINE_FLAG%' in line
          for line in python_lines)
      and any('--restore-addin "%MANIFEST%"' in line for line in python_lines),
      "quarantine, machine-wide confirmation and manifest restore are wired")
check('set "MODE=diagnose"' in batch and 'set "ACTION_FLAG="' in batch
      and 'if /i "%MODE%"=="apply" set "ACTION_FLAG=--apply"' in batch,
      "only the explicit safe-fix mode arms --apply; the default remains read-only")
check("DisableDelayedExpansion" in batch and "chcp 65001" in batch
      and "endlocal & exit /b %RC%" in batch,
      "paths are handled safely and the diagnostic exit code reaches the caller")
check("arcgispro-py3\\python.exe" in batch and "where py" in batch
      and "where python" in batch and 'start "" "%LATEST%"' in batch,
      "Pro's Python, fallbacks and the HTML report opening are all wired")

print("-" * 70)
print("the .pyt loads and its tools are what ArcGIS Pro will see")
pyt = load_pyt()
inline_dir = tempfile.mkdtemp(prefix="properf-inline-html-")
inline_error = None
inline_written = False
real_html_module = sys.modules["html_report"]
try:
    sys.modules["html_report"] = None
    inline_path = pyt.write_html_report(
        document, os.path.join(inline_dir, "standalone.json"))
    inline_written = os.path.isfile(inline_path)
except Exception as exc:
    inline_error = exc
finally:
    sys.modules["html_report"] = real_html_module
    shutil.rmtree(inline_dir, ignore_errors=True)
check(inline_error is None and inline_written
      and os.path.basename(inline_path) == "standalone.html",
      "the CLI HTML helper uses the renderer inlined in the .pyt, even when "
      "the sibling module is unavailable")
box = pyt.Toolbox()
check(box.alias == "properf" and len(box.tools) == 2
      and box.tools == [pyt.Diagnose, pyt.AnalyzeProject],
      "the toolbox exposes the workstation diagnostic and the separate "
      "selected-project diagnostic; the offline self-test stays out of the pane")
check(box.canRunInBackground is False,
      "neither runs in the background: the diagnostic reads the session it "
      "runs in, and DL-12 asks whether Pro is open")
diagnose = pyt.Diagnose()
check(diagnose.canRunInBackground is False,
      "and the tool says so itself")
check(not hasattr(pyt, "VerifyAnalyser"),
      "and the self-test tool is gone from the shipped file, not merely "
      "hidden from the list")
params = diagnose.getParameterInfo()
names = [p.name for p in params]
check(names == ["output_folder", "sde_server", "sde_port", "portal", "timeout",
                "verbose", "write_text", "open_browser", "apply_fixes",
                "html_report"],
      "the parameter list is the CLI surface, plus the two the toolbox adds")
check(params[9].direction == "Output" and params[9].parameterType == "Derived",
      "the HTML path is a derived output, so the tool can be chained")
check(params[8].value is False and params[8].enabled is False,
      "the legacy workstation-fix switch is off and disabled inside Pro: the "
      "host application cannot be asked to modify its own startup settings")
check(all((getattr(par, "helpMessage", "") or "").strip() for par in params),
      "every parameter carries a tooltip, so hovering the question mark "
      "answers what belongs in the box")
check(all((getattr(par, "parameterHelp", "") or "").strip() for par in params),
      "and a help-panel paragraph, which is where the default and the "
      "example live - a display name has to stay short enough to fit "
      "the pane, so it cannot carry either")
check("1433" in (params[2].parameterHelp or "")
      and "4000" in (params[4].parameterHelp or ""),
      "the numeric parameters name their own default and the values "
      "actually seen in the field, not just a range")
check("Laissez vide" in (params[1].parameterHelp or "")
      and "Laissez vide" in (params[3].parameterHelp or ""),
      "both network probes say what leaving them empty does, because "
      "empty is the right answer for most runs and a blank box "
      "otherwise reads as something forgotten")
check("désactivé" in (params[8].parameterHelp or "")
      and "AP-02 est retiré" in (params[8].parameterHelp or "")
      and "undo" in (params[8].parameterHelp or ""),
      "the disabled legacy switch explains the cache-only CLI fix, why the "
      "GPU change was retired, and where an older run's exact undo is stored")
check(any(ord(c) > 127 for c in "".join(
          (par.helpMessage or "") + (par.parameterHelp or "") for par in params)),
      "the parameter help is written in accented French, not in French with "
      "the accents stripped out: an unaccented paragraph reads as a machine "
      "translation and the editor stops trusting the rest of the report")
check(all("À quoi ça sert" in (par.parameterHelp or "") for par in params),
      "every parameter opens by saying what it is FOR, before its default and "
      "its example - the editor is not a specialist and should not have to "
      "guess from the display name")

print("-" * 70)
print("the toolbox refuses what the analyser's parser refuses")
for index, host in ((1, "https://gis.example.org/portal"),
                    (1, "gisdb01/share"), (3, "portal a")):
    fresh = pyt.Diagnose()
    p = fresh.getParameterInfo()
    p[index].value = host
    fresh.updateMessages(p)
    check(bool(p[index].errors),
          "%r in --%s is refused before the run" % (host, names[index]))
fresh = pyt.Diagnose()
p = fresh.getParameterInfo()
p[2].value = 0
p[4].value = 0
fresh.updateMessages(p)
check(bool(p[2].errors) and bool(p[4].errors),
      "port 0 and timeout 0 are refused, matching the exit-64 rules")
fresh = pyt.Diagnose()
p = fresh.getParameterInfo()
p[8].value = True
fresh.updateMessages(p)
check(bool(p[8].errors) and "AP-02" in p[8].errors[0],
      "a caller that forces the legacy switch gets a validation error; no "
      "workstation fix, especially the retired GPU change, can run inside Pro")

print("-" * 70)
print("the toolbox's own execute, end to end")
folder = tempfile.mkdtemp()
try:
    fresh = pyt.Diagnose()
    p = fresh.getParameterInfo()
    p[0].value = os.path.join(folder, "reports")
    p[7].value = False        # no browser in here
    messages = _Messages()
    fresh.execute(p, messages)
    html_path = p[9].value
    check(bool(html_path) and os.path.isfile(html_path),
          "execute writes the HTML report and returns its path as the output")
    json_path = os.path.splitext(html_path)[0] + ".json"
    txt_path = os.path.splitext(html_path)[0] + ".txt"
    check(os.path.isfile(json_path), "and the JSON beside it")
    check(os.path.isfile(txt_path), "and the text report, which is on by default")
    body = io.open(html_path, encoding="utf-8").read()
    check("the ArcGIS Pro geoprocessing pane" in body,
          "the page says it was produced from inside Pro, so the reader knows "
          "PF-22 was refused and DL-12 is a finding about this very session")
    check("3.4" in body, "and names the Pro release it ran under")
    check(not messages.err, "and execute reported no error on a clean run")
    written = [l for l in messages.info if l.strip().startswith("report:")]
    check(len(written) == 3, "every file it wrote is named in the messages")
    check(any(l.strip().upper().startswith(("PASS ", "FAIL ", "WARN ",
                                            "INFO ", "SKIPPED "))
              for l in messages.info),
          "the status tally reaches the geoprocessing messages, so the "
          "operator sees the verdict without leaving ArcGIS Pro")

    # Two runs in the same second must not overwrite each other.
    p2 = fresh.getParameterInfo()
    p2[0].value = os.path.join(folder, "reports")
    p2[7].value = False
    m2 = _Messages()
    fresh.execute(p2, m2)
    check(p2[9].value != html_path and os.path.isfile(p2[9].value),
          "a second run in the same second gets its own file instead of "
          "silently overwriting the first")
    check(os.path.isfile(html_path), "and the first report is still there")

    # Even a legacy model or direct caller that bypasses the disabled widget
    # cannot arm a workstation mutation in the hosting ArcGIS Pro process.
    p3 = fresh.getParameterInfo()
    p3[0].value = os.path.join(folder, "reports")
    p3[7].value = False
    p3[8].value = True
    seen_apply = []
    real_read_run = pyt.run
    def capture_read_run(env, opts):
        seen_apply.append(opts.get("apply"))
        return real_read_run(env, opts)
    pyt.run = capture_read_run
    m3 = _Messages()
    try:
        fresh.execute(p3, m3)
    finally:
        pyt.run = real_read_run
    applied = [l for l in m3.warn if l.startswith("APPLIED")]
    check(not applied, "with the legacy fixes switch forced on, nothing was applied")
    check(seen_apply == [False],
          "execute itself passes apply=False, independently of parameter validation")
    check(any("fix gate:" in l for l in m3.info),
          "and the read-only gates still report their refusals")

    # The messaging logic, against a run that does have act-first findings.
    # The document is the real one from the analyser's stand-in sick workstation;
    # only run() is swapped, so the toolbox's own execute is what runs.
    #
    # The swap lands on the .pyt module, not on the engine module imported at
    # the top of this file. The shipped .pyt carries its own copy of the
    # engine and resolves engine.run through its own globals, so patching the
    # separate module would silently not reach the tool - the assertion below
    # pins that, because a test that patched the wrong copy would keep passing
    # while testing nothing.
    sick = sick_document()
    check(pyt.run is not engine.run,
          "the shipped .pyt carries its own copy of the engine, so the swap "
          "below has to land on the .pyt to be seen by the tool")
    real_run = pyt.run
    pyt.run = lambda env, opts: json.loads(json.dumps(sick))
    try:
        p4 = fresh.getParameterInfo()
        p4[0].value = os.path.join(folder, "reports")
        p4[7].value = False
        m4 = _Messages()
        fresh.execute(p4, m4)
    finally:
        pyt.run = real_run
    rollup = [w for w in m4.warn if w.startswith("ACT ON THESE FIRST")]
    check(len(rollup) == 1, "a run with data-loss findings raises one "
                            "act-first warning in the geoprocessing window")
    check(all(cid in rollup[0] for cid in sick["act_first"])
          and len(sick["act_first"]) > 0,
          "and it names every act-first id the records produced, not a "
          "hardcoded list")
    check("exit code 2" in " ".join(m4.info),
          "the exit code and its meaning are reported, so a wrapper reading "
          "only the messages can tell a lost-edits finding from a clean run")
    check(any(l.startswith("APPLIED") for l in m4.warn) is False,
          "and still nothing was applied, with the fixes unticked")
finally:
    shutil.rmtree(folder, ignore_errors=True)

# =============================================================================
# SELECTED .APRX ANALYSIS, RECOVERY COPIES AND THE SECOND TOOL
# =============================================================================


def make_aprx(path, main_override=None, map_override=None):
    """Write a small Pro-3-shaped project with a view and one history item."""
    import zipfile
    main = {
        "type": "CIMGISProject",
        "defaultFolder": ".",
        "defaultGeoDatabase": "data.gdb",
        "views": [{"type": "CIMLayoutView",
                   "viewableObjectPath": "CIMPATH=Layout/layout.json",
                   "viewType": "esri_layouts_layoutPane"}],
        "viewLayoutXML": "<esriPaneState>layout active</esriPaneState>",
        "projectItems": [
            {"type": "CIMProjectItem", "itemType": "GPHistory",
             "name": "Buffer", "propertiesXML": "<process/>"},
            {"type": "CIMProjectItem", "itemType": "Map",
             "name": "Map", "catalogPath": "CIMPATH=Map/map.json"},
        ],
    }
    if main_override is not None:
        main = main_override
    map_doc = {
        "type": "CIMMapDocument",
        "map": {"type": "CIMMap", "name": "Map",
                "spatialReference": {"wkid": 4326},
                "layers": [{
                    "type": "CIMFeatureLayer", "name": "Parcels",
                    "spatialReference": {"wkid": 4326},
                    "refreshRate": 30,
                    "featureTable": {"dataConnection": {
                        "workspaceConnectionString":
                            "PASSWORD=do-not-report;DATABASE=data.gdb;USER=gis",
                        "dataset": "Parcels"}}}]},
    }
    if map_override is not None:
        map_doc = map_override
    layout = {"type": "CIMLayout", "name": "Layout"}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("GISProject.json", json.dumps(main))
        archive.writestr("Map/map.json", json.dumps(map_doc))
        archive.writestr("Layout/layout.json", json.dumps(layout))
        archive.writestr("DocumentInfo.xml", "<DocumentInfo/>")


print("-" * 70)
print("selected project: analysis is structured, snapshot-based and read-only")
folder = tempfile.mkdtemp(prefix="properf-project-")
try:
    source = os.path.join(folder, "CrashOnOpen.aprx")
    os.mkdir(os.path.join(folder, "data.gdb"))
    make_aprx(source)
    original = open(source, "rb").read()
    before_names = sorted(os.listdir(folder))
    document = engine.run_project(engine.Env(), {
        "project_path": source, "repair": "none"})
    check(document["analysis_type"] == "project"
          and document["project"] == source and len(document["checks"]) == 11,
          "the selected-project run has its own report type and eleven checks")
    check(open(source, "rb").read() == original
          and sorted(os.listdir(folder)) == before_names,
          "diagnostic-only mode leaves the source byte-identical and creates no project")
    by_id_project = dict((c["id"], c) for c in document["checks"])
    check(by_id_project["PR-01"]["status"] == "Pass"
          and by_id_project["PR-02"]["status"] == "Pass"
          and by_id_project["PR-05"]["status"] == "Info",
          "ZIP/CRC and JSON are validated, while a saved layout view is Info, not a false anomaly")
    check("1 vue(s)" in by_id_project["PR-05"]["observed"]
          and "GPHistory" not in by_id_project["PR-05"]["observed"],
          "the startup-state check reports the saved view without inventing a fault")
    check(by_id_project["PR-04"]["status"] == "Pass",
          "a relative DATABASE path is resolved against the original project folder, not the snapshot")
    check(all(c.get("explanation") and c.get("impact_fr")
              and c.get("steps") and c.get("verification")
              for c in document["checks"]),
          "every project check carries a French explanation, ArcGIS impact, ordered actions and verification")
    parsed = engine._parse(["--project", source, "--project-repair", "views"])
    check(parsed.project == source and parsed.project_repair == "views"
          and engine._parse(["--project", source]).project_repair == "none",
          "the CLI exposes selected-project analysis with repair off by default")
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        cli_code = engine.main(["--project", source, "--json"])
        cli_out = sys.stdout.getvalue()
        cli_err = sys.stderr.getvalue()
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    check(cli_code == 1 and json.loads(cli_out)["analysis_type"] == "project"
          and not cli_err,
          "the CLI project mode emits one clean JSON document and its measured exit code")
    try:
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        usage_code = engine.main(["--project-repair", "views"])
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    check(usage_code == 64,
          "--project-repair without --project is usage error 64, never an armed repair")
    check("do-not-report" not in json.dumps(document),
          "connection-string credentials never reach the project report")
    check(engine._database_path("SERVER=db01;DATABASE=gis;USER=editor") is None
          and engine._database_path("SERVER=db01;DATABASE=..\\data.gdb")
          == "..\\data.gdb",
          "an enterprise DATABASE name is not misreported as a missing relative file")
    project_page = html_report.render_html(document, exit_code=1,
                                            meta={"Projet analysé": source})
    check("Diagnostic d&#x27;un projet .aprx externe" in project_page
          and "Impact concret sur" in project_page
          and "Actions &agrave; prendre" in project_page
          and "V&eacute;rification apr&egrave;s correction" in project_page,
          "the HTML explains the problem, its ArcGIS impact, the actions and the after-check")
    check("CrashOnOpen.aprx" in project_page and "Comment lire et corriger" in project_page,
          "the report names the selected file and opens with a reading procedure")

    print("-" * 70)
    print("selected project: recovery changes only a verified new copy")
    recovered = engine.run_project(engine.Env(), {
        "project_path": source, "repair": "views_history"})
    output = recovered["recovery"]["path"]
    check(recovered["recovery"]["status"] == "created" and output
          and os.path.isfile(output) and output != source,
          "views_history creates a separate recovery .aprx beside the source")
    check(open(source, "rb").read() == original,
          "and the original is still byte-identical after the repair")
    inspected = engine.inspect_aprx_archive(output)
    main = inspected["main"]
    check(inspected["integrity"] == "Pass" and "views" not in main
          and "viewLayoutXML" not in main,
          "the output passes CRC again and has no startup view or pane state")
    check(not [i for i in main["projectItems"] if i.get("itemType") == "GPHistory"]
          and [i for i in main["projectItems"] if i.get("itemType") == "Map"],
          "history is removed while the map project item remains")
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        check(before.read("Map/map.json") == after.read("Map/map.json")
              and before.read("Layout/layout.json") == after.read("Layout/layout.json"),
              "map and layout definitions are byte-identical: only startup state/history changed")
    second = engine.run_project(engine.Env(), {
        "project_path": source, "repair": "views_history"})
    second_output = second["recovery"]["path"]
    check(second_output != output and os.path.isfile(second_output)
          and os.path.isfile(output),
          "a second recovery in the same second never overwrites the first copy")

    repacked = engine.run_project(engine.Env(), {
        "project_path": source, "repair": "repack"})
    repacked_path = repacked["recovery"]["path"]
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(repacked_path) as after:
        same = before.namelist() == after.namelist() and all(
            before.read(name) == after.read(name) for name in before.namelist())
    check(same and "views" in engine.inspect_aprx_archive(repacked_path)["main"],
          "repack reconstructs only the container and preserves every document, including views")

    print("-" * 70)
    print("selected project: unsafe recovery is refused, never guessed")
    bad = os.path.join(folder, "Broken.aprx")
    open(bad, "wb").write(b"this is not a zip archive")
    bad_before = open(bad, "rb").read()
    broken = engine.run_project(engine.Env(), {
        "project_path": bad, "repair": "views"})
    check(engine.by_id(broken["checks"], "PR-01")["status"] == "Fail"
          and broken["recovery"]["status"] == "refused"
          and open(bad, "rb").read() == bad_before,
          "an invalid ZIP is a measured integrity anomaly and views recovery is refused")
    bad_json = os.path.join(folder, "BrokenJson.aprx")
    with zipfile.ZipFile(bad_json, "w") as archive:
        archive.writestr("GISProject.json", "{not valid json")
        archive.writestr("Map/map.json", "{}")
    broken_json = engine.run_project(engine.Env(), {
        "project_path": bad_json, "repair": "views_history"})
    check(engine.by_id(broken_json["checks"], "PR-01")["status"] == "Pass"
          and engine.by_id(broken_json["checks"], "PR-02")["status"] == "Fail"
          and broken_json["recovery"]["status"] == "refused",
          "ZIP integrity and JSON validity stay distinct, and malformed JSON blocks modification")
    missing = engine.run_project(engine.Env(), {
        "project_path": os.path.join(folder, "Missing.aprx"), "repair": "none"})
    check(engine.by_id(missing["checks"], "PR-01")["status"] == "Info"
          and missing["applied"] == [],
          "a missing file produces an honest undetermined report, not a traceback or a repair")
    changing = os.path.join(folder, "Changing.aprx")
    make_aprx(changing)
    real_copyfile = shutil.copyfile
    def copy_then_change(src, dst):
        real_copyfile(src, dst)
        with open(src, "ab") as handle:
            handle.write(b"changed after snapshot")
    try:
        shutil.copyfile = copy_then_change
        changed_snapshot, changed_info = engine.prepare_project_snapshot(changing)
    finally:
        shutil.copyfile = real_copyfile
    if changed_snapshot and os.path.exists(changed_snapshot):
        os.unlink(changed_snapshot)
    check(changed_snapshot is None
          and "a changé pendant la copie" in changed_info["integrity_reason"],
          "a source modified during the snapshot is rejected instead of mixing two saves")
    check(engine.project_recovery_problem(
        "views", recovered["checks"], {"main": {"views": [],
        "projectItems": []}}) is not None,
        "a no-op recovery is not presented as a useful repair")
finally:
    shutil.rmtree(folder, ignore_errors=True)

print("-" * 70)
print("the ArcGIS Pro selected-project tool")
project_tool = pyt.AnalyzeProject()
project_params = project_tool.getParameterInfo()
project_names = [p.name for p in project_params]
check(project_names == ["project", "output_folder", "repair_mode", "verbose",
                        "write_text", "open_browser", "html_report",
                        "recovered_project"],
      "the second tool exposes project selection, recovery and two derived outputs")
check(project_params[2].value == pyt.RECOVERY_NONE
      and len(project_params[2].filter.list) == 4
      and project_params[6].direction == "Output"
      and project_params[7].direction == "Output",
      "recovery is off by default, has four explicit choices, and returns HTML/project paths")
check(all((p.helpMessage or "").strip() and "À quoi ça sert" in (p.parameterHelp or "")
          for p in project_params),
      "every project-tool parameter has a French tooltip and purpose paragraph")
check(project_tool.canRunInBackground is False,
      "the selected-project tool also runs in the foreground")

folder = tempfile.mkdtemp(prefix="properf-project-tool-")
try:
    source = os.path.join(folder, "External.aprx")
    os.mkdir(os.path.join(folder, "data.gdb"))
    make_aprx(source)
    original = open(source, "rb").read()
    reports = os.path.join(folder, "reports")
    old_mp = getattr(pyt.arcpy, "mp", None)
    try:
        pyt.arcpy.mp = types.SimpleNamespace(
            ArcGISProject=lambda _current: types.SimpleNamespace(filePath=source))
        active = pyt.AnalyzeProject()
        active_params = active.getParameterInfo()
        active_params[0].value = source
        active.updateMessages(active_params)
        check(bool(active_params[0].errors)
              and "actuellement ouvert" in active_params[0].errors[0],
              "the parameter validator refuses the project currently open in Pro")
        refused = False
        try:
            active.execute(active_params, _Messages())
        except pyt.arcpy.ExecuteError:
            refused = True
        check(refused, "execute refuses the active project too, not only updateMessages")

        pyt.arcpy.mp = types.SimpleNamespace(
            ArcGISProject=lambda _current: types.SimpleNamespace(
                filePath=os.path.join(folder, "Another.aprx")))
        fresh_project = pyt.AnalyzeProject()
        pp = fresh_project.getParameterInfo()
        pp[0].value = source
        pp[1].value = reports
        pp[5].value = False
        pm = _Messages()
        fresh_project.execute(pp, pm)
        check(pp[6].value and os.path.isfile(pp[6].value)
              and os.path.isfile(os.path.splitext(pp[6].value)[0] + ".json")
              and os.path.isfile(os.path.splitext(pp[6].value)[0] + ".txt"),
              "execute writes HTML, JSON and TXT for an external project while another is open")
        check(pp[7].value == "" and open(source, "rb").read() == original
              and not pm.err,
              "the default toolbox run creates no recovery project and preserves the source")
        body = io.open(pp[6].value, encoding="utf-8").read()
        check("External.aprx" in body and "copie instantan" in body
              and "Actions &agrave; prendre" in body,
              "the generated toolbox report identifies the file and carries the ordered guidance")
        check(any("Copie instantanée" in line for line in pm.info)
              and any("Pourquoi" in line for line in pm.info)
              and any("Étape 1" in line for line in pm.info),
              "the geoprocessing pane itself explains findings and their first action")

        fresh_recovery = pyt.AnalyzeProject()
        rp = fresh_recovery.getParameterInfo()
        rp[0].value = source
        rp[1].value = reports
        rp[2].value = pyt.RECOVERY_VIEWS_HISTORY
        rp[5].value = False
        rm = _Messages()
        fresh_recovery.execute(rp, rm)
        check(rp[7].value and os.path.isfile(rp[7].value)
              and rp[7].value != source and open(source, "rb").read() == original,
              "the toolbox returns the recovery path and never rewrites the original")
        check(any("COPIE CRÉÉE" in line for line in rm.warn),
              "the successful recovery and its rollback meaning are visible in Pro messages")
    finally:
        if old_mp is None:
            try:
                del pyt.arcpy.mp
            except AttributeError:
                pass
        else:
            pyt.arcpy.mp = old_mp
finally:
    shutil.rmtree(folder, ignore_errors=True)

print("-" * 70)
total = PASSED[0] + len(FAILED)
if FAILED:
    print("%d assertions, %d failed" % (total, len(FAILED)))
    for item in FAILED:
        print("  FAILED: %s" % item)
    sys.exit(1)
print("%d assertions, 0 failed" % total)
sys.exit(0)
