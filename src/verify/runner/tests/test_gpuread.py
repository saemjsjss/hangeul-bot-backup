"""Task 8 tests (RUNNER_SPEC section 11, Task 8): gpuread.py (the GPU pre-read worker) and
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\run_case.py (the owner's test wrapper).

Runner venv, no torch, no graphics card, no bot: the reader (`dv`), the guard (`gpu_guard`), torch, `ingest`,
`safety` and `botimport` are fakes. Every file is synthetic and written under
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\tmp; every page text is made up ("PAGE n"). No personal data.

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree> -p "test_gpuread*.py"
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pymupdf

from src.verify.runner import gpuread as G
from src.verify.runner import model as M

PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
RUN_CASE = PRIVATE / "run_case.py"
GPUREAD_PATH = Path(G.__file__).resolve()
LAYER_TEXT = "This made-up page has a text layer of more than forty characters on it."
_GB = 1024 ** 3


# ------------------------------------------------------------------------------------------------ fakes
def _page_count(path: Path) -> int:
    with pymupdf.open(str(path)) as doc:
        return doc.page_count


class FakeIngest:
    """ingest.open_input / file_sha256 / InputError with the spec's semantics, on synthetic files."""

    class InputError(Exception):
        pass

    def __init__(self):
        self.sha_calls = 0

    def file_sha256(self, path):
        self.sha_calls += 1
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def open_input(self, path):
        path = Path(path)
        if path.is_file() and path.suffix.lower() == ".pdf":
            files, mode = [path], "bundle"
        elif path.is_dir():
            files = sorted(f for f in path.iterdir() if f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg"))
            mode = "folder"
        else:
            raise self.InputError("not a PDF or a folder")
        sfs = []
        for f in files:
            img = f.suffix.lower() != ".pdf"
            st = f.stat()
            sfs.append(M.SourceFile(path=f, name=f.name, is_image=img, n_pages=1 if img else _page_count(f),
                                    size=st.st_size, mtime=st.st_mtime))
        key = "\n".join(f"{s.name}|{s.size}|{int(s.mtime)}" for s in sfs)
        case = "B-" + hashlib.sha1(path.read_bytes()).hexdigest()[:16] if mode == "bundle" else \
            "F-" + hashlib.sha1(key.encode()).hexdigest()[:16]
        return M.Source(mode=mode, root=path, files=sfs, case_id=case)


class FakeDV:
    """The bot's doc_verifier reading surface: read_pages calls _ocr_image (looked up at call time) for every
    page with < 40 text-layer characters, as the real one does; the fake OCR returns "PAGE n"."""

    def __init__(self, events=None, sideways_pages=(), fail_on=None, device="cuda:0"):
        self.events = events if events is not None else []
        self.sideways_pages = set(sideways_pages)
        self.fail_on = fail_on
        self.ocr_calls = []
        self.doc_calls = []
        self.reader_calls = 0
        self.device = device
        self._ocr_image = self._fake_ocr

    def _fake_ocr(self, arr, turned=None):
        self.ocr_calls.append(arr)
        self.events.append(f"ocr{arr}")
        if arr == self.fail_on:
            raise RuntimeError("made-up OCR failure")
        if arr in self.sideways_pages and turned is not None:
            turned.append(True)
        return f"PAGE {arr}"

    def _ocr_reader(self):
        self.reader_calls += 1
        self.events.append("reader")
        return SimpleNamespace(device=self.device)

    def read_pages(self, path, max_pages=20, sideways=None):
        out = []
        with pymupdf.open(str(path)) as doc:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                t = page.get_text().strip()
                if len(t) < 40:
                    turned = []
                    t = self._ocr_image(i + 1, turned)
                    if turned and sideways is not None:
                        sideways.append(i + 1)
                out.append(t)
        return out

    def read_document(self, path, max_pages=20):
        self.doc_calls.append(Path(path).name)
        self.events.append("image")
        return "IMAGE TEXT", [(10, 10)]


class FakeGuard:
    """gpu_guard's surface: gpu_slot, capped_process, the three exceptions and DEV_LOCK (never touched)."""

    class SlotInterrupted(Exception):
        pass

    class SlotExpired(TimeoutError):
        pass

    class UncappedCardWork(ValueError):
        pass

    DEV_LOCK = Path(r"C:\Hangeul\DOCCHECK\gpu-dev.lock")

    def __init__(self, events=None, stop_at=None, stop_with=None, no_slot=False, refuse_cap=False):
        self.events = events if events is not None else []
        self.stop_at, self.stop_with = stop_at, stop_with
        self.no_slot, self.refuse_cap = no_slot, refuse_cap
        self.checkpoints = 0
        self.slot_args = []
        self.caps = []

    @contextlib.contextmanager
    def gpu_slot(self, what, need_free_gb=4.0, wait_s=900, max_s=90):
        self.events.append("gpu_slot")
        self.slot_args.append((what, need_free_gb, wait_s, max_s))
        if self.no_slot:
            raise TimeoutError("made-up: no slot")
        guard = self

        class _Slot:
            def check_cap(self, cap_gb):
                if guard.refuse_cap:
                    raise guard.UncappedCardWork("made-up refusal")
                guard.caps.append(cap_gb)

            def checkpoint(self):
                guard.checkpoints += 1
                guard.events.append("cp")
                if guard.stop_at is not None and guard.checkpoints == guard.stop_at:
                    raise (guard.stop_with or guard.SlotInterrupted)("made-up stop")

        try:
            yield _Slot()
        finally:
            self.events.append("slot_end")

    @contextlib.contextmanager
    def capped_process(self):
        self.events.append("capped")
        yield


class FakeCuda:
    def __init__(self, available=True):
        self.available = available
        self.fraction = None

    def is_available(self):
        return self.available

    def get_device_properties(self, index):
        return SimpleNamespace(total_memory=8 * _GB)

    def set_per_process_memory_fraction(self, fraction, device=None):
        self.fraction = fraction

    def max_memory_allocated(self):
        return int(2.5 * _GB)

    def max_memory_reserved(self):
        return int(2.75 * _GB)


class FakeSafety:
    class SafetyError(Exception):
        pass

    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.guard_calls = []

    def install_write_guard(self, extra_allowed=(), block_bot_opens=True, extra_forbidden=()):
        self.events.append("install_write_guard")
        self.guard_calls.append((list(extra_allowed), block_bot_opens))
        self.forbidden_calls = list(extra_forbidden)

    def check_input(self, p):
        p = Path(p)
        if not p.is_absolute() or not p.exists():
            raise self.SafetyError("made-up refusal")
        return p

    @staticmethod
    def redact(text):
        return str(text)

    @staticmethod
    def snapshot(paths):
        return {str(p): (Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in paths}

    def check_out_dir(self, out, input_path):
        out = Path(out)
        if not out.is_absolute() or not out.resolve().is_relative_to(PRIVATE) or out.resolve() == PRIVATE:
            raise self.SafetyError("made-up refusal")
        return out.resolve()


# ------------------------------------------------------------------------------------------------ helpers
@contextlib.contextmanager
def captured():
    """stdout and stderr of the code under test, kept off the test log."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err


def make_pdf(path: Path, layer_pages=(3,), n=4) -> Path:
    """A synthetic n-page PDF: the `layer_pages` (1-based) carry a text layer, the others only a drawing."""
    doc = pymupdf.open()
    for p in range(1, n + 1):
        page = doc.new_page(width=300, height=400)
        page.draw_rect(pymupdf.Rect(20, 20, 120, 80), color=(0, 0, 0))
        if p in layer_pages:
            page.insert_text((10, 200), LAYER_TEXT, fontsize=6)
    doc.save(str(path))
    doc.close()
    return path


def make_png(path: Path) -> Path:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 16), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    pix.save(str(path))
    return path


def valid_page_texts(test: unittest.TestCase, d: dict, src, ing: FakeIngest, complete=True) -> None:
    """The checks engine.load_page_texts makes (RUNNER_SPEC 5.2.1), plus the schema's shape."""
    test.assertEqual(d["schema"], M.PAGE_TEXTS_SCHEMA)
    test.assertEqual(d["mode"], src.mode)
    test.assertEqual(d["case_id"], src.case_id)
    test.assertIsInstance(d["engine"], str)
    test.assertIsInstance(d["slots"], list)
    test.assertIsInstance(d["seconds_total"], float)
    test.assertIs(d["complete"], complete)
    test.assertEqual([e["name"] for e in d["files"]], [f.name for f in src.files])
    for e, f in zip(d["files"], src.files):
        test.assertEqual(e["sha256"], ing.file_sha256(f.path))
        test.assertEqual(e["n_pages"], f.n_pages)
        test.assertEqual(e["size"], f.size)
        nums = [p["page"] for p in e["pages"]]
        test.assertEqual(nums, sorted(set(nums)))
        for p in e["pages"]:
            test.assertEqual(set(p), {"page", "text", "kind", "sideways", "seconds"})
            test.assertTrue(1 <= p["page"] <= f.n_pages)
            test.assertIn(p["kind"], ("layer", "ocr", "image"))
        if complete:
            test.assertEqual(nums, list(range(1, f.n_pages + 1)))


class _Base(unittest.TestCase):
    def setUp(self):
        (PRIVATE / "tmp").mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="test_gpuread_", dir=str(PRIVATE / "tmp")))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.ing = FakeIngest()
        p = mock.patch.object(G, "_ingest", lambda: self.ing)
        p.start()
        self.addCleanup(p.stop)
        self.pdf = make_pdf(self.dir / "bundle.pdf")
        self.src = self.ing.open_input(self.pdf)
        self.saves = []

    def save(self, d):
        self.saves.append(json.loads(json.dumps(d)))


