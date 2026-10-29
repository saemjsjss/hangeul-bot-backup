"""Unit tests of packaging\\stage.py, hdc.spec, version.py and make_icon.py (Task F; APP_SPEC r2 R2.4.4, R2.4.5,
r1 6.1). Made-up trees under C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\F only; no card, no torch, no
PyInstaller (hdc.spec is executed with fake Analysis / PYZ / EXE / COLLECT and a fake collect_submodules).

    python -I -B -m unittest discover -s <worktree>\\packaging\\tests -p test_stage.py
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import types
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import make_icon  # noqa: E402
import manifest  # noqa: E402
import stage  # noqa: E402
import version  # noqa: E402

ROOT = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\F")
SPEC = PKG / "hdc.spec"
REAL_WORKTREE = PKG.parent
REAL_VENDOR = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\engine\vendor")
REAL_EASYOCR_CONFIG = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\.venv\Lib\site-packages\easyocr\config.py")
TORCH_DLLS = ("c10.dll", "c10_cuda.dll", "cudnn64_9.dll", "torch_cpu.dll", "torch_cuda.dll", "cudart64_12.dll")
ARCH = "sm_75 sm_80 sm_86 sm_90 sm_100 sm_120"


def fresh(name: str) -> Path:
    d = ROOT / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return path


def put_bytes(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


SW = {s.name: s.old for s in stage.SWAPS}


def fake_gpuread(nl: str = "\n", drop: Tuple[str, ...] = (), twice: Tuple[str, ...] = ()) -> str:
    """A made-up module holding the seven DEV literals exactly where gpuread.py has them."""
    def lit(name: str) -> List[str]:
        if name in drop:
            return []
        return [SW[name]] * (2 if name in twice else 1)

    lines = ['"""A made-up stand-in of the first-read module.', "",
             "It writes only under private-runner (docstring text, blanked by the scrub).", '"""',
             "import argparse", "from pathlib import Path", "", *lit("WORKTREE"), *lit("PRIVATE"),
             'GPU_DATA = PRIVATE / "gpu-data"', *lit("GUARD"), "", "",
             "def check_out(p):",
             '    """True when p lies under C:\\\\Hangeul\\\\DOCCHECK\\\\lab\\\\private-runner."""',
             "    # a comment naming C:\\Hangeul\\DOCCHECK\\lab\\private-runner never reaches the bytecode",
             "    if not p:", *["        " + s for s in lit("check_out message")], "    return p", "", "",
             "def write_json(p):", "    if not p:", *["        " + s for s in lit("write_json message")],
             "    return None", "", "",
             "def _parser():", "    ap = argparse.ArgumentParser()",
             *['    ap.add_argument("page_texts", nargs="?", ' + s + ")" for s in lit("page_texts help")],
             *['    ap.add_argument("--out", ' + s + ")" for s in lit("--out help")],
             "    return ap", ""]
    return nl.join(lines)


def fake_ppt(nl: str = "\n") -> str:
    return nl.join(['"""Made-up prototype loader."""', "from pathlib import Path", "", SW["DOCS_ROOT"],
                    SW["TEXT_DIR"], "", "", "def load(docs_root=DOCS_ROOT, text_dir=TEXT_DIR):",
                    '    """Reads nothing in the app."""', "    return [docs_root, text_dir]", ""])


CARDGATE = ('"""Made-up card gate."""\n'
            'BOT_LOCK_DEFAULT = r"C:\\Hangeul\\BOT\\data\\verification\\auto_verify.lock"   # the one string\n'
            "LOCK_STALE_S = 4 * 3600\n")


def config_py(md5_craft: str, md5_g2: str) -> str:
    return ("import os\n\nBASE_PATH = os.path.dirname(__file__)\n"
            "detection_models = {\n"
            f"    'craft': {{'filename': 'craft_mlt_25k.pth', 'url': 'https://example.invalid/c.zip', "
            f"'md5sum': '{md5_craft}'}},\n"
            "    'dbnet18': {'filename': 'x.pt', 'url': 'https://example.invalid/x.zip', "
            "'md5sum': '00000000000000000000000000000000'}\n}\n"
            "imgH = 64\n"
            "recognition_models = {\n"
            "    'gen1': {'latin': {'filename': 'latin.pth', 'characters': ''.join(separator_char) + 'abc'}},\n"
            f"    'gen2': {{'english_g2': {{'filename': 'english_g2.pth', 'model_script': 'english', "
            f"'md5sum': '{md5_g2}', 'characters': '0123'}}}}\n}}\n")


DISTS = {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "easyocr": "1.7.2", "numpy": "2.5.2",
         "opencv_python_headless": "5.0.0.93", "pymupdf": "1.28.2", "pillow": "12.3.0", "scipy": "1.18.1",
         "scikit_image": "0.26.0", "imageio": "2.38.1"}
DISTS2 = {"pyinstaller": "6.22.3", "pyinstaller_hooks_contrib": "2026.9"}


