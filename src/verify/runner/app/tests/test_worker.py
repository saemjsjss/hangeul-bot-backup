"""Task W: app\\worker.py run_job (APP_SPEC rev 2 R2.1.1, R2.1.4, R2.1.5, R2.3.3; rev 1 sections 2, 3.2, 3.3).

Every other task's module is a fake (hooks, safety, gpuinfo, cardgate, gpuengine, ingest, botimport, engine,
pipeline, places, resultcode, version), passed in through worker.Modules; report, summarypdf and checkfacts are the
real ones. The tests check the order of the steps (nothing touches the card before safety.prepare), the events,
the store path, READER_FACTORY, the reading note, every error mapping (exit 1-6), the outputs and that no made-up
personal data reaches checkfacts.json, the error texts or the worker log. Made-up data only; files only under
private-runner\\selftest\\app\\W (removed afterwards). No card, no torch, no real case."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import pymupdf

from src.verify.runner import ingest as REAL_INGEST
from src.verify.runner import report as REPORT
from src.verify.runner.app import checkfacts as CF
from src.verify.runner.app import summarypdf as SP
from src.verify.runner.app import worker as W
from src.verify.runner.app.tests.test_checkfacts import (MARKERS, NAME, TEST_ROOT, assert_no_personal_data,
                                                        fake_info, make_result)
from src.verify.runner.model import STOP

os.environ.setdefault("HDC_TEST_NO_CARD", "1")
INPUTS = TEST_ROOT / "worker-inputs"
JOB_ID = "0123456789abcdef0123456789abcdef"
CARD_NAMES = ("gpuinfo.", "CardGate", "gate.", "gpuengine.", "card.")

try:                                           # Task C's protocol: the events must be valid v1 events
    from src.verify.runner.app import protocol as PROTOCOL
except Exception:                              # pragma: no cover - C not built yet
    PROTOCOL = None


class Cancelled(BaseException):
    pass


def made_up_pdf(path: Path, pages: int = 1, **save) -> Path:
    doc = pymupdf.open()
    for k in range(pages):
        doc.new_page().insert_text((72, 72), f"MADE-UP PAGE {k + 1}")
    doc.save(str(path), **save)
    doc.close()
    return path


class World:
    """The fakes and what they were called with."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = []
        self.events = []
        self.cancel_on = None
        self.problems = []
        self.raise_at = {}
        self.input_changed = False
        self.result = make_result()
        self.info = fake_info()
        self.writes = []
        self.factory_seen = None
        self.ascii_seen = None
        self.engine = SimpleNamespace(READER_FACTORY=None)
        self.reader = SimpleNamespace(
            det=SimpleNamespace(stats=lambda: {"kind": "det", "calls_card": 12, "seconds_card": 3.5,
                                               "calls_cpu": 1, "seconds_cpu": 9.0, "oom_retries": 1,
                                               "cpu_reasons": {"oom": 1}}),
            rec=SimpleNamespace(stats=lambda: {"kind": "rec", "calls_card": 300, "seconds_card": 1.5,
                                               "calls_cpu": 0, "seconds_cpu": 0.0, "oom_retries": 0}))
        self.fr = {"pages": {"total": 5, "read": 5, "ocr": 3, "layer": 1, "image": 0, "skipped": 0},
                   "pending": 4, "reused": 0, "complete": True, "seconds_total": 6.2, "read_seconds": 3.6,
                   "model_loaded": True, "model_load_s": 2.1, "downscaled": 0, "skipped": 0, "oom_retries": 0,
                   "peak_alloc_gb": 2.41, "peak_reserved_gb": 2.8}
        self._build()

    # ------------------------------------------------------------------ plumbing
    def call(self, name, **kw):
        self.calls.append((name, kw))
        exc = self.raise_at.get(name)
        if exc is not None:
            raise exc

    def names(self):
        return [c[0] for c in self.calls]

    def kw(self, name):
        return next(k for n, k in self.calls if n == name)

    def listener(self, event, **data):
        self.events.append((event, dict(data)))
        if (self.cancel_on is not None and event not in ("done", "error", "cancelled")
                and data.get("state") != "end" and self.cancel_on(event, data)):
            raise Cancelled()

    def ev(self, name):
        return [d for e, d in self.events if e == name]

    # ------------------------------------------------------------------ the fake modules
    def _build(self):
        world = self
        hooks = SimpleNamespace(LISTENER=None, Cancelled=Cancelled)

        def emit(event, **data):
            if hooks.LISTENER is not None:
                hooks.LISTENER(event, **data)
        hooks.emit = emit
        self.hooks = hooks

        class SafetyError(Exception):
            pass

        def prepare(out, cache_root, input_path):
            world.call("safety.prepare", out=Path(out), cache_root=Path(cache_root), input=Path(input_path))
            Path(out).mkdir(parents=True, exist_ok=True)          # the real prepare creates the case folder
            world.state = SimpleNamespace(input=Path(input_path), snapshots={"made-up": (1, 2, "h")})
            return world.state

        def finish(state):
            world.call("safety.finish", state=state)
            if world.input_changed:
                raise SafetyError("the input changed during the run (1 file(s))")
            return {"input_unchanged": True}

        def assert_writable(p):
            p = Path(p)
            world.writes.append(p)
            if not str(p).startswith(str(world.root)):
                raise SafetyError("a written path must lie under the test folder")

        self.safety = SimpleNamespace(SafetyError=SafetyError, prepare=prepare, finish=finish,
                                      assert_writable=assert_writable)

        def probe():
            world.call("gpuinfo.probe")
            return world.info

        def problems(info, flags):
            world.call("gpuinfo.problems", flags=flags)
            return list(world.problems)

        def free_mb(index=0):
            world.call("gpuinfo.free_mb", index=index)
            return 6000

        self.gpuinfo = SimpleNamespace(probe=probe, problems=problems, free_mb=free_mb, NEED_FREE_MB=4096)

        class CardMemoryShort(Exception):
            def __init__(self, free_mb, need_mb):
                self.free_mb, self.need_mb = free_mb, need_mb
                super().__init__(f"free card memory {free_mb} MB, {need_mb} MB needed")

        class CardGate:
            def __init__(gate, lock_paths, emit, *, free_mb=lambda: None):
                world.call("CardGate", lock_paths=list(lock_paths), emit=emit, free_mb=free_mb)
                gate.on_pause = lambda: 0

            def wait_turn(gate, where):
                world.call("gate.wait_turn", where=where)

            def wait_memory(gate, need_mb, limit_s=900.0):
                world.call("gate.wait_memory", need=need_mb)
                if world.raise_at.get("memory_short"):
                    raise CardMemoryShort(2900, need_mb)
                return 6000

            def stats(gate):
                return {"waits": 1, "waited_s": 2.5, "by_why": {"lock": {"page": 1}}, "free_mb": 6000,
                        "lock_path": f"C:\\Users\\{NAME}\\auto_verify.lock", "locks": ["bot"]}

        self.cardgate = SimpleNamespace(CardGate=CardGate, CardMemoryShort=CardMemoryShort)

        class CardRefused(Exception):
            def __init__(self, code, values=None):
                self.code, self.values = code, dict(values or {})
                super().__init__(code)

        class Card:
            def facts(card):
                return {"torch": "2.11.0+cu128", "cuda": "12.8", "seconds": 14.2, "import_s": 6.1, "cuda_s": 2.3,
                        "name": "NVIDIA GeForce RTX 0000", "cap_gb": 3.0, "notes": ["made-up note"]}

            def peaks(card):
                return (2.6, 2.9)

            def free(card):
                world.call("card.free")

        def start_card(info, gate):
            world.call("gpuengine.start_card", info=info, gate=gate)
            return Card()

        def first_read(card, bot, src, gate, store, emit=None, *, ascii_dir=None):
            world.call("gpuengine.first_read", card=card, bot=bot, src=src, gate=gate, store=Path(store), emit=emit,
                       ascii_dir=ascii_dir)
            Path(ascii_dir).mkdir(parents=True, exist_ok=True)          # an ASCII copy was made
            (Path(ascii_dir) / "img-0001.jpg").write_bytes(b"made-up")
            world.ascii_seen = Path(ascii_dir)
            emit("page", done=1, of=4, seconds=1.2, where="card")
            if world.raise_at.get("first_read_late"):
                raise world.raise_at["first_read_late"]
            return dict(world.fr)

        def light_reader(card, gate, glue_data, model_dir, *, ascii_dir=None):
            world.call("gpuengine.light_reader", glue_data=glue_data, model_dir=model_dir, ascii_dir=ascii_dir)
            return world.reader

        def reading_note(fr):
            world.call("gpuengine.reading_note", fr=fr)
            return "app GPU build: made-up note"

        def card_error_code(e):
            texts = " ".join(str(x) for x in W.chain(e)).lower()
            if "no kernel image" in texts:
                return "CARD_NOT_SUPPORTED"
            return "CARD_FAILED" if "cuda" in texts else None

        self.gpuengine = SimpleNamespace(CardRefused=CardRefused, start_card=start_card, first_read=first_read,
                                         light_reader=light_reader, reading_note=reading_note,
                                         card_error_code=card_error_code)

        class InputError(Exception):
            pass

        def open_input(p):
            world.call("ingest.open_input", input=Path(p))
            return SimpleNamespace(case_id="B-0123456789abcdef", mode="bundle", files=[])

        self.ingest = SimpleNamespace(InputError=InputError, open_input=open_input,
                                      file_label=REAL_INGEST.file_label)
        self.botimport = SimpleNamespace(load=lambda cr: (world.call("botimport.load", cache_root=Path(cr)),
                                                          SimpleNamespace(dv=SimpleNamespace(_reader=None)))[1])

        class RefusedError(Exception):
            pass

        class RunCrashed(Exception):
            pass

        def run(input_path, program, out, *, today, cache_root, page_texts=None, reading_note="", **kw):
            world.call("pipeline.run", input=Path(input_path), program=program, out=Path(out), today=today,
                       cache_root=Path(cache_root), page_texts=Path(page_texts), reading_note=reading_note)
            f = world.engine.READER_FACTORY
            world.factory_seen = (f() if f else None, f(threads=4) if f else None)
            hooks.emit("stage", key="names", state="start")
            hooks.emit("budget", where="birth.qr", page=7)
            hooks.emit("stage", key="names", state="end", seconds=0.1)
            exc = world.raise_at.get("pipeline.run.late")
            if exc is not None:
                raise exc
            Path(out).mkdir(parents=True, exist_ok=True)
            return world.result

        self.pipeline = SimpleNamespace(run=run, RefusedError=RefusedError, RunCrashed=RunCrashed)
        self.places = SimpleNamespace(current=lambda: SimpleNamespace(
            glue_data=world.root / "engine" / "glue_data.json", models_dir=world.root / "engine" / "model",
            cache_root=world.root / "default-cache", temp_dir=world.root / "tmp", write_root=world.root))
        self.resultcode = SimpleNamespace(code=lambda r: (world.call("resultcode.code"), "ABCD-1234")[1])
        self.version = SimpleNamespace(
            APP_VERSION="1.0.0", BUILD_ID="20261029.1-abc1234", FLAVOUR="gpu-cu128", CUDNN="9.19.0",
            ARCH_FLAGS=("sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"),
            facts=lambda: {"app_version": "1.0.0", "build_id": "20261029.1-abc1234", "flavour": "gpu-cu128",
                           "cuda": "12.8", "cudnn": "9.19.0"},
            flavour_label=lambda: "GPU (CUDA 12.8)")

    def mods(self, **over):
        given = dict(hooks=self.hooks, safety=self.safety, gpuinfo=self.gpuinfo, cardgate=self.cardgate,
                     gpuengine=self.gpuengine, ingest=self.ingest, botimport=self.botimport, engine=self.engine,
                     pipeline=self.pipeline, places=self.places, resultcode=self.resultcode, version=self.version,
                     report=REPORT, summarypdf=SP, checkfacts=CF)
        given.update(over)
        return W.Modules(**given)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(INPUTS, ignore_errors=True)
        INPUTS.mkdir(parents=True)
        cls.pdf = made_up_pdf(INPUTS / "made-up bundle.pdf", 2)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(INPUTS, ignore_errors=True)
        with contextlib.suppress(OSError):
            (TEST_ROOT / "worker").rmdir()                       # only when every test's folder is gone

    def setUp(self):
        self.verbose = W.VERBOSE
        W.VERBOSE = False
        self.root = TEST_ROOT / "worker" / self._testMethodName
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        self.w = World(self.root)
        self.out = self.root / "reports" / "made-up case"

    def tearDown(self):
        W.VERBOSE = self.verbose
        shutil.rmtree(self.root, ignore_errors=True)

    def job(self, **over):
        j = {"schema": "hdc-job/1", "job": JOB_ID, "input": str(self.pdf), "program": "klp", "out": str(self.out),
             "today": "2026-10-28", "cache_root": str(self.root / "cache"), "trace_dir": None,
             "gpu": {"lock_paths": [str(self.root / "locks" / "fake.lock")], "index": 0, "cap_gb": 3.0,
                     "need_free_mb": 4096}}
        j.update(over)
        return j

    def run_job(self, job=None, **mods):
        return W.run_job(self.job() if job is None else job, self.w.listener, mods=self.w.mods(**mods))

    def error(self):
        errs = self.w.ev("error")
        self.assertEqual(len(errs), 1, self.w.events)
        self.assertEqual(self.w.ev("done"), [])
        return errs[0]

    def assert_no_card_calls(self):
        self.assertFalse([n for n in self.w.names() if n.startswith(CARD_NAMES)], self.w.names())


