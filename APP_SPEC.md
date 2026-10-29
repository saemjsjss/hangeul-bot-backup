# APP_SPEC — the installable Windows app of the document checker

This file holds two revisions. **Revision 2 (29 Oct 2026, "the GPU build")** comes first and overrides revision 1
wherever they differ. **Revision 1 (27 Oct 2026)** follows unchanged: everything in it that revision 2 does not
replace still holds (the window, the protocol, the places seam, the result code, the messages, the data folders, the
scrub, the tests). Revision 2 cites revision 1 as "rev 1 §n". No personal data: every name, number, date and code is a
placeholder; cases are named by case id only.

# REVISION 2 — the GPU build (29 Oct 2026)

Status: design. Builds on RUNNER_SPEC revision 2.7 (runner 0.2.7, rules bot 2026-10-21.3 + runner 2026-10-28.r2.6,
closer-4), branch prototype/page-types at 344b342. Nothing of revision 1 was built (there is no `places.py` and no
`app\` yet), so its tasks are folded into R2.7.

The owner (29 Oct): "just give me the exe file after installing which will use only gpu and give best verdict".
The owner's answers the same night:
- It runs on PCs with an NVIDIA card, INCLUDING the owner's own PC (RTX 5060, 8 GB), which also runs the live Telegram
  bot. There the app WAITS POLITELY while the bot's document check uses the card (it starts every 15 minutes; its
  lock file is `C:\Hangeul\BOT\data\verification\auto_verify.lock`), so the bot never runs out of card memory.
- The GPU engine is the one already on this PC, copied into the app with no download:
  `C:\Hangeul\DOCCHECK\lab\onnx\.venv` (Python 3.12.10, torch 2.11.0+cu128, torchvision 0.26.0+cu128, easyocr 1.7.2,
  pymupdf, numpy, opencv-python-headless, pillow, scipy, scikit-image) and the weights in
  `%USERPROFILE%\.EasyOCR\model`. The installer may be about 3 GB.
- Inno Setup 6.7.4 is installed (signature checked) at `C:\Hangeul\DOCCHECK\tools\InnoSetup6\ISCC.exe`. No other
  download is approved.
- "Best verdict" = the checker as it is now (runner rev 2.7). Every tested verdict came from: the first read of each
  page by the bot's EasyOCR on the card (`gpuread.py`), then the runner with its light reader (ONNX, CPU) for
  everything after. The torch-on-card closer look gave the same decisions as the ONNX CPU one on 550 looks
  (`private-runner\gpu_sessions\parity`).

**Facts read for this design (29 Oct; read-only, no student text):**
- The bot venv and `lab\onnx\.venv` hold the same torch 2.11.0+cu128, torchvision 0.26.0+cu128, easyocr 1.7.2, numpy
  2.5.2, opencv-python-headless 5.0.0.93, pymupdf 1.28.2 and pillow 12.3.0. They differ only in imageio (2.37.4 /
  2.38.1), python-bidi, shapely and tifffile; none of these takes part in an English read except imageio, which
  decodes JPEG/PNG files through the same Pillow.
- torch's compiled CUDA targets: `sm_75 sm_80 sm_86 sm_90 sm_100 sm_120` (no PTX); CUDA 12.8; cuDNN 9.19.0. Its
  defaults: `cudnn.allow_tf32` True, `cuda.matmul.allow_tf32` False, `cudnn.deterministic` False, `cudnn.benchmark`
  False.
- `torch\lib` holds 4.04 GB of DLLs. Hard imports (pefile): `torch_cuda.dll` → cublas64_12, cublasLt64_12, cudnn64_9,
  cufft64_11, cusolver64_11, cusparse64_12 (→ nvJitLink_120_0), c10_cuda, torch_cpu; `torch_cpu.dll` → cudart64_12,
  cupti64_2025.1.1, libiomp5md, uv, c10, msvcp140, vcruntime140, vcruntime140_1; `torch_python.dll` → shm. cuDNN loads
  its sub-libraries (graph, ops, cnn, adv, heuristic, engines_*) at run time. The weights: `craft_mlt_25k.pth`
  83,152,330 B, `english_g2.pth` 15,143,997 B.
- The r8 page texts (`runs\owner_f3_r8`, `owner_f9_r8`, `owner_eap1_r8`, `test1_r8`) are complete; none was read
  smaller and none was skipped: 101 pages (93 OCR, 4 text layer, 4 picture files).
- easyocr 1.7.2 imports `skimage.io` (file paths), `scipy.ndimage`, `bidi`, `yaml`, `torchvision.models`; only
  `easyocr.DBNet`, `easyocr.detection_db` and `easyocr.export` import onnx, pyclipper, shapely or `cpp_extension`.
- Inno Setup 6.7.4: one Setup.exe may hold up to 4,200,000,000 bytes of compressed data; above that DiskSpanning is
  required. `lzma2/ultra64` needs about 742 MB to compress per block thread and 64 MB to decompress.
- **Caution:** `torch.backends.cudnn.version()` starts CUDA on the card (seen once while reading these facts: a short
  CUDA start, no computation). Build and test scripts never call it; the cuDNN version is read from the version
  resource of `cudnn64_9.dll`.

## R2.0 Decisions

| # | Decision | Why |
|---|---|---|
| G1 | The deliverable is ONE `Setup.exe` (Inno Setup 6.7.4, per user, LZMA2). Rev 1's trial zip is dropped. | The owner asked for "the exe file after installing". A 4.6 GB folder zips badly for Explorer (zip64). The MANIFEST chain (dist → installer → installed files) still proves "what was tested is what gets installed". |
| G2 | One worker process per check (rev 1 §2) runs three phases in order: (0) the card start; (1) the FIRST READ of every page = `gpuread.read_all` in-process with the bot's EasyOCR reader on the card, under the process conditions gpuread had; (2) the runner (`pipeline.run`) with the light reader's two networks as torch modules on the card (`TorchSession`, strict fp32). | This is the tested path, with no bot venv and no second card process (R2.1). |
| G3 | Not bundled: onnxruntime (and `DirectML.dll`) and the two ONNX models. Bundled: torch with its CUDA DLLs, torchvision, easyocr, and the two EasyOCR `.pth` weights. | GPU only; ONNX Runtime would only serve a CPU engine. |
| G4 | No CPU OCR engine. With no usable card the app shows a plain message and checks nothing: Check is disabled, and the worker refuses with exit 6. One narrow exception, counted and reported: ONE model call that does not fit the 3 GB cap, even after the card cache is emptied and the bot's turn waited for, runs with the SAME weights on the processor (torch fp32). | Without it, one large page (a phone photo, a page larger than A4) could fail a whole check on the card's cap. DEV read such calls on the CPU. Q4 lets the owner turn this into a stop. |
| G5 | The card-memory cap is 3.0 GB on EVERY card, bigger cards too. A check starts only with ≥ 4.0 GB free (NVML). Cards reporting under 5800 MB in total (smaller than "6 GB") are refused. | The cap decides which pages the bot's reader reads again smaller after running out of memory (gpuread's 2000 / 1600 px ladder). With a bigger cap, a bigger card could read a page at full size that this PC reads smaller, and a verdict could then differ from the tested one. These are gpu_guard's and gpuread's numbers. |
| G6 | The card gate (`app\cardgate.py`) waits while a lock file exists. The path is a setting: by default the bot's lock, when its folder exists; a lock older than 4 h counts as stale, as in gpu_guard. It checks before every model call (at most every 0.25 s), pauses with the card cache emptied, and resumes by itself. Waiting never fails a check, and Cancel works during a wait. | The owner: wait politely. Checking between calls limits the overlap with a starting bot check to one model call (at most about 1.5 s). |
| G7 | The requirements are read with NVML through ctypes (`%SystemRoot%\System32\nvml.dll`), without torch: in the window at start and before every Check, and again in the worker. torch confirms them after its import. | Plain messages at once, before any heavy import. |
| G8 | Phase 1 runs with torch's defaults exactly as gpuread ran: cuDNN TF32 allowed, not deterministic, benchmark off; no `PYTORCH_CUDA_ALLOC_CONF`, no `CUBLAS_WORKSPACE_CONFIG`. Phase 2 then sets strict fp32 (`cudnn.deterministic` True, `benchmark` False, both TF32 switches off), as gpu_worker did. | Phase 1's texts must equal the r8 page texts, which gpuread made under torch's defaults. Phase 2's numbers must match the ONNX CPU ones (measured within 1e-6). |
| G9 | `gpuread.py` is bundled with no source change. The stage swaps its seven DEV path literals (R2.4.4), checked by AST. | The first read then has one source, in DEV and in the app. |
| G10 | The build environment lives only under `C:\Hangeul\DOCCHECK\lab\app-build`: a `--without-pip` venv of the system Python 3.12.10 with one `.pth` that points read-only at the site-packages of `lab\onnx\.venv` and `.venv-pkg-numpy`. Outputs go to `app-build\builds\<build-id>`. Nothing is installed anywhere. | Hard rule. |
| G11 | App version 1.0.0, flavour "GPU (CUDA 12.8)". Build id as in rev 1: `YYYYMMDD.N-<git short>[-dirty]`. | The first release. |
| G12 | "Best verdict" is the pass condition of test P1 (R2.6). On this PC, on the four r8 cases, the app's page texts must equal the r8 page texts and its verdict lines must equal the r8 lines. | The owner's definition. |

**What revision 2 changes in revision 1** (everything not listed still holds):

| Rev 1 | Rev 2 |
|---|---|
| D1, §1.1, §1.5, §1.6 (trial zip first, its README, its test on this PC) | Replaced by the installer (R2.5), README_INSTALL.txt and tests P1-P3 (R2.6). |
| D5 (CPU only, ONNX Runtime), D8 (A or B), §7.2 (installer B) | G2-G5. Installer A is chosen and Inno Setup is installed; B is dropped. |
| §1.2 self-test part `ocr` | Part `card` (R2.3.4). Part `runtime` also checks where the CUDA DLLs come from. 9 parts as before. |
| §1.4 fresh-PC failures | Rows added (R2.3.5). The "Low RAM" target becomes ≤ 4.5 GB (R2.6 P5). ARM64 Windows is now refused (no CUDA). |
| §2 process model, protocol, job.json, exit codes | Unchanged, plus the `card` and `gpu_wait` events, the `gpu` block in job.json and exit code 6 (R2.3.3). `--cli` now runs the worker's job in-process (R2.3.3). |
| §3 window, §3.3 messages | Unchanged, plus the card messages, the waiting banner, the card line and the lock setting (R2.3). |
| §4 places seam | Unchanged, plus R2.1.6 (`READER_FACTORY`, APP `models_dir`). |
| §5 bundle, §5 "Excluded" | Replaced by R2.4.1-R2.4.2. torch, torchvision, easyocr, scipy, skimage and `gpuread` are now IN. The "hardening" excludes (http, `urllib.request`, ssl ...) are dropped: easyocr and torch import `urllib.request`; the network guard still refuses every connection. |
| §6.1 stage, §6.2 spec, §6.4 commands | Amended by R2.4.3-R2.4.6. The build root moves to `app-build\builds\<id>`. |
| §8 b, c, g, h | Amended (R2.6). 8 d → P1, 8 e → P3, 8 f → P5; P4 is new. |
| §9 tasks | Replaced by R2.7. |
| §10 items 7-8 (wall-clock budgets, clock reads) | Still open, as cross-PC risks (R2.8). |
| §11 Q1 | Answered (A, installed). Q2 and Q3 keep their recommended defaults. |

---------------------------------------------------------------------------------------------------------

## R2.1 The engine in the app

### R2.1.1 One worker, three phases

`app\worker.py:run_job(job, listener)` runs after rev 1's boot (§6.2) and input pre-checks (§3.3):

```
safety.prepare(out, cache_root, input)            APP guard armed BEFORE any card work; its input snapshot is kept
info = gpuinfo.probe(); gpuinfo.problems(info, version.ARCH_FLAGS)  -> any problem: error event, exit 6 (R2.3)
gate = cardgate.CardGate(job.gpu.lock_paths, hooks.emit, free_mb=lambda: gpuinfo.free_mb(info.index))
stage "card":      gate.wait_turn("start"); gate.wait_memory(NEED_FREE_MB); card = gpuengine.start_card(info, gate)
src = ingest.open_input(input); bot = botimport.load(cache_root)
stage "firstread": fr = gpuengine.first_read(card, bot, src, gate, store=<cache_root>\pagetexts\<case_id>.json)
reader = gpuengine.light_reader(card, gate, places.current().glue_data, places.current().models_dir)  (strict fp32)
engine.READER_FACTORY = lambda threads=0: reader
r = pipeline.run(input, program, out, today=..., cache_root=..., page_texts=store,
                 reading_note=gpuengine.reading_note(fr))     # the Recorder asks gate.wait_turn("stage") at every
                                                              # runner stage start while it runs (R2.1.5)
the input snapshot compared again (INPUT_CHANGED); resultcode, summary.pdf, checkfacts (+ "gpu" block) -> done
```

The worker emits the stage events `card` and `firstread` itself (`hooks.emit("stage", ...)`). `pipeline.run` arms the
same guard a second time with the same arguments (R2.1.6). When the process ends, the card is freed completely; rev 1
§2 already starts one fresh worker per check.

### R2.1.2 Phase 1: the first read (the same code, under the same conditions as gpuread)

| | gpuread (made the r8 page texts) | the app, phase 1 |
|---|---|---|
| Code | `gpuread.read_all` → the bot's `dv.read_pages` / `dv.read_document` / `dv._ocr_image` (worktree, 07b1f22) | The same functions, bundled (G9) |
| Reader | `dv._ocr_reader()` = `easyocr.Reader(["en"], gpu=True, verbose=False)` | `easyocr.Reader(["en"], gpu=True, verbose=False, model_storage_directory=<bundle>\engine\easyocr\model, user_network_directory=<bundle>\engine\easyocr\user_network, download_enabled=False)`, set as `bot.dv._reader` inside a `GatedReader`. Same weights (easyocr checks their MD5), same networks, same defaults; only the folders differ and downloading is off. |
| torch | 2.11.0+cu128 (bot venv) | 2.11.0+cu128 (copied from `lab\onnx\.venv`) |
| torch flags | torch's defaults | torch's defaults: asserted by `start_card`, never set |
| Environment | the user's (no `PYTORCH_*` or `CUBLAS_*`) | `gpuinfo.card_env`: removes `PYTORCH_*`, `TORCH_*`, `CUBLAS_*`, `CUDNN_*`, `CUDA_*` (`CUDA_PATH*` too: the CUDA DLLs come from the app folder) and `NVIDIA_TF32_OVERRIDE`; sets `CUDA_DEVICE_ORDER=PCI_BUS_ID` and `CUDA_VISIBLE_DEVICES=<NVML index>`; sets nothing else |
| Cap | `torch.cuda.set_per_process_memory_fraction(3 GiB / total × 0.999, 0)` before the reader is made | The same |
| Out of card memory | `read_with_retries`: full size → 2000 px → 1600 px → the page is left to the runner | The same, plus GatedReader's one wait-and-retry at full size (below) |
| Between pages | `slot.checkpoint()` | `gate.wait_turn("page")`, plus a check before every model call |
| Output | `runner-page-texts/1` under private-runner | The same schema at `<DATA>\cache\pagetexts\<case_id>.json` (engine label `APP_ENGINE_LABEL`), rewritten atomically after every page |

`gpuengine.first_read(card, bot, src, gate, store, emit=hooks.emit) -> dict`:
- `done = gpuread.load_done(store, src)`: pages read by an earlier check of the same input (same file names, sha256
  values and page counts) are reused, through `usable_done(done, build_id)` (G1 review, I13): nothing is reused from
  a file whose engine label is not `APP_ENGINE_LABEL` or whose `"app_build"` is not this build's `BUILD_ID` (a later
  build reads every page again), and a page marked `"downscaled"` is never reused: it is read again at full size, and
  when it is cut down again its new record carries the mark itself (`gpuread._previous` rebuilds reused pages without
  that key). `first_read` reports `reread_downscaled` and `stale_store`. When `gpuread.pending_pages(src, done) == 0`,
  no reader is loaded: `read_all(src, None, never, done, save)` only rebuilds the file.
- `checkpoint = lambda: gate.wait_turn("page")`. `save(d)` sets `d["engine"] = APP_ENGINE_LABEL` and
  `d["app_build"] = <BUILD_ID>`, writes
  `<store>.tmp` and then `os.replace`, and emits
  `hooks.emit("page", done=<pages read>, of=<pages to read>, seconds=<last page>, where="card")`. `free = card.free`
  (gc + `torch.cuda.empty_cache()`, as gpuread's `run_session`).
- In a `finally`, always: `bot.dv._reader = None`, the reader deleted, `card.free()`. `Engine()` later sets
  `dv._reader` to the light reader. `gpuread._read_pdf` puts back the original `dv._ocr_image` itself.
- Returns `{"pages": gpuread.counts(result), "seconds_total", "model_load_s", "downscaled", "skipped", "oom_retries",
  "peak_alloc_gb", "peak_reserved_gb"}`: counts only, no text.
- A page left out (`skipped`) is read in phase 2 by the runner's `Engine._read_missing` with the light reader, just as
  DEV read it with its CPU light reader. A `hooks.Cancelled` leaves the partial file saved (`read_all` saves, then
  re-raises), and the next check resumes from it.

**`GatedReader(inner, gate, card, ascii_dir)`**, phase 1 only. `readtext(image, *a, **kw)`:
1. `gate.wait_turn("read")`;
2. `image = ascii_path(image, ascii_dir)`;
3. `inner.readtext(image, *a, **kw)`;
4. on a card OOM (`gpuread.is_oom`), ONCE: `card.pause()`, `gate.wait_turn("oom")`, `gate.wait_room("oom")` (R2.2),
   the same call again at full size. A second OOM is raised, and gpuread's ladder or the bot's own handling acts as
   in DEV.

Every other attribute comes from `inner` (`__getattr__`). Why the single retry: an OOM caused by another program (the
bot starting) must not make a page be read smaller, while an OOM that the page itself causes repeats and reaches the
ladder exactly as in DEV. The retry also covers the bot's `_ocr_image`, whose rotation tries swallow exceptions
(WORKPLAN step 2 a).

**`ascii_path(image, ascii_dir)`.** EasyOCR (`utils.reformat_input`) and `glue_numpy.reformat_input` read a file path
with `cv2.imread`, which fails on Windows for a path holding any non-ASCII letter. Such a path is copied byte for byte
to `<DATA>\tmp\<job>\ascii\img-<nnnn><ext>` and passed RELATIVE to the working folder. Boot sets the working folder to
DATA, so the relative path is ASCII even when DATA is not; `os.path.relpath` is asserted ASCII. Same bytes, same
decode. The copies are removed at job end and by rev 1's start-up housekeeping. Arrays and ASCII paths pass unchanged.

### R2.1.3 Phase 2: the light reader on the card

- `gpuengine.strict_fp32(torch) -> dict`: gpu_worker's `strict_fp32`, the four flags set and read back, with no
  environment change (G8).
- `gpuengine.load_models(torch, easyocr, model_dir) -> (det_cpu, rec_cpu, facts)`: `gpu_worker.export_modules` and
  `load_models` VERBATIM. That is export_onnx.py's `MeanPool`, `DetectorOut` and `RecognizerOut`;
  `easyocr.Reader(["en"], gpu=False, quantize=False, verbose=False, model_storage_directory=model_dir,
  download_enabled=False)`; the recogniser's `AdaptiveAvgPool` swapped for `MeanPool`; every tensor float32.
  `test_gpuengine` compares these definitions node for node with `lab\onnx\scripts\export_onnx.py` (read-only;
  skipped when that file is absent), as gpu_sessions' test does.
- `TorchSession(kind, cpu_module, card, gate)` with `kind` "det" or "rec":
  `self.cuda_module = copy.deepcopy(cpu_module).to("cuda")`, and the CPU module is kept for the processor exception
  (G4, about 100 MB of RAM). `run(output_names, feeds) -> [np.ndarray]`:
  1. `x = feeds["image"]` must be float32, rank 4, 3 channels (det) or 1 channel at height 64 (rec), with no empty
     axis; otherwise `ValueError` (what ONNX Runtime would refuse). A read-only or non-contiguous array is copied
     first.
  2. `gate.wait_turn(kind)`.
  3. On the card: `with torch.inference_mode(): y = cuda_module(torch.from_numpy(x).to("cuda"))`, then
     `out = y.contiguous().cpu().numpy()` (gpu_worker's `Nets`). Then `card.release()`: when torch reserves more
     than `CACHE_KEEP_MB` = 256 MB, `torch.cuda.empty_cache()` (no gc, no synchronize; counted as `releases`).
     So between two calls this process holds no more card memory than during a wait (R2.1.5). Freeing changes no
     number: the next call allocates again.
  4. On an OOM: `card.pause()`, `gate.wait_turn("oom")`, `gate.wait_room("oom")`, once more on the card, the
     detector this time through `lean_detector_scores` (I20: CRAFT.forward's operations in the same order with each
     skip feature dropped after its last use; counted as `calls_lean`). A second OOM runs THIS call on `cpu_module`
     (torch CPU, fp32, the detector lean there too) and counts it (`calls_cpu`, reason "oom").
  5. Any other CUDA error is raised; the worker maps it to CARD_FAILED.
  6. The output must be float32 with the input's batch size: det rank 4 with 2 score maps, rec rank 3 with 97
     classes. Otherwise `RuntimeError`.
  `stats()` gives calls and seconds on the card and on the processor, and the OOM retries.
- `light_reader(card, gate, glue_data, model_dir)` calls `strict_fp32`, then `load_models`, then returns
  `CardLightReader(TorchSession("det", ...), TorchSession("rec", ...), glue_data)`. `CardLightReader` is a subclass of
  `glue_numpy.LightReader` whose `readtext` applies `ascii_path` first.
- The engine wraps the reader in `CountingReader`, and `closer.page_image` binds it (`closer.bind`). So the closer
  look's detector (`reader.detect`) and recogniser (`reader.rec.run`) run on the card through the same objects, and so
  do the bot's `dv._ocr_image` / `dv.read_document` in phase 2 (crop re-reads, turned pages, skipped pages), exactly
  where DEV's runner used its CPU light reader.
- Large calls: rev 2.7's page search sends at most 2048 px to the detector. A whole 200-dpi page read again (for
  example `read_turned`) reaches about 2340 px (canvas 2560). gpu_sessions fitted a 2560 × 1824 detection under the
  cap only with `max_split_size_mb:256`, which the app does not set (G8). So the largest detections may run out of
  memory and go to the processor: slower, and fp32 like ONNX on the CPU. Test P1 counts them.
- `reading_note(fr) -> str` (≤ 200 characters, no personal data): `"app GPU build: first read on the graphics card
  (bot EasyOCR, 3.0 GB cap); light reader on the card"`, plus `"; <k> page(s) left to the light reader (too large
  for the cap)"` when the first read skipped pages and `"; <d> page(s) read smaller (card memory)"` when it read
  pages smaller (a shorter wording when both do not fit). The runner's READING line keeps its wording ("CPU light
  engine <n>" counts the pages the first read left to the light reader).

### R2.1.4 No usable card

- **Window:** probe at start and before every Check. A hard problem (R2.3.1) shows its message in the banner; Check
  stays disabled; Self-test stays available. Too little FREE memory is only a yellow note, because a check waits for
  memory (R2.2).
- **Worker** (`--worker`, `--cli`): an error event with the code and exit 6. Nothing is read, and nothing is written
  except the worker log.
- **Self-test:** part `card` FAILs with the code and message; the other parts still run.
- This build has no CPU check. Rev 1's CPU design would be a separate flavour, and it is not made now.

### R2.1.5 What happens to card memory, in one place

| Situation | Behaviour |
|---|---|
| The bot's lock is present before the check | Wait before CUDA starts, so no context is made while the bot works (`wait_turn("start")`). |
| Between two model calls (phase 2) | After every light-reader call on the card, `card.release()` empties torch's cache when it reserves more than 256 MB. Through the runner's processor stretches (the `bot` stage, 17-76 s on r8; the QR and TIN ladders; cut, bank, report) the process holds only its CUDA context, the two networks' weights (about 100 MB) and at most 256 MB of cache, as during a wait. (G1 review finding 1.) |
| The lock appears during a check | The current model call finishes. The lock is seen at the next model call (checked at most every 0.25 s) or at the next runner stage start (the worker's Recorder asks `gate.wait_turn("stage")` after each stage-start event while `pipeline.run` runs), whichever comes first; then `card.pause()` (synchronize, gc, `empty_cache`) and wait. During the wait the process holds its CUDA context and the weights (target: torch reserved ≤ 256 MB, test P4). Phase 1 keeps gpuread's allocator behaviour (no release between its back-to-back reads). |
| The lock is gone | `gate.wait_room(where)`: wait until NVML free memory ≥ what this process may still take up to its cap plus 512 MB (`card.room_mb()`), because the bot's process keeps its card memory while it sends its messages and writes its reports. At most 15 min; then the check goes on (never fails; counted as `room_short`). |
| Free memory below 4.0 GB at the start | Wait up to 15 min with the "memory" banner; then LOW_CARD_MEMORY (exit 6). |
| An OOM in phase 1 | GatedReader: pause, wait for the bot if its lock is there, wait for room, one retry at full size; then gpuread's ladder (2000 / 1600 px); then the page is left to phase 2. Pages read smaller or left are counted, shown as result notes and in the READING line, and read again at full size by the next check of the same input. |
| An OOM in phase 2 | TorchSession: pause, wait, wait for room, one retry on the card (the detector through the lean pass, I20: a 2560-pixel detection then fits the cap); then that one call runs on the processor (G4, Q4 (a)), counted and shown as a result note. |
| Any other CUDA error | CARD_FAILED (exit 6). "no kernel image is available" maps to CARD_NOT_SUPPORTED, and "driver version is insufficient" to DRIVER_TOO_OLD (`gpuengine.card_error_code(e)`, which walks `__cause__` and `__context__`). |

### R2.1.6 Runner changes (Task A; DEV behaviour unchanged)

All of rev 1 §4.3, plus:

| File | Name | Change |
|---|---|---|
| `engine.py` | `READER_FACTORY = None`, `make_reader(threads)` | First line: `if READER_FACTORY is not None: return READER_FACTORY(threads)`. Then `Engine()` and `closer._reader()` get the app's card reader, and onnxruntime is never imported. |
| `places.py` | APP `models_dir`, `glue_data` | `<bundle>\engine\easyocr\model` (the `.pth` weights) and `<bundle>\engine\glue_data.json`. DEV is unchanged (`...\lab\onnx\models`, `...\lab\onnx\pkg\glue_data.json`). |
| `safety.py` | `prepare` (APP) | May be called twice in one process with the same arguments; the second call arms the same policy (tested). |
| `engine.py` | `load_page_texts` | Accepts a file under places' `write_root` (DEV: PRIVATE as today; APP: DATA), through rev 1's PRIVATE rebinding (tested in APP). |
| `tests\test_reader_factory.py` (new) | | With a fake factory: `Engine()` uses it, `closer._reader()` uses it, and `onnxruntime` is not in `sys.modules`. Without one, DEV is unchanged. |

**Not changed:** `gpuread.py` (bundled as it is, R2.4.4), `closer.py`, `glue_numpy.py`, the 7 bot modules (byte-identical
to 07b1f22), `run_case.py`.

### R2.1.7 The RUNNER_SPEC safety rules S1-S11 in the app

| Rule | In the app (APP mode; DEV is unchanged) |
|---|---|
| S1, S3 | Writes go only under DATA and the chosen report folder (rev 1 §4.2). The input file or folder is forbidden even inside those roots. |
| S2 | Nothing under `C:\Hangeul\BOT` is opened. The ONLY touch is `os.stat` of the lock-path setting, which is never opened, listed or written. |
| S4 | No network (the guard). `download_enabled=False` on every EasyOCR reader. |
| S5 | One process uses the card, through the app's card gate (cap 3 GB, waits for the lock, checks between calls). Never DirectML. The worker's guard refuses every process start. The window starts only its own workers and self-test children. |
| S6 | The bot modules are staged from the worktree; the build checks them against 07b1f22. |
| S7, S8 | Unchanged (the runner's own code). |
| S9 | No personal data in code, tests, logs, checkfacts or SELFTEST.txt. Page texts live only in DATA (cache, pruned after 30 days; deleted on uninstall if the user says yes). |
| S10 | Builders do not commit. |
| S11 | There is no dev lock in the app. In DEV, the harness `cardrun.py` holds gpu_guard's dev lock during frozen-app runs (R2.6, Q-O1). |

---------------------------------------------------------------------------------------------------------

## R2.2 The card gate (`src\verify\runner\app\cardgate.py`)

Standard library only. It imports neither torch nor `hooks` (`emit` is passed in).

```python
BOT_LOCK_DEFAULT = r"C:\Hangeul\BOT\data\verification\auto_verify.lock"   # the one C:\Hangeul string in the bundle
LOCK_STALE_S = 4 * 3600      # = gpu_guard.BOT_LOCK_STALE_S: an older lock is a leftover of a crashed check
CHECK_EVERY_S = 0.25         # while the card works, the lock files are stat'ed at most this often
POLL_S = 2.0                 # while waiting
MEMORY_WAIT_S = 15 * 60      # the longest wait for free card memory

def default_lock_setting() -> Optional[str]
    # BOT_LOCK_DEFAULT when its FOLDER exists (os.path.isdir: stat only), else None. The window calls it once, at the
    # first start, and stores the answer in settings.json "gpu_lock_path"; the user may change it or turn it off.
def lock_active(path: str, now: float) -> bool
    # os.stat only (never open, list or write): the file exists and now - st_mtime < LOCK_STALE_S
def lock_kind(path: str) -> str            # "bot" when it is BOT_LOCK_DEFAULT (normcase), else "other"
class CardMemoryShort(Exception):          # .free_mb, .need_mb
class CardGate:
    def __init__(self, lock_paths: Sequence[str], emit: Callable[..., None], *,
                 free_mb: Callable[[], Optional[int]] = lambda: None,
                 clock=time.monotonic, wall=time.time, sleep=time.sleep)
    on_pause: Callable[[], int]            # set by gpuengine.start_card: card.pause (returns torch reserved MB); default 0
    on_room: Callable[[], Optional[int]]   # set by gpuengine.start_card: card.room_mb; default None (no room check)
    def wait_turn(self, where: str) -> None
    def wait_memory(self, need_mb: int, limit_s: float = MEMORY_WAIT_S, *, fatal=True, where="memory") -> int
    def wait_room(self, where: str) -> Optional[int]
    def stats(self) -> Dict[str, Any]      # waits, waited_s, by why and where, last free_mb, lock_checks,
                                           # room_checks, room_short; no paths
```

**`wait_turn(where)`**, where `where` is "start", "page", "read", "det", "rec", "oom" or "stage" (the worker, at each
runner stage start):
1. If the last check was less than `CHECK_EVERY_S` ago and found no lock, return at once.
2. Otherwise stat every lock path. With none active, return.
3. With one active: `reserved = on_pause()`, then `emit("gpu_wait", state="start", why="lock", lock=<kind>,
   where=where, reserved_mb=reserved)`.
4. Every `POLL_S`: `emit("gpu_wait", state="tick", why="lock", lock=<kind>, waited_s=...)`. A `Cancelled` raised by the
   listener passes through; the gate never catches it.
5. When no lock is active: `emit("gpu_wait", state="end", why="lock", waited_s=...)`, then `wait_room(where)` (not
   inside a memory wait), then return.

There is no time limit: the bot's check ends by itself, and a lock older than 4 h is stale.

**`wait_memory(need_mb)`**: `free_mb()` is None (unknown) or at least `need_mb` → return. Otherwise `gpu_wait`
start / tick / end with `why="memory"`, `free_mb` and `need_mb`, re-reading every 5 s. A lock that appears meanwhile
is waited for too. After `limit_s` it raises `CardMemoryShort`; with `fatal=False` it returns instead and its end event
carries `short: true`.

**`wait_room(where)`** (G1 review finding 2): `need = on_room()`; None (the card is not started) → return None.
Otherwise `wait_memory(need, MEMORY_WAIT_S, fatal=False, where=where)`. Called by the gate after every lock wait and by
GatedReader / TorchSession before their OOM retry. It never fails a check: the program that held the lock may hold its
memory a little longer, and after 15 min the OOM handling of R2.1.5 takes over (counted and reported).

**Settings and job:** settings.json `"gpu_lock_path"` (a string or null). The Options row reads "Graphics card: wait
while this file exists [path] [Change…] [Off]"; under the default it adds "(the Telegram bot's document check)".
job.json `"gpu": {"lock_paths": [...]}` lists the setting plus any test-only `--gpu-lock-extra PATH`, which is never
stored. With the default setting the REAL bot lock is always watched, also during tests.

---------------------------------------------------------------------------------------------------------

## R2.3 Requirements, messages, events, self-test

### R2.3.1 `src\verify\runner\app\gpuinfo.py` (NVML through ctypes; never imports torch)

```python
NVML_DLL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll")   # full path only
CUDA_DRIVER_MIN = 12080    # the driver must support CUDA 12.8 (torch 2.11.0+cu128)
MIN_TOTAL_MB = 5800        # a "6 GB" card reports about 6144 MiB
NEED_FREE_MB = 4096        # cap 3.0 GB + CUDA context + headroom (gpuread NEED_FREE_GB 4.0)
NO_CARD_ENV = "HDC_TEST_NO_CARD"   # unit tests: probe() returns "no NVML" without loading the DLL
@dataclass(frozen=True)
class CardInfo:
    nvml: bool; error: str          # error: a short scrubbed reason when nvml is False
    count: int; index: int          # index 0 = the first NVIDIA card in PCI bus order (NVML's order)
    name: str; driver: str          # "NVIDIA GeForce RTX 0000", "000.00"
    cuda_driver: int                # nvmlSystemGetCudaDriverVersion_v2, e.g. 12090
    cc: Tuple[int, int]             # nvmlDeviceGetCudaComputeCapability
    total_mb: int; free_mb: int     # nvmlDeviceGetMemoryInfo
def probe(loader=None) -> CardInfo            # nvmlInit_v2 ... nvmlShutdown; never raises
def free_mb(index: int = 0, loader=None) -> Optional[int]
def arch_ok(cc, arch_flags) -> bool           # "sm_<maj><min>" in arch_flags, or a "compute_<xy>" entry with xy <= cc
@dataclass(frozen=True)
class Problem: code: str; values: Dict[str, str]     # card name, driver, GB numbers only
def problems(info, arch_flags) -> List[Problem]      # in this order; the window shows the first
def card_env(environ: Mapping[str, str], index: int) -> Dict[str, str]   # R2.1.2
def selftest_line(info) -> str
```

The NVML functions used: `nvmlInit_v2`, `nvmlShutdown`, `nvmlSystemGetDriverVersion`,
`nvmlSystemGetCudaDriverVersion_v2`, `nvmlDeviceGetCount_v2`, `nvmlDeviceGetHandleByIndex_v2`, `nvmlDeviceGetName`,
`nvmlDeviceGetCudaComputeCapability`, `nvmlDeviceGetMemoryInfo`.

| Order | Code | When |
|---|---|---|
| 1 | NO_NVIDIA_CARD | `nvml.dll` is missing, `nvmlInit_v2` fails, or the device count is 0 |
| 2 | DRIVER_TOO_OLD | `cuda_driver < CUDA_DRIVER_MIN` |
| 3 | CARD_NOT_SUPPORTED | `not arch_ok(cc, BUILD.json arch_flags)`; the value "old" when cc < 7.5, "new" when cc > 12.0 |
| 4 | CARD_TOO_SMALL | `total_mb < MIN_TOTAL_MB` |
| (waited for) | LOW_CARD_MEMORY | `free_mb < NEED_FREE_MB` after `MEMORY_WAIT_S` (R2.2) |
| (during a check) | CARD_FAILED | a CUDA error that is not an OOM (R2.1.5), or `torch.cuda.is_available()` False after NVML found a card |

After importing torch, `gpuengine.start_card` confirms: `get_device_capability(0)` must appear in `get_arch_list()`,
and the device name and total must agree with NVML's index (a mismatch is logged, not fatal).

### R2.3.2 Messages (`app\messages.py`; rev 1 §3.3 style; values only, never paths)

| Code | Title | What the window says |
|---|---|---|
| NO_NVIDIA_CARD | No NVIDIA graphics card | "This checker reads documents on an NVIDIA graphics card, and none was found on this PC (or its driver is not installed). Use a PC with an NVIDIA GeForce RTX card (RTX 20 series or newer, 6 GB or more) and its driver. Nothing was checked." |
| DRIVER_TOO_OLD | Graphics driver too old | "The NVIDIA driver on this PC (version <driver>) is too old for the checker. Install NVIDIA driver 570 or newer (it supports CUDA 12.8) from NVIDIA's website or the NVIDIA App, restart the PC and check again. Nothing was checked." |
| CARD_NOT_SUPPORTED | Graphics card not supported | old: "This PC's graphics card (<name>) is too old for the checker's reading engine. It needs a GeForce RTX 20 series card or newer. Nothing was checked." new: "This PC's graphics card (<name>) is newer than this version of the checker supports. A newer version of the checker is needed. Nothing was checked." |
| CARD_TOO_SMALL | Graphics card memory too small | "This PC's graphics card (<name>) has <total> GB of memory; the checker needs a card with at least 6 GB. Nothing was checked." |
| LOW_CARD_MEMORY | Graphics card busy | "Only <free> GB of the graphics card's memory is free; the checker needs 4.0 GB. Close programs that use the graphics card (games, video or photo editors, other AI tools) and check again." |
| CARD_FAILED | Graphics card error | "The graphics card stopped working during the check (step '<stage>'). Your files were not changed. Restart the PC and check again; if it happens again, update the NVIDIA driver, run the Self-test and send SELFTEST.txt." |
| (status) waiting, lock "bot" | Waiting for the graphics card | "Waiting for the graphics card: the Telegram bot is checking documents (waited <m> min). The check goes on by itself when the bot is done. [Cancel]" |
| (status) waiting, lock "other" | Waiting for the graphics card | "Waiting for the graphics card: another program's lock file is present (waited <m> min). The check goes on by itself. [Cancel]" |
| (status) waiting, memory | Waiting for the graphics card | "Waiting for the graphics card: <free> GB free, 4.0 GB needed (waited <m> min, at most 15). Close programs that use the graphics card. [Cancel]" |

The footer gets a card line: "Graphics card: <name> (<total> GB) · driver <driver>". The progress text reads "Reading
page k of N on the graphics card — about X s left" (initially 3.0 s per page, then this PC's average, kept as
`seconds_per_page_gpu` in settings.json).

### R2.3.3 Protocol, job.json, exit codes, flags (additions to rev 1 §2)

```
{"v":1,"ev":"card","state":"found","name":"<card>","cc":"12.0","driver":"000.00","total_mb":8151,"free_mb":6120}
{"v":1,"ev":"gpu_wait","state":"start","why":"lock","lock":"bot","where":"page","reserved_mb":180}
{"v":1,"ev":"gpu_wait","state":"tick","why":"lock","lock":"bot","waited_s":42.0}
{"v":1,"ev":"gpu_wait","state":"end","why":"lock","waited_s":180.5}
{"v":1,"ev":"gpu_wait","state":"start","why":"memory","free_mb":2900,"need_mb":4096}
{"v":1,"ev":"card","state":"ready","torch":"2.11.0+cu128","cuda":"12.8","seconds":14.2}
{"v":1,"ev":"page","done":3,"of":14,"seconds":1.2,"where":"card"}                        (phase 1)
{"v":1,"ev":"stage","key":"firstread","state":"start"}                                   (also "card")
{"v":1,"ev":"done", ..., "budget_hits":0,"check_date":"2026-10-28",
                        "gpu":{"card":"<card>","peak_alloc_gb":2.41,"peak_reserved_gb":2.80,
                                "cpu_calls":0,"waits":1,"waited_s":180.5,"downscaled":0,"skipped":0}}
{"v":1,"ev":"gpu_wait","state":"end","why":"memory","free_mb":2000,"need_mb":3474,"waited_s":900.0,"short":true}
{"v":1,"ev":"error","code":"DRIVER_TOO_OLD","values":{"driver":"000.00"},"text":"<scrubbed>"}
```

- **job.json** adds `"gpu": {"lock_paths": ["<abs>", ...], "index": 0, "cap_gb": 3.0, "need_free_mb": 4096}`. The
  window ALWAYS writes `"today"`: in "today" mode it is the window's date at the moment Check was pressed (G1 review
  finding 6), so the window, the result code, the summary's RESULT CODE line and the date rules use one date. The
  done event's `check_date` is the worker's; the window shows that one. `--cli` without `--today` still lets the
  worker take its own date (one process).
- **The done event** adds `budget_hits` (the runner's wall-clock budget events), `check_date`, and in `gpu` the first
  read's `downscaled` and `skipped` page counts. The window turns them into result notes (`messages.budget_note`,
  `downscaled_note`, `skipped_note`): "<n> QR or barcode reading step(s) ran out of time ... Check again when the PC
  is idle; the result may then differ", "<n> page(s) were read at a smaller size because the graphics card ran out of
  memory ...", "<n> page(s) were too large for the graphics card's memory and were read by the light reader
  instead ...", next to rev 2's notes for waits and processor calls (G1 review finding 4).
- **Worker exit code 6**: a card problem (the six codes of R2.3.1), sent with an error event first. The other codes are
  as in rev 1.
- **The window's controller** treats `gpu_wait` as the state "waiting": the banner of R2.3.2, the progress bar paused,
  Cancel working (rev 1's cancel path; the gate's tick lets the hook raise within 2 s).
- **Flags** (`app\entry.py`):
  - `--cli <input> --program P --out DIR [--today D]` now runs `worker.run_job` IN THIS PROCESS (no window; events
    printed as JSON lines; the worker's exit codes). It is the same code path as a real check, and the tests use it.
  - `--data-dir DIR` sets `HDC_DATA_DIR` (tests).
  - `--gpu-lock-extra PATH` adds a watched lock (tests; never stored).
  - Rev 1's runner CLI is not in the app: there is no CPU engine.
- **Boot** (rev 1 §6.2) for the roles `--worker`, `--cli` and `--selftest-part card`, before any numpy or torch
  import: `os.environ.update(gpuinfo.card_env(os.environ, index))` (the window's `spawn` gives the child the same
  environment) and `OPENBLAS_NUM_THREADS=1` unless set (as `runner.__main__` does). If `PYTORCH_NO_CUDA_MEMORY_CACHING`
  or `PYTORCH_NO_HIP_MEMORY_CACHING` is still present, `start_card` refuses with CARD_FAILED, because the cap would not
  hold.

### R2.3.4 Self-test changes (rev 1 §1.2)

Nine parts as before: `integrity`, `runtime`, `core`, `pdf`, **`card`** (replaces `ocr`), `qr`, `excel`, `tk`, `guard`.

- **`card`** runs in its own child (`--selftest-part card`) with a timeout of 900 s, because it may wait for the bot.
  Steps:
  1. `gpuinfo.probe` and `problems`; a gate with the settings' lock path; `start_card`.
  2. `first_read` on the shipped `selftest_pages.pdf` (2 made-up pages; page 2 turned 90°) into a store in the
     scratch folder.
  3. `dv.read_document` of `selftest_page1.jpg` (made at build time), read from the non-ASCII scratch folder
     (`ascii_path`).
  4. `light_reader`; one `readtext` of page 1 through it; one recogniser call checked for shape `[1, T, 97]` float32.

  OK when:
  - no problem was found and the reader is on `cuda`;
  - CER ≤ 2% on page 1, page 2, the JPG and the light reader's page 1, and page 2 is reported sideways;
  - the strict flags read back as set;
  - peak reserved ≤ 3.0 GB, and no call ran on the processor.
- **`runtime`** additionally imports torch (no CUDA work) and reports, by `GetModuleFileNameW`, that
  `torch_cuda.dll`, `torch_cpu.dll`, `cudnn64_9.dll`, `cublas64_12.dll` and `cudart64_12.dll` were loaded from the app
  folder, and that `nvml.dll` came from Windows, with its file version. A CUDA DLL loaded from anywhere else (a CUDA
  toolkit on PATH) is a FAIL.
- **READ CODE** is now per card model. `build_read.json` holds the build PC's code and card name. The same card model
  should give the same code; another model may differ by a few characters (cuDNN picks other kernels, and Turing has
  no TF32).

New SELFTEST.txt lines (placeholders):

```
Graphics card: NVIDIA GeForce RTX 0000 | 8.0 GB total, 6.0 GB free | driver 000.00 (CUDA 12.9) | compute 12.0 (this build: 7.5-12.0)
Reading engine: torch 2.11.0+cu128, CUDA 12.8, cuDNN 9.19.0 | cap 3.0 GB | first read: bot EasyOCR (torch defaults) | light reader: strict fp32
Waits for: the Telegram bot's lock file (not present now) | waited 0 s during this test
Reading: start 14.2 s (torch 6.1, CUDA 2.3, readers 1.8) | page 1 (upright) 0.9 s | page 2 (sideways, 4 passes) 3.1 s | photo 0.8 s
Card memory: peak 2.41 GB allocated, 2.80 GB reserved (cap 3.0 GB) | calls on the processor: 0
  card       OK    reader on cuda:0; CER 0.4% / 0.9% / 0.6% / 0.4%; READ CODE 51C2-0A7E (build PC, same card model: 51C2-0A7E)
```

New WARNINGS: free card memory under 5 GB ("a check may wait"); a laptop card under 8 GB ("large pages may be read on
the processor").

### R2.3.5 Fresh-PC failures (rows added to rev 1 §1.4)

| Risk | Handling | Self-test shows |
|---|---|---|
| No NVIDIA card or no driver | NO_NVIDIA_CARD at start; Check disabled. The installer warns too (R2.5). | The card line and part `card`. |
| Driver too old for CUDA 12.8 | DRIVER_TOO_OLD, judged by NVML's CUDA version, not by a driver number. | Driver and CUDA version. |
| Card older than Turing (GTX 10 and older) or newer than sm_120 | CARD_NOT_SUPPORTED, from NVML before torch; torch's "no kernel image" maps to the same code. | Compute capability and the build's range. |
| A system CUDA toolkit or another cuDNN on PATH | torch loads every DLL of its own `torch\lib` by full path; `card_env` removes `CUDA_PATH*`. | Part `runtime` lists where each CUDA DLL came from. |
| ARM64 Windows | Refused by the installer (`ArchitecturesAllowed=x64os`): no CUDA there. | — |
| WDDM TDR (a model call over about 2 s on a weak laptop card) | The driver resets the card and the check ends with CARD_FAILED; the message says to retry or update the driver. | Seconds per page. |
| Antivirus scanning 4.6 GB of DLLs | The first start can take minutes; a quarantined DLL is MISSING_PART (rev 1). | Part `integrity`. |

---------------------------------------------------------------------------------------------------------

## R2.4 Bundle and build

### R2.4.1 What is copied (sizes on disk)

| Part | Source | MB |
|---|---|---|
| torch 2.11.0+cu128 DLLs: every `torch\lib\*.dll`, about 40 files (torch_cuda 774, cublasLt64_12 643, cudnn engines 459 + 27, cusparse 362, cufft 263, cudnn_adv 257, torch_cpu 254, cusolver 215, cusolverMg 150, cublas 108, cudnn_ops 101, nvrtc 83 + 83 + 6, nvJitLink 74, curand 69, cudnn_heuristic 56, nvperf_host 21, torch_python 18, cupti, libiomp5md, c10, cudart, shm, uv ...) | `lab\onnx\.venv\Lib\site-packages\torch\lib` | ~4,040 |
| torch's Python package and `_C` (hooks-contrib collects them, sources kept: `pyz+py`) | same venv | ~150 |
| torchvision 0.26.0+cu128 (easyocr imports `torchvision.models` and `transforms`) | same venv | ~29 |
| easyocr 1.7.2 (`en` characters only, via `hooksconfig`) | same venv | ~3 |
| The weights `craft_mlt_25k.pth`, `english_g2.pth` → `engine\easyocr\model\`; an empty `engine\easyocr\user_network\.keep` | `%USERPROFILE%\.EasyOCR\model` | 98.3 |
| `glue_numpy.py`, `easyocr_vendor.py`, `engine\glue_data.json` | `lab\onnx\pkg` | <1 |
| OpenCV 5.0.0.93 headless (without `opencv_videoio_ffmpeg`) | same venv | ~86 |
| scipy 1.18.1 (what `scipy.ndimage` needs), scikit-image 0.26.0 (`skimage.io` and its lazy-loader `.pyi` stubs), imageio 2.38.1, lazy_loader, tifffile, python-bidi, PyYAML | same venv | ~60-110 |
| numpy 2.5.2, PyMuPDF 1.28.2, Pillow 12.3.0 | same venv (same versions as `.venv-pkg-numpy`) | ~78 |
| sympy, mpmath, networkx, jinja2, filelock, fsspec, typing_extensions (only those PyInstaller's analysis of torch pulls in) | same venv | 0-80 |
| Python 3.12.10, Tk 8.6, the VC++ DLLs of rev 1 §5 | rev 1 | ~27 |
| Our code (staged and scrubbed), the vendored pyzbar / openpyxl / et_xmlfile, the self-test files, BUILD.json, MANIFEST.json | rev 1 | ~5 |
| **Total** | about 3,000-4,000 files | **≈ 4.5-4.7 GB** |

**No CUDA DLL is trimmed in this build.** `torch_cuda.dll` imports cufft, cusolver and cusparse (and so nvJitLink)
whether or not they are used, and cuDNN loads its sub-libraries at run time, so a missing one fails on another PC at
first use. Possible later trims, about 410 MB (curand, the nvrtc trio, cusolverMg, nvperf_host), need a recorded
DLL-load trace on at least two card generations.

### R2.4.2 What is left out

- onnxruntime with `DirectML.dll` (73 MB; both lab venvs hold `onnxruntime-directml`), onnx, onnxscript, tensorboard
  and `torch.utils.tensorboard`, triton.
- `easyocr.DBNet`, `easyocr.detection_db`, `easyocr.export`, `easyocr.cli`, `easyocr.scripts`.
- PySide6 and shiboken6 (from `.venv-pkg-numpy`), PyQt5 and PyQt6, matplotlib, pandas, IPython, pytest, pip, venv,
  ensurepip, idlelib, lib2to3, pydoc_data.
- `torch\lib\*.lib` (45 MB), `torch\include` (64 MB), `torch\share`, every `tests` / `test` folder, headers (`*.h`,
  `*.hpp`, `*.cuh`), static libraries, `opencv_videoio_ffmpeg*` (31 MB).
- DEV and test code: `src.verify.runner.devplaces`, `.tests`, `.selftest`, `src.verify.runner.app.tests`; `run_case.py`
  is outside `src`.
- The ONNX models (`craft_mlt_25k.onnx`, `english_g2.onnx`).

### R2.4.3 The build environment (G10)

```
C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe -m venv --without-pip ^
    C:\Hangeul\DOCCHECK\lab\app-build\.venv-build
C:\Hangeul\DOCCHECK\lab\app-build\.venv-build\Lib\site-packages\hdc_build.pth   (exactly these two lines, in this order)
    C:\Hangeul\DOCCHECK\lab\onnx\.venv\Lib\site-packages
    C:\Hangeul\DOCCHECK\lab\onnx\.venv-pkg-numpy\Lib\site-packages
```

- The base interpreter is the one both venvs were made from (their `pyvenv.cfg` `home`, Python 3.12.10).
- The first line wins for numpy, cv2, pymupdf and PIL (the same versions in both). The second supplies PyInstaller
  6.22.3, pyinstaller-hooks-contrib 2026.9, altgraph, pefile and pywin32-ctypes (their entry points are found through
  `importlib.metadata` on `sys.path`). `onnxruntime-directml` and PySide6 are visible but excluded by the spec, and
  their absence is checked.
- Every command runs `-I -B` with `PYTHONDONTWRITEBYTECODE=1` and
  `PYINSTALLER_CONFIG_DIR=C:\Hangeul\DOCCHECK\lab\app-build\pyi-config` (PyInstaller's binary cache would otherwise go
  to `%LOCALAPPDATA%\pyinstaller`). Nothing is written into either lab venv.
- Outputs: `C:\Hangeul\DOCCHECK\lab\app-build\builds\<build-id>\{stage, work, dist, installer, reports}`. Rev 1's
  `%LOCALAPPDATA%\HDCBuild` is not used. The scan (rev 1 §8 b) proves that no code object kept the build path. If one
  did, the build root has to move back outside `C:\Hangeul` (Q-O2).
- `packaging\make_build_env.py create | check | remove`:
  - `check` runs `.venv-build\Scripts\python.exe -I -B -c` to import PyInstaller, torch, torchvision, easyocr,
    numpy, cv2, pymupdf, PIL, `scipy.ndimage` and `skimage.io`, and prints their versions and
    `torch._C._cuda_getArchFlags()`. It never touches `torch.cuda.*` or `torch.backends.cudnn.version()`. The result
    goes to `app-build\build_env.json`.
  - `remove` deletes `.venv-build` only.
- Disk: about 15 GB per build under app-build (dist 4.6 GB, work and the PyInstaller cache up to 4.6 GB, Setup.exe
  about 3 GB), plus 4.6 GB for the install test. 408 GB was free on 29 Oct.

### R2.4.4 Stage (rev 1 §6.1, with these changes)

1. **The copy list is generated:** every `src\verify\runner\*.py` except `devplaces.py` (so `closer.py` and
   `gpuread.py` are included, and so is any module added later); every `src\verify\runner\app\*.py`; the bot files of
   rev 1 §5; the glue; the vendored packages (checked against VENDOR.json). Never `tests\`, `selftest\` or
   `app\tests\`. The list is written to `reports\STAGE_LIST.txt`.
2. **Weights:**
   - The two `.pth` files are copied to `stage\engine\easyocr\model\`.
   - Each MD5 must equal easyocr 1.7.2's own `md5sum` in `easyocr\config.py`: `detection_models["craft"]` and
     `recognition_models["gen2"]["english_g2"]`, read as text without importing.
   - Sizes and sha256 values are pinned in BUILD.json.
   - `stage\engine\easyocr\user_network\.keep` (empty) is added: EasyOCR runs `mkdir` on that folder and puts it on
     `sys.path`, so the folder must exist and lie under `_MEIPASS`.
   - No ONNX model is copied.
3. **Constant swaps** (rev 1 step 4, extended; each old literal must occur exactly once; then the AST check of rev 1
   step 5):
   - `prototype_page_types.py`: `DOCS_ROOT`, `TEXT_DIR`, as in rev 1.
   - `gpuread.py`, the seven literals that hold a DEV path:
     - `WORKTREE = Path(r"C:\Hangeul\JARVIS\prototype\page-types")` → `Path("no-worktree")`
     - `PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")` → `Path("no-dev-root")` (`GPU_DATA` follows)
     - `GUARD = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\scripts\gpu_guard.py")` → `Path("no-dev-guard.py")`
     - in `check_out`, "the page-texts file must be under C:\\Hangeul\\DOCCHECK\\lab\\private-runner" → "... must be
       under the dev root"
     - in `write_json`, "writes go only under private-runner" → "writes go only under the dev root"
     - the two argparse help texts "(under private-runner)" → "(under the dev root)".

     The app never reaches any of these. It calls only `read_all`, `load_done`, `pending_pages`, `counts` and
     `is_oom`; test 8 g imports the module.
4. **BUILD.json** adds:
   - `flavour` "gpu-cu128"; the `torch`, `torchvision` and `easyocr` versions; `cuda` "12.8"; `cudnn` (the file
     version of `cudnn64_9.dll`); `arch_flags`;
   - `cuda_driver_min` 12080, `cap_gb` 3.0, `need_free_mb` 4096, `min_total_mb` 5800;
   - `models` {file: size, md5, sha256};
   - `baseline` "r8 (runner 0.2.7, 344b342)"; `spec` "RUNNER_SPEC r2.7 / APP_SPEC r2".

   Versions are read from the build env's `*.dist-info` and from `torch._C._cuda_getArchFlags()` in a child run
   with `-I -B`.
5. **The fast scan** (rev 1 step 6) uses `packaging\scan_allow.txt` (R2.6).
6. `stage\selftest\selftest_page1.jpg` is made by `make_selftest_pages.py` (page 1 at 200 dpi, JPEG quality 85).

### R2.4.5 The spec file and hooks (`packaging\hdc.spec`, changes to rev 1 §6.2)

```python
SKIP = ("src.verify.runner.devplaces", "src.verify.runner.tests", "src.verify.runner.selftest",
        "src.verify.runner.app.tests")                     # gpuread is NOT skipped any more
hidden = collect_submodules("src.verify.runner", filter=lambda m: not m.startswith(SKIP))
hidden += ["src.verify.runner.__main__", "glue_numpy", "easyocr_vendor", "pyzbar.pyzbar", "easyocr.easyocr"]
EXCLUDES = ["onnxruntime", "onnx", "onnxscript", "tensorboard", "torch.utils.tensorboard", "triton",
            "easyocr.DBNet", "easyocr.detection_db", "easyocr.export", "easyocr.cli", "easyocr.scripts",
            "PySide6", "shiboken6", "PyQt5", "PyQt6", "matplotlib", "pandas", "IPython", "pytest",
            "pip", "venv", "ensurepip", "idlelib", "lib2to3", "pydoc_data"]
datas += [(str(STAGE / "engine/easyocr/model"), "engine/easyocr/model"),
          (str(STAGE / "engine/easyocr/user_network"), "engine/easyocr/user_network"),
          (str(STAGE / "engine/glue_data.json"), "engine")]       # no engine/models/*.onnx any more
a = Analysis(..., hooksconfig={"easyocr": {"lang_codes": ["en"]}}, excludes=EXCLUDES, ...)
DROP_WORDS = ("directml", "onnxruntime", "opencv_videoio_ffmpeg")
DROP_DATA = lambda dest: dest.endswith((".lib", ".h", ".hpp", ".cuh")) or "/include/" in dest or "/share/cmake/" in dest
# after Analysis: drop those, and every TOP-LEVEL binary whose same source file is also collected into a sub-folder
# (I17); then assert (the build stops otherwise):
#   no binary base name twice (case-insensitive; a 774 MB DLL collected twice would double the size);
#   {basename of every torch\lib\*.dll in the build env} == {the collected torch/lib DLLs};
#   no onnxruntime module or DLL, no PySide6.
```

- **Hooks:** pyinstaller-hooks-contrib 2026.9's hook-torch (dynamic libs kept under `torch\lib`, sources kept),
  hook-torchvision, hook-easyocr (with the `lang_codes` config), and the skimage, scipy, imageio and cv2 hooks. There
  is no own runtime hook: the environment is set by boot before torch is imported (R2.3.3). torch's own
  `_load_dll_libraries` finds `_internal\torch\lib`.
- UPX off and `strip=False` (rev 1). `optimize=0`. Windowed exe, version resource as rev 1 with "GPU" in
  FileDescription.

### R2.4.6 Commands (replace rev 1 §6.4; all absolute; one heavy step at a time)

```
set BPY=C:\Hangeul\DOCCHECK\lab\app-build\.venv-build\Scripts\python.exe
set RPY=C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Scripts\python.exe
set PKG=C:\Hangeul\JARVIS\prototype\page-types\packaging
set PYTHONDONTWRITEBYTECODE=1
set PYINSTALLER_CONFIG_DIR=C:\Hangeul\DOCCHECK\lab\app-build\pyi-config
C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe -I -B %PKG%\make_build_env.py create
%BPY% -I -B %PKG%\make_build_env.py check
%BPY% -I -B %PKG%\build.py stage                    -> app-build\builds\<id>\stage (prints <id>)
%BPY% -I -B %PKG%\build.py freeze --build-id <id>   -> PyInstaller at BELOW_NORMAL priority (about 15-25 min)
%BPY% -I -B %PKG%\build.py check  --build-id <id>   -> import smoke, MANIFEST.json, scan_bundle, dll_check,
                                                       frozen --selftest (through cardrun, R2.6)
%RPY% -I -B %PKG%\tests\parity.py     --build-id <id>          (P1; gives THIS_PC_CODE)
%RPY% -I -B %PKG%\tests\politeness.py --build-id <id>          (P4)
%RPY% -I -B %PKG%\tests\timing.py     --build-id <id>          (P5)
%BPY% -I -B %PKG%\build.py installer --build-id <id> --check-date <D> --this-pc-code <XXXX-XXXX>
                                                    -> ISCC at BELOW_NORMAL (about 30-60 min)
%RPY% -I -B %PKG%\tests\install_cycle.py --build-id <id>       (P3)
```

### R2.4.7 VC++ runtimes and driver DLLs

As rev 1 §5: `vcruntime140.dll`, `vcruntime140_1.dll`, `msvcp140.dll` and `msvcp140_1.dll` (≥ 14.40) at the top of
`_internal`, and `pyzbar\msvcr120.dll`, preloaded by `boot.preload_runtime()` before numpy or torch. They also cover
torch_cpu's imports; `libiomp5md.dll` ships in `torch\lib`. No VC_redist runs. `nvcuda.dll` (loaded by cudart) and
`nvml.dll` (ctypes) come from the NVIDIA driver and are never bundled: `dll_baseline.txt` lists them as
"driver-provided, loaded at run time".

---------------------------------------------------------------------------------------------------------

## R2.5 Installer (Inno Setup 6.7.4, per user)

`packaging\installer\build_installer.py` runs `C:\Hangeul\DOCCHECK\tools\InnoSetup6\ISCC.exe /Qp
/DAppFolder="<dist>\Hangeul Document Checker" /DAppVersion=1.0.0 /DFileVersion=1.0.0.<N> /DBuildId=<id>
/O"<builds>\<id>\installer" <PKG>\installer\hdc.iss` at BELOW_NORMAL priority, with a 3 h timeout. Only after
`build.py check` has passed.

`packaging\installer\hdc.iss` (the file is the source; this listing follows it). Corrected at integration (29 Oct)
after ISCC 6.7.4 compiled it: Inno reads a `;` only at the START of a line as a comment (a comment after a directive
would become part of its value), so comments stand on their own lines; and there is no `VersionInfoComments`
directive, so the build id goes into `VersionInfoProductTextVersion` (Explorer: Details > Product version). The
slice size below is 2 000 000 000 bytes, written as one number in the file.

```
[Setup]
AppId={{<one fixed GUID, chosen once by Task G and never changed: 767599DF-1DFD-490C-A03A-32EE783D7301>}
AppName=Hangeul Document Checker
AppVersion={#AppVersion}
AppVerName=Hangeul Document Checker {#AppVersion} (GPU, build {#BuildId})
AppPublisher=Hangeul
; per user, no admin right
PrivilegesRequired=lowest
; = %LOCALAPPDATA%\Programs\Hangeul Document Checker
DefaultDirName={autopf}\Hangeul Document Checker
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.17763
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
; about 4 threads and 3 GB while compressing
LZMANumBlockThreads=2
#ifdef Span
DiskSpanning=yes
DiskSliceSize=<2 000 000 000>
#else
DiskSpanning=no
#endif
; rev 1's single-instance mutex: no install or uninstall while it runs
AppMutex=HangeulDocumentChecker
WizardStyle=modern
SetupLogging=yes
UninstallDisplayName=Hangeul Document Checker (GPU)
UninstallDisplayIcon={app}\Hangeul Document Checker.exe
OutputBaseFilename=Hangeul Document Checker Setup {#AppVersion} GPU
VersionInfoVersion={#FileVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductName=Hangeul Document Checker
VersionInfoCompany=Hangeul
VersionInfoDescription=Hangeul Document Checker Setup (GPU)
VersionInfoProductTextVersion={#AppVersion} (GPU, build {#BuildId})
[InstallDelete]
; an upgrade leaves exactly the new build's files
Type: filesandordirs; Name: "{app}\_internal"
[Files]
Source: "{#AppFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"
[Icons]
Name: "{autoprograms}\Hangeul Document Checker"; Filename: "{app}\Hangeul Document Checker.exe"
Name: "{autodesktop}\Hangeul Document Checker"; Filename: "{app}\Hangeul Document Checker.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\Hangeul Document Checker.exe"; Description: "Open the checker now"; Flags: postinstall nowait skipifsilent
[Code]
InitializeSetup: when {sys}\nvml.dll is missing: "This PC has no NVIDIA graphics driver. The checker needs an NVIDIA
  graphics card (GeForce RTX 20 series or newer) to check documents. Install anyway?" (Yes / No, default No);
  a silent install only logs it and goes on.
CurUninstallStepChanged(usPostUninstall): "Also delete the app's saved data (saved page texts, recent checks,
  settings)? Your reports in Documents are kept." (default Yes, Q3; a silent uninstall takes Yes) ->
  DelTree(ExpandConstant('{localappdata}\Hangeul Document Checker'), True, True, True)
```

- **Size.** Uncompressed about 4.6 GB. Setup.exe is expected at about 2.5-3.1 GB, measured by the integrator. That is a
  single file as long as the compressed data stays under Inno's 4,200,000,000-byte limit, which is also under FAT32's
  4 GiB file limit for USB sticks.
  - If ISCC refuses on that limit, `build_installer.py` compiles again with `/DSpan=1`. The result is then
    `... Setup 1.0.0 GPU.exe` plus `...-1.bin`, `...-2.bin`, which must stay in one folder, and README_INSTALL.txt
    says so.
  - The target PC needs about 5 GB of free disk.
- **Version stamp.** One version, 1.0.0 with build `<id>`, appears in: Setup.exe's version resource and
  `AppVerName`; Settings → Apps (DisplayVersion); the app exe's version resource; BUILD.json; the window footer;
  SELFTEST.txt; summary.pdf; checkfacts.json; log.txt. All come from BUILD.json through `app\version.py`. Test P3
  checks that the installed BUILD.json `build_id` equals the Setup's `/DBuildId`.
- **Outputs** in `builds\<id>\installer\`: Setup.exe, `setup.sha256`, `installer.json` (bytes, seconds, ratio,
  spanning yes/no) and README_INSTALL.txt, filled from `packaging\installer\README_INSTALL.txt`:

```
HANGEUL DOCUMENT CHECKER {APP_VERSION} (GPU) - INSTALL (build {BUILD_ID})

NEEDS: Windows 10 (1809 or newer) or 11, 64-bit; an NVIDIA graphics card, GeForce RTX 20 series or newer, with 6 GB
or more; NVIDIA driver 570 or newer; about 5 GB of free disk. No internet and no admin rights are needed.

1. COPY "Hangeul Document Checker Setup {APP_VERSION} GPU.exe" (about {SETUP_GB} GB) to the PC, for example with a
   USB stick. If there are also files ending in "-1.bin", "-2.bin", copy them into the same folder.
2. RUN it. If Windows says "Windows protected your PC": click "More info", then "Run anyway".
   Your antivirus may take a few minutes to scan it.
3. OPEN "Hangeul Document Checker" from the Start menu. If it says the graphics card or driver is not suitable,
   do what the message says (usually: update the NVIDIA driver) and open it again.
4. SELF-TEST: click "Self-test" and wait about 2 minutes. It saves SELFTEST.txt in Documents\Hangeul Document Checks.
5. CHECK ONE CASE AND COMPARE: click "Choose PDF..." and pick CHECK_0001.pdf, program KLP. Open "Options", choose
   "Check date: fixed" and type {CHECK_DATE}. Click "Check" and look at "Result code".
   The office PC's code is {THIS_PC_CODE}. The same code means the same verdicts. Another graphics card model can
   read a few characters differently: if the code differs, send checkfacts.json too.
6. SEND BACK SELFTEST.txt and the code. Never send summary.txt, report.xlsx or run.json: they hold student data.

ON THE OFFICE PC (where the Telegram bot runs): while the bot is checking documents, the checker shows "Waiting for
the graphics card" and goes on by itself when the bot is done.

TO REMOVE: Settings > Apps > Hangeul Document Checker > Uninstall. It asks whether to delete the saved page texts and
settings too (Yes is the default). Your reports in Documents\Hangeul Document Checks stay.
```

---------------------------------------------------------------------------------------------------------

## R2.6 Tests that prove "best verdict"

Private outputs go under `C:\Hangeul\DOCCHECK\lab\private-runner\apptest\<build-id>\` as in rev 1 §8. Harnesses print
case ids, counts, rule ids, page numbers and codes; never text, names or paths.

**Card runs on THIS PC (DEV rule, needs the orchestrator's OK: Q-O1).** Every frozen-app run that uses the card goes
through `packaging\tests\cardrun.py`:
1. It loads gpu_guard by path and waits for its conditions: the bot's check not running, the dev lock free, at least
   4.0 GB free.
2. It holds `gpu_guard.gpu_slot(what, need_free_gb=4.0, wait_s=1800, max_s=<the run's timeout>)` for the whole run and
   calls `slot.checkpoint()` every 30 s from a watcher thread to refresh the dev lock. A SlotInterrupted there (the bot
   started) is recorded and does not stop the run: the app's gate pauses by itself.
3. One run at a time; it ends only its own child, and the lock is released in `finally`.

The app's own gate enforces the bot lock and the 3 GB cap inside the run. gpu_guard's 90 s slot cannot hold a whole
check (2-4 min), which is why this needs approval.

| Test | What | Pass |
|---|---|---|
| **P0 Unit** (builders) | Each task's unit tests on fakes, with `HDC_TEST_NO_CARD=1`: no card, no torch CUDA, no PyInstaller. | Green. |
| **P1 Parity with r8** (`parity.py`) | Frozen app, `--cli` with a fresh `--data-dir` per case, on the four r8 cases: owner folder 3, owner folder 9, the EAP folder (`runs\owner_*_r8`) and CHECK_0001 = test 1 (`runs\test1_r8`). The input, program and `today` are read from each r8 `run.json`. One extra `--auto` run of CHECK_0001 covers the window → worker path. | (a) Page texts: the app's `pagetexts\<case_id>.json` equals the r8 `page_texts.json` in file names, sha256, page counts, and every page's text, kind and sideways flag. Differing pages are listed by number with character counts. (b) summary.txt lines equal, except the READING line, the new RESULT CODE line and the output-path line. (c) In run.json: equal `overall`; `docs[*]` unit pages, verdict and `findings[*].(verdict, rule, pages)`; the cross findings; `missing_lines`; `notices`. (d) Every closer look's decision, best candidate and dot equal to r8's look log. (e) The result code printed per case: CHECK_0001's is THIS_PC_CODE; `--auto` gives the same. **The build passes only when (b)-(d) hold for all four cases.** A page-text difference with equal verdicts is explained in the report before hand-over. Also reported: calls on the processor and waits. |
| **P2 Self-test** | `--selftest` of the frozen dist (`build.py check`, via cardrun) and of the installed app (P3). | 9 of 9 OK. READ CODE recorded with the card name. |
| **P3 Install cycle** (`install_cycle.py`) | Runs only when neither the HKCU key `{AppId}_is1` nor `%LOCALAPPDATA%\Hangeul Document Checker` exists (never touches an install or data of the owner; otherwise it reports SKIPPED). Steps: `Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="%LOCALAPPDATA%\Temp\HDC install test" /TASKS="" /LOG=<reports>\install.log`; checks; installs again over it (upgrade path, `[InstallDelete]`); checks; `unins000.exe /VERYSILENT /SUPPRESSMSGBOXES`. | After each install: every file matches MANIFEST.json (size and sha256, none extra); the HKCU uninstall key with DisplayVersion 1.0.0; the Start-menu `.lnk` points at the exe; BUILD.json `build_id` = the Setup's; the installed `--selftest` (via cardrun) exits 0. After the uninstall: the folder, the key, the shortcut and the DATA folder made by the test are gone. |
| **P4 Politeness** (`politeness.py`) | Frozen `--cli` on CHECK_0001 via cardrun, with `--gpu-lock-extra <apptest>\fakelock\auto_verify.lock`. Scenarios: (1) lock there before the start; (2) lock made after the 3rd phase-1 page event; (3) lock made after the closer-look `names` stage starts (phase 2: the names stage after `financial`, I22); (4) Cancel (stdin `cancel` under `--worker`) during a wait; (5) a stale lock (mtime set 5 h back); (6) none (the reference); (7) lock made when the runner's `bot` stage starts (processor work, maybe no model call), held 60 s, with NVML free memory read every 2 s and again after the app ended (G1 review finding 1). Order 6, 1, 2, 3, 7, 5, 4. | (1) `gpu_wait` start with `where` "start" within 3 s and no `card ready` event for 60 s; after removal the check finishes. (2) and (3): `gpu_wait` start within 3 s, `reserved_mb` ≤ 256, no page or stage event during 60 s, resumed within 10 s of removal. (4) a `cancelled` event and exit 4 within 10 s; the worker process is gone; the saved page texts make the next run skip the pages already read. (5) no wait. (7) the app's card memory during the hold (free after the end − the least free during the hold) ≤ 1024 MB (context, weights, ≤ 256 MB cache), every wait in the hold with `reserved_mb` ≤ 256, the check finishes. (1), (2), (3), (5) and (7) give the same result code as (6) and P1. When the REAL bot lock was active during a scenario, its timing checks (and 7's memory check) are reported as "bot active" and not failed. |
| **P5 Timing and memory** (`timing.py`) | The four cases (fresh DATA), then CHECK_0001 again with saved page texts. | Reported: worker start (torch import, CUDA, readers), phase-1 seconds per page against the r8 `page_texts.json` seconds, phase-2 seconds, calls on the card and on the processor, peak working set and commit of the worker, peak card allocated and reserved, waits. Targets: worker peak working set ≤ 3.5 GB and peak commit ≤ 6.0 GB (the orchestrator's decision, I21; was commit ≤ 4.5 GB); card peak reserved ≤ 3.0 GB; each case's total ≤ r8's `run_case.log` total + 60 s; the re-check loads no first-read model. |
| **8 b Static scan** (amended) | As rev 1, over the 4.6 GB dist (the raw files, base_library.zip, the CArchive, the PYZ). | Zero hits of the forbidden strings except the lines of `packaging\scan_allow.txt` (`module or file \| string \| reason`). For our code it allows exactly ONE line: `src.verify.runner.app.cardgate \| c:\hangeul\bot\data\verification\auto_verify.lock \| the default lock path, used only when its folder exists (R2.2)`. Third-party hits (for example ".venv" in torch's own code) are reviewed and listed with a reason before the build passes. The `src.*` module set equals `STAGE_LIST.txt`; no onnxruntime, PySide6 or `DirectML.dll`. |
| **8 c Runtime trace** (amended) | As rev 1 on one case (CHECK_0001, `--auto`). | The allowed set adds `%WINDIR%` (nvml.dll; nvcuda.dll and the DriverStore) and `<DATA>\tmp\<job>\ascii`. No access under `C:\Hangeul\BOT`: the lock is only stat'ed, which raises no audit event. Native NVIDIA driver files (`%APPDATA%\NVIDIA\ComputeCache`, ProgramData) are outside the trace's reach; this is listed as a limit. |
| **8 g Frozen smoke** (amended) | `--selftest-part imports` imports every bundled `src.*` module (gpuread included), torch, torchvision, easyocr, `scipy.ndimage` and `skimage.io` without CUDA work. | No error. |
| **8 h DLL closure** (amended) | As rev 1, over every `.dll` and `.pyd` of the dist (pefile, `fast_load` + import tables). | Every import is in the bundle or on the baseline; `nvcuda.dll` and `nvml.dll` count as "driver-provided"; no base name twice; the `torch\lib` DLL set equals the build env's; the VC++ rule of rev 1. |

---------------------------------------------------------------------------------------------------------

## R2.7 Build tasks (replace rev 1 §9)

New app code goes in `C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\app\`; packaging goes in
`...\page-types\packaging\`. The runner's own files change only in Task A. File ownership is disjoint.

**Before the builders start**, the orchestrator creates the two EMPTY package files `app\__init__.py` and
`app\tests\__init__.py`, and nobody else writes them. Packaging tests run as
`-m unittest discover -s <PKG>\tests`, with no package files. Each task codes against the contracts of R2.1-R2.5 and
rev 1 §§1.2, 1.3, 2, 3.3, 4. It fakes the other tasks' modules in its unit tests, which must not import the card,
torch's CUDA or PyInstaller. `emit` and `hooks` are passed in, so E and T can be built before A.

**Every builder:**
- runs only its own light unit tests, with `HDC_TEST_NO_CARD=1` and in the runner venv;
- writes test files only under `private-runner\selftest\app\<task>`;
- never runs the full runner suite, a real case or PyInstaller, and never uses the card;
- leaves no process behind;
- puts no personal data anywhere;
- does not commit.

At most 3 builders run at a time, because of PC load.

| Task | Owns (exactly) | Delivers |
|---|---|---|
| **A Runner seam** | `src\verify\runner\places.py`, `devplaces.py`, `hooks.py`, `resultcode.py`, `tests\test_places.py`, `tests\test_resultcode.py`, `tests\test_reader_factory.py`; and ONLY the edits of rev 1 §4.3 + R2.1.6 in `botimport.py`, `safety.py`, `engine.py`, `pipeline.py`, `__main__.py`, `report.py`, `birth.py`, `tin.py`, `doccheck.py`, `tests\test_report.py` | The seam with DEV unchanged; `READER_FACTORY`; APP `models_dir` and `glue_data`; `prepare` safe to call twice; `load_page_texts` under write_root. It runs its own new tests and `test_report`, `test_safety` and `test_engine` (with the reader faked), not the whole suite. |
| **E Card info and gate** | `app\gpuinfo.py`, `app\cardgate.py`, `app\tests\test_gpuinfo.py`, `app\tests\test_cardgate.py` | R2.2 and R2.3.1: NVML through an injectable loader (a fake DLL object in the tests); `problems()` order and thresholds; `card_env`; `arch_ok`; lock stat-only, stale rule, `CHECK_EVERY_S`, waits with fake clock and sleep, Cancelled passing through, `wait_memory` timeout; `default_lock_setting` only when the folder exists (fake folders under private-runner). |
| **T Torch engine** | `app\gpuengine.py`, `app\tests\test_gpuengine.py`, `app\tests\fake_torch.py` | R2.1.2, R2.1.3, R2.1.5. `start_card` (refuses under `HDC_TEST_NO_CARD` before importing torch; cap fraction; native allocator; defaults asserted); `GatedReader`; `ascii_path`; `first_read` around the REAL `gpuread.read_all` with a fake dv and a fake reader (resume, skip, save, page events, release in `finally`); `strict_fp32`; `load_models` (node-for-node test against export_onnx.py when present); `TorchSession` (input checks, one OOM retry, processor call, CUDA error raised, output checks); `CardLightReader`; `card_error_code`; `reading_note`. Uses a fake torch namespace (`cuda.*`, `OutOfMemoryError`, `inference_mode`, `from_numpy`). |
| **C Process plumbing** | `app\entry.py`, `app\boot.py`, `app\protocol.py`, `app\spawn.py`, `app\controller.py`, `app\trace.py`, `app\version.py`, `app\tests\fake_worker.py`, `test_protocol.py`, `test_controller.py`, `test_boot.py`, `test_trace.py`, `test_spawn.py`, `test_version.py` | Rev 1 §2 and §6.2 with R2.3.3: the `card` and `gpu_wait` events, job.json `gpu`, exit 6, the waiting state and Cancel during a wait, `--cli` as the in-process `worker.run_job` (imported lazily), `--data-dir`, `--gpu-lock-extra`, boot applying `gpuinfo.card_env` + `OPENBLAS_NUM_THREADS` for the worker roles, the spawn environment, `version.py` with the BUILD.json GPU fields (`ARCH_FLAGS` ...). |
| **W Worker** | `app\worker.py`, `app\checkfacts.py`, `app\summarypdf.py`, `app\tests\test_worker.py`, `test_checkfacts.py`, `test_summarypdf.py` | R2.1.1 `run_job`: the order of the phases, the guard before the card, the input snapshot, `READER_FACTORY`, the reading note, error mapping (CardRefused, CardMemoryShort, CUDA errors to the six codes and exit 6; Cancelled to exit 4), the `done` gpu block, the checkfacts `gpu` block (counts, no personal data). Tests fake gpuinfo, cardgate, gpuengine and pipeline. |
| **B Window** | `app\window.py`, `app\messages.py`, `app\recent.py`, `app\settingsfile.py`, `app\userdirs.py`, `app\tests\test_messages.py`, `test_recent.py`, `test_userdirs.py`, `test_settingsfile.py`, `test_window_model.py` | Rev 1 §3 with R2.3.2: card messages and waiting banners, the startup and pre-Check probe (Check disabled on a hard problem), the footer card line, the Options lock setting (`gpu_lock_path`, default from `cardgate.default_lock_setting()`), GPU seconds per page. The window logic sits in a headless `WindowModel`, tested without a display. |
| **D Self-test** | `app\selftest.py`, `app\sysinfo.py`, `app\tests\test_selftest.py`, `test_sysinfo.py`, `packaging\make_selftest_pages.py` | Rev 1 §1.2 with R2.3.4: part `card` (fakes in the tests), part `runtime` with the CUDA DLL origins (a fake module list), the new SELFTEST.txt lines and warnings, READ CODE per card; `selftest_page1.jpg`. |
| **F Build** | `packaging\make_build_env.py`, `build.py`, `stage.py`, `hdc.spec`, `version.py`, `make_icon.py`, `manifest.py`, `dll_baseline.txt`, `scan_allow.txt`, `packaging\tests\test_stage.py`, `test_build_env.py`, `test_manifest.py` | R2.4: the build env (create, check, remove; only under app-build), the generated stage list, weights with MD5 and sha256, the seven gpuread swaps plus the rev 1 swaps under the AST check, BUILD.json fields, the spec, the after-Analysis assertions, `build.py` (`stage`, `freeze`, `check`, `installer` calling Task G's `build_installer.main`), BELOW_NORMAL subprocesses. Tests use made-up stage trees; none runs PyInstaller. |
| **G Installer** | `packaging\installer\hdc.iss`, `packaging\installer\build_installer.py`, `packaging\installer\README_INSTALL.txt`, `packaging\tests\install_cycle.py`, `packaging\tests\test_installer.py` | R2.5 and P3: the .iss (a fixed AppId GUID), ISCC at BELOW_NORMAL with the `/DSpan=1` second pass, filling the README, the cycle with its "never touch the owner's install or data" preconditions. Tests: static checks of the .iss text, the README fill, and the cycle's precondition and manifest logic on made-up folders. No ISCC run. |
| **H Release tests** | `packaging\tests\scan_bundle.py`, `dll_check.py`, `trace_check.py`, `parity.py`, `politeness.py`, `timing.py`, `cardrun.py`, `packaging\tests\test_release_tools.py` | R2.6 (8 b, 8 c, 8 h, P1, P4, P5, cardrun) with unit tests on fakes: made-up dist trees and DLL import tables, made-up r8/app run.json and summary pairs (equal, a verdict change, a page-text change), recorded event streams for the politeness rules, and a fake gpu_guard for cardrun. |

**Shared contracts:**
- E defines `gpuinfo.card_env`, `Problem` codes and `cardgate` events; C, W, B and D use them.
- T defines `Card`, `first_read`, `light_reader`, `TorchSession.stats`, `card_error_code` and `reading_note`; W and D
  use them.
- A defines `hooks`, `places` and `READER_FACTORY`; C, W and T use them.
- C defines the protocol and `app\version.py`; B renders C's events.
- F owns BUILD.json's fields (C's `version.py` reads them).
- G's `build_installer.main(argv)` is called by F's `build.py installer`.
- H's `cardrun.run(cmd, timeout)` is used by F's `build.py check` and by G's `install_cycle.py`.

**Integration (one integrator, after all tasks; WORKPLAN G1 then G2; one heavy process at a time, BELOW_NORMAL, at
most about half the threads and 6 GB, no process left behind, never stopping a process it did not start):**
1. Check ownership with `git status`; check that the 7 bot modules are unchanged since 07b1f22 (`git diff --quiet`).
2. Run the full runner suite once (DEV), then every app and packaging unit test, then `pii_scan`. Owners fix their own
   files.
3. `make_build_env create` and `check`; `build.py stage`, `freeze`, `check` (P2 on the dist through cardrun).
4. P1 parity, case by case; then P4, P5 and 8 c.
5. `build.py installer`, then P3.
6. Report: sizes, times, THIS_PC_CODE, the MISS lists, calls on the processor, waits, peaks; the paths of Setup.exe
   and README_INSTALL.txt.
7. Update WORKPLAN.md. Leave the commit to the orchestrator.

---------------------------------------------------------------------------------------------------------

## R2.8 Open risks

1. **The bot's 15-minute check on this PC.**
   - The overlap with a starting bot check is at most one model call (about 1.5 s for the largest detection). In
     that window this process may hold up to 3 GB plus its CUDA context, while the bot runs uncapped. Between calls
     it holds at most its context, the weights and 256 MB of cache (R2.1.5; P4 scenario 7). Phase 1's reads follow
     each other closely and keep gpuread's allocator behaviour: a lock that appears between two of them is seen at
     the next read (≤ 0.25 s of checks), with up to 3 GB still cached until then.
   - During a wait the app keeps its CUDA context and the weights (target reserved ≤ 256 MB, plus a context of about
     0.3-0.5 GB that torch does not count).
   - If the bot keeps a large idle cache, free memory may stay under 4.0 GB, so checks on this PC would wait and then
     fail with LOW_CARD_MEMORY. P4 and P5 log the free memory while the bot idles.
2. **Drivers on other PCs.** A driver for CUDA 12.8 (R570 or newer) is required, and older drivers get
   DRIVER_TOO_OLD. Owners of other PCs must update; no driver ships with the app.
3. **Other card models read slightly differently.** Turing has no TF32, and other cards get other cuDNN kernels. A
   few characters can differ from this PC, and through them a verdict and the result code. The result code and
   checkfacts.json show it. Only a strict-fp32 first read everywhere would remove it, and that needs a new baseline
   instead of r8 (out of scope).
4. **Antivirus and SmartScreen** on an unsigned 2.5-3 GB Setup.exe and 4.6 GB of DLLs: slow scans, a possible
   quarantine (MISSING_PART), the "Windows protected your PC" prompt when downloaded. Mitigations: deliver by USB (no
   internet mark), Inno's well-known stub, no UPX, version resources. Code signing remains a later owner decision.
5. **Size and time.** Installed 4.6 GB; Setup.exe about 2.5-3.1 GB (not measured yet). Above Inno's 4.2e9-byte limit
   the installer spans into `.bin` files. The build needs about 15-25 min of PyInstaller and 30-60 min of LZMA2 alone
   on the owner's PC; a heavy step during the owner's work would slow the PC.
6. **Phase 2 is not bit-identical to DEV.** The light reader's calls run on the card in strict fp32 (or on the
   processor for oversize calls) instead of ONNX on the CPU: numbers equal within about 1e-6. Decisions were equal on
   550 closer looks, but the other light-reader uses (crop re-reads, turned pages) are covered only by P1's four
   cases. Without `max_split_size_mb:256`, large detections may fall to the processor more often (slower, not
   different).
7. **Phase 1 exactness rests on the same process conditions as gpuread** (R2.1.2). These matter: torch defaults,
   allocator and cuBLAS settings untouched, the cap, the package versions (imageio 2.38.1 against 2.37.4 for picture
   files), and `OPENBLAS_NUM_THREADS=1`, which DEV gpuread did not set. P1 (a) checks every page.
8. **The gpuread review findings of WORKPLAN step 2 a** are inherited unchanged so the verdicts stay those of r8: an
   OOM inside the bot's rotation tries is swallowed (GatedReader's wait-and-retry lowers the chance), and the RGB/BGR
   order of a picture after an OOM is wrong. Fixing them later changes gpuread for DEV and the app together and needs
   a new P1.
9. **RAM.** torch and CUDA host state (about 1 GB) come on top of the runner's 2-2.8 GB: the target is 4.5 GB, so an
   8 GB PC may page. Each check starts in about 15-25 s (torch import, CUDA start, two model loads).
10. **The DEV card rule.** gpu_guard's 90 s slots cannot hold a whole app check, so cardrun.py needs approval (Q-O1).
11. **PyInstaller and the build path.** If a code object kept `C:\Hangeul\...\app-build\...`, the scan fails and the
    build root must move (Q-O2).
12. **Wall-clock budgets** (rev 1 §10 item 7: QR and barcode ladders in birth and tin) still make verdicts depend on
    CPU speed on slower PCs.
13. **One card per PC.** The first NVIDIA card in PCI order is used; there is no choice in the window. On laptops a
    weak card can hit Windows' 2 s TDR on large detections (CARD_FAILED).
14. **The one C:\Hangeul string.** The default lock path is a literal in the bundle (one scan allow-list line). It is
    only stat'ed, and only when its folder exists.

## R2.9 Questions (decided 29 Oct 2026)

The owner handed control to the orchestrator, which answered the owner's questions with the recommended answers below
(the owner will be told). Every answer is what the built code already does.

| # | For | Question | Decided |
|---|---|---|---|
| Q4 | owner | A single model call too large for the card's 3 GB cap: (a) run that one call on the processor with the same weights, or (b) stop the check with "a page is too large for the graphics card"? | **(a), decided.** That one call runs on the processor with the same weights (torch fp32; `TorchSession(allow_cpu=True)`), counted (`calls_cpu`, checkfacts `gpu.cpu_calls`) and shown in the result notes ("<n> large reading step(s) ran on the processor ..."). |
| Q5 | owner | Bigger cards (12 GB and more): keep the 3 GB cap, or use more? | **Keep 3 GB on every card, decided** (`gpuengine.CAP_GB`; the same pages are read the same way as on this PC). |
| Q6 | owner | Desktop shortcut ticked by default? | **Yes, decided** (`hdc.iss` task `desktopicon` without `unchecked`). |
| Q2 | owner | Rev 1's OneDrive question: the default report folder when Documents is synced by OneDrive. | **Decided as rev 1 recommends**: detect the OneDrive-synced Documents and use `C:\Users\<name>\Hangeul Document Checks` (not synced) instead; otherwise Documents (`userdirs`). |
| Q3 | owner | Rev 1's saved page texts: how long to keep them? | **Decided as rev 1 recommends**: delete them after 30 days (start-up housekeeping, `DATA\cache` including `pagetexts`), and offer their deletion on uninstall with Yes as the default (a silent uninstall takes Yes). |
| Q-O1 | orchestrator | Frozen-app card runs on this PC go through `cardrun.py` (gpu_guard's dev lock held and refreshed for the whole run; the app's own gate for the bot and the 3 GB cap) instead of ≤ 90 s gpu_guard slots. | **Approved.** `HDC_CARDRUN_APPROVAL=Q-O1` is set only for those runs and unset afterwards. |
| Q-O2 | orchestrator | If PyInstaller keeps the build path in code objects, may the build root move to `%LOCALAPPDATA%\HDCBuild` (rev 1) while the venv stays in app-build? | **Yes**, when the scan shows build paths kept in code objects. |

## R2.10 Integration notes (29 Oct; G1 integrated, nothing frozen yet)

Where the built code differs from the text above, the code is right and these notes say why. Each is pinned by a test
(`packaging\tests\test_integration.py` holds the cross-task ones).

| # | Where | Built | Why |
|---|---|---|---|
| I1 | R2.3.1 `arch_ok`, R2.3.1 / R2.1.2 `start_card` | CUDA binary compatibility, the same rule in `gpuinfo.arch_ok` (before torch) and `gpuengine.arch_supported` (after torch): `sm_XY` runs on X.Y and later minors of major X; `sm_XYa` on X.Y only; `compute_XY` on X.Y and newer. Equal on cc 5.0-13.9 (tested). | The literal "cc in the list" would refuse every RTX 40 card (cc 8.9: torch ships sm_86, no sm_89), and 8.7 / 10.3 / 12.1 cards. torch's own check uses the same-major rule. |
| I2 | R2.3.3 boot line | `gpuinfo.apply_card_env(os.environ, index)` changes the environment in place. | `os.environ.update(card_env(...))` cannot delete a variable. |
| I3 | R2.1.3 `load_models` | export_onnx's `load_reader` is unchanged node for node; `easyocr` reaches it through a thin facade that adds the bundle's `user_network_directory`. | Otherwise EasyOCR makes `%USERPROFILE%\.EasyOCR\user_network`, which the APP guard refuses on a fresh PC (this PC already has the folder, so P1 would not show it). |
| I4 | R2.6 8 g | `--selftest-part imports` = `selftest.part_imports` (not one of the nine parts): every runner module found package by package, the bot modules through `botimport.load`, then torch, torchvision, easyocr, scipy.ndimage, skimage.io and the other packages; FAIL if torch started CUDA. `build.py check` judges `imports.json`'s status, not only the exit code (a part child exits 0 once its result is written). | Task D had no such part; F's check runs it. |
| I5 | R2.6 8 c | `trace_check` fails only the guard's network socket events (connect, getaddrinfo, sendto, sendmsg, gethostbyname(_ex), gethostbyaddr, getnameinfo) without `refused: true`. `socket.__new__` / `socket.gethostname` (from `platform.uname()`) are counted, not failed. | Task C's trace records them with `refused: false`; they reach no other host. |
| I6 | R2.5 listing | Corrected above (comments on their own lines; `VersionInfoProductTextVersion`). | ISCC 6.7.4. |
| I7 | rev 1 §6.4 order | `build.py check`: imports smoke → MANIFEST → scan_bundle, dll_check → frozen `--selftest` (9 of 9 + READ CODE) → `build_read.json` → MANIFEST rewritten and verified. | The self-test's `integrity` part needs a MANIFEST. |
| I8 | R2.3.4 part `card` | Waits at most 300 s for free card memory (inside the 900 s child); CER over A-Z and 0-9 only; READ CODE over four texts (page 1, page 2, photo, light reader page 1); no QR on page 2; the photo is read inside the first read. | Fit the child timeout; spacing is not reading; a QR's blocks would count as stray characters. |
| I9 | R2.6 Q-O1 | `cardrun.run` refuses (NotApproved) unless `HDC_CARDRUN_APPROVAL=Q-O1` is set. | The approval is enforced in code, not only in a docstring. |
| I10 | rev 1 §2 `done` | `done.lines` and `done.out` hold student data; P1 / P4 / P5 print counts and codes only, and cardrun writes the child's stdout only to `apptest\...\stdout.log`. | S9. |
| I11 | R2.1.3, R2.1.5 (G1 review finding 1, major) | `TorchSession.run` calls `Card.release()` after every call on the card (empty_cache when torch reserves > 256 MB); the worker's `Recorder` asks `gate.wait_turn("stage")` after every runner stage-start event while `pipeline.run` runs (never at a stage end, which runs in the runner's `finally`). P4 scenario 7 measures NVML free memory during a lock made at the `bot` stage. | The worker kept torch's cache (up to the 3 GB cap) through the runner's processor stretches, and the lock was seen only at the next model call, up to a minute later. |
| I12 | R2.2 (finding 2) | `CardGate.on_room` / `wait_room`; `wait_memory(fatal=False)`. After a lock wait and before every OOM retry the gate waits (≤ 15 min, never failing) until free memory ≥ cap − torch reserved + 512 MB. | The bot's process keeps its card memory after its lock is gone (messages, reports, publishing). |
| I13 | R2.1.2 (finding 3) | `usable_done`: the page-texts file carries `"app_build"`; another build's or engine's file is not reused; a `"downscaled"` page is read again. | `gpuread._previous` drops the mark of a reused page, and a re-check on an idle card should read it at full size. |
| I14 | R2.3.3 (finding 4) | The done event's `budget_hits` and `gpu.downscaled` / `gpu.skipped` become result notes; `reading_note` also counts pages read smaller. | Both can change a verdict against an idle PC and were only in checkfacts.json. |
| I15 | rev 1 §2 exit codes (finding 5) | `map_error` checks LOW_MEMORY (MemoryError, "not enough memory", WinError 8 / 14 / 1455, "the paging file is too small") before STARTUP_FAILED (ImportError, WinError 126 / 127 / 193 / 1114) and before CUDA errors; `cuda_marked` never reads the text of an OSError or ImportError. | A DLL load that fails for lack of memory names `cublasLt64_12.dll`; it was reported as CARD_FAILED. |
| I16 | R2.3.3 job.json (finding 6) | The window always writes `today` (its date at Check); the done event carries the worker's `check_date`, which the window shows; `auto.json` records it. | One check date for the window, the worker and the result code (a check started just before midnight). |
| I17 | R2.4.5 spec (G2 first freeze) | `hdc.spec`'s `filter_toc` ends with `drop_top_level_copies`: a top-level binary whose source file (normcase, absolute) is also collected into a sub-folder is dropped and listed in `toc_checks.json` `dropped`. A same-named DLL from another source still stops the build. | PyInstaller 6.22.3 keeps a dependency's package folder only for DLLs under `site.getsitepackages()`; the build env's packages come through `hdc_build.pth`, so the dependency scan collected 24 DLLs a second time at the top level (20 of `torch\lib`, the two of `numpy.libs`, the one of `scipy.libs` and `pyzbar\libiconv.dll`), next to their package-folder copies, and the first freeze stopped. torch loads every `torch\lib` DLL by full path after adding the folder, numpy and scipy add their `*.libs` folder, and pyzbar falls back to its own folder, as in DEV. |
| I18 | R2.6 8 b / 8 h, R2.4.6 `check` (G2 first check) | `scan_bundle` reads a PyInstaller archive only from an exe that holds PyInstaller's cookie; any other exe (`_internal\torch\bin\protoc.exe`) is scanned raw and noted, and the app's own top-level exe without an archive is a FAIL. `scan_allow.txt` gained two reviewed third-party lines (`networkx.algorithms.dag` "jarvis": a literature reference in a docstring; `setuptools.command.sdist` ".venv": setuptools' default exclude pattern). `dll_baseline.txt` gained `imagehlp.dll` (system; imported by `torch\lib\libiomp5md.dll`). `build.load_module` puts the module in `sys.modules` before running it. | The first real check: `CArchiveReader` raised ArchiveReadError on protoc.exe; the two hits are package text, not this PC's folders (no build path was kept in any code object, so Q-O2's move of the build root was not needed); imagehlp.dll is in System32 of every Windows 10 and 11; cardrun's `@dataclass` failed without its module in `sys.modules`. |
| I19 | rev 1 §2 Cancel, `protocol.watch_stdin` (G2 P1 `--auto`) | On Windows the stdin watcher reads a PRIVATE duplicate of the stdin pipe's handle with kernel32 `ReadFile` (`protocol.private_stdin`), after fd 0, `sys.stdin` and `STD_INPUT_HANDLE` were pointed at NUL; it never reads fd 0. Test: `test_protocol.StdinPipeChild` (a child with a stdin pipe imports numpy, reads NUL on fd 0 and still gets `cancel`). | P1's `--auto` run hung: the window's worker stopped right after numpy's DLLs loaded, before its first log line, with no card work (0 bytes in its log, 0.3 s of CPU, one thread in a pipe read). The C runtime's `_read` holds fd 0's lock while it waits, and the window writes nothing until Cancel; `import numpy` takes that lock (reproduced in a plain child: `import numpy` and `msvcrt.setmode(0, ...)` hang while another thread waits on a stdin pipe, `GetFileType`, `os.fstat` and `import pymupdf` do not). `--cli` has no stdin pipe, which is why P1's four `--cli` cases and the self-test ran. Every Check from the window would have hung. |
| I20 | R2.1.3 step 4, R2.1.5 (G2 P5) | `gpuengine.lean_detector_scores`: the OOM retry runs the detector through CRAFT.forward's operations in the same order with each VGG skip feature dropped after its last use (and the unused `feature` not kept); the processor call is lean too. `TorchSession.stats()["calls_lean"]`, checkfacts `light_reader.lean_calls`. Tests: fakes for the order (first attempt plain, retry lean, processor lean) and a real-torch child (lab\onnx\.venv, CPU, random CRAFT weights, odd sizes, batch 2): bit-identical to `DetectorOut(CRAFT)`. | P1/P5: owner folder 9 sent 2 detections (2560 x 1856 and 2560 x 1952) and the EAP folder 1 (2560 x 1824) to the processor; each torch-CPU detection took 10-18 s and lifted the worker to a 4.8-5.2 GB working set and 6.3-10.2 GB commit (f3 and CHECK_0001, with none: 1.9-2.0 GB and 5.4 GB). On this PC's card (one gpu_guard slot, 3 GB cap, strict fp32) CRAFT's own forward ran out of memory at both 2560 sizes and the lean pass ran (peak allocated 2455 and 2579 MiB); at 1600 x 1216 both ran and gave bit-identical outputs. |
| I21 | R2.6 P1 / P5 inputs and targets (G2) | `parity.py` and `timing.py` take `--input LABEL=PATH` for a case whose r8 input is gone (the owner removed Desktop folders 3 and 9): G2 read them from the owner's zips into `private-runner\tmp\zipsrc` (deleted afterwards), each file's sha256 and size checked against r8's page texts and each mtime set to the zip entry's (= the r8 staged copy's), so the case id equals r8's (P1 (c) fails otherwise). One file of folder 3 differed in the zip from what r8 checked; it was taken from r8's staged copy (`runs\owner_f3_r8\docs`, same bytes and mtime). P5's memory target is the orchestrator's: worker peak WORKING SET ≤ 3.5 GB; commit reported, > 6 GB is a miss to be fixed. | Without the case id the r8 comparison is not the same input. Commit on this PC includes WDDM's charge for the card memory torch reserves (about 3 GB during the first read, measured: commit 5.3 GB with 2.4-2.7 GB of host-visible private memory), so commit is not RAM in use. |
| I22 | R2.6 P4 scenario (3) (G2 P4, build 20261029.3-369528d) | `politeness.py` makes scenario 3's lock at the names stage that starts after the `financial` stage (`names.evaluate`, the closer looks, card calls), not at the first `names` stage. Test: `test_release_tools.test_controller_scenario_3_fires_at_the_closer_look_names_stage`. | The runner has two names stages (pipeline.py): `names.affidavit_info` (0.0 s on CHECK_0001) and, after `financial`, `names.evaluate` (45.9 s, the closer looks). The first P4 run fired at the first one, which leads straight into the processor-only `bot` stage (22.5 s, no model call), so the wait began only at the next stage start (`bank`, 22.6 s after the lock, reserved 112 MB): scenario 7's case, which by design (R2.1.5) uses no card while the lock exists; scenario 7 itself passed (the app held 250 MB while the lock existed). With the closer-look trigger the wait started 0.3 s after the lock (where `rec`: the next model call), reserved 112 MB, resumed 1.8 s after the removal, same code. |
| I23 | R2.6 P1 (a), G12, the "Facts" list (G2 P1, build 20261029.3-369528d) | P1 passed (b)-(d) on all four cases and `--auto`; the page texts are equal except EAP pages 9 and 14 (2 and 14 characters). Explained, not removable: r8's EAP page texts are r6's first read (r7 and r8 reused it: same slot records and texts), made in 5 gpu_guard slots (5 processes: pages 1-5, 6-8, 9-13, 14-26, 27-29), and pages 9 and 14 are exactly the FIRST pages of slots 3 and 4. The app reads the folder in one process, like DEV's four one-slot first reads of the same folder (`runs\owner_eap1`, `_i1`, `_r5`, `_fx1`), which equal the app's texts on all 29 pages. Experiment (`apptest\<build>\coldstart-*`, through cardrun): the frozen app reading page 9 alone, and page 14 as the only pending page (page 1 of its file taken from the app's own saved texts), each as the first page of a fresh process, gives r8's texts exactly (0 characters differ). The differing spans are letters only (one 2-letter token read as 1 letter; one extra 5-letter word and one 5-to-7-letter word), not in digit runs, dates or the identity names; the app's verdicts with them equal r8's (summary lines, run.json findings, all 11 closer looks, code 2E6C-FDEC). Also: owner folder 9's pages 1, 2 and 11 ran out of card memory at full size in the first read and were read at 2000 px (`downscaled` 3, 3 first-read OOM retries), with texts equal to r8's: r8 read them smaller too, and its page_texts.json lost the mark when r6's read was reused (gpuread `_previous`), so "none was read smaller" in the Facts list does not hold for folder 9. No page was skipped and no model call ran on the processor in any case (the detector's lean retry ran 2 times in folder 9 and once in the EAP folder, I20). | The first model calls of a fresh card process can read a few characters differently from the same page read later in the process (the card's state, not the input); r8 inherited gpu_guard's slot boundaries. Making the app reproduce them would mean copying where r6's slots happened to expire. |
| I24 | R2.6 8 c (G2, build 20261029.3-369528d) | `trace_check`: an `os.mkdir` under the install folder of a folder that holds MANIFEST.json files is a no-op (counted as advisory; without a manifest it stays a finding), and the harness's own `--auto-result` file and its `.tmp` may be written (`--allow-file`; `run` passes its auto.json). Tests: `test_release_tools.test_mkdir_of_an_installed_folder_is_a_no_op_only_with_the_manifest`, `test_the_harness_result_file_may_be_written`. | The first 8 c run had 7 findings and no real write: 4 were EasyOCR's `Path(dir).mkdir(parents=True, exist_ok=True)` on `_internal\engine\easyocr\model` and `user_network` (first read and light reader; both folders shipped, their times unchanged after the run, MANIFEST still equal), 3 were the window writing the harness's `auto.json` (`.tmp`, rename) next to the trace folder, where trace_check's `run` asked for it. The same trace re-judged, and a fresh run, pass with 0 findings. |
| I25 | R2.6 P5 (G2, build 20261029.3-369528d) | Memory met on every case: worker peak working set 1.84-2.08 GB (≤ 3.5), commit 4.85-5.47 GB (≤ 6.0), card reserved ≤ 2.973 GB, 0 processor calls. One time miss: owner folder 9 took 336 s against r8's 101 s + 150 s + 60 s = 311 s. | r8's runner reused cached bot-stage reads from earlier DEV runs (folder 9: 0 reads, 1 cache hit; 76 s), while every app check starts with a fresh DATA folder (53 reads in 172 s on the card; bot stage 254 s). On the same footing the app is faster: first read about 50 s against r8's 150 s; DEV rev 2.7 on the EAP folder with no cache (`runs\owner_eap1_chk_cpu`) took 619 s against the app's 311 s. The light reader's recogniser averaged 25 ms per call on the card; 3686 of its 4658 calls in folder 9 ended with an `empty_cache` (I11's 256 MB rule). |
| I26 | R2.5, R2.6 P3 (G3, build 20261029.3-369528d) | `build.py installer --check-date 2026-10-29 --this-pc-code 90A2-20FC`: ISCC 6.7.4 at BELOW_NORMAL in 384 s (peak job memory 2469 MB), one Setup.exe of 1,939,911,235 bytes (ratio 0.41 of 4.71 GB; no disk spanning), unsigned, FileVersion 1.0.0.3, product version "1.0.0 (GPU, build 20261029.3-369528d)". `install_cycle.py` gained `--case <r8 run>` (after the upgrade and its self-test, the installed app checks that case with `--cli` through cardrun; its code must equal P1's `parity.json`, (b)-(d) must equal r8, its page texts must equal P1's read page by page) and `--data-dir` (I27). P3 with `--case owner_eap1_r8 --data-dir <apptest>`: PASS, 36 of 36 checks: Setup 92 s and 95 s, 3589 files equal MANIFEST twice, the upgrade's `[InstallDelete]` removed the probe, self-test 9 of 9 twice (READ CODE F6BF-334B), the case exit 0 in 313 s with code 2E6C-FDEC (= P1), 22 summary lines, run.json and 11 of 11 closer looks equal to r8, 29 of 29 pages equal to P1 (27 of 29 to r8: pages 9 and 14, as I23), 0 processor calls, worker working set 2076 MB; the silent uninstall removed the folder, the HKCU key, the Start-menu shortcut and the data folder. Tests: `test_installer.TestCycleCase` (8) and `TestCycleMain.test_main_with_a_case`. | The orchestrator asked for one owner case on the INSTALLED app with P1's code. Inno's uninstaller leaves its own copy in `%TEMP%\is-*-uninstall.tmp` (`_unins.tmp`, 4.4 MB, with `_unins-done.tmp`); the next Inno uninstall removes it. G3 removed the two its runs left. |
| I27 | R2.6 P3 on THIS PC; `safety._private` (G3) | Run inside the Claude desktop app's process tree (an MSIX package), a NEW top-level folder of `%LOCALAPPDATA%` is redirected into `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local` (existing folders such as `Temp` and `Programs` are not, so Setup's files were real; the Start-menu `.lnk` went to the package's `LocalCache\Roaming`). DATA's real path then differs from its name and `_private` (the resolved cache root against the UNRESOLVED write root) refuses: the first P3 run's installed self-test failed only part `guard` (8 of 9) and the case exited 2 (REPORT_NOT_WRITABLE, stage `safety`). Its SELFTEST files are in `reports\install_cycle\run1-redirected-data`. P3 was therefore re-run with `--data-dir` (a new folder under apptest) and a made-up file in the real data folder for the uninstaller's question. NOT changed in the app (it would need a new build and P1 again): a user who starts the app from Explorer or the Start menu is outside any package, so DATA is not redirected. | Open for the orchestrator: the same refusal would hit a PC whose `%LOCALAPPDATA%` lies behind a junction. A fix would let `_private` (APP mode) also accept a path under the RESOLVED write root, as `_app_roots` already does for the write roots; and the message would then not say REPORT_NOT_WRITABLE. |

**G1 smoke (29 Oct, from source on this PC's card, made-up self-test pages, one gpu_guard slot):** `worker.run_job`
with places in APP mode through the test overrides: exit 0 in 56 s; first read CER 0.0 on both PDF pages and the photo,
page 2 reported sideways; light reader 167 calls on the card, 0 on the processor, 0 OOM retries; peak card reserved
2.97 GB (the first read fills the 3 GB cap, as gpuread did); guard refusals 0, process starts 0. The DEV runner (ONNX
on the CPU) on the same input and page texts gave equal summary lines, equal run.json findings and the same result
code, and B's window rendered the worker's events on real Tk widgets. Seen: the worker's peak commit was 5.4 GB
(working set 1.8 GB) against P5's 4.5 GB target, and the runner's `bot` stage took 47 s for 3 made-up pages (DEV: 74 s,
so it is the runner's own work).

---------------------------------------------------------------------------------------------------------

# REVISION 1 — the CPU design (27 Oct 2026); in force wherever revision 2 above does not override it

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
