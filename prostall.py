#!/usr/bin/env python
r"""Diagnose why ArcGIS Pro is slow on this workstation, and never report a finding the machine did not produce.

An editor says Pro takes five minutes to open, hangs mid-edit, and sometimes
their saved edits are gone. There is no error message. The causes are spread
across the registry, the filesystem, OneDrive's redirection state, Offline
Files and the per-app GPU preference, and no single pane shows them together.
The deeper problem is the diagnostic itself. A check that prints a verdict on
something it never measured teaches the operator to distrust the whole report,
and the report is the only thing standing between them and a rebuild.

ArcGIS Pro ships a Diagnostic Monitor, and it is the right tool for what it
does: it traces Pro from inside the process, timestamps every event, and hands
Esri support a log they can read. It cannot see the workstation. Known Folder
Move, a dehydrated project file, Offline Files, the GPU Windows chose, the
storage class of the project folder - all of that is outside the process, and a
session that never finished opening produces no trace at all. That is the gap
this fills, from the outside, before the case is opened.

Check by default. A plain run reads and prints. --apply is the only path that
changes machine state, every applied change prints its own undo, and nothing
here deletes anything. The network probes are opt-in: without --sde-server and
--portal no host is contacted. The one exception is a project folder that
already lives on a share: reading it crosses the network, as Pro does when it
opens the project, and PF-03 times one listing of it.

    python prostall.py --self-test
    python prostall.py
    python prostall.py --sde-server gisdb01 --portal gis.example.org --verbose
    python prostall.py --report C:\Temp\properf
    python prostall.py --apply

Exit codes: 0 nothing to act on, 1 performance findings only, 2 at least one
finding bearing on lost edits, 3 no finding but nothing was determined either,
64 usage error or a report that could not be written.
"""

from __future__ import print_function

import argparse
import collections
import json
import ntpath
import os
import re
import socket
import sys
import textwrap
import time

# =============================================================================
# CONFIGURATION. Everything an operator is expected to change lives here.
# =============================================================================

# -- Which checks run ---------------------------------------------------------
# False turns a check off. It still appears in both reports, as Skipped with
# "turned off in CHECKS_ENABLED", because a check that silently vanished would
# read as one that passed. A fix whose check is off is refused. An id not
# listed here runs; an id listed here that no check owns is a startup error.
CHECKS_ENABLED = {
    # Data loss
    "DL-00": True,   # Account being checked
    "DL-05": True,   # Where projects are stored
    "DL-03": True,   # Project backup setting
    "DL-04": True,   # Network adapter power saving
    "DL-06": True,   # Documents folder synced by OneDrive
    "DL-07": True,   # Online-only OneDrive files in project folders
    "DL-08": True,   # Windows Offline Files
    "DL-09": True,   # ArcGIS Pro crash files
    "DL-10": True,   # Connection to the database server
    "DL-11": True,   # Database connection files (.sde)
    "DL-12": True,   # ArcGIS Pro open during the check
    "DL-13": True,   # Crashes and freezes in the Windows event log
    "PF-07": True,   # Leftover ArcGIS Pro session folders
    # Performance
    "PF-02": True,   # Network file-share (SMB) settings
    "PF-03": True,   # Network share speed
    "PF-04": True,   # ArcGIS Pro version
    "PF-05": True,   # Pro display settings
    "PF-06": True,   # Pro local cache
    "PF-08": True,   # Pro search index
    "PF-11": True,   # Graphics card used by Pro
    "PF-12": True,   # Memory (RAM)
    "PF-13": True,   # Free disk space
    "PF-15": True,   # Power plan
    "PF-17": True,   # Antivirus exclusions (Microsoft Defender)
    "PF-19": True,   # Connection to the portal
    "PF-22": True,   # Layer refresh timers and default geodatabase
    "PF-26": True,   # Roaming profile
    "PF-27": True,   # Map not redrawing after a field calculation
}

# -- Which fixes --apply may run ----------------------------------------------
# ALLOW_APPLY False refuses --apply outright, so no run on this copy can change
# the machine. FIXES_ENABLED turns off one fix at a time. Neither one arms
# anything: a fix still needs --apply on the command line.
ALLOW_APPLY = True
FIXES_ENABLED = {
    "AP-01": True,   # Rename the ESRI local cache folder aside (acts on PF-06)
    "AP-02": True,   # Pin Pro to the high-performance GPU (acts on PF-11)
}

# -- Run defaults -------------------------------------------------------------
# What a run uses when the matching flag is not given. A flag always wins.
# None for a host means that probe is skipped and no packet is sent.
SDE_SERVER = None       # --sde-server, such as "gisdb01"
SDE_PORT = 1433         # --sde-port
PORTAL = None           # --portal, such as "gis.example.org"
TIMEOUT_MS = 4000       # --timeout, the bound on each network probe
REPORT_DIR = None       # --report, None writes no file
HTML_FILE = None        # --html, such as r"C:\Temp\prostall.html"
OPEN_HTML = True        # open the HTML report in the browser once written;
                        # --no-open turns it off for one run

# The line printed above the title of the HTML report. "" prints none.
HTML_ORG_NAME = "New York City Emergency Management"

# Where Pro is assumed to be installed when the registry does not say.
DEFAULT_PRO_EXE = r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe"

# -- Thresholds ---------------------------------------------------------------
SDE_LATENCY_WARN_MS = 250       # a LAN SQL host answers far inside this
PORTAL_LATENCY_WARN_MS = 1500   # a portal over the WAN is allowed to be slower
MIN_MEMORY_GB = 16.0            # below this, enterprise SDE editing pages
LOW_FREE_MEMORY_GB = 2.0
MIN_FREE_DISK_GB = 20.0
MIN_FREE_DISK_PCT = 10.0
CACHE_BIG_BYTES = 2 * 1024 ** 3
CACHE_BIG_FILES = 20000
CACHE_STALE_DAYS = 30
CACHE_OLD_DAYS = 90
CACHE_STALE_RATIO = 0.8
CRASH_RECENT_DAYS = 45
PROTEMP_MANY = 25
INDEX_MANY_CONNECTIONS = 10

# The event timeline reads this many days back, and at most this many events
# per log. A log that hits the cap is reported as capped, not as complete.
EVENT_WINDOW_DAYS = 14
EVENT_MAX = 200

# One directory listing on the project share. These are this tool's
# allowances, not a published figure: Pro lists folders many times while it
# opens a project, so a single listing in the hundreds of milliseconds is
# already the slow open the editor describes.
SHARE_LIST_WARN_MS = 400
SHARE_LIST_FAIL_MS = 1500
SHARE_LIST_LIMIT = 500
SHARE_FOLDERS_TIMED = 2

# The .aprx read is bounded, because a project on a share is exactly where a
# full read is slow.
APRX_MAX_PROJECTS = 3
APRX_MAX_ENTRIES = 300
APRX_MAX_ENTRY_BYTES = 4 * 1024 ** 2
APRX_MAX_BYTES = 32 * 1024 ** 2

# =============================================================================
# THE STATUS CONTRACT. This is the whole point of the tool.
# =============================================================================

# Five statuses, and no sixth. A check that wants to say something else is
# saying one of these badly.
STATUSES = ("Pass", "Warn", "Fail", "Info", "Skipped")

# A verdict about the machine. Only a check that measured something may carry
# one of these.
DETERMINED = ("Pass", "Warn", "Fail")

# Not a verdict. Info means "here is what I read, it decides nothing"; Skipped
# means "I did not read it at all". Neither one reaches the act-first list and
# neither one lets a fix run.
UNDETERMINED = ("Info", "Skipped")

# The canonical sentences a check body uses when it did not measure. A body
# that puts one of these in its Observed text has declared itself undetermined,
# and make_check refuses to pair that text with a verdict.
#
# This exists because of a shipped defect. Two geodatabase checks set their
# status to Fail before the branch that would have gone and measured anything,
# so on a workstation that had never contacted the database host they both
# printed FAIL, were listed under ACT ON THESE FIRST, and said
# "Not observable from the workstation" in the same breath. The verdict and the
# evidence contradicted each other on the two most serious lines of the report.
# Both checks are gone. This gate is what stops the next one.
NOT_MEASURED = (
    "not observable from this workstation",
    "not observable from the workstation",
    "was not measured",
    "not measured this run",
    "could not be read",
    "could not be parsed",
    "could not be determined",
    "returned nothing",
    "not re-verified",
)

# The symptoms the checks map onto, kept generic. An Impact line that does not
# name one of these is describing a machine, not a complaint.
SYMPTOMS = (
    ("S1", "the project opens slowly, minutes rather than seconds"),
    ("S2", "the session hangs for minutes mid-edit, sometimes to a force-close"),
    ("S3", "saved edits are silently gone after such a hang (most serious)"),
    ("S4", "a field-calculated value updates the table but the map does not redraw"),
    ("S5", "copy or paste of a feature does nothing for many seconds, and "
           "retries duplicate it"),
)

# Report format version. Bumped when the JSON shape changes, so a diff against
# an older report from a known-good workstation fails loudly instead of quietly.
# 2 added the plain-language "result" sentence to every check.
REPORT_VERSION = 2

# Get-MpPreference does not fail for a caller who may not see the exclusion
# lists. It puts this text where each list would be. Compared as a path, it
# reports every exclusion missing on a machine that may have them all.
DEFENDER_SENTINEL = re.compile(r"^\s*N/A\s*:|must be an administrator",
                               re.IGNORECASE)

# Defender expands variables in an exclusion as LocalSystem, not as the user.
# These five resolve under the system profile, so an exclusion written with
# one of them never covers the editor's own folder.
LOCALSYSTEM_PROFILE_VARS = {
    "USERPROFILE": r"System32\config\systemprofile",
    "APPDATA": r"System32\config\systemprofile\AppData\Roaming",
    "LOCALAPPDATA": r"System32\config\systemprofile\AppData\Local",
    "TEMP": r"TEMP",
    "TMP": r"TEMP",
}

EVENT_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"

# An empty file geodatabase table file is a bare header. Anything larger holds
# rows, which is the difference between the husk of a crashed session and the
# unsaved scratch work that session never wrote out.
EMPTY_GDBTABLE_BYTES = 4096

HIGH_PERFORMANCE_PLAN = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
GPU_PREFERENCE_KEY = r"HKCU\Software\Microsoft\DirectX\UserGpuPreferences"
GPU_PREFERENCE_WANTED = "GpuPreference=2;"
PRO_REG_KEY = r"HKLM\SOFTWARE\ESRI\ArcGISPro"


class UnmeasuredFinding(ValueError):
    """A check tried to pair a verdict with text saying it measured nothing."""


def says_unmeasured(text):
    """True when this Observed text declares that the check did not measure."""
    low = (text or "").lower()
    for phrase in NOT_MEASURED:
        if phrase in low:
            return True
    return False


def _join(value):
    """Observed and Detail accept a list of lines or a single string."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(["%s" % line for line in value])
    return "%s" % value


def make_check(cid, category, title, status, observed, expected="", impact="",
               fix="", autofixable=False, dataloss=False, detail="",
               measured=True, result=""):
    """Build one check record, or refuse.

    Four refusals, each one a defect somebody shipped:

    1. A status outside the five. The original enforced this with a
       ValidateSet, and it is the only reason the report can be summed at all.
    2. A verdict on a check whose own text says it did not measure.
    3. A verdict on a check that passed measured=False. The text test catches a
       body that lies by accident; this catches one that lies on purpose.
    4. AutoFixable with no fix text. The summary offers those to the operator
       by id, so a fixable flag with nothing behind it is an offer of nothing.
    """
    if status not in STATUSES:
        raise ValueError("status %r is not one of %s"
                         % (status, ", ".join(STATUSES)))
    if not cid or not category or not title:
        raise ValueError("a check needs an id, a category and a title")
    observed = _join(observed)
    detail = _join(detail)
    if status in DETERMINED and (not measured or says_unmeasured(observed)):
        raise UnmeasuredFinding(
            "%s carries status %s but its Observed text says it measured "
            "nothing: %r" % (cid, status, observed[:120]))
    if autofixable and not fix.strip():
        raise ValueError("%s is marked auto-fixable with no fix text" % cid)
    return {
        "id": cid,
        "category": category,
        "title": title,
        "status": status,
        "result": result,
        "dataloss": bool(dataloss),
        "observed": observed,
        "expected": expected,
        "impact": impact,
        "fix": fix,
        "autofixable": bool(autofixable),
        "measured": bool(measured),
        "detail": detail,
    }


def add_check(checks, **kwargs):
    """Append one record to the run. Every check body ends here."""
    rec = make_check(**kwargs)
    checks.append(rec)
    return rec


def safe_check(checks, cid, category, title, body, dataloss=False):
    """Run one check body under containment.

    A probe that throws must not end the run, and must not be recorded as Warn.
    Skipped, deliberately, not Warn: every fix gates on Warn or Fail, so a
    crashed probe recorded as Warn is indistinguishable from a real defect and
    lets the fix fire on a determination that was never made. An apply must
    never act on a check whose body threw.

    Records the body already appended are kept. They were measured; the
    exception says only that the body did not finish.
    """
    before = len(checks)
    try:
        body(checks)
    except Exception as exc:   # any probe failure, including a refused verdict
        add_check(checks, cid=cid, category=category, title=title,
                  status="Skipped", dataloss=dataloss, measured=False,
                  result="Not checked: the check stopped with an error.",
                  observed="Probe failed and was not measured this run: "
                           "%s: %s" % (type(exc).__name__, exc),
                  expected="The check completes and records a value.",
                  impact="This possible cause is neither confirmed nor ruled "
                         "out, and no automatic fix will act on it.",
                  fix="Run prostall again. If the same error appears, check "
                      "this item by hand and report the error as a bug in "
                      "prostall.")
        return
    if len(checks) == before:
        # A body that emitted nothing is a bug in the body, not a healthy
        # machine. Saying nothing reads as saying Pass, which is the failure
        # this whole tool exists to prevent.
        add_check(checks, cid=cid, category=category, title=title,
                  status="Skipped", dataloss=dataloss, measured=False,
                  result="Not checked: the check finished without a result.",
                  observed="The check produced no record, so nothing about it "
                           "was measured.",
                  expected="At least one record.",
                  impact="This possible cause is neither confirmed nor ruled "
                         "out.",
                  fix="Check this item by hand, and report the empty result "
                      "as a bug in prostall.")


# =============================================================================
# ROLL-UPS. Pure functions over the finished check list.
# =============================================================================

def act_first(checks):
    """The findings that bear on lost edits, in the order they were recorded.

    Membership is a property of the record: flagged as bearing on data loss,
    and carrying a verdict of Warn or Fail. The original kept an explicit id
    list here so two named checks stayed listed "even if a future branch
    demotes them" - which is a roll-up promoting a finding the check itself had
    withdrawn. A check owns its own status. If it is Info, it belongs nowhere
    near the top of the report.
    """
    return [c for c in checks
            if c["dataloss"] and c["status"] in ("Fail", "Warn")]


def fixable(checks):
    """Findings a fix exists for, and that are actually in a failing state."""
    return [c for c in checks
            if c["autofixable"] and c["status"] in ("Fail", "Warn")]


def counts(checks):
    """Status tally, in the fixed order the summary prints."""
    return [(status, len([c for c in checks if c["status"] == status]))
            for status in ("Fail", "Warn", "Pass", "Info", "Skipped")]


def by_id(checks, cid):
    """The first record with this id, or None."""
    for c in checks:
        if c["id"] == cid:
            return c
    return None


# These two checks can answer Pass on a machine that read nothing else, so a
# Pass from either one proves nothing about the workstation.
ALWAYS_ANSWERING = ("DL-00", "DL-12")


def exit_code(checks):
    """0 clean, 1 performance findings only, 2 a finding bearing on lost edits,
    3 no finding and nothing was measured either.

    3 exists because a wrapper reading the code cannot see the stderr note. A
    run that could read nothing (PowerShell blocked, a host that is not
    Windows) has no finding, and 0 would tell the wrapper the machine is fine.
    """
    if act_first(checks):
        return 2
    if [c for c in checks if c["status"] in ("Fail", "Warn")]:
        return 1
    if not [c for c in checks if c["status"] == "Pass"
            and c["id"] not in ALWAYS_ANSWERING]:
        return 3
    return 0


# =============================================================================
# PURE PATH AND PARSE HELPERS
# =============================================================================

def format_bytes(size):
    """Human-readable size. Deterministic, so two reports diff cleanly."""
    size = float(size)
    for unit, scale in (("GB", 1024.0 ** 3), ("MB", 1024.0 ** 2),
                        ("KB", 1024.0)):
        if size >= scale:
            return "%.2f %s" % (size / scale, unit)
    return "%d B" % int(size)


def is_network_path(path, network_drives=()):
    """True for a UNC path or a path on a mapped network drive.

    ntpath, not os.path, on purpose. os.path is whichever module the host
    happens to be running, so on Linux os.path.splitdrive(r"C:\\GIS")
    returns no drive at all and a Windows path is silently misclassified. The
    question here is about the path, not about the host reading it.
    """
    if not path or not path.strip():
        return False
    path = path.replace("/", "\\")
    if path.startswith("\\\\"):
        return True
    drive = ntpath.splitdrive(path)[0]
    if drive and drive.rstrip("\\").upper() in [d.upper() for d in network_drives]:
        return True
    return False


# A Known Folder Move root is named "OneDrive - <Tenant>". Match the segment as
# well as the environment variable: OneDrive is commonly unset in an elevated
# shell even though the interactive user's Documents are redirected.
_ONEDRIVE_SEGMENT = re.compile(r"[\\/]onedrive( -|[\\/])", re.IGNORECASE)


def is_onedrive_path(path, onedrive_roots=()):
    """True when this path sits inside a OneDrive-synced tree."""
    if not path or not path.strip():
        return False
    norm = path.replace("/", "\\")
    low = norm.lower()
    for root in onedrive_roots:
        if root and low.startswith(root.replace("/", "\\").lower()):
            return True
    return bool(_ONEDRIVE_SEGMENT.search("\\" + norm))


def path_class(path, onedrive_roots=(), network_drives=()):
    """NETWORK, ONEDRIVE or LOCAL.

    Network wins over OneDrive: a redirected folder on a share pays SMB latency
    whether or not it also syncs, and the fix for the share is the one that
    matters first.
    """
    if is_network_path(path, network_drives):
        return "NETWORK"
    if is_onedrive_path(path, onedrive_roots):
        return "ONEDRIVE"
    return "LOCAL"


def path_root(path):
    """The drive or share root of a Windows path, or an empty string."""
    if not path:
        return ""
    return ntpath.splitdrive(path.replace("/", "\\"))[0]


def expand_vars(env, text):
    """Expand %NAME% against the environment this run is reading.

    Not the standard expander. os.path is whichever module the host happens to
    be running, so on Linux it expands $NAME and leaves %NAME% untouched, and a
    Windows shell folder value would come back unexpanded - the check reading
    it would then report a path that does not exist and call the folder absent.
    Expanding from the Env also means the stand-in machine in the self-test
    answers, instead of the real process environment leaking into a test.
    """
    def replace(match):
        return env.getenv(match.group(1)) or match.group(0)
    return re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", replace, text or "")


def parse_user_config(text):
    """Read the setting name/value pairs out of a Pro user.config.

    Read-only, always. Hand-editing user.config is unsupported by Esri and is
    overwritten whenever Pro next saves its settings, so this tool reports what
    it finds and never writes it back. First value wins: the names are unique
    within a section, and a later duplicate is a merge artefact.

    ElementTree's default parser does not resolve external entities and does
    not expand custom ones - it raises on an undefined entity instead - so a
    user.config cannot make this read a file off a share. Malformed XML is a
    missing answer, not a traceback: the check that called this then says the
    settings could not be parsed and stays undetermined.
    """
    from xml.etree import ElementTree   # imported here so a run with no
                                        # user.config never pays for it

    out = {}
    if not text or not text.strip():
        return out
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return out
    for node in root.iter("setting"):
        name = node.get("name")
        if not name or name in out:
            continue
        value = node.find("value")
        out[name] = "" if value is None or value.text is None else value.text
    return out


def cfg(settings, name):
    """One setting, or None. Kept separate so <absent> prints in one place."""
    if settings and name in settings:
        return settings[name]
    return None


def cfg_line(settings, name):
    """A printable 'name = value' line, marking a genuinely absent value."""
    value = cfg(settings, name)
    return "%s = %s" % (name, "<absent>" if value is None else value)


def cache_triggers(total_bytes, files, oldest_age_days, stale_files):
    """Size and staleness are separate triggers. Returns (big, stale).

    The original tested staleness with an absolute count, which was dead code
    on a folder Pro keeps well under fifty files: fourteen files, of which all
    fourteen were orphaned caches two years old, reported Pass - exactly the
    state the check's own Expected text calls the failure. Proportional, with
    an absolute age backstop.
    """
    big = (total_bytes >= CACHE_BIG_BYTES or files >= CACHE_BIG_FILES)
    ratio = (float(stale_files) / float(files)) if files else 0.0
    stale = ((stale_files > 0 and ratio >= CACHE_STALE_RATIO)
             or (oldest_age_days is not None
                 and oldest_age_days > CACHE_OLD_DAYS))
    return big, stale, ratio


# =============================================================================
# THE DECISIONS. One function per check, all pure. Each returns the keyword
# arguments for make_check, so a test can read the verdict without a machine.
# =============================================================================

def decide_session(me, other_profiles, elevated):
    """DL-00. Is this diagnostic reading the profile that actually edits?

    Pro leaves a roaming ESRI folder per profile. If a profile other than the
    one running this carries that folder, every per-user check below read the
    wrong hive, and its "absent" results mean nothing. That is more dangerous
    than a plain failure, because absent reads as healthy.
    """
    title = "Account being checked"
    lines = ["Running as: %s  (elevated: %s)" % (me, elevated)]
    for name in other_profiles:
        lines.append("Profile carrying ArcGIS Pro user data: %s" % name)
    if other_profiles:
        lines.append("Every per-user check in this report read the current "
                     "profile, not that one.")
        return dict(
            cid="DL-00", category="Session", dataloss=True, status="Fail",
            title=title,
            result="Wrong account. prostall ran as %s, but ArcGIS Pro is "
                   "used from %s. The per-user results in this report "
                   "describe the wrong account." % (me,
                                                    ", ".join(other_profiles)),
            observed=lines,
            expected="The tool runs in the editor's own logged-on session, so "
                     "the ESRI folders, user.config, project paths, OneDrive "
                     "state and local caches it reads are the editor's.",
            impact="Settings that look missing in this report may only be "
                   "missing from this account. Treat every per-user result "
                   "as unchecked.",
            fix="Sign in as %s and run prostall again, without \"Run as "
                "administrator\". The machine-wide results in this report "
                "are still valid." % ", ".join(other_profiles))
    return dict(
        cid="DL-00", category="Session", dataloss=True, status="Pass",
        title=title,
        result="prostall ran as %s, and no other account on this computer "
               "has ArcGIS Pro data." % me,
        observed=lines,
        expected="No other profile on this machine carries ArcGIS Pro user data.")


def decide_backup(config_path, settings, home_class):
    """DL-03. The project backup interval, and what it does not protect.

    Editors read "Create backups of project" as an autosave for their edits; it
    backs up the project file and nothing else, so a stranded version state is
    stranded regardless of this setting. On a network or synced home folder a
    short interval is also a recurring multi-second stall, which is the pause
    the editor calls "the refresh".
    """
    title = "Project backup setting"
    if not config_path:
        return dict(
            cid="DL-03", category="ArcGISPro", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check: Pro's settings file (user.config) was "
                   "not found for this account.",
            observed="No user.config was located for this profile, so the "
                     "backup settings could not be read.",
            expected="A readable user.config for the installed Pro release.",
            fix="In Pro, open Project > Options > General and look at "
                "\"Create backups of project\".")
    enabled = cfg(settings, "CreateProjectBackups")
    interval = cfg(settings, "CreateProjectBackupsIntervalMinutes")
    limit = cfg(settings, "SaveProProjectLimit")
    try:
        minutes = int(str(interval).strip())
    except (TypeError, ValueError):
        minutes = 0
    status = "Info"
    # A short interval plus a remote home folder is the combination that
    # stalls. Either one alone is a setting, not a finding.
    if (str(enabled) == "True" and 0 < minutes <= 5
            and home_class in ("NETWORK", "ONEDRIVE")):
        status = "Warn"
    if status == "Warn":
        result = ("Pro backs up the project every %d minutes to a folder "
                  "that is not on this computer, which causes a short "
                  "freeze each time." % minutes)
        fix = ("In Pro, open Project > Options > General > \"Create "
               "backups of project\". Set the interval to 15 minutes or "
               "more, or turn backups off while the project is on a network "
               "or OneDrive folder. Tell the editor this setting does not "
               "save their edits.")
    elif str(enabled) == "True":
        result = "Project backups are on, every %s minutes." % interval
        fix = ""
    else:
        result = "Project backups are off."
        fix = ""
    return dict(
        cid="DL-03", category="ArcGISPro", dataloss=True, status=status,
        title=title, result=result,
        observed=["CreateProjectBackups = %s" % enabled,
                  "CreateProjectBackupsIntervalMinutes = %s" % interval,
                  "SaveProProjectLimit = %s" % limit,
                  "recent project storage: %s" % home_class,
                  "user.config: %s" % config_path],
        expected="Understood as a project-file backup only. On a network or "
                 "synced home folder an interval of five minutes or less is a "
                 "recurring multi-second stall.",
        impact="This backup copies the project file only. It does not protect "
               "the edits themselves, so it will not bring back lost edits.",
        fix=fix,
        detail="Read-only parse. Hand-editing user.config is unsupported and "
               "is overwritten the next time Pro saves settings.")


def decide_storage(entries):
    """DL-05. Where the recently opened projects actually live.

    entries: (path, when, class) newest first.

    One title per check, and the outcome in the result. A title that stated
    the problem made a healthy machine print "[PASS] Active projects live on
    network storage", which reads as the failure.
    """
    title = "Where projects are stored"
    if not entries:
        return dict(
            cid="DL-05", category="Storage", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check: no recently opened project was found.",
            observed="No recently opened project was found, so where the "
                     "editor works could not be determined.",
            expected="Projects used for active editing on a local disk.",
            fix="Ask the editor to open their project, then look at Project "
                "> Options > General > Current Settings > Home Folder.")
    lines = []
    remote = []
    for path, when, cls in entries:
        lines.append("%-8s %s  %s" % (cls, when, path))
        if cls in ("NETWORK", "ONEDRIVE"):
            remote.append(path)
    if remote:
        return dict(
            cid="DL-05", category="Storage", dataloss=True, status="Fail",
            title=title,
            result="%d of %d recent project(s) are stored on a network drive "
                   "or in OneDrive, not on this computer."
                   % (len(remote), len(entries)),
            observed=lines,
            expected="Projects used for active editing on a local disk. File "
                     "geodatabases and project files are chatty - many small "
                     "metadata round trips per operation - and every one pays "
                     "the latency of the share.",
            impact="Pro reads and writes many small files while you work. Over "
                   "a network or a sync folder this makes projects slow to "
                   "open, causes freezes, and a save that times out can lose "
                   "edits.",
            fix="Copy the project folder, and any file geodatabase it uses, "
                "to a folder on this computer (for example C:\\GIS\\Projects) "
                "and edit it there. Keep only enterprise geodatabase layers "
                "on the server. prostall will not move projects for you.",
            detail="remote=%d of %d recent project(s)" % (len(remote),
                                                          len(entries)))
    return dict(
        cid="DL-05", category="Storage", dataloss=True, status="Pass",
        title=title,
        result="Every recent project is stored on this computer.",
        observed=lines,
        expected="Projects used for active editing on a local disk.")


def decide_kfm(documents, onedrive_root, account_lines):
    """DL-06. Are the user's documents redirected into OneDrive?

    The verdict is the Documents path, which is measured directly. The
    redirection bitmask is printed as supporting evidence and is not decoded:
    the value most guides name does not exist on current OneDrive builds, and
    rendered as absent on a box whose Desktop, Documents and Pictures were all
    redirected, so the line contradicted the finding. Every account subkey is
    enumerated, because a second tenant lands on its own key.
    """
    title = "Documents folder synced by OneDrive"
    lines = ["Documents (Personal) = %s" % (documents or "<unreadable>"),
             "OneDrive root        = %s" % (onedrive_root or "<unset>")]
    lines.extend(account_lines or
                 ["KFM: no OneDrive account subkey for this profile."])
    if not documents:
        return dict(
            cid="DL-06", category="Storage", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check: the location of the Documents folder "
                   "could not be read.",
            observed=lines + ["The Documents shell folder could not be read, "
                              "so redirection was not determined."],
            expected="GIS projects outside every OneDrive-synced tree.",
            fix="Right-click the Documents folder, choose Properties, and "
                "look at the Location tab. If it points into OneDrive, "
                "projects saved there are synced.")
    redirected = is_onedrive_path(
        documents, [onedrive_root] if onedrive_root else [])
    if not redirected:
        return dict(
            cid="DL-06", category="Storage", dataloss=True, status="Pass",
            title=title,
            result="The Documents folder is a normal local folder, not "
                   "synced by OneDrive.",
            observed=lines,
            expected="GIS projects and geodatabases outside every "
                     "OneDrive-synced tree.")
    return dict(
        cid="DL-06", category="Storage", dataloss=True, status="Fail",
        title=title,
        result="The Documents folder has been moved into OneDrive, so "
               "anything saved under Documents, including Pro's default "
               "project folder, is synced to the cloud.",
        observed=lines,
        expected="GIS projects and geodatabases outside every OneDrive-synced "
                 "tree. Esri does not support ArcGIS Pro against cloud-sync "
                 "storage.",
        impact="Esri does not support ArcGIS Pro on cloud-synced folders. "
               "OneDrive can lock or half-sync a geodatabase file while Pro "
               "is writing it, which causes freezes and lost edits.",
        fix="Keep projects in a local folder outside OneDrive, such as "
            "C:\\GIS\\Projects. If your organisation manages OneDrive "
            "centrally, ask IT to exclude that folder. prostall will not "
            "change this setting.")


def decide_placeholders(roots, inspected, placeholders):
    """DL-07. Dehydrated Files-On-Demand items under the project tree.

    A geodatabase whose component files are split between cloud-only
    placeholders and local copies stalls on every recall, and OneDrive can hold
    a lock on one of them while Pro writes another.
    """
    title = "Online-only OneDrive files in project folders"
    if not roots:
        return dict(
            cid="DL-07", category="Storage", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check: no project folder was found to look in.",
            observed="No project folder was found to inspect, so placeholder "
                     "state was not measured this run.",
            expected="Any project folder fully local, with no Files-On-Demand "
                     "placeholders.",
            fix="If projects are kept somewhere else, look in that folder for "
                "files with a cloud icon (\"available when online\").")
    if placeholders:
        return dict(
            cid="DL-07", category="Storage", dataloss=True, status="Warn",
            title=title,
            result="%d file(s) in the project folders are online-only: "
                   "OneDrive has to download them before Pro can read them."
                   % len(placeholders),
            observed=(["%d placeholder item(s) in %d inspected "
                       "entries:" % (len(placeholders), inspected)]
                      + list(placeholders[:15])),
            expected="No dehydrated item anywhere Pro reads a project or a "
                     "geodatabase.",
            impact="Pro freezes while each file downloads, and OneDrive can "
                   "lock a file while Pro is writing to it.",
            fix="Move the projects out of OneDrive (see DL-05 and DL-06). "
                "Until then, right-click the project folder and choose "
                "\"Always keep on this device\".")
    return dict(
        cid="DL-07", category="Storage", dataloss=True, status="Pass",
        title=title,
        result="No online-only files in the project folders.",
        observed="%d entries inspected under %s; no dehydrated item found."
                 % (inspected, "; ".join(roots)),
        expected="No dehydrated items.")


def decide_offline_files(policy_value):
    """DL-08. The Offline Files (CSC) client-side cache.

    Deliberately not the CSC service start value. That minifilter is
    boot-started on stock Windows whether or not the cache is enabled, so
    reading it as a stand-in reported the cache ENABLED and raised a warning on
    a machine whose cache was off - and it only ever ran on the one path where
    nobody could cross-check it, on a check that bears on data loss. Only the
    policy value states intent, so an unset policy is reported as unknown.
    """
    title = "Windows Offline Files"
    if policy_value is None:
        return dict(
            cid="DL-08", category="Network", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check: no Offline Files policy is set, so "
                   "whether it is on is unknown.",
            observed="No Offline Files policy value is set, so the cache state "
                     "could not be determined. The CSC service start value is "
                     "not used as a stand-in: it is boot-started on stock "
                     "Windows regardless of the cache, and inferring from it "
                     "produced a false warning.",
            expected="Offline Files disabled where geodatabases are accessed.",
            fix="Open Control Panel > Sync Center > Manage offline files and "
                "see whether Offline Files is enabled.")
    enabled = bool(policy_value)
    if not enabled:
        return dict(
            cid="DL-08", category="Network", dataloss=True, status="Pass",
            title=title,
            result="Offline Files is turned off.",
            observed="The Offline Files cache is disabled by policy (value "
                     "%s)." % policy_value,
            expected="Disabled.")
    return dict(
        cid="DL-08", category="Network", dataloss=True, status="Warn",
        title=title,
        result="Offline Files is turned on. Windows may keep its own copies "
               "of files from network drives.",
        observed="The Offline Files cache is ENABLED by policy (value %s)."
                 % policy_value,
        expected="Disabled. A cached copy of a geodatabase component file can "
                 "be served to Pro while the share holds a newer one.",
        impact="Pro can be handed an old copy of a geodatabase file, or have "
               "its writes held back. Both look exactly like lost edits.",
        fix="Ask IT to turn off Offline Files for the GIS network shares "
            "(Control Panel > Sync Center > Manage offline files). It is a "
            "machine-wide setting that other work may rely on, so prostall "
            "does not change it.")


def decide_crash(dumps, recent, wer_lines, wer_configured):
    """DL-09. Crash artefacts, and the honest reading of finding none.

    No dump is not health. Ending a hang with Task Manager writes no minidump
    at all, and per-process dump collection is off by default, so absence of a
    dump is absence of evidence. Reporting Pass here would be the same green
    light on a value that was never going to exist that this tool exists to
    stop, so the empty branch reports what collection is configured instead.
    """
    title = "ArcGIS Pro crash files"
    if dumps:
        return dict(
            cid="DL-09", category="ArcGISPro", dataloss=True,
            status="Warn" if recent else "Info",
            title=title,
            result="Found %d crash file(s) from ArcGIS Pro, %d from the last "
                   "%d days." % (len(dumps), recent, CRASH_RECENT_DAYS),
            observed=dumps,
            expected="No recent ArcGIS Pro dump. A dump timestamped inside an "
                     "editing window is the moment the session died.",
            impact="Each crash file marks the moment Pro crashed. Compare the "
                   "times with when the editor lost work.",
            fix="Keep these files for the Esri support case; their names "
                "identify the exact Pro build. Pro never deletes them, so "
                "remove them only after the case is closed.")
    return dict(
        cid="DL-09", category="ArcGISPro", dataloss=True, status="Info",
        title=title,
        result="No crash files found. This does not rule out a crash: "
               "closing a frozen Pro from Task Manager leaves no crash file.",
        observed=wer_lines,
        expected="Dump collection configured for ArcGISPro.exe before the next "
                 "hang, so there is an artefact to timestamp.",
        impact="Without a crash file there is no record of when Pro died.",
        fix=("Crash file collection is already set up; keep it."
             if wer_configured else
             "No dump collection is configured, so the next crash will leave "
             "no record. Ask IT to turn on Windows Error Reporting crash dumps "
             "(the LocalDumps registry key) for ArcGISPro.exe. It is a "
             "machine-wide setting, so prostall does not change it."))


def decide_tcp(cid, category, title, host, port, probe, warn_ms, impact, fix,
               dataloss=False, flag="--sde-server"):
    """DL-10 and PF-19. One bounded TCP connect, reported honestly.

    probe: None when no host was given, else (open, elapsed_ms, error). open
    is None when the host name did not resolve.

    Connect only. No credential is read, sent or stored, and nothing is written
    to the socket after the handshake.
    """
    if not host:
        return dict(
            cid=cid, category=category, dataloss=dataloss, status="Skipped",
            measured=False, title=title,
            result="Not checked: no server name was given.",
            observed="No host was given, so nothing was measured this run. "
                     "Network probes are opt-in.",
            expected="A reachable host.",
            fix="To test it, run prostall again with %s <server name>."
                % flag)
    opened, elapsed, error = probe
    if opened is None:
        return dict(
            cid=cid, category=category, dataloss=dataloss, status="Info",
            measured=False, title=title,
            result="Could not check: the name %s was not found on the network."
                   % host,
            observed="%s:%d did not resolve (%s), so the server was not "
                     "measured this run." % (host, port, error),
            expected="A host name that resolves.",
            fix="Check the spelling of the server name and that the VPN is "
                "connected, then run prostall again.")
    observed = "%s:%d open=%s elapsed=%d ms%s" % (
        host, port, opened, elapsed, (" (%s)" % error) if error else "")
    if not opened:
        status = "Fail"
        result = ("%s did not answer on port %d within %d ms."
                  % (host, port, elapsed))
    elif elapsed > warn_ms:
        status = "Warn"
        result = ("%s answered, but slowly: %d ms (should be under %d ms)."
                  % (host, elapsed, warn_ms))
    else:
        status = "Pass"
        result = "%s answered in %d ms." % (host, elapsed)
        impact = fix = ""
    return dict(
        cid=cid, category=category, dataloss=dataloss, status=status,
        title=title, result=result, observed=observed,
        expected="A TCP connect well inside %d ms. Connect only: no "
                 "credential is used." % warn_ms,
        impact=impact, fix=fix)


def decide_sde_files(paths, index_copies):
    """DL-11. Connection files on this box. Paths only, contents never opened.

    A .sde file can embed a saved credential, so this tool records where they
    are and never opens, reads, prints or copies one.

    The extension is re-checked after the scan. A Windows filename filter also
    matches the short 8.3 alias, so a glob for the .sde extension returns
    .sdeold and .sdebak strays too, which inflated both this inventory and the
    index count that gates on it.
    """
    title = "Database connection files (.sde)"
    if not paths:
        return dict(
            cid="DL-11", category="Geodatabase", dataloss=True, status="Info",
            title=title,
            result="No database connection files were found.",
            observed="No connection file under the ESRI favorites or index "
                     "folders.",
            expected="A small, curated set of connection files.")
    lines = []
    risky = []
    for path, cls in paths:
        lines.append("%s%s" % (path, "" if cls == "LOCAL" else " [%s]" % cls))
        if cls != "LOCAL":
            risky.append(path)
    status = "Info"
    if risky or index_copies >= INDEX_MANY_CONNECTIONS:
        status = "Warn"
    if status == "Warn":
        reasons = []
        if index_copies >= INDEX_MANY_CONNECTIONS:
            reasons.append("%d old copies are kept in Pro's search index"
                           % index_copies)
        if risky:
            reasons.append("%d are stored off this computer" % len(risky))
        result = ("Found %d connection files, more than needed: %s."
                  % (len(paths), " and ".join(reasons)))
    else:
        result = "Found %d connection file(s)." % len(paths)
    return dict(
        cid="DL-11", category="Geodatabase", dataloss=True, status=status,
        title=title, result=result,
        observed=(["%d connection file(s); %d cached under the search index:"
                   % (len(paths), index_copies)] + lines[:40]),
        expected="One connection file per environment, stored locally, pointed "
                 "at the version the editor is supposed to edit. Stale copies "
                 "cached under the search index bloat the index and re-open "
                 "dead connections at startup.",
        impact="Pro checks every connection when it starts, which slows "
               "opening. With several to choose from, it is easy to edit "
               "through the wrong version, which looks like lost edits.",
        fix="Delete the connection files nobody uses, and keep one per "
            "database. To clear the cached copies, close Pro and empty the "
            "ESRI ResourceIndex folder. Never open or copy a connection "
            "file to inspect it: they can contain saved passwords."
            if status == "Warn" else "")


def decide_pro_running(processes):
    """DL-12. Pro must be closed before any cache fix, and before a save is safe.

    processes: the matching process lines, or None when the process list could
    not be read. None is not an empty list: an unreadable list reported as a
    closed Pro opened the project file and let the cache fix run.
    """
    title = "ArcGIS Pro open during the check"
    if processes is None:
        return dict(
            cid="DL-12", category="ArcGISPro", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check whether ArcGIS Pro is open, so the "
                   "project-file check and the cache fix were not run.",
            observed="The process list could not be read, so whether Pro is "
                     "running was not measured this run.",
            expected="A readable process list.",
            fix="Close ArcGIS Pro yourself, then run prostall again.")
    if processes:
        return dict(
            cid="DL-12", category="ArcGISPro", dataloss=True, status="Warn",
            title=title,
            result="ArcGIS Pro is open, so some checks and the cache fix "
                   "could not run safely.",
            observed=processes,
            expected="Pro closed. Cache and index state cannot be cleared "
                     "safely while Pro holds it, and an open edit session must "
                     "be saved and closed first.",
            impact="While Pro is open, prostall will not read the project "
                   "file or touch the cache, and --apply refuses the cache fix.",
            fix="Ask the editor to save their edits, stop editing and close "
                "ArcGIS Pro. Then run prostall again.")
    return dict(
        cid="DL-12", category="ArcGISPro", dataloss=True, status="Pass",
        title=title,
        result="ArcGIS Pro is closed.",
        observed="No ArcGIS Pro process is running.",
        expected="Pro closed.")


def decide_protemp(folders, temp_root):
    """PF-07. Leftover session folders, read as a recovery lead.

    folders: (name, when, bytes, project_files, non_empty_tables) newest first.

    This keeps its performance id and runs in the data-loss block. The earlier
    version reported a count and a size and called it disk churn, which routed
    a possible recovery lead for the lost work into the performance section and
    out of the act-first list. What matters is which of these folders still
    holds content: an empty scratch geodatabase is a husk, a project file or a
    non-empty scratch table is unsaved work worth opening before deleting.
    """
    title = "Leftover ArcGIS Pro session folders"
    if not folders:
        return dict(
            cid="PF-07", category="Cache", dataloss=True, status="Info",
            title=title,
            result="No leftover session folders, so there is no unsaved work "
                   "to recover from them.",
            observed="No leftover session folder under %s, so there is no "
                     "unsaved scratch work to recover from one." % temp_root,
            expected="None, or a few that can be inspected.")
    lines = []
    with_work = []
    total = 0
    for name, when, size, projects, tables in folders:
        total += size
        works = (projects > 0 or tables > 0)
        if works:
            with_work.append(name)
        lines.append("%s  %-28s %10s  project files=%d non-empty scratch "
                     "tables=%d%s"
                     % (when, name, format_bytes(size), projects, tables,
                        "   <-- MAY HOLD UNSAVED WORK" if works else ""))
    observed = ["%d folder(s), %s total, under %s"
                % (len(folders), format_bytes(total), temp_root)]
    observed.extend(lines[:30])
    if len(folders) > 30:
        observed.append("... %d more folder(s) not listed."
                        % (len(folders) - 30))
    observed.append("%d of %d folder(s) still hold a project file or a "
                    "non-empty scratch table."
                    % (len(with_work), len(folders)))
    status = "Info"
    if with_work or len(folders) >= PROTEMP_MANY:
        status = "Warn"
    if with_work:
        result = ("%d of %d leftover session folders may hold unsaved work "
                  "from a session that ended unexpectedly."
                  % (len(with_work), len(folders)))
        fix = ("Before deleting anything, open the folders marked MAY HOLD "
               "UNSAVED WORK and check them against the date the edits were "
               "lost. After that, with Pro closed, delete session folders "
               "older than a few days. prostall never deletes these, because "
               "they may hold the last copy of the work.")
    else:
        result = ("%d leftover session folders, none holding work."
                  % len(folders))
        fix = ("With Pro closed, delete the ArcGISProTemp folders older than "
               "a few days." if status == "Warn" else "")
    return dict(
        cid="PF-07", category="Cache", dataloss=True, status=status,
        title=title, result=result,
        observed=observed,
        expected="One per live session. Pro does not clean these up, so the "
                 "count is a rough session history and not a crash count - but "
                 "a folder from a session that died can still hold the scratch "
                 "project that session never saved.",
        impact="A folder from a session that crashed can still hold the "
               "project that was never saved. It may be the only copy of the "
               "lost work.",
        fix=fix,
        detail=("Inspect first: " + "; ".join(with_work)) if with_work
               else "No folder holds a project file or a non-empty scratch "
                    "table.")


# ------------------------------------------------------- performance section

def decide_pro_version(version, real, build, install_dir, exe_version):
    """PF-04. The installed release. Always Info: a version is a fact.

    A verdict would need a support matrix that goes stale, so this records the
    build for the case instead of grading it.
    """
    title = "ArcGIS Pro version"
    if not version and not exe_version:
        return dict(
            cid="PF-04", category="ArcGISPro", status="Info", measured=False,
            title=title,
            result="Could not check: ArcGIS Pro was not found in the registry "
                   "or on disk.",
            observed="The ArcGIS Pro registry key and the executable were both "
                     "absent, so the installed version could not be read.",
            expected="A resolvable installed version.",
            fix="Confirm ArcGIS Pro is installed for all users, then run "
                "prostall again.")
    return dict(
        cid="PF-04", category="ArcGISPro", status="Info",
        title=title,
        result="ArcGIS Pro %s is installed%s." % (
            real or version or exe_version,
            (", build %s" % build) if build else ""),
        observed=["Version = %s" % (version or "<absent>"),
                  "RealVersion = %s" % (real or "<absent>"),
                  "BuildNumber = %s" % (build or "<absent>"),
                  "InstallDir = %s" % (install_dir or "<absent>"),
                  "ArcGISPro.exe file version = %s" % (exe_version or "<absent>")],
        expected="The current patch for the installed release.",
        impact="Some display problems are specific to a Pro release, so "
               "support will ask for this.",
        fix="Note this version on any support case, and check whether Esri "
            "has released a patch for it before changing settings.")


def decide_rendering(config_path, settings):
    """PF-05. The display settings, read only.

    None of these cause edit loss. They are the draw-performance levers, and
    they are here so the report can be diffed against a workstation that is
    known to be fast.
    """
    title = "Pro display settings"
    if not config_path:
        return dict(
            cid="PF-05", category="Display", status="Info", measured=False,
            title=title,
            result="Could not check: Pro's settings file (user.config) was "
                   "not found for this account.",
            observed="No user.config was located, so the display settings "
                     "could not be read.",
            expected="A readable user.config.",
            fix="In Pro, open Project > Options > Display to see them.")
    names = ("RenderingEngine", "RenderingQuality", "EnableRenderingAntialiasing",
             "AntialiasingMode", "TextAntialiasingMode", "EnableVSync",
             "StereoscopicMode", "ManageFeatureCaching",
             "ClearCacheWhenAppClosing")
    stereo = cfg(settings, "StereoscopicMode")
    status = "Info"
    if stereo and stereo.upper() != "OFF":
        status = "Warn"
    return dict(
        cid="PF-05", category="Display", status=status,
        title=title,
        result=("Stereoscopic (3D) display mode is on (%s), which slows "
                "drawing." % stereo if status == "Warn" else
                "Display settings were read; nothing unusual found."),
        observed=[cfg_line(settings, n) for n in names],
        expected="A rendering engine matching a stable driver path for this "
                 "graphics adapter, stereoscopic mode off, feature caching on. "
                 "None of these cause edit loss.",
        impact="Makes maps draw more slowly. It does not affect saved data.",
        fix=("In Pro, open Project > Options > Display and turn off "
             "stereoscopic mode. Change display settings only there: Pro "
             "overwrites any hand edit to user.config."
             if status == "Warn" else ""),
        detail="Source: %s" % config_path)


def decide_caches(path, exists, total_bytes, files, oldest, oldest_age_days,
                  stale_files):
    """PF-06. The local display and geoprocessing cache.

    Not a data-loss cause. It is the one folder that grows unattended and that
    Pro does not reliably purge, so it is the cheapest thing to fix first.
    """
    title = "Pro local cache"
    if not exists:
        return dict(
            cid="PF-06", category="Cache", status="Info",
            title=title,
            result="There is no local cache folder yet.",
            observed="%s  <absent>" % path,
            expected="Present, modest and self-clearing.")
    big, stale, ratio = cache_triggers(total_bytes, files, oldest_age_days,
                                       stale_files)
    if big or stale:
        why = []
        if big:
            why.append("is large (%s in %d files)"
                       % (format_bytes(total_bytes), files))
        if stale:
            why.append("is mostly old files Pro no longer uses (%.0f%% older "
                       "than %d days)" % (ratio * 100.0, CACHE_STALE_DAYS))
        result = "The local cache %s." % " and ".join(why)
    else:
        result = ("The local cache is a normal size (%s)."
                  % format_bytes(total_bytes))
    return dict(
        cid="PF-06", category="Cache", status="Warn" if (big or stale) else "Pass",
        title=title, result=result,
        observed=[path,
                  "%s in %d files; oldest %s; %d cache file(s) older than %d days"
                  % (format_bytes(total_bytes), files, oldest, stale_files,
                     CACHE_STALE_DAYS),
                  "stale fraction %.0f%%; size trigger %s; staleness trigger %s"
                  % (ratio * 100.0, big, stale)],
        expected="A display and geoprocessing cache Pro is actually reusing, "
                 "not months of orphaned cache files. Pro does not reliably "
                 "purge these.",
        impact="A bloated cache slows Pro's start-up and map drawing over "
               "time. It does not affect saved data.",
        fix="With Pro closed, run prostall with --apply to move the cache "
            "folder aside; Pro builds a fresh one on the next start. prostall "
            "renames the folder and never deletes it, and prints the command "
            "to undo. To stop it growing, tick \"Clear cache when the "
            "application closes\" in Project > Options > Display.",
        autofixable=True)


def decide_index(res_bytes, res_files, connection_copies, status_stamp,
                 settings):
    """PF-08. The catalog search index.

    Indexing a network connection walks the share at startup and again on every
    incremental pass, which is one of the two ways a five-minute open happens
    on a machine that is otherwise healthy.
    """
    lines = ["ResourceIndex: %s in %d files" % (format_bytes(res_bytes),
                                                res_files),
             "Cached connection files indexed: %d" % connection_copies,
             "Index status last written: %s" % status_stamp]
    lines.extend([cfg_line(settings, n) for n in
                  ("IndexLocalContent", "IndexNetworkConnections",
                   "SkipServerConnections", "RunEvenLoggedOut")])
    network = cfg(settings, "IndexNetworkConnections")
    status = "Info"
    if connection_copies >= INDEX_MANY_CONNECTIONS or network == "True":
        status = "Warn"
    why = []
    if network == "True":
        why.append("indexes network folders")
    if connection_copies >= INDEX_MANY_CONNECTIONS:
        why.append("holds %d cached connection files" % connection_copies)
    return dict(
        cid="PF-08", category="ArcGISPro", status=status,
        title="Pro search index",
        result=("Pro's search index %s, which slows start-up."
                % " and ".join(why) if why else
                "The search index settings look normal."),
        observed=lines,
        expected="Network-connection indexing off on a machine whose data is "
                 "on a share, and a handful of connection copies, not dozens.",
        impact="Indexing network folders means Pro scans the share at every "
               "start-up and again in the background.",
        fix=("In Pro, open Project > Options > General > Search Index and "
             "turn off indexing of network connections. To rebuild the index "
             "from scratch, close Pro and empty the ESRI ResourceIndex folder."
             if status == "Warn" else ""))


def decide_gpu_preference(exe, value, readable=True):
    """PF-11. Which adapter Windows hands ArcGIS Pro.

    With no value Windows decides, and on a hybrid laptop that regularly means
    the integrated adapter. Setting it is one registry string, it is per-user,
    and it is exactly what the Graphics page of Settings writes.
    """
    title = "Graphics card used by Pro"
    if not readable:
        return dict(
            cid="PF-11", category="Hardware", status="Info", measured=False,
            title=title,
            result="Could not check: the Windows graphics setting could not "
                   "be read.",
            observed="The registry could not be read on this platform, so the "
                     "graphics preference is unknown. An unreadable value is "
                     "not an absent one.",
            expected="%s, high performance." % GPU_PREFERENCE_WANTED,
            fix="Look under Settings > System > Display > Graphics.")
    if not value:
        return dict(
            cid="PF-11", category="Hardware", status="Warn",
            title=title,
            result="ArcGIS Pro is not set to use the high-performance "
                   "graphics card, so Windows may give it the slower "
                   "built-in one.",
            observed="No value named %s under %s, so Windows picks the adapter "
                     "automatically, which on a hybrid machine can mean the "
                     "integrated one." % (exe, GPU_PREFERENCE_KEY),
            expected="%s, high performance." % GPU_PREFERENCE_WANTED,
            impact="Maps draw slowly and may not redraw properly. It does not "
                   "affect saved data.",
            fix="Run prostall with --apply to set it (it prints the command to "
                "undo), or set it yourself: Settings > System > Display > "
                "Graphics > ArcGIS Pro > High performance.",
            autofixable=True)
    good = GPU_PREFERENCE_WANTED.rstrip(";") in value
    return dict(
        cid="PF-11", category="Hardware", status="Pass" if good else "Warn",
        title=title,
        result=("ArcGIS Pro is set to use the high-performance graphics card."
                if good else
                "ArcGIS Pro is set to a graphics option other than high "
                "performance (%s)." % value),
        observed="%s = %s" % (exe, value),
        expected="%s. 0 lets Windows decide, 1 is power saving and the "
                 "integrated adapter, 2 is high performance."
                 % GPU_PREFERENCE_WANTED,
        impact="" if good else "Maps draw more slowly.",
        fix="Run prostall with --apply to set it, or set it yourself in "
            "Settings > System > Display > Graphics. If the machine has only "
            "one graphics card, also check the card maker's control panel.",
        autofixable=True)


def decide_memory(total_gb, free_gb):
    """PF-12. Installed and free memory.

    Without the unread guard a failed read computed zero and rendered as
    "Fail, total 0 GB" - a hardware verdict off a value nobody read, telling
    the operator to replace memory that is probably fine.
    """
    title = "Memory (RAM)"
    if total_gb is None:
        return dict(
            cid="PF-12", category="Hardware", status="Info", measured=False,
            title=title,
            result="Could not check: the amount of memory could not be read.",
            observed="Installed and free memory could not be read on this "
                     "platform, so no verdict is given. This is a failed read, "
                     "not a hardware finding.",
            expected="At least %.0f GB for enterprise editing." % MIN_MEMORY_GB,
            fix="Check the installed memory in Task Manager > Performance > "
                "Memory.")
    if total_gb < MIN_MEMORY_GB:
        status = "Fail"
        result = ("This computer has %.0f GB of memory; at least %.0f GB is "
                  "recommended for editing." % (total_gb, MIN_MEMORY_GB))
        fix = ("Ask IT to upgrade the memory to at least %.0f GB (%.0f GB is "
               "comfortable)." % (MIN_MEMORY_GB, MIN_MEMORY_GB * 2))
    elif free_gb < LOW_FREE_MEMORY_GB:
        status = "Warn"
        result = ("Only %.1f GB of %.0f GB memory was free during the check."
                  % (free_gb, total_gb))
        fix = ("Close other large programs (browsers, other GIS sessions) "
               "while editing, and check Task Manager for what is using the "
               "memory.")
    else:
        status = "Pass"
        result = ("%.0f GB of memory, %.1f GB free." % (total_gb, free_gb))
        fix = ""
    return dict(
        cid="PF-12", category="Hardware", status=status,
        title=title, result=result,
        observed="Total %.2f GB; free %.2f GB at the time of the check, which "
                 "is a live snapshot and not a specification."
                 % (total_gb, free_gb),
        expected="At least %.0f GB for enterprise editing; %.0f GB comfortable."
                 % (MIN_MEMORY_GB, MIN_MEMORY_GB * 2),
        impact="" if status == "Pass" else
               "When memory runs short Windows swaps to disk, which makes Pro "
               "slow to open and freeze while drawing.",
        fix=fix)


def decide_disk(volumes):
    """PF-13. Free space on the system volume and on any local project volume.

    volumes: (name, total_bytes, free_bytes).
    """
    title = "Free disk space"
    if not volumes:
        return dict(
            cid="PF-13", category="Hardware", status="Info", measured=False,
            title=title,
            result="Could not check: no drive could be read.",
            observed="No volume could be read, so free space was not measured "
                     "this run.",
            expected="At least %.0f GB and %.0f%% free."
                     % (MIN_FREE_DISK_GB, MIN_FREE_DISK_PCT),
            fix="Check free space in File Explorer > This PC.")
    lines = []
    low = []
    for name, total, free in volumes:
        free_gb = free / float(1024 ** 3)
        total_gb = total / float(1024 ** 3)
        pct = (100.0 * free / total) if total else 0.0
        lines.append("%s %.2f GB free of %.2f GB (%.1f%%)"
                     % (name, free_gb, total_gb, pct))
        if free_gb < MIN_FREE_DISK_GB or pct < MIN_FREE_DISK_PCT:
            low.append("%s has %.1f GB free" % (name.rstrip("\\"), free_gb))
    return dict(
        cid="PF-13", category="Hardware", status="Warn" if low else "Pass",
        title=title,
        result=("Low disk space: %s (at least %.0f GB is recommended)."
                % ("; ".join(low), MIN_FREE_DISK_GB) if low else
                "Enough free disk space on every drive checked."),
        observed=lines,
        expected="At least %.0f GB and %.0f%% free on the volume holding the "
                 "caches, the scratch workspace and any local project."
                 % (MIN_FREE_DISK_GB, MIN_FREE_DISK_PCT),
        impact="" if not low else
               "Pro needs free space for its cache and for geoprocessing. A "
               "nearly full drive makes it slow and can make it freeze.",
        fix="" if not low else
            "Free up space. Start with Pro's local cache (PF-06) and old "
            "session folders (PF-07), which grow on their own.")


def decide_power_plan(guid, name):
    """PF-15. The active power plan.

    Not a data-loss cause on its own, but a throttled processor lengthens the
    window in which a save can time out, and a timed-out save is how an edit
    ends up stranded.
    """
    title = "Power plan"
    if not guid:
        return dict(
            cid="PF-15", category="System", status="Info", measured=False,
            title=title,
            result="Could not check: the active power plan could not be read.",
            observed="The active power scheme could not be parsed, so no "
                     "verdict is given.",
            expected="High performance, or a balanced plan with no processor "
                     "cap while on mains power.",
            fix="Look in Control Panel > Power Options.")
    high = (guid.lower() == HIGH_PERFORMANCE_PLAN
            or bool(re.search(r"high performance|ultimate", name or "",
                              re.IGNORECASE)))
    return dict(
        cid="PF-15", category="System", status="Pass" if high else "Warn",
        title=title,
        result=("The power plan is \"%s\"." % name if high else
                "The power plan is \"%s\", which can slow the processor down "
                "to save power." % (name or "unnamed")),
        observed="%s  (%s)" % (name or "<unnamed>", guid),
        expected="High performance while docked, so the processor is not "
                 "parked during a long draw or a long save.",
        impact="" if high else
               "A slowed processor makes Pro slower to open and draw, and "
               "gives a save longer to time out.",
        fix="" if high else
            "Switch to the High performance power plan in Control Panel > "
            "Power Options. If that plan is hidden by your organisation, ask "
            "IT to raise the minimum processor state instead. It is a "
            "machine-wide setting, so prostall does not change it.")


def decide_roaming(roaming_configured, esri_bytes, esri_files):
    """PF-26. A roaming profile carrying the ESRI settings folder.

    Copied down at logon and up at logoff, which adds to both the logon time
    and the first-launch delay, and is one of the quieter reasons a machine is
    slow only in the morning.
    """
    title = "Roaming profile"
    if roaming_configured is None:
        return dict(
            cid="PF-26", category="System", status="Info", measured=False,
            title=title,
            result="Could not check whether this account uses a roaming "
                   "profile.",
            observed=["The roaming profile state could not be read.",
                      "Roaming ESRI folder = %s in %d files"
                      % (format_bytes(esri_bytes), esri_files)],
            expected="The roaming ESRI folder excluded from the roaming "
                     "profile, or a local profile.",
            fix="Ask IT whether this account uses a roaming profile.")
    return dict(
        cid="PF-26", category="System",
        status="Warn" if roaming_configured else "Pass",
        title=title,
        result=("This account uses a roaming profile, and it copies Pro's "
                "settings folder (%s) at every sign-in and sign-out."
                % format_bytes(esri_bytes) if roaming_configured else
                "This account does not use a roaming profile."),
        observed=["RoamingConfigured = %s" % roaming_configured,
                  "Roaming ESRI folder = %s in %d files"
                  % (format_bytes(esri_bytes), esri_files)],
        expected="The roaming ESRI folder excluded from the roaming profile, "
                 "or a local profile.",
        impact="" if not roaming_configured else
               "Sign-in and the first Pro start of the day are slower, "
               "because the folder is copied down from the server first.",
        fix="" if not roaming_configured else
            "Ask IT to exclude the ESRI folders (in AppData\\Roaming and "
            "AppData\\Local) from the roaming profile.")


def decide_symbology(feature_caching):
    """PF-27. Symbology that does not refresh after a field calculation.

    Always Info, and deliberately. No client setting was found that governs
    symbology invalidation after an attribute edit made outside the map, and
    none is invented here. Saying "probably the feature cache" in a verdict
    field would be exactly the guess this tool refuses to make.
    """
    return dict(
        cid="PF-27", category="Display", status="Info",
        title="Map not redrawing after a field calculation",
        result="No setting controls this, so there is nothing to check. "
               "It is a known Pro display issue.",
        observed="ManageFeatureCaching = %s. No client setting was found that "
                 "governs symbology invalidation after an attribute edit made "
                 "outside the map, and none is invented here."
                 % ("<absent>" if feature_caching is None else feature_caching),
        expected="A redraw after a field calculation. The renderer draws from "
                 "the in-memory feature cache, and closing and reopening the "
                 "map pane discards that cache, which is why that workaround "
                 "works.",
        impact="If the map does not update after a field calculation, the "
               "data is still saved: this does not mean the change was lost. "
               "Check the value in the attribute table before treating it as "
               "lost data.",
        fix="If it happens, refresh the map, or close and reopen the map "
            "pane. Report it to Esri with the Pro version (PF-04).")


# ------------------------------------------------ the causes added in 1.6

def as_list(value):
    """A PowerShell JSON answer as a list. One object arrives bare, not listed."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def decide_nic(adapters, error):
    """DL-04. May Windows turn the network adapter off to save power?

    adapters: (name, description, state) per physical adapter that is up,
    where state is Enabled, Disabled, Unsupported, or None when unreadable.

    An adapter whose setting nobody could read keeps the check undetermined,
    even beside an adapter that reads Disabled. Pass on a partial reading is
    a verdict about a device nobody measured.
    """
    title = "Network adapter power saving"
    by_hand = ("Open Device Manager, open each network adapter, and on the "
               "Power Management tab check \"Allow the computer to turn off "
               "this device to save power\".")
    if error is not None:
        return dict(
            cid="DL-04", category="Network", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check: the list of network adapters could not "
                   "be read.",
            observed="The adapter list could not be read: %s" % error,
            expected="Allow the computer to turn off this device: Disabled on "
                     "every adapter that carries the database session.",
            fix=by_hand)
    if not adapters:
        return dict(
            cid="DL-04", category="Network", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check: no connected network adapter was found.",
            observed="No physical adapter reported up, so the power setting "
                     "could not be determined.",
            expected="At least one physical adapter up.",
            fix=by_hand)
    lines = []
    enabled = []
    unknown = []
    for name, description, state in adapters:
        lines.append("%s [%s] AllowComputerToTurnOffDevice = %s"
                     % (name, description, state or "<unreadable>"))
        if state == "Enabled":
            enabled.append(name)
        elif state not in ("Disabled", "Unsupported"):
            unknown.append(name)
    if enabled:
        return dict(
            cid="DL-04", category="Network", dataloss=True, status="Warn",
            title=title,
            result="Windows is allowed to switch off the network adapter "
                   "(%s) to save power." % ", ".join(enabled),
            observed=lines,
            expected="Disabled on every adapter that carries the database "
                     "session.",
            impact="If Windows switches the adapter off during an edit "
                   "session, Pro loses its connection to the database and "
                   "unsaved edits can be lost.",
            fix="Ask IT (it needs administrator rights) to open Device "
                "Manager > %s > Power Management and clear \"Allow the "
                "computer to turn off this device to save power\"."
                % " and ".join(enabled),
            detail="Adapters to change: %s" % ", ".join(enabled))
    if unknown:
        return dict(
            cid="DL-04", category="Network", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check every adapter: the setting could not be "
                   "read for %s." % ", ".join(unknown),
            observed=lines + ["The setting could not be read for %d adapter(s), "
                              "so a Pass here would be a guess."
                              % len(unknown)],
            expected="A readable setting on every adapter that is up. The "
                     "provider often answers nothing for a docking or USB "
                     "adapter, or when the session is not elevated.",
            fix="Run prostall again as administrator, or check by hand: "
                + by_hand)
    return dict(
        cid="DL-04", category="Network", dataloss=True, status="Pass",
        title=title,
        result="Windows will not switch off any network adapter to save power.",
        observed=lines,
        expected="Disabled or unsupported on every adapter that is up.")


# The timeline row text that marks an SMB client event. decide_events tells
# these rows from a Pro hang or a dirty shutdown by this prefix.
SMB_EVENT_PREFIX = "SMB client connectivity event"


def classify_event(log, event):
    """What one event means for the timeline, or None when it is not ours.

    Matched on the provider as well as the id. Event 1002 is also written by
    Winlogon, and a timeline that counted it would report a Pro hang on a
    machine where Pro never hung.
    """
    provider = event["provider"]
    eid = event["id"]
    if log == "Application":
        if not [d for d in event["data"]
                if ntpath.basename(d or "").lower() == "arcgispro.exe"]:
            return None
        if provider == "Application Error" and eid == 1000:
            return "ArcGIS Pro crashed (Application Error 1000)"
        if provider == "Application Hang" and eid == 1002:
            return "ArcGIS Pro hung and was ended (Application Hang 1002)"
        return None
    if log == "System":
        if provider == "Microsoft-Windows-Kernel-Power" and eid == 41:
            return "Windows restarted without a clean shutdown (Kernel-Power 41)"
        if provider == "EventLog" and eid == 6008:
            return "the previous shutdown was unexpected (EventLog 6008)"
        return None
    if event["level"] in (1, 2, 3):
        return ("%s %d, level %d, uninterpreted"
                % (SMB_EVENT_PREFIX, eid, event["level"]))
    return None


def parse_events(text):
    """The events in one wevtutil XML answer, or None when it is not XML.

    Unparsable output is an unread log, not an empty one: an empty timeline
    off a reply nobody could read is the same lie as a verdict with no
    measurement.
    """
    from xml.etree import ElementTree

    try:
        root = ElementTree.fromstring(text or "")
    except ElementTree.ParseError:
        return None
    out = []
    for node in root.iter(EVENT_NS + "Event"):
        system = node.find(EVENT_NS + "System")
        if system is None:
            continue
        provider = system.find(EVENT_NS + "Provider")
        created = system.find(EVENT_NS + "TimeCreated")
        try:
            eid = int(system.findtext(EVENT_NS + "EventID", "").strip())
            level = int(system.findtext(EVENT_NS + "Level", "0").strip() or 0)
        except ValueError:
            continue
        when = (created.get("SystemTime", "") if created is not None else "")
        out.append({
            "provider": provider.get("Name", "") if provider is not None else "",
            "id": eid,
            "level": level,
            "when": when[:19].replace("T", " "),
            "data": [d.text or "" for d in node.iter(EVENT_NS + "Data")],
        })
    return out


def decide_events(rows, unreadable, queried, capped):
    """DL-13. The wall-clock record of hangs, crashes and dirty shutdowns.

    rows: (when, what). unreadable: (log, reason). capped: logs that hit the
    event cap. No event is never Pass: a hang the editor waits out leaves no
    event at all, so an empty window is absence of evidence.
    """
    title = "Crashes and freezes in the Windows event log"
    lines = ["Window: the last %d days, newest first, times in UTC."
             % EVENT_WINDOW_DAYS]
    for log, reason in unreadable:
        lines.append("%s: unreadable (%s)" % (log, reason))
    for log in capped:
        lines.append("%s: capped at %d events, so older ones are not shown"
                     % (log, EVENT_MAX))
    serious = [r for r in rows if not r[1].startswith(SMB_EVENT_PREFIX)]
    if rows:
        ordered = sorted(rows, reverse=True)
        lines.extend(["%s  %s" % pair for pair in ordered[:40]])
        if len(ordered) > 40:
            lines.append("... %d more event(s) not listed."
                         % (len(ordered) - 40))
    quiet_note = ("No Pro crash or hang and no dirty shutdown in the logs "
                  "that were read. That is not a clean bill of health: a hang "
                  "the editor waits out leaves no event, and the incident may "
                  "be older than the window.")
    if serious:
        return dict(
            cid="DL-13", category="System", dataloss=True, status="Warn",
            title=title,
            result="The event log records %d Pro crash, freeze or unexpected "
                   "shutdown event(s) in the last %d days."
                   % (len(serious), EVENT_WINDOW_DAYS),
            observed=lines,
            expected="No ArcGIS Pro crash or hang, and no unexpected shutdown, "
                     "during editing hours.",
            impact="These times show when a Pro session ended without "
                   "saving. A freeze closed from Task Manager leaves no crash "
                   "file (DL-09), but it does leave one of these events.",
            fix="Compare the times listed under DL-13 with when the editor "
                "lost work, and include them in any support case.",
            detail="Sources: Application (Application Error 1000, Application "
                   "Hang 1002, naming ArcGISPro.exe); System (Kernel-Power "
                   "41, EventLog 6008); SMB client connectivity warnings and "
                   "errors, ids printed uninterpreted.")
    if rows:
        # SMB events alone say nothing about a Pro hang or a crash. A laptop
        # off the VPN raises them, and a Warn titled "Pro hangs" on that
        # machine is a verdict the logs did not produce.
        return dict(
            cid="DL-13", category="System", dataloss=True, status="Info",
            title=title,
            result="No Pro crash or freeze recorded, only network file-share "
                   "(SMB) connection events.",
            observed=lines + [quiet_note],
            expected="No ArcGIS Pro crash or hang, and no unexpected "
                     "shutdown.",
            impact="Network file-share connection problems can come before a "
                   "freeze on a project stored on a share.",
            fix="Compare these times with when the editor lost work. If a "
                "project is on a network share, see PF-02 and PF-03 too.")
    if len(unreadable) == queried:
        return dict(
            cid="DL-13", category="System", dataloss=True, status="Info",
            measured=False, title=title,
            result="Could not check: the Windows event logs could not be read.",
            observed=lines + ["No log could be read, so the timeline was not "
                              "measured this run."],
            expected="Readable Application and System logs.",
            fix="Open Event Viewer and look in the Application and System "
                "logs for ArcGIS Pro errors and unexpected shutdowns.")
    lines.append(quiet_note)
    return dict(
        cid="DL-13", category="System", dataloss=True, status="Info",
        title=title,
        result="Nothing recorded in the last %d days. This does not rule out "
               "a freeze: one that the editor waited out leaves no event."
               % EVENT_WINDOW_DAYS,
        observed=lines,
        expected="No ArcGIS Pro crash or hang, and no unexpected shutdown.",
        fix="If the lost edits are older than %d days, look for the same "
            "events in an archived log." % EVENT_WINDOW_DAYS)


def decide_smb(config, error, share_projects):
    """PF-02. The SMB client settings, read for the share the projects use.

    Multichannel is on by default. Off, it is a departure worth a line only
    when a recent project actually lives on a share; otherwise the setting
    is recorded for the diff and decides nothing.
    """
    title = "Network file-share (SMB) settings"
    if config is None:
        return dict(
            cid="PF-02", category="Network", status="Info", measured=False,
            title=title,
            result="Could not check: the file-share settings could not be "
                   "read.",
            observed="The SMB client configuration could not be read: %s"
                     % error,
            expected="A readable SMB client configuration.",
            fix="Run Get-SmbClientConfiguration in PowerShell to see them.")
    names = ("EnableMultiChannel", "RequireSecuritySignature",
             "DirectoryCacheLifetime", "FileInfoCacheLifetime",
             "SessionTimeout")
    lines = ["%s = %s" % (n, config.get(n, "<absent>")) for n in names]
    lines.append("recent projects on a share: %d" % share_projects)
    status = "Info"
    if config.get("EnableMultiChannel") is False and share_projects:
        status = "Warn"
    return dict(
        cid="PF-02", category="Network", status=status,
        title=title,
        result=("SMB Multichannel, a Windows feature that speeds up network "
                "shares, is turned off, and %d recent project(s) are on a "
                "share." % share_projects if status == "Warn" else
                "File-share settings were read; nothing unusual found."),
        observed=lines,
        expected="Multichannel on, which is the Windows default, wherever a "
                 "project is opened from a share.",
        impact="Projects on a network share open and save more slowly.",
        fix=("Ask the desktop or network team to turn SMB Multichannel back "
             "on (it is the Windows default). It is a machine-wide setting, "
             "so prostall does not change it." if status == "Warn" else ""))


def decide_share_latency(rows, omitted=0):
    """PF-03. One directory listing on the share each remote project uses.

    rows: (folder, elapsed_ms or None, entries, error). omitted: how many
    share folders the cap left untimed. A folder that could not be listed
    keeps a fast reading from becoming a Pass, the same rule DL-04 follows:
    the unreadable folder is the likely culprit.
    """
    title = "Network share speed"
    if not rows:
        return dict(
            cid="PF-03", category="Network", status="Info",
            title=title,
            result="No recent project is on a network share, so there was "
                   "nothing to time.",
            observed="No recent project lives on a share, so there was no "
                     "share folder to time.",
            expected="Projects on a local disk.")
    lines = []
    timed = []
    for folder, elapsed, entries, error in rows:
        if elapsed is None:
            lines.append("unreadable  %s  (%s)" % (folder, error))
            continue
        timed.append(elapsed)
        lines.append("%6d ms  %d entries  %s" % (elapsed, entries, folder))
    if omitted:
        lines.append("%d more share folder(s) holding a recent project were "
                     "not timed." % omitted)
    if not timed:
        return dict(
            cid="PF-03", category="Network", status="Info", measured=False,
            title=title,
            result="Could not check: no project folder on the share could be "
                   "opened.",
            observed=lines + ["No share folder could be listed, so the latency "
                              "was not measured this run."],
            expected="A listable project folder.",
            fix="Check that the network share opens in File Explorer from "
                "this account.")
    worst = max(timed)
    if worst >= SHARE_LIST_FAIL_MS:
        status = "Fail"
    elif worst >= SHARE_LIST_WARN_MS:
        status = "Warn"
    elif len(timed) < len(rows):
        return dict(
            cid="PF-03", category="Network", status="Info", measured=False,
            title=title,
            result="Could not check every share: at least one project folder "
                   "could not be opened.",
            observed=lines + ["At least one share folder could not be listed, "
                              "so a Pass here would be a guess."],
            expected="A listable project folder.",
            fix="Check that every project folder on the share opens in File "
                "Explorer from this account.")
    else:
        status = "Pass"
    if status == "Pass":
        result = ("Listing a project folder on the share took %d ms, which is "
                  "fine." % worst)
    else:
        result = ("Listing a project folder on the share took %d ms (should be "
                  "under %d ms). Pro does this many times while opening a "
                  "project." % (worst, SHARE_LIST_WARN_MS))
    return dict(
        cid="PF-03", category="Network", status=status,
        title=title, result=result,
        observed=lines,
        expected="One listing of at most %d entries well under %d ms. Pro "
                 "lists folders many times while it opens a project."
                 % (SHARE_LIST_LIMIT, SHARE_LIST_WARN_MS),
        impact="" if status == "Pass" else
               "Every folder Pro opens on the share costs this long, so "
               "projects open slowly.",
        fix="" if status == "Pass" else
            "Copy the project to a folder on this computer for editing (see "
            "DL-05). If it has to stay on the share, give this time to the "
            "network team.")


def _pattern_covers(pattern, target, same_depth=False):
    """Does a Defender exclusion pattern cover this path?

    Segment by segment, because Defender's asterisk in a folder path matches
    exactly one folder, and once the named folders match, everything below is
    covered. A process exclusion names one file, so it must match at the same
    depth.
    """
    def parts(path):
        return [p for p in path.replace("/", "\\").lower().rstrip("\\")
                .split("\\")]
    want = parts(pattern)
    have = parts(target)
    if len(want) > len(have) or (same_depth and len(want) != len(have)):
        return False
    for w, h in zip(want, have):
        rx = "^%s$" % re.escape(w).replace("\\*", ".*").replace("\\?", ".")
        if not re.match(rx, h):
            return False
    return True


def defender_expand(entry, system_env):
    """Expand %NAME% in an exclusion the way Defender does, as LocalSystem.

    An unknown variable stays as written, so it matches nothing rather than
    matching the editor's folder by accident.
    """
    def replace(match):
        return system_env.get(match.group(1).upper(), match.group(0))
    return re.sub(r"%([A-Za-z_()0-9]+)%", replace, entry or "")


def decide_defender(prefs, error, base_paths, pro_exe, system_env):
    """PF-17. Is Defender scanning the folders Pro reads and writes most?

    prefs: the Get-MpPreference fields, or None. The sentinel text an
    unprivileged read gets in place of each list is recognised as unreadable,
    never compared as a path: compared, it reported every baseline folder
    missing on a machine that may already exclude them all.
    """
    title = "Antivirus exclusions (Microsoft Defender)"
    if prefs is None:
        return dict(
            cid="PF-17", category="Security", status="Info", measured=False,
            title=title,
            result="Could not check: the Defender settings could not be read. "
                   "Another antivirus product may be in use.",
            observed="The Defender preferences could not be read: %s. Another "
                     "product may be the scanner here." % error,
            expected="A readable Defender configuration.",
            fix="Ask the security team which antivirus product scans files "
                "on this computer.")
    paths = [p for p in as_list(prefs.get("ExclusionPath")) if p]
    procs = [p for p in as_list(prefs.get("ExclusionProcess")) if p]
    lines = ["DisableRealtimeMonitoring = %s"
             % prefs.get("DisableRealtimeMonitoring")]
    baseline = (["path     %s" % p for p in base_paths]
                + ["process  %s" % pro_exe])
    if [p for p in paths + procs if DEFENDER_SENTINEL.search("%s" % p)]:
        return dict(
            cid="PF-17", category="Security", status="Info", measured=False,
            title=title,
            result="Could not check: Defender only shows its exclusions to "
                   "an administrator.",
            observed=lines + ["Defender put \"N/A: Must be an administrator "
                              "to view exclusions\" where each list should "
                              "be, so what is excluded could not be "
                              "determined. Baseline that would be checked:"]
                     + ["  " + b for b in baseline],
            expected="A read that returns the real lists.",
            impact="This does not mean the exclusions are missing.",
            fix="Run prostall again as administrator, or ask the security "
                "team for the exclusion list. Do not request new exclusions "
                "based on this run: it could not see the existing ones.")
    if prefs.get("DisableRealtimeMonitoring") is True:
        return dict(
            cid="PF-17", category="Security", status="Info",
            title=title,
            result="Defender real-time scanning is off, so its exclusions do "
                   "not matter. Another antivirus product may be scanning "
                   "instead.",
            observed=lines,
            expected="Exclusions decide nothing while real-time scanning is "
                     "off. Another product may be scanning instead.",
            fix="Ask the security team which antivirus product scans files "
                "on this computer.")
    if prefs.get("HideExclusionsFromLocalAdmins") is True and not paths:
        return dict(
            cid="PF-17", category="Security", status="Info", measured=False,
            title=title,
            result="Could not check: a policy hides the Defender exclusion "
                   "list.",
            observed=lines + ["HideExclusionsFromLocalAdmins = True and no "
                              "list came back, so what is excluded could not "
                              "be determined."],
            expected="A read that returns the real lists.",
            fix="Ask the security team for the exclusion list.")
    expanded = [defender_expand(p, system_env) for p in paths
                if "\\:{" not in p]
    missing = [b for b in base_paths
               if not [p for p in expanded if _pattern_covers(p, b)]]
    image = ntpath.basename(pro_exe).lower()
    proc_ok = [p for p in procs
               if ("\\" not in p and p.lower() == image)
               or ("\\" in p and _pattern_covers(defender_expand(p, system_env),
                                                 pro_exe, same_depth=True))]
    lines.append("Configured path exclusions: %d; process exclusions: %d"
                 % (len(paths), len(procs)))
    lines.extend(["  path     %s" % p for p in paths[:20]])
    lines.extend(["  process  %s" % p for p in procs[:20]])
    lines.extend(["NOT EXCLUDED  path     %s" % m for m in missing])
    if not proc_ok:
        lines.append("NOT EXCLUDED  process  %s" % pro_exe)
    gaps = len(missing) + (0 if proc_ok else 1)
    return dict(
        cid="PF-17", category="Security",
        status="Warn" if gaps else "Pass",
        title=title,
        result=("Defender scans %d of the folders or programs ArcGIS Pro "
                "uses most (marked NOT EXCLUDED below)." % gaps if gaps else
                "Defender already skips the folders and program ArcGIS Pro "
                "uses most."),
        observed=lines,
        expected="The Pro install folder and the editor's ESRI folders "
                 "excluded, and ArcGISPro.exe excluded as a process. This "
                 "baseline is this tool's, not an Esri list: review it against "
                 "the organisation's antivirus standard.",
        impact="" if not gaps else
               "Scanning every file Pro opens can make it slow to start and "
               "draw. Confirm this first with the Defender performance "
               "analyzer.",
        fix="" if not gaps else
            "Ask the security team to review the items marked NOT EXCLUDED "
            "under PF-17. Give them full paths, not %APPDATA%: Defender reads "
            "variables as the system account, so %APPDATA% does not point at "
            "the editor's folder. Exclusions are a security decision, so "
            "prostall does not change them.")


def scan_aprx(path, texts, onedrive, network_drives=()):
    """What one project file says about its home folder and its refresh timers.

    texts: (entry name, JSON text) from inside the .aprx. defaultFolder and
    defaultGeoDatabase are resolved against the project folder before they
    are classified: Pro stores "." for a home folder that is the project
    folder itself, and "." on its own is never a share.
    """
    folder = ntpath.dirname(path)
    out = {"path": path, "home": None, "gdb": None, "refresh": [],
           "parsed": 0}

    def walk(node, name):
        if isinstance(node, dict):
            rate = node.get("refreshRate")
            if isinstance(rate, (int, float)) and not isinstance(rate, bool) \
                    and rate > 0:
                out["refresh"].append("%s: %s refreshRate = %s %s"
                                      % (name, node.get("name", "<unnamed>"),
                                         rate,
                                         node.get("refreshRateUnit", "")))
            for value in node.values():
                walk(value, name)
        elif isinstance(node, list):
            for value in node:
                walk(value, name)

    for name, text in texts:
        try:
            doc = json.loads(text)
        except ValueError:
            continue
        out["parsed"] += 1
        if isinstance(doc, dict):
            for key, slot in (("defaultFolder", "home"),
                              ("defaultGeoDatabase", "gdb")):
                raw = doc.get(key)
                if raw and out[slot] is None:
                    full = ntpath.normpath(ntpath.join(folder, raw))
                    out[slot] = (raw, full,
                                 path_class(full, onedrive, network_drives))
        walk(doc, name)
    return out


def decide_aprx(pro_state, projects, omitted=0):
    """PF-22. Layer refresh timers and the default geodatabase, from the .aprx.

    pro_state: "closed", "running" or "unknown". The read is refused unless
    Pro is known to be closed. Reading a project file holds it open, and a
    save that lands while it is held can fail, which would make this
    diagnostic cause the failure it was run to explain. Not knowing whether
    Pro runs refuses it too.

    projects: dicts from scan_aprx, or {"path", "error"} for one that could
    not be opened, each with "truncated". omitted: how many older recent
    projects the cap left unread.
    """
    title = "Layer refresh timers and default geodatabase"
    if pro_state != "closed":
        return dict(
            cid="PF-22", category="ArcGISPro", status="Info", measured=False,
            title=title,
            result="Not checked: %s, and reading the project file while Pro "
                   "might save it is not safe."
                   % ("ArcGIS Pro is open" if pro_state == "running"
                      else "prostall could not tell whether ArcGIS Pro is "
                           "open"),
            observed="No project file was opened: %s, and reading one holds "
                     "it open while Pro may be saving it. The project file "
                     "was not measured this run."
                     % ("ArcGIS Pro is running" if pro_state == "running"
                        else "whether ArcGIS Pro is running was not "
                             "determined"),
            expected="Pro closed, so the project can be read without "
                     "contending with a save.",
            fix="Close ArcGIS Pro and run prostall again. Until then, check "
                "each layer's refresh interval in its Properties.")
    if not projects:
        return dict(
            cid="PF-22", category="ArcGISPro", status="Info", measured=False,
            title=title,
            result="Could not check: no recent project was found to read.",
            observed="No recent project was found, so no project file was "
                     "read.",
            expected="A recent project to read.",
            fix="Check each layer's refresh interval in its Properties.")
    lines = []
    hits = []
    remote_gdb = []
    incomplete = []
    for p in projects:
        if p.get("error") or not p.get("parsed"):
            incomplete.append(p["path"])
            lines.append("unreadable  %s  (%s)"
                         % (p["path"], p.get("error")
                            or "no JSON document inside, which is the layout "
                               "of an older release"))
            continue
        lines.append(p["path"])
        for label, slot in (("home folder", "home"),
                            ("default gdb", "gdb")):
            value = p.get(slot)
            if value is None:
                lines.append("   %-12s: <absent>" % label)
                continue
            lines.append("   %-12s: %s -> %s  [%s]" % (label, value[0],
                                                      value[1], value[2]))
            if slot == "gdb" and value[2] != "LOCAL":
                remote_gdb.append(value[1])
        for hit in p["refresh"]:
            hits.append(hit)
            lines.append("   refresh     : %s" % hit)
        if p.get("truncated"):
            incomplete.append(p["path"])
            lines.append("   scan capped, so this project was not read in "
                         "full")
    if omitted:
        lines.append("%d older recent project(s) were not read." % omitted)
    found = []
    steps = []
    if hits:
        found.append("%d layer(s) refresh on a timer" % len(hits))
        steps.append("In each layer listed under PF-22, open Properties and "
                     "clear the refresh interval.")
    if remote_gdb:
        found.append("the default geodatabase is not on this computer")
        steps.append("Point the project's default geodatabase at a folder on "
                     "this computer.")
    if found:
        status = "Warn"
        result = "In the recent projects, %s." % " and ".join(found)
    elif incomplete:
        status = "Info"
        result = ("Could not read every project file in full; no refresh "
                  "timers were found in the parts that were read.")
    else:
        status = "Pass"
        result = ("No layer refreshes on a timer, and the default "
                  "geodatabase is on this computer.")
    return dict(
        cid="PF-22", category="ArcGISPro", status=status, title=title,
        result=result,
        observed=lines,
        expected="No layer on a refresh timer against a slow source, and the "
                 "default geodatabase on a local disk.",
        impact="" if status != "Warn" else
               "A layer with a refresh timer re-queries its data every few "
               "seconds even when nobody touches the map, which feels like the "
               "project freezing. A default geodatabase on a network share "
               "slows every geoprocessing tool.",
        fix=" ".join(steps + ["prostall never edits the project file."])
            if steps else "",
        detail=("Not read in full: " + "; ".join(incomplete)) if incomplete
               else "Every project listed was read in full.")


# =============================================================================
# THE PLATFORM SURFACE. Every read the checks make goes through one object, so
# the whole diagnostic can be driven from a stand-in machine in the self-test.
# =============================================================================

Entry = collections.namedtuple("Entry", "path name size mtime attrs isdir")

# Files-On-Demand marks a dehydrated item with one of these attributes. Pro
# stalls on every recall when geodatabase component files carry them.
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
# FILE_ATTRIBUTE_REPARSE_POINT (0x400) is deliberately absent: it marks any
# junction or symbolic link, and a hydrated placeholder needs no download.
PLACEHOLDER_ATTRS = (FILE_ATTRIBUTE_RECALL_ON_OPEN
                     | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)

HIVES = ("HKCU", "HKLM", "HKCR", "HKU")


def stamp(epoch):
    """UTC timestamp for a report line.

    UTC, not local time, everywhere. The report exists to be diffed against a
    known-good workstation, and two machines in different time zones would
    otherwise disagree on every line that carries a date.
    """
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(epoch))


class Env(object):
    """Reads the real machine. Off Windows every Windows-only read says so.

    Nothing here decides anything. A method that cannot answer returns None or
    an empty result, and the decision function above it turns that into an
    undetermined record rather than a verdict.
    """

    windows = (os.name == "nt")

    def now(self):
        return time.time()

    def getenv(self, name):
        return os.environ.get(name)

    def username(self):
        return (os.environ.get("USERNAME") or os.environ.get("USER")
                or "<unknown>")

    def registry_readable(self):
        """Whether a registry read can mean anything on this platform.

        Absent and unreadable are different answers, and a check that cannot
        tell them apart reports a missing setting on a machine that has one.
        """
        return self.windows

    def elevated(self):
        if not self.windows:
            return False
        try:
            import ctypes   # imported here so a non-Windows run never loads it
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    # -- registry ----------------------------------------------------------

    def _open_key(self, path):
        import winreg   # imported here so --self-test and a Linux run never
                        # need it
        hive_name, sub = path.split("\\", 1)
        hive = {"HKCU": winreg.HKEY_CURRENT_USER,
                "HKLM": winreg.HKEY_LOCAL_MACHINE,
                "HKCR": winreg.HKEY_CLASSES_ROOT,
                "HKU": winreg.HKEY_USERS}[hive_name.upper()]
        return winreg.OpenKey(hive, sub, 0,
                              winreg.KEY_READ | winreg.KEY_WOW64_64KEY)

    def reg(self, path, name):
        """One registry value, or None when the key or the value is absent.

        Absent is a normal, reportable outcome here, so it is a return value
        and not an exception.
        """
        if not self.windows:
            return None
        try:
            import winreg
            key = self._open_key(path)
            try:
                return winreg.QueryValueEx(key, name)[0]
            finally:
                key.Close()
        except Exception:
            return None

    def reg_subkeys(self, path):
        """The immediate subkey names, or an empty list."""
        if not self.windows:
            return []
        out = []
        try:
            import winreg
            key = self._open_key(path)
            try:
                index = 0
                while True:
                    try:
                        out.append(winreg.EnumKey(key, index))
                    except OSError:
                        break
                    index += 1
            finally:
                key.Close()
        except Exception:
            return []
        return out

    def reg_write(self, path, name, value):
        """Write one string value. Only reached from --apply."""
        import winreg
        hive_name, sub = path.split("\\", 1)
        hive = {"HKCU": winreg.HKEY_CURRENT_USER,
                "HKLM": winreg.HKEY_LOCAL_MACHINE}[hive_name.upper()]
        key = winreg.CreateKeyEx(hive, sub, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        finally:
            key.Close()

    # -- filesystem --------------------------------------------------------

    def isdir(self, path):
        try:
            return bool(path) and os.path.isdir(path)
        except OSError:
            return False

    def isfile(self, path):
        try:
            return bool(path) and os.path.isfile(path)
        except OSError:
            return False

    def subdirs(self, path):
        """Immediate subdirectory names, newest first is not promised."""
        if not self.isdir(path):
            return []
        try:
            return sorted([n for n in os.listdir(path)
                           if os.path.isdir(os.path.join(path, n))])
        except OSError:
            return []

    def scan(self, root, depth=3, limit=5000):
        """Depth-bounded walk. Returns (entries, unreadable).

        Bounded on purpose: a full recursive walk of a synced profile is
        minutes slow and can itself trigger the hydration of every file it
        touches, which is the condition the caller is trying to measure.

        The walk reports the folders it could not open. A scanner that returns
        nothing for a folder it could not read tells you the tree is clean when
        it has no idea, which is the same lie as a verdict with no measurement.
        """
        entries = []
        unreadable = []
        if not self.isdir(root):
            return entries, unreadable
        base = root.rstrip("\\/").count(os.sep)
        for folder, dirs, names in os.walk(root,
                                           onerror=lambda e: unreadable.append(
                                               "%s: %s" % (getattr(e, "filename",
                                                                   root), e))):
            children = list(dirs)
            # The names are captured BEFORE the prune. Clearing dirs is what
            # stops os.walk descending, and it also empties the list this loop
            # is about to read, so the deepest level came back with its files
            # but without its folders.
            if folder.rstrip("\\/").count(os.sep) - base + 1 >= depth:
                dirs[:] = []
            for name in children + list(names):
                path = os.path.join(folder, name)
                try:
                    st = os.stat(path)
                except OSError as exc:
                    unreadable.append("%s: %s" % (path, exc))
                    continue
                entries.append(Entry(path, name, st.st_size, st.st_mtime,
                                     getattr(st, "st_file_attributes", 0),
                                     os.path.isdir(path)))
                if len(entries) >= limit:
                    return entries, unreadable
        return entries, unreadable

    def read_text(self, path):
        """File contents, or None. Never called on a connection file."""
        try:
            handle = open(path, "rb")
        except (OSError, IOError):
            return None
        try:
            return handle.read().decode("utf-8", "replace")
        finally:
            handle.close()

    def rename(self, src, dst):
        os.rename(src, dst)

    def open_file(self, path):
        """Open a file with its default program. None, or why it could not.

        Off Windows it opens only when there is a desktop to open it on: with
        no display, a text-mode browser could take over the terminal.
        """
        path = os.path.abspath(path)
        try:
            if self.windows:
                os.startfile(path)
                return None
            if sys.platform == "darwin":
                argv = ["open", path]
            elif self.getenv("DISPLAY") or self.getenv("WAYLAND_DISPLAY"):
                argv = ["xdg-open", path]
            else:
                return "there is no desktop session to open it in"
            import subprocess
            devnull = open(os.devnull, "wb")
            subprocess.Popen(argv, stdout=devnull, stderr=devnull)
            return None
        except (OSError, AttributeError) as exc:
            return "%s" % exc

    # -- processes, hardware, network --------------------------------------

    def processes(self, image):
        """Lines describing every running process with this image name.

        None when the list could not be read: off Windows, or tasklist failed.
        An empty list means tasklist ran and found none.
        """
        if not self.windows:
            return None
        try:
            import subprocess   # imported here so a plain run never spawns it
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq %s" % image, "/NH"],
                stderr=subprocess.STDOUT)
        except Exception:
            return None
        lines = []
        for line in out.decode("utf-8", "replace").splitlines():
            if line.lower().startswith(image.lower()):
                lines.append(" ".join(line.split()))
        return lines

    def memory(self):
        """(total_gb, free_gb), or (None, None) when it cannot be read."""
        if not self.windows:
            return None, None
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(
                    ctypes.byref(status)):
                return None, None
            gb = float(1024 ** 3)
            return status.ullTotalPhys / gb, status.ullAvailPhys / gb
        except Exception:
            return None, None

    def disk(self, root):
        """(total_bytes, free_bytes) for a volume, or None."""
        try:
            import shutil
            usage = shutil.disk_usage(root)
            return usage.total, usage.free
        except Exception:
            return None

    def power_plan(self):
        """(guid, name) of the active power scheme, or (None, None)."""
        if not self.windows:
            return None, None
        try:
            import subprocess
            out = subprocess.check_output(["powercfg", "/getactivescheme"],
                                          stderr=subprocess.STDOUT)
            text = out.decode("utf-8", "replace")
        except Exception:
            return None, None
        match = re.search(r"([0-9a-fA-F-]{36})\s*\((.+?)\)", text)
        if not match:
            return None, None
        return match.group(1), match.group(2).strip()

    def network_drives(self):
        """Drive letters that are network drives right now, such as ["Z:"].

        GetDriveTypeW answers DRIVE_REMOTE (4) for a mapped drive. Nothing
        else knows a letter is a share: without this, a project on H: is
        classified LOCAL.
        """
        if not self.windows:
            return []
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            mask = kernel.GetLogicalDrives()
            return ["%s:" % chr(65 + n) for n in range(26)
                    if mask & (1 << n)
                    and kernel.GetDriveTypeW(u"%s:\\" % chr(65 + n)) == 4]
        except Exception:
            return []

    def tcp(self, host, port, timeout_ms):
        """One bounded TCP connect. Returns (open, elapsed_ms, error).

        open is None, not False, when the name did not resolve: a name that
        never resolved says nothing about the server, and a Fail on it blamed
        a host nobody reached. Every address the name resolves to is tried,
        IPv6 included, inside one shared deadline. The name lookup itself is
        not bounded by the timeout.

        Bounded on purpose. The obvious connect blocks for tens of seconds
        against a dead host, which would dominate the runtime of a diagnostic
        whose whole job is to be run while somebody waits.
        """
        started = time.time()
        deadline = started + timeout_ms / 1000.0

        def took():
            return int((time.time() - started) * 1000)

        try:
            infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError) as exc:
            return None, took(), "the name did not resolve: %s" % exc
        error = "no address"
        for family, kind, proto, _canon, address in infos:
            sock = socket.socket(family, kind, proto)
            sock.settimeout(max(deadline - time.time(), 0.001))
            try:
                sock.connect(address)
                return True, took(), None
            except Exception as exc:
                error = "%s" % exc
            finally:
                sock.close()
        return False, took(), error

    # -- read-only PowerShell, event log, share and project reads (1.6) ------

    powershell = "powershell.exe"
    wevtutil = "wevtutil"

    def _run(self, argv, timeout=120):
        """(returncode, stdout text, first stderr line) of one bounded child."""
        import subprocess   # imported here so a plain non-Windows run never
                            # spawns anything
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()   # ponytail: a hung child is not reproducible offline
            proc.communicate()
            raise
        lines = [l.strip() for l in err.decode("utf-8", "replace").splitlines()
                 if l.strip()]
        return (proc.returncode, out.decode("utf-8", "replace"),
                lines[0] if lines else "exit code %d" % proc.returncode)

    def _powershell_json(self, script):
        """(parsed JSON, None) or (None, reason). Never a guess between them.

        A script that throws exits non-zero under Stop, so a failed read comes
        back as a reason rather than as an empty answer.
        """
        if not self.windows:
            return None, "PowerShell is read on Windows only"
        try:
            code, out, err = self._run(
                [self.powershell, "-NoProfile", "-NonInteractive", "-Command",
                 "$ErrorActionPreference = 'Stop'; [Console]::OutputEncoding "
                 "= [Text.Encoding]::UTF8; " + script])
        except Exception as exc:
            return None, "%s: %s" % (type(exc).__name__, exc)
        if code != 0:
            return None, err
        try:
            return json.loads(out.lstrip(u"\ufeff").strip()), None
        except ValueError:
            return None, "PowerShell printed something that is not JSON"

    def defender_preferences(self):
        """The Defender fields PF-17 reads, as a dict, or (None, reason)."""
        return self._powershell_json(
            "$p = Get-MpPreference; ConvertTo-Json -Compress -InputObject "
            "@{DisableRealtimeMonitoring = $p.DisableRealtimeMonitoring; "
            "HideExclusionsFromLocalAdmins = $p.HideExclusionsFromLocalAdmins; "
            "ExclusionPath = @($p.ExclusionPath); "
            "ExclusionProcess = @($p.ExclusionProcess)}")

    def nic_power(self):
        """[{name, description, state}] for each physical adapter that is up."""
        return self._powershell_json(
            "$r = @(Get-NetAdapter -Physical | Where-Object { $_.Status -eq "
            "'Up' } | ForEach-Object { $pm = Get-NetAdapterPowerManagement "
            "-Name $_.Name -ErrorAction SilentlyContinue; [pscustomobject]@{"
            "name = $_.Name; description = $_.InterfaceDescription; state = "
            "$(if ($pm) { [string]$pm.AllowComputerToTurnOffDevice } else "
            "{ $null })} }); ConvertTo-Json -Compress -InputObject $r")

    def smb_client(self):
        """The SMB client settings PF-02 reads, as a dict, or (None, reason)."""
        return self._powershell_json(
            "$c = Get-SmbClientConfiguration; ConvertTo-Json -Compress "
            "-InputObject @{EnableMultiChannel = $c.EnableMultiChannel; "
            "RequireSecuritySignature = $c.RequireSecuritySignature; "
            "DirectoryCacheLifetime = $c.DirectoryCacheLifetime; "
            "FileInfoCacheLifetime = $c.FileInfoCacheLifetime; "
            "SessionTimeout = $c.SessionTimeout}")

    def events(self, log, query, count):
        """(XML text, None) or (None, reason) for one bounded event query.

        A log with no matching event answers an empty root element and exit
        0. Access denied and a missing log answer non-zero, and come back as
        a reason, so an unread log can never pass for a quiet one.
        """
        if not self.windows:
            return None, "the event log is read on Windows only"
        try:
            code, out, err = self._run(
                [self.wevtutil, "qe", log, "/q:" + query, "/c:%d" % count,
                 "/rd:true", "/f:xml", "/e:Events"])
        except Exception as exc:
            return None, "%s: %s" % (type(exc).__name__, exc)
        if code != 0:
            return None, err
        return out, None

    def time_listdir(self, path, limit=SHARE_LIST_LIMIT):
        """(elapsed_ms, entries, None) or (None, entries, reason)."""
        started = time.time()
        count = 0
        try:
            with os.scandir(path) as listing:
                for _entry in listing:
                    count += 1
                    if count >= limit:
                        break
        except OSError as exc:
            return None, count, "%s" % exc
        return int((time.time() - started) * 1000), count, None

    def read_zip_json(self, path, max_entries=APRX_MAX_ENTRIES,
                      max_entry=APRX_MAX_ENTRY_BYTES, max_total=APRX_MAX_BYTES):
        """([(name, text)], truncated, None) or ([], False, reason).

        Only .json entries are read, each and all of them bounded.
        """
        import zipfile
        out = []
        truncated = False
        total = 0
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, IOError, zipfile.BadZipfile) as exc:
            return [], False, "%s" % exc
        try:
            for info in archive.infolist():
                if not info.filename.lower().endswith(".json"):
                    continue
                if (len(out) >= max_entries or info.file_size > max_entry
                        or total + info.file_size > max_total):
                    truncated = True
                    continue
                total += info.file_size
                out.append((info.filename,
                            archive.read(info).decode("utf-8", "replace")))
        finally:
            archive.close()
        return out, truncated, None


# =============================================================================
# THE PROBES. Each gathers facts and hands them to its decision function.
# =============================================================================

def pro_exe_path(env):
    """Where ArcGIS Pro is installed, falling back to the default location."""
    install = env.reg(PRO_REG_KEY, "InstallDir")
    if install:
        return ntpath.join(install.rstrip("\\/"), "bin", "ArcGISPro.exe")
    return DEFAULT_PRO_EXE


def user_config_path(env):
    """The live Pro user.config for this profile, or None.

    The registry points at it; the application-data search is only a fallback
    for when that value is missing.
    """
    path = env.reg(r"HKCU\Software\ESRI\ArcGISPro\Settings",
                   "UserConfigLocationRoaming")
    if path and env.isfile(path):
        return path
    appdata = env.getenv("APPDATA")
    if not appdata:
        return None
    entries, _ = env.scan(ntpath.join(appdata, "Esri"), depth=4, limit=4000)
    candidates = [e for e in entries
                  if not e.isdir and e.name.lower() == "user.config"]
    if not candidates:
        return None
    candidates.sort(key=lambda e: e.mtime, reverse=True)
    return candidates[0].path


def read_settings(env, path):
    """The parsed user.config, or an empty mapping."""
    if not path:
        return {}
    return parse_user_config(env.read_text(path))


def onedrive_roots(env):
    return [r for r in (env.getenv("OneDrive"),
                        env.getenv("OneDriveCommercial"),
                        env.getenv("OneDriveConsumer")) if r]


def documents_path(env):
    """The Documents shell folder, expanded, or None."""
    for key in (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"):
        value = env.reg(key, "Personal")
        if value:
            return expand_vars(env, value)
    return None


def recent_projects(env, limit=12):
    """The most recently opened projects, newest first.

    Read from the project folders Pro leaves under the Documents tree plus the
    shortcuts Windows keeps, because Pro keeps no machine-readable list of its
    own. The shortcut target is not resolved: the shortcut name carries the
    project name, and the folder it sits in is what the storage question is
    actually about.
    """
    out = []
    docs = documents_path(env)
    roots = [ntpath.join(docs, "ArcGIS", "Projects")] if docs else []
    for root in roots:
        entries, _ = env.scan(root, depth=2, limit=2000)
        for e in entries:
            if not e.isdir and e.name.lower().endswith(".aprx"):
                out.append((e.path, e.mtime))
    out.sort(key=lambda pair: pair[1], reverse=True)
    onedrive = onedrive_roots(env)
    network = env.network_drives()
    return [(path, stamp(when), path_class(path, onedrive, network))
            for path, when in out[:limit]]


def probe_session(env, facts):
    me = env.username()
    others = []
    profiles_root = ntpath.dirname((env.getenv("USERPROFILE") or "").rstrip("\\/"))
    for name in env.subdirs(profiles_root):
        if name.lower() == (me or "").lower():
            continue
        if env.isdir(ntpath.join(profiles_root, name, "AppData", "Roaming",
                                 "ESRI", "ArcGISPro")):
            others.append(name)
    return decide_session(me, others, env.elevated())


def probe_backup(env, facts):
    path = user_config_path(env)
    facts["user_config"] = path
    settings = read_settings(env, path)
    facts["settings"] = settings
    recent = facts.get("recent") or []
    home = recent[0][2] if recent else "unknown"
    return decide_backup(path, settings, home)


def probe_storage(env, facts):
    facts["recent"] = recent_projects(env)
    return decide_storage(facts["recent"])


def probe_kfm(env, facts):
    lines = []
    root = r"HKCU\SOFTWARE\Microsoft\OneDrive\Accounts"
    for account in env.reg_subkeys(root):
        now = env.reg("%s\\%s" % (root, account), "KfmFoldersProtectedNow")
        once = env.reg("%s\\%s" % (root, account), "KfmFoldersProtectedOnce")
        lines.append("KFM %s: FoldersProtectedNow = %s (bitmask, not decoded); "
                     "ProtectedOnce = %s"
                     % (account,
                        "<absent>" if now is None else now,
                        "<absent>" if once is None else once))
    return decide_kfm(documents_path(env), env.getenv("OneDrive"), lines)


def probe_placeholders(env, facts):
    docs = documents_path(env)
    roots = []
    if docs:
        candidate = ntpath.join(docs, "ArcGIS", "Projects")
        if env.isdir(candidate):
            roots.append(candidate)
    inspected = 0
    found = []
    for root in roots:
        entries, unreadable = env.scan(root, depth=2, limit=800)
        inspected += len(entries)
        for e in entries:
            if e.attrs & PLACEHOLDER_ATTRS:
                found.append(e.path)
        for line in unreadable:
            found.append("%s  [unreadable, so its contents were not measured]"
                         % line)
    return decide_placeholders(roots, inspected, found)


def probe_offline_files(env, facts):
    value = env.reg(r"HKLM\SOFTWARE\Policies\Microsoft\Windows\NetCache",
                    "Enabled")
    return decide_offline_files(value)


def probe_crash(env, facts):
    local = env.getenv("LOCALAPPDATA") or ""
    now = env.now()
    dumps = []
    recent = 0
    for root in (ntpath.join(local, "ESRI", "ErrorReports"),
                 ntpath.join(local, "CrashDumps")):
        entries, _ = env.scan(root, depth=2, limit=500)
        for e in entries:
            # The extension is re-checked rather than trusted to a filter: a
            # Windows name filter matches the short alias too, so .dmpold and
            # .dmpbak strays come back from a glob for the dump extension.
            if e.isdir or not e.name.lower().endswith(".dmp"):
                continue
            if "arcgis" not in e.name.lower() and "esri" not in root.lower():
                continue
            dumps.append("%s  %s  %s" % (stamp(e.mtime), format_bytes(e.size),
                                         e.path))
            if (now - e.mtime) / 86400.0 <= CRASH_RECENT_DAYS:
                recent += 1
    dumps.sort(reverse=True)
    wer = r"HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps"
    glob = env.reg(wer, "DumpFolder")
    pro = env.reg(wer + r"\ArcGISPro.exe", "DumpFolder")
    kind = env.reg(wer + r"\ArcGISPro.exe", "DumpType")
    lines = ["Windows Error Reporting LocalDumps (all processes): DumpFolder = %s"
             % ("<absent>" if glob is None else glob),
             "LocalDumps for ArcGISPro.exe: DumpFolder = %s; DumpType = %s"
             % ("<absent>" if pro is None else pro,
                "<absent>" if kind is None else kind)]
    return decide_crash(dumps[:20], recent, lines, bool(glob or pro))


def probe_sde_reach(env, facts, opts):
    host = opts.get("sde_server")
    port = opts.get("sde_port")
    probe = env.tcp(host, port, opts["timeout"]) if host else None
    return decide_tcp(
        "DL-10", "Network", "Connection to the database server",
        host, port, probe, SDE_LATENCY_WARN_MS,
        impact="A slow or unreliable connection to the database server turns "
               "a save into a timeout, and a save that times out can lose "
               "edits.",
        fix="Check the network connection and VPN, and test again from a "
            "wired connection. If it is still slow or closed, give these "
            "results to the network or database team.",
        dataloss=True)


def probe_sde_files(env, facts):
    appdata = env.getenv("APPDATA") or ""
    local = env.getenv("LOCALAPPDATA") or ""
    roots = [ntpath.join(appdata, "ESRI", "ArcGISPro", "Favorites"),
             ntpath.join(local, "ESRI", "ArcGISPro", "Favorites"),
             ntpath.join(local, "ESRI", "Index", "Connections")]
    onedrive = onedrive_roots(env)
    network = env.network_drives()
    found = []
    index_copies = 0
    seen = set()
    for root in roots:
        entries, _ = env.scan(root, depth=3, limit=2000)
        for e in entries:
            if e.isdir or not e.name.lower().endswith(".sde"):
                continue
            if e.path in seen:
                continue
            seen.add(e.path)
            found.append((e.path, path_class(e.path, onedrive, network)))
            if "\\index\\connections\\" in e.path.lower().replace("/", "\\"):
                index_copies += 1
    found.sort()
    return decide_sde_files(found, index_copies)


def probe_pro_running(env, facts):
    processes = env.processes("ArcGISPro.exe")
    facts["pro_running"] = None if processes is None else bool(processes)
    return decide_pro_running(processes)


def probe_protemp(env, facts):
    temp = env.getenv("TEMP") or ""
    folders = []
    for name in env.subdirs(temp):
        if not name.lower().startswith("arcgisprotemp"):
            continue
        root = ntpath.join(temp, name)
        entries, _ = env.scan(root, depth=4, limit=4000)
        size = 0
        newest = 0.0
        projects = 0
        tables = 0
        for e in entries:
            if e.isdir:
                continue
            size += e.size
            newest = max(newest, e.mtime)
            low = e.name.lower()
            if low.endswith(".aprx"):
                projects += 1
            elif (low.endswith(".gdbtable") and low.startswith("a")
                  and e.size > EMPTY_GDBTABLE_BYTES):
                tables += 1
        folders.append((name, stamp(newest or env.now()), size, projects,
                        tables))
    folders.sort(key=lambda row: row[1], reverse=True)
    return decide_protemp(folders, temp or "<TEMP unset>")


def probe_pro_version(env, facts):
    install = env.reg(PRO_REG_KEY, "InstallDir")
    exe = pro_exe_path(env)
    facts["pro_exe"] = exe
    exe_version = None
    if env.isfile(exe):
        exe_version = "present on disk"
    return decide_pro_version(env.reg(PRO_REG_KEY, "Version"),
                              env.reg(PRO_REG_KEY, "RealVersion"),
                              env.reg(PRO_REG_KEY, "BuildNumber"),
                              install, exe_version)


def probe_rendering(env, facts):
    return decide_rendering(facts.get("user_config"), facts.get("settings"))


def probe_caches(env, facts):
    path = env.reg(r"HKCU\Software\ESRI\ArcGISPro\Settings", "Cache Path")
    if not path:
        path = ntpath.join(env.getenv("LOCALAPPDATA") or "", "ESRI",
                           "Local Caches")
    facts["cache_path"] = path
    if not env.isdir(path):
        return decide_caches(path, False, 0, 0, "n/a", None, 0)
    entries, _ = env.scan(path, depth=4, limit=30000)
    files = [e for e in entries if not e.isdir]
    total = sum([e.size for e in files])
    now = env.now()
    oldest = min([e.mtime for e in files]) if files else now
    stale = len([e for e in files
                 if e.name.lower().endswith(".cache")
                 and (now - e.mtime) / 86400.0 > CACHE_STALE_DAYS])
    return decide_caches(path, True, total, len(files), stamp(oldest),
                         (now - oldest) / 86400.0, stale)


def probe_index(env, facts):
    root = ntpath.join(env.getenv("LOCALAPPDATA") or "", "ESRI", "Index")
    entries, _ = env.scan(ntpath.join(root, "ResourceIndex"), depth=4,
                          limit=30000)
    files = [e for e in entries if not e.isdir]
    conn_entries, _ = env.scan(ntpath.join(root, "Connections"), depth=2,
                               limit=2000)
    connections = len([e for e in conn_entries
                       if not e.isdir and e.name.lower().endswith(".sde")])
    status_file = ntpath.join(root, "Config", "Status.cfg")
    status_stamp = "<absent>"
    if env.isfile(status_file):
        found, _ = env.scan(ntpath.join(root, "Config"), depth=1, limit=200)
        for e in found:
            if e.name.lower() == "status.cfg":
                status_stamp = stamp(e.mtime)
    return decide_index(sum([e.size for e in files]), len(files), connections,
                        status_stamp, facts.get("settings"))


def probe_gpu_preference(env, facts):
    exe = facts.get("pro_exe") or pro_exe_path(env)
    facts["pro_exe"] = exe
    facts["gpu_previous"] = env.reg(GPU_PREFERENCE_KEY, exe)
    return decide_gpu_preference(exe, facts["gpu_previous"],
                                env.registry_readable())


def probe_memory(env, facts):
    total, free = env.memory()
    return decide_memory(total, free)


def probe_disk(env, facts):
    roots = []
    system = env.getenv("SystemDrive") or "C:"
    roots.append(system if system.endswith("\\") else system + "\\")
    for path, _when, cls in (facts.get("recent") or []):
        if cls != "LOCAL":
            continue
        root = path_root(path)
        if root and (root + "\\") not in roots:
            roots.append(root + "\\")
    volumes = []
    for root in roots:
        usage = env.disk(root)
        if usage:
            volumes.append((root, usage[0], usage[1]))
    return decide_disk(volumes)


def probe_power_plan(env, facts):
    guid, name = env.power_plan()
    return decide_power_plan(guid, name)


def probe_portal(env, facts, opts):
    host = opts.get("portal")
    probe = env.tcp(host, 443, opts["timeout"]) if host else None
    return decide_tcp(
        "PF-19", "Network", "Connection to the portal",
        host, 443, probe, PORTAL_LATENCY_WARN_MS,
        impact="A slow portal makes sign-in slower, and every portal layer "
               "in the project takes longer to load when it opens.",
        fix="Check the network connection and VPN. If it is still slow or "
            "closed, give these results to the network team before changing "
            "any Pro setting.",
        flag="--portal")


def probe_roaming(env, facts):
    appdata = env.getenv("APPDATA") or ""
    entries, _ = env.scan(ntpath.join(appdata, "ESRI"), depth=4, limit=20000)
    files = [e for e in entries if not e.isdir]
    roaming = None
    profile = (env.getenv("USERPROFILE") or "").rstrip("\\/").lower()
    root = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
    for sid in env.reg_subkeys(root):
        image = env.reg("%s\\%s" % (root, sid), "ProfileImagePath")
        if image and expand_vars(env, image).rstrip("\\/").lower() == profile:
            roaming = bool(env.reg("%s\\%s" % (root, sid), "CentralProfile"))
            break
    return decide_roaming(roaming, sum([e.size for e in files]), len(files))


def probe_symbology(env, facts):
    return decide_symbology(cfg(facts.get("settings"), "ManageFeatureCaching"))


def probe_nic(env, facts):
    data, error = env.nic_power()
    if error is not None:
        return decide_nic(None, error)
    return decide_nic([("%s" % r.get("name"), "%s" % r.get("description"),
                        r.get("state"))
                       for r in as_list(data) if isinstance(r, dict)], None)


def event_queries():
    """(log, XPath) for the timeline. Filtered by id here, by provider later."""
    since = ("TimeCreated[timediff(@SystemTime) <= %d]"
             % (EVENT_WINDOW_DAYS * 86400000))
    return [
        ("Application",
         "*[System[Provider[@Name='Application Error' or "
         "@Name='Application Hang'] and (EventID=1000 or EventID=1002) and "
         "%s]]" % since),
        ("System", "*[System[(EventID=41 or EventID=6008) and %s]]" % since),
        ("Microsoft-Windows-SMBClient/Connectivity",
         "*[System[(Level=1 or Level=2 or Level=3) and %s]]" % since),
    ]


def probe_events(env, facts):
    rows = []
    unreadable = []
    capped = []
    queries = event_queries()
    for log, query in queries:
        text, error = env.events(log, query, EVENT_MAX)
        found = None if error else parse_events(text)
        if found is None:
            unreadable.append((log, error or "the reply was not XML"))
            continue
        if len(found) >= EVENT_MAX:
            capped.append(log)
        for event in found:
            what = classify_event(log, event)
            if what:
                rows.append((event["when"], what))
    return decide_events(rows, unreadable, len(queries), capped)


def probe_smb(env, facts):
    config, error = env.smb_client()
    shares = len([1 for _p, _w, cls in (facts.get("recent") or [])
                  if cls == "NETWORK"])
    if not isinstance(config, dict):
        return decide_smb(None, error or "the answer was not a settings "
                                         "object", shares)
    return decide_smb(config, None, shares)


def probe_share_latency(env, facts):
    folders = []
    for path, _when, cls in (facts.get("recent") or []):
        folder = ntpath.dirname(path)
        if cls == "NETWORK" and folder not in folders:
            folders.append(folder)
    rows = []
    for folder in folders[:SHARE_FOLDERS_TIMED]:
        elapsed, entries, error = env.time_listdir(folder)
        rows.append((folder, elapsed, entries, error))
    return decide_share_latency(rows,
                                max(len(folders) - SHARE_FOLDERS_TIMED, 0))


def defender_system_env(env):
    """The variables Defender sees, as LocalSystem, keyed upper-case."""
    root = (env.getenv("SystemRoot") or r"C:\Windows").rstrip("\\/")
    out = {}
    for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData",
                 "ALLUSERSPROFILE", "PUBLIC", "SystemDrive", "SystemRoot",
                 "windir", "CommonProgramFiles"):
        value = env.getenv(name)
        if value:
            out[name.upper()] = value
    for name, tail in LOCALSYSTEM_PROFILE_VARS.items():
        out[name] = ntpath.join(root, tail)
    return out


def probe_defender(env, facts):
    prefs, error = env.defender_preferences()
    exe = facts.get("pro_exe") or pro_exe_path(env)
    base = [ntpath.dirname(ntpath.dirname(exe))]
    for name in ("LOCALAPPDATA", "APPDATA"):
        value = env.getenv(name)
        if value:
            base.append(ntpath.join(value, "ESRI"))
    if not isinstance(prefs, dict):
        prefs = None
        error = error or "the answer was not a settings object"
    return decide_defender(prefs, error, base, exe, defender_system_env(env))


def probe_aprx(env, facts):
    running = facts.get("pro_running")
    state = ("unknown" if running is None
             else "running" if running else "closed")
    if state != "closed":
        return decide_aprx(state, [])
    onedrive = onedrive_roots(env)
    network = env.network_drives()
    recent = facts.get("recent") or []
    projects = []
    for path, _when, _cls in recent[:APRX_MAX_PROJECTS]:
        texts, truncated, error = env.read_zip_json(path)
        if error:
            projects.append({"path": path, "error": error})
            continue
        found = scan_aprx(path, texts, onedrive, network)
        found["truncated"] = truncated
        projects.append(found)
    return decide_aprx(state, projects, max(len(recent) - APRX_MAX_PROJECTS, 0))


# =============================================================================
# THE RUN. Order in this list is the order of both reports.
# =============================================================================

def registrations(env, facts, opts):
    """Every check, data loss first. Do not reshuffle casually.

    Silent edit loss outranks every performance finding, so the block that
    bears on it runs first and prints first. One leftover-session check keeps
    its performance id and sits in this block anyway: a folder from a session
    that died can hold the unsaved work, which makes it a recovery lead rather
    than disk churn.
    """
    return [
        ("DL-00", "Session", "Account being checked", True,
         lambda: probe_session(env, facts)),
        ("DL-05", "Storage", "Where projects are stored", True,
         lambda: probe_storage(env, facts)),
        ("DL-03", "ArcGISPro", "Project backup setting", True,
         lambda: probe_backup(env, facts)),
        ("DL-04", "Network", "Network adapter power saving", True,
         lambda: probe_nic(env, facts)),
        ("DL-06", "Storage", "Documents folder synced by OneDrive", True,
         lambda: probe_kfm(env, facts)),
        ("DL-07", "Storage", "Online-only OneDrive files in project folders", True,
         lambda: probe_placeholders(env, facts)),
        ("DL-08", "Network", "Windows Offline Files", True,
         lambda: probe_offline_files(env, facts)),
        ("DL-09", "ArcGISPro", "ArcGIS Pro crash files", True,
         lambda: probe_crash(env, facts)),
        ("DL-10", "Network", "Connection to the database server", True,
         lambda: probe_sde_reach(env, facts, opts)),
        ("DL-11", "Geodatabase", "Database connection files (.sde)", True,
         lambda: probe_sde_files(env, facts)),
        ("DL-12", "ArcGISPro", "ArcGIS Pro open during the check", True,
         lambda: probe_pro_running(env, facts)),
        ("DL-13", "System", "Crashes and freezes in the Windows event log", True,
         lambda: probe_events(env, facts)),
        ("PF-07", "Cache", "Leftover ArcGIS Pro session folders", True,
         lambda: probe_protemp(env, facts)),

        ("PF-02", "Network", "Network file-share (SMB) settings", False,
         lambda: probe_smb(env, facts)),
        ("PF-03", "Network", "Network share speed",
         False, lambda: probe_share_latency(env, facts)),
        ("PF-04", "ArcGISPro", "ArcGIS Pro version", False,
         lambda: probe_pro_version(env, facts)),
        ("PF-05", "Display", "Pro display settings", False,
         lambda: probe_rendering(env, facts)),
        ("PF-06", "Cache", "Pro local cache", False,
         lambda: probe_caches(env, facts)),
        ("PF-08", "ArcGISPro", "Pro search index", False,
         lambda: probe_index(env, facts)),
        ("PF-11", "Hardware", "Graphics card used by Pro", False,
         lambda: probe_gpu_preference(env, facts)),
        ("PF-12", "Hardware", "Memory (RAM)", False,
         lambda: probe_memory(env, facts)),
        ("PF-13", "Hardware", "Free disk space", False,
         lambda: probe_disk(env, facts)),
        ("PF-15", "System", "Power plan", False,
         lambda: probe_power_plan(env, facts)),
        ("PF-17", "Security", "Antivirus exclusions (Microsoft Defender)", False,
         lambda: probe_defender(env, facts)),
        ("PF-19", "Network", "Connection to the portal", False,
         lambda: probe_portal(env, facts, opts)),
        ("PF-22", "ArcGISPro", "Layer refresh timers and default geodatabase", False,
         lambda: probe_aprx(env, facts)),
        ("PF-26", "System", "Roaming profile", False,
         lambda: probe_roaming(env, facts)),
        ("PF-27", "Display", "Map not redrawing after a field calculation", False,
         lambda: probe_symbology(env, facts)),
    ]


def check_ids():
    """Every registered check id, in report order. Probes are not called."""
    return [reg[0] for reg in registrations(None, {}, {})]


def fix_ids():
    return [fix[0] for fix in FIXES]


def config_problems(checks_enabled, fixes_enabled):
    """Why the configuration block cannot be used, as a list of sentences.

    An id nobody owns is a typo, and a typo in a switch that turns a check
    off would otherwise leave that check running while the operator believes
    it is off.
    """
    problems = []
    for name, table, known in (("CHECKS_ENABLED", checks_enabled, check_ids()),
                               ("FIXES_ENABLED", fixes_enabled, fix_ids())):
        for key in sorted(table):
            if key not in known:
                problems.append("%s names %r, which is not one of %s"
                                % (name, key, ", ".join(known)))
            elif not isinstance(table[key], bool):
                problems.append("%s[%r] is %r, not True or False"
                                % (name, key, table[key]))
    return problems


def run_checks(env, opts):
    """Run every check under containment. Returns (checks, facts).

    opts["checks_enabled"] maps an id to False to turn that check off. Without
    the key every check runs, which is what the self-test relies on.
    """
    checks = []
    facts = {}
    enabled = opts.get("checks_enabled") or {}
    for cid, category, title, dataloss, probe in registrations(env, facts,
                                                               opts):
        if enabled.get(cid, True) is False:
            add_check(checks, cid=cid, category=category, title=title,
                      status="Skipped", dataloss=dataloss, measured=False,
                      result="Not checked: turned off in CHECKS_ENABLED.",
                      observed="Turned off in CHECKS_ENABLED, so it was not "
                               "measured this run.",
                      expected="The check runs.",
                      impact="This possible cause is neither confirmed nor "
                             "ruled out, and no automatic fix will act on it.",
                      fix="To include it, set %s to True in CHECKS_ENABLED "
                          "at the top of prostall.py and run again." % cid)
            continue

        def body(target, probe=probe):
            add_check(target, **probe())
        safe_check(checks, cid, category, title, body, dataloss=dataloss)
    return checks, facts


# =============================================================================
# THE FIXES. Reached only with --apply, each one gated, reversible, and
# printing its own undo. Nothing here deletes anything.
# =============================================================================

def apply_gate(checks, target, per_user):
    """May a fix for this check run? Returns (allowed, reason).

    Skipped is called out by name rather than folded into "not failing". A
    skipped check is one whose probe threw or produced nothing, and a fix that
    fires off it acts on a determination that was never made.
    """
    check = by_id(checks, target)
    if check is None:
        return False, "%s did not run, so there is nothing to act on" % target
    if check["status"] == "Skipped":
        return False, ("%s was skipped, so nothing about it was determined and "
                       "no fix may run off that record" % target)
    if check["status"] == "Info":
        return False, ("%s was read but not determined (Info), so there is "
                       "nothing known to fix" % target)
    if check["status"] not in ("Fail", "Warn"):
        return False, "%s is already healthy (%s)" % (target, check["status"])
    if per_user:
        session = by_id(checks, "DL-00")
        if session is not None and session["status"] == "Fail":
            return False, ("a per-user fix would write into this profile, not "
                           "the editor's. Re-run it in the editor's session")
    return True, ""


def fix_cache_aside(env, checks, facts):
    """Rename the ESRI local cache folder aside so Pro rebuilds it."""
    path = facts.get("cache_path")
    if not path or not env.isdir(path):
        return None
    moved = "%s.prostall-%s" % (path,
                                time.strftime("%Y%m%d%H%M%S",
                                              time.gmtime(env.now())))
    env.rename(path, moved)
    return {"id": "AP-01",
            "what": "Renamed the ESRI local cache folder aside. Pro rebuilds "
                    "it on the next launch.",
            "result": "%s -> %s" % (path, moved),
            "undo": 'move "%s" "%s"' % (moved, path),
            "when": stamp(env.now())}


def fix_gpu_preference(env, checks, facts):
    """Pin ArcGIS Pro to the high-performance graphics adapter."""
    exe = facts.get("pro_exe")
    if not exe:
        return None
    previous = facts.get("gpu_previous")
    env.reg_write(GPU_PREFERENCE_KEY, exe, GPU_PREFERENCE_WANTED)
    if previous:
        undo = ('reg add "%s" /v "%s" /t REG_SZ /d "%s" /f'
                % (GPU_PREFERENCE_KEY, exe, previous))
    else:
        undo = 'reg delete "%s" /v "%s" /f' % (GPU_PREFERENCE_KEY, exe)
    return {"id": "AP-02",
            "what": "Pinned ArcGIS Pro to the high-performance graphics "
                    "adapter.",
            "result": "%s = %s  (previous: %s)"
                      % (exe, GPU_PREFERENCE_WANTED, previous or "<none>"),
            "undo": undo,
            "when": stamp(env.now())}


# (id, the check it acts on, is it per-user, extra gate, handler)
FIXES = (
    ("AP-01", "PF-06", True, "DL-12", fix_cache_aside),
    ("AP-02", "PF-11", True, None, fix_gpu_preference),
)


def apply_fixes(checks, env, facts, do_apply, enabled=None):
    """Returns (applied, notes). With do_apply false, applied is always empty.

    The gate is evaluated either way, so a check-only run still prints exactly
    which fixes would have run and which were refused, and why. enabled maps a
    fix id to False to turn that fix off; without it every fix is eligible.
    """
    applied = []
    notes = []
    for cid, target, per_user, blocker, handler in FIXES:
        if (enabled or {}).get(cid, True) is False:
            notes.append("off [%s] turned off in FIXES_ENABLED" % cid)
            continue
        allowed, why = apply_gate(checks, target, per_user)
        if not allowed:
            notes.append("skip [%s] %s" % (cid, why))
            continue
        if blocker:
            blocking = by_id(checks, blocker)
            if blocking is not None and blocking["status"] != "Pass":
                notes.append("refused [%s] %s: %s"
                             % (cid, blocker, blocking["title"]))
                continue
        summary = (handler.__doc__ or "").strip().splitlines()[0]
        if not do_apply:
            notes.append("would apply [%s] %s" % (cid, summary))
            continue
        try:
            record = handler(env, checks, facts)
        except Exception as exc:
            notes.append("failed [%s] %s: %s" % (cid, type(exc).__name__, exc))
            continue
        if record is None:
            notes.append("skip [%s] the target it would change is gone" % cid)
            continue
        applied.append(record)
    return applied, notes


def run(env, opts):
    """One whole diagnostic. Returns the report document."""
    checks, facts = run_checks(env, opts)
    applied, notes = apply_fixes(checks, env, facts, opts.get("apply", False),
                                 opts.get("fixes_enabled"))
    return {
        "prostall": REPORT_VERSION,
        "taken": stamp(env.now()),
        "user": env.username(),
        "elevated": env.elevated(),
        "checks": checks,
        "counts": dict(counts(checks)),
        "act_first": [c["id"] for c in act_first(checks)],
        "fixable": [c["id"] for c in fixable(checks)],
        "applied": applied,
        "notes": notes,
    }


# =============================================================================
# REPORTING
# =============================================================================

WIDTH = 79

# What each status is called in the console report. The JSON keeps the five
# status names; these are only how a person reads them.
STATUS_LABELS = {
    "Fail": "PROBLEM",
    "Warn": "WARNING",
    "Pass": "OK",
    "Info": "INFO",
    "Skipped": "NOT CHECKED",
}
LABEL_WIDTH = 13


def _wrap(text, first, rest=None):
    """Wrapped lines of one paragraph, or nothing for empty text."""
    if not (text or "").strip():
        return []
    return textwrap.wrap(text, WIDTH, initial_indent=first,
                         subsequent_indent=first if rest is None else rest,
                         break_long_words=False, break_on_hyphens=False)


def _heading(text):
    return "== %s %s" % (text, "=" * (WIDTH - len(text) - 4))


def undetermined(check):
    """Skipped, or Info on a check that says it did not measure."""
    return (check["status"] == "Skipped"
            or (check["status"] == "Info" and not check.get("measured", True)))


def next_steps(document):
    """The numbered to-do list: [(number, check)], data loss first.

    A step is a Fail or Warn that carries fix text. Numbering runs on across
    both groups, so the body of the report can point at a step by number.
    """
    found = [c for c in document["checks"]
             if c["status"] in ("Fail", "Warn") and c["fix"]]
    ordered = ([c for c in found if c["dataloss"]]
               + [c for c in found if not c["dataloss"]])
    return [(n + 1, c) for n, c in enumerate(ordered)]


def _fix_notes(document):
    """(fix id, plain sentence) for each fix that was held back or failed."""
    out = []
    titles = dict([(f[0], (f[4].__doc__ or "").strip().splitlines()[0])
                   for f in FIXES])
    for note in document["notes"]:
        match = re.match(r"(\w+(?: \w+)?) \[([\w-]+)\] ?(.*)$", note)
        if not match:
            continue
        kind, fid, rest = match.groups()
        what = titles.get(fid, fid).rstrip(".")
        if kind == "refused":
            bid = rest.split(":")[0]
            blocker = by_id(document["checks"], bid)
            out.append((fid, "%s: not done. Held back by \"%s\" [%s]."
                        % (what, blocker["title"] if blocker else bid, bid)))
        elif kind == "failed":
            out.append((fid, "%s: tried and failed (%s)." % (what, rest)))
        elif kind == "off":
            out.append((fid, "%s: turned off in FIXES_ENABLED." % what))
    return out


def render(document, verbose=False):
    """The console and text report, as a list of lines."""
    steps = next_steps(document)
    step_of = dict([(c["id"], n) for n, c in steps])
    tally = dict(counts(document["checks"]))
    lines = ["prostall: ArcGIS Pro workstation check",
             "Run on %s UTC as %s (%s)"
             % (document["taken"], document["user"],
                "as administrator" if document["elevated"]
                else "not as administrator"),
             ""]
    lines.extend(_wrap(
        "Overall: %d problem(s), %d warning(s), %d OK, %d for information, "
        "%d not checked." % (tally["Fail"], tally["Warn"], tally["Pass"],
                             tally["Info"], tally["Skipped"]), ""))
    for heading, wanted in (("RISKS TO SAVED EDITS", True),
                            ("PERFORMANCE", False)):
        lines.append("")
        lines.append(_heading(heading))
        for check in document["checks"]:
            if bool(check["dataloss"]) is not wanted:
                continue
            lines.append("")
            lines.extend(render_check(check, verbose,
                                      step=step_of.get(check["id"])))

    lines.append("")
    lines.append(_heading("WHAT TO DO NEXT"))
    first = [(n, c) for n, c in steps if c["dataloss"]]
    then = [(n, c) for n, c in steps if not c["dataloss"]]
    if not steps:
        lines.append("")
        if exit_code(document["checks"]) == 3:
            lines.extend(_wrap("Almost nothing could be checked on this "
                               "machine, so this report cannot say whether "
                               "anything needs fixing. See the list below.",
                               ""))
        else:
            lines.append("Nothing needs fixing based on what could be checked.")
    for intro, group in (("Start with these. They can cause lost edits:",
                          first),
                         ("Then these, which affect speed:", then)):
        if not group:
            continue
        lines.append("")
        lines.append(intro)
        for n, check in group:
            lines.append("")
            lines.extend(_wrap("%2d. %s [%s]" % (n, check["title"],
                                                 check["id"]),
                               "  ", "      "))
            lines.extend(_wrap(check["fix"], "      "))

    if document["fixable"]:
        lines.append("")
        lines.extend(_wrap("prostall can make %d of these changes for you. "
                           "Close ArcGIS Pro, then run prostall again with "
                           "--apply:" % len(document["fixable"]), ""))
        for cid in document["fixable"]:
            check = by_id(document["checks"], cid)
            lines.extend(_wrap("- %s [%s]" % (check["title"], cid),
                               "  ", "    "))
    held = _fix_notes(document)
    if held:
        lines.append("")
        lines.append("Automatic fixes not done this time:")
        for fid, text in held:
            lines.extend(_wrap("- [%s] %s" % (fid, text), "  ", "    "))
    if document["applied"]:
        lines.append("")
        lines.append("Changed by --apply:")
        for record in document["applied"]:
            lines.extend(_wrap("- [%s] %s" % (record["id"], record["what"]),
                               "  ", "    "))
            lines.append("    Result:  %s" % record["result"])
            lines.append("    To undo: %s" % record["undo"])

    unsure = [c for c in document["checks"] if undetermined(c) and c["fix"]]
    if unsure:
        lines.append("")
        lines.extend(_wrap("These could not be checked. Look at them by hand "
                           "if they might matter:", ""))
        for check in unsure:
            lines.append("")
            lines.extend(_wrap("- %s [%s]" % (check["title"], check["id"]),
                               "  ", "    "))
            lines.extend(_wrap(check["fix"], "    "))
    if verbose and document["notes"]:
        lines.append("")
        lines.append("Fix log:")
        for note in document["notes"]:
            lines.extend(_wrap(note, "  ", "    "))
    return lines


def render_check(check, verbose=False, step=None):
    """One check, as the report prints it.

    OK, INFO and NOT CHECKED print their one-line result; the evidence behind
    them is in --verbose. A PROBLEM or WARNING also prints why it matters and
    what was found, and points at its step in the to-do list.
    """
    pad = " " * LABEL_WIDTH
    lines = ["%-*s%s  [%s]" % (LABEL_WIDTH, STATUS_LABELS[check["status"]],
                               check["title"], check["id"])]
    lines.extend(_wrap(check["result"], pad))
    flagged = check["status"] in ("Warn", "Fail")
    if flagged or verbose:
        lines.extend(_wrap("Why it matters: %s" % check["impact"], pad)
                     if check["impact"] else [])
    if flagged and step is not None:
        lines.append("%sWhat to do: see step %d below." % (pad, step))
    if verbose and check["fix"] and step is None:
        lines.extend(_wrap("What to do: %s" % check["fix"], pad))
    if verbose and check["expected"]:
        lines.extend(_wrap("Expected: %s" % check["expected"], pad))
    if flagged or verbose:
        found = [l for l in check["observed"].split("\n") if l.strip()]
        if found:
            lines.append("%sFound:" % pad)
            lines.extend(["%s  %s" % (pad, l) for l in found])
    if verbose:
        for line in check["detail"].split("\n"):
            if line.strip():
                lines.append("%s| %s" % (pad, line))
    return lines


def write_report(document, directory, verbose=False):
    """Write the text, JSON and HTML reports. Returns the three paths."""
    if not os.path.isdir(directory):
        os.makedirs(directory)
    base = "prostall_%s" % time.strftime("%Y%m%d_%H%M%S",
                                         time.gmtime(time.time()))
    text_path = os.path.join(directory, base + ".txt")
    json_path = os.path.join(directory, base + ".json")
    html_path = os.path.join(directory, base + ".html")
    handle = open(text_path, "wb")
    try:
        handle.write(("\n".join(render(document, verbose)) + "\n")
                     .encode("utf-8"))
    finally:
        handle.close()
    handle = open(json_path, "wb")
    try:
        handle.write(json.dumps(document, indent=1, sort_keys=True)
                     .encode("utf-8"))
    finally:
        handle.close()
    write_html(document, html_path)
    return text_path, json_path, html_path


# -- The HTML report ----------------------------------------------------------
# One self-contained file: no web font, no script or stylesheet fetched from
# anywhere, so it opens the same on a locked-down workstation as on the desk of
# whoever it is mailed to. The colours and type follow the NYCEM brand guide:
# NYCEM Blue for the header, Electric Blue for headings and links, Sky Blue for
# accents, and the program colours for the status badges. Owners is an Adobe
# font a workstation will rarely have, so titles fall back to Aptos Black, the
# guide's own Microsoft substitute.

HTML_STATUS_CLASS = {
    "Fail": "fail",
    "Warn": "warn",
    "Pass": "ok",
    "Info": "info",
    "Skipped": "skip",
}

# A Windows path: a drive or a UNC share, then anything up to the first thing
# that ends a path in this tool's evidence lines. Paths hold single spaces
# ("Local Caches", "OneDrive - Example"), so a single space cannot end one; a
# double space, a column separator or a following word or number does.
_PATH_START = re.compile(r"(?:\b[A-Za-z]:\\|\\\\[\w.$-]+\\)")
_PATH_STOP = re.compile(r"\s{2}|\s[(=<\[]|\s->|[;,]\s|\s\d|\.\s|\.$|\s+$"
                        r"|\s(?:under|and|is|was|which|so|on|in|for|to|from|"
                        r"with|but|not)\s")
_FILE_EXT = re.compile(r"\.(aprx|dmp|sde|config|exe|xml|json|txt|log|zip)$",
                       re.IGNORECASE)


def find_paths(text):
    """The Windows paths in a block of evidence text, in order, once each."""
    found = []
    for line in (text or "").split("\n"):
        for start in _PATH_START.finditer(line):
            rest = line[start.start():]
            rest = rest.split('"')[0].split("<")[0]
            stop = _PATH_STOP.search(rest, 3)
            path = (rest[:stop.start()] if stop else rest).rstrip(".,;:)")
            if len(path) > 3 and path not in found:
                found.append(path)
    return found


def folder_of(path):
    """The folder to open for a path: a file's parent, or the path itself."""
    return ntpath.dirname(path) if _FILE_EXT.search(path) else path


def check_paths(check):
    """The paths a person may want to open for this check."""
    paths = find_paths("\n".join([check["observed"], check["detail"]]))
    if check["id"] == "PF-07":
        # The session folders are listed by name under one root. The ones
        # worth opening are those the check says may hold unsaved work.
        root = (paths or [""])[0]
        names = re.findall(r"ArcGISProTemp\w+", check["detail"])
        if root and names:
            paths = [ntpath.join(root, name) for name in names] + paths
    if len(paths) > 5:
        # A long list of files (a dozen .sde files) reads better as the few
        # folders that hold them.
        folders = []
        for path in paths:
            if folder_of(path) not in folders:
                folders.append(folder_of(path))
        paths = folders
    return paths


def _ps_quote(text):
    return "'%s'" % text.replace("'", "''")


_PRO_CLOSED = ("    if (Get-Process ArcGISPro -ErrorAction SilentlyContinue) {\n"
               "        Write-Warning 'Close ArcGIS Pro first, then run this "
               "again.'; return\n"
               "    }\n")


def cleanup_scripts(check):
    """[(title, what it does, PowerShell)] that help with this check's step.

    These are offered, not run: the person copies one into PowerShell. Each is
    wrapped in & { } so a pasted script stops as a whole when a guard says
    stop, rather than one line at a time. Nothing here deletes a file
    outright. The one that removes folders sends them to the Recycle Bin, and
    only after a preview run that changes nothing.
    """
    cid = check["id"]
    paths = check_paths(check)
    out = []
    if cid == "PF-07":
        keep = re.findall(r"ArcGISProTemp\w+", check["detail"])
        out.append((
            "List the session folders",
            "Read-only. Shows each leftover session folder with its size and "
            "how many project files it holds, newest first.",
            "& {\n"
            "    Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter "
            "'ArcGISProTemp*' |\n"
            "        Sort-Object LastWriteTime -Descending |\n"
            "        ForEach-Object {\n"
            "            $files = Get-ChildItem -LiteralPath $_.FullName "
            "-Recurse -File -ErrorAction SilentlyContinue\n"
            "            [pscustomobject]@{\n"
            "                Folder   = $_.FullName\n"
            "                Modified = $_.LastWriteTime\n"
            "                SizeMB   = [math]::Round(($files | Measure-Object "
            "Length -Sum).Sum / 1MB, 1)\n"
            "                Projects = @($files | Where-Object Extension -eq "
            "'.aprx').Count\n"
            "            }\n"
            "        } | Format-Table -AutoSize\n"
            "}"))
        out.append((
            "Recycle old session folders",
            "Sends session folders older than 7 days to the Recycle Bin. It "
            "keeps any folder that holds a project file, and the ones this "
            "report says may hold unsaved work. The first run only lists what "
            "it would do; change $Preview to $false to act.",
            "& {\n"
            "    $Preview = $true    # change to $false to recycle the folders\n"
            "    $Days    = 7\n"
            "    $Keep    = @(%s)\n"
            "%s"
            "    Add-Type -AssemblyName Microsoft.VisualBasic\n"
            "    $cutoff = (Get-Date).AddDays(-$Days)\n"
            "    Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter "
            "'ArcGISProTemp*' |\n"
            "        Where-Object { $_.LastWriteTime -lt $cutoff } |\n"
            "        ForEach-Object {\n"
            "            $held = Get-ChildItem -LiteralPath $_.FullName -Recurse "
            "-File -Filter '*.aprx' -ErrorAction SilentlyContinue\n"
            "            if ($held -or $Keep -contains $_.Name) {\n"
            "                Write-Host \"Kept, may hold unsaved work: "
            "$($_.FullName)\"; return\n"
            "            }\n"
            "            if ($Preview) { Write-Host \"Would recycle: "
            "$($_.FullName)\"; return }\n"
            "            [Microsoft.VisualBasic.FileIO.FileSystem]::"
            "DeleteDirectory($_.FullName, 'OnlyErrorDialogs', "
            "'SendToRecycleBin')\n"
            "            Write-Host \"Recycled: $($_.FullName)\"\n"
            "        }\n"
            "}" % (", ".join([_ps_quote(k) for k in keep]), _PRO_CLOSED)))
    elif cid == "PF-06" and paths:
        out.append((
            "Move the cache aside",
            "Does what --apply does: renames the cache folder so Pro builds a "
            "fresh one. Nothing is deleted, and it prints the command to undo. "
            "Once Pro has worked well for a week, the renamed folder can go.",
            "& {\n"
            "    $Cache = %s\n"
            "%s"
            "    if (-not (Test-Path -LiteralPath $Cache)) {\n"
            "        Write-Warning \"There is no cache folder at $Cache.\"; "
            "return\n"
            "    }\n"
            "    $name = '{0}.aside-{1:yyyyMMddHHmmss}' -f (Split-Path $Cache "
            "-Leaf), (Get-Date)\n"
            "    Rename-Item -LiteralPath $Cache -NewName $name\n"
            "    Write-Host \"Renamed to $name. Pro builds a new cache when it "
            "next starts.\"\n"
            "    Write-Host \"To undo, close Pro and run: Rename-Item "
            "-LiteralPath '$(Join-Path (Split-Path $Cache) $name)' -NewName "
            "'$(Split-Path $Cache -Leaf)'\"\n"
            "}" % (_ps_quote(paths[0]), _PRO_CLOSED)))
    elif cid == "DL-09":
        out.append((
            "Collect the crash files for a support case",
            "Copies Pro's crash files into one zip on the Desktop, ready to "
            "attach to an Esri support case. The originals are not touched.",
            "& {\n"
            "    $zip = Join-Path ([Environment]::GetFolderPath('Desktop')) "
            "('ArcGISPro-crash-files-{0:yyyyMMdd}.zip' -f (Get-Date))\n"
            "    $dumps = @(Get-ChildItem -LiteralPath (Join-Path "
            "$env:LOCALAPPDATA 'ESRI\\ErrorReports') -Recurse -File -Filter "
            "'*.dmp' -ErrorAction SilentlyContinue) +\n"
            "             @(Get-ChildItem -LiteralPath (Join-Path "
            "$env:LOCALAPPDATA 'CrashDumps') -File -Filter '*ArcGIS*.dmp' "
            "-ErrorAction SilentlyContinue)\n"
            "    if (-not $dumps) { Write-Host 'No crash files found.'; "
            "return }\n"
            "    Compress-Archive -LiteralPath $dumps.FullName "
            "-DestinationPath $zip -Force\n"
            "    Write-Host \"Copied $($dumps.Count) crash file(s) into "
            "$zip\"\n"
            "}"))
    elif cid == "DL-13":
        out.append((
            "Save these events to a spreadsheet",
            "Read-only. Saves the Pro crash and freeze events and unexpected "
            "shutdowns from the last %d days to a CSV file on the Desktop, "
            "for a support case." % EVENT_WINDOW_DAYS,
            "& {\n"
            "    $since = (Get-Date).AddDays(-%d)\n"
            "    $csv = Join-Path ([Environment]::GetFolderPath('Desktop')) "
            "('ArcGISPro-events-{0:yyyyMMdd}.csv' -f (Get-Date))\n"
            "    $app = Get-WinEvent -FilterHashtable @{LogName='Application'; "
            "Id=1000,1002; StartTime=$since} -ErrorAction SilentlyContinue |\n"
            "        Where-Object { $_.Message -match 'ArcGISPro' }\n"
            "    $sys = Get-WinEvent -FilterHashtable @{LogName='System'; "
            "Id=41,6008; StartTime=$since} -ErrorAction SilentlyContinue\n"
            "    @($app) + @($sys) | Sort-Object TimeCreated -Descending |\n"
            "        Select-Object TimeCreated, LogName, Id, ProviderName,\n"
            "            @{n='Message'; e={ ($_.Message -split \"`r?`n\")[0] }} "
            "|\n"
            "        Export-Csv -LiteralPath $csv -NoTypeInformation\n"
            "    Write-Host \"Saved to $csv\"\n"
            "}" % EVENT_WINDOW_DAYS))
    elif cid == "DL-07" and len(paths) > 0:
        folder = ntpath.dirname(paths[0]) or paths[0]
        out.append((
            "Keep the project folder on this computer",
            "Tells OneDrive to download everything in the folder and keep it "
            "here, the same as right-clicking it and choosing \"Always keep "
            "on this device\". A stopgap until the projects move out of "
            "OneDrive.",
            "& {\n"
            "    $Folder = %s\n"
            "    attrib +P -U \"$Folder\"\n"
            "    attrib +P -U \"$Folder\\*\" /S /D\n"
            "    Write-Host \"OneDrive will download and keep everything under "
            "$Folder.\"\n"
            "}" % _ps_quote(folder)))
    elif cid == "PF-15":
        out.append((
            "Switch to the High performance power plan",
            "Changes the active power plan and prints the command to switch "
            "back. If your organisation hides the plan, it says so and "
            "changes nothing.",
            "& {\n"
            "    $before = (powercfg /getactivescheme) -replace "
            "'.*GUID:\\s*([0-9a-fA-F-]+).*', '$1'\n"
            "    powercfg /setactive %s\n"
            "    if ($LASTEXITCODE -ne 0) {\n"
            "        Write-Warning 'The High performance plan is not "
            "available here. Ask IT.'; return\n"
            "    }\n"
            "    powercfg /getactivescheme\n"
            "    Write-Host \"To undo: powercfg /setactive $before\"\n"
            "}" % HIGH_PERFORMANCE_PLAN))
    elif cid == "PF-13":
        out.append((
            "Find the biggest folders in your profile",
            "Read-only. Lists the 15 largest folders under your user profile. "
            "It can take a few minutes on a full disk.",
            "& {\n"
            "    Get-ChildItem -LiteralPath $env:USERPROFILE -Directory -Force "
            "-ErrorAction SilentlyContinue |\n"
            "        ForEach-Object {\n"
            "            $bytes = (Get-ChildItem -LiteralPath $_.FullName "
            "-Recurse -File -Force -ErrorAction SilentlyContinue |\n"
            "                      Measure-Object Length -Sum).Sum\n"
            "            [pscustomobject]@{ Folder = $_.FullName; SizeGB = "
            "[math]::Round($bytes / 1GB, 2) }\n"
            "        } | Sort-Object SizeGB -Descending | Select-Object "
            "-First 15 | Format-Table -AutoSize\n"
            "}"))
        out.append((
            "Open Storage Sense",
            "Opens the Windows settings page that clears temporary files and "
            "the Recycle Bin.",
            "Start-Process ms-settings:storagesense"))
    elif cid == "PF-11":
        out.append((
            "Open Windows graphics settings",
            "Opens the page where ArcGIS Pro can be set to High performance "
            "by hand, if you would rather not use --apply.",
            "Start-Process ms-settings:display-advancedgraphics"))
    return out


_HTML_CSS = """
:root {
  --nycem-blue: #222b4e; --electric-blue: #2b358e; --sky-blue: #b5cbe7;
  --hazard-orange: #f44e1c; --notify-red: #d70010; --ready-yellow: #f4e203;
  --cert-green: #59ad46; --ink: #1b2140; --muted: #4a5170; --line: #d5dbe8;
  --paper: #ffffff; --wash: #f2f5fa;
  --title-font: "Owners", "Aptos Black", "Aptos", "Segoe UI Black",
                "Arial Black", sans-serif;
  --body-font: "Aptos", "Segoe UI", Calibri, Arial, sans-serif;
  --code-font: Consolas, "Cascadia Mono", "Courier New", monospace;
}
* { box-sizing: border-box; }
html { background: var(--wash); }
body { margin: 0; color: var(--ink); background: var(--wash);
       font: 16px/1.5 var(--body-font); }
a { color: var(--electric-blue); }
header { position: relative; background: var(--nycem-blue); color: #fff;
         padding: 28px 16px 26px; overflow: hidden; }
header::after { content: ""; position: absolute; top: 0; right: 32px;
                width: 26px; height: 58px; background: var(--sky-blue); }
.wrap { max-width: 980px; margin: 0 auto; }
.org { margin: 0 0 6px; font: 700 13px/1.3 var(--body-font);
       letter-spacing: .06em; text-transform: uppercase; color: var(--sky-blue); }
h1, h2, h3 { font-family: var(--title-font); font-weight: 900; margin: 0; }
h1 { font-size: 32px; line-height: 1.15; }
.run { margin: 8px 0 0; color: #dfe6f3; font-size: 15px; }
main { padding: 0 16px 48px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
         gap: 12px; margin: 24px 0 12px; }
.tile { background: var(--paper); border: 1px solid var(--line);
        border-top: 6px solid var(--muted); padding: 12px 14px; }
.tile b { display: block; font: 900 30px/1 var(--title-font); }
.tile span { color: var(--muted); font-size: 14px; }
.tile.fail { border-top-color: var(--notify-red); }
.tile.warn { border-top-color: var(--hazard-orange); }
.tile.ok { border-top-color: var(--cert-green); }
.tile.info { border-top-color: var(--sky-blue); }
.verdict { background: var(--paper); border-left: 6px solid var(--electric-blue);
           padding: 14px 16px; margin: 0 0 8px; font-size: 17px; }
.verdict a { font-weight: 700; }
section { margin-top: 36px; }
section > h2 { color: var(--nycem-blue); font-size: 26px;
               padding-bottom: 6px; border-bottom: 3px solid var(--sky-blue);
               margin-bottom: 14px; }
.lede { color: var(--muted); margin: -6px 0 14px; font-style: italic; }
.card { background: var(--paper); border: 1px solid var(--line);
        border-left: 6px solid var(--line); padding: 14px 16px; margin: 0 0 10px; }
.card.fail { border-left-color: var(--notify-red); }
.card.warn { border-left-color: var(--hazard-orange); }
.card.ok { border-left-color: var(--cert-green); }
.card.info { border-left-color: var(--sky-blue); }
.card-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 10px; }
.card h3 { font: 700 18px/1.3 var(--body-font); color: var(--nycem-blue); }
.cid { color: var(--muted); font: 13px var(--code-font); }
.badge { display: inline-block; min-width: 96px; text-align: center;
         padding: 2px 8px; font: 800 12px/1.6 var(--body-font);
         letter-spacing: .05em; border-radius: 3px; }
.badge.fail { background: var(--notify-red); color: #fff; }
.badge.warn { background: var(--ready-yellow); color: var(--nycem-blue); }
.badge.ok { background: var(--cert-green); color: var(--nycem-blue); }
.badge.info { background: var(--sky-blue); color: var(--nycem-blue); }
.badge.skip { background: #e3e6ee; color: var(--nycem-blue); }
.result { margin: 6px 0 0; }
.why { margin: 8px 0 0; }
.why b, .todo b { color: var(--nycem-blue); }
.todo { margin: 8px 0 0; }
details { margin-top: 8px; }
summary { cursor: pointer; color: var(--electric-blue); font-weight: 700;
          font-size: 14px; }
pre { margin: 8px 0 0; padding: 10px 12px; background: var(--wash);
      border: 1px solid var(--line); font: 13px/1.45 var(--code-font);
      white-space: pre-wrap; overflow-wrap: anywhere; }
.kv { margin: 8px 0 0; font-size: 14px; color: var(--muted); }
.steps { list-style: none; margin: 0; padding: 0; counter-reset: none; }
.step { background: var(--paper); border: 1px solid var(--line);
        padding: 16px; margin: 0 0 12px; display: grid;
        grid-template-columns: 44px 1fr; gap: 0 12px; }
.step .num { width: 40px; height: 40px; display: grid; place-items: center;
             background: var(--nycem-blue); color: #fff;
             font: 900 20px/1 var(--title-font); }
.step.dataloss .num { background: var(--notify-red); }
.step h3 { font: 700 18px/1.3 var(--body-font); color: var(--nycem-blue);
           margin-top: 8px; }
.step-body { min-width: 0; }
.group-intro { font: 700 17px/1.4 var(--body-font); color: var(--nycem-blue);
               margin: 18px 0 10px; }
.paths { margin: 10px 0 0; padding: 0; list-style: none; }
.paths li { display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
            margin: 0 0 6px; }
.path { font: 13px/1.4 var(--code-font); background: #eaf0f9;
        border: 1px solid var(--sky-blue); padding: 3px 8px;
        overflow-wrap: anywhere; min-width: 0; }
button.copy, a.open { font: 700 12px/1 var(--body-font); padding: 6px 10px;
        border: 1px solid var(--electric-blue); background: #fff;
        color: var(--electric-blue); cursor: pointer; text-decoration: none;
        border-radius: 3px; }
button.copy:hover, a.open:hover { background: var(--electric-blue); color: #fff; }
button.copy.done { background: var(--cert-green); border-color: var(--cert-green);
                   color: var(--nycem-blue); }
.script { margin: 12px 0 0; border: 1px solid var(--line); }
.script-head { display: flex; flex-wrap: wrap; justify-content: space-between;
               align-items: center; gap: 8px; background: var(--nycem-blue);
               color: #fff; padding: 8px 12px; }
.script-head b { font-size: 15px; }
.script-head button.copy { border-color: #fff; }
.script p { margin: 8px 12px 0; font-size: 14px; color: var(--muted); }
.script pre { margin: 8px 12px 12px; background: #0f1530; color: #e8edf8;
              border: 0; overflow-x: auto; white-space: pre; }
.sub { font: 700 15px/1.4 var(--body-font); color: var(--nycem-blue);
       margin: 14px 0 4px; }
.box { background: var(--paper); border: 1px solid var(--line);
       padding: 12px 16px; margin: 0 0 12px; }
.box ul { margin: 6px 0 0; padding-left: 20px; }
.box li { margin: 0 0 6px; }
.tip { font-size: 14px; color: var(--muted); }
footer { padding: 18px 16px 32px; color: var(--muted); font-size: 13px;
         border-top: 1px solid var(--line); }
@media (max-width: 560px) {
  h1 { font-size: 26px; }
  header::after { right: 16px; width: 18px; height: 40px; }
  .step { grid-template-columns: 1fr; }
  .step .num { margin-bottom: 6px; }
}
@media print {
  html, body { background: #fff; }
  button.copy, a.open { display: none; }
  details { display: block; }
  details > summary { display: none; }
  .card, .step, .script { break-inside: avoid; }
  .script pre { background: #fff; color: #000; border: 1px solid var(--line); }
}
"""

_HTML_JS = """
function copyText(text, button) {
  function done() {
    button.textContent = "Copied";
    button.classList.add("done");
    setTimeout(function () {
      button.textContent = button.getAttribute("data-label");
      button.classList.remove("done");
    }, 1600);
  }
  function fallback() {
    var area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); done(); } catch (e) {}
    document.body.removeChild(area);
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, fallback);
  } else {
    fallback();
  }
}
document.addEventListener("click", function (event) {
  var button = event.target.closest("button.copy");
  if (!button) { return; }
  var source = document.getElementById(button.getAttribute("data-for"));
  if (source) { copyText(source.textContent, button); }
});
"""


def _h(text):
    """Escape text for HTML."""
    return (("%s" % (text if text is not None else ""))
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _file_url(path):
    """A file:/// link for a local or UNC path."""
    if path.startswith("\\\\"):
        url = "file:" + path.replace("\\", "/")
    else:
        url = "file:///" + path.replace("\\", "/")
    return url.replace("%", "%25").replace(" ", "%20").replace("#", "%23")


class _Ids(object):
    """Unique element ids, so each Copy button finds its own text."""

    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return "c%d" % self.n


def _html_paths(paths, ids):
    if not paths:
        return []
    out = ['<ul class="paths">']
    for path in paths:
        eid = ids.next()
        out.append('<li><code class="path" id="%s">%s</code>'
                   '<button class="copy" type="button" data-for="%s" '
                   'data-label="Copy path">Copy path</button>'
                   '<a class="open" href="%s">Open folder</a></li>'
                   % (eid, _h(path), eid, _h(_file_url(folder_of(path)))))
    out.append("</ul>")
    return out


def _html_scripts(scripts, ids):
    out = []
    for title, what, code in scripts:
        eid = ids.next()
        out.append('<div class="script"><div class="script-head">'
                   '<b>%s</b><button class="copy" type="button" '
                   'data-for="%s" data-label="Copy script">Copy script'
                   '</button></div><p>%s</p><pre id="%s">%s</pre></div>'
                   % (_h(title), eid, _h(what), eid, _h(code)))
    return out


def _html_check(check, step, ids):
    css = HTML_STATUS_CLASS[check["status"]]
    flagged = check["status"] in ("Warn", "Fail")
    out = ['<article class="card %s" id="%s">' % (css, _h(check["id"])),
           '<div class="card-head"><span class="badge %s">%s</span>'
           '<h3>%s</h3><span class="cid">%s</span></div>'
           % (css, STATUS_LABELS[check["status"]], _h(check["title"]),
              _h(check["id"]))]
    if check["result"]:
        out.append('<p class="result">%s</p>' % _h(check["result"]))
    if flagged and check["impact"]:
        out.append('<p class="why"><b>Why it matters:</b> %s</p>'
                   % _h(check["impact"]))
    if flagged and step is not None:
        out.append('<p class="todo"><b>What to do:</b> '
                   '<a href="#step-%d">see step %d</a>.</p>' % (step, step))
    found = [l for l in check["observed"].split("\n") if l.strip()]
    extra = []
    if check["impact"] and not flagged:
        extra.append('<p class="kv"><b>Why it matters:</b> %s</p>'
                     % _h(check["impact"]))
    if check["fix"] and step is None:
        extra.append('<p class="kv"><b>What to do:</b> %s</p>'
                     % _h(check["fix"]))
    if check["expected"]:
        extra.append('<p class="kv"><b>Expected:</b> %s</p>'
                     % _h(check["expected"]))
    if found:
        extra.append("<pre>%s</pre>" % _h("\n".join(found)))
    if check["detail"].strip():
        extra.append('<p class="kv">%s</p>'
                     % _h(check["detail"]).replace("\n", "<br>"))
    if extra:
        out.append("<details%s><summary>%s</summary>%s</details>"
                   % (" open" if flagged else "",
                      "What was found" if flagged else "Details",
                      "".join(extra)))
    out.append("</article>")
    return out


def render_html(document):
    """The report as one self-contained HTML page, returned as a string.

    Same content and order as the text report: each check with its result,
    then the numbered steps at the end. The steps add what the text report
    cannot: the folders involved, with a Copy button and a link, and short
    PowerShell scripts for the cleanups a person would otherwise type by hand.
    """
    ids = _Ids()
    steps = next_steps(document)
    step_of = dict([(c["id"], n) for n, c in steps])
    tally = dict(counts(document["checks"]))
    who = "%s UTC, as %s (%s)" % (
        document["taken"], document["user"],
        "as administrator" if document["elevated"] else "not as administrator")
    out = ["<!doctype html>", '<html lang="en">', "<head>",
           '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, '
           'initial-scale=1">',
           "<title>ArcGIS Pro Workstation Check</title>",
           "<style>%s</style>" % _HTML_CSS, "</head>", "<body>",
           '<header><div class="wrap">']
    if HTML_ORG_NAME:
        out.append('<p class="org">%s</p>' % _h(HTML_ORG_NAME))
    out.append("<h1>ArcGIS Pro workstation check</h1>")
    out.append('<p class="run">Run on %s</p>' % _h(who))
    out.append("</div></header>")
    out.append('<main><div class="wrap">')

    out.append('<div class="tiles">')
    for status, word in (("Fail", "problems"), ("Warn", "warnings"),
                         ("Pass", "OK"), ("Info", "for information"),
                         ("Skipped", "not checked")):
        out.append('<div class="tile %s"><b>%d</b><span>%s</span></div>'
                   % (HTML_STATUS_CLASS[status], tally[status], word))
    out.append("</div>")
    if steps:
        verdict = ('%d thing(s) need attention. '
                   '<a href="#next">Go to what to do next</a>.' % len(steps))
    elif exit_code(document["checks"]) == 3:
        verdict = ("Almost nothing could be checked on this machine, so this "
                   "report cannot say whether anything needs fixing. "
                   '<a href="#next">See what could not be checked</a>.')
    else:
        verdict = "Nothing needs fixing based on what could be checked."
    out.append('<p class="verdict">%s</p>' % verdict)

    for heading, lede, wanted in (
            ("Risks to saved edits",
             "Checks for causes of lost or unsaved work.", True),
            ("Performance", "Checks for causes of slowness and freezes.",
             False)):
        out.append("<section><h2>%s</h2><p class=\"lede\">%s</p>"
                   % (heading, lede))
        for check in document["checks"]:
            if bool(check["dataloss"]) is wanted:
                out.extend(_html_check(check, step_of.get(check["id"]), ids))
        out.append("</section>")

    out.append('<section id="next"><h2>What to do next</h2>')
    if not steps:
        out.append('<div class="box"><p>%s</p></div>' % (
            "Almost nothing could be checked on this machine, so this report "
            "cannot say whether anything needs fixing."
            if exit_code(document["checks"]) == 3 else
            "Nothing needs fixing based on what could be checked."))
    for intro, wanted in (("Start with these. They can cause lost edits:",
                           True),
                          ("Then these, which affect speed:", False)):
        group = [(n, c) for n, c in steps if bool(c["dataloss"]) is wanted]
        if not group:
            continue
        out.append('<p class="group-intro">%s</p><ol class="steps">' % intro)
        for n, check in group:
            out.append('<li class="step%s" id="step-%d">'
                       '<span class="num">%d</span><div class="step-body">'
                       '<h3>%s <a class="cid" href="#%s">%s</a></h3>'
                       '<p>%s</p>'
                       % (" dataloss" if wanted else "", n, n,
                          _h(check["title"]), _h(check["id"]),
                          _h(check["id"]), _h(check["fix"])))
            paths = check_paths(check)
            if paths:
                out.append('<p class="sub">Folders involved</p>')
                out.extend(_html_paths(paths, ids))
            scripts = cleanup_scripts(check)
            if scripts:
                out.append('<p class="sub">Scripts that can help</p>')
                out.extend(_html_scripts(scripts, ids))
            out.append("</div></li>")
        out.append("</ol>")
    if any([cleanup_scripts(c) for n, c in steps]):
        out.append('<p class="tip">To run a script: open Windows PowerShell '
                   'from the Start menu (not as administrator), paste it, '
                   'and press Enter. Scripts that change anything check that '
                   'ArcGIS Pro is closed first.</p>')

    if document["fixable"]:
        out.append('<div class="box"><p><b>prostall can make %d of these '
                   'changes for you.</b> Close ArcGIS Pro, then run prostall '
                   'again with <code>--apply</code>:</p><ul>'
                   % len(document["fixable"]))
        for cid in document["fixable"]:
            check = by_id(document["checks"], cid)
            out.append('<li><a href="#%s">%s</a> [%s]</li>'
                       % (_h(cid), _h(check["title"]), _h(cid)))
        out.append("</ul></div>")
    held = _fix_notes(document)
    if held:
        out.append('<div class="box"><p><b>Automatic fixes not done this '
                   'time:</b></p><ul>')
        out.extend(['<li>[%s] %s</li>' % (_h(fid), _h(text))
                    for fid, text in held])
        out.append("</ul></div>")
    if document["applied"]:
        out.append('<div class="box"><p><b>Changed by --apply:</b></p><ul>')
        for record in document["applied"]:
            eid = ids.next()
            out.append('<li>[%s] %s<br>Result: <code>%s</code><br>To undo: '
                       '<code id="%s">%s</code> <button class="copy" '
                       'type="button" data-for="%s" data-label="Copy">Copy'
                       '</button></li>'
                       % (_h(record["id"]), _h(record["what"]),
                          _h(record["result"]), eid, _h(record["undo"]), eid))
        out.append("</ul></div>")
    unsure = [c for c in document["checks"] if undetermined(c) and c["fix"]]
    if unsure:
        out.append('<div class="box"><p><b>These could not be checked.</b> '
                   'Look at them by hand if they might matter:</p><ul>')
        for check in unsure:
            out.append('<li><a href="#%s">%s</a> [%s]: %s</li>'
                       % (_h(check["id"]), _h(check["title"]),
                          _h(check["id"]), _h(check["fix"])))
        out.append("</ul></div>")
    out.append("</section>")
    out.append("</div></main>")
    out.append('<footer><div class="wrap">prostall report format %d. '
               'Check results only: the scripts above run only if you paste '
               'them into PowerShell yourself.</div></footer>'
               % document["prostall"])
    out.append("<script>%s</script>" % _HTML_JS)
    out.append("</body></html>")
    return "\n".join(out) + "\n"


def write_html(document, path):
    """Write the HTML report to one file, creating its folder."""
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        os.makedirs(folder)
    handle = open(path, "wb")
    try:
        handle.write(render_html(document).encode("utf-8"))
    finally:
        handle.close()
    return path


# =============================================================================
# SELF-TEST
# =============================================================================

def _norm(path):
    return (path or "").replace("/", "\\").rstrip("\\").lower()


class FakeEnv(Env):
    """A stand-in workstation. Every read the probes make is answered here.

    This is why the self-test runs identically on Windows and on Linux: no
    probe reaches a registry, a socket, a process table or a disk.
    """

    windows = True

    def __init__(self, reg=None, subkeys=None, tree=None, procs=(),
                 mem=(None, None), disks=None, power=(None, None),
                 tcp_map=None, envvars=None, text=None, user="editor",
                 admin=False, clock=1700000000.0, unreadable=(), ps=None,
                 events=None, listing=None, zips=None, netdrives=()):
        self._reg = dict([(_norm(k), v) for k, v in (reg or {}).items()])
        self._subkeys = dict([(_norm(k), v)
                              for k, v in (subkeys or {}).items()])
        self._tree = dict([(_norm(k), (k, v)) for k, v in (tree or {}).items()])
        self._text = dict([(_norm(k), v) for k, v in (text or {}).items()])
        self._procs = None if procs is None else list(procs)
        self._mem = mem
        self._disks = dict([(_norm(k), v) for k, v in (disks or {}).items()])
        self._power = power
        self._tcp = dict(tcp_map or {})
        self._env = dict(envvars or {})
        self._user = user
        self._admin = admin
        self._clock = clock
        self._unreadable = set([_norm(p) for p in unreadable])
        self._ps = dict(ps or {})
        self._events = dict(events or {})
        self._listing = dict([(_norm(k), v) for k, v in (listing or {}).items()])
        self._zips = dict([(_norm(k), v) for k, v in (zips or {}).items()])
        self._netdrives = list(netdrives)
        self.zip_reads = []
        self.written = []

    def now(self):
        return self._clock

    def getenv(self, name):
        return self._env.get(name)

    def username(self):
        return self._user

    def elevated(self):
        return self._admin

    def reg(self, path, name):
        return self._reg.get(_norm(path), {}).get(name)

    def reg_subkeys(self, path):
        return list(self._subkeys.get(_norm(path), []))

    def reg_write(self, path, name, value):
        self._reg.setdefault(_norm(path), {})[name] = value
        self.written.append((path, name, value))

    def isdir(self, path):
        return _norm(path) in self._tree

    def isfile(self, path):
        parent = _norm(ntpath.dirname(path.replace("/", "\\")))
        leaf = ntpath.basename(path.replace("/", "\\")).lower()
        for name, _size, _age, _attrs, is_dir in self._tree.get(
                parent, ("", []))[1]:
            if name.lower() == leaf and not is_dir:
                return True
        return False

    def subdirs(self, path):
        return sorted([name for name, _s, _a, _t, is_dir
                       in self._tree.get(_norm(path), ("", []))[1] if is_dir])

    def scan(self, root, depth=3, limit=5000):
        entries = []
        unreadable = []
        key = _norm(root)
        if key not in self._tree:
            return entries, unreadable
        queue = [(key, 0)]
        while queue:
            current, level = queue.pop(0)
            if current in self._unreadable:
                unreadable.append("%s: Access is denied" % current)
                continue
            shown, children = self._tree.get(current, (current, []))
            for name, size, age_days, attrs, is_dir in children:
                path = ntpath.join(shown, name)
                entries.append(Entry(path, name, size,
                                     self._clock - age_days * 86400.0, attrs,
                                     is_dir))
                if len(entries) >= limit:
                    return entries, unreadable
                if is_dir and level + 1 < depth:
                    queue.append((_norm(path), level + 1))
        return entries, unreadable

    def read_text(self, path):
        return self._text.get(_norm(path))

    def rename(self, src, dst):
        key = _norm(src)
        self._tree[_norm(dst)] = (dst, self._tree.pop(key)[1])
        self.written.append(("rename", src, dst))

    # Every page a run asked to open, across instances, so a test can see
    # what main() did with an Env it built for itself.
    opened = []

    def open_file(self, path):
        FakeEnv.opened.append(path)
        return None

    def network_drives(self):
        return list(self._netdrives)

    def processes(self, image):
        if self._procs is None:
            return None
        return list(self._procs) if image.lower() == "arcgispro.exe" else []

    def memory(self):
        return self._mem

    def disk(self, root):
        return self._disks.get(_norm(root))

    def power_plan(self):
        return self._power

    def tcp(self, host, port, timeout_ms):
        return self._tcp.get(host, (False, timeout_ms,
                                    "no answer within %d ms" % timeout_ms))

    def _answer(self, key):
        return self._ps.get(key, (None, "not available on the stand-in machine"))

    def defender_preferences(self):
        return self._answer("defender")

    def nic_power(self):
        return self._answer("nic")

    def smb_client(self):
        return self._answer("smb")

    def events(self, log, query, count):
        return self._events.get(log, (None, "no such log on the stand-in "
                                            "machine"))

    def time_listdir(self, path, limit=SHARE_LIST_LIMIT):
        return self._listing.get(_norm(path),
                                 (None, 0, "not reachable on the stand-in "
                                           "machine"))

    def read_zip_json(self, path, max_entries=APRX_MAX_ENTRIES,
                      max_entry=APRX_MAX_ENTRY_BYTES, max_total=APRX_MAX_BYTES):
        self.zip_reads.append(path)
        found = self._zips.get(_norm(path))
        if found is None:
            return [], False, "not a zip on the stand-in machine"
        return found[0], found[1], None


USER_CONFIG_XML = """<?xml version="1.0" encoding="utf-8"?>
<configuration><userSettings><ArcGIS.Desktop.Settings>
  <setting name="CreateProjectBackups" serializeAs="String">
    <value>%s</value></setting>
  <setting name="CreateProjectBackupsIntervalMinutes" serializeAs="String">
    <value>%s</value></setting>
  <setting name="SaveProProjectLimit" serializeAs="String">
    <value>5</value></setting>
  <setting name="StereoscopicMode" serializeAs="String">
    <value>%s</value></setting>
  <setting name="ManageFeatureCaching" serializeAs="String">
    <value>True</value></setting>
  <setting name="IndexNetworkConnections" serializeAs="String">
    <value>%s</value></setting>
</ArcGIS.Desktop.Settings></userSettings></configuration>
"""


def event_xml(*events):
    """A synthetic wevtutil reply. events: (provider, id, when, data, level)."""
    body = []
    for provider, eid, when, data, level in events:
        body.append(
            "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/"
            "event'><System><Provider Name='%s'/><EventID>%d</EventID>"
            "<Level>%d</Level><TimeCreated SystemTime='%s.1234567Z'/>"
            "<Computer>WS-EXAMPLE</Computer></System><EventData>%s"
            "</EventData></Event>"
            % (provider, eid, level, when,
               "".join(["<Data>%s</Data>" % d for d in data])))
    return "<Events>\r\n%s</Events>\r\n" % "".join(body)


QUIET_LOGS = {"Application": (event_xml(), None),
              "System": (event_xml(), None),
              "Microsoft-Windows-SMBClient/Connectivity":
                  (None, "Access is denied.")}


def aprx_texts(home, gdb, rate):
    """The JSON documents inside a synthetic project file."""
    return [("GISProject.json",
             json.dumps({"type": "CIMGISProject", "defaultFolder": home,
                         "defaultGeoDatabase": gdb})),
            ("Map/map.json",
             json.dumps({"type": "CIMMapDocument", "layerDefinitions": [
                 {"type": "CIMFeatureLayer", "name": "Hydrants",
                  "refreshRate": rate,
                  "refreshRateUnit": "esriTimeUnitsSeconds"},
                 {"type": "CIMFeatureLayer", "name": "Streets",
                  "refreshRate": 0}]})),
            ("Index/thumbnail.json", "not json at all")]


def fake_healthy():
    """A workstation with nothing wrong with it."""
    home = r"C:\Users\editor"
    docs = home + r"\Documents"
    projects = docs + r"\ArcGIS\Projects"
    cache = home + r"\AppData\Local\ESRI\Local Caches"
    index = home + r"\AppData\Local\ESRI\Index"
    return FakeEnv(
        envvars={"USERPROFILE": home, "APPDATA": home + r"\AppData\Roaming",
                 "LOCALAPPDATA": home + r"\AppData\Local",
                 "TEMP": home + r"\AppData\Local\Temp",
                 "SystemDrive": "C:", "SystemRoot": r"C:\Windows",
                 "ProgramFiles": r"C:\Program Files"},
        reg={r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders":
                 {"Personal": docs},
             r"HKCU\Software\ESRI\ArcGISPro\Settings":
                 {"UserConfigLocationRoaming": home + r"\user.config",
                  "Cache Path": cache},
             PRO_REG_KEY: {"Version": "3.4", "RealVersion": "3.4.0",
                           "BuildNumber": "55405",
                           "InstallDir": r"C:\Program Files\ArcGIS\Pro"},
             r"HKLM\SOFTWARE\Policies\Microsoft\Windows\NetCache": {"Enabled": 0},
             r"HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps":
                 {"DumpFolder": r"C:\ProgramData\CrashDumps"},
             GPU_PREFERENCE_KEY:
                 {r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe":
                      "GpuPreference=2;"},
             r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\S-1-5-21-1":
                 {"ProfileImagePath": home}},
        subkeys={r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList":
                     ["S-1-5-21-1"]},
        tree={r"C:\Users": [("editor", 0, 0, 0, True),
                            ("Public", 0, 0, 0, True)],
              home: [("Documents", 0, 0, 0, True), ("user.config", 900, 1, 0, False)],
              docs: [("ArcGIS", 0, 0, 0, True)],
              docs + r"\ArcGIS": [("Projects", 0, 0, 0, True)],
              projects: [("Roads.aprx", 240000, 2, 0, False)],
              r"C:\Program Files\ArcGIS\Pro\bin": [("ArcGISPro.exe", 900000, 30, 0, False)],
              cache: [("g1.cache", 1024, 2, 0, False),
                      ("g2.cache", 2048, 3, 0, False)],
              index: [("ResourceIndex", 0, 0, 0, True)],
              index + r"\ResourceIndex": [("seg0", 40960, 2, 0, False)],
              home + r"\AppData\Local\Temp": [],
              home + r"\AppData\Roaming\ESRI": [("settings.xml", 4096, 3, 0, False)]},
        text={home + r"\user.config": USER_CONFIG_XML % ("False", "10", "OFF",
                                                         "False")},
        mem=(32.0, 12.0),
        disks={"c:\\": (500 * 1024 ** 3, 300 * 1024 ** 3)},
        power=(HIGH_PERFORMANCE_PLAN, "High performance"),
        ps={"defender": ({"DisableRealtimeMonitoring": False,
                          "HideExclusionsFromLocalAdmins": False,
                          "ExclusionPath": [r"%ProgramFiles%\ArcGIS\Pro",
                                            r"C:\Users\*\AppData\Local\ESRI",
                                            home + "\\AppData\\Roaming\\ESRI\\"],
                          "ExclusionProcess": ["ArcGISPro.exe"]}, None),
            "nic": ([{"name": "Ethernet", "description": "Example 1GbE",
                      "state": "Disabled"}], None),
            "smb": ({"EnableMultiChannel": True,
                     "RequireSecuritySignature": False,
                     "DirectoryCacheLifetime": 10,
                     "FileInfoCacheLifetime": 10, "SessionTimeout": 60}, None)},
        events=QUIET_LOGS,
        zips={projects + r"\Roads.aprx": (aprx_texts(".", "Roads.gdb", 0),
                                          False)})


def fake_sick():
    """The workstation from the story. Everything that can be wrong, is."""
    home = r"C:\Users\editor"
    docs = home + r"\OneDrive - Example\Documents"
    projects = docs + r"\ArcGIS\Projects"
    cache = home + r"\AppData\Local\ESRI\Local Caches"
    index = home + r"\AppData\Local\ESRI\Index"
    temp = home + r"\AppData\Local\Temp"
    favorites = home + r"\AppData\Roaming\ESRI\ArcGISPro\Favorites"
    return FakeEnv(
        envvars={"USERPROFILE": home, "APPDATA": home + r"\AppData\Roaming",
                 "LOCALAPPDATA": home + r"\AppData\Local",
                 "TEMP": temp, "SystemDrive": "C:", "SystemRoot": r"C:\Windows",
                 "ProgramFiles": r"C:\Program Files",
                 "OneDrive": home + r"\OneDrive - Example"},
        reg={r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders":
                 {"Personal": docs},
             r"HKCU\Software\ESRI\ArcGISPro\Settings":
                 {"UserConfigLocationRoaming": home + r"\user.config",
                  "Cache Path": cache},
             PRO_REG_KEY: {"Version": "3.4",
                           "InstallDir": r"C:\Program Files\ArcGIS\Pro"},
             r"HKLM\SOFTWARE\Policies\Microsoft\Windows\NetCache": {"Enabled": 1},
             r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\S-1-5-21-1":
                 {"ProfileImagePath": "%USERPROFILE%",
                  "CentralProfile": r"\\server\profiles\editor"}},
        subkeys={r"HKCU\SOFTWARE\Microsoft\OneDrive\Accounts": ["Business1"],
                 r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList":
                     ["S-1-5-21-1"]},
        tree={r"C:\Users": [("editor", 0, 0, 0, True)],
              home: [("user.config", 900, 1, 0, False)],
              docs: [("ArcGIS", 0, 0, 0, True)],
              docs + r"\ArcGIS": [("Projects", 0, 0, 0, True)],
              projects: [("Addresses.aprx", 240000, 1, 0, False),
                         ("Addresses.gdb", 0,
                          1, FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS, True)],
              cache: [("g%d.cache" % n, 4096, 700, 0, False) for n in range(14)],
              index: [("ResourceIndex", 0, 0, 0, True),
                      ("Connections", 0, 0, 0, True)],
              index + r"\ResourceIndex": [("seg0", 400000, 400, 0, False)],
              index + r"\Connections": [("c%d.sde" % n, 800, 30, 0, False)
                                        for n in range(12)]
                                       + [("c0.sdeold", 800, 30, 0, False)],
              favorites: [("prod.sde", 900, 40, 0, False)],
              temp: [("ArcGISProTemp12345", 0, 0, 0, True),
                     ("ArcGISProTemp67890", 0, 0, 0, True),
                     ("ArcGISProTemp24680", 0, 0, 0, True)],
              temp + r"\ArcGISProTemp12345": [("scratch.aprx", 180000, 20, 0, False)],
              # a bare header, which is a husk of a session and not work
              temp + r"\ArcGISProTemp67890": [("a00000001.gdbtable", 40, 20, 0, False)],
              # rows in it, plus a subfolder for the walk to step through
              temp + r"\ArcGISProTemp24680": [("Default.gdb", 0, 21, 0, True)],
              temp + r"\ArcGISProTemp24680\Default.gdb":
                  [("a00000002.gdbtable", 250000, 21, 0, False),
                   ("gdb", 0, 21, 0, True)],
              home + r"\AppData\Local\ESRI\ErrorReports":
                  [("ArcGISPro_2026.dmp", 90 * 1024 ** 2, 3, 0, False),
                   ("ArcGISPro_2025.dmp", 40 * 1024 ** 2, 300, 0, False),
                   ("readme.txt", 40, 3, 0, False)],
              home + r"\AppData\Roaming\ESRI":
                  [("settings.xml", 900 * 1024 ** 2, 3, 0, False)],
              r"C:\Program Files\ArcGIS\Pro\bin":
                  [("ArcGISPro.exe", 900000, 30, 0, False)]},
        text={home + r"\user.config": USER_CONFIG_XML % ("True", "3", "FULL",
                                                         "True")},
        procs=["ArcGISPro.exe 9120 Console 1 2,100,480 K"],
        mem=(8.0, 1.0),
        disks={"c:\\": (500 * 1024 ** 3, 5 * 1024 ** 3)},
        power=("381b4222-f694-41f0-9685-ff5bb260df2e", "Balanced"),
        tcp_map={"gisdb": (False, 4000, "no answer within 4000 ms"),
                 "portal.example.org": (True, 2400, None)},
        ps={"defender": ({"DisableRealtimeMonitoring": False,
                          "ExclusionPath": [r"%APPDATA%\ESRI",
                                            r"%ProgramFiles%\ArcGIS\Pro"],
                          "ExclusionProcess": []}, None),
            "nic": ([{"name": "Wi-Fi", "description": "Example wireless",
                      "state": "Enabled"},
                     {"name": "Dock", "description": "Example USB dock",
                      "state": None}], None),
            "smb": ({"EnableMultiChannel": False,
                     "RequireSecuritySignature": True,
                     "DirectoryCacheLifetime": 10,
                     "FileInfoCacheLifetime": 10, "SessionTimeout": 60}, None)},
        events={"Application": (event_xml(
                    ("Application Hang", 1002, "2026-09-02T14:31:07",
                     ["ArcGISPro.exe", "3.4.0.55405"], 2),
                    ("Application Error", 1000, "2026-09-01T09:12:44",
                     ["python.exe", "3.13.0"], 2),
                    ("Microsoft-Windows-Winlogon", 1002, "2026-09-01T08:00:00",
                     [r"C:\Windows\system32\userinit.exe"], 4)), None),
                "System": (event_xml(
                    ("Microsoft-Windows-Kernel-Power", 41,
                     "2026-09-02T14:40:02", ["0"], 1)), None),
                "Microsoft-Windows-SMBClient/Connectivity": (event_xml(
                    ("Microsoft-Windows-SMBClient", 30999,
                     "2026-09-02T14:29:55", [r"\\files\gis"], 3)), None)},
        zips={projects + r"\Addresses.aprx":
                  (aprx_texts(".", "Addresses.gdb", 30), False)})


def fake_bare():
    """A machine where nothing can be read. Every check must stay undetermined."""
    return FakeEnv(envvars={}, tree={})


DEFAULT_OPTS = {"sde_server": None, "sde_port": 1433, "portal": None,
                "timeout": 4000, "apply": False}


def opts_with(**kwargs):
    merged = dict(DEFAULT_OPTS)
    merged.update(kwargs)
    return merged


def summarize(passed, failed):
    """The self-test footer as (lines, exit code)."""
    total = passed + len(failed)
    if failed:
        return (["%d assertions, %d failed" % (total, len(failed))]
                + ["  FAILED: %s" % item for item in failed]), 1
    return ["%d assertions, 0 failed" % total], 0


def self_test():
    """Assertions over the whole diagnostic. No registry, no socket, no disk."""
    import io
    import shutil
    import tempfile

    passed = [0]
    failed = []

    def check(cond, label):
        if cond:
            passed[0] += 1
            print("PASS  %s" % label)
        else:
            failed.append(label)
            print("FAIL  %s" % label)

    def raises(fn, label, kind=ValueError):
        try:
            fn()
        except kind:
            check(True, label)
        except Exception as exc:
            check(False, "%s (wrong exception %r)" % (label, exc))
        else:
            check(False, "%s (no error raised)" % label)

    def rejects(argv, label):
        """An argument list argparse must refuse.

        argparse prints its usage to stderr on the way out, which would
        otherwise land in the middle of the self-test output.
        """
        real_stderr = sys.stderr
        sys.stderr = io.StringIO()
        try:
            _parse(argv)
        except SystemExit:
            check(True, label)
        else:
            check(False, "%s (argparse accepted it)" % label)
        finally:
            sys.stderr = real_stderr

    good = dict(cid="X-1", category="Test", title="t", status="Pass",
                observed="measured 3 files")
    unmeasured = "Not observable from the workstation. Re-run against the server."

    # ------------------------------------------------- the status contract
    print("-" * 70)
    print("the status contract: a verdict needs a measurement")
    for status in STATUSES:
        check(make_check(**dict(good, status=status))["status"] == status,
              "%s is an accepted status" % status)
    raises(lambda: make_check(**dict(good, status="Unknown")),
           "a sixth status is refused, which is what lets the summary be summed")
    raises(lambda: make_check(**dict(good, cid="")),
           "a check with no id is refused")
    raises(lambda: make_check(**dict(good, category="")),
           "a check with no category is refused")
    raises(lambda: make_check(**dict(good, title="")),
           "a check with no title is refused")

    for status in DETERMINED:
        raises(lambda s=status: make_check(**dict(good, status=s,
                                                  observed=unmeasured)),
               "a check saying it is not observable from the workstation "
               "cannot carry %s  <-- pinned defect" % status,
               kind=UnmeasuredFinding)
    for status in UNDETERMINED:
        check(make_check(**dict(good, status=status,
                                observed=unmeasured))["status"] == status,
              "the same text is fine on %s, which claims nothing" % status)
    raises(lambda: make_check(**dict(good, status="Fail", measured=False)),
           "measured=False refuses a verdict even when the text reads clean  "
           "<-- pinned defect", kind=UnmeasuredFinding)
    check(make_check(**dict(good, status="Info", measured=False))["status"]
          == "Info", "measured=False is fine on Info")
    raises(lambda: make_check(**dict(good, status="Warn", autofixable=True,
                                     fix="   ")),
           "a fixable flag with no fix text is refused, because the summary "
           "offers it to the operator by id")
    check(make_check(**dict(good, status="Warn", autofixable=True,
                            fix="do this"))["autofixable"],
          "a fixable flag with a fix is accepted")
    check(make_check(**dict(good, observed=["a", "b"]))["observed"] == "a\nb",
          "an observed list becomes one newline-joined string")
    check(make_check(**dict(good, detail=("x", "y")))["detail"] == "x\ny",
          "a detail tuple is joined the same way")
    check(make_check(**dict(good, observed=None))["observed"] == "",
          "a missing observed becomes an empty string, not the word None")

    for phrase in NOT_MEASURED:
        check(says_unmeasured("something " + phrase.upper() + " here"),
              "%r is recognised whatever its case" % phrase)
    check(not says_unmeasured("StereoscopicMode = <absent>"),
          "an absent setting is not an unmeasured check: a settings list full "
          "of <absent> can still carry a real verdict  <-- pinned defect")
    check(not says_unmeasured(""), "empty text claims nothing either way")
    check(not says_unmeasured(None), "no text at all does not raise")

    # ---------------------------------------------------------- containment
    print("-" * 70)
    print("containment: a probe that dies is skipped, never warned")

    def boom(target):
        raise RuntimeError("the registry provider died")

    kept = []
    safe_check(kept, "Z-1", "Test", "t", boom, dataloss=True)
    check(len(kept) == 1, "a body that throws records exactly one check")
    check(kept[0]["status"] == "Skipped",
          "a body that throws records Skipped, not Warn: every fix gates on "
          "Warn or Fail, so Warn would let a fix fire on a determination that "
          "was never made  <-- pinned defect")
    check("RuntimeError" in kept[0]["observed"],
          "the skipped record names the exception, so the operator sees which "
          "probe died")
    check(kept[0]["dataloss"] is True,
          "a data-loss check that dies stays flagged as one")

    quiet = []
    safe_check(quiet, "Z-2", "Test", "t", lambda target: None)
    check(len(quiet) == 1 and quiet[0]["status"] == "Skipped",
          "a body that emits nothing is Skipped, because saying nothing reads "
          "as saying Pass  <-- pinned defect")

    def half(target):
        add_check(target, cid="Z-3a", category="Test", title="t",
                  status="Pass", observed="measured")
        raise IOError("died after the first record")

    partial = []
    safe_check(partial, "Z-3", "Test", "t", half)
    check(len(partial) == 2, "a body that dies after one record keeps it")
    check(partial[0]["status"] == "Pass" and partial[1]["status"] == "Skipped",
          "the record it managed to measure survives, and the failure is "
          "recorded beside it")

    def lies(target):
        add_check(target, cid="Z-4a", category="Test", title="t",
                  status="Fail", observed=unmeasured)

    caught = []
    safe_check(caught, "Z-4", "Test", "t", lies)
    check(len(caught) == 1 and caught[0]["status"] == "Skipped",
          "a body that pairs a verdict with no measurement is contained as "
          "Skipped, not a traceback  <-- pinned defect")
    check("UnmeasuredFinding" in caught[0]["observed"],
          "and the report names the refusal, so the bug is visible in the run")

    # -------------------------------------------------------------- roll-ups
    print("-" * 70)
    print("the act-first roll-up owns no id list")
    sample = [
        make_check("A-1", "c", "t", "Fail", "measured", dataloss=True),
        make_check("A-2", "c", "t", "Warn", "measured", dataloss=True),
        make_check("A-3", "c", "t", "Pass", "measured", dataloss=True),
        make_check("A-4", "c", "t", "Info", "measured", dataloss=True),
        make_check("A-5", "c", "t", "Skipped", "measured", dataloss=True),
        make_check("A-6", "c", "t", "Fail", "measured"),
    ]
    ids = [c["id"] for c in act_first(sample)]
    check(ids == ["A-1", "A-2"],
          "act-first is exactly the data-loss checks that failed or warned")
    check("A-4" not in ids,
          "a data-loss check demoted to Info is not promoted back by a "
          "hardcoded id list: a roll-up that keeps a finding the check itself "
          "withdrew is a verdict nobody measured  <-- pinned defect")
    check("A-5" not in ids, "and a skipped one is not promoted either")
    check("A-6" not in ids,
          "a failing check that does not bear on data loss stays out")
    check([c["id"] for c in act_first(sample[::-1])] == ["A-2", "A-1"],
          "act-first preserves the order of the run, which is the order the "
          "report prints")
    check(act_first([]) == [], "an empty run has nothing to act on")

    fixes = [make_check("B-1", "c", "t", "Warn", "measured", fix="f",
                        autofixable=True),
             make_check("B-2", "c", "t", "Pass", "measured", fix="f",
                        autofixable=True),
             make_check("B-3", "c", "t", "Fail", "measured")]
    check([c["id"] for c in fixable(fixes)] == ["B-1"],
          "a fix is only offered for a check that is actually failing")
    check(dict(counts(sample))["Fail"] == 2, "the tally counts both failures")
    check(dict(counts(sample))["Skipped"] == 1, "and the skipped one")
    check([n for _s, n in counts([])] == [0, 0, 0, 0, 0],
          "an empty run tallies five zeroes, not an empty table")
    check([s for s, _n in counts([])]
          == ["Fail", "Warn", "Pass", "Info", "Skipped"],
          "and it is ordered worst first, which is the order the summary "
          "prints: a tally led by Pass buries the finding under the count of "
          "everything that was fine  <-- pinned defect")
    check(by_id(sample, "A-3")["status"] == "Pass", "by_id finds a record")
    check(by_id(sample, "nope") is None, "and returns None for a stranger")
    twice = [make_check("A-9", "c", "t", "Fail", "the record the check made"),
             make_check("A-9", "c", "t", "Pass", "a later stray of the same id")]
    check(by_id(twice, "A-9")["status"] == "Fail",
          "two records under one id answer with the first, not the last: the "
          "apply gate reads by id, and answering with a later Pass would let "
          "a fix run against a failure nothing withdrew  <-- pinned defect")
    check(exit_code(sample) == 2, "a data-loss finding exits 2")
    check(exit_code([sample[5]]) == 1, "a performance finding alone exits 1")
    check(exit_code([sample[2]]) == 0, "a clean run exits 0")
    check(exit_code([sample[4]]) == 3,
          "a run whose only record is Skipped exits 3, not 0: a wrapper "
          "reading the code cannot see the stderr note, and 0 told it the "
          "machine was fine when nothing was read  <-- pinned defect")
    check(exit_code([]) == 3, "and so does a run with no record at all")
    always = [make_check("DL-00", "c", "t", "Pass", "measured", dataloss=True),
              make_check("DL-12", "c", "t", "Pass", "measured", dataloss=True)]
    check(exit_code(always) == 3,
          "the two checks that answer on a machine that read nothing else do "
          "not make a run determined  <-- pinned defect")
    check(exit_code(always + [sample[2]]) == 0,
          "one more Pass from any other check does")
    check(exit_code(always + [sample[5]]) == 1,
          "and a finding outranks the undetermined code")

    # --------------------------------------------------------- path handling
    print("-" * 70)
    print("path handling answers for the path, not for the host")
    check(is_network_path(r"\\server\share\Roads.aprx"),
          "a UNC path is a network path")
    check(is_network_path(r"Z:\Roads.aprx", ["Z:"]),
          "a mapped drive letter is a network path when it is mapped")
    check(not is_network_path(r"Z:\Roads.aprx"),
          "and is not one when nothing says it is mapped")
    check(not is_network_path(r"C:\GIS\Roads.aprx", ["Z:"]),
          "a local drive is not a network path")
    check(not is_network_path(""), "an empty path is not a network path")
    check(not is_network_path("   "), "and neither is whitespace")
    check(path_root(r"C:\GIS\Roads.aprx") == "C:",
          "a Windows drive is recognised on THIS host, whichever host it is: "
          "os.path is the module the host happens to run, and on Linux it "
          "finds no drive in a Windows path at all  <-- pinned defect")
    check(path_root(r"\\server\share\x") == r"\\server\share",
          "a UNC share root is the server and the share")
    check(path_root("") == "", "an empty path has no root")
    check(is_onedrive_path(r"C:\Users\ed\OneDrive - Example\Projects\a.aprx"),
          "a Known Folder Move root is matched by its name segment, because "
          "the OneDrive variable is unset in an elevated shell  <-- pinned "
          "defect")
    check(is_onedrive_path(r"C:\Users\ed\OneDrive\a.aprx"),
          "a plain OneDrive folder matches too")
    check(is_onedrive_path(r"D:\sync\a.aprx", [r"D:\sync"]),
          "a configured root matches by prefix")
    check(not is_onedrive_path(r"C:\Users\ed\OneDriveRelics\a.aprx"),
          "a folder that merely starts with the word does not match")
    check(not is_onedrive_path(""), "an empty path is not in OneDrive")
    check(path_class(r"\\server\OneDrive - Example\a.aprx") == "NETWORK",
          "network beats OneDrive: the share latency is the problem to fix "
          "first, whether or not the folder also syncs")
    check(path_class(r"C:\Users\ed\OneDrive - Example\a.aprx") == "ONEDRIVE",
          "a synced local folder is ONEDRIVE")
    check(path_class(r"C:\GIS\a.aprx") == "LOCAL", "and a plain one is LOCAL")
    check(path_class("C:/GIS/a.aprx") == "LOCAL",
          "forward slashes are normalised, because a shell folder value can "
          "carry either separator")

    check(format_bytes(0) == "0 B", "zero bytes prints as bytes")
    check(format_bytes(2048) == "2.00 KB", "kilobytes")
    check(format_bytes(5 * 1024 ** 2) == "5.00 MB", "megabytes")
    check(format_bytes(3 * 1024 ** 3) == "3.00 GB", "gigabytes")
    check(format_bytes(1023) == "1023 B", "just under a kilobyte stays bytes")
    check(format_bytes(1024) == "1.00 KB",
          "and exactly a kilobyte is one: the unit boundary is inclusive, so "
          "no size falls through and prints in the unit below itself  "
          "<-- pinned defect")
    check(format_bytes(1024 ** 2) == "1.00 MB", "exactly a megabyte the same")
    check(format_bytes(1024 ** 3) == "1.00 GB", "and exactly a gigabyte")

    check(expand_vars(fake_healthy(), r"%USERPROFILE%\Documents")
          == r"C:\Users\editor\Documents",
          "a percent variable is expanded from the environment this run reads, "
          "never from the host process: on Linux the standard expander leaves "
          "%NAME% untouched and the check would read the wrong folder  "
          "<-- pinned defect")
    check(expand_vars(fake_healthy(), "%NOPE%\\x") == "%NOPE%\\x",
          "an unset variable is left alone rather than becoming empty, so the "
          "report shows what was actually configured")
    check(expand_vars(fake_healthy(), "") == "", "empty text expands to empty")

    # ------------------------------------------------------- user.config
    print("-" * 70)
    print("reading the settings file")
    settings = parse_user_config(USER_CONFIG_XML % ("True", "3", "OFF", "False"))
    check(settings["CreateProjectBackups"] == "True", "a setting is read")
    check(settings["CreateProjectBackupsIntervalMinutes"] == "3",
          "and so is its neighbour")
    check(parse_user_config("") == {}, "no text is an empty mapping")
    check(parse_user_config("<configuration>") == {},
          "malformed XML is a missing answer, not a traceback")
    check(parse_user_config(
        "<c><setting name='A'><value>1</value></setting>"
        "<setting name='A'><value>2</value></setting></c>")["A"] == "1",
        "the first value wins, because a later duplicate is a merge artefact")
    check(parse_user_config("<c><setting name='A'/></c>")["A"] == "",
          "a setting with no value node reads as empty, not as absent")
    check(parse_user_config("<c><setting><value>1</value></setting></c>") == {},
          "a setting with no name is skipped")
    check(cfg(settings, "nope") is None, "an absent setting is None")
    check(cfg(None, "x") is None, "and so is any setting of nothing")
    check(cfg_line(settings, "nope") == "nope = <absent>",
          "an absent setting prints as absent rather than as None")
    check(cfg_line(settings, "CreateProjectBackups")
          == "CreateProjectBackups = True", "a present one prints its value")

    big, stale, ratio = cache_triggers(0, 14, 700.0, 14)
    check(stale and not big,
          "fourteen files of which all fourteen are orphaned is stale: the "
          "absolute count this replaced was dead code on a folder Pro keeps "
          "under fifty files, and reported PASS on exactly the state its own "
          "expected text calls the failure  <-- pinned defect")
    check(abs(ratio - 1.0) < 1e-9, "and the stale fraction is reported as 100%")
    check(cache_triggers(0, 100, 1.0, 50)[1] is False,
          "half stale is not stale: the trigger is proportional, not a count")
    check(cache_triggers(0, 100, 1.0, 85)[1] is True,
          "eighty-five percent stale is")
    check(cache_triggers(0, 3, 400.0, 0)[1] is True,
          "and an old folder with nothing stale in it still trips the age "
          "backstop")
    check(cache_triggers(2 * 1024 ** 3, 1, 1.0, 0)[0] is True,
          "two gigabytes is big by size")
    check(cache_triggers(2 * 1024 ** 3 - 1, 1, 1.0, 0)[0] is False,
          "one byte under it is not")
    check(cache_triggers(0, 20000, 1.0, 0)[0] is True,
          "and twenty thousand files is big by count")
    check(cache_triggers(0, 19999, 1.0, 0)[0] is False,
          "one file under it is not: both size triggers are pinned to their "
          "numbers, so reading the constant back would not prove them")
    check(cache_triggers(0, 10, 90.0, 0)[1] is False,
          "ninety days old is not yet old enough to trip the age backstop")
    check(cache_triggers(0, 10, 91.0, 0)[1] is True,
          "ninety-one days is: the backstop is ninety days, and it is what "
          "catches a folder too small to trip either size trigger")
    check(cache_triggers(0, 0, None, 0) == (False, False, 0.0),
          "an empty folder divides by nothing and triggers nothing")
    cache_root = r"C:\Users\editor\AppData\Local\ESRI\Local Caches"
    aged_cache = probe_caches(
        FakeEnv(envvars={"LOCALAPPDATA": r"C:\Users\editor\AppData\Local"},
                tree={cache_root: [("new.cache", 1024, 20, 0, False),
                                   ("old.cache", 1024, 40, 0, False),
                                   ("old.log", 1024, 40, 0, False)]}), {})
    check("1 cache file(s) older than 30 days"
          in _join(aged_cache["observed"]),
          "of two cache files aged twenty and forty days one is stale: the "
          "window is thirty days, and the forty-day log sitting beside them "
          "is not counted because it is not a cache file  <-- pinned defect")

    # ------------------------------------------------------- the decisions
    print("-" * 70)
    print("every decision, on both sides of its branch")
    check(decide_session("editor", [], False)["status"] == "Pass",
          "one profile with Pro data, and it is the one running: Pass")
    wrong = decide_session("helpdesk", ["editor"], True)
    check(wrong["status"] == "Fail",
          "a second profile carrying Pro data fails the session check, because "
          "every per-user result below it read the wrong hive")
    check("editor" in wrong["fix"], "and the fix names the profile to re-run in")

    check(decide_backup(None, {}, "LOCAL")["status"] == "Info",
          "no settings file: the backup interval is undetermined, not healthy")
    check(decide_backup("c", {"CreateProjectBackups": "True",
                              "CreateProjectBackupsIntervalMinutes": "3"},
                        "ONEDRIVE")["status"] == "Warn",
          "a three-minute backup onto synced storage is the stall the editor "
          "calls a refresh")
    check(decide_backup("c", {"CreateProjectBackups": "True",
                              "CreateProjectBackupsIntervalMinutes": "3"},
                        "LOCAL")["status"] == "Info",
          "the same interval on a local disk is a setting, not a finding")
    check(decide_backup("c", {"CreateProjectBackups": "True",
                              "CreateProjectBackupsIntervalMinutes": "notanumber"},
                        "ONEDRIVE")["status"] == "Info",
          "an interval that is not a number does not crash and does not warn")
    check("does not protect" in decide_backup("c", {}, "LOCAL")["impact"],
          "and the impact line says plainly that this does not protect "
          "the editor's feature edits, which is the whole reason the check is named that way")

    check(decide_storage([])["status"] == "Info",
          "no recent project: where the editor works is undetermined")
    local_only = decide_storage([(r"C:\GIS\a.aprx", "2026-01-01 00:00", "LOCAL")])
    check(local_only["status"] == "Pass", "every recent project local: Pass")
    check("every recent project is stored on this computer"
          in local_only["result"].lower(),
          "and the result says so, instead of printing the accusation with a "
          "PASS in front of it  <-- pinned defect")
    remote = decide_storage([(r"\\srv\s\a.aprx", "2026-01-01 00:00", "NETWORK"),
                             (r"C:\GIS\b.aprx", "2026-01-01 00:00", "LOCAL")])
    check(remote["status"] == "Fail", "one project on a share fails the check")
    check("remote=1 of 2" in remote["detail"], "and the detail counts them")

    check(decide_kfm(None, None, [])["status"] == "Info",
          "an unreadable Documents path leaves redirection undetermined")
    check(decide_kfm(r"C:\Users\ed\Documents", None, [])["status"] == "Pass",
          "a plain Documents folder is not redirected")
    kfm = decide_kfm(r"C:\Users\ed\OneDrive - Example\Documents", None,
                     ["KFM Business1: FoldersProtectedNow = 7 (bitmask, not "
                      "decoded); ProtectedOnce = 7"])
    check(kfm["status"] == "Fail", "Documents inside OneDrive fails")
    check("not decoded" in _join(kfm["observed"]),
          "and the bitmask is printed undecoded, because the value most guides "
          "name does not exist on current builds and contradicted the finding")

    check(decide_placeholders([], 0, [])["status"] == "Info",
          "no project folder: placeholder state is undetermined")
    check(decide_placeholders(["r"], 40, [])["status"] == "Pass",
          "forty entries inspected and none dehydrated: Pass")
    check(decide_placeholders(["r"], 40, ["x"])["status"] == "Warn",
          "one dehydrated item warns")

    check(decide_offline_files(None)["status"] == "Info",
          "no Offline Files policy value: undetermined, and deliberately NOT "
          "inferred from the service start value, which is boot-started on "
          "stock Windows either way  <-- pinned defect")
    check(decide_offline_files(0)["status"] == "Pass",
          "the policy says the cache is off")
    check(decide_offline_files(1)["status"] == "Warn", "and on, it warns")

    check(decide_crash([], 0, ["wer"], False)["status"] == "Info",
          "no crash dump is Info, never Pass: a hang ended from Task Manager "
          "writes no dump at all, so no dump is absence of evidence  "
          "<-- pinned defect")
    check("no dump collection is configured"
          in decide_crash([], 0, ["wer"], False)["fix"].lower(),
          "and the fix says to turn collection on before the next hang")
    check("keep it" in decide_crash([], 0, ["wer"], True)["fix"],
          "when collection is already on, the fix says to leave it alone")
    check(decide_crash(["a dump"], 0, [], True)["status"] == "Info",
          "an old dump is history")
    check(decide_crash(["a dump"], 1, [], True)["status"] == "Warn",
          "a recent one warns")

    skipped = decide_tcp("T-1", "Network", "t", None, 1433, None, 250, "i", "f")
    check(skipped["status"] == "Skipped",
          "no host given: the probe is skipped, because network probes are "
          "opt-in and an unsent packet measures nothing")
    check(decide_tcp("T-1", "Network", "t", "h", 1433, (True, 10, None), 250,
                     "i", "f")["status"] == "Pass", "a fast connect passes")
    check(decide_tcp("T-1", "Network", "t", "h", 1433, (True, 900, None), 250,
                     "i", "f")["status"] == "Warn", "a slow one warns")
    closed = decide_tcp("T-1", "Network", "t", "h", 1433,
                        (False, 4000, "no answer within 4000 ms"), 250, "i", "f")
    check(closed["status"] == "Fail", "a closed port fails")
    check("no answer within 4000 ms" in closed["observed"],
          "and the reason is in the report, not only in the verdict")
    fast_db = probe_sde_reach(FakeEnv(tcp_map={"db": (True, 200, None)}), {},
                              opts_with(sde_server="db"))
    slow_db = probe_sde_reach(FakeEnv(tcp_map={"db": (True, 300, None)}), {},
                              opts_with(sde_server="db"))
    check(fast_db["status"] == "Pass",
          "a database host answering in 200 ms passes")
    check(slow_db["status"] == "Warn",
          "and one answering in 300 ms warns: the database allowance is 250 "
          "ms, because a LAN SQL host answers far inside that  "
          "<-- pinned defect")
    fast_portal = probe_portal(FakeEnv(tcp_map={"p": (True, 800, None)}), {},
                               opts_with(portal="p"))
    slow_portal = probe_portal(FakeEnv(tcp_map={"p": (True, 1600, None)}), {},
                               opts_with(portal="p"))
    check(fast_portal["status"] == "Pass",
          "a portal answering in 800 ms passes, though the same 800 ms would "
          "warn against the database: the portal is allowed to be slower, and "
          "each probe carries its own allowance  <-- pinned defect")
    check(slow_portal["status"] == "Warn",
          "and 1600 ms warns: the portal allowance is 1500 ms")

    check(decide_sde_files([], 0)["status"] == "Info",
          "no connection file is not a finding")
    check(decide_sde_files([(r"C:\c.sde", "LOCAL")], 1)["status"] == "Info",
          "one local connection file is not a finding either")
    check(decide_sde_files([(r"\\s\c.sde", "NETWORK")], 0)["status"] == "Warn",
          "a connection file on a share warns")
    check(decide_sde_files([(r"C:\c.sde", "LOCAL")], 12)["status"] == "Warn",
          "and so does a search index caching a dozen copies of them")
    check("never open or copy" in decide_sde_files([(r"C:\c.sde", "LOCAL")],
                                                   12)["fix"].lower(),
          "the fix says never to open a connection file, because they carry "
          "saved credentials and this tool only ever records their paths")

    check(decide_pro_running([])["status"] == "Pass", "Pro closed: Pass")
    check(decide_pro_running(["ArcGISPro.exe 9120"])["status"] == "Warn",
          "Pro running during the diagnostic warns, and blocks the cache fix")

    check(decide_protemp([], "T")["status"] == "Info",
          "no leftover session folder is no recovery lead")
    husk = decide_protemp([("A", "2026-01-01 00:00", 10, 0, 0)], "T")
    check(husk["status"] == "Info",
          "an empty leftover folder is a husk, not a finding")
    work = decide_protemp([("A", "2026-01-01 00:00", 10, 1, 0)], "T")
    check(work["status"] == "Warn",
          "a leftover folder holding a project file warns, because it is a "
          "recovery lead for the vanished edits and not disk churn  "
          "<-- pinned defect")
    check("MAY HOLD UNSAVED WORK" in _join(work["observed"]),
          "and the line that holds it is marked, so it is inspected first")
    check("Inspect first: A" in work["detail"],
          "the detail names the folder to open before anything is deleted")
    check(decide_protemp([("F%d" % n, "2026-01-01 00:00", 1, 0, 0)
                          for n in range(25)], "T")["status"] == "Warn",
          "twenty-five empty session folders warn on their own, as churn")
    check(decide_protemp([("F%d" % n, "2026-01-01 00:00", 1, 0, 0)
                          for n in range(24)], "T")["status"] == "Info",
          "twenty-four stay Info: none of them holds work, and a count alone "
          "below the threshold is history rather than a finding")
    many = decide_protemp([("F%d" % n, "2026-01-01 00:00", 1, 0, 0)
                           for n in range(40)], "T")
    check("10 more folder(s) not listed" in _join(many["observed"]),
          "and a long list is truncated with a count of what it left out")

    check(decide_pro_version(None, None, None, None, None)["status"] == "Info",
          "no Pro install found: undetermined, and no verdict on a version "
          "nobody read")
    check(decide_pro_version("3.4", "3.4.0", "5", "d", "e")["status"] == "Info",
          "a version is a fact, so this check never carries a verdict")

    check(decide_rendering(None, {})["status"] == "Info",
          "no settings file: the display settings are undetermined")
    check(decide_rendering("c", {})["status"] == "Info",
          "settings read, nothing alarming in them")
    check(decide_rendering("c", {"StereoscopicMode": "FULL"})["status"] == "Warn",
          "stereoscopic mode left on warns")
    check("<absent>" in "\n".join(decide_rendering("c", {})["observed"]),
          "and every setting it looked for is listed, present or not, so the "
          "report can be diffed against a workstation that is known to be fast")

    check(decide_caches("p", False, 0, 0, "n/a", None, 0)["status"] == "Info",
          "no cache folder is not a finding")
    check(decide_caches("p", True, 1024, 3, "s", 2.0, 0)["status"] == "Pass",
          "a small fresh cache passes")
    stale_cache = decide_caches("p", True, 4096 * 14, 14, "s", 700.0, 14)
    check(stale_cache["status"] == "Warn",
          "fourteen orphaned cache files warn  <-- pinned defect")
    check(stale_cache["autofixable"] is True, "and the cache has a fix here")
    check("never deletes" in stale_cache["fix"],
          "which renames the folder aside and prints the undo, because a "
          "diagnostic that deletes is how the last copy of something goes")

    check(decide_index(0, 0, 0, "<absent>", {})["status"] == "Info",
          "an empty index is not a finding")
    check(decide_index(0, 0, 12, "s", {})["status"] == "Warn",
          "a dozen cached connections warn")
    check(decide_index(0, 0, 0, "s",
                       {"IndexNetworkConnections": "True"})["status"] == "Warn",
          "indexing network connections warns on its own, because it walks the "
          "share at every startup")

    nogpu = decide_gpu_preference("e", None)
    check(nogpu["status"] == "Warn",
          "no per-app GPU preference warns: Windows then picks, and on a "
          "hybrid machine that can mean the integrated adapter")
    check(nogpu["autofixable"] is True, "and that one has a fix here")
    check(decide_gpu_preference("e", "GpuPreference=2;")["status"] == "Pass",
          "high performance already pinned: Pass")
    check(decide_gpu_preference("e", "GpuPreference=1;")["status"] == "Warn",
          "power saving pinned warns")
    check(decide_gpu_preference("e", None, readable=False)["status"] == "Info",
          "but a registry that could not be read at all is Info: unreadable "
          "and absent are different answers, and only one of them is a "
          "finding  <-- pinned defect")

    check(decide_memory(None, None)["status"] == "Info",
          "memory that could not be read is Info: without that guard a failed "
          "read computed zero and told the operator to replace memory that was "
          "probably fine  <-- pinned defect")
    check(decide_memory(8.0, 4.0)["status"] == "Fail", "eight gigabytes fails")
    check(decide_memory(32.0, 1.0)["status"] == "Warn",
          "plenty installed but nearly none free warns")
    check(decide_memory(32.0, 12.0)["status"] == "Pass", "and a healthy box passes")

    check(decide_disk([])["status"] == "Info", "no volume read: undetermined")
    check(decide_disk([("C:\\", 500 * 1024 ** 3,
                        300 * 1024 ** 3)])["status"] == "Pass",
          "plenty of free space passes")
    check(decide_disk([("C:\\", 500 * 1024 ** 3,
                        5 * 1024 ** 3)])["status"] == "Warn",
          "five gigabytes free of five hundred warns on both triggers")
    check(decide_disk([("C:\\", 0, 0)])["status"] == "Warn",
          "a volume reporting zero size divides by nothing and still warns")
    check(decide_disk([("C:\\", 40 * 1024 ** 3,
                        10 * 1024 ** 3)])["status"] == "Warn",
          "ten gigabytes free of forty is twenty-five percent, well clear of "
          "the percentage floor, and still warns: the twenty gigabyte floor "
          "fires on its own  <-- pinned defect")
    check(decide_disk([("C:\\", 1000 * 1024 ** 3,
                        50 * 1024 ** 3)])["status"] == "Warn",
          "fifty gigabytes free of a thousand clears the twenty gigabyte "
          "floor and warns on five percent: the ten percent floor fires on "
          "its own too  <-- pinned defect")
    check(decide_disk([("C:\\", 200 * 1024 ** 3,
                        40 * 1024 ** 3)])["status"] == "Pass",
          "forty gigabytes and twenty percent clears both and passes")

    check(decide_power_plan(None, None)["status"] == "Info",
          "an unparsable power scheme is undetermined")
    check(decide_power_plan("8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
                            "Managed power scheme")["status"] == "Pass",
          "the high performance plan passes by its identifier alone, under a "
          "name that says nothing: a managed image renames the plan, so the "
          "guid is the only part that survives  <-- pinned defect")
    check(decide_power_plan("8C5E7FDA-E8BF-4A96-9A85-A6E23A8C635C",
                            "Managed power scheme")["status"] == "Pass",
          "and the same guid in upper case, because powercfg prints either")
    check(decide_power_plan("other-guid", "Ultimate Performance")["status"]
          == "Pass", "and a renamed one passes by its name")
    check(decide_power_plan("other-guid", "Balanced")["status"] == "Warn",
          "balanced warns, because a parked processor lengthens the window in "
          "which a save can time out")

    check(decide_roaming(None, 0, 0)["status"] == "Info",
          "an unreadable profile state is undetermined")
    check(decide_roaming(False, 100, 2)["status"] == "Pass",
          "a local profile passes")
    check(decide_roaming(True, 100, 2)["status"] == "Warn",
          "a roaming profile carrying the settings folder warns")

    check(decide_symbology(None)["status"] == "Info",
          "the symbology check is always Info: no client setting was found "
          "that governs it, and none is invented here  <-- pinned defect")
    check(decide_symbology("True")["status"] == "Info",
          "reading the feature-caching setting does not turn it into a verdict")
    check("does NOT mean" in decide_symbology(None)["impact"].replace(
              "does not mean", "does NOT mean"),
          "and the impact line says the value was not lost, so a redraw bug is "
          "not escalated as data loss")

    # ----------------------------------------- the causes added in this release
    print("-" * 70)
    print("network power, the event timeline, SMB, share latency, Defender "
          "and the .aprx")
    check(as_list({"name": "Ethernet"}) == [{"name": "Ethernet"}],
          "one adapter arrives as a bare object, not a list of one, and is "
          "read as one adapter rather than as its field names  "
          "<-- pinned defect")
    check(as_list(None) == [] and as_list([1, 2]) == [1, 2],
          "nothing is no adapters, and a list stays a list")
    check(decide_nic(None, "module missing")["status"] == "Info",
          "an adapter list that could not be read is Info")
    check(decide_nic([], None)["status"] == "Info",
          "no adapter up leaves the power setting undetermined")
    nic_on = decide_nic([("Wi-Fi", "w", "Enabled"), ("Eth", "e", "Disabled")],
                        None)
    check(nic_on["status"] == "Warn" and nic_on["dataloss"] is True,
          "an adapter Windows may turn off warns, in the data-loss block")
    check("Wi-Fi" in nic_on["detail"] and "Eth" not in nic_on["detail"],
          "and the detail names only the adapter to change")
    check(decide_nic([("Eth", "e", "Disabled"), ("Dock", "d", None)],
                     None)["status"] == "Info",
          "one adapter that reads Disabled beside one nobody could read is "
          "Info, never Pass: a Pass off a partial reading is a verdict about "
          "a device nobody measured  <-- pinned defect")
    check(decide_nic([("Eth", "e", "Disabled"), ("Usb", "u", "Unsupported")],
                     None)["status"] == "Pass",
          "Disabled and Unsupported on every adapter passes")

    check(parse_events("Access is denied.") is None,
          "a reply that is not XML is an unread log, not an empty one  "
          "<-- pinned defect")
    check(parse_events(event_xml()) == [],
          "an empty root element is a log with no matching event")
    parsed = parse_events(event_xml(("Application Hang", 1002,
                                     "2026-09-02T14:31:07",
                                     ["ArcGISPro.exe"], 2)))
    check(parsed == [{"provider": "Application Hang", "id": 1002, "level": 2,
                      "when": "2026-09-02 14:31:07",
                      "data": ["ArcGISPro.exe"]}],
          "an event is read for its provider, id, level, UTC time and data")
    odd = ("<Events><Event xmlns='http://schemas.microsoft.com/win/2004/08/"
           "events/event'><EventData/></Event><Event xmlns='http://schemas."
           "microsoft.com/win/2004/08/events/event'><System><EventID>x"
           "</EventID></System></Event><Event xmlns='http://schemas.microsoft"
           ".com/win/2004/08/events/event'><System><EventID>41</EventID>"
           "<Level></Level></System></Event></Events>")
    check([e["id"] for e in parse_events(odd)] == [41],
          "an event with no System block or a non-numeric id is skipped, and "
          "one with no provider, level or time still reads")

    def ev(provider, eid, data=(), level=4):
        return {"provider": provider, "id": eid, "level": level, "when": "t",
                "data": list(data)}

    check(classify_event("Application", ev("Microsoft-Windows-Winlogon", 1002,
                                           ["ArcGISPro.exe"])) is None,
          "a Winlogon 1002 is not an Application Hang, even naming the same "
          "program: the id alone would report a Pro hang on a machine where "
          "Pro never hung  <-- pinned defect")
    check(classify_event("Application", ev("Application Error", 1000,
                                           ["python.exe"])) is None,
          "another program's crash is not counted")
    check("crashed" in classify_event(
              "Application", ev("Application Error", 1000,
                                [r"C:\Program Files\ArcGIS\Pro\bin"
                                 r"\ArcGISPro.exe"])),
          "a Pro crash is matched by the full path of the program too")
    check("hung" in classify_event("Application", ev("Application Hang", 1002,
                                                      ["ArcGISPro.exe"])),
          "a Pro hang is counted")
    check(classify_event("Application", ev("Application Hang", 1000,
                                           ["ArcGISPro.exe"])) is None,
          "a provider and an id that do not belong together are not counted")
    check("Kernel-Power 41" in classify_event(
              "System", ev("Microsoft-Windows-Kernel-Power", 41)),
          "a restart without a clean shutdown is counted")
    check("6008" in classify_event("System", ev("EventLog", 6008)),
          "and so is an unexpected previous shutdown")
    check(classify_event("System", ev("EventLog", 6006)) is None,
          "a clean shutdown is not")
    check("uninterpreted" in classify_event(
              "Microsoft-Windows-SMBClient/Connectivity",
              ev("Microsoft-Windows-SMBClient", 30999, level=3)),
          "an SMB connectivity warning is listed with its id uninterpreted, "
          "because no published reference gives the ids a meaning")
    check(classify_event("Microsoft-Windows-SMBClient/Connectivity",
                         ev("Microsoft-Windows-SMBClient", 30999,
                            level=4)) is None,
          "and an informational one is not listed")

    found = decide_events([("2026-09-01 10:00:00", "older"),
                           ("2026-09-02 10:00:00", "newer")], [], 3, [])
    check(found["status"] == "Warn" and found["dataloss"] is True,
          "a recorded hang or dirty shutdown warns, in the data-loss block")
    check(_join(found["observed"]).index("newer")
          < _join(found["observed"]).index("older"),
          "and the timeline is newest first")
    blind_log = decide_events([], [("Application", "Access is denied."),
                                   ("System", "x"), ("SMB", "y")], 3, [])
    check(blind_log["status"] == "Info" and "not measured"
          in _join(blind_log["observed"]),
          "every log unreadable is Info and says the timeline was not "
          "measured")
    quiet = decide_events([], [("SMB", "Access is denied.")], 3, [])
    check(quiet["status"] == "Info",
          "no event in the logs that were read is Info, never Pass: a hang "
          "the editor waits out leaves no event at all  <-- pinned defect")
    check("SMB: unreadable (Access is denied.)" in _join(quiet["observed"]),
          "and the log that could not be read is named rather than counted "
          "as quiet  <-- pinned defect")
    long_run = decide_events([("2026-09-%02d 00:00:00" % (n % 28 + 1), "e%d" % n)
                              for n in range(45)], [], 3, ["System"])
    check("5 more event(s) not listed" in _join(long_run["observed"]),
          "a long timeline is cut at forty with a count of the rest")
    check("System: capped at 200 events" in _join(long_run["observed"]),
          "and a log that hit the event cap says so")
    garbled = FakeEnv(events={"Application": ("<Events>", None),
                              "System": (event_xml(), None)})
    garbled_record = probe_events(garbled, {})
    check("Application: unreadable (the reply was not XML)"
          in _join(garbled_record["observed"]),
          "a reply that does not parse is reported as unreadable by name")
    flood = FakeEnv(events={"System": (event_xml(*[
        ("EventLog", 6006, "2026-09-01T00:00:00", [], 4)] * EVENT_MAX),
        None)})
    check("System: capped" in _join(probe_events(flood, {})["observed"]),
          "two hundred events in one log is the cap, so older ones are said "
          "to be missing rather than absent")
    check(len([q for _log, q in event_queries()
               if "timediff(@SystemTime) <= 1209600000" in q]) == 3,
          "every query is bounded to fourteen days, in milliseconds")
    check("@Name='Application Hang'" in event_queries()[0][1],
          "and the Application query names the providers, not only the ids")

    check(decide_smb(None, "denied", 1)["status"] == "Info",
          "unreadable SMB settings are Info")
    off = {"EnableMultiChannel": False}
    check(decide_smb(off, None, 1)["status"] == "Warn",
          "multichannel off with a project on a share warns")
    check(decide_smb(off, None, 0)["status"] == "Info",
          "the same setting with every project local decides nothing")
    check(decide_smb({"EnableMultiChannel": True}, None, 2)["status"] == "Info",
          "multichannel on is recorded, not graded")
    check("SessionTimeout = <absent>" in _join(decide_smb(off, None, 0)
                                                ["observed"]),
          "a setting the answer left out prints as absent")

    check(decide_share_latency([])["status"] == "Info",
          "no project on a share: nothing to time")
    check(decide_share_latency([("s", 399, 10, None)])["status"] == "Pass",
          "399 ms passes")
    check(decide_share_latency([("s", 400, 10, None)])["status"] == "Warn",
          "400 ms warns: the boundary is inclusive  <-- pinned defect")
    check(decide_share_latency([("s", 1499, 10, None)])["status"] == "Warn",
          "1499 ms still only warns")
    check(decide_share_latency([("s", 1500, 10, None)])["status"] == "Fail",
          "1500 ms fails")
    check(decide_share_latency([("s", None, 0, "gone")])["status"] == "Info",
          "a share that could not be listed is Info, not a latency verdict")
    mixed = decide_share_latency([("a", None, 0, "gone"),
                                  ("b", 20, 5, None)])
    check(mixed["status"] == "Info" and "unreadable  a" in
          _join(mixed["observed"]) and mixed["measured"] is False,
          "one folder timed fast and one unreadable is Info, never Pass, and "
          "names the unreadable one: the folder nobody could list is the "
          "likely culprit, and DL-04 refuses the same partial reading  "
          "<-- pinned defect")
    check(make_check(**mixed)["status"] == "Info",
          "and the record constructor accepts it")
    slow_mixed = decide_share_latency([("a", None, 0, "gone"),
                                       ("b", 450, 5, None)])
    check(slow_mixed["status"] == "Warn",
          "a slow folder beside an unreadable one still warns: that part was "
          "measured")

    sentinel = {"DisableRealtimeMonitoring": False,
                "ExclusionPath": ["N/A: Must be an administrator to view "
                                  "exclusions"],
                "ExclusionProcess": ["N/A: Must be an administrator to view "
                                     "exclusions"]}
    base = [r"C:\Program Files\ArcGIS\Pro",
            r"C:\Users\ed\AppData\Local\ESRI",
            r"C:\Users\ed\AppData\Roaming\ESRI"]
    exe = r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe"
    sysenv = defender_system_env(FakeEnv(envvars={
        "SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"}))
    hidden = decide_defender(sentinel, None, base, exe, sysenv)
    check(hidden["status"] == "Info",
          "the text an unprivileged read gets in place of the lists is "
          "recognised as hidden, and decides nothing  <-- pinned defect")
    check("NOT EXCLUDED" not in _join(hidden["observed"]),
          "and it is never compared as a path, which reported every baseline "
          "folder missing on a machine that may exclude them all  "
          "<-- pinned defect")
    check(DEFENDER_SENTINEL.search("must be an ADMINISTRATOR to view"),
          "the sentinel is matched whatever its case")
    check(decide_defender(None, "cmdlet missing", base, exe,
                          sysenv)["status"] == "Info",
          "unreadable Defender preferences are Info")
    check(decide_defender({"DisableRealtimeMonitoring": True}, None, base, exe,
                          sysenv)["status"] == "Info",
          "real-time scanning off makes exclusions decide nothing")
    check(decide_defender({"HideExclusionsFromLocalAdmins": True,
                           "ExclusionPath": []}, None, base, exe,
                          sysenv)["status"] == "Info",
          "lists hidden by policy are not read as empty  <-- pinned defect")
    appdata = decide_defender({"ExclusionPath": [r"%APPDATA%\ESRI",
                                                 r"%LOCALAPPDATA%\ESRI",
                                                 r"%ProgramFiles%\ArcGIS\Pro"],
                               "ExclusionProcess": ["ArcGISPro.exe"]},
                              None, base, exe, sysenv)
    check(appdata["status"] == "Warn"
          and "NOT EXCLUDED  path     C:\\Users\\ed\\AppData\\Roaming\\ESRI"
          in _join(appdata["observed"]),
          "an exclusion written as %APPDATA% does not cover the editor's "
          "folder: Defender expands it as LocalSystem, under the system "
          "profile  <-- pinned defect")
    check("NOT EXCLUDED  path     C:\\Program Files\\ArcGIS\\Pro"
          not in _join(appdata["observed"]),
          "while %ProgramFiles% expands to the same folder for everyone and "
          "does cover the install")
    check(defender_expand(r"%NOPE%\x", sysenv) == r"%NOPE%\x",
          "an unknown variable stays as written, so it matches nothing")
    check(defender_expand(r"%appdata%\x", sysenv)
          == r"C:\Windows\System32\config\systemprofile\AppData\Roaming\x",
          "variable names are matched whatever their case, as Windows does")
    check(_pattern_covers(r"C:\Users\*\AppData\Local\ESRI",
                          r"C:\Users\ed\AppData\Local\ESRI\Cache"),
          "an asterisk stands for one folder, and everything below the match "
          "is covered")
    check(not _pattern_covers(r"C:\Users\*\ESRI",
                              r"C:\Users\ed\AppData\Local\ESRI"),
          "but it does not stand for three folders  <-- pinned defect")
    check(_pattern_covers(r"C:\Data\Pro?", r"C:\data\Pro3"),
          "a question mark is one character, and case does not matter")
    check(not _pattern_covers(r"C:\Data\Pro\Deep", r"C:\Data\Pro"),
          "an exclusion below a folder does not cover the folder above it")
    check(_pattern_covers(r"C:\A\b.c", r"C:\A\b.c\d") and
          not _pattern_covers(r"C:\A", r"C:\A\b.exe", same_depth=True),
          "a process exclusion names one file, so a folder path in the "
          "process list covers nothing")

    def procs_of(entries):
        return decide_defender({"ExclusionPath": base,
                                "ExclusionProcess": entries}, None, base, exe,
                               sysenv)["status"]

    check(procs_of(["arcgispro.EXE"]) == "Pass",
          "a process excluded by image name covers Pro wherever it runs")
    check(procs_of([exe]) == "Pass", "and by its full path")
    check(procs_of([r"C:\Program Files\ArcGIS\Pro\bin\*"]) == "Pass",
          "and by a wildcard over its folder")
    check(procs_of([r"C:\Program Files\ArcGIS\Pro"]) == "Warn",
          "but a folder named as a process is not a process exclusion")
    check(procs_of(["ArcGISPro.exe"] + [""]) == "Pass",
          "an empty entry in a list is ignored")
    contextual = decide_defender({"ExclusionPath": [
        base[0], base[1], base[2] + r"\:{ScanTrigger:OnDemand}"],
        "ExclusionProcess": ["ArcGISPro.exe"]}, None, base, exe, sysenv)
    check(contextual["status"] == "Warn",
          "a contextual exclusion limited to on-demand scans does not count "
          "for on-access scanning")

    check(decide_aprx("running", [])["status"] == "Info",
          "Pro running: the project file is not opened")
    check("not determined" in decide_aprx("unknown", [])["observed"],
          "and when nobody knows whether Pro runs, it is not opened either  "
          "<-- pinned defect")
    check(decide_aprx("closed", [])["status"] == "Info",
          "no recent project: nothing to read")
    share_home = scan_aprx(r"\\files\gis\Roads\Roads.aprx",
                           aprx_texts(".", ".", 0), [])
    check(share_home["home"][2] == "NETWORK",
          "a home folder stored as '.' is resolved against the project "
          "folder before it is classified, so a project on a share has its "
          "home on the share, not on no drive at all  <-- pinned defect")
    check(share_home["home"][1] == r"\\files\gis\Roads",
          "and the resolved folder is the one the project sits in")
    check(share_home["parsed"] == 2,
          "an entry that is not JSON is skipped, and the two that are count")
    absolute = scan_aprx(r"C:\P\a.aprx", aprx_texts(r"D:\Work", r"D:\Work\w.gdb",
                                                    0), [])
    check(absolute["home"][1] == r"D:\Work",
          "an absolute home folder is kept as written")
    timed = scan_aprx(r"C:\P\a.aprx", aprx_texts(".", "a.gdb", 30), [])
    check(len(timed["refresh"]) == 1 and "Hydrants" in timed["refresh"][0],
          "a layer with a thirty-second refresh is found, and the layer at "
          "zero is not")
    check(scan_aprx(r"C:\P\a.aprx", [("x.json", json.dumps(
        [{"refreshRate": True}, {"refreshRate": -1}]))], [])["refresh"] == [],
        "a flag or a negative number is not a refresh interval")
    check(scan_aprx(r"C:\P\a.aprx", [("x.json", "[]")], [])["home"] is None,
          "a document that is not an object carries no home folder")

    def project(**kw):
        base_row = {"path": r"C:\P\a.aprx", "home": (".", r"C:\P", "LOCAL"),
                    "gdb": ("a.gdb", r"C:\P\a.gdb", "LOCAL"), "refresh": [],
                    "parsed": 2, "truncated": False}
        base_row.update(kw)
        return base_row

    check(decide_aprx("closed", [project()])["status"] == "Pass",
          "a project read in full, local, with no refresh timer passes")
    check(decide_aprx("closed", [project(refresh=["m: L refreshRate = 30"])])
          ["status"] == "Warn", "a refresh timer warns")
    check(decide_aprx("closed", [project(gdb=("g", r"\\s\g", "NETWORK"))])
          ["status"] == "Warn", "a default geodatabase on a share warns")
    capped_aprx = decide_aprx("closed", [project(truncated=True)])
    check(capped_aprx["status"] == "Info",
          "a project only partly read is Info, never Pass: the timer could be "
          "in the part the cap left out  <-- pinned defect")
    check("Not read in full" in capped_aprx["detail"],
          "and the detail names it")
    check(decide_aprx("closed", [project(parsed=0)])["status"] == "Info",
          "a project file with no JSON inside, which is the older layout, is "
          "unreadable rather than clean")
    check("<absent>" in _join(decide_aprx("closed",
                                        [project(home=None)])["observed"]),
          "a project with no home folder recorded prints it as absent")
    check(decide_aprx("closed", [{"path": "x", "error": "locked"}])["status"]
          == "Info", "a project that could not be opened is Info")

    # -------------------------------------------------------- the apply gate
    print("-" * 70)
    print("no fix runs off a determination that was never made")
    gated = [make_check("DL-00", "c", "t", "Pass", "measured", dataloss=True),
             make_check("PF-06", "c", "t", "Warn", "measured", fix="f",
                        autofixable=True),
             make_check("PF-11", "c", "t", "Pass", "measured", fix="f",
                        autofixable=True),
             make_check("PF-08", "c", "t", "Skipped", "measured")]
    check(apply_gate(gated, "PF-06", False)[0] is True,
          "a failing check with a fix lets that fix run")
    check(apply_gate(gated, "PF-11", False)[0] is False,
          "a healthy check does not")
    allowed, why = apply_gate(gated, "PF-08", False)
    check(allowed is False and "skipped" in why,
          "and a SKIPPED check refuses by name: its probe determined nothing, "
          "so a fix off that record acts on an answer nobody has  "
          "<-- pinned defect")
    check(apply_gate(gated, "PF-99", False)[0] is False,
          "a fix for a check that did not run is refused")
    check("did not run" in apply_gate(gated, "PF-99", False)[1],
          "and says which check is missing")
    wrong_profile = [make_check("DL-00", "c", "t", "Fail", "measured",
                                dataloss=True),
                     make_check("PF-06", "c", "t", "Warn", "measured", fix="f",
                                autofixable=True)]
    check(apply_gate(wrong_profile, "PF-06", True)[0] is False,
          "a per-user fix under the wrong profile is refused rather than "
          "reporting success and changing nothing for the editor  "
          "<-- pinned defect")
    check(apply_gate(wrong_profile, "PF-06", False)[0] is True,
          "a machine-wide fix is unaffected by which profile is running")

    # ------------------------------------------------------ end to end, sick
    print("-" * 70)
    print("a whole run against a stand-in workstation")
    env = fake_sick()
    opts = opts_with(sde_server="gisdb", portal="portal.example.org")
    document = run(env, opts)
    got = dict([(c["id"], c["status"]) for c in document["checks"]])
    expected_run = {
        "DL-00": "Pass", "DL-03": "Warn", "DL-05": "Fail", "DL-06": "Fail",
        "DL-07": "Warn", "DL-08": "Warn", "DL-09": "Warn", "DL-10": "Fail",
        "DL-11": "Warn", "DL-12": "Warn", "PF-04": "Info", "PF-05": "Warn",
        "PF-06": "Warn", "PF-07": "Warn", "PF-08": "Warn", "PF-11": "Warn",
        "PF-12": "Fail", "PF-13": "Warn", "PF-15": "Warn", "PF-19": "Warn",
        "PF-26": "Warn", "PF-27": "Info",
        "DL-04": "Warn", "DL-13": "Warn", "PF-02": "Info", "PF-03": "Info",
        "PF-17": "Warn", "PF-22": "Info",
    }
    for cid in sorted(expected_run):
        check(got.get(cid) == expected_run[cid],
              "the sick workstation reports %s %s  [got %s]"
              % (cid, expected_run[cid], got.get(cid)))
    check(len(document["checks"]) == len(expected_run),
          "every registered check produced exactly one record")
    check(len(set([c["id"] for c in document["checks"]]))
          == len(document["checks"]), "and no id was registered twice")
    offenders = [c["id"] for c in document["checks"]
                 if c["status"] in DETERMINED and says_unmeasured(c["observed"])]
    check(offenders == [],
          "not one record in a whole run pairs a verdict with text saying it "
          "measured nothing, which is the defect this tool shipped  "
          "<-- pinned defect")
    order = [c["dataloss"] for c in document["checks"]]
    check(order == sorted(order, reverse=True),
          "the data-loss block runs and prints first, ahead of every "
          "performance finding")
    check(document["act_first"][0] == "DL-05",
          "the act-first list starts with the first data-loss finding of "
          "the run, in the order the report prints them")
    check("PF-07" in document["act_first"],
          "the leftover-session check reaches the act-first list, because a "
          "folder holding unsaved work is a recovery lead and not churn")
    check("PF-06" not in document["act_first"],
          "and the cache check does not, however stale it is")
    check(exit_code(document["checks"]) == 2, "the sick run exits 2")
    check(document["applied"] == [],
          "with --apply absent the applied collection stays empty across a "
          "whole run  <-- pinned defect")
    check(len([n for n in document["notes"] if n.startswith("would apply")]) == 1,
          "one fix would have run, and the check-only run says which")
    check([n for n in document["notes"] if n.startswith("refused [AP-01]")],
          "the cache fix is refused while Pro is running, because clearing a "
          "live cache corrupts the session  <-- pinned defect")
    check(env.written == [],
          "and nothing whatsoever was written to the stand-in machine")
    check(document["fixable"] == ["PF-06", "PF-11"],
          "the summary offers both fixes, even the one the gate then refuses, "
          "because the finding is real either way")
    check(r"C:\Users\editor\OneDrive - Example" in
          by_id(document["checks"], "DL-05")["observed"],
          "the storage finding names the folder it found the projects in")
    check("14" in by_id(document["checks"], "PF-06")["observed"],
          "and the cache finding names the file count it measured")
    leftovers = by_id(document["checks"], "PF-07")
    check("2 of 3 folder(s) still hold" in leftovers["observed"],
          "of three leftover session folders, the two holding a project file "
          "or a scratch table with rows in it are named, and the one holding "
          "a bare table header is not: an empty scratch geodatabase is a husk, "
          "not a recovery lead  <-- pinned defect")
    check("ArcGISProTemp67890" not in leftovers["detail"],
          "so the husk is not on the list of folders to open first")
    check("readme.txt" not in by_id(document["checks"], "DL-09")["observed"],
          "a file that is not a dump is not counted as one, because a Windows "
          "name filter also matches the short alias  <-- pinned defect")
    check("1 from the last 45 days"
          in by_id(document["checks"], "DL-09")["result"],
          "of two dumps, only the recent one is counted against the incident: "
          "a dump from last year is history, not evidence about this hang")
    check(env.zip_reads == [],
          "with Pro running, not one project file was opened in a whole run  "
          "<-- pinned defect")
    check("DL-04" in document["act_first"] and "DL-13" in document["act_first"],
          "the adapter power setting and the event timeline reach the "
          "act-first list")
    timeline = _join(by_id(document["checks"], "DL-13")["observed"])
    check("Application Hang 1002" in timeline
          and "userinit" not in timeline and "python" not in timeline,
          "the timeline lists the Pro hang and leaves out the Winlogon event "
          "and the other program's crash")
    check(timeline.index("Kernel-Power 41") < timeline.index("Hang 1002"),
          "and the dirty shutdown ten minutes after the hang prints above it")

    # the settings file, found the second way
    roamed = FakeEnv(
        envvars={"APPDATA": r"C:\A"},
        tree={r"C:\A\Esri": [("ArcGISPro_StrongName_abc", 0, 0, 0, True)],
              r"C:\A\Esri\ArcGISPro_StrongName_abc": [("3.4.0.0", 0, 0, 0, True)],
              r"C:\A\Esri\ArcGISPro_StrongName_abc\3.4.0.0":
                  [("user.config", 900, 5, 0, False)],
              r"C:\A\Esri\ArcGISPro_StrongName_abc\3.3.0.0":
                  [("user.config", 900, 400, 0, False)]})
    found_config = user_config_path(roamed)
    check(found_config and found_config.endswith("user.config"),
          "with no registry pointer, the settings file is found by searching "
          "the application data folder instead")
    check("3.4.0.0" in found_config,
          "and the newest one wins, because an upgrade leaves the previous "
          "release's copy behind")
    check(user_config_path(FakeEnv(envvars={"APPDATA": r"C:\A"})) is None,
          "when there is no settings file at all, the answer is None and the "
          "checks that read it stay undetermined")
    check(user_config_path(FakeEnv(envvars={})) is None,
          "and a profile with no application data folder does not raise")

    # a Documents folder with no project folder under it
    empty_docs = FakeEnv(
        envvars={"LOCALAPPDATA": r"C:\L"},
        reg={r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer"
             r"\User Shell Folders": {"Personal": r"C:\Docs"}},
        tree={r"C:\Docs": []})
    check(probe_placeholders(empty_docs, {})["status"] == "Info",
          "a profile with no project folder under Documents leaves the "
          "placeholder question undetermined rather than calling it clean")

    duplicates = FakeEnv(
        envvars={"APPDATA": r"C:\A", "LOCALAPPDATA": r"C:\A"},
        tree={r"C:\A\ESRI\ArcGISPro\Favorites": [("p.sde", 900, 1, 0, False)]})
    dup_record = probe_sde_files(duplicates, {})
    check("1 connection file(s)" in _join(dup_record["observed"]),
          "one connection file reachable through two configured roots is "
          "counted once, not twice")

    blind = fake_sick()
    blind._unreadable = set([_norm(
        r"C:\Users\editor\OneDrive - Example\Documents\ArcGIS\Projects")])
    blind_record = probe_placeholders(blind, {})
    check(blind_record["status"] == "Warn"
          and "unreadable" in _join(blind_record["observed"]),
          "a project folder the walk could not open is reported as unreadable "
          "rather than as clean: a scan that returns nothing for a folder it "
          "could not read says the tree is fine when it has no idea  "
          "<-- pinned defect")
    limited, _ = blind.scan(r"C:\Users\editor\OneDrive - Example\Documents",
                            depth=4, limit=1)
    check(len(limited) == 1, "and the stand-in walk honours a limit too")

    other_drive = fake_healthy()
    other_drive._reg[_norm(
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer"
        r"\User Shell Folders")] = {"Personal": r"D:\Docs"}
    for path, children in ((r"D:\Docs", [("ArcGIS", 0, 0, 0, True)]),
                           (r"D:\Docs\ArcGIS", [("Projects", 0, 0, 0, True)]),
                           (r"D:\Docs\ArcGIS\Projects",
                            [("Parcels.aprx", 1000, 1, 0, False)])):
        other_drive._tree[_norm(path)] = (path, children)
    other_drive._disks[_norm("d:\\")] = (200 * 1024 ** 3, 100 * 1024 ** 3)
    other_run = run(other_drive, opts_with())
    check("D: 100.00 GB free" in
          by_id(other_run["checks"], "PF-13")["observed"].replace("D:\\ ", "D: "),
          "the volume holding the projects is measured as well as the system "
          "volume, because that is the one the caches and the scratch fill")

    check(fix_cache_aside(fake_sick(), [], {}) is None,
          "a fix whose target has gone since the check ran does nothing rather "
          "than guessing at a path  <-- pinned defect")
    check(fix_gpu_preference(fake_sick(), [], {}) is None,
          "and so does the graphics fix")
    vanished = [make_check("DL-00", "c", "t", "Pass", "measured", dataloss=True),
                make_check("DL-12", "c", "t", "Pass", "measured", dataloss=True),
                make_check("PF-06", "c", "t", "Warn", "measured", fix="f",
                           autofixable=True)]
    gone_applied, gone_notes = apply_fixes(vanished, fake_sick(), {}, True)
    check(gone_applied == [], "nothing is recorded as applied in that case")
    check([n for n in gone_notes if "the target it would change is gone" in n],
          "and the run says so instead of reporting a change it did not make")
    check([n for n in gone_notes if n.startswith("skip [AP-02]")
           and "did not run" in n],
          "a fix whose check never ran at all is refused by name")

    misprofiled = fake_sick()
    misprofiled._procs = []
    misprofiled._tree[_norm(r"C:\Users")] = (
        r"C:\Users", [("editor", 0, 0, 0, True), ("relief", 0, 0, 0, True)])
    relief = r"C:\Users\relief\AppData\Roaming\ESRI\ArcGISPro"
    misprofiled._tree[_norm(relief)] = (relief, [])
    misrun = run(misprofiled, opts_with(apply=True))
    check(by_id(misrun["checks"], "DL-00")["status"] == "Fail",
          "a second profile carrying Pro data fails the session check in a "
          "whole run, not only in the decision")
    check(misrun["applied"] == [],
          "and every per-user fix is refused under that profile, even with "
          "--apply, rather than reporting success and changing nothing for "
          "the editor  <-- pinned defect")
    check(misprofiled.written == [],
          "nothing at all was written to the wrong profile")

    # ---------------------------------------------------- end to end, healthy
    healthy = run(fake_healthy(), opts_with())
    healthy_status = dict([(c["id"], c["status"]) for c in healthy["checks"]])
    check(healthy["act_first"] == [],
          "a healthy workstation has nothing to act on")
    check(exit_code(healthy["checks"]) == 0, "and exits 0")
    check(healthy_status["DL-05"] == "Pass", "its projects are local")
    check(healthy_status["DL-06"] == "Pass", "its Documents are not redirected")
    check(healthy_status["DL-10"] == "Skipped",
          "with no host named, the database probe is skipped and no packet "
          "leaves the machine")
    check(healthy_status["PF-19"] == "Skipped", "and so is the portal probe")
    check(healthy["counts"]["Fail"] == 0, "nothing failed")
    check(healthy["counts"]["Warn"] == 0, "and nothing warned")
    check(healthy["fixable"] == [], "so no fix is offered")
    check(len([n for n in healthy["notes"] if n.startswith("skip [AP-02]")]) == 1,
          "the graphics fix reports itself already healthy rather than silently "
          "doing nothing")

    # ------------------------------------------------------- end to end, bare
    bare = run(fake_bare(), opts_with())
    bare_status = dict([(c["id"], c["status"]) for c in bare["checks"]])
    check(len(bare["checks"]) == len(expected_run),
          "a machine that answers nothing still produces every record")
    check(exit_code(bare["checks"]) == 1,
          "and exits 1, not 2: nothing it could still measure bears on lost "
          "edits")
    check(bare["act_first"] == [],
          "nothing is promoted to the act-first list off an unread machine  "
          "<-- pinned defect")
    check(bare_status["PF-12"] == "Info",
          "unreadable memory is Info, not a hardware verdict")
    check(bare_status["DL-06"] == "Info", "unreadable redirection is Info")
    check(bare_status["PF-15"] == "Info", "an unreadable power plan is Info")
    check(sorted([cid for cid, status in bare_status.items()
                  if status in DETERMINED]) == ["DL-00", "DL-12", "PF-11"],
          "exactly three checks can still answer on a machine that reads back "
          "nothing: who is running the tool, whether Pro is open, and a "
          "graphics preference whose absence IS the finding. Every other "
          "check reports Info or Skipped rather than a verdict  "
          "<-- pinned defect")

    # ---------------------------------------------- a probe that dies mid-run
    class BrokenEnv(FakeEnv):
        def reg(self, path, name):
            if "NetCache" in path:
                raise OSError("the registry provider died")
            return FakeEnv.reg(self, path, name)

    broken = fake_healthy()
    broken.__class__ = BrokenEnv
    broken_run = run(broken, opts_with())
    broken_status = dict([(c["id"], c["status"]) for c in broken_run["checks"]])
    check(len(broken_run["checks"]) == len(expected_run),
          "one probe dying does not shorten the run")
    check(broken_status["DL-08"] == "Skipped",
          "the probe that died is Skipped, and it is the only one")
    check(broken_status["DL-12"] == "Pass",
          "the checks after it still ran and still reported")

    class NoProcessTable(FakeEnv):
        def processes(self, image):
            raise OSError("the process table could not be listed")

    unsure = fake_healthy()
    unsure.__class__ = NoProcessTable
    unsure_run = run(unsure, opts_with())
    check(by_id(unsure_run["checks"], "DL-12")["status"] == "Skipped"
          and by_id(unsure_run["checks"], "PF-22")["status"] == "Info"
          and unsure.zip_reads == [],
          "when the Pro check itself dies, the project file is not opened: "
          "not knowing whether Pro runs is not the same as knowing it is "
          "closed  <-- pinned defect")

    # a profile whose Documents folder lives on a file server
    unc = fake_healthy()
    unc_docs = r"\\files\home\editor\Documents"
    unc_projects = unc_docs + r"\ArcGIS\Projects"
    unc._reg[_norm(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer"
                   r"\User Shell Folders")] = {"Personal": unc_docs}
    for path, children in ((unc_docs, [("ArcGIS", 0, 0, 0, True)]),
                           (unc_docs + r"\ArcGIS", [("Projects", 0, 0, 0,
                                                     True)]),
                           (unc_projects, [("Parcels.aprx", 1000, 1, 0,
                                            False)])):
        unc._tree[_norm(path)] = (path, children)
    unc._listing[_norm(unc_projects)] = (650, 12, None)
    unc._zips[_norm(unc_projects + r"\Parcels.aprx")] = (
        aprx_texts(".", ".\\Parcels.gdb", 0), False)
    unc._ps["smb"] = ({"EnableMultiChannel": False}, None)
    unc_run = run(unc, opts_with())
    unc_status = dict([(c["id"], c["status"]) for c in unc_run["checks"]])
    check(unc_status["PF-03"] == "Warn"
          and "650 ms" in by_id(unc_run["checks"], "PF-03")["observed"],
          "a project folder on a share that takes 650 ms to list warns, with "
          "the number in the report")
    check(unc_status["PF-02"] == "Warn",
          "and multichannel off now matters, because a project is on a share")
    check(unc_status["PF-22"] == "Warn" and "[NETWORK]" in
          by_id(unc_run["checks"], "PF-22")["observed"],
          "its default geodatabase, stored as a relative path, is found on the "
          "share")

    # ---------------------------------------------------------- applying, for real
    print("-" * 70)
    print("--apply, against the stand-in machine")
    applying = fake_sick()
    # Close Pro, which is what the refusal above tells the operator to do.
    applying._procs = []
    applied_run = run(applying, opts_with(sde_server="gisdb", apply=True))
    ids_applied = [r["id"] for r in applied_run["applied"]]
    check(ids_applied == ["AP-01", "AP-02"],
          "with Pro closed and --apply given, both fixes run")
    check(by_id(applied_run["checks"], "PF-22")["status"] == "Warn"
          and "Hydrants refreshRate = 30" in
          by_id(applied_run["checks"], "PF-22")["observed"],
          "with Pro closed the project file is read, and the layer on a "
          "thirty-second timer is named")
    check(len(applying.zip_reads) == 1,
          "one recent project, one read")
    check(applying._reg[_norm(GPU_PREFERENCE_KEY)][
              r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe"]
          == GPU_PREFERENCE_WANTED,
          "the graphics preference is actually written")
    undo = [r["undo"] for r in applied_run["applied"] if r["id"] == "AP-02"][0]
    check(undo.startswith("reg delete"),
          "and the undo deletes it again, because there was no previous value")
    cache_record = [r for r in applied_run["applied"] if r["id"] == "AP-01"][0]
    check(".prostall-" in cache_record["result"],
          "the cache folder is renamed aside, never deleted  <-- pinned defect")
    check(cache_record["undo"].startswith("move "),
          "and the undo moves it straight back")
    applied_text = "\n".join(render(applied_run))
    check("Changed by --apply:" in applied_text
          and "- [AP-02]" in applied_text,
          "the report names every change that was made")
    check("To undo: reg delete" in applied_text,
          "and prints the undo for each one beside it")
    check(applying.isdir(cache_record["result"].split(" -> ")[1]),
          "the renamed folder is still there afterwards, with its contents")
    check(not applying.isdir(cache_record["result"].split(" -> ")[0]),
          "and the original path is gone, which is what makes Pro rebuild it")

    previous = fake_sick()
    previous._procs = []
    previous._reg[_norm(GPU_PREFERENCE_KEY)] = {
        r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe": "GpuPreference=1;"}
    prev_run = run(previous, opts_with(apply=True))
    prev_undo = [r["undo"] for r in prev_run["applied"] if r["id"] == "AP-02"][0]
    check("GpuPreference=1;" in prev_undo,
          "when there was a previous value, the undo restores that value "
          "rather than deleting the setting  <-- pinned defect")

    class FailingEnv(FakeEnv):
        def reg_write(self, path, name, value):
            raise OSError("access denied")

    failing = fake_sick()
    failing._procs = []
    failing.__class__ = FailingEnv
    failing_run = run(failing, opts_with(apply=True))
    check([n for n in failing_run["notes"] if n.startswith("failed [AP-02]")],
          "a fix that throws is recorded as failed and does not end the run")
    check([r["id"] for r in failing_run["applied"]] == ["AP-01"],
          "and the fix beside it still applied")

    # ------------------------------------------------------------- reporting
    print("-" * 70)
    print("the report says what the run found, and nothing else")
    text = "\n".join(render(document))
    check("They can cause lost edits:" in text,
          "a run with data-loss findings lists those steps first")
    check(text.index("They can cause lost edits:")
          < text.index("Then these, which affect speed:"),
          "ahead of the speed steps")
    check("== RISKS TO SAVED EDITS" in text and "== PERFORMANCE" in text
          and "== WHAT TO DO NEXT" in text,
          "and all three sections are there")
    check(" S3 " not in text and "S1," not in text,
          "no symptom code is printed: every line reads on its own")
    first_step = [l for l in render(document) if l.startswith("   1. ")][0]
    check("[DL-05]" in first_step,
          "step 1 is the first data-loss finding")
    check("What to do: see step 1 below." in text,
          "and the finding points at its step by number")
    healthy_text = "\n".join(render(healthy))
    check("They can cause lost edits:" not in healthy_text
          and "Nothing needs fixing" in healthy_text,
          "a healthy run prints no steps, and says so")
    bare_text = "\n".join(render(dict(document, checks=always, fixable=[],
                                      notes=[], applied=[])))
    check("Nothing needs fixing" not in bare_text
          and "Almost nothing could be checked" in bare_text,
          "a run that measured almost nothing does not call the machine "
          "fine, the same rule as exit code 3  <-- pinned defect")
    check("Expected:" not in text,
          "a plain run does not print the expected text")
    check("Expected:" in "\n".join(render(document, verbose=True)),
          "and a verbose one does")
    check("Why it matters:" in text,
          "a failing check says why it matters even without verbose")
    quiet_text = "\n".join(render_check(
        make_check("Q-1", "c", "t", "Pass", "measured", impact="i", fix="f",
                   result="r")))
    check("Why it matters" not in quiet_text and "Found:" not in quiet_text
          and "r" in quiet_text.split("\n")[1],
          "a passing check prints only its result, so a clean report is short")
    bare_lines = render_check(make_check("Q-2", "c", "t", "Warn",
                                         ["measured", "", "   "]), verbose=True)
    check(len(bare_lines) == 3 and not [l for l in bare_lines if not l.strip()],
          "blank observed lines are dropped, so a multi-line reading does not "
          "print gaps down the middle of the report")
    check(not [line for line in bare_lines if "matters" in line
               or "What to do" in line or "Expected" in line],
          "and a check that carries no impact, fix or expected text prints "
          "none of those headings empty")

    temp_dir = tempfile.mkdtemp(prefix="prostall-selftest-")
    try:
        text_path, json_path, html_path = write_report(document, temp_dir)
        check(os.path.isfile(text_path) and os.path.isfile(json_path)
              and os.path.isfile(html_path),
              "--report writes a text, a JSON and an HTML report")
        handle = io.open(json_path, encoding="utf-8")
        try:
            reloaded = json.loads(handle.read())
        finally:
            handle.close()
        check(reloaded["prostall"] == 2,
              "the JSON report carries its format version, so a diff against "
              "an older report from another workstation fails loudly")
        check(reloaded["act_first"] == document["act_first"],
              "and the same act-first list the console printed")
        check(len(reloaded["checks"]) == len(expected_run),
              "with every check in it")
        check(reloaded["applied"] == [],
              "and no applied record, because nothing was applied")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    # ----------------------------------------------------------- the command line
    print("-" * 70)
    print("the command line: nothing dangerous is on by default")
    args = _parse([])
    check(args.apply is False,
          "--apply is off by default, so a plain run changes nothing  "
          "<-- pinned defect")
    check(args.sde_server is None and args.portal is None,
          "and no host is probed by default, so a plain run sends no packet  "
          "<-- pinned defect")
    check(args.report is None and args.html is None, "and writes no file")
    check(args.no_open is False, "and an HTML report opens by default")
    check(args.self_test is False, "the self-test is off by default")
    check(args.verbose is False, "and so is the long form")
    check(args.sde_port == SDE_PORT,
          "the database port defaults to SDE_PORT")
    check(args.timeout == TIMEOUT_MS, "and the probe timeout to TIMEOUT_MS")
    check(args.json is False, "the console format is the default output")
    check(_parse(["--apply"]).apply is True, "--apply is read")
    check(_parse(["--self-test"]).self_test is True, "--self-test is read")
    check(_parse(["--verbose"]).verbose is True, "--verbose is read")
    check(_parse(["--json"]).json is True, "--json is read")
    check(_parse(["--sde-server", "h"]).sde_server == "h", "--sde-server is read")
    check(_parse(["--sde-port", "1500"]).sde_port == 1500, "--sde-port is read")
    check(_parse(["--portal", "p"]).portal == "p", "--portal is read")
    check(_parse(["--timeout", "900"]).timeout == 900, "--timeout is read")
    check(_parse(["--report", "d"]).report == "d", "--report is read")
    rejects(["--sde-port", "not-a-number"], "a non-numeric port is refused")
    rejects(["--nonsense"], "an unknown flag is refused")
    rejects(["--ap"], "a unique prefix of --apply is refused, so a short flag "
            "cannot arm the fix path  <-- pinned defect")
    check(not hasattr(_parse([]), "password"),
          "there is no password option at all: nothing here signs in to "
          "anything, and a connection file is never opened")
    # Stubbed, because a self-test that called itself would never return.
    # The point of the assertion is the branch order inside main: --self-test
    # is read before anything constructs an Env or reads a flag that acts.
    real_self_test = globals()["self_test"]
    globals()["self_test"] = lambda: 4242
    try:
        check(main(["--self-test", "--apply", "--sde-server", "h",
                    "--report", "x"]) == 4242,
              "--self-test wins over every other flag, so a test run can "
              "neither apply a fix, nor probe a host, nor write a file  "
              "<-- pinned defect")
    finally:
        globals()["self_test"] = real_self_test
    def run_main(argv):
        """main with stdout and stderr captured: (code, stdout, stderr).

        Stdout is captured too, because a run that fails to write its report
        has already printed it, and that report names this machine's user and
        project paths. A self-test must not print those.
        """
        real_out, real_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        try:
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code
            return code, sys.stdout.getvalue(), sys.stderr.getvalue()
        finally:
            sys.stdout, sys.stderr = real_out, real_err

    def quiet_main(argv):
        code, _out, err = run_main(argv)
        return code, err

    def with_env(factory, argv):
        """run_main with the machine the tool reads swapped for a stand-in."""
        real_env = globals()["Env"]
        globals()["Env"] = factory
        try:
            return run_main(argv)
        finally:
            globals()["Env"] = real_env

    code, message = quiet_main(["--timeout", "0"])
    check(code == 64, "a zero timeout is a usage error")
    check("--timeout" in message,
          "and the message names the flag rather than a traceback")
    check(quiet_main(["--sde-port", "0"])[0] == 64,
          "so is an impossible port")
    check(quiet_main(["--sde-port", "70000"])[0] == 64,
          "at either end of the range")
    blocked = tempfile.mkdtemp(prefix="prostall-blocked-")
    try:
        wall = os.path.join(blocked, "wall")
        handle = io.open(wall, "w", encoding="utf-8")
        handle.write("not a directory")
        handle.close()
        code, message = quiet_main(["--report", os.path.join(wall, "out")])
        check(code == 64 and "could not write the report" in message,
              "a report directory that cannot be created is a usage error "
              "naming the reason, not a traceback over a finished diagnostic")
    finally:
        shutil.rmtree(blocked, ignore_errors=True)

    # ------------------------------------------------- the real reads, for real
    print("-" * 70)
    print("the reading layer, against a real temporary tree and a real socket")

    def captured(fn):
        """Run something that prints, and hand back (result, its output)."""
        buffer = io.StringIO()
        real = sys.stdout
        sys.stdout = buffer
        try:
            return fn(), buffer.getvalue()
        finally:
            sys.stdout = real

    live = Env()
    check(isinstance(live.now(), float), "the clock answers a number")
    check(bool(live.username()), "the running user has a name")
    check(live.elevated() in (True, False),
          "the elevation question answers yes or no, never an exception")
    check(live.getenv("PATH"), "an environment variable that exists is read")
    check(live.getenv("PROSTALL_NOTHING_SETS_THIS") is None,
          "and one that does not is None, not an empty string")
    check(live.registry_readable() is (os.name == "nt"),
          "the registry is readable exactly on Windows, and the checks that "
          "depend on it are told so rather than guessing from a None")

    root = tempfile.mkdtemp(prefix="prostall-live-")
    try:
        sub = os.path.join(root, "sub")
        deep = os.path.join(sub, "deep")
        os.makedirs(deep)
        for path, body in ((os.path.join(root, "a.txt"), "hello"),
                           (os.path.join(sub, "b.txt"), "two"),
                           (os.path.join(deep, "c.txt"), "three")):
            handle = io.open(path, "w", encoding="utf-8")
            handle.write(body)
            handle.close()

        check(live.isdir(root), "a real directory is a directory")
        check(not live.isdir(os.path.join(root, "nope")),
              "and a missing one is not")
        check(not live.isdir(""), "an empty path is not a directory")
        check(live.isfile(os.path.join(root, "a.txt")), "a real file is a file")
        check(not live.isfile(root), "a directory is not a file")
        check(live.subdirs(root) == ["sub"], "the subdirectories are listed")
        check(live.subdirs(os.path.join(root, "nope")) == [],
              "and a missing directory lists nothing rather than raising")

        shallow, bad = live.scan(root, depth=1)
        check(sorted([e.name for e in shallow]) == ["a.txt", "sub"],
              "a depth of one stops at the first level, because a full walk of "
              "a synced profile can itself hydrate every file it touches")
        check(bad == [], "and nothing was unreadable")
        full, _ = live.scan(root, depth=3)
        check("c.txt" in [e.name for e in full],
              "a depth of three reaches the third level")
        check([e.size for e in full if e.name == "a.txt"] == [5],
              "and the sizes are the real ones")
        check([e.isdir for e in full if e.name == "sub"] == [True],
              "a directory is reported as one")
        bounded, _ = live.scan(root, depth=3, limit=2)
        check(len(bounded) == 2, "the limit stops the walk where it says it will")
        missing, missing_bad = live.scan(os.path.join(root, "nope"))
        check(missing == [] and missing_bad == [],
              "scanning a directory that is not there is empty, not an error")

        check(live.read_text(os.path.join(root, "a.txt")) == "hello",
              "a file is read")
        check(live.read_text(os.path.join(root, "nope")) is None,
              "and a missing one reads as None rather than raising")

        live.rename(sub, sub + "-aside")
        check(live.isdir(sub + "-aside") and not live.isdir(sub),
              "renaming a folder aside moves it and leaves nothing behind, "
              "which is what the cache fix does instead of deleting")

        usage = live.disk(root)
        check(usage and usage[0] > 0 and usage[1] >= 0,
              "a real volume reports a real size and a real free space")
        check(live.disk(os.path.join(root, "nope", "nope")) is None,
              "and a volume that is not there reports nothing")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        opened, elapsed, error = live.tcp("127.0.0.1", port, 2000)
        check(opened is True, "a listening port is reported open")
        check(error is None, "with no error")
        check(elapsed >= 0, "and an elapsed time in milliseconds")
    finally:
        server.close()
    shut, shut_ms, shut_error = live.tcp("127.0.0.1", port, 1000)
    check(shut is False, "a port with nothing behind it is reported closed")
    check(bool(shut_error), "and the reason is carried back, not swallowed")
    check(shut_ms <= 1100,
          "and the probe returns inside its own timeout. Not faster than it: "
          "Linux refuses a dead loopback port at once, Windows retransmits and "
          "times out, and the connect this replaced blocked for tens of "
          "seconds against a dead host  <-- pinned defect")

    check(live.reg(PRO_REG_KEY, "Version") is None
          or isinstance(live.reg(PRO_REG_KEY, "Version"), str),
          "a registry read answers a value or absence, on either platform, "
          "and never a traceback")
    check(isinstance(live.reg_subkeys(PRO_REG_KEY), list),
          "and a subkey read always answers a list")
    check(live.reg("NOTAHIVE\\x", "y") is None,
          "a path with no real hive is absence, not a crash")
    check(isinstance(live.processes("ArcGISPro.exe"), list)
          or live.processes("ArcGISPro.exe") is None,
          "the process question answers a list, or None when it could not be "
          "asked")
    total_gb, free_gb = live.memory()
    check((total_gb is None) == (free_gb is None),
          "memory answers both numbers or neither, so no check can compute a "
          "verdict from half a reading  <-- pinned defect")
    guid, plan_name = live.power_plan()
    check((guid is None) == (plan_name is None),
          "and the power plan answers both parts or neither")

    # The same reads with the platform switched off, so both hosts walk the
    # branch the other one takes.
    offline = Env()
    offline.windows = False
    check(offline.elevated() is False and offline.reg(PRO_REG_KEY, "x") is None
          and offline.reg_subkeys(PRO_REG_KEY) == []
          and offline.processes("ArcGISPro.exe") is None
          and offline.network_drives() == []
          and offline.memory() == (None, None)
          and offline.power_plan() == (None, None),
          "off Windows every Windows read answers absence, never a guess")
    check(offline.defender_preferences()[0] is None
          and offline.nic_power()[1] and offline.smb_client()[1]
          and offline.events("System", "*", 1)[1],
          "and the PowerShell and event log reads answer a reason, not an "
          "empty result")
    ghost = Env()
    ghost.windows = True
    ghost.powershell = "prostall-no-such-program"
    ghost.wevtutil = "prostall-no-such-program"
    check(ghost._powershell_json("1")[1] and ghost.events("System", "*",
                                                          1)[1],
          "a reader program that is not installed is a reason, not a "
          "traceback and not an empty answer")
    check(live.reg_subkeys(r"HKCU\Software\prostall-nothing-here") == [],
          "a registry key that is not there lists no subkeys")

    data, reason = live._powershell_json("ConvertTo-Json -InputObject @(7)")
    check((data is None) != (reason is None),
          "a PowerShell read answers data or a reason, never both and never "
          "neither")
    check(live._powershell_json("throw 'prostall'")[1],
          "a script that throws is a reason, not an empty answer")
    check(live._powershell_json("Write-Output 'not json'")[1],
          "and output that is not JSON is a reason too")
    for name in ("defender_preferences", "nic_power", "smb_client"):
        data, reason = getattr(live, name)()
        check((data is None) != (reason is None),
              "the %s read on this machine answers data or a reason" % name)
    check(live.events("Prostall-No-Such/Log", "*", 1)[1],
          "a log that does not exist is an error, not an empty timeline  "
          "<-- pinned defect")
    text, reason = live.events("Application", "*[System[EventID=999999]]", 1)
    check(parse_events(text) == [] if reason is None else bool(reason),
          "a query nothing matches is an empty list where the log can be "
          "read, and a reason where it cannot")

    zroot = tempfile.mkdtemp(prefix="prostall-zip-")
    try:
        import zipfile
        good_zip = os.path.join(zroot, "Roads.aprx")
        archive = zipfile.ZipFile(good_zip, "w")
        try:
            for name, body in aprx_texts(".", "Roads.gdb", 30):
                archive.writestr(name, body)
            archive.writestr("thumbnail.png", "not read")
        finally:
            archive.close()
        texts, truncated, reason = live.read_zip_json(good_zip)
        check(len(texts) == 3 and truncated is False and reason is None,
              "a real project-shaped zip yields its three JSON entries and "
              "skips the image")
        check(len(scan_aprx(good_zip, texts, [])["refresh"]) == 1,
              "and the refresh timer is found in what was read from disk")
        check(live.read_zip_json(good_zip, max_entries=1)[1] is True,
              "an entry cap that is reached says the read was capped")
        check(live.read_zip_json(good_zip, max_entry=1)[:2] == ([], True),
              "an entry over the size cap is not read, and the read says so")
        check(live.read_zip_json(good_zip, max_total=1)[1] is True,
              "and so is one that would take the total past its cap")
        not_zip = os.path.join(zroot, "old.aprx")
        handle = io.open(not_zip, "w", encoding="utf-8")
        handle.write("not a zip")
        handle.close()
        check(live.read_zip_json(not_zip)[2],
              "a project file that is not a zip is a reason, not an empty "
              "project")
        for n in range(3):
            io.open(os.path.join(zroot, "f%d.txt" % n), "w").close()
        elapsed, entries, reason = live.time_listdir(zroot)
        check(entries == 5 and elapsed >= 0 and reason is None,
              "a real folder is listed and timed")
        check(live.time_listdir(zroot, limit=2)[1] == 2,
              "and the listing stops at its limit")
        gone = live.time_listdir(os.path.join(zroot, "nope"))
        check(gone[0] is None and gone[2],
              "a folder that cannot be listed has no time, only a reason")
    finally:
        shutil.rmtree(zroot, ignore_errors=True)

    # --------------------------------------------- the whole tool, on this box
    print("-" * 70)
    print("the tool end to end on the machine running this, read-only")
    code, printed = captured(lambda: main(["--json"]))
    check(code in (0, 1, 2, 3),
          "a real run exits 0, 1, 2 or 3, whatever this machine turns out to "
          "be")
    live_document = json.loads(printed)
    check(live_document["prostall"] == 2,
          "the JSON it prints carries report version 2, the number a diff "
          "against a known-good workstation is taken at: comparing it to the "
          "constant that defines it would hold for any value")
    check(len(live_document["checks"]) == len(expected_run),
          "and one record per registered check, on a real machine too")
    check(live_document["applied"] == [],
          "a plain run applies nothing to the machine it is run on  "
          "<-- pinned defect")
    live_offenders = [c["id"] for c in live_document["checks"]
                      if c["status"] in DETERMINED
                      and says_unmeasured(c["observed"])]
    check(live_offenders == [],
          "and not one record of a real run pairs a verdict with text saying "
          "it measured nothing  <-- pinned defect")

    report_dir = tempfile.mkdtemp(prefix="prostall-live-report-")
    try:
        code, printed = captured(
            lambda: main(["--report", os.path.join(report_dir, "out"),
                          "--verbose", "--no-open"]))
        written = sorted(os.listdir(os.path.join(report_dir, "out")))
        check(len(written) == 3,
              "--report writes exactly three files, and creates the directory")
        check("report :" in printed, "and names both of them on the console")
        check("They can cause lost edits:" in printed
              or "Overall: 0 problem(s), 0 warning(s)" in printed,
              "a real run either lists what to act on first or reports nothing "
              "failing")
    finally:
        shutil.rmtree(report_dir, ignore_errors=True)

    # ---------------------------------------- what the first review round found
    print("-" * 70)
    print("a verdict needs a measurement: the first review round")
    import ctypes
    import runpy
    import subprocess
    import types

    # A usage error is 64. argparse exits 2, and 2 means lost edits.
    for argv in (["--ap"], ["--bogus"], ["--timeout", "abc"],
                 ["--sde-port", "x"]):
        code, _out, err = run_main(argv)
        check(code == 64 and "usage" in err,
              "%s is a usage error, exit 64 and never the lost-edits code 2  "
              "<-- pinned defect" % " ".join(argv))
    check(run_main(["--help"])[0] == 0, "and --help still exits 0")

    # A host name is a host name.
    check(host_problem("gisdb01") is None
          and host_problem("gis.example.org") is None
          and host_problem("::1") is None,
          "a plain name, a dotted name and an IPv6 literal are hosts")
    for bad in ("https://gis.example.org/portal", "gis.example.org/portal",
                "gis example", "user@host", "", "   "):
        check(host_problem(bad),
              "%r is not a host name  <-- pinned defect" % bad)
    code, _out, err = run_main(["--portal", "https://gis.example.org/portal"])
    check(code == 64 and "--portal" in err,
          "a URL pasted as a host is refused with exit 64, and the message "
          "names the flag, rather than probed and reported as a failure")
    check(run_main(["--sde-server", ""])[0] == 64,
          "so is an empty host name")

    # A name that never resolved says nothing about the server.
    real_getaddrinfo = socket.getaddrinfo

    def no_such_name(*args, **kwargs):
        raise socket.gaierror(11001, "getaddrinfo failed")

    socket.getaddrinfo = no_such_name
    try:
        unresolved = live.tcp("gisdb01", 1433, 1000)
    finally:
        socket.getaddrinfo = real_getaddrinfo
    check(unresolved[0] is None and "did not resolve" in unresolved[2],
          "a host name that does not resolve is open=None with the reason, "
          "not open=False  <-- pinned defect")
    check(live.tcp("a" * 70 + ".example", 1433, 1000)[0] is None,
          "and so is a name no resolver can encode")
    gone_host = decide_tcp("DL-10", "Network", "t", "gisdb01", 1433,
                           (None, 3, "the name did not resolve: x"), 250, "i",
                           "f", dataloss=True)
    check(gone_host["status"] == "Info" and gone_host["measured"] is False
          and make_check(**gone_host)["status"] == "Info",
          "an unresolved name is Info and unmeasured: DL-10 is a data-loss "
          "check, and a Fail on a typo blamed a server nobody reached  "
          "<-- pinned defect")
    check(act_first([make_check(**gone_host)]) == [],
          "so it never reaches the act-first list")
    typo = fake_healthy()
    typo._tcp["gisdb"] = (None, 5, "the name did not resolve: x")
    typo_run = run(typo, opts_with(sde_server="gisdb"))
    check(by_id(typo_run["checks"], "DL-10")["status"] == "Info"
          and "DL-10" not in typo_run["act_first"]
          and exit_code(typo_run["checks"]) == 0,
          "in a whole run the unresolved name leaves the exit code alone")
    refused = decide_tcp("DL-10", "Network", "t", "h", 1433,
                         (False, 5, "refused"), 250, "i", "f")
    check(refused["status"] == "Fail",
          "while a host that resolved and refused the connection still fails")

    # Every address a name resolves to is tried, inside one deadline.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    good_port = listener.getsockname()[1]

    def two_addresses(*args, **kwargs):
        # Port 0 refuses at once on both hosts, so the shared deadline is not
        # spent on the first address.
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 ("127.0.0.1", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 ("127.0.0.1", good_port))]

    socket.getaddrinfo = two_addresses
    try:
        second = live.tcp("twoaddresses.example", good_port, 3000)
    finally:
        socket.getaddrinfo = real_getaddrinfo
        listener.close()
    check(second[0] is True,
          "a name with two addresses is tried on both: the first one "
          "refusing does not end the probe")

    def listen_on(family, address):
        """A listening socket, or None where the host cannot bind it."""
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.bind(address)
            sock.listen(1)
        except OSError:
            sock is None or sock.close()
            return None
        return sock

    check(listen_on(socket.AF_INET, ("::1", 0)) is None,
          "a listener that cannot bind answers None")
    six = listen_on(socket.AF_INET6, ("::1", 0))
    six_open = (six is not None
                and live.tcp("::1", six.getsockname()[1], 2000)[0])
    six is None or six.close()
    check(six is None or six_open is True,
          "an IPv6 literal is probed where this host has an IPv6 loopback, "
          "and the check is vacuous where it has none")

    # DL-12: a process list nobody could read is not a closed Pro.
    winenv = Env()
    winenv.windows = True
    real_check_output = subprocess.check_output

    def tasklist_says(out):
        def fake(*args, **kwargs):
            return out
        return fake

    def tasklist_fails(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "tasklist")

    try:
        subprocess.check_output = tasklist_says(
            b"ArcGISPro.exe     9120 Console    1  2,100,480 K\r\n"
            b"other.exe 7 Console 1 10 K\r\n")
        running = winenv.processes("ArcGISPro.exe")
        subprocess.check_output = tasklist_says(
            b"INFO: No tasks are running which match the specified "
            b"criteria.\r\n")
        none_running = winenv.processes("ArcGISPro.exe")
        subprocess.check_output = tasklist_fails
        unreadable_list = winenv.processes("ArcGISPro.exe")
        subprocess.check_output = tasklist_says(
            b"Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  "
            b"(Balanced)\r\n")
        plan_read = winenv.power_plan()
        subprocess.check_output = tasklist_says(b"nothing useful\r\n")
        plan_unparsed = winenv.power_plan()
        subprocess.check_output = tasklist_fails
        plan_failed = winenv.power_plan()
    finally:
        subprocess.check_output = real_check_output
    check(running == ["ArcGISPro.exe 9120 Console 1 2,100,480 K"],
          "a tasklist line for a running Pro is read, and the other "
          "process is not")
    check(none_running == [],
          "and tasklist finding none is an empty list: Pro is closed")
    check(unreadable_list is None,
          "while tasklist failing is None, not an empty list  "
          "<-- pinned defect")
    check(plan_read == ("381b4222-f694-41f0-9685-ff5bb260df2e", "Balanced"),
          "the active power scheme is read from powercfg")
    check(plan_unparsed == (None, None) and plan_failed == (None, None),
          "and a powercfg answer that is not a scheme, or that failed, is "
          "unread")
    unknown = decide_pro_running(None)
    check(unknown["status"] == "Info" and unknown["measured"] is False
          and unknown["dataloss"] is True
          and make_check(**unknown)["status"] == "Info",
          "an unreadable process list makes DL-12 Info and unmeasured  "
          "<-- pinned defect")
    unknown_pro = fake_sick()
    unknown_pro._procs = None
    unknown_run = run(unknown_pro, opts_with(apply=True))
    check(by_id(unknown_run["checks"], "DL-12")["status"] == "Info"
          and "DL-12" not in unknown_run["act_first"],
          "in a whole run the unreadable list is Info and not an act-first "
          "finding")
    check(unknown_pro.zip_reads == []
          and by_id(unknown_run["checks"], "PF-22")["status"] == "Info",
          "and no project file is opened off a Pro nobody could see  "
          "<-- pinned defect")
    check([r["id"] for r in unknown_run["applied"]] == ["AP-02"]
          and [n for n in unknown_run["notes"]
               if n.startswith("refused [AP-01]")],
          "and the cache fix is refused by name, because only a Pass from "
          "DL-12 lets it run  <-- pinned defect")
    check(unknown_pro.isdir(r"C:\Users\editor\AppData\Local\ESRI"
                            r"\Local Caches"),
          "the cache folder was not renamed")
    gated_info = [make_check("PF-08", "c", "t", "Info", "read")]
    allowed, why = apply_gate(gated_info, "PF-08", False)
    check(allowed is False and "healthy" not in why and "Info" in why,
          "a check that is Info is not called already healthy: only a Pass "
          "is  <-- pinned defect")

    # DL-13: SMB events alone are not a Pro hang.
    smb_row = ("2026-09-02 14:29:55",
               "SMB client connectivity event 30999, level 3, uninterpreted")
    pro_row = ("2026-09-02 14:31:07",
               "ArcGIS Pro hung and was ended (Application Hang 1002)")
    smb_only = decide_events([smb_row], [], 3, [])
    check(smb_only["status"] == "Info" and smb_only["dataloss"] is True
          and "Pro hangs" not in smb_only["title"]
          and act_first([make_check(**smb_only)]) == [],
          "an SMB-only timeline is Info and stays out of the act-first "
          "list: its Warn said Pro hangs on a laptop that was off the VPN  "
          "<-- pinned defect")
    check("SMB client connectivity event 30999" in _join(smb_only["observed"]),
          "and the SMB event is still listed")
    check(decide_events([smb_row, pro_row], [], 3, [])["status"] == "Warn",
          "an SMB event beside a Pro hang still warns")
    smb_env = fake_sick()
    smb_env._events = {
        "Application": (event_xml(), None), "System": (event_xml(), None),
        "Microsoft-Windows-SMBClient/Connectivity": (event_xml(
            ("Microsoft-Windows-SMBClient", 30999, "2026-09-02T14:29:55",
             [r"\\files\gis"], 3)), None)}
    smb_run = run(smb_env, opts_with())
    check(by_id(smb_run["checks"], "DL-13")["status"] == "Info"
          and "DL-13" not in smb_run["act_first"],
          "in a whole run an SMB-only log leaves DL-13 out of the act-first "
          "list")
    forty = [("2026-09-01 00:%02d:00" % n, "e%d" % n) for n in range(40)]
    check("more event(s)" not in
          _join(decide_events(forty, [], 3, [])["observed"]),
          "exactly forty events list with no remainder line")
    check("1 more event(s) not listed" in _join(decide_events(
        forty + [("2026-09-02 00:00:00", "e40")], [], 3, [])["observed"]),
        "and forty-one list forty and count one")
    smb_log = "Microsoft-Windows-SMBClient/Connectivity"
    for level, listed in ((0, False), (1, True), (2, True), (3, True),
                          (4, False), (5, False)):
        check((classify_event(smb_log, ev("Microsoft-Windows-SMBClient",
                                          30999, level=level))
               is not None) is listed,
              "an SMB event at level %d is %s" % (level, "listed" if listed
                                                  else "left out"))
    check(classify_event("System", ev("Some-Other-Provider", 41)) is None
          and classify_event("System",
                             ev("Microsoft-Windows-Kernel-Power", 42)) is None
          and classify_event("System", ev("EventLog", 41)) is None
          and classify_event("System", ev("Some-Other-Provider", 6008))
          is None,
          "a restart or shutdown event is matched on the provider as well as "
          "the id, in both directions  <-- pinned defect")

    # PF-03: a partial reading is not a Pass, and the cap is stated.
    shares = [(r"\\files\g%d\p.aprx" % n, "t", "NETWORK") for n in range(4)]
    share_env = FakeEnv(listing={r"\\files\g0": (40, 3, None),
                                 r"\\files\g1": (60, 3, None)})
    share_record = probe_share_latency(share_env, {"recent": shares})
    check(share_record["status"] == "Pass"
          and "2 more share folder(s)" in _join(share_record["observed"]),
          "four share folders, two timed: the report says two were not  "
          "<-- pinned defect")
    half_env = FakeEnv(listing={r"\\files\g0": (40, 3, None)})
    check(probe_share_latency(half_env, {"recent": shares})["status"]
          == "Info",
          "and in a probe, one folder fast and the next unreachable is Info")
    nothing_listed = decide_share_latency([("a", None, 0, "path not found")])
    check("path not found" in _join(nothing_listed["observed"])
          and "not measured" in _join(nothing_listed["observed"]),
          "a share nobody could list names the reason and says it was not "
          "measured")

    # PF-22: the cap is stated, and unreadable text names its cause.
    many = fake_healthy()
    many_recent = [(r"C:\Users\editor\Documents\ArcGIS\Projects\P%d.aprx" % n,
                    "t", "LOCAL") for n in range(8)]
    many_record = probe_aprx(many, {"pro_running": False,
                                    "recent": many_recent})
    check(len(many.zip_reads) == APRX_MAX_PROJECTS
          and "5 older recent project(s) were not read"
          in _join(many_record["observed"]),
          "eight recent projects, three read: the report says five were not  "
          "<-- pinned defect")
    check("no JSON document" in _join(decide_aprx(
        "closed", [project(parsed=0)])["observed"])
        and "locked" in _join(decide_aprx(
            "closed", [{"path": "x", "error": "locked"}])["observed"]),
        "a project with no JSON and one that could not be opened each name "
        "their own cause")
    first_doc = scan_aprx(r"C:\P\a.aprx", [
        ("a.json", json.dumps({"defaultFolder": r"C:\One"})),
        ("b.json", json.dumps({"defaultFolder": r"C:\Two"}))], [])
    check(first_doc["home"][1] == r"C:\One",
          "when two documents name a home folder, the first one wins")

    # Mapped drives were never classified: no caller supplied them.
    check(scan_aprx(r"C:\P\a.aprx", aprx_texts(".", r"Z:\g.gdb", 0), [],
                    ["Z:"])["gdb"][2] == "NETWORK"
          and scan_aprx(r"C:\P\a.aprx", aprx_texts(".", r"Z:\g.gdb", 0),
                        [])["gdb"][2] == "LOCAL",
          "a default geodatabase on a mapped drive is a share once the drive "
          "letters are supplied, and local when they are not")

    class _MappedKernel(object):
        def GetLogicalDrives(self):
            return (1 << 2) | (1 << 25)

        def GetDriveTypeW(self, root):
            return 4 if root.startswith("Z") else 3

    class _BrokenWindll(object):
        @property
        def kernel32(self):
            raise OSError("no kernel32")

        @property
        def shell32(self):
            raise OSError("no shell32")

    absent = object()

    def with_windll(fake, fn):
        """fn() with ctypes.windll replaced, then put back exactly as it was.

        ctypes has no windll off Windows, so the restore removes the name
        when there was none. Written without a branch so both hosts run the
        same lines.
        """
        saved = vars(ctypes).get("windll", absent)
        ctypes.windll = fake
        try:
            return fn()
        finally:
            vars(ctypes).pop("windll", None)
            vars(ctypes).update(
                dict([("windll", saved)][:int(saved is not absent)]))

    check(with_windll(types.SimpleNamespace(kernel32=_MappedKernel()),
                      winenv.network_drives) == ["Z:"],
          "GetDriveTypeW answering DRIVE_REMOTE (4) makes a drive letter a "
          "network drive, and a fixed drive is left out  <-- pinned defect")
    check(with_windll(_BrokenWindll(), winenv.network_drives) == [],
          "a drive query that fails is no drives")
    check(isinstance(live.network_drives(), list),
          "and the real query answers a list on either platform")
    mapped = fake_healthy()
    mapped._netdrives = ["H:"]
    shell_key = (r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer"
                 r"\User Shell Folders")
    mapped._reg[_norm(shell_key)] = {"Personal": r"H:\Docs"}
    for path, children in ((r"H:\Docs", [("ArcGIS", 0, 0, 0, True)]),
                           (r"H:\Docs\ArcGIS", [("Projects", 0, 0, 0, True)]),
                           (r"H:\Docs\ArcGIS\Projects",
                            [("Maps.aprx", 1000, 1, 0, False)])):
        mapped._tree[_norm(path)] = (path, children)
    mapped._listing[_norm(r"H:\Docs\ArcGIS\Projects")] = (900, 4, None)
    mapped_run = run(mapped, opts_with())
    check("NETWORK" in by_id(mapped_run["checks"], "DL-05")["observed"]
          and by_id(mapped_run["checks"], "DL-05")["status"] == "Fail",
          "a project folder on a mapped drive is a share: DL-05 fails it  "
          "<-- pinned defect")
    check("900 ms" in by_id(mapped_run["checks"], "PF-03")["observed"],
          "and PF-03 times its listing")

    # DL-07: a junction is not a dehydrated file.
    check(not (0x400 & PLACEHOLDER_ATTRS)
          and FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS & PLACEHOLDER_ATTRS
          and FILE_ATTRIBUTE_RECALL_ON_OPEN & PLACEHOLDER_ATTRS,
          "the recall bits mark a placeholder and the reparse-point bit does "
          "not  <-- pinned defect")
    linked = fake_healthy()
    linked._tree[_norm(r"C:\Users\editor\Documents\ArcGIS\Projects")][1].append(
        ("Link", 0, 1, 0x400, True))
    check(probe_placeholders(linked, {})["status"] == "Pass",
          "a junction under the project tree is not counted as a placeholder")

    # Defender: the controls the mutation sweep found unpinned.
    no_hide = decide_defender({"HideExclusionsFromLocalAdmins": False,
                               "ExclusionPath": [], "ExclusionProcess": []},
                              None, base, exe, sysenv)
    check(no_hide["status"] == "Warn" and "hidden" not in no_hide["title"],
          "no exclusions with the hide policy off is a finding, not 'hidden "
          "by policy'  <-- pinned defect")
    shown = decide_defender({"HideExclusionsFromLocalAdmins": True,
                             "ExclusionPath": base,
                             "ExclusionProcess": ["ArcGISPro.exe"]},
                            None, base, exe, sysenv)
    check(shown["status"] == "Pass",
          "and the hide policy on with lists that came back is read normally")
    check(procs_of(["other.exe"]) == "Warn"
          and procs_of([r"D:\Other\ArcGISPro.exe"]) == "Warn",
          "another program, and Pro at another path, are not a process "
          "exclusion for this Pro  <-- pinned defect")
    crowd = decide_defender(
        {"ExclusionPath": [r"C:\Data\p%d" % n for n in range(25)],
         "ExclusionProcess": ["tool%d.exe" % n for n in range(25)]},
        None, base, exe, sysenv)
    crowd_lines = _join(crowd["observed"]).split("\n")
    check(len([l for l in crowd_lines if l.startswith("  path     ")]) == 20
          and len([l for l in crowd_lines if l.startswith("  process  ")])
          == 20,
          "a long exclusion list is cut at twenty lines, with the true count "
          "in the line above")
    check("NOT EXCLUDED  process" in _join(crowd["observed"])
          and "NOT EXCLUDED  process" not in _join(shown["observed"]),
          "the process line appears when Pro is not excluded and only then")
    check("boom" in decide_nic(None, "boom")["observed"]
          and "<unreadable>" in _join(decide_nic([("Dock", "d", None)],
                                                 None)["observed"]),
          "an adapter list that failed names the reason, and an adapter "
          "whose setting is unreadable says so")
    flagged = [decide_nic(None, "e"), decide_nic([], None),
               decide_nic([("a", "d", "Disabled")], None),
               decide_nic([("a", "d", None)], None),
               decide_events([], [("A", "x"), ("B", "y"), ("C", "z")], 3, []),
               decide_events([], [("C", "z")], 3, []),
               decide_pro_running(None)]
    check(all([r["dataloss"] is True for r in flagged]),
          "every DL-04, DL-12 and DL-13 record, Info and Pass as well as "
          "Warn, stays in the data-loss block  <-- pinned defect")

    # The console must not crash on a name the code page cannot hold.
    cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="")
    check(make_output_safe(cp1252) is True,
          "a stream that can be reconfigured is made safe")
    print(u"user \u0416", file=cp1252)
    cp1252.flush()
    check(cp1252.buffer.getvalue() == b"user \\u0416\n",
          "and a character outside its code page prints as an escape instead "
          "of raising UnicodeEncodeError  <-- pinned defect")

    class _NoReconfigure(object):
        def reconfigure(self, **kwargs):
            raise ValueError("cannot")

    check(make_output_safe(io.StringIO()) is False
          and make_output_safe(_NoReconfigure()) is False,
          "while a stream that cannot be reconfigured is left alone")

    # --json is one document, with or without --report.
    jr_dir = tempfile.mkdtemp(prefix="prostall-json-report-")
    try:
        code, out, err = with_env(fake_healthy, ["--json", "--report", jr_dir])
        check(json.loads(out)["prostall"] == 2,
              "--json with --report prints one JSON document on stdout  "
              "<-- pinned defect")
        check("report :" in err and "report :" not in out,
              "and the report paths go to stderr")
        check(len(os.listdir(jr_dir)) == 3, "while all three files are written")
    finally:
        shutil.rmtree(jr_dir, ignore_errors=True)

    class OffMachine(FakeEnv):
        windows = False

    code, out, err = with_env(OffMachine, ["--json"])
    check("almost every probe" in err and "almost every probe" not in out
          and json.loads(out)["prostall"] == 2,
          "off Windows the note goes to stderr and stdout stays a document")

    # The reading layer, reached with stand-ins instead of left unreached.
    fake_dll = types.SimpleNamespace(
        shell32=types.SimpleNamespace(IsUserAnAdmin=lambda: 1))
    check(with_windll(fake_dll, winenv.elevated) is True
          and with_windll(_BrokenWindll(), winenv.elevated) is False,
          "the elevation answer is read, and a failing query is not elevated")

    def fill_memory(ref):
        ref._obj.ullTotalPhys = 8 * 1024 ** 3
        ref._obj.ullAvailPhys = 2 * 1024 ** 3
        return 1

    check(with_windll(types.SimpleNamespace(kernel32=types.SimpleNamespace(
        GlobalMemoryStatusEx=fill_memory)), winenv.memory) == (8.0, 2.0),
        "memory is read in gigabytes from the two physical-memory fields")
    check(with_windll(types.SimpleNamespace(kernel32=types.SimpleNamespace(
        GlobalMemoryStatusEx=lambda ref: 0)), winenv.memory) == (None, None)
        and with_windll(_BrokenWindll(), winenv.memory) == (None, None),
        "and a call that reports failure, or raises, is unread")

    registry_calls = []

    class _Key(object):
        def Close(self):
            registry_calls.append("close")

    def create_key(hive, sub, reserved, access):
        registry_calls.append(("create", hive, sub, access))
        return _Key()

    fake_winreg = types.ModuleType("winreg")
    fake_winreg.HKEY_CURRENT_USER = "hkcu"
    fake_winreg.HKEY_LOCAL_MACHINE = "hklm"
    fake_winreg.KEY_SET_VALUE = 2
    fake_winreg.REG_SZ = 1
    fake_winreg.CreateKeyEx = create_key
    fake_winreg.SetValueEx = lambda key, name, reserved, kind, value: \
        registry_calls.append(("set", name, kind, value))
    real_winreg = sys.modules.get("winreg", absent)
    sys.modules["winreg"] = fake_winreg
    try:
        live.reg_write(GPU_PREFERENCE_KEY, "Pro.exe", "GpuPreference=2;")
    finally:
        sys.modules.pop("winreg", None)
        sys.modules.update(
            dict([("winreg", real_winreg)][:int(real_winreg is not absent)]))
    check(registry_calls == [
        ("create", "hkcu", r"Software\Microsoft\DirectX\UserGpuPreferences", 2),
        ("set", "Pro.exe", 1, "GpuPreference=2;"), "close"],
        "the one registry write sets a string value under the current user "
        "and closes the key, proved against a stand-in registry module that "
        "never touches the real one")

    def raise_os(*args, **kwargs):
        raise OSError("boom")

    tmp = tempfile.gettempdir()
    real_isdir, real_isfile = os.path.isdir, os.path.isfile
    real_listdir, real_stat = os.listdir, os.stat
    os.path.isdir = raise_os
    try:
        dir_error = live.isdir(tmp)
    finally:
        os.path.isdir = real_isdir
    os.path.isfile = raise_os
    try:
        file_error = live.isfile(tmp)
    finally:
        os.path.isfile = real_isfile
    os.listdir = raise_os
    try:
        list_error = live.subdirs(tmp)
    finally:
        os.listdir = real_listdir
    check(dir_error is False and file_error is False and list_error == [],
          "a folder that vanishes or locks mid-read answers absence, never a "
          "traceback")
    scan_root = tempfile.mkdtemp(prefix="prostall-scan-")
    try:
        for name in ("a.txt", "b.txt"):
            handle = io.open(os.path.join(scan_root, name), "w")
            handle.close()

        def stat_denies(path, *args, **kwargs):
            if str(path).endswith("a.txt"):
                raise OSError("denied")
            return real_stat(path, *args, **kwargs)

        os.stat = stat_denies
        try:
            walked, walked_bad = live.scan(scan_root)
        finally:
            os.stat = real_stat
        check([e.name for e in walked] == ["b.txt"] and len(walked_bad) == 1
              and "denied" in walked_bad[0],
              "a file whose attributes cannot be read is named as unreadable "
              "and the walk goes on")
    finally:
        shutil.rmtree(scan_root, ignore_errors=True)

    started = time.time()
    raises(lambda: winenv._run([sys.executable, "-c",
                                "import time; time.sleep(30)"], timeout=0.5),
           "a child that hangs is killed at its timeout and raises, instead "
           "of holding the run for two minutes",
           kind=subprocess.TimeoutExpired)
    check(time.time() - started < 20, "and it was killed, not waited for")
    check(winenv._run([sys.executable, "-c",
                       "import sys; sys.stderr.write('oops\\n'); sys.exit(3)"])
          == (3, "", "oops")
          and winenv._run([sys.executable, "-c", "import sys; sys.exit(4)"])[2]
          == "exit code 4",
          "a child's exit code, output and first error line are returned, "
          "and a silent failure still gets a reason")

    lines, code = summarize(3, ["it broke"])
    check(code == 1 and lines == ["4 assertions, 1 failed",
                                  "  FAILED: it broke"],
          "a failing self-test says how many failed, names each, and exits 1")
    check(summarize(2, []) == (["2 assertions, 0 failed"], 0),
          "and a clean one exits 0")
    imported = runpy.run_path(os.path.abspath(__file__),
                              run_name="prostall_imported")
    check(callable(imported["main"]),
          "importing the file defines the tool and runs nothing")

    # ------------------------------------------ boundaries and defaults, pinned
    print("-" * 70)
    print("boundaries and defaults that a mutation sweep found unpinned")
    no_apply_key = dict([(k, v) for k, v in opts_with().items()
                         if k != "apply"])
    keyless_env = fake_sick()
    keyless = run(keyless_env, no_apply_key)
    check(keyless["applied"] == [] and keyless_env.written == [],
          "options that never mention --apply apply nothing: the default is "
          "off, so a caller that forgets the key cannot arm the fixes  "
          "<-- pinned defect")
    check(fix_cache_aside(FakeEnv(), [], {"cache_path": r"C:\nowhere"})
          is None and fix_cache_aside(FakeEnv(), [], {}) is None,
          "a cache folder that is gone, or never named, is left alone  "
          "<-- pinned defect")
    applied_sick = run(fake_sick(), opts_with(apply=True))["applied"]
    check([r["result"] for r in applied_sick if r["id"] == "AP-02"][0]
          .endswith("(previous: <none>)"),
          "a graphics preference with no previous value says so in its result")
    check(decide_pro_running([])["dataloss"] is True
          and decide_pro_running(["x"])["dataloss"] is True,
          "DL-12 stays in the data-loss block when Pro is closed as well as "
          "when it runs  <-- pinned defect")
    check("only network file-share (smb) connection events"
          in smb_only["result"].lower(),
          "an SMB-only timeline says so in its result  <-- pinned defect")
    some_unreadable = decide_events([], [("C", "z")], 3, [])
    check("nothing recorded" in some_unreadable["result"].lower()
          and "nothing recorded" not in decide_events(
              [], [("A", "x"), ("B", "y"), ("C", "z")], 3,
              [])["result"].lower(),
          "one unreadable log of three is a quiet timeline and three of "
          "three is an unread one  <-- pinned defect")
    listed = [l for l in _join(decide_events(
        forty + [("2026-09-02 00:00:00", "e40")], [], 3, [])["observed"])
        .split("\n") if l.startswith("2026-09-0")]
    check(len(listed) == 40,
          "forty-one events list forty rows, not all of them  "
          "<-- pinned defect")
    check(decide_share_latency([])["status"] == "Info"
          and "No recent project lives on a share"
          in decide_share_latency([])["observed"],
          "no project on a share is Info and says why")
    check("more share folder" not in _join(decide_share_latency(
        [("a", 40, 3, None)])["observed"])
        and "older recent project" not in _join(decide_aprx(
            "closed", [project()])["observed"]),
        "a cap that omitted nothing prints no line about omissions  "
        "<-- pinned defect")
    check(FILE_ATTRIBUTE_RECALL_ON_OPEN == 0x40000
          and FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS == 0x400000,
          "the two recall attribute bits are the documented values, not "
          "whatever the constants happen to hold  <-- pinned defect")
    check(decide_tcp("T-1", "Network", "t", "h", 1, (True, 250, None), 250,
                     "i", "f")["status"] == "Pass"
          and decide_tcp("T-1", "Network", "t", "h", 1, (True, 251, None),
                         250, "i", "f")["status"] == "Warn",
          "a connect exactly at the allowance passes and one millisecond "
          "over warns")
    for flag, value, wanted in (("--timeout", "1", False),
                                ("--sde-port", "1", False),
                                ("--sde-port", "65535", False),
                                ("--sde-port", "0", True),
                                ("--sde-port", "65536", True)):
        code, _out, _err = with_env(fake_healthy, [flag, value])
        check((code == 64) is wanted,
              "%s %s is %s  <-- pinned defect"
              % (flag, value, "refused" if wanted else "accepted"))
    healthy_doc = run(fake_healthy(), opts_with())
    sick_doc = run(fake_sick(), opts_with())
    for label, doc in (("healthy", healthy_doc), ("sick", sick_doc)):
        rendered = render(doc)
        split = [n for n, l in enumerate(rendered)
                 if l.startswith("== PERFORMANCE")][0]
        placed = [(rendered.count(render_check(c)[0]),
                   [n for n, l in enumerate(rendered)
                    if l == render_check(c)[0]][0] < split, c["dataloss"])
                  for c in doc["checks"]]
        check(all([once == 1 and before is dataloss
                   for once, before, dataloss in placed]),
              "%s run: every check prints exactly once, data-loss ahead of "
              "the performance heading and performance after it  "
              "<-- pinned defect" % label)
        check(bool(doc["fixable"]) == any(["can make" in l
                                           and "changes for you" in l
                                           for l in rendered]),
              "%s run: the fix offer prints exactly when a fix exists  "
              "<-- pinned defect" % label)
    check(sick_doc["fixable"], "and the sick run does have one")
    code, out, err = with_env(fake_healthy, ["--json"])
    check("almost every probe" not in err
          and out.strip() == json.dumps(json.loads(out), indent=1,
                                        sort_keys=True),
          "on a Windows host there is no off-Windows note, and the JSON keys "
          "are sorted so two reports diff cleanly  <-- pinned defect")

    # ------------------------------------------------------- the HTML report
    print("-" * 70)
    print("the HTML report: same findings, with folders and scripts")
    sick_html = render_html(sick_doc)
    healthy_html = render_html(healthy_doc)
    check(sick_html.startswith("<!doctype html>")
          and sick_html.rstrip().endswith("</html>"),
          "the HTML report is one whole page")
    check(all([('id="%s"' % c["id"]) in sick_html for c in sick_doc["checks"]]),
          "every check has a card the steps can link to")
    check(all([('id="step-%d"' % n) in sick_html
               for n, c in next_steps(sick_doc)]),
          "and every step in the to-do list is there, numbered")
    check("<script src" not in sick_html and "<link" not in sick_html
          and "http://" not in sick_html and "https://" not in sick_html,
          "it fetches nothing, so it opens the same on a machine with no "
          "internet")
    leaky = dict(sick_doc, checks=[dict(c, result='<b onclick="x">&')
                                   for c in sick_doc["checks"]])
    check('<b onclick' not in render_html(leaky)
          and "&lt;b onclick=&quot;x&quot;&gt;&amp;" in render_html(leaky),
          "text from the machine is escaped, so a path holding < or & cannot "
          "break the page  <-- pinned defect")
    check(find_paths(r"No value named C:\Program Files\ArcGIS\Pro\bin"
                     r"\ArcGISPro.exe under HKCU\x, so")
          == [r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe"],
          "a path with spaces in it is found whole, and ends where the "
          "sentence carries on")
    check(find_paths(r"see \\gisfs01\projects\Roads.aprx.")
          == [r"\\gisfs01\projects\Roads.aprx"],
          "a share path is found, without the full stop after it")
    check(find_paths(r"C:\ 5.00 GB free of 500.00 GB") == [],
          "and a bare drive followed by a number is not taken for a folder")
    temp_check = by_id(sick_doc["checks"], "PF-07")
    check(check_paths(temp_check)[:2]
          == [r"C:\Users\editor\AppData\Local\Temp\ArcGISProTemp12345",
              r"C:\Users\editor\AppData\Local\Temp\ArcGISProTemp24680"],
          "the session folders that may hold unsaved work are listed first, "
          "by their full path")
    check(len(check_paths(by_id(sick_doc["checks"], "DL-11"))) <= 5,
          "a long list of files is shown as the folders that hold them")
    check("Copy path" in sick_html and "file:///C:/Users/editor/AppData/"
          "Local/ESRI/Local%20Caches" in sick_html,
          "each folder has a Copy button and a link that opens it")
    temp_scripts = cleanup_scripts(temp_check)
    recycle = [code for title, what, code in temp_scripts
               if "Recycle" in title][0]
    check("$Preview = $true" in recycle and "SendToRecycleBin" in recycle
          and "Remove-Item" not in recycle,
          "the folder cleanup previews by default and sends to the Recycle "
          "Bin, never deleting outright  <-- pinned defect")
    check("'ArcGISProTemp12345', 'ArcGISProTemp24680'" in recycle
          and "*.aprx" in recycle,
          "and it keeps the folders this report says may hold unsaved work")
    every_script = [code for c in sick_doc["checks"]
                    for t, w, code in cleanup_scripts(c)]
    check(every_script and not [code for code in every_script
                                if "Remove-Item" in code or " del " in code],
          "no offered script deletes a file outright")
    check(all([code.startswith("& {") and code.rstrip().endswith("}")
               for code in every_script if "\n" in code]),
          "every multi-line script is one block, so a guard that says stop "
          "stops the whole paste, not one line of it  <-- pinned defect")
    check(all(["Get-Process ArcGISPro" in code
               for c in sick_doc["checks"] for t, w, code in cleanup_scripts(c)
               if "Rename-Item" in code or "Recycle" in t]),
          "a script that moves or recycles Pro's files first checks that Pro "
          "is closed")
    check("Scripts that can help" in sick_html
          and "Scripts that can help" not in healthy_html,
          "scripts are offered only for steps, so a healthy machine gets none")
    check("Nothing needs fixing" in healthy_html,
          "a healthy run says nothing needs fixing")
    check("Almost nothing could be checked"
          in render_html(dict(document, checks=always, fixable=[], notes=[],
                              applied=[])),
          "and a run that measured nothing does not claim to be healthy")
    html_dir = tempfile.mkdtemp(prefix="prostall-html-")
    try:
        target = os.path.join(html_dir, "sub", "report.html")
        FakeEnv.opened[:] = []
        code, out, err = with_env(fake_healthy, ["--html", target])
        check(os.path.isfile(target) and "web page : " + target in out,
              "--html writes the page, creates its folder, and names it")
        check(FakeEnv.opened == [target] and "Opened the web page" in out,
              "and opens it in the browser")
        FakeEnv.opened[:] = []
        with_env(fake_healthy, ["--html", target, "--no-open"])
        check(FakeEnv.opened == [], "but not with --no-open")
        report_into = os.path.join(html_dir, "both")
        with_env(fake_healthy, ["--html", target, "--report", report_into])
        check(FakeEnv.opened == [target],
              "with --html and --report together only one page opens")
        FakeEnv.opened[:] = []
        with_env(fake_healthy, ["--report", report_into])
        check(len(FakeEnv.opened) == 1
              and FakeEnv.opened[0].startswith(report_into)
              and FakeEnv.opened[0].endswith(".html"),
              "--report alone opens the page it wrote")
        FakeEnv.opened[:] = []
        with_env(fake_healthy, [])
        check(FakeEnv.opened == [],
              "and a plain run, which writes nothing, opens nothing")
        quiet = Env()
        quiet.windows = False
        quiet.getenv = lambda name: None
        check("no desktop" in (quiet.open_file(target) or ""),
              "off Windows with no desktop it declines rather than start a "
              "text browser in the terminal")
        code, out, err = with_env(fake_healthy, ["--json", "--html", target])
        check(json.loads(out)["prostall"] == 2 and "web page" in err,
              "with --json the page is still written and stdout stays one "
              "document")
    finally:
        shutil.rmtree(html_dir, ignore_errors=True)

    # ------------------------------------------------- the one-click launcher
    launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "prostall.bat")
    if os.path.isfile(launcher):
        handle = open(launcher, "rb")
        try:
            batch = handle.read().decode("ascii")
        finally:
            handle.close()
        check("\n" not in batch.replace("\r\n", ""),
              "prostall.bat has Windows line endings throughout, which cmd.exe "
              "needs to find its labels  <-- pinned defect")
        check('"%SCRIPT%" --report' in batch and "--apply" not in batch,
              "the launcher writes the report and never runs --apply")
        check("pause" in batch,
              "and it waits for a key, so the window does not vanish with the "
              "result")

    # ------------------------------------------------------- the harness itself
    print("-" * 70)
    print("the harness records a failure as a failure")
    kept_passed, kept_failed = passed[0], list(failed)
    buffer = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buffer
    try:
        check(False, "a false check must be recorded as a failure")
        raises(lambda: None, "a function that raises nothing must fail")
        raises(lambda: 1 / 0, "a function that raises the wrong thing must fail")
        rejects(["--self-test"], "an argument list argparse accepts must fail")
    finally:
        sys.stdout = real_stdout
    probe_passed, probe_failed = passed[0], list(failed)
    passed[0], failed[:] = kept_passed, kept_failed
    check(len(probe_failed) - len(kept_failed) == 4,
          "a false check, a missing exception, a wrong exception and an "
          "accepted argument list are four failures, so a broken tool turns "
          "this self-test red  <-- pinned defect")
    check(probe_passed == kept_passed, "not one of those four counted as a pass")
    check(buffer.getvalue().count("FAIL  ") == 4,
          "and every one of them printed a line the operator can see")

    # ----------------------------------------------- the configuration block
    print("-" * 70)
    print("the configuration block: a switch that is off says so")
    check(sorted(CHECKS_ENABLED) == sorted(check_ids()),
          "CHECKS_ENABLED lists every registered check, so none is hidden "
          "from the operator editing it")
    check(sorted(FIXES_ENABLED) == sorted(fix_ids()),
          "and FIXES_ENABLED lists every fix")
    check(config_problems(CHECKS_ENABLED, FIXES_ENABLED) == [],
          "the shipped configuration is accepted")
    check(config_problems({"PF-6": False}, {})
          and config_problems({}, {"AP-03": False})
          and config_problems({"PF-06": "no"}, {}),
          "a mistyped id or a value that is not a bool is refused, so a "
          "check the operator turned off cannot quietly keep running")
    off_env = fake_sick()
    off_run = run(off_env, opts_with(apply=True,
                                     checks_enabled={"PF-06": False},
                                     fixes_enabled={"AP-02": False}))
    off_check = by_id(off_run["checks"], "PF-06")
    check(off_check is not None and off_check["status"] == "Skipped"
          and "CHECKS_ENABLED" in off_check["observed"],
          "a check turned off is still in the report, as Skipped, rather "
          "than vanishing and reading as a pass")
    check(len(off_run["checks"]) == len(check_ids()),
          "and the report still has one record per check")
    check(off_run["applied"] == [] and off_env.written == [],
          "the fix whose check is off is refused, and the fix turned off "
          "never runs, even with --apply")
    check([n for n in off_run["notes"] if n.startswith("off [AP-02]")],
          "and the note says which fix was turned off")
    real_allow = ALLOW_APPLY
    globals()["ALLOW_APPLY"] = False
    try:
        code, _out, err = run_main(["--apply"])
        check(code == 64 and "ALLOW_APPLY" in err,
              "ALLOW_APPLY False refuses --apply before any check runs")
    finally:
        globals()["ALLOW_APPLY"] = real_allow
    real_checks = CHECKS_ENABLED
    globals()["CHECKS_ENABLED"] = {"PF-6": False}
    try:
        code, _out, err = run_main([])
        check(code == 64 and "PF-6" in err,
              "a typo in CHECKS_ENABLED stops the run with a usage error")
    finally:
        globals()["CHECKS_ENABLED"] = real_checks

    print("-" * 70)
    lines, code = summarize(passed[0], failed)
    for line in lines:
        print(line)
    return code


# =============================================================================
# COMMAND LINE
# =============================================================================

USAGE_ERROR = 64


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a bad flag, and 2 means a finding that bears on
    lost edits. A mistyped or abbreviated flag must not read as that."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("%s: error: %s\n" % (self.prog, message))
        sys.exit(USAGE_ERROR)


def host_problem(host):
    """Why this is not a host name, or None. A URL pasted as a host is the
    common typo, and a probe of it would blame a server nobody asked about."""
    if not host or not host.strip():
        return "it is empty"
    if re.search(r"[\s/\\?#@]", host):
        return "it holds a space, a slash or another character a host name " \
               "cannot (give the host only, with no scheme and no path)"
    return None


def make_output_safe(stream):
    """Make a stream that cannot encode a character print an escape instead.

    A redirected console on a cp1252 Windows host raised UnicodeEncodeError
    on a user name or path outside that code page, and the traceback exited 1.
    Returns False when the stream cannot be reconfigured.
    """
    try:
        stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError, OSError):
        return False
    return True


def _parse(argv):
    ap = _Parser(
        prog="prostall.py",
        allow_abbrev=False,
        description="Diagnose why ArcGIS Pro is slow on this workstation, and "
                    "never report a finding the machine did not produce.",
        epilog="Check by default. Without --apply nothing on the machine "
               "changes, and without --sde-server or --portal no host is "
               "contacted, apart from reading a project folder that already "
               "lives on a share. Connection files are recorded by path and "
               "never opened.")
    ap.add_argument("--self-test", dest="self_test", action="store_true",
                    help="run the offline assertions and exit")
    ap.add_argument("--verbose", action="store_true",
                    help="print the evidence, expected values and details for "
                         "every check, not only the ones that need action")
    ap.add_argument("--json", action="store_true",
                    help="print the report as JSON instead of the console form")
    ap.add_argument("--report", metavar="DIR", default=REPORT_DIR,
                    help="write the text, JSON and HTML reports into this "
                         "directory. "
                         "Without it nothing is written anywhere.")
    ap.add_argument("--html", metavar="FILE", default=HTML_FILE,
                    help="also write the report as a web page to this file, "
                         "with copyable folder paths and cleanup scripts. "
                         "--report writes one too. It opens in your "
                         "browser unless --no-open is given.")
    ap.add_argument("--no-open", dest="no_open", action="store_true",
                    help="write the HTML report but do not open it")
    ap.add_argument("--sde-server", dest="sde_server", metavar="HOST",
                    default=SDE_SERVER,
                    help="TCP-probe this enterprise geodatabase host. Connect "
                         "only, no credential. Without it the probe is skipped.")
    ap.add_argument("--sde-port", dest="sde_port", type=int, default=SDE_PORT,
                    metavar="PORT",
                    help="port for the geodatabase probe (default %d)"
                         % SDE_PORT)
    ap.add_argument("--portal", metavar="HOST", default=PORTAL,
                    help="TCP-probe this portal host on 443. Without it the "
                         "probe is skipped.")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_MS, metavar="MS",
                    help="bound on each network probe in milliseconds "
                         "(default %d)" % TIMEOUT_MS)
    ap.add_argument("--apply", action="store_true",
                    help="run the gated fixes. Off by default. Each one is "
                         "refused unless its check actually failed, prints its "
                         "own undo, and never deletes anything.")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)

    # The self-test wins over everything. A test run must never be able to
    # reach a real machine, whatever else is on the command line.
    if args.self_test:
        return self_test()

    for problem in config_problems(CHECKS_ENABLED, FIXES_ENABLED):
        print("error: the configuration block: %s." % problem,
              file=sys.stderr)
        return USAGE_ERROR
    if args.apply and not ALLOW_APPLY:
        print("error: --apply is turned off by ALLOW_APPLY in the "
              "configuration block, so nothing was run.", file=sys.stderr)
        return USAGE_ERROR
    if args.timeout < 1:
        print("error: --timeout must be at least 1 millisecond.",
              file=sys.stderr)
        return USAGE_ERROR
    if not 1 <= args.sde_port <= 65535:
        print("error: --sde-port must be between 1 and 65535, got %d."
              % args.sde_port, file=sys.stderr)
        return USAGE_ERROR
    for flag, host in (("--sde-server", args.sde_server),
                       ("--portal", args.portal)):
        if host is not None and host_problem(host):
            print("error: %s %r is not a host name: %s."
                  % (flag, host, host_problem(host)), file=sys.stderr)
            return USAGE_ERROR

    make_output_safe(sys.stdout)

    env = Env()
    if not env.windows:
        # stderr, not stdout: --json must emit a document and nothing else,
        # or the caller parsing it gets a note where the report should be.
        print("note: this is a Windows workstation diagnostic. On %s almost "
              "every probe reports Skipped or Info, which is what an "
              "undetermined check looks like. Use --self-test to verify the "
              "tool itself here." % sys.platform, file=sys.stderr)

    document = run(env, {"sde_server": args.sde_server,
                         "sde_port": args.sde_port,
                         "portal": args.portal,
                         "timeout": args.timeout,
                         "apply": args.apply,
                         "checks_enabled": CHECKS_ENABLED,
                         "fixes_enabled": FIXES_ENABLED})

    if args.json:
        print(json.dumps(document, indent=1, sort_keys=True))
    else:
        for line in render(document, args.verbose):
            print(line)

    # With --json, stdout is one document and nothing else.
    out = sys.stderr if args.json else sys.stdout
    if args.html:
        try:
            html_path = write_html(document, args.html)
        except (OSError, IOError) as exc:
            print("error: could not write the HTML report: %s" % exc,
                  file=sys.stderr)
            return USAGE_ERROR
        print("", file=out)
        print("  web page : %s" % html_path, file=out)

    if args.report:
        try:
            paths = write_report(document, args.report, args.verbose)
        except (OSError, IOError) as exc:
            print("error: could not write the report: %s" % exc,
                  file=sys.stderr)
            return USAGE_ERROR
        print("", file=out)
        for path in paths:
            print("  report : %s" % path, file=out)
        print("  Diff the JSON against a known-good workstation to isolate "
              "what is different about this one.", file=out)
        if not args.html:
            html_path = paths[2]

    # One page opens, even when both --html and --report wrote one.
    if (args.html or args.report) and OPEN_HTML and not args.no_open:
        why = env.open_file(html_path)
        if why:
            print("  Could not open the web page automatically (%s). Open "
                  "the file above in a browser." % why, file=out)
        else:
            print("  Opened the web page in your browser.", file=out)

    return exit_code(document["checks"])


if __name__ == "__main__":
    sys.exit(main())
