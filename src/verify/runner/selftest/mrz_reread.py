"""T2 mrz_reread (RUNNER_SPEC section 10): how often the passport's date of birth and expiry are TRUSTED
(doc_verifier._trusted terms) on real passports, before and after identity.mrz_from_words() (A6 a) and the
targeted MRZ re-read (identity.reread_mrz, CPU light engine). Goal >= 0.60 trusted; whatever comes out is
reported.

Read-only: the corpus (selftest/corpus.py: the bot's cached 200 dpi page texts) and the real passport files
in the student folders (rendered read-only at 300 dpi for the re-read). Writes only under
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\mrz_reread\\ (the MRZ crops, a private per-passport JSON
of booleans keyed by sid, the engine's private cache root). Prints counts and rates only.

    <runner venv python> -I -B -m src.verify.runner.selftest.mrz_reread [--limit 40]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from src.verify.runner import botimport

OUT = botimport.PRIVATE / "selftest" / "mrz_reread"


class _Adapter:
    """engine.Engine for one real file: page_image_of by the file's own page (the runner's engine knows
    only the files it read itself)."""

    def __init__(self, eng, path: Path):
        self.eng, self.path = eng, path

    def page_image_of(self, page, dpi: int = 300):
        return self.eng.page_image(self.path, page.file_page, dpi)

    def read_text(self, img) -> str:
        return self.eng.read_text(img)

    def read_boxes(self, img):
        return self.eng.read_boxes(img)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    from src.verify.runner import engine as E
    eng = E.Engine(OUT / "data")                     # loads the bot code with this private cache root
    dv = eng.bot.dv
    from src.verify.runner import identity as I
    from src.verify.runner.model import PageRead
    from src.verify.runner.selftest import corpus
    studs = corpus.load_corpus()
    tune, held = corpus.halves(studs)
    picked = []
    for st in held + tune:
        f = next((f for f in st.files if f.key == "passport" and f.pages), None)
        if f is not None:
            picked.append((st, f))
        if len(picked) >= args.limit:
            break

    def trusted(m):
        return bool(m) and dv.mrz_dob(m) is not None, bool(m) and dv.mrz_expiry(m) is not None

    rows, t0, reread_s, rereads = [], time.time(), 0.0, 0
    for st, f in picked:
        pages = [PageRead(k, f.name, k, t or "", "ocr", len(t or ""), False, 0.0, False, "cache")
                 for k, t in enumerate(f.pages)]
        bot_best, words_best, best_page = {}, {}, 0
        for k, p in enumerate(pages):
            b = dv.passport_mrz(p.text)
            w = I._best_mrz(b, I.mrz_from_words(p.text))
            if I._key(b) > I._key(bot_best):
                bot_best = b
            if I._key(w) > I._key(words_best):
                words_best, best_page = w, k
        before, after_words = trusted(bot_best), trusted(words_best)
        final = dict(words_best)
        did = False
        if not all(after_words):
            order = [best_page] + [k for k in range(len(pages)) if k != best_page]
            for k in order[:2]:
                ts = time.time()
                try:
                    rr = I.reread_mrz(_Adapter(eng, f.path), pages, k, OUT / "work" / st.sid)
                except Exception as exc:                  # counted, never printed with text
                    rr = {}
                    print(f"{st.sid} re-read error {type(exc).__name__}", file=sys.stderr)
                reread_s += time.time() - ts
                rereads += 1
                did = True
                rr.pop("_text", None)
                if I._key(rr) > I._key(final):
                    final = rr
                if all(trusted(final)):
                    break
        after = trusted(final)
        rows.append({"sid": st.sid, "image": f.image, "pages": len(pages), "before": before,
                     "words": after_words, "reread": after, "reread_done": did,
                     "line2_ok": bool(final.get("line2_ok")), "checks": int(final.get("checks") or 0)})
    (OUT / "report_private.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    n = len(rows)

    def rate(stage, i):
        return sum(1 for r in rows if r[stage][i]) / n if n else 0.0

    def both(stage):
        return sum(1 for r in rows if all(r[stage])) / n if n else 0.0
    print(f"mrz_reread: {n} passports (held-out half first), {rereads} re-reads "
          f"({reread_s / max(1, rereads):.1f} s each), {time.time() - t0:.0f} s in all")
    for stage, label in (("before", "bot passport_mrz"), ("words", "+ mrz_from_words"), ("reread", "+ re-read")):
        print(f"  {label:18s} DOB trusted {rate(stage, 0):.2f}  expiry trusted {rate(stage, 1):.2f}  "
              f"both {both(stage):.2f}")
    ok = both("reread") >= 0.60
    print(f"{'ok' if ok else 'MISS'} both dates trusted after the re-read {both('reread'):.2f} (goal 0.60)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
