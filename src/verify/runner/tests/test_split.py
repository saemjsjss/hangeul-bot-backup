"""Task 2 tests: the order-aware split (RUNNER_SPEC section 5.4, revision 2; Task 2 acceptance list).

Bundles are lists of made-up cue texts (tests/test_pagetype.py TEXTS); every name is a placeholder and
every number is built at run time. No personal data.

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree> -p "test_split*.py"
"""
from __future__ import annotations

import unittest
from pathlib import Path
from typing import List, Optional, Sequence

from src.verify.runner import botimport as B
from src.verify.runner import pagetype as PT
from src.verify.runner import split as SP
from src.verify.runner.model import FLAG, NOTE, DocUnit, Source, SourceFile, SplitResult
from src.verify.runner.tests.test_pagetype import (FA, FA2, MO, ST, TEXTS, death, nid_card, nid_noname_card,
                                                    nid_noname_tr, nid_tr, page)

T = TEXTS
FATHER = [nid_tr(FA, 1, "01 Jan 1960"), nid_card(FA, 1, "01 Jan 1960")]
MOTHER = [nid_tr(MO, 2, "02 Feb 1965"), nid_card(MO, 2, "02 Feb 1965")]
STUDENT = [nid_tr("GAMMA SAMPLE", 3, "03 Mar 2000"), nid_card("GAMMA SAMPLE", 3, "03 Mar 2000")]
NONAME = [nid_noname_tr(9), nid_noname_card(9)]
HEAD = [T["passport"], T["photo"]]
MIDDLE = [T["birth"], T["family"], T["apostille_hsc"], T["hsc_cert"], T["hsc_tr"], T["ssc_cert"], T["ssc_tr"]]
BANK = [T["solvency"], T["statement"]]
FIN = [T["trade"], T["tin"]]
FULL = HEAD + FATHER + MOTHER + MIDDLE + BANK + FIN + [T["income_tax"]]


def run(texts: Sequence[str], program: str = "KLP", refs: Optional[SP.Refs] = None):
    pages = [page(i, t) for i, t in enumerate(texts)]
    types = PT.type_pages(pages)
    seg = SP.segment(types, program)
    r = refs if refs is not None else SP.references(list(texts), seg.docs)
    res = SP.finish(seg, types, pages, r, program)
    return res, seg, types, pages


def layout(res: SplitResult):
    """[(key, fine, level, 1-based first, last, role)] in page order."""
    return [(u.key, u.fine, u.level, u.pages[0] + 1, u.pages[-1] + 1, u.role) for u in res.units]


def unit_at(res: SplitResult, page1: int) -> DocUnit:
    return next(u for u in res.units if page1 - 1 in u.pages)


def kinds(res: SplitResult, kind: str):
    return [n for n in res.notices if n.kind == kind]


class TestBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)

    # ------------------------------------------------------------------ the fixed order
    def test_fixed_order_bundle(self):
        res, *_ = run(FULL)
        self.assertEqual(layout(res), [
            ("passport", "passport", "", 1, 1, ""), ("photo", "photo", "", 2, 2, ""),
            ("father_nid", "nid", "", 3, 4, "father"), ("mother_nid", "nid", "", 5, 6, "mother"),
            ("birth_cert", "birth_cert", "", 7, 7, ""), ("family_cert", "family_cert", "", 8, 8, ""),
            ("academic", "academic", "HSC", 9, 11, ""), ("academic", "academic", "SSC", 12, 13, ""),
            ("bank", "bank", "", 14, 15, ""), ("trade_license", "trade_license", "", 16, 16, ""),
            ("trade_license", "tin_certificate", "", 17, 17, ""), ("income_tax", "income_tax", "", 18, 18, "")])
        self.assertEqual([u.uid for u in res.units], [f"D{i:02d}" for i in range(1, 13)])
        self.assertEqual([u.title for u in res.units][6:8], ["ACADEMIC HSC", "ACADEMIC SSC"])
        self.assertEqual([u.title for u in res.units][9:11], ["TRADE LICENSE", "TIN CERTIFICATE"])
        self.assertEqual([u.role_how for u in res.units][2:4], ["name", "name"])
        self.assertEqual(res.notices, [])
        self.assertEqual(res.missing, [])
        self.assertFalse(res.stop)
        self.assertEqual(res.maybe, {})
        self.assertTrue(all(not u.notes for u in res.units))
        res_m, *_ = run(FULL, "MASTER")
        self.assertEqual(res_m.missing, [])
        res_m2, *_ = run(FULL[:-1], "MASTER")
        self.assertEqual(res_m2.missing, ["income_tax"])
        res_k, *_ = run(FULL[:-1], "KLP")
        self.assertEqual(res_k.missing, [])

    def test_apostille_joins_its_level(self):
        acad = [T["hsc_cert"], T["hsc_tr"]]
        base = HEAD + FATHER + MOTHER + [T["birth"], T["family"]]
        tail = BANK + FIN + [T["income_tax"]]
        a0 = len(base) + 1                                   # 1-based first academic page
        # an apostille naming SSC, placed between the HSC transcript and the SSC certificate: joins SSC
        res, *_ = run(base + acad + [T["apostille_ssc"], T["ssc_cert"], T["ssc_tr"]] + tail)
        hsc, ssc = [u for u in res.units if u.key == "academic"]
        self.assertEqual((hsc.level, hsc.pages[0] + 1, hsc.pages[-1] + 1), ("HSC", a0, a0 + 1))
        self.assertEqual((ssc.level, ssc.pages[0] + 1, ssc.pages[-1] + 1), ("SSC", a0 + 2, a0 + 4))
        # an apostille that names no level joins the level that follows it
        res, *_ = run(base + acad + [T["apostille"], T["ssc_cert"], T["ssc_tr"]] + tail)
        hsc, ssc = [u for u in res.units if u.key == "academic"]
        self.assertEqual((ssc.level, ssc.pages[0] + 1), ("SSC", a0 + 2))
        # an apostille naming HSC, behind the HSC transcript: stays with HSC
        res, *_ = run(base + acad + [T["apostille_hsc"], T["ssc_cert"], T["ssc_tr"]] + tail)
        hsc, ssc = [u for u in res.units if u.key == "academic"]
        self.assertEqual((hsc.level, hsc.pages[-1] + 1), ("HSC", a0 + 2))
        self.assertEqual((ssc.level, ssc.pages[0] + 1), ("SSC", a0 + 3))
        # the academic chain unit split (pure function)
        types = PT.type_pages([page(i, t) for i, t in enumerate([T["apostille"], T["ssc_cert"], T["ssc_tr"]])])
        self.assertEqual(SP.split_academic(types, 0, 2), [(0, 2, "SSC")])

    # ------------------------------------------------------------------ ORDER and STOP
    def test_two_adjacent_documents_swapped(self):
        texts = list(FULL)
        texts[6], texts[7] = texts[7], texts[6]              # family certificate before the birth certificate
        res, *_ = run(texts)
        self.assertFalse(res.stop)
        orders = kinds(res, "ORDER")
        self.assertEqual(len(orders), 1)
        n = orders[0]
        self.assertTrue(n.blocks)
        self.assertIn(n.pages, [(7, 7), (8, 8)])
        self.assertEqual(n.units, (unit_at(res, n.pages[0]).uid,))
        self.assertIn("in the fixed order it goes", n.message)       # one page: "it goes"
        self.assertTrue(unit_at(res, n.pages[0]).misplaced)
        judged = [u for u in res.units if u.uid not in n.units]
        self.assertEqual(len(judged), len(res.units) - 1)
        self.assertEqual(res.missing, [])

    def test_document_moved(self):
        texts = HEAD + FATHER + MOTHER + [T["birth"], T["family"], T["trade"]] + MIDDLE[2:] + BANK + [T["tin"],
                                                                                                    T["income_tax"]]
        res, *_ = run(texts)
        self.assertFalse(res.stop)
        orders = kinds(res, "ORDER")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].pages, (9, 9))
        self.assertTrue(orders[0].blocks)
        u = unit_at(res, 9)
        self.assertEqual((u.key, u.fine, u.misplaced), ("trade_license", "trade_license", True))
        self.assertNotIn("owner_doc", res.missing)

    def test_parents_swapped_names_readable(self):
        res, *_ = run(HEAD + MOTHER + FATHER + FULL[6:])
        self.assertFalse(res.stop)
        self.assertEqual([(u.key, u.role_how) for u in res.units[2:4]], [("mother_nid", "name"), ("father_nid", "name")])
        orders = kinds(res, "ORDER")
        self.assertEqual(len(orders), 1)
        self.assertIn("NIDs are not in the order", orders[0].message)
        self.assertFalse(orders[0].blocks)
        self.assertEqual(orders[0].pages, (3, 6))
        self.assertEqual(set(orders[0].units), {res.units[2].uid, res.units[3].uid})

    def test_parents_out_of_order_position_only_is_stop(self):
        res, *_ = run(HEAD + MOTHER + NONAME + FULL[6:])
        self.assertTrue(res.stop)
        self.assertEqual(res.units[3].role_how, "position")
        stops = kinds(res, "STOP")
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].pages, (1, len(FULL)))
        self.assertTrue(any("NIDs are not in the order" in n.message for n in kinds(res, "ORDER")))

    def test_misplaced_nid(self):
        """An NID out of the NID block: its person by name -> blocking ORDER, judged no further; no name
        -> STOP (the NID-person ambiguity)."""
        named = HEAD + MOTHER + MIDDLE + BANK + FATHER + FIN + [T["income_tax"]]    # father NID on pages 14-15
        res, *_ = run(named)
        self.assertFalse(res.stop)
        u = unit_at(res, 14)
        self.assertEqual((u.key, u.role, u.role_how, u.misplaced), ("father_nid", "father", "name", True))
        self.assertEqual(u.pages, [13, 14])
        o = [n for n in kinds(res, "ORDER") if n.pages == (14, 15)]
        self.assertEqual(len(o), 1)
        self.assertTrue(o[0].blocks)
        self.assertEqual(o[0].units, (u.uid,))
        self.assertNotIn("father_id", res.missing)
        unnamed = HEAD + MOTHER + MIDDLE + BANK + NONAME + FIN + [T["income_tax"]]
        res2, *_ = run(unnamed)
        self.assertTrue(res2.stop)
        self.assertEqual(unit_at(res2, 14).key, "nid_unassigned")
        self.assertIn("14-15", kinds(res2, "STOP")[0].message)

    def test_two_missing_documents_give_hints_not_stop(self):
        # no photo and no academic block; an HSC-like page sits inside the bank block; a blank page at the end
        texts = [T["passport"]] + FATHER + MOTHER + [T["birth"], T["family"], T["solvency"], T["academic_in_bank"],
                                                     T["statement"]] + FIN + [T["income_tax"], ""]
        res, *_ = run(texts)
        self.assertFalse(res.stop)
        self.assertEqual(res.missing, ["photo", "academic"])
        hints = kinds(res, "HINT")
        self.assertEqual(len(hints), 2)
        self.assertEqual(sorted(h.pages for h in hints), [(9, 9), (len(texts), len(texts))])
        self.assertTrue(all("MISSING in its place" in h.message for h in hints))
        self.assertEqual(kinds(res, "ORDER"), [])

    def test_many_unrecognised_pages_order_not_stop(self):
        u = T["unrecognised_weak"]
        texts = [T["passport"], u, u, T["birth"], T["family"], T["solvency"], T["statement"], u, u, T["income_tax"]]
        res, *_ = run(texts)
        self.assertFalse(res.stop)
        orders = kinds(res, "ORDER")
        self.assertEqual(sorted(n.pages for n in orders), [(2, 3), (8, 9)])
        self.assertTrue(all(n.blocks for n in orders))
        for n in orders:
            self.assertEqual(len(n.units), 1)
            un = next(x for x in res.units if x.uid == n.units[0])
            self.assertEqual(un.key, "unrecognised")
            self.assertTrue(any(f.rule == "split.unrecognised" and f.verdict == FLAG for f in un.notes))

    def test_order_breaks_inside_a_document_are_non_blocking(self):
        cases = {
            "translation is behind": HEAD + [FATHER[1], FATHER[0]] + MOTHER + FULL[6:],
            "not newest first": HEAD + FATHER + MOTHER + [T["birth"], T["family"], T["ssc_cert"], T["ssc_tr"],
                                                          T["hsc_cert"], T["hsc_tr"]] + BANK + FIN + [T["income_tax"]],
            "transcript comes before": HEAD + FATHER + MOTHER + [T["birth"], T["family"], T["hsc_tr"], T["hsc_cert"],
                                                                 T["ssc_cert"], T["ssc_tr"]] + BANK + FIN
                                       + [T["income_tax"]],
            "statement page comes before": HEAD + FATHER + MOTHER + MIDDLE + [T["statement"], T["solvency"]] + FIN
                                           + [T["income_tax"]],
        }
        for cue, texts in cases.items():
            with self.subTest(cue):
                res, *_ = run(texts)
                self.assertFalse(res.stop)
                orders = kinds(res, "ORDER")
                self.assertEqual(len(orders), 1, [n.message for n in orders])
                self.assertIn(cue, orders[0].message)
                self.assertFalse(orders[0].blocks)
                self.assertEqual(res.missing, [])
        res, *_ = run(cases["transcript comes before"])
        hsc = [u for u in res.units if u.key == "academic"][0]
        self.assertEqual((hsc.level, len(hsc.pages)), ("HSC", 2))     # one HSC unit, not two

    def test_death_certificate_outside_the_nid_block(self):
        texts = HEAD + MOTHER + [T["birth"], T["family"], death(FA)] + MIDDLE[2:] + BANK + FIN + [T["income_tax"]]
        res, *_ = run(texts)
        self.assertFalse(res.stop)
        d = unit_at(res, 7)
        self.assertEqual((d.key, d.role, d.role_how, d.misplaced), ("father_death_cert", "father", "name", True))
        orders = kinds(res, "ORDER")
        self.assertEqual(len(orders), 1)
        self.assertFalse(orders[0].blocks)
        self.assertIn("the father's, by name", orders[0].message)
        self.assertNotIn("father_id", res.missing)
        # inside the NID block, in the father's place: no notice at all
        res2, *_ = run(HEAD + [death(FA)] + MOTHER + FULL[6:])
        self.assertEqual(res2.notices, [])
        self.assertEqual(unit_at(res2, 3).key, "father_death_cert")
        # an unnamed death certificate outside the block: STOP
        res3, *_ = run(HEAD + MOTHER + [T["birth"], T["family"], death("UNREADABLE PERSON")] + MIDDLE[2:] + BANK
                       + FIN + [T["income_tax"]])
        self.assertTrue(res3.stop)

    # ------------------------------------------------------------------ missing slots
    def test_missing_slots(self):
        no_birth = [t for t in FULL if t != T["birth"]]
        self.assertEqual(run(no_birth)[0].missing, ["birth_cert"])
        employment_only = HEAD + FATHER + MOTHER + MIDDLE + BANK + [T["employment"], T["income_tax"]]
        res, *_ = run(employment_only)
        self.assertIn("tin", res.missing)
        self.assertNotIn("owner_doc", res.missing)
        tin_only = HEAD + FATHER + MOTHER + MIDDLE + BANK + [T["tin"], T["income_tax"]]
        res, *_ = run(tin_only)
        self.assertIn("owner_doc", res.missing)
        self.assertNotIn("tin", res.missing)
        nothing = HEAD + FATHER + MOTHER + MIDDLE + BANK
        self.assertEqual(run(nothing)[0].missing, ["owner_doc", "tin"])
        self.assertEqual(run(nothing, "MASTER")[0].missing, ["owner_doc", "tin", "income_tax"])
        self.assertEqual(SP.slots_for("master")[-1], "income_tax")
        for s in SP.slots_for("MASTER"):
            self.assertIn(s, SP.MISSING_TEXT)

    def test_employment_and_tin_are_two_units(self):
        res, *_ = run(HEAD + FATHER + MOTHER + MIDDLE + BANK + [T["employment"], T["tin"], T["income_tax"]])
        fin = [(u.fine, u.title) for u in res.units if u.key == "trade_license"]
        self.assertEqual(fin, [("employment_certificate", "EMPLOYMENT CERTIFICATE"), ("tin_certificate", "TIN CERTIFICATE")])
        self.assertEqual(res.missing, [])

    def test_trailing_page(self):
        res, *_ = run(FULL + [T["unrecognised"]])
        tax = res.units[-1]
        self.assertEqual((tax.key, tax.pages[-1] + 1), ("income_tax", len(FULL) + 1))
        notes = [f for f in tax.notes if f.rule == "split.attached_trailing"]
        self.assertEqual(len(notes), 1)
        self.assertEqual((notes[0].verdict, notes[0].pages), (NOTE, (len(FULL) + 1,)))
        self.assertEqual(res.notices, [])
        # after anything else: its own unit (a FLAG), no ORDER notice (it is not in the middle)
        res2, *_ = run(HEAD + FATHER + MOTHER + MIDDLE + BANK + [T["unrecognised"]])
        last = res2.units[-1]
        self.assertEqual(last.key, "unrecognised")
        self.assertTrue(any(f.rule == "split.unrecognised" for f in last.notes))
        self.assertEqual(kinds(res2, "ORDER"), [])

    # ------------------------------------------------------------------ persons
    def test_roles_without_references_are_position_flags(self):
        res, *_ = run(FULL, refs=SP.Refs())
        father, mother = res.units[2], res.units[3]
        self.assertEqual((father.key, father.role_how, mother.key, mother.role_how),
                         ("father_nid", "position", "mother_nid", "position"))
        for u in (father, mother):
            self.assertEqual([(f.verdict, f.rule) for f in u.notes], [(FLAG, "split.role_position")])

    def test_strict_role_scoring(self):
        refs = SP.Refs(father=["ALPHA", "SAMPLE"], mother=["BETA", "SAMPLE"], student=["SAMPLE", "GAMMA"])
        res, *_ = run(HEAD + FATHER + MOTHER + STUDENT + FULL[6:], refs=refs)
        self.assertEqual([(u.key, u.role_how) for u in res.units[2:5]],
                         [("father_nid", "name"), ("mother_nid", "name"), ("student_nid", "name")])
        # a dropped MD is not the same name: half the words -> name-partial (a FLAG), never 'name'
        refs_md = SP.Refs(father=["MD", "ALPHA", "SAMPLE"], mother=["BETA", "SAMPLE"])
        res2, *_ = run(FULL, refs=refs_md)
        self.assertEqual((res2.units[2].role, res2.units[2].role_how), ("father", "name-partial"))
        self.assertTrue(any(f.rule == "split.role_partial" for f in res2.units[2].notes))
        # dots do not decide who it is (the dot rule is names.py's)
        self.assertEqual(SP._strict(["MD", "ALPHA"], ["MD.", "ALPHA"]), "yes")
        self.assertEqual(SP._strict(["LONGFORM", "ALPHA"], ["MD", "ALPHA"]), "no")       # 1 of 3 words shared
        self.assertEqual(SP._strict(["LONGFORM", "ALPHA", "SAMPLE"], ["MD", "ALPHA", "SAMPLE"]), "part")
        self.assertEqual(SP._strict(["SAMPLE", "GAMMA"], ["GAMMA", "SAMPLE"]), "part")
        self.assertEqual(SP._strict(["SAMPLE", "GAMMA"], ["GAMMA", "SAMPLE"], any_order=True), "yes")

    # ------------------------------------------------------------------ units, cuts, paths
    def test_provisional_units_equal_the_final_pages(self):
        res, seg, types, pages = run(FULL)
        prov = SP.provisional_units(seg, pages)
        self.assertEqual([(u.uid, u.pages) for u in prov], [(u.uid, u.pages) for u in res.units])
        self.assertEqual([u.key for u in prov][2:4], ["nid_unassigned", "nid_unassigned"])
        self.assertTrue(all(not u.role for u in prov))
        self.assertEqual([u.key for u in prov if u.key not in ("nid_unassigned",)],
                         [u.key for u in res.units if u.key not in ("father_nid", "mother_nid")])

    def test_cut_plan_and_paths(self):
        res, *_ = run(FULL)
        plan = SP.cut_plan(res)
        self.assertEqual(len(plan), len(res.units))
        self.assertEqual(plan[0], ("PASSPORT p01.pdf", 0, 0))
        self.assertEqual(plan[1], ("PASSPORT SIZE PHOTO p02.jpg", 1, 1))
        self.assertEqual(plan[2], ("FATHER NID p03-04.pdf", 2, 3))
        self.assertEqual(plan[6], ("ACADEMIC CERTIFICATE HSC p09-11.pdf", 8, 10))
        self.assertEqual(plan[10], ("TIN CERTIFICATE p17.pdf", 16, 16))
        self.assertEqual(len({p[0] for p in plan}), len(plan))
        root = B.PRIVATE / "selftest" / "split-test-paths"                   # never written: paths only
        SP.attach_paths(res, [root / name for name, _, _ in plan])
        self.assertEqual(res.units[0].path, root / "PASSPORT p01.pdf")
        with self.assertRaises(ValueError):
            SP.attach_paths(res, [root / "one.pdf"])

    def test_unit_file_names_round_trip_through_the_bot(self):
        classify = self.ns.dv.classify
        for key in SP.FILE_TITLE:
            levels = ["HSC", "SSC", "degree", "diploma", ""] if key == "academic" else [""]
            fines = list(SP.FINANCIAL_TITLE) if key == "trade_license" else [key]
            for lv in levels:
                for fine in fines:
                    for pages in ([4], [4, 5, 6]):
                        u = DocUnit(uid="D01", key=key, fine=fine, title="", pages=pages, level=lv)
                        name = SP.unit_file_name(u)
                        with self.subTest(name=name):
                            want = None if key in SP.NEVER_CHECKED else key
                            self.assertEqual(classify(name), want)
        self.assertEqual(SP.unit_file_name(DocUnit("D01", "photo", "photo", "", [1])), "PASSPORT SIZE PHOTO p02.jpg")


