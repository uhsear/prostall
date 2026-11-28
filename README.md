# prostall

Diagnose why ArcGIS Pro is slow on this workstation, and never report a finding the machine did
not produce. Read-only by default: nothing changes, and no host is contacted unless you name it.
Three flags write: `--report` creates three new files in a folder you name, `--html` writes one web
page, and `--apply` runs two gated fixes. `prostall.bat` runs it with one double-click. The one read that crosses the network unasked is a project folder that already lives
on a share, as Pro does when it opens the project.

An editor tells you Pro takes five minutes to open, that it hangs for two or three minutes in the
middle of an edit, and that sometimes the work they saved before the hang is gone afterwards. There
is no error message. There is no crash dump, because they ended the hang with Task Manager and that
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
that may already exist, off a list it never saw. prostall recognises that line, says the lists are
hidden, and gives no verdict. The same run on the test workstation showed the pattern twice more:
the network adapter's power setting came back empty, and the SMB client log answered "Access is
denied". Both are reported as unread, not as healthy.

```
$ python prostall.py --self-test
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
PASS  when there was a previous value, the undo restores that value rather than deleting the setting  <-- pinned defect
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
PASS  --report writes exactly three files, and creates the directory
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
674 assertions, 0 failed
```

## Requirements

Python 3.9 or newer. Standard library only: `argparse`, `collections`, `json`, `ntpath`, `os`,
`re`, `socket`, `sys`, `time`, plus `winreg`, `ctypes`, `subprocess`, `shutil` and `zipfile`
imported inside the functions that need them, and `xml.etree`, `io`, `tempfile` for the parsers
and the self-test.
It runs on ArcGIS Pro's Python and on a plain `python3`. `arcpy` is not used and the `arcgis`
package is not needed.

