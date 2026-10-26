# Prototype answer: can we tell which page is which document in a mixed scanned PDF?

Throwaway prototype on branch `prototype/page-types` (`src/verify/prototype_page_types.py`, commit 520107f).
Numbers only. No names, document numbers or page text appear here.

Re-run from the worktree root:

    C:\Hangeul\BOT\.venv\Scripts\python.exe -m src.verify.prototype_page_types --mixed 0

## Answer

**Partly.** The prototype can tell what kind of document a page is. It cannot yet reliably tell where one document
ends and the next begins, which is what the per-document hard rules need.

- **Page type: yes.** A text-only, rule-based recogniser types the documents that carry hard rules correctly at file
  level 97% of the time on students it was never tuned on. On the one real mixed scan tested (CHECK_0001), it typed
  all 14 pages correctly at the bot's own document-type level.
- **Document boundaries: not yet.** In a simulated merge of each student's files, 84% of files come back as exactly
  one document of the right type, and 89% of the required files do. On CHECK_0001 only 1 of 9 real documents came out
  with exactly the right pages; counting the employment letter and TIN certificate as the bot's one trade_license
  slot, it was 2 of 8. Two NIDs were merged into one, HSC and SSC were merged into one, and a trailing Bangla page was
  absorbed into the income-tax document.
- **"Upload a PDF → verdicts" is feasible.** It needs the build items listed at the end. The most important are
  cutting the PDF into one sub-PDF per document, and the portal student record, without which no check can run.

## Skeptic checks (done independently of the authors)

| Check | Result |
|---|---|
| Recogniser sees only page text and an is-image flag | Yes. `predict_page(text, image)`; file names are used only by `truth_label`. The cached OCR text contains no file name (read_pages / read_document return page text only). |
| Split by student, not by page | Yes. 201 student folders were split 100 tune / 101 held-out with seed 20261027. One student appears in two programme folders; both copies are in the held-out half (28 files counted twice, so there is no leak across the halves). No held-out file's text occurs exactly in the tune half. |
| Held-out half not used for tuning | Supported by the author's transcript. Every run during development was `--half tune`, and every diagnostic script looped over the tune half only. The held-out half was run once, after the last code edit. One display-only run followed it, with no edits after that. Tune-half results are a little higher than held-out (file 0.928 vs 0.915), which is consistent with no leak. |
| CHECK_0001 not tuned on | Supported. The recogniser author's transcript never references the CHECK_0001 text. The scan is not in the dataset folders. Its pages share at most 0.13–0.19 token Jaccard with any cached passport or NID-card page, so this is not a student in the data. Form pages (translation letterheads, eReturn, board mark sheets) share 0.45–0.64 with other students' forms, as forms do. The 4 fine-type errors and 3 boundary errors were left unfixed. |
| Re-run of the one-line command | Reproduced exactly: pages 0.846 / grouped 0.932 / files 1180/1289 = 0.915 / merged 1045/1252 = 0.835. |

## Numbers (held-out: 101 students, 1,289 typed files, 2,762 page-scored pages)

**Document (file) level, majority of page votes**

| Slice | Accuracy |
|---|---|
| All files | 0.915 (1180/1289); 95% student-bootstrap CI 0.900–0.931 |
| Excluding photos (trivial: an image with no text) | 0.908 (n=1188) |
| Excluding photos and id_card_cv (the catch-all slot the bot never checks) | 0.946 (n=1094) |
| Required documents with hard rules, excluding photo | 0.970 (n=928); CI 0.957–0.982 |
| Macro-average recall, each of 16 types weighted equally | 0.871 (0.953 over the 9 required types) |
| Students with every required (non-photo) file typed right | 79/101 |
| Baseline: always guess the most common type (nid) | 0.227 |

**Page level**

| Slice | Page alone | After grouping within its file |
|---|---|---|
| Pooled over pages | 0.846 | 0.932 |
| Pooled, excluding photos | 0.840 | — |
| Weighted per document (each file counts once) | 0.871 | 0.917 |
| Weighted per document, excluding photos | 0.859 | 0.910 |

