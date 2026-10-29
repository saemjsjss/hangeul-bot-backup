# -*- mode: python ; coding: utf-8 -*-
# hdc.spec - PyInstaller 6.22.3, one folder, windowed: Hangeul Document Checker, the GPU build.
# APP_SPEC r2 R2.4.5 over r1 6.2. Run only through `build.py freeze`, which sets HDC_STAGE (the staged, scrubbed
# copy: PyInstaller never sees the worktree) and HDC_REPORTS. After Analysis the TOC is filtered (a top-level copy of
# a DLL whose same source file is also collected into its package folder is dropped: see drop_top_level_copies) and
# then checked; the build stops when a check fails:
#   - no DLL base name twice (case-insensitive; a 774 MB DLL collected twice would double the size). The one
#     exception is a delvewheel copy "<name>-<32 hex>.dll" in two "*.libs" folders: same name = same content, each
#     loaded by its own package; such pairs are reported;
#   - {every torch\lib\*.dll of the build env} == {the collected torch/lib DLLs} (none trimmed, R2.4.1);
#   - no onnxruntime, PySide6 or shiboken6 module, and no onnxruntime / DirectML / PySide6 file;
#   - the src.* modules equal the staged ones (STAGE_LIST.txt); our VC++ copies are the only ones.
# There is no own runtime hook: boot sets the environment before torch is imported (R2.3.3).
import json
import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

STAGE = Path(os.environ["HDC_STAGE"])
REPORTS = os.environ.get("HDC_REPORTS")
NAME = "Hangeul Document Checker"

SKIP = ("src.verify.runner.devplaces", "src.verify.runner.tests", "src.verify.runner.selftest",
        "src.verify.runner.app.tests")                     # gpuread is NOT skipped any more
EXCLUDES = ["onnxruntime", "onnx", "onnxscript", "tensorboard", "torch.utils.tensorboard", "triton",
            "easyocr.DBNet", "easyocr.detection_db", "easyocr.export", "easyocr.cli", "easyocr.scripts",
            "PySide6", "shiboken6", "PyQt5", "PyQt6", "matplotlib", "pandas", "IPython", "pytest",
            "pip", "venv", "ensurepip", "idlelib", "lib2to3", "pydoc_data",
            "src.verify.runner.devplaces", "src.verify.runner.tests", "src.verify.runner.selftest",
            "src.verify.runner.app.tests"]
VCRT = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll")
DROP_WORDS = ("directml", "onnxruntime", "opencv_videoio_ffmpeg")
NEVER_FILES = ("onnxruntime", "directml", "pyside6", "shiboken6")
NEVER_MODULES = ("onnxruntime", "pyside6", "shiboken6")
DELVEWHEEL = re.compile(r"^.+-[0-9a-f]{32}\.dll$")


def _dest(entry):
    return str(entry[0]).replace("\\", "/")


def _low(entry):
    return _dest(entry).lower()


def _base(dest):
    return dest.rsplit("/", 1)[-1]


def _folder(dest):
    return dest.rsplit("/", 1)[0] if "/" in dest else ""


def drop_data(dest):
    """R2.4.5 DROP_DATA: import libraries, headers, include folders and cmake files never ship."""
    d = "/" + str(dest).replace("\\", "/").lower()
    return d.endswith((".lib", ".h", ".hpp", ".cuh")) or "/include/" in d or "/share/cmake/" in d


