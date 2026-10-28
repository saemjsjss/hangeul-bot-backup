"""The build environment (APP_SPEC r2 G10, R2.4.3): a --without-pip venv of the system Python 3.12.10 under
C:\\Hangeul\\DOCCHECK\\lab\\app-build, whose one .pth points READ-ONLY at the packages of lab\\onnx\\.venv (torch,
easyocr ...) and lab\\onnx\\.venv-pkg-numpy (PyInstaller, hooks-contrib, pefile ...). Nothing is ever installed, and
nothing is written into either lab venv: every write of this script goes under app-build.

    C:\\Users\\User\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -I -B make_build_env.py create
    C:\\Hangeul\\DOCCHECK\\lab\\app-build\\.venv-build\\Scripts\\python.exe -I -B make_build_env.py check
    ... make_build_env.py remove          (deletes .venv-build only)

`check` imports PyInstaller, torch, torchvision, easyocr, numpy, cv2, pymupdf, PIL, scipy.ndimage and skimage.io in
a child (-I -B, no card visible), prints their versions and torch._C._cuda_getArchFlags(), and writes
app-build\\build_env.json. It never touches torch.cuda and never asks torch for the cuDNN version (that starts CUDA).
Exit: 0 done, 1 failed, 2 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

PKG = Path(__file__).resolve().parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import stage  # noqa: E402  (run_low, child_env: standard library only)

BASE_PY = Path(r"C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe")
BASE_VERSION = "3.12.10"
LAB = Path(r"C:\Hangeul\DOCCHECK\lab")
LAB_ONNX = LAB / "onnx"
LAB_VENVS = (LAB_ONNX / ".venv", LAB_ONNX / ".venv-pkg-numpy")
PTH_LINES = (r"C:\Hangeul\DOCCHECK\lab\onnx\.venv\Lib\site-packages",
             r"C:\Hangeul\DOCCHECK\lab\onnx\.venv-pkg-numpy\Lib\site-packages")     # exactly these, in this order
PTH_NAME = "hdc_build.pth"
APP_BUILD = LAB / "app-build"
NEVER_WRITE = (LAB_ONNX, Path(r"C:\Hangeul\BOT"))

# The distribution versions this design was made with (R2.0 facts, R2.4.3); `check` fails on any other.
EXPECTED = {"pyinstaller": "6.22.3", "pyinstaller-hooks-contrib": "2026.9", "torch": "2.11.0+cu128",
            "torchvision": "0.26.0+cu128", "easyocr": "1.7.2", "numpy": "2.5.2",
            "opencv-python-headless": "5.0.0.93", "pymupdf": "1.28.2", "pillow": "12.3.0"}
DISTS = tuple(EXPECTED) + ("pefile", "altgraph", "pywin32-ctypes", "scipy", "scikit-image", "onnxruntime-directml",
                           "PySide6")

CHECK_CHILD = """\
import importlib, importlib.metadata as md, json, platform, sys
out = {"python": platform.python_version(), "executable": sys.executable, "prefix": sys.prefix,
       "base_prefix": sys.base_prefix, "sys_path": list(sys.path), "imports": {}, "dists": {}, "errors": {}}
for name in ("PyInstaller", "torch", "torchvision", "easyocr", "numpy", "cv2", "pymupdf", "PIL", "scipy.ndimage",
             "skimage.io"):
    try:
        mod = importlib.import_module(name)
        out["imports"][name] = str(getattr(mod, "__version__", "") or "")
    except Exception as e:
        out["errors"][name] = type(e).__name__
for dist in DISTS:
    try:
        out["dists"][dist] = md.version(dist)
    except md.PackageNotFoundError:
        out["dists"][dist] = None
try:
    import torch
    out["arch_flags"] = torch._C._cuda_getArchFlags()
    out["torch_cuda_build"] = torch.version.cuda
except Exception as e:
    out["errors"]["arch_flags"] = type(e).__name__
