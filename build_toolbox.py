#!/usr/bin/env python
r"""Assemble the single-file ArcGIS Pro toolbox from its three sources.

The artefact you share is one file:
ArcGISPro_PerformanceAnalyser/ArcGISPro_PerformanceAnalyser.pyt. Copy it
anywhere, add it to ArcGIS Pro, done - there is no sibling module to keep next
to it, so nothing can go missing on the machine it lands on.

The three sources stay apart because they are separately testable. The engine
self-tests with no ArcGIS installed; the renderer is a pure function over a
document; only the glue imports arcpy. Merging them would mean either testing
nothing or stubbing arcpy for code that never touches it.

    python build_toolbox.py            # write the .pyt
    python build_toolbox.py --check    # exit 1 if the .pyt is stale

tests/test_toolbox.py runs --check, so a .pyt that has drifted from the sources
it was built from cannot sit in the repository unnoticed.

The merge is a concatenation, not an import trick, and it is safe for one
reason that is checked here rather than assumed: the three sources share no
top-level name. If a future edit introduces a collision, the build stops
instead of silently letting one section shadow another.
"""

from __future__ import print_function

import argparse
import ast
import hashlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_REL = os.path.join("ArcGISPro_PerformanceAnalyser",
                       "ArcGISPro_PerformanceAnalyser.pyt")
OUT = os.path.join(HERE, OUT_REL)

# (banner title, source file, has a shebang, ends with a __main__ guard)
SECTIONS = [
    ("diagnostic engine", "arcgis_pro_performance_analyser.py", True, True),
    ("HTML report renderer", "html_report.py", False, False),
    ("ArcGIS Pro toolbox glue", "toolbox_glue.py", False, False),
]

# Names the merged file adds of its own. Checked against every section.
OWN_NAMES = ("_Inlined", "engine", "report")

# Replaces the glue's two imports. Both sections are already defined above it
# in this same module, so the qualified names it was written against resolve
# out of the module globals.
INLINE_STUB = '''# --- merged build: both sections above live in this same module ---
class _Inlined(object):
    """Serves the qualified names the glue was written against.

    engine.Env(), engine.run(), report.write_html(): the same calls as when the
    three sources were separate modules, resolved from this file's globals.
    """

    def __getattr__(self, name):
        try:
            return globals()[name]
        except KeyError:
            raise AttributeError(name)


engine = _Inlined()
report = _Inlined()
'''

MERGE_BEGIN = "# BEGIN MERGE-IMPORTS"
MERGE_END = "# END MERGE-IMPORTS"

HEADER = r'''"""ArcGIS Pro Performance Analyser - one file, no dependencies.

GENERATED FILE, do not edit by hand. build_toolbox.py assembles it from
arcgis_pro_performance_analyser.py (the diagnostic engine), html_report.py
(the report renderer) and toolbox_glue.py (the ArcGIS Pro glue). Edit those,
then run:

    python build_toolbox.py

tests/test_toolbox.py runs build_toolbox.py --check and fails if this file has
drifted from the sources, so a stale artefact cannot be committed unnoticed.

Install
-------
Copy this single file anywhere stable, for example
C:\GIS\Tools\ArcGISPro_PerformanceAnalyser.pyt. Nothing else is needed: the
engine and the renderer are inlined below. In ArcGIS Pro, Catalog pane >
Toolboxes > Add Toolbox, and point at this file. Two tools appear under
"ArcGIS Pro Performance Analyser":

  * Diagnostiquer la lenteur d'ArcGIS Pro - the workstation diagnostic,
    written out as HTML, JSON and text, with the HTML opened in the browser.
  * Analyser un projet .aprx externe - an explicitly selected, non-active
    project read through a snapshot, with an optional recovery COPY. Its
    source is never rewritten.

Read this before you trust the numbers
--------------------------------------
Running the workstation diagnostic from inside ArcGIS Pro means ArcGIS Pro is
running, and that is not a detail:

  * DL-12 reports Pro as running, which is a data-loss finding in its own
    right and lands in the act-first list.
  * PF-01, PF-09, PF-10, PF-14 and PF-22 read the project file, so they are
    refused. Reading an .aprx while Pro may be saving it can make the save
    fail, and this tool will not cause the failure it was run to explain.
  * AP-01, the cache fix, is refused for the same reason.

So a workstation run from inside Pro is a report minus the automatic
project-file checks, and the report says so on the page. For that complete
workstation report, close Pro and run arcgis_pro_performance_analyser.py from
a command prompt in the editor's own session. The separate selected-project
tool is the exception: it can analyse another .aprx while Pro stays open,
because it snapshots that file and never opens it as the active project.

Nothing here signs in to anything and no host is contacted unless you name one.
Workstation changes require "Apply the gated fixes"; project recovery requires
its own explicit mode and writes only a new copy. Every change reports how to
go back, and no original project or source data is deleted.

Provenance
----------
%(provenance)s
"""

from __future__ import print_function
'''


def read(name):
    """One source, with its line endings normalised so the merge is stable."""
    path = os.path.join(HERE, name)
    handle = io.open(path, "r", encoding="utf-8", newline="")
    try:
        return handle.read().replace("\r\n", "\n").replace("\r", "\n")
    finally:
        handle.close()


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def top_level_names(source, name):
    """Every name a section binds at module level."""
    tree = ast.parse(source, filename=name)
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                            ast.Name):
            names.add(node.target.id)
    return names