# ====================================================================================== the happy path
class TestOrder(Base):
    def test_steps_in_order(self):
        rc = self.run_job()
        self.assertEqual(rc, W.EXIT_OK)
        self.assertEqual(self.w.names(), [
            "safety.prepare", "gpuinfo.probe", "gpuinfo.problems", "CardGate", "gate.wait_turn",
            "gate.wait_memory", "gpuengine.start_card", "ingest.open_input", "botimport.load",
            "gpuengine.first_read", "gpuengine.light_reader", "gpuengine.reading_note", "pipeline.run",
            "safety.finish", "resultcode.code", "card.free"])
        self.assertEqual(self.w.kw("gate.wait_turn"), {"where": "start"})
        self.assertEqual(self.w.kw("gate.wait_memory"), {"need": 4096})

    def test_guard_before_any_card_work(self):
        self.run_job()
        names = self.w.names()
        first_card = min(i for i, n in enumerate(names) if n.startswith(CARD_NAMES))
        self.assertLess(names.index("safety.prepare"), first_card)
        p = self.w.kw("safety.prepare")
        self.assertEqual((p["out"], p["cache_root"], p["input"]), (self.out, self.root / "cache", self.pdf))
        self.assertIs(self.w.kw("safety.finish")["state"], self.w.state)        # the snapshot kept from before

    def test_gate_arguments(self):
        self.run_job()
        g = self.w.kw("CardGate")
        self.assertEqual(g["lock_paths"], [str(self.root / "locks" / "fake.lock")])
        self.assertIs(g["emit"], self.w.hooks.emit)
        self.assertEqual(g["free_mb"](), 6000)
        self.assertEqual(self.w.kw("gpuinfo.free_mb"), {"index": 0})
        self.assertEqual(self.w.kw("gpuinfo.problems")["flags"], self.w.version.ARCH_FLAGS)

    def test_store_note_and_runner_arguments(self):
        self.run_job()
        store = self.root / "cache" / "pagetexts" / "B-0123456789abcdef.json"
        fr = self.w.kw("gpuengine.first_read")
        self.assertEqual(fr["store"], store)
        self.assertTrue(store.parent.is_dir())
        self.assertIs(fr["emit"], self.w.hooks.emit)
        self.assertEqual(fr["ascii_dir"], str(self.root / "tmp" / JOB_ID / "ascii"))
        self.assertEqual(self.w.kw("botimport.load")["cache_root"], self.root / "cache")
        lr = self.w.kw("gpuengine.light_reader")
        self.assertEqual((lr["glue_data"], lr["model_dir"]),
                         (self.root / "engine" / "glue_data.json", self.root / "engine" / "model"))
        self.assertEqual(lr["ascii_dir"], fr["ascii_dir"])
        run = self.w.kw("pipeline.run")
        self.assertEqual(run["page_texts"], store)
        self.assertEqual(run["reading_note"], "app GPU build: made-up note")
        self.assertEqual(run["program"], "KLP")
        self.assertEqual(str(run["today"]), "2026-10-28")
        self.assertEqual((run["input"], run["out"], run["cache_root"]), (self.pdf, self.out, self.root / "cache"))
        self.assertEqual(self.w.kw("gpuengine.reading_note")["fr"]["model_load_s"], 2.1)

    def test_ascii_copies_removed_at_the_end(self):
        self.run_job()
        self.assertIsNotNone(self.w.ascii_seen)
        self.assertFalse(self.w.ascii_seen.exists())
        self.assertFalse(self.w.ascii_seen.parent.exists())

    def test_reader_factory_set_then_put_back(self):
        sentinel = object()
        self.w.engine.READER_FACTORY = sentinel
        self.run_job()
        self.assertEqual(self.w.factory_seen, (self.w.reader, self.w.reader))
        self.assertIs(self.w.engine.READER_FACTORY, sentinel)

    def test_listener_put_back(self):
        prev = object()
        self.w.hooks.LISTENER = prev
        self.run_job()
        self.assertIs(self.w.hooks.LISTENER, prev)

    def test_default_cache_root_from_places(self):
        job = self.job()
        del job["cache_root"]
        self.assertEqual(self.run_job(job), W.EXIT_OK)
        self.assertEqual(self.w.kw("safety.prepare")["cache_root"], self.root / "default-cache")
        self.assertEqual(self.w.kw("gpuengine.first_read")["store"].parent, self.root / "default-cache" / "pagetexts")

    def test_job_path_and_object(self):
        p = self.root / "job.json"
        p.write_text(json.dumps(self.job()), encoding="utf-8")
        self.assertEqual(self.run_job(str(p)), W.EXIT_OK)
        self.w = World(self.root)
        self.assertEqual(self.run_job(W.load_job(self.job())), W.EXIT_OK)

    def test_no_torch(self):
        before = set(sys.modules)
        self.run_job()
        new = set(sys.modules) - before
        self.assertFalse({m for m in new if m.split(".")[0] in ("torch", "torchvision", "easyocr", "onnxruntime")
                          or m.endswith(".gpuengine")}, new)


