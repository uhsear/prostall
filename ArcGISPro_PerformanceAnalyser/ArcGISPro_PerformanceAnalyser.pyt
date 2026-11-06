"""ArcGIS Pro Performance Analyser - one file, no dependencies.

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
The in-Pro workstation tool is always read-only. AP-01 belongs to the separate
command-line run with Pro closed; the former AP-02 GPU preference change was
retired because forcing an adapter can cause a startup crash. Project recovery
requires its own explicit mode and writes only a new copy. No original project
or source data is deleted.

Provenance
----------
  arcgis_pro_performance_analyser.py       11020 lines  sha256:ec1365b64955
  html_report.py                             966 lines  sha256:184c9b6eaf73
  toolbox_glue.py                            869 lines  sha256:9b8784c4dacf
"""

from __future__ import print_function

# ============================================================================
# 1/3  diagnostic engine
# source: arcgis_pro_performance_analyser.py  (sha256:ec1365b64955)
# ----------------------------------------------------------------------------
# r"""Diagnose why ArcGIS Pro is slow on this workstation, and never report a finding the machine did not produce.
#
# An editor says Pro takes five minutes to open, hangs mid-edit, and sometimes
# her saved edits are gone. There is no error message. The causes are spread
# across the registry, the filesystem, OneDrive's redirection state, Offline
# Files and the per-app GPU preference, and no single pane shows them together.
# The deeper problem is the diagnostic itself. A check that prints a verdict on
# something it never measured teaches the operator to distrust the whole report,
# and the report is the only thing standing between her and a rebuild.
#
# ArcGIS Pro ships a Diagnostic Monitor, and it is the right tool for what it
# does: it traces Pro from inside the process, timestamps every event, and hands
# Esri support a log they can read. It cannot see the workstation. Known Folder
# Move, a dehydrated project file, Offline Files, the GPU Windows chose, the
# storage class of the project folder - all of that is outside the process, and a
# session that never finished opening produces no trace at all. That is the gap
# this fills, from the outside, before the case is opened.
#
# Check by default. A plain run reads and prints. --apply changes only the gated
# cache setting, --project-repair writes only a separately verified recovery
# copy, and --quarantine-addin moves one exact PF-31 package into a verified,
# restorable quarantine. --restore-addin reverses that move without overwriting.
# Every applied change prints its own undo; no source project is rewritten and
# no quarantined package is erased. The network probes
# are opt-in: without --sde-server and --portal no host is contacted. The one
# exception is a project folder that already lives on a share: reading it crosses
# the network, as Pro does when it opens the project, and PF-03 times one listing
# of it. Selected-project data sources are never contacted.
#
#     python arcgis_pro_performance_analyser.py --self-test
#     python arcgis_pro_performance_analyser.py
#     python arcgis_pro_performance_analyser.py --sde-server gisdb01 --portal gis.example.org --verbose
#     python arcgis_pro_performance_analyser.py --report C:\Temp\properf
#     python arcgis_pro_performance_analyser.py --project C:\\GIS\\Review\\a.aprx
#     python arcgis_pro_performance_analyser.py --project C:\\GIS\\Review\\a.aprx --project-repair views
#     python arcgis_pro_performance_analyser.py --apply
#     python arcgis_pro_performance_analyser.py --quarantine-addin C:\AddIns\Vendor.esriAddinX
#     python arcgis_pro_performance_analyser.py --restore-addin C:\Quarantine\quarantine.json
#
# Exit codes: 0 nothing to act on, 1 measured findings without lost-edit impact,
# 2 at least one finding bearing on lost edits, 3 no finding but nothing was determined either,
# 64 usage error or a report that could not be written.
#
# The __main__ guard is dropped: ArcGIS Pro imports this file, it does not run it.
# ============================================================================


import argparse
import calendar
import collections
import json
import ntpath
import os
import re
import socket
import sys
import time

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

# Report format version. Bumped when the JSON shape changes, so a diff against
# an older report from a known-good workstation fails loudly instead of quietly.
# 2 adds the kpi mapping on the checks that measure a number, which is what the
# HTML report draws its gauges from. 4 adds structured French remediation and
# the selected-project report; older readers can still ignore the new fields.
REPORT_VERSION = 4

# Thresholds, named here rather than buried at the call site.
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

# Project complexity. These are this tool's allowances. A project that opens
# slowly because it is enormous is not a workstation fault, but it is an answer,
# and it is the answer that stops the rest of the report being read as the
# whole story.
APRX_BIG_BYTES = 20 * 1024 ** 2
LAYERS_MANY = 150
LAYERS_WARN = 60
GROUP_DEEP_WARN = 4
HTTP_LAYERS_WARN = 3
JOINS_WARN = 3

# A rotational disk holding the caches and the scratch workspace is the single
# most common hardware cause of a slow session, and the only one that no
# setting fixes. Compared in upper case, so "Unspecified" from the cmdlet and
# "unspecified" from a localised build are the same answer.
DISK_MEDIA_WARN = ("HDD", "UNSPECIFIED")

# A link below this cannot carry an enterprise editing session and a basemap
# at the same time. 1 GbE is the floor for a wired GIS workstation; 100 Mbit
# still ships on managed images and on docking stations.
LINK_SPEED_MIN_MBPS = 1000

# A pagefile smaller than this cannot absorb a 32-bit-equivalent draw spike
# on a machine that is already near its memory ceiling, and a pagefile on a
# network volume is a stall nobody will find in the Pro logs.
PAGEFILE_MIN_MB = 4096

# ---------------------------------------------------------------------------
# Esri's PUBLISHED ArcGIS Pro system requirements, read off the official pages
# (3.6 English and 3.7 French, both opened). These are Esri's figures, quoted,
# and the report says whose figures they are. Everything else in this file is
# an allowance this tool chose for itself, and says so.
#
#   https://doc.esri.com/fr/arcgis-pro/latest/get-started/
#       arcgis-pro-system-requirements.html
#   https://pro.arcgis.com/en/pro-app/3.6/get-started/
#       arcgis-pro-system-requirements.htm
# ---------------------------------------------------------------------------
ESRI_CPU_MIN_CORES = 2         # minimum, with simultaneous multithreading
ESRI_CPU_REC_CORES = 4         # recommended
ESRI_CPU_OPT_CORES = 10        # optimal
ESRI_RAM_MIN_GB = 8.0
ESRI_RAM_REC_GB = 32.0
ESRI_RAM_OPT_GB = 64.0
ESRI_FREE_DISK_GB = 32.0       # minimum free, and on an SSD when recommended
ESRI_VIZ_CACHE_GB = 32.0       # the visualization cache alone may take this
ESRI_GPU_VRAM_REC_GB = 8.0     # dedicated, not shared

GPU_DRIVER_MAX_DAYS = 365      # past a year, "update the driver" is the answer
# Esri's own performance readout names WARP explicitly: it means Pro found no
# capable GPU and is emulating one on the CPU, which is slow by construction.
GPU_EMULATED_MARKERS = ("WARP", "BASIC DISPLAY", "MICROSOFT BASIC")
# Esri recommends "a discrete (not integrated) GPU that is supported by the
# manufacturer". A machine whose only adapter is integrated is below that
# recommendation, which is a Warn and not a fault: it works, it just draws
# slower, and on a notebook it is often the only option there is.
GPU_INTEGRATED_MARKERS = ("INTEL UHD", "INTEL HD", "INTEL IRIS",
                          "INTEL(R) HD", "INTEL(R) UHD", "INTEL(R) IRIS",
                          "AMD RADEON(TM) GRAPHICS", "RADEON RX VEGA 8",
                          "RADEON RX VEGA 11", "VEGA 8", "VEGA 11",
                          "MICROSOFT BASIC")
GPU_DISCRETE_MARKERS = ("GEFORCE", "RTX", "GTX", "QUADRO", "RTX PRO",
                        "RADEON PRO", "RADEON RX", "TITAN", "ARC A")

# Data folder weight. Measured by walking the folder a recent project lives
# in, never by opening the .aprx: this check has to keep answering from inside
# ArcGIS Pro, where the project-file checks are refused on purpose.
DATA_SCAN_DEPTH = 4
DATA_SCAN_LIMIT = 20000
DATA_GDB_WARN_BYTES = 10 * 1024 * 1024 * 1024   # 10 GiB in one file geodatabase
DATA_FILES_WARN = 5000        # file COUNT matters for a synced folder, not size

# ---------------------------------------------------------------------------
# SOURCES. Where each remediation comes from.
#
# Every URL here was actually opened, either with the fetch tool or through a
# search result, before it was written down. A check with no honest source is
# simply absent from this map and the report prints no Sources block for it:
# inventing a citation to make a row look supported is worse than an empty row.
# ---------------------------------------------------------------------------

_ESRI_PERF = ("Esri - R&eacute;glages qui influent sur les performances "
              "d'acc&eacute;s aux donn&eacute;es",
              "https://doc.esri.com/en/arcgis-pro/latest/get-started/"
              "layer-map-project-perf-optimization.html")
_ESRI_TROUBLE = ("Esri - D&eacute;pannage des probl&egrave;mes de performance "
                 "dans ArcGIS Pro",
                 "https://community.esri.com/t5/arcgis-pro-documents/"
                 "troubleshooting-performance-issues-in-arcgis-pro/ta-p/"
                 "1025516")
_ESRI_3D = ("Esri - FPS, Tri/Sec, MemMb et l'affichage WARP",
            "https://www.esri.com/arcgis-blog/products/3d-gis/3d-gis/"
            "troubleshooting-performance-issues-in-arcgis-pro")
_ESRI_CACHE = ("Esri - Rendu lent : vider le cache de l'espace de travail",
               "https://support.esri.com/en-us/knowledge-base/problem-convert-"
               "labels-to-graphics-renders-slowly-in-ar-000034620")
_ESRI_CA = ("Esri Canada - D&eacute;panner ArcGIS Pro comme un analyste du "
            "support", "https://resources.esri.ca/getting-technical/"
            "troubleshooting-arcgis-pro-like-an-esri-canada-support-analyst-"
            "part-1")
_ESRI_PROJ = ("Esri Community - Vitesse d'un projet ArcGIS Pro",
              "https://community.esri.com/t5/arcgis-utility-network-questions/"
              "arcgis-pro-project-speed/td-p/1581320")
_ESRI_RECOVERY = ("Esri - Ce qu'une sauvegarde de projet permet de r&eacute;cup&eacute;rer",
                  "https://support.esri.com/en-us/knowledge-base/faq-what-is-"
                  "recovered-when-an-arcgis-pro-project-file-a-000026957")
_ESRI_PROJECT_FILES = ("Esri - Emplacements des projets et des donn&eacute;es",
                       "https://pro.arcgis.com/en/pro-app/latest/get-started/"
                       "project-files-data-and-pro.htm")
_ESRI_CIM_PROJECT = ("Esri - CIMGISProject : vues et &eacute;tat de l'interface",
                     "https://doc.esri.com/en/arcgis-pro/latest/sdk/"
                     "api-reference/topic1836.html")
_ESRI_PACKAGE_PROJECT = ("Esri - Empaqueter un projet, avec ou sans historique",
                         "https://pro.arcgis.com/en/pro-app/latest/tool-reference/"
                         "data-management/package-project.htm")
_ESRI_GP_HISTORY = ("Esri Community - Nettoyage de l'historique dans GISProject.json",
                    "https://community.esri.com/t5/arcgis-pro-ideas/"
                    "schedule-gp-history-clean-up/idi-p/1370815")
_ESRI_REQ = ("Esri - Configuration syst\u00e8me requise pour ArcGIS Pro "
             "(chiffres publi\u00e9s : minimum, recommand\u00e9, optimal)",
             "https://doc.esri.com/fr/arcgis-pro/latest/get-started/"
             "arcgis-pro-system-requirements.html")
_ESRI_ADDINS = ("Esri - Gérer les add-ins ArcGIS Pro et leurs dossiers",
                "https://pro.arcgis.com/en/pro-app/latest/get-started/"
                "manage-add-ins.htm")
_ESRI_ADDIN_DIAG = ("Esri - Diagnostiquer les add-ins ArcGIS Pro",
                    "https://pro.arcgis.com/en/pro-app/latest/sdk/api-reference/"
                    "conceptdocs/docs/ProGuide-Diagnosing-ArcGIS-Pro-Add-ins.html")
_ESRI_ADDIN_VERSION = ("Esri - Chargement et compatibilité des add-ins/configurations",
                       "https://pro.arcgis.com/en/pro-app/3.6/sdk/api-reference/"
                       "conceptdocs/docs/ProConcepts-Advanced-Topics.html")
_MS_NIC = ("Microsoft - MSFT_NetAdapterPowerManagementSettingData",
           "https://learn.microsoft.com/en-us/previous-versions/windows/"
           "desktop/legacy/hh872363(v=vs.85)")
_R_SINGLE = ("r/gis - Pro est mono-thread : 16 c\u0153urs n'y changent rien",
             "https://www.reddit.com/r/gis/comments/t7lc3h/not_being_funny_"
             "is_there_an_actual_reason_arcgis/")
_R_ONEDRIVE = ("r/ArcGIS - OneDrive et les fichiers SIG : 10 min pour ouvrir "
               "un projet", "https://www.reddit.com/r/ArcGIS/comments/"
               "1srr0qm/one_drive_and_gis_files_question/")
_R_ONEDRIVE2 = ("r/ArcGIS - OneDrive en \u00ab Pending \u00bb pendant un "
                "traitement", "https://www.reddit.com/r/ArcGIS/comments/"
                "1kix8v8/how_much_time_is_likely_left_on_this_process/")
_R_EDITSLOW = ("r/ArcGIS - Pro ralentit \u00e0 chaque modification : "
               "indexation, NAS, projection",
               "https://www.reddit.com/r/ArcGIS/comments/1ni4key/"
               "arcgis_pro_slowing_down_with_each_edit/")
_R_CRASH = ("r/gis - Pro plante sur les gros jeux de donn&eacute;es",
            "https://www.reddit.com/r/gis/comments/htmscu/arcgis_pro_"
            "crashing_when_editing_large_data_sets/")
_R_SLOWMAP = ("r/ArcGIS - Carte lente \u00e0 charger : 28 Go de JPEG "
              "g&eacute;or&eacute;f&eacute;renc&eacute;s",
              "https://www.reddit.com/r/ArcGIS/comments/1v82j68/"
              "arcgis_pro_slow_loading_map/")
_R_SHUTS = ("r/gis - \u00ab it just shuts with no warning and no saving \u00bb",
            "https://www.reddit.com/r/gis/comments/940cyk/arcgis_pro_"
            "performance_impressions/")

SOURCES = {
    "PR-01": [_ESRI_RECOVERY],
    "PR-02": [_ESRI_RECOVERY, _ESRI_CIM_PROJECT],
    "PR-03": [_ESRI_PROJECT_FILES, _ESRI_REQ],
    "PR-04": [_ESRI_PROJECT_FILES],
    "PR-05": [_ESRI_CIM_PROJECT],
    "PR-06": [_ESRI_PACKAGE_PROJECT, _ESRI_GP_HISTORY],
    "DL-03": [_ESRI_PROJ],
    "DL-04": [_MS_NIC],
    "DL-05": [_ESRI_REQ, _R_ONEDRIVE, _R_ONEDRIVE2],
    "DL-06": [_ESRI_REQ, _R_ONEDRIVE2, _R_ONEDRIVE],
    "DL-07": [_ESRI_REQ, _R_ONEDRIVE],
    "DL-08": [_R_ONEDRIVE],
    "DL-09": [_ESRI_TROUBLE],
    "DL-10": [_ESRI_PERF],
    "DL-11": [_ESRI_PERF],
    "DL-12": [_ESRI_TROUBLE],
    "DL-13": [_R_SHUTS, _ESRI_3D],
    "PF-01": [_ESRI_PERF, _R_EDITSLOW],
    "PF-02": [_ESRI_PROJ],
    "PF-03": [_ESRI_CACHE],
    "PF-04": [_ESRI_TROUBLE],
    "PF-05": [_ESRI_TROUBLE, _ESRI_3D],
    "PF-06": [_ESRI_TROUBLE, _ESRI_CACHE, _ESRI_REQ],
    "PF-07": [_ESRI_PROJ],
    "PF-08": [_ESRI_TROUBLE, _R_EDITSLOW, _ESRI_PROJ],
    "PF-09": [_ESRI_CACHE, _R_CRASH, _R_SLOWMAP],
    "PF-10": [_ESRI_TROUBLE, _ESRI_PROJ],
    "PF-11": [_ESRI_TROUBLE, _ESRI_3D, _ESRI_CA, _ESRI_REQ],
    "PF-12": [_ESRI_REQ, _ESRI_PROJ, _R_CRASH, _R_EDITSLOW],
    "PF-13": [_ESRI_REQ, _ESRI_PROJ],
    "PF-14": [_ESRI_PERF],
    "PF-15": [_ESRI_CA],
    "PF-16": [_R_SLOWMAP, _ESRI_PERF],
    "PF-17": [_ESRI_TROUBLE],
    "PF-18": [_ESRI_REQ, _R_EDITSLOW, _ESRI_CA],
    "PF-19": [_ESRI_PERF],
    "PF-20": [_ESRI_PROJ],
    "PF-21": [_ESRI_CACHE],
    "PF-22": [_ESRI_PERF],
    "PF-26": [_ESRI_PROJ],
    "PF-27": [_ESRI_PERF, _ESRI_3D],
    "PF-28": [_ESRI_REQ, _R_SINGLE, _ESRI_CA],
    "PF-29": [_ESRI_REQ, _ESRI_TROUBLE, _ESRI_3D],
    "PF-30": [_R_CRASH, _R_ONEDRIVE, _R_SLOWMAP],
    "PF-31": [_ESRI_ADDINS, _ESRI_ADDIN_DIAG, _ESRI_ADDIN_VERSION],
}


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

# The dedicated, explicitly selected-project reader takes a consistent local
# snapshot and verifies every ZIP member. It has its own, still bounded, caps:
# a real project with a large geoprocessing history must not become an
# unbounded decompression job. A file beyond a cap is Info and is never
# repaired; the report says how to reduce it through ArcGIS Pro instead.
PROJECT_MAX_BYTES = 512 * 1024 ** 2
PROJECT_MAX_ENTRIES = 5000
PROJECT_MAX_EXPANDED_BYTES = 1024 ** 3
PROJECT_JSON_MAX_ENTRY_BYTES = 64 * 1024 ** 2
PROJECT_JSON_MAX_TOTAL_BYTES = 256 * 1024 ** 2
PROJECT_HISTORY_WARN = 100
PROJECT_HISTORY_WARN_BYTES = 10 * 1024 ** 2
PROJECT_REPAIR_MODES = ("none", "repack", "views", "views_history")
PROJECT_CHECK_IDS = ("PR-01", "PR-02", "PR-03", "PR-04", "PR-05", "PR-06")

# Startup customisation packages are inspected without loading their code or
# extracting them. Directory walks, package counts, XML reads and optional CRC
# work are all bounded; reaching a cap makes the inventory explicitly partial.
ADDIN_SCAN_LIMIT = 400
ADDIN_MAX_PACKAGES = 64
ADDIN_MAX_PACKAGE_BYTES = 256 * 1024 ** 2
ADDIN_MAX_PACKAGE_ENTRIES = 5000
ADDIN_CONFIG_MAX_BYTES = 1024 ** 2
ADDIN_CRC_MAX_EXPANDED_BYTES = 32 * 1024 ** 2
ADDIN_FULL_CRC_MAX = 4
ADDIN_PACKAGE_EXTENSIONS = {
    ".esriaddinx": "add-in",
    ".proconfigx": "managed configuration",
    ".esriplugin": "plug-in data source",
}
ADDIN_FOLDER_KEYS = (
    ("user", r"HKCU\SOFTWARE\ESRI\ArcGISPro\Settings\Add-In Folders"),
    ("administrator", r"HKLM\SOFTWARE\ESRI\ArcGISPro\Settings\Add-In Folders"),
    ("user configuration", r"HKCU\SOFTWARE\ESRI\ArcGISPro\Settings\Configuration Folders"),
    ("administrator configuration", r"HKLM\SOFTWARE\ESRI\ArcGISPro\Settings\Configuration Folders"),
)

# A Pro user.config is a few kilobytes of XML. This ceiling is not about Pro:
# it is about a file this tool did not write, in a folder OneDrive may sync
# from somewhere else. Without it one read pulls the whole file into memory.
MAX_TEXT_BYTES = 2 * 1024 ** 2

# An .aprx is a zip, and the walk of its parsed documents is iterative rather
# than recursive, because a hostile or corrupt project nests arbitrarily deep
# and the recursive version ended the check with a RecursionError.
APRX_MAX_DEPTH = 200
APRX_MAX_NODES = 200000

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
DEFAULT_PRO_EXE = r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe"
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
               measured=True, kpi=None, explanation="", impact_fr="",
               steps=None, verification=""):
    """Build one check record, or refuse.

    Four refusals, each one a defect somebody shipped:

    1. A status outside the five. The original enforced this with a
       ValidateSet, and it is the only reason the report can be summed at all.
    2. A verdict on a check whose own text says it did not measure.
    3. A verdict on a check that passed measured=False. The text test catches a
       body that lies by accident; this catches one that lies on purpose.
    4. AutoFixable with no fix text. The summary offers those to the operator
       by id, so a fixable flag with nothing behind it is an offer of nothing.
    5. A KPI that is not a dict of numbers. The HTML report draws its gauges
       from these, and a gauge drawn off a string parsed back out of a
       sentence is a number the report invented on the way to the page.
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
    if kpi is not None:
        if not isinstance(kpi, dict):
            raise ValueError("%s carries a KPI that is not a mapping" % cid)
        for key, value in kpi.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("%s carries a KPI %r that is not a number"
                                 % (cid, key))
    if steps is not None and isinstance(steps, (str, bytes)):
        raise ValueError("%s steps must be a sequence of separate actions" % cid)
    record = {
        "id": cid,
        "category": category,
        "title": title,
        "status": status,
        "dataloss": bool(dataloss),
        "observed": observed,
        "expected": expected,
        "impact": impact,
        "fix": fix,
        "autofixable": bool(autofixable),
        "detail": detail,
    }
    # Structured, reader-facing remediation. The original impact/fix fields
    # stay intact for compatibility and for the technical evidence; these four
    # fields are what the French report and the geoprocessing messages show.
    if explanation:
        record["explanation"] = "%s" % explanation
    if impact_fr:
        record["impact_fr"] = "%s" % impact_fr
    if steps:
        record["steps"] = ["%s" % step for step in steps]
    if verification:
        record["verification"] = "%s" % verification
    if kpi:
        record["kpi"] = dict(kpi)
    return record


def add_check(checks, **kwargs):
    """Append one record to the run. Every check body ends here."""
    rec = make_check(**kwargs)
    checks.append(rec)
    return rec


# Reader-facing remediation, independent of the technical evidence. A finding
# says what was measured; this table explains what it means and the order in
# which to act. The wording deliberately says "peut" where the check identifies
# a contributing condition rather than proving the single cause of an incident.
GUIDANCE_FR = {
    "DL-00": (
        "Les réglages ArcGIS Pro et plusieurs caches appartiennent au compte Windows. Un diagnostic lancé depuis un autre compte peut donc examiner le mauvais profil.",
        "Les corrections par utilisateur risquent de ne produire aucun effet pour la personne qui subit les lenteurs.",
        ["Fermez ArcGIS Pro.", "Ouvrez une session Windows avec le compte qui rencontre le problème, sans élévation inutile.", "Relancez le diagnostic depuis cette session."],
        "DL-00 doit être Conforme et le compte affiché doit être celui de l'utilisateur concerné."),
    "DL-05": (
        "Le projet ou ses données de travail sont sur un partage réseau ou dans un dossier synchronisé. Chaque ouverture et chaque enregistrement dépendent alors d'un verrou, d'une latence ou d'une synchronisation externe.",
        "Ouverture et enregistrement plus lents, blocages pendant une sauvegarde et, en cas de coupure, risque d'enregistrement incomplet ou de version d'édition incohérente.",
        ["Terminez et enregistrez les éditions dans ArcGIS Pro, puis fermez le projet.", "Copiez le projet et ses géodatabases fichier vers un dossier local non synchronisé, par exemple C:\\GIS\\Travail.", "Conservez les connexions d'entreprise nécessaires, mais évitez de modifier directement une géodatabase fichier sur le partage.", "Ouvrez la copie locale et faites un test d'enregistrement avant de reprendre la production."],
        "Relancez DL-05 : le projet actif doit être LOCAL. Testez aussi une ouverture puis un enregistrement sans coupure."),
    "DL-03": (
        "La sauvegarde de projet copie le fichier .aprx ; elle ne sauvegarde pas les éditions d'entités. Un intervalle court peut aussi provoquer des écritures périodiques.",
        "Des pauses pendant le travail et une fausse impression de protection : une sauvegarde .aprx ne restaure pas des modifications d'entités perdues.",
        ["Dans ArcGIS Pro, ouvrez Projet > Options > Général > Récupération de projet.", "Sur un dossier réseau, augmentez l'intervalle ou désactivez temporairement ces sauvegardes pour isoler les pauses.", "Continuez à enregistrer explicitement les éditions d'entités ; ne comptez jamais sur la récupération du .aprx pour cela."],
        "Vérifiez qu'une sauvegarde projet ne coïncide plus avec les pauses et confirmez séparément que les éditions sont bien enregistrées."),
    "DL-04": (
        "Windows est autorisé à éteindre une carte réseau pour économiser l'énergie. La reconnexion n'est pas toujours transparente pour une session de géodatabase déjà ouverte.",
        "Perte momentanée de connexion, carte figée et échec possible lors de l'enregistrement d'une session d'édition.",
        ["Faites sauvegarder et fermer les sessions ArcGIS Pro.", "Ouvrez Gestionnaire de périphériques > Cartes réseau > la carte concernée > Gestion de l'alimentation.", "Décochez « Autoriser l'ordinateur à éteindre ce périphérique pour économiser l'énergie » avec un administrateur si la stratégie de votre organisation l'autorise.", "Branchez le câble réseau ou reconnectez le VPN, puis rouvrez le projet."],
        "Relancez DL-04 et vérifiez qu'aucune coupure n'apparaît dans le journal pendant une session d'édition."),
    "DL-06": (
        "Le dossier Documents est redirigé vers OneDrive par Known Folder Move. Un projet enregistré dans Documents peut donc être synchronisé sans que l'utilisateur l'ait explicitement choisi.",
        "Rappels de fichiers, verrous et écritures concurrentes peuvent ralentir l'ouverture ou interrompre un enregistrement ArcGIS Pro.",
        ["Fermez ArcGIS Pro et attendez la fin de la synchronisation OneDrive.", "Créez un dossier local hors Documents synchronisé, par exemple C:\\GIS\\Travail.", "Déplacez une copie du projet et des données de travail vers ce dossier ; ne désactivez pas brutalement la synchronisation pendant une édition.", "Si la redirection est imposée, demandez au service informatique une exclusion ou un emplacement GIS local pris en charge."],
        "Le chemin du projet ne doit plus passer par OneDrive et DL-06/DL-05 ne doit plus signaler le dossier de travail."),
    "DL-07": (
        "Des fichiers du projet sont des espaces réservés OneDrive déshydratés : leur contenu doit être téléchargé avant qu'ArcGIS Pro puisse les exploiter.",
        "Attentes à l'ouverture ou au dessin, voire échec d'accès si le fichier est rappelé pendant une écriture.",
        ["Fermez le projet dans ArcGIS Pro.", "Dans l'Explorateur, vérifiez que l'espace disque disponible permet de télécharger les fichiers.", "Pour un dépannage ponctuel, choisissez « Toujours conserver sur cet appareil » sur le dossier concerné et attendez la fin du téléchargement.", "Pour un usage régulier, déplacez ensuite le projet hors du dossier synchronisé."],
        "Les icônes de nuage doivent avoir disparu et DL-07 ne doit plus trouver de fichier déshydraté."),
    "DL-08": (
        "Le cache Fichiers hors connexion (CSC) peut servir une copie locale ou différer des écritures sur un partage. Ce comportement est incompatible avec une géodatabase fichier activement modifiée.",
        "Lecture d'une ancienne version, conflits différés et modifications qui semblent disparaître après une reconnexion.",
        ["Fermez ArcGIS Pro et laissez le Centre de synchronisation terminer ou résoudre les conflits existants.", "Sauvegardez les données avant de changer la configuration.", "Avec le service informatique, désactivez Fichiers hors connexion pour les partages GIS ou excluez ces partages par stratégie.", "Reprenez le travail depuis une copie locale plutôt que depuis le cache."],
        "Vérifiez dans le Centre de synchronisation qu'il ne reste ni conflit ni écriture en attente, puis relancez DL-08."),
    "DL-09": (
        "Des vidages mémoire de plantage ArcGIS Pro ont été trouvés. Ils prouvent un incident à une date donnée, mais ne désignent pas à eux seuls le composant fautif.",
        "Ils aident à corréler le crash avec une ouverture, une mise en page, un pilote ou une édition ; leur suppression ferait perdre une preuve utile au support.",
        ["Notez la date du vidage et l'action réalisée à ce moment.", "Conservez les fichiers .dmp et le numéro de build indiqué par PF-04.", "Corrélez-les avec DL-13, PF-29 et, pour un seul projet, le diagnostic .aprx.", "Joignez les plus récents à un incident Esri au lieu de les supprimer."],
        "Le rapport d'incident doit relier chaque crash à une heure et à une action reproductible."),
    "DL-10": (
        "La connexion TCP au serveur de géodatabase est lente, refusée ou interrompue. Si le nom ne se résout pas, le rapport le distingue explicitement d'un serveur qui répond mal.",
        "Cartes lentes à charger, requêtes qui se figent et délais d'attente pendant l'enregistrement d'éditions d'entreprise.",
        ["Vérifiez que le nom d'hôte et le port sont ceux du fichier de connexion, sans coller d'URL.", "Testez sur une liaison filaire et avec le VPN requis par l'organisation.", "Si le port est refusé, faites vérifier le service de base de données et le pare-feu par les équipes réseau/base de données.", "Si la latence reste élevée, fournissez la mesure et l'heure du test à ces équipes ; ne modifiez pas les données pour compenser."],
        "Répétez DL-10 plusieurs fois : la connexion doit aboutir avec une latence stable et adaptée à votre réseau."),
    "DL-11": (
        "De nombreuses connexions .sde ou leurs copies dans l'index peuvent faire répéter l'énumération des connexions et des versions. Une connexion à la mauvaise version peut aussi donner l'impression que des éditions ont disparu.",
        "Ouverture plus longue et risque de consulter ou modifier une version différente de celle attendue.",
        ["Dans ArcGIS Pro, vérifiez d'abord la version utilisée par chaque couche d'entreprise.", "Fermez Pro et sauvegardez la liste des connexions réellement nécessaires.", "Retirez uniquement les connexions obsolètes identifiées ; ne diffusez pas leur contenu, qui peut contenir des identifiants.", "Si l'index est incohérent, faites reconstruire ResourceIndex après avoir fermé Pro."],
        "Rouvrez le projet et confirmez la version et le serveur de chaque couche avant toute édition."),
    "DL-12": (
        "Ce résultat signifie surtout que le diagnostic de poste tourne pendant qu'ArcGIS Pro est ouvert ; ce n'est pas, à lui seul, une preuve de plantage ou de perte de données.",
        "Le cache ne peut pas être renommé proprement et les contrôles automatiques des projets récents sont suspendus pour éviter un conflit d'enregistrement.",
        ["Pour un diagnostic de poste complet, enregistrez les éditions, fermez les projets puis quittez ArcGIS Pro.", "Relancez le script ou appliquez AP-01 seulement à ce moment-là.", "Pour analyser un autre .aprx sans fermer Pro, utilisez le nouvel outil « Analyser un projet .aprx » et sélectionnez un fichier différent du projet actif."],
        "DL-12 doit être Conforme pour le correctif de cache ; l'outil projet ciblé reste utilisable pendant que Pro est ouvert."),
    "DL-13": (
        "Le journal Windows contient des événements de plantage, de suspension, d'arrêt brutal ou de connectivité. Un événement donne une chronologie, pas automatiquement la cause racine.",
        "Il permet de relier les fermetures ArcGIS Pro aux coupures réseau, aux pilotes et aux éditions signalées comme perdues.",
        ["Relevez l'heure exacte et le type de chaque événement lié à ArcGISPro.exe.", "Comparez ces heures avec les vidages DL-09 et les actions décrites par l'utilisateur.", "Pour un événement 1000, relevez aussi le module défaillant ; pour un 1002, distinguez une attente d'une fermeture forcée.", "Transmettez ces éléments au support avec la version PF-04 plutôt que de vider le journal."],
        "Une correction est validée si le même scénario ne produit plus d'événement et si les éditions restent présentes après réouverture."),
    "PF-07": (
        "Des dossiers temporaires d'anciennes sessions subsistent. Certains contiennent encore un projet ou une table non vide : ils peuvent être la seule piste de récupération.",
        "Ils consomment du disque, mais leur suppression prématurée peut surtout détruire un travail non enregistré.",
        ["Fermez ArcGIS Pro.", "Ouvrez d'abord les dossiers signalés comme contenant du travail et comparez leurs dates à l'incident.", "Copiez ailleurs tout élément à récupérer et vérifiez son ouverture.", "Seulement après cette revue, supprimez manuellement les dossiers anciens sans contenu utile."],
        "Vérifiez que les éléments récupérés sont ouverts et enregistrés dans un projet normal avant tout nettoyage."),
    "PF-01": (
        "Des couches utilisent un système de coordonnées différent de celui de la carte. ArcGIS Pro doit alors les reprojeter à la volée lors des affichages.",
        "Calculs supplémentaires à chaque ouverture, déplacement ou zoom ; ce réglage ralentit le dessin sans être une corruption du projet.",
        ["Dans les propriétés de la carte, notez son système de coordonnées.", "Dans les propriétés de source des couches signalées, identifiez celles qui utilisent un autre système.", "Choisissez le système adapté au projet, puis utilisez l'outil Projeter sur une copie des données concernées.", "Remplacez la source de la couche par la copie projetée après avoir contrôlé son emplacement et ses entités."],
        "Relancez PF-01 et comparez le temps de dessin ; ne reprojetez jamais directement une source en cours d'édition."),
    "PF-02": (
        "La configuration SMB du client n'est pas optimale pour les projets qui résident sur un partage. Elle est sans effet sur un projet entièrement local.",
        "Débit et latence moins favorables lors des nombreuses petites lectures/écritures d'un projet ou d'une géodatabase réseau.",
        ["Vérifiez d'abord si le projet est réellement sur un partage ; si non, passez aux autres causes.", "Comparez la configuration avec un poste qui fonctionne et avec la stratégie de l'organisation.", "Faites modifier SMB Multichannel par l'équipe système/réseau si nécessaire.", "Pour les éditions intensives, privilégiez tout de même un stockage de travail local."],
        "Relancez PF-02 et PF-03 sur le même partage et comparez les temps de listage."),
    "PF-03": (
        "Le simple listage d'un dossier de projet réseau est lent ou impossible. ArcGIS Pro répète ce type d'accès pendant l'ouverture.",
        "Attente avant même l'affichage de la carte, pauses lors de la résolution des sources et échecs si le partage devient inaccessible.",
        ["Testez le même dossier avec une liaison filaire et le VPN correctement connecté.", "Vérifiez l'espace et la charge du serveur de fichiers avec l'équipe réseau.", "Copiez le projet sur un disque local pour distinguer un problème de partage d'un problème de contenu.", "Si la copie locale s'ouvre normalement, faites traiter la latence du partage plutôt que de modifier les cartes."],
        "PF-03 doit repasser sous les seuils indiqués sur plusieurs mesures, pas seulement une fois."),
    "PF-04": (
        "La version et le build déterminent les correctifs disponibles et le comportement du moteur de rendu. Un numéro ancien n'explique pas seul un problème, mais il est indispensable au diagnostic.",
        "Un défaut déjà corrigé par Esri peut continuer à provoquer lenteurs ou fermetures si le correctif n'est pas installé.",
        ["Relevez la version et le build exacts dans le rapport.", "Consultez les correctifs publiés pour cette version et les problèmes connus correspondant à votre symptôme.", "Sauvegardez un projet représentatif, puis installez le correctif validé par votre organisation.", "Si le problème est apparu juste après une mise à jour, signalez cette chronologie au support avant de rétrograder."],
        "Après mise à jour, vérifiez le nouveau build et rejouez exactement le scénario qui échouait."),
    "PF-05": (
        "Les options de rendu influencent directement le travail demandé à la carte graphique. Un réglage incompatible avec le pilote peut dégrader l'affichage.",
        "Dessin lent, artefacts, symboles qui ne se rafraîchissent pas ou fermeture lors de l'ouverture d'une vue graphique.",
        ["Notez les valeurs actuelles avant toute modification.", "Ouvrez Projet > Options > Affichage et ne changez qu'un réglage à la fois.", "Mettez également le pilote à jour ou revenez à la version précédente si le problème coïncide avec une mise à jour.", "Redémarrez Pro et testez une copie du projet après chaque changement."],
        "Le problème doit disparaître de façon reproductible ; conservez le réglage initial si le test n'apporte rien."),
    "PF-06": (
        "Le cache local ESRI est volumineux ou contient une forte proportion d'éléments anciens. Il peut être reconstruit ; ce n'est pas une sauvegarde de données métier.",
        "Démarrage et dessin progressivement plus lents, avec parfois un affichage issu d'un cache incohérent.",
        ["Enregistrez et fermez toutes les sessions ArcGIS Pro.", "Utilisez AP-01, ou renommez manuellement le dossier de cache indiqué au lieu de le supprimer.", "Relancez ArcGIS Pro et laissez-le reconstruire un cache neuf.", "Si le problème persiste, restaurez l'ancien dossier avec la commande d'annulation et poursuivez avec PF-29/PF-05."],
        "Un nouveau dossier de cache doit être créé et le même projet doit s'ouvrir sans l'ancienne anomalie."),
    "PF-08": (
        "L'index de recherche peut parcourir des dossiers et des connexions réseau, ou contenir un état devenu incohérent.",
        "Activité disque/réseau au démarrage et ralentissements pendant que le catalogue met son index à jour.",
        ["Dans Projet > Options > Général > Index de recherche, désactivez l'indexation des connexions réseau non nécessaires.", "Excluez les dossiers qui ne doivent pas être parcourus.", "Si l'index reste incohérent, fermez Pro et faites-le reconstruire après avoir sauvegardé les éléments utiles.", "Laissez la première reconstruction se terminer avant de comparer les performances."],
        "Observez qu'aucun parcours réseau inutile ne repart au démarrage et relancez PF-08."),
    "PF-09": (
        "Plusieurs couches proviennent de services HTTP/HTTPS. Leur affichage dépend du portail, du VPN et de la bande passante, même si le poste local est sain.",
        "Attente à l'ouverture et à chaque déplacement ou zoom ; une couche indisponible peut bloquer la carte sans que le .aprx soit corrompu.",
        ["Identifiez les couches de service listées et vérifiez le VPN/portail requis.", "Désactivez temporairement les fonds de carte en ligne pour mesurer leur contribution.", "Exportez les données de travail autorisées vers une géodatabase locale si vous devez les modifier intensivement.", "Conservez en ligne uniquement les services nécessaires et corrigez séparément ceux qui ne répondent plus."],
        "Comparez l'ouverture avec les services désactivés, puis relancez PF-09 après simplification."),
    "PF-10": (
        "Le projet contient beaucoup de couches, de groupes imbriqués ou un historique volumineux. Une couche masquée reste présente dans le document.",
        "Lecture, énumération et enregistrement du .aprx plus longs ; la mémoire nécessaire à l'ouverture augmente aussi.",
        ["Faites une copie du projet avant de l'alléger.", "Séparez les cartes par métier et retirez de la copie les cartes, mises en page et couches inutilisées.", "Réduisez les groupements inutiles et l'historique de géotraitement conservé.", "Utilisez Enregistrer sous depuis ArcGIS Pro quand le projet s'ouvre encore ; sinon testez la copie de récupération proposée par l'outil projet."],
        "Relancez PF-10 sur la copie et vérifiez que toutes les cartes nécessaires sont toujours présentes."),
    "PF-11": (
        "Windows ne désigne pas explicitement la carte graphique hautes performances pour ArcGISPro.exe, ou la préférence est illisible. Ce réglage est un choix de rendu, pas un correctif de stabilité.",
        "Sur un portable à deux GPU, Pro peut utiliser le circuit intégré et afficher plus lentement les cartes et scènes. Forcer un GPU dont le pilote est instable peut au contraire provoquer un crash dès l'écran de démarrage.",
        ["Fermez ArcGIS Pro.", "Si le crash est apparu après un ancien AP-02, ouvrez le rapport JSON de ce passage et exécutez exactement sa commande undo pour restaurer la valeur précédente.", "Sinon, dans Windows, ouvrez Paramètres > Système > Affichage > Graphismes et sélectionnez ArcGISPro.exe ; ne choisissez Hautes performances que si Pro est stable et le pilote pris en charge.", "Pour distinguer une extension tierce, testez ArcGISPro.exe /disableaddins.", "Redémarrez Pro sur secteur et vérifiez le GPU réellement utilisé."],
        "Pro doit franchir l'écran de démarrage sans crash. Relancez ensuite PF-11 et vérifiez dans les informations de rendu que le GPU attendu est utilisé."),
    "PF-12": (
        "La mémoire installée ou libre est insuffisante pour le projet et les autres applications. Windows compense alors par le fichier d'échange.",
        "Ralentissements brutaux, longues pauses de disque et, sous forte pression mémoire, fermeture d'ArcGIS Pro.",
        ["Fermez les applications gourmandes et les projets ArcGIS Pro inutiles.", "Vérifiez dans le Gestionnaire des tâches si la mémoire est saturée pendant l'ouverture.", "Allégez le projet si PF-10 le signale et gardez le fichier d'échange géré par Windows sur un disque local.", "Si le besoin dépasse durablement la mémoire disponible, augmentez la RAM selon les paliers Esri indiqués."],
        "Répétez le scénario en surveillant la mémoire : le système ne doit plus passer son temps à paginer."),
    "PF-13": (
        "Un volume utilisé par Windows, le cache ou le projet manque d'espace libre.",
        "Le cache et les fichiers temporaires ne peuvent plus grossir normalement ; enregistrements et géotraitement peuvent échouer ou se bloquer.",
        ["Vérifiez le volume précis indiqué, pas seulement le disque C:.", "Libérez d'abord les fichiers sûrs et les anciennes sorties identifiées, sans supprimer les dossiers PF-07 qui contiennent du travail.", "Déplacez les données volumineuses vers un volume local adapté et videz la corbeille après vérification.", "Gardez une marge supérieure au seuil affiché avant de rouvrir le projet."],
        "Relancez PF-13 et effectuez un enregistrement ainsi qu'un petit géotraitement sur le volume concerné."),
    "PF-14": (
        "Jointures, requêtes de définition, étiquettes et moteur Maplex multiplient les calculs à chaque rafraîchissement de carte.",
        "Ouverture et dessin lents, requêtes répétées et délai avant que les attributs ou symboles ne se mettent à jour.",
        ["Relevez les couches qui cumulent le plus de jointures et d'étiquettes.", "Matérialisez les jointures permanentes avec Joindre un champ ou une copie exportée.", "Ajoutez les index nécessaires sur les champs filtrés après validation par le propriétaire des données.", "Désactivez les étiquettes inutiles et utilisez des plages d'échelle pour éviter de dessiner toutes les couches en permanence."],
        "Comparez le temps de rafraîchissement avant/après sur la même étendue et relancez PF-14."),
    "PF-15": (
        "Le mode d'alimentation actif peut limiter le processeur pour économiser l'énergie.",
        "Traitements et dessin plus lents, surtout sur portable sur batterie ou pendant une longue ouverture.",
        ["Branchez le portable sur secteur.", "Ouvrez les Options d'alimentation Windows et choisissez Performances élevées si votre organisation le permet.", "Si ce mode est masqué par stratégie, demandez au service informatique le réglage approprié plutôt que de modifier le registre.", "Relancez le même projet sans autre changement pour comparer."],
        "PF-15 doit indiquer le mode attendu et le temps d'ouverture doit être comparé dans les mêmes conditions."),
    "PF-16": (
        "Des rasters n'ont pas de pyramides ou d'aperçus. ArcGIS Pro doit donc lire davantage de pixels pour produire une petite image à l'écran.",
        "Affichage très lent à chaque zoom et forte lecture disque/réseau à l'ouverture de la carte.",
        ["Vérifiez que les rasters sont accessibles et faites une copie de travail locale s'ils sont sur un partage.", "Exécutez l'outil Construire les pyramides sur les rasters signalés, hors session d'édition.", "Laissez les fichiers latéraux .ovr ou .aux.xml avec le raster.", "Rouvrez la carte à plusieurs échelles pour contrôler l'affichage."],
        "PF-16 ne doit plus signaler ces rasters et le déplacement/zoom doit être nettement plus réactif."),
    "PF-17": (
        "Les exclusions Defender attendues par la politique de cet outil sont absentes ou illisibles. Cela ne prouve pas que l'antivirus est la cause et ne justifie jamais de le désactiver.",
        "L'analyse à l'accès peut ralentir les nombreux petits fichiers d'ArcGIS Pro, mais de mauvaises exclusions réduisent aussi la sécurité.",
        ["Mesurez d'abord avec l'outil d'analyse de performances Microsoft Defender, sans désactiver la protection.", "Fournissez les chemins réellement impliqués à l'équipe sécurité.", "Faites valider des exclusions minimales avec des chemins absolus du compte utilisateur ; évitez les variables résolues dans le mauvais profil.", "Refaites une mesure après application de la politique approuvée."],
        "La preuve Defender doit montrer une baisse du temps d'analyse et PF-17 doit refléter la politique réellement appliquée."),
    "PF-18": (
        "Le projet, le cache ou l'espace temporaire se trouve sur un disque rotatif ou sur un média dont le type n'est pas confirmé.",
        "Les nombreuses petites entrées/sorties d'ArcGIS Pro attendent le disque, ce qui rallonge ouverture, cache et géotraitement.",
        ["Identifiez précisément quel volume contient le projet et le cache.", "Déplacez une copie du projet et des données de travail vers un SSD local.", "Redirigez le cache et l'espace de travail temporaire vers ce SSD depuis les options prises en charge.", "Comparez la même ouverture sans modifier le contenu du projet."],
        "PF-18 doit identifier un SSD et PF-03/PF-13 doivent rester sains sur le nouveau volume."),
    "PF-19": (
        "La sonde du portail n'a pas été demandée, n'a pas résolu le nom, ou mesure une connexion lente/refusée selon la ligne affichée.",
        "Connexion au portail et résolution des couches web plus lentes à l'ouverture ; un hôte non renseigné ne signifie aucun problème.",
        ["Si le projet n'utilise aucun portail, ignorez cette sonde non mesurée.", "Sinon indiquez uniquement le nom DNS du portail, sans https:// ni chemin, puis relancez.", "Vérifiez le VPN, le DNS et le port 443 avec l'équipe réseau si la connexion échoue.", "Corrélez le résultat avec PF-09 pour identifier les couches concernées."],
        "La sonde doit aboutir avec une latence stable et les services du projet doivent s'ouvrir dans un navigateur autorisé."),
    "PF-20": (
        "Le fichier d'échange est trop petit ou placé sur un volume réseau. Il sert d'appoint lorsque la mémoire physique est sous pression.",
        "Ralentissements majeurs lors des pointes mémoire et fermetures possibles au lieu d'une simple pause.",
        ["Ouvrez Système > Paramètres système avancés > Performances > Avancé > Mémoire virtuelle.", "Placez le fichier d'échange sur un volume local avec suffisamment d'espace.", "Préférez une taille gérée par le système sauf recommandation contraire de votre service informatique.", "Redémarrez Windows pour appliquer le changement."],
        "Après redémarrage, relancez PF-20 et reproduisez la pointe mémoire avec PF-12 sous surveillance."),
    "PF-21": (
        "La carte réseau a négocié un débit inférieur à celui attendu, souvent à cause d'un câble, d'un dock ou d'un port de commutateur.",
        "Les données d'entreprise et les services semblent lents même si les serveurs et ArcGIS Pro sont correctement configurés.",
        ["Contrôlez le débit affiché pour la carte réellement utilisée.", "Remplacez ou rebranchez le câble et testez un autre port de commutateur.", "Sur portable, testez sans la station d'accueil et vérifiez l'alimentation du dock.", "Faites contrôler vitesse/duplex et erreurs du port par l'équipe réseau."],
        "PF-21 doit afficher le débit attendu et DL-10/PF-03 doit être retesté sur cette même liaison."),
    "PF-22": (
        "Des couches se rafraîchissent automatiquement ou la géodatabase par défaut se trouve sur un stockage distant. Ces éléments provoquent des requêtes et écritures sans action visible de l'utilisateur.",
        "Pauses périodiques, rafraîchissements de carte et lenteur des sorties de géotraitement ; une source indisponible peut aussi retarder l'ouverture.",
        ["Dans les propriétés de chaque couche listée, désactivez ou augmentez l'intervalle de rafraîchissement si le métier ne l'exige pas.", "Vérifiez la disponibilité des sources avant de changer les couches.", "Dans les options du projet, définissez une géodatabase par défaut locale et existante.", "Enregistrez sous une copie puis testez l'ouverture et un petit géotraitement."],
        "Relancez PF-22 : aucun minuteur inutile ne doit rester et la géodatabase par défaut doit être locale."),
    "PF-26": (
        "Le profil itinérant transporte une partie des réglages ESRI entre les sessions Windows.",
        "Copies au démarrage/à la fermeture de session et premier lancement d'ArcGIS Pro plus lent ; les verrous réseau peuvent ajouter des pauses.",
        ["Faites identifier par le service informatique les dossiers ESRI réellement itinérants.", "Excluez par stratégie les caches et éléments locaux qui n'ont pas besoin de suivre l'utilisateur.", "Conservez les paramètres métier nécessaires et testez sur un compte pilote.", "Ouvrez puis fermez deux sessions pour vérifier que le profil se synchronise correctement."],
        "Mesurez le temps d'ouverture de session et relancez PF-26 sur le compte pilote."),
    "PF-27": (
        "Le comportement signalé concerne le rafraîchissement de la symbologie après un calcul de champ, pas nécessairement la valeur enregistrée.",
        "La carte peut sembler obsolète alors que la table contient la bonne valeur ; il ne faut pas conclure trop vite à une perte de données.",
        ["Vérifiez d'abord la nouvelle valeur directement dans la table attributaire.", "Rafraîchissez la carte ou fermez puis rouvrez la vue.", "Notez le build PF-04 et les étapes exactes qui reproduisent le problème.", "Transmettez un suivi Diagnostic Monitor à Esri si le comportement persiste."],
        "La valeur doit rester correcte après réouverture et le rafraîchissement doit être vérifié séparément de l'enregistrement."),
    "PF-28": (
        "Le processeur est sous un palier mesuré ou son faible débit par cœur limite les opérations peu parallélisées d'ArcGIS Pro.",
        "Dessin, ouverture et certains géotraitements restent lents même avec beaucoup de cœurs ; ce n'est pas un réglage de projet à réécrire.",
        ["Fermez les applications qui consomment du CPU pendant le test.", "Appliquez le mode d'alimentation adapté PF-15 et branchez le portable.", "Vérifiez que les fonctions AVX requises sont présentes pour les outils concernés.", "Si le matériel reste sous les prérequis, prévoyez un remplacement en privilégiant les performances par cœur."],
        "Comparez le même traitement à charge contrôlée et vérifiez que les prérequis Esri sont atteints."),
    "PF-29": (
        "Le pilote graphique est ancien, le GPU est intégré seulement, ou ArcGIS Pro utilise un rendu logiciel tel que WARP selon la mesure affichée.",
        "Dessin très lent, artefacts et fermetures possibles à l'ouverture de cartes, scènes ou mises en page.",
        ["Relevez le modèle du GPU, la date du pilote et le nom du module éventuellement indiqué dans DL-13.", "Installez le pilote du fabricant du GPU ou du portable validé pour votre version de Pro.", "Si les crashes ont commencé juste après une mise à jour, testez le retour au pilote précédent.", "Sur double GPU, appliquez aussi PF-11 ; sur GPU intégré seul, réduisez les scènes 3D et envisagez le matériel recommandé."],
        "Vérifiez que WARP n'est plus utilisé et ouvrez plusieurs fois la même vue sans crash avant de reprendre la production."),
    "PF-30": (
        "Le dossier de données contient des géodatabases ou rasters volumineux, ou un très grand nombre de petits fichiers.",
        "Parcours, synchronisation, sauvegarde et accès disque plus lents ; sur partage, chaque petit fichier ajoute de la latence.",
        ["Repérez dans la preuve les dossiers qui portent réellement le volume ou le nombre de fichiers.", "Déplacez une copie de travail vers un SSD local non synchronisé.", "Construisez les pyramides nécessaires et retirez uniquement les anciennes sorties dont vous n'avez plus besoin.", "Pour les données partagées en édition, utilisez l'architecture de géodatabase validée par votre organisation plutôt qu'une copie concurrente."],
        "Relancez PF-30 et mesurez l'ouverture depuis le nouvel emplacement sans changer les couches."),
    "PF-31": (
        "ArcGIS Pro charge au démarrage les add-ins, configurations et plug-ins trouvés dans les dossiers personnels ou bien connus. Le contrôle relève leur compatibilité et leur structure ; leur seule présence ne prouve pas un crash.",
        "Une version incompatible, une DAML illisible, une assembly absente ou une copie d'assembly Esri peut empêcher le chargement, ralentir le splash screen ou fermer ArcGIS Pro avant tout projet.",
        ["Confirmez d'abord avec ArcGISPro.exe /disableaddins : ce test isole le groupe, sans désigner un package précis.", "Comparez les chemins, desktopVersion, assemblies et modules autoLoad de PF-31 avec le module défaillant de DL-13.", "Fermez Pro, puis déplacez un seul package suspect vers un dossier local de quarantaine situé hors de tous les dossiers bien connus ; ne supprimez rien.", "Relancez Pro normalement après chaque isolement et restaurez le package s'il n'est pas impliqué. Pour une configuration imposée par l'administrateur, faites modifier le déploiement par ce dernier.", "Réinstallez uniquement une version déclarée compatible avec le build PF-04, après validation de l'éditeur ou du service informatique."],
        "ArcGIS Pro doit franchir plusieurs démarrages normaux sans /disableaddins. Relancez PF-31 pour prouver quel package a été isolé et vérifiez que DL-13 n'enregistre plus le même crash."),
    "PR-01": (
        "Le fichier sélectionné n'est pas une archive .aprx lisible et intègre, ou l'un de ses éléments a échoué au contrôle CRC. Le diagnostic distingue cette corruption d'un simple projet volumineux.",
        "ArcGIS Pro peut refuser le projet ou se fermer pendant sa lecture. Une réparation automatique est refusée si tous les éléments ne peuvent pas être vérifiés.",
        ["Ne tentez pas plusieurs enregistrements par-dessus le fichier d'origine.", "Restaurez d'abord une sauvegarde ou une version précédente datant d'avant l'incident.", "Si seule la structure ZIP est en cause mais que tous les éléments sont lisibles, essayez le mode « Reconstruire le conteneur » : il crée une nouvelle copie sans changer les documents.", "Si le rapport indique une erreur CRC ou une archive illisible, conservez le fichier et contactez le support avec la sauvegarde et le message exact."],
        "Ouvrez la copie restaurée/reconstruite sur un poste de test et vérifiez cartes, mises en page, sources et historique avant de la remettre en production."),
    "PR-02": (
        "Un document JSON interne est absent, incomplet ou invalide. GISProject.json est indispensable à un projet Pro 3 ; les autres documents peuvent correspondre à une carte ou une mise en page précise.",
        "Ouverture impossible ou crash lors du chargement de l'élément corrompu. Tant que la structure n'est pas entièrement validée, l'outil ne prétend pas la réparer.",
        ["Conservez l'original et relevez le nom du document interne signalé.", "Restaurez une sauvegarde ArcGIS Pro ou une version précédente du fichier.", "Si le projet s'ouvre sur une autre machine avec la même version de Pro, utilisez immédiatement Enregistrer sous pour produire une nouvelle copie saine.", "Ne supprimez pas manuellement un document JSON sans connaître ses références ; transmettez le rapport au support si aucune sauvegarde n'est disponible."],
        "PR-01 et PR-02 doivent être Conforme sur la nouvelle copie, puis chaque carte et mise en page doit être ouverte."),
    "PR-03": (
        "Le .aprx, son dossier principal ou sa géodatabase par défaut utilise un emplacement réseau ou synchronisé. C'est une condition de risque mesurée, pas la preuve unique d'un crash.",
        "Lenteurs, attentes de verrou et échecs d'ouverture/enregistrement si le réseau ou la synchronisation interrompt les accès.",
        ["Fermez le projet dans toutes les sessions ArcGIS Pro.", "Copiez le .aprx avec son dossier principal et sa géodatabase fichier vers un emplacement local en conservant leur arborescence relative.", "Vérifiez que les chemins relatifs continuent de fonctionner depuis la copie.", "Ouvrez cette copie locale et comparez le résultat avant de modifier le contenu du projet."],
        "La copie locale doit s'ouvrir et s'enregistrer sans dépendre du partage, puis PR-03 doit être Conforme."),
    "PR-04": (
        "Une source fichier locale référencée par une couche est absente, ou des sources distantes doivent être vérifiées. Une couche cassée provoque normalement un point d'exclamation, pas nécessairement un crash.",
        "Couches non affichées et attentes pendant la résolution des sources ; les services et connexions d'entreprise ne sont pas contactés par ce contrôle.",
        ["Dans ArcGIS Pro, ouvrez la copie du projet et repérez les couches avec une source rompue.", "Utilisez Propriétés de la couche > Source > Définir la source de données pour retrouver le bon emplacement.", "Pour un chemin réseau, vérifiez d'abord VPN, partage et droits d'accès ; l'outil ne tente pas de s'y connecter.", "Ne remplacez pas une source par une table vide : vérifiez le nombre d'entités et la symbologie après reconnexion."],
        "Toutes les couches nécessaires doivent s'afficher sans point d'exclamation et leurs propriétés doivent indiquer la bonne source."),
    "PR-05": (
        "Le projet enregistre les vues à rouvrir et l'état des volets. Si Pro se fermait sur une mise en page ou une scène qui provoque un crash, cette vue est rouverte immédiatement au lancement suivant.",
        "Le même crash peut se répéter avant que vous ayez le temps de fermer la vue, alors que les cartes et mises en page elles-mêmes sont encore présentes.",
        ["Choisissez « Copie sans vues au démarrage » dans l'outil projet ; l'original reste inchangé.", "Ouvrez la nouvelle copie : elle ne restaure pas les anciennes vues actives.", "Rouvrez ensuite les cartes une par une, puis les mises en pages une par une pour identifier l'élément qui déclenche le problème.", "Si une vue précise provoque le crash, vérifiez PF-29/PF-05 et les extensions tierces avant de la recréer dans une copie."],
        "La copie doit s'ouvrir sans restaurer les anciens onglets ; vérifiez ensuite que toutes les cartes et mises en page figurent toujours dans le Catalogue."),
    "PR-06": (
        "L'historique de géotraitement est très volumineux selon les seuils de cet outil. Il est stocké dans GISProject.json et peut contenir de nombreux paramètres et messages.",
        "Lecture du projet et consommation mémoire augmentent à l'ouverture ; l'historique n'est pas une sauvegarde des données produites.",
        ["Si le projet s'ouvre, utilisez Enregistrer sous ou un paquetage sans historique après avoir conservé les opérations à rejouer.", "Sinon choisissez la copie de récupération « sans vues ni historique » dans l'outil.", "Vérifiez que les données de sortie existent indépendamment de l'historique.", "Conservez l'original si vous devez retrouver plus tard les paramètres d'un traitement."],
        "PR-06 doit être Conforme sur la copie et les cartes/données doivent être intactes ; seul l'historique est retiré."),
}


def enrich_check(check):
    """Attach the French explanation and ordered actions to one check."""
    guidance = GUIDANCE_FR.get(check.get("id"))
    if guidance:
        explanation, impact_fr, steps, verification = guidance
        check.setdefault("explanation", explanation)
        check.setdefault("impact_fr", impact_fr)
        check.setdefault("steps", list(steps))
        check.setdefault("verification", verification)
    return check


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
                  observed="Probe failed and was not measured this run: "
                           "%s: %s" % (type(exc).__name__, exc),
                  expected="The check completes and records a value.",
                  impact="Unknown. This cause was neither ruled in nor out, "
                         "and no fix will run off this record.",
                  fix="Re-run, collect the item by hand, or report the "
                      "exception above as a bug in this tool.")
        return
    if len(checks) == before:
        # A body that emitted nothing is a bug in the body, not a healthy
        # machine. Saying nothing reads as saying Pass, which is the failure
        # this whole tool exists to prevent.
        add_check(checks, cid=cid, category=category, title=title,
                  status="Skipped", dataloss=dataloss, measured=False,
                  observed="The check produced no record, so nothing about it "
                           "was measured.",
                  expected="At least one record.",
                  impact="Unknown.",
                  fix="Collect this item by hand and report the empty check.")


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
    """0 clean, 1 measured findings without lost-edit impact, 2 a finding
    bearing on lost edits, 3 no finding and nothing was measured either.

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

    ElementTree's default parser does not fetch external entities, so a
    user.config cannot make this read a file off a share. It does expand
    internal ones, which is a different problem: fifty lines of nested
    <!ENTITY> declarations turn a few hundred bytes into hundreds of
    megabytes inside expat, and this is a file a sync client may have put
    here. So a document type declaration is refused outright. A real Pro
    user.config has none, and a reader that wanted one would be a reader
    that had already been talked into expanding it.

    Malformed XML is a missing answer, not a traceback: the check that called
    this then says the settings could not be parsed and stays undetermined.
    """
    from xml.etree import ElementTree   # imported here so a run with no
                                        # user.config never pays for it

    out = {}
    if not text or not text.strip():
        return out
    if re.search(r"<!\s*DOCTYPE", text, re.IGNORECASE):
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
    lines = ["Running as: %s  (elevated: %s)" % (me, elevated)]
    for name in other_profiles:
        lines.append("Profile carrying ArcGIS Pro user data: %s" % name)
    if other_profiles:
        lines.append("Every per-user check in this report read the current "
                     "profile, not that one.")
        return dict(
            cid="DL-00", category="Session", dataloss=True, status="Fail",
            title="Diagnostic ran under the wrong profile, so the per-user "
                  "results are invalid",
            observed=lines,
            expected="The tool runs in the editor's own logged-on session, so "
                     "the ESRI folders, user.config, project paths, OneDrive "
                     "state and local caches it reads are hers.",
            impact="All symptoms. Every per-user check reported absent because "
                   "this profile has no Pro data. Absent here means not "
                   "measured, not healthy.",
            fix="Re-run inside the session of %s, without elevation first. The "
                "machine-wide checks in this run are still valid."
                % ", ".join(other_profiles))
    return dict(
        cid="DL-00", category="Session", dataloss=True, status="Pass",
        title="Diagnostic is running as the editor",
        observed=lines,
        expected="No other profile on this machine carries ArcGIS Pro user data.",
        impact="The per-user findings below describe the account that edits.",
        fix="None.")


def decide_backup(config_path, settings, home_class, error=None):
    """DL-03. The project backup interval, and what it does not protect.

    Named for what it is not. Editors read "Create backups of project" as an
    autosave for their edits; it backs up the project file and nothing else, so
    a stranded version state is stranded regardless of this setting. On a
    network or synced home folder a short interval is also a recurring
    multi-second stall, which is the pause the editor calls "the refresh".

    error is a failed read of user.config, as distinct from an absent one. A
    file nobody could read is not a file whose settings are all `<absent>`, and
    printing that list would carry a verdict about settings nobody saw.
    """
    if not config_path:
        return dict(
            cid="DL-03", category="ArcGISPro", dataloss=True, status="Info",
            measured=False,
            title="Pro project backup interval",
            observed="No user.config was located for this profile, so the "
                     "backup settings could not be read.",
            expected="A readable user.config for the installed Pro release.",
            impact="S2 and S3 undetermined.",
            fix="Read it in Pro: Project > Options > General > Create backups "
                "of project.")
    if error:
        return dict(
            cid="DL-03", category="ArcGISPro", dataloss=True, status="Info",
            measured=False,
            title="Pro project backup interval",
            observed=["%s could not be read: %s. Its settings are unknown, "
                      "which is not the same as all of them being unset."
                      % (config_path, error)],
            expected="A user.config small enough and well-formed enough to "
                     "read.",
            impact="S2 and S3 undetermined.",
            fix="Read it in Pro: Project > Options > General > Create backups "
                "of project.")
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
    return dict(
        cid="DL-03", category="ArcGISPro", dataloss=True, status=status,
        title="Pro project backup is not an edit autosave",
        observed=["CreateProjectBackups = %s" % enabled,
                  "CreateProjectBackupsIntervalMinutes = %s" % interval,
                  "SaveProProjectLimit = %s" % limit,
                  "recent project storage: %s" % home_class,
                  "user.config: %s" % config_path],
        expected="Understood as a project-file backup only. On a network or "
                 "synced home folder an interval of five minutes or less is a "
                 "recurring multi-second stall.",
        impact="S2, the periodic stall the editor calls a refresh. It does not "
               "protect against S3: this copies the project file, never the "
               "edit session, so a lost version state is lost either way.",
        fix="Project > Options > General > Create backups of project: raise "
            "the interval, or turn it off while the home folder is remote, and "
            "lower SaveProProjectLimit to cut backup churn. Tell the editor "
            "plainly that this feature does not protect her feature edits.",
        detail="Read-only parse. Hand-editing user.config is unsupported and "
               "is overwritten the next time Pro saves settings.")


def decide_storage(entries):
    """DL-05. Where the recently opened projects actually live.

    entries: (path, when, class) newest first.

    One title per branch. A single accusatory title made a healthy machine
    print "[PASS] Active projects live on network storage", which reads as the
    failure in a report whose whole value is being diffed against a known-good
    workstation.
    """
    if not entries:
        return dict(
            cid="DL-05", category="Storage", dataloss=True, status="Info",
            measured=False,
            title="Project storage location",
            observed="No recently opened project was found, so where the "
                     "editor works could not be determined.",
            expected="Projects used for active editing on a local disk.",
            impact="S1 and S2 undetermined.",
            fix="Ask the editor to open her project and read Project > "
                "Options > General > Current Settings > Home Folder.")
    lines = []
    remote = []
    for path, when, cls in entries:
        lines.append("%-8s %s  %s" % (cls, when, path))
        if cls in ("NETWORK", "ONEDRIVE"):
            remote.append(path)
    if remote:
        return dict(
            cid="DL-05", category="Storage", dataloss=True, status="Fail",
            title="Active projects live on network or synced storage",
            observed=lines,
            expected="Projects used for active editing on a local disk. File "
                     "geodatabases and project files are chatty - many small "
                     "metadata round trips per operation - and every one pays "
                     "the latency of the share.",
            impact="S1 slow open, S2 stall during a backup, S3 a save that "
                   "times out over SMB is what strands a version state, and "
                   "S5 paste round trips.",
            fix="Copy the working project and any file geodatabase to a local "
                "folder for active editing, and keep only the enterprise "
                "layers remote. --apply will not move projects: that is a "
                "reviewed operator action.",
            detail="remote=%d of %d recent project(s)" % (len(remote),
                                                          len(entries)))
    return dict(
        cid="DL-05", category="Storage", dataloss=True, status="Pass",
        title="Project storage location (every recent project is local)",
        observed=lines,
        expected="Projects used for active editing on a local disk.",
        impact="None.", fix="None.")


def decide_kfm(documents, onedrive_root, account_lines):
    """DL-06. Are the user's documents redirected into OneDrive?

    The verdict is the Documents path, which is measured directly. The
    redirection bitmask is printed as supporting evidence and is not decoded:
    the value most guides name does not exist on current OneDrive builds, and
    rendered as absent on a box whose Desktop, Documents and Pictures were all
    redirected, so the line contradicted the finding. Every account subkey is
    enumerated, because a second tenant lands on its own key.
    """
    lines = ["Documents (Personal) = %s" % (documents or "<unreadable>"),
             "OneDrive root        = %s" % (onedrive_root or "<unset>")]
    lines.extend(account_lines or
                 ["KFM: no OneDrive account subkey for this profile."])
    if not documents:
        return dict(
            cid="DL-06", category="Storage", dataloss=True, status="Info",
            measured=False,
            title="OneDrive Known Folder Move",
            observed=lines + ["The Documents shell folder could not be read, "
                              "so redirection was not determined."],
            expected="GIS projects outside every OneDrive-synced tree.",
            impact="S1, S2 and S3 undetermined.",
            fix="Read it by hand, on the Location tab of the Documents folder "
                "properties.")
    redirected = is_onedrive_path(
        documents, [onedrive_root] if onedrive_root else [])
    return dict(
        cid="DL-06", category="Storage", dataloss=True,
        status="Fail" if redirected else "Pass",
        title=("Documents redirected into OneDrive (Known Folder Move)"
               if redirected else "Documents not redirected into OneDrive"),
        observed=lines,
        expected="GIS projects and geodatabases outside every OneDrive-synced "
                 "tree. Esri does not support ArcGIS Pro against cloud-sync "
                 "storage.",
        impact="S1, S2 and S3. OneDrive can lock or partially sync a "
               "geodatabase component file mid-write while Pro expects "
               "exclusive access, and Known Folder Move redirects Documents "
               "silently, so a project saved there is cloud-synced without the "
               "user choosing it.",
        fix="Store projects under a plain local folder that is not redirected. "
            "If Known Folder Move is managed centrally, request an exclusion "
            "for the GIS working folder. --apply will not change redirection: "
            "that is an organisation policy decision.")


def decide_placeholders(roots, inspected, placeholders):
    """DL-07. Dehydrated Files-On-Demand items under the project tree.

    A geodatabase whose component files are split between cloud-only
    placeholders and local copies stalls on every recall, and OneDrive can hold
    a lock on one of them while Pro writes another.
    """
    if not roots:
        return dict(
            cid="DL-07", category="Storage", dataloss=True, status="Info",
            measured=False,
            title="Cloud placeholder (dehydrated) project files",
            observed="No project folder was found to inspect, so placeholder "
                     "state was not measured this run.",
            expected="Any project folder fully local, with no Files-On-Demand "
                     "placeholders.",
            impact="S1 and S2 undetermined.",
            fix="If projects are stored elsewhere, check that folder for items "
                "marked available when online.")
    if placeholders:
        return dict(
            cid="DL-07", category="Storage", dataloss=True, status="Warn",
            title="Project tree contains cloud placeholders",
            observed=(["%d placeholder item(s) in %d inspected "
                       "entries:" % (len(placeholders), inspected)]
                      + list(placeholders[:15])),
            expected="No dehydrated item anywhere Pro reads a project or a "
                     "geodatabase.",
            impact="S1, S2 and S3. Recall stalls the draw, and a file OneDrive "
                   "is rehydrating can be locked while Pro writes to it.",
            fix="Mark the folder always keep on this device, or better, move "
                "the projects out of the synced tree entirely.")
    return dict(
        cid="DL-07", category="Storage", dataloss=True, status="Pass",
        title="Cloud placeholder (dehydrated) project files",
        observed="%d entries inspected under %s; no dehydrated item found."
                 % (inspected, "; ".join(roots)),
        expected="No dehydrated items.", impact="None.", fix="None.")


def decide_offline_files(policy_value):
    """DL-08. The Offline Files (CSC) client-side cache.

    Deliberately not the CSC service start value. That minifilter is
    boot-started on stock Windows whether or not the cache is enabled, so
    reading it as a stand-in reported the cache ENABLED and raised a warning on
    a machine whose cache was off - and it only ever ran on the one path where
    nobody could cross-check it, on a check that bears on data loss. Only the
    policy value states intent, so an unset policy is reported as unknown.
    """
    if policy_value is None:
        return dict(
            cid="DL-08", category="Network", dataloss=True, status="Info",
            measured=False,
            title="Offline Files (CSC) state",
            observed="No Offline Files policy value is set, so the cache state "
                     "could not be determined. The CSC service start value is "
                     "not used as a stand-in: it is boot-started on stock "
                     "Windows regardless of the cache, and inferring from it "
                     "produced a false warning.",
            expected="Offline Files disabled where geodatabases are accessed.",
            impact="S3 undetermined.",
            fix="Read it by hand: Control Panel > Sync Center > Manage offline "
                "files.")
    enabled = bool(policy_value)
    return dict(
        cid="DL-08", category="Network", dataloss=True,
        status="Warn" if enabled else "Pass",
        title="Offline Files (CSC) on network project paths",
        observed="The Offline Files cache is %s by policy (value %s)."
                 % ("ENABLED" if enabled else "disabled", policy_value),
        expected="Disabled. A cached copy of a geodatabase component file can "
                 "be served to Pro while the share holds a newer one.",
        impact="S3. Stale reads and deferred writes on a network geodatabase "
               "look exactly like vanished edits.",
        fix="Control Panel > Sync Center > Manage offline files > disable, or "
            "exclude the GIS shares by policy. Not auto-fixed: it is "
            "machine-wide and may be required for other business data.")


def decide_crash(dumps, recent, wer_lines, wer_configured):
    """DL-09. Crash artefacts, and the honest reading of finding none.

    No dump is not health. Ending a hang with Task Manager writes no minidump
    at all, and per-process dump collection is off by default, so absence of a
    dump is absence of evidence. Reporting Pass here would be the same green
    light on a value that was never going to exist that this tool exists to
    stop, so the empty branch reports what collection is configured instead.
    """
    if dumps:
        return dict(
            cid="DL-09", category="ArcGISPro", dataloss=True,
            status="Warn" if recent else "Info",
            title="ArcGIS Pro crash dumps (evidence of a force-close during an "
                  "edit)",
            observed=dumps,
            expected="No recent ArcGIS Pro dump. A dump timestamped inside an "
                     "editing window is the moment the session died.",
            impact="S3. Correlate each dump timestamp against the edit the "
                   "editor says she lost. %d dump(s) in the last %d days."
                   % (recent, CRASH_RECENT_DAYS),
            fix="Keep the dumps for the support case; the filenames encode the "
                "exact build. Purge them only after the case closes, because "
                "Pro does not prune this folder.")
    return dict(
        cid="DL-09", category="ArcGISPro", dataloss=True, status="Info",
        title="No ArcGIS Pro crash dump found, and a force-close would not "
              "produce one",
        observed=wer_lines,
        expected="Dump collection configured for ArcGISPro.exe before the next "
                 "hang, so there is an artefact to timestamp.",
        impact="S3. This check can neither confirm nor rule out a force-close. "
               "A hang ended from Task Manager writes no minidump, so no dump "
               "is absence of evidence, not evidence of health.",
        fix=("Dump collection is configured; keep it. "
             if wer_configured else
             "No dump collection is configured, so there will be no artefact "
             "the next time this happens. Enable it under the Windows Error "
             "Reporting LocalDumps key for ArcGISPro.exe, elevated. ")
            + "Not auto-fixed: it is a machine-wide policy change and belongs "
              "to whoever owns the image.")


def decide_tcp(cid, category, title, host, port, probe, warn_ms, impact, fix,
               dataloss=False):
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
            observed="No host was given, so nothing was measured this run. "
                     "Network probes are opt-in.",
            expected="A reachable host.", impact="Undetermined.",
            fix="Re-run naming the host to probe.")
    opened, elapsed, error = probe
    if opened is None:
        return dict(
            cid=cid, category=category, dataloss=dataloss, status="Info",
            measured=False, title=title,
            observed="%s:%d did not resolve (%s), so the server was not "
                     "measured this run." % (host, port, error),
            expected="A host name that resolves.",
            impact="Undetermined. A name that never resolved says nothing "
                   "about the server's latency.",
            fix="Check the spelling of the host name, the DNS suffix and the "
                "VPN, then re-run.")
    observed = "%s:%d open=%s elapsed=%d ms%s" % (
        host, port, opened, elapsed, (" (%s)" % error) if error else "")
    if not opened:
        status = "Fail"
    elif elapsed > warn_ms:
        status = "Warn"
    else:
        status = "Pass"
    return dict(
        cid=cid, category=category, dataloss=dataloss, status=status,
        title=title, observed=observed,
        expected="A TCP connect well inside %d ms. Connect only: no "
                 "credential is used." % warn_ms,
        impact=impact, fix=fix,
        kpi={"elapsed_ms": elapsed, "warn_ms": warn_ms})


def decide_sde_files(paths, index_copies):
    """DL-11. Connection files on this box. Paths only, contents never opened.

    A .sde file can embed a saved credential, so this tool records where they
    are and never opens, reads, prints or copies one.

    The extension is re-checked after the scan. A Windows filename filter also
    matches the short 8.3 alias, so a glob for the .sde extension returns
    .sdeold and .sdebak strays too, which inflated both this inventory and the
    index count that gates on it.
    """
    if not paths:
        return dict(
            cid="DL-11", category="Geodatabase", dataloss=True, status="Info",
            title="SDE connection files on this box",
            observed="No connection file under the ESRI favorites or index "
                     "folders.",
            expected="A small, curated set of connection files.",
            impact="None.", fix="None.")
    lines = []
    risky = []
    for path, cls in paths:
        lines.append("%s%s" % (path, "" if cls == "LOCAL" else " [%s]" % cls))
        if cls != "LOCAL":
            risky.append(path)
    status = "Info"
    if risky or index_copies >= INDEX_MANY_CONNECTIONS:
        status = "Warn"
    return dict(
        cid="DL-11", category="Geodatabase", dataloss=True, status=status,
        title="SDE connection files (paths only, contents never opened)",
        observed=(["%d connection file(s); %d cached under the search index:"
                   % (len(paths), index_copies)] + lines[:40]),
        expected="One connection file per environment, stored locally, pointed "
                 "at the version the editor is supposed to edit. Stale copies "
                 "cached under the search index bloat the index and re-open "
                 "dead connections at startup.",
        impact="S1, every connection re-enumerates the version list at open. "
               "S3, editing through the wrong version is indistinguishable "
               "from lost edits.",
        fix="Prune the stale connection files. To force a clean rebuild, close "
            "Pro and delete the contents of the ESRI ResourceIndex folder. "
            "Never open or copy a connection file to inspect it: they carry "
            "saved credentials.")


def decide_pro_running(processes):
    """DL-12. Pro must be closed before any cache fix, and before a save is safe.

    processes: the matching process lines, or None when the process list could
    not be read. None is not an empty list: an unreadable list reported as a
    closed Pro opened the project file and let the cache fix run.
    """
    if processes is None:
        return dict(
            cid="DL-12", category="ArcGISPro", dataloss=True, status="Info",
            measured=False,
            title="Whether ArcGIS Pro is running could not be determined",
            observed="The process list could not be read, so whether Pro is "
                     "running was not measured this run.",
            expected="A readable process list.",
            impact="Blocks the cache fix and the project-file read, because "
                   "neither is safe while Pro may be running.",
            fix="Close Pro by hand, then re-run where tasklist can run.")
    if processes:
        return dict(
            cid="DL-12", category="ArcGISPro", dataloss=True, status="Warn",
            title="ArcGIS Pro is running during this diagnostic",
            observed=processes,
            expected="Pro closed. Cache and index state cannot be cleared "
                     "safely while Pro holds it, and an open edit session must "
                     "be saved and closed first.",
            impact="Blocks the cache fix, and any --apply cache action will be "
                   "refused while it is true.",
            fix="Have the editor save, close the edit session and exit Pro "
                "before re-running with --apply.")
    return dict(
        cid="DL-12", category="ArcGISPro", dataloss=True, status="Pass",
        title="ArcGIS Pro is not running during this diagnostic",
        observed="No ArcGIS Pro process is running.",
        expected="Pro closed.", impact="None.", fix="None.")


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
    if not folders:
        return dict(
            cid="PF-07", category="Cache", dataloss=True, status="Info",
            title="Leftover ArcGIS Pro session folders",
            observed="No leftover session folder under %s, so there is no "
                     "unsaved scratch work to recover from one." % temp_root,
            expected="None, or a few that can be inspected.",
            impact="S3, no recovery lead here.", fix="None.")
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
    return dict(
        cid="PF-07", category="Cache", dataloss=True, status=status,
        title="Leftover ArcGIS Pro session folders (possible unsaved scratch "
              "work)",
        observed=observed,
        expected="One per live session. Pro does not clean these up, so the "
                 "count is a rough session history and not a crash count - but "
                 "a folder from a session that died can still hold the scratch "
                 "project that session never saved.",
        impact="S3. Any folder flagged as holding work is a recovery lead for "
               "the vanished edits and must be inspected before it is deleted. "
               "S1 marginally, disk churn, for the rest.",
        fix="Open the flagged folders first and check the scratch project "
            "against the incident date. Only then, with Pro closed, remove the "
            "session folders older than a few days. Deliberately not "
            "auto-fixed: deleting these is exactly how the last copy of "
            "unsaved work disappears.",
        detail=("Inspect first: " + "; ".join(with_work)) if with_work
               else "No folder holds a project file or a non-empty scratch "
                    "table.")


# ------------------------------------------------------- performance section

def decide_pro_version(version, real, build, install_dir, exe_version):
    """PF-04. The installed release. Always Info: a version is a fact.

    A verdict would need a support matrix that goes stale, so this records the
    build for the case instead of grading it.
    """
    if not version and not exe_version:
        return dict(
            cid="PF-04", category="ArcGISPro", status="Info", measured=False,
            title="ArcGIS Pro version",
            observed="The ArcGIS Pro registry key and the executable were both "
                     "absent, so the installed version could not be read.",
            expected="A resolvable installed version.",
            impact="Symptoms cannot be mapped to version-specific behaviour.",
            fix="Confirm Pro is installed for all users and re-run.")
    return dict(
        cid="PF-04", category="ArcGISPro", status="Info",
        title="ArcGIS Pro version and patch level",
        observed=["Version = %s" % (version or "<absent>"),
                  "RealVersion = %s" % (real or "<absent>"),
                  "BuildNumber = %s" % (build or "<absent>"),
                  "InstallDir = %s" % (install_dir or "<absent>"),
                  "ArcGISPro.exe file version = %s" % (exe_version or "<absent>")],
        expected="The current patch for the installed release.",
        impact="S4 and any driver-related draw stall: the default rendering "
               "engine changed between releases, so which one is installed "
               "decides whether a graphics fault is even in scope.",
        fix="Record this version on the support case, and check for a released "
            "patch for this build before changing any client setting.")


def decide_rendering(config_path, settings, error=None):
    """PF-05. The display settings, read only.

    None of these cause edit loss. They are the draw-performance levers, and
    they are here so the report can be diffed against a workstation that is
    known to be fast.

    error is a failed read of user.config: the settings are unknown, not all
    off, and a rendering verdict off a file nobody opened would name a lever
    this run never looked at.
    """
    if not config_path:
        return dict(
            cid="PF-05", category="Display", status="Info", measured=False,
            title="Pro rendering and display settings",
            observed="No user.config was located, so the display settings "
                     "could not be read.",
            expected="A readable user.config.", impact="S4 undetermined.",
            fix="Read them in Pro: Project > Options > Display.")
    if error:
        return dict(
            cid="PF-05", category="Display", status="Info", measured=False,
            title="Pro rendering and display settings",
            observed="%s could not be read: %s. The display settings are "
                     "unknown, which is not the same as all of them being off."
                     % (config_path, error),
            expected="A readable user.config.", impact="S4 undetermined.",
            fix="Read them in Pro: Project > Options > Display.")
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
        title="Pro rendering and display settings (read-only)",
        observed=[cfg_line(settings, n) for n in names],
        expected="A rendering engine matching a stable driver path for this "
                 "graphics adapter, stereoscopic mode off, feature caching on. "
                 "None of these cause edit loss.",
        impact="S4, and general draw speed.",
        fix="Change these through Project > Options > Display only. This tool "
            "never writes user.config: Pro overwrites it on the next settings "
            "save.",
        detail="Source: %s" % config_path)


def decide_caches(path, exists, total_bytes, files, oldest, oldest_age_days,
                  stale_files):
    """PF-06. The local display and geoprocessing cache.

    Not a data-loss cause. It is the one folder that grows unattended and that
    Pro does not reliably purge, so it is the cheapest thing to fix first.
    """
    if not exists:
        return dict(
            cid="PF-06", category="Cache", status="Info",
            title="ESRI local cache size and staleness",
            observed="%s  <absent>" % path,
            expected="Present, modest and self-clearing.",
            impact="None.", fix="None.")
    big, stale, ratio = cache_triggers(total_bytes, files, oldest_age_days,
                                       stale_files)
    return dict(
        cid="PF-06", category="Cache", status="Warn" if (big or stale) else "Pass",
        title="ESRI local cache size and staleness",
        observed=[path,
                  "%s in %d files; oldest %s; %d cache file(s) older than %d days"
                  % (format_bytes(total_bytes), files, oldest, stale_files,
                     CACHE_STALE_DAYS),
                  "stale fraction %.0f%%; size trigger %s; staleness trigger %s"
                  % (ratio * 100.0, big, stale)],
        expected="A display and geoprocessing cache Pro is actually reusing, "
                 "not months of orphaned cache files. Pro does not reliably "
                 "purge these.",
        impact="S1, slower startup, and gradual draw degradation. Not a "
               "data-loss cause.",
        fix="With Pro closed, move the cache folder aside and let Pro rebuild "
            "it, or tick Project > Options > Display > clear the cache when "
            "the application closes. --apply renames the folder aside and "
            "prints the undo; it never deletes it.",
        autofixable=True,
        kpi={"bytes": total_bytes, "files": files,
             "stale_pct": ratio * 100.0, "stale_files": stale_files})


def decide_index(res_bytes, res_files, connection_copies, status_stamp,
                 settings, error=None):
    """PF-08. The catalog search index.

    Indexing a network connection walks the share at startup and again on every
    incremental pass, which is one of the two ways a five-minute open happens
    on a machine that is otherwise healthy.

    error is a failed read of user.config. The folder counts below it were
    measured, so a verdict off those stands; the settings lines say they were
    not read rather than printing `<absent>` for a file nobody opened.
    """
    lines = ["ResourceIndex: %s in %d files" % (format_bytes(res_bytes),
                                                res_files),
             "Cached connection files indexed: %d" % connection_copies,
             "Index status last written: %s" % status_stamp]
    names = ("IndexLocalContent", "IndexNetworkConnections",
             "SkipServerConnections", "RunEvenLoggedOut")
    if error:
        lines.append("user.config could not be read (%s), so the four index "
                     "settings below are unknown rather than unset: %s"
                     % (error, ", ".join(names)))
    else:
        lines.extend([cfg_line(settings, n) for n in names])
    network = cfg(settings, "IndexNetworkConnections")
    status = "Info"
    if connection_copies >= INDEX_MANY_CONNECTIONS or network == "True":
        status = "Warn"
    return dict(
        cid="PF-08", category="ArcGISPro", status=status,
        title="Project and catalog search index state",
        observed=lines,
        expected="Network-connection indexing off on a machine whose data is "
                 "on a share, and a handful of connection copies, not dozens.",
        impact="S1. Indexing a network connection walks the share at startup "
               "and during every incremental pass.",
        fix="Project > Options > General > Search Index: turn off "
            "network-connection indexing. To force a clean rebuild, close Pro "
            "and delete the contents of the ResourceIndex folder.")


def decide_gpu_preference(exe, value, readable=True):
    """PF-11. Which adapter Windows hands ArcGIS Pro.

    This stays advice, never an automatic fix. Selecting a different adapter
    can move a startup crash onto a machine whose other driver was stable;
    absence of a crash during the probe cannot prove that the switch is safe.
    """
    if not readable:
        return dict(
            cid="PF-11", category="Hardware", status="Info", measured=False,
            title="Per-app GPU preference for ArcGIS Pro",
            observed="The registry could not be read on this platform, so the "
                     "graphics preference is unknown. An unreadable value is "
                     "not an absent one.",
            expected="%s, high performance." % GPU_PREFERENCE_WANTED,
            impact="S4 undetermined.",
            fix="Read it under Settings > System > Display > Graphics.")
    if not value:
        return dict(
            cid="PF-11", category="Hardware", status="Warn",
            title="Per-app GPU preference for ArcGIS Pro",
            observed="No value named %s under %s, so Windows picks the adapter "
                     "automatically, which on a hybrid machine can mean the "
                     "integrated one." % (exe, GPU_PREFERENCE_KEY),
            expected="%s, high performance." % GPU_PREFERENCE_WANTED,
            impact="S4 and slow draw. Not a data-loss cause.",
            fix="Only while Pro is stable, review Settings > System > Display "
                "> Graphics with the graphics administrator. If a crash began "
                "after an older AP-02 run, use that report's exact undo "
                "command to restore the previous value before relaunching.")
    good = GPU_PREFERENCE_WANTED.rstrip(";") in value
    return dict(
        cid="PF-11", category="Hardware", status="Pass" if good else "Warn",
        title="Per-app GPU preference for ArcGIS Pro",
        observed="%s = %s" % (exe, value),
        expected="%s. 0 lets Windows decide, 1 is power saving and the "
                 "integrated adapter, 2 is high performance."
                 % GPU_PREFERENCE_WANTED,
        impact="S4 and slow draw; an incompatible forced adapter can also "
               "crash Pro at the splash screen.",
        fix="Review the adapter and supported driver with the graphics "
            "administrator; this tool never changes the preference. If a "
            "crash began after an older AP-02 run, use that report's exact "
            "undo command to restore the previous value.")


def decide_memory(total_gb, free_gb):
    """PF-12. Installed and free memory.

    Without the unread guard a failed read computed zero and rendered as
    "Fail, total 0 GB" - a hardware verdict off a value nobody read, telling
    the operator to replace memory that is probably fine.
    """
    if total_gb is None:
        return dict(
            cid="PF-12", category="Hardware", status="Info", measured=False,
            title="Physical memory",
            observed="Installed and free memory could not be read on this "
                     "platform, so no verdict is given. This is a failed read, "
                     "not a hardware finding.",
            expected="Esri's published requirement: minimum %.0f GB, recommended "
                     "%.0f GB, optimal %.0f GB or more. This tool warns "
                     "below %.0f GB, its own allowance for enterprise "
                     "editing, which sits between Esri's minimum and "
                     "recommended figures."
                     % (ESRI_RAM_MIN_GB, ESRI_RAM_REC_GB, ESRI_RAM_OPT_GB,
                        MIN_MEMORY_GB),
            impact="S1 and S2 undetermined.",
            fix="Read it by hand from Task Manager and re-run.")
    if total_gb < MIN_MEMORY_GB:
        status = "Fail"
    elif free_gb < LOW_FREE_MEMORY_GB:
        status = "Warn"
    else:
        status = "Pass"
    return dict(
        cid="PF-12", category="Hardware", status=status,
        title="Physical memory",
        observed="Total %.2f GB; free %.2f GB at the time of the check, which "
                 "is a live snapshot and not a specification."
                 % (total_gb, free_gb),
        expected="At least %.0f GB for enterprise editing; %.0f GB comfortable."
                 % (MIN_MEMORY_GB, MIN_MEMORY_GB * 2),
        impact="S1 and S2 if the machine is paging during a draw.",
        fix="None if adequate.",
        kpi={"total_gb": total_gb, "free_gb": free_gb,
             "free_pct": (100.0 * free_gb / total_gb) if total_gb else 0.0})


def decide_disk(volumes):
    """PF-13. Free space on the system volume and on any local project volume.

    volumes: (name, total_bytes, free_bytes).
    """
    if not volumes:
        return dict(
            cid="PF-13", category="Hardware", status="Info", measured=False,
            title="Free disk space",
            observed="No volume could be read, so free space was not measured "
                     "this run.",
            expected="Esri's published requirement is %.0f GB of free space, on an "
                     "SSD when recommended; the visualization cache alone "
                     "may take up to %.0f GB. This tool fails below %.0f GB "
                     "or %.0f%% free, its own lower allowance."
                     % (ESRI_FREE_DISK_GB, ESRI_VIZ_CACHE_GB,
                        MIN_FREE_DISK_GB, MIN_FREE_DISK_PCT),
            impact="S1 and S2 undetermined.",
            fix="Read it by hand and re-run.")
    lines = []
    low = False
    worst_gb = None
    worst_pct = None
    for name, total, free in volumes:
        free_gb = free / float(1024 ** 3)
        total_gb = total / float(1024 ** 3)
        pct = (100.0 * free / total) if total else 0.0
        lines.append("%s %.2f GB free of %.2f GB (%.1f%%)"
                     % (name, free_gb, total_gb, pct))
        if free_gb < MIN_FREE_DISK_GB or pct < MIN_FREE_DISK_PCT:
            low = True
        # The worst volume is the one the gauge draws, because a report that
        # averaged a full system disk with an empty data disk would show a
        # healthy machine.
        if worst_pct is None or pct < worst_pct:
            worst_pct = pct
            worst_gb = free_gb
    return dict(
        cid="PF-13", category="Hardware", status="Warn" if low else "Pass",
        title="Free disk space on the system and project volumes",
        observed=lines,
        expected="At least %.0f GB and %.0f%% free on the volume holding the "
                 "caches, the scratch workspace and any local project."
                 % (MIN_FREE_DISK_GB, MIN_FREE_DISK_PCT),
        impact="S1 and S2. A full volume stalls the display cache and the "
               "geoprocessing scratch workspace.",
        fix="Clear the local cache and the leftover session folders first. "
            "They are the two that grow unattended.",
        kpi={"free_gb_worst": worst_gb, "free_pct_worst": worst_pct,
             "volumes": len(volumes)})


def decide_power_plan(guid, name):
    """PF-15. The active power plan.

    Not a data-loss cause on its own, but a throttled processor lengthens the
    window in which a save can time out, and a timed-out save is how an edit
    ends up stranded.
    """
    if not guid:
        return dict(
            cid="PF-15", category="System", status="Info", measured=False,
            title="Active power plan",
            observed="The active power scheme could not be parsed, so no "
                     "verdict is given.",
            expected="High performance, or a balanced plan with no processor "
                     "cap while on mains power.",
            impact="S1 and S2 undetermined.",
            fix="Read it in Control Panel > Power Options.")
    high = (guid.lower() == HIGH_PERFORMANCE_PLAN
            or bool(re.search(r"high performance|ultimate", name or "",
                              re.IGNORECASE)))
    return dict(
        cid="PF-15", category="System", status="Pass" if high else "Warn",
        title="Active power plan",
        observed="%s  (%s)" % (name or "<unnamed>", guid),
        expected="High performance while docked, so the processor is not "
                 "parked during a long draw or a long save.",
        impact="S1 and S2. Not a data-loss cause on its own, but a throttled "
               "processor lengthens the window in which a save can time out.",
        fix="Switch the active plan to high performance. Some managed images "
            "hide that plan; then raise the processor minimum and maximum "
            "state on the active plan instead. Not auto-fixed: it is a "
            "machine-wide setting and may be set by policy.")


def decide_roaming(roaming_configured, esri_bytes, esri_files):
    """PF-26. A roaming profile carrying the ESRI settings folder.

    Copied down at logon and up at logoff, which adds to both the logon time
    and the first-launch delay, and is one of the quieter reasons a machine is
    slow only in the morning.
    """
    if roaming_configured is None:
        return dict(
            cid="PF-26", category="System", status="Info", measured=False,
            title="Roaming profile carrying the ESRI settings folder",
            observed=["The roaming profile state could not be read.",
                      "Roaming ESRI folder = %s in %d files"
                      % (format_bytes(esri_bytes), esri_files)],
            expected="The roaming ESRI folder excluded from the roaming "
                     "profile, or a local profile.",
            impact="S1 undetermined.",
            fix="Read it by hand from the user profile settings.")
    return dict(
        cid="PF-26", category="System",
        status="Warn" if roaming_configured else "Pass",
        title="Roaming profile carrying the ESRI settings folder",
        observed=["RoamingConfigured = %s" % roaming_configured,
                  "Roaming ESRI folder = %s in %d files"
                  % (format_bytes(esri_bytes), esri_files)],
        expected="The roaming ESRI folder excluded from the roaming profile, "
                 "or a local profile.",
        impact="S1. A roamed ESRI folder is copied down at logon and up at "
               "logoff, adding to both the logon time and the first launch.",
        fix="Exclude the roaming and local ESRI folders from the roaming "
            "profile by policy, or point Pro at a local settings location.")


def decide_symbology(feature_caching):
    """PF-27. Symbology that does not refresh after a field calculation.

    Always Info, and deliberately. No client setting was found that governs
    symbology invalidation after an attribute edit made outside the map, and
    none is invented here. Saying "probably the feature cache" in a verdict
    field would be exactly the guess this tool refuses to make.
    """
    return dict(
        cid="PF-27", category="Display", status="Info",
        title="Symbology not refreshing after a field calculation (S4)",
        observed="ManageFeatureCaching = %s. No client setting was found that "
                 "governs symbology invalidation after an attribute edit made "
                 "outside the map, and none is invented here."
                 % ("<absent>" if feature_caching is None else feature_caching),
        expected="A redraw after a field calculation. The renderer draws from "
                 "the in-memory feature cache, and closing and reopening the "
                 "map pane discards that cache, which is why that workaround "
                 "works.",
        impact="S4 only. This is a display-refresh behaviour and does not mean "
               "the attribute change was lost. Verify the value in the table "
               "before treating it as data loss.",
        fix="The workaround stays manual: refresh the map, or close and reopen "
            "the map pane. Report it with the build number from PF-04 and a "
            "Diagnostic Monitor trace. It is a client defect, not a setting.")


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
    if error is not None:
        return dict(
            cid="DL-04", category="Network", dataloss=True, status="Info",
            measured=False,
            title="Network adapter power management",
            observed="The adapter list could not be read: %s" % error,
            expected="Allow the computer to turn off this device: Disabled on "
                     "every adapter that carries the database session.",
            impact="S2 and S3 undetermined.",
            fix="Read it by hand: Device Manager > the adapter > Power "
                "Management.")
    if not adapters:
        return dict(
            cid="DL-04", category="Network", dataloss=True, status="Info",
            measured=False,
            title="Network adapter power management",
            observed="No physical adapter reported up, so the power setting "
                     "could not be determined.",
            expected="At least one physical adapter up.",
            impact="S2 and S3 undetermined.",
            fix="Read it by hand: Device Manager > the adapter > Power "
                "Management.")
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
            title="Windows may turn the network adapter off to save power",
            observed=lines,
            expected="Disabled on every adapter that carries the database "
                     "session.",
            impact="S2 and S3. With this on, Windows may power the adapter "
                   "down, and an edit session left open across that loses its "
                   "connection to the database.",
            fix="Device Manager > %s > Power Management > clear Allow the "
                "computer to turn off this device to save power. Not "
                "auto-fixed: it is a machine-wide device setting and needs an "
                "administrator." % enabled[0],
            detail="Adapters to change: %s" % ", ".join(enabled))
    if unknown:
        return dict(
            cid="DL-04", category="Network", dataloss=True, status="Info",
            measured=False,
            title="Network adapter power management",
            observed=lines + ["The setting could not be read for %d adapter(s), "
                              "so a Pass here would be a guess."
                              % len(unknown)],
            expected="A readable setting on every adapter that is up. The "
                     "provider often answers nothing for a docking or USB "
                     "adapter, or when the session is not elevated.",
            impact="S2 and S3 undetermined.",
            fix="Re-run elevated, or read it in Device Manager > the adapter "
                "> Power Management.")
    return dict(
        cid="DL-04", category="Network", dataloss=True, status="Pass",
        title="Windows will not turn the network adapter off to save power",
        observed=lines,
        expected="Disabled or unsupported on every adapter that is up.",
        impact="None.", fix="None.")


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
            module = (ntpath.basename(event["data"][3])
                      if len(event["data"]) > 3 and event["data"][3] else "")
            return ("ArcGIS Pro crashed (Application Error 1000)"
                    + ("; fault module: %s" % module if module else ""))
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
            title="Event timeline: Pro hangs, crashes or dirty shutdowns are "
                  "recorded on this machine",
            observed=lines,
            expected="No ArcGIS Pro crash or hang, and no unexpected shutdown, "
                     "during editing hours.",
            impact="S2 and S3. This is the wall-clock evidence. A hang ended "
                   "from Task Manager writes no dump (DL-09), but it can leave "
                   "an Application Hang event, and its time is the moment the "
                   "edit session died.",
            fix="Give these times to the editor and to the support case, and "
                "lay them beside the edit she says she lost. Read-only: "
                "nothing is cleared.",
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
            title="Event timeline: only SMB client connectivity events in "
                  "the window",
            observed=lines + [quiet_note],
            expected="No ArcGIS Pro crash or hang, and no unexpected "
                     "shutdown.",
            impact="S2 and S3. SMB connectivity events can precede a stall "
                   "on a share, but their ids are not interpreted here.",
            fix="Lay these times beside the edit she says she lost. If a "
                "project is on a share, read PF-02 and PF-03 as well.")
    if len(unreadable) == queried:
        return dict(
            cid="DL-13", category="System", dataloss=True, status="Info",
            measured=False,
            title="Event timeline",
            observed=lines + ["No log could be read, so the timeline was not "
                              "measured this run."],
            expected="Readable Application and System logs.",
            impact="S2 and S3 undetermined.",
            fix="Read the same events in Event Viewer, or re-run where the "
                "logs are readable.")
    lines.append(quiet_note)
    return dict(
        cid="DL-13", category="System", dataloss=True, status="Info",
        title="Event timeline: nothing recorded in the window",
        observed=lines,
        expected="No ArcGIS Pro crash or hang, and no unexpected shutdown.",
        impact="S2 and S3. Absence of an event is absence of evidence.",
        fix="If the incident is older than the window, read the same events "
            "from an archived log instead.")


def decide_smb(config, error, share_projects):
    """PF-02. The SMB client settings, read for the share the projects use.

    Multichannel is on by default. Off, it is a departure worth a line only
    when a recent project actually lives on a share; otherwise the setting
    is recorded for the diff and decides nothing.
    """
    if config is None:
        return dict(
            cid="PF-02", category="Network", status="Info", measured=False,
            title="SMB client configuration",
            observed="The SMB client configuration could not be read: %s"
                     % error,
            expected="A readable SMB client configuration.",
            impact="S1 and S2 undetermined.",
            fix="Read it by hand with Get-SmbClientConfiguration.")
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
        title="SMB client configuration (read-only)",
        observed=lines,
        expected="Multichannel on, which is the Windows default, wherever a "
                 "project is opened from a share.",
        impact="S1 and S2 when a project or geodatabase is on a share.",
        fix="Multichannel is a machine-wide network setting. Raise it with "
            "the desktop or network team. Not auto-fixed.")


def decide_share_latency(rows, omitted=0):
    """PF-03. One directory listing on the share each remote project uses.

    rows: (folder, elapsed_ms or None, entries, error). omitted: how many
    share folders the cap left untimed. A folder that could not be listed
    keeps a fast reading from becoming a Pass, the same rule DL-04 follows:
    the unreadable folder is the likely culprit.
    """
    if not rows:
        return dict(
            cid="PF-03", category="Network", status="Info",
            title="Directory listing time on the project share",
            observed="No recent project lives on a share, so there was no "
                     "share folder to time.",
            expected="Projects on a local disk.", impact="None.", fix="None.")
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
            title="Directory listing time on the project share",
            observed=lines + ["No share folder could be listed, so the latency "
                              "was not measured this run."],
            expected="A listable project folder.",
            impact="S1 undetermined.",
            fix="Check that the share is reachable from this session.")
    worst = max(timed)
    if worst >= SHARE_LIST_FAIL_MS:
        status = "Fail"
    elif worst >= SHARE_LIST_WARN_MS:
        status = "Warn"
    elif len(timed) < len(rows):
        return dict(
            cid="PF-03", category="Network", status="Info", measured=False,
            title="Directory listing time on the project share",
            observed=lines + ["At least one share folder could not be listed, "
                              "so a Pass here would be a guess."],
            expected="A listable project folder.",
            impact="S1 undetermined.",
            fix="Check that every share folder is reachable from this "
                "session.")
    else:
        status = "Pass"
    return dict(
        cid="PF-03", category="Network", status=status,
        title="Directory listing time on the project share",
        observed=lines,
        kpi={"worst_ms": worst, "warn_ms": SHARE_LIST_WARN_MS,
             "fail_ms": SHARE_LIST_FAIL_MS, "folders_timed": len(timed)},
        expected="One listing of at most %d entries well under %d ms. Pro "
                 "lists folders many times while it opens a project."
                 % (SHARE_LIST_LIMIT, SHARE_LIST_WARN_MS),
        impact="S1. Every folder listing Pro makes at open pays this.",
        fix="Copy the working project to a local disk for active editing. If "
            "the share must be used, give the network team this number.")


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
    if prefs is None:
        return dict(
            cid="PF-17", category="Security", status="Info", measured=False,
            title="Microsoft Defender exclusions for ArcGIS Pro",
            observed="The Defender preferences could not be read: %s. Another "
                     "product may be the scanner here." % error,
            expected="A readable Defender configuration.",
            impact="S1 and S2 undetermined.",
            fix="Ask the security team which product scans on access here.")
    paths = [p for p in as_list(prefs.get("ExclusionPath")) if p]
    procs = [p for p in as_list(prefs.get("ExclusionProcess")) if p]
    lines = ["DisableRealtimeMonitoring = %s"
             % prefs.get("DisableRealtimeMonitoring")]
    baseline = (["path     %s" % p for p in base_paths]
                + ["process  %s" % pro_exe])
    if [p for p in paths + procs if DEFENDER_SENTINEL.search("%s" % p)]:
        return dict(
            cid="PF-17", category="Security", status="Info", measured=False,
            title="Microsoft Defender exclusions are hidden from this session",
            observed=lines + ["Defender put \"N/A: Must be an administrator "
                              "to view exclusions\" where each list should "
                              "be, so what is excluded could not be "
                              "determined. Baseline that would be checked:"]
                     + ["  " + b for b in baseline],
            expected="A read that returns the real lists.",
            impact="S1 and S2 undetermined. Nothing here says the baseline is "
                   "missing.",
            fix="Re-run elevated, or ask the security team for the list. Do "
                "not request exclusions off this run: it never saw what is "
                "already configured.")
    if prefs.get("DisableRealtimeMonitoring") is True:
        return dict(
            cid="PF-17", category="Security", status="Info",
            title="Defender real-time scanning is off",
            observed=lines,
            expected="Exclusions decide nothing while real-time scanning is "
                     "off. Another product may be scanning instead.",
            impact="None from Defender.",
            fix="Ask the security team which product scans on access here.")
    if prefs.get("HideExclusionsFromLocalAdmins") is True and not paths:
        return dict(
            cid="PF-17", category="Security", status="Info", measured=False,
            title="Microsoft Defender exclusions are hidden by policy",
            observed=lines + ["HideExclusionsFromLocalAdmins = True and no "
                              "list came back, so what is excluded could not "
                              "be determined."],
            expected="A read that returns the real lists.",
            impact="S1 and S2 undetermined.",
            fix="Ask the security team for the configured list.")
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
    return dict(
        cid="PF-17", category="Security",
        status="Warn" if (missing or not proc_ok) else "Pass",
        title="Microsoft Defender exclusions for ArcGIS Pro",
        observed=lines,
        expected="The Pro install folder and the editor's ESRI folders "
                 "excluded, and ArcGISPro.exe excluded as a process. This "
                 "baseline is this tool's, not an Esri list: review it against "
                 "the organisation's antivirus standard.",
        impact="S1 and S2 when on-access scanning is what is slow. Prove it "
               "first with the Defender performance analyzer.",
        fix="Ask the security team to review the folders marked NOT EXCLUDED. "
            "Defender expands a variable in an exclusion as LocalSystem, so "
            "%APPDATA% there is not the editor's folder: give the real path. "
            "Not auto-fixed: exclusions are a security decision.")


def scan_aprx(path, texts, onedrive, network_drives=()):
    """What one project file says about its home folder and its refresh timers.

    texts: (entry name, JSON text) from inside the .aprx. defaultFolder and
    defaultGeoDatabase are resolved against the project folder before they
    are classified: Pro stores "." for a home folder that is the project
    folder itself, and "." on its own is never a share.
    """
    folder = ntpath.dirname(path)
    out = {"path": path, "home": None, "gdb": None, "refresh": [],
           "parsed": 0, "capped": False,
           # What the newer checks read out of the same documents. Each one
           # is a count or a list this tool measured, never an inference: an
           # absent key is an absent key, and the check above it says so.
           "layers": [], "maps": [], "label_engine": None, "depth": 0}

    def data_uri(node):
        """Where a layer's rows come from, as a string this tool can classify."""
        table = node.get("featureTable")
        if isinstance(table, dict):
            conn = table.get("dataConnection")
            if isinstance(conn, dict):
                for key in ("workspaceConnectionString", "url", "dataset",
                            "workspaceFactory"):
                    value = conn.get(key)
                    if value:
                        return "%s" % value
        conn = node.get("serviceConnectionProperties")
        if isinstance(conn, dict) and conn.get("url"):
            return "%s" % conn["url"]
        return ""

    def layer_record(node, depth):
        kind = "%s" % node.get("type", "")
        table = node.get("featureTable") if isinstance(
            node.get("featureTable"), dict) else {}
        joins = table.get("joins")
        labels = node.get("labelClasses")
        record = {
            "name": "%s" % node.get("name", "<unnamed>"),
            "type": kind,
            "uri": data_uri(node),
            "wkid": None,
            "joins": len(joins) if isinstance(joins, list) else 0,
            "query": bool(node.get("definitionQuery")
                          or node.get("definitionExpression")
                          or table.get("definitionExpression")),
            "labels": len(labels) if isinstance(labels, list) else 0,
            "scaled": bool(node.get("minScale") or node.get("maxScale")),
            "depth": depth,
        }
        ref = node.get("spatialReference")
        if isinstance(ref, dict) and ref.get("wkid"):
            record["wkid"] = ref.get("wkid")
        out["layers"].append(record)
        if depth > out["depth"]:
            out["depth"] = depth

    # An explicit stack, not recursion. A project document nests as deep as
    # whoever wrote the writer nested it, and the recursive version of this
    # walk ended the whole check with a RecursionError at about a thousand
    # levels - which turned one odd project into "no project was read".
    # Bounded on depth and on node count, because a document that is deep in
    # one direction is a document this tool did not write.
    def walk(doc, name):
        pending = [(doc, 0, 0)]
        seen = 0
        while pending:
            node, depth, group = pending.pop()
            seen += 1
            if seen > APRX_MAX_NODES or depth > APRX_MAX_DEPTH:
                out["capped"] = True
                return
            if isinstance(node, dict):
                kind = node.get("type")
                if kind == "CIMMap":
                    ref = node.get("spatialReference")
                    out["maps"].append((
                        "%s" % node.get("name", "<unnamed>"),
                        ref.get("wkid") if isinstance(ref, dict) else None))
                    placement = node.get("generalPlacementProperties")
                    if isinstance(placement, dict) \
                            and placement.get("type") \
                            and out["label_engine"] is None:
                        out["label_engine"] = "%s" % placement["type"]
                if isinstance(kind, str) and kind.startswith("CIM") \
                        and kind.endswith("Layer"):
                    layer_record(node, group)
                rate = node.get("refreshRate")
                if isinstance(rate, (int, float)) \
                        and not isinstance(rate, bool) and rate > 0:
                    out["refresh"].append(
                        "%s: %s refreshRate = %s %s"
                        % (name, node.get("name", "<unnamed>"), rate,
                           node.get("refreshRateUnit", "")))
                nest = group + 1 if kind == "CIMGroupLayer" else group
                for value in node.values():
                    pending.append((value, depth + 1, nest))
            elif isinstance(node, list):
                for value in node:
                    pending.append((value, depth + 1, group))

    for name, text in texts:
        try:
            doc = json.loads(text)
        except ValueError:
            continue
        except RecursionError:
            # The parser's own depth limit, hit before the walk. That is a
            # document this tool declines, not an empty one.
            out["capped"] = True
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
    title = "Layer refresh timers and default geodatabase (read from the .aprx)"
    if pro_state != "closed":
        return dict(
            cid="PF-22", category="ArcGISPro", status="Info", measured=False,
            title=title,
            observed="No project file was opened: %s, and reading one holds "
                     "it open while Pro may be saving it. The project file "
                     "was not measured this run."
                     % ("ArcGIS Pro is running" if pro_state == "running"
                        else "whether ArcGIS Pro is running was not "
                             "determined"),
            expected="Pro closed, so the project can be read without "
                     "contending with a save.",
            impact="S2 undetermined.",
            fix="Have the editor save and exit Pro, then re-run. Until then "
                "check each layer's refresh interval by hand.")
    if not projects:
        return dict(
            cid="PF-22", category="ArcGISPro", status="Info", measured=False,
            title=title,
            observed="No recent project was found, so no project file was "
                     "read.",
            expected="A recent project to read.", impact="S2 undetermined.",
            fix="Check each layer's refresh interval by hand.")
    lines = []
    hits = []
    remote_gdb = []
    incomplete = []
    for p in projects:
        if not p.get("parsed"):
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
            if p.get("error"):
                lines.append("   partly read: %s" % p["error"])
            else:
                lines.append("   scan capped, so this project was not read in "
                             "full")
    if omitted:
        lines.append("%d older recent project(s) were not read." % omitted)
    if hits or remote_gdb:
        status = "Warn"
    elif incomplete:
        status = "Info"
    else:
        status = "Pass"
    return dict(
        cid="PF-22", category="ArcGISPro", status=status, title=title,
        observed=lines,
        expected="No layer on a refresh timer against a slow source, and the "
                 "default geodatabase on a local disk.",
        impact="S2. A layer with a refresh interval re-queries on a timer "
               "with nobody touching the map, which is a stall the editor "
               "reads as the project refreshing. A default geodatabase on a "
               "share pays the share on every geoprocessing write.",
        fix="Clear the refresh interval in the properties of each layer "
            "listed, and point the default geodatabase at a local "
            "folder. This check never writes the project file; the separate "
            "selected-project recovery mode only creates a new copy.",
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

    def reg_values(self, path, limit=256):
        """(value-name mapping, error) for one key, with an explicit cap.

        Add-In Folders stores each folder as a VALUE NAME, not as a subkey or
        as data under a known value. An absent key is an empty mapping; an
        unreadable key is None, so the inventory cannot confuse the two.
        """
        if not self.windows:
            return None, "the registry is not available on this platform"
        try:
            import winreg
            key = self._open_key(path)
            try:
                count = winreg.QueryInfoKey(key)[1]
                out = {}
                for index in range(min(count, limit)):
                    name, value, _kind = winreg.EnumValue(key, index)
                    out[name] = value
                if count > limit:
                    return out, "value cap %d reached; %d values were not read" % (
                        limit, count - limit)
                return out, None
            finally:
                key.Close()
        except FileNotFoundError:
            return {}, None
        except Exception as exc:
            return None, "%s" % exc

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

    def read_text(self, path, limit=MAX_TEXT_BYTES):
        """(contents, None) or (None, reason). Never called on a connection
        file.

        Bounded, and the two failures are told apart. A file this tool did not
        write, in a folder a sync client may have replaced, is not a file whose
        size it knows. A caller that cannot distinguish "absent" from "too big
        to read" reports a missing setting on a machine that has one.
        """
        try:
            handle = open(path, "rb")
        except (OSError, IOError) as exc:
            return None, "%s" % exc
        try:
            raw = handle.read(limit + 1)
        except (OSError, IOError) as exc:
            return None, "%s" % exc
        finally:
            handle.close()
        if len(raw) > limit:
            return None, ("larger than the %s this tool will read into memory"
                          % format_bytes(limit))
        return raw.decode("utf-8", "replace"), None

    def rename(self, src, dst):
        os.rename(src, dst)

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
                [self.system32("tasklist"), "/FI", "IMAGENAME eq %s" % image, "/NH"],
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
            out = subprocess.check_output([self.system32("powercfg"),
                                           "/getactivescheme"],
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

    def resolve(self, host, port, timeout_ms):
        """(addresses, None) or (None, reason), inside a bounded wait.

        A name lookup has no timeout argument of its own, and a resolver that
        is down or a DNS server that drops the query can hold one for the
        system's full retry budget. The whole point of --timeout is that the
        operator is waiting, so the lookup runs in a thread this method stops
        waiting for. The thread is a daemon: nothing here can cancel a
        getaddrinfo already in flight, and holding the run for it would undo
        the bound.

        Unanswered is its own answer. It is not "the name did not resolve" -
        that would say the host does not exist when all that happened is that
        nobody replied - and it is not a closed port either.
        """
        import threading
        box = {}

        def lookup():
            try:
                box["infos"] = socket.getaddrinfo(host, port, 0,
                                                  socket.SOCK_STREAM)
            except (socket.gaierror, UnicodeError, OSError) as exc:
                box["error"] = exc

        worker = threading.Thread(target=lookup)
        worker.daemon = True
        worker.start()
        worker.join(timeout_ms / 1000.0)
        if worker.is_alive():
            return None, ("the name lookup did not answer within %d ms"
                          % timeout_ms)
        if "error" in box:
            return None, "the name did not resolve: %s" % box["error"]
        return box.get("infos"), None

    def tcp(self, host, port, timeout_ms):
        """One bounded TCP connect. Returns (open, elapsed_ms, error).

        open is None, not False, when the name did not resolve: a name that
        never resolved says nothing about the server, and a Fail on it blamed
        a host nobody reached. Every address the name resolves to is tried,
        IPv6 included, inside one shared deadline, and the name lookup is
        bounded by the same allowance.

        Bounded on purpose. The obvious connect blocks for tens of seconds
        against a dead host, which would dominate the runtime of a diagnostic
        whose whole job is to be run while somebody waits.
        """
        started = time.time()
        deadline = started + timeout_ms / 1000.0

        def took():
            return int((time.time() - started) * 1000)

        infos, error = self.resolve(host, port, timeout_ms)
        if error:
            return None, took(), error
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

    def system32(self, name):
        """The absolute path of a program that ships with Windows, or the name.

        Windows searches the current directory for a program named without a
        path, ahead of PATH. Every child this tool starts is one that ships in
        System32, so naming it by its full path removes the case where the
        folder this happens to be run from supplies a different file with the
        same name. The fallback keeps the tool working on a machine whose
        SystemRoot is somewhere else, at the cost of that case.
        """
        root = self.getenv("SystemRoot")
        if not root:
            return name
        candidate = ntpath.join(root, "System32", name)
        return candidate if self.isfile(candidate) else name

    def python_exe(self):
        """A Python interpreter this process may safely spawn, or None.

        `sys.executable` is not always Python. Run inside ArcGIS Pro it is
        `C:\\Program Files\\ArcGIS\\Pro\\bin\\ArcGISPro.exe`, so spawning it with
        `-c` does not evaluate a snippet: it opens a second copy of the
        application, which ignores the argument, puts up a window on the
        editor's screen and never exits. A self-test that hangs for two
        minutes and then dies on TimeoutExpired is what that looks like.

        So the interpreter is resolved by name, and the answer is None when
        none can be found rather than the host application.
        """
        here = sys.executable or ""
        if os.path.basename(here).lower().startswith("python"):
            return here
        name = "python.exe" if self.windows else "python3"
        for folder in (getattr(sys, "prefix", "") or "",
                       getattr(sys, "base_prefix", "") or "",
                       os.path.dirname(here)):
            if not folder:
                continue
            candidate = os.path.join(folder, name)
            if self.isfile(candidate):
                return candidate
            if not self.windows:
                continue
            fallback = os.path.join(folder, "python.exe")
            if fallback != candidate and self.isfile(fallback):
                return fallback
        return None

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
                [self.system32(self.powershell), "-NoProfile",
                 "-NonInteractive", "-Command",
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

    def disk_media(self):
        """[(friendly name, media type)] for each physical disk, or (None,
        reason).

        MediaType answers SSD, HDD or Unspecified. Unspecified is common on a
        disk behind a RAID or storage-space controller, and it is reported as
        its own answer rather than read as either.
        """
        data, error = self._powershell_json(
            "$d = @(Get-PhysicalDisk | ForEach-Object { [pscustomobject]@{"
            "name = $_.FriendlyName; media = [string]$_.MediaType} }); "
            "ConvertTo-Json -Compress -InputObject $d")
        return _ps_rows(data, "name", "media"), error

    def link_speed(self):
        """[(adapter name, link speed)] for each adapter that is up, or (None,
        reason).

        The negotiated speed, not the rated one. An adapter rated for gigabit
        and linked at a hundred megabit is a cable or a switch port, and it
        presents as a slow server.
        """
        data, error = self._powershell_json(
            "$a = @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | "
            "ForEach-Object { [pscustomobject]@{name = $_.Name; speed = "
            "[string]$_.LinkSpeed} }); ConvertTo-Json -Compress -InputObject $a")
        return _ps_rows(data, "name", "speed"), error

    def cpu(self):
        """([processor rows], None) or (None, reason).

        One row per socket. ArcGIS Pro draws largely on one core, so the clock
        of a single core matters more than the count of them; both are read
        because a low count starves parallel geoprocessing instead.
        """
        if not self.windows:
            return None, "the processor is read on Windows only"
        data, error = self._powershell_json(
            "$p = @(Get-CimInstance Win32_Processor | ForEach-Object { "
            "[pscustomobject]@{name = $_.Name; cores = $_.NumberOfCores; "
            "logical = $_.NumberOfLogicalProcessors; mhz = "
            "$_.MaxClockSpeed} }); ConvertTo-Json -Compress -InputObject $p")
        if error:
            return None, error
        if not isinstance(data, list):
            data = [data]
        return [row for row in data if isinstance(row, dict)], None

    def gpu(self):
        """([video controller rows], None) or (None, reason).

        The driver date is the finding, not the adapter name: the first thing
        Esri's own troubleshooting guide asks for is a driver from the GPU
        maker rather than the one Windows Update offered.
        """
        if not self.windows:
            return None, "the graphics adapter is read on Windows only"
        data, error = self._powershell_json(
            "$g = @(Get-CimInstance Win32_VideoController | ForEach-Object { "
            "[pscustomobject]@{name = $_.Name; driver = $_.DriverVersion; "
            "date = $(if ($_.DriverDate) { $_.DriverDate.ToString('o') } "
            "else { $null })} }); ConvertTo-Json -Compress -InputObject $g")
        if error:
            return None, error
        if not isinstance(data, list):
            data = [data]
        return [row for row in data if isinstance(row, dict)], None

    def pagefile(self):
        """([entries], None) or (None, reason) from the boot-time setting.

        An empty list means system managed, which is an answer and not a
        failed read: Windows decides the size at boot and does not write the
        number here.
        """
        if not self.windows:
            return None, "the pagefile setting is read on Windows only"
        value = self.reg(
            r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager"
            r"\Memory Management", "PagingFiles")
        if value is None:
            return None, ("no PagingFiles value could be read, which is not "
                          "the same as the pagefile being off")
        if isinstance(value, (list, tuple)):
            return ["%s" % v for v in value], None
        return ["%s" % value], None

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
                [self.system32(self.wevtutil), "qe", log, "/q:" + query,
                 "/c:%d" % count,
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

    def startup_package(self, path, full_crc=False):
        """Read one startup package's directory and configuration, never code.

        The optional whole-archive CRC is capped by expanded bytes. The DAML or
        XML read has its own decompressed-byte cap. Every failure is data for
        PF-31, not an exception that can abort the rest of the diagnostic.
        """
        import zipfile
        result = {"members": [], "config": None, "config_error": None,
                  "crc": "not run (package budget)", "error": None}
        archive = None
        try:
            if os.path.getsize(path) > ADDIN_MAX_PACKAGE_BYTES:
                result["error"] = ("package exceeds the %d-byte inspection cap"
                                   % ADDIN_MAX_PACKAGE_BYTES)
                return result
            archive = zipfile.ZipFile(path)
            infos = archive.infolist()
            if len(infos) > ADDIN_MAX_PACKAGE_ENTRIES:
                result["error"] = ("package has more than %d entries"
                                   % ADDIN_MAX_PACKAGE_ENTRIES)
                return result
            result["members"] = [info.filename for info in infos]
            if full_crc:
                expanded = sum(max(0, info.file_size) for info in infos)
                if expanded > ADDIN_CRC_MAX_EXPANDED_BYTES:
                    result["crc"] = ("not run (expanded size %d exceeds cap %d)"
                                     % (expanded, ADDIN_CRC_MAX_EXPANDED_BYTES))
                else:
                    bad = archive.testzip()
                    result["crc"] = ("failed at %s" % bad if bad else
                                     "passed for every archive member")
            expected = ("config.xml" if path.lower().endswith(".esriplugin")
                        else "config.daml")
            candidates = [info for info in infos
                          if info.filename.replace("\\", "/").lower() == expected]
            if len(candidates) > 1:
                result["config_error"] = ("multiple root %s entries make the "
                                          "package ambiguous" % expected)
                return result
            if not candidates:
                return result
            info = candidates[0]
            if info.file_size > ADDIN_CONFIG_MAX_BYTES:
                result["config_error"] = ("%s exceeds the %d-byte XML cap"
                                          % (info.filename, ADDIN_CONFIG_MAX_BYTES))
                return result
            handle = archive.open(info, "r")
            try:
                raw = handle.read(ADDIN_CONFIG_MAX_BYTES + 1)
            finally:
                handle.close()
            if len(raw) > ADDIN_CONFIG_MAX_BYTES:
                result["config_error"] = ("%s decompressed beyond the XML cap"
                                          % info.filename)
                return result
            result["config"] = raw.decode("utf-8-sig", "replace")
            return result
        except Exception as exc:
            result["error"] = "%s: %s" % (type(exc).__name__, exc)
            result["crc"] = "failed or incomplete"
            return result
        finally:
            if archive is not None:
                archive.close()

    def read_zip_json(self, path, max_entries=APRX_MAX_ENTRIES,
                      max_entry=APRX_MAX_ENTRY_BYTES, max_total=APRX_MAX_BYTES):
        """([(name, text)], truncated, None) or ([], False, reason).

        Only .json entries are read, each and all of them bounded, and the
        bound is enforced on the bytes actually decompressed rather than on the
        size the archive header claims. A zip header is a number the file's
        author chose, and Python's reader stops at it, so trusting it both
        understates the cap for a lying header and turns a corrupt entry into
        an exception that escapes this function.

        One entry that fails is reported and the walk goes on. A project whose
        third document is corrupt still answers for its first two, which is
        more than a check that reports nothing about any of them.
        """
        import zipfile
        out = []
        unreadable = []
        truncated = False
        total = 0
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, IOError, zipfile.BadZipfile, ValueError) as exc:
            return [], False, "%s" % exc
        try:
            for info in archive.infolist():
                if not info.filename.lower().endswith(".json"):
                    continue
                if len(out) >= max_entries or total >= max_total:
                    truncated = True
                    continue
                budget = min(max_entry, max_total - total)
                chunks = []
                got = 0
                try:
                    handle = archive.open(info, "r")
                    try:
                        # Read to the cap or to the end of the entry. Going
                        # all the way to the end is what makes the archive's
                        # own CRC check run: a header that understates the
                        # entry hands back its own short read and then fails
                        # the checksum, which is the difference between a
                        # truncated document that looks fine and one that is
                        # reported.
                        while got <= budget:
                            block = handle.read(min(65536, budget + 1 - got))
                            if not block:
                                break
                            chunks.append(block)
                            got += len(block)
                    finally:
                        handle.close()
                except (OSError, IOError, zipfile.BadZipfile, ValueError,
                        NotImplementedError) as exc:
                    unreadable.append("%s: %s" % (info.filename, exc))
                    continue
                if got > budget:
                    truncated = True
                    continue
                total += got
                out.append((info.filename,
                            b"".join(chunks).decode("utf-8", "replace")))
        finally:
            archive.close()
        if unreadable:
            return out, truncated, "; ".join(unreadable)
        return out, truncated, None


def _xml_local(tag):
    """An XML tag without its namespace."""
    return (tag or "").rsplit("}", 1)[-1]


def _version_pair(text):
    """(major, minor) from an Esri version, ignoring its build components."""
    found = re.match(r"^\s*(\d+)(?:\.(\d+))?", text or "")
    return (int(found.group(1)), int(found.group(2) or 0)) if found else None


def parse_startup_package(path, kind, payload):
    """Turn one raw package read into metadata and evidence-based warnings.

    No DLL is loaded and no file is extracted. Compatibility with the installed
    Pro release is decided later, when that release is available to the report.
    """
    record = {"path": path, "kind": kind, "name": ntpath.basename(path),
              "source": "", "modified": "", "size": 0,
              "id": "", "version": "", "desktop_version": "",
              "default_assembly": "", "company": "", "assemblies": [],
              "startup_modules": [], "warnings": [], "info": [],
              "crc": payload.get("crc") or "not measured"}
    if payload.get("error"):
        record["warnings"].append("Package could not be inspected: %s"
                                  % payload["error"])
        return record
    if record["crc"].lower().startswith("failed"):
        record["warnings"].append("Archive CRC verification failed: %s"
                                  % record["crc"])
    members = [p.replace("\\", "/") for p in payload.get("members") or []]
    assemblies = sorted(set(ntpath.basename(p) for p in members
                            if p.lower().endswith(".dll")), key=str.lower)
    record["assemblies"] = assemblies
    esri_copies = [name for name in assemblies
                   if name.lower().startswith("arcgis.desktop.")
                   or name.lower() in ("arcgis.core.dll", "arcgis.corehost.dll")]
    if esri_copies:
        record["warnings"].append(
            "Bundled Esri API assemblies can shadow the installed Pro runtime "
            "and have documented startup-crash potential: %s"
            % ", ".join(esri_copies))
    config = payload.get("config")
    expected_config = "Config.xml" if kind == "plug-in data source" else "Config.daml"
    if payload.get("config_error"):
        record["warnings"].append(payload["config_error"])
        return record
    if not config:
        record["warnings"].append("%s is absent or unreadable; package "
                                  "compatibility cannot be established"
                                  % expected_config)
        return record
    if re.search(r"<!\s*DOCTYPE", config, re.IGNORECASE):
        record["warnings"].append("%s contains a DOCTYPE and was refused"
                                  % expected_config)
        return record
    from xml.etree import ElementTree
    try:
        root = ElementTree.fromstring(config)
    except (ElementTree.ParseError, ValueError) as exc:
        record["warnings"].append("%s is not valid XML: %s"
                                  % (expected_config, exc))
        return record
    default_assembly = root.get("defaultAssembly") or ""
    record["default_assembly"] = default_assembly
    if default_assembly and ntpath.basename(default_assembly).lower() not in [
            name.lower() for name in assemblies]:
        record["warnings"].append(
            "defaultAssembly=%s is declared but no assembly with that name "
            "exists in the package" % default_assembly)
    info = next((node for node in root.iter()
                 if _xml_local(node.tag) == "AddInInfo"), None)
    if info is not None:
        record["id"] = info.get("id") or ""
        record["version"] = info.get("version") or ""
        record["desktop_version"] = info.get("desktopVersion") or ""
        for node in info:
            if _xml_local(node.tag) == "Name" and (node.text or "").strip():
                record["name"] = node.text.strip()
            elif _xml_local(node.tag) == "Company":
                record["company"] = (node.text or "").strip()
    elif kind != "plug-in data source":
        record["warnings"].append("AddInInfo is missing; id, version and "
                                  "desktopVersion cannot be read")
    startup = []
    for node in root.iter():
        if (_xml_local(node.tag) == "insertModule"
                and (node.get("autoLoad") or "").strip().lower() == "true"):
            startup.append(node.get("className") or node.get("id") or
                           "<unnamed module>")
    record["startup_modules"] = sorted(set(startup))
    if kind == "managed configuration":
        record["info"].append("A managed configuration can customise the "
                              "splash screen and startup experience")
    return record


def decide_addins(records, coverage, pro_version, crash_modules=None,
                  platform_readable=True):
    """PF-31. Startup customisations, without calling presence a crash cause."""
    lines = ["Offline inventory: no add-in code was loaded or extracted; "
             "digital signatures were not verified."]
    lines.extend("COVERAGE: %s" % reason for reason in coverage)
    if not platform_readable:
        return dict(cid="PF-31", category="ArcGISPro", status="Info",
                    measured=False, title="Startup add-ins and configurations",
                    observed="Add-in folders and packages are inspected on "
                             "Windows only.",
                    expected="A readable Windows add-in inventory.",
                    impact="S6 and S1 undetermined.",
                    fix="Run the BAT launcher or CLI on the affected Windows "
                        "workstation, in the user's own session.")
    crash_names = set(ntpath.basename(name or "").lower()
                      for name in (crash_modules or []) if name)
    id_counts = {}
    for record in records:
        if record.get("id"):
            key = record["id"].strip().lower()
            id_counts[key] = id_counts.get(key, 0) + 1
    issue_count = 0
    crash_matches = 0
    installed = _version_pair(pro_version)
    for record in records:
        warnings = list(record.get("warnings") or [])
        info = list(record.get("info") or [])
        target = _version_pair(record.get("desktop_version"))
        if record.get("kind") != "plug-in data source":
            if not record.get("desktop_version"):
                if not any("AddInInfo" in w or "Config.daml" in w
                           or "inspected" in w for w in warnings):
                    warnings.append("desktopVersion is absent or unreadable")
            elif not target or not installed:
                info.append("Compatibility not decided: target=%s; installed "
                            "Pro=%s" % (record.get("desktop_version"),
                                       pro_version or "<unreadable>"))
            elif target[0] != installed[0]:
                warnings.append(
                    "desktopVersion=%s targets a different major release "
                    "than installed Pro %s" %
                    (record.get("desktop_version"), pro_version))
            elif target[1] > installed[1]:
                warnings.append(
                    "desktopVersion=%s requires a newer minor release than "
                    "installed Pro %s" %
                    (record.get("desktop_version"), pro_version))
            else:
                info.append("Target major/minor is compatible with Pro %s; "
                            "this is not a stability guarantee" % pro_version)
        matches = sorted(set(name.lower() for name in record.get("assemblies")
                             or []) & crash_names)
        if matches:
            crash_matches += len(matches)
            warnings.append("Application Error 1000 names fault module(s) "
                            "packaged here: %s" % ", ".join(matches))
        if record.get("id") and id_counts.get(record["id"].lower(), 0) > 1:
            info.append("The same add-in id occurs in multiple locations; "
                        "Esri documents that the highest add-in version loads")
        issue_count += len(warnings)
        lines.append("%s: %s | %s" % (record.get("kind"),
                                      record.get("name"), record.get("path")))
        lines.append("  source=%s; modified=%s; bytes=%s"
                     % (record.get("source") or "<not recorded>",
                        record.get("modified") or "<not recorded>",
                        record.get("size") or 0))
        lines.append("  id=%s; version=%s; desktopVersion=%s; company=%s"
                     % (record.get("id") or "<unreadable>",
                        record.get("version") or "<unreadable>",
                        record.get("desktop_version") or "<unreadable>",
                        record.get("company") or "<unreadable>"))
        lines.append("  defaultAssembly=%s; CRC=%s; DLLs=%s"
                     % (record.get("default_assembly") or "<not declared>",
                        record.get("crc") or "<not measured>",
                        ", ".join((record.get("assemblies") or [])[:16])
                        or "<none>"))
        if len(record.get("assemblies") or []) > 16:
            lines.append("  ... %d more DLL(s) not printed"
                         % (len(record["assemblies"]) - 16))
        if record.get("startup_modules"):
            lines.append("  autoLoad modules: %s"
                         % ", ".join(record["startup_modules"]))
        lines.extend("  NOTE: %s" % note for note in info)
        lines.extend("  RISK: %s" % warning for warning in warnings)
    if not records:
        if coverage:
            return dict(cid="PF-31", category="ArcGISPro", status="Info",
                        measured=False, title="Startup add-in inventory is incomplete",
                        observed=lines,
                        expected="Readable default and well-known add-in folders.",
                        impact="S6 and S1 undetermined.",
                        fix="Read Settings > Add-In Manager and the folder "
                            "paths named in this check; do not treat an "
                            "incomplete inventory as no add-ins.")
        return dict(cid="PF-31", category="ArcGISPro", status="Pass",
                    title="No startup add-in, configuration or plug-in found",
                    observed=lines + ["No .esriAddinX, .proConfigX or "
                                      ".esriPlugin package was found in the "
                                      "readable default/well-known folders."],
                    expected="No third-party startup package when /disableaddins "
                             "changes the startup result.",
                    impact="S6: no startup package was found in this scope.",
                    fix="If /disableaddins still changes the result, inspect "
                        "Add-In Manager and any managed-configuration launch "
                        "shortcut; this inventory does not prove another cause.")
    return dict(
        cid="PF-31", category="ArcGISPro",
        status="Warn" if issue_count else "Info",
        title=("%d startup package(s), %d compatibility/structure indicator(s)"
               % (len(records), issue_count) if issue_count else
               "%d startup package(s); no measured structural anomaly"
               % len(records)),
        observed=lines,
        expected="Packages readable, a compatible desktopVersion, declared "
                 "assemblies present, and no bundled Pro API assembly that "
                 "can shadow the installed runtime.",
        impact="S6 and S1. Startup custom code can crash Pro before a project "
               "opens or slow its initialization. Presence alone is not proof.",
        fix="Since /disableaddins changes the result, close Pro and isolate "
            "ONE exact listed package at a time with the BAT/CLI "
            "--quarantine-addin action. It verifies a copy outside every "
            "loading folder and writes a quarantine.json; --restore-addin "
            "restores it without overwriting. Relaunch normally after each "
            "move and restore uninvolved packages. Never erase the backup. "
            "Correlate any DL-13 fault module; machine-wide packages require "
            "administrator approval and a separate confirmation flag.",
        detail=("Coverage limits: %d entries/location, %d packages, full CRC "
                "on at most %d packages up to %d expanded bytes each. %s"
                % (ADDIN_SCAN_LIMIT, ADDIN_MAX_PACKAGES, ADDIN_FULL_CRC_MAX,
                   ADDIN_CRC_MAX_EXPANDED_BYTES,
                   "Inventory incomplete; see COVERAGE lines." if coverage else
                   "All discovered locations were scanned within those limits.")),
        kpi={"packages": len(records), "issues": issue_count,
             "crash_module_matches": crash_matches})


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
    """(parsed user.config, None) or (empty mapping, reason).

    A file nobody could read is not a file whose settings are all absent, and
    the two must not reach the same branch: `<absent>` is a real answer, and a
    verdict built on it describes a setting that may be set.
    """
    if not path:
        return {}, None
    text, error = env.read_text(path)
    if error:
        return {}, error
    return parse_user_config(text), None


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
    settings, error = read_settings(env, path)
    facts["settings"] = settings
    facts["settings_error"] = error
    recent = facts.get("recent") or []
    home = recent[0][2] if recent else "unknown"
    return decide_backup(path, settings, home, error)


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
        "DL-10", "Network",
        "TCP reachability and latency to the enterprise geodatabase server",
        host, port, probe, SDE_LATENCY_WARN_MS,
        impact="S1, S2 and S3. A slow or intermittently unreachable database "
               "host is what turns a save into a timeout, and a timed-out save "
               "is what strands a version state.",
        fix="If it is slow or closed, confirm the network state and the name "
            "resolution for this host, and re-test from a wired connection "
            "before blaming the client.",
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
    version = env.reg(PRO_REG_KEY, "Version")
    real = env.reg(PRO_REG_KEY, "RealVersion")
    facts["pro_release"] = real or version
    exe_version = None
    if env.isfile(exe):
        exe_version = "present on disk"
    return decide_pro_version(version, real, env.reg(PRO_REG_KEY, "BuildNumber"),
                              install, exe_version)


def discover_startup_packages(env):
    """(records, coverage, locations) for diagnostics and explicit quarantine."""
    coverage = []
    locations = []
    docs = documents_path(env)
    if docs:
        locations.append(("default user", ntpath.join(
            docs, "ArcGIS", "AddIns"), False))
    else:
        coverage.append("Documents shell folder is unreadable, so the default "
                        "ArcGIS/AddIns tree was not scanned")
    for scope, key in ADDIN_FOLDER_KEYS:
        values, error = env.reg_values(key)
        if values is None:
            coverage.append("%s is unreadable: %s" % (key, error))
            continue
        if error:
            coverage.append("%s: %s" % (key, error))
        for folder in values:
            if isinstance(folder, str) and folder.strip():
                locations.append((scope, expand_vars(env, folder.strip()), True))
    unique_locations = []
    seen = set()
    for scope, path, registered in locations:
        key = _norm(path)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_locations.append((scope, path, registered))
    packages = {}
    for scope, folder, registered in unique_locations:
        if not env.isdir(folder):
            if registered:
                coverage.append("registered folder is absent or unreadable: "
                                "%s (%s)" % (folder, scope))
            continue
        entries, unreadable = env.scan(folder, depth=3,
                                       limit=ADDIN_SCAN_LIMIT)
        coverage.extend("%s (%s)" % (line, scope) for line in unreadable)
        if len(entries) >= ADDIN_SCAN_LIMIT:
            coverage.append("scan cap %d reached at %s; inventory may be "
                            "incomplete" % (ADDIN_SCAN_LIMIT, folder))
        for entry in entries:
            if entry.isdir:
                continue
            kind = ADDIN_PACKAGE_EXTENSIONS.get(
                ntpath.splitext(entry.name)[1].lower())
            if kind:
                packages[_norm(entry.path)] = (entry, kind, scope, folder)
    ordered = sorted(packages.values(),
                     key=lambda row: (row[0].mtime, row[0].path), reverse=True)
    if len(ordered) > ADDIN_MAX_PACKAGES:
        coverage.append("package cap %d reached; %d package(s) were not "
                        "inspected" % (ADDIN_MAX_PACKAGES,
                                       len(ordered) - ADDIN_MAX_PACKAGES))
    records = []
    for index, (entry, kind, scope, folder) in enumerate(
            ordered[:ADDIN_MAX_PACKAGES]):
        payload = env.startup_package(entry.path,
                                      full_crc=index < ADDIN_FULL_CRC_MAX)
        record = parse_startup_package(entry.path, kind, payload)
        record["source"] = scope
        record["source_root"] = folder
        record["modified"] = stamp(entry.mtime)
        record["size"] = entry.size
        records.append(record)
    return records, coverage, unique_locations


def probe_addins(env, facts):
    """Inventory every discovered startup package within explicit limits."""
    if not env.registry_readable():
        return decide_addins([], [], None, platform_readable=False)
    records, coverage, _locations = discover_startup_packages(env)
    facts["addin_inventory"] = records
    return decide_addins(records, coverage, facts.get("pro_release"),
                         facts.get("crash_modules"))


def probe_rendering(env, facts):
    return decide_rendering(facts.get("user_config"), facts.get("settings"),
                            facts.get("settings_error"))


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
                        status_stamp, facts.get("settings"),
                        facts.get("settings_error"))


def probe_gpu_preference(env, facts):
    exe = facts.get("pro_exe") or pro_exe_path(env)
    facts["pro_exe"] = exe
    value = env.reg(GPU_PREFERENCE_KEY, exe)
    return decide_gpu_preference(exe, value, env.registry_readable())


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
        "PF-19", "Network", "TCP reachability and latency to the portal",
        host, 443, probe, PORTAL_LATENCY_WARN_MS,
        impact="S1. A slow portal lengthens sign-in and every layer the "
               "project resolves through it at open.",
        fix="If it is slow or closed, confirm the network state and the name "
            "resolution for this host before changing any client setting.")


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
    fault_modules = []
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
            if (log == "Application" and event["provider"] == "Application Error"
                    and event["id"] == 1000 and what
                    and len(event["data"]) > 3 and event["data"][3]):
                fault_modules.append(ntpath.basename(event["data"][3]))
    facts["crash_modules"] = sorted(set(name.lower() for name in fault_modules))
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
    """PF-22. Shares the one project-file read with PF-01, PF-09, PF-10 and
    PF-14."""
    projects, state, omitted = aprx_projects(env, facts)
    return decide_aprx(state, projects, omitted)


def aprx_projects(env, facts):
    """The parsed recent projects, read once and shared by every check.

    Cached in facts on purpose. Five checks read the project file, and each
    read holds it open; doing it five times would be five chances to contend
    with a save, which is the failure this tool refuses to cause. Returns
    (projects, state, omitted) where state is "closed", "running" or
    "unknown".
    """
    if "aprx" in facts:
        return facts["aprx"]
    running = facts.get("pro_running")
    state = ("unknown" if running is None
             else "running" if running else "closed")
    projects = []
    omitted = 0
    if state == "closed":
        onedrive = onedrive_roots(env)
        network = env.network_drives()
        recent = facts.get("recent") or []
        for path, _when, _cls in recent[:APRX_MAX_PROJECTS]:
            texts, truncated, error = env.read_zip_json(path)
            if error and not texts:
                projects.append({"path": path, "error": error})
                continue
            found = scan_aprx(path, texts, onedrive, network)
            # A document the walk declined is a partial reading too: the
            # timer could have been in the part that was never reached.
            found["truncated"] = (truncated or bool(error)
                                  or found.get("capped"))
            if error:
                found["error"] = error
            projects.append(found)
        omitted = max(len(recent) - APRX_MAX_PROJECTS, 0)
    result = (projects, state, omitted)
    facts["aprx"] = result
    return result


def probe_projection(env, facts):
    projects, state, _omitted = aprx_projects(env, facts)
    if state != "closed":
        return dict(
            cid="PF-01", category="ArcGISPro", status="Info", measured=False,
            title="Coordinate systems in the project (on-the-fly projection)",
            observed="No project file was opened: %s. Nothing was compared."
                     % ("ArcGIS Pro is running" if state == "running"
                        else "whether ArcGIS Pro is running was not "
                             "determined"),
            expected="Pro closed, so the project can be read.",
            impact="S1 and S2 undetermined.",
            fix="Read it in Pro: the map's Properties > Coordinate Systems.")
    return decide_projection(projects)


def probe_http_layers(env, facts):
    projects, state, _omitted = aprx_projects(env, facts)
    if state != "closed":
        return dict(
            cid="PF-09", category="Network", status="Info", measured=False,
            title="Layers served over the network by URL",
            observed="No project file was opened: %s."
                     % ("ArcGIS Pro is running" if state == "running"
                        else "whether ArcGIS Pro is running was not "
                             "determined"),
            expected="Pro closed, so the project can be read.",
            impact="S1 and S2 undetermined.",
            fix="Read the layer list in Pro's Contents pane.")
    return decide_http_layers(projects)


def probe_project_weight(env, facts):
    projects, state, _omitted = aprx_projects(env, facts)
    sizes = []
    for path, _when, _cls in (facts.get("recent") or [])[:APRX_MAX_PROJECTS]:
        try:
            sizes.append((path, os.stat(path).st_size))
        except OSError:
            pass
    if state != "closed":
        return decide_project_weight([], sizes)
    return decide_project_weight(projects, sizes)


def probe_layer_load(env, facts):
    projects, state, _omitted = aprx_projects(env, facts)
    if state != "closed":
        return dict(
            cid="PF-14", category="ArcGISPro", status="Info", measured=False,
            title="Per-layer draw load: joins, queries, labels, visibility",
            observed="No project file was opened: %s."
                     % ("ArcGIS Pro is running" if state == "running"
                        else "whether ArcGIS Pro is running was not "
                             "determined"),
            expected="Pro closed, so the project can be read.",
            impact="S1, S2 and S4 undetermined.",
            fix="Read it per layer in Pro.")
    return decide_layer_load(projects)


def probe_overviews(env, facts):
    """PF-16. Rasters under the recent project folders, and their sidecars.

    The same depth and entry bound as every other walk here: a full recursive
    scan of a synced project tree can hydrate every file it touches, which is
    the condition being measured.
    """
    roots = []
    seen = set()
    for path, _when, _cls in (facts.get("recent") or [])[:APRX_MAX_PROJECTS]:
        folder = ntpath.dirname(path)
        key = _norm(folder)
        if folder and key not in seen:
            seen.add(key)
            roots.append(folder)
    if not roots:
        return decide_overviews([], set(), [])
    rasters = []
    with_overview = set()
    unreadable = []
    for root in roots:
        entries, bad = env.scan(root, depth=3, limit=5000)
        unreadable.extend(bad)
        names = set([_norm(e.name) for e in entries])
        for e in entries:
            if e.isdir:
                continue
            low = e.name.lower()
            if low.endswith(RASTER_EXTENSIONS):
                rasters.append(e.path)
                stem = e.path[:-len(ntpath.splitext(low)[1])] if "." in low \
                    else e.path
                for sidecar in (".ovr", ".aux.xml"):
                    if _norm(ntpath.basename(stem + sidecar)) in names:
                        with_overview.add(e.path)
                        break
    return decide_overviews(rasters, with_overview, unreadable)


def _int_or_none(value):
    """An int out of one CIM answer, or None. A bool is not a count."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _cpu_row(row):
    """One processor row coerced, or None when it carries nothing usable."""
    name = row.get("name")
    cores = _int_or_none(row.get("cores"))
    logical = _int_or_none(row.get("logical"))
    mhz = _int_or_none(row.get("mhz"))
    if name is None and cores is None and logical is None and mhz is None:
        return None
    return {"name": "%s" % (name or "<unnamed processor>"),
            "cores": cores or 0, "logical": logical or 0, "mhz": mhz or 0}


def _driver_age_days(text, now):
    """Days since a driver date, or None.

    A date that cannot be parsed answers None, never zero: a driver with an
    unreadable date is not a driver installed today, and reading it as fresh
    would pass the one check Esri's guide leads with.
    """
    if not text:
        return None
    try:
        stamp = calendar.timegm(time.strptime("%s" % text[:10], "%Y-%m-%d"))
    except (ValueError, OverflowError, TypeError):
        return None
    if stamp > now:
        return None
    return int((now - stamp) / 86400)


def _gpu_row(row, now):
    """One video controller coerced, or None when it carries nothing usable."""
    name = row.get("name")
    driver = row.get("driver")
    date = row.get("date")
    if name is None and driver is None and date is None:
        return None
    return {"name": "%s" % (name or "<unnamed adapter>"),
            "driver": "%s" % (driver or "<version not reported>"),
            "date": date or None,
            "age_days": _driver_age_days(date, now)}


def decide_cpu(rows, error):
    """PF-28. What the processor is.

    The second machine finding no ArcGIS Pro setting fixes, after the disk.
    Pro draws largely on one core, so a weak single-core clock reads as an
    unexplained stall while every setting in the report is correct.
    """
    title = "Processor model, cores and clock"
    expected = ("Esri's published requirement: minimum %d cores with "
                "simultaneous multithreading, recommended %d cores, optimal "
                "%d cores."
                % (ESRI_CPU_MIN_CORES, ESRI_CPU_REC_CORES, ESRI_CPU_OPT_CORES))
    if error:
        return dict(cid="PF-28", category="Hardware", status="Info",
                    measured=False, title=title,
                    observed="The processor could not be read: %s. An unread "
                             "processor is not a processor that is fine."
                             % error,
                    expected=expected, impact="S1 and S2 undetermined.",
                    fix="Read it by hand: Get-CimInstance Win32_Processor | "
                        "Select Name, NumberOfCores, NumberOfLogicalProcessors, "
                        "MaxClockSpeed.")
    clean = [r for r in [_cpu_row(x) for x in rows] if r]
    if not clean:
        return dict(cid="PF-28", category="Hardware", status="Info",
                    measured=False, title=title,
                    observed="The processor list came back empty, so nothing "
                             "was read.",
                    expected=expected, impact="S1 and S2 undetermined.",
                    fix="Read it by hand: Get-CimInstance Win32_Processor.")
    logical = sum(r["logical"] for r in clean)
    cores = sum(r["cores"] for r in clean)
    clocks = [r["mhz"] for r in clean if r["mhz"]]
    mhz = max(clocks) if clocks else 0
    lines = ["%s: %d core(s), %d logical, %s"
             % (r["name"], r["cores"], r["logical"],
                ("%.2f GHz" % (r["mhz"] / 1000.0)) if r["mhz"]
                else "<clock not reported>") for r in clean]
    lines.append("totals: %d core(s), %d logical processor(s)"
                 % (cores, logical))
    # Graded on CORES, because that is what Esri counts. Grading on logical
    # processors would read a 2-core/4-thread chip - Esri's bare minimum - as
    # if it met the 4-core recommendation.
    if cores < ESRI_CPU_MIN_CORES:
        status = "Fail"
        lines.append("fewer cores than Esri's published minimum of %d"
                     % ESRI_CPU_MIN_CORES)
    elif cores < ESRI_CPU_REC_CORES:
        status = "Warn"
        lines.append("at Esri's published minimum of %d cores, below the "
                     "recommended %d" % (ESRI_CPU_MIN_CORES,
                                         ESRI_CPU_REC_CORES))
    else:
        status = "Pass"
    return dict(
        cid="PF-28", category="Hardware", status=status, title=title,
        observed=lines, expected=expected,
        impact="S1 and S2. ArcGIS Pro draws largely on one core, so a weak "
               "single-core clock shows up as a stall no setting explains; "
               "the core count starves parallel geoprocessing instead. Esri "
               "counts cores, not threads, so a chip reporting four logical "
               "processors may still be a two-core part.",
        fix="Hardware is not configurable, so act on what is: close other "
            "applications before a heavy session, set the power plan to High "
            "performance (PF-15), and pin ArcGISPro.exe to the "
            "high-performance GPU (PF-11). Esri's published tiers are %d "
            "cores minimum, %d recommended, %d optimal; when specifying a "
            "machine, favour single-core clock over core count, and note that "
            "some geoprocessing tools need an AVX-capable processor and the "
            "geoprocessing tool search needs AVX2."
            % (ESRI_CPU_MIN_CORES, ESRI_CPU_REC_CORES, ESRI_CPU_OPT_CORES),
        kpi={"cores": cores, "logical": logical,
             "max_ghz": round(mhz / 1000.0, 2)})


def decide_gpu(rows, error, now):
    """PF-29. The graphics adapter and how old its driver is.

    Updating the driver from the GPU maker is the first thing Esri's own
    troubleshooting guide asks for, and it is the one cause on this list that
    presents as a crash rather than as a slowdown.
    """
    title = "Graphics adapter and driver age"
    expected = ("Esri recommends a discrete - not integrated - GPU supported "
                "by its manufacturer, with %d GB or more of dedicated "
                "graphics memory, and says to use the latest available "
                "driver. The %d-day age limit is this tool's own reading of "
                "that instruction."
                % (ESRI_GPU_VRAM_REC_GB, GPU_DRIVER_MAX_DAYS))
    if error:
        return dict(cid="PF-29", category="Hardware", status="Info",
                    measured=False, title=title,
                    observed="The graphics adapter could not be read: %s. An "
                             "unread adapter is not an adapter that is fine."
                             % error,
                    expected=expected, impact="S2 and S4 undetermined.",
                    fix="Read it by hand: Get-CimInstance Win32_VideoController "
                        "| Select Name, DriverVersion, DriverDate.")
    clean = [r for r in [_gpu_row(x, now) for x in rows] if r]
    if not clean:
        return dict(cid="PF-29", category="Hardware", status="Info",
                    measured=False, title=title,
                    observed="The adapter list came back empty, so nothing was "
                             "read.",
                    expected=expected, impact="S2 and S4 undetermined.",
                    fix="Read it by hand: Get-CimInstance Win32_VideoController.")
    lines = ["%s: driver %s, dated %s"
             % (r["name"], r["driver"],
                ("%d day(s) old" % r["age_days"]) if r["age_days"] is not None
                else "<date not readable>") for r in clean]
    emulated = [r for r in clean
                if any(m in r["name"].upper() for m in GPU_EMULATED_MARKERS)]
    if emulated:
        lines.append("emulated on the CPU: %s"
                     % ", ".join(r["name"] for r in emulated))
        return dict(
            cid="PF-29", category="Hardware", status="Fail", title=title,
            observed=lines, expected=expected,
            impact="S2 and S4. Pro found no capable GPU and is emulating one "
                   "on the CPU, which is slow by construction and is the "
                   "single most common cause of a session that hangs on draw.",
            fix="Install the driver from the GPU maker's own site - NVIDIA, "
                "AMD or Intel - not the one Windows Update offers. On a "
                "laptop, take it from the laptop maker instead. Then pin "
                "ArcGISPro.exe to that GPU (PF-11). If Pro still reports "
                "WARP, the card or its driver does not support the required "
                "capabilities and switching the rendering engine to OpenGL "
                "(PF-05) is the next step.")
    ages = [r["age_days"] for r in clean if r["age_days"] is not None]
    newest = min(ages) if ages else None
    if newest is None:
        lines.append("no driver date could be read, so no age was computed")
        return dict(cid="PF-29", category="Hardware", status="Info",
                    measured=False, title=title, observed=lines,
                    expected=expected, impact="S2 and S4 undetermined.",
                    fix="Read the driver date in Device Manager > Display "
                        "adapters > Driver, and compare it with the GPU "
                        "maker's site.")
    names = " ".join(r["name"].upper() for r in clean)
    discrete = any(m in names for m in GPU_DISCRETE_MARKERS)
    integrated_only = (not discrete
                       and any(m in names for m in GPU_INTEGRATED_MARKERS))
    status = "Warn" if newest > GPU_DRIVER_MAX_DAYS else "Pass"
    if status == "Warn":
        lines.append("newest driver is %d day(s) old, past this tool's "
                     "allowance" % newest)
    if integrated_only:
        status = "Warn"
        lines.append("no discrete GPU: only an integrated adapter is present, "
                     "where Esri recommends a discrete one")
    return dict(
        cid="PF-29", category="Hardware", status=status, title=title,
        observed=lines, expected=expected,
        impact="S2 and S4. A stale driver is the first thing Esri's own "
               "troubleshooting guide asks about, and on this list it is the "
               "one that presents as a crash rather than as a slowdown. An "
               "integrated-only adapter is below Esri's published "
               "recommendation of a discrete GPU: it runs, but visualisation "
               "and 3D views pay for it, and on a notebook the integrated "
               "chip also borrows from system RAM (PF-12).",
        fix="Update the driver from the GPU maker's own site - NVIDIA, AMD or "
            "Intel - not from Windows Update, which often carries an older "
            "one; Esri's own requirement page says plainly to use the latest "
            "available driver. On a laptop, take it from the laptop maker. If "
            "crashes began right after a driver update, roll that update "
            "back: it is a known cause, and rolling back is a legitimate fix "
            "rather than a step backwards. If the only adapter is integrated, "
            "Esri recommends a discrete GPU with %d GB or more of dedicated "
            "memory; failing that, raise system RAM to compensate for the "
            "shared memory, and pin ArcGISPro.exe to the best adapter "
            "available (PF-11)." % ESRI_GPU_VRAM_REC_GB,
        kpi={"driver_age_days": newest})


def _gdb_owner(path):
    """The .gdb folder a path lives in, or None.

    Split on both separators on purpose: the engine reads Windows paths on
    whatever host it happens to run on, and os.path.split would not separate
    a backslash path on Linux.
    """
    for part in re.split(r"[\\/]", path or ""):
        if part.lower().endswith(".gdb"):
            return part
    return None


def decide_data_weight(folders):
    """PF-30. How heavy the data beside the project actually is.

    Deliberately independent of the .aprx. PF-10 weighs the project file and
    is refused whenever Pro is open, which is most of the time this tool is
    used; a size that needs the project file open would be useless from
    inside Pro. This one only stats files, so it answers either way.
    """
    title = "Data folder weight: geodatabases, rasters, file count"
    expected = ("No single file geodatabase over %s, and fewer than %d files "
                "in the folder the project lives in. Both thresholds are this "
                "tool's own allowances, not published Esri figures."
                % ("%d GiB" % (DATA_GDB_WARN_BYTES // (1024 * 1024 * 1024)),
                   DATA_FILES_WARN))
    if not folders:
        return dict(
            cid="PF-30", category="Storage", status="Info", measured=False,
            title=title,
            observed="No recent project was found, so there was no folder to "
                     "weigh.",
            expected=expected, impact="S1 and S2 undetermined.",
            fix="Point the tool at the folder by hand: open the project's "
                "folder in Explorer, right-click the .gdb, Properties. Or run "
                "the command line with Pro closed so the recent list is read.")
    lines = []
    total = 0
    files = 0
    capped = False
    unreadable = []
    worst = None
    for folder in folders:
        total += folder.get("bytes", 0)
        files += folder.get("files", 0)
        capped = capped or folder.get("capped", False)
        unreadable.extend(folder.get("unreadable") or [])
        lines.append("%s: %d file(s), %s%s"
                     % (folder.get("root", "<folder>"), folder.get("files", 0),
                        format_bytes(folder.get("bytes", 0)),
                        " (scan stopped early, so this is a floor)"
                        if folder.get("capped") else ""))
        for name, (gbytes, gfiles) in sorted(
                (folder.get("gdbs") or {}).items(),
                key=lambda kv: -kv[1][0]):
            lines.append("    %s: %s in %d file(s)"
                         % (name, format_bytes(gbytes), gfiles))
            if worst is None or gbytes > worst[1]:
                worst = (name, gbytes, folder.get("root", "<folder>"))
    if unreadable:
        lines.extend(["unreadable, so not counted: %s" % u
                      for u in unreadable[:5]])
        if len(unreadable) > 5:
            lines.append("    ... %d more unreadable item(s)"
                         % (len(unreadable) - 5))
    lines.append("totals: %d file(s), %s%s"
                 % (files, format_bytes(total),
                    " (floor, the scan stopped early)" if capped else ""))

    reasons = []
    if worst and worst[1] >= DATA_GDB_WARN_BYTES:
        reasons.append("a single file geodatabase holds %s"
                       % format_bytes(worst[1]))
    if files >= DATA_FILES_WARN:
        reasons.append("%d files in the folder" % files)

    if capped and not reasons:
        # A scan that stopped early cannot prove the folder is small. Grading
        # Pass here would be a verdict the walk did not produce.
        lines.append("the scan stopped before the end, so a small total here "
                     "proves nothing")
        return dict(cid="PF-30", category="Storage", status="Info",
                    measured=False, title=title, observed=lines,
                    expected=expected, impact="S1 and S2 undetermined.",
                    fix="Weigh the folder by hand in Explorer, or narrow the "
                        "scan by moving the biggest rasters out first.")
    if not reasons:
        return dict(
            cid="PF-30", category="Storage", status="Pass", title=title,
            observed=lines, expected=expected,
            impact="No data-weight penalty found beside the project.",
            kpi={"data_gib": round(total / float(1024 ** 3), 2),
                 "data_files": files})
    return dict(
        cid="PF-30", category="Storage", status="Warn", title=title,
        observed=lines, expected=expected,
        impact="S1 and S2. A file geodatabase this heavy is slow to open and "
               "to edit, and it is slowest of all on a network share or a "
               "spinning disk. The file count matters on its own: a synced "
               "folder has to track every file, so ten thousand small files "
               "cost more to sync than one large geodatabase of the same "
               "size.",
        fix="Split the heaviest geodatabase by theme, or export only the area "
            "you are editing into a small working geodatabase and append back "
            "when done. Move it to a local SSD if it currently sits on a "
            "network share or a spinning disk - that alone has fixed this for "
            "people whose only symptom was a slowdown. Keep it out of any "
            "synced folder: for data that must be shared, a mobile "
            "geodatabase has far fewer files to sync than a file geodatabase. "
            "Rebuild the attribute and spatial indexes after a large load.",
        kpi={"data_gib": round(total / float(1024 ** 3), 2),
             "data_files": files,
             "largest_gdb_gib": round(worst[1] / float(1024 ** 3), 2)
             if worst else 0.0})


def probe_data_weight(env, facts):
    """PF-30. Weigh the folders the recent projects live in.

    Only os.stat and a bounded walk: the .aprx is never opened, so this keeps
    answering while Pro holds the project open.
    """
    roots = []
    seen = set()
    for path, _when, _cls in (facts.get("recent") or []):
        parent = ntpath.dirname("%s" % path).rstrip("\\/")
        if parent and parent.lower() not in seen:
            seen.add(parent.lower())
            roots.append(parent)
    folders = []
    for root in roots[:APRX_MAX_PROJECTS]:
        entries, unreadable = env.scan(root, depth=DATA_SCAN_DEPTH,
                                       limit=DATA_SCAN_LIMIT)
        gdbs = {}
        total = 0
        files = 0
        for entry in entries:
            if entry.isdir:
                continue
            total += entry.size
            files += 1
            owner = _gdb_owner(entry.path)
            if owner:
                gbytes, gfiles = gdbs.get(owner, (0, 0))
                gdbs[owner] = (gbytes + entry.size, gfiles + 1)
        folders.append({"root": root, "bytes": total, "files": files,
                        "gdbs": gdbs, "unreadable": unreadable,
                        "capped": len(entries) >= DATA_SCAN_LIMIT})
    return decide_data_weight(folders)


def probe_cpu(env, facts):
    rows, error = env.cpu()
    return decide_cpu(rows or [], error)


def probe_gpu(env, facts):
    rows, error = env.gpu()
    return decide_gpu(rows or [], error, env.now())


def probe_disk_media(env, facts):
    disks, error = env.disk_media()
    return decide_disk_media(disks or [], error)


def probe_pagefile(env, facts):
    files, error = env.pagefile()
    return decide_pagefile(files or [], error)


def probe_link_speed(env, facts):
    adapters, error = env.link_speed()
    return decide_link_speed(adapters or [], error)


# =============================================================================
# THE DOCUMENTED PERFORMANCE CAUSES. Same contract as everything above: a
# check that could not read says so, and never carries a verdict.
# =============================================================================

# Raster formats whose absence of overviews is a documented draw cost. The
# sidecar names are the two Pro writes: .ovr beside the raster, or .aux.xml.
RASTER_EXTENSIONS = (".tif", ".tiff", ".img", ".jp2", ".bmp", ".gif", ".png",
                     ".jpg", ".jpeg", ".ecw", ".sid")


def _ps_rows(data, name_key, value_key):
    """[(name, value)] out of one ConvertTo-Json answer.

    PowerShell answers a single object where a list was expected, because
    ConvertTo-Json drops the array wrapper for one row. Unwrapping it here
    rather than at each call site is what keeps a machine with exactly one
    disk from reading as a machine with none.
    """
    if not isinstance(data, list):
        data = [data]
    out = []
    for row in data:
        if isinstance(row, dict):
            out.append(("%s" % (row.get(name_key) or "<unnamed>"),
                        row.get(value_key) or None))
    return out


def is_http_uri(uri):
    """True for a data connection that crosses the network by URL.

    Classified from the scheme, not from a hostname: an offline machine and a
    VPN drop both make these layers the slow part of the draw, and the report
    should say that rather than guess at the reason.
    """
    low = (uri or "").strip().lower()
    return low.startswith("http://") or low.startswith("https://")


def wkid_label(wkid):
    """A spatial reference this report can compare, or None."""
    if wkid is None:
        return None
    try:
        return int(wkid)
    except (TypeError, ValueError):
        return None


def decide_projection(projects):
    """PF-01. Coordinate systems in the project, and whether they agree.

    A layer whose spatial reference differs from the map's is reprojected on
    every draw. Pro calls this on-the-fly projection and does it silently, so
    the editor sees a slow map and no message. It is one of the most common
    answers on a machine that is otherwise healthy.

    Only the spatial references the project file actually carries are compared.
    A layer with none recorded is not a mismatch: it inherits the map's, and
    calling that a mismatch would be a verdict about something the file does
    not say.
    """
    title = "Coordinate systems in the project (on-the-fly projection)"
    if not projects:
        return dict(
            cid="PF-01", category="ArcGISPro", status="Info", measured=False,
            title=title,
            observed="No project file was read this run, so no coordinate "
                     "system was compared.",
            expected="Every layer in the map's own coordinate system.",
            impact="S1 and S2 undetermined.",
            fix="Read it in Pro: the map's Properties > Coordinate Systems, "
                "against each layer's Source tab.")
    maps = []
    layers = []
    incomplete = []
    for p in projects:
        if not p.get("parsed"):
            continue
        if p.get("truncated") or p.get("error"):
            incomplete.append(p["path"])
        maps.extend(p.get("maps") or [])
        layers.extend(p.get("layers") or [])
    map_wk = set([w for _n, w in maps if wkid_label(w) is not None])
    layer_wk = set([wkid_label(l["wkid"]) for l in layers
                    if wkid_label(l["wkid"]) is not None])
    if not map_wk and not layer_wk:
        return dict(
            cid="PF-01", category="ArcGISPro", status="Info", measured=False,
            title=title,
            observed=["%d project document(s) were read and not one of them "
                      "records a spatial reference this tool can compare."
                      % len(projects)]
                     + (["partly read: %s" % ", ".join(incomplete)]
                        if incomplete else []),
            expected="A spatial reference recorded on the map and on the "
                     "layers.",
            impact="S1 and S2 undetermined.",
            fix="Read it in Pro: the map's Properties > Coordinate Systems.")
    mismatch = sorted(layer_wk - map_wk) if map_wk else []
    lines = ["map coordinate system(s): %s"
             % (", ".join(str(w) for w in sorted(map_wk)) or "<none recorded>"),
             "layer coordinate system(s): %s"
             % (", ".join(str(w) for w in sorted(layer_wk)) or "<none recorded>")]
    if mismatch:
        lines.append("layers in a different system, reprojected on every draw: "
                     "%s" % ", ".join(str(w) for w in mismatch))
    if len(map_wk) > 1:
        lines.append("more than one map coordinate system is recorded in this "
                     "project")
    if incomplete:
        lines.append("partly read, so the comparison is not complete: %s"
                     % ", ".join(incomplete))
    status = "Warn" if mismatch else ("Info" if incomplete else "Pass")
    return dict(
        cid="PF-01", category="ArcGISPro", status=status, title=title,
        observed=lines,
        expected="Every layer stored in the map's own coordinate system, so "
                 "nothing is reprojected during a draw.",
        impact="S1 and S2. A layer in a different system is reprojected on "
               "every draw, and Pro does it without saying so.",
        fix="Project the data once, offline, with the Project tool into the "
            "map's coordinate system, and point the layer at the result. "
            "Setting the map to the layer's system only moves the cost to the "
            "other layers. Do not do it to a layer that is being edited: "
            "project a copy.")


def decide_http_layers(projects):
    """PF-09. Layers whose rows come from a URL.

    An online basemap or a feature service draws over the network on every
    pan and zoom. On a laptop that has lost the VPN, or a workstation whose
    portal is across a slow link, that is the whole of the stall, and it is
    invisible in every local reading this report makes.
    """
    title = "Layers served over the network by URL"
    if not projects:
        return dict(
            cid="PF-09", category="Network", status="Info", measured=False,
            title=title,
            observed="No project file was read this run, so no layer's source "
                     "was classified.",
            expected="Basemaps and service layers understood as network "
                     "draws, not as local ones.",
            impact="S1 and S2 undetermined.",
            fix="Read the layer list in Pro's Contents pane.")
    served = []
    local = 0
    incomplete = []
    for p in projects:
        if not p.get("parsed"):
            continue
        if p.get("truncated") or p.get("error"):
            incomplete.append(p["path"])
        for layer in p.get("layers") or []:
            if is_http_uri(layer["uri"]):
                served.append("%s  (%s)" % (layer["name"], layer["uri"]))
            elif layer["uri"]:
                local += 1
    lines = ["%d layer(s) draw over the network by URL" % len(served)]
    lines.extend(["   %s" % s for s in served[:20]])
    if len(served) > 20:
        lines.append("   and %d more" % (len(served) - 20))
    lines.append("%d layer(s) draw from a file or a database connection"
                 % local)
    if incomplete:
        lines.append("partly read: %s" % ", ".join(incomplete))
    if len(served) >= HTTP_LAYERS_WARN:
        status = "Warn"
    elif served:
        status = "Info"
    else:
        status = "Info" if incomplete else "Pass"
    return dict(
        cid="PF-09", category="Network", status=status, title=title,
        observed=lines,
        kpi={"http_layers": len(served), "local_layers": local,
             "warn_at": HTTP_LAYERS_WARN},
        expected="A small number of service layers, understood as network "
                 "draws, with the working data local.",
        impact="S1 and S2. These draw over the network on every pan and zoom. "
               "Off the VPN, or on a slow link to the portal, they are the "
               "whole of the stall.",
        fix="Take the working layers local: export the ones that are edited "
            "to a file geodatabase on a local disk, and keep the online "
            "basemap as the only network draw. Where a basemap must stay "
            "online, turn it off while editing.")


def decide_project_weight(projects, sizes):
    """PF-10. How big and how deep the project is.

    Not a fault. A project with two hundred layers in six nested groups opens
    slowly because of what is in it, and reporting that is what stops the rest
    of this report being read as the whole story.
    """
    title = "Project weight: layers, group depth and file size"
    if not projects and not sizes:
        return dict(
            cid="PF-10", category="ArcGISPro", status="Info", measured=False,
            title=title,
            observed="No project was found to weigh.",
            expected="A project small enough to open quickly.",
            impact="S1 undetermined.",
            fix="None.")
    lines = []
    worst_layers = 0
    worst_depth = 0
    counted = 0
    for p in projects:
        if not p.get("parsed"):
            continue
        counted += 1
        layers = len(p.get("layers") or [])
        depth = p.get("depth") or 0
        worst_layers = max(worst_layers, layers)
        worst_depth = max(worst_depth, depth)
        lines.append("%s: %d layer(s), group depth %d" % (p["path"], layers,
                                                          depth))
    for path, size in sizes:
        lines.append("%s: %s" % (path, format_bytes(size)))
    if counted:
        if worst_layers >= LAYERS_MANY or worst_depth >= GROUP_DEEP_WARN \
                or any(s >= APRX_BIG_BYTES for _p, s in sizes):
            status = "Warn"
        elif worst_layers >= LAYERS_WARN:
            status = "Info"
        else:
            status = "Pass"
    else:
        return dict(
            cid="PF-10", category="ArcGISPro", status="Info", measured=False,
            title=title,
            observed="No project document could be read, so nothing was "
                     "weighed. Sizes alone are not a project weight.",
            expected="A project small enough to open quickly.",
            impact="S1 undetermined.",
            fix="None.")
    return dict(
        cid="PF-10", category="ArcGISPro", status=status, title=title,
        observed=lines,
        kpi={"layers": worst_layers, "group_depth": worst_depth,
             "aprx_bytes": max([s for _p, s in sizes]) if sizes else 0},
        expected="Fewer than %d layers, no more than %d levels of group "
                 "nesting, and a project file under %s. Warn at %d layers or "
                 "%d deep." % (LAYERS_WARN, GROUP_DEEP_WARN,
                               format_bytes(APRX_BIG_BYTES), LAYERS_MANY,
                               GROUP_DEEP_WARN),
        impact="S1. A project this size is slow to open and slow to save "
               "because of what is in it, which is a different fix from every "
               "other finding in this report.",
        fix="Split the project: one map per task, and the layers that are not "
            "used moved to their own project. Ungroup what does not need a "
            "group. A layer that is turned off still costs its place in the "
            "project file.")


def decide_layer_load(projects):
    """PF-14. The per-layer work a draw has to do.

    Joins, definition queries, label expressions and visibility ranges are all
    read from the project file. None of them is wrong; together they are the
    reason a map with forty layers draws like one with four hundred.
    """
    title = "Per-layer draw load: joins, queries, labels, visibility"
    if not projects:
        return dict(
            cid="PF-14", category="ArcGISPro", status="Info", measured=False,
            title=title,
            observed="No project file was read this run.",
            expected="A draw that does not re-join and re-query on every "
                     "refresh.",
            impact="S1, S2 and S4 undetermined.",
            fix="Read it per layer in Pro.")
    joins = []
    join_total = 0
    queries = 0
    labels = 0
    scaled = 0
    engines = set()
    counted = 0
    incomplete = []
    for p in projects:
        if not p.get("parsed"):
            continue
        if p.get("truncated") or p.get("error"):
            incomplete.append(p["path"])
        counted += 1
        if p.get("label_engine"):
            engines.add(p["label_engine"])
        for layer in p.get("layers") or []:
            if layer["joins"]:
                # The count that matters is the joins, not the layers carrying
                # one. Three joins on a single layer cost three
                # re-evaluations per draw, and grading on the number of
                # layers would report that as one.
                joins.append("%s: %d join(s)" % (layer["name"],
                                                 layer["joins"]))
                join_total += layer["joins"]
            if layer["query"]:
                queries += 1
            if layer["labels"]:
                labels += 1
            if layer["scaled"]:
                scaled += 1
    if not counted:
        return dict(
            cid="PF-14", category="ArcGISPro", status="Info", measured=False,
            title=title,
            observed="No project document could be read.",
            expected="A draw that does not re-join and re-query on every "
                     "refresh.",
            impact="S1, S2 and S4 undetermined.",
            fix="Read it per layer in Pro.")
    lines = ["%d join(s) across %d layer(s)" % (join_total, len(joins))]
    lines.extend(["   %s" % j for j in joins[:10]])
    lines.append("%d layer(s) carry a definition query" % queries)
    lines.append("%d layer(s) are labelled" % labels)
    lines.append("%d layer(s) draw only inside a visibility range" % scaled)
    lines.append("label engine: %s" % (", ".join(sorted(engines))
                                       or "<not recorded>"))
    if incomplete:
        lines.append("partly read: %s" % ", ".join(incomplete))
    status = "Warn" if join_total >= JOINS_WARN else "Info"
    return dict(
        cid="PF-14", category="ArcGISPro", status=status, title=title,
        observed=lines,
        expected="Joins made permanent where the join is permanent, definition "
                 "queries on the source rather than on the draw, and the "
                 "label engine understood: Maplex places better and costs "
                 "more.",
        impact="S1, S2 and S4. A join is re-evaluated on every draw, a "
               "definition query on every refresh, and a label expression on "
               "every placement pass.",
        fix="Make a permanent join permanent: run Join Field, or export the "
            "joined result, and point the layer at that instead. Move a "
            "definition query into the source where the data allows it. Turn "
            "labelling off on the layers that do not need it, and consider the "
            "standard engine where Maplex placement is not being used.")


def decide_overviews(rasters, with_overview, unreadable):
    """PF-16. Rasters with no overviews beside them.

    A raster with no pyramid is read at full resolution and thinned on the
    screen, every draw. Building them once costs minutes; not building them
    costs the same minutes on every pan for the life of the project.
    """
    title = "Raster overviews (pyramids) in the project folder"
    if unreadable and not rasters:
        return dict(
            cid="PF-16", category="ArcGISPro", status="Info", measured=False,
            title=title,
            observed="The project folder could not be read: %s. Nothing was "
                     "counted, which is not the same as finding none."
                     % "; ".join(unreadable[:4]),
            expected="An overview beside every raster.",
            impact="S1 and S4 undetermined.",
            fix="Look for a .ovr or .aux.xml beside each raster.")
    if not rasters:
        return dict(
            cid="PF-16", category="ArcGISPro", status="Info",
            title=title,
            observed="No raster file was found under the recent project "
                     "folders that this tool looks at.",
            expected="An overview beside every raster.",
            impact="None found here.",
            fix="If the rasters live elsewhere, build overviews there with "
                "the Build Pyramids tool.")
    missing = [r for r in rasters if r not in with_overview]
    lines = ["%d raster file(s) under the project folder, %d with an overview "
             "beside them" % (len(rasters), len(with_overview))]
    lines.extend(["   no overview: %s" % m for m in missing[:10]])
    if len(missing) > 10:
        lines.append("   and %d more" % (len(missing) - 10))
    if unreadable:
        lines.append("folders that could not be read: %s"
                     % "; ".join(unreadable[:4]))
    status = "Warn" if missing else "Pass"
    return dict(
        cid="PF-16", category="ArcGISPro", status=status, title=title,
        observed=lines,
        expected="An .ovr or .aux.xml beside every raster, built once.",
        impact="S1 and S4. A raster with no pyramid is read at full "
               "resolution and thinned on screen, on every draw.",
        fix="Run Build Pyramids over the raster folder once, with Pro closed. "
            "It writes the sidecar and does not touch the raster. On a share, "
            "copy the rasters local first: building pyramids over the network "
            "is slow and leaves the sidecar there too.")


def decide_disk_media(disks, error):
    """PF-18. What the volumes are physically made of.

    The one hardware finding no setting fixes. A rotational disk holding the
    display cache, the scratch workspace and the project is the most common
    hardware cause of a slow session, and it reads as a mystery because every
    software setting on the machine is correct.
    """
    title = "Disk media type under the caches and the project"
    if error:
        return dict(
            cid="PF-18", category="Hardware", status="Info", measured=False,
            title=title,
            observed="The disk list could not be read: %s. An unread list is "
                     "not a disk that is fine." % error,
            expected="SSD under the caches, the scratch workspace and the "
                     "project.",
            impact="S1 and S2 undetermined.",
            fix="Read it by hand: Get-PhysicalDisk | Select FriendlyName, "
                "MediaType.")
    if not disks:
        return dict(
            cid="PF-18", category="Hardware", status="Info", measured=False,
            title=title,
            observed="The disk list came back empty, so no media type was "
                     "read.",
            expected="SSD under the caches, the scratch workspace and the "
                     "project.",
            impact="S1 and S2 undetermined.",
            fix="Read it by hand: Get-PhysicalDisk.")
    lines = ["%s: %s" % (name, media or "<not reported>")
             for name, media in disks]
    slow = [name for name, media in disks
            if (media or "").upper() in DISK_MEDIA_WARN]
    status = "Warn" if slow else "Pass"
    if slow:
        lines.append("rotational or unreported: %s" % ", ".join(slow))
    return dict(
        cid="PF-18", category="Hardware", status=status, title=title,
        observed=lines,
        expected="SSD. A rotational disk under the caches and the project is "
                 "the most common hardware cause of a slow session, and no "
                 "setting fixes it.",
        impact="S1 and S2. Every cache write, every scratch workspace and "
               "every project save waits on the platter.",
        fix="Move the ESRI cache folder and the geoprocessing scratch "
            "workspace to the fastest local volume: Project > Options > "
            "General for the scratch workspace, and the Cache Path value "
            "under HKCU\\Software\\ESRI\\ArcGISPro\\Settings for the cache. "
            "Then move the project. Nothing in this report helps until the "
            "disk does.")


def decide_pagefile(files, error):
    """PF-20. The pagefile, and where it lives.

    Read from the registry value, which is what Windows reads at boot. A
    pagefile on a network volume is a stall that appears in no Pro log, and a
    pagefile that is too small turns a memory spike into a hard failure
    instead of a slow one.
    """
    title = "Pagefile size and location"
    if error:
        return dict(
            cid="PF-20", category="System", status="Info", measured=False,
            title=title,
            observed="The pagefile setting could not be read: %s." % error,
            expected="A system-managed pagefile on a local disk.",
            impact="S2 undetermined.",
            fix="Read it in Windows: System > Advanced > Performance > "
                "Advanced > Virtual memory.")
    if not files:
        return dict(
            cid="PF-20", category="System", status="Info",
            title=title,
            observed="The pagefile is set to system managed, so its size is "
                     "decided by Windows at boot and this tool cannot read "
                     "the number it chose.",
            expected="A system-managed pagefile on a local disk.",
            impact="S2. System managed is the safe default; the size is not "
                   "readable from here.",
            fix="None unless the machine is running out of memory, in which "
                "case set an explicit initial size on a local disk.")
    lines = list(files)
    remote = [f for f in files if is_network_path(f.split(" ")[0])]
    small = [f for f in files if _pagefile_mb(f) is not None
             and _pagefile_mb(f) < PAGEFILE_MIN_MB]
    status = "Fail" if remote else ("Warn" if small else "Pass")
    if remote:
        lines.append("on a network volume: %s" % ", ".join(remote))
    if small:
        lines.append("below %d MB: %s" % (PAGEFILE_MIN_MB, ", ".join(small)))
    return dict(
        cid="PF-20", category="System", status=status, title=title,
        observed=lines,
        expected="A pagefile of at least %d MB on a local disk."
                 % PAGEFILE_MIN_MB,
        impact="S2. A pagefile on a network volume is a multi-second stall "
               "that appears in no Pro log. One that is too small turns a "
               "memory spike into a failure instead of a pause.",
        fix="System > Advanced > Performance Settings > Advanced > Virtual "
            "memory: put it on a local disk, system managed or with an "
            "explicit initial size, then restart.")


def _pagefile_mb(entry):
    """The initial size in MB out of one PagingFiles entry, or None."""
    parts = (entry or "").split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def decide_link_speed(adapters, error):
    """PF-21. What the adapters actually negotiated.

    An adapter configured for gigabit and linked at a hundred megabit is a
    cable, a switch port or a power-saving setting, and it presents as a slow
    server. Nothing in the SMB settings explains it.
    """
    title = "Network adapter link speed"
    if error:
        return dict(
            cid="PF-21", category="Network", status="Info", measured=False,
            title=title,
            observed="The adapter list could not be read: %s." % error,
            expected="%d Mbit or better on the adapter carrying the database "
                     "session." % LINK_SPEED_MIN_MBPS,
            impact="S1, S2 and S3 undetermined.",
            fix="Read it by hand: Get-NetAdapter | Select Name, LinkSpeed.")
    if not adapters:
        return dict(
            cid="PF-21", category="Network", status="Info", measured=False,
            title=title,
            observed="No adapter that is up reported a link speed.",
            expected="%d Mbit or better." % LINK_SPEED_MIN_MBPS,
            impact="S1, S2 and S3 undetermined.",
            fix="Read it by hand: Get-NetAdapter.")
    lines = ["%s: %s" % (name, speed) for name, speed in adapters]
    slow = [(name, speed) for name, speed in adapters
            if _mbps(speed) is not None and _mbps(speed) < LINK_SPEED_MIN_MBPS]
    unknown = [name for name, speed in adapters if _mbps(speed) is None]
    if slow:
        lines.append("below %d Mbit: %s" % (
            LINK_SPEED_MIN_MBPS,
            ", ".join("%s (%s)" % pair for pair in slow)))
    if unknown and not slow:
        return dict(
            cid="PF-21", category="Network", status="Info", measured=False,
            title=title, observed=lines,
            expected="%d Mbit or better." % LINK_SPEED_MIN_MBPS,
            impact="S1, S2 and S3 undetermined.",
            fix="Read it by hand: Get-NetAdapter | Select Name, LinkSpeed.")
    status = "Warn" if slow else "Pass"
    speeds = [_mbps(v) for _n, v in adapters]
    known = [v for v in speeds if v is not None]
    return dict(
        cid="PF-21", category="Network", status=status, title=title,
        observed=lines,
        kpi={"worst_mbps": min(known) if known else 0,
             "min_mbps": LINK_SPEED_MIN_MBPS, "adapters": len(adapters)},
        expected="%d Mbit or better on the adapter carrying the database "
                 "session." % LINK_SPEED_MIN_MBPS,
        impact="S1, S2 and S3. A gigabit adapter linked at a hundred megabit "
               "presents as a slow server, and nothing in the SMB settings "
               "explains it.",
        fix="Check the cable and the switch port, then the adapter's speed "
            "and duplex setting in Device Manager. On a docking station, the "
            "dock is often the limit.")


def _mbps(speed):
    """Megabits per second out of a Get-NetAdapter LinkSpeed string, or None."""
    text = (speed or "").strip().lower()
    match = re.search(r"([\d.]+)\s*(gbps|mbps|gbit|mbit|gb|mb)", text)
    if not match:
        return None
    value = float(match.group(1))
    return value * 1000.0 if match.group(2).startswith("g") else value


# =============================================================================
# EXPLICITLY SELECTED .APRX PROJECT. A local snapshot, full bounded integrity
# checks, then an optional recovery COPY. The source is never rewritten.
# =============================================================================


def _empty_project_inspection(path):
    return {
        "path": path, "size": None, "entries": 0, "texts": [],
        "main": None, "main_name": None, "main_format": None,
        "integrity": "Info", "integrity_reason": "not inspected",
        "archive_opened": False, "read_complete": False,
        "duplicates": [], "crc_errors": [], "read_errors": [],
        "json_errors": [], "json_capped": [], "main_not_dict": False,
    }


def inspect_aprx_archive(path, display_path=None):
    """Inspect one local .aprx snapshot without contacting its data sources.

    Every non-directory ZIP member is streamed to EOF so its CRC is checked.
    JSON documents are retained only inside explicit entry/total budgets. A
    malformed archive is Fail; a file that exceeds a safety budget or cannot
    be read is Info, because its content was not determined. No repair is
    allowed from an Info integrity record.
    """
    import zipfile
    import zlib

    shown = display_path or path
    out = _empty_project_inspection(shown)
    if not shown or not str(shown).lower().endswith(".aprx"):
        out["integrity_reason"] = ("le fichier sélectionné ne porte pas "
                                   "l'extension .aprx")
        return out
    try:
        stat = os.stat(path)
        out["size"] = stat.st_size
    except (OSError, IOError, ValueError) as exc:
        out["integrity_reason"] = "lecture du fichier impossible: %s" % exc
        return out
    if stat.st_size == 0:
        out["integrity"] = "Fail"
        out["integrity_reason"] = "le fichier est vide (0 octet)"
        return out
    if stat.st_size > PROJECT_MAX_BYTES:
        out["integrity_reason"] = (
            "fichier de %s, au-delà de la limite de sécurité de %s; contenu "
            "non mesuré" % (format_bytes(stat.st_size),
                            format_bytes(PROJECT_MAX_BYTES)))
        return out

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, IOError) as exc:
        out["integrity_reason"] = "ouverture impossible: %s" % exc
        return out
    except (zipfile.BadZipfile, ValueError) as exc:
        out["integrity"] = "Fail"
        out["integrity_reason"] = "archive ZIP/APRX invalide: %s" % exc
        return out

    retained_json = 0
    expanded = 0
    with archive:
        try:
            infos = archive.infolist()
        except (OSError, IOError, zipfile.BadZipfile, ValueError) as exc:
            out["integrity"] = "Fail"
            out["integrity_reason"] = "table des éléments ZIP invalide: %s" % exc
            return out
        out["archive_opened"] = True
        out["entries"] = len(infos)
        if len(infos) > PROJECT_MAX_ENTRIES:
            out["integrity_reason"] = (
                "%d éléments, au-delà de la limite de %d; contenu non mesuré"
                % (len(infos), PROJECT_MAX_ENTRIES))
            return out
        declared = sum(max(0, info.file_size) for info in infos)
        if declared > PROJECT_MAX_EXPANDED_BYTES:
            out["integrity_reason"] = (
                "taille décompressée déclarée de %s, au-delà de la limite de "
                "%s; contenu non mesuré"
                % (format_bytes(declared),
                   format_bytes(PROJECT_MAX_EXPANDED_BYTES)))
            return out
        names = collections.Counter(info.filename for info in infos)
        out["duplicates"] = sorted(name for name, count in names.items()
                                   if count > 1)
        if out["duplicates"]:
            out["integrity"] = "Fail"
            out["integrity_reason"] = (
                "noms d'éléments dupliqués dans l'archive: %s"
                % ", ".join(out["duplicates"][:10]))
            return out

        lower_names = [info.filename.lower() for info in infos]
        if "gisproject.json" in lower_names:
            out["main_format"] = "json"
            out["main_name"] = infos[lower_names.index("gisproject.json")].filename
        elif "gisproject.xml" in lower_names:
            out["main_format"] = "xml"

        for info in infos:
            if info.filename.endswith("/"):
                continue
            if info.flag_bits & 0x1:
                out["integrity"] = "Fail"
                out["integrity_reason"] = (
                    "élément chiffré non pris en charge: %s" % info.filename)
                return out
            chunks = []
            got = 0
            keep_json = info.filename.lower().endswith(".json")
            try:
                handle = archive.open(info, "r")
                try:
                    while True:
                        block = handle.read(1024 * 1024)
                        if not block:
                            break
                        got += len(block)
                        expanded += len(block)
                        if expanded > PROJECT_MAX_EXPANDED_BYTES:
                            out["integrity_reason"] = (
                                "taille réellement décompressée au-delà de %s; "
                                "contenu non mesuré"
                                % format_bytes(PROJECT_MAX_EXPANDED_BYTES))
                            return out
                        if keep_json and got <= PROJECT_JSON_MAX_ENTRY_BYTES \
                                and retained_json + got \
                                <= PROJECT_JSON_MAX_TOTAL_BYTES:
                            chunks.append(block)
                        else:
                            # Once a JSON entry crosses a budget, no partial
                            # document is kept: a truncated JSON parse would
                            # falsely look like corruption.
                            chunks = []
                            keep_json = False
                finally:
                    handle.close()
            except (zipfile.BadZipfile, ValueError, EOFError, zlib.error,
                    NotImplementedError) as exc:
                out["crc_errors"].append("%s: %s" % (info.filename, exc))
                continue
            except (OSError, IOError) as exc:
                out["read_errors"].append("%s: %s" % (info.filename, exc))
                continue
            if info.filename.lower().endswith(".json"):
                if keep_json and got <= PROJECT_JSON_MAX_ENTRY_BYTES \
                        and retained_json + got <= PROJECT_JSON_MAX_TOTAL_BYTES:
                    retained_json += got
                    try:
                        text = b"".join(chunks).decode("utf-8-sig")
                    except UnicodeDecodeError as exc:
                        out["json_errors"].append("%s: encodage UTF-8 "
                                                  "invalide: %s"
                                                  % (info.filename, exc))
                    else:
                        out["texts"].append((info.filename, text))
                else:
                    out["json_capped"].append(info.filename)

        out["read_complete"] = not out["crc_errors"] and not out["read_errors"]
        if out["crc_errors"]:
            out["integrity"] = "Fail"
            out["integrity_reason"] = (
                "CRC ou compression invalide: %s"
                % "; ".join(out["crc_errors"][:10]))
        elif out["read_errors"]:
            out["integrity_reason"] = (
                "éléments non lus: %s" % "; ".join(out["read_errors"][:10]))
        else:
            out["integrity"] = "Pass"
            out["integrity_reason"] = (
                "%d éléments lus jusqu'à leur fin et vérifiés par CRC"
                % len([i for i in infos if not i.filename.endswith("/")]))

    # JSON syntax is a separate finding from ZIP integrity. Decode errors are
    # contained per document so one bad map does not hide every other map.
    valid_texts = []
    for name, text in out["texts"]:
        try:
            doc = json.loads(text)
        except (ValueError, TypeError, RecursionError) as exc:
            out["json_errors"].append("%s: %s" % (name, exc))
            continue
        valid_texts.append((name, text))
        if name == out["main_name"]:
            if isinstance(doc, dict):
                out["main"] = doc
            else:
                out["main_not_dict"] = True
    out["texts"] = valid_texts
    return out


def prepare_project_snapshot(path):
    """Return (temporary snapshot, inspection). The caller removes the snapshot.

    The source is opened only for the copy and for stat checks before/after.
    If it changes during that copy, the snapshot is rejected instead of
    presenting a mixture of two saves as one project.
    """
    import shutil
    import tempfile

    # Only stat the source here. The ZIP itself is opened after the snapshot,
    # never once for a pre-check and then a second time for the real one.
    initial = _empty_project_inspection(path)
    if not path or not str(path).lower().endswith(".aprx"):
        initial["integrity_reason"] = ("le fichier sélectionné ne porte pas "
                                       "l'extension .aprx")
        return None, initial
    try:
        initial["size"] = os.stat(path).st_size
    except (OSError, IOError, ValueError) as exc:
        initial["integrity_reason"] = "lecture du fichier impossible: %s" % exc
        return None, initial
    if initial["size"] == 0:
        initial["integrity"] = "Fail"
        initial["integrity_reason"] = "le fichier est vide (0 octet)"
        return None, initial
    if initial["size"] > PROJECT_MAX_BYTES:
        initial["integrity_reason"] = (
            "fichier de %s, au-delà de la limite de sécurité de %s; contenu "
            "non mesuré" % (format_bytes(initial["size"]),
                            format_bytes(PROJECT_MAX_BYTES)))
        return None, initial

    temporary = None
    try:
        before = os.stat(path)
        handle, temporary = tempfile.mkstemp(prefix="properf_aprx_",
                                             suffix=".aprx")
        os.close(handle)
        shutil.copyfile(path, temporary)
        after = os.stat(path)
        before_sig = (before.st_size, getattr(before, "st_mtime_ns",
                                             int(before.st_mtime * 1000000000)))
        after_sig = (after.st_size, getattr(after, "st_mtime_ns",
                                           int(after.st_mtime * 1000000000)))
        if before_sig != after_sig:
            os.unlink(temporary)
            temporary = None
            out = _empty_project_inspection(path)
            out["size"] = after.st_size
            out["integrity_reason"] = (
                "le fichier a changé pendant la copie; fermez les autres "
                "sessions qui l'enregistrent puis réessayez")
            return None, out
        return temporary, inspect_aprx_archive(temporary, path)
    except (OSError, IOError, ValueError) as exc:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
        out = _empty_project_inspection(path)
        try:
            out["size"] = os.stat(path).st_size
        except (OSError, IOError, ValueError):
            pass
        out["integrity_reason"] = "copie instantanée impossible: %s" % exc
        return None, out


def _project_check(cid, title, status, observed, expected, impact, fix,
                   measured=True, kpi=None):
    return dict(cid=cid, category="Projet APRX", title=title, status=status,
                observed=observed, expected=expected, impact=impact, fix=fix,
                measured=measured, kpi=kpi)


def _integrity_project_check(info):
    lines = ["Projet sélectionné : %s" % info["path"]]
    if info.get("size") is not None:
        lines.append("Taille : %s" % format_bytes(info["size"]))
    if info.get("archive_opened"):
        lines.append("Éléments ZIP : %d" % info["entries"])
    lines.append(info["integrity_reason"])
    return _project_check(
        "PR-01", "Lisibilité et intégrité du conteneur .aprx",
        info["integrity"], lines,
        "Archive ZIP non chiffrée, sans doublon, dont tous les éléments sont "
        "lus jusqu'à leur fin et validés par CRC, dans les limites de sécurité.",
        "S6. Un conteneur corrompu peut empêcher l'ouverture du projet ou "
        "fermer ArcGIS Pro pendant sa lecture.",
        "Restaurer une sauvegarde; ne reconstruire que si tous les éléments "
        "sont vérifiables.", measured=info["integrity"] != "Info",
        kpi={"aprx_bytes": info.get("size") or 0,
             "zip_entries": info.get("entries") or 0,
             "crc_errors": len(info.get("crc_errors") or [])})


def _structure_project_check(info):
    errors = info.get("json_errors") or []
    capped = info.get("json_capped") or []
    main_error = [e for e in errors if info.get("main_name")
                  and e.startswith(info["main_name"] + ":")]
    lines = ["Document principal : %s" % (info.get("main_name")
                                           or info.get("main_format")
                                           or "<absent>"),
             "%d document(s) JSON analysé(s), %d erreur(s) de syntaxe, "
             "%d document(s) non analysé(s) à cause d'une limite"
             % (len(info.get("texts") or []), len(errors), len(capped))]
    lines.extend("JSON invalide : %s" % e for e in errors[:20])
    lines.extend("Non analysé : %s" % e for e in capped[:20])
    if info.get("main"):
        status = "Warn" if errors else ("Info" if capped else "Pass")
        measured = not capped
        lines.append("GISProject.json est présent et contient un objet projet.")
    elif main_error or info.get("main_not_dict"):
        status, measured = "Fail", True
        lines.append("GISProject.json ne contient pas un objet projet valide.")
    elif info.get("main_format") == "xml":
        status, measured = "Info", False
        lines.append("Projet au format XML d'ArcGIS Pro 2.x : la réparation "
                     "JSON Pro 3 n'est pas applicable.")
    elif info.get("integrity") == "Info":
        status, measured = "Info", False
        lines.append("La structure interne n'a pas pu être déterminée.")
    else:
        status, measured = "Fail", True
        lines.append("GISProject.json est absent du projet Pro 3.")
    return _project_check(
        "PR-02", "Validité des documents JSON internes du projet", status,
        lines, "GISProject.json valide et tous les documents JSON analysés "
        "sans erreur ni troncature.",
        "S6. Un document interne invalide peut empêcher le chargement d'une "
        "carte, d'une mise en page ou du projet entier.",
        "Restaurer une version saine; ne pas supprimer au hasard un document "
        "référencé par le projet.", measured=measured,
        kpi={"json_documents": len(info.get("texts") or []),
             "json_errors": len(errors), "json_capped": len(capped)})


def _storage_project_check(env, path, projects):
    onedrive = onedrive_roots(env)
    network = env.network_drives()
    own_class = path_class(path, onedrive, network)
    lines = ["Fichier projet : %s [%s]" % (path, own_class)]
    remote = [path] if own_class != "LOCAL" else []
    for project in projects:
        for label, slot in (("Dossier principal", "home"),
                            ("Géodatabase par défaut", "gdb")):
            value = project.get(slot)
            if not value:
                lines.append("%s : <non enregistré>" % label)
                continue
            lines.append("%s : %s [%s]" % (label, value[1], value[2]))
            if value[2] != "LOCAL":
                remote.append(value[1])
    status = "Warn" if remote else "Pass"
    if remote:
        lines.append("Emplacement(s) distant(s) ou synchronisé(s) : %d"
                     % len(remote))
    else:
        lines.append("Le fichier et les emplacements par défaut enregistrés "
                     "sont locaux.")
    return _project_check(
        "PR-03", "Emplacement du projet et de ses espaces de travail", status,
        lines, "Projet, dossier principal et géodatabase par défaut sur un "
        "disque local non synchronisé pour le dépannage.",
        "S1, S2 et S6. Le réseau ou la synchronisation peut retarder ou "
        "interrompre l'ouverture et l'enregistrement.",
        "Tester une copie locale complète en conservant les chemins relatifs.",
        kpi={"remote_locations": len(remote)})


def _database_path(uri):
    """Extract only DATABASE from a connection string; never print secrets."""
    text = (uri or "").strip()
    match = re.search(r"(?:^|;)DATABASE=([^;]+)", text, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        # In an enterprise connection, DATABASE is often just "gis" or
        # "sde". Treating that as a relative file would invent a missing
        # source. Only a path-shaped value, or a known file-geodatabase/
        # database suffix, is eligible for a local existence check.
        if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|/)", value) \
                or value.lower().endswith((".gdb", ".sqlite", ".db", ".mdb")):
            return value
        return None
    # A plain path is safe to classify. Any other key/value connection string
    # stays opaque so SERVER/USER/PASSWORD never reaches the report.
    if "=" not in text and not is_http_uri(text) \
            and not text.upper().startswith("CIMPATH="):
        if re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|\.{1,2}[\\/]|/)", text):
            return text
    return None


def _sources_project_check(env, path, projects):
    folder = ntpath.dirname(path)
    network_drives = env.network_drives()
    onedrive = onedrive_roots(env)
    missing = {}
    tested = set()
    remote = set()
    opaque = 0
    services = 0
    for project in projects:
        for layer in project.get("layers") or []:
            uri = layer.get("uri") or ""
            if is_http_uri(uri):
                services += 1
                continue
            raw = _database_path(uri)
            if not raw:
                if uri:
                    opaque += 1
                continue
            expanded = expand_vars(env, raw)
            if os.name != "nt" and not re.match(r"^[A-Za-z]:", expanded) \
                    and not expanded.startswith("\\\\"):
                # The CLI can inspect a copied project off Windows too. Use
                # the host filesystem for a relative POSIX test path instead
                # of turning /tmp into the Windows-looking \\tmp.
                full = os.path.normpath(os.path.join(
                    folder, expanded.replace("\\", "/")))
            else:
                full = ntpath.normpath(ntpath.join(folder, expanded))
            classification = path_class(full, onedrive, network_drives)
            if classification != "LOCAL":
                remote.add(full)
                continue
            if full in tested or full in missing:
                if full in missing:
                    missing[full].append(layer.get("name") or "<sans nom>")
                continue
            tested.add(full)
            if not env.isdir(full) and not env.isfile(full):
                missing[full] = [layer.get("name") or "<sans nom>"]
    lines = ["%d source(s) fichier locale(s) testée(s), %d absente(s)"
             % (len(tested), len(missing)),
             "%d source(s) réseau/synchronisée(s) référencée(s) mais non "
             "contactée(s), %d couche(s) de service HTTP(S), %d connexion(s) "
             "opaque(s) non testée(s)" % (len(remote), services, opaque)]
    for source, layers in sorted(missing.items()):
        lines.append("ABSENTE : %s — couche(s) : %s"
                     % (source, ", ".join(layers[:12])))
    for source in sorted(remote)[:20]:
        lines.append("NON TESTÉE (aucun accès réseau) : %s" % source)
    if missing:
        status = "Warn"
    elif tested and not remote and not opaque:
        status = "Pass"
    else:
        status = "Info"
    return _project_check(
        "PR-04", "Sources de données fichier et références indisponibles",
        status, lines,
        "Toutes les sources fichier locales existent; les sources distantes "
        "sont identifiées mais ne sont jamais contactées par ce diagnostic.",
        "S1 et S2. Une source absente casse l'affichage d'une couche; une "
        "source distante indisponible peut faire attendre l'ouverture.",
        "Reconnecter chaque source dans une copie et vérifier le contenu; "
        "ne pas confondre couche cassée et projet corrompu.",
        measured=bool(tested or remote or services or opaque),
        kpi={"local_sources": len(tested), "missing_sources": len(missing),
             "remote_sources": len(remote), "service_layers": services})


def _views_project_check(info):
    doc = info.get("main")
    if not doc:
        return _project_check(
            "PR-05", "Vues restaurées automatiquement à l'ouverture", "Info",
            "GISProject.json valide non disponible : les vues n'ont pas été "
            "mesurées.",
            "Une liste de vues lisible dans le document principal.",
            "S6 indéterminé.", "Analyser d'abord PR-01 et PR-02.",
            measured=False)
    views = doc.get("views", [])
    if not isinstance(views, list):
        return _project_check(
            "PR-05", "Vues restaurées automatiquement à l'ouverture", "Warn",
            "La propriété views n'est pas une liste : schéma inattendu %s."
            % type(views).__name__,
            "La propriété views, si présente, doit être une liste.",
            "S6. Cet état de vue incohérent peut perturber la restauration de "
            "l'interface.", "Restaurer une sauvegarde saine du projet.")
    layout = len([v for v in views if isinstance(v, dict)
                  and (v.get("type") == "CIMLayoutView"
                       or "layout" in str(v.get("viewType", "")).lower())])
    maps = len(views) - layout
    has_dock = bool(doc.get("viewLayoutXML"))
    lines = ["%d vue(s) enregistrée(s) pour réouverture : %d carte/scène(s), "
             "%d mise(s) en page" % (len(views), maps, layout),
             "État des volets enregistré : %s" % ("oui" if has_dock else "non")]
    if views or has_dock:
        lines.append("Ce n'est pas une anomalie en soi. La copie de "
                     "récupération peut retirer cet état sans supprimer les "
                     "cartes ni les mises en page.")
    status = "Info" if views or has_dock else "Pass"
    return _project_check(
        "PR-05", "Vues restaurées automatiquement à l'ouverture", status,
        lines, "Aucune vue défectueuse restaurée automatiquement pendant un "
        "diagnostic de crash.",
        "S6. Une vue qui déclenche un plantage peut être rouverte avant que "
        "l'utilisateur puisse intervenir.",
        "Créer une copie sans views ni viewLayoutXML; conserver toutes les "
        "définitions de cartes et mises en page.",
        kpi={"saved_views": len(views), "layout_views": layout})


def _history_project_check(info):
    doc = info.get("main")
    if not doc:
        return _project_check(
            "PR-06", "Volume de l'historique de géotraitement", "Info",
            "GISProject.json valide non disponible : historique non mesuré.",
            "Une liste projectItems lisible.", "S1 indéterminé.",
            "Analyser d'abord PR-01 et PR-02.", measured=False)
    items = doc.get("projectItems", [])
    if not isinstance(items, list):
        return _project_check(
            "PR-06", "Volume de l'historique de géotraitement", "Warn",
            "La propriété projectItems n'est pas une liste : schéma inattendu "
            "%s." % type(items).__name__,
            "La propriété projectItems, si présente, doit être une liste.",
            "S1 et S6. Le document principal peut être incohérent.",
            "Restaurer une sauvegarde; réparation automatique refusée.")
    history = [item for item in items if isinstance(item, dict)
               and item.get("itemType") == "GPHistory"]
    try:
        size = sum(len(json.dumps(item, ensure_ascii=False)) for item in history)
    except (ValueError, TypeError, RecursionError) as exc:
        return _project_check(
            "PR-06", "Volume de l'historique de géotraitement", "Info",
            "%d éléments GPHistory trouvés, mais leur poids n'a pas pu être "
            "mesuré: %s: %s" % (len(history), type(exc).__name__, exc),
            "Une sérialisation bornée des éléments d'historique.",
            "S1 indéterminé.", "Restaurer une sauvegarde ou inspecter ce "
            "document avec le support; ne pas le modifier automatiquement.",
            measured=False, kpi={"history_items": len(history)})
    status = ("Warn" if len(history) >= PROJECT_HISTORY_WARN
              or size >= PROJECT_HISTORY_WARN_BYTES else "Pass")
    lines = ["%d opération(s) dans l'historique de géotraitement"
             % len(history),
             "Poids JSON approximatif de ces opérations : %s" % format_bytes(size),
             "Seuil d'avertissement de cet outil : %d opérations ou %s"
             % (PROJECT_HISTORY_WARN, format_bytes(PROJECT_HISTORY_WARN_BYTES))]
    return _project_check(
        "PR-06", "Volume de l'historique de géotraitement", status, lines,
        "Historique conservé sous les seuils de cet outil, ou retiré d'une "
        "copie après sauvegarde des paramètres utiles.",
        "S1 et S2. Un gros historique allonge la lecture du projet et augmente "
        "la mémoire utilisée à l'ouverture.",
        "Créer une copie sans historique si nécessaire; l'original conserve "
        "les paramètres et messages.",
        kpi={"history_items": len(history), "history_bytes": size})


def _project_document(env, path, checks, applied, notes, recovery):
    """Build the common report document for a selected-project run."""
    for check in checks:
        found = SOURCES.get(check.get("id"))
        if found:
            check["sources"] = [list(pair) for pair in found]
        enrich_check(check)
    return {
        "report_version": REPORT_VERSION,
        "analysis_type": "project",
        "project": path,
        "taken": stamp(env.now()),
        "user": env.username(),
        "elevated": env.elevated(),
        "checks": checks,
        "counts": dict(counts(checks)),
        "act_first": [c["id"] for c in act_first(checks)],
        "fixable": [c["id"] for c in fixable(checks)],
        "applied": applied,
        "notes": notes,
        "recovery": recovery,
        "symptoms": list(SYMPTOMS),
    }


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
        ("DL-00", "Session", "Diagnostic is running as the editor", True,
         lambda: probe_session(env, facts)),
        ("DL-05", "Storage", "Project storage location", True,
         lambda: probe_storage(env, facts)),
        ("DL-03", "ArcGISPro", "Project backup interval", True,
         lambda: probe_backup(env, facts)),
        ("DL-04", "Network", "Network adapter power management", True,
         lambda: probe_nic(env, facts)),
        ("DL-06", "Storage", "OneDrive Known Folder Move", True,
         lambda: probe_kfm(env, facts)),
        ("DL-07", "Storage", "Cloud placeholders", True,
         lambda: probe_placeholders(env, facts)),
        ("DL-08", "Network", "Offline Files", True,
         lambda: probe_offline_files(env, facts)),
        ("DL-09", "ArcGISPro", "Crash dumps", True,
         lambda: probe_crash(env, facts)),
        ("DL-10", "Network", "SDE server reachability", True,
         lambda: probe_sde_reach(env, facts, opts)),
        ("DL-11", "Geodatabase", "SDE connection files", True,
         lambda: probe_sde_files(env, facts)),
        ("DL-12", "ArcGISPro", "Pro running during the diagnostic", True,
         lambda: probe_pro_running(env, facts)),
        ("DL-13", "System", "Event timeline", True,
         lambda: probe_events(env, facts)),
        ("PF-07", "Cache", "Leftover session folders", True,
         lambda: probe_protemp(env, facts)),

        ("PF-01", "ArcGISPro", "Coordinate systems in the project", False,
         lambda: probe_projection(env, facts)),
        ("PF-02", "Network", "SMB client configuration", False,
         lambda: probe_smb(env, facts)),
        ("PF-03", "Network", "Directory listing time on the project share",
         False, lambda: probe_share_latency(env, facts)),
        ("PF-04", "ArcGISPro", "Pro version and patch level", False,
         lambda: probe_pro_version(env, facts)),
        ("PF-31", "ArcGISPro", "Startup add-ins, configurations and plug-ins",
         False, lambda: probe_addins(env, facts)),
        ("PF-05", "Display", "Rendering and display settings", False,
         lambda: probe_rendering(env, facts)),
        ("PF-06", "Cache", "ESRI local caches", False,
         lambda: probe_caches(env, facts)),
        ("PF-08", "ArcGISPro", "Search index state", False,
         lambda: probe_index(env, facts)),
        ("PF-09", "Network", "Layers served over the network", False,
         lambda: probe_http_layers(env, facts)),
        ("PF-10", "ArcGISPro", "Project weight", False,
         lambda: probe_project_weight(env, facts)),
        ("PF-11", "Hardware", "Per-app GPU preference", False,
         lambda: probe_gpu_preference(env, facts)),
        ("PF-12", "Hardware", "Physical memory", False,
         lambda: probe_memory(env, facts)),
        ("PF-13", "Hardware", "Free disk space", False,
         lambda: probe_disk(env, facts)),
        ("PF-14", "ArcGISPro", "Per-layer draw load", False,
         lambda: probe_layer_load(env, facts)),
        ("PF-15", "System", "Active power plan", False,
         lambda: probe_power_plan(env, facts)),
        ("PF-16", "ArcGISPro", "Raster overviews", False,
         lambda: probe_overviews(env, facts)),
        ("PF-17", "Security", "Microsoft Defender exclusions", False,
         lambda: probe_defender(env, facts)),
        ("PF-18", "Hardware", "Disk media type", False,
         lambda: probe_disk_media(env, facts)),
        ("PF-19", "Network", "Portal reachability", False,
         lambda: probe_portal(env, facts, opts)),
        ("PF-20", "System", "Pagefile size and location", False,
         lambda: probe_pagefile(env, facts)),
        ("PF-21", "Network", "Network adapter link speed", False,
         lambda: probe_link_speed(env, facts)),
        ("PF-22", "ArcGISPro", "Layer refresh timers from the .aprx", False,
         lambda: probe_aprx(env, facts)),
        ("PF-26", "System", "Roaming profile", False,
         lambda: probe_roaming(env, facts)),
        ("PF-27", "Display", "Symbology refresh (S4)", False,
         lambda: probe_symbology(env, facts)),
        ("PF-28", "Hardware", "Processor model, cores and clock", False,
         lambda: probe_cpu(env, facts)),
        ("PF-29", "Hardware", "Graphics adapter and driver age", False,
         lambda: probe_gpu(env, facts)),
        ("PF-30", "Storage", "Data folder weight: geodatabases, rasters, "
                              "file count", False,
         lambda: probe_data_weight(env, facts)),
    ]


def run_checks(env, opts):
    """Run every check under containment. Returns (checks, facts)."""
    checks = []
    facts = {}
    for cid, category, title, dataloss, probe in registrations(env, facts,
                                                               opts):
        def body(target, probe=probe):
            add_check(target, **probe())
        safe_check(checks, cid, category, title, body, dataloss=dataloss)
    for check in checks:
        # Attached after the fact rather than passed through make_check: the
        # source belongs to the check's id, not to what the machine said, so
        # a check that could not be measured still carries its remediation
        # references.
        found = SOURCES.get(check.get("id"))
        if found:
            check["sources"] = [list(pair) for pair in found]
        enrich_check(check)
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
                           "the editor's. Re-run it in her session")
    return True, ""


def fix_cache_aside(env, checks, facts):
    """Rename the ESRI local cache folder aside so Pro rebuilds it."""
    path = facts.get("cache_path")
    if not path or not env.isdir(path):
        return None
    moved = "%s.properf-%s" % (
        path, time.strftime("%Y%m%d%H%M%S", time.gmtime(env.now())))
    moved = unique_path(moved)
    env.rename(path, moved)
    src, dst = quote_cmd(moved), quote_cmd(path)
    undo = ('move %s %s' % (src, dst) if src and dst else
            "the folder was moved to %s; move it back by hand, because that "
            "path cannot be quoted safely on a command line" % moved)
    return {"id": "AP-01",
            "what": "Renamed the ESRI local cache folder aside. Pro rebuilds "
                    "it on the next launch.",
            "result": "%s -> %s" % (path, moved),
            "undo": undo,
            "when": stamp(env.now())}


# (id, the check it acts on, is it per-user, extra gate, handler)
# AP-02 was retired: forcing ArcGIS Pro onto another GPU can turn a driver
# problem into a crash before any project opens. Historical reports still
# contain their exact undo command; new runs only diagnose PF-11.
FIXES = (
    ("AP-01", "PF-06", True, "DL-12", fix_cache_aside),
)


def apply_fixes(checks, env, facts, do_apply):
    """Returns (applied, notes). With do_apply false, applied is always empty.

    The gate is evaluated either way, so a check-only run still prints exactly
    which fixes would have run and which were refused, and why.
    """
    applied = []
    notes = []
    for cid, target, per_user, blocker, handler in FIXES:
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
    applied, notes = apply_fixes(checks, env, facts, opts.get("apply", False))
    return {
        "report_version": REPORT_VERSION,
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
# SELECTED-PROJECT RECOVERY AND RUN
# =============================================================================


def project_recovery_problem(mode, checks, info):
    """Why the requested recovery copy must not be produced, or None."""
    if mode not in PROJECT_REPAIR_MODES:
        return "mode de récupération inconnu: %s" % mode
    if mode == "none":
        return "aucune récupération demandée"
    integrity = by_id(checks, "PR-01")
    if integrity is None or integrity["status"] != "Pass":
        return ("PR-01 n'est pas Conforme : tous les éléments du fichier "
                "doivent être lus et vérifiés avant toute copie reconstruite")
    if mode == "repack":
        return None
    structure = by_id(checks, "PR-02")
    if structure is None or structure["status"] != "Pass":
        return ("PR-02 n'est pas Conforme : une copie modifiant le JSON serait "
                "risquée tant que tous les documents ne sont pas valides")
    doc = info.get("main")
    if not isinstance(doc, dict):
        return "GISProject.json valide est indispensable à cette récupération"
    if not isinstance(doc.get("views", []), list):
        return "la propriété views n'a pas le schéma attendu"
    if not isinstance(doc.get("projectItems", []), list):
        return "la propriété projectItems n'a pas le schéma attendu"
    history_check = by_id(checks, "PR-06")
    if history_check is not None and history_check["status"] not in ("Pass", "Warn"):
        return "l'historique n'a pas pu être mesuré et sérialisé sans risque"
    views = doc.get("views", [])
    has_dock = bool(doc.get("viewLayoutXML"))
    history = [item for item in doc.get("projectItems", [])
               if isinstance(item, dict) and item.get("itemType") == "GPHistory"]
    if mode == "views" and not views and not has_dock:
        return "le projet n'enregistre aucune vue ni état de volet à retirer"
    if mode == "views_history" and not views and not has_dock and not history:
        return "le projet n'a ni vue au démarrage ni historique à retirer"
    return None


def create_aprx_recovery(snapshot, source, mode, now=None):
    """Write and verify a recovery copy beside source; never modify source.

    repack changes only the ZIP container. views removes `views` and
    `viewLayoutXML`, not the map/layout definitions. views_history additionally
    removes projectItems whose itemType is GPHistory. Every other ZIP member is
    streamed unchanged. Verification happens on the temporary output before it
    receives its final name.
    """
    import shutil
    import tempfile
    import zipfile

    if mode not in ("repack", "views", "views_history"):
        raise ValueError("unsupported project recovery mode: %s" % mode)
    folder = os.path.dirname(os.path.abspath(source))
    stem = os.path.splitext(os.path.basename(source))[0]
    suffix = {"repack": "_reconstruit",
              "views": "_recuperation_sans_vues",
              "views_history": "_recuperation_sans_vues_historique"}[mode]
    output = unique_path(os.path.join(folder, stem + suffix + ".aprx"))
    handle, temporary = tempfile.mkstemp(prefix=".properf_recovery_",
                                         suffix=".tmp", dir=folder)
    os.close(handle)
    removed_views = 0
    removed_history = 0
    try:
        with zipfile.ZipFile(snapshot, "r") as src:
            with zipfile.ZipFile(temporary, "w", allowZip64=True) as dst:
                dst.comment = src.comment
                for info in src.infolist():
                    payload = None
                    if mode != "repack" \
                            and info.filename.lower() == "gisproject.json":
                        doc = json.loads(src.read(info).decode("utf-8-sig"))
                        removed_views = len(doc.get("views", []))
                        doc.pop("views", None)
                        doc.pop("viewLayoutXML", None)
                        if mode == "views_history":
                            items = doc.get("projectItems", [])
                            kept = []
                            for item in items:
                                if isinstance(item, dict) \
                                        and item.get("itemType") == "GPHistory":
                                    removed_history += 1
                                else:
                                    kept.append(item)
                            doc["projectItems"] = kept
                        payload = json.dumps(doc, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8")
                    if payload is not None:
                        dst.writestr(info, payload)
                    elif info.filename.endswith("/"):
                        dst.writestr(info, b"")
                    else:
                        # Passing the original ZipInfo preserves timestamps,
                        # attributes and compression while ZipFile computes a
                        # fresh CRC and sizes for the output stream.
                        with src.open(info, "r") as reader:
                            with dst.open(info, "w") as writer:
                                shutil.copyfileobj(reader, writer, 1024 * 1024)

        verified = inspect_aprx_archive(temporary, output)
        if verified["integrity"] != "Pass":
            raise ValueError("la copie produite ne passe pas le contrôle "
                             "d'intégrité: %s" % verified["integrity_reason"])
        if mode != "repack":
            if verified["integrity"] != "Pass" or not verified.get("main"):
                raise ValueError("GISProject.json n'est pas valide dans la "
                                 "copie produite")
            if verified.get("json_errors") or verified.get("json_capped"):
                raise ValueError("tous les documents JSON de la copie n'ont "
                                 "pas pu être revérifiés")
            doc = verified["main"]
            if "views" in doc or "viewLayoutXML" in doc:
                raise ValueError("l'état des vues persiste dans la copie")
            if mode == "views_history" and any(
                    isinstance(item, dict) and item.get("itemType") == "GPHistory"
                    for item in doc.get("projectItems", [])):
                raise ValueError("des éléments GPHistory persistent dans la copie")
        os.rename(temporary, output)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)

    if mode == "repack":
        action = ("conteneur ZIP reconstruit; tous les documents internes "
                  "sont restés inchangés")
    else:
        action = ("%d vue(s) et l'état des volets retirés de la copie"
                  % removed_views)
        if mode == "views_history":
            action += "; %d élément(s) d'historique retirés" % removed_history
    return {
        "id": "APRX-REC-01",
        "what": "Copie de récupération du projet créée sans modifier l'original.",
        "result": "%s\nOriginal conservé : %s\nCopie vérifiée : %s"
                  % (action, source, output),
        "undo": ("Aucune restauration n'est nécessaire : l'original n'a pas "
                 "été modifié. Revenez à %s ; ne supprimez la copie qu'après "
                 "avoir vérifié que vous n'en avez plus besoin." % source),
        "when": stamp(now if now is not None else time.time()),
        "path": output,
        "mode": mode,
    }


def run_project(env, opts):
    """Analyse one explicitly selected .aprx, optionally writing a safe copy."""
    selected = opts.get("project_path") or opts.get("project")
    mode = opts.get("repair", "none")
    snapshot, info = prepare_project_snapshot(selected)
    path = selected or "<aucun projet sélectionné>"
    info["path"] = path
    checks = []
    applied = []
    notes = []
    recovery = {"requested": mode, "source": path, "path": None}
    try:
        for record in (_integrity_project_check(info),
                       _structure_project_check(info)):
            add_check(checks, **record)

        projects = []
        if info.get("texts"):
            try:
                found = scan_aprx(path, info["texts"], onedrive_roots(env),
                                  env.network_drives())
                found["truncated"] = bool(info.get("json_capped")
                                          or info.get("json_errors")
                                          or found.get("capped"))
                if info.get("json_errors"):
                    found["error"] = "; ".join(info["json_errors"][:20])
                projects = [found]
            except Exception as exc:
                # Keep the integrity findings and turn the content walk into an
                # honest undetermined record rather than aborting the report.
                projects = []
                info["json_errors"].append("parcours du projet: %s: %s"
                                           % (type(exc).__name__, exc))

        add_check(checks, **_storage_project_check(env, path, projects))
        add_check(checks, **_sources_project_check(env, path, projects))
        add_check(checks, **_views_project_check(info))
        add_check(checks, **_history_project_check(info))

        sizes = [(path, info["size"])] if info.get("size") is not None else []
        decisions = [
            ("PF-10", "Project weight", lambda: decide_project_weight(projects, sizes)),
            ("PF-22", "Refresh timers and default geodatabase",
             lambda: decide_aprx("closed", projects)),
            ("PF-09", "Layers served over the network",
             lambda: decide_http_layers(projects)),
            ("PF-01", "Coordinate systems in the project",
             lambda: decide_projection(projects)),
            ("PF-14", "Per-layer draw load", lambda: decide_layer_load(projects)),
        ]
        for cid, title, decision in decisions:
            def body(target, decision=decision):
                add_check(target, **decision())
            safe_check(checks, cid, "Projet APRX", title, body)

        if mode == "none":
            notes.append("Diagnostic seul : le fichier source n'a été ni "
                         "renommé ni réécrit. Une copie de récupération peut "
                         "être demandée explicitement.")
        else:
            problem = project_recovery_problem(mode, checks, info)
            if problem:
                notes.append("RÉCUPÉRATION REFUSÉE : %s" % problem)
                recovery["status"] = "refused"
                recovery["reason"] = problem
            elif not snapshot:
                problem = "la copie instantanée locale n'est pas disponible"
                notes.append("RÉCUPÉRATION REFUSÉE : %s" % problem)
                recovery["status"] = "refused"
                recovery["reason"] = problem
            else:
                try:
                    record = create_aprx_recovery(snapshot, path, mode, env.now())
                except Exception as exc:
                    problem = "%s: %s" % (type(exc).__name__, exc)
                    notes.append("ÉCHEC DE LA RÉCUPÉRATION : %s. L'original "
                                 "n'a pas été modifié." % problem)
                    recovery["status"] = "failed"
                    recovery["reason"] = problem
                else:
                    applied.append(record)
                    recovery["status"] = "created"
                    recovery["path"] = record["path"]
                    notes.append("Récupération créée et revérifiée : %s"
                                 % record["path"])
        return _project_document(env, path, checks, applied, notes, recovery)
    finally:
        if snapshot and os.path.exists(snapshot):
            os.unlink(snapshot)


# =============================================================================
# REPORTING
# =============================================================================

def render(document, verbose=False):
    """The console and text report, as a list of lines."""
    is_project = document.get("analysis_type") == "project"
    lines = ["ArcGIS Pro Performance Analyser %d  -  %s"
             % (REPORT_VERSION, "selected .aprx project diagnostic" if is_project
                else "workstation diagnostic"),
             "taken %s UTC as %s (elevated: %s)"
             % (document["taken"], document["user"], document["elevated"]),
             ""]
    if document.get("project"):
        lines.append("Selected project: %s" % document["project"])
        lines.append("The source was analysed through a snapshot; it was not "
                     "rewritten by the diagnostic.")
        lines.append("")
    lines.append("Symptom key used in the impact lines:")
    for code, text in SYMPTOMS:
        lines.append("  %s  %s" % (code, text))
    for heading, wanted in (("DATA LOSS", True),
                            ("PROJECT" if is_project else "PERFORMANCE", False)):
        lines.append("")
        lines.append("--- %s %s" % (heading, "-" * (62 - len(heading))))
        for check in document["checks"]:
            if bool(check["dataloss"]) is not wanted:
                continue
            lines.extend(render_check(check, verbose))
    lines.append("")
    lines.append("--- SUMMARY " + "-" * 62)
    lines.append("  " + "   ".join(["%s %d" % (name.upper(), n)
                                    for name, n in counts(document["checks"])]))
    if document["fixable"]:
        lines.append("  %d finding(s) have a fix here. Re-run with --apply:"
                     % len(document["fixable"]))
        for cid in document["fixable"]:
            check = by_id(document["checks"], cid)
            lines.append("     %s  %s" % (cid, check["title"]))
    if document["act_first"]:
        lines.append("")
        lines.append("  ACT ON THESE FIRST (they bear on the vanished edits):")
        for cid in document["act_first"]:
            check = by_id(document["checks"], cid)
            lines.append("     %s  %s" % (cid, check["title"]))
    if document["notes"]:
        lines.append("")
        lines.append("--- FIXES " + "-" * 64)
        for note in document["notes"]:
            lines.append("  " + note)
    for record in document["applied"]:
        lines.append("  APPLIED [%s] %s" % (record["id"], record["what"]))
        lines.append("           result : %s" % record["result"])
        lines.append("           undo   : %s" % record["undo"])
    return lines


def render_check(check, verbose=False):
    """One check, as the report prints it."""
    lines = ["%s [%-7s] %-6s %s"
             % ("DATA" if check["dataloss"] else "    ",
                check["status"].upper(), check["id"], check["title"])]
    for line in check["observed"].split("\n"):
        if line.strip():
            lines.append("          " + line)
    if check["status"] in ("Warn", "Fail", "Info", "Skipped") or verbose:
        if check.get("explanation"):
            lines.append("          pourquoi : %s" % check["explanation"])
        if check.get("impact_fr"):
            lines.append("          impact : %s" % check["impact_fr"])
        elif check["impact"]:
            lines.append("          impact : %s" % check["impact"])
        for number, step in enumerate(check.get("steps") or [], 1):
            lines.append("          action %d : %s" % (number, step))
        if check.get("verification") and check["status"] in ("Warn", "Fail"):
            lines.append("          vérification : %s" % check["verification"])
        if check["fix"] and not check.get("steps"):
            lines.append("          fix    : %s" % check["fix"])
    if verbose:
        if check["expected"]:
            lines.append("          expect : %s" % check["expected"])
        for line in check["detail"].split("\n"):
            if line.strip():
                lines.append("          | " + line)
    return lines


def unique_path(path):
    """A path that does not exist yet, so a write never overwrites a report.

    The stamp is to the second. Two runs inside one second - a wrapper in a
    loop, a diagnostic fired twice from one script - used to land on the same
    name, and the second silently replaced the first. A report that vanished
    between being written and being read is the same failure as a check that
    reports something it never measured, only later.
    """
    root, ext = os.path.splitext(path)
    counter = 1
    while os.path.exists(path):
        path = "%s_%d%s" % (root, counter, ext)
        counter += 1
    return path


def quote_cmd(value):
    """One argument quoted for a Windows command line, or None.

    The undo commands this tool prints are meant to be pasted into cmd.exe.
    None here means the value cannot be represented there safely, and the
    caller then prints the manual route instead of a command that does
    something other than what it appears to do.

    A double quote is the case that matters: cmd strips one level of quoting
    and the program's argument parser strips another, so an embedded quote
    closes the argument early and the rest of the path becomes new arguments.
    `&`, `|`, `<`, `>`, `^` and `%` are command separators and expansions
    outside quotes, and `!` is one under delayed expansion. Refusing beats
    guessing which parser the reader is using.
    """
    if value is None:
        return None
    if re.search(r"[\"&|<>^%!]", value):
        return None
    return '"%s"' % value


def write_report(document, directory, verbose=False):
    """Write the text and JSON reports. Returns the two paths."""
    if not os.path.isdir(directory):
        os.makedirs(directory)
    prefix = ("arcgis_pro_project_analysis"
              if document.get("analysis_type") == "project"
              else "arcgis_pro_performance_analyser")
    base = "%s_%s" % (prefix, time.strftime(
        "%Y%m%d_%H%M%S", time.gmtime(time.time())))
    text_path = unique_path(os.path.join(directory, base + ".txt"))
    json_path = unique_path(os.path.join(directory, base + ".json"))
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
    return text_path, json_path


def write_html_report(document, json_path, verbose=False):
    """Write HTML using the inline toolbox renderer or the CLI sibling.

    The generated .pyt already owns ``write_html``. Resolving that global first
    keeps its single-file promise; the stand-alone CLI loads html_report.py only
    when --html was explicitly requested.
    """
    writer = globals().get("write_html")
    if writer is None:
        writer = __import__("html_report", fromlist=["write_html"]).write_html
    path = unique_path(os.path.splitext(json_path)[0] + ".html")
    return writer(document, path, verbose=verbose,
                  exit_code=exit_code(document["checks"]))


# =============================================================================
# EXPLICIT ADD-IN QUARANTINE. Presence is never enough to select a package.
# =============================================================================

ADDIN_QUARANTINE_SCHEMA = "arcgis-pro-addin-quarantine"
ADDIN_MANIFEST_MAX_BYTES = 1024 ** 2


class AddinRemediationError(Exception):
    """A refused or incomplete add-in action, with no success claim."""


def _addin_pro_closed(env):
    processes = env.processes("ArcGISPro.exe")
    if processes is None:
        raise AddinRemediationError(
            "Whether ArcGIS Pro is running could not be determined; no "
            "add-in was changed. Close Pro and retry when tasklist works.")
    if processes:
        raise AddinRemediationError(
            "ArcGIS Pro is running; no add-in was changed. Save work and "
            "close every Pro session before quarantine or restore.")


def _addin_path_within(path, root):
    """Lexical containment for either a Windows path or a test's POSIX path."""
    windows = bool(ntpath.splitdrive(path or "")[0] or "\\" in (path or "")
                   or ntpath.splitdrive(root or "")[0] or "\\" in (root or ""))
    module = ntpath if windows else os.path
    if not path or not root:
        return False
    candidate = module.normcase(module.abspath(path))
    parent = module.normcase(module.abspath(root))
    try:
        return module.commonpath([candidate, parent]) == parent
    except (ValueError, OSError):
        return False


def _addin_fingerprint(path):
    import hashlib
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 ** 2)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return {"size": size, "sha256": digest.hexdigest()}


def _addin_copy_verified(source, destination_dir):
    """Copy to a temporary sibling and prove source stability and hash equality."""
    import shutil
    before = _addin_fingerprint(source)
    temp = unique_path(os.path.join(
        destination_dir, ".%s.properf-partial" % os.path.basename(source)))
    try:
        shutil.copyfile(source, temp)
        after = _addin_fingerprint(source)
        copied = _addin_fingerprint(temp)
        if before != after:
            raise AddinRemediationError(
                "The package changed while it was being copied; the source "
                "was left in place and the copy was not published.")
        if copied != before:
            raise AddinRemediationError(
                "The copied package failed SHA-256/size verification; the "
                "source was left in place.")
        return temp, before
    except Exception:
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass
        raise


def _addin_publish_noclobber(temp, destination):
    """Publish in one directory without replacing a file that already exists."""
    if os.path.exists(destination):
        raise AddinRemediationError(
            "Destination already exists; refusing to overwrite it: %s"
            % destination)
    try:
        if os.name == "nt":
            # Windows rename fails when the destination exists.
            os.rename(temp, destination)
        else:
            # Hard-link publication is atomic and no-clobber on the test hosts.
            os.link(temp, destination)
            os.remove(temp)
    except FileExistsError:
        raise AddinRemediationError(
            "Destination appeared during the operation; nothing was "
            "overwritten: %s" % destination)


def _addin_write_manifest(path, document):
    temp = unique_path(path + ".partial")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=1, sort_keys=True)
            handle.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def _addin_restore_command(manifest_path):
    executable = quote_cmd(sys.executable)
    script = quote_cmd(os.path.abspath(__file__))
    manifest = quote_cmd(manifest_path)
    if not executable or not script or not manifest:
        return None
    return "%s %s --restore-addin %s" % (executable, script, manifest)


def quarantine_addin(env, package_path, quarantine_dir=None,
                     confirm_machine_wide=False):
    """Move one explicitly selected discovered package into verified quarantine.

    This is deliberately not an automatic fix: the operator supplies the exact
    path from PF-31. The package remains recoverable and its bytes are hashed.
    """
    if not env.windows or not env.registry_readable():
        raise AddinRemediationError(
            "Add-in quarantine is supported only on the affected Windows "
            "workstation. No file was changed.")
    _addin_pro_closed(env)
    records, coverage, locations = discover_startup_packages(env)
    wanted = _norm(ntpath.normpath(package_path or ""))
    record = next((row for row in records
                   if _norm(ntpath.normpath(row.get("path") or "")) == wanted),
                  None)
    if record is None:
        raise AddinRemediationError(
            "The exact package was not found by the current PF-31 discovery. "
            "Nothing was moved. Run the diagnostic and copy a package path "
            "from PF-31; arbitrary files are not accepted.")
    if ("administrator" in (record.get("source") or "").lower()
            and not confirm_machine_wide):
        raise AddinRemediationError(
            "This is a machine-wide/administrator package and changing it can "
            "affect every user. Nothing was moved. Re-run with "
            "--confirm-machine-wide only after administrator approval.")
    source = record["path"]
    if not os.path.isfile(source):
        raise AddinRemediationError(
            "The discovered package is no longer a readable file: %s" % source)
    if not quarantine_dir:
        local = env.getenv("LOCALAPPDATA")
        if not local:
            raise AddinRemediationError(
                "LOCALAPPDATA is unavailable; specify --quarantine-dir with "
                "a local folder outside every add-in location.")
        quarantine_dir = os.path.join(
            local, "ArcGISPro_PerformanceAnalyser", "Quarantine")
    quarantine_dir = os.path.abspath(quarantine_dir)
    for _scope, folder, _registered in locations:
        if _addin_path_within(quarantine_dir, folder):
            raise AddinRemediationError(
                "Quarantine must be outside every startup-loading folder; "
                "%s is inside %s. Nothing was moved." % (quarantine_dir, folder))

    import shutil
    import tempfile
    os.makedirs(quarantine_dir, exist_ok=True)
    transaction = tempfile.mkdtemp(prefix="quarantine-", dir=quarantine_dir)
    quarantined = os.path.join(transaction, os.path.basename(source))
    manifest_path = os.path.join(transaction, "quarantine.json")
    try:
        temp, fingerprint = _addin_copy_verified(source, transaction)
        os.replace(temp, quarantined)
        taken = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))
        installed_pro = (env.reg(PRO_REG_KEY, "RealVersion")
                         or env.reg(PRO_REG_KEY, "Version"))
        manifest = {
            "schema": ADDIN_QUARANTINE_SCHEMA, "schema_version": 1,
            "status": "prepared", "taken": taken, "user": env.username(),
            "original_path": source, "quarantined_path": quarantined,
            "size": fingerprint["size"], "sha256": fingerprint["sha256"],
            "installed_pro": installed_pro,
            "package": record, "coverage": coverage,
            "selection": "Exact operator-selected path; presence is not a "
                         "causation finding.",
            "indicators": list(record.get("warnings") or []),
            "restore_command": _addin_restore_command(manifest_path),
        }
        _addin_write_manifest(manifest_path, manifest)
    except Exception:
        # The source is still present at this point, so the incomplete copy is
        # safe to remove. No user package is deleted by this cleanup.
        shutil.rmtree(transaction, ignore_errors=True)
        raise

    try:
        os.remove(source)
    except Exception as exc:
        manifest["status"] = "copy_only_source_present"
        manifest["error"] = "%s: %s" % (type(exc).__name__, exc)
        _addin_write_manifest(manifest_path, manifest)
        raise AddinRemediationError(
            "The source could not be removed, so the add-in was NOT "
            "quarantined. The original remains in place and a verified copy "
            "is recorded in %s (%s)" % (manifest_path, exc))

    warning = None
    manifest["status"] = "quarantined"
    manifest["quarantined_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))
    try:
        _addin_write_manifest(manifest_path, manifest)
    except (OSError, IOError) as exc:
        # The prepared manifest already contains every restore field. Report
        # the partial audit failure without pretending the move was rolled back.
        warning = ("Package moved, but the final manifest update failed: %s. "
                   "The prepared manifest is still usable for restore." % exc)
    return {
        "action": "quarantine", "status": "quarantined",
        "original_path": source, "quarantined_path": quarantined,
        "manifest": manifest_path,
        "restore_command": _addin_restore_command(manifest_path),
        "indicators": list(record.get("warnings") or []), "warning": warning,
    }


def _restore_addin_manifest(manifest_path):
    """Restore from a bounded, validated manifest without overwriting anything."""
    if not os.path.isfile(manifest_path):
        raise AddinRemediationError("Manifest not found: %s" % manifest_path)
    if os.path.getsize(manifest_path) > ADDIN_MANIFEST_MAX_BYTES:
        raise AddinRemediationError("Manifest exceeds the 1 MB safety cap.")
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        raise AddinRemediationError("Manifest is unreadable: %s" % exc)
    if not isinstance(manifest, dict):
        raise AddinRemediationError("Manifest root must be a JSON object.")
    if (manifest.get("schema") != ADDIN_QUARANTINE_SCHEMA
            or manifest.get("schema_version") != 1):
        raise AddinRemediationError("Not a supported add-in quarantine manifest.")
    if manifest.get("status") not in ("prepared", "quarantined"):
        raise AddinRemediationError(
            "Manifest status %r is not restorable (already restored or "
            "incomplete)." % manifest.get("status"))
    original = manifest.get("original_path")
    quarantined = manifest.get("quarantined_path")
    sha256 = manifest.get("sha256")
    size = manifest.get("size")
    if not all(isinstance(value, str) and value
               for value in (original, quarantined, sha256)):
        raise AddinRemediationError("Manifest paths or SHA-256 are missing.")
    if (not os.path.isabs(original) or not os.path.isabs(quarantined)
            or os.path.splitext(original)[1].lower()
            not in ADDIN_PACKAGE_EXTENSIONS):
        raise AddinRemediationError("Manifest contains an invalid package path.")
    if not re.match(r"^[0-9a-f]{64}$", sha256) or isinstance(size, bool) \
            or not isinstance(size, int) or size < 0:
        raise AddinRemediationError("Manifest integrity fields are invalid.")
    manifest_dir = os.path.realpath(os.path.dirname(os.path.abspath(manifest_path)))
    quarantined = os.path.realpath(quarantined)
    if not _addin_path_within(quarantined, manifest_dir):
        raise AddinRemediationError(
            "The quarantined package must be inside the manifest's own "
            "transaction folder; an external source is refused.")
    if os.path.basename(quarantined) != os.path.basename(original):
        raise AddinRemediationError("Manifest package names do not match.")
    if not os.path.isfile(quarantined):
        raise AddinRemediationError("Quarantined package is missing: %s" % quarantined)
    if os.path.exists(original):
        raise AddinRemediationError(
            "The original path already exists; refusing to overwrite it: %s"
            % original)
    parent = os.path.dirname(original)
    if not os.path.isdir(parent):
        raise AddinRemediationError(
            "The original parent folder is absent. No folder was invented; "
            "restore it first or copy the package manually: %s" % parent)
    actual = _addin_fingerprint(quarantined)
    if actual != {"size": size, "sha256": sha256}:
        raise AddinRemediationError(
            "Quarantined package no longer matches the manifest SHA-256/size; "
            "restore was refused.")
    temp = None
    try:
        temp, _fingerprint = _addin_copy_verified(quarantined, parent)
        _addin_publish_noclobber(temp, original)
        temp = None
    finally:
        if temp and os.path.exists(temp):
            os.remove(temp)

    warning = None
    manifest["status"] = "restored"
    manifest["restored_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))
    manifest["restored_path"] = original
    try:
        _addin_write_manifest(manifest_path, manifest)
    except (OSError, IOError) as exc:
        warning = ("Package restored, but the manifest status update failed: "
                   "%s. The original is present and the backup was retained."
                   % exc)
    return {
        "action": "restore", "status": "restored", "original_path": original,
        "quarantined_path": quarantined, "manifest": manifest_path,
        "warning": warning,
    }


def restore_addin(env, manifest_path):
    if not env.windows:
        raise AddinRemediationError(
            "Add-in restore is supported only on the affected Windows "
            "workstation. No file was changed.")
    _addin_pro_closed(env)
    return _restore_addin_manifest(manifest_path)


def print_addin_action(result):
    if result["action"] == "quarantine":
        print("ADD-IN QUARANTINED (removed from startup loading; not erased)")
    else:
        print("ADD-IN RESTORED (the verified quarantine backup was retained)")
    print("  original   : %s" % result["original_path"])
    print("  backup     : %s" % result["quarantined_path"])
    print("  manifest   : %s" % result["manifest"])
    for indicator in result.get("indicators") or []:
        print("  indicator  : %s" % indicator)
    if result.get("restore_command"):
        print("  undo       : %s" % result["restore_command"])
    if result.get("warning"):
        print("  WARNING    : %s" % result["warning"])
    print("  next       : close Pro, then start it normally and compare the "
          "result. A change supports isolation evidence; it does not prove "
          "that this package was the only cause.")


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
                 events=None, listing=None, zips=None, netdrives=(), packages=None):
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
        self._packages = dict([(_norm(k), v) for k, v in (packages or {}).items()])
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

    def reg_values(self, path, limit=256):
        values = self._reg.get(_norm(path))
        if values is None:
            return {}, None
        items = list(values.items())
        out = dict(items[:limit])
        if len(items) > limit:
            return out, "value cap %d reached; %d values were not read" % (
                limit, len(items) - limit)
        return out, None

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

    def read_text(self, path, limit=MAX_TEXT_BYTES):
        text = self._text.get(_norm(path))
        if text is None:
            return None, "absent on the stand-in machine"
        if len(text) > limit:
            return None, ("larger than the %s this tool will read into memory"
                          % format_bytes(limit))
        return text, None

    def rename(self, src, dst):
        key = _norm(src)
        self._tree[_norm(dst)] = (dst, self._tree.pop(key)[1])
        self.written.append(("rename", src, dst))

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

    def disk_media(self):
        # Through the same conversion the real reader uses. A stand-in that
        # handed back the raw PowerShell shape would answer differently from
        # the machine, and the check would be testing the difference.
        data, error = self._answer("disk_media")
        return _ps_rows(data, "name", "media"), error

    def link_speed(self):
        data, error = self._answer("link_speed")
        return _ps_rows(data, "name", "speed"), error

    def cpu(self):
        return self._answer("cpu")

    def gpu(self):
        return self._answer("gpu")


    def events(self, log, query, count):
        return self._events.get(log, (None, "no such log on the stand-in "
                                            "machine"))

    def time_listdir(self, path, limit=SHARE_LIST_LIMIT):
        return self._listing.get(_norm(path),
                                 (None, 0, "not reachable on the stand-in "
                                           "machine"))

    def startup_package(self, path, full_crc=False):
        found = self._packages.get(_norm(path))
        if found is None:
            return {"members": [], "config": None, "config_error": None,
                    "crc": "failed or incomplete",
                    "error": "package is absent from the stand-in machine"}
        return dict(found)

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


def aprx_texts_heavy(home, gdb, rate):
    """A project with the weight and the network draws a slow one has.

    Everything the newer checks read is in here: a map coordinate system that
    three layers disagree with, three layers served by URL, a group level, a
    three-way join, a definition query and a label engine. It is a fixture,
    not a transcription of a real project file.
    """
    def service(name):
        return ("https://services.example.org/arcgis/rest/services/%s/"
                "MapServer" % name)

    def gdb_conn(dataset):
        return {"workspaceConnectionString": r"DATABASE=..\g.gdb",
                "dataset": dataset}

    basemaps = [{"type": "CIMFeatureLayer", "name": name,
                 "spatialReference": {"wkid": 3857},
                 "serviceConnectionProperties": {"url": service(name)}}
                for name in ("World_Imagery", "World_Topo", "World_Streets")]
    return [
        ("GISProject.json",
         json.dumps({"type": "CIMGISProject", "defaultFolder": home,
                     "defaultGeoDatabase": gdb})),
        ("Map/map.json", json.dumps({
            "type": "CIMMapDocument",
            "map": {
                "type": "CIMMap",
                "name": "Map",
                "spatialReference": {"wkid": 4326},
                "generalPlacementProperties":
                    {"type": "CIMMaplexLabelPlacementProperties"},
                "layers": [
                    {"type": "CIMGroupLayer", "name": "Basemaps",
                     "layers": basemaps},
                    {"type": "CIMFeatureLayer", "name": "Hydrants",
                     "spatialReference": {"wkid": 4326},
                     "refreshRate": rate,
                     "refreshRateUnit": "esriTimeUnitsSeconds",
                     "minScale": 50000,
                     "labelClasses": [{"expression": "[NAME]"}],
                     "featureTable": {
                         "joins": [{"type": "CIMRelationshipJoin"},
                                   {"type": "CIMRelationshipJoin"},
                                   {"type": "CIMRelationshipJoin"}],
                         "definitionExpression": "STATUS = 'Active'",
                         "dataConnection": gdb_conn("Hydrants")}},
                    {"type": "CIMFeatureLayer", "name": "Streets",
                     "spatialReference": {"wkid": 4326},
                     "refreshRate": 0,
                     "definitionQuery": "CLASS = 1",
                     "featureTable": {"dataConnection":
                                          gdb_conn("Streets")}}]}})),
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
             r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager"
             r"\Memory Management": {"PagingFiles": ["C:\\pagefile.sys 16384 32768"]},
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
            "disk_media": ([{"name": "SAMSUNG SSD 980 PRO", "media": "SSD"}],
                           None),
            "link_speed": ([{"name": "Ethernet", "speed": "1 Gbps"}], None),
            "cpu": ([{"name": "Intel Core i7-11800H", "cores": 8,
                      "logical": 16, "mhz": 2304}], None),
            "gpu": ([{"name": "NVIDIA GeForce RTX 3060",
                      "driver": "31.0.15.3623", "date": "2023-10-01"}],
                    None),
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
             r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager"
             r"\Memory Management": {"PagingFiles": ["Z:\\pagefile.sys 2048 4096"]},
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
                          1, FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS, True),
                         # one raster with an overview beside it, one without
                         ("ortho.tif", 900 * 1024 ** 2, 40, 0, False),
                         ("dem.tif", 400 * 1024 ** 2, 40, 0, False),
                         ("dem.ovr", 90 * 1024 ** 2, 40, 0, False)],
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
            "disk_media": ([{"name": "SAMSUNG SSD 870", "media": "SSD"},
                            {"name": "TOSHIBA MQ01ABD100", "media": "HDD"}],
                           None),
            "link_speed": ([{"name": "Wi-Fi", "speed": "866 Mbps"},
                            {"name": "Ethernet 2", "speed": "100 Mbps"}],
                           None),
            "cpu": ([{"name": "Intel Core i5-8250U", "cores": 2,
                      "logical": 2, "mhz": 1608}], None),
            "gpu": ([{"name": "Intel UHD Graphics 620",
                      "driver": "27.20.100.8681", "date": "2022-01-10"}],
                    None),
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
                  (aprx_texts_heavy(".", "Addresses.gdb", 30), False)})


def fake_bare():
    """A machine where nothing can be read. Every check must stay undetermined."""
    return FakeEnv(envvars={}, tree={})


DEFAULT_OPTS = {"sde_server": None, "sde_port": 1433, "portal": None,
                "timeout": 4000, "apply": False}


def opts_with(**kwargs):
    merged = dict(DEFAULT_OPTS)
    merged.update(kwargs)
    return merged


def summarize(passed, failed, skipped=0):
    """The self-test footer as (lines, exit code).

    Skipped assertions are counted in neither number and are named in the
    footer, because an assertion that could not run is not one that passed.
    """
    total = passed + len(failed)
    tail = "" if not skipped else ", %d skipped" % skipped
    if failed:
        return (["%d assertions, %d failed%s" % (total, len(failed), tail)]
                + ["  FAILED: %s" % item for item in failed]), 1
    return ["%d assertions, 0 failed%s" % (total, tail)], 0


def self_test():
    """Assertions over the whole diagnostic. No registry, no socket, no disk."""
    import io
    import shutil
    import tempfile

    passed = [0]
    failed = []
    not_run = [0]   # not 'skipped': that name is rebound below to a record

    def check(cond, label):
        if cond:
            passed[0] += 1
            print("PASS  %s" % label)
        else:
            failed.append(label)
            print("FAIL  %s" % label)

    def skip(label, reason):
        """An assertion this host cannot run, said out loud.

        Not a pass: the count stays honest, and the reason is printed so a
        reader can tell a host that cannot run it from a host that did not.
        """
        not_run[0] += 1
        print("SKIP  %s -- %s" % (label, reason))

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
    check("kpi" not in make_check(**good),
          "a check that measured no number carries no kpi key at all, rather "
          "than an empty one a gauge would draw as zero")
    check(make_check(**dict(good, kpi={"free_gb": 12.5}))["kpi"]
          == {"free_gb": 12.5},
          "and one that did carries its numbers, which is what the report "
          "draws a gauge from instead of parsing a sentence")
    raises(lambda: make_check(**dict(good, kpi={"free_gb": "12 GB"})),
           "a kpi that is a string is refused: a gauge drawn off text parsed "
           "back out of a sentence is a number the report invented  "
           "<-- pinned defect")
    raises(lambda: make_check(**dict(good, kpi={"healthy": True})),
           "and so is a boolean, which is an int in Python and would draw as "
           "a gauge at 1  <-- pinned defect")
    raises(lambda: make_check(**dict(good, kpi=["free_gb"])),
           "and a kpi that is not a mapping")
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
          "and the impact line says plainly that this does not protect her "
          "feature edits, which is the whole reason the check is named that way")

    check(decide_storage([])["status"] == "Info",
          "no recent project: where she works is undetermined")
    local_only = decide_storage([(r"C:\GIS\a.aprx", "2026-01-01 00:00", "LOCAL")])
    check(local_only["status"] == "Pass", "every recent project local: Pass")
    check("every recent project is local" in local_only["title"],
          "and the title says so, instead of printing the accusation with a "
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
                                                   0)["fix"].lower(),
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
    check(make_check(**nogpu)["autofixable"] is False
          and all(row[0] != "AP-02" for row in FIXES),
          "the GPU preference is advice only: forcing another adapter can "
          "crash Pro at the splash screen, so AP-02 is retired")
    check("undo" in nogpu["fix"],
          "the guidance sends an affected user to the exact undo recorded by "
          "an older AP-02 report instead of guessing a registry value")
    check(decide_gpu_preference("e", "GpuPreference=2;")["status"] == "Pass",
          "high performance already pinned: Pass")
    check(decide_gpu_preference("e", "GpuPreference=1;")["status"] == "Warn",
          "power saving pinned warns")
    check(decide_gpu_preference("e", None, readable=False)["status"] == "Info",
          "but a registry that could not be read at all is Info: unreadable "
          "and absent are different answers, and only one of them is a "
          "finding  <-- pinned defect")

    daml = """<ArcGIS defaultAssembly='Vendor.dll'
      xmlns='http://schemas.esri.com/DADF/Registry'>
      <AddInInfo id='{abc}' version='1.2' desktopVersion='3.3.48105'>
        <Name>Vendor Tools</Name><Company>Vendor</Company>
      </AddInInfo><modules><insertModule id='M' className='Vendor.Startup'
        autoLoad='true' /></modules></ArcGIS>"""
    payload = {"members": ["Config.daml", "Vendor.dll", "Native.dll"],
               "config": daml, "crc": "passed for every archive member"}
    addin = parse_startup_package(r"C:\AddIns\Vendor.esriAddinX", "add-in", payload)
    check(addin["name"] == "Vendor Tools" and addin["desktop_version"]
          == "3.3.48105" and addin["startup_modules"] == ["Vendor.Startup"]
          and not addin["warnings"],
          "a real DAML-shaped add-in yields its identity, target and startup module")
    compatible = decide_addins([addin], [], "3.4.0")
    check(compatible["status"] == "Info" and compatible["kpi"]["issues"] == 0,
          "a compatible add-in is inventoried, not accused merely for being present")
    newer = dict(addin, desktop_version="3.5.0")
    check(decide_addins([newer], [], "3.4.0")["status"] == "Warn",
          "an add-in targeting a newer minor Pro release warns")
    old_major = dict(addin, desktop_version="2.9.0")
    check(decide_addins([old_major], [], "3.4.0")["status"] == "Warn",
          "a 2.x add-in on Pro 3.x is an established compatibility finding")
    missing_assembly = parse_startup_package(
        "x.esriAddinX", "add-in", dict(payload, members=["Config.daml"]))
    check(any("defaultAssembly" in warning for warning in
              missing_assembly["warnings"]),
          "a DAML declaring an assembly absent from the package is named")
    esri_copy = parse_startup_package(
        "x.esriAddinX", "add-in",
        dict(payload, members=["Config.daml", "Vendor.dll",
                               "ArcGIS.Desktop.Framework.dll"]))
    check(any("shadow" in warning for warning in esri_copy["warnings"]),
          "a bundled Pro API assembly is flagged as a startup-crash risk")
    correlated = decide_addins([addin], [], "3.4.0", ["native.dll"])
    check(correlated["status"] == "Warn"
          and correlated["kpi"]["crash_module_matches"] == 1
          and "native.dll" in "\n".join(correlated["observed"]),
          "Application Error's fault module is correlated with the exact package DLL")
    corrupt = parse_startup_package(
        "broken.esriAddinX", "add-in",
        {"error": "BadZipFile: central directory", "members": [], "crc": "failed"})
    check(decide_addins([corrupt], [], "3.4.0")["status"] == "Warn",
          "an unreadable add-in archive is a finding, not a clean package")
    doctype = parse_startup_package(
        "xml.esriAddinX", "add-in",
        dict(payload, config="<!DOCTYPE ArcGIS><ArcGIS />"))
    check(any("DOCTYPE" in warning for warning in doctype["warnings"]),
          "a DAML with a document type declaration is refused, not expanded")
    check(decide_addins([], [], "3.4.0")["status"] == "Pass",
          "no package in completely readable locations passes")
    incomplete = decide_addins([], ["registered folder is unreadable"], "3.4.0")
    check(incomplete["status"] == "Info" and incomplete["measured"] is False,
          "an incomplete add-in inventory never reports that no add-in exists")
    unknown_release = decide_addins([addin], [], None)
    check(unknown_release["status"] == "Info"
          and "Compatibility not decided" in "\n".join(
              unknown_release["observed"]),
          "an unreadable Pro release leaves compatibility undecided, not failed")
    addin_env = fake_healthy()
    addin_key = (r"HKCU\SOFTWARE\ESRI\ArcGISPro\Settings\Add-In Folders")
    addin_path = r"C:\AddIns\Vendor.esriAddinX"
    addin_env._reg[_norm(addin_key)] = {r"C:\AddIns": ""}
    addin_env._tree[_norm(r"C:\AddIns")] = (
        r"C:\AddIns", [("Vendor.esriAddinX", 1000, 1, 0, False)])
    addin_env._packages[_norm(addin_path)] = payload
    probed_addins = probe_addins(addin_env, {
        "pro_release": "3.4.0", "crash_modules": ["native.dll"]})
    check(probed_addins["status"] == "Warn"
          and "Vendor Tools" in "\n".join(probed_addins["observed"])
          and "source=user" in "\n".join(probed_addins["observed"]),
          "the probe follows a HKCU well-known folder and inspects its package")

    print("-" * 70)
    print("explicit add-in quarantine is verified, reversible and no-clobber")
    quarantine_test = tempfile.mkdtemp(prefix="properf-quarantine-test-")
    real_discovery = globals()["discover_startup_packages"]
    try:
        loading = os.path.join(quarantine_test, "loading")
        os.makedirs(loading)
        source = os.path.join(loading, "Vendor.esriAddinX")
        original_bytes = b"PK-test-addin\x00\xff"
        with open(source, "wb") as handle:
            handle.write(original_bytes)
        quarantine_parent = os.path.join(quarantine_test, "quarantine")
        selected = dict(addin, path=source, source="user", source_root=loading)
        locations = [("user", loading, True)]
        globals()["discover_startup_packages"] = lambda env: (
            [selected], [], locations)
        closed_env = fake_healthy()
        original_fingerprint = _addin_fingerprint(source)
        quarantined = quarantine_addin(closed_env, source, quarantine_parent)
        check(not os.path.exists(source)
              and os.path.isfile(quarantined["quarantined_path"])
              and _addin_fingerprint(quarantined["quarantined_path"])["sha256"]
              == original_fingerprint["sha256"]
              and open(quarantined["quarantined_path"], "rb").read()
              == original_bytes,
              "quarantine removes the loading copy and preserves exact package bytes")
        manifest_file = quarantined["manifest"]
        with open(manifest_file, encoding="utf-8") as handle:
            manifest = json.load(handle)
        check(manifest["status"] == "quarantined"
              and manifest["sha256"] == _addin_fingerprint(
                  quarantined["quarantined_path"])["sha256"]
              and manifest["restore_command"],
              "the manifest records a verified hash, paths and an undo command")
        restored = restore_addin(closed_env, manifest_file)
        check(restored["status"] == "restored"
              and open(source, "rb").read() == original_bytes
              and os.path.isfile(quarantined["quarantined_path"]),
              "restore returns the exact package and retains the quarantine backup")
        with open(manifest_file, encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["status"] = "quarantined"
        with open(manifest_file, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        raises(lambda: _restore_addin_manifest(manifest_file),
               "restore refuses to overwrite a package that reappeared",
               AddinRemediationError)
        check(open(source, "rb").read() == original_bytes,
              "and the existing package survives that refusal byte for byte")
        os.remove(source)
        with open(quarantined["quarantined_path"], "wb") as handle:
            handle.write(b"tampered")
        raises(lambda: _restore_addin_manifest(manifest_file),
               "a quarantined package whose hash changed is not restored",
               AddinRemediationError)
        check(not os.path.exists(source),
              "and the failed integrity check does not publish a partial file")
        with open(source, "wb") as handle:
            handle.write(original_bytes)
        raises(lambda: quarantine_addin(fake_sick(), source, quarantine_parent),
               "quarantine is refused while ArcGIS Pro runs",
               AddinRemediationError)
        raises(lambda: quarantine_addin(FakeEnv(procs=None), source,
                                        quarantine_parent),
               "and when the process list is unreadable rather than assuming closed",
               AddinRemediationError)
        selected["source"] = "administrator"
        raises(lambda: quarantine_addin(closed_env, source, quarantine_parent),
               "a machine-wide package requires its separate explicit confirmation",
               AddinRemediationError)
        selected["source"] = "user"
        raises(lambda: quarantine_addin(closed_env, source,
                                        os.path.join(loading, "still-loaded")),
               "quarantine inside a startup-loading folder is refused",
               AddinRemediationError)
        raises(lambda: quarantine_addin(closed_env,
                                        os.path.join(loading, "Other.esriAddinX"),
                                        quarantine_parent),
               "an arbitrary package absent from PF-31 discovery is refused",
               AddinRemediationError)
        check(os.path.isfile(source)
              and open(source, "rb").read() == original_bytes,
              "every refused action leaves the selected source untouched")
    finally:
        globals()["discover_startup_packages"] = real_discovery
        shutil.rmtree(quarantine_test, ignore_errors=True)

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
    # --------------------------------------- the documented performance causes
    print("-" * 70)
    print("the documented performance causes, on both sides of each branch")

    heavy = scan_aprx(r"C:\P\a.aprx", aprx_texts_heavy(".", "a.gdb", 30), [])
    light = scan_aprx(r"C:\P\a.aprx", aprx_texts(".", "a.gdb", 0), [])
    check(len(heavy["layers"]) == 6 and heavy["depth"] == 1,
          "the heavy fixture really carries six layers, three of them under a "
          "group level, so the checks below are reading something")

    check(decide_projection([heavy])["status"] == "Warn",
          "three layers in 3857 under a map in 4326 warn: they are reprojected "
          "on every draw and Pro does not say so")
    check("3857" in _join(decide_projection([heavy])["observed"]),
          "and the system that disagrees is named, not just counted")
    agreed = scan_aprx(r"C:\P\a.aprx", aprx_texts_heavy(".", "a.gdb", 0), [])
    for layer in agreed["layers"]:
        layer["wkid"] = 4326
    check(decide_projection([agreed])["status"] == "Pass",
          "the same project with every layer in the map's system passes")
    check(decide_projection([light])["status"] == "Info"
          and decide_projection([light])["measured"] is False,
          "a project that records no spatial reference is Info and unmeasured, "
          "never a Pass: the file does not say, so nothing was compared  "
          "<-- pinned defect")
    check(decide_projection([])["status"] == "Info",
          "and no project at all is Info")
    check(decide_projection([dict(agreed, truncated=True)])["status"] == "Info",
          "and a project read only in part cannot pass a comparison it did "
          "not finish: the mismatch could be in the part that was never read")
    check(decide_projection([dict(heavy, truncated=True)])["status"] == "Warn",
          "while a mismatch that WAS found stays a finding, because the "
          "layers it named were named")

    check(decide_http_layers([heavy])["status"] == "Warn",
          "three layers served by URL warn: those draw over the network on "
          "every pan")
    check("World_Imagery" in _join(decide_http_layers([heavy])["observed"]),
          "and the layers are named, so the editor can recognise them")
    check(decide_http_layers([light])["status"] == "Pass",
          "a project with no URL layers passes")
    one_http = [dict(light, layers=[dict(light["layers"][0],
                                        uri="https://x.example.org/a")])]
    check(decide_http_layers(one_http)["status"] == "Info",
          "one service layer is reported and not warned: it is a choice, not "
          "a fault")
    check(decide_http_layers([dict(heavy, truncated=True)])["status"] == "Warn",
          "and a finding already made survives a partial read, because the "
          "layers it found were found")
    check(is_http_uri("HTTPS://X/A") and not is_http_uri(r"\\server\share\g.gdb")
          and not is_http_uri("DATABASE=..\\g.gdb"),
          "a URL is classified by its scheme, and a UNC path and a geodatabase "
          "connection are not URLs")

    weighed = decide_project_weight(
        [heavy], [(r"C:\P\a.aprx", 30 * 1024 ** 2)])
    check(weighed["status"] == "Warn",
          "a thirty megabyte project file warns: that is minutes of open, and "
          "it is not a workstation fault")
    check(decide_project_weight([light], [(r"C:\P\a.aprx", 240000)])["status"]
          == "Pass",
          "and a small project with few layers passes")
    many = [dict(light, layers=[{"name": "l%d" % n} for n in range(LAYERS_MANY)])]
    check(decide_project_weight(many, [])["status"] == "Warn",
          "%d layers warn on their own count" % LAYERS_MANY)
    check(decide_project_weight([], [(r"C:\P\a.aprx", 1)])["status"] == "Info"
          and decide_project_weight([], [])["measured"] is False,
          "sizes with no project document are Info and unmeasured: a file size "
          "is not a project weight  <-- pinned defect")

    check(decide_layer_load([heavy])["status"] == "Warn",
          "a three-way join warns: it is re-evaluated on every draw")
    check("Maplex" in _join(decide_layer_load([heavy])["observed"]),
          "and the label engine is reported, because Maplex places better and "
          "costs more")
    check(decide_layer_load([light])["status"] == "Info",
          "a project with no joins and no recorded engine is Info, not Pass: "
          "the engine was not recorded, so nothing was compared")
    check(decide_layer_load([dict(heavy, layers=[])])["status"] == "Info",
          "and a project whose documents were read but hold no layers is Info")

    check(decide_overviews([r"C:\P\ortho.tif", r"C:\P\dem.tif"],
                           set([r"C:\P\dem.tif"]), [])["status"] == "Warn",
          "a raster with no overview beside it warns: it is read at full "
          "resolution on every draw")
    check(decide_overviews([r"C:\P\dem.tif"], set([r"C:\P\dem.tif"]),
                           [])["status"] == "Pass",
          "and one with its sidecar passes")
    unread = decide_overviews([], set(), [r"\\files\gis: access denied"])
    check(unread["status"] == "Info" and unread["measured"] is False,
          "a project folder nobody could read is Info and unmeasured, not a "
          "clean scan: a walk that returns nothing for a folder it could not "
          "open says the tree is fine when it has no idea  <-- pinned defect")
    check(decide_overviews([], set(), [])["status"] == "Info",
          "and no raster at all is Info, because there was nothing to build "
          "overviews for")

    check(decide_disk_media([("SSD 980", "SSD"), ("TOSHIBA", "HDD")],
                            None)["status"] == "Warn",
          "one rotational disk among solid-state ones warns: no setting fixes "
          "it, and it reads as a mystery because every software setting is "
          "correct")
    check(decide_disk_media([("SSD 980", "SSD")], None)["status"] == "Pass",
          "and an all-flash machine passes")
    check(decide_disk_media([("RAID volume", "Unspecified")], None)["status"]
          == "Warn",
          "Unspecified is reported as a disk that may be rotational, because a "
          "controller that will not say is not an answer that it is fast")
    check(decide_disk_media([], "access denied")["status"] == "Info"
          and decide_disk_media([], "access denied")["measured"] is False,
          "and an unread disk list is Info and unmeasured, never a Pass  "
          "<-- pinned defect")

    check(decide_pagefile([r"\\server\pagefile.sys 4096 8192"], None)["status"]
          == "Fail",
          "a pagefile on a network volume fails: it is a multi-second stall "
          "that appears in no Pro log")
    check(decide_pagefile(["C:\\pagefile.sys 1024 2048"], None)["status"]
          == "Warn",
          "a pagefile under %d MB warns" % PAGEFILE_MIN_MB)
    check(decide_pagefile(["C:\\pagefile.sys 16384 32768"], None)["status"]
          == "Pass",
          "and a sized pagefile on a local disk passes")
    check(decide_pagefile([], None)["status"] == "Info",
          "system managed is Info, not Pass: Windows decides the size at boot "
          "and this tool cannot read the number it chose")
    check(decide_pagefile([], "no value")["status"] == "Info"
          and decide_pagefile([], "no value")["measured"] is False,
          "and an unread setting is unmeasured, not system managed  "
          "<-- pinned defect")
    check(_pagefile_mb("C:\\pagefile.sys 4096 8192") == 4096
          and _pagefile_mb("C:\\pagefile.sys") is None,
          "the initial size is read out of the entry, and an entry without one "
          "answers nothing rather than zero")

    check(decide_link_speed([("Ethernet", "1 Gbps"), ("Dock", "100 Mbps")],
                            None)["status"] == "Warn",
          "a gigabit adapter beside one linked at a hundred megabit warns: it "
          "presents as a slow server and nothing in the SMB settings explains "
          "it")
    check(decide_link_speed([("Ethernet", "1 Gbps")], None)["status"] == "Pass",
          "and a single gigabit link passes")
    check(_mbps("1 Gbps") == 1000.0 and _mbps("100 Mbps") == 100.0
          and _mbps("Unrecognized") is None,
          "the link speed is parsed into megabits, and a string this tool does "
          "not recognise answers nothing rather than a number")
    unknown_link = decide_link_speed([("Ethernet", "Unrecognized")], None)
    check(unknown_link["status"] == "Info" and unknown_link["measured"] is False,
          "a link speed nobody could read is Info and unmeasured, not a "
          "Pass  <-- pinned defect")
    check(decide_link_speed([], "access denied")["status"] == "Info",
          "and an unread adapter list is Info")

    check(decide_cpu([{"name": "i7", "cores": 8, "logical": 16,
                       "mhz": 2304}], None)["status"] == "Pass",
          "a sixteen-logical processor passes the allowance this tool sets")
    check(decide_cpu([{"name": "i5", "cores": 2, "logical": 2,
                       "mhz": 1608}], None)["status"] == "Warn",
          "and two logical processors warn: Pro draws largely on one core, so "
          "a weak clock reads as a stall no setting explains")
    check(decide_cpu([{"name": "i7", "cores": 8, "logical": 16,
                       "mhz": 2304}], None)["kpi"]["logical"] == 16,
          "the core count is carried as a number so the report can gauge it")
    check(decide_cpu([{"name": None, "cores": None, "logical": None,
                       "mhz": None}], None)["status"] == "Info",
          "a processor row carrying nothing at all is Info, not a Pass on "
          "zeroes  <-- pinned defect")
    check(decide_cpu([], "access denied")["status"] == "Info"
          and decide_cpu([], None)["status"] == "Info",
          "an unread processor list and an empty one are both Info")
    check(_int_or_none(True) is None and _int_or_none(" 12 ") == 12
          and _int_or_none("twelve") is None,
          "a count is parsed from a CIM string, and a bool is never read as "
          "a count  <-- pinned defect")

    check(_driver_age_days("2023-10-01T00:00:00.0000000+01:00",
                           1700000000.0) == 44,
          "a driver date is read as an age in days, and the CIM offset "
          "suffix does not break it")
    check(_driver_age_days("garbage", 1700000000.0) is None
          and _driver_age_days(None, 1700000000.0) is None,
          "an unreadable date answers nothing rather than zero: read as zero "
          "it would pass the one check Esri's own guide leads with "
          "<-- pinned defect")
    check(_driver_age_days("2030-01-01", 1700000000.0) is None,
          "and a date in the future answers nothing rather than a negative "
          "age that would always pass")
    check(decide_gpu([{"name": "NVIDIA GeForce RTX 3060",
                       "driver": "31.0.15.3623",
                       "date": "2023-10-01"}], None, 1700000000.0)["status"]
          == "Pass",
          "a driver a few weeks old passes")
    check(decide_gpu([{"name": "Intel UHD Graphics 620",
                       "driver": "27.20.100.8681",
                       "date": "2022-01-10"}], None, 1700000000.0)["status"]
          == "Warn",
          "and a driver more than a year old warns: Esri's troubleshooting "
          "guide asks for a driver from the GPU maker as its first step")
    warp = decide_gpu([{"name": "Microsoft Basic Render Driver (WARP)",
                        "driver": "10.0", "date": "2023-10-01"}],
                      None, 1700000000.0)
    check(warp["status"] == "Fail" and "emulating" in warp["impact"],
          "WARP is a Fail and says Pro is emulating a GPU on the CPU: it is "
          "the most common cause of a session that hangs on draw "
          "<-- pinned defect")
    no_date = decide_gpu([{"name": "NVIDIA GeForce RTX 3060",
                           "driver": "31.0", "date": None}],
                         None, 1700000000.0)
    check(no_date["status"] == "Info" and no_date["measured"] is False,
          "a driver with no readable date is Info and unmeasured, not a Pass "
          "on an age of zero  <-- pinned defect")
    check(decide_gpu([], "access denied", 1700000000.0)["status"] == "Info"
          and decide_gpu([], None, 1700000000.0)["status"] == "Info",
          "an unread adapter list and an empty one are both Info")
    check(decide_gpu([{"name": "NVIDIA GeForce RTX 3060", "driver": "31.0",
                       "date": "2023-10-01"},
                      {"name": "Intel UHD Graphics", "driver": "31.0",
                       "date": "2022-01-10"}], None, 1700000000.0)["status"]
          == "Pass",
          "with a discrete GPU and an integrated one, the newest driver "
          "decides: pinning ArcGISPro.exe to the discrete card is the fix "
          "<-- pinned defect")

    check(_gdb_owner(r"C:\\data\\parcels.gdb\\a00000001.gdbtable")
          == "parcels.gdb",
          "a file inside a file geodatabase is attributed to that geodatabase")
    check(_gdb_owner(r"C:\\data\\shape\\parcels.shp") is None,
          "and a shapefile beside it is attributed to none")

    big = {"root": r"C:\\gis", "bytes": 40 * 1024 ** 3, "files": 900,
           "gdbs": {"parcels.gdb": (40 * 1024 ** 3, 890)},
           "unreadable": [], "capped": False}
    check(decide_data_weight([big])["status"] == "Warn",
          "a forty-gigabyte file geodatabase warns: it is slow to open, to "
          "edit, and slowest of all on a share or a spinning disk")
    many = {"root": r"C:\\gis", "bytes": 2 * 1024 ** 3, "files": 9000,
            "gdbs": {}, "unreadable": [], "capped": False}
    check(decide_data_weight([many])["status"] == "Warn",
          "and nine thousand small files warn on their own: a synced folder "
          "tracks every file, so count costs more than size")
    check(decide_data_weight([{"root": r"C:\\gis", "bytes": 1024,
                               "files": 3, "gdbs": {},
                               "unreadable": [], "capped": False}])["status"]
          == "Pass",
          "a small local folder passes")
    capped = decide_data_weight([{"root": r"C:\\gis", "bytes": 1024,
                                  "files": 3, "gdbs": {}, "unreadable": [],
                                  "capped": True}])
    check(capped["status"] == "Info",
          "a scan that stopped early cannot prove the folder is small, so it "
          "answers Info rather than a Pass the walk never produced "
          "<-- pinned defect")
    unread = decide_data_weight([{"root": r"C:\gis", "bytes": 1024,
                                  "files": 3, "gdbs": {},
                                  "unreadable": [r"C:\gis\locked: denied"],
                                  "capped": False}])
    check("unreadable" in " ".join(unread["observed"]),
          "a folder the walk could not open is named, not silently left out "
          "of the total  <-- pinned defect")
    check(decide_data_weight([])["status"] == "Info",
          "no recent project means no folder to weigh, which is Info")
    check(decide_data_weight([big])["kpi"]["largest_gdb_gib"] == 40.0,
          "the heaviest geodatabase is carried as a number so the report can "
          "gauge it")

    check(decide_cpu([{"name": "Celeron", "cores": 1, "logical": 2,
                       "mhz": 1100}], None)["status"] == "Fail",
          "one core is below Esri's published minimum of two and is a Fail, "
          "not a Warn: Pro will not run properly on it")
    check(decide_cpu([{"name": "i5", "cores": 2, "logical": 4,
                       "mhz": 1608}], None)["status"] == "Warn",
          "two cores with hyperthreading is Esri's bare MINIMUM, and it is a "
          "Warn: grading on logical processors would have passed it as if it "
          "met the four-core recommendation  <-- pinned defect")
    check(decide_cpu([{"name": "i7", "cores": 4, "logical": 8,
                       "mhz": 2800}], None)["status"] == "Pass",
          "four cores meets Esri's published recommendation and passes")
    check("Esri" in decide_cpu([{"name": "i7", "cores": 8, "logical": 16,
                                 "mhz": 2304}], None)["expected"],
          "the CPU check names Esri as the source of its tiers rather than "
          "presenting a number this tool chose as if Esri published it")

    only_int = decide_gpu([{"name": "Intel(R) UHD Graphics 620",
                            "driver": "31.0", "date": "2023-10-01"}],
                          None, 1700000000.0)
    check(only_int["status"] == "Warn"
          and "discrete" in " ".join(only_int["observed"]),
          "a recent driver on an integrated-only adapter still warns: Esri "
          "recommends a discrete GPU, and a fresh driver does not change the "
          "silicon  <-- pinned defect")
    check(decide_gpu([{"name": "Intel(R) UHD Graphics 620", "driver": "31.0",
                       "date": "2023-10-01"},
                      {"name": "NVIDIA GeForce RTX 3060", "driver": "31.0",
                       "date": "2023-10-01"}], None, 1700000000.0)["status"]
          == "Pass",
          "and an integrated chip beside a discrete one is fine: pinning Pro "
          "to the discrete card (PF-11) is the fix, not a hardware complaint")
    check("Esri" in decide_gpu([{"name": "NVIDIA GeForce RTX 3060",
                                 "driver": "31.0", "date": "2023-10-01"}],
                               None, 1700000000.0)["expected"],
          "the GPU check quotes Esri's own 'use the latest available driver' "
          "instead of only this tool's day count")
    check(any("arcgis-pro-system-requirements" in u
              for pairs in SOURCES.values() for _l, u in pairs),
          "Esri's published system-requirements page is cited in the report, "
          "so the numbers a reader is asked to compare against can be "
          "checked  <-- pinned defect")

    registered = set(cid for cid, _c, _t, _d, _p
                     in registrations(FakeEnv(), {}, {}))
    registered.update(PROJECT_CHECK_IDS)
    unknown = sorted(set(SOURCES) - registered)
    check(not unknown,
          "every check id in SOURCES is registered by the workstation or "
          "selected-project analyser, so no source is attached to a check "
          "that no longer exists "
          "<-- pinned defect")
    malformed = sorted(cid for cid, pairs in SOURCES.items()
                       for pair in pairs
                       if len(pair) != 2 or not pair[0] or not pair[1])
    check(not malformed,
          "every source is a label and a URL, and neither is empty")
    insecure = sorted(u for pairs in SOURCES.values() for _l, u in pairs
                      if not u.startswith("https://"))
    check(not insecure,
          "every source is https: the report is read on machines where a "
          "plain-http link would be rewritten or blocked outright")
    check(len(SOURCES) >= 30,
          "the great majority of the checks carry at least one remediation "
          "source; a check with no honest source is simply absent rather "
          "than padded with an invented citation")
    sourced = [c for c in run(FakeEnv(ps={"smb": ({}, None)}),
                              {"sde_server": None, "sde_port": 1433,
                               "portal": None, "timeout": 4000,
                               "verbose": False, "apply": False})["checks"]
               if c.get("sources")]
    check(len(sourced) >= 30,
          "and the sources reach the finished report document, not just the "
          "table they were written in  <-- pinned defect")
    check(all(isinstance(c["sources"], list)
              and all(len(p) == 2 for p in c["sources"]) for c in sourced),
          "each one arrives as a list of (label, url) pairs the renderer can "
          "walk without a special case")

    check(_ps_rows({"name": "one", "media": "SSD"}, "name", "media")
          == [("one", "SSD")],
          "a single PowerShell row is unwrapped: ConvertTo-Json drops the "
          "array wrapper for one object, and a machine with exactly one disk "
          "would otherwise read as a machine with none  <-- pinned defect")

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
    check("Vendor.dll" in classify_event(
              "Application", ev("Application Error", 1000,
                                ["ArcGISPro.exe", "3.4.0", "123", "Vendor.dll"])),
          "Application Error 1000 contributes its fault-module name to the report")
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
        # The four that read the project file are Info here and not Warn,
        # because DL-12 found Pro running and the read is refused. That is the
        # refusal working, and it is pinned rather than assumed.
        "PF-01": "Info", "PF-09": "Info", "PF-10": "Info", "PF-14": "Info",
        "PF-16": "Warn", "PF-18": "Warn", "PF-20": "Warn", "PF-21": "Warn",
        "PF-28": "Warn", "PF-29": "Warn",
        "PF-30": "Pass", "PF-31": "Pass",
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
    check(not [n for n in document["notes"] if n.startswith("would apply")],
          "no automatic change is proposed while Pro is running")
    check([n for n in document["notes"] if n.startswith("refused [AP-01]")],
          "the cache fix is refused while Pro is running, because clearing a "
          "live cache corrupts the session  <-- pinned defect")
    check(env.written == [],
          "and nothing whatsoever was written to the stand-in machine")
    check(document["fixable"] == ["PF-06"],
          "only the reversible cache fix is offered; PF-11 remains visible as "
          "a finding but cannot arm a graphics-preference change")
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
    check("1 dump(s) in the last 45 days"
          in by_id(document["checks"], "DL-09")["impact"],
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
    vanished = [make_check("DL-00", "c", "t", "Pass", "measured", dataloss=True),
                make_check("DL-12", "c", "t", "Pass", "measured", dataloss=True),
                make_check("PF-06", "c", "t", "Warn", "measured", fix="f",
                           autofixable=True)]
    gone_applied, gone_notes = apply_fixes(vanished, fake_sick(), {}, True)
    check(gone_applied == [], "nothing is recorded as applied in that case")
    check([n for n in gone_notes if "the target it would change is gone" in n],
          "and the run says so instead of reporting a change it did not make")
    missing_allowed, missing_why = apply_gate(vanished, "PF-11", True)
    check(missing_allowed is False and "did not run" in missing_why,
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
    check(len([n for n in healthy["notes"] if n.startswith("skip [AP-01]")]) == 1,
          "the cache fix reports itself already healthy rather than silently "
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
    check(ids_applied == ["AP-01"],
          "with Pro closed and --apply given, only the reversible cache fix runs")
    check(by_id(applied_run["checks"], "PF-22")["status"] == "Warn"
          and "Hydrants refreshRate = 30" in
          by_id(applied_run["checks"], "PF-22")["observed"],
          "with Pro closed the project file is read, and the layer on a "
          "thirty-second timer is named")
    check(len(applying.zip_reads) == 1,
          "one recent project, one read")
    check(not [change for change in applying.written
               if change[0] == GPU_PREFERENCE_KEY],
          "even with --apply, no graphics preference is written; the recorded "
          "cache rename is the only machine change")
    cache_record = [r for r in applied_run["applied"] if r["id"] == "AP-01"][0]
    check(".properf-" in cache_record["result"],
          "the cache folder is renamed aside, never deleted  <-- pinned defect")
    check(cache_record["undo"].startswith("move "),
          "and the undo moves it straight back")
    applied_text = "\n".join(render(applied_run))
    check("APPLIED [AP-01]" in applied_text,
          "the report names every change that was made")
    check("undo   :" in applied_text,
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
    check(previous._reg[_norm(GPU_PREFERENCE_KEY)][
              r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe"]
          == "GpuPreference=1;"
          and not [change for change in previous.written
                   if change[0] == GPU_PREFERENCE_KEY]
          and [r["id"] for r in prev_run["applied"]] == ["AP-01"],
          "an existing graphics preference survives --apply byte for byte: "
          "the retired AP-02 cannot turn a driver issue into a startup crash")

    class FailingEnv(FakeEnv):
        def rename(self, src, dst):
            raise OSError("access denied")

    failing = fake_sick()
    failing._procs = []
    failing.__class__ = FailingEnv
    failing_run = run(failing, opts_with(apply=True))
    check([n for n in failing_run["notes"] if n.startswith("failed [AP-01]")],
          "a fix that throws is recorded as failed and does not end the run")
    check(failing_run["applied"] == [],
          "and a failed change is never recorded as applied")

    # ------------------------------------------------------------- reporting
    print("-" * 70)
    print("the report says what the run found, and nothing else")
    text = "\n".join(render(document))
    check("ACT ON THESE FIRST" in text,
          "a run with data-loss findings prints the act-first block")
    check("--- DATA LOSS" in text and "--- PERFORMANCE" in text,
          "and both sections")
    check("S3  des modifications enregistrees ont disparu" in text,
          "the symptom key is printed, so the impact lines can be read")
    check("ACT ON THESE FIRST" not in "\n".join(render(healthy)),
          "a healthy run prints no act-first block at all")
    check("expect :" not in text, "a plain run does not print the expected text")
    check("expect :" in "\n".join(render(document, verbose=True)),
          "and a verbose one does")
    check("impact :" in text,
          "a failing check prints its impact even without verbose")
    quiet_text = "\n".join(render_check(
        make_check("Q-1", "c", "t", "Pass", "measured", impact="i", fix="f")))
    check("impact :" not in quiet_text,
          "a passing check does not, because a clean report should be short")
    bare_lines = render_check(make_check("Q-2", "c", "t", "Warn",
                                         ["measured", "", "   "]), verbose=True)
    check(len(bare_lines) == 2,
          "blank observed lines are dropped, so a multi-line reading does not "
          "print gaps down the middle of the report")
    check(not [line for line in bare_lines if "impact" in line
               or "fix" in line or "expect" in line],
          "and a check that carries no impact, fix or expected text prints "
          "none of those headings empty")

    temp_dir = tempfile.mkdtemp(prefix="properf-selftest-")
    try:
        text_path, json_path = write_report(document, temp_dir)
        check(os.path.isfile(text_path) and os.path.isfile(json_path),
              "--report writes both a text and a JSON report")
        handle = io.open(json_path, encoding="utf-8")
        try:
            reloaded = json.loads(handle.read())
        finally:
            handle.close()
        check(reloaded["report_version"] == REPORT_VERSION,
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
    check(args.report is None, "and writes no file")
    check(args.self_test is False, "the self-test is off by default")
    check(args.verbose is False, "and so is the long form")
    check(args.sde_port == 1433, "the database port defaults to 1433")
    check(args.timeout == 4000, "and the probe timeout to four seconds")
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
                    "--report", "x", "--quarantine-addin", "x.esriAddinX",
                    "--restore-addin", "quarantine.json"]) == 4242,
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
    check(quiet_main(["--quarantine-dir", "x"])[0] == 64,
          "a quarantine directory without a selected package is a usage error")
    check(quiet_main(["--quarantine-addin", "x.esriAddinX",
                      "--restore-addin", "quarantine.json"])[0] == 64,
          "quarantine and restore cannot be armed in the same invocation")
    check(quiet_main(["--quarantine-addin", "not-a-package.txt"])[0] == 64,
          "an arbitrary file extension cannot reach the quarantine file operations")
    blocked = tempfile.mkdtemp(prefix="properf-blocked-")
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
    check(live.getenv("PROPERF_NOTHING_SETS_THIS") is None,
          "and one that does not is None, not an empty string")
    check(live.registry_readable() is (os.name == "nt"),
          "the registry is readable exactly on Windows, and the checks that "
          "depend on it are told so rather than guessing from a None")

    root = tempfile.mkdtemp(prefix="properf-live-")
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

        check(live.read_text(os.path.join(root, "a.txt")) == ("hello", None),
              "a file is read, and a successful read says so in the second "
              "slot rather than leaving the caller to guess")
        check(live.read_text(os.path.join(root, "nope"))[0] is None
              and live.read_text(os.path.join(root, "nope"))[1],
              "and a missing one reads as None with a reason rather than "
              "raising")
        huge = os.path.join(root, "huge.config")
        handle = io.open(huge, "w", encoding="utf-8")
        try:
            handle.write("x" * 2048)
        finally:
            handle.close()
        text, reason = live.read_text(huge, limit=1024)
        check(text is None and "larger than" in (reason or ""),
              "a file over the limit is refused with a reason, not read: a "
              "synced user.config is a file this tool did not write  "
              "<-- pinned defect")
        check(live.read_text(huge, limit=4096) == ("x" * 2048, None),
              "and the same file reads whole once the limit allows it, so the "
              "refusal is about the limit and not about the file")

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
    ghost.powershell = "properf-no-such-program"
    ghost.wevtutil = "properf-no-such-program"
    check(ghost._powershell_json("1")[1] and ghost.events("System", "*",
                                                          1)[1],
          "a reader program that is not installed is a reason, not a "
          "traceback and not an empty answer")
    check(live.reg_subkeys(r"HKCU\Software\properf-nothing-here") == [],
          "a registry key that is not there lists no subkeys")

    data, reason = live._powershell_json("ConvertTo-Json -InputObject @(7)")
    check((data is None) != (reason is None),
          "a PowerShell read answers data or a reason, never both and never "
          "neither")
    check(live._powershell_json("throw 'properf'")[1],
          "a script that throws is a reason, not an empty answer")
    check(live._powershell_json("Write-Output 'not json'")[1],
          "and output that is not JSON is a reason too")
    for name in ("defender_preferences", "nic_power", "smb_client"):
        data, reason = getattr(live, name)()
        check((data is None) != (reason is None),
              "the %s read on this machine answers data or a reason" % name)
    check(live.events("Properf-No-Such/Log", "*", 1)[1],
          "a log that does not exist is an error, not an empty timeline  "
          "<-- pinned defect")
    text, reason = live.events("Application", "*[System[EventID=999999]]", 1)
    check(parse_events(text) == [] if reason is None else bool(reason),
          "a query nothing matches is an empty list where the log can be "
          "read, and a reason where it cannot")

    zroot = tempfile.mkdtemp(prefix="properf-zip-")
    froot = None
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
        addin_zip = os.path.join(zroot, "Vendor.esriAddinX")
        with zipfile.ZipFile(addin_zip, "w", zipfile.ZIP_DEFLATED) as zarch:
            zarch.writestr("Config.daml", daml)
            zarch.writestr("Vendor.dll", b"MZ-test")
            zarch.writestr("Native.dll", b"MZ-native")
        raw_addin = live.startup_package(addin_zip, full_crc=True)
        check(raw_addin["config"] == daml
              and raw_addin["crc"] == "passed for every archive member"
              and len(raw_addin["members"]) == 3,
              "a real add-in-shaped ZIP yields bounded DAML, members and full CRC")
        check(not parse_startup_package(addin_zip, "add-in", raw_addin)["warnings"],
              "and the real package parses without loading any assembly")
        check(live.startup_package(not_zip)["error"],
              "a corrupt add-in archive returns evidence instead of aborting the run")

        # A lying header. The uncompressed size in a zip's central directory
        # is a number the file's author chose, and the reader stops there, so
        # a cap that trusts it both understates itself and turns a corrupt
        # entry into an exception that escapes the reader. These get a folder
        # of their own: the listing test below counts the entries of zroot,
        # and a diagnostic's own test fixtures are not part of the folder it
        # is timing.
        import struct
        froot = tempfile.mkdtemp(prefix="properf-forged-")
        lying = os.path.join(froot, "lying.aprx")
        payload = b"A" * (4 * 1024 * 1024)
        with zipfile.ZipFile(lying, "w", zipfile.ZIP_DEFLATED) as zarch:
            zarch.writestr("project/project.json", payload)
        blob = bytearray(io.open(lying, "rb").read())
        eocd = blob.rfind(b"PK\x05\x06")
        central = struct.unpack_from("<I", blob, eocd + 16)[0]
        declared = struct.unpack_from("<I", blob, central + 24)[0]
        check(declared == len(payload),
              "the forged archive really does declare the full four megabytes "
              "before the test lies about it")
        struct.pack_into("<I", blob, central + 24, 100)
        handle = io.open(lying, "wb")
        try:
            handle.write(bytes(blob))
        finally:
            handle.close()
        texts, capped, reason = live.read_zip_json(lying, max_total=65536)
        check(texts == [] and reason and "project.json" in reason,
              "a header that understates the entry is caught by reading to the "
              "end and letting the archive's own checksum speak, not by "
              "trusting the number in the header  <-- pinned defect")
        check("Bad CRC" in (reason or "") or "CRC" in (reason or ""),
              "and the reason says which entry failed and why, so a corrupt "
              "project is named rather than read as an empty one")

        honest = os.path.join(froot, "honest.aprx")
        with zipfile.ZipFile(honest, "w", zipfile.ZIP_DEFLATED) as zarch:
            zarch.writestr("a.json", '{"defaultFolder": "."}')
            zarch.writestr("b.json", "x" * 600)
        texts, capped, reason = live.read_zip_json(honest, max_entry=100)
        check([n for n, _t in texts] == ["a.json"] and capped is True,
              "one entry over the cap costs that entry and no other: a "
              "project with three readable documents and one oversized one "
              "still answers for the three  <-- pinned defect")
        texts, capped, reason = live.read_zip_json(honest)
        check(len(texts) == 2 and reason is None,
              "and with the cap where it belongs the same file reads whole")
        for n in range(3):
            io.open(os.path.join(zroot, "f%d.txt" % n), "w").close()
        elapsed, entries, reason = live.time_listdir(zroot)
        check(entries == 6 and elapsed >= 0 and reason is None,
              "a real folder with its six fixtures is listed and timed")
        check(live.time_listdir(zroot, limit=2)[1] == 2,
              "and the listing stops at its limit")
        gone = live.time_listdir(os.path.join(zroot, "nope"))
        check(gone[0] is None and gone[2],
              "a folder that cannot be listed has no time, only a reason")
    finally:
        shutil.rmtree(zroot, ignore_errors=True)
        if froot:
            shutil.rmtree(froot, ignore_errors=True)

    # ---------------------------------------------------- the hardened reads
    print("-" * 70)
    print("the reads are hardened against a file this tool did not write")

    bomb = ('<?xml version="1.0"?>\n<!DOCTYPE r [\n'
            ' <!ENTITY a "xxxxxxxxxx">\n <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;'
            '&a;&a;">\n <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
            ' <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">\n'
            ' <!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">\n'
            ' <!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">\n]>\n'
            '<configuration><section><setting name="k"><value>&f;</value>'
            '</setting></section></configuration>')
    check(len(bomb) < 700, "the entity bomb really is a few hundred bytes")
    check(parse_user_config(bomb) == {},
          "a user.config carrying a document type declaration is refused "
          "outright: expat expands internal entities, so those few hundred "
          "bytes are a million characters by the time the parser is done  "
          "<-- pinned defect")
    check(len(parse_user_config(
        '<configuration><setting name="a"><value>1</value></setting>'
        '</configuration>')) == 1,
          "and a real user.config, which has no document type declaration, "
          "still parses")
    check(parse_user_config('<!doctype configuration><setting name="a"/>'
                            '</configuration>') == {},
          "the refusal does not care how the declaration is spelled")

    deep = "[" * 5000 + "]" * 5000
    try:
        found = scan_aprx(r"C:\P\a.aprx", [("x.json", deep)], [])
        check(found["capped"] is True,
              "a project document nested five thousand deep is declined with "
              "a cap, not with a RecursionError that took the whole check "
              "down with it  <-- pinned defect")
    except RecursionError:
        check(False, "a deeply nested project must not end the check with a "
                     "RecursionError")
    nested = scan_aprx(r"C:\P\a.aprx",
                       [("x.json", json.dumps({"a": {"refreshRate": 30}}))], [])
    check(len(nested["refresh"]) == 1,
          "and an ordinary project still yields its timers after the walk was "
          "made iterative")

    probe_dir = tempfile.mkdtemp(prefix="properf-uniq-")
    try:
        target = os.path.join(
            probe_dir, "arcgis_pro_performance_analyser_20260101_000000.txt")
        check(unique_path(target) == target,
              "a name nobody has taken is left alone")
        handle = io.open(target, "w")
        handle.close()
        second = unique_path(target)
        check(second != target and not os.path.exists(second),
              "and a name that is taken gets another one: two runs inside one "
              "second used to land on the same file and the second silently "
              "replaced the first  <-- pinned defect")
        handle = io.open(second, "w")
        handle.close()
        check(unique_path(target) not in (target, second),
              "and it keeps going until it finds one that is free")
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)

    check(quote_cmd(r"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe")
          == r'"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe"',
          "a path with spaces in it is quoted for the undo command")
    check(quote_cmd('C:\\bin"ArcGISPro.exe') is None,
          "and a path holding a double quote is refused rather than printed: "
          "cmd strips one level of quoting and the program's parser strips "
          "another, so the command would do something other than what it "
          "says  <-- pinned defect")
    for hostile in ("a&b", "a|b", "a<b", "a>b", "a^b", "a%PATH%b", "a!b!"):
        if quote_cmd(hostile) is not None:
            check(False, "%r must not be quoted for a command line" % hostile)
            break
    else:
        check(True,
              "a command separator or a percent expansion is refused too, "
              "because outside quotes those are live in cmd.exe")

    ghost = FakeEnv(envvars={})
    check(ghost.system32("tasklist") == "tasklist",
          "with no SystemRoot the program name is passed through unchanged, "
          "rather than built into a path that cannot exist")
    rooted = FakeEnv(envvars={"SystemRoot": r"C:\Windows"},
                     tree={r"C:\Windows\System32": [("wevtutil.exe", 1000, 0,
                                                     0, False)]})
    check(rooted.system32("wevtutil.exe")
          == r"C:\Windows\System32\wevtutil.exe",
          "and with one, a program that ships with Windows is started by its "
          "full path: Windows searches the current directory first for a bare "
          "name, so a bare name run from somebody else's folder can run "
          "their file  <-- pinned defect")
    check(rooted.system32("properf-not-here.exe") == "properf-not-here.exe",
          "while a name that is not there is left as it was")

    live_tcp = Env()
    real_getaddrinfo = socket.getaddrinfo

    def slow_name(*args, **kwargs):
        time.sleep(2)
        return real_getaddrinfo(*args, **kwargs)

    socket.getaddrinfo = slow_name
    try:
        started = time.time()
        addresses, reason = live_tcp.resolve("slow.example.invalid", 1433, 100)
        took_ms = int((time.time() - started) * 1000)
        check(addresses is None and "did not answer" in (reason or ""),
              "a name lookup that does not answer is its own answer, inside "
              "the bound: it is not 'the name did not resolve', which would "
              "say the host does not exist  <-- pinned defect")
        check(took_ms < 1500,
              "and it gave up in %d ms rather than waiting out the system "
              "resolver's retry budget" % took_ms)
    finally:
        socket.getaddrinfo = real_getaddrinfo

    # --------------------------------------------- the whole tool, on this box
    print("-" * 70)
    print("the tool end to end on the machine running this, read-only")
    code, printed = captured(lambda: main(["--json"]))
    check(code in (0, 1, 2, 3),
          "a real run exits 0, 1, 2 or 3, whatever this machine turns out to "
          "be")
    live_document = json.loads(printed)
    check(live_document["report_version"] == 4,
          "the JSON it prints carries report version 4, the number a diff "
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

    report_dir = tempfile.mkdtemp(prefix="properf-live-report-")
    try:
        code, printed = captured(
            lambda: main(["--report", os.path.join(report_dir, "out"),
                          "--verbose"]))
        written = sorted(os.listdir(os.path.join(report_dir, "out")))
        check(len(written) == 2,
              "--report writes exactly two files, and creates the directory")
        check("report :" in printed, "and names both of them on the console")
        check("ACT ON THESE FIRST" in printed
              or "  FAIL 0   WARN 0" in printed,
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
    check(unknown_run["applied"] == [] and unknown_pro.written == []
          and [n for n in unknown_run["notes"]
               if n.startswith("refused [AP-01]")],
          "and the cache fix is refused by name, because only a Pass from "
          "DL-12 lets it run; no registry fallback is attempted  <-- pinned defect")
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

    code, out, err = with_env(fake_healthy, ["--html"])
    check(code == 64 and "--html requires --report" in err,
          "--html without a report directory is a usage error, never a partial run")

    # --json is one document, with or without --report.
    jr_dir = tempfile.mkdtemp(prefix="properf-json-report-")
    try:
        code, out, err = with_env(fake_healthy, ["--json", "--report", jr_dir,
                                                 "--html"])
        check(json.loads(out)["report_version"] == 4,
              "--json with --report prints one JSON document on stdout  "
              "<-- pinned defect")
        check("report :" in err and "report :" not in out,
              "and the report paths go to stderr")
        check(len(os.listdir(jr_dir)) == 3,
              "TXT, JSON and the requested self-contained HTML are all written")
        html_names = [name for name in os.listdir(jr_dir) if name.endswith(".html")]
        check(len(html_names) == 1 and "PF-31" in io.open(
                  os.path.join(jr_dir, html_names[0]), encoding="utf-8").read(),
              "the CLI HTML includes the startup add-in diagnostic")
    finally:
        shutil.rmtree(jr_dir, ignore_errors=True)

    class OffMachine(FakeEnv):
        windows = False

    code, out, err = with_env(OffMachine, ["--json"])
    check("almost every probe" in err and "almost every probe" not in out
          and json.loads(out)["report_version"] == 4,
          "off Windows the note goes to stderr and stdout stays a document")
    code, out, err = with_env(
        OffMachine, ["--quarantine-addin", r"C:\AddIns\Vendor.esriAddinX"])
    check(code == 64 and "No file was changed" in err,
          "off Windows quarantine refuses before discovery or any file operation")

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
    scan_root = tempfile.mkdtemp(prefix="properf-scan-")
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

    # A child is spawned by resolved interpreter, never by sys.executable:
    # inside ArcGIS Pro that is ArcGISPro.exe, and starting it opens a second
    # copy of the application instead of running the snippet.
    py = winenv.python_exe()
    if py is None:
        skip("a child that hangs is killed at its timeout and raises",
             "no Python interpreter to spawn here; sys.executable is %r"
             % (sys.executable,))
        skip("and it was killed, not waited for", "nothing was spawned")
        skip("a child's exit code, output and first error line are returned",
             "nothing was spawned")
    else:
        started = time.time()
        raises(lambda: winenv._run([py, "-c", "import time; time.sleep(30)"],
                                   timeout=0.5),
               "a child that hangs is killed at its timeout and raises, "
               "instead of holding the run for two minutes",
               kind=subprocess.TimeoutExpired)
        check(time.time() - started < 20, "and it was killed, not waited for")
        check(winenv._run([py, "-c",
                           "import sys; sys.stderr.write('oops\\n'); "
                           "sys.exit(3)"], timeout=20) == (3, "", "oops")
              and winenv._run([py, "-c", "import sys; sys.exit(4)"],
                              timeout=20)[2] == "exit code 4",
              "a child's exit code, output and first error line are returned, "
              "and a silent failure still gets a reason")
    check(winenv.python_exe() is None
          or os.path.basename(winenv.python_exe()).lower().startswith("python"),
          "the interpreter is resolved by name and is never the host "
          "application: spawning ArcGISPro.exe with -c opens a second copy of "
          "Pro and hangs until the timeout  <-- pinned defect")

    lines, code = summarize(3, ["it broke"])
    check(code == 1 and lines == ["4 assertions, 1 failed",
                                  "  FAILED: it broke"],
          "a failing self-test says how many failed, names each, and exits 1")
    check(summarize(2, []) == (["2 assertions, 0 failed"], 0),
          "and a clean one exits 0")
    check(summarize(2, [], 3) == (["2 assertions, 0 failed, 3 skipped"], 0),
          "an assertion a host could not run is named in the footer and is "
          "counted in neither number: a skip is not a pass")
    imported = runpy.run_path(os.path.abspath(__file__),
                              run_name="properf_imported")
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
    running_sick = fake_sick()
    applied_sick = run(running_sick, opts_with(apply=True))["applied"]
    check(applied_sick == [] and running_sick.written == [],
          "with Pro running, --apply neither renames its cache nor writes a "
          "graphics preference")
    check(decide_pro_running([])["dataloss"] is True
          and decide_pro_running(["x"])["dataloss"] is True,
          "DL-12 stays in the data-loss block when Pro is closed as well as "
          "when it runs  <-- pinned defect")
    check("only SMB client connectivity events" in smb_only["title"],
          "an SMB-only timeline says so in its title  <-- pinned defect")
    some_unreadable = decide_events([], [("C", "z")], 3, [])
    check("nothing recorded" in some_unreadable["title"]
          and "nothing recorded" not in decide_events(
              [], [("A", "x"), ("B", "y"), ("C", "z")], 3, [])["title"],
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
                 if l.startswith("--- PERFORMANCE")][0]
        placed = [(rendered.count(render_check(c)[0]),
                   [n for n, l in enumerate(rendered)
                    if l == render_check(c)[0]][0] < split, c["dataloss"])
                  for c in doc["checks"]]
        check(all([once == 1 and before is dataloss
                   for once, before, dataloss in placed]),
              "%s run: every check prints exactly once, data-loss ahead of "
              "the performance heading and performance after it  "
              "<-- pinned defect" % label)
        check(bool(doc["fixable"]) == any(["have a fix here" in l
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

    print("-" * 70)
    lines, code = summarize(passed[0], failed, not_run[0])
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
        prog="arcgis_pro_performance_analyser.py",
        allow_abbrev=False,
        description="Diagnose this workstation or one explicitly selected "
                    ".aprx, and never report a finding the file or machine "
                    "did not produce.",
        epilog="Check by default. Without --apply, --project-repair, "
               "--quarantine-addin or --restore-addin, nothing on the machine "
               "or in the source project changes; without "
               "--sde-server or --portal no host is "
               "contacted, apart from reading a project folder that already "
               "lives on a share. Connection files are recorded by path and "
               "never opened.")
    ap.add_argument("--self-test", dest="self_test", action="store_true",
                    help="run the offline assertions and exit")
    ap.add_argument("--verbose", action="store_true",
                    help="print the expected text and the evidence lines too")
    ap.add_argument("--json", action="store_true",
                    help="print the report as JSON instead of the console form")
    ap.add_argument("--report", metavar="DIR",
                    help="write the text and JSON reports into this directory. "
                         "Without it no report files are written.")
    ap.add_argument("--html", action="store_true",
                    help="with --report, also write a self-contained HTML "
                         "report beside the TXT and JSON")
    ap.add_argument("--sde-server", dest="sde_server", metavar="HOST",
                    help="TCP-probe this enterprise geodatabase host. Connect "
                         "only, no credential. Without it the probe is skipped.")
    ap.add_argument("--sde-port", dest="sde_port", type=int, default=1433,
                    metavar="PORT",
                    help="port for the geodatabase probe (default 1433)")
    ap.add_argument("--portal", metavar="HOST",
                    help="TCP-probe this portal host on 443. Without it the "
                         "probe is skipped.")
    ap.add_argument("--timeout", type=int, default=4000, metavar="MS",
                    help="bound on each network probe in milliseconds "
                         "(default 4000)")
    ap.add_argument("--project", metavar="APRX",
                    help="analyse only this explicitly selected .aprx through "
                         "a local snapshot, even while another project is open")
    ap.add_argument("--project-repair", choices=PROJECT_REPAIR_MODES,
                    default="none", metavar="MODE",
                    help="with --project: none, repack, views or views_history. "
                         "Always writes a new copy beside the source; never "
                         "rewrites the source")
    ap.add_argument("--apply", action="store_true",
                    help="run the gated fixes. Off by default. Each one is "
                         "refused unless its check actually failed, prints its "
                         "own undo, and never deletes anything.")
    ap.add_argument("--quarantine-addin", metavar="PACKAGE",
                    help="move this exact PF-31-discovered package into a "
                         "verified, restorable quarantine; Pro must be closed")
    ap.add_argument("--quarantine-dir", metavar="DIR",
                    help="quarantine parent outside every startup-loading "
                         "folder (default: LOCALAPPDATA analyser folder)")
    ap.add_argument("--restore-addin", metavar="MANIFEST",
                    help="restore the package recorded in a quarantine.json "
                         "without overwriting any existing file")
    ap.add_argument("--confirm-machine-wide", action="store_true",
                    help="confirm an administrator-approved quarantine of a "
                         "machine-wide package; affects other users")
    return ap.parse_args(argv)


def main(argv=None):
    args = _parse(sys.argv[1:] if argv is None else argv)

    # The self-test wins over everything. A test run must never be able to
    # reach a real machine, whatever else is on the command line.
    if args.self_test:
        return self_test()

    if args.html and not args.report:
        print("error: --html requires --report DIR.", file=sys.stderr)
        return USAGE_ERROR
    addin_actions = int(bool(args.quarantine_addin)) + int(
        bool(args.restore_addin))
    if addin_actions > 1:
        print("error: choose only one add-in action at a time.",
              file=sys.stderr)
        return USAGE_ERROR
    if args.quarantine_dir and not args.quarantine_addin:
        print("error: --quarantine-dir requires --quarantine-addin.",
              file=sys.stderr)
        return USAGE_ERROR
    if args.confirm_machine_wide and not args.quarantine_addin:
        print("error: --confirm-machine-wide requires --quarantine-addin.",
              file=sys.stderr)
        return USAGE_ERROR
    if addin_actions and (args.project or args.project_repair != "none"
                          or args.apply or args.report or args.html or args.json
                          or args.sde_server or args.portal):
        print("error: an add-in quarantine/restore is a standalone action; "
              "run the diagnostic separately so its report is never confused "
              "with the action result.", file=sys.stderr)
        return USAGE_ERROR
    if (args.quarantine_addin and os.path.splitext(args.quarantine_addin)[1]
            .lower() not in ADDIN_PACKAGE_EXTENSIONS):
        print("error: --quarantine-addin requires an .esriAddinX, .proConfigX "
              "or .esriPlugin package.", file=sys.stderr)
        return USAGE_ERROR
    if args.project_repair != "none" and not args.project:
        print("error: --project-repair requires --project APRX.",
              file=sys.stderr)
        return USAGE_ERROR
    if args.project and args.apply:
        print("error: --apply is for workstation fixes and cannot be combined "
              "with --project; use --project-repair for a recovery copy.",
              file=sys.stderr)
        return USAGE_ERROR
    if args.project and (args.sde_server or args.portal):
        print("error: --project is a project-only analysis; remove the "
              "workstation network probes.", file=sys.stderr)
        return USAGE_ERROR
    if args.project and not args.project.lower().endswith(".aprx"):
        print("error: --project requires a file with the .aprx extension.",
              file=sys.stderr)
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
    # stderr too. The three lines that go there name a path, and a path on a
    # redirected cp1252 console can hold a character that code page has no
    # glyph for. One stream reconfigured and not the other turned "could not
    # write the report" into a UnicodeEncodeError traceback, so the run exited
    # on an encoding error instead of with the 64 that says why.
    make_output_safe(sys.stderr)

    env = Env()
    if not env.windows and not args.project and not addin_actions:
        # stderr, not stdout: --json must emit a document and nothing else,
        # or the caller parsing it gets a note where the report should be.
        print("note: this is a Windows workstation diagnostic. On %s almost "
              "every probe reports Skipped or Info, which is what an "
              "undetermined check looks like. Use --self-test to verify the "
              "tool itself here." % sys.platform, file=sys.stderr)

    if addin_actions:
        try:
            if args.quarantine_addin:
                result = quarantine_addin(
                    env, args.quarantine_addin, args.quarantine_dir,
                    args.confirm_machine_wide)
            else:
                result = restore_addin(env, args.restore_addin)
        except (AddinRemediationError, OSError, IOError, ValueError) as exc:
            print("error: add-in action refused or incomplete: %s" % exc,
                  file=sys.stderr)
            return USAGE_ERROR
        print_addin_action(result)
        return 0

    if args.project:
        document = run_project(env, {"project_path": args.project,
                                     "repair": args.project_repair})
    else:
        document = run(env, {"sde_server": args.sde_server,
                             "sde_port": args.sde_port,
                             "portal": args.portal,
                             "timeout": args.timeout,
                             "apply": args.apply})

    if args.json:
        print(json.dumps(document, indent=1, sort_keys=True))
    else:
        for line in render(document, args.verbose):
            print(line)

    if args.report:
        try:
            text_path, json_path = write_report(document, args.report,
                                                args.verbose)
        except (OSError, IOError) as exc:
            print("error: could not write the report: %s" % exc,
                  file=sys.stderr)
            return USAGE_ERROR
        html_path = None
        if args.html:
            try:
                html_path = write_html_report(document, json_path, args.verbose)
            except (ImportError, OSError, IOError, ValueError, TypeError) as exc:
                print("error: could not write the HTML report: %s" % exc,
                      file=sys.stderr)
                return USAGE_ERROR
        # With --json, stdout is one document and nothing else.
        out = sys.stderr if args.json else sys.stdout
        print("", file=out)
        print("  report : %s" % text_path, file=out)
        print("  report : %s" % json_path, file=out)
        if html_path:
            print("  report : %s" % html_path, file=out)
        if document.get("analysis_type") == "project":
            print("  Comparez ce JSON avec celui d'une copie saine du projet "
                  "pour isoler la différence.", file=out)
        else:
            print("  Diff the JSON against a known-good workstation to isolate "
                  "what is different about this one.", file=out)

    return exit_code(document["checks"])

# ============================================================================
# 2/3  HTML report renderer
# source: html_report.py  (sha256:184c9b6eaf73)
# ----------------------------------------------------------------------------
# r"""Render an ArcGIS Pro Performance Analyser report as one self-contained
# HTML page.
#
# A pure function over the document the analyser returns: no ArcGIS import, no
# network, no template engine, no external asset. That matters twice over. The
# report is opened from a file:// URL on a workstation that may be offline or
# air-gapped, so a CDN link would be a broken page. And the report is full of
# machine-supplied strings - account names, folder paths off a share, network
# adapter descriptions, Defender exclusion paths, event-log text - so every one
# of them is escaped on the way out. A folder on a file server whose name
# contains markup must not become script in the operator's browser.
#
# Every number on the page comes from the kpi mapping a check carried, never
# from a sentence parsed back into a number. A gauge that had to read its value
# out of prose would be a number this renderer invented on the way to the page,
# which is the same defect this analyser exists to prevent, moved downstream.
#
#     import arcgis_pro_performance_analyser as engine, html_report
#     doc = engine.run(engine.Env(), {...})
#     open(path, "w", encoding="utf-8").write(html_report.render_html(doc))
# ============================================================================


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
    return gauge(label, pct, caption, tone,
                 sub=("%s: %s" % (check_id, unit)))


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
                         sub="PF-12"))
    disk = _kpi(by_id.get("PF-13"), "free_pct_worst")
    if disk is not None:
        out.append(gauge("Disque libre (pire volume)", disk,
                         "%.1f Go libres"
                         % (_kpi(by_id["PF-13"], "free_gb_worst") or 0),
                         sub="PF-13"))
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
                         ("warn" if pct >= 33 else "bad"), sub="PF-06"))
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
    rows = []
    for code, label in symptoms:
        ids = flags.get(code) or []
        tone = "bad" if ids else "calm"
        badge = ('<span class="flag hit">%d</span>' % len(ids)) if ids \
            else '<span class="flag none">0</span>'
        links = " ".join('<a href="#check-%s">%s</a>' % (esc(cid), esc(cid))
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
.chead .id{font:600 13px ui-monospace,Consolas,monospace;color:var(--mut)}
.chead .ttl{font-weight:600}
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
            '<li><b><a href="#check-%s">%s</a></b> &mdash; %s</li>'
            % (esc(cid), esc(cid), esc((by_id.get(cid) or {}).get("title", "")))
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


def _check_card(check):
    status = check.get("status", "")
    cls = STATUS_CLASSES.get(status, "st-skipped")
    finding = status in ("Fail", "Warn")
    parts = ['<article class="check is-%s" id="check-%s" data-status="%s" '
             'data-finding="%s" data-dataloss="%s">'
             % (cls.replace("st-", ""), esc(check.get("id")), esc(status),
                "1" if finding else "0",
                "1" if check.get("dataloss") else "0")]
    parts.append('<div class="chead"><span class="id">%s</span>'
                 '<span class="badge %s">%s</span>%s'
                 '<span class="ttl">%s</span>'
                 '<span class="cat2">%s</span></div>'
                 % (esc(check.get("id")), cls, esc(STATUS_FR.get(status, status)),
                    '<span class="dl">perte de donn&eacute;es</span>'
                    if check.get("dataloss") else "",
                    esc(check.get("title")), esc(check.get("category"))))

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

# ============================================================================
# 3/3  ArcGIS Pro toolbox glue
# source: toolbox_glue.py  (sha256:9b8784c4dacf)
# ----------------------------------------------------------------------------
# r"""The ArcGIS Pro glue: two geoprocessing tools over the diagnostic engine
# and the HTML renderer.
#
# This file is a build input, not the shipped artefact. build_toolbox.py inlines
# it below the engine and the renderer to produce the single file
# ArcGISPro_PerformanceAnalyser/ArcGISPro_PerformanceAnalyser.pyt, and that one
# file is what you add to ArcGIS Pro. It stays a separate source because it is
# the only part that imports arcpy, so the engine and the renderer can be tested
# on a machine with no ArcGIS at all.
#
# The two tools appear under "ArcGIS Pro Performance Analyser".
#
# Run "Diagnostiquer la lenteur d'ArcGIS Pro" for the workstation. It writes
# arcgis_pro_performance_analyser_<stamp>.html, .json and .txt into the output
# folder and opens the HTML in the default browser.
#
# Run "Analyser un projet .aprx externe" for an explicitly selected, non-active
# project. That reader snapshots the file, performs bounded ZIP/JSON/source
# checks and can write a verified recovery COPY beside the source. The original
# is never rewritten, and the recovery choice is diagnostic-only by default.
#
# Read this before you trust the numbers
# --------------------------------------
# Running the diagnostic from inside ArcGIS Pro means ArcGIS Pro is running.
# That is not a detail:
#
#   * DL-12 reports Pro as running, which is a data-loss finding in its own
#     right and lands in the act-first list.
#   * PF-22, the read of the .aprx layer refresh timers, is refused. Reading a
#     project file while Pro may be saving it can make the save fail, so the
#     tool refuses rather than cause the very failure it was run to explain.
#   * AP-01, the cache fix, is refused for the same reason.
#
# So a run from inside Pro is a workstation report minus the project-file
# checks. For the complete report, close Pro and run arcgis_pro_performance_analyser.py from a command
# prompt in the editor's own session. The toolbox exists for the run you can do
# without leaving the application, and it says so on the page.
#
# Nothing here signs in to anything and no host is contacted unless you name one.
# The in-Pro workstation tool never changes machine state: AP-01 belongs to the
# command-line run with Pro closed, and the former AP-02 GPU change is retired.
# Only an explicit selected-project recovery mode can write here, and it writes
# a new copy only.
# ============================================================================

import os
import time

import arcpy

# --- merged build: both sections above live in this same module ---
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
def _pro_release():
    """The ArcGIS Pro release this toolbox is running under, or None."""
    try:
        return "%s (%s)" % (arcpy.GetInstallInfo()["Version"],
                            arcpy.GetInstallInfo()["BuildNumber"])
    except Exception:
        return None


def _unique(path):
    """A path that does not already exist.

    the analyser's own report writer stamps to the second, so two runs inside one
    second land on the same name and the second silently overwrites the first.
    """
    root, ext = os.path.splitext(path)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    candidate = "%s_%s%s" % (root, stamp, ext)
    counter = 1
    while os.path.exists(candidate):
        candidate = "%s_%s_%d%s" % (root, stamp, counter, ext)
        counter += 1
    return candidate


def _tail(text, lines=40):
    """The last N non-empty lines of a captured stream."""
    kept = [l for l in (text or "").splitlines() if l.strip()]
    return kept[-lines:]


def _current_project_path():
    """The active project's path, or None when Pro has none/cannot answer."""
    try:
        project = arcpy.mp.ArcGISProject("CURRENT")
        return project.filePath or None
    except Exception:
        return None


def _same_path(left, right):
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == \
        os.path.normcase(os.path.abspath(right))


def _safe_stem(path):
    """A readable report filename without path punctuation."""
    stem = os.path.splitext(os.path.basename(path or "projet"))[0]
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_"
                      for c in stem)
    return cleaned.strip("_")[:80] or "projet"


def _emit_findings(document, messages):
    """Put the explanation and ordered actions in the Pro messages as well."""
    for check in document.get("checks") or []:
        if check.get("status") not in ("Fail", "Warn"):
            continue
        messages.addWarningMessage(
            "[%s] %s — %s" % (check.get("id"),
                              "ANOMALIE" if check.get("status") == "Fail"
                              else "AVERTISSEMENT", check.get("title")))
        evidence = [line for line in (check.get("observed") or "").splitlines()
                    if line.strip()]
        for line in evidence[:3]:
            messages.addMessage("  Mesure : %s" % line)
        if len(evidence) > 3:
            messages.addMessage("  … %d autre(s) ligne(s) de mesure dans le "
                                "rapport." % (len(evidence) - 3))
        if check.get("explanation"):
            messages.addMessage("  Pourquoi : %s" % check["explanation"])
        if check.get("impact_fr") or check.get("impact"):
            messages.addMessage("  Impact ArcGIS Pro : %s"
                                % (check.get("impact_fr") or check.get("impact")))
        for number, step in enumerate(check.get("steps") or [], 1):
            messages.addMessage("  Étape %d : %s" % (number, step))
        if check.get("verification"):
            messages.addMessage("  Vérification : %s" % check["verification"])


RECOVERY_NONE = "Diagnostic uniquement — ne créer aucune copie"
RECOVERY_REPACK = "Reconstruire le conteneur — contenu interne inchangé"
RECOVERY_VIEWS = "Copie de récupération sans vues au démarrage"
RECOVERY_VIEWS_HISTORY = "Copie de récupération sans vues ni historique"
RECOVERY_MODES = {
    RECOVERY_NONE: "none",
    RECOVERY_REPACK: "repack",
    RECOVERY_VIEWS: "views",
    RECOVERY_VIEWS_HISTORY: "views_history",
}


class Toolbox(object):
    def __init__(self):
        self.label = "ArcGIS Pro Performance Analyser"
        self.alias = "properf"
        self.description = (
            "Diagnostique pourquoi ArcGIS Pro est lent sur ce poste, et ne "
            "rapporte jamais une trouvaille que la machine n'a pas "
            "produite. Rend un rapport HTML, contrôle par contrôle : ce qui "
            "a été mesuré, ce que cela coûte à ArcGIS Pro, et quoi faire. "
            "Lecture seule tant que les correctifs ne sont pas cochés.")
        self.tools = [Diagnose, AnalyzeProject]
        self.canRunInBackground = False


class Diagnose(object):
    """The workstation diagnostic, with the result as HTML."""

    def __init__(self):
        self.label = "Diagnostiquer la lenteur d'ArcGIS Pro"
        self.description = (
            "DIAGNOSTIC DU POSTE. Lit ce poste — registre, stockage, "
            "redirection OneDrive, réseau, processeur, carte graphique, "
            "cache, journal d'événements — et rend un rapport HTML qui dit, "
            "contrôle par contrôle, ce qui a été mesuré, ce que cela coûte à "
            "ArcGIS Pro et comment le corriger. Les contrôles portent notamment "
            "sur les modifications qui peuvent se perdre, et "
            "un qui relève dans le journal d'événements les plantages, les "
            "suspensions et les fermetures sans arrêt propre. Lecture seule, "
            "sauf si vous cochez explicitement les correctifs. Écrit un HTML, "
            "un JSON et un texte. Les paramètres par défaut suffisent pour un "
            "premier passage : ne renseignez rien.")
        self.canRunInBackground = False
        self.category = "Diagnostic"

    def getParameterInfo(self):
        def hinted(param, tooltip, long_help):
            """Attache les deux champs d'aide qu'ArcGIS Pro affiche.

            `helpMessage` est l'infobulle derrière le petit point
            d'interrogation ; `parameterHelp` est le paragraphe du volet
            d'aide de l'outil. Les deux sont remplis ici plutôt que laissés au
            libellé, parce que le libellé doit rester assez court pour tenir
            dans le volet — et un libellé court ne peut pas porter d'exemple.
            """
            param.helpMessage = tooltip
            param.parameterHelp = long_help
            return param

        out = arcpy.Parameter(
            displayName="Dossier de sortie",
            name="output_folder",
            datatype="DEFolder",
            parameterType="Optional",
            direction="Input")
        out.value = os.path.join(
            arcpy.env.scratchFolder or os.environ.get("TEMP") or ".",
            "ArcGISPro_PerformanceAnalyser")
        hinted(out,
               "Où écrire les rapports. Laissez vide pour garder le défaut.",
               "<p><b>À quoi ça sert</b> : c'est le dossier où l'outil écrit "
               "ses trois fichiers — le rapport <b>HTML</b> (celui qu'on lit), "
               "le <b>JSON</b> (pour comparer deux passages) et le <b>TXT</b> "
               "(pour coller dans un ticket).</p>"
               "<p><b>Défaut</b> : <code>&lt;scratch&gt;\\ArcGISPro_Performance"
               "Analyser</code>. Le dossier est créé s'il n'existe pas.</p>"
               "<p><b>Exemple</b> : <code>C:\\Temp\\diag_analyser</code>. "
               "Choisissez un dossier <b>local</b>, jamais un partage réseau ni "
               "un dossier OneDrive : l'écriture doit rester rapide et ne "
               "dépendre d'aucun serveur. Un rapport écrit sur un partage "
               "synchronisé peut être lu à moitié.</p>"
               "<p><b>Rien d'existant n'est écrasé</b> : deux passages dans la "
               "même seconde produisent deux jeux de fichiers distincts.</p>")

        sde = arcpy.Parameter(
            displayName="Hôte de la géodatabase d'entreprise à sonder",
            name="sde_server",
            datatype="GPString",
            parameterType="Optional",
            direction="Input")
        sde.category = "Sondes réseau (sur demande)"
        hinted(sde,
               "Nom du serveur SQL/Oracle de vos données. Vide = aucun hôte "
               "contacté.",
               "<p><b>À quoi ça sert</b> : une latence élevée vers ce serveur "
               "explique à elle seule un Pro lent à l'ouverture des cartes. Le "
               "contrôle DL-10 mesure le temps de réponse.</p>"
               "<p><b>Laissez vide si vous ne savez pas quoi mettre.</b> Vide, "
               "aucun paquet ne quitte la machine et DL-10 répond "
               "<code>Info</code> au lieu d'inventer une mesure.</p>"
               "<p>Renseigné, l'outil ouvre <b>une seule</b> connexion TCP, "
               "mesure le temps de réponse, puis referme. Il ne s'authentifie "
               "jamais et n'envoie aucun identifiant — il n'existe d'ailleurs "
               "aucun paramètre de mot de passe.</p>"
               "<p><b>Exemple</b> : <code>gisdb01</code> ou "
               "<code>gisdb01.monorg.ch</code>. C'est le serveur nommé dans "
               "vos fichiers de connexion <code>.sde</code> ; le contrôle "
               "DL-11 les liste pour vous.</p>")

        port = arcpy.Parameter(
            displayName="Port de la géodatabase",
            name="sde_port",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input")
        port.value = 1433
        port.category = "Sondes réseau (sur demande)"
        hinted(port,
               "Port du serveur. 1433 = SQL Server, le plus courant.",
               "<p><b>À quoi ça sert</b> : c'est le port sur lequel la sonde "
               "DL-10 se connecte. Un port fermé se lit comme un serveur "
               "injoignable, ce qui est une fausse piste si le moteur n'est "
               "simplement pas le bon.</p>"
               "<p><b>Défaut</b> : <code>1433</code> (SQL Server).</p>"
               "<p><b>Valeurs courantes</b> : <code>1433</code> SQL Server, "
               "<code>1521</code> Oracle, <code>5432</code> PostgreSQL.</p>"
               "<p><b>Exemple</b> : si vos <code>.sde</code> nomment un serveur "
               "PostgreSQL, mettez <code>5432</code>. Dans le doute, laissez "
               "1433 : l'outil ne devine jamais, il dit ce qu'il a trouvé.</p>"
               "<p>Les valeurs hors de 1 à 65535 sont refusées avant "
               "l'exécution.</p>")

        portal = arcpy.Parameter(
            displayName="Hôte du portail à sonder (port 443)",
            name="portal",
            datatype="GPString",
            parameterType="Optional",
            direction="Input")
        portal.category = "Sondes réseau (sur demande)"
        hinted(portal,
               "Adresse de votre portail ou d'ArcGIS Online. Vide = aucun "
               "hôte contacté.",
               "<p><b>À quoi ça sert</b> : Pro interroge le portail au "
               "démarrage et pour chaque couche publiée. Un portail lent se "
               "lit comme un Pro lent à l'ouverture, sans qu'aucune couche "
               "soit en cause. Le contrôle PF-19 mesure cette réponse.</p>"
               "<p><b>Laissez vide si vous ne savez pas quoi mettre.</b></p>"
               "<p><b>Exemple</b> : <code>gis.monorg.ch</code> pour un Portal "
               "for ArcGIS interne, ou <code>www.arcgis.com</code> pour "
               "ArcGIS Online.</p>"
               "<p>Une seule connexion TCP sur le port 443, mesurée puis "
               "refermée. Aucun identifiant n'est envoyé, aucune page n'est "
               "téléchargée.</p>")

        timeout = arcpy.Parameter(
            displayName="Délai des sondes (millisecondes)",
            name="timeout",
            datatype="GPLong",
            parameterType="Optional",
            direction="Input")
        timeout.value = 4000
        timeout.category = "Sondes réseau (sur demande)"
        hinted(timeout,
               "Plafond d'attente des sondes réseau. Une borne, pas une "
               "attente.",
               "<p><b>À quoi ça sert</b> : c'est le temps maximal que DL-10 et "
               "PF-19 attendent avant de déclarer l'hôte injoignable.</p>"
               "<p><b>Défaut</b> : <code>4000</code> ms.</p>"
               "<p><b>C'est une borne, pas une attente</b> : une sonde qui "
               "répond en 40 ms rend la main aussitôt. Rien n'est bloqué "
               "pendant quatre secondes quand le serveur est rapide.</p>"
               "<p><b>Exemple</b> : montez à <code>10000</code> sur une liaison "
               "VPN lente, pour ne pas lire « injoignable » sur un serveur qui "
               "répond en six secondes. Descendez à <code>1500</code> pour un "
               "passage rapide en atelier.</p>"
               "<p>Ce paramètre ne concerne <b>que</b> les sondes que vous avez "
               "demandées : videz les deux hôtes et il ne sert plus à "
               "rien.</p>")

        verbose = arcpy.Parameter(
            displayName="Détaillé",
            name="verbose",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        verbose.value = False
        hinted(verbose,
               "Affiche ce que l'outil attendait et ce qu'il a réellement "
               "lu.",
               "<p><b>À quoi ça sert</b> : à <b>prouver</b> un résultat à "
               "quelqu'un. Chaque contrôle montre alors la valeur attendue et "
               "la ligne brute lue sur la machine, en plus du verdict. C'est "
               "ce qu'il faut joindre quand le support demande « sur quoi vous "
               "basez-vous ? ».</p>"
               "<p><b>Défaut</b> : décoché.</p>"
               "<p>Décoché, le rapport reste complet et tous les verdicts sont "
               "là ; seules ces lignes de preuve sont repliées.</p>"
               "<p><b>Exemple</b> : cochez-le avant un appel au support Esri, "
               "puis joignez le <code>.txt</code> — il contiendra les valeurs "
               "brutes qui appuient chaque ligne.</p>")

        text = arcpy.Parameter(
            displayName="Écrire aussi le rapport texte",
            name="write_text",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        text.value = True
        hinted(text,
               "Ajoute un .txt, pratique à coller dans un ticket.",
               "<p><b>À quoi ça sert</b> : le <code>.txt</code> reprend le "
               "rapport HTML en texte brut. C'est le format à coller dans un "
               "appel au support ou dans un courriel, parce qu'il ne dépend "
               "d'aucun navigateur et qu'il passe dans n'importe quelle "
               "messagerie.</p>"
               "<p><b>Défaut</b> : coché.</p>"
               "<p><b>Exemple</b> : laissez-le coché. Décochez-le seulement si "
               "le dossier de sortie est soumis à un quota serré, ou si vous "
               "ne voulez qu'un seul fichier.</p>"
               "<p>Le JSON est écrit dans tous les cas : c'est lui qui permet "
               "de comparer deux passages à quelques jours d'écart.</p>")

        browser = arcpy.Parameter(
            displayName="Ouvrir le rapport HTML dans le navigateur",
            name="open_browser",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        browser.value = True
        hinted(browser,
               "Ouvre le rapport à la fin. Décochez pour un lot ou un "
               "modèle.",
               "<p><b>À quoi ça sert</b> : le rapport s'ouvre dans le "
               "navigateur par défaut dès qu'il est écrit, pour ne pas avoir à "
               "chercher le fichier.</p>"
               "<p><b>Défaut</b> : coché.</p>"
               "<p><b>Exemple</b> : décochez-le si l'outil tourne dans un "
               "modèle ModelBuilder ou dans un lot — une fenêtre qui s'ouvre à "
               "chaque itération bloque le lot. Décochez-le aussi si le "
               "navigateur est bloqué par stratégie de groupe.</p>"
               "<p><b>Le fichier est écrit dans tous les cas</b> et son chemin "
               "apparaît dans les messages de l'outil : rien n'est perdu si "
               "l'ouverture échoue.</p>")

        apply_fixes = arcpy.Parameter(
            displayName="Correctifs du poste (indisponibles dans ArcGIS Pro)",
            name="apply_fixes",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        apply_fixes.value = False
        # Keep the parameter for compatibility with existing models, but never
        # let this in-Pro entry point mutate the application that hosts it.
        apply_fixes.enabled = False
        apply_fixes.category = "Lecture seule dans ArcGIS Pro"
        hinted(apply_fixes,
               "Désactivé : le diagnostic dans ArcGIS Pro ne modifie jamais le "
               "poste. Le correctif de cache se lance hors de Pro.",
               "<p><b>À quoi ça sert</b> : ce paramètre historique est "
               "conservé pour les modèles existants, mais il est "
               "<b>désactivé</b>. Le diagnostic exécuté dans ArcGIS Pro est "
               "toujours en lecture seule.</p>"
               "<p><b>Défaut</b> : <b>décoché et désactivé</b>.</p>"
               "<p><b>AP-01</b>, seul correctif de poste encore automatisé, "
               "renomme le cache sans le supprimer et affiche sa commande "
               "d'annulation. Il doit être lancé avec le script Python, "
               "<b>ArcGIS Pro fermé</b>.</p>"
               "<p><b>AP-02 est retiré</b> : forcer le GPU hautes performances "
               "peut provoquer un crash au splash screen si son pilote est "
               "instable. Si un ancien passage a appliqué AP-02, utilisez la "
               "commande <code>undo</code> enregistrée dans son rapport JSON "
               "pour restaurer exactement la valeur précédente.</p>"
               "<p><b>Exemple</b> : laissez ce paramètre désactivé ici. Pour "
               "un cache à reconstruire, fermez Pro et utilisez "
               "<code>python arcgis_pro_performance_analyser.py --apply</code> "
               "après avoir lu le diagnostic.</p>")

        html_out = arcpy.Parameter(
            displayName="Rapport HTML",
            name="html_report",
            datatype="GPString",
            parameterType="Derived",
            direction="Output")
        hinted(html_out,
               "Sortie : chemin du rapport HTML. Ne se remplit pas à la main.",
               "<p><b>À quoi ça sert</b> : c'est une <b>sortie dérivée</b> — "
               "l'outil la remplit lui-même avec le chemin du rapport qu'il "
               "vient d'écrire.</p>"
               "<p><b>Ne la remplissez pas</b> : elle est en lecture seule et "
               "toute valeur saisie serait ignorée.</p>"
               "<p>Elle existe pour que l'outil puisse être <b>enchaîné</b> "
               "dans un modèle ModelBuilder.</p>"
               "<p><b>Exemple</b> : dans un modèle, reliez cette sortie à un "
               "outil « Envoyer un courrier » pour joindre automatiquement le "
               "rapport du jour.</p>")

        return [out, sde, port, portal, timeout, verbose, text, browser,
                apply_fixes, html_out]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        """Refuse what the analyser's own parser would refuse, before it runs."""
        port = parameters[2].value
        if port is not None and not 1 <= int(port) <= 65535:
            parameters[2].setErrorMessage(
                "The port must be between 1 and 65535, got %s." % port)
        timeout = parameters[4].value
        if timeout is not None and int(timeout) < 1:
            parameters[4].setErrorMessage(
                "The timeout must be at least 1 millisecond.")
        for index, flag in ((1, "geodatabase host"), (3, "portal host")):
            host = parameters[index].valueAsText
            if host:
                problem = engine.host_problem(host)
                if problem:
                    parameters[index].setErrorMessage(
                        "%r is not a host name: %s" % (host, problem))
        if parameters[8].value:
            parameters[8].setErrorMessage(
                "Workstation fixes cannot run inside ArcGIS Pro. AP-01 must "
                "run from a command prompt with Pro closed. The former AP-02 "
                "GPU fix was retired because forcing an adapter can cause a "
                "startup crash; use an older report's exact undo command if "
                "that change needs to be reversed.")
        return

    def execute(self, parameters, messages):
        folder = (parameters[0].valueAsText
                  or os.path.join(os.environ.get("TEMP") or ".", "ArcGISPro_PerformanceAnalyser"))
        opts = {
            "sde_server": parameters[1].valueAsText or None,
            "sde_port": int(parameters[2].value or 1433),
            "portal": parameters[3].valueAsText or None,
            "timeout": int(parameters[4].value or 4000),
            # Defence in depth: even a caller bypassing the disabled parameter
            # and its validation cannot mutate the running Pro workstation.
            "apply": False,
        }
        verbose = bool(parameters[5].value)

        messages.addMessage("ArcGIS Pro Performance Analyser %d - reading this workstation."
                            % engine.REPORT_VERSION)
        if opts["sde_server"] or opts["portal"]:
            messages.addMessage(
                "Network probes: %s"
                % ", ".join(filter(None, [opts["sde_server"], opts["portal"]])))
        else:
            messages.addMessage(
                "No host will be contacted. Name a geodatabase or portal host "
                "to add the two bounded TCP probes.")
        messages.addMessage(
            "Note: a project folder that already lives on a share is read, "
            "because that is what Pro does when it opens the project, and "
            "PF-03 times one listing of it.")

        started = time.time()
        env = engine.Env()
        document = engine.run(env, opts)
        code = engine.exit_code(document["checks"])
        document["exit_code"] = code
        document["symptoms"] = list(engine.SYMPTOMS)
        messages.addMessage("Read %d checks in %.1f s."
                            % (len(document["checks"]), time.time() - started))

        if not os.path.isdir(folder):
            os.makedirs(folder)
        html_path = _unique(os.path.join(folder, "arcgis_pro_performance_analyser.html"))
        json_path = os.path.splitext(html_path)[0] + ".json"

        meta = {}
        release = _pro_release()
        if release:
            meta["ArcGIS Pro"] = release
        meta["ran from"] = "the ArcGIS Pro geoprocessing pane, so Pro was open"

        report.write_html(document, html_path, verbose, code, meta)
        handle = open(json_path, "wb")
        try:
            import json as _json
            handle.write(_json.dumps(document, indent=1, sort_keys=True)
                         .encode("utf-8"))
        finally:
            handle.close()
        written = [html_path, json_path]

        if parameters[6].value:
            text_path = os.path.splitext(html_path)[0] + ".txt"
            handle = open(text_path, "wb")
            try:
                handle.write(("\n".join(engine.render(document, verbose))
                              + "\n").encode("utf-8"))
            finally:
                handle.close()
            written.append(text_path)

        # The summary, in the messages window, so the operator sees the
        # verdict without leaving ArcGIS Pro.
        for status, count in engine.counts(document["checks"]):
            if count:
                messages.addMessage("  %-8s %d" % (status.upper(), count))
        _emit_findings(document, messages)
        if document["act_first"]:
            messages.addWarningMessage(
                "ACT ON THESE FIRST - they bear on the vanished edits: %s"
                % ", ".join(document["act_first"]))
        for note in document["notes"]:
            messages.addMessage("  fix gate: %s" % note)
        for record in document["applied"]:
            messages.addWarningMessage(
                "APPLIED [%s] %s  undo: %s"
                % (record["id"], record["result"], record["undo"]))
        for path in written:
            messages.addMessage("  report: %s" % path)

        parameters[9].value = html_path

        if parameters[7].value:
            try:
                import webbrowser
                webbrowser.open("file:///" + html_path.replace("\\", "/"))
                messages.addMessage("Opened the HTML report in the default "
                                    "browser.")
            except Exception as exc:
                messages.addWarningMessage(
                    "Could not open a browser (%s). The report is at %s"
                    % (exc, html_path))

        messages.addMessage(
            "The report carries the account name, local paths, connection file "
            "names and exclusion paths. Read it before you send it to anyone.")
        messages.addMessage("Analyser exit code %d: %s"
                            % (code, report.EXIT_MEANING.get(code, "?")))
        return


class AnalyzeProject(object):
    """Read one explicitly selected, non-active .aprx and optionally recover a copy."""

    def __init__(self):
        self.label = "Analyser un projet .aprx externe"
        self.description = (
            "Sélectionnez un fichier .aprx autre que le projet actuellement "
            "ouvert. L'outil en fait une copie instantanée temporaire, vérifie "
            "le conteneur ZIP, les documents JSON, les vues restaurées, "
            "l'historique, les sources et la charge des cartes. Le rapport "
            "explique chaque problème, son impact dans ArcGIS Pro et les "
            "étapes de correction. En option, il crée une copie de récupération "
            "sans vues au démarrage et/ou sans historique. L'original n'est "
            "jamais modifié.")
        self.canRunInBackground = False
        self.category = "Diagnostic"

    def getParameterInfo(self):
        def hinted(param, tooltip, long_help):
            param.helpMessage = tooltip
            param.parameterHelp = long_help
            return param

        project = arcpy.Parameter(
            displayName="Projet .aprx à analyser (pas le projet ouvert)",
            name="project",
            datatype="DEFile",
            parameterType="Required",
            direction="Input")
        project.filter.list = ["aprx"]
        hinted(project,
               "Sélectionnez un .aprx différent de celui qui est ouvert dans "
               "cette session ArcGIS Pro.",
               "<p><b>À quoi ça sert</b> : choisir précisément le projet à "
               "diagnostiquer, au lieu d'analyser les projets récents ou le "
               "projet actif.</p><p><b>Obligatoire</b> : un fichier "
               "<code>.aprx</code>. Le projet actuellement ouvert est refusé, "
               "même si vous demandez seulement un diagnostic.</p>"
               "<p>L'outil travaille sur une <b>copie instantanée temporaire"
               "</b>. Si le fichier change pendant cette copie, l'analyse est "
               "abandonnée pour ne pas mélanger deux enregistrements.</p>"
               "<p><b>Exemple</b> : <code>C:\\GIS\\Anciens\\Parcelles.aprx</code>."
               " Vous pouvez laisser un autre projet ouvert dans Pro.</p>")

        out = arcpy.Parameter(
            displayName="Dossier des rapports",
            name="output_folder",
            datatype="DEFolder",
            parameterType="Optional",
            direction="Input")
        out.value = os.path.join(
            arcpy.env.scratchFolder or os.environ.get("TEMP") or ".",
            "ArcGISPro_PerformanceAnalyser")
        hinted(out,
               "Dossier local où écrire le HTML, le JSON et le TXT.",
               "<p><b>À quoi ça sert</b> : recevoir les trois rapports. Ce "
               "dossier ne reçoit <b>pas</b> la copie de récupération du "
               "projet.</p><p><b>Défaut</b> : <code>&lt;scratch&gt;\\ArcGISPro_"
               "PerformanceAnalyser</code>.</p><p>La copie .aprx est écrite "
               "<b>à côté de l'original</b> pour préserver les chemins relatifs "
               "des données. Son nom reçoit un suffixe explicite et un fichier "
               "existant n'est jamais écrasé.</p>")

        repair = arcpy.Parameter(
            displayName="Action de récupération sur une copie",
            name="repair_mode",
            datatype="GPString",
            parameterType="Optional",
            direction="Input")
        repair.value = RECOVERY_NONE
        repair.filter.type = "ValueList"
        repair.filter.list = [RECOVERY_NONE, RECOVERY_REPACK, RECOVERY_VIEWS,
                              RECOVERY_VIEWS_HISTORY]
        repair.category = "Récupération — jamais sur l'original"
        hinted(repair,
               "Diagnostic seul par défaut. Les autres choix créent un nouveau "
               ".aprx à côté de l'original.",
               "<p><b>À quoi ça sert</b> : tenter une récupération ciblée "
               "<b>sans jamais réécrire le projet d'origine</b>.</p>"
               "<ul><li><b>Diagnostic uniquement</b> (défaut) : aucun nouveau "
               "projet.</li><li><b>Reconstruire le conteneur</b> : réécrit le "
               "ZIP sans changer les documents ; utile seulement si tous les "
               "éléments sont lisibles.</li><li><b>Sans vues au démarrage</b> : "
               "retire <code>views</code> et <code>viewLayoutXML</code> de la "
               "copie pour ne pas rouvrir une carte ou mise en page qui fait "
               "planter Pro. Les définitions des cartes et mises en page sont "
               "conservées.</li><li><b>Sans vues ni historique</b> : retire "
               "aussi les éléments <code>GPHistory</code> de la copie.</li></ul>"
               "<p><b>Garanties</b> : intégrité revérifiée avant de nommer la "
               "copie, aucun écrasement, original intact. La récupération est "
               "refusée si les contrôles nécessaires ne sont pas Conforme. "
               "Elle ne garantit pas de réparer toutes les corruptions.</p>")

        verbose = arcpy.Parameter(
            displayName="Inclure les preuves détaillées",
            name="verbose",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        verbose.value = False
        hinted(verbose,
               "Ajoute les valeurs attendues et preuves techniques au TXT.",
               "<p><b>À quoi ça sert</b> : conserver les détails nécessaires "
               "à un ticket de support.</p><p><b>Défaut</b> : décoché. Les "
               "explications, impacts et étapes restent affichés dans tous "
               "les cas.</p><p><b>Exemple</b> : cochez avant d'envoyer le "
               "rapport à Esri ou à votre administrateur SIG.</p>")

        text = arcpy.Parameter(
            displayName="Écrire aussi le rapport texte",
            name="write_text",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        text.value = True
        hinted(text,
               "Ajoute un TXT contenant les mêmes étapes de correction.",
               "<p><b>À quoi ça sert</b> : copier facilement le diagnostic "
               "dans un ticket ou un courriel.</p><p><b>Défaut</b> : coché. "
               "Le HTML et le JSON sont écrits dans tous les cas.</p>")

        browser = arcpy.Parameter(
            displayName="Ouvrir le rapport HTML",
            name="open_browser",
            datatype="GPBoolean",
            parameterType="Optional",
            direction="Input")
        browser.value = True
        hinted(browser,
               "Ouvre le rapport à la fin de l'analyse.",
               "<p><b>À quoi ça sert</b> : lire immédiatement les explications "
               "et les étapes numérotées.</p><p><b>Défaut</b> : coché. Le "
               "chemin du fichier reste affiché même si le navigateur ne "
               "s'ouvre pas.</p>")

        html_out = arcpy.Parameter(
            displayName="Rapport HTML du projet",
            name="html_report",
            datatype="GPString",
            parameterType="Derived",
            direction="Output")
        hinted(html_out,
               "Sortie dérivée : chemin du rapport HTML.",
               "<p><b>À quoi ça sert</b> : chaîner le rapport dans un modèle. "
               "Ne renseignez pas cette sortie vous-même.</p>")

        recovered = arcpy.Parameter(
            displayName="Projet de récupération créé",
            name="recovered_project",
            datatype="DEFile",
            parameterType="Derived",
            direction="Output")
        hinted(recovered,
               "Sortie dérivée : nouveau .aprx, vide en diagnostic seul.",
               "<p><b>À quoi ça sert</b> : retrouver la copie créée et la "
               "chaîner à un autre outil.</p><p>Elle reste vide en diagnostic "
               "seul, si aucune modification n'est nécessaire, ou si les "
               "contrôles de sécurité refusent la récupération.</p>")
        return [project, out, repair, verbose, text, browser, html_out, recovered]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        return

    def updateMessages(self, parameters):
        path = parameters[0].valueAsText.strip()
        if path and not path.lower().endswith(".aprx"):
            parameters[0].setErrorMessage(
                "Sélectionnez un fichier portant l'extension .aprx.")
        elif path and not os.path.isfile(path):
            parameters[0].setErrorMessage(
                "Le fichier sélectionné est introuvable ou inaccessible : %s" % path)
        current = _current_project_path()
        if path and _same_path(path, current):
            parameters[0].setErrorMessage(
                "Ce .aprx est le projet actuellement ouvert. Enregistrez-le, "
                "ouvrez un autre projet, puis sélectionnez à nouveau ce fichier "
                "ou utilisez une copie. L'outil refuse le projet actif.")
        mode = parameters[2].valueAsText or RECOVERY_NONE
        if mode not in RECOVERY_MODES:
            parameters[2].setErrorMessage(
                "Choisissez une des quatre actions proposées dans la liste.")
        elif mode != RECOVERY_NONE:
            parameters[2].setWarningMessage(
                "L'original restera intact. La récupération crée un nouveau "
                "fichier à côté de lui et seulement si les contrôles d'intégrité "
                "l'autorisent.")
        if mode == RECOVERY_VIEWS_HISTORY:
            parameters[0].setWarningMessage(
                "L'historique de géotraitement sera retiré de la copie seulement. "
                "Conservez l'original pour retrouver les anciens paramètres.")
        return

    def execute(self, parameters, messages):
        path = parameters[0].valueAsText.strip()
        current = _current_project_path()
        if not path or not path.lower().endswith(".aprx"):
            raise arcpy.ExecuteError("Un fichier .aprx doit être sélectionné.")
        if _same_path(path, current):
            raise arcpy.ExecuteError(
                "Le projet actif ne peut pas être analysé par cet outil. "
                "Ouvrez un autre projet ou sélectionnez une copie.")
        mode_label = parameters[2].valueAsText or RECOVERY_NONE
        if mode_label not in RECOVERY_MODES:
            raise arcpy.ExecuteError("Action de récupération inconnue.")
        mode = RECOVERY_MODES[mode_label]
        folder = (parameters[1].valueAsText or os.path.join(
            os.environ.get("TEMP") or ".", "ArcGISPro_PerformanceAnalyser"))
        verbose = bool(parameters[3].value)

        messages.addMessage("Analyse du projet externe : %s" % path)
        messages.addMessage("Copie instantanée temporaire ; projet actif et "
                            "fichier source non modifiés par le diagnostic.")
        messages.addMessage("Action : %s" % mode_label)
        started = time.time()
        document = engine.run_project(engine.Env(), {
            "project_path": path, "repair": mode})
        code = engine.exit_code(document["checks"])
        document["exit_code"] = code
        messages.addMessage("%d contrôles en %.1f s."
                            % (len(document["checks"]), time.time() - started))

        if not os.path.isdir(folder):
            os.makedirs(folder)
        base = "diagnostic_projet_%s.html" % _safe_stem(path)
        html_path = _unique(os.path.join(folder, base))
        json_path = os.path.splitext(html_path)[0] + ".json"
        meta = {"Projet analysé": path,
                "Action demandée": mode_label,
                "Méthode": "copie instantanée locale ; original jamais réécrit"}
        release = _pro_release()
        if release:
            meta["ArcGIS Pro"] = release
        report.write_html(document, html_path, verbose, code, meta)
        handle = open(json_path, "wb")
        try:
            import json as _json
            handle.write(_json.dumps(document, indent=1, sort_keys=True)
                         .encode("utf-8"))
        finally:
            handle.close()
        written = [html_path, json_path]
        if parameters[4].value:
            text_path = os.path.splitext(html_path)[0] + ".txt"
            handle = open(text_path, "wb")
            try:
                handle.write(("\n".join(engine.render(document, verbose))
                              + "\n").encode("utf-8"))
            finally:
                handle.close()
            written.append(text_path)

        for status, count in engine.counts(document["checks"]):
            if count:
                messages.addMessage("  %-8s %d" % (status.upper(), count))
        _emit_findings(document, messages)
        for note in document["notes"]:
            if "REFUSÉE" in note or "ÉCHEC" in note:
                messages.addWarningMessage(note)
            else:
                messages.addMessage(note)
        for record in document["applied"]:
            messages.addWarningMessage(
                "COPIE CRÉÉE [%s] %s\n%s\nRetour arrière : %s"
                % (record["id"], record["what"], record["result"],
                   record["undo"]))
        for written_path in written:
            messages.addMessage("  report: %s" % written_path)
        parameters[6].value = html_path
        parameters[7].value = document.get("recovery", {}).get("path") or ""

        if parameters[5].value:
            try:
                import webbrowser
                webbrowser.open("file:///" + html_path.replace("\\", "/"))
                messages.addMessage("Rapport HTML ouvert dans le navigateur.")
            except Exception as exc:
                messages.addWarningMessage(
                    "Ouverture du navigateur impossible (%s). Rapport : %s"
                    % (exc, html_path))
        messages.addMessage("Le rapport contient des chemins et noms de couches. "
                            "Relisez-le avant de le transmettre.")
        messages.addMessage("Code de sortie %d : %s"
                            % (code, report.EXIT_MEANING.get(code, "?")))
        return
