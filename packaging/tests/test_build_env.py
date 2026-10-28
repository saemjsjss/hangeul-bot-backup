"""Unit tests of packaging\\make_build_env.py (Task F; APP_SPEC r2 G10, R2.4.3). Made-up app-build folders under
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\F; the subprocesses are faked (no venv is made, nothing is
imported from torch, the card is never visible). Nothing is ever written under lab\\onnx.

    python -I -B -m unittest discover -s <worktree>\\packaging\\tests -p test_build_env.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import make_build_env as mbe  # noqa: E402
import stage  # noqa: E402

ROOT = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\F")
WANT_PTH = ("C:\\Hangeul\\DOCCHECK\\lab\\onnx\\.venv\\Lib\\site-packages\n"
            "C:\\Hangeul\\DOCCHECK\\lab\\onnx\\.venv-pkg-numpy\\Lib\\site-packages\n")


def fresh(name: str) -> Path:
    d = ROOT / "env" / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def fake_venv(venv: Path, pth: Optional[str] = WANT_PTH) -> Path:
    (venv / "Scripts").mkdir(parents=True)
    (venv / "Scripts" / "python.exe").write_bytes(b"MZ made-up")
    (venv / "pyvenv.cfg").write_text("home = C:\\made-up\nversion = 3.12.10\n", encoding="utf-8")
    site = venv / "Lib" / "site-packages"
    site.mkdir(parents=True)
    if pth is not None:
        (site / "hdc_build.pth").write_text(pth, encoding="utf-8", newline="\n")
    return venv


class FakeRun:
    """Stands in for stage.run_low: records each command and its environment."""

    def __init__(self, version: str = "3.12.10", child: Optional[Dict[str, Any]] = None, rc: int = 0) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.version, self.child, self.rc = version, child, rc

    def __call__(self, cmd: List[str], **kw: Any) -> stage.Ran:
        self.calls.append({"cmd": list(cmd), **kw})
        if "-m" in cmd and "venv" in cmd:
            target = Path(cmd[-1])
            (target / "Scripts").mkdir(parents=True)
            (target / "Scripts" / "python.exe").write_bytes(b"MZ made-up")
            (target / "Lib" / "site-packages").mkdir(parents=True)
            (target / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
            return stage.Ran(list(cmd), 0, 0.1)
        if "-c" in cmd and "platform.python_version()" in cmd[-1] and "DISTS" not in cmd[-1]:
            return stage.Ran(list(cmd), 0, 0.1, stdout=self.version + "\n")
        return stage.Ran(list(cmd), self.rc, 1.0, stdout="noise\n" + json.dumps(self.child or {}) + "\n")


def good_child() -> Dict[str, Any]:
    return {"python": "3.12.10", "sys_path": ["C:\\x\\python312.zip", "C:\\x\\.venv-build\\Lib\\site-packages",
                                              *mbe.PTH_LINES],
            "imports": {"PyInstaller": "6.22.3", "torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128",
                        "easyocr": "1.7.2", "numpy": "2.5.2", "cv2": "5.0.0", "pymupdf": "1.28.2", "PIL": "12.3.0",
                        "scipy.ndimage": "", "skimage.io": ""},
            "dists": dict(mbe.EXPECTED, **{"pefile": "2024.8.26", "onnxruntime-directml": "1.24.4", "PySide6": None}),
            "errors": {}, "arch_flags": "sm_75 sm_80 sm_86 sm_90 sm_100 sm_120", "torch_cuda_build": "12.8"}


class CreateTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = fresh(self._testMethodName)
        self.app_build = self.root / "app-build"
        self.base_py = Path(r"C:\made-up\Python312\python.exe")
        self.labs = []
        for name in (".venv", ".venv-pkg-numpy"):
            lv = self.root / "lab" / name
            lv.mkdir(parents=True)
            (lv / "pyvenv.cfg").write_text(f"home = {self.base_py.parent}\nversion = 3.12.10\n", encoding="utf-8")
            self.labs.append(lv)
        self.logs: List[str] = []

    def create(self, run: FakeRun) -> Path:
        return mbe.create(self.app_build, base_py=self.base_py, lab_venvs=self.labs, run=run, log=self.logs.append)

    def test_pth_is_exactly_the_two_lines(self) -> None:
        self.assertEqual(mbe.pth_text(), WANT_PTH)
        run = FakeRun()
        venv = self.create(run)
        self.assertEqual(venv, self.app_build / ".venv-build")
        pth = venv / "Lib" / "site-packages" / "hdc_build.pth"
        self.assertEqual(pth.read_bytes(), WANT_PTH.encode("utf-8"))
        self.assertEqual(sorted(p.name for p in pth.parent.iterdir()), ["hdc_build.pth"])
        venv_cmd = run.calls[1]["cmd"]
        self.assertEqual(venv_cmd, [str(self.base_py), "-I", "-B", "-m", "venv", "--without-pip", str(venv)])
        self.assertEqual(run.calls[0]["cmd"][:3], [str(self.base_py), "-I", "-B"])
        for c in run.calls:
            self.assertEqual(c["env"]["CUDA_VISIBLE_DEVICES"], "-1")
            self.assertEqual(c["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_an_existing_venv_is_refused(self) -> None:
        (self.app_build / ".venv-build").mkdir(parents=True)
        with self.assertRaisesRegex(mbe.EnvError, "exists already"):
            self.create(FakeRun())

    def test_another_base_python_is_refused(self) -> None:
        with self.assertRaisesRegex(mbe.EnvError, "3.12.10 is needed"):
            self.create(FakeRun(version="3.12.9"))
        self.assertFalse((self.app_build / ".venv-build").exists())

    def test_lab_venvs_from_another_python_are_refused(self) -> None:
        (self.labs[1] / "pyvenv.cfg").write_text("home = C:\\other\\Python312\n", encoding="utf-8")
        with self.assertRaisesRegex(mbe.EnvError, "was not made from this base Python"):
            self.create(FakeRun())

    def test_writes_never_go_under_lab_onnx_or_the_bot(self) -> None:
        onnx = mbe.LAB_ONNX
        for path, app_build in ((onnx / ".venv" / "Lib" / "site-packages" / "hdc_build.pth", onnx),
                                (onnx / "x", self.app_build),
                                (Path(r"C:\Hangeul\BOT\data\x"), Path(r"C:\Hangeul\BOT")),
                                (self.root / "elsewhere" / "x", self.app_build)):
            with self.assertRaisesRegex(mbe.EnvError, "refused"):
                mbe.assert_write_ok(path, app_build)
        mbe.assert_write_ok(self.app_build / ".venv-build" / "x", self.app_build)
        with self.assertRaisesRegex(mbe.EnvError, "read-only"):
            mbe.create(onnx / "app-build", base_py=self.base_py, lab_venvs=self.labs, run=FakeRun())
        self.assertFalse((onnx / "app-build").exists())
        self.assertEqual(mbe.APP_BUILD, Path(r"C:\Hangeul\DOCCHECK\lab\app-build"))


class CheckTests(unittest.TestCase):

    def setUp(self) -> None:
        self.app_build = fresh(self._testMethodName) / "app-build"
        self.venv = fake_venv(self.app_build / ".venv-build")
        self.logs: List[str] = []

    def test_check_writes_build_env_json(self) -> None:
        run = FakeRun(child=good_child())
        rep = mbe.check(self.app_build, run=run, log=self.logs.append)
        self.assertTrue(rep["ok"], rep["problems"])
        saved = json.loads((self.app_build / "build_env.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["arch_flags"], "sm_75 sm_80 sm_86 sm_90 sm_100 sm_120")
        self.assertEqual(saved["dists"]["torch"], "2.11.0+cu128")
        self.assertEqual(saved["pth"], list(mbe.PTH_LINES))
        c = run.calls[0]
        self.assertEqual(c["cmd"][:4], [str(self.venv / "Scripts" / "python.exe"), "-I", "-B", "-c"])
        self.assertEqual(c["env"]["CUDA_VISIBLE_DEVICES"], "-1")
        self.assertEqual(c["env"]["PYINSTALLER_CONFIG_DIR"], str(self.app_build / "pyi-config"))
        self.assertTrue(c["cmd"][-1].startswith("DISTS = ("))
        self.assertIn("build env OK", self.logs[-1])

    def test_problems_are_reported(self) -> None:
        child = good_child()
        child["dists"]["torch"] = "2.10.0+cu126"
        child["errors"] = {"skimage.io": "ModuleNotFoundError"}
        child["sys_path"] = list(reversed(child["sys_path"]))
        child.pop("arch_flags")
        rep = mbe.check(self.app_build, run=FakeRun(child=child), log=self.logs.append)
        self.assertFalse(rep["ok"])
        text = "\n".join(rep["problems"])
        for want in ("torch is 2.10.0+cu126", "import skimage.io failed", ".pth order", "arch flags are missing"):
            self.assertIn(want, text)
        self.assertFalse(json.loads((self.app_build / "build_env.json").read_text(encoding="utf-8"))["ok"])

    def test_a_failing_child_and_a_bad_pth_are_refused(self) -> None:
        with self.assertRaisesRegex(mbe.EnvError, "check child failed"):
            mbe.check(self.app_build, run=FakeRun(child=good_child(), rc=1), log=self.logs.append)
        (self.venv / "Lib" / "site-packages" / "hdc_build.pth").write_text(WANT_PTH + "C:\\extra\n",
                                                                           encoding="utf-8")
        with self.assertRaisesRegex(mbe.EnvError, "two lines"):
            mbe.check(self.app_build, run=FakeRun(child=good_child()), log=self.logs.append)
        shutil.rmtree(self.venv)
        with self.assertRaisesRegex(mbe.EnvError, "run `create` first"):
            mbe.check(self.app_build, run=FakeRun(), log=self.logs.append)

    def test_the_child_never_touches_the_card(self) -> None:
        code = mbe.CHECK_CHILD
        self.assertIn("torch._C._cuda_getArchFlags()", code)
        self.assertNotRegex(code, r"torch\.cuda\b|cudnn|backends|set_per_process|is_available")
        for name in ("PyInstaller", "torch", "torchvision", "easyocr", "numpy", "cv2", "pymupdf", "PIL",
                     "scipy.ndimage", "skimage.io"):
            self.assertIn(f'"{name}"', code)

    def test_the_child_script_runs(self) -> None:
        """The real child script, run by THIS interpreter (the runner venv has no torch, so its imports fail
        cleanly and are reported): it prints one JSON line with the expected keys."""
        code = f"DISTS = {mbe.DISTS!r}\n" + mbe.CHECK_CHILD
        r = stage.run_low([sys.executable, "-I", "-B", "-c", code], env=stage.child_env(), timeout=300, capture=True)
        self.assertEqual(r.returncode, 0, r.stdout[-300:])
        info = json.loads(r.stdout.strip().splitlines()[-1])
        for k in ("python", "sys_path", "imports", "dists", "errors"):
            self.assertIn(k, info)
        self.assertEqual(set(info["dists"]), set(mbe.DISTS))
        if "torch" in info["errors"]:
            self.assertIn("arch_flags", info["errors"])
        self.assertTrue(mbe.evaluate(info))                     # the runner venv is not the build env


class RemoveTests(unittest.TestCase):

    def test_remove_only_the_venv(self) -> None:
        app_build = fresh("remove") / "app-build"
        logs: List[str] = []
        self.assertFalse(mbe.remove(app_build, log=logs.append))
        venv = fake_venv(app_build / ".venv-build")
        (app_build / "builds" / "20261029.1-abc1234").mkdir(parents=True)
        self.assertTrue(mbe.remove(app_build, log=logs.append))
        self.assertFalse(venv.exists())
        self.assertTrue((app_build / "builds" / "20261029.1-abc1234").is_dir())

    def test_a_folder_that_is_not_a_venv_is_refused(self) -> None:
        app_build = fresh("remove_refused") / "app-build"
        (app_build / ".venv-build" / "data").mkdir(parents=True)
        with self.assertRaisesRegex(mbe.EnvError, "not a plain venv"):
            mbe.remove(app_build)
        self.assertTrue((app_build / ".venv-build" / "data").is_dir())

    def test_main_arguments(self) -> None:
        self.assertEqual(mbe.main(["install"]), 2)


if __name__ == "__main__":
    unittest.main()