class TestFolder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)

    def folder(self, files, program="KLP", refs=None):
        """files: [(file name, key_by_name, [page texts])] in name order."""
        sfs, pages = [], []
        root = Path(r"C:\made-up-folder")                     # never opened: the split reads no file
        for name, key, texts in files:
            sfs.append(SourceFile(path=root / name, name=name, is_image=False, n_pages=len(texts), size=1,
                                  mtime=0.0, key_by_name=key))
            for j, t in enumerate(texts):
                pages.append(page(len(pages), t, file=name, file_page=j))
        src = Source(mode="folder", root=root, files=sfs, case_id="F-0")
        types = PT.type_pages(pages)
        seg = SP.segment_folder(src, pages, types)
        r = refs if refs is not None else SP.Refs(father=["ALPHA", "SAMPLE"], mother=["BETA", "SAMPLE"],
                                                  student=["SAMPLE", "GAMMA"])
        return SP.finish(seg, types, pages, r, program), seg, pages

    def test_folder(self):
        files = [("ACADEMIC CERTIFICATE.pdf", "academic", [T["hsc_cert"], T["hsc_tr"], T["ssc_cert"], T["ssc_tr"]]),
                 ("BANK STATEMENT.pdf", "bank", [T["family"]]),
                 ("BIRTH CERTIFICATE.pdf", "birth_cert", [T["birth"]]),
                 ("DEATH CERTIFICATE.pdf", "death_cert", [death(FA)]),
                 ("FAMILY CERTIFICATE.pdf", "family_cert", [T["family"]]),
                 ("MOTHER NID.pdf", "mother_nid", MOTHER),
                 ("PASSPORT.pdf", "passport", [T["passport"]]),
                 ("PHOTO.jpg", "photo", [""]),
                 ("TRADE LICENSE.pdf", "trade_license", [T["trade"], T["tin"]])]
        res, seg, pages = self.folder(files)
        self.assertEqual(res.notices, [])
        self.assertFalse(res.stop)
        by_file = {u.source_file: u for u in res.units if not u.part}
        # revision 2.3: a file that holds several documents is cut into them (two levels; trade licence + TIN)
        self.assertEqual(len(res.units), len(files) + 2)
        acads = [u for u in res.units if u.source_file == "ACADEMIC CERTIFICATE.pdf"]
        self.assertEqual([(u.key, u.title, u.level, u.part) for u in acads],
                         [("academic", "ACADEMIC HSC", "HSC", "p1-2"), ("academic", "ACADEMIC SSC", "SSC", "p3-4")])
        dc = by_file["DEATH CERTIFICATE.pdf"]
        self.assertEqual((dc.key, dc.role, dc.role_how), ("father_death_cert", "father", "name"))
        mo = by_file["MOTHER NID.pdf"]
        self.assertEqual((mo.key, mo.role, mo.role_how), ("mother_nid", "mother", "file-name"))
        bank = by_file["BANK STATEMENT.pdf"]
        self.assertEqual([(f.verdict, f.rule) for f in bank.notes], [(FLAG, "split.name_vs_content")])
        self.assertTrue(all(not u.notes for u in res.units if u is not bank))
        tls = [u for u in res.units if u.source_file == "TRADE LICENSE.pdf"]
        self.assertEqual([(u.key, u.fine, u.title, u.part) for u in tls],
                         [("trade_license", "trade_license", "TRADE LICENSE", "p1"),
                          ("trade_license", "tin_certificate", "TIN CERTIFICATE", "p2")])
        self.assertEqual(res.missing, [])                     # the TIN page inside TRADE LICENSE.pdf fills "tin"
        staged = {name: Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\runs\X\docs") / name for name, _, _ in files}
        SP.attach_paths(res, staged)
        self.assertEqual(by_file["PASSPORT.pdf"].path, staged["PASSPORT.pdf"])
        prov = SP.provisional_units(seg, pages)
        self.assertEqual([u.pages for u in prov], [u.pages for u in res.units])
        self.assertEqual(next(u for u in prov if u.source_file == "MOTHER NID.pdf").key, "nid_unassigned")

    def test_unresolved_death_certificate_is_a_maybe(self):
        files = [("DEATH CERTIFICATE.pdf", "death_cert", [death("UNREADABLE PERSON")]),
                 ("MOTHER NID.pdf", "mother_nid", MOTHER), ("PASSPORT.pdf", "passport", [T["passport"]])]
        res, *_ = self.folder(files)
        dc = next(u for u in res.units if u.source_file == "DEATH CERTIFICATE.pdf")
        self.assertEqual((dc.key, dc.role), ("death_cert", ""))
        self.assertEqual(res.maybe, {"father_id": dc.uid})
        self.assertNotIn("father_id", res.missing)
        self.assertNotIn("mother_id", res.missing)
        self.assertIn("birth_cert", res.missing)

    def test_unnamed_file_types_by_content(self):
        files = [("SCAN 1.pdf", None, FATHER), ("SCAN 2.pdf", None, [T["income_tax"]]),
                 ("SCAN 3.pdf", None, [T["unrecognised"]])]
        res, *_ = self.folder(files)
        self.assertEqual([(u.key, u.role, u.role_how) for u in res.units],
                         [("father_nid", "father", "name"), ("income_tax", "", ""), ("unrecognised", "", "")])
        self.assertEqual(res.notices, [])


if __name__ == "__main__":
    unittest.main()