# ------------------------------------------------------------------------------------------------ read_all
class TestReadAll(_Base):
    def test_bundle_complete(self):
        events = []
        dv = FakeDV(events)
        d = G.read_all(self.src, dv, lambda: events.append("cp"), {}, self.save)
        valid_page_texts(self, d, self.src, self.ing)
        pages = d["files"][0]["pages"]
        self.assertEqual([p["kind"] for p in pages], ["ocr", "ocr", "layer", "ocr"])
        self.assertEqual([p["text"] for p in pages], ["PAGE 1", "PAGE 2", LAYER_TEXT, "PAGE 4"])
        self.assertEqual(d["engine"], G.ENGINE_LABEL)
        # a checkpoint right before every OCR page, none for the text-layer page
        self.assertEqual(events, ["cp", "ocr1", "cp", "ocr2", "cp", "ocr4"])
        self.assertIs(dv._ocr_image.__func__, FakeDV._fake_ocr)      # the wrapper is removed afterwards
        # saved after every page; the last save is the complete result
        self.assertGreaterEqual(len(self.saves), 3)
        self.assertEqual(self.saves[-1], json.loads(json.dumps(d)))
        for s in self.saves:        # "complete" exactly when every page is in the file
            self.assertEqual(s["complete"], len(s["files"][0]["pages"]) == 4)
        self.assertEqual(sorted(p["page"] for p in self.saves[0]["files"][0]["pages"]), [1, 3])

    def test_sideways_flag(self):
        d = G.read_all(self.src, FakeDV(sideways_pages={2}), lambda: None, {}, self.save)
        self.assertEqual([p["sideways"] for p in d["files"][0]["pages"]], [False, True, False, False])

    def test_interrupt_then_resume(self):
        class Stop(Exception):
            pass

        calls = {"n": 0}

        def stop_third():
            calls["n"] += 1
            if calls["n"] == 3:
                raise Stop()

        dv = FakeDV(sideways_pages={2})
        with self.assertRaises(Stop):
            G.read_all(self.src, dv, stop_third, {}, self.save)
        partial = self.saves[-1]
        valid_page_texts(self, partial, self.src, self.ing, complete=False)
        self.assertEqual([p["page"] for p in partial["files"][0]["pages"]], [1, 2, 3])
        self.assertEqual(dv.ocr_calls, [1, 2])
        # resume: only page 4 is read, with one checkpoint; the earlier pages and their flags are reused
        dv2, cps = FakeDV(), []
        d = G.read_all(self.src, dv2, lambda: cps.append(1), partial, self.save)
        valid_page_texts(self, d, self.src, self.ing)
        self.assertEqual(dv2.ocr_calls, [4])
        self.assertEqual(len(cps), 1)
        self.assertEqual([p["text"] for p in d["files"][0]["pages"]], ["PAGE 1", "PAGE 2", LAYER_TEXT, "PAGE 4"])
        self.assertEqual([p["sideways"] for p in d["files"][0]["pages"]], [False, True, False, False])

    def test_done_of_another_file_is_not_reused(self):
        d1 = G.read_all(self.src, FakeDV(), lambda: None, {}, self.save)
        (self.dir / "other").mkdir()
        other = make_pdf(self.dir / "other" / "bundle.pdf", layer_pages=(1,))
        src2 = self.ing.open_input(other)
        dv = FakeDV()
        d2 = G.read_all(src2, dv, lambda: None, d1, self.save)          # same name, other sha256
        self.assertEqual(dv.ocr_calls, [2, 3, 4])
        valid_page_texts(self, d2, src2, self.ing)

    def test_complete_done_reads_nothing(self):
        d1 = G.read_all(self.src, FakeDV(), lambda: None, {}, self.save)
        d2 = G.read_all(self.src, None, G._never, d1, self.save)
        self.assertEqual(d2["files"], d1["files"])
        self.assertTrue(d2["complete"])

    def test_folder_with_image(self):
        folder = self.dir / "folder"
        folder.mkdir()
        make_pdf(folder / "A FILE.pdf", layer_pages=(2,), n=2)
        make_png(folder / "B FILE.png")
        src = self.ing.open_input(folder)
        events = []
        dv = FakeDV(events)
        d = G.read_all(src, dv, lambda: events.append("cp"), {}, self.save)
        valid_page_texts(self, d, src, self.ing)
        self.assertEqual(len(d["files"]), 2)                      # one entry per input file, in input order
        self.assertEqual([p["kind"] for p in d["files"][0]["pages"]], ["ocr", "layer"])
        self.assertEqual(d["files"][1]["pages"], [{"page": 1, "text": "IMAGE TEXT", "kind": "image",
                                                   "sideways": False,
                                                   "seconds": d["files"][1]["pages"][0]["seconds"]}])
        self.assertEqual(dv.doc_calls, ["B FILE.png"])            # the image is read once
        self.assertEqual(events, ["cp", "ocr1", "cp", "image"])
        # resume after the PDF: the image only
        dv2 = FakeDV()
        partial = copy.deepcopy(d)
        partial["files"][1]["pages"] = []
        d2 = G.read_all(src, dv2, lambda: None, partial, self.save)
        self.assertEqual((dv2.ocr_calls, dv2.doc_calls), ([], ["B FILE.png"]))
        self.assertTrue(d2["complete"])

    def test_crash_saves_partial(self):
        with self.assertRaises(RuntimeError):
            G.read_all(self.src, FakeDV(fail_on=2), lambda: None, {}, self.save)
        self.assertFalse(self.saves[-1]["complete"])
        self.assertEqual([p["page"] for p in self.saves[-1]["files"][0]["pages"]], [1, 3])


