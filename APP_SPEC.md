# APP_SPEC — the installable Windows app of the document checker

Status: design, revision 1, 27 Oct 2026. It builds on RUNNER_SPEC.md revision 2 (runner 0.2.0, rules: bot
2026-10-21.3 + runner 2026-10-27.r2). Apply it AFTER the runner build finishes. This file holds no personal data:
every name, number, date and code in it is a placeholder.

The owner's words (27 Oct): "this system is not for this pc. i want a exe file after initial build which i can
install in any pc who can operate without bot in this pc."

The app is the runner plus a window, frozen with PyInstaller into one folder. It runs on any Windows 10/11 x64 PC
with no Telegram bot, no Python, no internet, no portal and no graphics card. Staff choose ONE bundle PDF (fixed
order) or ONE folder of named files (PASSPORT.pdf, BANK STATEMENT.pdf ...), choose KLP / EAP / Bachelor's /
Master's, press Check, and get the overall PASS / FLAG / FAIL, one line per document with reasons, the
"X MISSING" lines, the "fix these pages" notices, and the runner's Excel report.

## 0. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | The FIRST deliverable is the trial zip (section 1). The installer comes later and wraps the SAME folder byte for byte. | The owner tests on another PC first. |
| D2 | One exe, two roles: the window, and a worker that is the same exe started with `--worker`. One fresh worker per check. | Section 2. |
| D3 | Tkinter + ttk, not PySide6. | About 9 MB against 46-52 MB (T1 size table); nothing we need is missing. |
| D4 | A config seam, `places.py`, with two modes: DEV (this PC, today's guards unchanged) and APP. | Section 4. |
| D5 | Processor only: ONNX Runtime's CPU execution provider, the bot's CRAFT + english_g2 in fp32, the numpy glue. T1 measured 100% verdict parity. DirectML is out and `DirectML.dll` is left out of the bundle. The CUDA option comes later (G2 a, T6). | MAP.md, T1. |
| D6 | App data, caches and temp go in `%LOCALAPPDATA%\Hangeul Document Checker`. Reports go in `Documents\Hangeul Document Checks\<case>` (the user can pick another folder). | The brief. |
| D7 | A result code `XXXX-XXXX` over the verdict lines and the check date. It is the same on any PC for the same verdicts. | The owner compares PCs. |
| D8 | Installer A (Inno Setup 6, per-user) is recommended. B (a self-made Setup.exe) is the fallback. The owner chooses (Q1). | Section 7. |
| D9 | No code signing for the trial. SmartScreen and antivirus risk is reduced by: one folder, no UPX, a version resource, and clear README steps. | Section 7.3. |

**Out of scope:** the CUDA option; the rules update file (T3: until then a rules change means a new build); the
portal details file (T2); dropping a file onto the window (phase 2, ctypes `WM_DROPFILES`). Dropping a PDF or folder
onto the exe or its shortcut works from day one (Windows passes it as `argv[1]`).

---------------------------------------------------------------------------------------------------------

## 1. Trial kit (the first deliverable)

### 1.1 The trial zip

```
Hangeul Document Checker TRIAL.zip                  (about 190 MB, ZIP deflate level 6)
  TRIAL_README.txt                                  (section 1.5; outside the app folder)
  Hangeul Document Checker\                         (the PyInstaller one-folder build, untouched)
    Hangeul Document Checker.exe
    _internal\  (runtime, libraries, engine\models\*.onnx, selftest\*, BUILD.json, MANIFEST.json)
```

- **Made by** `packaging\make_trial_zip.py` from `%LOCALAPPDATA%\HDCBuild\<build-id>\dist\Hangeul Document
  Checker`, only after `build.py check` and parity (8 d) pass. Deflate (Explorer's "Extract All" cannot open every
  LZMA zip); file times kept; nothing else in the zip. Kept in `%LOCALAPPDATA%\HDCBuild\<build-id>\trial\` with
  `trial.sha256`; never rebuilt under the same build id.
- **Use.** Unzip anywhere, double-click the exe: no install, no admin right. The app writes nothing into its own
  folder (test 8 c proves it). App data goes to `%LOCALAPPDATA%\Hangeul Document Checker` exactly as in APP mode;
  reports to `Documents\Hangeul Document Checks`. No registry entry, no shortcut.
- **Removal.** Delete the unzipped folder and `%LOCALAPPDATA%\Hangeul Document Checker` (and the reports, if wanted).
- **Same bytes in the installer.** `build.py installer --build-id <id>` extracts THIS zip, checks every file against
  `MANIFEST.json` (relative path, size, sha256 of every file of the app folder) and wraps that folder. Test 8 e checks
  the installed files against the same manifest: what was tested is what gets installed.

### 1.2 The built-in self-test

Started by the **Self-test** button, or by `"Hangeul Document Checker.exe" --selftest [--report-dir DIR]` (no window;
writes SELFTEST.txt; exit 0 when every part is OK, else 1). Each part runs in its own child process, one at a time
(the same exe with `--selftest-part <name> <result.json>`; 120 s each, the OCR part 300 s), so a native crash (an
illegal instruction, a missing DLL, an access violation) becomes a FAIL line with its exit code, not a dead app.

| Part | What it does | OK when |
|---|---|---|
| `integrity` (parent) | Checks every file of the app folder against `MANIFEST.json`: size always, sha256 in the self-test. | No file is missing or changed. Otherwise it names the file, as a path relative to the app folder. |
| `runtime` | Loads the bundled `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll`, `msvcp140_1.dll` and `pyzbar\msvcr120.dll` by full path. It reports, for each one, whether the bundled copy or a Windows copy is in the process, and its file version. | All load, and the bundled copies are the ones in use. |
| `core` | Imports numpy, cv2 and PIL. Reports numpy's `__cpu_features__` (SSE4.2, POPCNT, AVX, AVX2, AVX512F, FMA3). | The imports work. A numpy "baseline" error, or exit code `0xC000001D`, becomes CPU_UNSUPPORTED. |
| `pdf` | Opens the shipped `selftest\selftest_pages.pdf` (2 pages) with PyMuPDF and renders page 1 at 200 dpi. Saves a one-page cut into the scratch folder (a MuPDF native write). Writes a text PDF with `insert_htmlbox` holding "—", "…" and one Bengali letter, and reads it back. | The page count is 2, the render size is right, and the read-back text equals the written text. |
| `ocr` | Loads both ONNX models on `CPUExecutionProvider` and times the load. Reads the 2 made-up pages through the runner's own path (`engine.Engine(cache_root=<scratch>).read_file`). Page 1 is upright. Page 2 is turned 90°, which exercises the rotation retries. | The session providers are exactly CPU. The character error rate against the expected text is ≤ 2% per page. Page 2 is reported sideways. |
| `qr` | Encodes a made-up payload with `cv2.QRCodeEncoder`. Decodes it with pyzbar (the zbar DLL) and with `cv2.QRCodeDetector`. | Both decodes equal the payload. |
| `excel` | Writes a made-up workbook with openpyxl (sheets Summary, Documents, Pages, Findings) into the scratch folder, opens it again and compares the cells. | The cells are equal. |
| `tk` | Creates a hidden Tk root with one label, calls update, destroys it, and reports the Tcl version. | No error. |
| `guard` | Arms the APP guard. Tries `socket.create_connection(("127.0.0.1", 9))` and a write to `%WINDIR%\hdc-guard-probe.txt`. The guard refuses both before Windows is asked, so nothing is created. | Both are refused by the guard. |

- **Scratch folder** `%LOCALAPPDATA%\Hangeul Document Checker\selftest\Ünïcode ঠ test\` (spaces and non-ASCII
  letters, so such paths are covered even when the user name is plain ASCII), emptied before each run.
- **Made-up pages**, made at build time by `packaging\make_selftest_pages.py`: 2 image-only A4 pages at 200 dpi,
  JPEG quality 85, made-up text only ("SAMPLE BOARD OF EXAMINATIONS", "This is to certify that SAMPLE STUDENT ONE",
  "Roll No 000123"; no digit run longer than 6); page 2 also carries a QR of `https://example.invalid/selftest/0001`.
  Expected lines in `selftest\selftest_expected.json`.
- **Build-PC read.** `build.py check` runs the frozen self-test once, stores this PC's READ CODE (sha256[:8] of the
  normalised text read from both pages) in `selftest\build_read.json`, and only then writes `MANIFEST.json`. On
  another PC a different READ CODE means its CPU kernels read a few characters differently, which explains a
  different result code (1.3).
- **Output.** `<report root>\SELFTEST <YYYY-MM-DD HHMM>.txt`, plus a copy in `<DATA>\selftest\SELFTEST.txt`. No user
  name, PC name or path: every error text goes through `sysinfo.scrub()` (drive-letter and UNC paths and the user
  name become `<path>` and `<user>`).

SELFTEST.txt (the layout is fixed; the values here are placeholders):

```
HANGEUL DOCUMENT CHECKER - SELF-TEST
App 1.0.0, build 20261028.1-abc1234 | rules: bot 2026-10-21.3 + runner 2026-10-27.r2 | runner 0.2.0
Run: 2026-10-28 14:12 | overall: OK (9 of 9 parts)
Windows: Windows 10 Pro 22H2, build 19045.6456, x64 | Smart App Control: off | long paths enabled: no
CPU: AMD Ryzen 5 8600G w/ Radeon 760M Graphics | 6 cores, 12 threads | SSE4.2 yes, AVX yes, AVX2 yes, AVX512F yes
RAM: 31.2 GB total, 22.4 GB free | free disk (app data drive): 210 GB
App data path: 38 characters, spaces: no, non-ASCII letters: no | overridden: no
Reading: model load 1.4 s | page 1 (upright) 6.1 s | page 2 (sideways, 4 passes) 19.8 s | 6.1 s per page
Reading accuracy: page 1 CER 0.4%, page 2 CER 0.9% | READ CODE 51C2-0A7E (build PC: 51C2-0A7E, same)
Peak memory of the reading process: 1310 MB
PARTS
  integrity  OK    312 files, 0 missing, 0 changed
  runtime    OK    msvcp140 14.50 (bundled), vcruntime140 14.50 (bundled), msvcr120 12.0 (bundled)
  core       OK    numpy 2.5.2 (baseline X86_V2), opencv 5.0.0, pillow 12.3.0
  pdf        OK    PyMuPDF 1.28.2: open, render, save, text PDF
  ocr        OK    onnxruntime 1.24.4, providers CPUExecutionProvider
  qr         OK    zbar and OpenCV both decoded the made-up code
  excel      OK    openpyxl 3.1.5: written and read back
  tk         OK    Tcl/Tk 8.6
  guard      OK    network refused, outside write refused
WARNINGS
  (none)
```