class TestEvents(Base):
    def test_event_sequence(self):
        self.run_job()
        seq = [(e, d.get("state"), d.get("key")) for e, d in self.w.events]
        self.assertEqual(seq, [("card", "found", None), ("stage", "start", "card"), ("card", "ready", None),
                               ("stage", "end", "card"), ("stage", "start", "firstread"), ("page", None, None),
                               ("stage", "end", "firstread"), ("stage", "start", "names"), ("budget", None, None),
                               ("stage", "end", "names"), ("done", None, None)])
        found = self.w.ev("card")[0]
        self.assertEqual(found, {"state": "found", "name": "NVIDIA GeForce RTX 0000", "cc": "12.0",
                                 "driver": "000.00", "total_mb": 8151, "free_mb": 6120})
        ready = self.w.ev("card")[1]
        self.assertEqual(ready, {"state": "ready", "torch": "2.11.0+cu128", "cuda": "12.8", "seconds": 14.2})

    def test_done_event(self):
        rc = self.run_job()
        done = self.w.ev("done")[0]
        self.assertEqual(done["exit"], rc)
        self.assertEqual((done["overall"], done["code"], done["out"]), ("FAIL", "ABCD-1234", str(self.out)))
        self.assertTrue(done["headline"].startswith("FAIL"))
        self.assertEqual(done["lines"], [[a, b] for a, b in REPORT.summary_lines(self.w.result, self.out)])
        self.assertTrue(done["summary_pdf"])
        self.assertTrue(done["checkfacts"])
        self.assertEqual(done["gpu"], {"card": "NVIDIA GeForce RTX 0000", "peak_alloc_gb": 2.6,
                                       "peak_reserved_gb": 2.9, "cpu_calls": 1, "waits": 1, "waited_s": 2.5})
        if os.name == "nt":
            self.assertIsInstance(done["peak_mb"], int)

    @unittest.skipIf(PROTOCOL is None, "Task C's protocol is not built")
    def test_events_are_valid_protocol_events(self):
        self.run_job()
        for e, d in self.w.events:
            self.assertEqual(PROTOCOL.validate(PROTOCOL.make(e, **d)), [], (e, d))
        self.w = World(self.root)
        self.w.problems = [SimpleNamespace(code="DRIVER_TOO_OLD", values={"driver": "000.00"})]
        self.run_job()
        for e, d in self.w.events:
            self.assertEqual(PROTOCOL.validate(PROTOCOL.make(e, **d)), [], (e, d))

    def test_stop_gives_exit_3(self):
        self.w.result = make_result(overall=STOP)
        self.assertEqual(self.run_job(), W.EXIT_STOP)
        self.assertEqual(self.w.ev("done")[0]["exit"], W.EXIT_STOP)


