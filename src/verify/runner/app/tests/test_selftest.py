"""Unit tests of app/selftest.py and packaging/make_selftest_pages.py (Task D).

The other tasks' modules are faked through ``selftest._OVERRIDES`` (gpuinfo, cardgate, gpuengine, places, botimport,
ingest, safety, version, spawn, userdirs, and the runtime part's module list). No card, no torch, no PyInstaller,
no real case: the pages are made up (make_selftest_pages.py, written under private-runner\\selftest\\app\\D) and the
user and PC names in the scrub checks are made up too. Light real parts (core, pdf, qr, excel) run with the runner
venv's own numpy, PyMuPDF, OpenCV, pyzbar and openpyxl; a few tiny Python children test the process runner.

    C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\.venv-runner\\Scripts\\python.exe -I -B -m unittest ^
        src.verify.runner.app.tests.test_selftest
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("HDC_TEST_NO_CARD", "1")

from src.verify.runner.app import selftest as T  # noqa: E402
from src.verify.runner.app import sysinfo  # noqa: E402

WORKTREE = Path(__file__).resolve().parents[5]
BASE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\D")
FAKE_ENV = {"USERNAME": "Xqz Yvw", "USERPROFILE": r"C:\Users\Xqz Yvw", "COMPUTERNAME": "MADEUP-PC7"}
CARD = "NVIDIA GeForce RTX 0000"
WHEN = dt.datetime(2026, 10, 29, 14, 12)
_STATE = {}


def pages_module():
    if "pages" not in _STATE:
        spec = importlib.util.spec_from_file_location("make_selftest_pages_under_test",
                                                      WORKTREE / "packaging" / "make_selftest_pages.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _STATE["pages"] = mod
    return _STATE["pages"]


def setUpModule():
    BASE.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="bundle-", dir=BASE))
    _STATE["root"] = root
    bundle = root / "app" / "_internal"
    pages_module().make(bundle / "selftest")
    _STATE["bundle"] = bundle


def tearDownModule():
    shutil.rmtree(_STATE.get("root", BASE / "no-such"), ignore_errors=True)


def expected_text(page):
    return " ".join(pages_module().PAGE_LINES[page])


# ================================================================================================= fakes
class FakeInfo(SimpleNamespace):
    pass


def fake_info():
    return FakeInfo(nvml=True, count=1, index=0, name=CARD, driver="000.00", cuda_driver=12090, cc=(12, 0),
                    total_mb=8151, free_mb=6120)


class FakeGpuinfo:
    NEED_FREE_MB = 4096
    NO_CARD_ENV = "HDC_TEST_NO_CARD"
    NVML_DLL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll")

    def __init__(self, problems=()):
        self._problems = list(problems)
        self.env_applied = 0

    def probe(self):
        return fake_info()

    def problems(self, info, arch):
        self.arch = arch
        return list(self._problems)

    def free_mb(self, index=0):
        return 6000

    def gb_text(self, mb):
        return "?" if mb is None else f"{mb / 1024.0:.1f}"

    def apply_card_env(self, environ, index):
        self.env_applied += 1
        return []

    def selftest_line(self, info, arch=None):
        return f"Graphics card: {CARD} | made up"


class FakeCardMemoryShort(Exception):
    def __init__(self, free_mb, need_mb, waited_s=0.0):
        super().__init__("short")
        self.free_mb, self.need_mb, self.waited_s = free_mb, need_mb, waited_s


class FakeGate:
    def __init__(self, lock_paths, emit, free_mb=None, short=False):
        self.lock_paths = list(lock_paths)
        self.emit = emit
        self.turns = []
        self.on_pause = None
        self.short = short
        self.memory = None

    def wait_turn(self, where):
        self.turns.append(where)

    def wait_memory(self, need_mb, limit_s=None):
        self.memory = (need_mb, limit_s)
        if self.short:
            raise FakeCardMemoryShort(2900, need_mb, limit_s or 0)
        return 6000

    def stats(self):
        return {"waits": 1, "waited_s": 42.0}


class FakeCardgate:
    CardMemoryShort = FakeCardMemoryShort
    BOT = "auto_verify.lock"

    def __init__(self, short=False, default=None):
        self.short = short
        self.default = default
        self.gates = []

    def CardGate(self, lock_paths, emit, *, free_mb=None):
        g = FakeGate(lock_paths, emit, free_mb, self.short)
        self.gates.append(g)
        return g

    def lock_kind(self, path):
        return "bot" if str(path).endswith(self.BOT) else "other"

    def lock_active(self, path, now):
        return os.path.exists(path)

    def default_lock_setting(self):
        return self.default


class FakeCardRefused(Exception):
    def __init__(self, code, values=None):
        super().__init__(code)
        self.code = code
        self.values = dict(values or {})


class FakeSession:
    def __init__(self, shape, dtype="float32"):
        self.device = "cuda"
        self.shape = shape
        self.dtype = dtype
        self.feeds = []

    def run(self, names, feeds):
        import numpy as np
        self.feeds.append(feeds["image"].shape)
        return [np.zeros(self.shape, dtype=self.dtype)]


class FakeLightReader:
    def __init__(self, text, shape=(1, 64, 97), dtype="float32", cpu_calls=0):
        self.text = text
        self.det = FakeSession((1, 10, 10, 2))
        self.rec = FakeSession(shape, dtype)
        self.cpu_calls = cpu_calls
        self.paths = []

    def readtext(self, image, detail=1):
        self.paths.append(image)
        return self.text.split()

    def stats(self):
        return {"cpu_calls": self.cpu_calls, "oom_retries": 0}


class FakeCard:
    def __init__(self, reserved=2.8):
        self.torch = SimpleNamespace(cuda=SimpleNamespace(current_device=lambda: 0))
        self.reserved = reserved

    def facts(self):
        return {"torch": "2.11.0+cu128", "cuda": "12.8", "import_s": 6.1, "cuda_s": 2.3}

    def peaks(self):
        return (2.41, self.reserved)


class FakeEngine:
    CardRefused = FakeCardRefused

    def __init__(self, texts=None, sideways2=True, passes=(1, 2), reserved=2.8, cpu_calls=0, rec_shape=(1, 64, 97),
                 rec_dtype="float32", strict=True, start_error=None, read_error=None, light_text=None):
        self.texts = texts or {1: expected_text(1), 2: expected_text(2), "photo": expected_text(1)}
        self.sideways2, self.passes, self.reserved = sideways2, passes, reserved
        self.cpu_calls, self.rec_shape, self.rec_dtype, self.strict = cpu_calls, rec_shape, rec_dtype, strict
        self.start_error, self.read_error = start_error, read_error
        self.light_text = expected_text(1) if light_text is None else light_text
        self.calls = []

    def start_card(self, info, gate):
        self.calls.append("start_card")
        if self.start_error:
            raise self.start_error
        gate.on_pause = lambda: 0
        return FakeCard(self.reserved)

    def first_read(self, card, bot, src, gate, store, emit=None, *, ascii_dir=None, **kw):
        self.calls.append("first_read")
        self.first_read_args = {"src": src, "store": Path(store), "ascii_dir": ascii_dir, "bot": bot}
        if self.read_error:
            raise self.read_error
        files = []
        done = 0
        for f in src.files:
            pages = []
            if f.name == T.JPG_NAME:
                gate.wait_turn("page")
                gate.wait_turn("read")
                pages.append({"page": 1, "text": self.texts["photo"], "kind": "image", "sideways": False,
                              "seconds": 0.8})
            else:
                for p in (1, 2):
                    gate.wait_turn("page")
                    for _ in range(self.passes[p - 1]):
                        gate.wait_turn("read")
                    pages.append({"page": p, "text": self.texts[p], "kind": "ocr",
                                  "sideways": p == 2 and self.sideways2, "seconds": 0.9 if p == 1 else 3.1})
            done += len(pages)
            if emit is not None:
                emit("page", done=done, of=3, seconds=1.0, where="card")
            files.append({"name": f.name, "sha256": "0" * 64, "size": 1, "n_pages": f.n_pages, "pages": pages})
        Path(store).parent.mkdir(parents=True, exist_ok=True)
        Path(store).write_text(json.dumps({"schema": "runner-page-texts/1", "files": files}), encoding="utf-8")
        return {"pages": {"read": 3}, "model_loaded": True, "model_load_s": 1.2, "skipped": 0, "downscaled": 0,
                "oom_retries": 0, "peak_alloc_gb": 2.0, "peak_reserved_gb": 2.5}

    def light_reader(self, card, gate, glue_data, model_dir, *, ascii_dir=None, **kw):
        self.calls.append("light_reader")
        self.light_args = {"glue": glue_data, "models": model_dir, "ascii_dir": ascii_dir}
        self.reader = FakeLightReader(self.light_text, self.rec_shape, self.rec_dtype, self.cpu_calls)
        return self.reader

    def torch_flags(self, torch):
        return {"cudnn_deterministic": self.strict, "cudnn_benchmark": False, "matmul_allow_tf32": False,
                "cudnn_allow_tf32": not self.strict}

    def card_error_code(self, e):
        if isinstance(e, FakeCardRefused):
            return e.code
        if "cuda" in str(e).lower():
            return "CARD_FAILED"
        return None


def fake_places(tmp):
    cur = SimpleNamespace(glue_data=tmp / "glue_data.json", models_dir=tmp / "model", cache_root=tmp / "data" / "cache",
                          temp_dir=tmp / "data" / "tmp")
    return SimpleNamespace(current=lambda: cur, app_data_dir=lambda: tmp / "data", bundle_dir=lambda: _STATE["bundle"],
                           app_install_dir=lambda: _STATE["bundle"].parent)


def fake_ingest():
    def open_input(path):
        path = Path(path)
        assert (path / T.PDF_NAME).is_file() and (path / T.JPG_NAME).is_file()
        files = sorted([SimpleNamespace(name=T.JPG_NAME, n_pages=1, path=path / T.JPG_NAME),
                        SimpleNamespace(name=T.PDF_NAME, n_pages=2, path=path / T.PDF_NAME)],
                       key=lambda f: f.name.casefold())
        return SimpleNamespace(mode="folder", root=path, files=files, case_id="F-0000000000000000")
    return SimpleNamespace(open_input=open_input)


FAKE_VERSION = SimpleNamespace(APP_VERSION="1.0.0", BUILD_ID="20261029.1-abc1234", BASE_RULES_VERSION="2026-10-21.3",
                               RUNNER_RULES_VERSION="2026-10-28.r2.6", RUNNER_VERSION="0.2.7",
                               ARCH_FLAGS=("sm_75", "sm_120"), CUDNN="9.19.0")


class Base(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="t-", dir=BASE))
        T._OVERRIDES.clear()
        T._OVERRIDES["version"] = FAKE_VERSION
        self.env = mock.patch.dict(os.environ, FAKE_ENV)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        T._OVERRIDES.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ctx(self, part, locks=(), bundle="default"):
        root = self.tmp / "data" / "selftest" / T.SCRATCH_NAME
        scratch = root / part
        scratch.mkdir(parents=True, exist_ok=True)
        return T.Context(part=part, root=root, scratch=scratch, data_dir=self.tmp / "data",
                         bundle=_STATE["bundle"] if bundle == "default" else bundle,
                         app_dir=_STATE["bundle"].parent, lock_paths=list(locks), events=self.tmp / "ev.jsonl",
                         cancel_file=self.tmp / "cancel")


# ============================================================================================ small pieces
class TestMeasures(Base):
    def test_cer_and_levenshtein(self):
        self.assertEqual(T.levenshtein("kitten", "sitting"), 3)
        self.assertEqual(T.cer("Roll No. 000123", "ROLL NO 000123"), 0.0)
        self.assertAlmostEqual(T.cer("ROLL NO 000124", "ROLL NO 000123"), 1 / 12)
        self.assertIsNone(T.cer(None, "x"))
        self.assertEqual(T.cer("", ""), 0.0)

    def test_read_code(self):
        c = T.read_code(["a  b", "c"])
        self.assertRegex(c, r"^[0-9A-F]{4}-[0-9A-F]{4}$")
        self.assertEqual(c, T.read_code(["a b", " c "]))
        self.assertNotEqual(c, T.read_code(["a b", "C"]))

    def test_exit_text(self):
        self.assertEqual(T.exit_text(0xC000001D), "0xC000001D (illegal instruction)")
        self.assertEqual(T.exit_text(-0x3FFFFFE3), "0xC000001D (illegal instruction)")
        self.assertEqual(T.exit_code_class(0xC0000135), "MISSING_PART")
        self.assertEqual(T.exit_code_class(0xC0000017), "LOW_MEMORY")
        self.assertEqual(T.exit_code_class(0xC0000005), "CRASH")
        self.assertEqual(T.exit_text(3), "exit code 3")

    def test_part_result_json(self):
        r = T.PartResult("qr", T.OK, "fine", facts={"x": (1, 2)})
        back = T.PartResult.from_json(json.loads(json.dumps(r.to_json())))
        self.assertEqual((back.part, back.status, back.facts), ("qr", T.OK, {"x": [1, 2]}))
        self.assertIsNone(T.PartResult.from_json({"part": "qr"}))
        self.assertIsNone(T.PartResult.from_json([]))

    def test_counting_gate(self):
        inner = FakeGate([], None)
        g = T.CountingGate(inner)
        for w in ("start", "read", "page", "read", "page", "read", "read", "oom", "read", "page", "read"):
            g.wait_turn(w)
        self.assertEqual(g.passes(), [1, 3, 1])
        g.on_pause = "set"
        self.assertEqual(inner.on_pause, "set")
        self.assertEqual(inner.turns[0], "start")

    def test_lock_setting_and_report_dir(self):
        cg = FakeCardgate(default=str(self.tmp / "x" / "auto_verify.lock"))
        T._OVERRIDES.update(cardgate=cg, userdirs=SimpleNamespace(default_report_root=lambda: self.tmp / "docs"))
        data = self.tmp / "data"
        data.mkdir()
        self.assertEqual(T.lock_setting(data), [cg.default])
        self.assertEqual(T.default_report_dir(data), self.tmp / "docs")
        (data / "settings.json").write_text(json.dumps({"gpu_lock_path": None, "report_root": str(self.tmp / "r")}))
        self.assertEqual(T.lock_setting(data), [])
        self.assertEqual(T.default_report_dir(data), self.tmp / "r")
        (data / "settings.json").write_text(json.dumps({"gpu_lock_path": "Q:\\other.lock"}))
        self.assertEqual(T.lock_setting(data), ["Q:\\other.lock"])
        (data / "settings.json").write_text("{damaged")
        self.assertEqual(T.lock_setting(data), [cg.default])

    def test_module_import_is_light(self):
        code = ("import sys; import src.verify.runner.app.selftest; "
                "print(sorted(m for m in ('numpy', 'torch', 'cv2', 'pymupdf', 'easyocr') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=60,
                             cwd=str(WORKTREE), env=dict(os.environ, HDC_TEST_NO_CARD="1"))
        self.assertEqual(out.stdout.strip(), "[]", out.stderr)


# ================================================================================================ the pages
class TestMakeSelftestPages(Base):
    def test_files(self):
        import pymupdf
        pm = pages_module()
        sel = _STATE["bundle"] / "selftest"
        exp = json.loads((sel / T.EXPECTED_NAME).read_text(encoding="ascii"))
        self.assertEqual(exp["schema"], "hdc-selftest-expected/1")
        self.assertEqual(exp["pages"], {str(k): v for k, v in pm.PAGE_LINES.items()})
        self.assertEqual(exp["sideways"], {"1": False, "2": True})
        jpg = (sel / T.JPG_NAME).read_bytes()
        with pymupdf.open(str(sel / T.PDF_NAME)) as doc:
            self.assertEqual(len(doc), 2)
            for i, page in enumerate(doc, 1):
                self.assertEqual(page.get_text().strip(), "")           # image only: read by OCR
                self.assertEqual(len(page.get_images()), 1)
                pix = page.get_pixmap(dpi=200)
                self.assertEqual([pix.width, pix.height], exp["page_px"][str(i)])
            first = doc.extract_image(doc[0].get_images()[0][0])
        self.assertEqual(first["ext"], "jpeg")
        self.assertEqual(first["image"], jpg)                              # the photo is page 1, byte for byte
        w, h = exp["page_px"]["1"]
        self.assertTrue(h > w and exp["page_px"]["2"] == [h, w])           # page 2 is page-1-sized, turned
        for name in (T.PDF_NAME, T.JPG_NAME):
            data = (sel / name).read_bytes()
            self.assertEqual(exp["files"][name], {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()})

    def test_text_rules(self):
        pm = pages_module()
        for page, lines in pm.PAGE_LINES.items():
            joined = " ".join(lines)
            self.assertGreaterEqual(len(joined), pm.MIN_PAGE_CHARS)
            self.assertIsNone(re.search(r"\d{7,}", joined))
        with self.assertRaises(ValueError):
            pm.check_text({1: ["Roll 1234567"] * 40})
        with self.assertRaises(ValueError):
            pm.check_text({1: ["too short"]})

    def test_same_output_twice(self):
        pm = pages_module()
        a = pm.make(self.tmp / "a")
        b = pm.make(self.tmp / "b")
        self.assertEqual(a[T.JPG_NAME].read_bytes(), b[T.JPG_NAME].read_bytes())
        self.assertEqual(a[T.PDF_NAME].read_bytes(), b[T.PDF_NAME].read_bytes())

    def test_cli(self):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = pages_module().main(["--out", str(self.tmp / "cli")])
        self.assertEqual(rc, 0)
        self.assertIn("selftest_pages.pdf:", buf.getvalue())
        self.assertEqual(sorted(p.name for p in (self.tmp / "cli").iterdir()),
                         sorted([T.EXPECTED_NAME, T.JPG_NAME, T.PDF_NAME]))


# =================================================================================================== card
class TestCardPart(Base):
    def setUp(self):
        super().setUp()
        self.gi, self.cg, self.ge = FakeGpuinfo(), FakeCardgate(), FakeEngine()
        T._OVERRIDES.update(gpuinfo=self.gi, cardgate=self.cg, gpuengine=self.ge, places=fake_places(self.tmp),
                            ingest=fake_ingest(),
                            botimport=SimpleNamespace(load=lambda root: SimpleNamespace(dv=SimpleNamespace(),
                                                                                        cache_root=root)),
                            loaded_modules=lambda: [r"X:\app\_internal\torch\lib\cudnn64_9.dll"],
                            file_version=lambda p: "9.19.0.70")

    def run_card(self, locks=()):
        return T.execute_part("card", self.ctx("card", locks))

    def test_ok(self):
        lock = str(self.tmp / "auto_verify.lock")
        res = self.run_card([lock])
        self.assertEqual(res.status, T.OK, res.detail)
        self.assertRegex(res.detail, r"^reader on cuda:0; CER 0\.0% / 0\.0% / 0\.0% / 0\.0%; READ CODE "
                                     r"[0-9A-F]{4}-[0-9A-F]{4} \(no build PC code in this build\)$")
        f = res.facts
        self.assertEqual(f["passes"], [1, 2, 1])
        self.assertEqual(f["sideways"], [False, True])
        self.assertEqual((f["torch"], f["cuda"], f["cudnn"], f["device"]), ("2.11.0+cu128", "12.8", "9.19.0", "cuda:0"))
        self.assertEqual((f["seconds"]["page1"], f["seconds"]["page2"], f["seconds"]["photo"]), (0.9, 3.1, 0.8))
        self.assertAlmostEqual(f["seconds"]["start"], 9.6, places=1)
        self.assertEqual((f["peak_reserved_gb"], f["cpu_calls"], f["strict"]), (2.8, 0, True))
        self.assertEqual(f["rec_shape"], [1, 64, 97])
        self.assertEqual((f["waits"], f["waited_s"]), (1, 42.0))
        self.assertEqual(f["read_code"], T.read_code([expected_text(1), expected_text(2), expected_text(1),
                                                      expected_text(1)]))
        gate = self.cg.gates[0]
        self.assertEqual(gate.lock_paths, [lock])
        self.assertEqual(gate.memory, (4096, T.MEMORY_WAIT_S))
        self.assertIsNotNone(gate.on_pause)                    # start_card set it through the counting gate
        self.assertEqual(self.ge.calls, ["start_card", "first_read", "light_reader"])
        args = self.ge.first_read_args
        self.assertEqual(args["ascii_dir"], str(self.tmp / "data" / "tmp" / T.ASCII_DIR))
        self.assertIn(T.SCRATCH_NAME, str(args["src"].root))   # read from the non-ASCII scratch folder
        self.assertEqual(self.ge.light_args["ascii_dir"], args["ascii_dir"])
        self.assertEqual(self.ge.light_args["glue"], self.tmp / "glue_data.json")
        photo = self.ge.reader.paths[0]
        self.assertTrue(photo.endswith(T.JPG_NAME) and T.SCRATCH_NAME in photo)
        self.assertEqual(self.ge.reader.rec.feeds, [T.REC_PROBE_SHAPE])
        events = [json.loads(x) for x in (self.tmp / "ev.jsonl").read_text(encoding="ascii").splitlines()]
        self.assertEqual(events[0]["ev"], "page")
        self.assertNotIn("text", json.dumps(f))

    def test_build_pc_same_card(self):
        code = T.read_code([expected_text(1), expected_text(2), expected_text(1), expected_text(1)])
        br = _STATE["bundle"] / "selftest" / T.BUILD_READ_NAME
        try:
            br.write_text(json.dumps({"read_code": code, "card": CARD}), encoding="ascii")
            res = self.run_card()
            self.assertTrue(res.detail.endswith(f"READ CODE {code} (build PC, same card model: {code})"))
            self.assertEqual(res.warnings, [])
            br.write_text(json.dumps({"read_code": "0000-0000", "card": CARD}), encoding="ascii")
            res = self.run_card()
            self.assertEqual(res.status, T.OK)
            self.assertEqual(len(res.warnings), 1)
            br.write_text(json.dumps({"read_code": "0000-0000", "card": "NVIDIA GeForce RTX 1111"}), encoding="ascii")
            res = self.run_card()
            self.assertIn("(build PC, NVIDIA GeForce RTX 1111: 0000-0000; another card model may differ)", res.detail)
        finally:
            br.unlink(missing_ok=True)

    def test_fail_conditions(self):
        cases = [
            (dict(sideways2=False), "page 2 not reported sideways"),
            (dict(texts={1: expected_text(1), 2: expected_text(2), "photo": "garbled words only"}), "photo CER"),
            (dict(light_text="nothing like page one"), "light reader page 1 CER"),
            (dict(reserved=3.2), "peak reserved 3.20 GB is over the 3.0 GB cap"),
            (dict(cpu_calls=1), "1 call(s) ran on the processor"),
            (dict(rec_shape=(1, 10, 96)), "not [1, T, 97] float32"),
            (dict(rec_dtype="float16"), "not [1, T, 97] float32"),
            (dict(strict=False), "strict float32 flags did not read back as set"),
        ]
        for kw, text in cases:
            with self.subTest(text=text):
                T._OVERRIDES["gpuengine"] = FakeEngine(**kw)
                res = self.run_card()
                self.assertEqual(res.status, T.FAIL)
                self.assertIn(text, res.detail)
                self.assertIn("READ CODE", res.detail)

    def test_problem_before_any_card_work(self):
        self.gi._problems = [SimpleNamespace(code="DRIVER_TOO_OLD", values={"driver": "000.00"})]
        res = self.run_card()
        self.assertEqual((res.status, res.code), (T.FAIL, "DRIVER_TOO_OLD"))
        self.assertIn("driver 000.00 is too old", res.detail)
        self.assertEqual(self.ge.calls, [])
        self.assertEqual(self.gi.arch, ["sm_75", "sm_120"])

    def test_card_refused_and_cuda_error(self):
        T._OVERRIDES["gpuengine"] = FakeEngine(start_error=FakeCardRefused("CARD_NOT_SUPPORTED",
                                                                           {"name": CARD, "which": "new"}))
        res = self.run_card()
        self.assertEqual(res.code, "CARD_NOT_SUPPORTED")
        self.assertIn("newer than this build", res.detail)
        self.assertIn("step 'start'", res.detail)
        err = RuntimeError("CUDA error: an illegal memory access at C:\\Users\\Xqz Yvw\\AppData\\x.py")
        T._OVERRIDES["gpuengine"] = FakeEngine(read_error=err)
        res = self.run_card()
        self.assertEqual(res.code, "CARD_FAILED")
        self.assertIn("step 'firstread'", res.detail)
        self.assertNotIn("Xqz", res.detail)
        self.assertIn("<path>", res.detail)
        T._OVERRIDES["gpuengine"] = FakeEngine(read_error=ValueError("not a card thing"))
        res = self.run_card()
        self.assertEqual((res.status, res.code), (T.FAIL, ""))
        self.assertIn("ERROR (step 'firstread'): ValueError", res.detail)

    def test_low_card_memory(self):
        T._OVERRIDES["cardgate"] = FakeCardgate(short=True)
        res = self.run_card()
        self.assertEqual(res.code, "LOW_CARD_MEMORY")
        self.assertIn("only 2.8 GB", res.detail)
        self.assertEqual(self.ge.calls, [])

    def test_cancel_file(self):
        ctx = self.ctx("card")
        ctx.cancel_file.write_text("cancel")
        res = T.execute_part("card", ctx)
        self.assertEqual(res.detail, "cancelled")

    def test_missing_selftest_files(self):
        res = T.execute_part("card", self.ctx("card", bundle=self.tmp / "no-bundle"))
        self.assertEqual((res.status, res.code), (T.FAIL, "MISSING_PART"))
        self.assertEqual(self.ge.calls, [])

    def test_no_card_switch_without_a_fake_engine(self):
        del T._OVERRIDES["gpuengine"]
        res = self.run_card()
        self.assertEqual(res.code, "CARD_FAILED")
        self.assertIn("off for this test run", res.detail)


# ================================================================================================ runtime
class TestRuntimePart(Base):
    def setUp(self):
        super().setUp()
        self.app = _STATE["bundle"].parent
        for _stem, rel in T.VCRT:
            p = _STATE["bundle"].joinpath(*rel.split("/"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"")
        self.windir = os.environ.get("SystemRoot") or r"C:\Windows"
        self.gi = FakeGpuinfo()
        self.loaded = []
        T._OVERRIDES.update(gpuinfo=self.gi, load_dll=lambda p: self.loaded.append(p),
                            import_torch=lambda: SimpleNamespace(__version__="2.11.0+cu128"),
                            file_version=self.version)

    def version(self, p):
        base = os.path.basename(p).lower()
        if base.startswith(("msvc", "vcruntime")):
            return "14.50.35719.0"
        return {"cudnn64_9.dll": "9.19.0.70", "nvml.dll": "32.0.15.7000"}.get(base, "1.0.0.0")

    def modules(self, extra=(), nvml=True):
        b = _STATE["bundle"]
        mods = [str(b.joinpath(*rel.split("/"))) for _s, rel in T.VCRT]
        mods += [str(b / "torch" / "lib" / n) for n in T.CUDA_DLLS]
        mods += [os.path.join(self.windir, "System32", "kernel32.dll")]
        if nvml:
            mods.append(os.path.join(self.windir, "System32", "nvml.dll"))
        return mods + list(extra)

    def run_runtime(self, mods):
        T._OVERRIDES["loaded_modules"] = lambda: mods
        return T.execute_part("runtime", self.ctx("runtime"))

    def test_ok(self):
        res = self.run_runtime(self.modules())
        self.assertEqual(res.status, T.OK, res.detail)
        self.assertEqual(res.detail, "msvcp140 14.50 (bundled), vcruntime140 14.50 (bundled), msvcr120 14.50 (bundled) | "
                                     "torch 2.11.0+cu128: CUDA DLLs from the app folder (cuDNN 9.19.0) | "
                                     "nvml.dll 32.0.15.7000 (Windows)")
        self.assertEqual(self.gi.env_applied, 1)
        self.assertEqual(len(self.loaded), len(T.VCRT) + 1)       # the five VC++ DLLs, then nvml.dll
        self.assertNotIn(str(self.app), json.dumps(res.facts))

    def test_cuda_dll_from_a_toolkit(self):
        mods = [m for m in self.modules() if not m.endswith("cudnn64_9.dll")]
        mods.append(r"Z:\Toolkit\CUDA\v12.8\bin\cudnn64_9.dll")
        res = self.run_runtime(mods)
        self.assertEqual(res.status, T.FAIL)
        self.assertIn("cudnn64_9 was loaded from outside the app folder", res.detail)
        self.assertNotIn("Toolkit", res.detail)

    def test_other_cuda_library_from_elsewhere(self):
        res = self.run_runtime(self.modules(extra=[r"Z:\Toolkit\bin\cufft64_11.dll"]))
        self.assertIn("cufft64_11 was loaded from outside the app folder", res.detail)

    def test_windows_copy_of_a_vc_runtime(self):
        res = self.run_runtime(self.modules(extra=[os.path.join(self.windir, "System32", "vcruntime140.dll")]))
        self.assertEqual(res.status, T.FAIL)
        self.assertIn("vcruntime140.dll: a copy from Windows is in use", res.detail)
        self.assertIn("vcruntime140 14.50 (bundled and Windows)", res.detail)

    def test_no_driver_and_no_torch(self):
        res = self.run_runtime(self.modules(nvml=False))
        self.assertEqual(res.status, T.OK)
        self.assertTrue(res.detail.endswith("nvml.dll not present (no NVIDIA driver)"))

        def no_torch():
            raise ImportError("DLL load failed")
        T._OVERRIDES["import_torch"] = no_torch
        res = self.run_runtime([m for m in self.modules() if "torch" not in m])
        self.assertEqual(res.status, T.FAIL)
        self.assertIn("torch could not be imported (ImportError)", res.detail)

    def test_missing_bundled_dll(self):
        (_STATE["bundle"] / "pyzbar" / "msvcr120.dll").unlink()
        try:
            res = self.run_runtime(self.modules())
            self.assertIn("msvcr120.dll is missing from the app", res.detail)
        finally:
            (_STATE["bundle"] / "pyzbar" / "msvcr120.dll").write_bytes(b"")


# ============================================================================================== integrity
class TestIntegrity(Base):
    def make_app(self):
        app = self.tmp / "app"
        files = {"Hangeul Document Checker.exe": b"exe", "_internal/BUILD.json": b"{}", "_internal/x/y.dll": b"dll"}
        for rel, data in files.items():
            p = app.joinpath(*rel.split("/"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        man = {"schema": "hdc-manifest/1", "files": {rel: {"size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                                                     for rel, d in files.items()}}
        (app / "_internal" / "MANIFEST.json").write_text(json.dumps(man), encoding="ascii")
        return app

    def test_ok_missing_changed(self):
        app = self.make_app()
        res = T.part_integrity(app, app / "_internal")
        self.assertEqual((res.status, res.detail), (T.OK, "3 files, 0 missing, 0 changed"))
        (app / "unins000.exe").write_bytes(b"extra")                     # an installer's file is not judged
        self.assertEqual(T.part_integrity(app, app / "_internal").status, T.OK)
        (app / "_internal" / "x" / "y.dll").write_bytes(b"DLL")          # same size, other bytes
        res = T.part_integrity(app, app / "_internal")
        self.assertEqual((res.status, res.code), (T.FAIL, "MISSING_PART"))
        self.assertIn("_internal/x/y.dll (changed)", res.detail)
        self.assertEqual(T.part_integrity(app, app / "_internal", full=False).status, T.OK)
        (app / "Hangeul Document Checker.exe").unlink()
        res = T.part_integrity(app, app / "_internal")
        self.assertIn("1 missing, 1 changed: Hangeul Document Checker.exe (missing)", res.detail)

    def test_no_manifest(self):
        res = T.part_integrity(self.tmp, self.tmp)
        self.assertEqual(res.status, T.SKIP)
        with mock.patch.object(T.sys, "frozen", True, create=True):
            self.assertEqual(T.part_integrity(self.tmp, self.tmp).code, "MISSING_PART")


# ======================================================================================= light real parts
class TestLightRealParts(Base):
    def test_core(self):
        res = T.execute_part("core", self.ctx("core"))
        self.assertEqual(res.status, T.OK, res.detail)
        self.assertRegex(res.detail, r"^numpy \S+ \(baseline \S+\), opencv \S+, pillow \S+$")
        self.assertIn("AVX2", res.facts["numpy_features"])

    def test_pdf(self):
        res = T.execute_part("pdf", self.ctx("pdf"))
        self.assertEqual(res.status, T.OK, res.detail)
        self.assertTrue(res.detail.endswith(": open, render, save, text PDF"))
        self.assertTrue((self.ctx("pdf").scratch / "cut \u09a0 page 1.pdf").is_file())

    def test_qr(self):
        res = T.execute_part("qr", self.ctx("qr"))
        self.assertEqual((res.status, res.detail), (T.OK, "zbar and OpenCV both decoded the made-up code"))

    def test_excel(self):
        res = T.execute_part("excel", self.ctx("excel"))
        self.assertEqual(res.status, T.OK, res.detail)
        self.assertRegex(res.detail, r"^openpyxl \S+: written and read back$")

    def test_guard_with_a_fake_guard(self):
        state = SimpleNamespace(blocked_total=0)
        seen = {}

        def prepare(out, cache_root, inp):
            seen["args"] = (out, cache_root, inp)
            return state

        def refused():
            state.blocked_total += 1
            return "guard"

        T._OVERRIDES.update(safety=SimpleNamespace(prepare=prepare, armed=lambda: state), places=fake_places(self.tmp),
                            probe_network=refused, probe_write=lambda target: refused())
        res = T.execute_part("guard", self.ctx("guard"))
        self.assertEqual((res.status, res.detail), (T.OK, "network refused, outside write refused"))
        self.assertEqual(seen["args"][1], self.tmp / "data" / "cache")
        T._OVERRIDES.update(probe_network=lambda: "not by the guard (ConnectionRefusedError)",
                            probe_write=lambda target: "created")
        res = T.execute_part("guard", self.ctx("guard"))
        self.assertEqual(res.status, T.FAIL)
        self.assertIn("network not by the guard (ConnectionRefusedError)", res.detail)
        self.assertIn("outside write created", res.detail)

    def test_imports_part_with_a_fake_importer(self):
        """8 g (integration): the runner tree is listed package by package (DEV / test packages never entered), every
        module and the third-party list are imported, the bot modules come through botimport.load, and torch must
        not have started CUDA."""
        import importlib
        names = T.runner_module_names()
        self.assertIn("src.verify.runner.gpuread", names)
        self.assertIn("src.verify.runner.app.worker", names)
        self.assertFalse([n for n in names if n.startswith(T.IMPORT_SKIP)])
        seen = []
        torch = SimpleNamespace(cuda=SimpleNamespace(is_initialized=lambda: False))
        bot = SimpleNamespace(dv=1, PC=1, TC=1, R=1, C=1, FC=1, OV=1, P=1)

        def importer(name):
            seen.append(name)
            if name in T.IMPORT_THIRD_PARTY:
                if name == "torch":
                    sys.modules["torch"] = torch
                return SimpleNamespace()
            if name == "src.verify.runner.botimport":
                return SimpleNamespace(load=lambda: bot)
            return importlib.import_module(name) if name.count(".") <= 3 and name.endswith(("runner", "app")) \
                else SimpleNamespace()

        old_torch = sys.modules.get("torch")
        try:
            res = T.part_imports(self.ctx("imports"), importer=importer)
            self.assertEqual(res.status, T.OK, res.detail)
            self.assertEqual(res.facts["cuda_initialized"], False)
            for n in ("torch", "easyocr", "scipy.ndimage", "skimage.io", "src.verify.runner.gpuread"):
                self.assertIn(n, seen)
            torch.cuda.is_initialized = lambda: True

            def broken(name):
                if name == "skimage.io":
                    raise ImportError("DLL load failed")
                return importer(name)
            with self.assertRaises(T.PartFailed) as cm:
                T.part_imports(self.ctx("imports"), importer=broken)
            self.assertIn("skimage.io (ImportError)", cm.exception.detail)
            self.assertIn("torch started CUDA", cm.exception.detail)
            self.assertEqual(cm.exception.code, "MISSING_PART")
        finally:
            if old_torch is None:
                sys.modules.pop("torch", None)
            else:
                sys.modules["torch"] = old_torch

    def test_unknown_part_and_run_part_writes_a_result(self):
        out = self.tmp / "parts" / "nope.json"
        self.assertEqual(T.run_part("nope", out), 0)
        r = T.PartResult.from_json(json.loads(out.read_text(encoding="ascii")))
        self.assertEqual((r.part, r.status), ("nope", T.FAIL))
        self.assertIn("unknown part", r.detail)

    def test_run_part_reads_its_input_file(self):
        parts = self.tmp / "scratch" / "parts"
        parts.mkdir(parents=True)
        (parts / "qr.in.json").write_text(json.dumps({
            "root": str(self.tmp / "scratch"), "data_dir": str(self.tmp / "data"), "bundle": str(_STATE["bundle"]),
            "lock_paths": ["Q:\\x.lock"], "events": str(parts / "qr.events.jsonl")}), encoding="ascii")
        ctx = T.make_context("qr", parts / "qr.json")
        self.assertEqual((ctx.scratch, ctx.lock_paths, ctx.bundle), (self.tmp / "scratch" / "qr", ["Q:\\x.lock"],
                                                                      _STATE["bundle"]))
        self.assertEqual(T.run_part("qr", parts / "qr.json"), 0)
        self.assertEqual(T.PartResult.from_json(json.loads((parts / "qr.json").read_text())).status, T.OK)


# ============================================================================================ the parent
CARD_FACTS = {"card_name": CARD, "torch": "2.11.0+cu128", "cuda": "12.8", "cudnn": "9.19.0", "device": "cuda:0",
              "cer": [0.004, 0.009, 0.006, 0.004], "sideways": [False, True], "passes": [1, 2, 1],
              "seconds": {"start": 14.2, "torch": 6.1, "cuda": 2.3, "readers": 1.8, "page1": 0.9, "page2": 3.1,
                          "photo": 0.8, "light": 0.4},
              "peak_alloc_gb": 2.41, "peak_reserved_gb": 2.8, "cap_gb": 3.0, "cpu_calls": 0, "waits": 1,
              "waited_s": 42.0, "read_code": "51C2-0A7E", "peak_ws_mb": 1310}
DETAILS = {"runtime": "msvcp140 14.50 (bundled), vcruntime140 14.50 (bundled), msvcr120 12.0 (bundled)",
           "core": "numpy 2.5.2 (baseline X86_V2), opencv 5.0.0, pillow 12.3.0",
           "pdf": "PyMuPDF 1.28.2: open, render, save, text PDF",
           "card": "reader on cuda:0; CER 0.4% / 0.9% / 0.6% / 0.4%; READ CODE 51C2-0A7E (build PC, same card model: "
                   "51C2-0A7E)",
           "qr": "zbar and OpenCV both decoded the made-up code", "excel": "openpyxl 3.1.5: written and read back",
           "tk": "Tcl/Tk 8.6", "guard": "network refused, outside write refused"}


def made_up_system_facts():
    return {"windows": {"product": "Windows 10 Pro", "display_version": "22H2", "build": 19045, "ubr": 6456,
                        "arch": "x64"},
            "smart_app_control": "off", "long_paths": False,
            "cpu": {"name": "Made-up CPU 9000", "cores": 6, "threads": 12,
                    "flags": {"SSE4.2": True, "AVX": True, "AVX2": True, "AVX512F": True}},
            "ram": {"total_mb": 31949, "free_mb": 22938}, "disk_free_gb": 210.4,
            "data_path": {"chars": 38, "spaces": False, "non_ascii": False, "overridden": False},
            "card": {"nvml": True, "count": 1, "name": CARD, "total_mb": 8151, "free_mb": 6120},
            "card_line": f"Graphics card: {CARD} | 8.0 GB total, 6.0 GB free | driver 000.00 (CUDA 12.9) | "
                         "compute 12.0 (this build: 7.5-12.0)"}


class FakeRunner:
    def __init__(self, behaviours=None):
        self.behaviours = behaviours or {}
        self.calls = []

    def run(self, part, result_path, timeout_s, *, cancel=None, cancel_file=None, tick=None, log_path=None,
            env=None):
        self.calls.append((part, timeout_s, env))
        b = self.behaviours.get(part)
        if b is None:
            facts = CARD_FACTS if part == "card" else (
                {"numpy_features": {"SSE42": True, "AVX2": True}} if part == "core" else {})
            Path(result_path).write_text(json.dumps(T.PartResult(part, T.OK, DETAILS[part], facts=facts).to_json()),
                                         encoding="ascii")
            return T.ChildOutcome(0, seconds=1.0)
        return b(part, Path(result_path), tick)


class TestParent(Base):
    def setUp(self):
        super().setUp()
        self.app = TestIntegrity.make_app(self)
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.lock = self.tmp / "bot" / "auto_verify.lock"
        (self.data / "settings.json").write_text(json.dumps({"gpu_lock_path": str(self.lock)}), encoding="utf-8")
        self.reports = self.tmp / "reports"
        T._OVERRIDES.update(collect=lambda data, arch_flags=None: made_up_system_facts(), cardgate=FakeCardgate(),
                            spawn=SimpleNamespace(child_env=lambda i, base: dict(base, HDC_CARD_ENV_MADE="1")))

    def run_all(self, runner, **kw):
        return T.run_selftest(self.reports, data_dir=self.data, bundle_dir=self.app / "_internal", app_dir=self.app,
                              runner=runner, now=WHEN, **kw)

    def test_all_ok(self):
        events = []
        runner = FakeRunner()
        out = self.run_all(runner, on_progress=lambda *a: events.append(a))
        self.assertTrue(out.ok, out.text)
        self.assertEqual(out.exit_code, 0)
        lines = out.text.splitlines()
        self.assertEqual(lines[:3], [
            "HANGEUL DOCUMENT CHECKER - SELF-TEST",
            "App 1.0.0, build 20261029.1-abc1234 | rules: bot 2026-10-21.3 + runner 2026-10-28.r2.6 | runner 0.2.7",
            "Run: 2026-10-29 14:12 | overall: OK (9 of 9 parts)"])
        self.assertEqual(lines[3:8], sysinfo.lines(made_up_system_facts(), {"SSE42": True, "AVX2": True}))
        self.assertEqual(lines[8:13], [
            "Reading engine: torch 2.11.0+cu128, CUDA 12.8, cuDNN 9.19.0 | cap 3.0 GB | first read: bot EasyOCR "
            "(torch defaults) | light reader: strict fp32",
            "Waits for: the Telegram bot's lock file (not present now) | waited 42 s during this test",
            "Reading: start 14.2 s (torch 6.1, CUDA 2.3, readers 1.8) | page 1 (upright) 0.9 s | page 2 (sideways, "
            "2 passes) 3.1 s | photo 0.8 s",
            "Card memory: peak 2.41 GB allocated, 2.80 GB reserved (cap 3.0 GB) | calls on the processor: 0",
            "Peak memory of the reading process: 1310 MB"])
        self.assertEqual(lines[13], "PARTS")
        self.assertEqual(lines[14], "  integrity  OK    3 files, 0 missing, 0 changed")
        self.assertEqual(lines[15], "  runtime    OK    " + DETAILS["runtime"])
        self.assertEqual(lines[18], "  card       OK    " + DETAILS["card"])
        self.assertEqual([ln.split()[0] for ln in lines[14:23]], list(T.PARTS))
        self.assertEqual(lines[23:], ["WARNINGS", "  (none)"])
        self.assertEqual([c[0] for c in runner.calls], list(T.PARTS[1:]))
        self.assertEqual([c[1] for c in runner.calls if c[0] == "card"], [900])
        self.assertTrue(all(c[1] == 120 for c in runner.calls if c[0] != "card"))
        card_env = next(c[2] for c in runner.calls if c[0] == "card")
        self.assertEqual((card_env["HDC_CARD_ENV_MADE"], card_env["HDC_DATA_DIR"]), ("1", str(self.data)))
        self.assertNotIn("HDC_CARD_ENV_MADE", next(c[2] for c in runner.calls if c[0] == "qr"))
        self.assertEqual(out.report_path, self.reports / "SELFTEST 2026-10-29 1412.txt")
        self.assertEqual(out.report_path.read_text(encoding="utf-8"), out.text)
        self.assertEqual((self.data / "selftest" / "SELFTEST.txt").read_text(encoding="utf-8"), out.text)
        summary = json.loads((self.data / "selftest" / "selftest.json").read_text(encoding="ascii"))
        self.assertEqual((summary["ok"], summary["ok_parts"], summary["card"]["read_code"]), (True, 9, "51C2-0A7E"))
        rec = T.build_read_record(summary)
        self.assertEqual((rec["read_code"], rec["card"], rec["build_id"]), ("51C2-0A7E", CARD, "20261029.1-abc1234"))
        card_in = json.loads((self.data / "selftest" / T.SCRATCH_NAME / "parts" / "card.in.json").read_text())
        self.assertEqual(card_in["lock_paths"], [str(self.lock)])
        self.assertEqual([e[1] for e in events if e[0] == "card"], ["start", "end"])
        self.assertEqual(sysinfo.holds_private(out.text, FAKE_ENV), [])
        out2 = self.run_all(FakeRunner())
        self.assertEqual(out2.report_path.name, "SELFTEST 2026-10-29 1412 (2).txt")

    def test_lock_present_now_and_other_locks(self):
        self.lock.parent.mkdir(parents=True)
        self.lock.write_text("")
        out = self.run_all(FakeRunner(), extra_locks=[str(self.tmp / "extra.lock")])
        self.assertIn("Waits for: 2 lock files (the Telegram bot's among them), 1 present now | waited 42 s",
                      out.text)
        (self.data / "settings.json").write_text(json.dumps({"gpu_lock_path": None}))
        out = self.run_all(FakeRunner())
        self.assertIn("Waits for: no lock file (the setting is off) | waited 42 s during this test", out.text)

    def test_crashed_child(self):
        def crash(part, result, tick):
            return T.ChildOutcome(0xC000001D)
        out = self.run_all(FakeRunner({"core": crash}))
        self.assertFalse(out.ok)
        self.assertEqual(out.exit_code, 1)
        self.assertIn("Run: 2026-10-29 14:12 | overall: FAIL (8 of 9 parts OK)", out.text)
        self.assertIn("  core       FAIL  process ended with 0xC000001D (illegal instruction)", out.text.splitlines())
        core = next(r for r in out.parts if r.part == "core")
        self.assertEqual(core.code, "CPU_UNSUPPORTED")
        self.assertIn("AVX2 yes", out.text)                      # the CPU line stays (Windows' flags)

    def test_card_timeout_with_waits_and_events(self):
        events = []

        def slow(part, result, tick):
            ev = Path(json.loads((result.parent / "card.in.json").read_text())["events"])
            with open(ev, "a", encoding="ascii") as f:
                f.write(json.dumps({"ev": "gpu_wait", "state": "start", "why": "lock", "lock": "bot",
                                    "where": "start", "reserved_mb": 0}) + "\n")
                f.write(json.dumps({"ev": "gpu_wait", "state": "tick", "why": "lock", "lock": "bot",
                                    "waited_s": 120.0}) + "\n")
            tick()
            return T.ChildOutcome(1, timed_out=True)

        out = self.run_all(FakeRunner({"card": slow}), on_progress=lambda *a: events.append(a))
        self.assertIn("  card       FAIL  did not finish within 900 s (it waited 120 s for the graphics card)",
                      out.text.splitlines())
        self.assertIn("Reading engine: not started (CRASH)", out.text)
        self.assertIn("Reading: not run", out.text)
        self.assertIn("Card memory: not measured", out.text)
        waits = [e[2] for e in events if e[0] == "card" and e[1] == "event"]
        self.assertEqual([w["state"] for w in waits], ["start", "tick"])

    def test_card_problem_line(self):
        def no_card(part, result, tick):
            r = T.PartResult("card", T.FAIL, "NO_NVIDIA_CARD: no NVIDIA graphics card or driver was found (step "
                             "'probe')", "NO_NVIDIA_CARD", facts={"card_name": "", "code": "NO_NVIDIA_CARD"})
            result.write_text(json.dumps(r.to_json()))
            return T.ChildOutcome(0)
        out = self.run_all(FakeRunner({"card": no_card}))
        self.assertIn("Reading engine: not started (NO_NVIDIA_CARD)", out.text)
        self.assertIn("  card       FAIL  NO_NVIDIA_CARD: no NVIDIA graphics card", out.text)

    def test_scrubbed_details_and_warnings(self):
        def leaky(part, result, tick):
            r = T.PartResult("runtime", T.FAIL, "could not load C:\\Users\\Xqz Yvw\\AppData\\Local\\x.dll on "
                             "MADEUP-PC7", warnings=["seen at \\\\MADEUP-PC7\\share\\y by Xqz Yvw"])
            result.write_text(json.dumps(r.to_json()))
            return T.ChildOutcome(0)
        out = self.run_all(FakeRunner({"runtime": leaky}))
        self.assertIn("  runtime    FAIL  could not load <path>", out.text)
        self.assertIn("  seen at <path>", out.text)
        self.assertNotIn("Xqz", out.text)
        self.assertNotIn("MADEUP", out.text)
        self.assertNotIn(str(self.tmp), out.text)
        self.assertEqual(sysinfo.holds_private(out.text, FAKE_ENV), [])

    def test_start_failure_and_cancel(self):
        def cannot(part, result, tick):
            return T.ChildOutcome(None, error="FileNotFoundError")
        state = {"stop": False}

        def progress(part, st, info):
            if part == "pdf" and st == "end":
                state["stop"] = True
        out = self.run_all(FakeRunner({"runtime": cannot}), on_progress=progress, cancel=lambda: state["stop"])
        by = {r.part: r for r in out.parts}
        self.assertEqual((by["runtime"].code, by["runtime"].detail),
                         ("STARTUP_FAILED", "the part could not be started (FileNotFoundError)"))
        self.assertEqual(by["pdf"].status, T.OK)
        for name in ("card", "qr", "excel", "tk", "guard"):
            self.assertEqual(by[name].detail, "not run (the self-test was cancelled)")
        self.assertTrue(out.cancelled)
        self.assertEqual(out.exit_code, 1)

    def test_warnings_from_parts_and_system(self):
        facts = made_up_system_facts()
        facts["ram"]["total_mb"] = 6000

        def warn(part, result, tick):
            r = T.PartResult("card", T.OK, DETAILS["card"], facts=CARD_FACTS,
                             warnings=["the READ CODE differs from the build PC's on the same card model: send "
                                       "SELFTEST.txt"])
            result.write_text(json.dumps(r.to_json()))
            return T.ChildOutcome(0)
        T._OVERRIDES["collect"] = lambda data, arch_flags=None: facts
        out = self.run_all(FakeRunner({"card": warn}))
        tail = out.text.splitlines()[out.text.splitlines().index("WARNINGS") + 1:]
        self.assertEqual(len(tail), 2)
        self.assertTrue(tail[0].startswith("  only 5.9 GB of RAM"))
        self.assertTrue(tail[1].startswith("  the READ CODE differs"))

    def test_main_uses_report_dir(self):
        seen = {}

        def fake_run(report_dir=None, **kw):
            seen["dir"], seen["kw"] = report_dir, kw
            return T.Outcome(ok=True, text="", parts=[], summary={})
        with mock.patch.object(T, "run_selftest", fake_run):
            self.assertEqual(T.main(report_dir=str(self.tmp / "r")), 0)
            self.assertEqual(seen["dir"], self.tmp / "r")
            self.assertEqual(T.main(["--selftest", "--report-dir", "R:\\x", "--gpu-lock-extra", "Q:\\l.lock",
                                     "--trace-opens", "T:\\t"]), 0)
            self.assertEqual((seen["dir"], seen["kw"]["extra_locks"]), (Path("R:\\x"), ["Q:\\l.lock"]))


# ======================================================================================= the process runner
class TestSubprocessRunner(Base):
    """Tiny Python children only (no app, no card); each is waited for or ended."""

    def runner(self, code, **kw):
        return T.SubprocessRunner(command=lambda *a: [sys.executable, "-I", "-c", code, *a], poll_s=0.05, **kw)

    def test_result_and_args(self):
        code = ("import json, sys; open(sys.argv[3], 'w').write(json.dumps({'schema': 'hdc-selftest-part/1', "
                "'part': sys.argv[2], 'status': 'OK', 'detail': sys.argv[1]}))")
        out = self.tmp / "r.json"
        oc = self.runner(code).run("qr", out, 30, log_path=self.tmp / "qr.log")
        self.assertEqual((oc.returncode, oc.timed_out), (0, False))
        r = T.PartResult.from_json(json.loads(out.read_text()))
        self.assertEqual((r.part, r.detail), ("qr", "--selftest-part"))

    def test_timeout_ends_the_child(self):
        oc = self.runner("import time; time.sleep(60)").run("x", self.tmp / "r.json", 1.0)
        self.assertTrue(oc.timed_out)
        self.assertIsNotNone(oc.returncode)
        self.assertLess(oc.seconds, 30)

    def test_crash_code(self):
        oc = self.runner("import os; os._exit(-0x3FFFFFE3)").run("x", self.tmp / "r.json", 30)
        self.assertEqual(oc.returncode & 0xFFFFFFFF, 0xC000001D)
        res = T._outcome_result("core", oc, 120, None)
        self.assertEqual((res.detail, res.code), ("process ended with 0xC000001D (illegal instruction)",
                                                  "CPU_UNSUPPORTED"))

    def test_cancel_makes_the_file_then_ends_the_child(self):
        cancel_file = self.tmp / "cancel"
        oc = self.runner("import time; time.sleep(60)", grace_s=0.3).run(
            "x", self.tmp / "r.json", 60, cancel=lambda: True, cancel_file=cancel_file)
        self.assertTrue(oc.cancelled)
        self.assertTrue(cancel_file.exists())
        self.assertIsNotNone(oc.returncode)

    def test_start_failure(self):
        r = T.SubprocessRunner(command=lambda *a: [str(self.tmp / "no-such-program.exe")])
        oc = r.run("x", self.tmp / "r.json", 5)
        self.assertEqual((oc.returncode, oc.error), (None, "FileNotFoundError"))

    @unittest.skipUnless(sys.platform == "win32", "job objects")
    def test_kill_on_close_job(self):
        job = T.KillOnCloseJob()
        p = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            self.assertTrue(job.add(p))
            job.close()
            self.assertIsNotNone(p.wait(timeout=15))
        finally:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=15)


# ============================================================================================ the text
class TestComposeText(Base):
    def test_layout_of_a_failed_card(self):
        results = [T.PartResult(n, T.OK, "fine") for n in T.PARTS]
        results[4] = T.PartResult("card", T.FAIL, "NO_NVIDIA_CARD: none", "NO_NVIDIA_CARD")
        text = T.compose_text({"app_version": "1.0.0", "build_id": "b", "base_rules": "r1", "runner_rules": "r2",
                               "runner": "0.2.7"}, WHEN, results, ["SYSTEM LINE"], {"code": "NO_NVIDIA_CARD"},
                              [("bot", True)], [])
        self.assertEqual(text.splitlines()[:9], [
            "HANGEUL DOCUMENT CHECKER - SELF-TEST",
            "App 1.0.0, build b | rules: bot r1 + runner r2 | runner 0.2.7",
            "Run: 2026-10-29 14:12 | overall: FAIL (8 of 9 parts OK)",
            "SYSTEM LINE",
            "Reading engine: not started (NO_NVIDIA_CARD)",
            "Waits for: the Telegram bot's lock file (present now) | waited 0 s during this test",
            "Reading: not run",
            "Card memory: not measured",
            "Peak memory of the reading process: not measured"])
        self.assertIn("  card       FAIL  NO_NVIDIA_CARD: none", text.splitlines())

    def test_waits_line_other_lock_and_passes(self):
        self.assertEqual(T._waits_line([("other", False)], 0.0),
                         "Waits for: another program's lock file (not present now) | waited 0 s during this test")
        lines = T.card_lines(dict(CARD_FACTS, passes=[4, 4, 1], sideways=[True, True]), [])
        self.assertIn("page 1 (sideways, 4 passes) 0.9 s | page 2 (sideways, 4 passes) 3.1 s", lines[2])


if __name__ == "__main__":
    unittest.main()