A FAIL line reads `<part> FAIL <scrubbed error text>`, plus `process ended with 0xC000001D (illegal instruction)` for
a crashed child. WARNINGS: no AVX2 ("reading will be slower"); under 8 GB RAM or under 2.5 GB free; Windows below
build 19045 ("not tested"); Smart App Control on; an app data path over 100 characters.

### 1.3 The result code

**Purpose.** The same input, program and check date give the same code on any PC whenever the verdicts are the
same. The owner compares this PC's code on CHECK_0001.pdf (KLP) with the other PC's.

**Format.** 8 hex digits, upper case, shown as `XXXX-XXXX`: line 3 of summary.txt (`RESULT CODE: 3F2A-91C0 (check
date 2026-10-28)`), `run.json` `"result_code"`, the window's verdict banner (with Copy), and `checkfacts.json`.
**Module:** `src\verify\runner\resultcode.py` (Task A), in the runner so DEV runs print it too.

```python
RC_SCHEMA = "HDC-RC/1"
VERDICT_LEVELS = (FAIL, ERROR, FLAG)               # the levels that are reasons; PASS/NOTE/PRESENT/ORDER are not

def norm(s) -> str:       # Unicode NFC, whitespace runs -> one space, stripped, upper case
def reasons(fs) -> list:  # sorted set of [verdict, rule, sorted 1-based pages] for f in fs if f.verdict in VERDICT_LEVELS;
                          # sorted by (-RANK[verdict], rule, pages)
def where_key(u, mode):   # bundle: [first, last] 1-based input pages of the unit; folder: [norm(u.source_file)]
def canonical(r: RunResult) -> list:
    return [RC_SCHEMA, r.today, r.program, r.mode, r.overall,
            [[norm(report.title_of(d.unit)), where_key(d.unit, r.mode), d.verdict, reasons(d.findings)]
             for d in report.display_docs(r)],                       # the summary's document lines, in its order
            [[name, report._line_label(fs), reasons(fs)]              # NAMES, FINANCIAL CHAIN, BUNDLE (when present)
             for name, fs in cross_groups(r) if fs],                  # grouped with report._cross_line_of
            [[lv, norm(text)] for lv, text in r.missing_lines],
            sorted([n.kind, n.pages[0], n.pages[1], bool(n.blocks)] for n in r.notices)]
def code(r: RunResult) -> str:
    h = hashlib.sha256(json.dumps(canonical(r), ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return f"{h[:4]}-{h[4:8]}".upper()
```

**What goes in:** the check date, program, bundle/folder, overall verdict, and every summary verdict line: each
document line (title, pages or file name, verdict, reasons as (level, rule id, pages)); the NAMES, FINANCIAL CHAIN
and BUNDLE lines; the MISSING lines; the ORDER / HINT / NOTE / STOP notices with their pages and whether they block.

**What stays out:** timings, paths, the case id, the reading/engine line, versions, run timestamps, and the reason
WORDING. A reason's message can carry a value read by OCR (a number, a name). T1 measured that such details differ
by a character between engines in about 10% of students while the verdict stays the same. The rule id and the pages
identify the decision. A different code therefore always means a different decision, and `checkfacts.json` shows
which line.

**Check date.** Verdicts depend on the date (expiry and recency rules), so the date is part of the code. The window's
Options row offers "Check date: (•) today ( ) fixed [YYYY-MM-DD]", which becomes the runner's `today`. Two PCs can
then be compared on any day. Section 10 lists two small clock reads that the fixed date does not cover.

**Unit tests** (`tests\test_resultcode.py`, made-up RunResult fixtures):
- Stable: the same code over two calls and over two separately built identical fixtures; unchanged by other
  timings, input path, out folder, case_id, `reading`, engine stats, reason messages, or reordered findings in a line.
- Changes with: one document verdict FLAG → FAIL; one added FLAG reason (new rule id); one added MISSING line; an
  ORDER notice turning blocking; another `today`; another program.
- Matches `^[0-9A-F]{4}-[0-9A-F]{4}$`; a pinned golden value for one fixture (an algorithm change must raise
  `RC_SCHEMA`).

### 1.4 Fresh-PC failures: how each is handled

| Risk | Handling | Self-test shows |
|---|---|---|
| MSVC runtime missing. `onnxruntime.dll` imports `msvcp140.dll` + `msvcp140_1.dll`; MuPDF imports `msvcp140.dll`; `libzbar-64.dll` imports **`msvcr120.dll` (VC++ 2013)**. Neither wheel ships these. | The four VC++ 2015-2022 DLLs (14.50, from `C:\Windows\System32`) go in `_internal\`, and `msvcr120.dll` (12.0) goes in `_internal\pyzbar\`. `boot.preload_runtime()` loads them by full path before numpy, ORT or MuPDF is imported, so an older Windows copy never wins. Mixing an old msvcp140 with ORT is the known std::mutex crash. Test 8 h fails the build if any import is not satisfied. | `runtime` part: the versions and "bundled" or "Windows" for each DLL. |
| CPU without AVX/AVX2 | numpy 2.5.2's baseline is `X86_V2` (SSE4.2 + POPCNT); ORT and OpenCV pick kernels at run time. Without AVX2 the app runs, more slowly, with a warning. Without SSE4.2, numpy refuses to import; the worker or self-test child reports CPU_UNSUPPORTED. Exit code `0xC000001D` (illegal instruction) is shown with the same message, never as a crash. The window never imports numpy. | `core` part and the CPU line (AVX/AVX2/AVX512F/SSE4.2 from `IsProcessorFeaturePresent` 38-41 and numpy). |
| SmartScreen "Windows protected your PC" | It only appears when the file carries the internet mark (downloaded or emailed); a USB copy has none. README step 3: More info → Run anyway. Smart App Control (Windows 11 clean installs) blocks unsigned apps with no override; the self-test reports it, and the fix is code signing (a later decision). | The Smart App Control state (registry `CI\Policy\VerifiedAndReputablePolicyState`). |
| Antivirus quarantines the exe or a DLL | At start, the window checks every file's size against `MANIFEST.json` (instant). A missing or changed file gives MISSING_PART, naming the file relative to the app folder, with the steps: restore it in the antivirus history, allow the folder, unzip again. A worker that dies before its first event gives STARTUP_FAILED, with the same advice. | `integrity` part (full sha256). |
| Non-ASCII letters or spaces in the user name (`%LOCALAPPDATA%`, Documents) | Every path goes through Python's wide-character calls. The runner already avoids `cv2.imread`/`imwrite` on paths (it uses `imdecode`/`imencode`). The self-test scratch folder always has spaces and non-ASCII letters. Test 1.6 unzips under such a path. | The scratch-path parts, plus "non-ASCII letters: yes/no" for the app data path (never the path itself). |
| Long paths (`MAX_PATH` 260) | Case labels are cut to 40 characters. A report root over 150 characters is refused ("choose a shorter folder"). Before a check, the window works out the longest output path (`<out>\docs\<longest unit file name>`, `<out>\work\<crop name>`), cuts the label until it is under 240, and refuses with PATH_TOO_LONG if it cannot. An input path of 260 or more is refused with the same message. | LongPathsEnabled and the app data path length. |
| Windows too old | The app supports Windows 10 1809 (build 17763) or newer, x64. It is tested on 19045 (this PC) and on the owner's PC. `boot.check_platform()` refuses older builds with WINDOWS_TOO_OLD. ARM64 Windows runs the x64 build under emulation; this is reported, not refused. | The Windows line. |
| Low RAM (8 GB office PCs) | One fresh worker per check frees all memory afterwards. The worker reports its peak working set; target ≤ 2.5 GB, measured in 8 f. Below 2.5 GB free at Check, the window warns "Close other programs" with Continue / Cancel. MemoryError, or exit code `0xC0000017`, gives LOW_MEMORY. | RAM line, and the peak memory of the `ocr` part. |
| No write access to the unzip folder | The app never writes there. Every write goes to app data or the report folder, and the working folder is set to app data at start. Test 8 c proves zero write events under the app folder. | Not needed. |
| The exe started from inside the zip (Explorer preview) | The PyInstaller bootloader cannot find `_internal` and shows its own error. README step 2 says "Extract All first". | Not applicable. |
| Two windows at once | A named mutex `Local\HangeulDocumentChecker` allows one instance. A second start says "The checker is already open" and exits. This also protects the text cache from two writers. | Not applicable. |

### 1.5 TRIAL_README.txt (verbatim; `{...}` is filled in by make_trial_zip.py)

```
HANGEUL DOCUMENT CHECKER - TRIAL ({APP_VERSION}, build {BUILD_ID})

This trial runs without installing. It needs no internet and no admin rights.
It works on Windows 10 (version 1809 or newer) and Windows 11, 64-bit.

1. COPY
   Copy "Hangeul Document Checker TRIAL.zip" to the other PC (USB stick).
   Also copy CHECK_0001.pdf from this office PC's Downloads folder.

2. UNZIP
   Right-click the zip, choose "Extract All...", then "Extract".
   Do not open the program from inside the zip.

