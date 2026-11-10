# ArcGIS Pro Performance Analyser

Diagnose why ArcGIS Pro is slow on this workstation, and never report a finding the machine did
not produce. Read-only by default: nothing changes, and no host is contacted unless you name it.
Explicit options can write reports, run one gated cache fix, create a verified `.aprx` recovery
copy, or quarantine one exact startup package with SHA-256 verification and a restore manifest.
The one read that crosses the network unasked is a project folder that already lives
on a share, as Pro does when it opens the project.

An editor tells you Pro takes five minutes to open, that it hangs for two or three minutes in the
middle of an edit, and that sometimes the work she saved before the hang is gone afterwards. There
is no error message. There is no crash dump, because she ended the hang with Task Manager and that
writes no dump at all.

So you run a diagnostic. It prints a red line at the top of the report, under "act on these first",
naming a database fault as the root cause. Then you read the same check's own evidence line, three
characters further down, and it says "not observable from this workstation". The check had set its
status to Fail before the branch that would have gone and measured anything. It never contacted the
database. It could not have.

That is the defect this tool was rebuilt around. Two checks did it, they were the two most serious
lines in the report, and once an operator sees one verdict contradict its own evidence, the other
forty checks are worth nothing to him either. The three checks that could not be measured from a
workstation are gone. What replaced them is a rule the record constructor enforces: a check that
says it did not measure cannot carry a verdict, and the self-test asserts it on every status, on
every check, and across a whole run.

The next round went after the causes the first one never measured, and the same rule caught a new
liar on the first day. Ask Microsoft Defender for its exclusion lists from the editor's own
session, and it does not refuse. It answers, and where each list should be it puts one line:
`N/A: Must be an administrator to view exclusions`. Compare that line against the folders Pro
needs, and every folder reads as missing. The report then asks the security team for exclusions
that may already exist, off a list it never saw. The analyser recognises that line, says the lists are
hidden, and gives no verdict. The same run on the test workstation showed the pattern twice more:
the network adapter's power setting came back empty, and the SMB client log answered "Access is
denied". Both are reported as unread, not as healthy.

