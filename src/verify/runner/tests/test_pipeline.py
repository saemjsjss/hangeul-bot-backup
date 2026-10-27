"""Task 6: pipeline.py and __main__.py (RUNNER_SPEC 5 and 1.1) with every stage module replaced by a fake
(sys.modules, restored after each test). Made-up data only. Outputs go to a temporary folder under
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\tmp, removed afterwards. verdict.py and report.py are the real ones."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np  # noqa: F401  (loaded before sys.modules is patched: patch.dict drops modules first
import openpyxl  # noqa: F401      #  imported inside it, and numpy cannot be loaded twice)
import openpyxl.cell.cell  # noqa: F401
import openpyxl.styles  # noqa: F401
import openpyxl.utils  # noqa: F401

from src.verify.runner import __main__ as CLI
from src.verify.runner import pipeline as PL
from src.verify.runner import report as REP  # noqa: F401  (imported before any patching)
from src.verify.runner import verdict as VER  # noqa: F401
from src.verify.runner.botimport import PRIVATE
from src.verify.runner.model import (FAIL, FLAG, NOT_JUDGED, PASS, PRESENT, STOP, AffidavitInfo, BirthFacts,
                                     DerivedRecord, DocUnit, Finding, Identity, NameRef, Notice, PageRead, PageType,
                                     Source, SourceFile, SplitResult)

PKG = "src.verify.runner"
TMP_ROOT = PRIVATE / "tmp"
# the made-up bundle: one page per document, in the owner's order
KINDS = ["passport", "photo", "father_nid", "mother_nid", "birth_cert", "bank", "tin_certificate", "income_tax"]
TITLES = {"passport": "PASSPORT", "photo": "PASSPORT SIZE PHOTO", "father_nid": "FATHER NID",
          "mother_nid": "MOTHER NID", "birth_cert": "BIRTH CERTIFICATE", "bank": "BANK STATEMENT",
          "tin_certificate": "TIN CERTIFICATE", "income_tax": "INCOME TAX"}


def F(v, rule, msg=""):
    return Finding(verdict=v, rule=rule, message=msg or f"{rule} {v.lower()}", source="fake")


class World:
    """The fake stage modules and what they were called with."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.calls = []
        self.args = {}
        self.stop = False
        self.crop_ok = True
        self.unit_crop = True          # doccheck offers crop_unit_photo (the real one does)
        self.from_tin = True           # financial offers taxpayer_from_tin (the real one does)
        self.crash_in = ""
        self.passport_parents_page = False
        self.mods = {}
        self._build()

    def rec(self, name, **kw):
        self.calls.append(name)
        self.args.setdefault(name, []).append(kw)
        if self.crash_in == name:
            raise RuntimeError("made-up crash")

    # ------------------------------------------------------------------ modules
    def _mod(self, name):
        m = types.ModuleType(f"{PKG}.{name}")
        self.mods[f"{PKG}.{name}"] = m
        return m

    def _build(self):
        w = self
        # ---- safety
        sm = self._mod("safety")

        class SafetyError(Exception):
            pass

        sm.SafetyError = SafetyError

        def prepare(out, cache_root, input_path):
            w.rec("safety.prepare", out=out, cache_root=cache_root, input_path=input_path)
            if "inside-input" in str(out):
                raise SafetyError("the out folder lies inside the input")
            return SimpleNamespace(out=out, cache_root=cache_root, snapshots={"in": (1, 2, "h")}, blocked=[],
                                   bot_opens=0)

        def assert_writable(p):
            w.rec("safety.assert_writable", p=Path(p))
            if not Path(p).resolve().is_relative_to(PRIVATE):
                raise PermissionError("outside private-runner")

        def finish(state):
            w.rec("safety.finish", state=state)
            return {"unchanged": True}

        sm.prepare, sm.assert_writable, sm.finish = prepare, assert_writable, finish
        # ---- ingest
        im = self._mod("ingest")

        class InputError(Exception):
            pass

        im.InputError = InputError

        def open_input(path):
            w.rec("ingest.open_input", path=path)
            path = Path(path)
            if path.is_dir():
                files = [SourceFile(path=f, name=f.name, is_image=False, n_pages=1, size=1, mtime=0.0,
                                    key_by_name=None) for f in sorted(path.iterdir())]
                return Source(mode="folder", root=path, files=files, case_id="F-0123456789abcdef")
            return Source(mode="bundle", root=path, files=[SourceFile(path=path, name=path.name, is_image=False,
                                                                      n_pages=len(KINDS), size=1, mtime=0.0)],
                          case_id="B-0123456789abcdef")

        def stage(src, docs_dir):
            w.rec("ingest.stage", src=src, docs_dir=docs_dir)
            return {f.name: docs_dir / f.name for f in src.files} if src.mode == "folder" else {}

        im.open_input, im.stage = open_input, stage
        # ---- engine
        em = self._mod("engine")

        def load_page_texts(path, src):
            w.rec("engine.load_page_texts", path=path, src=src)
            if "foreign" in Path(path).name:
                raise InputError("the page texts belong to another file")
            return SimpleNamespace(label="given page texts")

        class Engine:
            def __init__(self, cache_root, threads=0):
                w.rec("engine.Engine", cache_root=cache_root)
                self.bot = SimpleNamespace(dv=SimpleNamespace(notarised=w.notarised))

            def set_today(self, today):
                w.rec("engine.set_today", today=today)

            @contextlib.contextmanager
            def case(self, case_id):
                w.rec("engine.case", case_id=case_id)
                yield SimpleNamespace()
                w.rec("engine.case_end")

            def read_source(self, src, staged, given=None):
                w.rec("engine.read_source", src=src, staged=staged, given=given)
                n = len(src.files) if src.mode == "folder" else len(KINDS)
                return [PageRead(index=i, file=(src.files[i].name if src.mode == "folder" else src.root.name),
                                 file_page=0 if src.mode == "folder" else i, text=f"MADE-UP {KINDS[i % len(KINDS)]}",
                                 kind="ocr", chars=20, sideways=False, seconds=0.1, image_only=True,
                                 engine="gpu" if given is not None else "cpu") for i in range(n)]

            def cut(self, bundle, parts, out_dir):
                w.rec("engine.cut", bundle=bundle, parts=list(parts), out_dir=out_dir)
                paths = []
                for name, a, b in parts:
                    p = out_dir / name
                    p.write_bytes(b"made-up")
                    paths.append(p)
                return paths

            def prime(self, cut_path, page_texts):
                w.rec("engine.prime", cut_path=cut_path, page_texts=list(page_texts))

            def page_image_of(self, page, dpi=300):
                w.rec("engine.page_image_of", page=page, dpi=dpi)
                return "IMAGE"

            def cut_page(self, page, out_dir):
                w.rec("engine.cut_page", page=page, out_dir=out_dir)
                return out_dir / f"page p{page.index + 1:02d}.pdf"

            def stats(self):
                return {"ocr_calls": 0, "pages_by_engine": {}}

        em.load_page_texts, em.Engine = load_page_texts, Engine
        # ---- pagetype
        pm = self._mod("pagetype")

        def type_pages(pages):
            w.rec("pagetype.type_pages", pages=pages)
            return [PageType(index=p.index, best=p.text.split()[-1], score=6.0, scores={}, translation=False,
                             level=None, kind="", nid_numbers=[], birth_dates=[], holder=[]) for p in pages]

        pm.type_pages = type_pages
        # ---- split
        spm = self._mod("split")
        spm.SLOTS = ["passport", "photo", "father_id", "mother_id", "birth_cert", "family_cert", "academic", "bank",
                     "owner_doc", "tin"]
        spm.MISSING_TEXT = {"family_cert": "FAMILY CERTIFICATE MISSING", "academic": "ACADEMIC CERTIFICATE MISSING",
                            "owner_doc": "TRADE LICENSE / EMPLOYMENT CERTIFICATE MISSING",
                            "income_tax": "INCOME TAX MISSING"}
        spm.SOLVENCY_MISSING_TEXT = "BANK SOLVENCY CERTIFICATE MISSING"

        def segment(types_, program):
            w.rec("split.segment", types=types_, program=program)
            return SimpleNamespace(mode="bundle", docs=[], feats=types_)

        def segment_folder(src, pages, types_):
            w.rec("split.segment_folder", src=src, pages=pages, types=types_)
            return SimpleNamespace(mode="folder", docs=[], feats=types_)

        def _units(final, seg):
            out = []
            n = len(seg.feats)
            for i in range(n):
                k = KINDS[i % len(KINDS)]
                key = {"tin_certificate": "trade_license"}.get(k, k)
                if not final and k.endswith("_nid"):
                    key = "nid_unassigned"
                out.append(DocUnit(uid=f"D{i + 1:02d}", key=key, fine=k, title=TITLES[k], pages=[i],
                                   role=("father" if k == "father_nid" else "mother" if k == "mother_nid" else "")
                                   if final else "", role_how="name" if final and k.endswith("_nid") else "",
                                   source_file=f"{TITLES[k]}.pdf" if seg.mode == "folder" else ""))
            return out

        def provisional_units(seg, pages):
            w.rec("split.provisional_units", seg=seg, pages=pages)
            return _units(False, seg)

        def finish(seg, types_, pages, refs, program):
            w.rec("split.finish", seg=seg, types=types_, pages=pages, refs=refs, program=program)
            ns = [Notice(kind=STOP, pages=(3, 4), message="whose NID is on pages 3-4 could not be told")] \
                if w.stop else [Notice(kind="HINT", pages=(9, 9), message="FAMILY CERTIFICATE MISSING in its place")]
            return SplitResult(units=_units(True, seg), notices=ns, missing=["family_cert", "academic"], stop=w.stop)

        def cut_plan(res):
            w.rec("split.cut_plan", res=res)
            return [(f"{u.title} p{u.pages[0] + 1:02d}" + (".jpg" if u.key == "photo" else ".pdf"),
                     u.pages[0], u.pages[-1]) for u in res.units]

        def attach_paths(res, paths):
            w.rec("split.attach_paths", res=res, paths=paths)
            if isinstance(paths, dict):
                for u in res.units:
                    u.path = paths.get(u.source_file)
            else:
                for u, p in zip(res.units, paths):
                    u.path = p

        spm.segment, spm.segment_folder, spm.provisional_units = segment, segment_folder, provisional_units
        spm.finish, spm.cut_plan, spm.attach_paths = finish, cut_plan, attach_paths
        # ---- names
        nm = self._mod("names")

        def make_confirmer(engine, pages, work_dir):
            w.rec("names.make_confirmer", engine=engine, pages=pages, work_dir=work_dir)
            return w.confirm

        def affidavit_info(units, pages):
            w.rec("names.affidavit_info", units=units, pages=pages)
            return AffidavitInfo()

        def covered(aff, a, b):
            return False

        def evaluate(ident, units, pages, aff, machine, births, confirm, extra_reads=()):
            w.rec("names.evaluate", ident=ident, units=units, aff=aff, machine=machine, births=births,
                  confirm=confirm, extra_reads=list(extra_reads))
            return SimpleNamespace(per_unit={"D03": [F(PASS, "names.father")]}, per_person={"father": PASS},
                                   summary=[F(PASS, "names.father", "father's name is the same on 2 documents")],
                                   rereads=[{"page": 2, "effect": "none", "crop": w.tmp / "made-up.png"}])

        nm.make_confirmer, nm.affidavit_info, nm.covered, nm.evaluate = make_confirmer, affidavit_info, covered, evaluate
        # ---- birth
        bm = self._mod("birth")

        def read_birth_certificate(unit, pages, eng, work_dir, notarised):
            w.rec("birth.read_birth_certificate", unit=unit, eng=eng, work_dir=work_dir, notarised=notarised)
            return BirthFacts(pages=tuple(unit.pages), format="english_bdris", english_pages=list(unit.pages),
                              dob=dt.date(2001, 2, 3))

        bm.read_birth_certificate = read_birth_certificate
        # ---- identity
        idm = self._mod("identity")

        def build_identity(units, pages, births, engine, work_dir, confirm=None):
            w.rec("identity.build_identity", units=units, births=births, engine=engine, confirm=confirm)
            father, mother = NameRef(), NameRef()
            if births:                                   # A3: the parents come from the birth certificate
                father = NameRef(parts=("SAMPLE", "FATHER"), written="SAMPLE FATHER", source="birth certificate",
                                 unit="D05", pages=(4,))
                mother = NameRef(parts=("SAMPLE", "MOTHER"), written="SAMPLE MOTHER", source="birth certificate",
                                 unit="D05", pages=(4,))
            elif w.passport_parents_page:
                father = NameRef(parts=("SAMPLE", "FATHER"), source="passport field")
            return Identity(student=NameRef(parts=("SAMPLE", "ONE"), source="passport MRZ", confirmed=True),
                            father=father, mother=mother, dob=dt.date(2001, 2, 3), dob_source="MRZ", dob_trusted=True)

        def refs(ident):
            w.rec("identity.refs", ident=ident)
            return SimpleNamespace(father=list(ident.father.parts), mother=list(ident.mother.parts),
                                   student=list(ident.student.parts))

        def finish_identity(ident, units, pages, births, confirm=None):
            w.rec("identity.finish_identity", ident=ident, units=units, births=births)
            for u in units:
                if u.key == "father_nid" and u.role_how in ("name", "file-name"):
                    ident.father_nid = NameRef(parts=("SAMPLE", "FATHER"), source="father NID", unit=u.uid)
            return ident

        idm.build_identity, idm.refs, idm.finish_identity = build_identity, refs, finish_identity
        # ---- doccheck
        dm = self._mod("doccheck")

        def crop_photo(eng, page_image, out_jpg):
            w.rec("doccheck.crop_photo", eng=eng, page_image=page_image, out_jpg=out_jpg)
            return out_jpg if w.crop_ok else None

        def check_documents(units, pages, rec, eng, mode, aff, births, confirm, *, work_dir=None, source_pdf=None):
            w.rec("doccheck.check_documents", units=units, rec=rec, eng=eng, mode=mode, aff=aff, births=births,
                  confirm=confirm, paths={u.uid: u.path for u in units}, work_dir=work_dir, source_pdf=source_pdf)
            per = {u.uid: [F(PASS, "page.colour")] for u in units}
            per["D05"] = [F(FLAG, "birth.dob", "DOB unclear, check by eye (page 5)")]
            per["D08"] = [F(PRESENT, "doc.present_info", "present — information only for KLP")]
            return SimpleNamespace(per_unit=per, machine=[], bundle=[])

        def crop_unit_photo(eng, unit, pages, source_pdf=None, docs_dir=None):
            w.rec("doccheck.crop_unit_photo", unit=unit, source_pdf=source_pdf, docs_dir=docs_dir)
            got = crop_photo(eng, eng.page_image_of(pages[unit.pages[0]], 300),
                             Path(docs_dir) / f"PASSPORT SIZE PHOTO p{unit.pages[0] + 1:02d}.jpg")
            if got is not None:
                unit.path = got
            return got

        dm.crop_photo, dm.check_documents = crop_photo, check_documents
        if self.unit_crop:
            dm.crop_unit_photo = crop_unit_photo
        # ---- bank
        bkm = self._mod("bank")

        def check_bank_unit(unit, page_texts, page_index, ident, covered_fn, confirm, solvency_pdf):
            w.rec("bank.check_bank_unit", unit=unit, page_texts=page_texts, page_index=page_index, ident=ident,
                  covered=covered_fn, confirm=confirm, solvency=solvency_pdf(page_index[0]))
            return SimpleNamespace(findings=[F(PASS, "bank.holder_parent", "account holder is the father")],
                                   holder=SimpleNamespace(name="SAMPLE FATHER", joint=[]), holder_role="father",
                                   solvency_pages=list(page_index), solvency_missing=False,
                                   person_reads=[("father", unit.uid, "SAMPLE FATHER", page_index[0])])

        bkm.check_bank_unit = check_bank_unit
        # ---- tin
        tm = self._mod("tin")

        def tin_page_types(unit, types_):
            w.rec("tin.tin_page_types", unit=unit)
            return ["tin" if unit.fine == "tin_certificate" else ""] * len(unit.pages)

        def check_tin_certificate(path, text, page_types=None, max_pages=20, confirm=None, *, page_texts=None,
                                  run_pages=None):
            w.rec("tin.check_tin_certificate", path=path, text=text, page_types=page_types, confirm=confirm,
                  page_texts=page_texts, run_pages=run_pages)
            return {"qr": {"name": "SAMPLE FATHER"}, "all_codes": ["made-up payload"], "img": np.zeros((2, 2))}

        def tin_findings(result, unit, pages):
            w.rec("tin.tin_findings", result=result, unit=unit)
            return [F(FLAG, "tin.web_address", "printed from a made-up host"), F(PASS, "tin.qr_tin")]

        tm.tin_page_types, tm.check_tin_certificate, tm.tin_findings = tin_page_types, check_tin_certificate, \
            tin_findings
        # ---- financial
        fm = self._mod("financial")

        def tin_taxpayer(text, qr_payloads=()):
            w.rec("financial.tin_taxpayer", text=text, qr_payloads=list(qr_payloads))
            return {"name": "SAMPLE FATHER", "source": "QR"}

        def chain_findings(taxpayer, tin_unit, owner_units, owner_texts, holder, bank_unit, ident, covered, confirm):
            w.rec("financial.chain_findings", taxpayer=taxpayer, tin_unit=tin_unit, owner_units=owner_units,
                  holder=holder, bank_unit=bank_unit)
            return {tin_unit.uid: [F(PASS, "financial.taxpayer_holder", "the taxpayer is the account holder")]}

        def chain_person_reads(taxpayer, tin_unit, owner_units, owner_texts, ident):
            w.rec("financial.chain_person_reads", taxpayer=taxpayer)
            return [("father", tin_unit.uid, "SAMPLE FATHER", tin_unit.pages[0])]

        def income_tax_findings(unit, text, tin_text, program):
            w.rec("financial.income_tax_findings", unit=unit, tin_text=tin_text, program=program)
            return [F(PASS, "income_tax.tax_office", "same tax circle/zone as the TIN certificate")]

        def taxpayer_from_tin(result, tin_unit=None):
            w.rec("financial.taxpayer_from_tin", result=result, tin_unit=tin_unit)
            return {"name": (result.get("qr") or {}).get("name", ""), "source": "QR"}

        fm.tin_taxpayer, fm.chain_findings, fm.chain_person_reads = tin_taxpayer, chain_findings, chain_person_reads
        if self.from_tin:
            fm.taxpayer_from_tin = taxpayer_from_tin
        fm.income_tax_findings = income_tax_findings

    def rebuild(self, **flags):
        for k, v in flags.items():
            setattr(self, k, v)
        self.mods.clear()
        self._build()
        return self.mods

    def notarised(self, text, path):
        return True

    def confirm(self, i, variant):
        return "unclear"