3. OPEN
   Open the extracted folder, then the "Hangeul Document Checker" folder.
   Double-click "Hangeul Document Checker.exe".
   If Windows says "Windows protected your PC": click "More info", then "Run anyway".
   If your antivirus removes a file, restore it, allow this folder, and unzip again.

4. SELF-TEST
   Click "Self-test" and wait about 2 minutes.
   It saves SELFTEST.txt in Documents\Hangeul Document Checks.

5. CHECK ONE REAL CASE AND COMPARE
   Click "Choose PDF..." and pick CHECK_0001.pdf. Choose program KLP.
   Open "Options", choose "Check date: fixed" and type {CHECK_DATE}.
   Click "Check". When it finishes, look at "Result code".
   The office PC's code for this check is: {THIS_PC_CODE}
   Same code = the two PCs gave the same verdicts.

6. SEND BACK
   Send SELFTEST.txt, and the result code you saw.
   If the code is different, also send checkfacts.json from the case folder in
   Documents\Hangeul Document Checks. It has no student names or numbers.
   Do NOT send summary.txt, report.xlsx or run.json: they hold student data.

TO REMOVE THE TRIAL
   Delete the extracted folder.
   Then paste %LOCALAPPDATA%\Hangeul Document Checker into the Explorer address bar,
   go up one folder, and delete "Hangeul Document Checker".
   The reports in Documents\Hangeul Document Checks stay until you delete them.
```

### 1.6 Test on this PC first

`packaging\tests\trial_this_pc.py --build-id <id>` (build venv, `-I -B`) does it all in Python, so the non-ASCII
path needs no console code page. App data is the real `%LOCALAPPDATA%\Hangeul Document Checker`, as on the
owner's PC. It prints ok/MISS lines and the code, never page text or student data.

1. Extract the archived trial zip with `zipfile` into a fresh `%LOCALAPPDATA%\Temp\HDC trial é <id>\` (spaces + a
   non-ASCII letter).
2. Run `<that folder>\Hangeul Document Checker\Hangeul Document Checker.exe --selftest --report-dir <that
   folder>\reports --trace-opens <that folder>\trace`. Expected: exit 0, 9 of 9 OK, READ CODE = `build_read.json`.
3. Run the real window → worker path in automatic mode: `... --auto C:\Users\User\Downloads\CHECK_0001.pdf --program
   KLP --today <CHECK_DATE> --report-root <that folder>\reports --auto-result <that folder>\auto.json --trace-opens
   <that folder>\trace`. Expected: `auto.json` shows the check finished, with result code = `{THIS_PC_CODE}` (the DEV
   runner's code from 8 d).
4. Run `trace_check.py` (8 c) on the trace folder with the app folder as the install folder. Expected: zero opens
   outside the allowed set; zero write events under the app folder; its files still match `MANIFEST.json`, none
   added.

---------------------------------------------------------------------------------------------------------

## 2. Process model

**Recommendation: one exe with two roles.** The **window** process runs Tk and does no PDF or OCR work. For each
check it starts a fresh **worker**: the same exe with `--worker <job.json>`, BELOW_NORMAL priority, no console,
stdin/stdout as pipes, stderr into `logs\worker-<job>.log`. An in-process thread is rejected for five reasons:

1. **Responsiveness.** Minutes of Python-heavy work (the numpy glue between ORT calls, split, names, birth) would
   fight Tk's main loop for the GIL. A separate process keeps the window smooth.
2. **Cancel.** A thread cannot be stopped inside an ORT or MuPDF call; a process can, in two steps. The window writes
   `cancel` to the worker's stdin, and the worker's hook raises `hooks.Cancelled` at the next page or stage (a
   BaseException, so neither the bot's nor the pipeline's `except Exception` swallows it; `Engine.case`'s `finally`
   still saves the case cache). If the worker is still alive after 10 s, the window ends it with TerminateProcess
   (its own child; no other process is touched), then removes the case folder only if this check created it
   (marker `.hdc-run-<job>`).
3. **Crash isolation.** A native crash (a damaged PDF in MuPDF, an illegal instruction on an old CPU, out of memory)
   ends only the worker; the window maps the exit code to a message (3.3).
4. **Parity.** A fresh process per check runs exactly the CLI's path with clean module state: `botimport`'s
   one-cache-root rule, `dv.TODAY`, `PageLog`'s wrapping of `dv._ocr_image`, and the audit hook that cannot be removed.
5. **Separate guards.** The runner's audit hook is permanent and process-wide; the window's file dialogs, "Open
   Excel" and "Save as PDF" stay under the window's own guard (4.2).

Cost: about 3-5 s per check (imports and model load) against 1.5-3 minutes of reading.

**Protocol** (`app\protocol.py`, version 1): one ASCII JSON line per event on the worker's stdout. At start the
worker duplicates fd 1 for the protocol and points fd 1 at fd 2 (nothing else can print into the pipe), and sets
`PYMUPDF_MESSAGE=fd:2`. The window reads the pipe on a reader thread into a `queue.Queue` and drains it with
`root.after(100)`; Tk is only touched from the main thread.

```
{"v":1,"ev":"hello","app":"1.0.0","pid":1234}
{"v":1,"ev":"stage","key":"ocr","state":"start"}                 (pipeline._Clock, every stage)
{"v":1,"ev":"file","name":"<file>","pages":14,"ocr":12}          (engine.read_file)
{"v":1,"ev":"page","done":3,"of":12,"seconds":6.1}               (engine.PageLog, during stage "ocr" only)
{"v":1,"ev":"budget","where":"birth.qr","page":7}                (a time budget ran out; section 10)
{"v":1,"ev":"done","exit":0,"overall":"FAIL","headline":"...","code":"3F2A-91C0","out":"...",
 "lines":[["FAIL","ACADEMIC HSC (p6-7): FAIL — ..."], ...],"peak_mb":1310}
{"v":1,"ev":"error","code":"ENCRYPTED","text":"<scrubbed runner text>"}
{"v":1,"ev":"cancelled"}
```

**job.json** (`%LOCALAPPDATA%\Hangeul Document Checker\jobs\<job>\job.json`; the window deletes it after the
check):

```
{"schema":"hdc-job/1","job":"<uuid hex>","input":"<abs>","program":"KLP","out":"<abs case folder>",
 "today":"2026-10-28" | null,"cache_root":"<data>\\cache","trace_dir":null | "<abs>"}