class TestOutputs(Base):
    def test_summary_pdf_and_checkfacts(self):
        self.run_job()
        pdf, facts_p = self.out / "summary.pdf", self.out / "checkfacts.json"
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF"))
        self.assertIn(pdf, self.w.writes)                   # every write asked the guard first
        self.assertIn(facts_p, self.w.writes)
        text = facts_p.read_text(encoding="ascii")
        assert_no_personal_data(self, text)
        f = json.loads(text)
        self.assertEqual(f["result_code"], "ABCD-1234")
        self.assertEqual(f["app"]["app_version"], "1.0.0")
        self.assertEqual(f["budget_hits"], [{"where": "birth.qr", "page": 7}])
        self.assertEqual(set(f["timings_s"]) & {"card", "firstread", "ingest", "names"},
                         {"card", "firstread", "ingest", "names"})
        g = f["gpu"]
        self.assertEqual(g["card"]["name"], "NVIDIA GeForce RTX 0000")
        self.assertEqual(g["engine"], {"torch": "2.11.0+cu128", "cuda": "12.8", "cudnn": "9.19.0", "cap_gb": 3.0})
        self.assertEqual(g["first_read"]["pages"]["ocr"], 3)
        self.assertEqual(g["first_read"]["model_load_s"], 2.1)
        self.assertEqual(g["light_reader"]["calls"]["det"]["cpu_calls"], 1)
        self.assertEqual(g["light_reader"]["calls"]["rec"]["card_calls"], 300)
        self.assertEqual((g["cpu_calls"], g["oom_retries"]), (1, 1))
        self.assertEqual(g["waits"]["count"], 1)
        self.assertNotIn("lock_path", g["waits"]["detail"])  # a path never enters
        self.assertEqual(g["peaks"], {"alloc_gb": 2.6, "reserved_gb": 2.9, "first_read_alloc_gb": 2.41,
                                      "first_read_reserved_gb": 2.8})
        self.assertIsNotNone(g["card_start_s"])
        self.assertIsNotNone(g["light_reader"]["model_load_s"])

    def test_summary_pdf_failure_is_not_fatal(self):
        def broken(*a, **k):
            raise OSError("made-up")
        rc = self.run_job(summarypdf=SimpleNamespace(write=broken))
        self.assertEqual(rc, W.EXIT_OK)
        done = self.w.ev("done")[0]
        self.assertFalse(done["summary_pdf"])
        f = json.loads((self.out / "checkfacts.json").read_text(encoding="ascii"))
        self.assertEqual(f["warnings"], ["summary.pdf not written: OSError"])

    def test_summary_pdf_says_gpu(self):
        self.run_job()
        with pymupdf.open(str(self.out / "summary.pdf")) as doc:
            t = doc[0].get_text()
        self.assertIn("App 1.0.0 GPU (CUDA 12.8), build 20261029.1-abc1234", t)
        self.assertIn("ABCD-1234", t)


# ====================================================================================== refusals (exit 2)
class TestRefusals(Base):
    def test_missing_input(self):
        rc = self.run_job(self.job(input=str(self.root / "no such file.pdf")))
        self.assertEqual(rc, W.EXIT_REFUSED)
        self.assertEqual(self.error()["code"], "NOT_PDF_OR_FOLDER")
        self.assertEqual(self.w.calls, [])                       # not even the guard

    def test_encrypted_input(self):
        enc = made_up_pdf(self.root / "locked.pdf", owner_pw="owner-x", user_pw="user-x",
                          encryption=pymupdf.PDF_ENCRYPT_AES_256)
        rc = self.run_job(self.job(input=str(enc)))
        self.assertEqual(rc, W.EXIT_REFUSED)
        err = self.error()
        self.assertEqual((err["code"], err["values"]), ("ENCRYPTED", {"file": "The bundle"}))
        self.assertNotIn("locked", err["text"])
        self.assertEqual(self.w.calls, [])

    def test_unsafe_report_folder(self):
        self.w.raise_at["safety.prepare"] = self.w.safety.SafetyError(
            "the report folder must not lie inside the input")
        self.assertEqual(self.run_job(), W.EXIT_REFUSED)
        self.assertEqual(self.error()["code"], "REPORT_INSIDE_INPUT")
        self.assert_no_card_calls()
        self.assertFalse((self.out / "checkfacts.json").exists())

    def test_safety_path_in_message_is_redacted(self):
        self.w.raise_at["safety.prepare"] = self.w.safety.SafetyError(
            f"a written path must lie under the data folder: C:\\Users\\{NAME}\\x")
        self.assertEqual(self.run_job(), W.EXIT_REFUSED)
        err = self.error()
        self.assertEqual(err["code"], "REPORT_NOT_WRITABLE")
        self.assertNotIn(NAME, err["text"])
        self.assertIn("<path>", err["text"])

    def test_runner_refusal(self):
        cause = self.w.ingest.InputError("the bundle is password-protected")
        try:
            raise self.w.pipeline.RefusedError(str(cause)) from cause
        except Exception as e:
            self.w.raise_at["pipeline.run"] = e
        self.assertEqual(self.run_job(), W.EXIT_REFUSED)
        err = self.error()
        self.assertEqual((err["code"], err["values"]), ("ENCRYPTED", {"file": "The bundle"}))

    def test_open_input_refusal(self):
        self.w.raise_at["ingest.open_input"] = self.w.ingest.InputError(
            "the folder holds no .pdf, .jpg, .jpeg or .png file")
        self.assertEqual(self.run_job(), W.EXIT_REFUSED)
        self.assertEqual(self.error()["code"], "EMPTY_FOLDER")
        self.assertIn("card.free", self.w.names())              # the started card is released

    def test_input_changed_after_the_runner(self):
        self.w.input_changed = True
        self.assertEqual(self.run_job(), W.EXIT_REFUSED)
        self.assertEqual(self.error()["code"], "INPUT_CHANGED")
        self.assertFalse((self.out / "summary.pdf").exists())
        self.assertFalse((self.out / "checkfacts.json").exists())
        self.assertNotIn("resultcode.code", self.w.names())

    def test_input_changed_inside_the_runner(self):
        cause = self.w.safety.SafetyError("the input changed during the run (2 file(s))")
        try:
            raise self.w.pipeline.RunCrashed("SafetyError in stage 'report'") from cause
        except Exception as e:
            self.w.raise_at["pipeline.run.late"] = e
        self.assertEqual(self.run_job(), W.EXIT_REFUSED)
        self.assertEqual(self.error()["code"], "INPUT_CHANGED")

    def test_bad_program_date_and_job(self):
        for job, code in ((self.job(program="XYZ"), "BAD_ARGS"), (self.job(today="28-10-2026"), "BAD_ARGS"),
                          (self.job(out="relative\\out"), "BAD_ARGS"),
                          ({k: v for k, v in self.job().items() if k != "out"}, "BAD_ARGS"),
                          (self.job(gpu={"index": "x"}), "BAD_ARGS")):
            self.w = World(self.root)
            self.assertEqual(self.run_job(job), W.EXIT_REFUSED, job)
            self.assertEqual(self.error()["code"], code)
            self.assertEqual(self.w.calls, [])