class Fake:
    """A made-up source world: worktree, glue, vendor, weights, two site dirs, System32; plus fake readers."""

    def __init__(self, name: str, *, nl: str = "\n", gpuread: Optional[str] = None, weight_extra: bytes = b"",
                 torch_version: str = "2.11.0+cu128", vcrt_version: str = "14.50.35719.0", git_rc: int = 0,
                 extra_app: Optional[Dict[str, str]] = None) -> None:
        self.root = fresh(name)
        r = self.root
        wt = r / "worktree"
        self.worktree = wt
        runner = wt / "src" / "verify" / "runner"
        put(wt / "src" / "__init__.py", '"""Made-up package."""\n')
        put(wt / "src" / "verify" / "__init__.py", "")
        for rel in stage.BOT_FILES:
            if not (wt / rel).exists():
                put(wt / rel, f'"""Made-up bot module {Path(rel).stem}."""{nl}VALUE = 1{nl}')
        put(wt / stage.PPT, fake_ppt(nl))
        put(runner / "__init__.py", '"""Runner."""\nfrom .model import RUNNER_VERSION\n')
        put(runner / "model.py", 'RUNNER_VERSION = "0.0.9"\nBASE_RULES_VERSION = "2000-01-01.1"\n'
                                 'RUNNER_RULES_VERSION = "2000-01-02.r1"\n')
        put(runner / "__main__.py", "def main():\n    return 0\n")
        put(runner / "engine.py", '"""Engine.\n\nMulti-line docstring.\n"""\n\n\ndef f():\n'
                                  '    """One line."""\n    return "page"\n')
        put(runner / "closer.py", "X = 1\n")
        put(runner / "gpuread.py", gpuread if gpuread is not None else fake_gpuread(nl))
        put(runner / "devplaces.py", 'PRIVATE = r"C:\\Hangeul\\DOCCHECK\\lab\\private-runner"\n')
        put(runner / "tests" / "__init__.py", "")
        put(runner / "tests" / "test_x.py", 'P = "C:\\\\Hangeul\\\\x"\n')
        put(runner / "selftest" / "check.py", 'P = "jarvis"\n')
        app = runner / "app"
        put(app / "__init__.py", "")
        put(app / "entry.py", '"""Entry."""\n\n\ndef main():\n    return 0\n')
        put(app / "cardgate.py", CARDGATE)
        for fname, text in (extra_app or {}).items():
            put(app / fname, text)
        put(app / "tests" / "__init__.py", "")
        put(app / "tests" / "test_y.py", 'P = ".venv"\n')

        self.glue = r / "glue"
        put(self.glue / "glue_numpy.py", '"""Glue."""\n\n\nclass LightReader:\n    """Reader."""\n')
        put(self.glue / "easyocr_vendor.py", '"""Vendor glue."""\nX = 2\n')
        put(self.glue / "glue_data.json", '{"made_up": true}\n')

        self.vendor = r / "vendor"
        put(self.vendor / "pyzbar" / "__init__.py", "")
        put(self.vendor / "pyzbar" / "pyzbar.py", "def decode(x):\n    return []\n")
        put_bytes(self.vendor / "pyzbar" / "libzbar-64.dll", b"MZ made-up zbar")
        put_bytes(self.vendor / "pyzbar" / "libiconv.dll", b"MZ made-up iconv")
        put(self.vendor / "pyzbar" / "zbar-LICENSE.txt", "made-up licence\n")
        put(self.vendor / "pyzbar" / "__pycache__" / "x.cpython-312.pyc", "junk")
        put(self.vendor / "openpyxl" / "__init__.py", '"""Workbook."""\n')
        put(self.vendor / "openpyxl" / "cell" / "cell.py", "C = 1\n")
        put(self.vendor / "et_xmlfile" / "__init__.py", "")
        listed = {}
        for pkg, ver in (("openpyxl", "3.1.5"), ("et_xmlfile", "2.0.0")):
            n, h = stage.tree_sha256(self.vendor / pkg)
            listed[pkg] = {"version": ver, "files": n, "tree_sha256": h}
        put(self.vendor / "VENDOR.json", json.dumps({"packages": listed}))
        n, h = stage.tree_sha256(self.vendor / "pyzbar")
        self.pins = {"pyzbar": {"files": n, "tree_sha256": h}}

        self.weights = r / "weights"
        craft = put_bytes(self.weights / "craft_mlt_25k.pth", b"made-up craft weights" * 50)
        g2 = put_bytes(self.weights / "english_g2.pth", b"made-up g2 weights" * 40)
        self.md5 = {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in (craft, g2)}
        if weight_extra:
            with open(craft, "ab") as f:
                f.write(weight_extra)

        s1, s2 = r / "site1", r / "site2"
        self.site_dirs = (s1, s2)
        for d, dists in ((s1, DISTS), (s2, DISTS2)):
            for dname, ver in dists.items():
                v = torch_version if dname == "torch" else ver
                put(d / f"{dname}-{v}.dist-info" / "METADATA",
                    f"Metadata-Version: 2.1\nName: {dname}\nVersion: {v}\n\nmade-up\n")
        put(s1 / "easyocr" / "config.py",
            config_py(self.md5["craft_mlt_25k.pth"], self.md5["english_g2.pth"]))
        put(s1 / "torch" / "version.py", f"from typing import Optional\n__version__ = '{torch_version}'\n"
                                         "cuda: Optional[str] = '12.8'\n")
        self.torch_lib = s1 / "torch" / "lib"
        for dll in TORCH_DLLS:
            put_bytes(self.torch_lib / dll, b"MZ made-up " + dll.encode())

        self.system32 = r / "System32"
        for name in stage.VCRT_MIN:
            put_bytes(self.system32 / name, b"MZ made-up " + name.encode())

        self.logs: List[str] = []
        self.git_calls: List[List[str]] = []
        self.probe_calls = 0
        self.vcrt_version = vcrt_version
        self.git_rc = git_rc
        self.stage_dir = r / "build" / "stage"
        self.reports = r / "build" / "reports"

    def sources(self, **kw: Any) -> stage.Sources:
        return stage.Sources(worktree=self.worktree, glue_dir=self.glue, vendor_dir=self.vendor,
                             weights_dir=self.weights, site_dirs=self.site_dirs, system32=self.system32,
                             python_exe=Path(sys.executable), selftest_maker=self.root / "make_selftest_pages.py",
                             allow_file=PKG / "scan_allow.txt", vendor_pins=self.pins, **kw)

    def file_version(self, p: Path) -> str:
        if p.name == "cudnn64_9.dll":
            return "9.19.0.56"
        if p.name == "msvcr120.dll":
            return "12.0.40664.0"
        return self.vcrt_version

    def probe(self, py: Path) -> Dict[str, Any]:
        self.probe_calls += 1
        return {"python": "3.12.10", "torch": "2.11.0+cu128", "numpy": "2.5.2", "arch_flags": ARCH,
                "numpy_cpu_baseline": ["SSE", "SSE2", "SSE3", "SSSE3", "SSE41", "POPCNT", "SSE42"]}

    @staticmethod
    def make_selftest(py: Path, maker: Path, out: Path) -> None:
        put_bytes(out / "selftest_pages.pdf", b"%PDF-1.7 made-up")
        put(out / "selftest_expected.json", '{"page1": ["SAMPLE"]}\n')
        put_bytes(out / "selftest_page1.jpg", b"\xff\xd8\xff made-up")

    def git(self, cmd: List[str], **kw: Any) -> subprocess.CompletedProcess:
        self.git_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.git_rc, b"", b"")

    def tools(self) -> stage.Tools:
        return stage.Tools(file_version=self.file_version, probe=self.probe, make_selftest=self.make_selftest,
                           git_run=self.git, now=lambda: "2026-10-29T00:00:00Z", log=self.logs.append)

    def run(self, build_id: str = "20261029.1-abcdef1", **kw: Any) -> Dict[str, Any]:
        return stage.run(self.sources(**kw), self.stage_dir, self.reports, build_id, self.tools())


_SHARED: Dict[str, Any] = {}


def shared_stage() -> Fake:
    """One full stage of the made-up world, shared by the read-only tests below."""
    if "fake" not in _SHARED:
        f = Fake("stage_ok")
        _SHARED["build"] = f.run()
        _SHARED["fake"] = f
    return _SHARED["fake"]


# ---------------------------------------------------------------------------------------------------------------