```

**Worker exit codes:** 0 done; 3 done with STOP; 2 refused (an error event was sent); 1 crash (an error event was
sent; the runner wrote `log.txt` and `run.json` `{"error": ...}`); 4 cancelled; 5 platform or start-up failure.

**The worker:** `boot` (6.2) → input pre-checks (the codes of 3.3) → `hooks.LISTENER = <event translator>` →
`pipeline.run(input, program, out, today=..., cache_root=...)` → `resultcode.code(r)`, `summary.pdf` and
`checkfacts.json` (3.2) → `done`.

---------------------------------------------------------------------------------------------------------

## 3. The window (Tkinter + ttk)

### 3.1 Layout

```
┌ Hangeul Document Checker ──────────────────────────────────────────────────────────────┐
│ 1  What to check   [Choose PDF…] [Choose folder…]   CHECK_0001.pdf                        │
│ 2  Program         [KLP ▾]  (KLP, EAP, Bachelor's, Master's; remembered)                 │
│    ▸ Options       Check date (•) today ( ) fixed [2026-10-28]   Report folder [Change…] │
│ [ Check ]  [ Cancel ]   Reading page 5 of 12 — about 50 s left   [■■■■■□□□□□□□]          │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  FAIL — 2 FAIL, 1 FLAG, 4 MISSING                       Result code 3F2A-91C0 [Copy]   │
│  Fix these pages: pages 4-5 — <ORDER message>   (one line per ORDER notice; yellow)    │
│  Document               Pages   Verdict      Reasons                                   │
│  PASSPORT               1       PASS                                                   │
│  ACADEMIC HSC           6-7     FAIL         no e-Apostille; …                         │
│  PHOTO MISSING                  MISSING                                                │
│  NAMES                          FLAG         …                                         │
│  ── the reasons of the selected line, in full ──                                       │
│ [Open Excel report] [Open report folder] [Save as PDF…] [Copy summary]                 │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ Recent checks: 28 Oct 14:12  CHECK_0001.pdf  KLP  FAIL  3F2A-91C0  (double-click opens) │
│ [Self-test]        App 1.0.0 (build …) · Rules: bot 2026-10-21.3 + runner 2026-10-27.r2  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Widgets.** The documents are a `ttk.Treeview` with a colour tag per verdict (the report's FILL colours): NOT
  JUDGED grey, MISSING orange, the NAMES / FINANCIAL CHAIN / BUNDLE lines last. The program list shows "Bachelor's"
  and "Master's" and maps them to BACHELOR and MASTER.
- **Look and keys.** DPI-aware (`SetProcessDpiAwareness(1)` before Tk starts); Segoe UI 10, banner 16 bold; Enter =
  Check, Esc = Cancel.
- **Progress.** "Reading page k of N"; time left = OCR pages left × seconds per page (6.3 s at first, from T1 at
  200 dpi, then this PC's measured average, kept in `settings.json`). After reading: "Checking documents…" with the
  stage name. A re-check with saved page texts says "Using the page texts saved by an earlier check".

### 3.2 Behaviour

- **Check.** The window does the fast pre-checks itself: the input exists and is a .pdf or a folder; the folder has
  files and is not a folder of folders; path lengths; the report folder is writable; free disk ≥ 2 × input + 500 MB.
  It then creates the case folder `<report root>\<input stem, max 40, Windows-safe> <PROGRAM> <YYYY-MM-DD HHMM>`
  (" (2)" on a clash), writes `job.json` and starts the worker. Check is disabled while a check runs.
- **Case folder**: the runner's outputs (`summary.txt` with the RESULT CODE line, `report.xlsx`, `run.json`,
  `log.txt`, `docs\` with the cut documents or staged copies, `work\`) plus `summary.pdf` and `checkfacts.json`. The
  app never writes into the checked PDF or folder: the guard refuses it, and a report folder inside the input is
  refused before the check.
- **summary.pdf** is written by the worker after the runner (PyMuPDF is already loaded there): `page.insert_htmlbox`
  on A4 pages from HTML-escaped summary lines, with the verdict words in colour, the result code, check date,
  program, and the app and rules versions in the footer. The self-test's `pdf` part proves the built-in fonts cover
  "—", "…" and Bengali; if that fails at build time, embed `%WINDIR%\Fonts\arial.ttf` and `Nirmala.ttf` (`fontfile`).
  **"Save as PDF…"** opens a save dialog (default `<label>.pdf` in the case folder) and copies `summary.pdf`; the
  window does no PDF work, and saving into the checked folder is refused.
- **checkfacts.json** (no personal data): versions, build id, result code, check date, program, mode, pages per
  engine, OCR seconds (mean / median / max), model load, peak memory, budget hits (where, page), per line [title,
  pages, verdict, rule ids], the MISSING texts, and the notice kinds and pages. Never messages, names, numbers or
  paths.
- **Buttons.** "Open Excel report" and "Open report folder" use `os.startfile` (no program for .xlsx: say so and
  offer the folder). "Copy summary" copies `summary.txt` to the clipboard.
- **Recent checks.** `recent.json` keeps the last 15 (time, input base name, program, overall, code, case folder);
  double-click opens the case folder. **Settings** (`settings.json`): report root, last program, seconds per page,
  cache days (30).
- **Start-up housekeeping** before any check: empty `jobs\` and `tmp\`; remove cache files older than 30 days (Q3);
  keep the last 20 worker logs. **Single instance:** a named mutex (1.4).

### 3.3 Plain-English messages (`app\messages.py`; codes come from the worker or the window's pre-checks)

| Code | Title | What the window says |
|---|---|---|
| ORDER notice (result) | Fix these pages | "Some pages are not in the fixed order. Fix pages a-b: <runner message>. The documents on these pages are NOT JUDGED until you rescan or reorder them and check again." A non-blocking notice ends: "(the documents were still checked)". |
| STOP (result) | Check stopped | "The father's and the mother's NID pages could not be told apart (pages a-b). Put the father's NID first, then the mother's, and check again." |
| NOT_PDF_OR_FOLDER | Not a PDF | "Choose one .pdf file (the whole bundle) or one folder of the student's documents." |
| EMPTY_FOLDER | Empty folder | "This folder has no PDF, JPG or PNG files. Choose the folder that holds the student's documents." |
| FOLDER_OF_FOLDERS | Wrong folder | "This folder holds other folders, not documents. Open it and choose one student's folder." |
| ENCRYPTED | Password-protected PDF | "<file> is password-protected. Open it, save or print a copy without the password, and check that copy." |
| UNREADABLE_PDF | PDF cannot be opened | "<file> could not be opened. It may be damaged or not a real PDF. If it opens in a PDF viewer, print it to a new PDF and check that." |
| NO_PAGES | Empty PDF | "<file> has no pages." |
| UNREADABLE_IMAGE | Picture cannot be opened | "<file> is not a readable JPG or PNG picture." |
| PATH_TOO_LONG | Path too long | "The file or report path is too long for Windows. Move the folder closer to the drive (for example C:\Checks) or choose a shorter report folder." |
| REPORT_INSIDE_INPUT / INPUT_INSIDE_REPORT | Choose another report folder | "The report folder is inside the folder being checked (or the other way round). Choose another report folder in Options." |
| REPORT_NOT_WRITABLE | Cannot save reports | "The report folder cannot be written. Choose another folder in Options." |
| DISK_FULL | Disk full | "There is not enough free disk space for the report. Free some space and check again." |
| LOW_MEMORY | Not enough memory | "The PC ran out of memory. Close other programs and check again." |
| INPUT_CHANGED | File changed | "The file changed while it was being checked (still scanning or syncing?). Check again when it is finished." |
| CPU_UNSUPPORTED | Processor too old | "This PC's processor lacks an instruction the reading engine needs (SSE4.2). Use a newer PC, and send SELFTEST.txt." |
| WINDOWS_TOO_OLD | Windows too old | "This app needs Windows 10 version 1809 or newer (64-bit)." |
| MISSING_PART | A program file is missing | "<relative file> is missing or changed — often an antivirus removed it. Restore it from the antivirus history, allow this folder, or unzip / install again." |
| STARTUP_FAILED | Could not start the check | "The checking part did not start. Your antivirus may have blocked it. Run the Self-test and send SELFTEST.txt." |
| CRASH | The check stopped | "The check stopped unexpectedly in step '<stage>'. Your files were not changed. Run the Self-test, then send SELFTEST.txt and checkfacts.json." |
| CANCELLED | Cancelled | "Check cancelled. Nothing was saved." |

- **Classification.** The worker pre-checks the input in ingest's order (exists → type → folder files →
  `pymupdf.open` → `needs_pass` → page count; `PIL.Image.open` for pictures). A leftover `ingest.InputError` or
  `safety.SafetyError` maps by class and a pinned message table, tested against bad files made in the test (an
  encrypted PDF, junk bytes, a 0-page file, an empty folder).
- **Windows exit codes:** `0xC000001D` → CPU_UNSUPPORTED; `0xC0000017` → LOW_MEMORY; `0xC0000135` / `0xC0000139` →
  MISSING_PART; other `0xC…` → CRASH; no `hello` event → STARTUP_FAILED.

---------------------------------------------------------------------------------------------------------

## 4. Path modes and the config seam

### 4.1 The two modes

`src\verify\runner\places.py` (new). It holds no this-PC path. Its mode is APP when `sys.frozen` is set or
`HDC_MODE=app` (unit tests); otherwise DEV.

| Field | DEV (this PC, values exactly as today) | APP |
|---|---|---|
| `write_root` | `PRIVATE` = `...\DOCCHECK\lab\private-runner` | `DATA` = `%LOCALAPPDATA%\Hangeul Document Checker` (`HDC_DATA_DIR` overrides, for tests) |
| `cache_root` | `PRIVATE\runner-data` | `DATA\cache` |
| `temp_dir` | `PRIVATE\tmp` | `DATA\tmp` |
| `models_dir`, `glue_data` | `...\lab\onnx\models`, `...\lab\onnx\pkg\glue_data.json` | `<bundle>\engine\models`, `<bundle>\engine\glue_data.json` (`<bundle>` = `sys._MEIPASS`, or `HDC_BUNDLE_DIR` in tests) |
| `code_root` (where `src` must come from) | the worktree | `<bundle>` (or `HDC_CODE_ROOT` in tests) |
| `forbidden` | `C:\Hangeul\BOT`, `VERIFIED STUDENT DOCUMENTS`, the numpy venv, + the "* STUDENT DOCUMENTS" sibling rule | none (the input is added per run, 4.2) |
| `no_open_root` | `C:\Hangeul\BOT` | none |
| `write_label`, `no_open_label` | "private-runner", the bot folder (message words, as today) | "the app's data folder", "protected folders" |
| `out_rule` | `--out` strictly under PRIVATE (as today) | any folder the user picked, under the APP rules of 4.2 |

**DEV literals.** Every DEV value lives in `src\verify\runner\devplaces.py` (new), moved verbatim from botimport,
safety and engine. `places.current()` imports it only in DEV. The frozen build excludes it (section 6), and the
static scan (8 b) proves it is absent.

### 4.2 Guards in APP mode

- **Worker** (`safety.prepare(out, cache_root, input)`, APP branch): writes only under `DATA` and `out` (temp is
  `DATA\tmp`); writes into the input file or folder refused even inside those roots (the input joins `forbidden`);
  network refused as today (`socket.*`, `urllib.Request`, `http.client` ...); process starts recorded and the input
  snapshotted before and after, as today.
- **APP rules for `out`** (`check_out_dir`): absolute and not a drive root; not under `%WINDIR%`, `%ProgramFiles%`,
  `%ProgramFiles(x86)%`, `%ProgramData%` or the app's own folder; not inside the input, nor the input inside it. It
  may lie under DATA (the tests use that).
- **Window** (`safety.arm_window(roots)`, new): network refused; writes only under `DATA` and the report root. "Save
  as PDF…" adds exactly the chosen file (`allow_file(path)`), after refusing a target inside the checked input.
- **Native writes** (a MuPDF save, a PIL save) raise no audit event. The runner already calls `assert_writable`
  before each one; in APP mode that checks the APP roots.

### 4.3 The seam: minimal diff plan (Task A, after the runner build is finished and reviewed)

Nothing else in the runner changes. In DEV every value, guard and message stays as today except: the new RESULT
CODE line in `summary.txt` and `"result_code"` in `run.json`; the no-op hooks; and `birth._safe_dir` asking the
guard (the same answer under PRIVATE).

| File | Function / name | Change |
|---|---|---|
| `places.py` (new) | `Places`, `mode()`, `current()`, `app_data_dir()`, `bundle_dir()` | As in 4.1. `current()` is cached and frozen. |
| `devplaces.py` (new) | `WORKTREE`, `PRIVATE`, `BOT`, `HANGEUL`, `VERIFIED`, `NUMPY_VENV`, `ONNX_LAB`, `WRITE_LABEL`, `NO_OPEN_LABEL` | The DEV literals, moved verbatim. DEV only; never bundled. |
| `hooks.py` (new) | `LISTENER = None`, `emit(event, **data)`, `class Cancelled(BaseException)` | `emit` is a no-op without a listener. It lets the listener's exception through (that is how cancel works). |
| `resultcode.py` (new) | `canonical`, `code` | Section 1.3. |
| `botimport.py` | `WORKTREE`, `PRIVATE`, `DEFAULT_CACHE_ROOT` | `= places.current().code_root / .write_root / .cache_root`. The names are kept, because other modules import them. |
| `botimport.py` | `load()` | The "src from the worktree" assertions use `code_root` and `code_root / "src"`. In APP that is `sys._MEIPASS`, where frozen modules have `__file__ = <_MEIPASS>\src\...\x.pyc`. The rule "cache_root under PRIVATE" is unchanged (PRIVATE is the APP data folder in APP). |
| `safety.py` | `BOT`, `HANGEUL`, `FORBIDDEN`, `NO_INSTALL`, `TMP_DIR`, `WRITE_ROOT`, `BOT_ROOT` | Taken from `places.current()`. They are None or empty in APP. The names are kept for `test_safety`. |
| `safety.py` | `is_forbidden` | Handles `HANGEUL is None` (no sibling rule). |
| `safety.py` | `_write_refusal`, `_check_bot`, `check_out_dir` | The literal message words ("private-runner", the bot path) become `places.current().write_label` / `no_open_label` / `BOT_ROOT`. The DEV text is identical. `check_out_dir` gets the APP branch (4.2). |
| `safety.py` | `assert_writable` | In APP it checks the armed policy's roots (or DATA when nothing is armed) instead of `_private`. |
| `safety.py` | `prepare` | APP branch: write roots `[DATA, out]`, `forbidden += [input]`, `bot_root=None`. The DEV branch is unchanged. |
| `safety.py` | `arm_window(roots)`, `allow_file(path)` (new) | The window guard (4.2). |
| `engine.py` | `ONNX_LAB`, `MODELS`, `GLUE_DATA` | `ONNX_LAB` moves to devplaces. `MODELS` and `GLUE_DATA` come from `places.current()`. |
| `engine.py` | `PageLog.__call__` | `hooks.emit("ocr", phase="start")` before a non-memo OCR call; `emit("ocr", phase="end", seconds=…, memo=hit)` after it. |
| `engine.py` | `Engine.read_file`, `Engine._read_image` | `hooks.emit("file", name=…, pages=n, ocr=<OCR pages to read>)` once the page kinds and served pages are known. |
| `pipeline.py` | `_Clock.__call__` | `hooks.emit("stage", key=…, state="start")` before the yield, and `state="end", seconds=…` in the `finally`. The listener never raises on "end". |
| `pipeline.py` | `_write_log` | The literal "opens under C:\\Hangeul\\BOT" becomes `places.current().no_open_label`. |
| `__main__.py` | `check_args` | The `--out` test becomes `safety.check_out_dir` in APP (wrapped as BadArgs). DEV is unchanged. |
| `report.py` | `summary_lines` | A third line, `RESULT CODE: <code> (check date <today>)`. |
| `report.py` | `_facts`, `write_all` | The label replaces the literal bot path. `run.json` gets `"result_code"`. |
| `tests\test_report.py` | the summary format test | Accepts the new third line. This is the only existing test that is edited. |
| `birth.py` | `_safe_dir` | `try: safety.assert_writable(p); return p` `except SafetyError: return None`, instead of `is_relative_to(PRIVATE)`. Without it, APP runs would silently write no crops. |
| `birth.py` | `decode_qr_image`, `code128_votes` | `hooks.emit("budget", where=…, page=…)` at each time-budget `break` (record only). |
| `tin.py` | `_stage_b`, `_stage_e` | The same budget record. |
| `doccheck.py` | `crop_unit_photo`, `check_documents.work_for` | The `botimport.PRIVATE / "tmp"` fallbacks become `places.current().temp_dir`. |
| `tests\test_places.py`, `tests\test_resultcode.py` (new) | | Tests 8 a and 1.3. |

**Not touched:** gpuread.py and run_case.py (DEV only, excluded from the bundle), ingest, split, names, identity,
bank, financial, language, verdict, model and strict. The bot modules are not touched either. The two literal path
constants in `prototype_page_types.py` are handled in the staged copy only (6.1).

---------------------------------------------------------------------------------------------------------

## 5. Bundle contents

**Python code** (in the PYZ, compiled from the scrubbed stage of 6.1):

| Group | Modules |
|---|---|
| Runner | `src.verify.runner`: `model`, `strict`, `botimport`, `places`, `hooks`, `resultcode`, `safety`, `ingest`, `engine`, `pagetype`, `split`, `identity`, `names`, `bank`, `tin`, `financial`, `doccheck`, `language`, `birth`, `verdict`, `report`, `pipeline`, `__main__` |
| App | `src.verify.runner.app.*` (section 9) |
| Bot (worktree 07b1f22 + the prototype) | `src` (`__init__`), `src.verify.{doc_verifier, page_checks, common, rules, text_cache, field_check, prototype_page_types}`, `src.scraper.ocr_validator`, `src.sheets.progress_builder` (`field_check` imports it at module level; it is never run) |
| Glue | top-level `glue_numpy` and `easyocr_vendor`, from `C:\Hangeul\DOCCHECK\lab\onnx\pkg` |
| Vendored | `pyzbar`, `openpyxl` 3.1.5 and `et_xmlfile` 2.0.0, from `C:\Hangeul\DOCCHECK\lab\private-runner\engine\vendor`, checked against `VENDOR.json` |
| Libraries | numpy, cv2, pymupdf, PIL, onnxruntime (no `tools`, `transformers`, `quantization`), tkinter, and the stdlib they need |

**Data and DLLs** (in `_internal`; sizes on disk):

| Part | Files | MB |
|---|---|---|
| Launcher + PYZ | `Hangeul Document Checker.exe` | ~10 |
| Python 3.12.10 | `python312.dll`, `python3.dll` (cv2 needs it), `base_library.zip`, stdlib `.pyd`, `libffi-8.dll`, `libcrypto-3.dll` (for `_hashlib`) | ~16 |
| VC++ runtime | `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll`, `msvcp140_1.dll` (14.50, `C:\Windows\System32`); `pyzbar\msvcr120.dll` (12.0) | ~1.7 |
| Tk 8.6 | `_tkinter.pyd`, `tcl86t.dll`, `tk86t.dll`, `_tcl_data`, `_tk_data` | ~9 |
| numpy 2.5.2 | incl. `numpy.libs\libscipy_openblas64_*.dll`, `msvcp140-<hash>.dll` | ~27 |
| OpenCV 5.0.0 headless | `cv2.pyd` (`opencv_videoio_ffmpeg500_64.dll` left out: 31 MB, video only) | ~86 |
| PyMuPDF 1.28.2 | `_mupdf.pyd`, `mupdfcpp64.dll`, `_extra.pyd` | ~40 |
| onnxruntime 1.24.4 | `onnxruntime.dll`, `onnxruntime_pybind11_state.pyd`, `onnxruntime_providers_shared.dll`. `DirectML.dll` is left out (18.5 MB); `onnxruntime.dll` only delay-loads it, d3d12 and dxgi, so a CPU-only session never loads it. | ~48 |
| Pillow 12.3.0 | | ~11 |
| zbar | `pyzbar\libzbar-64.dll`, `pyzbar\libiconv.dll` | ~1.2 |
| Models | `engine\models\craft_mlt_25k.onnx` (83,059,970 B), `english_g2.onnx` (15,137,538 B), `engine\glue_data.json`; sha256 pinned in BUILD.json | 98.2 |
| Self-test | `selftest\selftest_pages.pdf`, `selftest_expected.json`, `build_read.json` | ~0.5 |
| Build facts | `BUILD.json`, `MANIFEST.json` | <0.1 |
| **Total** | about 300 files | **~350** |

Packed, this is about 190 MB as the trial zip (deflate) and about 155-170 MB as the Inno Setup LZMA2 installer. For
comparison, T1 measured 175 MB for a build that still carried PySide6 and DirectML.

**Excluded** (spec `excludes`; 8 b checks the result):
- torch, torchvision, easyocr, scipy, skimage, PySide6, shiboken6, PyQt5, PyQt6, matplotlib, pandas, IPython;
  pytest, setuptools, pip, sympy, mpmath, google (protobuf), venv, ensurepip, idlelib, lib2to3, pydoc_data;
  `onnxruntime.tools`, `.transformers`, `.quantization`.
- Portal and network code: `src.scraper.client`, `src.sheets.passport_issue`, `src.sheets.verified_docs`,
  `src.verify.auto_verify`, pypdf, googleapiclient, google_auth_oauthlib, httpx.
- DEV or test only: `src.verify.runner.gpuread`, `.devplaces`, `.tests`, `.selftest` (the runner's DEV self-tests),
  `src.verify.runner.app.tests`. `run_case.py` lies outside the worktree's `src` and is never staged.
- Hardening, kept only while the 8 g smoke stays green: ssl, _ssl, http, urllib.request, xmlrpc, ftplib, smtplib.

---------------------------------------------------------------------------------------------------------

## 6. Build

### 6.1 Stage and scrub (`packaging\stage.py`)

PyInstaller freezes a staged copy, never the worktree. Build root: `%LOCALAPPDATA%\HDCBuild\<build-id>\`, with
the folders `stage`, `work`, `dist`, `trial`, `installer` and `reports`. The path holds no forbidden string, even if
a code object ever kept it.

1. **Copy** an explicit file list into `stage\`: the runner and app modules of section 5 (never `devplaces.py`,
   `gpuread.py`, `tests\`, `selftest\`); the bot files of section 5 with their package `__init__.py`; `glue_numpy.py`
   and `easyocr_vendor.py`; `vendor\{pyzbar, openpyxl, et_xmlfile}` (tree hashes checked against `VENDOR.json`);
   `engine\models\*.onnx` and `engine\glue_data.json` (sizes and sha256 checked against the pins).
2. **Copy the runtime DLLs** into `vcrt\`: `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll`,
   `msvcp140_1.dll`, `msvcr120.dll`, from `C:\Windows\System32`, each at file version ≥ 14.40 (12.0 for msvcr120).
   The versions are recorded.
3. **Blank the docstrings** of every staged module of ours (`src\**`, the glue): each becomes an empty
   triple-quoted string with the same number of lines, so traceback line numbers stay right. Comments never reach
   bytecode. Nothing reads `__doc__` at run time (only the prototype's argparse CLI, never run).
4. **Swap two constants** in `prototype_page_types.py`, the CLI loader's defaults that the runner never uses:
   `DOCS_ROOT` → `Path("no-docs-root")`, `TEXT_DIR` → `Path("no-text-dir")`. No other constant changes.
5. **AST check:** for every scrubbed file, `ast.dump` of the original (docstrings removed, the two constants
   swapped) must equal `ast.dump` of the scrubbed file; any other difference stops the build.
6. **Fast scan:** the forbidden strings of 8 b have zero hits in `stage\`.
7. **`stage\BUILD.json`:** `app_version`; `build_id` = `YYYYMMDD.N-<git short>[-dirty]`; `runner_version`,
   `base_rules_version`, `runner_rules_version` (read from the staged `model.py`); `spec` "RUNNER_SPEC r2 / APP_SPEC
   r1"; `built`; numpy's `__cpu_baseline__`; the model pins; the VC++ DLL versions.
8. **`stage\version_info.txt`** (PyInstaller `VSVersionInfo`): FileVersion `1.0.0.N`, ProductVersion `1.0.0`,
   ProductName and FileDescription "Hangeul Document Checker", CompanyName "Hangeul", Comments "rules 2026-10-21.3 +
   2026-10-27.r2; runner 0.2.0; build <id>", OriginalFilename "Hangeul Document Checker.exe".
9. **Extras:** `stage\icon.ico` (made-up artwork from `make_icon.py`) and `stage\hdc_entry.py` (three lines:
   import `src.verify.runner.app.entry`, call `sys.exit(main())`).

### 6.2 The spec file (`packaging\hdc.spec`) and the app's boot

```python
# PyInstaller 6.22.3, one folder, windowed. HDC_STAGE is set by build.py.
STAGE = Path(os.environ["HDC_STAGE"]); NAME = "Hangeul Document Checker"
SKIP = ("src.verify.runner.gpuread", "src.verify.runner.devplaces", "src.verify.runner.tests",
        "src.verify.runner.selftest", "src.verify.runner.app.tests")
hidden = collect_submodules("src.verify.runner", filter=lambda m: not m.startswith(SKIP))  # pipeline imports stages by name
hidden += ["src.verify.runner.__main__", "glue_numpy", "easyocr_vendor", "pyzbar.pyzbar"]
VCRT = ["vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll"]
binaries = [(str(STAGE / "vcrt" / n), ".") for n in VCRT] + [
    (str(STAGE / "vendor/pyzbar/libzbar-64.dll"), "pyzbar"), (str(STAGE / "vendor/pyzbar/libiconv.dll"), "pyzbar"),
    (str(STAGE / "vcrt/msvcr120.dll"), "pyzbar")]
datas = [(str(STAGE / "engine/models"), "engine/models"), (str(STAGE / "engine/glue_data.json"), "engine"),
         (str(STAGE / "selftest"), "selftest"), (str(STAGE / "BUILD.json"), ".")]
a = Analysis([str(STAGE / "hdc_entry.py")], pathex=[str(STAGE), str(STAGE / "vendor")], binaries=binaries,
             datas=datas, hiddenimports=hidden, excludes=EXCLUDES, noarchive=False, optimize=0)
DROP = ("directml.dll", "opencv_videoio_ffmpeg")
a.binaries = [e for e in a.binaries if not any(d in e[0].lower() for d in DROP)
              and not (e[0].lower() in VCRT and e[1] != str(STAGE / "vcrt" / e[0]))]   # our VC++ copies win
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, console=False, upx=False, strip=False,
          icon=str(STAGE / "icon.ico"), version=str(STAGE / "version_info.txt"), uac_admin=False)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, strip=False, name=NAME)