```
$ python arcgis_pro_performance_analyser.py --self-test
----------------------------------------------------------------------
the status contract: a verdict needs a measurement
PASS  Pass is an accepted status
PASS  Warn is an accepted status
PASS  Fail is an accepted status
PASS  Info is an accepted status
PASS  Skipped is an accepted status
PASS  a sixth status is refused, which is what lets the summary be summed
PASS  a check saying it is not observable from the workstation cannot carry Pass  <-- pinned defect
PASS  a check saying it is not observable from the workstation cannot carry Warn  <-- pinned defect
PASS  a check saying it is not observable from the workstation cannot carry Fail  <-- pinned defect
PASS  the same text is fine on Info, which claims nothing
PASS  the same text is fine on Skipped, which claims nothing
PASS  measured=False refuses a verdict even when the text reads clean  <-- pinned defect
PASS  a fixable flag with no fix text is refused, because the summary offers it to the operator by id
PASS  an absent setting is not an unmeasured check: a settings list full of <absent> can still carry a real verdict  <-- pinned defect
...
----------------------------------------------------------------------
containment: a probe that dies is skipped, never warned
PASS  a body that throws records Skipped, not Warn: every fix gates on Warn or Fail, so Warn would let a fix fire on a determination that was never made  <-- pinned defect
PASS  the skipped record names the exception, so the operator sees which probe died
PASS  a body that emits nothing is Skipped, because saying nothing reads as saying Pass  <-- pinned defect
PASS  a body that dies after one record keeps it
PASS  a body that pairs a verdict with no measurement is contained as Skipped, not a traceback  <-- pinned defect
----------------------------------------------------------------------
the act-first roll-up owns no id list
PASS  act-first is exactly the data-loss checks that failed or warned
PASS  a data-loss check demoted to Info is not promoted back by a hardcoded id list: a roll-up that keeps a finding the check itself withdrew is a verdict nobody measured  <-- pinned defect
PASS  a failing check that does not bear on data loss stays out
PASS  and it is ordered worst first, which is the order the summary prints: a tally led by Pass buries the finding under the count of everything that was fine  <-- pinned defect
PASS  two records under one id answer with the first, not the last: the apply gate reads by id, and answering with a later Pass would let a fix run against a failure nothing withdrew  <-- pinned defect
PASS  a run whose only record is Skipped exits 3, not 0: a wrapper reading the code cannot see the stderr note, and 0 told it the machine was fine when nothing was read  <-- pinned defect
----------------------------------------------------------------------
path handling answers for the path, not for the host
PASS  a Windows drive is recognised on THIS host, whichever host it is: os.path is the module the host happens to run, and on Linux it finds no drive in a Windows path at all  <-- pinned defect
PASS  a Known Folder Move root is matched by its name segment, because the OneDrive variable is unset in an elevated shell  <-- pinned defect
PASS  a folder that merely starts with the word does not match
PASS  network beats OneDrive: the share latency is the problem to fix first, whether or not the folder also syncs
PASS  and exactly a kilobyte is one: the unit boundary is inclusive, so no size falls through and prints in the unit below itself  <-- pinned defect
PASS  a percent variable is expanded from the environment this run reads, never from the host process: on Linux the standard expander leaves %NAME% untouched and the check would read the wrong folder  <-- pinned defect
...
PASS  fourteen files of which all fourteen are orphaned is stale: the absolute count this replaced was dead code on a folder Pro keeps under fifty files, and reported PASS on exactly the state its own expected text calls the failure  <-- pinned defect
PASS  half stale is not stale: the trigger is proportional, not a count
PASS  and an old folder with nothing stale in it still trips the age backstop
PASS  one file under it is not: both size triggers are pinned to their numbers, so reading the constant back would not prove them
PASS  of two cache files aged twenty and forty days one is stale: the window is thirty days, and the forty-day log sitting beside them is not counted because it is not a cache file  <-- pinned defect
----------------------------------------------------------------------
every decision, on both sides of its branch
...
PASS  and the title says so, instead of printing the accusation with a PASS in front of it  <-- pinned defect
PASS  no Offline Files policy value: undetermined, and deliberately NOT inferred from the service start value, which is boot-started on stock Windows either way  <-- pinned defect
PASS  no crash dump is Info, never Pass: a hang ended from Task Manager writes no dump at all, so no dump is absence of evidence  <-- pinned defect
PASS  a portal answering in 800 ms passes, though the same 800 ms would warn against the database: the portal is allowed to be slower, and each probe carries its own allowance  <-- pinned defect
PASS  a leftover folder holding a project file warns, because it is a recovery lead for the vanished edits and not disk churn  <-- pinned defect
PASS  but a registry that could not be read at all is Info: unreadable and absent are different answers, and only one of them is a finding  <-- pinned defect
PASS  memory that could not be read is Info: without that guard a failed read computed zero and told the operator to replace memory that was probably fine  <-- pinned defect
PASS  ten gigabytes free of forty is twenty-five percent, well clear of the percentage floor, and still warns: the twenty gigabyte floor fires on its own  <-- pinned defect
PASS  fifty gigabytes free of a thousand clears the twenty gigabyte floor and warns on five percent: the ten percent floor fires on its own too  <-- pinned defect
PASS  the high performance plan passes by its identifier alone, under a name that says nothing: a managed image renames the plan, so the guid is the only part that survives  <-- pinned defect
PASS  the symbology check is always Info: no client setting was found that governs it, and none is invented here  <-- pinned defect
----------------------------------------------------------------------
network power, the event timeline, SMB, share latency, Defender and the .aprx
PASS  one adapter that reads Disabled beside one nobody could read is Info, never Pass: a Pass off a partial reading is a verdict about a device nobody measured  <-- pinned defect
PASS  a reply that is not XML is an unread log, not an empty one  <-- pinned defect
PASS  a Winlogon 1002 is not an Application Hang, even naming the same program: the id alone would report a Pro hang on a machine where Pro never hung  <-- pinned defect
PASS  no event in the logs that were read is Info, never Pass: a hang the editor waits out leaves no event at all  <-- pinned defect
PASS  and the log that could not be read is named rather than counted as quiet  <-- pinned defect
PASS  400 ms warns: the boundary is inclusive  <-- pinned defect
PASS  the text an unprivileged read gets in place of the lists is recognised as hidden, and decides nothing  <-- pinned defect
PASS  and it is never compared as a path, which reported every baseline folder missing on a machine that may exclude them all  <-- pinned defect
PASS  lists hidden by policy are not read as empty  <-- pinned defect
PASS  an exclusion written as %APPDATA% does not cover the editor's folder: Defender expands it as LocalSystem, under the system profile  <-- pinned defect
PASS  but it does not stand for three folders  <-- pinned defect
PASS  and when nobody knows whether Pro runs, it is not opened either  <-- pinned defect
PASS  a home folder stored as '.' is resolved against the project folder before it is classified, so a project on a share has its home on the share, not on no drive at all  <-- pinned defect
PASS  a project only partly read is Info, never Pass: the timer could be in the part the cap left out  <-- pinned defect
...
----------------------------------------------------------------------
no fix runs off a determination that was never made
PASS  a failing check with a fix lets that fix run
PASS  a healthy check does not
PASS  and a SKIPPED check refuses by name: its probe determined nothing, so a fix off that record acts on an answer nobody has  <-- pinned defect
PASS  a per-user fix under the wrong profile is refused rather than reporting success and changing nothing for the editor  <-- pinned defect
----------------------------------------------------------------------
a whole run against a stand-in workstation
PASS  the sick workstation reports DL-05 Fail  [got Fail]
PASS  the sick workstation reports DL-06 Fail  [got Fail]
...
PASS  every registered check produced exactly one record
PASS  not one record in a whole run pairs a verdict with text saying it measured nothing, which is the defect this tool shipped  <-- pinned defect
PASS  the data-loss block runs and prints first, ahead of every performance finding
PASS  with --apply absent the applied collection stays empty across a whole run  <-- pinned defect
PASS  the cache fix is refused while Pro is running, because clearing a live cache corrupts the session  <-- pinned defect
PASS  with Pro running, not one project file was opened in a whole run  <-- pinned defect
PASS  the timeline lists the Pro hang and leaves out the Winlogon event and the other program's crash
PASS  when the Pro check itself dies, the project file is not opened: not knowing whether Pro runs is not the same as knowing it is closed  <-- pinned defect
PASS  a project folder on a share that takes 650 ms to list warns, with the number in the report
PASS  with Pro closed the project file is read, and the layer on a thirty-second timer is named
PASS  of three leftover session folders, the two holding a project file or a scratch table with rows in it are named, and the one holding a bare table header is not: an empty scratch geodatabase is a husk, not a recovery lead  <-- pinned defect
PASS  a file that is not a dump is not counted as one, because a Windows name filter also matches the short alias  <-- pinned defect
PASS  a project folder the walk could not open is reported as unreadable rather than as clean: a scan that returns nothing for a folder it could not read says the tree is fine when it has no idea  <-- pinned defect
PASS  and every per-user fix is refused under that profile, even with --apply, rather than reporting success and changing nothing for the editor  <-- pinned defect
PASS  nothing is promoted to the act-first list off an unread machine  <-- pinned defect
PASS  exactly three checks can still answer on a machine that reads back nothing: who is running the tool, whether Pro is open, and a graphics preference whose absence IS the finding. Every other check reports Info or Skipped rather than a verdict  <-- pinned defect
PASS  the cache folder is renamed aside, never deleted  <-- pinned defect
PASS  an existing graphics preference survives --apply byte for byte: the retired AP-02 cannot turn a driver issue into a startup crash
...
----------------------------------------------------------------------
the command line: nothing dangerous is on by default
PASS  --apply is off by default, so a plain run changes nothing  <-- pinned defect
PASS  and no host is probed by default, so a plain run sends no packet  <-- pinned defect
PASS  a unique prefix of --apply is refused, so a short flag cannot arm the fix path  <-- pinned defect
PASS  there is no password option at all: nothing here signs in to anything, and a connection file is never opened
PASS  --self-test wins over every other flag, so a test run can neither apply a fix, nor probe a host, nor write a file  <-- pinned defect
...
----------------------------------------------------------------------
the reading layer, against a real temporary tree and a real socket
PASS  a depth of one stops at the first level, because a full walk of a synced profile can itself hydrate every file it touches
PASS  renaming a folder aside moves it and leaves nothing behind, which is what the cache fix does instead of deleting
PASS  a listening port is reported open
PASS  a port with nothing behind it is reported closed
PASS  and the probe returns inside its own timeout. Not faster than it: Linux refuses a dead loopback port at once, Windows retransmits and times out, and the connect this replaced blocked for tens of seconds against a dead host  <-- pinned defect
PASS  off Windows every Windows read answers absence, never a guess
PASS  a reader program that is not installed is a reason, not a traceback and not an empty answer
PASS  a log that does not exist is an error, not an empty timeline  <-- pinned defect
PASS  a real project-shaped zip yields its three JSON entries and skips the image
PASS  memory answers both numbers or neither, so no check can compute a verdict from half a reading  <-- pinned defect
----------------------------------------------------------------------
the tool end to end on the machine running this, read-only
PASS  a real run exits 0, 1, 2 or 3, whatever this machine turns out to be
PASS  a plain run applies nothing to the machine it is run on  <-- pinned defect
PASS  and not one record of a real run pairs a verdict with text saying it measured nothing  <-- pinned defect
PASS  --report writes exactly two files, and creates the directory
----------------------------------------------------------------------
a verdict needs a measurement: the first review round
PASS  --ap is a usage error, exit 64 and never the lost-edits code 2  <-- pinned defect
PASS  and --help still exits 0
PASS  a host name that does not resolve is open=None with the reason, not open=False  <-- pinned defect
PASS  an unresolved name is Info and unmeasured: DL-10 is a data-loss check, and a Fail on a typo blamed a server nobody reached  <-- pinned defect
PASS  while tasklist failing is None, not an empty list  <-- pinned defect
PASS  an unreadable process list makes DL-12 Info and unmeasured  <-- pinned defect
PASS  an SMB-only timeline is Info and stays out of the act-first list: its Warn said Pro hangs on a laptop that was off the VPN  <-- pinned defect
PASS  one folder timed fast and one unreadable is Info, never Pass, and names the unreadable one: the folder nobody could list is the likely culprit, and DL-04 refuses the same partial reading  <-- pinned defect
PASS  a project folder on a mapped drive is a share: DL-05 fails it  <-- pinned defect
...
----------------------------------------------------------------------
boundaries and defaults that a mutation sweep found unpinned
PASS  options that never mention --apply apply nothing: the default is off, so a caller that forgets the key cannot arm the fixes  <-- pinned defect
PASS  a cache folder that is gone, or never named, is left alone  <-- pinned defect
PASS  one unreadable log of three is a quiet timeline and three of three is an unread one  <-- pinned defect
...
----------------------------------------------------------------------
the harness records a failure as a failure
PASS  a false check, a missing exception, a wrong exception and an accepted argument list are four failures, so a broken tool turns this self-test red  <-- pinned defect
PASS  not one of those four counted as a pass
PASS  and every one of them printed a line the operator can see
----------------------------------------------------------------------
880 assertions, 0 failed
```

## Requirements

Python 3.9 or newer. Standard library only: `argparse`, `collections`, `json`, `ntpath`, `os`,
`re`, `socket`, `sys`, `time`, plus `winreg`, `ctypes`, `subprocess`, `shutil` and `zipfile`
imported inside the functions that need them, and `xml.etree`, `io`, `tempfile` for the parsers
and the self-test.
It runs on ArcGIS Pro's Python and on a plain `python3`. `arcpy` is not used and the `arcgis`
package is not needed.

```
git clone https://github.com/pratisig/prostall_arcgis_slowness_diagnostic.git
python arcgis_pro_performance_analyser.py --self-test
```

`--self-test` needs no ArcGIS install, so you can check the tool before you point it at
somebody's workstation. It never writes to the registry, and it contacts no host. It reads the
registry of the machine it runs on, read-only, binds loopback sockets, writes temporary directories that it removes
afterwards, and starts a few small child programs of its own. It drives every check through a stand-in
machine that answers inside the process: a healthy one, a sick one, one that reads back nothing,
one whose registry provider dies mid-run, and one logged in under the wrong profile. It also
exercises the reading layer itself against a real temporary directory and a real loopback socket,
and runs the whole tool once, read-only, on the machine you run it on. That run is captured, not
printed.

The suite contains 880 assertions with platform-neutral labels. That matters more than it sounds:
the path logic answers questions about Windows paths, and two of the bugs it pins are cases where
the standard library answers for the host instead of for the path. The reads that only exist on
Windows are also exercised with the platform switched off, so Linux and Windows can cover each
other's fallback branches. Run the suite on the target Windows host as well as in CI before release.