def stage_modules(stage):
    """The dotted names of every staged .py under stage/src (the src.* part of STAGE_LIST.txt)."""
    out = []
    for p in sorted(Path(stage, "src").rglob("*.py")):
        parts = list(p.relative_to(stage).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out.append(".".join(parts))
    return out


def torch_lib_dir():
    """torch\\lib of the build env (found without importing torch); HDC_TORCH_LIB overrides it (unit tests)."""
    env = os.environ.get("HDC_TORCH_LIB")
    if env:
        return Path(env)
    import importlib.util
    spec = importlib.util.find_spec("torch")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("hdc.spec: torch is not in the build env")
    return Path(list(spec.submodule_search_locations)[0]) / "lib"


def our_runtime(stage):
    """{dest (lower, "/"): our staged source} of the VC++ DLLs: the four at the top, msvcr120 next to libzbar."""
    out = {n: Path(stage, "vcrt", n) for n in VCRT}
    out["pyzbar/msvcr120.dll"] = Path(stage, "vcrt", "msvcr120.dll")
    return out


def _src_key(entry):
    return os.path.normcase(os.path.abspath(str(entry[1])))


def drop_top_level_copies(binaries):
    """(binaries, dropped dests): a TOP-LEVEL entry whose source file is also collected into a sub-folder is dropped.
    PyInstaller 6 keeps a dependency's package folder only for DLLs under site.getsitepackages(); the build env's
    packages come through hdc_build.pth (R2.4.3), so its dependency scan collects torch\\lib, numpy.libs,
    scipy.libs and the vendored pyzbar DLLs a second time at the top level, next to the copy in their own folder.
    The packages load them from their own folders (torch adds torch\\lib and loads every DLL in it by full path,
    numpy and scipy add their *.libs folder, pyzbar falls back to its own folder), as in DEV. A top-level DLL from
    ANOTHER source with the same name is not touched: check_toc still stops the build on it."""
    in_folder = {_src_key(e) for e in binaries if "/" in _dest(e)}
    keep, dropped = [], []
    for e in binaries:
        if "/" not in _dest(e) and _src_key(e) in in_folder:
            dropped.append(_dest(e))
        else:
            keep.append(e)
    return keep, dropped


def filter_toc(binaries, datas, stage):
    """(binaries, datas, dropped dests): DROP_WORDS and DROP_DATA removed; every VC++ runtime copy that is not
    ours removed and ours put back where it belongs (our copies win, r1 6.2). Top-level copies of a file that is
    also collected into a package folder are dropped last (drop_top_level_copies)."""
    ours = our_runtime(stage)
    runtime_names = {_base(k) for k in ours}
    keep_b, keep_d, dropped = [], [], []
    for e in binaries:
        d = _low(e)
        src = str(e[1]).replace("\\", "/").lower()
        if any(w in d or w in src for w in DROP_WORDS) or drop_data(d):
            dropped.append(_dest(e))
            continue
        if _base(d) in runtime_names:
            mine = ours.get(d)
            if mine is None or os.path.normcase(os.path.abspath(str(e[1]))) != \
                    os.path.normcase(os.path.abspath(str(mine))):
                dropped.append(_dest(e))
                continue
        keep_b.append(e)
    have = {_low(e) for e in keep_b}
    for dest, src in ours.items():
        if dest not in have:
            keep_b.append((dest.replace("/", os.sep), str(src), "BINARY"))
    for e in datas:
        d = _low(e)
        if drop_data(d) or any(w in d for w in DROP_WORDS):
            dropped.append(_dest(e))
            continue
        keep_d.append(e)
    keep_b, copies = drop_top_level_copies(keep_b)
    return keep_b, keep_d, dropped + copies


def check_toc(pure, binaries, datas, stage, torch_lib):
    """The after-Analysis assertions. Returns (problems, facts); any problem stops the build."""
    problems, facts = [], {}
    by_base = {}
    for e in list(binaries) + list(datas):
        d = _low(e)
        if d.endswith(".dll"):
            by_base.setdefault(_base(d), []).append(_dest(e))
    allowed = {}
    for base, dests in sorted(by_base.items()):
        if len(dests) < 2:
            continue
        if DELVEWHEEL.match(base) and all(_folder(x.lower()).endswith(".libs") for x in dests):
            allowed[base] = dests
        else:
            problems.append(f"DLL collected twice: {base} ({', '.join(sorted(dests))})")
    facts["delvewheel_pairs"] = allowed

    want = {p.name.lower() for p in Path(torch_lib).glob("*.dll")}
    got = {_base(_low(e)) for e in binaries if _folder(_low(e)) == "torch/lib" and _low(e).endswith(".dll")}
    facts["torch_lib_dlls"] = len(got)
    if not want:
        problems.append("no torch\\lib\\*.dll found in the build env")
    elif want != got:
        problems.append(f"torch/lib DLLs differ from the build env: missing {sorted(want - got)}, "
                        f"extra {sorted(got - want)}")

    for e in pure:
        m = str(e[0]).lower()
        if any(m == n or m.startswith(n + ".") for n in NEVER_MODULES):
            problems.append(f"excluded module collected: {e[0]}")
    for e in list(binaries) + list(datas):
        if any(w in _low(e) for w in NEVER_FILES):
            problems.append(f"excluded file collected: {_dest(e)}")

    got_src = {str(e[0]) for e in pure if str(e[0]) == "src" or str(e[0]).startswith("src.")}
    want_src = set(stage_modules(stage))
    if got_src != want_src:
        problems.append(f"src.* modules differ from the stage: missing {sorted(want_src - got_src)[:10]}, "
                        f"extra {sorted(got_src - want_src)[:10]}")
    facts["src_modules"] = len(got_src)

    for dest, src in our_runtime(stage).items():
        hits = [e for e in binaries if _low(e) == dest]
        if len(hits) != 1 or os.path.normcase(os.path.abspath(str(hits[0][1]))) != \
                os.path.normcase(os.path.abspath(str(src))):
            problems.append(f"the VC++ runtime {dest} is not exactly our staged copy")
    return problems, facts


def _report(name, data):
    if REPORTS:
        Path(REPORTS).mkdir(parents=True, exist_ok=True)
        Path(REPORTS, name).write_text(json.dumps(data, indent=1, ensure_ascii=True), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------
sys.path[:0] = [str(STAGE), str(STAGE / "vendor")]          # collect_submodules must find the staged code

hidden = list(collect_submodules("src.verify.runner", filter=lambda m: not m.startswith(SKIP)))
hidden += [m for m in stage_modules(STAGE) if m not in hidden]    # pipeline imports stages by name
hidden += ["src.verify.runner.__main__", "glue_numpy", "easyocr_vendor", "pyzbar.pyzbar", "easyocr.easyocr"]
hidden = [m for m in dict.fromkeys(hidden) if not m.startswith(SKIP)]

binaries = [(str(STAGE / "vcrt" / n), ".") for n in VCRT] + [
    (str(STAGE / "vendor" / "pyzbar" / "libzbar-64.dll"), "pyzbar"),
    (str(STAGE / "vendor" / "pyzbar" / "libiconv.dll"), "pyzbar"),
    (str(STAGE / "vcrt" / "msvcr120.dll"), "pyzbar")]
datas = [(str(STAGE / "engine" / "easyocr" / "model" / "craft_mlt_25k.pth"), "engine/easyocr/model"),
         (str(STAGE / "engine" / "easyocr" / "model" / "english_g2.pth"), "engine/easyocr/model"),
         (str(STAGE / "engine" / "easyocr" / "user_network" / ".keep"), "engine/easyocr/user_network"),
         (str(STAGE / "engine" / "glue_data.json"), "engine"),        # no engine/models/*.onnx any more
         (str(STAGE / "selftest"), "selftest"),
         (str(STAGE / "BUILD.json"), "."),
         (str(STAGE / "vendor" / "pyzbar" / "zbar-LICENSE.txt"), "pyzbar")]

a = Analysis([str(STAGE / "hdc_entry.py")],
             pathex=[str(STAGE), str(STAGE / "vendor")],
             binaries=binaries,
             datas=datas,
             hiddenimports=hidden,
             hookspath=[],
             hooksconfig={"easyocr": {"lang_codes": ["en"]}},
             runtime_hooks=[],
             excludes=EXCLUDES,
             noarchive=False,
             optimize=0)

a.binaries, a.datas, _dropped = filter_toc(a.binaries, a.datas, STAGE)
_problems, _facts = check_toc(a.pure, a.binaries, a.datas, STAGE, torch_lib_dir())
_facts.update({"binaries": len(a.binaries), "datas": len(a.datas), "pure": len(a.pure), "dropped": sorted(_dropped),
               "problems": _problems})
_report("toc_checks.json", _facts)
print(f"hdc.spec: {len(a.binaries)} binaries, {len(a.datas)} datas, {len(a.pure)} modules; "
      f"{len(_dropped)} dropped; {len(_facts['delvewheel_pairs'])} delvewheel pair(s)")
if _problems:
    raise SystemExit("hdc.spec: the build stops:\n  " + "\n  ".join(_problems))

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, disable_windowed_traceback=False, uac_admin=False,
          icon=str(STAGE / "icon.ico"), version=str(STAGE / "version_info.txt"), contents_directory="_internal")
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name=NAME)