class _PipelineCase(unittest.TestCase):
    def setUp(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="test_pipeline_", dir=TMP_ROOT))
        self.world = World(self.tmp)
        self.bundle = self.tmp / "in" / "made-up bundle.pdf"
        self.bundle.parent.mkdir()
        self.bundle.write_bytes(b"%PDF-made-up")
        self.out = self.tmp / "out"
        self.patch = mock.patch.dict(sys.modules, self.world.mods)
        self.patch.start()
        self.verbose = PL.VERBOSE
        PL.VERBOSE = False
        self.stdout = mock.patch("sys.stdout", new_callable=lambda: _Sink())
        self.stderr = mock.patch("sys.stderr", new_callable=lambda: _Sink())
        self.stdout.start()
        self.stderr.start()

    def tearDown(self):
        self.stderr.stop()
        self.stdout.stop()
        PL.VERBOSE = self.verbose
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def main(self, *extra, inp=None, out=None):
        argv = [str(inp or self.bundle), "--program", "klp", "--out", str(out or self.out), "--today", "2026-10-27",
                *extra]
        return CLI.main(argv)

    def run_json(self):
        return json.loads((self.out / "run.json").read_text(encoding="utf-8"))

    def summary(self):
        return (self.out / "summary.txt").read_text(encoding="utf-8").splitlines()