On Windows the tool starts four programs that ship with Windows, all read-only: `powershell.exe`
for `Get-MpPreference`, `Get-NetAdapter`, `Get-NetAdapterPowerManagement` and
`Get-SmbClientConfiguration`, `wevtutil qe` for the event logs, `tasklist` for the process list and
`powercfg /getactivescheme` for the power plan. It reads a project file with `zipfile` and never
writes one.

## The existing alternatives

ArcGIS Pro ships the [Diagnostic Monitor](https://doc.esri.com/en/arcgis-pro/latest/get-started/arcgis-diagnostic-monitor.html),
opened with Ctrl+Alt+M while Pro is open, and it is the right tool for what it does. It traces Pro
from inside the process: performance counters, a task log that records unresponsive events, an
HTTP request viewer and an event log. It cannot
see the workstation around Pro: Known Folder Move, Offline Files, the adapter's power setting or
Defender's lists. A session that never finished opening gives it nothing to trace.

Spatineo publishes a diagnostics tool for ArcGIS Pro in a
[distribution-only repository](https://github.com/spatineo/arcgis-diagnostics-tool-public). It
ships without source, and this author has not run it, so no comparison is made here.

Event Viewer shows the same events as DL-13, one log at a time. Microsoft's
[performance analyzer for Defender](https://learn.microsoft.com/en-us/defender-endpoint/tune-performance-defender-antivirus)
(`New-MpPerformanceRecording`) proves which files on-access scanning actually slows. Run it, elevated,
before you ask for any exclusion PF-17 lists. The analyser puts these readings side by side in one
report, from the editor's own session, and says which of them it could not read.

## Quick start

On Windows, keep `Diagnostiquer_ArcGIS_Pro.bat`,
`arcgis_pro_performance_analyser.py` and `html_report.py` together and
**double-click the `.bat`**. It opens a numbered menu, so every feature is
reachable without remembering a flag and without keeping several batch files:

```
Assistant ArcGIS Pro : diagnostic, reparation et add-ins
  1) Diagnostic complet, lecture seule (TXT, JSON et HTML)
  2) Corriger le cache ESRI : AP-01, reversible, Pro ferme
  3) Reparer une COPIE d'un projet .aprx (l'original reste intact)
  4) Mettre en quarantaine un add-in precis, de facon reversible
  5) Restaurer un add-in depuis son quarantine.json
  6) Analyser un test deja fait : Pro demarre SANS les add-ins
  7) Aide : dossiers, commandes equivalentes et codes de sortie
  0) Quitter
```

Enter takes choice 1, the read-only run. It needs no open ArcGIS Pro session,
writes TXT, JSON and HTML and prints their paths; open the HTML in a browser to
read the report. The default folder is
`%LOCALAPPDATA%\\ArcGISPro_PerformanceAnalyser\\Rapports`; pass another local
folder as the batch file's first argument if needed. The console shows report
and launcher failures and stays open until you read them.

The menu is a front end, not a second implementation. Each choice prints the
`arcgis_pro_performance_analyser.py` line it maps to and then runs exactly
that line, so the same refusals, the same undo text and the same exit codes
apply. It never picks a project, a repair mode or an add-in on your behalf,
and it never starts `ArcGISPro.exe`. A run with no terminal (a scheduled task,
a pipe, a redirected file) is not shown a menu: it falls back to the read-only
diagnostic instead of waiting for an answer nobody can type.

The same actions are still available as explicit flags, which is what a script
or a deployment should use:

```bat
Diagnostiquer_ArcGIS_Pro.bat --apply-safe-fixes
Diagnostiquer_ArcGIS_Pro.bat --repair-aprx "D:\GIS\Broken.aprx" views
Diagnostiquer_ArcGIS_Pro.bat --repair-aprx "D:\GIS\Broken.aprx" views_history
Diagnostiquer_ArcGIS_Pro.bat --quarantine-addin "D:\AddIns\Vendor.esriAddinX"
Diagnostiquer_ArcGIS_Pro.bat --restore-addin "D:\Quarantine\quarantine-...\quarantine.json"
```

`--apply-safe-fixes` exposes only the existing gated AP-01 cache rename. Project
modes also accept `repack` and `none`; every repair writes a new project beside
the source. Quarantine requires the exact package path copied from PF-31 and
Pro closed. It removes that package from startup loading but preserves its
bytes, hash and restore manifest. After quarantine or restore, the batch runs a
fresh diagnostic and opens its HTML report. The menu calls these same flags
(choice 2 is `--apply`, choice 3 is `--repair-aprx`, and so on). See the safety
gates below before using either route.

### Sharing it across an organisation

Three files make the tool: `Diagnostiquer_ArcGIS_Pro.bat`,
`arcgis_pro_performance_analyser.py` and `html_report.py`. Copy them together
into a local folder on each workstation — running them from a network share is
not tested — and nobody needs to install anything, run as administrator, or
open ArcGIS Pro.

- **Nothing in the code names a machine.** No host, no profile path, no add-in
  name, no e-mail address: the launcher resolves its report folder from the
  running account, and the test suite fails if a shipped file starts naming a
  real Windows profile. That is what makes one copy safe to hand out.
- **Each operator runs it in their own Windows session.** The per-user
  settings, caches and Add-In Manager entries belong to the account that
  starts the tool, so running it *for* a colleague from your session reads the
  wrong profile. The reports then land in that user's
  `%LOCALAPPDATA%\ArcGISPro_PerformanceAnalyser\Rapports`.
- **The machine stays untouched unless a mode is chosen.** A plain run writes
  reports and nothing else. The cache fix, a recovery copy and the add-in
  quarantine are explicit, each prints its undo, and quarantine copies rather
  than deletes.
- **A report is an internal document.** It contains the account name, Windows
  paths, installed add-ins, the event timeline and the project folder. Share
  it with support deliberately, keep it off public hosts, and send the JSON
  over a channel the organisation already trusts — it holds the same detail
  in machine-readable form and is what two workstations are compared with.
- **The tool talks to no server of its own.** No telemetry, no update check,
  and the report's Content-Security-Policy forbids external resources. Only
  `--sde-server` and `--portal` reach the network, and only when named.

For a scripted run, use the editor's own session, without elevation, while Pro
is closed:

```
python arcgis_pro_performance_analyser.py
```

That reads the machine and prints the report. Nothing is written and no setting is changed. No
host is contacted, apart from reading a recent project folder that already sits on a share.

```
python arcgis_pro_performance_analyser.py --sde-server gisdb01 --portal gis.example.org --report C:\Temp\properf
```

That adds two bounded TCP probes and writes the report as text and as JSON. Add `--html` to
write the self-contained HTML report in the same run.

To analyse **one explicitly selected project**, including while another project is open in Pro:

```
python arcgis_pro_performance_analyser.py --project C:\\GIS\\Review\\Broken.aprx --report C:\\Temp\\properf
```

The selected file is copied to a temporary local snapshot first. Its ZIP members are read to EOF and CRC-checked, its JSON documents are parsed, and the report explains each finding in French with its ArcGIS Pro impact, ordered remediation steps and a verification procedure. The source is not rewritten.

For a project that crashes while restoring a map or layout view, create a **separate recovery copy**:

```
python arcgis_pro_performance_analyser.py --project C:\\GIS\\Review\\Broken.aprx --project-repair views --report C:\\Temp\\properf
```

This writes `Broken_recuperation_sans_vues.aprx` beside the original. It removes only the saved `views` and `viewLayoutXML` startup state from the copy; map and layout definitions remain in the project. Use `views_history` to remove geoprocessing history from the copy as well, or `repack` to rebuild only the ZIP container with every document unchanged. The original is never overwritten.

## Usage

| Flag | Default | What it does |
|---|---|---|
| `--self-test` | off | Run the offline assertions and exit. Wins over every other flag. |
| `--verbose` | off | Print the expected text and the evidence lines as well. |
| `--json` | off | Print the report as JSON on stdout instead of the console form. Stdout is that one document and nothing else, also with `--report`. |
| `--report DIR` | none | Write the text and JSON reports into this directory, creating it. Without it, only an explicit `--apply` or `--project-repair` can write anything. |
| `--html` | off | With `--report`, also write the self-contained HTML report. Keep `html_report.py` beside the engine; the `.bat` does this automatically. `--html` without `--report` is exit 64. |
| `--sde-server HOST` | none | TCP-probe this geodatabase host. Connect only, no credential. Without it the probe is skipped. A URL, a path or a space in the value is refused with exit 64. |
| `--sde-port PORT` | `1433` | Port for that probe. |
| `--portal HOST` | none | TCP-probe this portal host on 443. Without it the probe is skipped. Refused like `--sde-server`. |
| `--timeout MS` | `4000` | Bound on each network probe. |
| `--project APRX` | none | Run the eleven-check selected-project analysis instead of the workstation analysis. Uses a snapshot; safe to use while another project is open. |
| `--project-repair MODE` | `none` | With `--project`: `repack`, `views`, or `views_history`. Writes a verified new copy beside the source, never overwrites the source. `--apply` cannot be combined with this mode. |
| `--addins-isolated-ok` | off | Record the operator's isolation test: Pro starts with `/disableaddins` and crashes with the startup packages loaded. Read-only; it turns PF-31 into a ranked isolation plan and a data-loss finding. Refused with `--project` or `--apply`. |
| `--menu` | off | Offer the actions as a numbered menu. Each choice runs the documented flag, so nothing is reachable here that a script could not run. Falls back to the read-only diagnostic when stdin has no terminal. |
| `--apply` | off | Run the gated cache fix. It is refused unless its check actually failed and Pro is closed, prints its own undo, and never deletes anything. It never changes the GPU preference. |
| `--quarantine-addin PACKAGE` | none | Move this exact PF-31-discovered package into verified quarantine. Pro must be closed; arbitrary files are refused. Standalone action, not an automatic diagnosis or fix. |
| `--quarantine-dir DIR` | analyser folder under `LOCALAPPDATA` | Parent for quarantine transactions. It must be outside every discovered startup-loading folder. |
| `--restore-addin MANIFEST` | none | Restore the exact package from `quarantine.json`, verifying SHA-256/size and refusing to overwrite an existing file. |
| `--confirm-machine-wide` | off | Separately confirm administrator-approved quarantine of an HKLM/machine-wide package, which can affect every user. |

Every parser error is exit 64, including a refused abbreviation such as `--ap`. No abbreviation is
accepted, so a prefix of `--apply` cannot arm the fix path.

There is no password option and no connection string. Nothing here signs in to anything, and a
`.sde` connection file is recorded by path and never opened, because those files can carry a saved
credential.

## What it checks

Forty checks. The thirteen that bear on lost edits run first and print first, because
silent edit loss outranks every performance finding.

**Data loss**, in the order they run and print.

| Id | What it measures |
|---|---|
| DL-00 | Whether the tool is running as the account that actually edits. If another profile carries the Pro data, every per-user result below is invalid. |
| DL-05 | Where the recently opened projects live: local disk, a share, or a synced folder. |
| DL-03 | The project backup interval, and that it is not an edit autosave. It copies the project file, never the edit session. |
| DL-04 | Whether Windows may turn off each physical network adapter that is up, to save power. |
| DL-06 | Whether Documents is redirected into OneDrive by Known Folder Move. |
| DL-07 | Dehydrated Files-On-Demand placeholders anywhere under the project tree, read from the recall attribute bits. A junction or symbolic link is not a placeholder. |
| DL-08 | The Offline Files cache, read from the policy value and from nowhere else. |
| DL-09 | Crash dumps, and what dump collection is configured if there are none. |
| DL-10 | A bounded TCP connect to the geodatabase host, with its latency. A name that does not resolve is `Info`, not `Fail`: nothing reached the server. |
| DL-11 | Connection files on the box, by path, including the copies cached under the search index. |
| DL-12 | Whether Pro is running right now, which blocks the cache fix and the project-file read. A process list that could not be read is `Info`, and blocks both too. |
| DL-13 | The event timeline for the last 14 days: Pro crashes and hangs, dirty shutdowns, and SMB client connectivity warnings. Application Error 1000 includes the faulting module when Windows supplies it. SMB events alone are `Info`; a Pro crash, a Pro hang or a dirty shutdown makes it `Warn`. |
| PF-07 | Leftover session folders, and which of them still hold a project file or a scratch table with rows in it. |

**Performance**

| Id | What it measures |
|---|---|
| PF-01 | The coordinate systems in the project, and whether the layers agree with the map. A layer in a different system is reprojected on every draw and Pro does not say so. |
| PF-02 | The SMB client settings, with multichannel graded only when a recent project is on a share. |
| PF-03 | How long one listing takes in each of the first two share folders that hold a recent project. The report says how many further folders were not timed. One folder that cannot be listed makes it `Info`. |
| PF-04 | The installed release and build. |
| PF-31 | Startup add-ins, configurations and plug-ins: redirected/default and HKCU/HKLM well-known folders, DAML identity/version/target, `autoLoad`, corrupt packages, missing or bundled Esri assemblies, and correlation with Application Error fault modules. Static ZIP/XML reads only; no package code is loaded. |
| PF-05 | The display settings, read only. A `user.config` nobody could read is `Info`, not a list of `<absent>`. |
| PF-06 | The local cache: size, and how much of it is orphaned. |
| PF-08 | The catalog search index, and whether it indexes network connections. |
| PF-09 | How many layers draw their rows over the network by URL, named. These draw on every pan and zoom, and off the VPN they are the whole of the stall. |
| PF-10 | The weight of the project: layer count, group nesting and project-file size. Not a fault, but the answer that stops the rest of the report reading as the whole story. |
| PF-11 | Which graphics adapter Windows hands Pro. |
| PF-12 | Installed and free memory. |
| PF-13 | Free space on the system volume and on any local project volume. |
| PF-14 | The per-layer work a draw does: joins, definition queries, labelled layers, visibility ranges, and which label engine the map uses. |
| PF-15 | The active power plan. |
| PF-16 | Rasters under the project folder with no overview beside them. A raster with no pyramid is read at full resolution and thinned on screen, every draw. |
| PF-17 | Whether Microsoft Defender excludes the Pro install folder, the editor's ESRI folders and `ArcGISPro.exe`. |
| PF-18 | What the physical disks are made of. `Unspecified` is graded as possibly rotational, because a controller that will not say is not an answer that the disk is fast. |
| PF-19 | A bounded TCP connect to the portal. A name that does not resolve is `Info`, like DL-10. |
| PF-20 | The pagefile, from the boot-time registry value. One on a network volume is `Fail`: a multi-second stall that appears in no Pro log. |
| PF-21 | What each adapter actually negotiated. A gigabit adapter linked at a hundred megabit presents as a slow server, and nothing in the SMB settings explains it. |
| PF-22 | Layer refresh timers and the default geodatabase, read from inside the three newest recent `.aprx` files, only while DL-12 shows Pro closed. The report says how many older ones were not read. |
| PF-26 | Whether a roaming profile carries the ESRI settings folder. |
| PF-27 | Symbology that does not refresh after a field calculation. |
| PF-28 | CPU/core capacity and single-core performance evidence. |
| PF-29 | GPU model, driver evidence and software-rendering/WARP risk. |
| PF-30 | Dataset storage, oversized shapefiles and network or synchronised storage risks. |

PF-01, PF-09, PF-10, PF-14 and PF-22 all read the project file, and they share **one** read. Each
read holds the file open, and doing it five times would be five chances to contend with a save -
which is the failure this tool refuses to cause. All five are refused together while Pro runs.

PF-07 keeps its performance id and runs in the data-loss block on purpose. An earlier version
reported those folders as a count and a size and called it disk churn, which routed a possible
recovery lead for the lost work into the performance section and out of the act-first list.

### Explicitly selected `.aprx`: eleven checks and safe recovery copies

`--project` and the toolbox tool **Analyser un projet .aprx externe** are separate from the
workstation scan above. They analyse the file the operator chose, not the active project and not a
recent-project list. The toolbox rejects `ArcGISProject("CURRENT").filePath` when it is the same
file. A local temporary snapshot is analysed instead, and a source whose size or modification time
changes during that snapshot is rejected.

| Id | What the selected-project run measures |
|---|---|
| PR-01 | File size, ZIP readability, duplicate/encrypted entries and a full streaming CRC check, all bounded. Invalid integrity is `Fail`; an unreadable or over-budget file is `Info` and cannot arm a repair. |
| PR-02 | Presence and validity of `GISProject.json`, plus syntax/encoding of the other JSON documents. Pro 2.x XML is reported as unsupported for JSON recovery rather than modified. |
| PR-03 | Project, home-folder and default-geodatabase storage class: local, network or OneDrive. |
| PR-04 | Existence of path-shaped local file sources, resolved against the original project folder. Remote sources are named but never contacted. An enterprise `DATABASE=gis` name is not mistaken for a relative file, and connection credentials are never printed. |
| PR-05 | Saved `views` and `viewLayoutXML`, including layout views. Their presence is normally `Info`, not a false anomaly. |
| PR-06 | Geoprocessing-history item count and approximate JSON weight. The warning thresholds are this tool's, not an Esri limit. |
| PF-10, PF-22, PF-09, PF-01, PF-14 | Project weight, refresh timers/default geodatabase, service layers, on-the-fly projection and per-layer draw load, using the same measured decision functions as the workstation scan. |

Recovery is a separate opt-in and always writes **beside the source**, preserving relative data
paths. `repack` requires PR-01 Pass and preserves every document byte-for-byte after decompression.
`views` and `views_history` additionally require PR-02 Pass and the expected CIM list shapes. They
remove startup view/pane state from `GISProject.json`; `views_history` also filters `GPHistory`
items. They do **not** delete map or layout definitions. The output is reopened, fully CRC-checked,
its modified JSON is verified, and only then does it receive its final unique filename. A temporary
output that fails verification is discarded; the source is never touched.

This can bypass a crash loop caused by restoring a problematic view, and can reduce a bloated
history. It does not claim to repair every corrupt CIM document, missing dataset, incompatible
add-in, driver fault or damaged geodatabase. For those, the report gives manual steps and says when
restoring a backup or contacting Esri is the safe action.

### How the newer checks stay honest

- **DL-04, adapter power.** One adapter that reads `Disabled` beside one nobody could read is
  `Info`, never `Pass`. An unelevated read often answers nothing for the setting, and did on the
  test workstation.
- **DL-13, event timeline.** Each log is queried with `wevtutil qe`. A log that answers "Access is
  denied", or a reply that is not XML, is named as unreadable. It is never counted as a quiet log.
  Events are matched on the provider as well as the id, because Winlogon also writes event 1002.
  No event is `Info`, never `Pass`: a hang the editor waits out leaves no event at all. The SMB
  client events are listed by id and level and are not interpreted, so a log holding only those
  is `Info` and stays out of the act-first list: a laptop off the VPN raises them without any Pro
  hang.
- **PF-02 and PF-03, SMB and share latency.** Multichannel is on by default in Windows. Off, it
  warns only when a recent project is on a share. The listing time is graded at 400 ms (Warn) and
  1500 ms (Fail). Those are this tool's allowances, not a published figure. Only the first two
  share folders are timed, and one folder that cannot be listed turns an otherwise fast reading
  into `Info`: the folder nobody could list is the likely culprit.
- **PF-17, Defender.** The `N/A: Must be an administrator to view exclusions` line is recognised and
  never compared as a path. Real-time scanning off, or lists hidden by
  `HideExclusionsFromLocalAdmins`, are `Info` too. Defender expands variables as LocalSystem, so an
  exclusion written as `%APPDATA%\ESRI` covers the system profile, not the editor's folder, and is
  reported that way. An asterisk in a folder path stands for exactly one folder, and the folders it
  names and their subfolders are covered. The baseline is this tool's, not an Esri list. Review it
  against your antivirus standard.
- **PF-22, the `.aprx`.** The project file is opened only when DL-12 found Pro closed. Pro running,
  or a process list that could not be read, refuses the read, because a read holds the file open
  while Pro may be saving it. Only the three newest recent projects are opened.
  `defaultFolder` and `defaultGeoDatabase` are resolved against the project folder before they are
  classified, because Pro stores `.` for a home folder that is the project folder. A project read
  only in part, because a size or count cap was reached, is `Info`, never `Pass`.

- **PF-31, startup packages.** Discovery follows the default and redirected Documents locations,
  HKCU/HKLM well-known folders and registered configurations, then versioned
  `3.x` plug-ins. `.esriAddinX`, `.proConfigX` and `.esriPlugin` archives are
  read as ZIP directories and bounded XML, never loaded or extracted. The report
  inventories identity, publisher, add-in version, `desktopVersion`,
  `autoLoad`, assemblies and archive integrity. A newer target, a major-version
  mismatch, a missing `defaultAssembly`, a bundled Esri API assembly or a
  corrupt package is a concrete finding. If Application Error 1000 names a DLL
  that is present in a package, PF-31 reports the match as supporting evidence,
  not proof. Digital signatures are not verified, bounded reads can be
  incomplete, and no static inventory can enumerate every possible Pro crash
  cause. Unreadable locations make coverage incomplete rather than claiming
  that no add-in exists.
- **DL-10, PF-19 and DL-12, a name or a list nobody could read.** A host name that does not resolve
  is `Info` and unmeasured. It is not `Fail`, because nothing reached the server, and it does not
  reach the act-first list. A refused or timed-out connect to a name that did resolve still fails.
  A `tasklist` that fails is `Info`, never an empty list that reads as a closed Pro.

Three checks were deleted rather than rewritten. Two read a geodatabase the workstation cannot see,
and the third read an attribute rule definition that lives on the server. All three printed
findings a workstation had no way to produce.

## The five statuses

| Status | Means |
|---|---|
| `Pass` | Measured, and it is what it should be. |
| `Warn` | Measured, and it is a contributing cause. |
| `Fail` | Measured, and it is a cause. |
| `Info` | Read, and it decides nothing. |
| `Skipped` | Not read at all. The probe threw, produced nothing, or was never asked to run. |

`Pass`, `Warn` and `Fail` are verdicts. `Info` and `Skipped` are not, and the difference is
enforced rather than documented:

- The record constructor refuses a verdict on a check whose evidence text says it did not measure,
  and refuses one on a check that declares itself unmeasured.
- A check body that throws is recorded `Skipped`, never `Warn`. Every fix gates on `Warn` or
  `Fail`, so `Warn` would let a fix fire on a determination nobody made.
- A check body that records nothing at all is recorded `Skipped` too, because saying nothing reads
  as saying `Pass`.
- The act-first list is exactly the data-loss checks in `Warn` or `Fail`, computed from the
  records. It carries no list of ids. The version this replaced kept two ids in the roll-up so they
  stayed listed "even if a future branch demotes them", which is a report promoting a finding the
  check itself had withdrawn.

## Exit codes

| Code | Means |
|---|---|
| 0 | Nothing to act on, and at least one check other than DL-00 and DL-12 passed. |
| 1 | Measured workstation or project findings, none bearing on lost edits. |
| 2 | At least one finding bearing on lost edits. |
| 3 | No finding, and nothing was determined either. |
| 64 | A usage error, a refused/incomplete add-in action, `--report` could not write its files, or the menu hit an internal failure (its traceback is printed, so the case can be reported). |

Code 3 exists because a wrapper reads the exit code and never sees the note on stderr. A run that
could read nothing, such as PowerShell blocked or a host that is not Windows, has no finding, and
0 would tell the wrapper the machine is fine. DL-00 (who runs the tool) and DL-12 (whether Pro
runs) can answer on a machine that read nothing else, so a `Pass` from either does not make a run
determined. A finding outranks code 3.

## The fixes

`--apply` runs one fix, and only one. It is per-user, gated, and prints the command that undoes it.

| Id | Acts on | What it does |
|---|---|---|
| AP-01 | PF-06 | Renames the local cache folder aside so Pro rebuilds it. Refused while Pro is running. |

Four gates, in order: the check must exist, it must not be `Skipped`, it must be in `Warn` or
`Fail` (an `Info` check is refused as not determined, never called healthy), and a per-user fix is
refused when the session check failed. AP-01 also needs DL-12 to be a `Pass`: Pro running, or a
process list nobody could read, refuses it. That last one matters on a
remoted-in support session, where the per-user fix would write into the support account's hive and
report success while changing nothing for the editor.

AP-01 renames. It does not delete. A diagnostic that deletes a folder is how the last copy of
something disappears, and the undo is a single `move` back.

**AP-02 is retired.** Older versions could force `ArcGISPro.exe` onto the high-performance GPU.
That is not a safe automatic repair: an unstable driver can make Pro crash at the splash screen,
before any project opens. PF-11 still reports the preference and gives reviewed manual guidance,
but neither `--apply` nor the Python toolbox changes it. If Pro started crashing after an older
AP-02 run, open that run's JSON report, find the `applied` record whose `id` is `AP-02`, and run its
exact `undo` command. The same run's TXT report prints that command after `undo   :` without JSON
escaping, so it is the easiest version to copy. This restores the recorded previous value instead
of guessing whether the registry value should be deleted or changed. As a separate, non-mutating isolation test, Esri
supports launching `"C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe" /disableaddins`.

### When Pro only starts with `/disableaddins`

That test is the strongest evidence this tool cannot measure by itself: it
places the fault in startup code, not in the project, the licence or the
database. Say so and the report changes from an inventory to a plan:

```bat
Diagnostiquer_ArcGIS_Pro.bat --menu      (choice 6)
python arcgis_pro_performance_analyser.py --addins-isolated-ok --report C:\Temp\properx --html
```

PF-31 then becomes a data-loss finding and ranks the packages it found, with
the reason beside each one: a packaged fault module named by Application Error
1000, an install or update dated shortly before the first recorded crash, a
`desktopVersion` older than the installed Pro, and runtime libraries Pro also
loads (`Newtonsoft.Json.dll`, `Microsoft.IdentityModel.*`, …) that can win the
assembly-binding race and take the .NET runtime down. The ranking is labelled
a lead, not a verdict: isolation is still one package at a time, and the
report says so in the same sentence.

The step list is generated from that ranking, so it names the first package to
quarantine, the command, and what to do when the crash comes back. An empty
inventory is not reported as a pass in this case: it warns and points at
Add-In Manager and any managed-configuration launch shortcut.

### Explicit add-in quarantine and restore

Quarantine is **not** an automatic repair and PF-31 never selects a package as
the culprit. The operator chooses one exact path from the inventory, ideally
one package at a time after `/disableaddins` has changed the result. The action
then enforces these gates:

- Windows, a readable current PF-31 discovery, and a process list proving Pro
  is closed; a running Pro or unreadable process list refuses the move;
- only a discovered `.esriAddinX`, `.proConfigX` or `.esriPlugin` package;
  machine-wide packages additionally require `--confirm-machine-wide`;
- a quarantine outside every default, redirected and registered loading
  folder, with a verified copy and a manifest written **before** the loading
  copy is removed;
- identical source/copied SHA-256 and size, and a source that did not change
  during copying. A failed copy leaves the source in place;
- restore only from the manifest's own transaction folder, with the expected
  package name and unchanged hash. An existing destination, missing parent or
  altered backup is refused and never overwritten.

The manifest records package identity, discovery indicators, original/backup
paths, hash, installed Pro version and an undo command. Restore retains the
backup as an additional safeguard. There is deliberately no permanent-purge
command: quarantine removes the add-in from Pro's startup path without making
an uncertain diagnosis irreversible. Test normal startup after each isolation;
if nothing changes, restore that package before testing another.

The CLI add-in action is standalone so its result cannot be confused with a
40-check diagnostic document. The batch runs a separate diagnostic after a
successful action. No diagnostic or recovery mode can promise to find or fix
every possible ArcGIS Pro fault; drivers, installation damage, profiles,
hardware, server faults and unsupported CIM corruption can require other
evidence or Esri support.

Nothing else is automated in the workstation run, on purpose. OneDrive redirection, the project
location, the power plan, Defender, the adapter power setting, the GPU preference, the SMB client,
dump collection and anything inside a geodatabase are reported and never touched. Those are policy
decisions or reviewed operator actions. The selected-project recovery described above is a
separate mode: it can write a new `.aprx` copy, but never changes the selected source or any
geodatabase. The in-Pro workstation tool is read-only even if a legacy model forces its old
`apply_fixes` parameter on.

## Output

The console report lists the data-loss block, then the performance block, then a tally, then the
findings to act on first. `--report` writes the same thing as text, plus a JSON document beside it;
`--html` adds a self-contained HTML rendering of that same document:

```json
{
 "report_version": 4,
 "taken": "2026-09-17 20:59",
 "user": "editor",
 "elevated": false,
 "act_first": ["DL-05", "DL-03", "DL-06"],
 "fixable": ["PF-06"],
 "counts": {"Fail": 2, "Warn": 3, "Pass": 7, "Info": 8, "Skipped": 2},
 "applied": [],
 "notes": ["would apply [AP-01] Rename the ESRI local cache folder aside so Pro rebuilds it."],
 "checks": [{"id": "DL-06", "category": "Storage", "status": "Fail", "dataloss": true,
             "title": "Documents redirected into OneDrive (Known Folder Move)",
             "observed": "...", "expected": "...", "impact": "...", "fix": "...",
             "explanation": "...", "impact_fr": "...", "steps": ["...", "..."],
             "verification": "...", "autofixable": false, "detail": ""}]
}
```

With `--json` and `--report` together, stdout stays one JSON document and the `report :`
lines go to stderr: two paths normally, three with `--html`.

Diff that JSON against a workstation that is known to be fast. That is the fastest way to find what
is different about this one, and it is why every timestamp in the report is UTC: two machines in
different time zones would otherwise disagree on every dated line.

A check-only run still evaluates the fix gates and prints what it would have done. `applied` is
empty unless `--apply` was given, and the self-test asserts that across a whole run.

## ArcGIS Pro toolbox, and the HTML report

[`ArcGISPro_PerformanceAnalyser/`](ArcGISPro_PerformanceAnalyser/LISEZMOI.md) exposes the
workstation diagnostic and the selected-project diagnostic as two tools in one ArcGIS Pro Python
Toolbox, rendering each result as one self-contained HTML file. It ships as **one file**: the
engine and the renderer are inlined into the `.pyt`, so sharing it
is copying it. There is no sibling module to keep beside it, and therefore nothing that can go
missing on the machine it lands on.

| File | What it is |
|---|---|
| `Diagnostiquer_ArcGIS_Pro.bat` | Windows launcher: a numbered menu on double-click (read-only diagnosis by default), plus the explicit AP-01, `.aprx` recovery-copy and add-in quarantine/restore flags for scripts; keep the engine and renderer beside it |
| `ArcGISPro_PerformanceAnalyser/ArcGISPro_PerformanceAnalyser.pyt` | **The shipped artefact.** Two tools, with the engine and the renderer inlined |
| `arcgis_pro_performance_analyser.py` | The diagnostic engine and CLI. A single file, no `arcpy` import |
| `html_report.py` | The HTML renderer. A pure function over the report document, no `arcpy` |
| `toolbox_glue.py` | The ArcGIS Pro glue. The only part that imports `arcpy` |
| `build_toolbox.py` | Assembles those three into the one `.pyt` |
| `tests/test_toolbox.py` | 156 assertions over the renderer, both tools, in-Pro mutation guards and real temporary `.aprx` recovery copies, on a stand-in `arcpy` |

Install by copying that one `.pyt` anywhere stable, then Catalog pane > Toolboxes > Add Toolbox,
pointing at it. The tool appears under "ArcGIS Pro Performance Analyser".

The `.pyt` is generated: **edit the sources and run `python build_toolbox.py`**, never the `.pyt`
itself. `tests/test_toolbox.py` runs the build and compares the result byte for byte, so an
artefact that has drifted from its sources cannot sit in the repository unnoticed. The sources
stay apart because they are separately testable: the engine self-tests with no ArcGIS installed
and the renderer is a pure function, so only the glue needs `arcpy`. The merge is a
concatenation, and the build stops if two sources ever bind the same top-level name, because one
would silently shadow the other.

The HTML is one page with no external asset at all, so it opens on a workstation that is offline:

- a **sommaire** that stays at the top and links to every section, with the count of checks in each
  block and the data-loss link marked when there is something in it;
- **KPI cards**: health score, anomalies, warnings, data-loss findings, conforming checks,
  undetermined ones, and measurement coverage;
- **gauges** in inline SVG for the health score, the coverage, free memory, free disk on the worst
  volume, share latency, negotiated link speed and cache size;
- **flags**, one row per symptom S1-S6 (including a crash while opening a project), counting the
  findings whose impact line names that symptom and linking to each of them by name;
- every check as a card: **the check's French name as the heading**, the status badge, the
  data-loss marker, the measured problem, a plain-French explanation, the concrete ArcGIS Pro
  impact, ordered remediation steps and an after-fix verification, with the expected value and
  technical evidence folded away. The identifier (`DL-09`, `PF-06`) is a small reference beside the
  heading: it survives a re-wording, so a support case and a JSON diff keep one stable handle, but
  it is never what the reader has to decode first. The original English wording is kept inside the
  folded details, for Esri support;
- the fix gates, the undo command of anything applied, a legend, and the JSON document embedded for
  the diff.

A `Content-Security-Policy` header allows no external source.

**Every number on the page comes from a `kpi` mapping the check carried.** Report format 2 added
that mapping to the checks that measure a number, and `make_check` refuses one that is not a
mapping of numbers - a string or a boolean is refused. Report format 4 adds the structured
`explanation`, `impact_fr`, `steps` and `verification` fields, plus selected-project metadata;
older consumers can ignore those fields while continuing to read the original evidence. A gauge
whose value had been parsed back out of a
sentence would be a number the renderer invented on the way to the page, which is the defect this
tool exists to prevent, moved downstream. A check that carries no number draws no gauge.

The report is full of strings the machine supplied, not the operator: account names, paths off a
file server, adapter descriptions, Defender exclusion paths, event text. Every one is escaped, and
the embedded JSON encodes `<`, `>` and `&` as `\u003c`, `\u003e` and `\u0026`, so a folder name on a
share cannot become markup in the operator's browser and cannot close the script element early. The
JSON still decodes to the identical document. `tests/test_toolbox.py` asserts all of it against a
document whose every field is a payload.

**Running it from inside ArcGIS Pro means ArcGIS Pro is open.** DL-12 reports Pro as running and
lands in the act-first list, and the five checks that read the project file - PF-01, PF-09, PF-10,
PF-14 and PF-22 - are refused, because a project file must not be held open while Pro may be
saving it. AP-01 is refused for the same reason. A run from the Geoprocessing pane is therefore a
workstation report minus the automatic project-file checks. For that full workstation set, close
Pro and run `arcgis_pro_performance_analyser.py` in the editor's own session. The separate
**Analyser un projet .aprx externe** tool is the exception: it can inspect another selected file
while Pro stays open, using a snapshot and rejecting the active project.

```
python tests/test_toolbox.py      # 156 assertions over the renderer, both tools and project recovery
python arcgis_pro_performance_analyser.py --self-test   # 880 assertions over the diagnostic
python build_toolbox.py --check   # the shipped .pyt is what its three sources build to
```

The toolbox test executes the `.pyt` itself against a stand-in `arcpy`, so what runs there is the
toolbox's own `getParameterInfo`, `updateMessages` and `execute`, through to the three files it
writes. Not a copy of them.

## Hardened reads

Every file this tool opens is one it did not write, and some of them live in a folder a sync client
may have replaced. Each of these was a real finding, and each is pinned by an assertion:

| Read | What it was | What it is now |
|---|---|---|
| `user.config` | Read whole, with no bound. A 200 MB file came back as 209,715,203 characters. | 2 MB ceiling, and a document type declaration is refused outright, because expat expands internal entities and a few hundred bytes of nested declarations is a million characters by the time the parser finishes. |
| `user.config` parse failure | A file nobody could read printed every setting as `<absent>`, which is a real answer. | A failed read is its own answer. DL-03, PF-05 and PF-08 say the settings are unknown and carry no verdict. |
| `.aprx` entries | Bounded by the size the zip header claimed, and a corrupt entry raised out of the reader. | Bounded by the bytes actually decompressed, reading to the end so the archive's own checksum runs. One bad entry costs that entry; the survivors still answer. |
| `.aprx` walk | Recursive. A document nested a thousand deep ended the whole check with a `RecursionError`, and one odd project became "no project was read". | An explicit stack, bounded on depth and node count, with the cap reported as a partial read. |
| Selected `.aprx` | Reading the chosen file throughout the checks could contend with another session and mix two saves. | A temporary local snapshot, before/after stat comparison, bounded full CRC/JSON checks, no remote source probes, and recovery only into a separately verified copy. |
| Report filenames | Stamped to the second, so two runs inside one second landed on the same file and the second silently replaced the first. | A name that does not exist yet. |
| Undo commands | The executable path was interpolated between quotes, so a `"` in `InstallDir` closed the argument early. | Quoted through one helper that refuses a value holding a quote, `&`, `|`, `<`, `>`, `^`, `%` or `!`, and prints the manual route instead. |
| Child programs | Started by bare name, and Windows searches the current directory first. | Started from `%SystemRoot%\System32` when that path exists. |
| Name resolution | Unbounded, so a resolver that dropped the query held the run for the system's full retry budget. | A bounded wait. An unanswered lookup is its own answer, distinct from a name that does not resolve. |
| `stderr` | Not reconfigured, so a non-cp1252 path in "could not write the report" raised a `UnicodeEncodeError` instead of exiting 64. | Reconfigured like `stdout`. |

## Limits

- It reads a workstation or the structure of a selected `.aprx`. It does not read a server. Nothing
  here opens a geodatabase, runs a query, or signs in to a portal, and the two workstation network
  probes are TCP connects that send nothing. Selected-project source checks never contact remote data.
- Off Windows almost every workstation check reports `Info` or `Skipped`, and that run exits 3.
  The selected `.aprx` container/JSON analysis itself is portable. An unreadable registry is not an
  absent value, and the tool says so rather than guessing.
- Project recovery is deliberately conservative and is not an ArcGIS Pro runtime certification:
  the generated ZIP and JSON are verified here, but the final proof is opening the copy in the
  target ArcGIS Pro release and checking every required map, layout and source.
- Several readings need Pro's own configuration. Run it in the editor's session. DL-00 exists to
  tell you when you did not.
- The OneDrive redirection bitmask is printed and deliberately not decoded. The value most guides
  name does not exist on current builds, and on a machine whose folders were all redirected it
  rendered as absent, so the line contradicted the finding. The verdict comes from the Documents
  path, which is measured directly.
- The event log is read through `wevtutil`, unelevated. The Application and System logs answer
  there. The SMB client connectivity log answered "Access is denied" on the test workstation, so
  DL-13 names it as unreadable unless the run is elevated. No WMI and no scheduled tasks are read.
- The `Application Hang` provider name is the one Windows Error Reporting writes, but the test
  workstation had no hang event to read it from. The query that names it ran there and returned
  an empty list, not an error. `Application Error`, `Microsoft-Windows-Kernel-Power` and `EventLog`
  were read from real events on that machine.
- PF-17 approximates Defender's matching: an exact or parent folder, one folder per asterisk, a
  question mark for one character, and the LocalSystem variables. A contextual exclusion (one with
  `\:{...}`) is not counted, because it may not apply to on-access scanning. Prove a slow scan
  with the Defender performance analyzer before asking for an exclusion.
- PF-22 reads projects that store their contents as JSON, which is the layout of current releases.
  A project with no JSON inside is reported unreadable. The read is capped at 300 entries, 4 MB per
  entry and 32 MB in all.
- PF-03 was exercised against a real local folder and a stand-in machine's listing, never against a
  real file server. The clock it uses is the system clock, so a clock step during a listing gives a
  wrong elapsed time.
- It finds recently opened projects by looking under the Documents project folder. A project kept
  somewhere else is not in that list, and DL-05 says what it looked at rather than claiming
  coverage.
- It does not diagnose a network. `DL-10` says the host answered in so many milliseconds, and PF-03
  says how long a share took to list a folder. Why it was slow is somebody else's tool.
- Branch coverage of the self-test is 100% on Windows (Python 3.13, `coverage run --branch`). The
  `coverage` package is not a dependency of this repo, and it was not installed on the Linux host,
  so the Linux figure was not measured. Two lines are proved against a stand-in and not against
  the real thing: `Env.reg_write`, the one registry write that only `--apply` reaches, runs against
  a stand-in `winreg` module and never touches the real registry, and the kill of a hung child
  runs against a child that sleeps. A mistake in how the real registry reacts would show only
  under `--apply`.
- SMB client connectivity events are listed by id and level only and are not interpreted.
- DL-04 cannot tell which adapter carries the session, so it warns on any physical adapter that is
  up. A Wi-Fi adapter left at the Windows default beside a wired adapter warns even when it carries
  no database session.
- The Defender text that stands in for hidden lists is recognised in English only. On a localised
  Windows build it would be compared as a path, and every baseline folder would read as missing.
- PF-17 reads an exclusion that ends in `\*` as covering the folders below the ESRI folder, not
  the ESRI folder itself, so a trailing-wildcard exclusion reports `NOT EXCLUDED`. It also drops a
  contextual exclusion whatever it restricts, even one that restricts only `ScanTrigger:OnAccess`.
- PF-22 counts a `refreshRate` only when it is a number above zero. A string, a boolean or a key
  in another letter case is not a timer.
- An entry inside a project that cannot be decoded or parsed is not counted, so a project with one
  such entry beside readable ones can still pass.
- One malformed project makes PF-22 `Skipped` for all projects. A `defaultFolder` that is not a
  string, or a document nested thousands of levels deep, is enough.
- The 32 MB project cap is enforced on the bytes actually decompressed, not on the size the zip
  header claims. A header that understates its entry is caught by the archive's own checksum and the
  entry is reported as unreadable; one that would take the total past the cap is left out and the
  read is reported as capped. An encrypted entry, or one using a compression Python does not
  implement, is reported the same way. An earlier revision of this page claimed a lying header could
  use more memory than the cap. It could not: `ZipExtFile` stops at the declared size. The claim was
  wrong and is corrected here.
- Reading a project file that is a dehydrated OneDrive placeholder makes OneDrive download it,
  although `--apply` was not given.
- The name lookup for the two TCP probes is not bounded by `--timeout`, only the connects are.
- It starts `powershell.exe`, `wevtutil`, `tasklist` and `powercfg` by name, and Windows also
  searches the current directory for a program name, so run it from a folder you control.
- When PowerShell fails, the reason shown is its first line on stderr, which can be unhelpful.
  `wevtutil` output is decoded as UTF-8 with replacement characters, so a non-ASCII event field can
  be garbled.
- A character that the console code page cannot hold prints as a `\uXXXX` escape when output is
  redirected. The `--report` files and `--json` are encoded for you and are not affected.
- The report carries the account name, local paths, connection file names (names only, never
  contents), exclusion paths and layer names. Read it before you send it to anyone.
- `user.config` is read with a 2 MB ceiling and any document type declaration in it is refused
  outright. A real Pro `user.config` is a few kilobytes and has no DTD; expat expands internal
  entities, so a few hundred bytes of nested declarations would otherwise be a million characters
  before the parser finished. A file over the ceiling, or one carrying a DTD, is reported as unread
  rather than as a set of unset settings.
- The four child programs are started from `%SystemRoot%\System32` when that path exists, and by
  bare name only when it does not.
- The DNS lookup behind the two probes runs in a daemon thread this tool stops waiting for. A lookup
  already in flight cannot be cancelled, so a resolver that never answers leaves one thread behind
  for the rest of the process.
- PF-16 walks the project folder at depth 3 with a 5000-entry bound, the same as every other walk
  here. Rasters deeper than that, or in a folder this tool does not look at, are not counted, and the
  folders it could not open are named.
- PF-01 compares only the spatial references the project file actually records. A layer with none
  inherits the map's, and calling that a mismatch would be a verdict about something the file does
  not say.
- PF-18 reads `Get-PhysicalDisk`. A disk behind a RAID or storage-space controller commonly answers
  `Unspecified`, and is graded as possibly rotational rather than as fine.
- PF-20 reads the boot-time `PagingFiles` value. System managed is reported as `Info`, because
  Windows decides the size at boot and does not write the number there.
- PF-21 grades the negotiated speed, not the rated one. An adapter whose speed string this tool does
  not recognise is `Info`, not a pass.
- If `--report` cannot be written, the exit code is 64 whatever the findings were.
- The HTML report is a rendering of the same document, not a second reading: it can be no more
  determined than the run behind it. Its filters need JavaScript; without it every check stays
  visible and nothing is hidden.
- The toolbox writes into a folder you name and creates it if it is missing. It does not check that
  the folder is one you meant, and ArcGIS Pro runs it with the privileges of the Pro session.
- `tests/test_toolbox.py` executes the `.pyt` against a stand-in `arcpy`, which is enough to prove
  the parameter surface, the validation and the file writes, and not enough to prove that ArcGIS Pro
  itself accepts the `datatype` names. Open the toolbox once in Pro before you rely on it.

## Sources

Every Windows, Esri and Defender behaviour the newer checks rely on, and where it is documented:

- `wevtutil qe` with `/q`, `/c`, `/rd`, `/f:xml` and `/e` for the root element:
  [wevtutil](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/wevtutil).
- Kernel-Power 41 and EventLog 6008:
  [Event ID 41](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/event-id-41-restart).
- `AllowComputerToTurnOffDevice` values `Unsupported`, `Disabled`, `Enabled`:
  [MSFT_NetAdapterPowerManagementSettingData](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/legacy/hh872363(v=vs.85)),
  an archived 2015 page. The current
  [Get-NetAdapterPowerManagement](https://learn.microsoft.com/en-us/powershell/module/netadapter/get-netadapterpowermanagement)
  page names the cmdlet and its output class and does not list the values.
- Mapped drive letters: [GetDriveTypeW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdrivetypew)
  answers `DRIVE_REMOTE` (4) for a remote (network) drive.
- The Diagnostic Monitor shortcut and its six tabs:
  [ArcGIS Diagnostic Monitor](https://doc.esri.com/en/arcgis-pro/latest/get-started/arcgis-diagnostic-monitor.html).
- Add-in well-known folders, administrative folders and `/disableaddins` recovery:
  [Manage add-ins](https://pro.arcgis.com/en/pro-app/latest/get-started/manage-add-ins.htm)
  and [ArcGIS Pro crashes on launch after installing an add-in](https://support.esri.com/en-us/knowledge-base/arcgis-pro-crashes-on-launch-after-installing-add-in-000036367).
  Registry locations, configurations, plug-ins and loading order:
  [Advanced Topics](https://pro.arcgis.com/en/pro-app/3.6/sdk/api-reference/conceptdocs/docs/ProConcepts-Advanced-Topics.html)
  and [Configurations](https://pro.arcgis.com/en/pro-app/3.6/sdk/api-reference/conceptdocs/docs/ProConcepts-Configurations.html).
  DAML `desktopVersion`, `defaultAssembly` and add-in failure evidence:
  [Diagnosing ArcGIS Pro Add-ins](https://pro.arcgis.com/en/pro-app/3.6/sdk/api-reference/conceptdocs/docs/ProGuide-Diagnosing-ArcGIS-Pro-Add-ins.html).
- Multichannel on by default:
  [Manage SMB Multichannel](https://learn.microsoft.com/en-us/windows-server/storage/storage-spaces/manage-smb-multichannel).
  The settings read: [Set-SmbClientConfiguration](https://learn.microsoft.com/en-us/powershell/module/smbshare/set-smbclientconfiguration).
- Exclusions hidden from `Get-MpPreference` by `HideExclusionsFromLocalAdmins`:
  [Configure custom exclusions](https://learn.microsoft.com/en-us/defender-endpoint/microsoft-defender-antivirus-exclusions-configure).
  Wildcards (an asterisk in a folder path matches one folder, and the matched folders' subfolders
  are covered), and system variables expanded as LocalSystem, where `%USERPROFILE%`, `%APPDATA%`,
  `%LOCALAPPDATA%`, `%TEMP%` and `%TMP%` resolve somewhere other than the user's location:
  [Exclusions in Microsoft Defender Antivirus](https://learn.microsoft.com/en-us/defender-endpoint/microsoft-defender-antivirus-exclusions-overview).
  The `N/A: Must be an administrator to view exclusions` line is not in that documentation. It was
  read from a real unelevated `Get-MpPreference` on the test workstation.
- `refreshRate` and `refreshRateUnit` on several layer types (for example `CIMGroupLayer` and
  `CIMKMLLayer`), and `defaultFolder` and `defaultGeoDatabase` on
  `CIMGISProject`: the [CIM specification](https://github.com/Esri/cim-spec) (`CIMLayer.md` and
  `CIMDocument.md`). The specification does not say that an `.aprx` holds those documents as JSON
  inside a zip. That was read from real Pro projects on the test workstation, where the relative
  `.` home folder was resolved as described above.

## Related

Other single-file tools in this portfolio that pair with this one:

- [cimscan](https://github.com/uhsear/cimscan) - once the workstation is ruled out, report what the project actually points at, and refuse to call an unreadable project a clean one
- [gdbxray](https://github.com/uhsear/gdbxray) - read the geodatabase schema behind the layers the slow project draws
- [gdbfence](https://github.com/uhsear/gdbfence) - keep a File Geodatabase out of the places this tool finds them in

## Contributing

Open an issue or pull request on GitHub.

## Author

Built by [Asir Khan](https://www.linkedin.com/in/asir-khan-310317264/).

## License

MIT.