class StageOutputTests(unittest.TestCase):
    """A full stage of the made-up world: the generated list, the scrub, the weights, BUILD.json, the extras."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.f = shared_stage()
        cls.build = _SHARED["build"]
        cls.st = cls.f.stage_dir

    def test_generated_list(self) -> None:
        rows = stage.read_stage_list(self.f.reports / "STAGE_LIST.txt")
        paths = {r["path"] for r in rows}
        for rel in ("src/verify/runner/gpuread.py", "src/verify/runner/closer.py", "src/verify/runner/engine.py",
                    "src/verify/runner/__main__.py", "src/verify/runner/app/__init__.py",
                    "src/verify/runner/app/entry.py", "src/verify/runner/app/cardgate.py", "glue_numpy.py",
                    "easyocr_vendor.py", "engine/glue_data.json", "vendor/pyzbar/libzbar-64.dll",
                    "vendor/openpyxl/cell/cell.py", "engine/easyocr/model/craft_mlt_25k.pth",
                    "engine/easyocr/model/english_g2.pth", "engine/easyocr/user_network/.keep",
                    "vcrt/msvcr120.dll", "selftest/selftest_page1.jpg", "BUILD.json", "version_info.txt",
                    "icon.ico", "hdc_entry.py", *stage.BOT_FILES):
            self.assertIn(rel, paths)
        for rel in paths:
            self.assertNotRegex(rel, r"devplaces\.py$|/tests/|^src/.*/selftest/|__pycache__|\.onnx$|\.pyc$")
        self.assertEqual(stage.src_modules(self.f.reports / "STAGE_LIST.txt"),
                         sorted(m for m in (r["module"] for r in rows) if m and m.startswith("src")))
        self.assertIn("src.verify.runner.app.cardgate", stage.src_modules(self.f.reports / "STAGE_LIST.txt"))
        by = {r["path"]: r for r in rows}
        self.assertEqual(by["vendor/openpyxl/cell/cell.py"]["module"], "openpyxl.cell.cell")
        self.assertEqual(by["src/verify/runner/app/__init__.py"]["module"], "src.verify.runner.app")
        self.assertEqual(by["BUILD.json"]["module"], None)
        self.assertEqual(by["src/verify/runner/gpuread.py"]["group"], "runner")
        self.assertEqual(by["BUILD.json"]["sha256"], manifest.sha256_file(self.st / "BUILD.json"))

    def test_a_new_runner_module_is_staged_by_itself(self) -> None:
        put(self.f.worktree / "src" / "verify" / "runner" / "laterstage.py", "Y = 1\n")
        try:
            rels = [e.rel for e in stage.copy_plan(self.f.sources())]
        finally:
            (self.f.worktree / "src" / "verify" / "runner" / "laterstage.py").unlink()
        self.assertIn("src/verify/runner/laterstage.py", rels)
        self.assertNotIn("src/verify/runner/devplaces.py", rels)

    def test_gpuread_swapped_and_scrubbed(self) -> None:
        orig = (self.f.worktree / stage.GPUREAD).read_text(encoding="utf-8")
        text = (self.st / stage.GPUREAD).read_text(encoding="utf-8")
        for s in stage.swaps_for(stage.GPUREAD):
            self.assertNotIn(s.old, text)
            self.assertEqual(text.count(s.new), 1)
        self.assertEqual(len(stage.split_lines(orig)), len(stage.split_lines(text)))
        self.assertNotIn("It writes only under", text)
        self.assertIn("# a comment naming", text)          # comments stay in the source, never in the bytecode
        code = compile(text, "src/verify/runner/gpuread.py", "exec")
        self.assertEqual([h for s in stage.code_strings(code) for h in stage.text_hits(s)], [])
        tree = ast.parse(text)
        self.assertEqual(ast.get_docstring(tree, clean=False), "\n" * 3)
        ppt = (self.st / stage.PPT).read_text(encoding="utf-8")
        self.assertIn('DOCS_ROOT = Path("no-docs-root")', ppt)
        self.assertIn('TEXT_DIR = Path("no-text-dir")', ppt)

    def test_docstrings_blank_everywhere_ours_not_vendor(self) -> None:
        eng = (self.st / "src/verify/runner/engine.py").read_text(encoding="utf-8")
        self.assertTrue(eng.startswith('"""\n\n\n"""\n'))
        self.assertIn('    """"""\n    return "page"', eng)
        self.assertIn('""""""', (self.st / "glue_numpy.py").read_text(encoding="utf-8"))
        self.assertEqual((self.st / "vendor/openpyxl/__init__.py").read_text(encoding="utf-8"), '"""Workbook."""\n')

    def test_weights_keep_and_no_onnx(self) -> None:
        m = self.build["models"]
        self.assertEqual(set(m), {"craft_mlt_25k.pth", "english_g2.pth"})
        for fname, facts in m.items():
            p = self.st / "engine" / "easyocr" / "model" / fname
            self.assertEqual(facts["md5"], self.f.md5[fname])
            self.assertEqual(facts["sha256"], hashlib.sha256(p.read_bytes()).hexdigest())
            self.assertEqual(facts["size"], p.stat().st_size)
        self.assertEqual((self.st / "engine/easyocr/user_network/.keep").read_bytes(), b"")
        self.assertEqual(list(self.st.rglob("*.onnx")), [])
        self.assertFalse((self.st / "engine" / "models").exists())

    def test_build_json_fields(self) -> None:
        b = json.loads((self.st / "BUILD.json").read_text(encoding="utf-8"))
        self.assertEqual(b, self.build)
        want = {"app_version": "1.0.0", "build_id": "20261029.1-abcdef1", "file_version": "1.0.0.1",
                "flavour": "gpu-cu128", "built": "2026-10-29T00:00:00Z", "runner_version": "0.0.9",
                "base_rules_version": "2000-01-01.1", "runner_rules_version": "2000-01-02.r1",
                "spec": "RUNNER_SPEC r2.7 / APP_SPEC r2", "baseline": "r8 (runner 0.2.7, 344b342)",
                "python": "3.12.10", "torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "easyocr": "1.7.2",
                "cuda": "12.8", "cudnn": "9.19.0", "cudnn_file_version": "9.19.0.56",
                "arch_flags": ARCH.split(), "cuda_driver_min": 12080, "cap_gb": 3.0, "need_free_mb": 4096,
                "min_total_mb": 5800, "bot_commit": "07b1f22"}
        for k, v in want.items():
            self.assertEqual(b[k], v, k)
        self.assertEqual(b["vcrt"]["msvcr120.dll"], "12.0.40664.0")
        self.assertEqual(b["vcrt"]["msvcp140_1.dll"], "14.50.35719.0")
        self.assertEqual(b["packages"]["pyinstaller"], "6.22.3")      # from the second site dir
        self.assertEqual(b["packages"]["opencv"], "5.0.0.93")
        self.assertEqual(b["vendor"]["pyzbar"]["checked_against"], "pin")
        self.assertEqual(b["vendor"]["openpyxl"]["checked_against"], "VENDOR.json")
        self.assertIn("SSE42", b["numpy_cpu_baseline"])
        self.assertEqual(self.f.probe_calls, 1)
        text = json.dumps(b).lower()
        for p in stage.FORBIDDEN:
            self.assertNotIn(p, text)

    def test_bot_check_and_extras(self) -> None:
        self.assertEqual(self.f.git_calls[0][:6], ["git", "-C", str(self.f.worktree), "diff", "--quiet", "07b1f22"])
        self.assertEqual(self.f.git_calls[0][7:], list(stage.BOT_MODULES))
        self.assertEqual((self.st / "hdc_entry.py").read_text(encoding="utf-8"), stage.HDC_ENTRY)
        vi = (self.st / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn("Hangeul Document Checker (GPU)", vi)
        self.assertIn("1.0.0.1", vi)
        self.assertTrue((self.st / "icon.ico").read_bytes().startswith(b"\x00\x00\x01\x00"))
        for n in stage.VCRT_MIN:
            self.assertTrue((self.st / "vcrt" / n).is_file())
        summary = json.loads((self.f.reports / "stage.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["swaps"], 9)
        self.assertEqual(summary["scan_hits"], 0)

    def test_restage_into_a_used_folder_is_refused(self) -> None:
        with self.assertRaisesRegex(stage.StageError, "not empty"):
            stage.run(self.f.sources(), self.st, self.f.reports, "20261029.1-abcdef1", self.f.tools())


class StageRefusalTests(unittest.TestCase):
    """Each broken rule stops the stage with a StageError."""

    def setUp(self) -> None:
        self._icon = mock.patch.object(make_icon, "write_icon", lambda p, sizes=(): put_bytes(p, b"\0\0\1\0"))
        self._icon.start()

    def tearDown(self) -> None:
        self._icon.stop()

    def test_swap_literal_missing(self) -> None:
        f = Fake("ref_missing", gpuread=fake_gpuread(drop=("GUARD",)))
        with self.assertRaisesRegex(stage.StageError, r"GUARD\) occurs 0 times"):
            f.run()

    def test_swap_literal_twice(self) -> None:
        f = Fake("ref_twice", gpuread=fake_gpuread(twice=("write_json message",)))
        with self.assertRaisesRegex(stage.StageError, r"write_json message\) occurs 2 times"):
            f.run()

    def test_md5_refused(self) -> None:
        f = Fake("ref_md5", weight_extra=b"x")
        with self.assertRaisesRegex(stage.StageError, "craft_mlt_25k.pth: MD5 differs"):
            f.run()

    def test_engine_version_refused(self) -> None:
        f = Fake("ref_torch", torch_version="2.10.0+cu126")
        with self.assertRaisesRegex(stage.StageError, "the tested engine is 2.11.0"):
            f.run()

    def test_old_vcrt_refused(self) -> None:
        f = Fake("ref_vcrt", vcrt_version="14.29.30139.0")
        with self.assertRaisesRegex(stage.StageError, "older than 14.40"):
            f.run()

    def test_vendor_tree_refused(self) -> None:
        f = Fake("ref_vendor")
        put(f.vendor / "openpyxl" / "cell" / "cell.py", "C = 2\n")
        with self.assertRaisesRegex(stage.StageError, "vendored openpyxl"):
            f.run()

    def test_bot_modules_changed_refused(self) -> None:
        f = Fake("ref_bot", git_rc=1)
        with self.assertRaisesRegex(stage.StageError, "differ from 07b1f22"):
            f.run()

    def test_scan_hit_refused(self) -> None:
        f = Fake("ref_scan", extra_app={"leaky.py": 'ROOT = "C:/Hangeul/x"\n# c:\\hangeul in a comment is fine\n'})
        with self.assertRaisesRegex(stage.StageError, r"fast scan: 1 forbidden.*src\.verify\.runner\.app\.leaky: c:/hangeul"):
            f.run()

    def test_allow_line_covers_only_its_string(self) -> None:
        f = Fake("ref_allow", extra_app={"cardgate.py": CARDGATE + 'OTHER = "c:\\\\hangeul\\\\bot\\\\x"\n'})
        with self.assertRaisesRegex(stage.StageError, r"cardgate: c:\\hangeul"):
            f.run()

    def test_comment_only_is_clean_and_selftest_maker_checked(self) -> None:
        f = Fake("ref_comment", extra_app={"quiet.py": "# mentions C:\\Hangeul and jarvis in a comment only\nQ = 1\n"})
        tools = f.tools()
        tools.make_selftest = lambda py, maker, out: put(out / "selftest_pages.pdf", "x")
        with self.assertRaisesRegex(stage.StageError, "did not make selftest"):
            stage.run(f.sources(), f.stage_dir, f.reports, "20261029.2-abcdef1", tools)
        f2 = Fake("ref_comment2", extra_app={"quiet.py": "# mentions C:\\Hangeul and jarvis in a comment only\n"})
        f2.run()

    def test_crlf_sources(self) -> None:
        f = Fake("ref_crlf", nl="\r\n")
        f.run()
        staged = (f.stage_dir / stage.GPUREAD).read_bytes()
        self.assertNotIn(b"private-runner\")", staged)
        self.assertEqual(staged.count(b"\r\n"), (f.worktree / stage.GPUREAD).read_bytes().count(b"\r\n"))
        self.assertEqual(staged.replace(b"\r\n", b"").count(b"\n"), 0)


class AstCheckTests(unittest.TestCase):
    """The scrub's guard: any change beyond the docstrings and the listed constants stops the build."""

    REL = stage.GPUREAD

    def setUp(self) -> None:
        self.orig = fake_gpuread()
        self.mine = stage.swaps_for(self.REL)

    def test_clean_scrub_passes(self) -> None:
        staged, n = stage.scrub_file(self.REL, self.orig)
        self.assertEqual(n, 2)
        stage.ast_check(self.REL, self.orig, staged, self.mine)

    def test_an_extra_constant_change_is_refused(self) -> None:
        staged, _ = stage.scrub_file(self.REL, self.orig)
        bad = staged.replace('"gpu-data"', '"gpu-data2"')
        with self.assertRaisesRegex(stage.StageError, "beyond the docstrings"):
            stage.ast_check(self.REL, self.orig, bad, self.mine)

    def test_a_scrubber_that_changes_more_is_caught(self) -> None:
        real = stage.blank_docstrings

        def greedy(text: str) -> Tuple[str, int]:
            out, n = real(text)
            return out.replace("return p", "return None"), n

        with mock.patch.object(stage, "blank_docstrings", greedy):
            with self.assertRaisesRegex(stage.StageError, "beyond the docstrings"):
                stage.scrub_file(self.REL, self.orig)

    def test_an_unlisted_swap_is_refused(self) -> None:
        staged, _ = stage.scrub_file(self.REL, self.orig)
        with self.assertRaisesRegex(stage.StageError, "beyond the docstrings"):
            stage.ast_check(self.REL, self.orig, staged, self.mine[:-1])

    def test_a_moved_line_is_refused(self) -> None:
        staged, _ = stage.scrub_file(self.REL, self.orig)
        bad = staged.replace("import argparse\n", "\nimport argparse\n", 1)
        with self.assertRaisesRegex(stage.StageError, "number of lines|another line"):
            stage.ast_check(self.REL, self.orig, bad, self.mine)

    def test_swaps_are_well_formed(self) -> None:
        self.assertEqual(len(stage.swaps_for(stage.PPT)), 2)
        self.assertEqual(len(stage.swaps_for(stage.GPUREAD)), 7)
        self.assertEqual(len({s.name for s in stage.SWAPS}), len(stage.SWAPS))
        for s in stage.SWAPS:
            self.assertNotEqual(s.old_value, s.new_value)
            self.assertEqual(stage.text_hits(s.new_value), [], s.name)
            self.assertTrue(stage.text_hits(s.old_value), s.name)
        with self.assertRaises(stage.StageError):
            stage.literal_of('f("a", "b")')

    def test_lone_carriage_return_refused(self) -> None:
        with self.assertRaisesRegex(stage.StageError, "carriage return"):
            stage.blank_docstrings('"""a"""\rX = 1\n')

    def test_real_swap_targets(self) -> None:
        """The REAL gpuread.py and prototype_page_types.py of the worktree (read only): every swap literal occurs
        exactly once, the AST check passes, and no forbidden string is left in their code."""
        for rel in (stage.GPUREAD, stage.PPT):
            p = REAL_WORKTREE / rel
            if not p.is_file():
                self.skipTest(f"{rel} not in this worktree")
            orig = p.read_bytes().decode("utf-8")
            staged, _ = stage.scrub_file(rel, orig)
            code = compile(staged, rel, "exec")
            hits = sorted({h for s in stage.code_strings(code) for h in stage.text_hits(s)})
            self.assertEqual(hits, [], rel)