class _Sink:
    def write(self, s):
        return len(s)

    def flush(self):
        pass

    def reconfigure(self, **kw):
        pass


class TestStages(_PipelineCase):
    ORDER = ["safety.prepare", "ingest.open_input", "engine.load_page_texts", "engine.Engine", "engine.set_today",
             "engine.case", "ingest.stage", "engine.read_source", "pagetype.type_pages", "split.segment",
             "names.make_confirmer", "split.provisional_units", "birth.read_birth_certificate",
             "identity.build_identity", "identity.refs", "split.finish", "identity.finish_identity",
             "split.cut_plan", "engine.cut", "split.attach_paths", "engine.prime", "doccheck.crop_unit_photo",
             "names.affidavit_info", "doccheck.check_documents", "bank.check_bank_unit",
             "tin.check_tin_certificate", "tin.tin_findings", "financial.taxpayer_from_tin", "financial.chain_findings",
             "financial.chain_person_reads", "financial.income_tax_findings", "names.evaluate", "engine.case_end",
             "safety.finish"]

    def assert_subsequence(self, want, got):
        it = iter(got)
        for w in want:
            self.assertTrue(any(g == w for g in it), f"{w} missing or out of order in {got}")

    def test_order_types_and_outputs(self):
        pt = self.tmp / "page_texts.json"
        pt.write_text("{}", encoding="utf-8")
        self.assertEqual(self.main("--page-texts", str(pt)), 0)
        w = self.world
        self.assert_subsequence(self.ORDER, w.calls)
        # birth before identity; finish_identity after split.finish; prime for every cut
        self.assertLess(w.calls.index("birth.read_birth_certificate"), w.calls.index("identity.build_identity"))
        self.assertLess(w.calls.index("split.finish"), w.calls.index("identity.finish_identity"))
        self.assertEqual(w.calls.count("engine.prime"), len(KINDS))
        primed = [a["cut_path"].name for a in w.args["engine.prime"]]
        self.assertEqual(primed, [p[0] for p in w.args["engine.cut"][0]["parts"]])
        self.assertEqual(w.args["engine.prime"][2]["page_texts"], ["MADE-UP father_nid"])
        # section-5 types
        pages = w.args["pagetype.type_pages"][0]["pages"]
        self.assertTrue(pages and all(isinstance(p, PageRead) for p in pages))
        self.assertTrue(all(isinstance(t, PageType) for t in w.args["split.segment"][0]["types"]))
        births = w.args["identity.build_identity"][0]["births"]
        self.assertEqual(list(births), [(4,)])
        self.assertIsInstance(births[(4,)], BirthFacts)
        self.assertIs(w.args["birth.read_birth_certificate"][0]["notarised"].__func__, World.notarised)
        self.assertEqual([u.key for u in w.args["identity.build_identity"][0]["units"]][2], "nid_unassigned")
        self.assertEqual([u.key for u in w.args["identity.finish_identity"][0]["units"]][2], "father_nid")
        self.assertIsNotNone(w.args["identity.build_identity"][0]["engine"])          # MRZ re-read on
        self.assertIs(w.args["engine.read_source"][0]["given"].label, "given page texts")
        rec = w.args["doccheck.check_documents"][0]["rec"]
        self.assertIsInstance(rec, DerivedRecord)
        self.assertEqual({k: v for k, v in rec.portal_view().items() if v}, {"Program": "KLP"})
        self.assertEqual(w.args["doccheck.check_documents"][0]["mode"], "bundle")
        self.assertEqual(w.args["bank.check_bank_unit"][0]["page_index"], [5])
        self.assertEqual(w.args["bank.check_bank_unit"][0]["solvency"].name, "page p06.pdf")
        self.assertEqual(w.args["tin.check_tin_certificate"][0]["page_types"], ["tin"])
        self.assertEqual(w.args["tin.check_tin_certificate"][0]["text"], "MADE-UP tin_certificate")
        self.assertEqual(w.args["financial.taxpayer_from_tin"][0]["tin_unit"].uid, "D07")
        self.assertNotIn("financial.tin_taxpayer", w.calls)
        tin_call = w.args["tin.check_tin_certificate"][0]
        self.assertEqual((tin_call["page_texts"], tin_call["run_pages"]), (["MADE-UP tin_certificate"], [6]))
        dc_call = w.args["doccheck.check_documents"][0]
        self.assertEqual((dc_call["work_dir"], dc_call["source_pdf"]), (self.out / "work", self.bundle))
        self.assertEqual(w.args["doccheck.crop_unit_photo"][0]["source_pdf"], self.bundle)
        self.assertEqual(w.args["financial.chain_findings"][0]["taxpayer"], {"name": "SAMPLE FATHER", "source": "QR"})
        self.assertEqual(w.args["financial.income_tax_findings"][0]["tin_text"], "MADE-UP tin_certificate")
        self.assertEqual(w.args["names.evaluate"][0]["extra_reads"],
                         [("father", "D06", "SAMPLE FATHER", 5), ("father", "D07", "SAMPLE FATHER", 6)])
        # outputs
        for name in ("summary.txt", "report.xlsx", "run.json", "log.txt"):
            self.assertTrue((self.out / name).is_file(), name)
        d = self.run_json()
        self.assertEqual(d["schema"], "runner-run/2")
        self.assertEqual(d["overall"], FLAG)
        self.assertEqual(d["reading"]["pages_by_engine"], {"gpu": len(KINDS), "cpu": 0, "layer": 0})
        self.assertEqual(d["safety"]["unchanged"], True)
        self.assertEqual(d["safety"]["blocked"], [])
        self.assertEqual(d["tin"]["D07"], {"qr": {"name": "SAMPLE FATHER"}, "all_codes": ["made-up payload"]})
        self.assertEqual(d["names_rereads"][0]["effect"], "none")
        self.assertEqual(set(d["timings"]) >= {"safety", "ingest", "engine", "stage", "ocr", "typing", "segment",
                                                "birth", "identity", "split", "cut", "names", "bot", "bank", "tin",
                                                "financial", "verdict"}, True)
        lines = self.summary()
        self.assertTrue(lines[0].endswith("RESULT: FLAG"))
        self.assertIn("READING: GPU pre-read 8 pages, CPU light engine 0, text layer 0", lines)
        self.assertIn("BIRTH CERTIFICATE (p5): FLAG — DOB unclear, check by eye (page 5)", lines)
        self.assertIn("INCOME TAX (p8): PRESENT — present — information only for KLP", lines)
        self.assertIn("FAMILY CERTIFICATE MISSING", lines)
        self.assertIn("NEW CHECKS (owner to confirm): tin.web_address on TIN CERTIFICATE (p7)", lines)
        self.assertIn("FINANCIAL CHAIN: PASS", lines)
        log = (self.out / "log.txt").read_text(encoding="utf-8")
        self.assertIn("timings", log)
        self.assertNotIn("MADE-UP", log)                       # no page text in the log

    def test_photo_crop_by_unit_keeps_the_cut_when_no_edge(self):
        self.patch.stop()
        self.patch = mock.patch.dict(sys.modules, self.world.rebuild(crop_ok=False))
        self.patch.start()
        self.assertEqual(self.main(), 0)
        paths = self.world.args["doccheck.check_documents"][0]["paths"]
        self.assertEqual(paths["D02"].name, "PASSPORT SIZE PHOTO p02.jpg")     # kept; doccheck marks it

    def test_photo_without_edges_has_no_path(self):
        self.patch.stop()
        self.patch = mock.patch.dict(sys.modules, self.world.rebuild(crop_ok=False, unit_crop=False,
                                                                     from_tin=False))
        self.patch.start()
        self.assertEqual(self.main(), 0)
        paths = self.world.args["doccheck.check_documents"][0]["paths"]
        self.assertIsNone(paths["D02"])                        # doccheck then gives photo.edges FLAG
        self.assertEqual(self.world.args["financial.tin_taxpayer"][0]["qr_payloads"], ["made-up payload"])
        self.assertEqual(self.world.args["doccheck.crop_photo"][0]["out_jpg"].name, "PASSPORT SIZE PHOTO p02.jpg")
        self.assertEqual(self.world.args["doccheck.crop_photo"][0]["page_image"], "IMAGE")
        self.assertEqual(self.run_json()["reading"]["pages_by_engine"], {"gpu": 0, "cpu": len(KINDS), "layer": 0})

    def test_reread_mrz_switch(self):
        self.assertEqual(self.main("--no-reread-mrz"), 0)
        self.assertIsNone(self.world.args["identity.build_identity"][0]["engine"])

    def test_parents_from_birth_certificate_without_passport_parents_page(self):
        self.world.passport_parents_page = False
        self.assertEqual(self.main(), 0)
        d = self.run_json()
        self.assertEqual(d["identity"]["father"]["source"], "birth certificate")
        self.assertEqual(d["identity"]["mother"]["source"], "birth certificate")
        self.assertEqual(d["identity"]["father_nid"]["source"], "father NID")      # finish_identity, final units
        refs = self.world.args["split.finish"][0]["refs"]
        self.assertEqual(refs.father, ["SAMPLE", "FATHER"])                         # the split saw the parents

    def test_folder_mode(self):
        folder = self.tmp / "in" / "made-up folder"
        folder.mkdir()
        for k in KINDS:
            (folder / f"{TITLES[k]}.pdf").write_bytes(b"%PDF-made-up")
        self.assertEqual(self.main(inp=folder), 0)
        w = self.world
        self.assertIn("split.segment_folder", w.calls)
        self.assertNotIn("split.segment", w.calls)
        self.assertNotIn("engine.cut", w.calls)
        self.assertNotIn("engine.prime", w.calls)
        self.assertNotIn("doccheck.crop_photo", w.calls)
        self.assertIsInstance(w.args["split.attach_paths"][0]["paths"], dict)
        self.assertEqual(w.args["doccheck.check_documents"][0]["mode"], "folder")
        self.assertTrue(any(s.startswith("PASSPORT (PASSPORT.pdf): ") for s in self.summary()))