# ====================================================================================== the card (exit 6)
class TestCard(Base):
    def test_problem_found_by_nvml(self):
        self.w.problems = [SimpleNamespace(code="DRIVER_TOO_OLD", values={"driver": "000.00"}),
                           SimpleNamespace(code="CARD_TOO_SMALL", values={})]
        self.assertEqual(self.run_job(), W.EXIT_CARD)
        err = self.error()
        self.assertEqual((err["code"], err["values"]), ("DRIVER_TOO_OLD", {"driver": "000.00"}))
        self.assertEqual(self.w.names(), ["safety.prepare", "gpuinfo.probe", "gpuinfo.problems"])
        self.assertEqual(len(self.w.ev("card")), 1)              # "found", then the error
        facts = json.loads((self.out / "checkfacts.json").read_text(encoding="ascii"))
        self.assertEqual(facts["error"]["code"], "DRIVER_TOO_OLD")

    def test_no_nvidia_card(self):
        self.w.info = SimpleNamespace(nvml=False, error="no NVML", count=0, index=0, name="", driver="",
                                      cuda_driver=0, cc=(0, 0), total_mb=0, free_mb=0)
        self.w.problems = [SimpleNamespace(code="NO_NVIDIA_CARD", values={})]
        self.assertEqual(self.run_job(), W.EXIT_CARD)
        self.assertEqual(self.error()["code"], "NO_NVIDIA_CARD")
        self.assertEqual(self.w.ev("card"), [])

    def test_memory_short(self):
        self.w.raise_at["memory_short"] = True
        self.assertEqual(self.run_job(), W.EXIT_CARD)
        err = self.error()
        self.assertEqual(err["code"], "LOW_CARD_MEMORY")
        self.assertEqual((err["values"]["free"], err["values"]["need"], err["values"]["need_mb"]),
                         ("2.8", "4.0", "4096"))
        self.assertNotIn("gpuengine.start_card", self.w.names())

    def test_card_refused(self):
        self.w.raise_at["gpuengine.start_card"] = self.w.gpuengine.CardRefused("CARD_FAILED", {"stage": "card"})
        self.assertEqual(self.run_job(), W.EXIT_CARD)
        err = self.error()
        self.assertEqual((err["code"], err["values"]["stage"], err["values"]["name"]),
                         ("CARD_FAILED", "card", "NVIDIA GeForce RTX 0000"))
        self.assertNotIn("ingest.open_input", self.w.names())

    def test_card_refused_under_the_test_switch(self):
        self.w.raise_at["gpuengine.start_card"] = self.w.gpuengine.CardRefused("NO_NVIDIA_CARD", {})
        self.assertEqual(self.run_job(), W.EXIT_CARD)
        self.assertEqual(self.error()["code"], "NO_NVIDIA_CARD")

    def test_cuda_error_in_the_first_read(self):
        self.w.raise_at["first_read_late"] = RuntimeError(
            "CUDA error: no kernel image is available for execution on the device")
        self.assertEqual(self.run_job(), W.EXIT_CARD)
        err = self.error()
        self.assertEqual(err["code"], "CARD_NOT_SUPPORTED")
        self.assertEqual((err["values"]["stage"], err["values"]["which"]), ("firstread", "new"))
        self.assertNotIn("pipeline.run", self.w.names())
        self.assertFalse((self.out / "summary.pdf").exists())
        facts = json.loads((self.out / "checkfacts.json").read_text(encoding="ascii"))
        self.assertEqual((facts["error"]["code"], facts["error"]["stage"], facts["error"]["type"]),
                         ("CARD_NOT_SUPPORTED", "firstread", "RuntimeError"))
        self.assertEqual(facts["gpu"]["card"]["name"], "NVIDIA GeForce RTX 0000")

    def test_cuda_error_deep_in_the_runner(self):
        cause = RuntimeError("CUDA error: an illegal memory access was encountered")
        try:
            raise self.w.pipeline.RunCrashed("RuntimeError in stage 'names'") from cause
        except Exception as e:
            self.w.raise_at["pipeline.run.late"] = e
        self.assertEqual(self.run_job(), W.EXIT_CARD)
        err = self.error()
        self.assertEqual((err["code"], err["values"]["stage"]), ("CARD_FAILED", "names"))

    def test_card_code_without_gpuengine_loaded(self):
        chk = W._Check(W.Modules(gpuinfo=self.w.gpuinfo), W.load_job(self.job()), W.Recorder(None))
        self.assertIsNone(chk.card_code(ValueError("made-up")))
        if "src.verify.runner.app.gpuengine" not in sys.modules:   # never imported just to name the code
            self.assertEqual(chk.card_code(RuntimeError("CUDA error: unspecified launch failure")), "CARD_FAILED")
            self.assertNotIn("src.verify.runner.app.gpuengine", sys.modules)

    def test_card_code_outside_the_six_is_card_failed(self):
        gpuengine = SimpleNamespace(card_error_code=lambda e: "SOMETHING_ELSE")
        chk = W._Check(W.Modules(gpuengine=gpuengine), W.load_job(self.job()), W.Recorder(None))
        self.assertEqual(chk.card_code(RuntimeError("CUDA error: x")), "CARD_FAILED")