print(json.dumps(out, ensure_ascii=True))
"""


class EnvError(Exception):
    pass


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _under(p: Path, root: Path) -> bool:
    a, r = _norm(p), _norm(root)
    return a == r or a.startswith(r.rstrip("\\/") + os.sep)


def assert_write_ok(path: Path, app_build: Path) -> None:
    """Every write goes under app-build, and never under lab\\onnx or C:\\Hangeul\\BOT (pure checks, no I/O)."""
    for bad in NEVER_WRITE:
        if _under(app_build, bad) or _under(path, bad):
            raise EnvError(f"refused: {path} lies under a read-only folder")
    if not _under(path, app_build):
        raise EnvError(f"refused: {path} is not under {app_build}")


def pth_text() -> str:
    return "".join(line + "\n" for line in PTH_LINES)


def _home_of(venv: Path) -> Optional[str]:
    cfg = Path(venv) / "pyvenv.cfg"
    if not cfg.is_file():
        return None
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        k, _, v = line.partition("=")
        if k.strip().lower() == "home":
            return v.strip()
    return None


def create(app_build: Path = APP_BUILD, *, base_py: Path = BASE_PY, lab_venvs: Sequence[Path] = LAB_VENVS,
           run: Callable[..., stage.Ran] = stage.run_low, log: Callable[[str], None] = print) -> Path:
    venv = Path(app_build) / ".venv-build"
    assert_write_ok(venv, app_build)
    if venv.exists():
        raise EnvError(".venv-build exists already: run `remove` first")
    r = run([str(base_py), "-I", "-B", "-c", "import platform; print(platform.python_version())"],
            env=stage.child_env(), timeout=120, capture=True)
    got = (r.stdout or "").strip()
    if r.returncode != 0 or got != BASE_VERSION:
        raise EnvError(f"the base Python is {got or 'not runnable'}, {BASE_VERSION} is needed")
    for lv in lab_venvs:
        home = _home_of(lv)
        if home is None or _norm(Path(home)) != _norm(Path(base_py).parent):
            raise EnvError(f"{Path(lv).name} was not made from this base Python")
    Path(app_build).mkdir(parents=True, exist_ok=True)
    r = run([str(base_py), "-I", "-B", "-m", "venv", "--without-pip", str(venv)], env=stage.child_env(),
            timeout=600, capture=True)
    if r.returncode != 0 or not (venv / "Scripts" / "python.exe").is_file():
        raise EnvError(f"venv creation failed (exit {r.returncode})")
    site = venv / "Lib" / "site-packages"
    pth = site / PTH_NAME
    assert_write_ok(pth, app_build)
    site.mkdir(parents=True, exist_ok=True)
    with open(pth, "w", encoding="utf-8", newline="\n") as f:
        f.write(pth_text())
    log(f"created {venv} (Python {BASE_VERSION}, no pip) with {PTH_NAME} -> the two lab site-packages (read-only)")
    return venv


def evaluate(info: Dict[str, Any]) -> List[str]:
    """Problems of a check child's report ([] = the build env is the one this design was made with)."""
    problems = [f"import {k} failed ({v})" for k, v in sorted(info.get("errors", {}).items())]
    dists = info.get("dists", {})
    for name, want in EXPECTED.items():
        got = dists.get(name)
        if got != want:
            problems.append(f"{name} is {got}, {want} is needed")
    if info.get("python") != BASE_VERSION:
        problems.append(f"Python is {info.get('python')}, {BASE_VERSION} is needed")
    path = [_norm(Path(p)) for p in info.get("sys_path", []) if p]
    idx = [path.index(_norm(Path(p))) if _norm(Path(p)) in path else -1 for p in PTH_LINES]
    if -1 in idx or idx != sorted(idx):
        problems.append("sys.path does not hold the two lab site-packages in the .pth order")
    flags = info.get("arch_flags")
    if not isinstance(flags, str) or "sm_" not in flags:
        problems.append("torch's arch flags are missing")
    return problems


def check(app_build: Path = APP_BUILD, *, run: Callable[..., stage.Ran] = stage.run_low,
          log: Callable[[str], None] = print) -> Dict[str, Any]:
    venv = Path(app_build) / ".venv-build"
    py = venv / "Scripts" / "python.exe"
    if not py.is_file():
        raise EnvError(".venv-build is missing: run `create` first")
    pth = venv / "Lib" / "site-packages" / PTH_NAME
    if not pth.is_file() or pth.read_text(encoding="utf-8") != pth_text():
        raise EnvError(f"{PTH_NAME} is missing or not exactly the two lines of R2.4.3")
    out_json = Path(app_build) / "build_env.json"
    assert_write_ok(out_json, app_build)
    env = stage.child_env(extra={"PYINSTALLER_CONFIG_DIR": str(Path(app_build) / "pyi-config")})
    code = f"DISTS = {DISTS!r}\n" + CHECK_CHILD
    r = run([str(py), "-I", "-B", "-c", code], env=env, timeout=900, capture=True)
    if r.timed_out or r.returncode != 0:
        raise EnvError(f"the check child failed (exit {r.returncode}, timed out {r.timed_out})")
    try:
        info = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise EnvError("the check child printed no JSON") from None
    problems = evaluate(info)
    report = {"ok": not problems, "problems": problems, "python": info.get("python"),
              "modules": info.get("imports", {}), "dists": info.get("dists", {}),
              "arch_flags": info.get("arch_flags"),
              "torch_cuda_build": info.get("torch_cuda_build"), "pth": list(PTH_LINES),
              "seconds": round(r.seconds, 1)}
    tmp = out_json.with_name(out_json.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    os.replace(tmp, out_json)
    for k in ("PyInstaller", "torch", "torchvision", "easyocr", "numpy", "cv2", "pymupdf", "PIL", "scipy.ndimage",
              "skimage.io"):
        log(f"  {k:<14} {info.get('imports', {}).get(k, 'missing')}")
    log(f"  arch flags     {info.get('arch_flags')}")
    log("build env OK" if not problems else "build env: " + "; ".join(problems))
    return report


def remove(app_build: Path = APP_BUILD, *, log: Callable[[str], None] = print) -> bool:
    """Deletes app-build\\.venv-build and nothing else (it must look like a venv; links are refused)."""
    venv = Path(app_build) / ".venv-build"
    assert_write_ok(venv, app_build)
    if not venv.exists():
        log(".venv-build does not exist")
        return False
    if venv.is_symlink() or venv.is_junction() or not (venv / "pyvenv.cfg").is_file():
        raise EnvError("refused: .venv-build is not a plain venv folder")
    shutil.rmtree(venv)
    log(f"removed {venv}")
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="make_build_env.py")
    ap.add_argument("cmd", choices=("create", "check", "remove"))
    try:
        a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as e:
        return 2 if e.code else 0
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        if a.cmd == "create":
            create()
        elif a.cmd == "check":
            return 0 if check()["ok"] else 1
        else:
            remove()
        return 0
    except EnvError as e:
        print(f"make_build_env {a.cmd}: {e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