# ------------------------------------------------------------------------------------------------ legacy
class TestConvertLegacy(_Base):
    def _legacy(self, n=4, layer=3, layer_text=LAYER_TEXT):
        pages = {}
        for i in range(1, n + 1):
            if i == layer:
                pages[str(i)] = {"text": layer_text, "layer_chars": len(layer_text), "sideways": False,
                                 "method": "text_layer", "seconds": 0.0, "gpu_seconds": 0.0, "readtext_calls": 0}
            else:
                pages[str(i)] = {"text": f"PAGE {i}", "layer_chars": 0, "sideways": i == 2,
                                 "method": "ocr_200dpi_gpu", "seconds": 1.5, "gpu_seconds": 1.5,
                                 "readtext_calls": 1, "slot": 1}
        meta = {"source_pdf": "made-up.pdf", "pages_in_pdf": n, "engine": "made-up",
                "slots": [{"slot": 1, "waited_s": 0.1, "pages_read": [1, 2], "ended": "SlotExpired",
                           "model_load_s": 9.0, "peak_allocated_gb": 2.5, "peak_reserved_gb": 2.6},
                          {"slot": 2, "waited_s": 0.2, "pages_read": [4], "ended": "complete",
                           "model_load_s": 8.0, "peak_allocated_gb": 2.4, "peak_reserved_gb": 2.5}]}
        path = self.dir / "legacy_pages.json"
        path.write_text(json.dumps({"meta": meta, "pages": pages}), encoding="utf-8")
        return path

    def test_convert(self):
        out = self.dir / "runs" / "page_texts.json"
        G.convert_legacy(self._legacy(), self.pdf, out)
        d = json.loads(out.read_text(encoding="utf-8"))
        valid_page_texts(self, d, self.src, self.ing)
        pages = d["files"][0]["pages"]
        self.assertEqual([p["kind"] for p in pages], ["ocr", "ocr", "layer", "ocr"])
        self.assertEqual([p["sideways"] for p in pages], [False, True, False, False])
        self.assertEqual(d["seconds_total"], 4.5)
        self.assertEqual([(s["slot"], s["pages_read"], s["ended"]) for s in d["slots"]],
                         [(1, 2, "SlotExpired"), (2, 1, "complete")])
        self.assertFalse(out.with_name(out.name + ".tmp").exists())

    def test_page_count_must_agree(self):
        with self.assertRaises(ValueError):
            G.convert_legacy(self._legacy(n=3), self.pdf, self.dir / "x.json")
        self.assertFalse((self.dir / "x.json").exists())

    def test_text_layer_must_agree(self):
        with self.assertRaises(ValueError):
            G.convert_legacy(self._legacy(layer=2), self.pdf, self.dir / "x.json")
        with self.assertRaises(ValueError):
            G.convert_legacy(self._legacy(layer_text="another made-up text layer of more than forty chars"),
                             self.pdf, self.dir / "x.json")

    def test_out_outside_private(self):
        with self.assertRaises(ValueError):
            G.convert_legacy(self._legacy(), self.pdf, Path(r"C:\Hangeul\DOCCHECK\lab\not-private\x.json"))


