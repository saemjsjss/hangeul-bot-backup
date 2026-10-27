"""T6 — the owner's 4 birth-certificate examples through birth.py (RUNNER_SPEC section 10, Task 7).

Inputs (PRIVATE, read-only): C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\birth_cert\\fixtures\\expected.json, the
saved page texts of BOTH engines (ocr_cpu.json = the CPU light engine, ocr_gpu.json = the bot's EasyOCR on the
graphics card), each at two image scales, and the page images birth_cert\\private\\owner\\<n>_200.png (QR,
barcode, seal, the boxed-date re-read with the CPU light engine).

    <runner venv python> -I -B -m src.verify.runner.selftest.birth_fixtures

Prints ok/MISS per check and the totals, never a name, number or date. Writes only under
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\birth_fixtures\\ (results.json, work\\ crops). No network.
Exit code 0 when every check is ok, 1 otherwise.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from .. import birth as B
from .. import model as M
from ..botimport import DEFAULT_CACHE_ROOT, PRIVATE, load

BC = PRIVATE / "birth_cert"
FIX = BC / "fixtures"
OWN = BC / "private" / "owner"
OUT = PRIVATE / "selftest" / "birth_fixtures"
ONNX = Path(r"C:\Hangeul\DOCCHECK\lab\onnx")
ENGINES = ("cpu", "gpu")
SCALES = ("2.6", "1.0")


def iso(s: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def light_reader():
    """The CPU light engine's reader: Task 1's engine.make_reader() when it is there, else the same two ORT
    CPU sessions built here (never DirectML, S5)."""
    try:
        from ..engine import make_reader
        return make_reader()
    except ImportError:
        pass
    import glue_numpy
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.log_severity_level = 3
    det = ort.InferenceSession(str(ONNX / "models" / "craft_mlt_25k.onnx"), so, providers=["CPUExecutionProvider"])
    rec = ort.InferenceSession(str(ONNX / "models" / "english_g2.onnx"), so, providers=["CPUExecutionProvider"])
    return glue_numpy.LightReader(det, rec, ONNX / "pkg" / "glue_data.json")


class FixtureEngine:
    """The Engine surface birth.py uses, over the fixture PNGs (read-only): page_image_of, read_boxes, cut_page
    (the fixture image itself, for the seal test) and bot.dv._reader (the line recogniser)."""

    def __init__(self, reader, images: Dict[int, Path]):
        self.reader = reader
        self.images = images
        self.bot = SimpleNamespace(dv=SimpleNamespace(_reader=reader))
        self._memo: Dict[int, Any] = {}

    def page_image_of(self, page: M.PageRead, dpi: int = 300):
        import cv2
        import numpy as np
        if page.index not in self._memo:
            bgr = cv2.imdecode(np.fromfile(str(self.images[page.index]), dtype=np.uint8), cv2.IMREAD_COLOR)
            self._memo[page.index] = bgr[:, :, ::-1].copy()
        return self._memo[page.index]

    def read_boxes(self, img):
        return [(box, str(text), float(conf)) for box, text, conf in self.reader.readtext(img, detail=1)]

    def cut_page(self, page: M.PageRead, out_dir: Path) -> Path:
        return self.images[page.index]


class Tally:
    def __init__(self):
        self.n = {"ok": 0, "MISS": 0}
        self.rows: List[Dict[str, Any]] = []

    def chk(self, where: str, label: str, cond: bool) -> None:
        k = "ok" if cond else "MISS"
        self.n[k] += 1
        self.rows.append({"where": where, "check": label, "result": k})
        print(f"    {k:4s} {label}")


def verdicts(findings: List[M.Finding], rule: str) -> List[str]:
    return [f.verdict for f in findings if f.rule == rule]


def run_case(ids: List[str], T: Dict[str, str], eng: FixtureEngine, notarised, work: Path) -> Any:
    pages = [M.PageRead(index=k, file=f"FIXTURE {n}", file_page=k, text=T[n], kind="ocr", chars=len(T[n]),
                        sideways=False, seconds=0.0, image_only=False) for k, n in enumerate(ids)]
    eng.images = {k: OWN / f"{n}_200.png" for k, n in enumerate(ids)}
    eng._memo = {}
    unit = M.DocUnit(uid="D01", key="birth_cert", fine="birth_cert", title="BIRTH CERTIFICATE",
                     pages=list(range(len(ids))))
    facts = B.read_birth_certificate(unit, pages, eng, work, notarised)
    return facts, unit, pages


def ident_with(dob: Optional[dt.date]) -> M.Identity:
    return M.Identity(student=M.NameRef(), father=M.NameRef(), mother=M.NameRef(), dob=dob,
                      dob_source="MRZ" if dob else "", dob_trusted=dob is not None)


def dob_ok(f, exp_dob: Optional[dt.date], out: List[M.Finding]) -> bool:
    """The certificate's DOB equals the truth and is trusted, or it is withheld with a DOB-unclear FLAG —
    never a wrong trusted DOB."""
    if f.dob is not None and f.dob_trusted:
        return f.dob == exp_dob
    if f.dob == exp_dob:
        return True
    return any(x.rule == "birth.dob" and x.verdict == M.FLAG and "DOB unclear" in x.message for x in out) or \
        (f.dob is None and any(x.rule == "birth.dob" and x.verdict == M.FLAG for x in out))


def not_issue_or_registration(f, x: Dict[str, Any]) -> bool:
    if f.dob is None:
        return True
    bad = {iso(x.get("issue_date")), iso(x.get("registration_date"))} - {None, iso(x.get("dob"))}
    return f.dob not in bad


def main(argv: Optional[List[str]] = None) -> int:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    bot = load(DEFAULT_CACHE_ROOT)
    notarised = bot.dv.notarised
    E = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
    reader = light_reader()
    eng = FixtureEngine(reader, {})
    tally = Tally()
    facts_log: List[Dict[str, Any]] = []

    for engine in ENGINES:
        reads = json.loads((FIX / f"ocr_{engine}.json").read_text(encoding="utf-8"))["reads"]
        for scale in SCALES:
            T = {n: reads[f"{n}@{scale}"]["text"] for n in ("17", "18", "19", "20")}
            tag = f"{engine}@{scale}"
            print(f"== {engine} engine text, scale {scale}")

            # 17 alone: a Bangla-only original without its translation
            print("  17 alone (Bangla original, no translation)")
            f, u, _ = run_case(["17"], T, eng, notarised, OUT / "work" / f"{tag}_17")
            out = B.check_birth_certificate(f, u, ident_with(iso(E["18"]["dob"])))
            w = f"{tag} 17"
            tally.chk(w, "format bangla_original, no translation", f.format == "bangla_original" and not f.has_translation)
            tally.chk(w, "birth.format FAIL", verdicts(out, "birth.format") == ["FAIL"])
            tally.chk(w, "birth.notary FAIL (registrar's seal only)", verdicts(out, "birth.notary") == ["FAIL"])
            tally.chk(w, "no DOB taken from the Bangla noise", f.dob is None)
            facts_log.append({"where": w, "format": f.format, "dob_source": f.dob_source, "trusted": f.dob_trusted})

            # 17 + 18 (and 18 + 17): the Bangla original with its notarised translation
            for order in (["17", "18"], ["18", "17"]):
                x = E["18"]
                w = f"{tag} {'+'.join(order)}"
                print(f"  {'+'.join(order)} (Bangla original + notarised translation)")
                f, u, _ = run_case(order, T, eng, notarised, OUT / "work" / f"{tag}_{'_'.join(order)}")
                out = B.check_birth_certificate(f, u, ident_with(iso(x["dob"])))
                k18 = order.index("18")
                tally.chk(w, "format bangla_original with translation, birth.format PASS",
                          f.format == "bangla_original" and f.has_translation and verdicts(out, "birth.format") == ["PASS"])
                tally.chk(w, "fields from the translation page only", f.english_pages == [k18]
                          and all(p == k18 for _, _, p in f.dob_readings))
                tally.chk(w, "birth.notary PASS", verdicts(out, "birth.notary") == ["PASS"])
                tally.chk(w, "DOB right and trusted (label+words), or withheld with DOB unclear", dob_ok(f, iso(x["dob"]), out))
                tally.chk(w, "DOB is not the issue / registration date", not_issue_or_registration(f, x))
                tally.chk(w, "BRN right where read", (not f.brn) or f.brn == x["brn"])
                tally.chk(w, "issue and registration dates kept apart",
                          f.issue_date in (None, iso(x["issue_date"])) and f.registration_date in (None, iso(x["registration_date"])))
                facts_log.append({"where": w, "format": f.format, "dob_source": f.dob_source, "trusted": f.dob_trusted,
                                  "dob_right": f.dob == iso(x["dob"]), "brn_read": bool(f.brn)})

            # 19: the new bilingual print
            x = E["19"]
            w = f"{tag} 19"
            print("  19 (bilingual QR print)")
            f, u, _ = run_case(["19"], T, eng, notarised, OUT / "work" / f"{tag}_19")
            out = B.check_birth_certificate(f, u, ident_with(iso(x["dob"])))
            tally.chk(w, "format bilingual_qr, birth.format PASS", f.format == "bilingual_qr" and verdicts(out, "birth.format") == ["PASS"])
            tally.chk(w, "birth.notary PASS (expected notarised)", bool(x["notarised"]) and verdicts(out, "birth.notary") == ["PASS"])
            tally.chk(w, "QR decoded, host bdris", f.qr_status == "decoded" and f.qr.get("host") == "bdris.gov.bd")
            tally.chk(w, "birth.qr PASS + the online NOTE", verdicts(out, "birth.qr") == ["PASS", "NOTE"])
            tally.chk(w, "no QR FAIL", "FAIL" not in [v.verdict for v in out if v.rule.startswith("birth.qr")])
            tally.chk(w, "DOB equal to the truth", f.dob == iso(x["dob"]))
            tally.chk(w, "birth.dob PASS", verdicts(out, "birth.dob") == ["PASS"])
            tally.chk(w, "DOB is not the issue / registration date", not_issue_or_registration(f, x))
            tally.chk(w, "BRN right where read", (not f.brn) or f.brn == x["brn"])
            facts_log.append({"where": w, "format": f.format, "dob_source": f.dob_source, "trusted": f.dob_trusted,
                              "qr_status": f.qr_status, "qr_stage": f.qr.get("stage"), "key_len": f.qr.get("key_len"),
                              "barcode": (f.qr.get("barcode") or {}).get("status"), "brn_read": bool(f.brn)})
            q19 = dict(f.qr)

            # 20: the older English print, boxed DOB
            x = E["20"]
            w = f"{tag} 20"
            print("  20 (older English bdris print, boxed DOB)")
            f, u, _ = run_case(["20"], T, eng, notarised, OUT / "work" / f"{tag}_20")
            out = B.check_birth_certificate(f, u, ident_with(iso(x["dob"])))
            tally.chk(w, "format english_bdris, birth.format PASS", f.format == "english_bdris" and verdicts(out, "birth.format") == ["PASS"])
            tally.chk(w, "birth.notary PASS (expected notarised)", bool(x["notarised"]) and verdicts(out, "birth.notary") == ["PASS"])
            tally.chk(w, "DOB right and trusted (boxes+words), or withheld with DOB unclear", dob_ok(f, iso(x["dob"]), out))
            tally.chk(w, "never a wrong trusted DOB", not (f.dob_trusted and f.dob != iso(x["dob"])))
            tally.chk(w, "DOB is not the issue / registration date", not_issue_or_registration(f, x))
            tally.chk(w, "BRN right where read", (not f.brn) or f.brn == x["brn"])
            other = iso(x["issue_date"])
            out_w = B.check_birth_certificate(f, u, ident_with(other))
            want = ("FAIL" if f.dob_trusted else "FLAG") if f.dob is not None else "FLAG"
            tally.chk(w, "a different passport DOB -> FAIL (trusted) / FLAG", verdicts(out_w, "birth.dob") == [want])
            facts_log.append({"where": w, "format": f.format, "dob_source": f.dob_source, "trusted": f.dob_trusted,
                              "dob_right": f.dob == iso(x["dob"]), "notes": [n.split(":")[0] for n in f.notes]})

            # the negative set: fixture 19's payload shape with made-up hosts and keys (no image)
            if q19.get("status") == "decoded":
                print("  19 negative set (made-up hosts and keys)")
                w = f"{tag} 19-neg"
                key = str(q19.get("key") or "")
                for label, payload, want in (
                        ("look-alike host -> FAIL", f"https://bdris-gov.sample.workers.dev{q19.get('path')}?key={key}", "FAIL"),
                        ("52-character key -> FLAG", f"https://bdris.gov.bd{q19.get('path')}?key={key[:52]}", "FLAG"),
                        ("http, not https -> FAIL", f"http://bdris.gov.bd{q19.get('path')}?key={key}", "FAIL")):
                    q = B.parse_payload(payload)
                    q.update(status="decoded", payload=payload, page=0, barcode={"status": "undecoded", "payload": ""})
                    fn = M.BirthFacts(pages=(0,), format="bilingual_qr", page_kinds=["bilingual_qr"], english_pages=[0],
                                      qr=q, qr_status="decoded", notarised=True, notary_pages=[0])
                    got = verdicts(B.check_birth_certificate(fn, u, ident_with(None)), "birth.qr")
                    tally.chk(w, label, got[:1] == [want])

        # 19: the QR decodes in both setups (pyzbar on / OpenCV only), on the fixture image
    print("== 19 QR in both setups")
    for use_zbar in (True, False):
        r = B.decode_birth_qr(OWN / "19_200.png", barcode=False, use_zbar=use_zbar)
        tally.chk("19 setups", f"QR decoded {'with pyzbar' if use_zbar else 'OpenCV only'}",
                  r.get("status") == "decoded" and r.get("host") == "bdris.gov.bd" and r.get("key_len") == 64
                  and r.get("key_bytes") == 48)
    secs = round(time.time() - t0, 1)
    print("TOTAL", tally.n, f"{secs}s")
    (OUT / "results.json").write_text(json.dumps({"total": tally.n, "seconds": secs, "checks": tally.rows,
                                                  "facts": facts_log}, indent=1, default=str), encoding="utf-8")
    return 0 if tally.n["MISS"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