```
git clone https://github.com/uhsear/prostall.git
python prostall.py --self-test
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

The same 629 assertions pass on Windows (Python 3.13) and on Linux (Python 3.12), with identical
labels. That matters more than it sounds: the path logic answers questions about Windows paths,
and two of the bugs it pins are cases where the standard library answers for the host instead of
for the path. The reads that only exist on Windows are run twice, once with the platform switched
off, so each host walks the branch the other one takes and the count stays equal.

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
before you ask for any exclusion PF-17 lists. prostall puts these readings side by side in one
report, from the editor's own session, and says which of them it could not read.

## Quick start

Run it in the editor's own session, without elevation, while Pro is closed.

The simplest way is to double-click **`prostall.bat`**, kept in the same folder as `prostall.py`.
It:

- finds a Python: ArcGIS Pro's own first, which is on every machine with Pro, then the `py`
  launcher, then `python`;
- runs the check with `--report "%LOCALAPPDATA%\prostall\reports"`;
- opens the web page;
- ends with a one-line result and waits for a key, so the window does not close on you.

It never runs `--apply`. To add options to every run, such as `--sde-server`, set
`PROSTALL_ARGS` at the top of the file; to send the reports elsewhere, change `REPORT_DIR` there.
Options typed after it on a command line are passed through, for example
`prostall.bat --no-open`.

From a command prompt:

```
python prostall.py
```

That reads the machine and prints the report. Nothing is written and no setting is changed. No
host is contacted, apart from reading a recent project folder that already sits on a share.

```
python prostall.py --sde-server gisdb01 --portal gis.example.org --report C:\Temp\properf
```

That adds two bounded TCP probes and writes the report as text and as JSON.

## Usage

| Flag | Default | What it does |
|---|---|---|
| `--self-test` | off | Run the offline assertions and exit. Wins over every other flag. |
| `--verbose` | off | Print the evidence, expected values and details for every check, not only the ones that need action. |
| `--json` | off | Print the report as JSON on stdout instead of the console form. Stdout is that one document and nothing else, also with `--report`. |
| `--report DIR` | none | Write the text, JSON and HTML reports into this directory, creating it. Without it, or `--html`, nothing is written. |
| `--html FILE` | none | Write the report as one web page to this file, creating its folder, and open it in the browser. See [The HTML report](#the-html-report). |
| `--no-open` | off | Write the HTML report but do not open it. |
| `--sde-server HOST` | none | TCP-probe this geodatabase host. Connect only, no credential. Without it the probe is skipped. A URL, a path or a space in the value is refused with exit 64. |
| `--sde-port PORT` | `1433` | Port for that probe. |
| `--portal HOST` | none | TCP-probe this portal host on 443. Without it the probe is skipped. Refused like `--sde-server`. |
| `--timeout MS` | `4000` | Bound on each network probe. |
| `--apply` | off | Run the gated fixes. Each is refused unless its check actually failed, prints its own undo, and never deletes anything. |

Every parser error is exit 64, including a refused abbreviation such as `--ap`. No abbreviation is
accepted, so a prefix of `--apply` cannot arm the fix path.

There is no password option and no connection string. Nothing here signs in to anything, and a
`.sde` connection file is recorded by path and never opened, because those files can carry a saved
credential.

## Configuration

The block at the top of `prostall.py` holds everything an operator is expected to change. Edit it
and run as usual.

| Setting | Shipped value | What it does |
|---|---|---|
| `CHECKS_ENABLED` | every check `True` | One entry per check id. `False` turns that check off. |
| `ALLOW_APPLY` | `True` | `False` refuses `--apply` with exit 64, so that copy can never change the machine. |
| `FIXES_ENABLED` | both `True` | `False` turns off one fix: `AP-01` (cache aside) or `AP-02` (GPU preference). |
| `SDE_SERVER`, `SDE_PORT`, `PORTAL`, `TIMEOUT_MS`, `REPORT_DIR`, `HTML_FILE` | `None`, `1433`, `None`, `4000`, `None`, `None` | What a run uses when the matching flag is not given. A flag always wins. |
| Thresholds | as listed in the file | Memory, disk, cache, latency, event window, share listing and `.aprx` bounds. |
| `OPEN_HTML` | `True` | Open the HTML report in the default browser after writing it. `False` never opens it. |
| `HTML_ORG_NAME` | `New York City Emergency Management` | The line above the title of the HTML report. `""` prints none. |
| `DEFAULT_PRO_EXE` | `C:\Program Files\ArcGIS\Pro\bin\ArcGISPro.exe` | Where Pro is assumed to be when the registry does not say. |

A check turned off is still in both reports, as Skipped with "turned off in CHECKS_ENABLED". A
check that vanished would read as one that passed. A fix whose check is off is refused, even under
`--apply`. An id in either table that no check or fix owns, or a value that is not `True` or
`False`, stops the run with exit 64, so a typo cannot leave a check running that you meant to turn
off.

Nothing in the block arms `--apply`; it stays a command-line flag. The self-test pins the safe
defaults, so setting `SDE_SERVER`, `PORTAL`, `REPORT_DIR` or `HTML_FILE` there turns those assertions red
on purpose: a plain run would then send a packet or write a file. Changing a threshold can also
fail the assertions that pin a verdict at the shipped value.

## What it checks

Thirty-two checks. The thirteen that bear on lost edits run first and print first, because
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
| DL-13 | The event timeline for the last 14 days: Pro crashes and hangs, dirty shutdowns, and SMB client connectivity warnings. SMB events alone are `Info`; a Pro crash, a Pro hang or a dirty shutdown makes it `Warn`. |
| PF-07 | Leftover session folders, and which of them still hold a project file or a scratch table with rows in it. |

**Performance**

| Id | What it measures |
|---|---|
| PF-02 | The SMB client settings, with multichannel graded only when a recent project is on a share. |
| PF-03 | How long one listing takes in each of the first two share folders that hold a recent project. The report says how many further folders were not timed. One folder that cannot be listed makes it `Info`. |
| PF-04 | The installed release and build. |
| PF-05 | The display settings, read only. |
| PF-06 | The local cache: size, and how much of it is orphaned. |
| PF-08 | The catalog search index, and whether it indexes network connections. |
| PF-11 | Which graphics adapter Windows hands Pro. |
| PF-12 | Installed and free memory. |
| PF-13 | Free space on the system volume and on any local project volume. |
| PF-15 | The active power plan. |
| PF-17 | Whether Microsoft Defender excludes the Pro install folder, the editor's ESRI folders and `ArcGISPro.exe`. |
| PF-19 | A bounded TCP connect to the portal. A name that does not resolve is `Info`, like DL-10. |
| PF-22 | Layer refresh timers and the default geodatabase, read from inside the three newest recent `.aprx` files, only while DL-12 shows Pro closed. The report says how many older ones were not read. |
| PF-26 | Whether a roaming profile carries the ESRI settings folder. |
| PF-27 | Symbology that does not refresh after a field calculation. |
| PF-30 | Why Pro may sit at "Creating geoprocessing cache": Python variables (`PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`) set for every program, Pro pointed at a conda environment cloned under an older release, and a build-up of geoprocessing XML log files. The `g*.cache` files themselves are listed, not graded. |
| PF-31 | The SQL Server ODBC drivers installed, an older driver beside a supported one, and ODBC tracing left on. |
| PF-32 | How long the database server's name takes to resolve, apart from the connect DL-10 times. Only with `--sde-server`. |
| PF-33 | Add-in folders Pro reads at every start, and whether any is on a share or no longer reachable. |

PF-07 keeps its performance id and runs in the data-loss block on purpose. An earlier version
reported those folders as a count and a size and called it disk churn, which routed a possible
recovery lead for the lost work into the performance section and out of the act-first list.

### How the newer checks stay honest

- **PF-30, geoprocessing cache.** Python variables are read from the registry (the user and
  machine environment), not from prostall's own process, because the launcher that started
  prostall may set Python variables of its own. A clone's release is read from its `conda-meta`
  record names (`arcpy-3.2.0-....json`), without starting its Python. An old clone Pro is not using
  is listed and not graded. If which environment Pro uses cannot be read, an old clone makes the
  check `Info`, not a verdict either way. A third-party package or add-in that breaks the cache
  build cannot be told apart from a working one by reading files, so the fix names it and the
  check does not grade it. The 1000-file log allowance is this tool's, not an Esri figure.
- **PF-31, ODBC.** "SQL Server", the driver every copy of Windows carries, is never counted as a
  second installed version. With no supported driver, the check is `Info` unless a host was given
  on the SQL Server port, because the geodatabase may not be SQL Server at all.
- **PF-32, name lookup.** Skipped without `--sde-server`, so no lookup is made unasked. The 200 ms
  allowance is this tool's.

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

- **DL-10, PF-19 and DL-12, a name or a list nobody could read.** A host name that does not resolve
  is `Info` and unmeasured. It is not `Fail`, because nothing reached the server, and it does not
  reach the act-first list. A refused or timed-out connect to a name that did resolve still fails.
  A `tasklist` that fails is `Info`, never an empty list that reads as a closed Pro.

Three checks were deleted rather than rewritten. Two read a geodatabase the workstation cannot see,
and the third read an attribute rule definition that lives on the server. All three printed
findings a workstation had no way to produce.

## The five statuses

| Status | Console label | Means |
|---|---|---|
| `Pass` | `OK` | Measured, and it is what it should be. |
| `Warn` | `WARNING` | Measured, and it is a contributing cause. |
| `Fail` | `PROBLEM` | Measured, and it is a cause. |
| `Info` | `INFO` | Read, and it decides nothing. |
| `Skipped` | `NOT CHECKED` | Not read at all. The probe threw, produced nothing, was turned off, or was never asked to run. |

The JSON report keeps the five status names; the labels are only how the console prints them.

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
| 1 | Performance findings, none of them bearing on lost edits. |
| 2 | At least one finding bearing on lost edits. |
| 3 | No finding, and nothing was determined either. |
| 64 | A usage error, or `--report` or `--html` could not write its files. |

Code 3 exists because a wrapper reads the exit code and never sees the note on stderr. A run that
could read nothing, such as PowerShell blocked or a host that is not Windows, has no finding, and
0 would tell the wrapper the machine is fine. DL-00 (who runs the tool) and DL-12 (whether Pro
runs) can answer on a machine that read nothing else, so a `Pass` from either does not make a run
determined. A finding outranks code 3.

## The fixes

`--apply` runs two fixes, and only two. Both are per-user, both are gated, and both print the
command that undoes them.

| Id | Acts on | What it does |
|---|---|---|
| AP-01 | PF-06 | Renames the local cache folder aside so Pro rebuilds it. Refused while Pro is running. |
| AP-02 | PF-11 | Writes the high-performance graphics preference for ArcGIS Pro. |

Four gates, in order: the check must exist, it must not be `Skipped`, it must be in `Warn` or
`Fail` (an `Info` check is refused as not determined, never called healthy), and a per-user fix is
refused when the session check failed. AP-01 also needs DL-12 to be a `Pass`: Pro running, or a
process list nobody could read, refuses it. That last one matters on a
remoted-in support session, where the per-user fix would write into the support account's hive and
report success while changing nothing for the editor.

AP-01 renames. It does not delete. A diagnostic that deletes a folder is how the last copy of
something disappears, and the undo is a single `move` back.

Nothing else is automated, on purpose. OneDrive redirection, the project location, the power plan,
Defender, the adapter power setting, the SMB client, the project file, dump collection and anything
inside a geodatabase are reported and never touched. Those
are policy decisions or reviewed operator actions, and a script that makes them for you is a script
nobody will run twice.

## Output

The console report has three parts. Every check prints its status and one plain sentence saying
what it found. A `PROBLEM` or `WARNING` also says why it matters, lists the evidence, and points at
its step in the to-do list. `OK`, `INFO` and `NOT CHECKED` print just the sentence; `--verbose`
prints the evidence for those too.

```
== RISKS TO SAVED EDITS =======================================================