# ====================================================================================== cancel (exit 4)
class TestCancel(Base):
    def test_cancel_during_the_first_read(self):
        self.w.cancel_on = lambda e, d: e == "page"
        self.assertEqual(self.run_job(), W.EXIT_CANCELLED)
        self.assertEqual(self.w.ev("cancelled"), [{}])
        self.assertEqual(self.w.ev("error") + self.w.ev("done"), [])
        self.assertNotIn("pipeline.run", self.w.names())
        self.assertIn("card.free", self.w.names())
        self.assertIsNone(self.w.engine.READER_FACTORY)
        self.assertFalse((self.out / "checkfacts.json").exists())
        ends = [d for e, d in self.w.events if e == "stage" and d.get("state") == "end"]
        self.assertEqual([d["key"] for d in ends], ["card", "firstread"])

    def test_cancel_while_waiting_before_the_card(self):
        self.w.cancel_on = lambda e, d: e == "stage" and d.get("key") == "card"
        self.assertEqual(self.run_job(), W.EXIT_CANCELLED)
        self.assertNotIn("gpuengine.start_card", self.w.names())
        self.assertEqual(self.w.events[-1], ("cancelled", {}))

    def test_cancel_in_the_runner_puts_the_factory_back(self):
        self.w.raise_at["pipeline.run.late"] = Cancelled()
        self.assertEqual(self.run_job(), W.EXIT_CANCELLED)
        self.assertIsNone(self.w.engine.READER_FACTORY)
        self.assertEqual(self.w.events[-1], ("cancelled", {}))

    def test_cancel_raised_on_the_cancelled_event_is_ignored(self):
        seen = []

        def angry(event, **data):                 # raises on every event, terminal ones too
            seen.append(event)
            raise Cancelled()
        self.assertEqual(W.run_job(self.job(), angry, mods=self.w.mods()), W.EXIT_CANCELLED)
        self.assertEqual(seen, ["card", "cancelled"])


# ====================================================================================== crashes (exit 1, 5)
class TestCrash(Base):
    def crash(self, cause, stage="names"):
        try:
            raise self.w.pipeline.RunCrashed(f"{type(cause).__name__} in stage '{stage}'") from cause
        except Exception as e:
            self.w.raise_at["pipeline.run.late"] = e

    def test_crash_names_the_stage_not_the_message(self):
        self.crash(ValueError(f"a value read off a page: {NAME} {MARKERS[3]}"))
        W.VERBOSE = True
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = self.run_job()
        self.assertEqual(rc, W.EXIT_CRASH)
        err = self.error()
        self.assertEqual((err["code"], err["values"]), ("CRASH", {"stage": "names"}))
        self.assertEqual(err["text"], "ValueError in stage 'names'")
        log = buf.getvalue()
        assert_no_personal_data(self, log)
        self.assertIn("ValueError", log)
        self.assertIn("test_worker.py", log)                  # the frames are kept, by base name
        facts = (self.out / "checkfacts.json").read_text(encoding="ascii")
        assert_no_personal_data(self, facts)
        self.assertEqual(json.loads(facts)["error"], {"code": "CRASH", "stage": "names", "type": "ValueError"})

    def test_low_memory(self):
        self.crash(MemoryError())
        self.assertEqual(self.run_job(), W.EXIT_CRASH)
        self.assertEqual(self.error()["code"], "LOW_MEMORY")

    def test_disk_full(self):
        self.crash(OSError(28, "No space left on device"))
        self.assertEqual(self.run_job(), W.EXIT_CRASH)
        self.assertEqual(self.error()["code"], "DISK_FULL")

    def test_missing_module_is_a_startup_failure(self):
        self.w.raise_at["gpuengine.light_reader"] = ModuleNotFoundError("No module named 'made_up_glue'")
        self.assertEqual(self.run_job(), W.EXIT_STARTUP)
        self.assertEqual(self.error()["code"], "STARTUP_FAILED")

    def test_crash_in_the_worker_itself(self):
        self.w.raise_at["botimport.load"] = RuntimeError(f"bot code already loaded {NAME}")
        self.assertEqual(self.run_job(), W.EXIT_CRASH)
        err = self.error()
        self.assertEqual((err["code"], err["values"]), ("CRASH", {"stage": "ingest"}))
        self.assertNotIn(NAME, json.dumps(err))

    def test_keyboard_interrupt_is_not_mapped(self):
        self.w.raise_at["pipeline.run.late"] = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_job()
        self.assertIsNone(self.w.engine.READER_FACTORY)


# ====================================================================================== the real hooks and protocol
def _real_hooks_and_protocol():
    try:
        from src.verify.runner import hooks
        from src.verify.runner.app import protocol
    except Exception:
        return None, None
    if not all(hasattr(hooks, n) for n in ("LISTENER", "emit", "Cancelled")):
        return None, None
    return hooks, protocol


class TestWithEntryListener(Base):
    """run_job as entry.py calls it: Task A's real hooks module, Task C's real WorkerListener over a byte stream."""

    def setUp(self):
        super().setUp()
        self.hooks, self.protocol = _real_hooks_and_protocol()
        if self.hooks is None:
            self.skipTest("Task A's hooks or Task C's protocol is not built")
        self.prev = self.hooks.LISTENER

    def tearDown(self):
        if self.hooks is not None:
            self.hooks.LISTENER = self.prev
        super().tearDown()

    def go(self, cancel_first: bool = False):
        buf = io.BytesIO()
        flag = self.protocol.CancelFlag()
        if cancel_first:
            flag.set("made-up cancel")
        listener = self.protocol.WorkerListener(self.protocol.Writer(buf), flag, cancelled_exc=self.hooks.Cancelled,
                                                cancel_on_broken=False)
        rc = W.run_job(self.job(), listener, mods=self.w.mods(hooks=self.hooks))
        events = [self.protocol.decode(x) for x in buf.getvalue().splitlines()]
        return rc, listener, events

    def test_done_through_the_real_listener(self):
        rc, listener, events = self.go()
        self.assertEqual(rc, W.EXIT_OK)
        self.assertEqual(listener.terminal, "done")
        self.assertTrue(all(e is not None and self.protocol.validate(e) == [] for e in events), events)
        self.assertEqual([e["ev"] for e in events][-1], "done")
        self.assertIs(self.hooks.LISTENER, self.prev)

    def test_cancel_through_the_real_listener(self):
        rc, listener, events = self.go(cancel_first=True)
        self.assertEqual(rc, W.EXIT_CANCELLED)
        self.assertEqual(listener.terminal, "cancelled")
        self.assertEqual([e["ev"] for e in events], ["card", "cancelled"])
        self.assertNotIn("gpuengine.start_card", self.w.names())