# ------------------------------------------------------------------------------------------------ one session
class TestRunSession(_Base):
    def _run(self, gg, dv, torch=None, done=None, out=None):
        out = out or self.dir / "runs" / "page_texts.json"
        torch = torch or SimpleNamespace(cuda=FakeCuda())
        with captured() as (stdout, _):
            code = G.run_session(self.src, out, gg, dv, wait_s=120, slot_s=90, done=done,
                                 torch_loader=lambda: torch)
        self.assertNotIn("PAGE", stdout.getvalue())          # counts and timings only, never page text
        return code, out

    def test_complete(self):
        events = []
        gg, dv, torch = FakeGuard(events), FakeDV(events), SimpleNamespace(cuda=FakeCuda())
        code, out = self._run(gg, dv, torch)
        self.assertEqual(code, G.EXIT_OK)
        d = json.loads(out.read_text(encoding="utf-8"))
        valid_page_texts(self, d, self.src, self.ing)
        self.assertEqual(events[:4], ["gpu_slot", "capped", "reader", "cp"])
        self.assertEqual(events[-1], "slot_end")
        self.assertEqual(gg.caps, [G.CAP_GB])
        self.assertEqual(gg.slot_args[0][1:], (G.NEED_FREE_GB, 120, 90))
        self.assertLessEqual(torch.cuda.fraction * 8 * _GB, 3.0 * _GB)
        slot = d["slots"][-1]
        self.assertEqual((slot["slot"], slot["ended"], slot["peak_allocated_gb"], slot["pages_read"]),
                         (1, "complete", 2.5, 4))
        self.assertTrue(slot["device"].startswith("cuda"))

    def test_interrupted_then_resumed(self):
        out = self.dir / "runs" / "page_texts.json"
        dv = FakeDV()
        code, _ = self._run(FakeGuard(stop_at=2), dv, out=out)
        self.assertEqual(code, G.EXIT_STOPPED)
        d = json.loads(out.read_text(encoding="utf-8"))
        valid_page_texts(self, d, self.src, self.ing, complete=False)
        self.assertEqual(d["slots"][-1]["ended"], "SlotInterrupted")
        self.assertEqual(dv.ocr_calls, [1])
        # the next run resumes from the file on disk
        dv2 = FakeDV()
        code, _ = self._run(FakeGuard(), dv2, done=G.load_done(out, self.src), out=out)
        self.assertEqual(code, G.EXIT_OK)
        d = json.loads(out.read_text(encoding="utf-8"))
        valid_page_texts(self, d, self.src, self.ing)
        self.assertEqual(dv2.ocr_calls, [2, 4])
        self.assertEqual([(s["slot"], s["ended"]) for s in d["slots"]], [(1, "SlotInterrupted"), (2, "complete")])

    def test_slot_expired_is_exit_3(self):
        gg = FakeGuard(stop_at=1, stop_with=FakeGuard.SlotExpired)
        code, out = self._run(gg, FakeDV())
        self.assertEqual(code, G.EXIT_STOPPED)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["slots"][-1]["ended"], "SlotExpired")

    def test_no_slot_is_exit_5(self):
        dv = FakeDV()
        code, out = self._run(FakeGuard(no_slot=True), dv)
        self.assertEqual(code, G.EXIT_NO_SLOT)
        self.assertFalse(out.exists())
        self.assertEqual(dv.reader_calls, 0)

    def test_no_cuda_is_exit_4(self):
        dv = FakeDV()
        code, out = self._run(FakeGuard(), dv, torch=SimpleNamespace(cuda=FakeCuda(available=False)))
        self.assertEqual(code, G.EXIT_NO_CUDA)
        self.assertEqual(dv.reader_calls, 0)
        self.assertFalse(out.exists())

    def test_cap_refused_is_exit_4(self):
        code, out = self._run(FakeGuard(refuse_cap=True), FakeDV())
        self.assertEqual(code, G.EXIT_NO_CUDA)
        self.assertFalse(out.exists())

    def test_reader_not_on_the_card_is_exit_4(self):
        dv = FakeDV(device="cpu")
        code, out = self._run(FakeGuard(), dv)
        self.assertEqual(code, G.EXIT_NO_CUDA)
        self.assertEqual(dv.ocr_calls, [])

    def test_already_complete_takes_no_slot(self):
        code, out = self._run(FakeGuard(), FakeDV())
        gg = FakeGuard()
        code, _ = self._run(gg, FakeDV(), done=G.load_done(out, self.src), out=out)
        self.assertEqual(code, G.EXIT_OK)
        self.assertEqual(gg.events, [])

    def test_load_done_ignores_another_input(self):
        out = self.dir / "runs" / "page_texts.json"
        self._run(FakeGuard(stop_at=2), FakeDV(), out=out)
        other = self.ing.open_input(make_pdf(self.dir / "other.pdf", layer_pages=(1,)))
        self.assertEqual(G.load_done(out, other), {})
        self.assertEqual(G.load_done(self.dir / "absent.json", self.src), {})


