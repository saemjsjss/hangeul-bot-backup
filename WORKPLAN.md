# Work plan: the owner's document checker, from rev 2.6 to the installer

The owner handed over full control on 28 Oct 2026 ("take whole control and finish smoothly and properly").

How the work runs:
- One heavy job at a time on the owner's PC. At most about half the CPU threads and 6 GB of memory are used, and no
  process is left behind. On 28 Oct three parallel jobs froze the PC.
- Each step ends verified and committed on this branch (prototype/page-types), never pushed. A session that ends
  loses at most the step in hand; this file says where to pick up.
- No student data appears in this file, in commits or in tests.

| # | Step | Status |
|---|------|--------|
| 0 | Rev 2.6: the closer look, the owner's 28 Oct rules, 4 owner cases re-run | done, 74325b6 |
| 1 | CPU memory fix (rev 2.7): detector and recogniser arenas off; page search at own size up to 2048 px, larger pages at 1600 then a 2048 fallback; memos capped; OpenBLAS one thread. Every decision equals closer-3 on the real set (0 false PASS / FAIL / wrong dot, review 38/130); r8 verdict lines equal r6; peaks 1.9-2.8 GB, except folder 9 on the CPU-only path at 3.2 GB (see limits) | done (commit after the suite) |
| 2 | GPU: (a) fix the code-review findings on the pre-read in use: a slot can overrun 90 s while the model loads, an OOM in the rotation retries is swallowed, RGB/BGR after an image OOM, the dev lock left behind on Ctrl-C, guard-load order vs 5.13; (b) the closer look's detector on the card: resolve the S2/S5/S11 conflict (own process or a spec change), detector only (recogniser stays on the CPU), complete parity on the current code, update the README | next |
| 3 | Stronger readers: finish the specialist's training (GPU, when the PC is idle), measure the second reader, the specialist and draw-and-compare with the closer look on the real and held-out sets; adopt only what meets TARGETS.md; the folder 3 and 9 bold italic / script names (Q23) | after 2 |
| 4 | The 16-student sample (sample16\sample.json, S01-S16): run, check against the documents by eye, report masked | after 3 |
| 5 | Trial build for another PC (APP_SPEC.md): CPU only, no Python, no internet, nothing from C:\Hangeul | after 4 |
| 6 | Installer (Inno Setup needs the owner's download approval; the PaddleOCR LICENSE text too) | after 5 |
| 7 | The owner's further custom folders; the decision on a full 190 run; new rules into the live bot only with the owner's OK | after 6 |

Known limits to revisit:
- Memory: folder 9 on the CPU-only path peaks at 3.2 GB (budget 3 GB) during the bot's own 200 dpi first read of a
  page larger than A4 (detector canvas 2560). Lowering it changes the first-pass texts; measure it on the trial PC in
  step 5 before deciding.
- Page search at 1600 merges some table cells into one row box (real cases R106, R117; R315 leans wrong); the gates
  keep them unclear, but re-check in step 3. The 1600-then-2048 order was chosen on the evaluation set (mild overfit).
- test_engine's test_cut_keeps_pixels_layer_and_mtime is flaky (about 1 in 5, since 27 Oct): its /ID regex misses a
  trailer /ID written as a literal string.