```

`optimize=0` is kept on purpose. `-OO` would also strip docstrings from numpy, PyMuPDF and openpyxl, which is
untested there. The runner and bot have no `assert`, but our docstrings are blanked by the scrub anyway.

**Boot order** (`app\entry.py` → `app\boot.py`, in every process, before any heavy import):
1. Parse the flags: none or a path (window), `--worker`, `--selftest`, `--selftest-part`, `--cli`, `--auto`,
   `--trace-opens DIR`. With `--trace-opens`, `trace.arm(DIR)` comes first.
2. `boot.isolate()`: every `sys.path` entry must lie under `sys._MEIPASS` (others are dropped and logged); the
   working folder becomes DATA; TMP and TEMP become `DATA\tmp`.
3. `boot.check_platform()`: Windows build ≥ 17763, 64-bit; CPU flags from `sysinfo.cpu_flags()` (pure ctypes).
4. `boot.preload_runtime()`: the VC++ DLLs and msvcr120, by full path.
5. The window also runs the integrity quick check (sizes) and takes the single-instance mutex; then dispatch.

`--cli <input> --program P --out DIR [--today D] [--cache-root DIR]` calls the runner's `__main__.main(argv)` in APP
mode. The tests use it.

### 6.3 Version stamping

The window footer, SELFTEST.txt, `summary.pdf`, `checkfacts.json`, `log.txt` (an added line) and the exe's version
resource all show the app version and build id; the rules (`BASE_RULES_VERSION`, the bot's RULES_VERSION, +
`RUNNER_RULES_VERSION`); and the runner revision (`RUNNER_VERSION`, "RUNNER_SPEC r2"). They come from the staged
`model.py` through `BUILD.json`, so they cannot drift from the frozen code. `app\version.py` reads `BUILD.json` from
the bundle, with DEV defaults when unfrozen.

### 6.4 Commands (all absolute; nothing is ever installed into a venv)

```
set BPY=C:\Hangeul\DOCCHECK\lab\onnx\.venv-pkg-numpy\Scripts\python.exe
set RPY=C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Scripts\python.exe
set PKG=C:\Hangeul\JARVIS\prototype\page-types\packaging
set PYTHONDONTWRITEBYTECODE=1
%BPY% -I -B %PKG%\build.py stage                    -> %LOCALAPPDATA%\HDCBuild\<id>\stage (prints <id>)
%BPY% -I -B %PKG%\build.py freeze --build-id <id>   -> runs:
      %BPY% -I -B -m PyInstaller --noconfirm --clean --distpath %LOCALAPPDATA%\HDCBuild\<id>\dist ^
            --workpath %LOCALAPPDATA%\HDCBuild\<id>\work %PKG%\hdc.spec        (env HDC_STAGE=...\<id>\stage)