# ------------------------------------------------------------------------------------------------ main
class TestMain(_Base):
    def _patch_all(self, events, dv, gg):
        safety, bot = FakeSafety(events), SimpleNamespace(dv=dv)
        loads = []

        def load(root):
            events.append("botimport.load")
            loads.append(Path(root))
            return bot

        class Ing(FakeIngest):
            def open_input(inner, path):
                events.append("open_input")
                return FakeIngest.open_input(inner, path)

        ing = Ing()
        torch = SimpleNamespace(cuda=FakeCuda())
        for name, value in (("_safety", lambda: safety), ("_botimport", lambda: SimpleNamespace(load=load)),
                            ("_ingest", lambda: ing), ("_load_guard", lambda: gg),
                            ("_import_torch", lambda: torch)):
            p = mock.patch.object(G, name, value)
            p.start()
            self.addCleanup(p.stop)
        return safety, loads, ing

    def test_order_of_main(self):
        events = []
        gg, dv = FakeGuard(events), FakeDV(events)
        safety, loads, ing = self._patch_all(events, dv, gg)
        out = self.dir / "runs" / "page_texts.json"
        with captured() as (stdout, _):
            code = G.main([str(self.pdf), str(out)])
        self.assertEqual(code, 0)
        self.assertEqual(events[:6], ["install_write_guard", "botimport.load", "open_input", "gpu_slot",
                                      "capped", "reader"])
        self.assertEqual(safety.guard_calls, [([FakeGuard.DEV_LOCK], False)])
        self.assertEqual(safety.forbidden_calls, [Path(os.path.abspath(self.pdf))])    # the input: never written
        self.assertEqual(loads, [G.GPU_DATA])
        valid_page_texts(self, json.loads(out.read_text(encoding="utf-8")), self.src, ing)
        # what main prints: counts and timings only, never page text or the input's name
        printed = stdout.getvalue()
        self.assertIn("pages 4/4", printed)
        self.assertNotIn("PAGE 1", printed)
        self.assertNotIn(LAYER_TEXT, printed)
        self.assertNotIn("bundle.pdf", printed)

    def test_crash_is_exit_1_with_log(self):
        events = []
        self._patch_all(events, FakeDV(events, fail_on=2), FakeGuard(events))
        out = self.dir / "runs" / "page_texts.json"
        with captured():
            code = G.main([str(self.pdf), str(out)])
        self.assertEqual(code, G.EXIT_CRASH)
        self.assertTrue(out.with_name(out.stem + ".gpuread.log").is_file())
        d = json.loads(out.read_text(encoding="utf-8"))
        self.assertFalse(d["complete"])
        self.assertTrue(d["slots"][-1]["ended"].startswith("error"))

    def _refused(self, argv):
        events = []
        safety, _, _ = self._patch_all(events, FakeDV(events), FakeGuard(events))
        with captured():
            code = G.main(argv)
        self.assertEqual(code, G.EXIT_ARGS, argv)
        self.assertEqual(events, [], argv)                  # nothing armed, loaded, opened or read
        return code

    def test_out_outside_private_is_exit_2(self):
        outside = Path(r"C:\Hangeul\DOCCHECK\lab\not-private-runner-probe\page_texts.json")
        self._refused([str(self.pdf), str(outside)])
        self._refused(["--from-legacy", str(self.dir / "l.json"), "--source", str(self.pdf), "--out", str(outside)])
        self.assertFalse(outside.parent.exists())

    def test_input_the_safety_check_refuses_is_exit_2(self):
        """S2 / S6: safety.check_input (resolved: under the bot folder, through a junction or a device /
        local-share spelling) refuses the input before anything is armed, loaded or read."""
        def refuse(p):
            raise FakeSafety.SafetyError("made-up refusal")
        with mock.patch.object(FakeSafety, "check_input", lambda self, p: refuse(p)):
            self._refused([str(self.pdf), str(self.dir / "runs" / "page_texts.json")])
            self._refused(["--from-legacy", str(self.dir / "l.json"), "--source", str(self.pdf), "--out",
                           str(self.dir / "runs" / "pt.json")])

    def test_bad_arguments_are_exit_2(self):
        out = str(self.dir / "page_texts.json")
        self._refused([])
        self._refused([str(self.pdf)])
        self._refused([str(self.pdf), str(self.dir / "page_texts.txt")])          # not a .json file
        self._refused([str(self.pdf), out, "--source", str(self.pdf)])
        self._refused(["--from-legacy", "l.json", "--out", out])                  # no --source
        self._refused([str(self.pdf), out, "--slot-s", "500"])                    # longer than a slot
        self._refused([str(self.pdf), out, "--no-such-flag"])
        folder = self.dir / "folder"
        folder.mkdir()
        self._refused([str(folder), str(folder / "page_texts.json")])             # inside the input

    def test_script_mode(self):
        """As a script (python -I -B gpuread.py): the help, and a refused output path, with no guard armed."""
        try:
            r = subprocess.run([sys.executable, "-I", "-B", str(GPUREAD_PATH), "--help"], capture_output=True,
                               text=True, timeout=120, cwd=str(PRIVATE))
        except (PermissionError, RuntimeError) as e:      # a guard armed earlier in this process
            self.skipTest(f"cannot start a child here: {e}")
        self.assertEqual(r.returncode, 0, r.stderr)
        env = dict(os.environ, **{G.NO_CARD_ENV: "1"})   # a broken check can never take the dev lock
        r = subprocess.run([sys.executable, "-I", "-B", str(GPUREAD_PATH), str(self.pdf),
                            r"C:\Hangeul\DOCCHECK\lab\not-private-runner-probe\x.json"],
                           capture_output=True, text=True, timeout=120, cwd=str(PRIVATE), env=env)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("not-private-runner-probe", r.stderr)          # error lines name no path

    def test_constants(self):
        self.assertEqual(G.SCHEMA, M.PAGE_TEXTS_SCHEMA)
        self.assertLessEqual(G.CAP_GB, 3.0)
        self.assertEqual(G.GUARD, Path(r"C:\Hangeul\DOCCHECK\lab\onnx\scripts\gpu_guard.py"))
        self.assertTrue(G.GPU_DATA.is_relative_to(PRIVATE))
        self.assertEqual(G.WORKTREE, Path(r"C:\Hangeul\JARVIS\prototype\page-types"))


