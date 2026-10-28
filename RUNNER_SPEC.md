# RUNNER_SPEC — test runner for the owner's desktop document checker (prototype)

Status: design, revision 2.6, 28 Oct 2026. Branch `prototype/page-types`, worktree `C:\Hangeul\JARVIS\prototype\page-types`.
Not committed. This file holds no personal data. Every example name, number or date in it is a placeholder.

The runner is a prototype, not the app. It takes either one bundle PDF or one folder of bot-named files,
plus the program (KLP / EAP / BACHELOR / MASTER, given on the command line). It checks only those files
against the hard rules and writes three outputs: a chat-ready summary, a page-by-page Excel report and a JSON
of everything. The owner will hand over 7 real test cases, and the runner must turn each one into a report.
For the owner's tests the pages are read on the graphics card by a separate pre-read process (Task 8); the
runner itself stays on the CPU.

## Revision 2 (27 Oct) — changelog

Owner decisions A1-A8 (27 Oct) and the owner-rules critique (items 1-20) are applied. Where revision 1 and
these decisions differ, revision 2 wins everywhere in this file.

- **A1 strict names.** `strict.py` is rewritten: letter for letter, case and whitespace aside, every letter and
  every dot counts. No fuzzy match, no token subset, no honorific dropping. A difference is FAIL only when it is
  CONFIRMED (two independent reads agree) and no affidavit covers it; an unconfirmed difference is FLAG "check by
  eye, page N"; a difference is never PASS. The targeted name re-read is in scope and can fail
  (`REREAD_CAN_FAIL = True`, no audit gate). One policy for every person check (critique 9).
- **A2 dot rule.** `strict.dot_rule`: dots are judged against the passport's PRINTED name (never the MRZ); mixed
  dots across the other documents = FAIL (when confirmed), uniform dots = OK.
- **A3 birth certificate.** New Task 7, `birth.py`: formats (Bangla original + translation + notary, new bilingual
  with QR, older English print), the boxed DOB with its black blocks, the "In words" cross-check, the QR
  (offline checks). `dv.check_birth_cert` is no longer called. `BirthFacts` and `MachineRead` join `model.py`.
- **A4 GPU pre-read.** New Task 8: `gpuread.py` (a separate process in the bot venv, bot EasyOCR on CUDA under
  `gpu_guard.py`) and `C:\Hangeul\DOCCHECK\lab\private-runner\run_case.py` (GPU pre-read, CPU fallback after 120 s,
  then the runner). The runner gains `--page-texts` and `--reading-note`; bundle sub-PDFs are always primed.
- **A5 critique 1-20.** Photo edge finding (1); strict TIN QR name (2); whole-value owner row (3); parents from the
  birth certificate with every document as vocabulary, recomputed after the split (4); new MISSING slots (5); STOP
  only for the NID-person ambiguity, HINT notices (6); every order break is an ORDER notice, no cap (7); apostille
  page-1 rule is an ORDER notice in a bundle (8); one FAIL/FLAG policy (9); holder against birth certificate OR
  role-confirmed NID, every joint holder (10); `financial.holder_owner` (11); language DOB only when trusted (12);
  re-read MRZ DOB reaches the birth-certificate check (13); one affidavit rule for language (14); USD and seal on
  the solvency page (15); one line per academic level (16); death-certificate edge cases (17); income tax outside
  Master's gets a line (18); colour scan question + default (19); extras removed (20).
- **A6 test-1 findings.** (a) MRZ whose two lines OCR joined; (b) no false student-name FAIL from a dropped dot;
  (c) board QR codes on SSC/HSC; (d) NID numbers with glued OCR-noise digits; (e) glued initial; (f) a trailing
  unrecognised page after income tax attaches to it; (g) employment and TIN certificates stay separate lines;
  (h) income tax outside Master's: PRESENT + NOTEs; (i) TIN QR is the primary taxpayer source; (j) two NEW FLAG
  checks (TIN printed from a non-.gov.bd address; tax circle/zone differs), listed as "new, keep?".
- **A7 bank.** Account-age rule gone; holder named on the SOLVENCY and the father or mother (A1); chain taxpayer =
  holder = owner/employee (A1); balances as before; USD and seal on the solvency page.
- **A8.** The program is always given on the command line; test 1 is KLP.
- **Removed:** CLI `--edge`, `--prime`, `--force-split`, `--no-reread-names`; `doc.academic_gpa`;
  `income_tax.assessee`, `income_tax.tin`, `tin.return_qr`; `tin.qr_dob_parent`; the "other QR codes" NOTE; the
  `bank.statement_header` row; `financial.taxpayer_parent`; the affidavit deponent; language extras (Duolingo, PTE,
  EnglishScore, medium-of-instruction letters, 60-day warning, General Training FLAG, authenticity NOTEs); Excel
  sheets Identity, Names, Financial, Run; overall labels REVIEW, INCOMPLETE and ERROR; `strict.clearly_different`.
- **Versions:** `RUNNER_VERSION = "0.2.0"`, `RUNNER_RULES_VERSION = "2026-10-27.r2"`, run schema `runner-run/2` (revision 2.1: `"0.2.1"`, `"2026-10-27.r2.1"`, same schema).
- **Answered questions removed from section 12:** Q5 (MRZ re-read: yes), Q6 (own-name account: FAIL, A7), Q7
  (honorifics: strict, A1), Q8 (spacing: whitespace does not count, A1), Q11 (GPA check removed), Q12 (re-read can
  fail, A1). New: Q13-Q17.

### Revision 2.1 (27 Oct, after the three reviews) — changelog