%BPY% -I -B %PKG%\build.py check  --build-id <id>   -> frozen self-test, build_read.json, MANIFEST.json,
                                                       scan_bundle (8 b), dll_check (8 h)
%RPY% -I -B %PKG%\tests\parity.py --build-id <id> --today <YYYY-MM-DD>    (8 d; gives THIS_PC_CODE)
%BPY% -I -B %PKG%\build.py trial  --build-id <id> --check-date <YYYY-MM-DD> --this-pc-code <XXXX-XXXX>
%BPY% -I -B %PKG%\tests\trial_this_pc.py --build-id <id>                  (1.6)
%BPY% -I -B %PKG%\build.py installer --build-id <id> --kind A|B           (phase 2, after the owner's trial)
```

`PYTHONDONTWRITEBYTECODE=1` keeps PyInstaller's helper subprocesses from writing `__pycache__` into the venv. The
build runs `git rev-parse` and `git status` read-only, for the build id.

---------------------------------------------------------------------------------------------------------

## 7. Installer (phase 2; the owner chooses A or B, Q1)

Both installers wrap the folder extracted from the archived trial zip, after the manifest check (1.1).

### 7.1 A — Inno Setup 6 (recommended)

- **Getting it** needs the owner's permission to download it (about 6 MB, jrsoftware.org); installed "for me only"
  in `%LOCALAPPDATA%\Programs\Inno Setup 6\`.
- **Build:** `"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /DAppFolder="<installer-src>\Hangeul Document Checker"
  /DAppVersion=1.0.0 /DBuildId=<id> %PKG%\installer\hdc.iss`.
- **`hdc.iss`:**
  - `[Setup]`: `AppId={{<one fixed GUID>}`, `AppName=Hangeul Document Checker`, `AppVersion`,
    `AppPublisher=Hangeul`; `PrivilegesRequired=lowest` (per-user, no admin right; installs to
    `%LOCALAPPDATA%\Programs\Hangeul Document Checker`); `DefaultDirName={autopf}\Hangeul Document Checker`;
    `DisableProgramGroupPage=yes`; `ArchitecturesAllowed=x64compatible`;
    `ArchitecturesInstallIn64BitMode=x64compatible`; `MinVersion=10.0.17763`; `Compression=lzma2/ultra64`;
    `SolidCompression=yes`; `WizardStyle=modern`; `UninstallDisplayIcon={app}\Hangeul Document Checker.exe`;
    `OutputBaseFilename=Hangeul Document Checker Setup 1.0.0`; `VersionInfo*`.
  - `[Files]` `Source: "{#AppFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs`.
    `[Tasks]` `desktopicon` (checked). `[Icons]` `{autoprograms}\Hangeul Document Checker` and `{autodesktop}\...`
    (Tasks: desktopicon). `[Run]` "Open now" (postinstall, skipifsilent).
  - `[Code]` at `usPostUninstall` asks "Also delete the app's saved data (saved page texts, recent checks,
    settings)? Your reports in Documents are kept." Default Yes (Q3); a silent uninstall takes the default.
- **Result:** uninstall in Settings → Apps (HKCU key `{AppId}_is1`). Inno's setup stub is widely known to antivirus
  engines, which helps.

### 7.2 B — self-made Setup.exe (fallback)

Files in `%PKG%\setup_b\`: `setup_main.py`, `uninstall_main.py`, `setup_b.spec`.
- **Setup.exe** is a PyInstaller onefile with a Tk wizard (welcome, target, "desktop shortcut" checked, install,
  "open now"). It carries `payload.zip` (the trial app folder) and extracts it to `%LOCALAPPDATA%\Programs\Hangeul
  Document Checker` after checking it against `MANIFEST.json`.
- **Shortcuts** (Start menu, desktop) via `powershell.exe -NoProfile -NonInteractive -Command` with `(New-Object
  -ComObject WScript.Shell).CreateShortcut(...)`.
- **Uninstall entry** `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\HangeulDocumentChecker`:
  `DisplayName`, `DisplayVersion`, `Publisher`, `InstallLocation`, `DisplayIcon`,
  `UninstallString="<dir>\Uninstall.exe"`, `EstimatedSize`, `NoModify=1`.
- **Uninstall.exe** (onefile) copies itself to `%TEMP%`, runs from there, removes the app folder, the shortcuts and
  the HKCU key, and asks A's data question.
- **Against A:** a onefile setup unpacks about 190 MB into `%TEMP%` before it starts (slower); onefile PyInstaller
  exes are flagged by antivirus more often; about 2 days of work against half a day.

### 7.3 SmartScreen and antivirus (both options, and the trial)

Unsigned PyInstaller exes can trigger SmartScreen when they carry the internet mark, and some antivirus engines flag
PyInstaller's prebuilt bootloader. **What reduces it:** one folder (not onefile) for the app; no UPX (`upx=False`
everywhere); a full version resource and the same product name everywhere; no self-extraction into `%TEMP%`;
delivery by USB (no internet mark). **Further, later (owner decisions):** a code-signing certificate (it also lets
the app run under Windows 11 Smart App Control); submitting the files to Microsoft's false-positive portal (needs
internet; the owner's action); rebuilding the bootloader from source (needs a C compiler, not installed).

---------------------------------------------------------------------------------------------------------

## 8. Tests

Personal data stays private: harnesses print ok/MISS, counts, rule ids and codes only. Outputs that hold student
data go under `C:\Hangeul\DOCCHECK\lab\private-runner\apptest\<build-id>\`. The made-up self-test pages and the
owner's own CHECK_0001.pdf are the only inputs used outside that folder.

**(a) Config seam unit tests** (`tests\test_places.py`, runner venv, `unittest discover` as in RUNNER_SPEC 2.4).
- **DEV:** `places.current()` equals the devplaces literals; `botimport.PRIVATE` / `DEFAULT_CACHE_ROOT`,
  `safety.FORBIDDEN` / `BOT_ROOT` / `TMP_DIR` and `engine.MODELS` / `GLUE_DATA` are exactly today's values; the whole
  existing runner suite passes unchanged (except the edited summary-format test).
- **APP, in a child process** with `HDC_MODE=app`, `HDC_DATA_DIR=...\private-runner\selftest\places\data`,
  `HDC_BUNDLE_DIR=` a fake bundle with dummy model files, `HDC_CODE_ROOT=` the worktree. All writes stay under
  private-runner (RUNNER_SPEC S1).
  - Every Places field lies under the given folders; `forbidden` and `no_open_root` are empty; `devplaces` is not in
    `sys.modules`; `botimport.load()` with a cache root outside DATA raises.
  - After `prepare(out=...\places\reports\c1, input=...\places\input)`: writes under DATA and `out` work; a write to
    `...\places\elsewhere\x` and a write into the input are refused and nothing is created;
    `socket.create_connection(("127.0.0.1", 9))` is refused.
  - `check_out_dir` refuses out inside the input, the input inside out, a drive root, `%WINDIR%\x` (pure checks, no
    write). `arm_window` + `allow_file` allow exactly that one file.
  - `hooks.emit` is a no-op without a listener; a listener's `Cancelled` passes through `pipeline.run`.

**(b) Static independence** (`packaging\tests\scan_bundle.py <dist folder>`, build venv).
- **Reads** every dist file raw; every member of `base_library.zip`; every entry of the exe's PyInstaller archive
  (`PyInstaller.archive.readers.CArchiveReader`, decompressed); and every module of the embedded PYZ (the TOC entry
  of type `z`, or a separate `.pyz` if one exists), decompressed, then unmarshalled so a hit names the module,
  `co_filename` and the constant.
- **Looks for** (case-insensitive, UTF-8 and UTF-16LE): `c:\hangeul`, `c:/hangeul`, `c:\\hangeul`, `jarvis`,
  `private-runner`, `.venv`. **Zero hits are allowed.**
- **Also checks:** the `src.*` modules in the PYZ equal the section 5 list exactly; no excluded module is present;
  `DirectML.dll` and `opencv_videoio_ffmpeg*` are absent.
- **Advisory** (reported, not failing): `\users\`, `telegram`, `supabase`, `token.json`. The last three are expected
  from `src\__init__.py`'s redaction patterns and `progress_builder`, listed as known.
- A pre-check of the build venv's site-packages and the stdlib found zero hits, so the only expected hits are ours,
  which the scrub and the seam remove.

**(c) Runtime independence** (`packaging\tests\trace_check.py`, runner venv, so it can pick the folders).
- **The trace.** With `--trace-opens DIR`, every process (window, worker, self-test children) first arms an audit
  hook that appends one JSON line per event to `DIR\trace-<pid>.jsonl` (the log is opened before the hook). Audit
  events: `open` (path, mode), `os.listdir`, `os.scandir`, `os.mkdir`, `os.remove`, `os.rename`, `shutil.*`,
  `ctypes.dlopen`, `os.add_dll_directory`, `winreg.OpenKey`, `subprocess.Popen`, `os.startfile`, `socket.*`.
  Wrappers also catch native file use: `pymupdf.open`, `pymupdf.Document.save`, `onnxruntime.InferenceSession`,
  `PIL.Image.open` / `Image.save`, `cv2.imread` / `cv2.imwrite`.
- **Runs:** CHECK_0001.pdf (KLP) through `--auto`; 2 held-out student folders through `--cli`, picked by
  `selftest\corpus.halves()` (the first KLP sid and the first MASTER sid; paths never printed), with
  `HDC_DATA_DIR=...\private-runner\apptest\<id>\data`.
- **Allowed set:** {the install or unzip folder, DATA (incl. `DATA\tmp`), the input, the report folder, the trace
  folder, `%WINDIR%` (incl. System32 and Fonts)}, device names (`NUL`, `CONIN$`), and bare DLL names in
  `ctypes.dlopen` (pyzbar first tries `libiconv.dll` by bare name, which searches only the exe folder and System32,
  then loads by full path).
- **Pass:** zero paths outside the allowed set (so nothing under `C:\Hangeul\` except the input and report
  folders, and nothing in the system `%TEMP%`); zero write events under the install folder; no `socket` event that
  was not refused; registry reads only of the expected keys (CPU name, Windows version, LongPathsEnabled, Smart App
  Control state). Any exception needs a written reason in the checker.
- **Limit:** opens inside native code (MuPDF's own fonts, Windows loading DLLs) are not seen; the static scan, the
  DLL check and the owner's other PC cover them.

**(d) Parity** (`packaging\tests\parity.py`, runner venv).
- **Runs:** each input by the DEV runner (`%RPY% -I -B -m src.verify.runner <input> --program P --out
  ...\apptest\<id>\dev\<n> --cache-root ...\apptest\<id>\cache-dev --today D`) and by the frozen app (`--cli`, a
  fresh DATA, the same `--today`). Both read on the CPU from an empty cache.
- **Inputs:** CHECK_0001.pdf (KLP), whose code becomes `THIS_PC_CODE`; the 2 folders of (c); the made-up self-test
  PDF.
- **Pass:** the same exit code; `summary.txt` identical except the `Report:` line; the same result code; in
  `run.json`, equal `overall`, `docs[*].verdict`, `docs[*].findings[*].(verdict, rule, pages)`, `missing_lines` and
  `notices`; page texts equal on ≥ 13 of 14 CHECK_0001 pages (any differing page listed by number and character
  difference). A differing verdict is a MISS.

**(e) Install cycle** (`packaging\tests\install_cycle.py`, phase 2, as this normal user; never touches an install
made by the owner).
- **A:** `"<Setup.exe>" /VERYSILENT /SUPPRESSMSGBOXES /DIR="%LOCALAPPDATA%\Temp\HDC install test" /NOICONS
  /TASKS="" /LOG="...\install.log"`. Every installed file matches `MANIFEST.json`; `--selftest --report-dir <tmp>`
  gives exit 0; the HKCU uninstall key exists. Then `"<dir>\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES` removes the
  folder and the key, and leaves no shortcut.
- **B:** the same with `Setup.exe --silent --dir ... --no-shortcuts` and `Uninstall.exe --silent`.

**(f) Timing on this PC** (`packaging\tests\timing.py`, Ryzen 5 8600G; `reports\timing.json`, counts and seconds
only). `--cli` on CHECK_0001 with a cold cache, and the self-test pages. It reports the worker start, the model
load, seconds per OCR page (mean, median, p90, max, from `run.json pages[].seconds`), stage timings, the total and
the peak working set. Expected: about 6.3 s per page at 200 dpi (T1), within ±15% of the DEV runner on the same
pages; peak memory ≤ 2.5 GB. Other PCs are measured by their own SELFTEST.txt (T5).

**(g) Frozen smoke** (in `build.py check`): `--selftest` gives 9 of 9 OK on this PC; `--cli` on the made-up PDF
gives exit 0; importing every bundled `src.*` module in a `--selftest-part imports` child raises nothing.

**(h) DLL closure** (`packaging\tests\dll_check.py`, pefile from the build venv).
- For every `.dll` and `.pyd` in the dist folder, every imported DLL must be in the bundle (the same folder or
  `_internal`) or on `packaging\dll_baseline.txt`: the DLLs every Windows 10 x64 has (kernel32, user32, gdi32,
  advapi32, shell32, ole32, oleaut32, comdlg32, comctl32, shlwapi, ws2_32, iphlpapi, crypt32, bcrypt, rpcrt4,
  version, winmm, imm32, dwmapi, uxtheme, setupapi, dbghelp, shcore, ucrtbase, `api-ms-win-*`, msvcrt, ntdll,
  d3d12, dxgi, ...), each with a one-line reason.
- Delay-loaded imports are listed separately and may be missing only when on the "never called" list:
  `directml.dll` (CPU-only sessions).
- `vcruntime140*`, `msvcp140*` and `msvcr120` must NOT be satisfied by the baseline: they must be in the bundle at
  version ≥ 14.40 (12.0 for msvcr120).

**App unit tests** (`src\verify\runner\app\tests\`, runner venv, APP mode with `HDC_DATA_DIR` under private-runner,
made-up data only): protocol encode/decode; the controller against a scripted fake worker
(`app\tests\fake_worker.py`) for progress, done, error, cancel and the 10 s TerminateProcess; message classification
against real bad inputs; case labels (Windows-safe, 40-character cut, clash suffix); the longest-path computation;
`recent.json`; sysinfo (`scrub()` removes paths and the user name, and SELFTEST.txt holds neither); the self-test
with fake part results, including a crashed child mapped to its code. Result code tests: section 1.3.

**The real clean-PC test** is the owner's other PC: the trial (section 1) first, then the installer.

---------------------------------------------------------------------------------------------------------

## 9. Build tasks

New app code goes in `C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\app\`, packaging files in
`C:\Hangeul\JARVIS\prototype\page-types\packaging\`. The runner's existing files change only through Task A. Each
task codes against the contracts of sections 1.2, 1.3, 2, 3.3 and 4 and fakes the other tasks' modules in its unit
tests. Nobody commits; the orchestrator commits after review.

**Phase 1 (the trial):**

| Task | Owns (disjoint) | Delivers |
|---|---|---|
| **A Seam** (starts when the runner build is reviewed) | `runner\places.py`, `devplaces.py`, `hooks.py`, `resultcode.py`, `tests\test_places.py`, `tests\test_resultcode.py`, and ONLY the edits of 4.3 in `botimport.py`, `safety.py`, `engine.py`, `pipeline.py`, `__main__.py`, `report.py`, `birth.py`, `tin.py`, `doccheck.py`, `tests\test_report.py` | The seam. The full runner suite plus T1 (`selftest\check0001.py`) are green in DEV. |
| **B Window** | `app\window.py`, `app\messages.py`, `app\recent.py`, `app\settingsfile.py`, `app\userdirs.py` (Known Folder Documents, case labels, path lengths), `app\tests\test_messages.py`, `test_recent.py`, `test_userdirs.py` | Section 3. `--auto` mode for tests. |
| **C Worker** | `app\__init__.py`, `app\entry.py`, `app\boot.py`, `app\worker.py`, `app\controller.py`, `app\protocol.py`, `app\spawn.py`, `app\trace.py`, `app\summarypdf.py`, `app\checkfacts.py`, `app\version.py`, `app\tests\fake_worker.py`, `test_protocol.py`, `test_controller.py`, `test_worker.py`, `test_boot.py`, `test_trace.py` | Section 2, the boot of 6.2, `--cli` and `--trace-opens`. |
| **D Self-test** | `app\selftest.py`, `app\sysinfo.py`, `app\tests\test_selftest.py`, `test_sysinfo.py`, `packaging\make_selftest_pages.py` | Section 1.2 and SELFTEST.txt. |
| **E Build** | `packaging\build.py`, `stage.py`, `hdc.spec`, `version.py`, `make_icon.py`, `manifest.py`, `make_trial_zip.py`, `TRIAL_README.txt`, `dll_baseline.txt`, `packaging\tests\test_stage.py` | Sections 6, 1.1 and 1.5. |
| **F Release tests** | `packaging\tests\scan_bundle.py`, `dll_check.py`, `trace_check.py`, `parity.py`, `timing.py`, `trial_this_pc.py` | Tests 8 b-d, f-h and 1.6. |

**Shared contracts:** C defines the event and error codes (2, 3.3) that B renders. D's parts run through C's
`spawn.self_command("--selftest-part", name, out)`, and C's `boot` calls D's `sysinfo.cpu_flags()`. B, C and D read
versions only through C's `app\version.py`. E owns `packaging\STAGE_LIST.txt`, which must list every module A-D
create; the other tasks send E their entries.

**Integration (one integrator, after A-F):** `build.py stage → freeze → check` (owners fix their own files) →
parity (8 d), giving this PC's code → `build.py trial` → `trial_this_pc.py` (1.6) → timing (8 f) → hand the zip and
README to the owner.

**Phase 2 (after the owner's SELFTEST.txt and matching code):**

| Task | Owns | Delivers |
|---|---|---|
| **G Installer** | `packaging\installer\hdc.iss` (A) or `packaging\setup_b\*` (B), `packaging\tests\install_cycle.py` | Section 7, and test 8 e on the archived trial folder. |

---------------------------------------------------------------------------------------------------------

## 10. What in the runner makes packaging hard (file : function)

1. **This-PC paths as module constants**, evaluated at import: `botimport` `WORKTREE` / `PRIVATE` /
   `DEFAULT_CACHE_ROOT`; `safety` `BOT` / `HANGEUL` / `FORBIDDEN` / `NO_INSTALL` / `TMP_DIR`; `engine` `ONNX_LAB` /
   `MODELS` / `GLUE_DATA`. The mode must be known before the first runner import. Fix: `places`, decided at import
   (frozen or `HDC_MODE`).
2. **This-PC strings in messages and docstrings.** Message literals in `safety._write_refusal` ("private-runner"),
   `safety._check_bot` and `check_out_dir` (the bot path), `pipeline._write_log` and `report._facts` go through the
   seam's labels. Docstrings in `__init__`, `__main__`, `engine`, `ingest`, `birth` and `safety` go through the
   scrub (6.1).
3. **`src\verify\prototype_page_types.py`** holds `DOCS_ROOT` / `TEXT_DIR` literals pointing at the student folders
   and the bot. Fix: the staged constant swap only, since the worktree file is not ours to change.
4. **`--out` and crop paths assume private-runner.** `__main__.check_args` requires `--out` under PRIVATE;
   `birth._safe_dir` silently returns None outside PRIVATE, so APP runs would write no crops;
   `doccheck.crop_unit_photo` and `check_documents.work_for` fall back to `PRIVATE\tmp`.
5. **Dynamic stage imports.** `pipeline._Modules` (and `report` and `verdict` through `importlib`) load stage modules
   by name, which PyInstaller cannot see. Fix: `collect_submodules` in the spec and 8 g's import-all child.
6. **No per-page progress or cancel point.** The pipeline only prints stage times to stderr (`pipeline._Clock`), and
   pages are read inside the bot's `dv.read_pages`. Fix: `hooks.emit` in `engine.PageLog.__call__` and
   `Engine.read_file`, plus `Cancelled(BaseException)`, because the bot's `_ocr_image` rotation loop swallows
   `Exception`.
7. **Wall-clock time budgets make verdicts depend on PC speed:** `birth.decode_qr_image` (`QR_BUDGET_S` 10 s),
   `birth.code128_votes` (3 s), `tin._stage_b` (8 s), `tin._stage_e` (12 s). On a slower office PC the QR ladder can
   stop earlier, so "QR not decoded" turns PASS into FLAG and the result code differs. The seam only RECORDS budget
   hits (`budget` events, `checkfacts.json`). The real fix is step-count budgets calibrated to today's cost on this
   PC: a runner logic change for the owners of Tasks 4 and 7, recommended before the installer.
8. **Clock reads that ignore `--today`:** `birth.MAX_BIRTH_YEAR = date.today().year + 1` (at import) and
   `ocr_validator.parse_mrz_date`'s century pivot (`datetime.now()`). They only matter across a new-year boundary,
   but a fixed check date does not fully freeze them.
9. **The bot import pulls in non-checking code.** `field_check` imports `src.sheets.progress_builder` at module level;
   that module lazily imports `src.scraper.client`, the Google API and `src.sheets.passport_issue`, and carries the
   Drive folder id and `token.json` paths as constants. Fix: a minimal stage plus the excludes; parity (8 d) catches
   any lazy import a check really needs.
10. **Missing VC++ runtimes on fresh PCs.** `pyzbar`'s `libzbar-64.dll` needs `msvcr120.dll` (VC++ 2013), and
    `onnxruntime.dll` needs `msvcp140.dll` + `msvcp140_1.dll`; neither wheel ships them. Fix: the bundled copies, the
    preload and 8 h.
11. **Native writes bypass the audit hook:** `engine.cut_bundle` / `cut_page` (MuPDF `save`) and `doccheck.crop_photo`
    (PIL `save`). Safety depends on the `assert_writable` calls before them; any new native write needs one.
12. **Memory.** `engine.page_image_of` keeps up to 12 renders at 300 dpi (about 300 MB) on top of CRAFT's peak. Fine
    at 16 GB; on 8 GB PCs it must be measured (8 f).
13. **Privacy at rest.** The bot's text cache (`text_cache.install`, under `cache_root`) keeps full page texts per
    case: in APP that is `DATA\cache`, hence the 30-day pruning and the uninstall question (Q3). If OneDrive syncs
    Documents, the case folders (with `docs\` copies) go to the cloud, outside the app's network guard (Q2).

---------------------------------------------------------------------------------------------------------

## 11. Open questions for the owner (recommended answer first)

| # | Question | Recommended |
|---|---|---|
| Q1 | Installer: **A** Inno Setup 6 (allow us to download it, about 6 MB) or **B** our own Setup.exe? | **A**: half a day, fewer antivirus alarms, a standard uninstall in Apps. |
| Q2 | Default report folder when the PC's Documents folder is synced by OneDrive (student documents would be uploaded). | Detect it and use `C:\Users\<name>\Hangeul Document Checks` (not synced) instead; otherwise Documents. |
| Q3 | Saved page texts (they make a re-check instant, but hold student text): how long to keep them? | Delete after 30 days, and on uninstall (the question defaults to Yes). |