PROBLEM      Where projects are stored  [DL-05]
             1 of 1 recent project(s) are stored on a network drive or in
             OneDrive, not on this computer.
             Why it matters: Pro reads and writes many small files while you
             work. Over a network or a sync folder this makes projects slow to
             open, causes freezes, and a save that times out can lose edits.
             What to do: see step 1 below.
             Found:
               ONEDRIVE 2026-09-16 20:59  C:\Users\editor\OneDrive - Example\Documents\...

OK           Account being checked  [DL-00]
             prostall ran as editor, and no other account on this computer has
             ArcGIS Pro data.
```

The last part, `WHAT TO DO NEXT`, is a numbered to-do list: the steps that bear on lost edits come
first, then the ones that affect speed. After the list come:

- the changes `--apply` can make;
- any automatic fix that was held back, and the check that held it;
- what `--apply` changed, with the command to undo each change;
- the checks that could not be completed, each with how to check it by hand.

When almost nothing could be measured, the report says it cannot tell whether anything needs
fixing, rather than calling the machine fine.

`--report` writes the same thing as text, an HTML page (below), and a JSON document:

```json
{
 "prostall": 2,
 "taken": "2026-09-17 20:59",
 "user": "editor",
 "elevated": false,
 "act_first": ["DL-05", "DL-03", "DL-06"],
 "fixable": ["PF-06"],
 "counts": {"Fail": 2, "Warn": 3, "Pass": 7, "Info": 8, "Skipped": 2},
 "applied": [],
 "notes": ["would apply [AP-01] Rename the ESRI local cache folder aside so Pro rebuilds it.",
           "skip [AP-02] PF-11 is already healthy (Pass)"],
 "checks": [{"id": "DL-06", "category": "Storage", "status": "Fail", "dataloss": true,
             "title": "Documents folder synced by OneDrive",
             "result": "The Documents folder has been moved into OneDrive, ...",
             "observed": "...", "expected": "...", "impact": "...", "fix": "...",
             "autofixable": false, "measured": true, "detail": ""}]
}
```

Report format 2 added `result` and `measured` to every check. With `--json` and `--report`
together, stdout stays one JSON document and the `report :` lines go to stderr.

Diff that JSON against a workstation that is known to be fast. That is the fastest way to find what
is different about this one, and it is why every timestamp in the report is UTC: two machines in
different time zones would otherwise disagree on every dated line.

A check-only run still evaluates the fix gates and prints what it would have done. `applied` is
empty unless `--apply` was given, and the self-test asserts that across a whole run.

### The HTML report

`--html FILE` (and `--report`) writes the same report as one web page, styled after the NYCEM
brand guide: NYCEM Blue header, Electric Blue headings, Sky Blue accents, the program colours for
the status badges, and Aptos type (Owners when it is installed). It is a single file with nothing
fetched from the internet, so it opens the same on a locked-down workstation as in an email.

Once the page is written it opens in your default browser. With both `--html` and `--report`,
only the `--html` page opens. `--no-open`, or `OPEN_HTML = False` in the configuration block, turns
this off. A plain run writes no page, so it opens nothing. Off Windows it opens only when there
is a desktop session, so it never starts a text browser in your terminal. If the page cannot be
opened, the run says so and carries on.

It has the same checks in the same order as the text report, with the evidence folded under
each card. In "What to do next", each step also has:

- **Folders involved**: every folder or file path the check found, with a **Copy path** button and
  an **Open folder** link. A long list of files is shown as the folders that hold them.
- **Scripts that can help**: short PowerShell scripts for the cleanup the step describes, each with
  a **Copy script** button. prostall never runs them; you paste them into PowerShell yourself.

| Step | Script | Changes anything? |
|---|---|---|
| Leftover session folders (PF-07) | List the session folders with size and project count | No |
| Leftover session folders (PF-07) | Recycle session folders older than 7 days | Previews first; `$Preview = $false` sends them to the Recycle Bin. Keeps any folder with a project file and any the report flagged |
| Pro local cache (PF-06) | Move the cache aside, as `--apply` does | Renames the folder and prints the undo |
| ArcGIS Pro crash files (DL-09) | Zip the crash files onto the Desktop for a support case | Only writes the zip |
| Crashes and freezes (DL-13) | Save the events to a CSV on the Desktop | Only writes the CSV |
| Online-only OneDrive files (DL-07) | "Always keep on this device" for the project folder | Downloads the files |
| Power plan (PF-15) | Switch to High performance | Yes, and prints the command to switch back |
| Free disk space (PF-13) | List the biggest folders in the profile; open Storage Sense | No |
| Graphics card (PF-11) | Open Windows graphics settings | No |
| Geoprocessing cache (PF-30) | Remove the Python variables from your account | Yes, and prints each old value to put back |
| Geoprocessing cache (PF-30) | Switch Pro back to `arcgispro-py3` with `proswap` | Yes; the old clone is left in place |
| Geoprocessing cache (PF-30) | Clear the geoprocessing cache files; move the log files aside | Moves them aside and prints the undo |
| Database drivers (PF-31) | Turn off ODBC tracing for your account; list the SQL Server drivers | The first does, and prints the undo |
| Name lookup (PF-32) | Time the lookup three times and show the DNS servers | No |
| Add-ins (PF-33) | List the add-in folders; clear the add-in cache | The second moves it aside and prints the undo |

After the steps, every report, a healthy one included, has an **Optional clean-up** section. A
cache can slow Pro down while every measurement reads healthy, so these are offered whatever the
checks found. They are listed lightest first, and any or all of them can be run with Pro closed:

| Script | What it moves aside |
|---|---|
| Clear the geoprocessing cache files | Only the `g*.cache` files in the local cache, into a dated folder beside it |
| Move the whole local cache aside | The local cache folder (map, tile and geoprocessing caches) |
| Rebuild the search index | `%LOCALAPPDATA%\ESRI\Index` |
| Clear the add-in cache | `%LOCALAPPDATA%\ESRI\ArcGISPro\AssemblyCache` |
| Move the geoprocessing log files aside | `%APPDATA%\ESRI\ArcGISPro\ArcToolbox\History` |
| Do all of the above at once | All four folders, in one paste |
| Reset ArcGIS Pro's settings (soft reset) | Esri's soft reset: the `ArcGISPro*` folders under `%LOCALAPPDATA%\ESRI`, `%APPDATA%\ESRI\ArcGISPro`, and the `HKCU\Software\ESRI\ArcGISPro` key after a `.reg` backup on the Desktop. Previews first; `$Preview = $false` resets. Favorites, recent projects, sign-in and options go back to defaults |

Pro rebuilds every one of these on its next start, so that first start is slower than usual.

No script deletes a file outright. Each multi-line script is one `& { }` block, so when a guard
stops it (for example because ArcGIS Pro is still open), the whole paste stops. A script that moves
or recycles Pro's files checks that Pro is closed first.

## Limits

- It reads a workstation. It does not read a server. Nothing here opens a geodatabase, runs a
  query, or signs in to a portal, and the two network probes are TCP connects that send nothing.
  With `--sde-server`, PF-32 also times the name lookup for that host, which asks the DNS server and
  sends nothing to the host.
- Off Windows almost every check reports `Info` or `Skipped`, and the run exits 3. That is correct
  behaviour and not a port: an unreadable registry is not an absent value, and the tool says so rather than guessing.
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
- The 32 MB project cap trusts the sizes in the zip header. A project that understates its size, or
  an entry that is encrypted or uses an unsupported compression, can use more memory than the cap
  or leave PF-22 `Skipped`.
- Reading a project file that is a dehydrated OneDrive placeholder makes OneDrive download it,
  although `--apply` was not given.
- The name lookup for the two TCP probes is not bounded by `--timeout`, only the connects are.
- It starts `powershell.exe`, `wevtutil`, `tasklist` and `powercfg` by name, and Windows also
  searches the current directory for a program name, so run it from a folder you control.
- When PowerShell fails, the reason shown is its first line on stderr, which can be unhelpful.
  `wevtutil` output is decoded as UTF-8 with replacement characters, so a non-ASCII event field can
  be garbled.
- A character that the console code page cannot hold prints as a `\uXXXX` escape when output is
  redirected. The `--report`, `--html` and `--json` output is encoded for you and is not affected.
- The report carries the account name, local paths, connection file names (names only, never
  contents), exclusion paths and layer names. Read it before you send it to anyone.
- `prostall.bat` waits for a key at the end, so it is for people, not for scheduled tasks. Call
  `prostall.py` directly from a script.
- `prostall.bat` must keep Windows line endings (`.gitattributes` makes git keep them). Saved with
  Unix line endings, cmd.exe can fail to find its labels.
- If `--report` or `--html` cannot be written, the exit code is 64 whatever the findings were.
- The HTML report finds paths in the evidence text by pattern. A path in an unusual sentence can
  be cut short or missed; the full evidence is always under the card.
- The **Open folder** link uses `file:///`. Some browsers and policies block those links from a
  page; **Copy path** and pasting into File Explorer's address bar always works.

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
- The geoprocessing cache as `g*.cache` files in the local cache, built once per release, and the
  causes named for a slow build (old Python environments, Python variables, a third-party
  package):
  [How to delete ArcGIS Pro temporary files](https://support.esri.com/en-us/knowledge-base/how-to-delete-arcgis-pro-temporary-files-000026912),
  [Optimise and improve Creating Geoprocessing Cache](https://community.esri.com/t5/arcgis-pro-ideas/optimise-and-improve-creating-geoprocessing-cache/idi-p/1638656),
  [Creating Geoprocessing Cache sits there forever](https://community.esri.com/t5/arcgis-pro-questions/arcgis-pro-2-8-creating-geoprocessing-cache-sits/td-p/1085889).
- The soft reset folders and registry key:
  [How to perform an ArcGIS Pro soft reset](https://support.esri.com/en-us/knowledge-base/how-to-perform-an-arcgis-pro-soft-reset-000027631).
- Several ODBC driver versions installed side by side:
  [Underlying DBMS error](https://support.esri.com/en-us/knowledge-base/error-arcgis-pro-database-connection-underlying-dbms-er-000039635).
- Not yet confirmed on the test workstation, and to be checked against a real one before the
  verdict is trusted: the `PythonCondaEnv` registry value PF-30 reads for the active environment,
  the geoprocessing history folder path, and whether an add-in folder is registered as a value
  name or as its data (PF-33 accepts either).

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