class TestExits(_PipelineCase):
    def test_stop_exit_3_no_cut(self):
        self.world.stop = True
        self.assertEqual(self.main(), 3)
        for c in ("split.cut_plan", "engine.cut", "engine.prime", "doccheck.check_documents", "names.evaluate"):
            self.assertNotIn(c, self.world.calls)
        lines = self.summary()
        self.assertTrue(lines[0].endswith("RESULT: STOP"))
        self.assertIn("STOP: whose NID is on pages 3-4 could not be told (pages 3-4)", lines)
        self.assertEqual(self.run_json()["overall"], STOP)
        self.assertTrue((self.out / "report.xlsx").is_file())

    def test_split_only(self):
        self.assertEqual(self.main("--split-only"), 0)
        for c in ("engine.cut", "doccheck.check_documents", "bank.check_bank_unit", "names.evaluate"):
            self.assertNotIn(c, self.world.calls)
        d = self.run_json()
        self.assertEqual(d["overall"], NOT_JUDGED)
        self.assertEqual({x["verdict"] for x in d["docs"]}, {NOT_JUDGED})
        self.assertEqual([x[1] for x in d["missing_lines"]], ["FAMILY CERTIFICATE MISSING",
                                                              "ACADEMIC CERTIFICATE MISSING"])
        self.assertTrue(any(s.startswith("PAGE TYPES: ") for s in self.summary()))

    def test_foreign_page_texts_exit_2_nothing_written(self):
        pt = self.tmp / "foreign page_texts.json"
        pt.write_text("{}", encoding="utf-8")
        self.assertEqual(self.main("--page-texts", str(pt)), 2)
        self.assertFalse(self.out.exists())
        self.assertNotIn("engine.Engine", self.world.calls)      # refused before the engine loads

    def test_unsafe_out_exit_2_nothing_written(self):
        outside = PRIVATE.parent / "not-private-runner-test-out"
        self.assertEqual(self.main(out=outside), 2)
        self.assertFalse(outside.exists())
        self.assertEqual(self.world.calls, [])                    # refused before any stage
        inside = self.tmp / "inside-input"
        self.assertEqual(self.main(out=inside), 2)                # safety.prepare refuses it
        self.assertFalse(inside.exists())
        folder = self.tmp / "in"
        self.assertEqual(self.main(inp=folder, out=folder / "out"), 2)
        self.assertFalse((folder / "out").exists())

    def test_removed_and_bad_flags_exit_2(self):
        for flags in (["--edge", "attach"], ["--prime"], ["--force-split"], ["--no-reread-names"], ["--split"]):
            self.assertEqual(self.main(*flags), 2, flags)
        self.assertEqual(CLI.main([str(self.bundle), "--program", "PHD", "--out", str(self.out)]), 2)
        self.assertEqual(self.main("--today", "27-10-2026"), 2)
        self.assertEqual(CLI.main([str(self.bundle), "--out", str(self.out)]), 2)            # --program missing
        self.assertEqual(self.main("--cache-root", str(PRIVATE.parent / "elsewhere")), 2)
        self.assertEqual(self.main("--page-texts", str(self.tmp / "absent.json")), 2)
        self.assertEqual(CLI.main([str(self.tmp / "absent.pdf"), "--program", "KLP", "--out", str(self.out)]), 2)
        self.assertEqual(self.main(out=Path("relative-out")), 2)                       # S1: absolute paths only
        self.assertEqual(CLI.main(["in/made-up bundle.pdf", "--program", "KLP", "--out", str(self.out)]), 2)
        self.assertEqual(self.world.calls, [])
        self.assertFalse(self.out.exists())

    def test_crash_exit_1_log_and_error_json(self):
        self.world.crash_in = "pagetype.type_pages"
        self.assertEqual(self.main(), 1)
        log = (self.out / "log.txt").read_text(encoding="utf-8")
        self.assertIn("Traceback", log)
        self.assertIn("CRASH in stage typing", log)
        d = self.run_json()
        self.assertEqual(d["error"], "RuntimeError: made-up crash")
        self.assertEqual(d["stage"], "typing")
        self.assertEqual(len(d["pages"]), len(KINDS))               # whatever finished
        self.assertEqual(d["case_id"], "B-0123456789abcdef")
        self.assertEqual(d["safety"]["unchanged"], True)

    def test_stdout_is_quiet_unless_show(self):
        """S3 (safety review): stdout carries the RESULT line, the headline and where the outputs are, never the
        document lines (they hold names read off the documents); --show prints the whole summary."""
        import io
        for extra, whole in (((), False), (("--show",), True)):
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                self.assertEqual(self.main(*extra), 0)
            printed = buf.getvalue().splitlines()
            lines = self.summary()
            self.assertEqual(printed[:2], lines[:2])
            if whole:
                self.assertEqual(printed, lines)
            else:
                self.assertEqual(len(printed), 3)
                self.assertTrue(printed[2].startswith("Summary: "))
                self.assertFalse(set(printed) & set(lines[2:]))

    def test_error_lines_name_no_path(self):
        import io
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertEqual(CLI.main([str(self.tmp / "made-up student name" / "absent.pdf"), "--program", "KLP",
                                       "--out", str(self.out)]), 2)
            self.assertEqual(self.main(out=PRIVATE.parent / "made-up student name"), 2)
        self.assertNotIn("made-up student name", err.getvalue())
        self.assertIn("<path>", err.getvalue())

    def test_reading_note_is_kept_short(self):
        self.assertEqual(self.main("--reading-note", "GPU slot not available within 120 s " + "x" * 300), 0)
        note = self.run_json()["reading"]["note"]
        self.assertLessEqual(len(note), CLI.NOTE_MAX)
        self.assertTrue(note.startswith("GPU slot not available within 120 s"))


class TestHelpers(unittest.TestCase):
    def test_call_flex(self):
        def fn(a, b, c=3):
            return (a, b, c)

        self.assertEqual(PL.call_flex(fn, a=1, b=2, d=4), (1, 2, 3))
        with self.assertRaises(TypeError):
            PL.call_flex(fn, a=1)

    def test_pages_by_engine(self):
        ps = [PageRead(index=i, file="f", file_page=i, text="", kind=k, chars=0, sideways=False, seconds=0,
                       image_only=False, engine=e) for i, (k, e) in enumerate([("layer", ""), ("ocr", ""),
                                                                               ("ocr", "gpu"), ("layer", "layer")])]
        self.assertEqual(PL.pages_by_engine(ps), {"gpu": 1, "cpu": 1, "layer": 2})


if __name__ == "__main__":
    unittest.main()