Long files do not inflate the score: 33% of pages come from files of 6 or more pages, and the per-document weighted
figures are equal to or higher than the pooled ones. Photos raise the headline by under 1 point.

**Merged-PDF simulation** (each held-out student's files merged in random order; "exact" means the file comes back as
one document with the right type and the right page span)

| Slice | Exact |
|---|---|
| All files | 0.835 (1045/1252) |
| Excluding photos | 0.821 |
| Excluding photos and id_card_cv | 0.854 |
| Required documents, excluding photo | 0.887 (813/917) |
| Multi-page files only | 0.812 (567/698) |
| Pages that land in a document of the right type | 0.914 |

Exact recovery per type: passport 96/102, nid 248/292, birth 90/100, family 93/101, death 12/15, academic 91/102,
bank 93/103, trade_license 86/95, income_tax 4/7, language 54/64, affidavit 12/20, eca 9/19, personal_statement 8/17,
recommendation 7/20, id_card_cv 42/94, photo 100/101.

**CHECK_0001** (one real 14-page notarised mixed scan, read by OCR at 200 dpi)

- Bot-level page type: 14/14. Fine type: 10/14 (both NID originals were called translations; both board transcripts
  were called certificates). Academic level (HSC/SSC): 4/4.
- Documents with exactly the right pages: 1/9 (2/8 counting employment letter + TIN as the one trade_license slot).
- Scored as one file, the whole PDF votes "nid". It must be split into documents before any check can run.

## Main weaknesses

1. **Boundaries, not types.** These need fixing:
   - adjacent same-type documents: two NIDs, or HSC followed by SSC;
   - an unrecognised first or last page joining its neighbour.
2. **The NID translation flag fires on 9 of 14 pages of a fully notarised bundle.** Notary and NID-back wording
   triggers it, so the translation-then-card split never fires.
3. **Bangla-only pages become noise in English OCR** and fall to "other". Examples are trade licences and the land-tax
   page. They are recovered only by joining a neighbour.
4. **The free-form optional types are weak:** id_card_cv, recommendation, ECA and personal statement. Required types
   are strong.
5. **Ground truth is file-level only.** Fine subtypes (apostille / certificate / transcript, solvency / statement,
   TIN / employment, translation / original) have no truth labels beyond the 14 CHECK_0001 pages.
6. **The simulation is optimistic.** It merges separately scanned files, and drops files that have no cache or whose
   page breaks are unknown (37 such held-out files are scored at file level only).

## What the build must include

1. **A page-type step, then a grouping step, then one sub-PDF per document (page ranges).**
   `check_nid`, `check_academic`, `notarised`/`seal_present` and the page checks (`inspect`, colour, QR) all take a
   file path. Run on the whole PDF, they would read other documents' pages.
2. **Boundary rules that the prototype lacks:**
   - The translation cue should use letterhead wording only, so the NID translation-then-card split works again.
   - A change of academic level, or a certificate coming again after a transcript, should start a new academic
     document.
   - An unrecognised trailing or leading page should stay a separate "unrecognised" document and not be absorbed,
     so Bangla noise cannot feed amounts into `check_income_tax`.
3. **Person assignment for NIDs** (and death certificates). Match the portal Father, Mother and Full Name against
   each NID with `name_in`; gendered name markers are only a tie-breaker.
4. **The portal student record and programme at run time.** Every checker needs them. Missing documents should be
   reported against the portal's required list, not against what the upload contains.
5. **A reviewer surface.** Show each predicted document's page range and type, let a person correct a boundary or
   type before verdicts are final, and flag "unrecognised page" for a person to look at. Exact boundaries are right
   for about 85–89% of files, so a person must confirm them.
6. **Optional, later:** Bangla OCR or a real "unreadable" detector, and a learned classifier once page-level labels
   exist.