- **Names (A1/A2/A3).** A value that ADDS words to the name is a difference: an added honorific always (FLAG, FAIL
  when confirmed); any other added word only when two reads agree on it (another page of the document or the
  zoomed re-read), because whole-page OCR runs misread labels, a table's district, layout words and Bangla text
  read as Latin on around a name (124 such FLAGs on the corpus, nearly all OCR); read once and unconfirmed, the
  read counts as the name inside it (Q18). A value wholly unlike the name is FAIL when two pages agree on it, else
  a FLAG `names.<person>_unread` "not compared — check by eye" (never PASS; the NAMES line says "not compared").
  The birth certificate's parent value is cut where
  the other documents' words end (152 of 390 corpus values go on with OCR junk); the words after the cut are the
  name's `NameRef.tail`: the targeted re-read confirms them into the name, and a document or account holder that
  adds exactly them (OCR distance allowed) is FLAG, never FAIL (Q19). Reference confirmation and the NID name
  are strict (no token subset). The re-read no longer refutes a variant that CONTAINS the reference (an added
  MST). "Name of (the) Deceased" is read on death certificates. Titles are dropped only at the start (Q16). A
  glued read (two letters run together where ANY spelling, the passport's included, has a dot) cannot confirm a
  dot, and never PASSes when its dots differ from the reference; a glued passport read is no dot reference.
  The language certificate's candidate name enters the dot rule. The assumed A2 case is reported ("ASSUMED")
  and every summary sheet lists the assumptions in force. Bank: a confirmed extra holder word is FAIL.
  Birth DOB window: a bare "Date:"/"Dated" ends it, "(In Figure)" does not.
- **Correctness.** Folder mode numbers every page inside its file (`model.page_no`). A "Date of Expiry" label
  printed in a row with "Date of Issue" takes the later date. The NID-block role search is polynomial (no hang).
  A statement file is not "solvency missing" when another bank file holds the certificate. The tax circle/zone
  is read only from the TIN pages. An unnamed low-text folder file is "unrecognised", not the photo. The headline
  counts every line that decides the result (a NAMES line on no judged document included). `--out` naming a file
  is exit 2. The RESULT line is never folded into the PASS list. "page"/"pages" wording. One death certificate of
  an unknown person cannot fill both parents' slots: `FATHER OR MOTHER NID (OR DEATH CERTIFICATE) MISSING`.
- **Safety.** Hard links (source and multiply-linked targets), junction / device / local-share spellings of the
  bot folder (real path resolved), `sqlite3.connect`, `_winapi.CreateFile` and `_winapi.CreateJunction` are
  guarded; the runner refuses every process start; the GPU worker reads only `C:\Hangeul\BOT\.venv`; the input
  is write-protected and snapshotted from before the GPU phase (`run_case.py`); refused events are counted and
  capped, a refused create-new open is not retried by tempfile; stdout carries no personal data (`--show` for
  the owner's terminal) and error lines carry no paths; pii_scan covers the e-passport shape, 11/12/16-digit
  runs, real dates of birth and numbers, names read off documents and APP_SPEC.md; two tests can no longer touch
  real folders or the card if a check broke.
- **Second fixer (27 Oct, evening).** A single read too unlike the name is FLAG (was NOTE: it counted as PASS) and
  the NAMES line says "not compared" (Q20 asks the owner whether to keep it: 240 such reads on the corpus). A refused `os.mkdir` raises CreateRefused too (`tempfile.mkdtemp` and
  `TemporaryDirectory` retried the PermissionError up to 2**31 times while the folder existed). A crash line on
  stderr names the exception type and stage only (its text may hold a value read off a document); log.txt and
  run.json keep the text. The Excel Summary lists Q18 and Q19 among the assumptions. A taxpayer page that the
  TIN check does not name is the first TIN page of the unit, not a trade licence page before it. "3 mother"
  (not "3-3 mother") in the NID-order notice.

### Revision 2.2 (27 Oct, night, after the checkers' second pass) — changelog

- **Names.** A label's value ends at a word printed in another case than the name (a capitalised name, then a
  Title-case or small-letter word: "<A> <B> successfully", a card's Title-case field word), at a field label glued to
  a colon ("Program:"), and at sentence, academic and occupation words (successfully, completed, appeared, Programme,
  Department, CGPA, Farmer, Housewife, Freedom Fighter ...): such words are never "extra name words", so a correct
  document no longer FAILs when the re-read or a second page confirms the printed words (Q21). A family table
  "<NAME> <occupation> <relation>" is read back over the occupation, and a relation cell followed by the next row's
  number is no label. The birth certificate's tail is confirmed by another page of the certificate printing the
  whole value too (section 7); unconfirmed, the certificate's line is FLAG `names.<parent>_tail` "check by eye" and
  the NAMES line says so (Q19: never a silent PASS); a document or holder adding other words where the unconfirmed
  tail stands (no more words than the tail) is FLAG, not FAIL (corpus, re-read off: 2 FAILs of one student become
  FLAG; no labelled FAIL changes). Bank: a descriptor after the holder's name ends it; a word the name reader never
  takes into a name extending the reference is FLAG, never FAIL.
- **Correctness.** The passport's "Date of Expiry" label in a row of date labels takes the date at its position
  among the dates after the row, in reading order (three labels, a non-date label between, a numeric date further
  on); trusted only when the row's dates were all read and the expiry is the latest. A chain or bank FAIL on a NOT
  JUDGED document is counted in the headline. Folder mode: a finding about pages of two files carries its run pages;
  the report names each page's file ("TIN CERTIFICATE.pdf p1; BANK SOLVENCY.pdf p2", the names re-reads too) and a
  message names the other file's page ("page 2 of BANK SOLVENCY.pdf"); on the FINANCIAL CHAIN summary line, which
  belongs to no file, a message citing its document's page gets that file's name ("page 2 (BANK SOLVENCY.pdf)"). The solvency certificate and the statement in
  separate bank files are one bank document: the bot's one-page FLAG becomes a NOTE and the dates are compared
  across the files (Q22). Error lines name an input file by its position ("file 2 of 7 in the folder"), never by
  its name, and never echo a relative path (redact() removes drive and UNC paths only).
- **Safety.** A device-namespace spelling that names no drive (`\\?\Volume{GUID}\...`, `\\?\GLOBALROOT\...`,
  `\\.\HarddiskVolumeN\...`) and any share but a local drive share is refused for every open, read or write (it
  was stripped into a path relative to the current folder); an NTFS stream suffix is the file itself. Every write
  to a file that has another hard link is refused (truncate, utime, chmod, CreateFile with write access, CopyFile2
  onto it, not only open). `_winapi.CopyFile2` (shutil.copy2 on Python 3.12) is checked (source as a read, the copy
  as a write) and `_winapi.CreateProcess` (multiprocessing) is refused in the runner and recorded in the worker. A
  create-new open under the bot raises CreateRefused (tempfile no longer spins). An input folder that holds a
  junction or a link is refused before any snapshot or listing reads through it.

### Revision 2.3 (28 Oct, after the owner's first two real folders) — changelog

The owner's folders (KLP) combine documents in one file and showed four runner bugs. Versions: `RUNNER_VERSION =
"0.2.3"`, `RUNNER_RULES_VERSION = "2026-10-28.r2.3"`, same run schema (new fields have defaults).

- **Folder: a file that holds several documents is cut into them (section 5.4, folder rule 7).** A "FINANCIAL
  DOCUMENTS" file (solvency, statements, trade licence, TIN) became ONE bank unit: false "TRADE LICENSE / EMPLOYMENT
  CERTIFICATE MISSING" and "TIN CERTIFICATE MISSING", the trade licence and TIN never checked, no financial chain.
  Now `split.file_documents` runs the bundle split's own rules on each folder file's pages alone (the alignment to
  the fixed order, the academic levels with their e-Apostille pages, the financial parts); a file that makes two
  documents or more gives one unit per document (`DocUnit.part` = the pages inside the file, "p4-6"), cut out of the
  staged copy (revision 2.4: not primed), judged by its own checks; the report names "<file> p4-6" and bot
  messages name the file's pages. Inside one file the NID block is not split into persons and a page with little
  text is no photo. No ORDER/NOT JUDGED in folder mode. A one-document file is unchanged (its "name vs content" FLAG
  included); a cut file none of whose parts is the named document gets that FLAG on every part.
- **Academic split (both modes).** A page with no academic sign right after an e-Apostille (a certificate scanned
  upside down) goes with that apostille to the level that follows (owner folder 3: the SSC apostille no longer
  joined the HSC). Between HSC and SSC, the examination a page names wins ("Dakhil Examination" is SSC even when the
  school's name holds "Alim").
- **Names: an outvoted reference (section 7).** The birth certificate's one OCR read (a bilingual JPG) misread one
  letter of the mother's name; its own zoomed re-read repeated the misread, so the passport, the student NID, the
  mother NID and the family certificate, which agree with each other, all FAILed. A name FAIL needs BOTH spellings
  confirmed; the reference's own re-read re-reads the same print, so when two or more other documents agree with
  each other on one spelling the reference does not have, no other document prints the reference and no
  independent read confirms it (a QR / MRZ machine read, another page of its document, another document or account
  holder), the finding is ONE FLAG `names.<person>_reference` "check by eye" on the reference document; the agreeing
  documents get a NOTE, a third spelling is at most FLAG, and the dot rule runs over the agreeing documents. One
  other document against a reference confirmed by its own re-read is still FAIL.
- **Birth certificate QR (section 5.12).** A decoded payload that carries the parents' names (`text_fields`
  father / mother; URL parameters or "Father's Name:" text) gives `MachineRead`s; equal letters confirm the reference
  (identity) and its tail; a difference is FLAG `birth.qr_father` / `birth.qr_mother` (never FAIL here). The genuine
  bdris link carries an opaque key: nothing changes for it.
- **GPU pre-read (section 5.13).** One page too large for the 3 GB cap (`torch.OutOfMemoryError`) ended the whole
  pre-read (owner folder 9: every page then read on the CPU). Now the page is read again after
  `torch.cuda.empty_cache()` with its long side cut to 2000, then 1600 pixels (`"downscaled"` in its record); if it
  still does not fit it is left out (the file's `"skipped"` list) and the runner reads it on the CPU; the other
  pages go on. The cap is never raised. `run_case.py` uses a page-texts file that belongs to the input on any worker
  exit and its READING note says how many pages the card, the text layer and the CPU read.
- **A file the runner cannot read (section 5.1).** A folder's other files (an `.odg` drawing next to the PDF of the
  same name) and a PDF or image that will not open are listed as "Not checked (not a PDF or image): <file>" /
  "Not checked (could not be opened): <file>" (`Source.not_checked`, `RunResult.not_checked`): never a document, a
  slot or a crash. A folder with nothing that opens is still exit 2.
- **Checks.** `tests/test_fixes23.py` (made-up data: the folder cut, the outvoted reference, the QR parents, the card
  memory retries, run_case's note, the not-checked files); the unit suite, pii_scan, venv_check, birth_fixtures,
  sim_split (numbers identical), sim_money (identical), sim_names (re-read off: the same 46 FAILs and FAIL precision;
  41 references outvoted, their per-document FLAGs become one FLAG + NOTEs) and CHECK_0001 (summary identical) pass.

### Revision 2.4 (28 Oct, the by-eye check of the owner's two folders after revision 2.3) — changelog

Every line of the re-run was compared with the page images by eye. Versions: `RUNNER_VERSION = "0.2.4"`,
`RUNNER_RULES_VERSION = "2026-10-28.r2.4"`, same run schema.

- **Folder parts are not primed (section 5.4 folder rule 7, 5.6).** Priming a part's cut with the run's page texts
  took the bot's own read away from the bot's rules for parts only (a bank balance the bot's own read prints in full
  came out split in the run's page read: the part's balance became a FLAG where the uncut file had PASSed). A part's
  cut is read by the bot as every folder file is.
- **The present-only ID card / CV gets no `split.name_vs_content` FLAG** (a CV's personal table prints the NID and
  passport numbers and "read like NID"; the hidden FLAG was counted in the headline).
- **Passport name label (section 5.5).** A passport prints the name in CAPITALS: a first word of the personal-data
  page's name read in Title case or small letters, within 2 letters of "name", before CAPITALISED words is the
  field's label misread ("Name:" read as another word), not a name word (`names.passport_label_lead`). The printed
  name then matches the MRZ and is the dot reference again (it was a false FLAG `identity.printed_name`).
- **Inside one folder file (section 5.4 folder rule 7).** Empty pages (typed "photo" for their emptiness) join the
  document before them, never a bank document (its page count is a rule) and never as a file's first page; floats
  and unrecognised pages between two documents of the same kind join that document when every page of them reads at
  most partly like any document (best cue ≤ `HINT_MIN`): a long return of income whose schedule pages read weakly is
  one INCOME TAX, not INCOME TAX + UNRECOGNISED + AFFIDAVIT + INCOME TAX (a false affidavit is used by the name rule).
  A float that reads clearly stays its own document.
- **Family table, wrapped name cells (section 5.9.1).** A name cell that wraps prints its second line after the
  row's other first-line cells. When the row's date cell is cut at the year ("01-06-"), the first CAPITALISED name run
  after it that a digit cell follows at once (the year, the number's end) is the name's second line
  (`names.wrapped_cell_tail`). The row reader also stops at a label token ("<Place>:") and at a Title-case word before
  a CAPITALISED name (the row before's address cell). The targeted re-read of a variant that is the reference minus
  its last word(s) takes in the line below as well (`WRAP_LINES`): a one-line crop had "confirmed" the dropped word,
  a false FAIL. Such a re-read is "unclear", never "confirmed", when the variant's words are followed by words within
  a third of their letters of the dropped word(s) (`names.dropped_words_follow`: the surname is there, one letter
  misread).
- **Not compared, with an outvoted reference (section 7).** The UNREAD FLAG ("a single read too unlike the name") is
  not raised on a document whose other read shows the spelling the other documents agree on.
- **Financial kind (section 5.6 adapter table).** `dv.check_financial` tells the kind by a few title words; a title it
  does not know ("TRADE AND BUSINESS LICENSE") or an OCR slip gives FLAG "could not tell which financial document
  this is" and a licence's own rows never run. When a page of the unit is typed as the unit's fine type by its own
  text, that row becomes PASS "contains: <kind> (by the page typing ...)" and a trade licence gets the bot's licence
  rows unchanged (`fiscal_year_check`, the business start date, the notarisation) — `doccheck.financial_kind_rows`.
- **Checks.** `tests/test_fixes24.py` (made-up data); `test_fixes23.TestCutParts` now asserts no priming.

### Revision 2.5, part A (28 Oct, the owner's closer-look and board-paper rules) — changelog

Owner, 28 Oct: "make sure if the reader misreads one letter of a name before giving verdict it will take a closer look
and give correct verdict ... industry standard"; "board papers md. is fine, no fail for that". These override section 7
where they differ.

- **`closer.py` (new): the closer look, a verification of KNOWN values.** `look(image, box, candidates, kind)` ->
  `Look(decision, best, scores, margin, variants, agree, dot, note, detail)`; `page_image(engine, page, dpi=400)` (a
  vector page at 400 dpi, a scan at its own resolution, a JPG as it is); `MARGIN` (0.12 nats per reference character;
  `MIN_SCORE` -1.6, `MOST` 0.6, `DISSENT` 0.1). The text is found by the CRAFT detector's line boxes and a keyword
  score of every candidate (the boxes beside it on its line join it; a wrapped table cell is also tried as its two
  lines side by side). Views: four vertical margins x five channels (grey, darkest channel, R, G, B) plus contrast
  stretch, Otsu and adaptive binarisation, sharpening, thicker and thinner strokes and two skews; small lines are
  enlarged 2-3x (Lanczos); printed rules (an underline turns a 3 into a 2) are painted out. On every view the
  english_g2 recogniser (the engine's CPU ONNX session, never DirectML) gives per-frame softmaxes and the CTC forward
  log-likelihood of EACH candidate is computed (case-blind letters; a space or a label's colon free; '.' its own
  class for kind "dots"; '<' a separator), over the candidates' own region of the line (a label before or a field
  after is not counted; a word one candidate adds is). "same" / "differs" only when the winner leads by >= MARGIN
  on average AND in >= 60% of the views, with <= 10% of the views backing another candidate by the margin;
  else "unclear". Two independent experts besides: the letter-shape check (a small letter's height against the
  word's own ascender and x lines: t / l, i / l, n / h ...; owner folder 3's birth certificate read "t" as "l" in
  every view) and, for dots, a geometric dot detector (a small solid blob on the baseline between the letters). A
  single candidate is verified against the recogniser's own reading of its place.
- **Names (`names.py`).** The Confirmer takes closer looks when the engine carries the recogniser (`Confirmer.look`,
  `.pair`, logged in `names_rereads` with the crop `work/look_pNN_k.png`); a plain test confirmer keeps the legacy
  re-read rule. Before a difference becomes a finding BOTH sides are looked at: the variant's page ("same": a misread,
  no finding, the log's effect "misread corrected by closer look") and the reference's own page. FAIL = the variant
  supported on its page (or two pages / a machine read agreeing, the look not "same") AND the reference supported
  (its look "same"; when unclear, a machine read or another document printing it) AND no affidavit; else FLAG. **The
  revision 2.3 outvote is removed** (`_outvoted`, `ref_independently_confirmed`): other documents never decide a
  spelling. `identity.verify_references` (finish_identity): when other documents print another spelling of a parent,
  the closer look on the birth certificate's own page decides; "differs" corrects the reference (NOTE
  `identity.reference_corrected`). The birth certificate's tail ("reads on with NOR") is looked at: "same" drops it
  (NOTE `identity.tail_not_printed`), "differs" takes it, "unclear" keeps the FLAG.
- **Dots.** When the reads disagree on a dot, each document's dots (and the passport's) come from a closer look of
  kind "dots"; an unclear look leaves the OCR dots unconfirmed (FLAG at most). **Board papers** (an academic unit of
  level SSC / HSC, a title naming SSC / HSC / JSC / JDC / Dakhil / Alim, or a page printing an education board's
  examination with no university word: `names.board_paper`) are left out of the dot rule; their letters stay
  strict; a NOTE "board papers' dots not judged (owner rule 28 Oct)" where their dots differ.
- **Family table.** A wrapped name cell's second line is kept apart (`Read.wrap`) and taken in only on name-like
  evidence (`names._wrap_choice`: the read with it IS the reference, or the read is the reference minus its last
  word(s) and the second line is within OCR distance of them); an address word under a one-word name is never taken.
- **Identity / language.** MRZ line 1 with a noisy read (not well formed, or letters other than the printed name's):
  the printed name in MRZ order is verified on the passport page with the closer look ("same": it becomes the MRZ
  name, well formed; the printed name stays the dot reference). A language certificate's name is compared with the
  passport's PRINTED name when MRZ line 1 is not usable (never "passport name not available" when it is known); a
  word that reads differently gets the closer look on the certificate's page and the passport's
  (`passport_for_language()["closer"]`).
- **Closer look, second pass (Agent A2, 28 Oct; measured on Agent C's real set, 130 cases from the four owner cases
  and the corpus).** The first version gave 3 false "differs" on misreads (a wrapped family-table cell read from its
  first line only, twice; a bold italic certificate whose a / t the recogniser reads as u / l in every view) and left
  the q of a name out of the crop. Changes:
  - **Every difference must hold by itself** (`closer.segments`, `differences_hold`, `SEG_MIN` 2.0 nats): the places
    where the winning spelling differs from another candidate are found by a minimal edit alignment; for each place
    the winner is scored against itself with that ONE place taken from the other candidate. "same" / "differs" need
    every place to favour the winner by >= SEG_MIN (mean over the views). A spelling that wins on the sum while one of
    its places reads the other way is not what the print shows (a third spelling, or a misread the recogniser shares):
    "unclear". This is what keeps a real one-letter difference from "same" when the OCR variant also adds an
    unprinted word, and a shared italic misread from "differs".
  - **Added words must read as print** (`added_reads`, `ADDED_MIN` -1.6 per letter): letters the winner has where the
    other spelling has none (an MST, a tail "NOR") must read as those letters by themselves; artwork, a stamp or a
    Bangla line scores below it: "unclear".
  - **A wrapped line** (`line_under`: the ink line right under the box, no table border between) is tried as the
    line's continuation in box mode too. When the line alone and the line with the one under it support different
    spellings, "unclear" (a wrapped cell or the next row: the look cannot tell); the two-line reading decides only
    when the line alone reads as no candidate or both agree.
  - **Descenders** stay in the crop (`tighten` grows the ink band over its thin rows: a q cut at the baseline reads a
    or c).
  - **Near-unanimous views** (`MOST` 0.6 -> 0.85): a decision needs >= 85% of the views to back the winner by the
    margin. Folder 3's SSC prints the mother's name in bold italic on a guilloche background; 19 of 28 views read one
    letter fewer, which gave a false "differs" (a false FAIL in the end-to-end run). Views that split like this mean
    the print is ambiguous to the recogniser: "unclear" (FLAG), never a verdict.
  - **Dots**: the dot's shape (a solid blob on the baseline between the letters) decides when the recogniser's lead
    is below `DOTS_ALONE` (it drops printed dots, most of all a dot glued to the next word, "MD.NAME"); without a
    measured shape the recogniser alone may confirm a dot (lead >= DOTS_ALONE) but never "no dot". `Look.dot` is
    given only with a decision. A dot-only place is judged by these rules, not by SEG_MIN.
  - **Measured** (box mode, `selftest/closer_eval` cases): real set 130 cases (75 the reference printed, 55 a real or
    constructed difference): 0 false "same", 0 false "differs", 21 unclear (16%: script-font degree certificates,
    bold italic board papers, the two wrapped family-table cells, 4 dots on HSC / SSC / CV pages), Look.dot 18 right,
    0 wrong. Before this pass: 0 / 3 / 29. Synthetic sample of 400 (made-up names, 19 Windows fonts, nine
    degradations): 371 right, 1 false "same" and 1 false "differs", both a LETTER inside a digit string (O for 0 in
    the print; I for 1 in the reference: the recogniser's digit context reads the digit; no government number prints
    letters among its digits), 27 unclear (6.8%, mostly Edwardian Script and bold serif dots), Look.dot 66 right, 0
    wrong. About 2-3 s a look on the CPU. End to end, owner folder 3: the old closer's TIN "differs" and the SSC
    mother's false FAIL are gone; NAMES is FLAG on two bold italic / script board-paper lines that split the views.
  - **Limits.** One recogniser: a print it misreads in nearly every view cannot be verified by it; such looks come out
    "unclear" (FLAG), never a verdict. The built-in Windows OCR (Windows.Media.Ocr, en-US, present on this PC, no
    download) was tried on five such crops as a possible second judge and misread them too; a better second engine
    would need the owner's decision.
- **Names, second pass.** Every page (up to 3, `names.LOOK_PAGES`) of a document that reads the variant is looked at:
  "differs" on any page -> the document prints it; "same" on every page -> a misread (no finding); else "unclear".
  Two pages agreeing confirm the variant only when no look on them says "same". The reference's own page showing the
  variant -> FLAG ("the passport's own read may be the misread"), never FAIL. A "not compared" read (a misparsed
  label) first gets a closer look for the reference itself on its page: printed clearly -> no finding. The language
  look is taken on the language certificate's own pages (`identity._language_closer`; the certificate's name comes
  from a separate row read, so its page is not found by text alone).

### Revision 2.5, part B (28 Oct, documents: the owner's new rules and the by-eye check of the four cases) — changelog

These override sections 5.8-5.9 where they differ.

- **NID back side (owner rule 28 Oct; `doccheck.nid_back_rows`, rule `nid.back_side`).** A smart NID's back carries a
  TD1 machine-readable zone (ICAO 9303): line 1 `I<BGD` + the number (9 digits, `<`, the 10th digit and its check digit
  in the optional data), line 2 the date of birth + check, the sex, the expiry + check, line 3 the name. `td1_reads`
  finds every MRZ in each page's text (the scan and the translation) and keeps a field only when its check digit agrees
  (line 2's sex only when both its date checks agree). Signals against the front and the file's role: the number (3+
  digits off the front's number read on the unit's pages: never an OCR slip), the date of birth (against the front's
  labelled date) and the sex (against the role: father M, mother F, the student the passport's). Two signals or more =
  confirmed: FAIL "the back side of this NID belongs to another person (...)" (+ "in the scan and the translation" when
  both carry it); one: FLAG check by eye; the back's checked number equal to the front's = one card (a front date read
  otherwise is an OCR misread: PASS, `dob_note` in the evidence); no MRZ (an old laminated card): NOTE, never judged.
- **Opening balance dropped (owner rule 28 Oct).** Every row of the bot's `opening_balance_check` (PASS / NOTE / FLAG)
  leaves the bank unit; one NOTE "opening balance not judged (owner rule 28 Oct)" takes its place
  (`doccheck.opening_balance_dropped`). The runner has no opening-balance rule of its own.
- **`bank.seal`: handwritten signatures and round stamps.** Besides the stamp colour (`PC.seal_present` on the one-page
  cut) and the wording, the solvency page image (150 dpi) is searched (`bank.ink_marks`): long rules and underlines
  are taken out, pen strokes crossing the removed signature line are mended; a joined ink blob below the letterhead is
  a signature when it is 1.9+ printed-word heights tall and 2.5+ wide with a fill of 0.03-0.26 AND its longest single
  pen stroke is 1.4+ word heights tall, 2.5+ wide, with its own fill at most 0.27 (printed words and name stamps: at
  most ~1.5 tall or 2.4 wide, or a fill of 0.37+). Blobs closer than one word height are one signature; more than 4: a
  noisy scan, nothing trusted. A round piece of ink with one large inner hole (35%+ of its box) is a stamp's ring: a
  stamp by its shape, whatever its colour. The message says what was found ("stamp and 2 handwritten signatures found
  ...", "handwritten signature found, no stamp, ...", "round stamp (its ring's shape) found ..."). Calibrated on the
  four owner solvency pages (two signatures each: all eight found, no printed block taken) and nine other pages. A
  signature without a stamp is PASS (5.9.2 "seal or signature"); owner question listed.
- **Checker regressions.** The present-only ID card / CV is never cut into checked documents (`split.segment_folder`:
  key `id_card_cv`, a file name saying ID card / CV / resume, or pages mostly typed as one). `doccheck.part_page_refs`
  maps every number of a page list ("page 2, 3", "pages 2 and 3", "page(s) 2", "pages 2-3"), never an amount.
- **Sideways scans (`pagetype.turn_junk_pages`, `engine.read_turned`).** An OCR'd page typed "other" with 8+ words of
  which under 10% are common English / document words is read again turned on the CPU light engine (kept in the case
  cache, `RUNNER-TURN`). Which turns: `engine.text_line_ratio` (no reading: the ink smeared along the rows and along
  the columns, the bars each makes compared) says which way the text lines run: down the page (ratio <= 0.5; the four
  sideways certificates 0.02-0.04) 90 and 270; across it (>= 1.5; every upright text page 1.57 and up: a Bangla or
  handwritten page reads as junk the right way up, CHECK_0001 p14) only 180; otherwise 90, 270, 180. A turned read
  that types as a document and reads clearly better (20%+ common words, +15 points) replaces the page's text
  (`PageRead.turned`). One NOTE names every page read turned
  ("scanned sideways or upside down, turned to be read: <file> p2, 5 - the student should upload them the right way
  up").
- **Academic levels.** An e-Apostille followed by a page that names no level starts a new level unit when the next
  page that names a level names another level than the set's (EAP folder: HSC p1-3, SSC p4-6, no longer HSC p1-5).
  The bot's "not all for one qualification" FLAG (its level words take "Alim" in a madrasah's name for HSC) is
  re-judged by the runner's level reader (the examination the page names: Dakhil = SSC, Alim = HSC): one level on
  every page the bot named gives PASS `academic.level` (`doccheck.level_override`); otherwise the bot's FLAG stays.
- **TIN.** The printed parents' labels are found after OCR item-number noise ("3 3) Mother's Name"). When the printed
  TIN is one or two digits off the QR's (`OCR_DIGITS`), the closer look (`closer.look`, kind "digits", candidates
  [the QR's, the page read's], a 400 dpi render) decides: "same" PASS (the page read misread a digit); "differs" or
  "unclear" FLAG check by eye, never FAIL (the page read and the closer look are one kind of recogniser and can share
  a look-alike misread: an earlier closer-look version read owner folder 3's last digit wrong in every view). A
  difference beyond OCR's reach stays FAIL.
- **Page-type ties (`pagetype.break_ties`, `PageType.tie`).** A page whose two best financial types score the same
  (TIN / income tax / trade licence / employment) is settled by a title in its head, else by its neighbours (a page
  opening with a government letterhead starts a document: the next page's type; otherwise the page before's); the
  winner gets +3 so the split follows (owner folder 9: TIN p5, income tax p6-17).
- **A QR the page check did not find (`doccheck.qr_ladder_rows`).** On a trade licence or a family certificate whose
  bot row says "no QR code" (or an unreadable one), every page showing a QR-like square is read with tin's crop /
  threshold ladder (its deep stages only where QR finder patterns are seen): a decoded QR gives PASS "QR code read on
  page N with a closer look"; nothing decoded leaves the bot's row, which then names the pages looked at. Owner folder
  3's licence QR (a blurred 150 dpi scan) does not decode: whether a QR that is there but cannot be read should FLAG
  is an owner question.
- **The income tax's "notarised" PASS: evidence only.** The bot's `seal_present` PASS gets `evidence.notarised` in
  run.json: the wording, every seal-coloured blob (colour, box, roundness, the green / yellow around it),
  `emblem_like_only` (a round red blob at the top centre with green around it: the government monogram) and the
  office's own seal words ("Seal of Tax Office"). The verdict stays the bot's (owner question pending).
- **Date of birth (`doc.dob_consistency`).** The run's page reads count with the bot's; a read that differs from the
  passport's date gets the closer look (kind "digits", the print against the passport's date written in the print's
  own layout): "same" PASS; otherwise FLAG.
- **A weak page next to the named document.** Inside one folder file, a document whose every page reads at most partly
  like any document and lies next to the document the file's name names joins it (EAP folder: the ECA file's second
  page is no employment certificate).
- **Checks.** `tests/test_fixes25.py` (made-up data, synthetic images, the closer look stubbed).

### Revision 2.5, integration (28 Oct, the closer look as the runner calls it; the four owner cases re-run) — changelog

Versions: `RUNNER_VERSION = "0.2.5"`, `RUNNER_RULES_VERSION = "2026-10-28.r2.5"`, same run schema. `closer.VERSION =
"closer-2"`. Section 7 is rewritten for the closer look; where parts A and B and section 7 differ, section 7 wins.

- **The page search (the closer look with `box=None`, as names.py, tin.py and doccheck.py call it).** Agent C's
  production-mode measure (the whole page, the runner's own candidates) found what the box-mode measure could not:
  18 errors on the real set (16 false "differs", 2 false "same"). Three causes, three changes in `closer.py`:
  - **A word the detector missed** (`_extend_ink`, `EXTEND_GAP` 1.5 line heights, `EXTEND_REACH` 14): the CRAFT
    detector often leaves out a short word after a name (an italic or underlined surname). The look then saw the line
    one word short and "confirmed" an OCR read that had dropped that word (15 of the 16 false "differs"; owner folder
    9's HSC transcript mother, a false FAIL end to end). The found line is now widened over the ink in the core of its
    band on either side, word gaps under 1.5 line heights, never across a vertical rule; horizontal rules are no ink.
    An added word the detector missed is now seen too (it could only have hidden a real difference before).
  - **A page that prints the name in more than one place** (`_locate_all`, `_combine`; `OCCUR_LEAD` 0.5 nats per
    character, `OCCUR_MIN` -1.0, `OCCUR_MAX` 3): the search took the single best-matching line, which can be another
    place than the one the OCR read came from (a family certificate's footer printing the reference while its table
    row prints a short form: a false "same"; an NID card front printing the reference number while the back's MRZ
    carries another: a false "same"). Every line matching about as well as the best is looked at; all places must
    give the same decision (and, for dots, the same dot), else "unclear" ("the page prints the name in more than one
    place and they do not agree").
  - **A wrapped table cell** (`hit["cell"]`): the second line is now found under the matched cell (not under the
    joined table row), and the two-line view stitches the line only up to the end of that cell.
- **A weak read is no confirmed difference** (`DIFFERS_MIN` -1.0 nats per character): "differs" needs the winning
  spelling to read as clear print; a script print the recogniser reads at -1.2 per character (owner folder 9's
  Dakhil certificate, a garbled OCR read of the father's name that won on a two-line view: a false FAIL end to end)
  is "unclear". Cost on the real box-mode set: four real differences in script prints go from "differs" to "unclear".
- **The dot's shape** (`dot_at`): the window reaches back under the left letter's box (up to 0.6 letter heights, never
  before the letter's own place: a script t's crossbar or an N's swash runs on over the dot after it, and the old
  window saw clean paper there); "no dot" only when the paper in the gap is clean, not merely under Otsu's cut (a
  blurred dot: "cannot tell"); a two-line view measures the dot on the line alone. Four of the five wrong dots of
  the synthetic set are gone.
- **The dot rule with one confirmed pattern** (`strict.dot_rule`): when the confirmed dots differ from the
  reference's while another document reads the reference's dots (unconfirmed), a document following the reference
  is PASS and the confirmed different one FLAG; before, the different one was PASS and the followers FLAG
  (CHECK_0001: the employment certificate's father without the dot of the passport, the NID and the TIN).
- **Measured** (`selftest/closer_eval`, TARGETS.md; closer-2, sha256 `02092e485e83`): see section 7 ("Measured accuracy").
- **The reference's own page printing the name twice** (`names._reference_places`): a passport prints the father
  in its Father field and its emergency contact; when one place is settled and the other is not, the settled places
  decide the REFERENCE's dots (they agree and none says otherwise). Never used for a document being judged.
- **The four owner cases, re-run with the final code** (runs `owner_f3_r5`, `owner_f9_r5`, `owner_eap1_r5`,
  `test1_r5`; every line compared with the by-eye truth). The EAP folder (PASS, NOTEs only) and CHECK_0001 (HSC and
  SSC FAIL: no e-Apostille and the mother's real one-letter difference; EMPLOYMENT CERTIFICATE FAIL: the father's
  initial without the dot the passport, the NID and the TIN print; TIN FLAG: the non-government web address; NIDs
  PASS; 4 MISSING) match on every line. Folder 9 matches on every line but one: the HSC transcript's dropped-word
  misread (a false FAIL before) is now settled as the reference, the MOTHER NID's foreign back side FAILs, the bank
  PASSes on two handwritten signatures with the opening balance a NOTE; the Dakhil certificate's ornate script
  father's name (by eye the reference; a false FAIL before) is FLAG "check by eye". Folder 3: FAMILY CERTIFICATE and
  TRADE LICENSE FAIL as expected, the TIN and the birth certificate PASS; the HSC transcript's bold italic student
  name and the SSC certificate's bold italic mother's name (by eye the reference) stay FLAG "check by eye". Those
  three board-paper lines are prints the one recogniser cannot verify either way (its views split); a second
  engine is owner question Q23.
- **Checks.** `tests/test_fixes25i.py` (made-up data: the dot rule with one confirmed pattern, the combination of
  places, the ink widening, a reference in a footer with a short form in a row, a dropped last word).

### Revision 2.6 (28 Oct, after the independent check of the closer look) — changelog

Versions: `RUNNER_VERSION = "0.2.6"`, `RUNNER_RULES_VERSION = "2026-10-28.r2.6"`, same run schema. `closer.VERSION =
"closer-3"`. An independent checker hunted for false PASSes with 82 new made-up cases (37 fonts the synthetic set never
used) and 27 made-up bundles run through `names.evaluate` with the real CPU recogniser. Two-spelling looks never said
"same" on a real difference; two wiring paths and one blind spot let a real difference through as PASS. Section 7 is
updated; where it and this changelog differ, section 7 wins.

- **An added word the look cannot settle is FLAG, never the same name** (`names.evaluate`; Q18 default changed). A
  value read once that adds a word other than an honorific (BEGUM, KHATUN: neither is an honorific) was kept as a
  difference only when the closer look said "differs"; "unclear", or no look, made it the name inside it (PASS).
  Checker bundles W03b (+Khatun in a script print), W21 (+Begum in blackletter) and W22 (+Begum in a script print)
  passed. Now only a look that shows the reference ("same": the added word is not printed with the name) drops the
  word; "differs" keeps a confirmed difference (FAIL when the reference is supported and no affidavit covers it);
  "unclear" or no look keeps it unconfirmed: FLAG "check by eye". W03b, W21, W22: FLAG.
- **A single spelling is verified with the words beside it** (`closer.beside_words`, `RUN_GAP` 2.5 line heights,
  `WORD_BREAK` 1.2, `LABEL_WORDS`, `LEFT_TITLES`, `MRZ_HEAD`). `names` asks for ONE spelling when a label's read is
  too unlike the name to compare (is the name printed on the page at all?) and `identity` when the passport MRZ has no
  name. That look scored only the reference's own letters, so an MST, MD or BEGUM printed right before or after the
  name was never seen: checker cases K03-K05 came out "same" in both modes, and bundles W11 and W24 (a garbled label
  read; the page prints the name + Mst / + BEGUM) PASSed. Now the recogniser's own reading beside the value is read
  in every view: a further word within 2.5 line heights (not a label, a connecting word, a next field's label, a
  number, a colon-ended label, a leading title, or a passport MRZ line's "P<" + issuing-state header) in more than 10%
  of the views makes the look "unclear: the line reads a further word beside the value". K03-K05: unclear in both
  modes; W11, W24: FLAG. Ten single-spelling controls (the page prints the reference after a label) stay "same" in
  box mode (7 of 10 on the page search: 3 unclear, never "differs").
- **The dot probe** (`names._dot_probe`, `names.dot_places`, `DOT_PROBE_MAX` 12, `LOOK_RESERVE` 16). When every read
  shows the same dots, no look was taken, and the recogniser is known to drop printed dots: checker bundle W16 (the
  passport and the NID "MD", the degree printing "MD." that OCR read "MD") PASSed where the dot rule says FAIL. Now,
  when the name has a place for a dot (a word of one or two letters, or an honorific of three or fewer, before the
  last word), each non-board document is looked at with the other dot pattern too, up to 12 looks a run and never
  into the last 16 of the look budget. A pattern the look sees with BOTH judges (the dot's shape measured and the
  recogniser on the same side) joins the dot rule like a read; the result is FLAG at most ("only the closer look's
  dot probe sees the other dot; no OCR read shows it"), never FAIL. W16: FLAG. Board papers are not probed.
- **A stamp's word far after the name is cut away** (`closer.far_tail`). The EAP folder's HSC transcript: the
  notary stamp's "MD", printed far to the right of the student's name, was read by the page OCR as part of the
  value; the look on [the name, the name + MD] was unclear (20 of 28 views), which the Q18 change would have made a
  FLAG. A word another candidate adds AFTER the whole reference, whose first letter lies more than 2.5 line heights
  after the reference's last letter AND that is printed in another ink (a colour more than 40 levels away, under 0.6
  of the name's darkness, or no ink at all), is cut from the line before the views. Both signals are needed: a far
  word in the name's own ink, or a near word in any ink, stays in the look. The EAP stamp look is now "same" (28 of
  28 views). Takeover fix: the WHOLE added word must lie far (its first letter); a near BEGUM whose last letters ran
  past 2.5 line heights was cut in its middle and then read as the reference (a false "same" on a made-up line).
- **Takeover fixes found while re-measuring.**
  - `names.evaluate`: an added-word read kept as an unsettled difference (the Q18 change) no longer counted as "the
    document names the person here", so a misparsed label value on two other pages of the same document (wholly
    unlike the name) became a confirmed difference: a false FAIL at text level (corpus student S174, audit label
    "ocr"). Such a read now drops the misparse like a matching read does; the added word itself stays FLAG.
  - `closer.LABEL_WORDS`: the next field's labels (DATE, BIRTH, ROLL, REG, NO, SEX, ADDRESS, DISTRICT, SESSION,
    GROUP, BOARD, YEAR, GPA, ...; no name word among them) end a single value, so a table row's next column is not
    read as a further word. `closer.MRZ_HEAD`: a passport MRZ line's "P<" + issuing-state header, read glued to the
    surname, is no added word (without it `identity._mrz_by_closer_look` could never settle a noisy MRZ name; a
    made-up MRZ line with an added MD still comes out "differs").
  - `closer.tokens` renamed `rec_tokens` (the S8 banned-helper rule of `test_contract`).
  - `doccheck.td1_reads` (the checker's rules probe: another person's NID back whose date-of-birth check digit is
    misread gave FLAG): the sex letter counts when ONE of the two date check digits beside it agrees (the line is
    anchored digit by digit; a misread date digit is a substitution, not a shift); the date of birth still needs its
    own check digit. The checked number and the sex are then two signals: FAIL.
- **Measured** (closer-3): see section 7 ("Measured accuracy") and TARGETS.md. The checker's own cases with the final
  code: the hunt 0 false "same" and 0 wrong dot in 186 looks (K03-K05 now unclear), the 15 controls 28 "same" of 30;
  the wiring 25 of 25 as expected, W15 the known limit (PASS) and W16 FLAG. Re-run on the final code (closer
  sha256 c8a809a7eb83): `closer_eval\check\hunt_fx4_results.json` (the same figures; plain OCR of the 71
  non-dot crops read the printed variant 65 times, something else 6 times, the reference never), the wiring
  (`wiring_results.json`, 11:26) and the rules probe 32 of 32.
- **The four owner cases, re-run with the final code** (runs `owner_f3_r6`, `owner_f9_r6`, `owner_eap1_r6`,
  `test1_r6`): see the fixer's report; every line compared with the by-eye truth.
- **Checks.** `tests/test_fixes26.py` (made-up data: the words beside a value, a label, a title, a number, a far word,
  an MRZ header; single-spelling looks on rendered lines with an added Mst / BEGUM / MD and controls; the added-word
  rule with a look "same" / "differs" / "unclear", and a misparse beside an unsettled added word; the dot probe (FLAG,
  never FAIL; the shape alone is not enough; board papers not probed); the far-tail cut with a stamp's ink, the
  name's own ink and a near word; a next field's label and an MRZ header beside a single value; a made-up MRZ name
  line with and without an added MD; another person's NID back with a misread date check digit).
- **Takeover (28 Oct, evening): no code change.** The re-measurements and the four owner cases were finished on the
  final code. The owner removed the folder-3 and folder-9 case folders from the Desktop at about 16:24 on 28 Oct,
  after their r6 runs; the one real production case still to run that reads them (R121, folder 9's academic file)
  was run from a copy taken from the owner's original download with the same sha256 as the r6 run's page texts.
  Measured on the way: one CPU detection (CRAFT) on a 400-dpi page commits about 4.3 GB, which ONNX Runtime's
  CPU arena keeps (limit 15).

---------------------------------------------------------------------------------------------------------

## 0. Hard requirements (apply to every module, test and self-test)

| # | Requirement | How it is enforced |
|---|---|---|
| S1 | `C:\Hangeul\BOT` (code, data and venv) and the student document folders (`C:\Hangeul\VERIFIED STUDENT DOCUMENTS` and its siblings) are READ-ONLY. Nothing is ever written, moved or deleted there. Nothing is ever installed into `C:\Hangeul\BOT\.venv` or `C:\Hangeul\DOCCHECK\lab\onnx\.venv-pkg-numpy`. Every command uses ABSOLUTE paths. | `safety.prepare()` (runner) and `safety.install_write_guard()` (GPU worker) install a `sys.addaudithook` write guard. It raises `PermissionError` on any write-mode `open`, `os.remove`, `os.rename`/`os.replace`, `os.mkdir`, `os.rmdir`, `os.truncate`, `os.utime`, `shutil.copyfile` or `shutil.rmtree` whose target is outside `C:\Hangeul\DOCCHECK\lab\private-runner` (S11 lists the one exception). |
| S2 | During a runner run, nothing under `C:\Hangeul\BOT` is opened at all. The runner needs no bot file: its code comes from the worktree and its libraries from the lab venvs. The GPU worker (Task 8) is a separate process and is the only code that reads the bot venv (read-only), and only the venv. | The runner's audit hook raises on any `open`, listing, `sqlite3.connect`, `_winapi.CreateFile` or `os.link` source under `C:\Hangeul\BOT`, read or write, as written or resolved (junctions, links, `\\.\C:\...`, `\\localhost\C$\...`, NTFS stream suffixes); a device-namespace spelling that names no drive (`\\?\Volume{GUID}\...`, `\\?\GLOBALROOT\...`) or any other share is refused outright, and `_winapi.CopyFile2` (shutil.copy2) is checked too (revision 2.2). The GPU worker's hook allows reads under `C:\Hangeul\BOT\.venv` only; `safety.check_input` refuses an input that resolves under the bot (runner and `run_case.py`, before the worker starts). Self-test harnesses that read bot caches run in a separate process before the runner process starts and opt in explicitly (`bot_read_ok`). Native opens (PyMuPDF) raise no audit event: the input is checked before they run. |
| S3 | Every output, scratch copy, render, crop, page-text file and cache goes under `C:\Hangeul\DOCCHECK\lab\private-runner`. | `safety.check_out_dir()`, `engine.load_page_texts()` and `botimport.load()` refuse any other root. `tempfile.tempdir` and `TMP`/`TEMP` point to `...\private-runner\tmp`. |
| S4 | No network. Never contact hangeul.com.bd or bdris. | The audit hook raises on `socket.connect`, `socket.getaddrinfo`, `socket.sendto` and `urllib.Request` (runner and GPU worker). `tin.py`, `names.py`, `birth.py` and `language.py` contain no online lookup; the lab `birth_cert.py`'s `qr_lookup` is not ported. |
| S5 | The runner process is CPU only. The graphics card is used ONLY by the GPU worker `gpuread.py` (Task 8), through `C:\Hangeul\DOCCHECK\lab\onnx\scripts\gpu_guard.py`: the bot's `auto_verify.lock` absent, card-memory cap ≤ 3 GB, a checkpoint between pages. Never DirectML. Never start, stop or restart the bot. Never kill a process you did not start. No git commit or push. | The ONNX sessions use `CPUExecutionProvider` only. The runner process spawns no processes: its guard refuses every process start, `_winapi.CreateProcess` (multiprocessing) included (a child would escape the guard); the GPU worker's records them (`nvidia-smi`). `run_case.py` spawns only `gpuread.py` and the runner, and waits for them. `gpuread.py` takes `gpu_guard.gpu_slot` and `capped_process` before it loads the model, and asserts the reader is on `cuda`. |
| S6 | The bot's checking code is imported from the WORKTREE `src\verify`, never from the live bot. The worktree's bot modules stay byte-identical to commit 07b1f22 (RULES_VERSION 2026-10-21.3). The new rules live in the runner package and go into the bot later, at build time. | `botimport.load()` asserts that every bot module's `__file__` is under `C:\Hangeul\JARVIS\prototype\page-types\src` (the GPU worker uses the same loader). Test `test_bot_unchanged` runs `git diff --quiet 07b1f22 -- src/verify/doc_verifier.py src/verify/page_checks.py src/verify/common.py src/verify/rules.py src/verify/text_cache.py src/verify/field_check.py src/scraper/ocr_validator.py`. |
| S7 | No portal data at all, anywhere in the run. | The runner never imports `auto_verify`, the scraper's portal code or `field_check.check_student`, and never reads `results.json` or portal snapshots (S2 also blocks them). The derived record (section 5.7) carries no portal value. |
| S8 | Names are compared LETTER FOR LETTER (A1): case and whitespace aside, every letter and every dot counts, for every person (student, father, mother, account holder, taxpayer, trade-licence owner, employee, deceased parent, language-certificate candidate). No fuzzy match, no token subset, no honorific dropping. A difference is FAIL only when confirmed and uncovered; otherwise FLAG. A difference is never PASS. | `strict.py` (sections 4.2 and 7). Test `test_no_name_in` (section 4.5) greps the runner package for `name_in(`, `name_tokens(`, `\btokens(`, `same_person(`, `disagreement(` and `name_agreement(` and must find nothing; `SequenceMatcher` is allowed only on lines marked `# label-match only` (label and month-word matching, never a name comparison). |
| S9 | Code, tests and spec files contain no personal data (placeholders only; tests use made-up data). Reports and caches under `private-runner` may contain the student's data, because they are the owner's private reports. Text a builder returns to the orchestrator is masked (`<STUDENT>`, `<FATHER>`, `<NUMBER>` ...). | `selftest/pii_scan.py` (section 10, T0). |
| S10 | Builders do not commit. The orchestrator commits after review. | — |
| S11 | The GPU worker writes only under `private-runner`, except `gpu_guard`'s own dev lock `C:\Hangeul\DOCCHECK\gpu-dev.lock`, which only `gpu_guard` writes. | `safety.install_write_guard(extra_allowed=[gpu_guard.DEV_LOCK], block_bot_opens=False)` in `gpuread.py`. |

---------------------------------------------------------------------------------------------------------

## 1. The commands

**1.1 The runner** (CPU; the app's default reading engine):

```
C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Scripts\python.exe -I -B -m src.verify.runner ^
    "<bundle.pdf | folder>" --program KLP|EAP|BACHELOR|MASTER ^
    --out C:\Hangeul\DOCCHECK\lab\private-runner\runs\<CASE>
```

**1.2 The owner's tests** (GPU pre-read, then the runner; section 5.13):

```
C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Scripts\python.exe -I -B ^
    C:\Hangeul\DOCCHECK\lab\private-runner\run_case.py ^
    "<bundle.pdf | folder>" --program KLP|EAP|BACHELOR|MASTER ^
    --out C:\Hangeul\DOCCHECK\lab\private-runner\runs\<CASE> [--cpu] [--today YYYY-MM-DD] [--wait-s 120] [--show]
```

**Runner options** (all optional except `--program` and `--out`):

| Option | Default | Meaning |
|---|---|---|
| `--program` | required | KLP, EAP, BACHELOR or MASTER (case-insensitive). The owner's choice (A8); nothing reads it from a document. |
| `--out DIR` | required | Must resolve under `C:\Hangeul\DOCCHECK\lab\private-runner`. It must not lie inside the input. It is created if absent, and fixed file names are overwritten. |
| `--today YYYY-MM-DD` | `date.today()` | Sets `dv.TODAY` and the language checker's `today`. |
| `--cache-root DIR` | `...\private-runner\runner-data` | The bot's text and image caches (`verification\text`, `verification\images`), one JSON per case id. Must be under private-runner. |
| `--page-texts JSON` | none | Page texts read in advance (schema `runner-page-texts/1`, section 5.2.1), normally by the GPU worker. Pages it holds are served instead of OCR; pages it lacks are read on the CPU light engine. Must be under private-runner; each file's sha256 and page count must equal the input's, else exit 2. |
| `--reading-note TEXT` | "" | One line shown after `READING:` in the summary (≤ 200 characters, no personal data). `run_case.py` uses it to say that the GPU slot was not available and the CPU engine read the pages. |
| `--no-reread-mrz` | re-read on | Turns off the targeted MRZ re-read (section 5.5). Q5 is answered (in scope); the switch is kept for tests only. |
| `--split-only` | off | Stops after the split. Writes the summary (notices and the page typing), the Excel file and the JSON. |
| `--show` | off | Prints the whole summary (names read off the documents included) to stdout. `run_case.py --show` passes it on. |

Removed in revision 2: `--edge` (unrecognised edge pages always attach), `--prime` (bundle sub-PDFs are always
primed), `--force-split` (STOP is now only the NID-person ambiguity) and `--no-reread-names` (the name re-read is
part of the A1 confirmation model).

**Outputs** in `--out`:

| File | Content |
|---|---|
| `summary.txt` | The chat-ready summary (section 5.11.1). stdout gets only the RESULT line, the headline and where the outputs are (no personal data: an agent may run the case); `--show` prints the whole summary (the owner's own terminal). Error lines name no file or folder (`safety.redact`). |
| `report.xlsx` | The Excel report: sheets Summary, Documents, Pages, Findings (section 5.11.2). |
| `run.json` | Everything (`RunResult`, schema `runner-run/2`, section 5.11.3). |
| `log.txt` | Timings, OCR statistics, warnings, and the traceback on a crash. |
| `docs\` | Cut sub-PDFs, the photo crop, and staged copies of folder files. |
| `work\` | MRZ, name, BRN and solvency-page re-read crops (PNG), one-page solvency cuts, QR stage logs. |
| `page_texts.json`, `run_case.log` | Written by `run_case.py` only (section 5.13). |

**Exit codes** (runner):
- 0: report written, whatever the verdict.
- 3: STOP (the NID-person ambiguity only, section 5.4). The report is written with notices only.
- 2: bad arguments, an unsafe path (an `--out` that names an existing file included), or page texts that do not belong to the input. Nothing is written.
- 1: crash. `log.txt` holds the traceback, and `run.json` holds `{"error": ...}` with whatever finished.

`run_case.py` returns the runner's exit code (2 when it refuses its own arguments or the input; 1 when the input
changed between its start and its end, GPU phase included).

---------------------------------------------------------------------------------------------------------

## 2. Environment

**2.1 Interpreter.** Create a dedicated venv, with nothing installed in it (Task 1):

```
C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe -m venv --without-pip C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner
```

`C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Lib\site-packages\runner.pth` holds exactly these lines, in this order:

```
C:\Hangeul\JARVIS\prototype\page-types
C:\Hangeul\DOCCHECK\lab\onnx\.venv-pkg-numpy\Lib\site-packages
C:\Hangeul\DOCCHECK\lab\private-runner\engine\vendor
C:\Hangeul\DOCCHECK\lab\onnx\pkg
```

- The worktree comes first, so `src` resolves to the worktree. `engine\botcode` (the engine study's copy of the bot) is deliberately left out, and stays on disk unused.
- The numpy venv supplies: Python 3.12, numpy, pymupdf, opencv-python-headless (with `QRCodeDetectorAruco`), pillow and onnxruntime. Its CPU execution provider is the one used.
- `onnx\pkg` supplies `glue_numpy.LightReader`. The models are `C:\Hangeul\DOCCHECK\lab\onnx\models\{craft_mlt_25k,english_g2}.onnx`.
- Never install anything into `C:\Hangeul\BOT\.venv` or the numpy venv.

**2.2 Vendored pure-Python packages** go in `C:\Hangeul\DOCCHECK\lab\private-runner\engine\vendor` (copied, never installed):
- `pyzbar` is already there, with `libzbar-64.dll` and `libiconv.dll` (QR and CODE128 barcodes; `birth.py` and `tin.py` use it).
- **New (Task 1):** `openpyxl` 3.1.5 and `et_xmlfile` 2.0.0. They are copied from `C:\Hangeul\BOT\.venv\Lib\site-packages\{openpyxl,et_xmlfile}`. Copying reads the bot venv; the only writes are under private-runner. Record their sha256 values in `engine\vendor\VENDOR.json`.

**2.3 Importing the bot code.** Every module gets the bot's modules through `botimport.load(cache_root)` (section 4.3). Before importing `src.verify.*`, it puts a stub `src.config` in `sys.modules`:
- The stub needs no pydantic and never reads the bot's `.env`.
- `docs_root()` and `konyang_root()` return `<cache_root>\no-docs-root`.
- `verification_dir()` returns `<cache_root>\verification`.

`load()` then asserts that `dv.REPORT_DIR`, `TC.TEXT_DIR`, `TC.IMAGES_DIR`, `PC.APOSTILLE_SUBJECTS` and `dv.DOCS_ROOTS` all resolve under `cache_root`. The runner never imports `src.verify.auto_verify`, which rebinds paths.

**2.4 Running the tests:**

```
set PY=C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Scripts\python.exe
%PY% -I -B -m unittest discover -s C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\tests -t C:\Hangeul\JARVIS\prototype\page-types
```

Self-tests are run as `%PY% -I -B -m src.verify.runner.selftest.<name> [args]` (section 10). Every unit test runs in
the runner venv, including `test_gpuread` (which fakes the reader and the guard; no torch).

**2.5 The GPU worker (Task 8).** It runs in the bot's own venv, read-only, as a script path (with `-I`, neither the
script's folder nor the current folder is on `sys.path`):

```
C:\Hangeul\BOT\.venv\Scripts\python.exe -I -B C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\gpuread.py ^
    "<bundle.pdf | folder>" C:\Hangeul\DOCCHECK\lab\private-runner\runs\<CASE>\page_texts.json [--wait-s 120] [--slot-s 90]
```

- `gpuread.py` puts `C:\Hangeul\JARVIS\prototype\page-types` first on `sys.path` and gets the bot code through `botimport.load(C:\Hangeul\DOCCHECK\lab\private-runner\gpu-data)`: the worktree's `doc_verifier` (byte-identical to the live bot's 07b1f22), the stub `src.config`, every bot path under `gpu-data`. It asserts `src` comes from the worktree.
- EasyOCR, torch and CUDA come from the bot venv; the reader is the bot's own `dv._ocr_reader()` (`easyocr.Reader(["en"], gpu=True)`), driven through the bot's `dv.read_pages` with its `_ocr_image` rotation retries. This is the approach of `test1\tools\read_gpu_pages.py`, which read CHECK_0001 (14 pages) in 84 s under the guard at a 2.92 GB peak.
- `gpu_guard` is loaded by path from `C:\Hangeul\DOCCHECK\lab\onnx\scripts\gpu_guard.py`.

---------------------------------------------------------------------------------------------------------

## 3. Module layout and ownership

All new code lives in `C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\`, plus `run_case.py` (Task 8) in
`C:\Hangeul\DOCCHECK\lab\private-runner\` and the vendored packages (Task 1). Nothing else in the worktree is changed.

```
src/verify/runner/
  __init__.py        Step 0   package doc + re-export of model.RUNNER_VERSION
  model.py           Step 0   the shared data contract (verbatim, section 4.1)
  strict.py          Step 0   the one strict name comparison + dot rule (verbatim, section 4.2)
  botimport.py       Step 0   bot code from the worktree with the stub config (verbatim, section 4.3)
  __main__.py        Task 6   CLI -> pipeline.run(); exit codes
  pipeline.py        Task 6   orchestration of the stages (section 5)
  verdict.py         Task 6   document / overall verdicts, MISSING lines, ORDER lifting, NOT JUDGED
  report.py          Task 6   summary.txt, report.xlsx, run.json
  safety.py          Task 1   audit hook, path guards, network block, input snapshots, write guard for the GPU worker
  engine.py          Task 1   light ONNX engine adapter (port of engine\light_engine.py) + page-texts serving
  ingest.py          Task 1   input -> Source; folder name classification; staging
  pagetype.py        Task 2   per-page features (port of order_split page features)
  split.py           Task 2   order-aware alignment, documents, roles, notices, STOP, missing, cut plan
  identity.py        Task 3   passport MRZ (+ joined lines, + targeted re-read), printed name, parents, Identity
  names.py           Task 3   strict cross-document names, confirmation, dot rule, affidavit (port of names_affidavit.py)
  bank.py            Task 4   solvency holder (port of bank_holder.py) + bank rules
  tin.py             Task 4   TIN QR ladder + strict QR-vs-print + web address + tax office (port of tin_qr.py)
  financial.py       Task 4   taxpayer / owner / employee + the strict chain + tax office check (port of fin_names.py)
  doccheck.py        Task 5   per-document checks: bot checkers + adapters + post-processing
  language.py        Task 5   IELTS / TOEFL / TOPIK offline checks (port of language.py, trimmed)
  birth.py           Task 7   birth certificate: format, notary, translation, QR, boxed DOB (port of lab birth_cert)
  gpuread.py         Task 8   GPU pre-read worker (bot venv, separate process)
  tests/__init__.py  Step 0
  tests/test_contract.py                                             Step 0 (model/strict/botimport, no_name_in)
  tests/test_safety.py, test_engine.py, test_ingest.py              Task 1
  tests/test_pagetype.py, test_split.py                              Task 2
  tests/test_identity.py, test_names.py                              Task 3
  tests/test_bank.py, test_tin.py, test_financial.py                 Task 4
  tests/test_doccheck.py, test_language.py                           Task 5
  tests/test_verdict.py, test_report.py, test_pipeline.py            Task 6
  tests/test_birth.py                                                Task 7
  tests/test_gpuread.py                                              Task 8
  selftest/__init__.py, selftest/corpus.py                           Step 0
  selftest/venv_check.py, selftest/pii_scan.py                       Task 1
  selftest/sim_split.py                                              Task 2
  selftest/sim_names.py, selftest/mrz_reread.py                      Task 3
  selftest/sim_money.py                                              Task 4
  selftest/folder_parity.py                                          Task 5
  selftest/check0001.py, selftest/bundle_parity.py, selftest/cases.py Task 6
  selftest/birth_fixtures.py                                         Task 7
C:\Hangeul\DOCCHECK\lab\private-runner\run_case.py                   Task 8
```

Self-test output (private, counts plus per-case files) goes to `C:\Hangeul\DOCCHECK\lab\private-runner\selftest\<name>\`.

---------------------------------------------------------------------------------------------------------

## 4. Shared contract (Step 0, written verbatim before the parallel tasks start)

### 4.1 `model.py`

```python
"""Shared data contract of the document-check test runner (RUNNER_SPEC section 4.1, revision 2).
Standard library only, no I/O, no personal data. Changed only by agreement of every task."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RUNNER_VERSION = "0.2.5"
BASE_RULES_VERSION = "2026-10-21.3"      # the bot rules in the worktree's src/verify (commit 07b1f22)
RUNNER_RULES_VERSION = "2026-10-28.r2.5"   # + the owner's 21-28 Oct decisions (A1-A8, the 28 Oct rules), runner only
RUN_SCHEMA = "runner-run/2"
PAGE_TEXTS_SCHEMA = "runner-page-texts/1"   # gpuread.py writes it, engine.load_page_texts reads it (5.2.1)

# Finding levels. NOTE counts as PASS; ERROR shows as FLAG ("could not be checked: ...").
PASS, NOTE, FLAG, FAIL, ERROR = "PASS", "NOTE", "FLAG", "FAIL", "ERROR"
PRESENT = "PRESENT"          # presence-only line: ID card / CV, income tax outside Master's
ORDER = "ORDER"              # a "fix these pages" finding; verdict.merge lifts it into a Notice
# Document-line and overall labels: the owner's words, plus the two the runner needs.
NOT_JUDGED = "NOT JUDGED"    # a unit a blocking ORDER notice touches: fix the pages, then check again
MISSING = "MISSING"          # a "X MISSING" line
STOP = "STOP"                # only the NID-person ambiguity (section 5.4)
OVERALL = (STOP, FAIL, FLAG, PASS)
RANK = {PASS: 0, NOTE: 0, PRESENT: 0, ORDER: 0, FLAG: 1, NOT_JUDGED: 1, ERROR: 2, FAIL: 3}
PROGRAMS = ("KLP", "EAP", "BACHELOR", "MASTER")
# the portal-shaped keys the bot's checkers read (== doc_verifier.PORTAL_KEYS_READ, tested)
PORTAL_KEYS = ("Full Name", "Father", "Mother", "DOB", "Gender", "Passport No", "Passport Expiry",
               "Sponsor", "SSC GPA", "HSC GPA", "Program")
# checks added from test 1 (A6 j): FLAG only, listed in the report as "new, keep?" (Q14)
NEW_CHECK_RULES = ("tin.web_address", "income_tax.tax_office")


@dataclass
class Finding:
    verdict: str                     # PASS | NOTE | FLAG | FAIL | ERROR | PRESENT | ORDER
    rule: str                        # rule id (RUNNER_SPEC section 6), e.g. "bank.holder_parent"
    message: str                     # owner-facing text; may carry the student's data (report only)
    source: str                      # "bot:check_bank", "bot:page_checks.qr_check", "runner:bank" ...
    pages: Tuple[int, ...] = ()      # 1-based pages of the input (bundle page, or page of the folder file)
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceFile:
    path: Path                       # the input file (read-only)
    name: str
    is_image: bool
    n_pages: int
    size: int
    mtime: float
    key_by_name: Optional[str] = None   # folder mode: bot key from ingest.classify_name()


@dataclass
class Source:
    mode: str                        # "bundle" | "folder"
    root: Path                       # the bundle file or the folder
    files: List[SourceFile]
    case_id: str                     # "B-<sha1 of bundle bytes>[:16]" | "F-<sha1 of name|size|mtime list>[:16]"
    # folder mode, revision 2.3: files the runner cannot check, (file name, why): "not a PDF or image" (an .odg,
    # a .docx ...) or "could not be opened" (a broken or password-protected PDF or image). Listed in the report
    # as "Not checked (...)"; never a document, never a slot.
    not_checked: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class PageRead:
    index: int                       # 0-based position in the run's page list
    file: str                        # SourceFile.name
    file_page: int                   # 0-based page inside that file
    text: str                        # the bot's read_pages text (200 dpi; text layer when >= 40 chars)
    kind: str                        # "layer" | "ocr" | "image"
    chars: int
    sideways: bool
    seconds: float
    image_only: bool                 # no text layer and one embedded image covering >= 50% of the page
    engine: str = ""                 # "layer" | "gpu" (served from --page-texts) | "cpu" (runner light engine)


@dataclass
class PageType:
    index: int
    best: str                        # fine type (prototype labels, "photo" or "other")
    score: float
    scores: Dict[str, float]
    translation: bool                # letterhead translation cue (order study)
    level: Optional[str]             # order_split._LEVELS names: "degree" | "diploma" | "HSC" | "SSC" | None
    kind: str                        # academic: "apostille" | "certificate" | "transcript"; bank: "solvency" |
                                     # "statement"; else ""
    nid_numbers: List[str]
    birth_dates: List[str]           # ISO dates read after a birth label
    holder: List[str]                # name parts after a "Name" label (never Father's/Mother's/Husband's)


@dataclass
class DocUnit:
    uid: str                         # "D01", "D02", ... in page order
    key: str                         # bot key (rules.FILE_PATTERNS keys), "nid_unassigned" or "unrecognised"
    fine: str                        # finer type: "tin_certificate", "employment_certificate", "trade_license", ...
    title: str                       # report title: "FATHER NID", "TIN CERTIFICATE", "ACADEMIC HSC" ...
    pages: List[int]                 # 0-based PageRead indexes, in order
    role: str = ""                   # "father" | "mother" | "student" | ""
    role_how: str = ""               # "name" | "name-partial" | "position" | "file-name" | ""
    misplaced: bool = False
    level: str = ""                  # academic unit: "HSC" | "SSC" | "degree" | "diploma"; else ""
    sub: List[Tuple[int, int, str]] = field(default_factory=list)   # folder file holding several parts (0-based, incl.)
    path: Optional[Path] = None      # file the bot checkers read: cut sub-PDF, staged copy or photo crop
    source_file: str = ""            # folder mode: the input file name
    notes: List[Finding] = field(default_factory=list)              # split-level findings about this unit
    # folder mode, revision 2.3: "p4-6" when the unit is one of several documents cut out of its file (the
    # pages inside that file); "" when the unit is the whole file (or in a bundle)
    part: str = ""


@dataclass
class Notice:
    kind: str                        # "ORDER" | "HINT" | "NOTE" | "STOP"
    pages: Tuple[int, int]           # 1-based, inclusive
    message: str
    blocks: bool = False             # ORDER only: the units on these pages are NOT JUDGED until the pages are fixed
    units: Tuple[str, ...] = ()      # uids the notice touches


@dataclass
class SplitResult:
    units: List[DocUnit]
    notices: List[Notice]
    missing: List[str]               # slot ids (split.SLOTS) with no document
    stop: bool
    maybe: Dict[str, str] = field(default_factory=dict)   # slot id -> uid of a death certificate whose person is unknown


@dataclass
class NameRef:
    parts: Tuple[str, ...] = ()      # strict.words() of the name (titles dropped; initials, MD/MST kept)
    written: str = ""                # as printed, dots kept: the dot reference (A2); "" when only a code read it
    source: str = ""                 # "passport MRZ" | "passport MRZ re-read" | "passport printed" | "birth certificate" |
                                     # "passport field" | "father NID" | "mother NID" | ""
    unit: str = ""                   # uid of the document it was read from
    pages: Tuple[int, ...] = ()      # 0-based run pages it was read on
    confirmed: bool = False          # A1: a second independent read agrees (MRZ, another page or document, re-read)
    truncated: bool = False          # MRZ name field full (39 characters): the name may be cut


@dataclass
class Identity:
    student: NameRef                 # parts: MRZ letters; written: the passport's printed name (dot reference)
    father: NameRef                  # birth certificate (A3); else passport field; else the role-confirmed NID
    mother: NameRef
    father_nid: NameRef = field(default_factory=NameRef)   # the name on the father's role-confirmed NID (A7)
    mother_nid: NameRef = field(default_factory=NameRef)
    surname: str = ""
    given_names: str = ""
    name_trusted: bool = False       # MRZ line 1 well formed
    passport_no: str = ""
    passport_no_ok: bool = False     # MRZ check digit agrees
    printed_passport_no: str = ""
    dob: Optional[dt.date] = None
    dob_source: str = ""             # "MRZ" | "MRZ re-read" | "passport label" | "birth certificate" | ""
    dob_trusted: bool = False        # only an MRZ date trusted on doc_verifier._trusted terms
    expiry: Optional[dt.date] = None
    expiry_source: str = ""          # "MRZ" | "MRZ re-read" | "passport label" | "scan date" | ""
    expiry_trusted: bool = False     # MRZ (trusted) or the "Date of Expiry" label; a "scan date" never
    sex: str = ""
    name_verified: bool = False      # language.passport_name_verified(): every MRZ name word is printed
    mrz: Dict[str, Any] = field(default_factory=dict)   # the best MRZ read (doc_verifier.passport_mrz keys)
    notes: List[Finding] = field(default_factory=list)


@dataclass
class DerivedRecord:
    program: str
    today: dt.date
    identity: Identity

    def portal_view(self) -> Dict[str, str]:
        """What the bot's checkers get instead of the portal record (RUNNER_SPEC section 5.7).
        Every name, date, number, sponsor and GPA field is EMPTY on purpose. In the bot those
        fields drive loose name matches (name_in), self-confirming trust escapes (DOB, expiry)
        or tautologies (the passport number). The runner's strict rules use `identity`."""
        view = {k: "" for k in PORTAL_KEYS}
        view["Program"] = self.program
        return view


@dataclass
class MachineRead:
    """A name read by a code, not by OCR (A1: a machine read confirms letters, never dots)."""
    unit: str                        # uid of the document carrying the code
    person: str                      # "student" | "father" | "mother"
    name: str                        # as decoded
    source: str                      # "board QR" | "birth QR"
    page: int                        # 0-based run page


@dataclass
class BirthFacts:
    """What birth.read_birth_certificate() reads off one birth-certificate unit (RUNNER_SPEC 5.12)."""
    pages: Tuple[int, ...] = ()      # 0-based run pages of the unit; the lookup key is tuple(unit.pages)
    format: str = "unknown"          # "bangla_original" | "bilingual_qr" | "english_bdris" | "english_translation" |
                                     # "unknown"
    variant: str = ""                # "everify" | "manual_english" | ""
    page_kinds: List[str] = field(default_factory=list)      # birth.page_kind() per page, in page order
    english_pages: List[int] = field(default_factory=list)   # pages names and fields are read from (0-based run pages)
    has_translation: bool = False
    notarised: bool = False
    notary_pages: List[int] = field(default_factory=list)
    seal_only_pages: List[int] = field(default_factory=list) # a seal colour but no notary wording
    dob: Optional[dt.date] = None    # the certificate's date of birth (None when unread or unclear)
    dob_trusted: bool = False        # two independent readings agree (boxes or label, AND the "In words" line)
    dob_source: str = ""             # "boxes+words" | "label+words" | "boxes" | "label" | "words" | ""
    dob_readings: List[Tuple[str, str, int]] = field(default_factory=list)   # (source, ISO date, page)
    brn: str = ""                    # birth registration number as printed (17 digits)
    brn_confirmed: bool = False      # read alike twice (page text and zoomed re-read, or two pages)
    issue_date: Optional[dt.date] = None          # NEVER the date of birth
    registration_date: Optional[dt.date] = None   # NEVER the date of birth
    qr: Dict[str, Any] = field(default_factory=dict)   # decode result (host, path, key_len, key_bytes, kind ...), no images
    qr_status: str = "absent"        # "decoded" | "undecoded" | "absent"
    qr_name: str = ""                # a name the payload carries ("" for the opaque bdris key)
    qr_brn: str = ""                 # a BRN the payload or the CODE128 barcode carries
    qr_dob: Optional[dt.date] = None # a date of birth the payload carries
    notes: List[str] = field(default_factory=list)


@dataclass
class AffidavitInfo:
    present: bool = False
    units: List[str] = field(default_factory=list)                  # uids of affidavit documents
    groups: List[Tuple[str, List[str]]] = field(default_factory=list)   # (whose, same-person spellings AS WRITTEN)
    documents: List[str] = field(default_factory=list)              # kinds it names: nid, birth, academic ...


@dataclass
class DocResult:
    unit: DocUnit
    findings: List[Finding]
    verdict: str                     # PASS | FLAG | FAIL | PRESENT | NOT JUDGED
    reasons: List[str]               # FAIL, then ERROR, then FLAG messages (for the summary line)


@dataclass
class RunResult:
    schema: str                      # RUN_SCHEMA
    runner_version: str
    base_rules_version: str
    runner_rules_version: str
    case_id: str
    mode: str
    input: str
    program: str
    today: str
    reading: Dict[str, Any]          # {"pages_by_engine": {"gpu": n, "cpu": n, "layer": n}, "note": str, "page_texts": str}
    pages: List[PageRead]
    types: List[PageType]
    split: SplitResult
    identity: Optional[Identity]
    births: List[BirthFacts]
    docs: List[DocResult]
    cross: List[Finding]             # findings behind the NAMES and FINANCIAL CHAIN lines + bundle-level findings
    notices: List[Notice]            # split notices + lifted ORDER findings, in page order
    missing_lines: List[Tuple[str, str]]   # (MISSING | FLAG, text)
    overall: str                     # STOP | FAIL | FLAG | PASS
    headline: str
    timings: Dict[str, float]
    safety: Dict[str, Any]
    # folder mode, revision 2.3: Source.not_checked, (file name, why), one "Not checked (...)" summary line each
    not_checked: List[Tuple[str, str]] = field(default_factory=list)
```

### 4.2 `strict.py`

```python
"""The runner's one strict 'same written name' comparison (RUNNER_SPEC section 7; owner decisions A1 and
A2 of 27 Oct 2026). Letter for letter: case and whitespace do not count, every LETTER and every DOT does.
EQUAL is the only match. Every other kind only DESCRIBES a difference (for the message); none of them is
ever a match. Whether a difference is FAIL or FLAG is judge() (A1) and, for dots, dot_rule() (A2).
Standard library only. No personal data."""
from __future__ import annotations

import itertools
import re
import unicodedata
from typing import Dict, Optional, Sequence, Tuple, Union

TITLES = ("LATE", "MRS", "MR", "MISS", "MS", "DR")    # dropped only at the START of a name (Q16)
KINDS = ("EQUAL", "DOTS", "OCR", "ORDER", "SUBSET", "LETTERS", "DIFFERENT", "EMPTY")
KIND_RANK = {k: i for i, k in enumerate(KINDS)}
OCR_KINDS = frozenset({"DOTS", "OCR"})     # a misread can explain these (message wording only, never a pass)
Name = Union[str, Sequence[str]]
_TITLE_RE = re.compile(r"^[^A-Z0-9|]*(?:(?:%s)(?![A-Z0-9|])[^A-Z0-9|]*)+" % "|".join(TITLES))
_FOLD = str.maketrans({"I": "1", "L": "1", "|": "1", "O": "0"})    # OCR look-alikes: I/l/1/| and O/0
_MAX_PERM = 6


def _upper(name: Name) -> str:
    if not isinstance(name, str):
        name = " ".join(name or ())
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().upper()
    return _TITLE_RE.sub("", s)


def words(name: Name) -> Tuple[str, ...]:
    """The name's words: runs of letters (and of the OCR look-alikes 0-9 and '|'), cut at spaces, dots and
    every other mark. Leading TITLES are dropped; initials, two-letter words and MD/MST are kept."""
    return tuple(re.findall(r"[A-Z0-9|]+", _upper(name)))


parts = words      # the revision-1 name, kept for callers (split role scoring)


def form(name: Name) -> str:
    """What is compared: upper case, letters (and look-alikes) and dots only. Spaces and every other mark
    are removed; a run of dots is one dot; a dot before the first letter or after the last one is
    punctuation (Q16)."""
    s = re.sub(r"[^A-Z0-9|.]", "", _upper(name))
    return re.sub(r"\.+", ".", s).strip(".")


def letters(name: Name) -> str:
    """form() without its dots."""
    return form(name).replace(".", "")


def dot_pattern(name: Name) -> Tuple[int, ...]:
    """Where the dots stand: for each dot, how many letters come before it ("MD. X" -> (2,))."""
    out, n = [], 0
    for ch in form(name):
        if ch == ".":
            out.append(n)
        else:
            n += 1
    return tuple(out)


def _perm_equal(ws: Sequence[str], target: str) -> bool:
    if not ws or len(ws) > _MAX_PERM or sum(map(len, ws)) != len(target):
        return False
    return any("".join(p) == target for p in itertools.permutations(ws))


def _edit(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def compare(a: Name, b: Name, any_order: bool = False, dots: bool = True) -> str:
    """One of KINDS.
    dots=False compares letters only: the who-is-this checks, whose dots go to dot_rule() through names.
    any_order=True only where one side is a machine read (MRZ, QR): surname-first and without dots. The
    words may then stand in any order and dots are not compared."""
    la, lb = letters(a), letters(b)
    if not la or not lb:
        return "EMPTY"
    if la == lb:
        return "EQUAL" if (any_order or not dots or form(a) == form(b)) else "DOTS"
    wa, wb = words(a), words(b)
    if _perm_equal(wa, lb) or _perm_equal(wb, la):
        return "EQUAL" if any_order else "ORDER"
    if la.translate(_FOLD) == lb.translate(_FOLD):
        return "OCR"
    if set(wa) < set(wb) or set(wb) < set(wa):
        return "SUBSET"
    if _edit(la, lb) <= 2:
        return "LETTERS"
    return "DIFFERENT"


def best_match(name: Name, candidates: Sequence[Name], any_order: bool = False,
               dots: bool = False) -> Tuple[int, str]:
    """(index, kind) of the candidate closest to `name` by KIND_RANK; (-1, "EMPTY") when there is none."""
    best = (-1, "EMPTY")
    for i, c in enumerate(candidates):
        k = compare(name, c, any_order=any_order, dots=dots)
        if best[0] < 0 or KIND_RANK[k] < KIND_RANK[best[1]]:
            best = (i, k)
    return best


def find_run(name: Name, text: str, any_order: bool = False) -> bool:
    """True when a contiguous run of the text's words spells the name letter for letter (dots and spaces
    aside; with any_order, the words of the run in any order)."""
    target = letters(name)
    if not target:
        return False
    ws = re.findall(r"[A-Z0-9|]+", _upper(text))
    n = len(words(name)) + 2
    for i in range(len(ws)):
        acc = ""
        for j in range(i, min(len(ws), i + n)):
            acc += ws[j]
            if acc == target or (any_order and _perm_equal(ws[i:j + 1], target)):
                return True
            if len(acc) >= len(target):
                break
    return False


def judge(kind: str, covered: bool, confirmed: bool) -> str:
    """A1, the one policy for every person check (who-is-this and same-on-every-document):
    EQUAL -> PASS; EMPTY (a side unread) -> FLAG; covered by an affidavit -> NOTE; a CONFIRMED
    difference -> FAIL; any other difference -> FLAG ("check by eye, page N"). `confirmed` means BOTH
    spellings are confirmed (RUNNER_SPEC section 7): each is a machine read, or two independent reads
    agree on it. A dot-only difference (DOTS) is never judged here: dot_rule() decides it."""
    if kind == "DOTS":
        raise ValueError("a dot-only difference is judged by dot_rule()")
    if kind == "EQUAL":
        return "PASS"
    if kind == "EMPTY":
        return "FLAG"
    if covered:
        return "NOTE"
    return "FAIL" if confirmed else "FLAG"


def dot_rule(reference: Optional[Name],
             others: Sequence[Tuple[str, Sequence[Name], bool]]) -> Dict[str, str]:
    """A2, for one person. `reference`: the passport's PRINTED name (student) or the birth certificate (a
    parent); None or "" when it could not be read letter for letter. `others`: (doc id, the spellings read
    on that document, confirmed) for every OTHER document whose letters equal the reference's; confirmed =
    two independent reads agree on one dot pattern and none disagrees. Returns doc id -> PASS | FLAG | FAIL:
      * one dot pattern on every other document -> PASS for all, whether or not the reference has it;
      * confirmed spellings with different dots (mixed) -> a document following the reference: PASS; a
        document whose confirmed dots differ from the reference: FAIL; any other: FLAG;
      * the confirmed spellings agree, an unconfirmed read does not -> that document FLAG, the others PASS;
      * nothing confirmed -> a document whose reads all follow the reference: PASS; the others FLAG.
    The caller turns a FAIL into NOTE when an affidavit covers the two spellings."""
    cands = {doc: {dot_pattern(n) for n in names} for doc, names, _ in others}
    every = set().union(*cands.values()) if cands else set()
    sure = {next(iter(cands[doc])) for doc, _, ok in others if ok and len(cands[doc]) == 1}
    ref = dot_pattern(reference) if reference and letters(reference) else None
    out: Dict[str, str] = {}
    for doc, _, ok in others:
        c = cands[doc]
        firm = ok and len(c) == 1
        if len(every) <= 1:
            out[doc] = "PASS"
        elif len(sure) >= 2:
            if ref is not None and c == {ref}:
                out[doc] = "PASS"
            else:
                out[doc] = "FAIL" if (firm and ref is not None) else "FLAG"
        elif sure:
            out[doc] = "PASS" if c <= sure else "FLAG"
        else:
            out[doc] = "PASS" if (ref is not None and c == {ref}) else "FLAG"
    return out
```

### 4.3 `botimport.py` (unchanged from revision 1)

```python
"""The bot's checking code, imported from THIS worktree with a stub src.config (RUNNER_SPEC 2.3)."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

WORKTREE = Path(r"C:\Hangeul\JARVIS\prototype\page-types")
PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
DEFAULT_CACHE_ROOT = PRIVATE / "runner-data"
_loaded = None


def load(cache_root: Path = DEFAULT_CACHE_ROOT) -> SimpleNamespace:
    """SimpleNamespace(dv, PC, TC, R, C, FC, OV, P, cache_root). Idempotent. A second call with another
    cache_root raises. Every folder the bot code can write to lies under cache_root, which must be
    under PRIVATE."""
    global _loaded
    cache_root = Path(cache_root).resolve()
    if _loaded is not None:
        if _loaded.cache_root != cache_root:
            raise RuntimeError("bot code already loaded with another cache root")
        return _loaded
    if not cache_root.is_relative_to(PRIVATE):
        raise RuntimeError(f"cache root must be under {PRIVATE}")
    sys.dont_write_bytecode = True
    import src
    if not Path(src.__file__).resolve().is_relative_to(WORKTREE):
        raise RuntimeError(f"src is imported from {src.__file__}, not from the worktree")
    old = sys.modules.get("src.config")
    if old is not None and not getattr(old, "_RUNNER_STUB", False):
        raise RuntimeError("the real src.config is already imported")
    stub = types.ModuleType("src.config")
    stub._RUNNER_STUB = True

    class _Settings:
        def docs_root(self) -> Path:
            return cache_root / "no-docs-root"

        def konyang_root(self) -> Path:
            return cache_root / "no-docs-root"

        def verification_dir(self) -> Path:
            return cache_root / "verification"

    stub.settings = _Settings()
    sys.modules["src.config"] = stub
    src.config = stub
    from src.verify import common as C
    from src.verify import doc_verifier as dv
    from src.verify import field_check as FC
    from src.verify import page_checks as PC
    from src.verify import prototype_page_types as P
    from src.verify import rules as R
    from src.verify import text_cache as TC
    from src.scraper import ocr_validator as OV
    for p in (dv.REPORT_DIR, TC.TEXT_DIR, TC.IMAGES_DIR, PC.APOSTILLE_SUBJECTS, *dv.DOCS_ROOTS):
        if not Path(p).resolve().is_relative_to(cache_root):
            raise RuntimeError(f"bot path outside the cache root: {p}")
    for m in (C, dv, FC, PC, P, R, TC, OV):
        if not Path(m.__file__).resolve().is_relative_to(WORKTREE / "src"):
            raise RuntimeError(f"{m.__name__} not imported from the worktree: {m.__file__}")
    _loaded = SimpleNamespace(dv=dv, PC=PC, TC=TC, R=R, C=C, FC=FC, OV=OV, P=P, cache_root=cache_root)
    return _loaded
```

### 4.4 `selftest/corpus.py` (Step 0; measurement only, read-only)

It mirrors `prototype_page_types.load()` and walks the same folders in the same order, so it yields the same sids.

```python
@dataclass
class CorpusFile:
    name: str            # kept in memory only, never printed
    path: Path           # the real file (read-only)
    key: Optional[str]   # prototype classify_name()
    truth: Optional[str] # prototype truth_label()
    image: bool
    pages: List[str]     # prototype _pages_for() (bot cache text, 200 dpi pages)
    blob: bool

@dataclass
class CorpusStudent:
    sid: str             # "S000" ...
    program: str         # KLP | EAP | BACHELOR | MASTER (prototype mapping)
    folder: Path         # the real student folder (read-only)
    cache_json: Path     # C:\Hangeul\BOT\data\verification\text\<id>.json (read-only)
    images_json: Path    # ...\images\<id>.json (read-only; may not exist)
    files: List[CorpusFile]

def load_corpus() -> List[CorpusStudent]
def halves(students) -> Tuple[List[CorpusStudent], List[CorpusStudent]]   # (tune, heldout), seed 20261027
def check_same_as_prototype() -> None   # sids and typed-file counts equal prototype load()
```

It never prints a folder name, file name, cache file name or any text. Callers print counts and sids only.

### 4.5 `tests/test_contract.py` (Step 0)

- `PORTAL_KEYS == dv.PORTAL_KEYS_READ`, and `portal_view()` is all empty except Program. `RANK` covers every level; `OVERALL` is STOP, FAIL, FLAG, PASS.
- `strict.form` / `letters` / `dot_pattern`: case and spaces do not count ("md.  sample" == "MD.SAMPLE"); a run of dots is one; a leading or trailing dot is dropped; leading "LATE"/"MR."/"MRS" are dropped, "MST" and "MD" are kept; hyphens and commas are removed.
- `strict.compare` kind table (all names made-up):
  - EQUAL: "MD. SAMPLE ONE" vs "md.sample one"; "A.SAMPLE" vs "A. SAMPLE" (a glued initial with its dot, A6 e); "SAMPLE ONE" vs the MRZ-style ("ONE", "SAMPLE") with `any_order`; "ABC DEF" vs "ABCDEF" (whitespace does not count).
  - DOTS: "MD. SAMPLE" vs "MD SAMPLE"; "ASAMPLE" vs "A. SAMPLE".
  - EQUAL with `dots=False` for the two DOTS pairs.
  - OCR: "SAMPLE" vs "SAMPIE" and vs "SAMP1E"; "SAMPLE ONE" vs "SAMPLE 0NE".
  - ORDER: "SAMPLE ONE" vs "ONE SAMPLE" (no `any_order`).
  - SUBSET: "MD SAMPLE ONE" vs "SAMPLE ONE" (a dropped MD is a difference, A1).
  - LETTERS: "SAMPLE" vs "SAMPELE" (one extra vowel).
  - DIFFERENT: "MOHAMMAD SAMPLE" vs "MD SAMPLE" (no honorific equivalence, A1); two unrelated names.
  - EMPTY when either side has no letters.
- `strict.judge` truth table: EQUAL → PASS; EMPTY → FLAG; covered → NOTE; confirmed → FAIL for OCR, ORDER, SUBSET, LETTERS and DIFFERENT; unconfirmed → FLAG; DOTS raises `ValueError`.
- `strict.dot_rule` — the owner's cases (A2), every spelling confirmed:
  - reference "MD. X", others all "MD X" → all PASS;
  - reference "MD. X", others "MD. X" and "MD X" → the "MD X" document FAIL, the other PASS;
  - reference "MD X", others mixed → the "MD. X" document FAIL;
  - reference "MD X", others all "MD X" → PASS;
  - the assumed symmetric case (Q13): reference "MD X", others all "MD. X" → PASS;
  - test 1's case: reference "MD. X", document A confirmed "MD. X", document B read "MD X" once and "MD. X" by the re-read (unconfirmed) → A PASS, B FLAG (never FAIL);
  - no reference, mixed confirmed → FLAG for all differing.
- `strict.find_run` on made-up text, with and without `any_order`; a name inside a longer name does not count when its words are not contiguous.
- `test_no_name_in`: no `name_in(`, `name_tokens(`, `\btokens(`, `same_person(`, `disagreement(` or `name_agreement(` anywhere under `src\verify\runner` (except this test's own pattern list); `SequenceMatcher` only on lines marked `# label-match only`.
- `botimport.load()`: paths are under the cache root and modules come from the worktree; a second root raises; `src.config` is the stub.

All names in tests are made-up placeholders such as "SAMPLE ONE".

---------------------------------------------------------------------------------------------------------

## 5. Pipeline and interfaces

`pipeline.run()` is the only orchestrator. Each stage's input and output types are those of `model.py`. Ports keep the lab code's logic; the changes listed are the only ones allowed.

```python
# pipeline.py (Task 6)
def run(input_path: Path, program: str, out: Path, *, today: dt.date, cache_root: Path,
        page_texts: Optional[Path] = None, reading_note: str = "", reread_mrz: bool = True,
        split_only: bool = False) -> RunResult
```

**Stage order** (timings are recorded under the key in brackets):

1. `[safety]` `state = safety.prepare(out, cache_root, input_path)`
2. `[ingest]` `src = ingest.open_input(input_path)`
3. `[engine]` `eng = engine.Engine(cache_root)`, then `eng.set_today(today)`, then `given = engine.load_page_texts(page_texts, src) if page_texts else None`
4. Inside `with eng.case(src.case_id):`
   1. `[stage]` `staged = ingest.stage(src, out / "docs")`
   2. `[ocr]` `pages = eng.read_source(src, staged, given)`
   3. `[typing]` `types = pagetype.type_pages(pages)`
   4. `[segment]` `seg = split.segment(types, program)` for a bundle, or `split.segment_folder(src, pages, types)` for a folder
   5. `confirm = names.make_confirmer(eng, pages, out / "work")` (the targeted re-read, shared by every name check)
   6. `[birth]` `prov = split.provisional_units(seg, pages)`; `births = {tuple(u.pages): birth.read_birth_certificate(u, pages, eng, out / "work", eng.bot.dv.notarised) for u in prov if u.key == "birth_cert"}`
   7. `[identity]` `ident = identity.build_identity(prov, pages, births, eng if reread_mrz else None, out / "work", confirm)`
   8. `[split]` `res = split.finish(seg, types, pages, identity.refs(ident), program)`
   9. `[identity]` `ident = identity.finish_identity(ident, res.units, pages, births, confirm)` (critique 4)
   10. If `res.stop`: write a STOP report (notices only) and return (exit 3). If `split_only`: write the split-only report and return.
   11. `[cut]` For a bundle: `split.attach_paths(res, eng.cut(src.root, split.cut_plan(res), out / "docs"))`, then `eng.prime(u.path, [pages[i].text for i in u.pages])` for every cut unit (one read per page, always), then `doccheck.crop_photo(...)` for the photo unit. For a folder: `split.attach_paths(res, staged)` (no priming: the bot reads each file as it does on folders).
   12. `rec = DerivedRecord(program, today, ident)`
   13. `[names]` `aff = names.affidavit_info(res.units, pages)`
   14. `[bot]` `dc = doccheck.check_documents(res.units, pages, rec, eng, src.mode, aff, births, confirm)`. This covers the bot rows, post-processing, passport, photo, birth certificate (via `birth.check_birth_certificate`), NID numbers, death-certificate stand-in, board QR, income tax and language rows.
   15. `[bank]` `bank.check_bank_unit(u, [pages[i].text for i in u.pages], u.pages, ident, cov, confirm, solvency_pdf)` for each bank unit
   16. `[tin]` `tin.check_tin_certificate(u.path, text, tin.tin_page_types(u, types), confirm=confirm)` then `tin.tin_findings(...)` for each TIN unit
   17. `[financial]` `financial.chain_findings(...)` and `financial.income_tax_findings(...)`
   18. `[names]` `nres = names.evaluate(ident, res.units, pages, aff, dc.machine, births, confirm, extra_reads)`
   19. `[verdict]` `docs, cross, notices = verdict.merge(res, dc.per_unit, bank_map, tin_map, chain_map, names=nres, chain_summary=chain_sum, bundle=dc.bundle, program=program)`, then `missing = verdict.missing_lines(res, program, bank_outcomes)`, then `overall, headline = verdict.overall(docs, missing, res, cross, notices)`.
5. `[report]` `report.write_all(run_result, out)`, then `safety.finish(state)`, which re-checks the input snapshots.

Here:
- `cov = lambda a, b: names.covered(aff, a, b)`;
- `solvency_pdf = lambda i: eng.cut_page(pages[i], out / "work")` (a one-page PDF of run page i);
- `bank_map`, `tin_map` and `chain_map` are uid → findings maps built from the stage 15-17 results, and `chain_sum` is the worst chain finding per rule;
- `dc.machine` are the board-QR and birth-QR names (`MachineRead`); the TIN QR name is judged by `tin.py` and `financial.py`, not by `names.py` (no double count);
- `extra_reads` are the `(person, uid, written, page)` reads of `BankOutcome.person_reads` and `financial.chain_person_reads(...)`: names whose letters equal a parent's reference, used by the dot rule only.

### 5.1 ingest.py (Task 1)

```python
class InputError(Exception): ...
def open_input(path: Path) -> Source
    # A .pdf file -> mode "bundle" (one SourceFile).
    # A directory -> mode "folder": its .pdf/.jpg/.jpeg/.png files, not recursive, sorted by name.
    #   Hidden and ~$ files are skipped.
    #   Revision 2.3: every other visible file (an .odg, a .docx ...) -> Source.not_checked (name, "not a PDF or
    #   image"); a .pdf / image that will not open -> (name, "could not be opened"), the run goes on without it.
    #   Nothing that opens at all -> InputError naming the first file by its position (exit 2).
    # Anything else -> InputError.
    # case_id: bundle "B-" + sha1(bytes)[:16]; folder "F-" + sha1("\n".join(f"{name}|{size}|{int(mtime)}"))[:16].
def classify_name(filename: str) -> Optional[str]
    # 1. the bot's rules.FILE_PATTERNS, longest pattern wins, name tried as-is and with "_" as space
    #    (dv.classify semantics),
    # 2. but every pattern of <= 4 letters ("tin", "eca", "cv", "hsc", "ssc", "pte", "sop") must match as
    #    a whole word,
    # 3. plus RUNNER_FILE_PATTERNS (overlay below).
    # Returns None when nothing matches.
RUNNER_FILE_PATTERNS = {
    "trade_license": ["trade licence", "tin certificate", "e-tin", "etin", "salary certificate", "employment"],
    "academic": ["hsc", "ssc", "apostille", "marksheet", "mark sheet", "academic"],
    "income_tax": ["tax return", "return acknowledgement"],
    "father_nid": ["father id", "father national id"],
    "mother_nid": ["mother id", "mother national id"],
    "id_card_cv": ["cv", "resume"],
    "language_cert": ["duolingo", "pte", "english score", "englishscore", "language"],
    "personal_statement": ["sop", "statement of purpose"],
}
def stage(src: Source, docs_dir: Path) -> Dict[str, Path]
    # folder: shutil.copy2 every file to docs_dir/<same name>; asserts size and int(mtime) are kept, so
    #   the text-cache key name:size:mtime is the same as the original's.
    # bundle: {} (the bundle is read in place, read-only).
def file_sha256(path: Path) -> str      # used by engine.load_page_texts and gpuread (one definition)
```

The classification fixes come from the checker inventory probe. They make these names come out right: "TRADE LICENCE", "HSC", "SSC", "TAX RETURN", "SALARY CERTIFICATE" and "CV". "CONTINUATION" no longer becomes `trade_license`.

### 5.2 engine.py (Task 1) — port of `engine\light_engine.py`, plus page-texts serving

```python
class Engine:
    def __init__(self, cache_root: Path, threads: int = 0) -> None
        # make_reader() (2 ORT CPU sessions + glue_numpy.LightReader); self.bot = botimport.load(cache_root);
        # bot.dv._reader = reader; self.log = PageLog(bot.dv)  (same-pixels memo, timing per call)
    bot: SimpleNamespace
    log: "PageLog"
    def set_today(self, today: dt.date) -> None                     # bot.dv.TODAY = today
    @contextlib.contextmanager
    def case(self, case_id: str) -> Iterator["CacheHandle"]
        # TC.install(case_id, text_dir=<root>/verification/text, images_dir=<root>/verification/images) ... uninstall
    def read_file(self, path: Path, name: str, start_index: int,
                  given: Optional["PageTexts"] = None) -> List[PageRead]
        # PDF: pages present in `given` are served from it (engine "gpu", or "layer" for its layer pages);
        #   the others: bot dv.read_pages(path, max_pages=<all pages>, sideways=...) restricted to the
        #   missing pages (light_engine.ocr_pages; engine "cpu", or "layer" for pages with >= 40 layer chars).
        #   kind "layer"/"ocr" from page_kinds(); image_only from pymupdf.
        # Image file: from `given` when present, else one PageRead(kind="image", text=dv.read_document(path)[0]).
    def read_source(self, src: Source, staged: Dict[str, Path],
                    given: Optional["PageTexts"] = None) -> List[PageRead]
        # bundle: read_file(src.root); folder: read_file(staged[name]) for each file in name order.
        # Remembers each file's path for page_image_of().
    def cut(self, bundle: Path, parts: Sequence[Tuple[str, int, int]], out_dir: Path) -> List[Path]
        # light_engine.cut_bundle unchanged (insert_pdf copy; .jpg = largest embedded JPEG or 300 dpi
        # render; bundle mtime pinned); safety.assert_writable(out_dir) first
    def cut_page(self, page: PageRead, out_dir: Path) -> Path
        # one-page PDF of that run page ("<file stem> p<NN>.pdf"; an image page becomes a one-page PDF)
    def page_image(self, path: Path, page: int, dpi: int = 300) -> "np.ndarray"   # RGB uint8 (pymupdf; PIL for images)
    def page_image_of(self, page: PageRead, dpi: int = 300) -> "np.ndarray"       # same, by run page (memoised per dpi)
    def read_text(self, img: "np.ndarray") -> str               # bot dv._ocr_image (rotation retries), via PageLog
    def read_boxes(self, img: "np.ndarray") -> List[Tuple[list, str, float]]       # reader.readtext(img, detail=1)
    def prime(self, cut_path: Path, page_texts: List[str]) -> None
        # writes "PAGES:<name>:<size>:<int mtime>:20" = {"pages": page_texts} into the open case cache
        # (smoke_test2.prime_pages). Bundle mode primes every cut unit, so dv.read_document on a cut file
        # makes no fresh OCR: one read per page, from the GPU page texts or the CPU engine.
    def stats(self) -> Dict[str, Any]       # ocr_calls, memo_hits, ocr_seconds, cache_hits, pages_by_engine
def load_page_texts(path: Path, src: Source) -> "PageTexts"
    # section 5.2.1. Raises ingest.InputError (exit 2) when the path is not under PRIVATE, the schema is not
    # PAGE_TEXTS_SCHEMA, a file entry is not in src, or its sha256 / n_pages differ from the input file's.
class PageTexts:                     # {(file name, 0-based file page): (text, kind, sideways, seconds)}, complete flag, engine label
    ...
```

`LightReader` has no `allowlist`. Character filtering for MRZ and names is done after reading. Engine study timings: about 7 s per page at 200 dpi on the CPU; near-blank pages cost 4 passes. The GPU worker read about 6 s per page (model load not included).

#### 5.2.1 Page-texts file (`runner-page-texts/1`)

Written by `gpuread.py` (Task 8) and read by `engine.load_page_texts` (Task 1). Private (it holds page text); always under `private-runner`.

```json
{"schema": "runner-page-texts/1",
 "complete": true,
 "engine": "bot EasyOCR on CUDA (gpu_guard, cap 3.0 GB)",
 "mode": "bundle",
 "case_id": "B-<16 hex>",
 "files": [{"name": "<file name>", "sha256": "<64 hex>", "size": 0, "n_pages": 0,
            "pages": [{"page": 1, "text": "...", "kind": "ocr", "sideways": false, "seconds": 0.0}]}],
 "slots": [{"slot": 1, "waited_s": 0.0, "model_load_s": 0.0, "peak_allocated_gb": 0.0, "ended": "complete"}],
 "seconds_total": 0.0}
```

- `page` is 1-based within the file; `kind` is "layer" (text layer ≥ 40 chars, no OCR), "ocr" or "image".
- `complete: false` means some pages are absent; the runner reads those on the CPU light engine and the summary's READING line says how many pages each engine read.
- Folder mode lists one entry per input file, in `ingest.open_input` order.

### 5.3 pagetype.py (Task 2)

This is a port of `order_split.py` L99-238: `_LETTERHEAD`, `_TRANSLAT`, `ENGLISH_TYPED`, `_NID_NO`, `_DOB_*`, `_GRADE`, `_CERT_WORDS`, `_LEVELS`, `_NAME_STOP`, `_eng_ratio`, `nid_numbers`, `birth_dates`, `_name_after`, `labelled`, `mrz_name` and `page_features`. It uses the prototype's `score_page`, `OTHER_BELOW` and `EMPTY_CHARS` through `botimport.load().P`.

```python
def page_features(text: str, image_only: bool = False) -> PageType   # order_split.page_features; index set by caller
    # additions:
    #  * image_only and chars < 150  ->  scores["photo"] = max(scores["photo"], 5.0)
    #  * bank pages: kind "solvency" when the text matches SOLVENCY_CUE, else "statement" when it matches
    #    STATEMENT_CUE (both case-insensitive):
    #      SOLVENCY_CUE  = r"solvency|to\s+whom\s+it\s+may\s+concern|this\s+is\s+to\s+certify"
    #      STATEMENT_CUE = r"statement\s+of\s+account|opening\s+balance|closing\s+balance|withdraw|deposit"
    #  * apostille pages keep the `level` their text names (the certificate it certifies), else None
def type_pages(pages: List[PageRead]) -> List[PageType]
def labelled(text: str, label: str) -> List[List[str]]                # order_split.labelled (used by split)
FINE_TO_KEY: Dict[str, str]   # prototype fine type -> bot key (engine study table); nid / death_cert need a role
```

The port's name helpers (`_name_after`, `mrz_name`, `labelled`) use `strict.words` instead of the bot's
`name_tokens` (which drops initials, two-letter words and honorifics): role scoring compares strictly (S8).

### 5.4 split.py (Task 2) — port of `order_split.py` L46-98 and L239-706

```python
SLOTS = ["passport", "photo", "father_id", "mother_id", "birth_cert", "family_cert", "academic", "bank",
         "owner_doc", "tin"]                            # + "income_tax" for MASTER
SLOT_ACCEPTS = {"father_id": ("father_nid", "father_death_cert"), "mother_id": ("mother_nid", "mother_death_cert"),
                "owner_doc": ("trade_license", "employment_certificate"),   # fine types of the trade_license key
                "tin": ("tin_certificate",)}
MISSING_TEXT = {"passport": "PASSPORT MISSING", "photo": "PHOTO MISSING",
                "father_id": "FATHER NID (OR DEATH CERTIFICATE) MISSING",
                "mother_id": "MOTHER NID (OR DEATH CERTIFICATE) MISSING",
                "birth_cert": "BIRTH CERTIFICATE MISSING", "family_cert": "FAMILY CERTIFICATE MISSING",
                "academic": "ACADEMIC CERTIFICATE MISSING", "bank": "BANK SOLVENCY / STATEMENT MISSING",
                "owner_doc": "TRADE LICENSE / EMPLOYMENT CERTIFICATE MISSING",
                "tin": "TIN CERTIFICATE MISSING", "income_tax": "INCOME TAX MISSING"}
SOLVENCY_MISSING_TEXT = "BANK SOLVENCY CERTIFICATE MISSING"   # printed by verdict from bank.py's outcome
OPTIONAL_SLOTS = ["student_nid", "language_cert", "affidavit", "eca", "personal_statement", "recommendation",
                  "id_card_cv"]                         # never reported missing; income tax is optional outside MASTER
@dataclass class Costs: ...     # order_split.Costs, same defaults
@dataclass class SplitCfg: ...  # order_split.SplitCfg, same defaults; edge is always "attach" (no CLI switch)
@dataclass class Doc: ...       # order_split.Doc
@dataclass
class Segmentation:
    mode: str                   # "bundle" | "folder"
    docs: List[Doc]
    feats: List[PageType]
@dataclass
class Refs:                     # strict reference names for role scoring (from identity.refs())
    father: List[str]; mother: List[str]; student: List[str]

def align(feats, c=Costs()) -> Alignment                       # verbatim
def documents(feats, al, cfg) -> List[Doc]                      # verbatim + the revision-2 changes below
def segment(types: List[PageType], program: str, cfg: SplitCfg = SplitCfg(), costs: Costs = Costs()) -> Segmentation
def segment_folder(src: Source, pages: List[PageRead], types: List[PageType]) -> Segmentation
    # One Doc per input file. label = the section/float of key_by_name, or of the file's majority
    # fine type when the name is unknown. Revision 2.3: a file whose pages make two documents or more by the
    # bundle rules (file_documents) gives one Doc per document (Doc.part); a one-document file keeps one Doc,
    # with sub = parts by fine type / level.
def file_documents(types, a: int, b: int, key: str = "", fname: str = "") -> Optional[List[Doc]]
    # Revision 2.3: align() + documents() on the file's pages a..b alone; the NID block is one Doc (no person
    # split inside a file); a "photo" run is unrecognised unless the name says photo; adjacent unrecognised runs
    # merge. Documents counted: academic = its known levels (>= 2 to cut), financial = its fine types, others 1,
    # unrecognised 0. >= 2 -> the Docs (part=True, file_start=a, file_key=key, misplaced=False, key = the name's
    # key on the part it names only); else None.
def provisional_units(seg: Segmentation, pages: List[PageRead]) -> List[DocUnit]
    # DocUnits without roles: NID-block units keyed "nid_unassigned". Used by birth and identity only.
    # Unit pages equal the final units' pages for every non-NID unit (birth lookups use tuple(pages)).
def finish(seg: Segmentation, types: List[PageType], pages: List[PageRead], refs: Refs, program: str) -> SplitResult
def cut_plan(res: SplitResult) -> List[Tuple[str, int, int]]   # (unit_file_name(u), first, last) for every unit
def unit_file_name(u: DocUnit) -> str
    # f"{FILE_TITLE[u.key]} p{a:02d}-{b:02d}.pdf" (".jpg" for the photo, single page "p05");
    # dv.classify(name) == u.key for every key and level (tested)
def attach_paths(res: SplitResult, paths: Union[List[Path], Dict[str, Path]]) -> None   # sets DocUnit.path
```

**Revision-2 changes to `documents()` and `finish()`** (both modes unless noted):

1. **Academic: one unit per level** (critique 16). In a bundle each level (degree, diploma, HSC, SSC) is its own unit (key `academic`, `level` set, title "ACADEMIC HSC" ...), so the bot's academic chain runs per level file. An e-Apostille page joins the level its text names (`PageType.level`), else the level that follows it. Revision 2.3: a page with no academic sign (no level, no certificate / transcript kind: a certificate scanned upside down) right after an e-Apostille goes with that apostille. In folder mode (revision 2.3) a file with two known levels or more is cut into one unit per level, as in a bundle (rule 7); a file with one known level stays one unit (title "ACADEMIC HSC", or "ACADEMIC" when no level is read).
2. **Financial: one unit per part** (A6 g, kept). Keys all `trade_license`, fine type `trade_license`, `employment_certificate` or `tin_certificate`, titles "TRADE LICENSE", "EMPLOYMENT CERTIFICATE", "TIN CERTIFICATE": one report line each.
3. **Trailing page after income tax** (A6 f). Unrecognised page(s) at the end of the bundle, right after the income-tax document, join the income-tax unit: no ORDER notice, a unit NOTE `split.attached_trailing` "page N (not recognised) kept with INCOME TAX as its last page".
4. **Death certificate anchored to the NID block** (critique 7). `death_cert` is no longer a FLOAT: it belongs to the NID section. Outside the NID block it is misplaced (an ORDER notice, below).
5. **Notices** (critique 6, 7):
   - **STOP** only for the NID-person ambiguity: a misplaced NID or death certificate whose person the names cannot settle, or NIDs out of order while a role rests on position only. Nothing else stops a run: no STOP for 2+ ORDER notices, none for > 25% unrecognised pages.
   - **ORDER, blocking** (`blocks=True`): a misplaced run, a document out of its slot, an unrecognised run in the middle of the bundle. The units on those pages get NOT JUDGED ("fix pages a-b, then check again"); every other unit is judged.
   - **ORDER, non-blocking** (`blocks=False`; the unit is judged and the notice says which pages to fix): an NID translation behind its original; academic levels not newest first; a transcript before its certificate within a level; a bank statement page before the solvency certificate (`PageType.kind`); a death certificate outside the NID block whose person is settled by name; NIDs out of order when both roles are settled by name. The apostille-not-first rule arrives from `doccheck` as an ORDER finding (section 5.8).
   - **HINT**: "X MISSING in its place; page N reads partly like it — if it is the X, move it to its place". Never ORDER, never STOP.
   - **NOTE**: anything else the port emits as NOTE.
   - No cap on the number of notices.
6. **Missing slots** (critique 5). `owner_doc` is filled by a trade licence or an employment certificate; `tin` by a TIN certificate (Q3); `bank` by any bank unit (a bank unit with no solvency page gives `SOLVENCY_MISSING_TEXT` through `bank.py`, unless another bank file of a folder holds the certificate). A death certificate whose person is unresolved does not fill a slot: when a parent's slot is otherwise empty, `res.maybe[slot] = uid` and the slot is left out of `missing` (critique 17). When both parents' slots are open and there are fewer such death certificates than open slots, `missing` also gets `parent_id`: "FATHER OR MOTHER NID (OR DEATH CERTIFICATE) MISSING — a death certificate stands for one parent only".
7. **Folder mode:** roles from the file name (role_how "file-name"); a generic DEATH CERTIFICATE gets its role by strict name against refs (assign_persons scoring with the position weight set to 0); no ORDER/STOP; a unit whose majority fine type does not map to its file-name key gets a unit note FLAG `split.name_vs_content`.
   **Revision 2.3, a file that holds several documents** (the owner's "FINANCIAL DOCUMENTS", "ACADEMIC DOCUMENTS ATTESTED"): `file_documents` cuts it with the bundle rules inside the file (bank solvency then statement; trade licence / employment certificate; TIN; income tax; academic newest first, certificate then transcript, e-Apostille pages attached; affidavit and the other floats; unrecognised pages kept as an `unrecognised` unit with its FLAG, or attached at an edge as in a bundle). Each part is a unit (academic per level, financial per part) with `DocUnit.part` = its pages inside the file ("p4-6", 1-based) and `source_file` = the file; `pipeline._cut_parts` cuts it out of the staged copy (`engine.cut`, file-local pages, `docs\parts\<unit_file_name>`) and attaches the cut, NOT primed (revision 2.4: the bot reads it itself, as every folder file); bot messages that cite "page N" of the cut name the file's page (`doccheck.part_page_refs`). The part the file's name names keeps the name's key (and its role); the others get their keys from their pages. No ORDER notice and no NOT JUDGED in folder mode: a part out of the file's order is judged where it is. When no part is the document the name says, every part gets FLAG `split.name_vs_content` ("the file name says X but its pages read like A, B"). Missing slots count the parts: no "X MISSING" when X is inside another file. A file of one document (a two-page NID, a bank file of solvency and statements, a passport with a blank back) is not cut.
8. **Both modes:** Doc → DocUnit mapping (below); unit notes for role_how "position" / "name-partial" (FLAG `split.role_position` / `split.role_partial`), "extra" NID (key `nid_unassigned`, FLAG `split.nid_unassigned`) and unrecognised pages (FLAG `split.unrecognised`).

**Doc to DocUnit mapping:**

| Doc label | Units produced |
|---|---|
| passport, photo, birth_cert, family_cert, bank, income_tax, and each float label | One unit, with the key equal to the label. |
| nid | One unit per person. The key comes from the role: `father_nid`, `mother_nid`, `student_nid`, or `nid_unassigned` for "extra". |
| death_cert | `father_death_cert` or `mother_death_cert` by role, else `death_cert`. |
| academic | Bundle: one unit per level (key `academic`, `level`, title "ACADEMIC <LEVEL>"). Folder: one unit per file. |
| financial | One unit per part: key `trade_license`, fine type `trade_license`, `employment_certificate` or `tin_certificate`. |
| unrecognised | One unit with key `unrecognised` (unless rule 3 attaches it to income tax). |

**FILE_TITLE** (so that `dv.classify` maps back to the key):

| Key | File title |
|---|---|
| passport | PASSPORT |
| photo | PASSPORT SIZE PHOTO |
| father_nid | FATHER NID |
| mother_nid | MOTHER NID |
| student_nid | STUDENT NID |
| father_death_cert | FATHER DEATH CERTIFICATE |
| mother_death_cert | MOTHER DEATH CERTIFICATE |
| death_cert | DEATH CERTIFICATE |
| birth_cert | BIRTH CERTIFICATE |
| family_cert | FAMILY CERTIFICATE |
| academic | ACADEMIC CERTIFICATE <LEVEL> (for example "ACADEMIC CERTIFICATE HSC") |
| bank | BANK STATEMENT |
| trade_license | TRADE LICENSE, TIN CERTIFICATE or EMPLOYMENT CERTIFICATE, by fine type |
| income_tax | INCOME TAX |
| affidavit | AFFIDAVIT |
| language_cert | IELTS TOEFL TOPIK |
| personal_statement | PERSONAL STATEMENT |
| recommendation | RECOMMENDATION |
| eca | ECA |
| id_card_cv | ID CARD CV |
| nid_unassigned | NID UNASSIGNED |
| unrecognised | UNRECOGNISED |

`nid_unassigned` and `unrecognised` are never run through bot checkers.

### 5.5 identity.py (Task 3)

```python
def build_identity(units: List[DocUnit], pages: List[PageRead], births: Dict[Tuple[int, ...], BirthFacts],
                   engine: Optional["Engine"], work_dir: Path,
                   confirm: Optional[Callable[[int, str], str]] = None) -> Identity
def finish_identity(ident: Identity, units: List[DocUnit], pages: List[PageRead],
                    births: Dict[Tuple[int, ...], BirthFacts],
                    confirm: Optional[Callable[[int, str], str]] = None) -> Identity
def refs(ident: Identity) -> "split.Refs"                         # upper-case strict.words lists for role scoring
def mrz_from_words(text: str) -> Dict[str, Any]                   # A6 a; doc_verifier.passport_mrz keys
def reread_mrz(engine: "Engine", pages: List[PageRead], page_index: int, work_dir: Path) -> Dict[str, Any]
def passport_for_language(ident: Identity) -> Dict[str, Any]
    # {"surname", "given_names", "passport_no", "dob" (ONLY when ident.dob_trusted, else None),
    #  "name_verified", "name_trusted"}   (critique 12)
```

**Steps of `build_identity`** (all text is the 200 dpi page text):

1. **MRZ** (A6 a). For each passport-unit page take the best by `(line2_ok, checks)` of:
   - `dv.passport_mrz(page_text)` (the bot's reader, which can misparse a run where OCR joined both lines), and
   - `mrz_from_words(page_text)`: line 2 read from its own word. For every whitespace token (and every pair of adjacent tokens, for a line OCR split in two), after mapping characters outside `[A-Z0-9<]` (`«` becomes `<<`): a token of ≥ 88 characters that starts with "P" is cut at 44 into line 1 and line 2; `OV.parse_mrz_line2` runs on every 44-character window of every token of ≥ 28 characters; `OV.parse_mrz_line1` runs at each `P[<KC(]?[A-Z]{3}` match.
   - If `engine` is set and the date of birth or expiry is still not trusted (`dv.mrz_dob(m, None)` / `dv.mrz_expiry(m, None)` is None) or line 1 is not well formed, call `reread_mrz`: render the page at 300 dpi, crop the bottom 35%, scale ×2, read with `engine.read_text`, map characters as above and run both readers on the result. The crop PNG goes to `work\mrz_p<NN>.png`. If the re-read wins, `dob_source`/`expiry_source` are "MRZ re-read".
   - A valid MRZ (line 2 check digits agree) is trusted on `doc_verifier._trusted` terms; then no MRZ FLAG or NOTE is produced.
2. **Student name.** `names.extract_passport({"text":..., "pages": [...]})` gives the reads plus the `passport_mrz_name` dict.
   - `student.parts` = the MRZ line-1 words; `source` "passport MRZ"; `truncated` from the MRZ dict; `name_trusted` = line 1 well formed.
   - **Printed name** (A2's dot reference): the data page's Given Name(s) + Surname as printed (`written`, dots kept). When `strict.compare(written, mrz_words, any_order=True) == "EQUAL"`, `student.written = written` and `student.confirmed = True` (the MRZ is the second read of its letters). Otherwise `confirm(page, written)` is asked once; "confirmed" keeps it; else FLAG `identity.printed_name` "the passport's printed name could not be read letter for letter (page N) — dots on the other documents are judged without it", and `written = ""`.
   - MRZ line 1 absent: the printed name gives the letters (confirmed only by `confirm`), FLAG `identity.mrz_name_unread`.
3. **Parents** (A3: the birth certificate is the canonical source). `names.canonical("father"|"mother", reads, mrz, vocabulary)`:
   - `reads` are first the reads of each birth-certificate unit's `BirthFacts.english_pages` (the translation for a Bangla original; never the Bangla page), then the passport's Father's/Mother's fields.
   - `vocabulary` (critique 4): the reads of EVERY provisional unit (NID block, family certificate, academic, financial), so that a passport scan without its parents' page does not leave the cut value empty.
   - Source "birth certificate": `written` as printed; `confirmed` when another page of the certificate, another document's read, or `confirm()` gives the same letters.
   - No birth-certificate read: the passport field, source "passport field", NOTE `identity.parents_from_passport`.
   - Neither: FLAG `identity.parents_unknown` (`finish_identity` retries).
4. **Date of birth.** The trusted MRZ date first. Fallbacks, untrusted: `dv.labelled_dob(passport text)` ("passport label"), then the birth certificate's `BirthFacts.dob` ("birth certificate"). The birth-certificate DOB check itself is `birth.py`'s (section 5.12), which uses `ident.dob` when it is trusted (critique 13).
5. **Expiry.** The trusted MRZ date first. Next the "Date of Expiry" label: the date within 40 characters after `date\s*of\s*expiry|expiry\s*date`; this one is trusted. Last the latest future date in the scan (`dv.find_dates`), source "scan date", never trusted.
6. **Passport number.** `passport_no` and `passport_no_ok` come from the MRZ. `printed_passport_no` is the first `[A-Z]{1,2}\d{7}` within 30 characters after a "Passport No" label.
7. **Name check.** `name_verified = language.passport_name_verified(passport_text, surname, given_names)`.
8. **Notes** (they go to the passport unit's line):
   - FLAG `identity.mrz_truncated` when `truncated`.
   - FLAG `identity.mrz_unread` when no line 2 was read by any of the three readers.
   - NOTE `identity.mrz_reread` when the re-read decided a field.

**`finish_identity`** (after `split.finish`, critique 4 and A7):
- Recomputes the parents' canonical names with every role-labelled read as vocabulary.
- Sets `father_nid` / `mother_nid` = the strict name on that parent's NID unit when its role is "name" or "file-name" (role-confirmed). `bank.py` accepts a holder equal to either reference (A7).
- When the parents were unknown, the role-confirmed NID name becomes the reference (source "father NID" / "mother NID"); a NID whose role rests on position only never becomes a reference.

`DerivedRecord(program, today, identity).portal_view()` is the ONLY dict that reaches the bot's checkers (section 5.7).

### 5.6 Person assignment

Person assignment is `split.finish` with `identity.refs(ident)`. In a bundle, the NID and death-certificate block is scored as in `order_split.assign_persons`: +1.5 for the role's place, +5 for a name strictly EQUAL to the reference (`strict.compare(..., dots=False)`), +2 for half the words shared, and −1 for nothing shared. Only "name" (strict equality) confirms a role. "name-partial" and "position" give FLAG unit notes, never PASS. Death certificates can only be father or mother.

### 5.7 Derived record (what the bot's checkers receive)

| Portal key | Bot use (doc_verifier line) | Value given | Why |
|---|---|---|---|
| Full Name | check_nid student leg 763-779, family cert 959, academic 997-1003, bank holder fallback 1437, cross_checks | "" | Every use is the loose `name_in`. `names.py` does these checks strictly. |
| Father / Mother | check_nid 763-779, academic 997-1003, sponsor_holder 1343-1359 | "" | The same reason. The holder chain is `bank.py` and `financial.py`. |
| DOB | birth_date_check 695-722 (trust escape, portal PASS path) | "" | `check_birth_cert` is no longer called; `birth.py` judges the DOB (A3). |
| Passport Expiry | check_passport 572-590 | "" | `check_passport` is not called. The runner's `passport_rows` replaces it (section 5.8). |
| Passport No | check_passport 603-615, cross_checks 1749 | "" | Tautological without the portal. Replaced by the MRZ check digit plus the MRZ-vs-printed number check. |
| Gender | check_photo 662 (NOTE) | "" | The owner: photo background and shape only. |
| Sponsor | check_bank 1435, check_financial 1578-1581 | "" | The sponsor is removed. With "" the OTHER FLAG and the holder rows cannot fire. |
| SSC GPA / HSC GPA | check_academic 1004-1009 | "" | No reference value, and the runner's GPA check is removed (critique 20). |
| Program | (dv.program_of, not called) | the `--program` value | The owner's choice (A8). Passed explicitly as `program`. |

### 5.8 doccheck.py (Task 5) — the per-document checks

```python
@dataclass
class DocChecks:
    per_unit: Dict[str, List[Finding]]   # uid -> findings
    machine: List[MachineRead]           # board-QR and birth-QR names (A1 machine reads)
    bundle: List[Finding]                # bundle-level findings (page.colour_bundle)
def check_documents(units: List[DocUnit], pages: List[PageRead], rec: DerivedRecord, eng: "Engine",
                    mode: str, aff: AffidavitInfo, births: Dict[Tuple[int, ...], BirthFacts],
                    confirm: Callable[[int, str], str]) -> DocChecks
def bot_findings(rows: List[Tuple[str, str]], fn: str, unit: DocUnit, pages: List[PageRead]) -> List[Finding]
    # (verdict, message) -> Finding(verdict, rule=f"bot.{fn}", message, source=f"bot:{fn}", pages=<unit pages, 1-based>)
def passport_rows(text: str, ident: Identity, today: dt.date) -> List[Finding]
def photo_rows(unit: DocUnit, mode: str, eng: "Engine") -> List[Finding]
def crop_photo(eng: "Engine", page_image: "np.ndarray", out_jpg: Path) -> Optional[Path]
def nid_number_rows(page_texts: List[str], unit: DocUnit) -> List[Finding]          # A6 d
def board_qr(unit: DocUnit, pages: List[PageRead]) -> Tuple[List[Finding], List[MachineRead]]   # A6 c
def page_rows(unit: DocUnit, text: str, eng: "Engine", skip_qr: bool) -> List[Finding]
DROP: Dict[str, List[str]]       # key -> regexes (re.search) of bot messages removed (pinned, see below)
KEEP_ONLY: Dict[str, List[str]]  # bundle photo: the only bot messages kept
REWRITE: Dict[str, Dict[str, str]]
```

**`check_documents` loop.** It mirrors `dv.verify_student` / `_check_file`, but iterates the units, not a folder:
- Order: units sorted by `R.REQUIRED[program] + R.OPTIONAL + ["income_tax"]` key order (de-duplicated; critique 18), so the passport text exists before the NIDs. `nid_unassigned` and `unrecognised` units come last and get only their split notes.
- For each unit:
  - `text, sizes = dv.read_document(unit.path)` (a bundle cut is primed: no fresh OCR). An `[unreadable:` text or a `CheckError` gives ERROR `doc.unreadable`. `CODE_FAULTS` are re-raised.
  - Then `texts[key] = texts.get(key, "") + "\n" + text` (the bot's "others" dict).
  - Then the adapter for the key (table below).
  - Then `page_rows` for every key except photo, id_card_cv, nid_unassigned and unrecognised.
- The identity notes (`ident.notes`) are added to the passport unit.

**`page_rows`:**
- `info = PC.inspect(path)`, then `PC.colour_check(info, key)`. **Bundle colour** (critique 19, Q15): when the colour check fails on every unit of a bundle, those per-unit colour rows are dropped and one bundle finding FLAG `page.colour_bundle` "the whole bundle was scanned in black and white — the colour rule cannot be judged per document; rescan in colour" is returned in `DocChecks.bundle`. When only some units fail, their rows stay.
- `PC.qr_check(info, required=key in R.QR_DOCS, text=defused)`, where `defused = re.sub(r"(?i)taxpayer|tin\s?certificate|etin", " ", text)`. This stops the substring skip at PC 233-234 from swallowing files that merely contain "marketing", "meeting" and the like. `qr_check` is skipped (`skip_qr`) for units that hold TIN pages (`tin.py` reports the QR), for birth-certificate units (`birth.py`), and for academic units whose board QR `board_qr` read.
- Then `PC.bangla_check(text)`, except for birth-certificate units (`birth.py` owns the translation rule).

**Adapters** (bot function called unchanged with `rec.portal_view()`, and what is done to its rows):

| Key | Bot call | Post-processing | Runner rows added (rule id: levels) |
|---|---|---|---|
| passport | none (replaced) | — | `passport_rows` (below) + `ident.notes`. |
| photo | `dv.check_photo(text, view, sizes, jpg)` on the crop | Bundle: KEEP_ONLY `^\d+x\d+px, ratio`, `^background is`, `^photo file could not be opened`, `^could not open the image`. Folder: all rows (the Gender NOTE cannot fire). | Bundle with no photo edge found: `photo.edges` FLAG "the photo's edges were not found on page N — check by eye"; then NO ratio or background row (critique 1). |
| birth_cert | none (`dv.check_birth_cert` is NOT called) | — | `birth.check_birth_certificate(facts, unit, ident, printed_name, printed_confirmed, confirm)` with `facts = births[tuple(unit.pages)]` (re-read with `birth.read_birth_certificate` if the key is absent) and `printed_name, printed_confirmed = names.printed_student_name(unit, pages, facts)` (section 5.12). Its QR name, when any, becomes a `MachineRead`. |
| father_nid, mother_nid, student_nid | `dv.check_nid(text, view, sizes, whose, path, texts)` | REWRITE for student_nid: "no notarisation found — parents' NID must be notarised" becomes "no notarisation found — the student's NID must be notarised". DROP the bot's NID-number rows: `-digit ID number reads `, `reads the same on the original and the translation`, `read, but not on two pages in the same format`, `^no NID/birth-registration number could be read`. | `nid_number_rows` (A6 d, below), rule `nid.number`. Names come from `names.py`. |
| father/mother_death_cert, death_cert | none | — | `death.stands_in` PASS "accepted in place of the father's/mother's NID" ONLY when that parent's NID is absent; with the NID present: NOTE "the father's NID is also given — the death certificate is not used in its place" (critique 17). For a `death_cert` whose role is unresolved: FLAG "whose death certificate could not be told — check by eye" (the slot's line comes from `SplitResult.maybe`). |
| family_cert | `dv.check_family_cert(text, view, sizes, path)` | none | — (`names.family_first_row` from `names.py`) |
| academic (one unit per level) | `dv.check_academic(text, view, sizes, path, texts)` | Apostille FAIL kept (R15). Bundle mode (critique 8): the row "page 1 is not an e-Apostille — the first apostille is on page N" becomes an ORDER finding `academic.apostille_order` "the e-Apostille for <LEVEL> is on page N — put it first in that level's pages" (pages = that apostille page); "no e-Apostille with a scannable QR" and "an e-Apostille page is there but its QR could not be read" stay FAIL. When `board_qr` read codes: DROP `^no QR code and nothing showing it is the online registration copy`, `^a QR code is on the page but could not be read`, `^no QR code on this document`. | `academic.board_qr` (A6 c, below). |
| bank | `dv.check_bank(text, view, sizes, program, path, texts)` | DROP `^account opened ` (A7: account age removed). DROP `^solvency certificate and statement both dated`, `^the solvency certificate is dated `, `^solvency certificate dated `, `^statement generated `, `^neither date could be read` (replaced by `bank.dates`). DROP `^solvency amount stated in USD`, `^no USD equivalent found`, `^carries a seal and signature`, `^no seal or signature of any kind found` (replaced by `bank.usd` / `bank.seal` on the solvency page, critique 15). | — (`bank.py`) |
| trade_license (all fine types) | `dv.check_financial(text, view, sizes, path, texts)` | DROP `^TIN and trade licence are both in the name of`, `^the TIN names '` (loose any-token test). The holder rows cannot fire with the empty record (asserted). Revision 2.4 (`financial_kind_rows`): "could not tell which financial document this is" becomes PASS "contains: <kind> (by the page typing; ...)" when a page of the unit is typed as the unit's fine type by its own text; a trade licence then gets the bot's licence rows on its text (`fiscal_year_check`, business start date, notarisation, as `check_financial` words them). | — (`tin.py`, `financial.py`) |
| income_tax | `dv.check_income_tax(text, view, sizes, program, path)` | MASTER: rows unchanged. Any other program (A6 h, critique 18): every FLAG or FAIL row of the bot AND of `page_rows` becomes NOTE with " (information only: the income-tax guideline is for Master's)" appended; plus `doc.present_info` PRESENT "present — information only for <PROGRAM>". | — (`financial.income_tax_findings`; its FLAG stays a FLAG) |
| language_cert | none | — | IELTS, TOEFL and TOPIK: `language.check_language_file(rows_text, identity.passport_for_language(ident), program, today, covered_fn)`, where `covered_fn = lambda cert_name: names.covered(aff, ident.student.written or ident.student.parts, cert_name)` (critique 14). Text = `language.rows_text(eng.read_boxes(page_image))` per page, joined with "\f". Fallback: the 200 dpi text. Each `(verdict, check, why)` becomes `Finding(rule="language."+check)`. Any other certificate kind: `doc.present_only` PRESENT "present — only IELTS, TOEFL and TOPIK are checked" (critique 20). |
| affidavit | none | — | `doc.present` PASS "affidavit present — used by the name rule". |
| personal_statement, recommendation, eca | none | — | `doc.present` PASS "present (no automatic rule)" — the bot's default — plus `page_rows`, as the bot does (Q10). |
| id_card_cv | never read | — | `doc.present_only` PRESENT "present — not checked (owner, 21 Oct)". |

**Passport rows** (`passport_rows`):

`passport.validity`:
- A trusted expiry (MRZ, MRZ re-read or label): `m = dv.months_between(today, expiry)`.
  - FAIL "passport expired on X" when m < 0.
  - FAIL "only m month(s) validity left" when m < `R.PASSPORT_MIN_MONTHS`.
  - Otherwise PASS "valid until X (m months left; read from <source>)".
- A scan date: PASS when m ≥ 12, otherwise FLAG. It is never FAIL. The message says "read from a date on the scan, not the MRZ".
- No date: FLAG "could not read an expiry date".

`passport.number`:
- MRZ check digit OK and the printed number read: PASS when equal, FLAG when they differ.
- Check digit bad: FLAG.
- MRZ line 2 unread: FLAG "the MRZ could not be read — check the passport by eye".

`passport.mrz`: PASS "MRZ read; its check digits agree (source)" when `mrz["line2_ok"]`.

**NID number rows** (`nid_number_rows`, A6 d), rule `nid.number`:
- Per page: `dv.nid_numbers(text)`, plus OCR-glue windows: every digit run that `nid_numbers` would join, of 11-12 digits (10-digit windows), 14-16 (13-digit windows) or 18-20 (17-digit windows). A window counts only when it equals a clean number (exact length) read on ANOTHER page of the same unit.
- Then the bot's own pure `dv.nid_number_agreement(per_page, every)` on these sets, rows kept with their verdicts (PASS / NOTE / FLAG) under rule `nid.number`; no number at all: FLAG "no NID/birth-registration number could be read from the scan".
- Effect: a number with two glued noise digits on one page no longer gives the false "not compared" NOTE.

**Board QR** (`board_qr`, A6 c), academic units only: `tin.read_qr(unit.path, deep=True)` (the crop/threshold ladder A/B/E/C/D). Meaningful codes → `academic.board_qr` PASS "board QR read on page N"; a payload holding `(?i)name\s*[:=]\s*([A-Z .'-]{3,})` gives a `MachineRead(person="student", source="board QR")`. No code → no row (the bot's `qr_check` row stays).

**`crop_photo`** (bundle only; critique 1):
1. Render the photo page at 300 dpi and convert it to grey.
2. **Whole-image shortcut only when the embedded image is not page-sized:** the page's single embedded image covers ≥ 60% of the page AND under 90% of it AND its height/width is 1.0-1.6. A scanned bundle page (one image covering the whole page) never takes this path.
3. Otherwise find the photo's rectangular edge: Canny (50/150), a 3 px dilation, external contours, `approxPolyDP` to a convex 4-corner shape whose area is 2-60% of the page and whose h/w is 1.0-1.6; the largest wins. Never a dark-pixel mask (it finds the head, not the photo).
4. No rectangle: return None → `photo.edges` FLAG; the background and shape are not judged on an unverified crop.
5. Crop 2% inside the edge and write a JPEG at quality 95 to `docs\PASSPORT SIZE PHOTO pNN.jpg`.

`dv.check_photo` then measures shape (ratio 0.78 ± 0.06) and background (top band and corners average > 190) on the crop.

**Pinned messages.** `tests/test_doccheck.py::test_pinned_messages` asserts that every DROP, KEEP_ONLY and REWRITE pattern, applied with `re.search`, matches at least one string literal (f-string templates as they stand in the source) in `inspect.getsource(dv)` or `inspect.getsource(PC)`. A drift in the bot code therefore breaks the test, not the report.

### 5.9 Cross-document checks

#### 5.9.1 names.py (Task 3) — port of `names_affidavit.py`

**Kept:** L55-1130 (reads, extractors, OCR classification, affidavit parsing, covered) and L1189-1533 (canonical, judge_read, evaluate_person, consensus_spelling, evaluate_person_anchored), with the changes below.

**Removed:** `BOT`, the `sys.path` insert, `TEXT_DIR`, `RESULTS`, `classify_file`, `student_files`, `portal_names`, `all_students`, `extract_all`'s file loop, `affidavit_deponent` (critique 20), `covered_parts`, the honorific policy switch (A1 is always strict), and every token-level matcher that ignores dots, initials or honorifics (`strict_in`, `same_name`, `honorific_kind` become `strict.find_run` / `strict.compare`).

**Changes:**
- Imports come from `botimport.load()` (`C.edit_distance`, `OV.parse_mrz_line1`, `OV._split_merged`), never from `C:\Hangeul\BOT`.
- `Read` gains `unit: str = ""`, `written: str = ""` (the value as OCR printed it, dots kept, tokens joined by one space) and `page: int = 0` (0-based run page). The names-internal `Finding` is renamed `NameFinding` and gains `unit` and `page`.
- Birth-certificate units are read only on `BirthFacts.english_pages`.
- `AffGroup` keeps every spelling AS WRITTEN; `covered(aff, a, b)` is True when `strict.form(a)` and `strict.form(b)` (letters AND dots) are both printed in ONE same-person group. `covered_by_noise` (the affidavit names a spelling only as OCR read it) gives FLAG, never NOTE.
- `_related` (which affidavit lines belong to one group) keeps its `SequenceMatcher`, marked `# label-match only`; it never decides that two names are the same.
- `printed_student_name(unit, pages, facts) -> Tuple[str, bool]`: the student's name as written on a birth-certificate unit's English pages, and whether two reads agree (for `birth.check_birth_certificate`'s QR-name rule, called by `doccheck`).

```python
REREAD_MAX = 24            # targeted re-reads per run, shared by every caller of the confirmer
REREAD_MIN_CONF = 0.80
REREAD_CAN_FAIL = True     # A1: a confirmed difference is FAIL (no audit gate)
def make_confirmer(engine: Optional["Engine"], pages: List[PageRead], work_dir: Path) -> Callable[[int, str], str]
    # (run page index, variant as written) -> "confirmed" | "refuted" | "unclear". engine None -> "unclear".
    # 1. engine.page_image_of(pages[i], 300) (memoised) and engine.read_boxes(img);
    # 2. find the box run whose upper-cased letters equal strict.letters(variant); crop the union with a 30%
    #    margin, scale x2, read the boxes again; save work\name_p<NN>_<k>.png;
    # 3. "confirmed": the re-read prints strict.form(variant) exactly (dots included) with every word's
    #    confidence >= REREAD_MIN_CONF; "refuted": it prints another spelling with that confidence (for
    #    example the reference's); "unclear": anything else (box run not found, low confidence, budget spent).
def unit_rec(u: DocUnit, pages: List[PageRead]) -> dict            # {"text": "\n".join(texts), "pages": texts}
def collect_reads(units: List[DocUnit], pages: List[PageRead],
                  births: Dict[Tuple[int, ...], BirthFacts]) -> Tuple[List[Read], Optional[dict]]
    # EXTRACTORS by unit.key (passport via extract_passport); each Read.unit = uid. Bank and trade_license
    # units are NOT read here: their person checks are bank.py's and financial.py's (no double count).
def affidavit_info(units: List[DocUnit], pages: List[PageRead]) -> AffidavitInfo
    # affidavit_groups / affidavit_documents over every affidavit unit
def covered(aff: AffidavitInfo, a: "strict.Name", b: "strict.Name") -> bool
def canonical(person: str, reads: List[Read], mrz: Optional[dict],
              vocabulary: Sequence[Read] = ()) -> Tuple[Tuple[str, ...], str]   # + vocabulary (critique 4)
@dataclass
class NamesResult:
    per_unit: Dict[str, List[Finding]]   # uid -> model.Finding (rule "names.<person>" / "names.<person>_dots")
    per_person: Dict[str, str]           # "student"/"father"/"mother" -> worst verdict
    summary: List[Finding]               # one line per person for the NAMES cross line
    rereads: List[Dict[str, Any]]        # crop, re-read text, confidence, effect
def evaluate(ident: Identity, units: List[DocUnit], pages: List[PageRead], aff: AffidavitInfo,
             machine: List[MachineRead], births: Dict[Tuple[int, ...], BirthFacts],
             confirm: Callable[[int, str], str],
             extra_reads: Sequence[Tuple[str, str, str, int]] = ()) -> NamesResult
def family_first_row(text: str, ident: Identity) -> Finding        # rule names.family_first_row
```

**`evaluate` steps**, for each person P (student, father, mother):
1. **Reference R.** Student: letters from the MRZ (`ident.student.parts`), dots from `ident.student.written`. Parents: `ident.father` / `ident.mother` (birth certificate; else passport field; else role-confirmed NID). R's own document is not judged against itself.
2. **Reads per document** d: the page reads of P on d (extractors), the machine reads on d (`MachineRead`, letters only), and the re-reads made below. A death certificate is the deceased parent's document.
3. **Letters rule (A1)** per document: if every read's `strict.letters` equals R's, the letters are fine. Otherwise, for the variant V (letters ≠ R):
   - V is CONFIRMED when two independent reads agree on V's letters — two different pages of d, a machine read on d, or `confirm(page, V) == "confirmed"` — AND no read of d (page read or re-read) gives R's letters. When V has one read only, `confirm` is asked (within the budget).
   - The verdict is `strict.judge(strict.compare(R, V, dots=False), covered(aff, R, V), confirmed=V_confirmed and R.confirmed)`: FAIL "<person>'s name is <V> here (pages N, M) but <R> on the <R source>; no affidavit covers it"; NOTE when covered; otherwise FLAG "check by eye, page N" — with the reason "a closer read shows <R> — probably OCR" when the re-read refuted V. Never PASS.
   - A truncated MRZ (`ident.student.truncated`) and a V that only extends R at its end: FLAG "the MRZ name may be cut (39 characters)", never FAIL.
4. **Dot rule (A2)**: `others` = every document whose letters all equal R's (including the `extra_reads` from bank and the chain), each with its written reads and `confirmed` (two reads agree on one dot pattern and none disagrees; a lone dot that differs from the others' agreed pattern is re-read with `confirm` first). `strict.dot_rule(R.written or None, others)`. FAIL becomes NOTE when `covered(aff, R.written, doc spelling)`. Rule `names.<person>_dots`; FAIL message "the dots in <person>'s name are mixed across the documents, and this one (page N) differs from the passport's printed name; no affidavit covers it"; FLAG "the dot in <person>'s name differs on page N — check by eye".
5. **Mapping.** Findings go to their units by `Read.unit` (extra reads: to their bank / financial unit). The summary gets a PASS "<person>'s name is the same on N documents" when nothing else fired.
6. **`family_first_row`**: `strict.find_run(ident.student.parts, first 1200 characters of the family certificate, any_order=True)`: PASS when found, FLAG when not.

Every re-read is listed in `NamesResult.rereads` (crop path, text, confidence, effect) for by-eye review (T3).

#### 5.9.2 bank.py (Task 4)

The extractor section of `bank_holder.py` is copied verbatim (lines 1-307: `Holder`, `holder_from_solvency`, `is_solvency_page`, `holders_from_solvency_pages`, `holder_from_statement`, `holders_from_statement_pages`).

```python
@dataclass
class BankOutcome:
    findings: List[Finding]
    holder: Optional[Holder]          # the solvency holder used for the chain (None when not read)
    holder_role: str                  # "father" | "mother" | "student" | "other" | ""
    solvency_pages: List[int]         # 0-based run pages that pass is_solvency_page
    solvency_missing: bool            # the bank pages read well (>= 300 characters each) and none is a certificate
    person_reads: List[Tuple[str, str, str, int]]   # (person, uid, written, page) for the names dot rule
def check_bank_unit(unit: DocUnit, page_texts: List[str], page_index: List[int], ident: Identity,
                    covered: Callable[[str, str], bool], confirm: Callable[[int, str], str],
                    solvency_pdf: Callable[[int], Optional[Path]]) -> BankOutcome
def dates_rows(page_texts: List[str]) -> List[Finding]
```

**Rules, in order** (A7; pages are 1-based input pages):

1. **`bank.solvency_present`.** No page passes `is_solvency_page` or matches `dv._CERT_WORDS`: when every page reads ≥ 300 characters, `solvency_missing = True` (verdict prints "BANK SOLVENCY CERTIFICATE MISSING"; critique 5); otherwise FLAG "no solvency certificate page could be read in the bank document — check by eye" (Q2).
2. **`bank.holder_read`.** `hs = holders_from_solvency_pages(page_texts)`. Empty while a solvency page exists: FLAG "the account holder's name could not be read on the solvency certificate — check by eye" (Q2).
3. **`bank.holder_parent`** (A7, critique 10), judged for EVERY name: each holder's `name` and every name in its `joint` list. For a name n, compare letters only (`dots=False`; dots go to the names dot rule through `person_reads`) with the father's two references (`ident.father`, `ident.father_nid`), the mother's two, and the student (`any_order=True` against the MRZ words):
   - EQUAL to the father or mother on either reference: PASS "account holder <n> is the student's father/mother (as on the birth certificate | as on the father's NID)". Sets `holder_role`; n goes to `person_reads`.
   - EQUAL to the student and to no parent: FAIL "the account is in the student's own name — the holder must be the father or mother" (`holder_role` "student"); in a joint account this is FLAG (Q2).
   - Otherwise take the closest parent reference (`strict.best_match`) and judge `strict.judge(kind, covered(n, ref), confirmed)`, where confirmed = the holder read is confirmed (the same letters on a statement header page of this unit, or `confirm(page, n) == "confirmed"`) AND the parent reference is confirmed. FAIL "account holder <n> is not the father or the mother (<kind> against <parent>); no affidavit covers it" (`holder_role` "other"); NOTE when covered; FLAG "... check by eye, page N" otherwise.
   - Joint account: additionally FLAG "joint account (holders: ...) — owner decides (Q2)". The holder rule passes when any joint holder is a parent; the worst of the per-name verdicts otherwise.
4. **`bank.dates`** (`dates_rows`). The solvency page is the first `is_solvency_page` page, wherever it sits; the statement pages are the rest. Compare `dv.document_date(solvency)` with `dv.statement_date(statement pages)`: PASS equal, FLAG different, NOTE when one or both are unread; the bot's own messages (L1445-1455).
5. **`bank.usd`** (critique 15), on the solvency page(s) only: `C.has_any(text, R.USD_WORDS)` → PASS "solvency amount stated in USD (page N)" / FLAG "no USD equivalent found on the solvency certificate (page N)".
6. **`bank.seal`** (critique 15), on the solvency page only: `PC.seal_present(solvency_pdf(i))` or signature wording on that page (`(?i)authori[sz]ed\s+signat|signature|seal`) → PASS "seal or signature on the solvency certificate (page N)" / FLAG "no seal or signature found on the solvency certificate (page N)". **Revision 2.5:** also a handwritten signature or a round stamp's ring found on the page image (`bank.ink_marks`); the message says what was found ("handwritten signature found, no stamp, ..."); see the revision 2.5 part B changelog.

The statement-header holder read is no longer a row (critique 20); it is used only as a confirming read in rule 3. The minimum balance (KLP/EAP 1.8M, Bachelor's/Master's 2.5M, FAIL only when clear), the opening balance and the one-page rule stay the bot's rows (section 5.8). **Revision 2.5 (owner rule 28 Oct): the opening-balance rule is dropped** — the bot's opening-balance rows become one NOTE "opening balance not judged (owner rule 28 Oct)".

#### 5.9.3 tin.py (Task 4)

`tin_qr.py` is ported: the decode ladder A/B/E/C/D, `render`, `read_page`, `read_qr`, `parse_tin_qr`, `tin_section`, `printed_tin_fields`, `tin_agreement`, `check_tin_certificate`. Changes (critique 2, A6 i/j, critique 20):
- `tokens` and `name_agreement` are deleted. `compare`/`verdicts` judge the QR name and the QR father's name with `strict.compare(qr, printed, dots=False)` and `strict.judge(kind, covered=False, confirmed=<printed read confirmed: two pages or confirm()>)` (the QR side is a machine read): EQUAL → PASS; confirmed difference → FAIL "the QR's taxpayer name is not the printed one — the certificate may be altered"; otherwise FLAG.
- `parse_return_qr`, `return_qr` and the "other QR codes" NOTE are removed.

```python
def check_tin_certificate(path, text: str, page_types: Optional[List[str]] = None, max_pages: int = 20,
                          confirm: Optional[Callable[[int, str], str]] = None) -> Dict[str, Any]
def tin_findings(result: Dict[str, Any], unit: DocUnit, pages: List[PageRead]) -> List[Finding]
    # rules: tin.qr (no / undecoded QR: FLAG), tin.qr_tin (PASS / FLAG / FAIL "may be altered"),
    #        tin.qr_name (PASS / FLAG / FAIL), tin.qr_father (PASS / FLAG / FAIL), tin.web_address (below)
def tin_page_types(unit: DocUnit, types: List[PageType]) -> List[str]   # "tin" for tin_certificate pages, else ""
def web_hosts(text: str, payloads: Sequence[str]) -> List[str]          # hosts from https?:// URLs and www. names
def tax_office(text: str) -> Dict[str, str]                             # {"circle": "<digits>", "zone": "<digits>"}
    # circle: (?i)circle\s*[-:#.]*\s*(?:no\.?\s*)?(\d{1,3}); zone: (?i)zone\s*[-:#.]*\s*(?:no\.?\s*)?(\d{1,2});
    # numbers compared as integers
```

**`tin.web_address`** (A6 j, NEW, FLAG only, Q14): the hosts printed on the TIN pages (browser print header/footer) and in the TIN unit's QR payloads. Any host not ending in `.gov.bd` → FLAG "the TIN certificate was printed from <host>, not a government (.gov.bd) address — verify the TIN on the NBR site". Every host `.gov.bd` → PASS "printed from <host>". No host read → no row.

The TIN QR is the PRIMARY taxpayer source (A6 i): `check_tin_certificate(...)["qr"]["name"]`.

#### 5.9.4 financial.py (Task 4) — port of `fin_names.py`

**Kept verbatim:** the extractors `trade_proprietor`, `owner_candidates`, `employee_name`, `assessee_name` (unused by rules; kept for the report) and `tin_taxpayer` (its `import tin_qr as Q` becomes `from . import tin as Q`); the vocabulary and regexes.

**Replaced by `strict.py`:** `norm`, `name_tokens`, `same_person`, `disagreement`, `clearly_different`; the verbatim copy of the bot's `name_in` is deleted.

**`owner_slot`** (critique 3): returns OWNER only when the owner row's WHOLE value (from its label to the next label or stop word) gives `strict.compare(value, taxpayer, dots=False) == "EQUAL"`. A taxpayer merely contained in a longer owner value is not OWNER; it falls through to `best_owner_match` and `strict.judge`.

```python
def best_owner_match(taxpayer: str, owners: List[str]) -> Tuple[str, str]    # strict.compare kinds, closest first
def chain_findings(taxpayer: Dict[str, Any], tin_unit: Optional[DocUnit], owner_units: List[DocUnit],
                   owner_texts: Dict[str, str], holder: Optional["bank.Holder"], bank_unit: Optional[DocUnit],
                   ident: Identity, covered: Callable[[str, str], bool],
                   confirm: Callable[[int, str], str]) -> Dict[str, List[Finding]]    # uid -> findings
def chain_person_reads(...) -> List[Tuple[str, str, str, int]]   # names EQUAL (letters) to a parent's reference
def income_tax_findings(unit: DocUnit, text: str, tin_text: str, program: str) -> List[Finding]
```

**`chain_findings` rules** (A7: taxpayer = holder = trade-licence owner / employee, A1 strict). Every comparison is `strict.judge(strict.compare(a, b, dots=False), covered(a, b), confirmed)`, where confirmed = both sides confirmed (a QR name is a machine read; an OCR read needs a second page or `confirm`). Findings go to the TIN unit and are echoed to the owner/employee unit; with no TIN unit, to the owner/employee unit; with neither, to the bank unit; with none of them, the cross line only (critique 11).

| Rule | Comparison | Levels |
|---|---|---|
| `financial.taxpayer_read` | No taxpayer name (QR and print both empty). | FLAG |
| `financial.taxpayer_print_only` | The taxpayer name came from the print because no QR was read. | FLAG |
| `financial.taxpayer_owner` | Trade licence: `owner_slot(trade_text, taxpayer) == "OWNER"` → PASS; otherwise `best_owner_match` over `owner_candidates`, judged. No owner read → FLAG. | PASS / NOTE / FLAG / FAIL |
| `financial.taxpayer_employee` | Employment certificate: `employee_name`, same judging. | PASS / NOTE / FLAG / FAIL |
| `financial.taxpayer_holder` | Against `holder.name` from `bank.py`. Holder unknown or no bank document → FLAG "the bank holder could not be compared (no solvency holder read)". | PASS / NOTE / FLAG / FAIL |
| `financial.holder_owner` | Holder vs owner/employee directly, when no taxpayer was read (critique 11). | PASS / NOTE / FLAG / FAIL |

Removed: `financial.taxpayer_parent` (critique 20: holder-is-parent plus taxpayer = holder already covers it), `tin.missing` (now the TIN slot's MISSING line), `tin.qr_dob_parent`.

**`income_tax_findings`** (A6 j): only **`income_tax.tax_office`** (NEW, FLAG only, Q14): `tin.tax_office(return text)` vs `tin.tax_office(TIN certificate text)`. Both circles read and different, or both zones read and different → FLAG "the return names circle X, zone Y; the TIN certificate circle A, zone B — check the tax office seal and the return by eye". Equal → PASS "same tax circle/zone as the TIN certificate". Unread on either side → no row. It stays a FLAG for every program (a fraud signal), so a non-Master's income-tax line is PRESENT unless this fires. `income_tax.assessee` and `income_tax.tin` are removed (critique 20).

#### 5.9.5 Date of birth and passport-number consistency (in doccheck, Task 5)

- `doc.dob_consistency` compares `dv.labelled_dob(text)` on the student NID and the SSC certificate (academic level "SSC") with `ident.dob`, when that date is trusted. PASS when equal, FLAG when different, nothing when unread. It is never FAIL. The birth certificate's DOB is `birth.py`'s (section 5.12).
- `doc.passport_no_consistency`: a different passport-format number after a "Passport No" label on an affidavit or solvency gives FLAG. The language certificate has its own ID check.

### 5.10 verdict.py (Task 6)

```python
MISSING_MAKES = FLAG               # Q4: the overall when MISSING lines are the only problem
def doc_verdict(findings: List[Finding], blocked: bool = False) -> str
    # NOT JUDGED when blocked; else FAIL > FLAG (ERROR counts as FLAG, its message prefixed "could not be
    # checked: ") > PASS (NOTE counts as PASS); PRESENT when a PRESENT finding is there and nothing is FLAG/FAIL.
def merge(res: SplitResult, *per_unit_maps: Dict[str, List[Finding]],
          names: "names.NamesResult", chain_summary: List[Finding] = (), bundle: List[Finding] = (),
          program: str = "") -> Tuple[List[DocResult], List[Finding], List[Notice]]
    # Every unit's findings = unit.notes + every map's list for its uid, in stage order.
    # ORDER-level findings are lifted out of the units into Notice(kind="ORDER", blocks=False) (critique 8).
    # Units named by a blocking ORDER notice get NOT JUDGED with the reason "fix pages a-b, then check again".
    # cross = names.summary + chain_summary + bundle. Notices are returned in page order.
def missing_lines(res: SplitResult, program: str, bank: Sequence["bank.BankOutcome"]) -> List[Tuple[str, str]]
    # [(MISSING, split.MISSING_TEXT[s]) for s in res.missing]  (slot order)
    # + (MISSING, split.SOLVENCY_MISSING_TEXT) when any bank outcome has solvency_missing
    # + (FLAG, f"{split.MISSING_TEXT[s]} unless the death certificate on pages a-b is the {parent}'s — check by eye")
    #   for each res.maybe slot (critique 17)
def overall(docs: List[DocResult], missing: List[Tuple[str, str]], res: SplitResult, cross: List[Finding],
            notices: List[Notice]) -> Tuple[str, str]
    # STOP when res.stop; else FAIL when any FAIL (documents or cross); else FLAG when any FLAG, ERROR,
    # NOT JUDGED, ORDER notice or missing line (MISSING_MAKES); else PASS.
    # headline: "<OVERALL> — <n> FAIL, <n> FLAG, <n> MISSING[, <n> ORDER]"
```

### 5.11 report.py (Task 6)

```python
def summary_text(r: RunResult) -> str
def write_excel(r: RunResult, path: Path) -> None            # openpyxl (vendored)
def write_json(r: RunResult, path: Path) -> None             # dataclasses.asdict; Path->str, date->iso, tuple->list
def write_all(r: RunResult, out: Path) -> Dict[str, Path]    # summary.txt, report.xlsx, run.json (safety.assert_writable)
```

#### 5.11.1 summary.txt (chat-ready, UTF-8, ≤ 80 lines)

```
<case_id> | <PROGRAM> | <bundle|folder> | <N> pages | RESULT: <PASS|FLAG|FAIL|STOP>
<headline>
READING: GPU pre-read <n> pages, CPU light engine <n>, text layer <n>[ — <reading note>]
ORDER: OK — the pages fit the fixed order                   (only when there is no ORDER notice of any kind)
ORDER: fix pages <a>-<b> — <message>                         (one per ORDER notice, no cap)
STOP: <message>                                              (only on STOP: the NID-person ambiguity; then no document lines)
HINT: <message>                                              (missing-document hints)
NOTE: <message>                                              (split NOTE notices)
<TITLE> (p<a>-<b>): <PASS|FLAG|FAIL|PRESENT|NOT JUDGED>[ — <reason>; <reason>; <reason> (+N more)]
...                                                          (one line per unit, in page order; one per academic level)
<X> MISSING                                                  (one per missing slot, e.g. "BIRTH CERTIFICATE MISSING")
<X> MISSING unless the death certificate on pages a-b is the <parent>'s — check by eye
NAMES: <worst> — <reason> ...                                 (student / father / mother)
FINANCIAL CHAIN: <worst> — <reason> ...                       (taxpayer = holder = owner/employee; holder = parent)
BUNDLE: <FLAG> — <reason>                                     (bundle-level findings, e.g. colour)
NEW CHECKS (owner to confirm): <rule> on <TITLE> (p<a>-<b>)   (only when a NEW_CHECK_RULES finding fired)
Not checked (present): ID CARD / CV (p<a>-<b>)
Not checked (not a PDF or image): <file>; <file>              (folder mode, revision 2.3: Source.not_checked)
Not checked (could not be opened): <file>
Report: <out>\report.xlsx | JSON: <out>\run.json
```

- Reasons are FAIL first, then ERROR, then FLAG, each at most 160 characters. PASS and NOTE are not listed.
- In folder mode, `(p<a>-<b>)` becomes `(<file name>)`; for a unit cut out of a file that holds several documents
  (revision 2.3) `(<file name> p<a>-<b>)`, the pages inside that file.

#### 5.11.2 report.xlsx (sheets in this order; critique 20)

**Summary** — the summary lines, one per row, with the verdict cell coloured by verdict. Below them, a run-facts block: versions, case_id, input, mode, program, today, reading (pages per engine, note, page-texts file), timings, engine stats, the safety audit (blocked events: 0, opens under `C:\Hangeul\BOT`: 0), the input snapshot before and after, the identity sources (student, father, mother, DOB, expiry: source and trusted/confirmed), and the new checks that fired.

**Documents** — uid, title, key, fine, level, pages, cut_file, role, role_how, verdict, FAIL reasons, FLAG reasons, notes, pass_count.

**Pages** — one row per input page:

| Column | Content |
|---|---|
| page | 1-based |
| file | file name |
| file_page | page within the file |
| read | layer / ocr / image |
| engine | gpu / cpu / layer |
| chars | characters read |
| seconds | read time |
| sideways | whether the page was turned |
| type | best fine type |
| score | its score |
| second | second-best type: score |
| translation | translation cue |
| academic_level | academic level |
| kind | apostille / certificate / transcript / solvency / statement |
| unit | uid |
| document | unit title |
| role | role |
| role_how | how the role was found |
| misplaced | misplaced flag |
| notices | ORDER/HINT/NOTE/STOP text touching the page |
| doc_verdict | the unit's verdict |
| fails | FAIL findings on the page |
| flags | FLAG findings on the page |
| text_start | first 160 characters, newlines shown as " \| " |

**Findings** — uid, title, pages, verdict, rule, source, message, evidence (short JSON). It holds every finding: identity notes, name findings with their reads and re-reads, the financial chain, the bank holder, the TIN QR, the birth-certificate rows, and the notices (uid "", verdict = notice kind).

#### 5.11.3 run.json

`dataclasses.asdict(RunResult)` with `schema = "runner-run/2"`. Page texts are included in full: it is the private review copy. Two extra keys:
- `"names_rereads"` (`NamesResult.rereads`);
- `"tin"` (the raw `check_tin_certificate` dicts, without image arrays).

### 5.12 birth.py (Task 7) — the birth certificate (A3)

A port of the lab study `C:\Hangeul\DOCCHECK\lab\private-runner\birth_cert\` (`birth_cert.py`, `birth_qr.py`, `dob_boxes.py`), read-only references.

**Ported:** `page_kind` and the format detection; `find_dates_ocr`, `_boxed_candidates`, `extract_dob_boxed`, `reread_boxed_dob` (image re-read of the boxes), `in_words_reading` / `parse_dob_in_words`, `labelled_dob`, `issue_and_registration`, `extract_brn`, `NOTARY_WORDS` and the seal-colour test; `decode_birth_qr` (QR ladder at smaller scales, blur, adaptive thresholds, thinning), `parse_payload`, `decode_barcode_image` / `code128_votes` / `zbar_votes`, `offline_match` without `key_elsewhere`; `find_dob_row` and `read_dob_groups`.
**Not ported:** `qr_lookup` (online), `extract_names` / `same_name` / any `name_in` (names are `names.py`'s), the key-reuse register (a run checks one case only; Q17). The month-word fuzzy match in the date reader keeps its `SequenceMatcher`, marked `# label-match only`.

```python
def page_kind(text: str) -> str
    # "bilingual_qr" | "english_bdris" | "everify" | "translation" | "bangla" | "manual_english" | "other"
def read_birth_certificate(unit: DocUnit, pages: List[PageRead], eng: Optional["Engine"], work_dir: Path,
                           notarised: Callable[[str, Optional[Path]], bool]) -> BirthFacts
    # notarised = the bot's dv.notarised(text, path) (wording OR seal). Page images: eng.page_image_of(pages[i], 300).
    # Without eng, text only (no QR, no image re-read of the boxes).
def check_birth_certificate(facts: BirthFacts, unit: DocUnit, ident: Identity, printed_name: str = "",
                            printed_confirmed: bool = False,
                            confirm: Optional[Callable[[int, str], str]] = None) -> List[Finding]
    # printed_name / printed_confirmed: the student's name as names.printed_student_name() read it on the
    # English pages (used only by the QR-name rule); confirm: the shared re-read (BRN confirmation)
def read_dob(text: str, image: Optional["np.ndarray"], eng: Optional["Engine"]) -> List[Tuple[str, dt.date]]
    # (source, date) readings on one page: "label" | "boxes" | "words"; never an issue or registration date
```

**Format** (`birth.format`):
| Format | Rule | Level |
|---|---|---|
| Bangla-only original (bdris Bangla print) WITH its English translation | fields read from the translation (`english_pages`) | PASS "Bangla-only certificate with its English translation" |
| Bangla-only original WITHOUT a translation | — | FAIL "Bangla-only certificate with no English translation — it must be translated and notarised" |
| New bilingual print (English + Bangla, QR top-left, barcode top-right) | self-sufficient (no translation needed) | PASS "new bilingual bdris certificate — needs no translation" |
| Older English bdris print (BDR Form-3; everify variant) | self-sufficient | PASS "older English bdris print — needs no translation" |
| An English translation alone (the original it translates not found) | — | FLAG "only an English translation; the original was not found — check by eye" |
| An older hand-issued English form (manual_english) | — | FLAG "older hand-issued certificate, not a bdris print — check by eye" |
| Not recognised | — | FLAG "birth certificate format not recognised — check by eye" |

**Notary** (`birth.notary`, every format; for a Bangla original the translation must carry it): notarised → PASS; a seal colour without notary wording → FLAG "a seal but no notary wording — check that it is a notary's seal"; neither → FAIL "no notarisation found — the birth certificate must be notarised".

**QR** (`birth.qr`, bilingual print only; the QR MUST be decoded and must match):
- Not decoded (a QR is seen but unreadable) or no QR found → FLAG "the QR could not be decoded — check the QR by eye".
- Host not `bdris.gov.bd` / `everify.bdris.gov.bd`, or not `https://` → FAIL "the QR does not point to the government bdris site" (a machine read is confirmed).
- The payload carries a name → `strict.compare(qr_name, printed_name, dots=False)`: EQUAL → PASS; a difference → `strict.judge(kind, covered=False, confirmed=printed_confirmed)` → FAIL "the name in the QR is not the printed name — the certificate may be altered" / FLAG. No printed name read → FLAG. The QR name is also passed to `names.py` as a `MachineRead` (it then confirms or contradicts the printed spelling against the passport).
- Revision 2.3: the payload carries a parent's name (`text_fields["father"]` / `["mother"]`: URL parameters father, fatherName, father_name, fathers_name and the mother's alike; text "Father's Name: ..." / "Mother's Name: ...") → `birth.qr_parent_reads` gives `MachineRead(person="father"/"mother", source="birth QR")`; `birth.qr_father` / `birth.qr_mother`: the letters equal the certificate's printed reference (any order, dots aside) → PASS, and `identity` takes the QR as the confirmation of the reference (and of its tail); a difference → FLAG "check by eye" (the printed side is one OCR read: never FAIL here; `names.py` then treats the reference as not independently confirmed). The genuine bdris link carries an opaque key: no parent name, nothing changes.
- The payload or the CODE128 barcode carries a BRN → equal to the printed BRN: PASS; different → FAIL when `brn_confirmed`, else FLAG.
- The payload carries a DOB → equal to the certificate's DOB: PASS; different → FAIL when `dob_trusted`, else FLAG.
- The opaque bdris key alone (the genuine format: 64 base64 characters = 48 bytes, no spaces) → PASS "QR is a genuine-looking bdris verification link" + NOTE "the name behind the bdris QR can only be checked online — not done (no network)". A malformed key → FLAG.
- The BRN's first 4 digits differ from the DOB year → FLAG `birth.brn_year`.

**Date of birth** (`birth.dob`):
- On a bdris print the DOB is the BOXED field "DD ■ MM ■ YYYY", black blocks between day, month and year. OCR may read a block as `1`, `l`, `I`, `|` or `i`, or drop it; `_boxed_candidates` and the image re-read (`find_dob_row` + `read_dob_groups` on the page image) read the three groups without passing the blocks to OCR. Inside a date token `O` folds to `0`.
- The label reading (`DD/MM/YYYY` after a "Date of Birth" label) serves the bilingual print and translations.
- NEVER the Date of Issue or the Date of Registration: their label windows are excluded and `issue_date` / `registration_date` are kept apart (the owner: "previously it misunderstood").
- The "In word(s)" line is the cross-check. Boxes (or label) and words agree → `dob`, `dob_trusted = True`. They disagree → `dob = None`, FLAG "DOB unclear, check by eye (page N)". One source only → `dob` set, untrusted.
- Against the passport: `ident.dob_trusted` and `dob_trusted` and different → FAIL "the certificate's date of birth X (boxes and words agree) is not the passport's Y (MRZ)". Either untrusted and different → FLAG "DOB unclear, check by eye". Equal → PASS. No reading → FLAG "the date of birth could not be read — compare it with the passport by eye". No passport DOB → FLAG.

Names on the birth certificate are not judged here: `names.py` reads them from `english_pages` (the student against the passport; the parents are the canonical reference, A3).

### 5.13 gpuread.py and run_case.py (Task 8) — the GPU pre-read (A4)

**`gpuread.py`** runs in the bot venv, as a separate process (section 2.5):

```
C:\Hangeul\BOT\.venv\Scripts\python.exe -I -B C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\gpuread.py ^
    "<bundle.pdf | folder>" <OUT page_texts.json> [--wait-s 120] [--slot-s 90]
<any runner-venv python> -I -B ...\gpuread.py --from-legacy <legacy pages.json> --source <PDF> --out <OUT.json>
```

```python
GUARD = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\scripts\gpu_guard.py")
CAP_GB = 3.0                # gpu_guard.BOT_PC_MAX_CAP_GB on this PC
NEED_FREE_GB = 4.0
def read_all(src: Source, dv, checkpoint: Callable[[], None], done: Dict[str, Any],
             save: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]
    # The core loop, testable with a fake dv: per file, dv.read_pages(path, max(20, n), sideways) with
    # dv._ocr_image wrapped (read_gpu_pages.on_card): checkpoint() before every OCR page (never for a text-layer
    # page), pages already in `done` reused (resume), save() after every page. Image files: dv.read_document(path)
    # under the same wrapper. Returns the runner-page-texts/1 dict.
    # Revision 2.3 (card memory; signature + free: Optional[Callable[[], None]] = None): a page whose read raises
    # CUDA out-of-memory (is_oom: torch.OutOfMemoryError or a RuntimeError saying "out of memory") is read again
    # after free() (gc + torch.cuda.empty_cache, from run_session) with its long side cut to OOM_LONG_SIDES =
    # (2000, 1600) pixels (cv2 INTER_AREA; the page record gets "downscaled": <px>); an image file is read again as
    # an RGB array the same way. A page that still runs out of memory is left out of "pages" and listed in the file
    # entry's "skipped" [{"page", "reason"}]; read_pages gets "" for it and goes on with the other pages;
    # "complete" is false and the runner reads that page on the CPU. Any other error still ends the run (saved
    # first). CAP_GB is never raised.
def convert_legacy(legacy: Path, source: Path, out: Path) -> None
    # test1\tools\read_gpu_pages.py output {"meta", "pages": {"1": {...}}} -> runner-page-texts/1 (sha256 of the
    # source PDF added; n_pages must agree). No card.
def main(argv: Sequence[str]) -> int
```

- **Order of `main`:** parse arguments (OUT must be under private-runner) → `safety.install_write_guard(extra_allowed=[gpu_guard.DEV_LOCK], block_bot_opens=False)` (S1, S4, S11) → put the worktree first on `sys.path`, `botimport.load(PRIVATE / "gpu-data")` → `src = ingest.open_input(input)` → load `gpu_guard` by path → `with gg.gpu_slot(what, need_free_gb=NEED_FREE_GB, wait_s=wait_s, max_s=slot_s) as slot:` → `slot.check_cap(CAP_GB)` → `with gg.capped_process():` `torch.cuda.set_per_process_memory_fraction(CAP_GB × 1024³ / total × 0.999, 0)` → `reader = dv._ocr_reader()`; assert `str(reader.device).startswith("cuda")` → `read_all(...)`.
- **Output:** OUT is rewritten atomically (`OUT.tmp` then `os.replace`) after every page, with `"complete": false` until every page is read. Pages read in an earlier slot are reused.
- **Exit codes:** 0 complete; 3 stopped at a checkpoint (`SlotInterrupted`: the bot's check started; `SlotExpired`: the slot ran out) — run again to continue; 5 no GPU slot within `--wait-s` (`gpu_slot` TimeoutError); 4 CUDA not available or the cap refused; 2 bad arguments; 1 crash.
- Prints counts and timings only (pages, seconds, slots, peak memory), never page text.

**`run_case.py`** (`C:\Hangeul\DOCCHECK\lab\private-runner\run_case.py`, runner venv; command in section 1.2):

```python
BOT_PY = r"C:\Hangeul\BOT\.venv\Scripts\python.exe"
RUNNER_PY = r"C:\Hangeul\DOCCHECK\lab\private-runner\.venv-runner\Scripts\python.exe"
GPUREAD = r"C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\gpuread.py"
MAX_RUNS = 12
def plan(args, call: Callable[[List[str]], int]) -> Tuple[Optional[Path], str]
    # the GPU loop with an injectable `call` (subprocess.call in main, a fake in tests):
    # returns (page_texts path or None, reading note)
def main(argv: Sequence[str]) -> int
```

1. Checks `--out` with `safety.check_out_dir` (nothing written on refusal: exit 2).
2. Unless `--cpu`: runs `[BOT_PY, "-I", "-B", GPUREAD, input, out\page_texts.json, "--wait-s", "120"]` up to `MAX_RUNS` times: 0 → done; 3 → run again (resumes); 5 → fall back: note "GPU slot not available within 120 s — <n> page(s) read on the CPU light engine"; 4, 1 or anything else → fall back with the note "GPU pre-read failed (exit <code>) — ...". Revision 2.3: a page-texts file that belongs to the input (every entry's name, sha256 and page count) is given to the runner after ANY worker exit (the runner reads only the missing pages on the CPU); the note counts the pages each engine read ("<g> page(s) read on the card (+<l> text layer), <n> page(s) read on the CPU light engine"); exit 0 with pages left out for card memory says "GPU pre-read left <k> page(s) out (too large for the card's memory cap)".
3. Runs the runner: `[RUNNER_PY, "-I", "-B", "-m", "src.verify.runner", input, "--program", P, "--out", out, ("--page-texts", json) when it exists (complete or partial), "--reading-note", note, ("--today", D)]` and returns its exit code.
4. Starts and waits only for its own children; never kills another process; on Ctrl-C it ends only its own child. Writes `out\run_case.log` (timings and exit codes, no text).

---------------------------------------------------------------------------------------------------------

## 6. Owner rules → code location → verdict levels

| # | Owner rule | Code location | Verdict levels |
|---|---|---|---|
| R1 | No portal data at all | `model.DerivedRecord.portal_view` (all empty); S2/S7 audit hook; `identity.build_identity` | — |
| R2 | Student name, passport number, DOB and expiry from the passport (MRZ) | `identity.build_identity` (+ `mrz_from_words`, `reread_mrz`); `doccheck.passport_rows` | validity: FAIL only on a trusted expiry (expired or < 12 months); scan date → FLAG; number: PASS / FLAG; a valid MRZ is trusted (no MRZ FLAG/NOTE) |
| R3 | Parents' names from the birth certificate (A3), with every document as vocabulary | `identity.build_identity` step 3 + `finish_identity` via `names.canonical` | NOTE parents from passport; FLAG unknown |
| R4 | Program from the owner's choice (A8) | `__main__` `--program`; passed to `R.REQUIRED`, `R.MIN_BANK_TAKA` and the other tables | — |
| R5 | Fixed bundle order; say which pages to fix | `split.segment` (align), `split.finish` (notices) | ORDER (blocking → NOT JUDGED units; non-blocking → judged); HINT; NOTE |
| R6 | A missing REQUIRED document → "X MISSING", others still judged | `split.finish` → `SplitResult.missing` / `maybe`; `verdict.missing_lines` | MISSING line; conditional FLAG line; overall FLAG (Q4) unless a FAIL |
| R7 | Optional documents never reported missing | `split.OPTIONAL_SLOTS` | — |
| R8 | Names STRICT, letter for letter, dots count (A1) | `strict.py`; `names.evaluate`; `bank.py`, `tin.py`, `financial.py`, `language.py` through `strict` | PASS only on EQUAL; confirmed + uncovered → FAIL; unconfirmed → FLAG "check by eye, page N"; never PASS on a difference |
| R8a | Dot rule (A2): dots against the passport's PRINTED name; mixed dots across the other documents → FAIL | `strict.dot_rule`; `names.evaluate` step 4 | PASS / FLAG / FAIL (NOTE when covered) |
| R9 | Birth certificate mandatory; formats, notary, QR, boxed DOB (A3) | `split.SLOTS` "birth_cert"; `birth.read_birth_certificate` + `check_birth_certificate` | MISSING; format FAIL (Bangla without translation) / FLAG; notary FAIL; QR FAIL (non-gov host, confirmed mismatch) / FLAG (undecodable); DOB FAIL (both trusted and different) / FLAG (unclear) |
| R10 | Student NID optional | `split.OPTIONAL_SLOTS`; `doccheck` `check_nid` + REWRITE | bot rows when present |
| R11 | A death certificate replaces a parent's NID | `split.SLOT_ACCEPTS`; roles in `split.finish`; `doccheck` `death.stands_in` (only when the NID is absent); the deceased's name through `names.py` | PASS stands in; names levels; FLAG unknown role + conditional MISSING line |
| R12 | Income tax required for every Master's; information only otherwise (A6 h) | `split.SLOTS` + "income_tax" for MASTER; `dv.check_income_tax`; non-Master's REWRITE to NOTE + PRESENT | MISSING (Master's); bot rows (Master's); PRESENT + NOTEs (others) |
| R13 | ID card / CV not checked | `doccheck` id_card_cv → PRESENT; no page checks | PRESENT |
| R14 | Study plan not required | `split.OPTIONAL_SLOTS` (personal_statement) | — |
| R15 | Apostille FAIL kept | `dv.check_academic` → `PC.academic_check`, one unit per level; bundle page-1 rule → ORDER (critique 8) | FAIL / FLAG / NOTE / PASS (bot); ORDER notice |
| R16 | Family certificate valid 3 months; a cited recent affidavit date counts | `dv.check_family_cert` unchanged; `engine.set_today` | PASS / FAIL / FLAG (bot) |
| R17 | Statement-period rule removed | not called (`dv.statement_period_check` is never used) | — |
| R18 | Minimum balance: KLP/EAP 1.8M, Bachelor's/Master's 2.5M; FAIL only when clear (A7) | `dv.balance_check` via `check_bank` (`R.MIN_BANK_TAKA`) | PASS / FLAG / FAIL (clear only) |
| R19 | Opening balance: KLP/EAP ≥ 1.8M; Bachelor's/Master's ≥ 2.2M and ≤ 2.6M preferred | `dv.opening_balance_check` (`R.MIN_OPENING_BALANCE`, `R.PREFERRED_OPENING_MAX`) | PASS / FLAG / NOTE |
| R20 | USD equivalent on the solvency | `bank.py` rule 5, solvency page only (critique 15) | PASS / FLAG |
| R21 | Seal / signature | `bank.py` rule 6, solvency page only (critique 15) | PASS / FLAG |
| R22 | Solvency date = statement date | `bank.dates_rows` (solvency page found by content); bot rows DROPped | PASS / FLAG / NOTE |
| R23 | ≥ 2 pages | `check_bank` L1457-1459 | FLAG |
| R24 | Account-age rule removed (A7) | `doccheck.DROP["bank"]` `^account opened ` | — |
| R25 | Holder named on the SOLVENCY; must be the father or mother, strict, against the birth certificate OR that parent's role-confirmed NID (A7) | `bank.holders_from_solvency_pages`; `bank.check_bank_unit` rules 1-3; `identity.finish_identity` (`father_nid`, `mother_nid`) | PASS / NOTE (affidavit) / FLAG / FAIL; "BANK SOLVENCY CERTIFICATE MISSING" |
| R26 | The TIN QR checked by the system and matching the print; the QR is the primary taxpayer source (A6 i) | `tin.check_tin_certificate` + `tin.tin_findings` (strict, critique 2) | TIN: PASS / FLAG / FAIL; name and father: PASS / FLAG / FAIL; no QR: FLAG |
| R27 | Taxpayer = bank holder = trade-licence owner / employee, "100%" (A7) | `financial.chain_findings` (whole-value owner row, `holder_owner`) | PASS / NOTE / FLAG / FAIL |
| R28 | Affidavit: a covered name mistake is not a problem; a name mistake without an affidavit is a hard red | `names.covered` (letters and dots, one same-person group) injected into `bank`, `financial`, `language`, `birth` | NOTE covered; FAIL confirmed; FLAG otherwise |
| R29 | IELTS/TOEFL offline checks; minimum scores are placeholders | `language.check_language_file`; `language.MIN_SCORES` (None → NOTE); DOB passed only when trusted | per `language.py`: PASS / NOTE / FLAG / FAIL (expired) |
| R30 | Photo inside a bundle: background and shape only | `doccheck.crop_photo` (edge finding) + `photo_rows` KEEP_ONLY | PASS / FLAG / FAIL (background); FLAG edges not found |
| R31 | New rules not in the live bot now | S1/S6; runner-only code | — |
| R32 | The owner's tests read on the graphics card; the app's default stays the CPU engine (A4) | `gpuread.py` + `run_case.py`; `engine.load_page_texts` | READING line; CPU fallback stated |
| R33 | STOP only for the NID-person ambiguity; other units still judged (critique 6) | `split.finish` | STOP; ORDER |
| R34 | One line per document; one per academic level; employment and TIN separate (critique 16, A6 g) | `split.documents`; `verdict.merge` | — |
| R35 | MRZ whose lines OCR joined is read from its own word (A6 a) | `identity.mrz_from_words` | trusted MRZ |
| R36 | Board QR codes on SSC/HSC are read (A6 c) | `doccheck.board_qr` via `tin.read_qr` | PASS; machine read |
| R37 | NID number with glued OCR noise (A6 d) | `doccheck.nid_number_rows` | PASS / NOTE / FLAG |
| R38 | A trailing unrecognised page after income tax belongs to it (A6 f) | `split.documents` rule 3 | NOTE |
| R39 | NEW: TIN printed from a non-government address (A6 j) | `tin.web_address` | FLAG only ("new, keep?", Q14) |
| R40 | NEW: tax circle/zone on the return differs from the TIN's (A6 j) | `financial.income_tax_findings` → `income_tax.tax_office` | FLAG only ("new, keep?", Q14) |
| R41 | Colour scan in a bundle (critique 19) | `doccheck.page_rows` bundle colour | one bundle FLAG (Q15) |
| R42 | Folder: a file that holds several documents is cut into them, each judged; no false MISSING (rev 2.3) | `split.file_documents`, `split._units` (`DocUnit.part`), `pipeline._cut_parts`, `doccheck.part_page_refs`, `report.where` | per document; `split.name_vs_content` FLAG when no part is the named document |
| R43 | A name FAIL needs BOTH spellings confirmed: a reference outvoted by agreeing documents (rev 2.3) | `names._outvoted`, `names.ref_independently_confirmed` | one FLAG `names.<person>_reference` on the reference document; NOTE on the agreeing documents; a third spelling at most FLAG |
| R44 | The bilingual birth-certificate QR must decode and match: the parents' names when the payload carries them (rev 2.3) | `birth.parse_payload`, `birth.qr_parent_reads`, `birth._qr_parent_rows`, `identity._parent_refs` | PASS (and the reference confirmed) / FLAG |
| R45 | A folder file the runner cannot read is listed, never a document or a slot (rev 2.3) | `ingest.open_input` (`Source.not_checked`), `report.not_checked_lines` | "Not checked (not a PDF or image)" / "Not checked (could not be opened)" line |
| R46 | One page too large for the card's 3 GB cap does not end the GPU pre-read (rev 2.3) | `gpuread.read_with_retries`, `run_case.plan` | READING note counts each engine |

---------------------------------------------------------------------------------------------------------

## 7. Strict names, the closer look and the affidavit cover — one definition (revision 2.5)

The owner's rules of 27-28 Oct, as the runner applies them. Revision 2.5 rewrites this section for the closer look
(owner 28 Oct: "make sure if the reader misreads one letter of a name before giving verdict it will take a closer look
and give correct verdict. i want the final outcome of this as industry standard"); where the changelogs above differ,
this section wins.

**Letter for letter (A1).** A written name is reduced by `strict.form`: upper case, letters and dots only; spaces and
every other mark removed; a run of dots is one dot; a dot before the first or after the last letter is punctuation;
leading titles LATE/MR/MRS/MS/MISS/DR are dropped (Q16). Two names are the same ONLY when their forms are equal
(`compare(...) == "EQUAL"`). MD, MST, MOHAMMAD, BEGUM, initials and two-letter words are letters like any other: "MD"
vs "MOHAMMAD", or an added or dropped "MD" / "MST" / "BEGUM", is a difference. Case and spacing never are. Only a
machine read (MRZ, QR), which is surname-first and has no dots, is compared with `any_order=True` and without dots.
A FAIL needs the difference CONFIRMED; a difference is never PASS.

**Two kinds of check, one policy (critique 9):**
1. **"Is this written name that person?"** — bank holder, taxpayer, trade-licence owner, employee, deceased parent,
   language candidate, birth-QR name, role scoring. `strict.compare(a, b, dots=False)` (dots go to the dot rule) and
   `strict.judge(kind, covered, confirmed)`.
2. **"Is the person's name printed the same on every document?"** — `names.evaluate`: the letters rule (A1) per
   document against the reference, and the dot rule (A2) across the documents.

**The closer look (`closer.py`, owner 28 Oct).** When two reads of a name or a number disagree, the system looks at
the page image again ITSELF before any verdict. It does not read the line again; it asks which of the KNOWN spellings
the print supports (verification of a claimed value, the way cheque and form readers verify an amount):
`closer.look(image, box, candidates, kind)`, `candidates[0]` = the reference, the others = the OCR read(s); kind
"name", "dots" or "digits". The answer is "same" (the print is the reference: the other read was a misread),
"differs" (the print is another candidate: a real difference) or "unclear" (the image does not settle it).
1. **Find the text.** The caller's box, else the page is searched: the CRAFT detector's line boxes in the four
   quarter turns and a keyword score of every candidate per box. The best line AND every other line matching about as
   well (within 0.5 nats per character, clean matches only, at most 3) are looked at; each line is joined with the
   boxes beside it and widened over ink the detector missed (word gaps under 1.5 line heights, never across a table
   border); the line under the matched cell is tried as a wrapped cell's second line.
2. **Views.** 28 images of the line: four vertical margins x five colour channels (grey, the darkest channel, R, G,
   B), contrast stretch, Otsu and adaptive binarisation, sharpening, bold, thin, two skews; small text enlarged 2-3x;
   printed rules painted out.
3. **Score.** On every view the english_g2 recogniser (the engine's CPU ONNX session; never DirectML; it must work in
   the CPU-only app) gives per-frame probabilities, and the CTC forward log-likelihood of EACH candidate is computed
   over the candidates' own region of the line (case-blind; a space or a label's colon free; '.' its own class for
   dots; '<' a separator for an MRZ line).
4. **Decide.** "same" / "differs" only when ALL hold, else "unclear":
   - the winner leads by `MARGIN` (0.12 nats per reference character) on average, in >= 85% of the views (`MOST`),
     with <= 10% of the views backing another candidate (`DISSENT`);
   - the winner reads as print: >= -1.6 nats per character (`MIN_SCORE`); for "differs" >= -1.0 (`DIFFERS_MIN`: a
     weak read of a script print is no confirmed difference);
   - every place where the winner differs from another candidate favours it by itself (`SEG_MIN` 2.0 nats: a minimal
     edit alignment, the winner against itself with that ONE place taken from the other spelling), and letters the
     winner adds read as print by themselves (`ADDED_MIN` -1.6 per letter: artwork, a stamp or a Bangla line does not);
   - the line alone and the line with the one under it support the same spelling (a wrapped cell or the next row);
   - every place of the page the search found gives the same decision.
   Two independent experts: the letter's shape (a small letter's height against the word's ascender and x lines: t /
   l, i / l, n / h ...) can settle a one-letter tie the recogniser misreads in every view, with at least one view
   backing it; the dot's shape (a small solid blob on the baseline between the letters) decides a dot when the
   recogniser's lead is under `DOTS_ALONE` (0.6); without a measured shape the recogniser may confirm a dot but never
   "no dot". `Look.dot` is given only with a decision.
5. **Never a guess.** No recogniser, text not found, a failure: "unclear". By construction a REAL difference can come
   out "differs" or "unclear", never "same" ("same" needs the reference to explain the print clearly better than the
   other spelling in nearly every view, at every place where they differ).

**What a look does to a verdict.**
- **Names** (`names.evaluate`, `names.Confirmer.look/pair`; every look logged in run.json `names_rereads` with its
  crop `work/look_pNN_k.png`). Every page (up to 3) of a document that reads the variant is looked at: "same" on every
  page -> a misread, no finding (the log says "misread corrected by closer look"); "differs" on any page -> the
  document prints the variant; anything else -> unclear. The reference's own page is looked at too. **FAIL** = the
  variant printed (a look "differs", or two pages of the document agreeing with no look "same") AND the reference
  supported (its own page's look "same"; when that is unclear, a machine read or another document printing it letter
  for letter) AND no affidavit covers the two spellings. The reference's own page showing the variant -> FLAG ("the
  passport's own read may be the misread"). Every other difference -> **FLAG "check by eye, page N"**, with what the
  look could not tell. **No outvote:** other documents never decide which spelling a document prints (the revision
  2.3 outvote, `_outvoted` / `ref_independently_confirmed`, is removed: it turned confirmed real differences, an added
  MST, into FLAG). A read too unlike the name to compare (a misparsed label) first gets a look for the reference itself
  on its page: printed clearly -> no finding. Revision 2.6: that single-spelling look also reads the words beside the
  value; a further word of the value there (an added MST, MD, BEGUM; not a label, a connecting word, a next field's
  label, a number or a leading title) makes it "unclear", never "same". An added word that is not an honorific, read
  on one page, is dropped only when the look on that page shows the reference ("same"); "differs" keeps a confirmed
  difference; "unclear" or no look is FLAG "check by eye" (Q18, revision 2.6). A word the OCR ran onto the name from
  a stamp printed far after it in another ink is cut from the line before the look (`closer.far_tail`).
- **The reference.** When other documents print another spelling of a parent, the look on the birth certificate's own
  page decides ("differs" corrects the reference: NOTE `identity.reference_corrected`); its tail ("reads on with ...")
  is looked at ("same" drops it, "differs" takes it, "unclear" keeps the FLAG). A noisy passport MRZ line 1: the
  printed name in MRZ order is verified on the passport page.
- **Numbers.** The TIN printed against its QR (kind "digits"): "same" PASS; "differs" or "unclear" FLAG check by eye,
  never FAIL (the page read and the look are one kind of recogniser and can share a look-alike misread). A date of
  birth that reads differently from the passport's: "same" PASS, otherwise FLAG.
- Without the recogniser (a test's plain confirmer) the legacy re-read rule of revision 2 applies: the zoomed re-read
  confirms (-> FAIL when uncovered), refutes (-> FLAG "probably OCR") or leaves unclear (-> FLAG); never PASS.

**Measured accuracy** (`selftest/closer_eval`, the targets in `private-runner\closer_eval\TARGETS.md`; closer-2,
sha256 02092e485e83; the real set is never used for tuning). Real set: 130 comparisons from the four owner cases and the
corpus audit, every label checked by eye. Synthetic set: 2400 made-up names (19 Windows fonts, nine degradations).

| set, mode (closer-2, sha256 02092e485e83, MARGIN 0.12) | FALSE PASS | FALSE FAIL | review | wrong dot |
|---|---|---|---|---|
| real, box (130: the name's box given) | 0 of 55 | 0 of 75 | 20.0 % | 0 of 17 given |
| real, production (130: the whole page, box=None, the runner's own candidates: how names.py, tin.py and doccheck.py call it) | 0 of 55 | 0 of 75 | 29.2 % | 0 of 15 given |
| synthetic, box (2400) | 0.17 % (2 of 1200; Wilson 95 % bound 0.50 %) | 0.00 % (bound 0.22 %) | 8.25 % | 0.26 % (1 of 380 given) |
| targets (TARGETS.md) | real 0; synthetic <= 0.5 % | real 0; synthetic <= 1 % | real reported; synthetic <= 5 % | real 0; synthetic <= 0.5 % |
| before the integration (11f4ffb2313b) | real box 0, production 2; synthetic 0.42 % | real box 0, production 16; synthetic 0.08 % | 16.2 % / 20.8 % / 7.5 % | synthetic 1.28 % |
| plain OCR, no closer look | real box 1 of 55 | real box 8 of 75 | 36.9 % | 9 of 19 |
| **real, box (closer-3, sha256 c8a809a7eb83, revision 2.6: the final code)** | 0 of 55 | 0 of 75 | 20.0 % | 0 of 17 given |
| real, production (closer-3) | 0 of 55 | 0 of 75 | 29.2 % | 0 of 15 given |
| synthetic, box (closer-3; 2400) | 0.17 % (the same 2 of 1200) | 0.00 % | 8.25 % | 0.26 % (1 of 380 given) |

closer-3 (revision 2.6) decides every one of the 2400 synthetic cases as closer-2 did (its changes act on a
single-spelling look, a far word in a stamp's ink and the dot probe, none of which the synthetic set offers),
and the real set gives the same numbers in box mode. Results: `fx3_real_box_c7`, `fx4_real_prod_c7`,
`fx4_synth_full_c7` (the last two merged from a run cut off by the end of a session and the runs of its remaining
ids with the same closer sha256; see TARGETS.md).

Every real target is met in both modes; on the synthetic set the error and wrong-dot targets are met and the review
target (5 %) is MISSED (8.25 %: dots 24 %, script fonts 26 %, x-height under 8 px 87 %, salt noise 18.5 %; plain
fonts 1-7 %). MARGIN was tuned on the synthetic set only: the exact sweep on a 600-case sample gives the same errors at
0.06, 0.12 and 0.25 and review 8.7 / 8.8 / 10.2 %, so 0.12 is kept (MARGIN is not the deciding gate). About 2-3 s a
look on the CPU (28 views); a page-wide look 4-15 s.

FALSE PASS = a real difference judged "same" (the worst error); FALSE FAIL = an identical name judged "differs";
review = "unclear" (FLAG check by eye). **Limits.** One recogniser: a print it misreads in nearly every view (bold
italic and ornate script board papers, dots in script faces, text under 8 px x-height) cannot be verified by it;
those looks come out "unclear" (FLAG), never a verdict. A second engine would need the owner's decision (a download).

**The dot rule (A2).** Dots are judged against the passport's PRINTED name (data page), never the MRZ; for a parent,
against the birth certificate. When the reads disagree on a dot, each document's dots (and the reference's) come from
a closer look of kind "dots" (an unclear look leaves the OCR dots unconfirmed). `strict.dot_rule`, the owner's cases:
passport "MD." + all others "MD" -> OK; passport "MD." + others mixed -> FAIL; passport "MD" + others mixed -> FAIL;
passport "MD" + others "MD" -> OK; assumed by symmetry (Q13): passport "MD" + all others "MD." -> OK. A FAIL goes to
each document whose CONFIRMED dots differ from the reference while the others are mixed; a document following the
reference is PASS; anything unconfirmed is FLAG at most (revision 2.5: one confirmed pattern unlike the reference
while another document reads the reference's dots -> the confirmed one FLAG, the followers PASS). **Board papers**
(owner 28 Oct: "board papers md. is fine, no fail for that"): education-board SSC / HSC certificates and transcripts,
the madrasah Dakhil (= SSC) and Alim (= HSC), JSC / JDC (`names.board_paper`) are out of the dot rule: their dots
neither count as mixed nor are compared with the passport; a NOTE "board papers' dots not judged (owner rule 28 Oct)"
where their dots differ. Their LETTERS stay strict. University degree papers stay in the dot rule.
**The dot probe (revision 2.6).** When every read shows the same dots, the recogniser may still have dropped a printed
dot on one document. When the name has a place for a dot (MD, MST, an initial: a word of one or two letters, or an
honorific of three or fewer, before the last word), each non-board document is looked at with the other dot pattern
too (at most 12 looks a run). A pattern the look sees with both judges (the dot's shape measured, the recogniser on
the same side) joins the rule like a read; what the rule then finds is FLAG at most, never FAIL (no OCR read shows
that dot).

**Affidavit cover.** Both spellings must be printed letter for letter, dots included, in ONE same-person group of an
affidavit (`names.covered`). Covered -> NOTE.
- An affidavit that names a spelling only as OCR read it gives FLAG.
- An affidavit that exists but does not cover the difference does not help: a confirmed difference is still FAIL.

**Revision 2.1: what a document's name is.** The name is the label's VALUE (`names.read_value`: the name-like
words right after the label, up to a stop word, a number, a symbol or a closing mark; a trailing "(Late)" is a
title). When the reference's letters are a run inside the value, the value's other words belong to the name:
- an added honorific (MD, MST, SK, MOS and the bot's list) before the run → a difference (FLAG; FAIL when
  confirmed);
- any other added word, before or after → a difference; FAIL only when CONFIRMED (the same value on another page
  of the document, or the closer look prints it); revision 2.6: unconfirmed, it is FLAG "check by eye", and it counts
  as the name inside it only when the closer look on its page shows the reference without it (Q18);
- a value wholly unlike the name (at least two words, two of them of 3+ letters, no word in common) → a
  difference when two pages agree on it (FAIL when the reference is confirmed); a single read →
  FLAG `names.<person>_unread` "not compared — check by eye" (a misparsed label reads like this, so never FAIL;
  an unconfirmed difference is never PASS either, A1).
The birth certificate's parent value is cut after the last word another document also prints (whole-page OCR
reads a bilingual print's Bangla as Latin junk right after the English value: 152 of 390 corpus values). The
words after the cut are `NameRef.tail`: another page of the certificate printing the whole value, or the
targeted re-read of the whole value, confirms them into the name; unconfirmed, the other documents are compared
with the cut name and the birth certificate's line is FLAG `names.<parent>_tail` "check by eye" (an unconfirmed
difference, never a silent PASS); a document or an account holder that adds exactly the tail (within OCR
distance) is FLAG "the birth certificate's read goes on with <tail> too, but no second read shows it", and one that
adds other words in the tail's place at the end (no more words than the tail) is FLAG "adds <X> where the birth
certificate's read goes on with <tail>" (Q19; revision 2.2: the certificate's end is not confirmed, so never FAIL).
**Revision 2.2: where a value ends.** Besides a stop word, a number or a symbol, a value ends at a word printed in
another case than the name (a capitalised name, then a Title-case or small-letter word; a Title-case name, then a
small-letter word; honorifics and initials carry no style), at a field label glued to a colon ("Program:"), and
at sentence, academic and occupation words (names.STOP). Such a word is never an "added word" (Q21).

**Glued reads (revision 2.1).** A read that runs two letters together where ANY spelling of the name (the
passport's printed name or another document's) has a dot cannot show that dot: it is never "confirmed" on dots,
and when its dots differ from the reference it is FLAG, never PASS. A glued passport read is no dot reference:
the documents are then judged against each other (mixed and confirmed → FLAG, never FAIL).

---------------------------------------------------------------------------------------------------------

## 8. Reuse vs new code

| Area | Reused unchanged (worktree bot code) | Ported from the lab (logic kept) | New |
|---|---|---|---|
| Reading | `dv.read_pages`, `dv.read_document`, `dv._ocr_image`, `dv._ocr_reader` (GPU worker), `text_cache.install/uninstall` | `light_engine` (reader, PageLog, cut, prime); `test1\tools\read_gpu_pages.py` (GPU loop) | `engine.read_boxes/page_image_of/cut_page`, page-texts serving, Engine class, `gpuread.py`, `run_case.py` |
| Page types | `prototype_page_types.score_page` + thresholds | `order_split` page features | `image_only` cue, bank page kind |
| Split | — | `order_split` align / documents / persons / notices | `segment_folder`, `finish` with identity refs, per-level academic units, trailing attach, HINT/ORDER/STOP rules, new slots, cut plan |
| Identity | `dv.passport_mrz`, `mrz_dob`, `mrz_expiry`, `labelled_dob`, `find_dates`, `OV.parse_mrz_line1/2` | `names_affidavit.passport_mrz_name`, `canonical`; `language.passport_name_verified` | MRZ from its own word, MRZ re-read, printed name, expiry label, `finish_identity` |
| Birth certificate | `dv.notarised` | lab `birth_cert.py`, `birth_qr.py`, `dob_boxes.py` | the A3 rules, `BirthFacts` |
| Document rules | `check_nid`, `check_family_cert`, `check_academic` (+ `PC.academic_check`), `check_bank`, `check_financial`, `check_income_tax`, `check_photo`, `nid_number_agreement`; `PC.inspect/colour_check/qr_check/bangla_check/seal_present` | — | `passport_rows`, `crop_photo` (edge finding), `nid_number_rows`, `board_qr`, DROP/KEEP/REWRITE, `qr_check` defuse, bundle colour, DOB / passport-number consistency |
| Bank | `balance_check`, `opening_balance_check`, `document_date`, `statement_date` (via `check_bank` and `dates_rows`) | `bank_holder` extractors | holder rules (every joint name, two references), USD/seal on the solvency page, `dates_rows` |
| TIN / chain | — | `tin_qr` (ladder, parsers), `fin_names` extractors | strict QR-vs-print, `web_hosts`, `tax_office`, `chain_findings` on `strict`, `holder_owner` |
| Names | `C.edit_distance` | `names_affidavit` (rule, extractors, affidavit) | A1 confirmation, A2 dot rule, confirmer, machine reads, unit/page mapping, `family_first_row` |
| Language | — | `language.py` (IELTS/TOEFL/TOPIK part) | row text from `read_boxes`; `covered` from `names` |
| Verdict / report | `C.row_verdict` semantics | — | `verdict.py`, `report.py`, `pipeline.py`, CLI |

**Not used by the runner:**
- `dv.verify_student` (folder classification by name only);
- `dv.cross_checks` (loose, portal);
- `dv.check_passport` (portal escapes);
- `dv.check_birth_cert` (replaced by `birth.py`, A3);
- `sponsor_holder`, `taxpayer_name`, `proprietor_name`, `is_tin` (replaced);
- `field_check.check_student` (portal);
- `dv.program_of`, `auto_verify`, `dv.write_report`.

---------------------------------------------------------------------------------------------------------

## 9. What each lab file becomes

All lab files are under `C:\Hangeul\DOCCHECK\lab\private-runner\`. Lab files stay where they are (read-only references). Private data files never move into the worktree.

| Lab file(s) | Becomes |
|---|---|
| `engine\light_engine.py` | `runner\engine.py`. `load_bot` is replaced by `botimport.load` (worktree, stub config); the rest is ported. |
| `engine\sync_botcode.py`, `engine\botcode\` | Retired (the runner imports the worktree). Kept on disk, unused. |
| `engine\smoke_test.py` | `tests\test_engine.py` fixture: the synthetic 5-page bundle generator (made-up text) is ported. |
| `engine\smoke_test2.py` | `engine.prime()` + `test_engine.test_prime_zero_ocr`. |
| `engine\venv_check.py` | `selftest\venv_check.py` (adds openpyxl and the worktree `src` checks). |
| `engine\vendor\pyzbar` | Used as is. `engine\vendor\openpyxl` and `et_xmlfile` are added (Task 1). |
| `engine\work\smoke`, `engine\data\verification` | Left as the engine study's artefacts. |
| `bank_holder.py` | `runner\bank.py` (the extractor, verbatim) + holder rules. |
| `test_bank_holder.py` | `runner\tests\test_bank.py` (55 trap tests) + rule tests. |
| `eval_holder.py`, `catalogue.py`, `review_holder.py`, `bank_data.py`, `anon.py` | Stay as measurement. `selftest\sim_money.py` re-implements the counting on the runner modules. |
| `bank_cases.json`, `truth_overrides.json`, `review_v*.txt`, `catalogue_out.txt` | Private truth and review data. Stay. `sim_money` reads the two JSONs read-only, counts only. |
| `tin_qr.py` | `runner\tin.py` (ladder and parsers; name matching replaced by `strict`) + `tin_findings`, `tin_page_types`, `web_hosts`, `tax_office`. |
| `fin_names.py` | `runner\financial.py` (extractors verbatim; matching replaced by `strict`). |
| `test_tin_qr.py` | `runner\tests\test_tin.py` (the name-agreement cases rewritten to strict kinds). |
| `tinqr\scripts\`, `tinqr\work\` | Stay (measurement and private results). |
| `names_affidavit.py` | `runner\names.py` (rule, extractors, affidavit; A1/A2) + `identity.py` (canonical use). Loaders are dropped. |
| `names_scratch\*.py`, `names_scratch\*_private.json` | Stay (measurement and private audit labels). `sim_names` reads the audit labels read-only for the FAIL-precision check. |
| `language.py` | `runner\language.py` (IELTS/TOEFL/TOPIK part; extras removed; its own `name_tokens` replaced by `strict.words`, and the candidate-vs-passport and affidavit tests by `strict.compare` / `names.covered`, A1). |
| `test_language.py` | `runner\tests\test_language.py`. The name-like example word at L66/L68 is replaced by a placeholder ("SAMPLEFAMILY"); tests of removed extras are dropped. |
| `measure_language.py`, `fresh_read.py`, `out\language\` | Stay (measurement and private outputs). |
| `order_split.py` | `runner\pagetype.py` (L99-238) + `runner\split.py` (L46-98, L239-706) + `selftest\sim_split.py` (L708-923 bundle simulation and scoring, L925 `check0001`). |
| `order_data.py` | `selftest\corpus.py` (Step 0). |
| `order_scratch\`, `order_scratch2\`, `scratch\` | Stay. |
| `classify_probe.py` | Its findings become `ingest.classify_name` and `RUNNER_FILE_PATTERNS`. Stays as a probe. |
| `noportal_sources_probe.py`, `label_layout_probe.py` | Their findings feed `identity.py`. They stay. |
| `tin_qr_shape_probe.py`, `tin_chain_probe.py` | Their findings feed `tin.py` and `financial.py`. They stay. |
| `birth_cert\birth_cert.py`, `birth_qr.py`, `dob_boxes.py` | `runner\birth.py` (Task 7; see 5.12 for what is ported). The other `birth_cert\` scripts stay as measurement. |
| `birth_cert\fixtures\` (17-20.png, `expected.json`, `ocr_cpu.json`, `ocr_gpu.json`), `birth_cert\private\owner\*_200.png` | Private T6 fixtures, read-only (`selftest\birth_fixtures.py`). |
| `birth_cert\qr_renders\`, `survey*.json` | Private measurement. Stay. |
| `test1\tools\read_gpu_pages.py`, `run_gpu_read.py` | `runner\gpuread.py` and `run_case.py` (Task 8). Stay as the reference. |
| `test1\` (`pages.json` = the GPU read, `pages_cpu.json`, `docs\`, `source_sha256_before.txt`, `TEST1_REPORT.md`, `verifiers.json`) | Read-only T1 fixtures and truth (private). `pages.json` becomes T1's page texts via `gpuread.py --from-legacy`. |

---------------------------------------------------------------------------------------------------------

## 10. Self-test plan

Every self-test writes only under `C:\Hangeul\DOCCHECK\lab\private-runner\selftest\<name>\` (T1 and T5 under `runs\`). It prints counts, rates, ok/MISS and sids, never names, numbers or text.

Harness steps that read the live bot's caches or the student folders do so read-only, in the harness process, before they start the runner. Each runner run is a separate process with the audit hook installed.

**T0 — unit tests (each task's `tests\`).**
- Synthetic, made-up data only, run with `unittest discover` (section 2.4).
- `selftest\pii_scan.py` (Task 1) loads, into memory and read-only, the portal names, dates of birth and numbers
  (passport number, guardian phone) from `C:\Hangeul\BOT\data\verification\results.json` and the names the
  runner read off documents in `private-runner\runs\*\run.json`. It scans every file under `src\verify\runner`,
  `C:\Hangeul\DOCCHECK\lab\private-runner\run_case.py`, `RUNNER_SPEC.md` and `APP_SPEC.md` for:
  - any of those name parts of 4 or more letters;
  - any 10-13/16/17-digit run (NID, phone, e-TIN, account, birth-registration shapes);
  - any `[A-Z]{1,2}\d{7,8}` token (the e-passport's 1 letter + 8 digits included);
  - any real date of birth (YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, DD MON YYYY) and any real number.
  Placeholders: a repeated or consecutive digit run, the dates 1 Jan 2000 / 2030, a line marked
  "pii-scan: placeholder" or "pii-scan: not a number".
- It prints only file:line and the category. It must print "clean".

**T1 — CHECK_0001, the A6 regression** (`selftest\check0001.py`, Task 6). The input is `C:\Users\User\Downloads\CHECK_0001.pdf`, the owner's own file, never modified. The program is **KLP** (A8).

1. **Before.** Record sha256, size and mtime. The sha256 must equal `test1\source_sha256_before.txt`.
2. **Page texts.** `gpuread.py --from-legacy test1\pages.json --source CHECK_0001.pdf --out runs\CHECK_0001\page_texts.json` (the existing GPU read; no card needed).
3. **Text level** (no OCR): `pagetype` + `split` on those page texts. The truth is passport p1; father NID p2-3; mother NID p4-5; academic HSC p6-7; academic SSC p8-9; employment certificate p10; TIN certificate p11; income tax p12-14 (p14, the Bangla tax-payment challan, attached).
4. **Full run:**

   ```
   %PY% -I -B -m src.verify.runner C:\Users\User\Downloads\CHECK_0001.pdf --program KLP ^
       --out C:\Hangeul\DOCCHECK\lab\private-runner\runs\CHECK_0001 ^
       --page-texts C:\Hangeul\DOCCHECK\lab\private-runner\runs\CHECK_0001\page_texts.json
   ```

   Assertions (the expected KLP outcome):
   - exit 0 (no STOP); all three outputs exist; READING says 14 pages from the GPU read (text-layer pages counted as layer);
   - **overall FAIL**;
   - **ACADEMIC HSC (p6-7) FAIL** and **ACADEMIC SSC (p8-9) FAIL**, each with BOTH reasons: no e-Apostille (`bot.check_academic`) AND the mother's confirmed one-letter spelling difference (`names.mother`, kind LETTERS, confirmed by two pages, no affidavit; the passport field and both NID sides agree with each other);
   - **no false SSC student-name FAIL**: the SSC's student name is at most FLAG (`names.student_dots` "check by eye") — the dot of "MD." dropped by OCR;
   - **MISSING lines exactly**: PHOTO MISSING, BIRTH CERTIFICATE MISSING, FAMILY CERTIFICATE MISSING, BANK SOLVENCY / STATEMENT MISSING (no TRADE LICENSE / EMPLOYMENT CERTIFICATE line, no TIN line);
   - **TIN CERTIFICATE (p11) FLAG** with `tin.web_address` (the non-government address); `tin.qr_tin` PASS (the QR decoded and equals the printed TIN); NEW CHECKS line lists `tin.web_address`;
   - **PASSPORT (p1) PASS** with a trusted MRZ (`mrz["line2_ok"]`, `dob_source`/`expiry_source` "MRZ" or "MRZ re-read"); no `identity.mrz_unread` FLAG and no MRZ NOTE;
   - **no ORDER notice naming page 14**; INCOME TAX is one line for p12-14 with a `split.attached_trailing` NOTE;
   - INCOME TAX (p12-14) PRESENT, the bot's tax-paid row a NOTE (not FLAG); an `income_tax.tax_office` FLAG is allowed only when both circles were read (confirm by eye on the first run, then it becomes the expectation);
   - MOTHER NID (p4-5): `nid.number` PASS, no "not compared" NOTE (A6 d);
   - no finding message contains "letters" about a name length (A6 e); a glued initial is at most FLAG;
   - EMPLOYMENT CERTIFICATE (p10) and TIN CERTIFICATE (p11) are two lines (A6 g); EMPLOYMENT is PASS or FLAG, never FAIL;
   - `academic.board_qr` PASS on SSC (the board QRs are read, A6 c);
   - roles: p2-3 father, p4-5 mother (role_how "name" via the passport-field parents, or "position" with its FLAG);
   - `report.xlsx` has sheets Summary, Documents, Pages, Findings; 14 Pages rows; `run.json` loads with schema `runner-run/2`.
   Every assertion prints ok/MISS only. A MISS lists the rule ids and pages, never text.
5. **GPU variant** (when a GPU slot is free; else skipped with a note): `run_case.py CHECK_0001.pdf --program KLP --out runs\CHECK_0001_gpu`. Its document lines and MISSING lines must equal step 4's; differences are listed by rule id.
6. **After.** sha256, size and mtime are unchanged. The audit log shows 0 blocked events and 0 opens under `C:\Hangeul\BOT` in the runner process.
7. **Folder twin.** Copy `test1\docs\*` with bot-style names into `selftest\check0001\folder\` (PASSPORT.pdf, FATHER NID.pdf, MOTHER NID.pdf, ACADEMIC CERTIFICATE HSC.pdf, ACADEMIC CERTIFICATE SSC.pdf, EMPLOYMENT CERTIFICATE.pdf, TIN CERTIFICATE.pdf, INCOME TAX.pdf; the p14 file is left out). Run the runner in folder mode with `--program KLP`. Per-slot verdicts must equal the bundle run's, except for differences explained by the split, which are listed.

**T2 — simulated fixed-order bundles from real cached text** (text level, no OCR, no PDFs).
- Uses `selftest\corpus.py`, the held-out half and seed 20261027.
- **`sim_split.py` (Task 2).** Builds the order study's 101 correct-order bundles and 291 one-mistake bundles, with NID pages translation-first. Floors (the study minus 0.01):

  | Measure | Floor |
  |---|---|
  | Exact boundaries, correct order | ≥ 0.85 |
  | Required, incl. NID / death certificate | ≥ 0.90 |
  | Mistake bundles, exact boundaries | ≥ 0.82 |
  | NID / death certificate: right pages and person | ≥ 0.88 |
  | Mistakes named by an ORDER or HINT notice | ≥ 0.78 |
  | Correct-order bundles with a false ORDER notice | ≤ 10 of 101 |
  | STOP on correct-order bundles (NID ambiguity only) | ≤ 3 of 101 |
  | True missing reported | 11/11 |

  Parity: with `order_split.references()`-style refs, the port gives spans and roles identical to the lab `order_split.run_bundle` on all 392 bundles (academic compared after joining the per-level units); notices are identical after mapping the revision-2 changes (HINT kind, NOTE → non-blocking ORDER, the STOP rule, the trailing attach), and every remaining difference is listed. It also prints the tune-half numbers.
- **`sim_names.py` (Task 3).** All 200 students, re-read off (confirmation only by pages, documents and machine reads).
  - Prints the new baseline under A1/A2: students FAIL / FLAG-only / NOTE-only / clean, findings FAIL, the dot rule's outcomes. The revision-1 tolerances no longer apply (honorifics and subsets are no longer softened); the shift is reported.
  - Canonical source: father from the birth certificate ≥ 184, mother ≥ 183 (revision 1 measured 186 and 185; the vocabulary change of critique 4 must not lose any).
  - FAIL precision against `names_scratch\audit_labels_private.json` (read-only): ≥ 0.75 real.
- **`sim_money.py` (Task 4).**
  - Bank: a holder read on ≥ 188/200, 0 wrong persons (against `bank_cases.json` + `truth_overrides.json`).
  - Decision counts are printed with the parent references from `names.canonical` and the role-confirmed NIDs (no portal). The shift from revision 1 (strict letters, every joint name, the confirmation rule) is reported.
  - Wrong-person rule outcome: no student-held account PASS, and no truly parent-held account FAIL except on a
    spelling difference (revision 2.1: the holder adds or drops a word against both the birth certificate and
    the NID, confirmed twice, no affidavit: A1/A7 FAIL; counted and printed, 1 of 200 cases).
  - Chain: counts per rule and kind.
  - The QR re-run (`--qr`, renders the real files read-only, slow) must read 201/201 TIN QRs on the 228 financial files.
- **`mrz_reread.py` (Task 3).** On 40 real passports, read-only, with the light engine: trusted DOB and expiry rate before and after `mrz_from_words` and the re-read. Goal ≥ 0.60 trusted; report whatever comes out.

**T3 — real folders, cache-seeded** (`selftest\folder_parity.py`, Task 5).
- 20 students, mixed programs, from `corpus.halves()` held-out.
- The harness copies the bot's `text\<id>.json` and `images\<id>.json` (read-only) to `runner-data\verification\{text,images}\F-<case>.json`. The runner case id is computed by `ingest.open_input` on the real folder. Then it runs the runner on the real folder. Staged copies keep name, size and mtime, so there are 0 fresh OCR calls apart from the MRZ, name and BRN re-reads.
- **Parity.** For the rules the owner did not change (`page.colour`, `page.qr` (not academic), `page.bangla`, notarisation rows, `bank.balance`, `bank.opening`, `bank.one_page`, `family.date/issuer`, `academic.apostille`, `financial.fiscal_year`, `income_tax.*` for Master's), the runner's verdicts equal the live bot's rows in `results.json` (read-only), document by document, on ≥ 99% of compared rows. Left out (changed on purpose): `bank.usd`, `bank.seal` (critique 15), the birth-certificate rows (A3), the NID-number rows (A6 d), non-Master's income tax (A6 h). Every difference is listed by rule id and sid.
- **Re-read audit list.** Every FLAG→FAIL caused by a re-read confirmation is listed (sid, rule, crop path) for a person to check by eye. The count of false FAILs is reported to the owner; it is not a switch (A1 decided `REREAD_CAN_FAIL = True`).

**T4 — bundle/folder parity on real PDFs** (`selftest\bundle_parity.py`, Task 6).
- For 5 students, concatenate their real folder files into `selftest\bundle_parity\<sid>.pdf` in the owner's fixed order (images become pages). NID files keep their own page order.
- Run the runner on the bundle (CPU light engine, about 5-8 minutes each, in the background) and on the folder.
- Per-slot verdicts are equal wherever the split was exact. Every difference is explained.

**T5 — the owner's 7 cases** (`selftest\cases.py`, Task 6).
- Run each case as given, read-only, through the wrapper (GPU pre-read, CPU fallback after 120 s), with the program the owner names for that case:

  ```
  %PY% -I -B C:\Hangeul\DOCCHECK\lab\private-runner\run_case.py "<case input>" --program <P> ^
      --out C:\Hangeul\DOCCHECK\lab\private-runner\runs\CASE0N
  ```

- Acceptance: no crash; three outputs per case; input snapshots unchanged; 0 blocked events; the READING line says which engine read the pages.
- `summary.txt` is ready to paste.
- A by-eye review checklist per case is written to `runs\CASE0N\review.txt`: every FAIL with its pages, every ORDER notice, every MISSING line, every name FLAG "check by eye" with its page, the new checks that fired, and the identity sources.

**T6 — birth-certificate fixtures** (`selftest\birth_fixtures.py`, Task 7). The owner's 4 example images (`birth_cert\fixtures\17-20.png`, the 200 dpi copies in `birth_cert\private\owner\`), with the saved texts of BOTH engines (`ocr_cpu.json`, `ocr_gpu.json`), against `expected.json` (all private, read-only). Prints ok/MISS per check only.
- 17 alone (Bangla original, no translation) → `birth.format` FAIL; `birth.notary` FAIL.
- 17 + 18 (original + notarised translation) → format PASS, fields from 18, notary PASS; DOB equal to the expected value and trusted (label + words), or FLAG "DOB unclear" — never a wrong trusted DOB.
- 19 (bilingual) → format PASS, notary PASS, QR decoded (both engines' setups), host bdris → PASS + the online NOTE; DOB equal to the expected value.
- 20 (older English print) → format PASS, notary PASS; DOB from boxes + words equal to the expected value, or FLAG "DOB unclear" — never a wrong trusted DOB.
- For every image: `issue_date` / `registration_date` are never returned as the DOB; the BRN equals the expected value where it is read.
- Plus a negative set built in the test from fixture 19's payload shape with made-up hosts and keys: a look-alike host → FAIL; a 52-character key → FLAG.

**T7 — GPU pre-read smoke** (Task 8; only when the bot's `auto_verify.lock` is absent and a slot is free within 120 s, else skipped with a note). `run_case.py` on CHECK_0001: `page_texts.json` complete, 14 pages, `peak_allocated_gb` ≤ 3.0, `ended` "complete" (or resumed after exit 3); the texts equal `test1\pages.json` on ≥ 12 of 14 pages (the rest listed by page number and character difference). The guard's dev lock is gone afterwards.

---------------------------------------------------------------------------------------------------------

## 11. Build tasks

**Step 0** (one builder, before the others start; about 30 minutes):
1. Create `src\verify\runner\__init__.py`, `model.py`, `strict.py`, `botimport.py`, `tests\__init__.py`, `tests\test_contract.py`, `selftest\__init__.py` and `selftest\corpus.py`, exactly as in section 4 (revision 2 text).
2. Create the venv and `runner.pth` (section 2.1), so that `botimport.load()` can run in tests.
3. Acceptance: `test_contract` passes (including the A2 truth table and `test_no_name_in`); `corpus.check_same_as_prototype()` passes.

After Step 0, Tasks 1-8 run in parallel. Each task owns only the files listed for it, codes against the signatures in sections 4 and 5, and fakes the other modules in its unit tests. Nobody edits `model.py`, `strict.py` or `botimport.py` without agreement from all tasks, and nobody edits the bot modules in the worktree. Nobody commits.

**File ownership (disjoint):**

| Task | Files |
|---|---|
| Step 0 | `__init__.py`, `model.py`, `strict.py`, `botimport.py`, `tests\__init__.py`, `tests\test_contract.py`, `selftest\__init__.py`, `selftest\corpus.py` |
| 1 Platform | `safety.py`, `engine.py`, `ingest.py`, `tests\test_safety.py`, `tests\test_engine.py`, `tests\test_ingest.py`, `selftest\venv_check.py`, `selftest\pii_scan.py`, `engine\vendor\openpyxl\`, `engine\vendor\et_xmlfile\`, `engine\vendor\VENDOR.json` |
| 2 Split | `pagetype.py`, `split.py`, `tests\test_pagetype.py`, `tests\test_split.py`, `selftest\sim_split.py` |
| 3 Names | `identity.py`, `names.py`, `tests\test_identity.py`, `tests\test_names.py`, `selftest\sim_names.py`, `selftest\mrz_reread.py` |
| 4 Money | `bank.py`, `tin.py`, `financial.py`, `tests\test_bank.py`, `tests\test_tin.py`, `tests\test_financial.py`, `selftest\sim_money.py` |
| 5 Documents | `doccheck.py`, `language.py`, `tests\test_doccheck.py`, `tests\test_language.py`, `selftest\folder_parity.py` |
| 6 Verdict | `verdict.py`, `report.py`, `pipeline.py`, `__main__.py`, `tests\test_verdict.py`, `tests\test_report.py`, `tests\test_pipeline.py`, `selftest\check0001.py`, `selftest\bundle_parity.py`, `selftest\cases.py` |
| 7 Birth | `birth.py`, `tests\test_birth.py`, `selftest\birth_fixtures.py` |
| 8 GPU | `gpuread.py`, `tests\test_gpuread.py`, `C:\Hangeul\DOCCHECK\lab\private-runner\run_case.py` |

All runner files are under `C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\`; the vendor folders under `C:\Hangeul\DOCCHECK\lab\private-runner\engine\vendor\`.

### Task 1 — Platform: safety, engine (with page-texts serving), ingest, environment

- **Files:** see the ownership table.
- **`safety.py` API:**

  ```python
  class SafetyError(Exception): ...
  PRIVATE = botimport.PRIVATE
  FORBIDDEN = [Path(r"C:\Hangeul\BOT"), Path(r"C:\Hangeul\VERIFIED STUDENT DOCUMENTS")]   # + siblings "* STUDENT DOCUMENTS"
  def is_forbidden(p: Path) -> bool           # pure; no I/O
  def check_out_dir(out: Path, input_path: Path) -> Path
  def assert_writable(p: Path) -> None
  @dataclass class SafetyState: out, cache_root, snapshots, blocked: List[str], bot_opens: int
  def prepare(out: Path, cache_root: Path, input_path: Path, extra_forbidden: Sequence[Path] = ()) -> SafetyState
      # audit hook (writes outside PRIVATE, any open under C:\Hangeul\BOT, network) + tempdir + input snapshot
  def install_write_guard(extra_allowed: Sequence[Path] = (), block_bot_opens: bool = True) -> None
      # the same hook for another process (the GPU worker): writes only under PRIVATE or to extra_allowed
      # files, no network; with block_bot_opens=False reads under C:\Hangeul\BOT (its venv) are allowed
  def snapshot(paths) -> Dict[str, Tuple[int, int, str]]     # size, mtime_ns, sha256
  def finish(state: SafetyState) -> Dict[str, Any]           # re-snapshot; raises SafetyError on any change
  ```

- **`engine.py` additions:** `load_page_texts`, `PageTexts`, `read_file`/`read_source` with `given`, `PageRead.engine`, `page_image_of`, `cut_page`, `stats()["pages_by_engine"]`; bundle mode is always primed by the pipeline.
- **Acceptance:**
  - `venv_check` prints OK, verifying: Python 3.12, ORT CPU provider, `src` from the worktree; no torch, torchvision, easyocr, scipy or skimage loaded; zbar available, openpyxl importable; bot paths under the cache root.
  - `test_safety`: `is_forbidden` table; `check_out_dir` rejects paths outside PRIVATE and inside the input; with `extra_forbidden=[<private-runner\selftest\forbidden_probe>]`, a write there raises `PermissionError` and creates nothing (never probe the real bot folder); `socket.create_connection(("127.0.0.1", 9))` raises; `install_write_guard(extra_allowed=[<probe file>])` allows that one file and refuses its sibling.
  - `test_engine` (synthetic bundle): 5 PageReads with kinds layer and ocr; the cut keeps pixels, text layer and the bundle mtime; a second run in the same case makes 0 OCR calls; `prime` gives 0 OCR calls in `read_document`; `read_boxes` returns (box, text, conf); `page_image_of` shape; a complete page-texts file → 0 OCR calls and engine "gpu"; a partial one → OCR only for the missing pages; a wrong sha256 or page count → `InputError`; a page-texts path outside PRIVATE → `InputError`; `cut_page` gives a one-page PDF.
  - `test_ingest`: the `classify_name` table, including TRADE LICENCE, HSC, SSC, CONTINUATION → None, FATHER ID CARD → father_nid, CV; `case_id` is stable; `stage` keeps name, size and int mtime; the input sha256 is unchanged.
  - `pii_scan` is clean.

### Task 2 — Page typing and the order-aware split

- **Files:** see the ownership table.
- **Acceptance:**
  - `test_split`, on made-up cue-word page texts:
    - a fixed-order bundle → exact units and keys, academic one unit per level with the apostille joined to its level;
    - two adjacent documents swapped → a blocking ORDER notice naming the pages, the touched units listed, the other units judged (no STOP);
    - a document moved → ORDER;
    - father and mother NIDs swapped with readable names → a non-blocking ORDER "NIDs are not in the order" notice; with position-only roles → STOP;
    - two missing documents with hint pages → two HINT notices, no STOP (critique 6);
    - more than 25% unrecognised → ORDER notices, no STOP;
    - an NID translation behind its original, academic not newest first, a transcript before its certificate, a statement page before the solvency page, a death certificate outside the NID block → non-blocking ORDER notices (critique 7);
    - a missing birth certificate → `missing == ["birth_cert"]`; employment certificate only → `missing` has "tin" but not "owner_doc"; TIN only → "owner_doc";
    - an unresolved death certificate with the father's NID missing → `maybe == {"father_id": uid}`, "father_id" not in `missing`;
    - NID translation+original pairs → two persons;
    - HSC→SSC → two academic units;
    - employment+TIN → two financial units;
    - a trailing unrecognised page right after income tax → part of the income-tax unit with `split.attached_trailing` NOTE, no ORDER (A6 f); a trailing page after anything else → its own unit;
    - the folder: name → key, a name/content mismatch → FLAG note, a generic death certificate given its role by strict name;
    - for every key and level, `dv.classify(unit_file_name(u)) == u.key`.
  - `sim_split` floors and parity (T2). The CHECK_0001 text level (T1 step 3) gives the truth split exactly.

### Task 3 — Identity, names and affidavit

- **Files:** see the ownership table.
- **Acceptance:**
  - `test_identity`, with synthetic MRZ lines whose ICAO check digits are computed in the test from made-up data:
    - trusted DOB and expiry;
    - both MRZ lines joined into one OCR word (and a line 2 split in two words) → `mrz_from_words` reads it, trusted (A6 a);
    - one bad check digit → untrusted, so the source becomes the label or the scan date;
    - a 39-character name field → `truncated`;
    - the printed name with "MD." whose letters equal the MRZ → `student.written` keeps the dot, `confirmed`;
    - a BRIS-style made-up birth-certificate text (English pages only) → father and mother parts, source "birth certificate";
    - no birth certificate → passport fields + NOTE;
    - a passport without its parents' page plus a birth certificate → parents read with the other documents as vocabulary (critique 4);
    - `finish_identity`: role-confirmed NID names in `father_nid` / `mother_nid`; a position-only role never becomes a reference;
    - `portal_view` empty; `passport_for_language` has no `dob` when untrusted and has `name_trusted`.
  - `test_names` on made-up texts, with a fake confirmer:
    - MATCH → no unit finding, summary PASS;
    - a one-letter difference on two pages of one document, no affidavit → FAIL (the test-1 mother case);
    - the same difference on one page and the fake re-read "confirmed" → FAIL; re-read "refuted" → FLAG "probably OCR"; "unclear" → FLAG; never PASS;
    - a dropped "MD" (SUBSET) confirmed → FAIL; "MOHAMMAD" vs "MD" confirmed → FAIL (no honorific softening);
    - covered by an affidavit (letters and dots in one group) → NOTE; an affidavit that does not cover it → still FAIL;
    - the owner's four dot cases and the symmetric case through `evaluate` (A2);
    - test 1's dot case: passport "MD.", HSC "MD." on two pages, SSC "MD" once and "MD." by the re-read → SSC FLAG, never FAIL (A6 b);
    - a glued initial with its dot → no finding (A6 e);
    - a variant extending a truncated MRZ name → FLAG;
    - a machine read (board QR) with the reference letters → counts as a confirming read;
    - bank/trade_license units produce no names finding (no double count); their `extra_reads` enter the dot rule only;
    - `printed_student_name` reads only a birth unit's `english_pages` (a Bangla page's noise never becomes a name);
    - `family_first_row` PASS and FLAG.
  - `sim_names` prints the new baseline; canonical source and FAIL precision meet T2. `mrz_reread` report written.

### Task 4 — Bank holder, TIN QR and the financial chain

- **Files:** see the ownership table.
- **Acceptance:**
  - `test_bank`:
    - the 55 ported trap tests pass;
    - holder rules: father (birth certificate) → PASS; father as on his role-confirmed NID only → PASS naming the NID; student → FAIL; joint with a parent → PASS + FLAG (Q2); joint with the student second → FLAG, not FAIL; a different confirmed name (statement header agrees) with no affidavit → FAIL; the same unconfirmed → FLAG; covered → NOTE;
    - every name in `h.joint` is judged (critique 10);
    - well-read bank pages with no solvency page → `solvency_missing`; unreadable → FLAG; holder unread → FLAG;
    - `bank.usd` and `bank.seal` judge the solvency page only: USD on a statement page only → FLAG;
    - `dates_rows` uses the solvency page when the statement comes first.
  - `test_tin`: the 32 ported tests pass (name-agreement cases rewritten to strict kinds), plus the `tin_findings` mapping, `tin_page_types`, `web_hosts` (made-up hosts: a `.gov.bd` host → PASS; a look-alike → FLAG; none → no row) and `tax_office` parsing.
  - `test_financial`:
    - EQUAL → PASS; LETTERS, SUBSET, ORDER, OCR → FLAG unconfirmed / FAIL confirmed; covered → NOTE;
    - the owner row: "MD SAMPLE ONE" owner value vs taxpayer "SAMPLE ONE" → not OWNER (critique 3);
    - **"MD <A> <B>" vs "<A> <B>" through every comparison point** — `tin.qr_name`, `owner_slot`, `chain_findings`, `bank.check_bank_unit` — never PASS (critique 2; the language point is in `test_language`);
    - print-only taxpayer → FLAG; no TIN unit → chain findings on the owner unit; `holder_owner` when no taxpayer is read;
    - `income_tax.tax_office`: different circles → FLAG; equal → PASS; unread → no row;
    - no `name_in` / `tokens(` anywhere.
  - `sim_money` meets the T2 numbers.

### Task 5 — Document checks (bot reuse) and language

- **Files:** see the ownership table.
- **Acceptance:**
  - `test_doccheck`:
    - pinned messages (section 5.8);
    - with the empty `portal_view`, `check_nid`, `check_family_cert` and `check_academic` (path=None, made-up text) produce no name or GPA row, and `check_bank`/`check_financial` produce no holder or sponsor row;
    - DROP removes the account-age, date, USD and seal rows, the TIN-vs-trade loose row and the NID-number rows;
    - the student NID REWRITE;
    - `passport_rows` thresholds with a synthetic Identity: expired → FAIL; 6 months → FAIL; scan date at 6 months → FLAG; no date → FLAG; MRZ-vs-printed number; `passport.mrz` PASS;
    - `nid_number_rows`: a made-up 10-digit number on one page and the same number with two glued noise digits on the other → PASS, no "not compared" NOTE (A6 d);
    - `board_qr` with a fake ladder returning a made-up board payload → `academic.board_qr` PASS, a student `MachineRead`, the bot's "no QR code" row dropped (A6 c);
    - bundle academic: "page 1 is not an e-Apostille" → an ORDER finding; "no e-Apostille" stays FAIL;
    - income tax for KLP: the bot's FLAG rows become NOTE, the line PRESENT; for MASTER unchanged (A6 h);
    - death certificate with the parent's NID present → NOTE, no stands-in PASS (critique 17);
    - bundle photo on a synthetic full-page scan (one image covering the page) with a near-white-background photo rectangle (thin grey edge) and a dark head ellipse → a crop within 5% of the rectangle and no background FAIL; the same page without a rectangle → `photo.edges` FLAG and no ratio/background row (critique 1); a dark background inside a found rectangle → FAIL;
    - bundle colour: every unit grey → one `page.colour_bundle` FLAG and no per-unit colour FAIL; one grey unit → its own row kept;
    - `qr_check` defuse: a text holding "marketing" still gets a QR row;
    - id_card_cv → PRESENT with no page checks;
    - the order (passport text exists before the NIDs; income tax sorted for every program).
  - `test_language`: the ported self-test passes (placeholder name word), with: `dob` absent when untrusted → no DOB FAIL (critique 12); the certificate name judged with `covered` from `names` (critique 14); "MD <A> <B>" vs "<A> <B>" → not PASS; Duolingo / PTE / EnglishScore → PRESENT only; no 60-day or General Training rows (critique 20).
  - `folder_parity` (T3) is at least 99% on unchanged rules, and its re-read audit list is written.

### Task 6 — Verdict, report, pipeline and CLI

- **Files:** see the ownership table.
- **Acceptance (independent):**
  - `test_verdict`:
    - row verdict semantics (NOTE = PASS, ERROR shown as FLAG, PRESENT only without FLAG/FAIL);
    - overall labels STOP / FAIL / FLAG / PASS only; MISSING lines alone → FLAG (`MISSING_MAKES`); a FAIL outranks;
    - ORDER findings lifted into non-blocking notices; a blocking ORDER → NOT JUDGED for the touched units only;
    - `missing_lines` in slot order + the solvency line + the conditional death-certificate line;
    - PRESENT lines never change the overall verdict.
  - `test_report`:
    - the summary format against a fixed `RunResult` fixture with made-up data (the line regexes in section 5.11.1), including READING, "ORDER: OK" only without notices, NEW CHECKS;
    - Excel sheets exactly Summary, Documents, Pages, Findings; Pages rows = pages; verdict colours;
    - JSON round trip (dates as ISO, paths as str), schema `runner-run/2`.
  - `test_pipeline`:
    - with every stage module replaced by fakes, stages are called in order with the section 5 types (birth before identity, `finish_identity` after `split.finish`, prime for every bundle cut);
    - STOP path → exit 3 with no cut;
    - `--split-only`;
    - `--page-texts` passed through to the engine; a mismatching file → exit 2 before any write;
    - an unsafe `--out` → exit 2 before any write;
    - removed flags (`--edge`, `--prime`, `--force-split`, `--no-reread-names`) → exit 2;
    - a passport with no parents' page and a birth certificate → parents known (critique 4);
    - a crash → exit 1, `log.txt` and `run.json["error"]`.
- **Acceptance (integration, after Tasks 1-5, 7 and 8 land):** T1 passes (the A6 regression). T4 gives explained parity on 5 students. `selftest\cases.py` dry-runs on the CHECK_0001 bundle and its folder twin. `pii_scan` is clean over the whole package.

### Task 7 — Birth certificate

- **Files:** see the ownership table.
- **Interface:** section 5.12 (`page_kind`, `read_birth_certificate`, `check_birth_certificate`, `read_dob`); fills `model.BirthFacts`. Called by `pipeline` (stage `[birth]`) and `doccheck` (the birth_cert adapter). Uses `engine.page_image_of` / `read_boxes` / `read_text` (Task 1), the vendored pyzbar, cv2 from the numpy venv, `dv.notarised` passed in by the caller, `strict` for the QR name.
- **Acceptance:**
  - `test_birth` on made-up texts and synthetic images:
    - format detection for each format (made-up English cue texts);
    - a Bangla original without translation → FAIL; with an unnotarised translation → notary FAIL; with a notarised translation → PASS, fields from the translation pages only;
    - a boxed DOB "DD ■ MM ■ YYYY" with the blocks read as `1`, `l`, `I`, `|`, `i` or dropped → the right date; a date token with `O` for `0` → the right date;
    - boxes and words agree → trusted; disagree → `dob` None + FLAG "DOB unclear"; one source → untrusted;
    - a Date of Issue and a Date of Registration printed right next to the DOB label → never taken as the DOB;
    - against a trusted passport DOB: both trusted and different → FAIL; birth untrusted and different → FLAG; equal → PASS;
    - QR: a synthetic QR image (cv2 encoder) with a made-up `https://bdris.gov.bd/certificate/verify?key=<64 made-up base64 characters>` → decoded, PASS + online NOTE; a look-alike host → FAIL; a 52-character key → FLAG; no QR on a bilingual print → FLAG; a payload name different from the printed name with a confirmed print → FAIL;
    - barcode BRN different from the printed BRN: FLAG; FAIL when `brn_confirmed`;
    - no online call (the network guard is on in the test).
  - `birth_fixtures` (T6) prints all ok.

### Task 8 — GPU pre-read

- **Files:** see the ownership table.
- **Interface:** section 5.13 (`gpuread.read_all`, `convert_legacy`, `main`; `run_case.plan`, `main`); writes `runner-page-texts/1` (section 5.2.1). The `--page-texts` plumbing in the runner is Task 1's `engine.py`.
- **Acceptance:**
  - `test_gpuread` (runner venv, no torch, no card):
    - `read_all` with a fake `dv` (its `read_pages` calls `_ocr_image` per OCR page; the fake returns "PAGE n") on a synthetic 4-page PDF with one text-layer page → a valid `runner-page-texts/1` dict; `checkpoint` called before every OCR page and never for the layer page; resume reuses pages already done; a checkpoint raising a fake `SlotInterrupted` → a partial file with `complete: false` (exit 3 path);
    - folder input with an image file → one entry per file, image read once;
    - `convert_legacy` on a made-up legacy JSON + the synthetic PDF → schema valid, sha256 of the PDF, page count checked;
    - the output path outside PRIVATE → exit 2;
    - `run_case.plan` with a fake `call`: 0 → page texts used, empty note; 3 then 0 → resumed; 5 → fallback note "GPU slot not available within 120 s"; 4 → fallback note with the exit code; `--cpu` → no GPU call; never more than `MAX_RUNS` calls.
  - T7 (live smoke) when a slot is free; else skipped with a note. `gpuread.py` never runs while the bot's lock exists (the guard waits, then exit 5).

---------------------------------------------------------------------------------------------------------

## 12. Open questions for the owner (defaults used until answered)

Answered in revision 2 and removed: Q5 (targeted MRZ re-read in scope: yes), Q6 (an account, TIN or trade licence in
the student's own name: FAIL, A7), Q7 (honorific-only differences: strict, A1), Q8 (spacing-only differences:
whitespace does not count, A1), Q11 (GPA consistency: removed, critique 20), Q12 (two agreeing reads may FAIL: yes, A1).

| # | Question | Default in the runner |
|---|---|---|
| Q1 | Where do the e-Apostille pages sit in the bundle's academic block? The kept FAIL needs an apostille for each level. | In a bundle, an apostille that is not first in its level's pages is an ORDER notice ("put it first"); "no e-Apostille with a scannable QR" stays FAIL. |
| Q2 | The holder's name unreadable on a readable solvency certificate; joint accounts (including a joint account with the student). | FLAG for both. A bank document whose pages read well but hold no solvency certificate gives "BANK SOLVENCY CERTIFICATE MISSING". |
| Q3 | Is a TIN certificate required in addition to a trade licence or employment certificate? | Yes: "TIN CERTIFICATE MISSING" line. |
| Q4 | The overall verdict when MISSING lines are the only problem. | FLAG (`MISSING_MAKES`); a FAIL anywhere outranks it. The missing lines are always listed. |
| Q9 | Language: program minimum scores; an expired certificate in this optional slot FAIL or FLAG. | Minimum scores None → NOTE; expired → FAIL. Only IELTS, TOEFL and TOPIK are checked. |
| Q10 | ECA, personal statement and recommendation: keep the bot's page checks (colour, QR, Bangla)? | Yes, as the bot does. Only the ID card/CV is unchecked. |
| Q13 | A2's unstated case: passport "MD" and ALL other documents "MD." (the same dot everywhere except the passport). | OK (PASS), by symmetry with "passport MD. + all others MD". |
| Q14 | Keep the two NEW FLAG checks found in test 1? (a) a TIN certificate printed from a non-government web address (not `*.gov.bd`) → FLAG "verify on the NBR site"; (b) the tax circle/zone on the return/acknowledgement differs from the TIN certificate's → FLAG. | Both on, FLAG only, listed under "NEW CHECKS (owner to confirm)" in every summary where they fire. |
| Q15 | The colour rule in bundle mode: the owner's scanner decides the colour. A bundle scanned in grey would FAIL every document. | When every unit fails the colour check, one bundle-level FLAG ("rescan in colour") instead of a FAIL per document; a single grey document inside a colour scan keeps its own row. |
| Q16 | Leading titles (LATE, MR, MRS, MS, MISS, DR) are not part of a name, and a dot after the last letter of a name is punctuation. A1 says "no honorific dropping"; a reviewer reads that as covering MR/MRS too. | Yes for both, at the START of a name only (a bank or TIN print's "Mr." would otherwise fail every chain). MD, MST and every other word count letter for letter. |
| Q18 | A value read ONCE that adds a word other than an honorific to the name ("<A> <B> BEGUM" on a one-page document, no second read): a difference (FLAG "check by eye") or page noise? On the corpus 124 such reads were nearly all OCR (a misread "Name" label, a table's district, "PHOTOGRAPH", Bangla read as Latin). | Revision 2.6 (independent check: an added BEGUM or KHATUN in a script print passed): the closer look on that page decides. "same" (the reference printed without the word): page noise, the name inside it. "differs": a confirmed difference, FAIL when uncovered. "unclear" or no look: FLAG "check by eye", never PASS. Two agreeing reads (two pages) make it a difference. An added honorific is always a difference. |
| Q19 | The birth certificate's value goes on after the words the other documents print, and no second read (another page of the certificate, the zoomed re-read) confirms the extra words (they may be OCR junk). | The other documents are compared with the confirmed part; the birth certificate's line is FLAG `names.<parent>_tail` "check by eye" (revision 2.2: never a silent PASS); a document or holder that adds those words, or other words in their place at the end (no more words than the tail), is FLAG, not FAIL: the certificate's end is not confirmed. |
| Q21 | A word printed right after a name in another case than the name, a field label glued to a colon, or a sentence / academic / occupation word ("successfully", "Programme", "Farmer", "Freedom Fighter"). | It ends the name (never an extra name word): a correct document no longer FAILs when the re-read confirms the printed words. A real extra name word printed in the name's own case is still a difference. |
| Q22 | Folder mode: the solvency certificate and the statement in two separate bank files. | One bank document: the bot's per-file "only one page" FLAG becomes a NOTE naming the files, and the solvency date is compared with the statement in the other file. |
| Q20 | A name label read ONCE whose value is wholly unlike the name (no word in common; a misparsed label, another person's row on a family certificate, or a truly different name). At text level on the corpus (re-read off) 240 document reads are like this (106 on family certificates); students with any FLAG go from 198 to 200 of 204. | FLAG `names.<person>_unread` "not compared — check by eye" (A1: an unconfirmed difference is never PASS). Two pages agreeing on the value: a difference (FAIL when uncovered). |
| Q23 | Revision 2.5: a name printed in bold italic or ornate script that the one recogniser cannot verify either way (owner folders 3 and 9: three board-paper lines that print the reference by eye). Accept FLAG "check by eye" for such prints, or add a second, stronger recogniser (a download)? | FLAG "check by eye" (never a guessed PASS or FAIL). |
| Q24 | Revision 2.5 (part B): a solvency certificate signed by hand with no bank stamp; a trade-licence QR printed but too blurred to decode; the income tax's "notarised" PASS from the government emblem and the tax office's own seal; a TIN one or two look-alike digits off its QR that the closer look cannot settle; an NID back that disagrees with the front on one field only. | Signature alone PASS; undecodable QR keeps the bot's PASS with a note; emblem + office seal PASS; TIN FLAG (never FAIL); one field FLAG, two or more FAIL. |
| Q17 | Birth certificate: the barcode's BRN differs from the printed BRN; the same QR key on another student's certificate (a copied QR). | BRN difference: FAIL only when the printed BRN is read alike twice, else FLAG. The key-reuse check needs other students' files, so a single-case run cannot do it (not checked). |

## 13. Known limits

1. **Untrusted passport reads on whole pages.** Whole-page OCR trusts the MRZ date of birth and expiry on only about 16% of passports; `mrz_from_words` and the targeted re-read carry R2. The MRZ name line has no check digit. A name FAIL needs two confirmed spellings (section 7).
2. **Page-order effects.**
   - `seal_present` and `inspect` look only at the first 4 pages of a unit, as the bot does on folders.
   - Bot messages that cite a page number (for example, the apostille rule) count pages within the unit. The report shows the unit's page range next to them.
3. **Split reliability.**
   - Father/mother NID swaps are caught by notices only when the names can be read (56% in the study). The strict name rule still shows the mismatch on the NID line.
   - The real one-pass scans the owner gives may be noisier than the simulated bundles. Exact-boundary rates on the 7 cases are reported, not assumed.
4. **Paths with no real sample.** No real joint, nominee or business-name bank holder; no TOEFL or TOPIK certificate; no affidavit that covers a difference; no death certificate standing in for an NID; no real mixed-dot case (the dot rule is tested on made-up names only).
5. **No online verification.** The offline checks cannot prove a certificate is authentic. The name behind a bdris QR, the TIN on the NBR site and the apostille subject (`apostille.json`) can only be checked online, which the runner never does.
6. **More "check by eye" FLAGs.** A1 never passes an unconfirmed OCR-explicable difference, so noisy scans give more name FLAGs; each names its page.
7. **The GPU is shared with the bot.** The pre-read waits at most 120 s for a slot and stops at a checkpoint when the bot's check starts; the run then falls back to the CPU light engine for the rest and says so. GPU and CPU texts differed by 0.18% of characters on test 1.
8. **The bot's apostille QR reader** (`PC.page_codes`) is used unchanged; it may miss apostille QRs the way it missed the board QRs (A6 c fixes only the board-QR line).
9. **The boxed DOB on old prints** is read weakly from the image (6 of 11 exact in the study); it can only be trusted when the "In words" line agrees.
10. **Revision 2.3, folder files that hold several documents.** Inside one file the NID block is one person (a file
    holding the father's and the mother's NIDs is one unit, its person by name); a page the alignment cannot place
    and no 2.4 rule joins to a neighbour (a clear statement page after the income tax, a blank first page) becomes an
    `unrecognised` unit with its FLAG; a part out of
    the file's order is judged where it is (no ORDER notice in folder mode). An outvoted reference (section 7) turns
    a confirmed spelling difference of the birth certificate itself into one FLAG: the owner looks at the
    certificate once instead of reading several FAILs.
11. **Revision 2.4.** A wrapped family-table name cell is read whole only when the row's date cell wraps too (cut at
    the year); otherwise the first line alone is read and the wider re-read crop turns a would-be FAIL into a FLAG.
    The page typer can still tie two kinds on one page (an income-tax certificate typed TIN in owner folder 9: it
    joins the TIN part). The bot's own rules keep their limits: its academic level words read a school's name
    ("... Alim Madrasah") as HSC (a FLAG on an SSC part), and its opening-balance reader may miss a balance printed
    before its label (a NOTE). A handwritten signature is not read: `bank.seal` needs seal wording or seal colour.
    (Revision 2.5 lifts the last three: ties are settled, Dakhil / Alim go by the examination, signatures are read.)
12. **Revision 2.5.** The signature detector is calibrated on the owner folders' four solvency certificates (150 dpi
    scans); a very small or very faint signature, or one written over printed text so that no single pen stroke is
    longer than a printed word, is not seen (the page then needs a stamp or the wording). A signature written across
    a round stamp's ring counts as the ring (a stamp). The NID back-side rule needs the smart card's MRZ: an old
    laminated card, or an MRZ the reader garbles past its check digits, gives a NOTE. The QR ladder cannot decode a
    QR blurred below about 3 pixels a module (owner folder 3's trade licence): the bot's "no QR code" row then stays.
13. **Revision 2.5, the closer look.** One recogniser (english_g2) judges the print: a name it misreads in nearly
    every view cannot be verified by it, so the look says "unclear" and the line is FLAG "check by eye" (owner folder
    3: the HSC transcript's bold italic student name and the SSC certificate's bold italic mother's name; owner folder
    9: the Dakhil certificate's ornate script father's name; all print the reference by eye). The page search cannot
    tell which of two places of a page the OCR read came from: a page that prints the name in two places where one
    cannot be settled, or the two disagree, is "unclear" (a FLAG where a single-place page would have settled). A
    difference that reads at under -1.0 nats per character (script prints) is never confirmed by the look alone (FLAG,
    not FAIL). A second recogniser would need a download (owner question).
14. **Revision 2.6, what no look is asked about.** A read that already equals the reference triggers no letter look:
    a letter the page OCR misreads INTO the reference would pass unseen (the checker's bundle W15; on the checker's 71
    hunt crops plain OCR never read a variant as the reference, so letters are a small risk). Dots are the realistic
    risk (the recogniser drops printed dots) and are probed (the dot probe, section 7); the probe sees a dot only
    with both judges and gives FLAG at most. The line finder works on the darkest ink: a word printed right beside
    the name in a lighter, coloured ink (a stamp's, a pen's) is no part of the line the look reads (made-up test: a
    purple "MD" beside a black name gives "same"); a typed name and its typed honorific share one ink. A parent field
    that names clearly another person on ONE read is FLAG "not compared" (a misparsed label reads the same way),
    never FAIL; two pages agreeing make it a difference.
15. **Revision 2.6, memory (measured, not changed).** The CPU detector (CRAFT, `craft_mlt_25k.onnx`) on a 400-dpi A4
    page (a page-wide closer look) raises the process's committed memory from about 0.5 GB to 4.8 GB (a made-up
    page, `fixer4\mem_detect.py`), and ONNX Runtime's CPU memory arena keeps it; with pages of several sizes the
    runner process on the EAP folder reached about 11 GB committed. A PC with 8 GB of RAM would page heavily.
    No decision depends on it; options for the owner's app: the session option `enable_cpu_mem_arena = False`,
    or detecting on a page scaled to the light engine's 200 dpi and mapping the boxes back (both need the
    measurements re-run).