class TestRealCardModulesWithoutCard(Base):
    """The no-card path (R2.1.4) with Tasks E's and T's real modules under HDC_TEST_NO_CARD=1, which refuse before
    NVML or torch are touched: nothing is read, exit 6."""

    def setUp(self):
        super().setUp()
        if os.environ.get("HDC_TEST_NO_CARD") != "1":
            self.skipTest("needs HDC_TEST_NO_CARD=1")
        try:
            from src.verify.runner.app import cardgate, gpuengine, gpuinfo
        except Exception:
            self.skipTest("Task E's or T's modules are not built")
        self.real = dict(gpuinfo=gpuinfo, cardgate=cardgate, gpuengine=gpuengine)

    def test_real_gpuinfo_finds_no_card(self):
        before = set(sys.modules)
        rc = self.run_job(**self.real)
        self.assertEqual(rc, W.EXIT_CARD)
        self.assertEqual(self.error()["code"], "NO_NVIDIA_CARD")
        self.assertEqual(self.w.ev("card"), [])
        self.assertEqual(self.w.names(), ["safety.prepare"])
        self.assertFalse({m for m in set(sys.modules) - before if m.split(".")[0] in ("torch", "easyocr")})

    def test_real_gpuengine_refuses_before_torch(self):
        before = set(sys.modules)
        rc = self.run_job(cardgate=self.real["cardgate"], gpuengine=self.real["gpuengine"])
        self.assertEqual(rc, W.EXIT_CARD)
        self.assertEqual(self.error()["code"], "NO_NVIDIA_CARD")
        self.assertNotIn("ingest.open_input", self.w.names())
        self.assertFalse({m for m in set(sys.modules) - before if m.split(".")[0] in ("torch", "easyocr")})

    def test_real_card_error_code_agrees(self):
        G = self.real["gpuengine"]
        for msg, code in (("CUDA error: no kernel image is available for execution on the device",
                           "CARD_NOT_SUPPORTED"),
                          ("CUDA driver version is insufficient for CUDA runtime version", "DRIVER_TOO_OLD"),
                          ("CUDA error: an illegal memory access was encountered", "CARD_FAILED")):
            self.assertTrue(W.cuda_marked(RuntimeError(msg)))
            self.assertEqual(G.card_error_code(RuntimeError(msg)), code)
        for e in (ValueError("made-up"), MemoryError(), RuntimeError("DefaultCPUAllocator: not enough memory")):
            self.assertFalse(W.cuda_marked(e))
            self.assertIsNone(G.card_error_code(e))


# ====================================================================================== the pieces
class TestPrecheck(Base):
    def test_files(self):
        self.assertEqual(W.precheck(self.root / "missing.pdf", REAL_INGEST).code, "NOT_PDF_OR_FOLDER")
        txt = self.root / "notes.txt"
        txt.write_text("made-up", encoding="utf-8")
        self.assertEqual(W.precheck(txt, REAL_INGEST).code, "NOT_PDF_OR_FOLDER")
        junk = self.root / "junk.pdf"
        junk.write_bytes(b"not a pdf at all")
        r = W.precheck(junk, REAL_INGEST)
        self.assertEqual((r.code, r.values, r.exit_code), ("UNREADABLE_PDF", {"file": "The bundle"}, W.EXIT_REFUSED))
        self.assertIsNone(W.precheck(self.pdf, REAL_INGEST))
        self.assertEqual(W.precheck(Path("C:\\" + "x" * 300 + ".pdf"), REAL_INGEST).code, "PATH_TOO_LONG")

    def test_no_pages(self):
        class Doc:
            needs_pass, is_pdf = False, True

            def __len__(self):
                return 0

            def close(self):
                pass
        r = W.precheck(self.pdf, REAL_INGEST, opener=lambda p: Doc())
        self.assertEqual((r.code, r.values), ("NO_PAGES", {"file": "The bundle"}))

    def test_folders(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(W.precheck(empty, REAL_INGEST).code, "EMPTY_FOLDER")
        nested = self.root / "nested"
        (nested / "inner").mkdir(parents=True)
        self.assertEqual(W.precheck(nested, REAL_INGEST).code, "FOLDER_OF_FOLDERS")
        other = self.root / "other"
        other.mkdir()
        (other / "notes.docx").write_bytes(b"made-up")
        self.assertEqual(W.precheck(other, REAL_INGEST).code, "EMPTY_FOLDER")
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "photo.jpg").write_bytes(b"not a picture")
        r = W.precheck(bad, REAL_INGEST)
        self.assertEqual((r.code, r.values), ("UNREADABLE_IMAGE",
                                              {"file": "File 1 of 1 in the folder (sorted by name)"}))
        made_up_pdf(bad / "passport.pdf")
        self.assertIsNone(W.precheck(bad, REAL_INGEST))        # one readable file: the runner checks the folder
        self.assertIsNone(W.precheck(bad, None))               # the same without ingest's lister