class ScanAndReaderTests(unittest.TestCase):

    def test_allow_file(self) -> None:
        allow = stage.load_allow(PKG / "scan_allow.txt")
        ours = [a for a in allow if a.where.startswith("src.")]
        self.assertEqual(len(ours), 1)
        self.assertEqual(ours[0].where, "src.verify.runner.app.cardgate")
        self.assertEqual(ours[0].string, "c:\\hangeul\\bot\\data\\verification\\auto_verify.lock")
        bad = put(fresh("allow_bad") / "a.txt", "x | y\n")
        with self.assertRaisesRegex(stage.StageError, "line 1"):
            stage.load_allow(bad)

    def test_bytes_hits_utf8_and_utf16(self) -> None:
        self.assertEqual(stage.bytes_hits(b"abc"), [])
        self.assertEqual(stage.bytes_hits("x C:\\HANGEUL y".encode("utf-16-le")), ["c:\\hangeul"])
        self.assertEqual(stage.bytes_hits(b"..\\.VENV\\.."), [".venv"])
        self.assertEqual(stage.bytes_hits(b"c:\\hangeul\\bot\\a.lock", ["c:\\hangeul\\bot\\a.lock"]), [])

    def test_module_name(self) -> None:
        self.assertEqual(stage.module_name("src/verify/runner/app/__init__.py"), "src.verify.runner.app")
        self.assertEqual(stage.module_name("vendor/pyzbar/pyzbar.py"), "pyzbar.pyzbar")
        self.assertEqual(stage.module_name("glue_numpy.py"), "glue_numpy")
        self.assertIsNone(stage.module_name("engine/glue_data.json"))

    def test_tree_sha256_matches_vendor_json(self) -> None:
        vj = REAL_VENDOR / "VENDOR.json"
        if not vj.is_file():
            self.skipTest("no vendored packages on this PC")
        listed = json.loads(vj.read_text(encoding="utf-8"))["packages"]
        for pkg in ("openpyxl", "et_xmlfile"):
            n, h = stage.tree_sha256(REAL_VENDOR / pkg)
            self.assertEqual((n, h), (listed[pkg]["files"], listed[pkg]["tree_sha256"]), pkg)
        n, h = stage.tree_sha256(REAL_VENDOR / "pyzbar")
        self.assertEqual({"files": n, "tree_sha256": h}, stage.VENDOR_PINS["pyzbar"])

    def test_real_easyocr_config_read_as_text(self) -> None:
        if not REAL_EASYOCR_CONFIG.is_file():
            self.skipTest("lab\\onnx\\.venv not on this PC")
        self.assertEqual(stage.easyocr_md5s(REAL_EASYOCR_CONFIG),
                         {"craft_mlt_25k.pth": "2f8227d2def4037cdb3b34389dcf9ec1",
                          "english_g2.pth": "5864788e1821be9e454ec108d61b887d"})
        self.assertNotIn("easyocr", sys.modules)

    def test_dist_version(self) -> None:
        f = shared_stage()
        self.assertEqual(stage.dist_version("opencv-python-headless", f.site_dirs), "5.0.0.93")
        self.assertEqual(stage.dist_version("PyInstaller", f.site_dirs), "6.22.3")
        with self.assertRaisesRegex(stage.StageError, "not installed"):
            stage.dist_version("onnxruntime", f.site_dirs)

    def test_file_version_reader(self) -> None:
        k32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "kernel32.dll"
        if not k32.is_file():
            self.skipTest("not Windows")
        self.assertRegex(stage.read_file_version(k32), r"^\d+\.\d+\.\d+\.\d+$")
        with self.assertRaises(stage.StageError):
            stage.read_file_version(PKG / "scan_allow.txt")

    def test_probe_child_never_touches_the_card(self) -> None:
        code = stage.PROBE_CHILD
        self.assertIn("torch._C._cuda_getArchFlags()", code)
        self.assertNotRegex(code, r"torch\.cuda\b|cudnn|backends")
        env = stage.child_env({"CUDA_PATH": "x", "PYTORCH_CUDA_ALLOC_CONF": "y", "PYTHONPATH": "z", "KEEP": "1",
                               "HDC_TEST_NO_CARD": "1", "HDC_BUNDLE_DIR": "b"})
        self.assertNotIn("HDC_TEST_NO_CARD", env)
        self.assertNotIn("HDC_BUNDLE_DIR", env)
        self.assertEqual(stage.child_env({}, extra={"HDC_DATA_DIR": "d"})["HDC_DATA_DIR"], "d")
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "-1")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertNotIn("CUDA_PATH", env)
        self.assertNotIn("PYTORCH_CUDA_ALLOC_CONF", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["KEEP"], "1")

    def test_run_low_below_normal_and_timeout(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows only")
        code = ("import ctypes; k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; "
                "k.GetPriorityClass.argtypes = [ctypes.c_void_p]; print(k.GetPriorityClass(k.GetCurrentProcess()))")
        r = stage.run_low([sys.executable, "-I", "-B", "-c", code], env=stage.child_env(), timeout=60, capture=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(int(r.stdout.strip()), 0x4000)
        r = stage.run_low([sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"], env=stage.child_env(),
                          timeout=1, capture=True)
        self.assertTrue(r.timed_out)
        self.assertIsNone(r.returncode)
        self.assertLess(r.seconds, 20)


# ---------------------------------------------------------------------------------------------------------------
# hdc.spec, executed with fake PyInstaller names

class FakeAnalysis:
    toc: Callable[[Dict[str, Any]], Tuple[list, list, list]]
    last: "FakeAnalysis"

    def __init__(self, scripts: List[str], **kw: Any) -> None:
        self.scripts_in, self.kw = scripts, kw
        self.scripts = [("hdc_entry", scripts[0], "PYSOURCE")]
        self.pure, self.binaries, self.datas = FakeAnalysis.toc(kw)
        FakeAnalysis.last = self


class Recorder:
    def __init__(self, *args: Any, **kw: Any) -> None:
        self.args, self.kw = args, kw


class SpecTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.f = shared_stage()
        cls.st = cls.f.stage_dir
        cls.reports = fresh("spec_reports")
        cls.sys32 = cls.f.system32

    def good_toc(self) -> Tuple[list, list, list]:
        st, j = self.st, os.path.join
        pure = [(m, str(st / "x.py"), "PYMODULE") for m in stage.src_modules(self.f.reports / "STAGE_LIST.txt")]
        pure += [("numpy", "n.py", "PYMODULE"), ("torch.onnx", "t.py", "PYMODULE"), ("easyocr.easyocr", "e", "PYMODULE")]
        binaries = [(j("torch", "lib", d), str(self.f.torch_lib / d), "BINARY") for d in TORCH_DLLS]
        binaries += [(j("torch", "lib", "torch_cpu.lib"), "x.lib", "BINARY"),
                     ("vcruntime140.dll", str(st / "vcrt" / "vcruntime140.dll"), "BINARY"),
                     ("vcruntime140_1.dll", str(self.sys32 / "vcruntime140_1.dll"), "BINARY"),   # not ours: dropped
                     ("msvcp140.dll", str(st / "vcrt" / "msvcp140.dll"), "BINARY"),
                     ("msvcr120.dll", str(self.sys32 / "msvcr120.dll"), "BINARY"),               # not ours: dropped
                     (j("pyzbar", "libzbar-64.dll"), str(st / "vendor/pyzbar/libzbar-64.dll"), "BINARY"),
                     (j("cv2", "opencv_videoio_ffmpeg500_64.dll"), "ff.dll", "BINARY"),          # dropped
                     (j("numpy.libs", "msvcp140-" + "a" * 32 + ".dll"), "n.dll", "BINARY"),
                     (j("ml_dtypes.libs", "msvcp140-" + "a" * 32 + ".dll"), "m.dll", "BINARY"),  # a delvewheel pair
                     ("python312.dll", "p.dll", "BINARY")]
        datas = [(j("torch", "include", "ATen", "x.h"), "x.h", "DATA"), (j("torch", "share", "cmake", "a.cmake"),
                 "a", "DATA"), (j("engine", "glue_data.json"), str(st / "engine/glue_data.json"), "DATA"),
                 ("BUILD.json", str(st / "BUILD.json"), "DATA")]
        return pure, binaries, datas

    def exec_spec(self, toc: Optional[Tuple[list, list, list]] = None,
                  torch_lib: Optional[Path] = None) -> Dict[str, Any]:
        toc = toc or self.good_toc()
        FakeAnalysis.toc = staticmethod(lambda kw: (list(toc[0]), list(toc[1]), list(toc[2])))  # type: ignore
        hooks = types.ModuleType("PyInstaller.utils.hooks")
        calls: List[Any] = []

        def collect_submodules(pkg: str, filter: Callable[[str], bool] = lambda m: True) -> List[str]:
            calls.append((pkg, list(sys.path[:2])))
            return [m for m in ("src.verify.runner", "src.verify.runner.engine", "src.verify.runner.tests",
                                "src.verify.runner.tests.test_x", "src.verify.runner.devplaces") if filter(m)]

        hooks.collect_submodules = collect_submodules  # type: ignore[attr-defined]
        utils = types.ModuleType("PyInstaller.utils")
        utils.hooks = hooks  # type: ignore[attr-defined]
        pi = types.ModuleType("PyInstaller")
        pi.utils = utils  # type: ignore[attr-defined]
        ns: Dict[str, Any] = {"__name__": "__main__", "__file__": str(SPEC), "SPECPATH": str(PKG),
                              "Analysis": FakeAnalysis, "PYZ": Recorder, "EXE": Recorder, "COLLECT": Recorder}
        env = {"HDC_STAGE": str(self.st), "HDC_REPORTS": str(self.reports),
               "HDC_TORCH_LIB": str(torch_lib or self.f.torch_lib)}
        saved_path = list(sys.path)
        try:
            with mock.patch.dict(sys.modules, {"PyInstaller": pi, "PyInstaller.utils": utils,
                                               "PyInstaller.utils.hooks": hooks}), \
                    mock.patch.dict(os.environ, env):
                exec(compile(SPEC.read_text(encoding="utf-8"), str(SPEC), "exec"), ns)
        finally:
            sys.path[:] = saved_path
        ns["_collect_calls"] = calls
        return ns

    def test_happy_path(self) -> None:
        ns = self.exec_spec()
        a = FakeAnalysis.last
        self.assertEqual(a.scripts_in, [str(self.st / "hdc_entry.py")])
        self.assertEqual(a.kw["hooksconfig"], {"easyocr": {"lang_codes": ["en"]}})
        self.assertEqual(a.kw["optimize"], 0)
        for m in ("onnxruntime", "PySide6", "triton", "easyocr.DBNet", "torch.utils.tensorboard"):
            self.assertIn(m, a.kw["excludes"])
        hidden = a.kw["hiddenimports"]
        for m in ("src.verify.runner.gpuread", "src.verify.runner.app.cardgate", "src.verify.runner.__main__",
                  "glue_numpy", "easyocr_vendor", "pyzbar.pyzbar", "easyocr.easyocr", "src.verify.runner.engine"):
            self.assertIn(m, hidden)
        self.assertFalse([m for m in hidden if ".tests" in m or "devplaces" in m])
        self.assertEqual(ns["_collect_calls"][0][1], [str(self.st), str(self.st / "vendor")])
        dests = {d.replace("\\", "/").lower() for d, _, _ in a.binaries}
        self.assertNotIn("torch/lib/torch_cpu.lib", dests)
        self.assertNotIn("cv2/opencv_videoio_ffmpeg500_64.dll", dests)
        self.assertNotIn("msvcr120.dll", dests)
        self.assertIn("pyzbar/msvcr120.dll", dests)
        by = {d.replace("\\", "/").lower(): s for d, s, _ in a.binaries}
        for n in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll"):
            self.assertEqual(Path(by[n]), self.st / "vcrt" / n)
        self.assertEqual(Path(by["pyzbar/msvcr120.dll"]), self.st / "vcrt" / "msvcr120.dll")
        ddests = {d.replace("\\", "/").lower() for d, _, _ in a.datas}
        self.assertEqual(ddests, {"engine/glue_data.json", "build.json"})
        sources = {Path(s) for s, _ in a.kw["datas"]}
        self.assertIn(self.st / "engine/easyocr/user_network/.keep", sources)
        self.assertIn(self.st / "engine/easyocr/model/english_g2.pth", sources)
        self.assertFalse([s for s in sources if str(s).endswith(".onnx")])
        exe, coll = ns["exe"], ns["coll"]
        self.assertEqual(exe.kw["name"], "Hangeul Document Checker")
        self.assertIs(exe.kw["console"], False)
        self.assertIs(exe.kw["upx"], False)
        self.assertIs(exe.kw["strip"], False)
        self.assertIs(exe.kw["exclude_binaries"], True)
        self.assertEqual(Path(exe.kw["icon"]), self.st / "icon.ico")
        self.assertEqual(Path(exe.kw["version"]), self.st / "version_info.txt")
        self.assertIs(coll.kw["upx"], False)
        self.assertEqual(coll.kw["name"], "Hangeul Document Checker")
        rep = json.loads((self.reports / "toc_checks.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["problems"], [])
        self.assertEqual(list(rep["delvewheel_pairs"]), ["msvcp140-" + "a" * 32 + ".dll"])
        self.assertEqual(rep["torch_lib_dlls"], len(TORCH_DLLS))

    def _stops(self, toc: Tuple[list, list, list], pattern: str, torch_lib: Optional[Path] = None) -> None:
        with self.assertRaises(SystemExit) as cm:
            self.exec_spec(toc, torch_lib)
        self.assertRegex(str(cm.exception), pattern)

    def test_a_dll_twice_stops(self) -> None:
        pure, b, d = self.good_toc()
        other = fresh("spec_other_dll") / "cudnn64_9.dll"           # the same name from ANOTHER source
        b.append(("cudnn64_9.dll", str(other), "BINARY"))
        self._stops((pure, b, d), r"DLL collected twice: cudnn64_9\.dll")
        pure, b, d = self.good_toc()
        d.append((os.path.join("torch", "lib", "torch_cuda.dll"), "x", "DATA"))      # once more, as data
        self._stops((pure, b, d), r"DLL collected twice: torch_cuda\.dll")

    def test_top_level_copies_of_folder_dlls_are_dropped(self) -> None:
        """PyInstaller's dependency scan collects a DLL of a .pth site dir (and of the staged vendor folder) a second
        time at the top level; the top-level entry with the SAME source is dropped, the package-folder one kept."""
        pure, b, d = self.good_toc()
        iconv = str(self.st / "vendor" / "pyzbar" / "libiconv.dll")
        delve = "msvcp140-" + "a" * 32 + ".dll"
        b += [("cudnn64_9.dll", str(self.f.torch_lib / "cudnn64_9.dll"), "BINARY"),
              ("torch_cpu.dll", str(self.f.torch_lib / "torch_cpu.dll").upper(), "BINARY"),   # case of the path
              (os.path.join("pyzbar", "libiconv.dll"), iconv, "BINARY"),
              ("libiconv.dll", iconv, "BINARY"),
              (delve, "n.dll", "BINARY")]                                                  # numpy.libs' source
        self.exec_spec((pure, b, d))
        a = FakeAnalysis.last
        dests = [x.replace("\\", "/").lower() for x, _, _ in a.binaries]
        for top in ("cudnn64_9.dll", "torch_cpu.dll", "libiconv.dll", delve):
            self.assertNotIn(top, dests)
        for kept in ("torch/lib/cudnn64_9.dll", "torch/lib/torch_cpu.dll", "pyzbar/libiconv.dll",
                     "numpy.libs/" + delve, "python312.dll", "vcruntime140.dll"):
            self.assertIn(kept, dests)
        rep = json.loads((self.reports / "toc_checks.json").read_text(encoding="utf-8"))
        self.assertEqual(rep["problems"], [])
        for top in ("cudnn64_9.dll", "torch_cpu.dll", "libiconv.dll", delve):
            self.assertIn(top, rep["dropped"])
        self.assertEqual(list(rep["delvewheel_pairs"]), [delve])          # the two *.libs copies stay a pair

    def test_drop_top_level_copies_rule(self) -> None:
        ns = self.exec_spec()
        keep, dropped = ns["drop_top_level_copies"]([("a.dll", "x/a.dll", "BINARY"), ("p/a.dll", "x/a.dll", "BINARY"),
                                                     ("b.dll", "y/b.dll", "BINARY"), ("q/b.dll", "z/b.dll", "BINARY"),
                                                     ("c.dll", "x/c.dll", "BINARY")])
        self.assertEqual(dropped, ["a.dll"])
        self.assertEqual([e[0] for e in keep], ["p/a.dll", "b.dll", "q/b.dll", "c.dll"])

    def test_torch_lib_set_must_equal(self) -> None:
        pure, b, d = self.good_toc()
        b = [e for e in b if not e[0].endswith("cudart64_12.dll")]
        self._stops((pure, b, d), r"missing \['cudart64_12\.dll'\]")
        empty = fresh("spec_empty_torch_lib")
        self._stops(self.good_toc(), r"no torch\\lib", torch_lib=empty)

    def test_excluded_modules_and_files_stop(self) -> None:
        pure, b, d = self.good_toc()
        pure.append(("onnxruntime.capi", "o.py", "PYMODULE"))
        self._stops((pure, b, d), "excluded module collected: onnxruntime.capi")
        pure, b, d = self.good_toc()
        d.append((os.path.join("PySide6", "plugins", "x.dll"), "q.dll", "DATA"))
        self._stops((pure, b, d), "excluded file collected: PySide6")
        pure, b, d = self.good_toc()
        b.append(("onnxruntime_providers_shared.dll", "o.dll", "BINARY"))     # dropped by DROP_WORDS
        ns = self.exec_spec((pure, b, d))
        self.assertIn("onnxruntime_providers_shared.dll", json.loads(
            (self.reports / "toc_checks.json").read_text(encoding="utf-8"))["dropped"])
        self.assertIn("coll", ns)

    def test_src_modules_must_equal_the_stage(self) -> None:
        pure, b, d = self.good_toc()
        pure.append(("src.verify.runner.devplaces", "d.py", "PYMODULE"))
        self._stops((pure, b, d), r"extra \['src\.verify\.runner\.devplaces'\]")
        pure, b, d = self.good_toc()
        pure = [e for e in pure if e[0] != "src.verify.runner.gpuread"]
        self._stops((pure, b, d), r"missing \['src\.verify\.runner\.gpuread'\]")

    def test_drop_data_rule(self) -> None:
        ns = self.exec_spec()
        dd = ns["drop_data"]
        for dest in ("torch/lib/c10.lib", "torch\\include\\a.h", "x/y.hpp", "k.cuh", "torch/share/cmake/T.cmake"):
            self.assertTrue(dd(dest), dest)
        for dest in ("torch/lib/c10.dll", "engine/glue_data.json", "easyocr/character/en_char.txt"):
            self.assertFalse(dd(dest), dest)


# ---------------------------------------------------------------------------------------------------------------

class VersionTests(unittest.TestCase):

    def test_build_ids(self) -> None:
        self.assertEqual(version.parse_build_id("20261029.3-abc1234-dirty"),
                         {"date": "20261029", "n": 3, "git": "abc1234", "dirty": True})
        for bad in ("20261029-abc1234", "2026102.1-abc1234", "20261029.0-abc1234", "20261029.1-ABC1234",
                    "20261029.1-abc1234-clean", "../x"):
            with self.assertRaises(ValueError, msg=bad):
                version.parse_build_id(bad)
        root = fresh("ids")
        self.assertEqual(version.next_build_id(root, "20261029", "abc1234", False), "20261029.1-abc1234")
        (root / "20261029.1-abc1234").mkdir()
        (root / "20261029.4-def5678-dirty").mkdir()
        (root / "20261028.9-abc1234").mkdir()
        (root / "junk").mkdir()
        self.assertEqual(version.next_build_id(root, "20261029", "abc1234", True), "20261029.5-abc1234-dirty")
        self.assertEqual(version.file_version("20261029.5-abc1234-dirty"), "1.0.0.5")

    def test_git_facts(self) -> None:
        calls = []

        def run(cmd: List[str], **kw: Any) -> subprocess.CompletedProcess:
            calls.append(cmd)
            out = "abc1234\n" if "rev-parse" in cmd else " M APP_SPEC.md\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")

        self.assertEqual(version.git_facts(Path("C:/wt"), run), ("abc1234", True))
        self.assertEqual(calls[0][-3:], ["rev-parse", "--short=7", "HEAD"])
        self.assertEqual(calls[1][-2:], ["status", "--porcelain"])

    def test_version_info_text(self) -> None:
        text = version.version_info_text("20261029.2-abc1234", "0.2.7", "2026-10-21.3", "2026-10-28.r2.6")

        def rec(name: str) -> Callable[..., Dict[str, Any]]:
            return lambda *a, **kw: {"_": name, "args": a, **kw}

        names = {n: rec(n) for n in ("VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable",
                                      "StringStruct", "VarFileInfo", "VarStruct")}
        v = eval(compile(ast.parse(text, mode="eval"), "vi", "eval"), {"__builtins__": {}}, names)
        self.assertEqual(v["ffi"]["filevers"], (1, 0, 0, 2))
        self.assertEqual(v["ffi"]["prodvers"], (1, 0, 0, 0))
        table = v["kids"][0]["args"][0][0]
        strings = {s["args"][0]: s["args"][1] for s in table["args"][1]}
        self.assertEqual(strings["FileDescription"], "Hangeul Document Checker (GPU)")
        self.assertEqual(strings["OriginalFilename"], "Hangeul Document Checker.exe")
        self.assertEqual(strings["FileVersion"], "1.0.0.2")
        self.assertEqual(strings["ProductVersion"], "1.0.0")
        self.assertIn("GPU (CUDA 12.8)", strings["Comments"])
        self.assertIn("build 20261029.2-abc1234", strings["Comments"])


class IconTests(unittest.TestCase):

    def test_ico_structure_and_determinism(self) -> None:
        sizes = (16, 32, 64)
        data = make_icon.ico_bytes(sizes)
        self.assertEqual(data, make_icon.ico_bytes(sizes))
        reserved, kind, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual((reserved, kind, count), (0, 1, 3))
        for i, s in enumerate(sizes):
            w, h, _, _, planes, bpp, n, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
            self.assertEqual((w, h, planes, bpp), (s, s, 1, 32))
            blob = data[off:off + n]
            if s >= make_icon.PNG_FROM:
                self.assertTrue(blob.startswith(b"\x89PNG\r\n\x1a\n"))
            else:
                self.assertEqual(struct.unpack_from("<Iii", blob, 0), (40, s, 2 * s))
                self.assertEqual(len(blob), 40 + 4 * s * s + ((s + 31) // 32) * 4 * s)
        px = make_icon.render(16)
        self.assertEqual(px[0][0][3], 0)                     # a transparent corner
        self.assertEqual(px[8][4][3], 255)                   # an opaque page

    def test_write_icon(self) -> None:
        out = fresh("icon") / "icon.ico"
        self.assertEqual(make_icon.main([str(out)]), 0)
        data = out.read_bytes()
        self.assertEqual(struct.unpack_from("<HHH", data, 0), (0, 1, len(make_icon.SIZES)))
        self.assertEqual(data[6], 16)
        self.assertEqual(data[6 + 16 * (len(make_icon.SIZES) - 1)], 0)       # 256 is written as 0
        self.assertEqual(make_icon.main(["x.png"]), 2)


if __name__ == "__main__":
    unittest.main()