# ------------------------------------------------------------------------------------------------ run_case
def _load_run_case():
    spec = importlib.util.spec_from_file_location("run_case_under_test", str(RUN_CASE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRunCase(_Base):
    def setUp(self):
        super().setUp()
        self.rc = _load_run_case()
        self.out = self.dir / "runs" / "CASE01"
        self.args = self.rc.parse_args([str(self.pdf), "--program", "klp", "--out", str(self.out)])

    def _plan(self, codes, inspect=(True, 4, 4), args=None):
        calls = []
        seq = list(codes)

        def call(cmd):
            calls.append(list(cmd))
            return seq.pop(0) if len(seq) > 1 else seq[0]

        with mock.patch.object(self.rc, "_inspect", lambda pt, inp: inspect):
            pt, note = self.rc.plan(args or self.args, call)
        return pt, note, calls

    def test_complete_gpu_read(self):
        pt, note, calls = self._plan([0])
        self.assertEqual((pt, note), (self.out / "page_texts.json", ""))
        self.assertEqual(calls, [[self.rc.BOT_PY, "-I", "-B", self.rc.GPUREAD, str(self.pdf),
                                  str(self.out / "page_texts.json"), "--wait-s", "120"]])

    def test_resumed(self):
        pt, note, calls = self._plan([3, 3, 0])
        self.assertEqual((pt, note, len(calls)), (self.out / "page_texts.json", "", 3))

    def test_no_slot_falls_back(self):
        pt, note, calls = self._plan([5], inspect=(False, 4, 0))
        self.assertIsNone(pt)
        self.assertTrue(note.startswith("GPU slot not available within 120 s"), note)
        self.assertIn("4 page(s) read on the CPU light engine", note)
        self.assertEqual(len(calls), 1)

    def test_no_slot_after_a_partial_read(self):
        pt, note, calls = self._plan([3, 5], inspect=(True, 4, 3))
        self.assertEqual(pt, self.out / "page_texts.json")            # the partial file is still used
        self.assertTrue(note.startswith("GPU slot not available within 120 s"), note)
        self.assertIn("1 page(s) read on the CPU light engine", note)

    def test_failure_falls_back_with_the_exit_code(self):
        for code in (4, 1, 7):
            pt, note, calls = self._plan([code], inspect=(False, 4, 0))
            self.assertIsNone(pt)
            self.assertTrue(note.startswith(f"GPU pre-read failed (exit {code})"), note)
            self.assertEqual(len(calls), 1)

    def test_cpu_makes_no_gpu_call(self):
        args = self.rc.parse_args([str(self.pdf), "--program", "EAP", "--out", str(self.out), "--cpu"])
        pt, note, calls = self._plan([0], args=args)
        self.assertEqual((pt, calls), (None, []))
        self.assertIn("--cpu", note)

    def test_never_more_than_max_runs(self):
        pt, note, calls = self._plan([3], inspect=(True, 4, 2))
        self.assertEqual(len(calls), self.rc.MAX_RUNS)
        self.assertIn("not finished", note)
        self.assertEqual(pt, self.out / "page_texts.json")

    def test_note_fits_the_runner(self):
        for codes, inspect in (([5], (False, 4, 0)), ([4], (False, 4, 0)), ([3], (True, 4, 1)), ([0], (False, 4, 0))):
            _, note, _ = self._plan(codes, inspect)
            self.assertLessEqual(len(note), 200)
            self.assertTrue(note)

    def test_runner_command(self):
        args = self.rc.parse_args([str(self.pdf), "--program", "master", "--out", str(self.out),
                                   "--today", "2026-10-27"])
        cmd = self.rc.runner_command(args, self.out / "page_texts.json", "")
        self.assertEqual(cmd[:5], [self.rc.RUNNER_PY, "-I", "-B", "-m", "src.verify.runner"])
        self.assertEqual(cmd[cmd.index("--program") + 1], "MASTER")
        self.assertEqual(cmd[cmd.index("--page-texts") + 1], str(self.out / "page_texts.json"))
        self.assertEqual(cmd[cmd.index("--reading-note") + 1], "")
        self.assertEqual(cmd[cmd.index("--today") + 1], "2026-10-27")
        cmd = self.rc.runner_command(self.args, None, "GPU slot not available")
        self.assertNotIn("--page-texts", cmd)
        self.assertNotIn("--today", cmd)
        self.assertEqual(cmd[cmd.index("--reading-note") + 1], "GPU slot not available")

    def test_bad_arguments(self):
        for argv in ([str(self.pdf), "--program", "PHD", "--out", str(self.out)],
                     [str(self.pdf), "--program", "KLP", "--out", str(self.out), "--today", "27/10/2026"],
                     [str(self.pdf), "--out", str(self.out)],
                     [str(self.pdf), "--program", "KLP"]):
            with self.assertRaises(ValueError):
                self.rc.parse_args(argv)
            with captured():
                self.assertEqual(self.rc.main(argv), 2)
        self.assertFalse(self.out.exists())

    def test_main_refuses_an_unsafe_out(self):
        safety = FakeSafety()
        outside = Path(r"C:\Hangeul\DOCCHECK\lab\not-private-runner-probe\CASE01")
        with mock.patch.object(self.rc, "_safety", lambda: safety), captured():
            self.assertEqual(self.rc.main([str(self.pdf), "--program", "KLP", "--out", str(outside)]), 2)
            self.assertEqual(self.rc.main([str(self.pdf), "--program", "KLP", "--out", str(PRIVATE)]), 2)
        self.assertFalse(outside.parent.exists())

    def test_main_refuses_an_input_the_safety_check_refuses(self):
        """S2: the input is checked (resolved) before the GPU worker sees it."""
        safety, calls = FakeSafety(), []

        def refuse(p):
            raise safety.SafetyError("made-up refusal")
        safety.check_input = refuse
        with mock.patch.object(self.rc, "_safety", lambda: safety),                 mock.patch.object(self.rc, "_call", lambda *a: calls.append(a) or 0), captured():
            self.assertEqual(self.rc.main([str(self.pdf), "--program", "KLP", "--out", str(self.out)]), 2)
        self.assertEqual(calls, [])
        self.assertFalse(self.out.exists())

    def test_main_refuses_an_input_folder_holding_a_junction(self):
        """Review 2 (S2/S7): an input folder that holds a junction is refused by the REAL safety.check_input before
        run_case lists or snapshots anything through it (no guard is armed in run_case). The junction points at a
        made-up folder in this test's scratch folder, never at the bot."""
        import _winapi
        from src.verify.runner import safety as real
        folder, target = self.dir / "infolder", self.dir / "elsewhere"
        folder.mkdir()
        target.mkdir()
        make_pdf(folder / "PASSPORT.pdf")
        (target / "a.pdf").write_bytes(b"made-up")
        j = folder / "j_inner"
        _winapi.CreateJunction(str(target), str(j))
        self.addCleanup(lambda: os.rmdir(j) if os.path.lexists(j) else None)    # the junction only, before rmtree
        calls, snaps = [], []
        with mock.patch.object(self.rc, "_safety", lambda: real), \
                mock.patch.object(real, "snapshot", lambda files: snaps.append(list(files)) or {}), \
                mock.patch.object(self.rc, "_call", lambda *a: calls.append(a) or 0), captured():
            self.assertEqual(self.rc.main([str(folder), "--program", "KLP", "--out", str(self.out)]), 2)
        self.assertEqual((calls, snaps), ([], []))
        self.assertFalse(self.out.exists())

    def test_main_input_changed_is_exit_1(self):
        """S7: the input is snapshotted before the GPU phase and compared at the end."""
        safety = FakeSafety()

        def fake_call(cmd, log, what):
            if what == "runner":
                with open(self.pdf, "ab") as fh:              # the made-up input changes during the run
                    fh.write(b"%% made-up change")
            return 0

        with mock.patch.object(self.rc, "_safety", lambda: safety),                 mock.patch.object(self.rc, "_call", fake_call),                 mock.patch.object(self.rc, "_inspect", lambda pt, inp: (True, 4, 4)), captured():
            code = self.rc.main([str(self.pdf), "--program", "KLP", "--out", str(self.out)])
        self.assertEqual(code, 1)
        self.assertIn("the input changed", (self.out / "run_case.log").read_text(encoding="utf-8"))

    def test_main_runs_plan_then_runner(self):
        safety, cmds = FakeSafety(), []

        def fake_call(cmd, log, what):
            cmds.append((what, list(cmd)))
            return 0

        with mock.patch.object(self.rc, "_safety", lambda: safety), \
                mock.patch.object(self.rc, "_call", fake_call), \
                mock.patch.object(self.rc, "_inspect", lambda pt, inp: (True, 4, 4)), captured():
            code = self.rc.main([str(self.pdf), "--program", "KLP", "--out", str(self.out)])
        self.assertEqual(code, 0)
        self.assertEqual([w for w, _ in cmds], ["gpuread", "runner"])
        runner = cmds[1][1]
        self.assertEqual(runner[runner.index("--page-texts") + 1], str(self.out / "page_texts.json"))
        log = (self.out / "run_case.log").read_text(encoding="utf-8")
        self.assertIn("runner exit 0", log)
        self.assertNotIn("bundle.pdf", log)                                  # no names or paths in the log

    def test_inspect_with_a_real_page_texts_file(self):
        with mock.patch.object(self.rc, "_ingest", lambda: self.ing):
            pt = self.out / "page_texts.json"
            self.assertEqual(self.rc._inspect(pt, self.pdf), (False, 4, 0))    # no file yet
            G.read_all(self.src, FakeDV(), lambda: None, {}, lambda d: G.write_json(pt, d))
            self.assertEqual(self.rc._inspect(pt, self.pdf), (True, 4, 4))
            other = make_pdf(self.dir / "other.pdf", layer_pages=(1,))
            self.assertEqual(self.rc._inspect(pt, other)[0], False)            # another input

    def test_constants(self):
        self.assertEqual(self.rc.PROGRAMS, M.PROGRAMS)
        self.assertEqual(self.rc.PAGE_TEXTS_SCHEMA, M.PAGE_TEXTS_SCHEMA)
        self.assertEqual(Path(self.rc.GPUREAD).resolve(), GPUREAD_PATH)
        self.assertEqual(self.rc.MAX_RUNS, 12)
        self.assertEqual(self.rc.WAIT_S, 120)


if __name__ == "__main__":
    unittest.main()