class TestClassify(unittest.TestCase):
    def test_pinned_table(self):
        cases = {
            "the bundle is password-protected": ("ENCRYPTED", {"file": "The bundle"}),
            "file 2 of 5 in the folder (sorted by name) has no pages":
                ("NO_PAGES", {"file": "File 2 of 5 in the folder (sorted by name)"}),
            "the bundle could not be opened as a PDF: FileDataError": ("UNREADABLE_PDF", {"file": "The bundle"}),
            "the bundle is not a PDF": ("UNREADABLE_PDF", {"file": "The bundle"}),
            "file 1 of 1 in the folder (sorted by name) could not be opened as an image: UnidentifiedImageError":
                ("UNREADABLE_IMAGE", {"file": "File 1 of 1 in the folder (sorted by name)"}),
            "the folder holds no .pdf, .jpg, .jpeg or .png file": ("EMPTY_FOLDER", {}),
            "the input must be one bundle .pdf or one folder": ("NOT_PDF_OR_FOLDER", {}),
            "the page texts were read from another file than the bundle (sha256 differs)": ("INPUT_CHANGED", {}),
            "the page texts give the bundle 3 page(s), the input has 4": ("INPUT_CHANGED", {}),
            "--out must not lie inside the input": ("REPORT_INSIDE_INPUT", {}),
            "the input must not lie inside --out": ("INPUT_INSIDE_REPORT", {}),
            "--out could not be created (PermissionError)": ("REPORT_NOT_WRITABLE", {}),
            "the input folder holds a junction or a link; give a folder of plain files": ("NOT_PDF_OR_FOLDER", {}),
        }
        for msg, (code, values) in cases.items():
            self.assertEqual(W.classify_refusal(Exception(msg), "input"), (code, values), msg)

    def test_app_messages_of_the_real_safety(self):
        from src.verify.runner import safety
        want = {"APP_OUT_RELATIVE": "REPORT_NOT_WRITABLE", "APP_OUT_DEVICE": "REPORT_NOT_WRITABLE",
                "APP_OUT_ROOT": "REPORT_NOT_WRITABLE", "APP_OUT_PROTECTED": "REPORT_NOT_WRITABLE",
                "APP_OUT_DATA": "REPORT_NOT_WRITABLE", "APP_OUT_INSIDE_INPUT": "REPORT_INSIDE_INPUT",
                "APP_INPUT_INSIDE_OUT": "INPUT_INSIDE_REPORT", "APP_INPUT_RELATIVE": "NOT_PDF_OR_FOLDER"}
        have = {k: getattr(safety, k) for k in want if isinstance(getattr(safety, k, None), str)}
        if not have:
            self.skipTest("Task A's APP messages are not built")
        for k, msg in have.items():
            self.assertEqual(W.classify_refusal(Exception(msg), "safety")[0], want[k], k)

    def test_unknown_messages(self):
        self.assertEqual(W.classify_refusal(Exception("made-up"), "safety"), ("REPORT_NOT_WRITABLE", {}))
        self.assertEqual(W.classify_refusal(Exception("made-up"), "input"), ("NOT_PDF_OR_FOLDER", {}))


class TestHelpers(unittest.TestCase):
    def test_cuda_marked(self):
        self.assertTrue(W.cuda_marked(RuntimeError("CUDA error: an illegal memory access was encountered")))
        self.assertTrue(W.cuda_marked(RuntimeError("cuDNN error: CUDNN_STATUS_EXECUTION_FAILED")))
        self.assertFalse(W.cuda_marked(ValueError("made-up")))
        self.assertFalse(W.cuda_marked(MemoryError()))
        AcceleratorError = type("AcceleratorError", (RuntimeError,), {})
        self.assertTrue(W.cuda_marked(AcceleratorError("x")))
        try:
            try:
                raise RuntimeError("CUBLAS_STATUS_ALLOC_FAILED")
            except RuntimeError as inner:
                raise ValueError("made-up") from inner
        except ValueError as outer:
            self.assertTrue(W.cuda_marked(outer))

    def test_redact(self):
        self.assertEqual(W.redact(f"refused: C:\\Users\\{NAME}\\x.pdf"), "refused: <path>")
        self.assertEqual(W.redact("\\\\server\\share\\y"), "<path>")

    def test_frames_only_hold_no_message(self):
        try:
            raise ValueError(f"made-up {NAME}")
        except ValueError as e:
            s = W.frames_only(e)
        self.assertIn("builtins.ValueError", s)
        self.assertIn("test_worker.py", s)
        self.assertNotIn(NAME, s)
        self.assertNotIn("\\", s)

    def test_recorder_conventions(self):
        got = []
        rec = W.Recorder(lambda *a, **k: got.append((a, k)))
        rec("budget", where="birth.qr", page=7)
        rec("budget", {"where": "tin.e", "page": 2})
        rec({"ev": "budget", "where": "birth.code128", "page": 3})
        rec("stage", key="split", state="start")
        rec("stage", key="split", state="end", seconds=1.0)
        self.assertEqual(rec.budget, [{"where": "birth.qr", "page": 7}, {"where": "tin.e", "page": 2},
                                      {"where": "birth.code128", "page": 3}])
        self.assertEqual(rec.stage, "split")
        self.assertEqual(len(got), 5)                          # every call forwarded unchanged
        self.assertEqual(got[0], (("budget",), {"where": "birth.qr", "page": 7}))
        W.Recorder(None)("budget", where="x", page=1)          # no listener: nothing to forward

    def test_recorder_lets_cancel_through(self):
        def listener(*a, **k):
            raise Cancelled()
        with self.assertRaises(Cancelled):
            W.Recorder(listener)("page", done=1, of=2)

    def test_load_job(self):
        j = W.load_job({"input": "C:\\in.pdf", "program": "EAP", "out": "C:\\out", "today": None,
                        "gpu": {"lock_paths": "C:\\x.lock", "need_free_mb": "4096"}})
        self.assertEqual((j.gpu.lock_paths, j.gpu.need_free_mb, j.gpu.cap_gb, j.cache_root),
                         (("C:\\x.lock",), 4096, 3.0, None))
        j2 = W.load_job(SimpleNamespace(input="C:\\in.pdf", program="EAP", out="C:\\out", gpu=None))
        self.assertEqual(j2.gpu.lock_paths, ())
        with self.assertRaises(W.JobError):
            W.load_job({"input": "C:\\in.pdf", "program": "EAP"})
        with self.assertRaises(W.JobError):
            W.load_job("C:\\no such folder\\job.json")

    def test_card_peaks_and_facts(self):
        card = SimpleNamespace(peaks=lambda: (1.5, 2.0), facts=lambda: {"torch": "t", "cuda": "c", "seconds": 3})
        self.assertEqual(W.card_peaks(card), {"peak_alloc_gb": 1.5, "peak_reserved_gb": 2.0})
        self.assertEqual(W.card_facts(card, "9.19.0")["cudnn"], "9.19.0")
        self.assertEqual(W.card_facts(card)["seconds"], 3)
        self.assertEqual(W.card_peaks(SimpleNamespace()), {})

    def test_call_opt(self):
        def f(a, *, x=None):
            return (a, x)

        def g(a, **kw):
            return (a, kw)
        self.assertEqual(W.call_opt(f, 1, x=2, y=3), (1, 2))
        self.assertEqual(W.call_opt(g, 1, x=2), (1, {"x": 2}))

    def test_process_memory(self):
        m = W.process_memory()
        self.assertEqual(set(m), {"peak_working_set_mb", "peak_commit_mb"})
        if os.name == "nt":
            self.assertGreater(m["peak_working_set_mb"], 0)


if __name__ == "__main__":
    unittest.main()