def split_docstring(source, name):
    """(docstring text, source without it).

    A section's docstring is documentation, but only one docstring can lead a
    module, and a bare string expression in the middle of a file reads like a
    mistake. So each section's docstring is carried into its banner as
    comments instead.
    """
    tree = ast.parse(source, filename=name)
    if not tree.body:
        return "", source
    first = tree.body[0]
    if not (isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return "", source
    lines = source.splitlines(True)
    doc = "".join(lines[first.lineno - 1:first.end_lineno])
    rest = "".join(lines[first.end_lineno:])
    return doc.strip().strip('"').strip(), rest.lstrip("\n")


def strip_future(source):
    """Pull the __future__ imports out; only the first statement may hold one."""
    kept, futures = [], []
    for line in source.splitlines(True):
        if line.startswith("from __future__ import "):
            futures.append(line.rstrip("\n"))
        else:
            kept.append(line)
    return futures, "".join(kept)


def strip_main_guard(source, name):
    """Drop the CLI guard: ArcGIS Pro imports this file, it never runs it."""
    guard = '\nif __name__ == "__main__":\n    sys.exit(main())\n'
    if not source.endswith(guard):
        raise SystemExit("%s does not end with the expected __main__ guard; "
                         "fix build_toolbox.py rather than guess." % name)
    return source[:-len(guard)]


def comment_block(text):
    return "".join(("# " + line.rstrip() + "\n").rstrip() + "\n"
                   if line.strip() else "#\n"
                   for line in text.splitlines(True))


def build():
    """The whole .pyt, as text. Deterministic: no timestamp, no host name."""
    parts, futures, seen, provenance = [], [], {}, []

    for index, (title, name, has_shebang, has_guard) in enumerate(SECTIONS, 1):
        source = read(name)
        raw_digest = digest(source)
        provenance.append("  %-40s %5d lines  sha256:%s"
                          % (name, len(source.splitlines()), raw_digest))

        if has_shebang and source.startswith("#!"):
            source = source.split("\n", 1)[1]
        doc, source = split_docstring(source, name)
        found, source = strip_future(source)
        for line in found:
            if line not in futures:
                futures.append(line)
        if has_guard:
            source = strip_main_guard(source, name)

        # Names are collected before the stub goes in: OWN_NAMES below must be
        # checked against what the sources define, not against the stub itself.
        for name_bound in top_level_names(source, name):
            where = seen.get(name_bound)
            if where:
                raise SystemExit(
                    "name collision: %r is defined in both %s and %s. The "
                    "merge is a concatenation, so one would shadow the other. "
                    "Rename it in one of the two sources."
                    % (name_bound, where, name))
            seen[name_bound] = name

        if MERGE_BEGIN in source:
            head, tail = source.split(MERGE_BEGIN, 1)
            _, tail = tail.split(MERGE_END, 1)
            source = head + INLINE_STUB + tail.lstrip("\n")

        banner = [
            "\n",
            "# " + "=" * 76 + "\n",
            "# %d/%d  %s\n" % (index, len(SECTIONS), title),
            "# source: %s  (sha256:%s)\n" % (name, raw_digest),
            "# " + "-" * 76 + "\n",
        ]
        if doc:
            banner.append(comment_block(doc))
        if has_guard:
            banner.append("#\n# The __main__ guard is dropped: ArcGIS Pro "
                          "imports this file, it does not run it.\n")
        banner.append("# " + "=" * 76 + "\n\n")
        parts.append("".join(banner))
        parts.append(source.rstrip("\n") + "\n")

    for name in OWN_NAMES:
        if name in seen:
            raise SystemExit("the merged file binds %r itself, and %s already "
                             "defines it. Rename one of them."
                             % (name, seen[name]))

    if futures != ["from __future__ import print_function"]:
        raise SystemExit("unexpected __future__ imports: %r" % (futures,))
    if not isinstance(HEADER, str) or "%(provenance)s" not in HEADER:
        raise SystemExit("HEADER lost its provenance placeholder")

    return HEADER % {"provenance": "\n".join(provenance)} + "".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="build_toolbox.py",
        description="Assemble the single-file ArcGIS Pro toolbox.",
        allow_abbrev=False)
    parser.add_argument("--check", action="store_true",
                        help="do not write; exit 1 if the .pyt is stale")
    parser.add_argument("--print", dest="show", action="store_true",
                        help="write the result to stdout instead of the file")
    opts = parser.parse_args(argv)

    text = build()

    if opts.show:
        sys.stdout.write(text)
        return 0

    current = None
    if os.path.exists(OUT):
        handle = io.open(OUT, "r", encoding="utf-8", newline="")
        try:
            current = handle.read()
        finally:
            handle.close()

    if opts.check:
        if current == text:
            print("%s is up to date (%d lines)."
                  % (OUT_REL, len(text.splitlines())))
            return 0
        print("%s is STALE. Run: python build_toolbox.py" % OUT_REL,
              file=sys.stderr)
        if current is None:
            print("  the file does not exist.", file=sys.stderr)
        else:
            print("  committed %d lines, sources produce %d."
                  % (len(current.splitlines()), len(text.splitlines())),
                  file=sys.stderr)
        return 1

    folder = os.path.dirname(OUT)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    handle = io.open(OUT, "w", encoding="utf-8", newline="")
    try:
        handle.write(text)
    finally:
        handle.close()
    print("wrote %s (%d lines, %d bytes)."
          % (OUT_REL, len(text.splitlines()), len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
