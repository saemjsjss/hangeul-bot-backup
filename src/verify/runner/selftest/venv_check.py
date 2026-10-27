"""Self-test: the runner venv (RUNNER_SPEC 2.1, 2.2 and Task 1 acceptance; port of engine\\venv_check.py).

    C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\.venv-runner\\Scripts\\python.exe -I -B -m src.verify.runner.selftest.venv_check

Checks, with the write guard armed (writes only under private-runner, nothing under C:\\Hangeul\\BOT opened,
no network):
  * Python 3.12 from the runner venv, and runner.pth holding exactly the four lines of section 2.1;
  * `src` (and every bot module) imported from the worktree; the bot's writable paths under the cache root;
  * the numpy venv's numpy / pymupdf / cv2 (with QRCodeDetectorAruco) / PIL / onnxruntime, glue_numpy from
    onnx\\pkg; the ONNX sessions on the CPU execution provider ONLY (DirectML is offered, never used);
  * one OCR of a made-up picture through the bot's _ocr_image;
  * no torch, torchvision, easyocr, scipy or skimage loaded;
  * zbar available (vendored pyzbar), openpyxl 3.1.5 and et_xmlfile 2.0.0 importable from engine\\vendor and
    byte-identical to VENDOR.json;
  * no module loaded from C:\\Hangeul\\BOT, no guarded event blocked.
Prints OK or the failed checks; writes selftest\\venv_check\\report.json (no personal data). Exit 0 / 1.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from src.verify.runner import botimport, safety

VENV = botimport.PRIVATE / ".venv-runner"
PTH_LINES = [r"C:\Hangeul\JARVIS\prototype\page-types",
             r"C:\Hangeul\DOCCHECK\lab\onnx\.venv-pkg-numpy\Lib\site-packages",
             r"C:\Hangeul\DOCCHECK\lab\private-runner\engine\vendor",
             r"C:\Hangeul\DOCCHECK\lab\onnx\pkg"]
NUMPY_SITE = Path(PTH_LINES[1])
VENDOR = Path(PTH_LINES[2])
GLUE = Path(PTH_LINES[3])
OUT = botimport.PRIVATE / "selftest" / "venv_check"


def _under(p, root: Path) -> bool:
    try:
        return Path(p).resolve().is_relative_to(root.resolve())
    except (TypeError, ValueError, OSError):
        return False


def tree_sha256(folder: Path):
    files = {}
    for f in sorted(folder.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            files[f.relative_to(folder).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    lines = sorted(f"{rel}\0{sha}\n" for rel, sha in files.items())
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest(), files


def main() -> int:
    state = safety.install_write_guard(block_bot_opens=True)
    checks = {}

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks[name] = {"ok": bool(ok), "detail": detail}

    check("python 3.12", sys.version_info[:2] == (3, 12), sys.version.split()[0])
    check("runner venv", _under(sys.prefix, VENV), sys.prefix)
    pth = Path(sys.prefix) / "Lib" / "site-packages" / "runner.pth"
    lines = [x for x in pth.read_text(encoding="utf-8").splitlines() if x.strip()] if pth.exists() else []
    check("runner.pth", lines == PTH_LINES, f"{len(lines)} line(s)")

    bot = botimport.load()
    import src
    check("src from the worktree", _under(src.__file__, botimport.WORKTREE / "src"), src.__file__)
    paths = [bot.dv.REPORT_DIR, bot.TC.TEXT_DIR, bot.TC.IMAGES_DIR, bot.PC.APOSTILLE_SUBJECTS, *bot.dv.DOCS_ROOTS]
    check("bot paths under the cache root", all(_under(p, bot.cache_root) for p in paths), str(bot.cache_root))
    mods = (bot.C, bot.dv, bot.FC, bot.PC, bot.P, bot.R, bot.TC, bot.OV)
    check("bot modules from the worktree", all(_under(m.__file__, botimport.WORKTREE / "src") for m in mods))
    check("stub src.config", getattr(sys.modules.get("src.config"), "_RUNNER_STUB", False))

    import cv2
    import numpy
    import onnxruntime
    import PIL
    import pymupdf
    for name, mod in (("numpy", numpy), ("pymupdf", pymupdf), ("cv2", cv2), ("PIL", PIL),
                      ("onnxruntime", onnxruntime)):
        check(f"{name} from the numpy venv", _under(mod.__file__, NUMPY_SITE), getattr(mod, "__version__", ""))
    check("cv2 QRCodeDetectorAruco", hasattr(cv2, "QRCodeDetectorAruco"))

    from src.verify.runner import engine
    eng = engine.Engine(botimport.DEFAULT_CACHE_ROOT)
    import glue_numpy
    check("glue_numpy from onnx\\pkg", _under(glue_numpy.__file__, GLUE))
    provs = [eng.reader.inner.det.get_providers(), eng.reader.inner.rec.get_providers()]
    check("ONNX on the CPU provider only", provs == [engine.PROVIDERS, engine.PROVIDERS],
          f"sessions {provs}; available {onnxruntime.get_available_providers()}")
    img = numpy.full((120, 640, 3), 255, numpy.uint8)
    cv2.putText(img, "SAMPLE TEXT", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    text = eng.read_text(img)
    check("one OCR through the bot's _ocr_image", "SAMPLE" in text.upper(), f"{len(text)} chars")
    check("no heavy modules", engine.heavy_modules() == [], ",".join(engine.heavy_modules()))

    zb = bot.PC.zbar_available()
    import pyzbar
    check("zbar available (vendored pyzbar)", bool(zb[0]) and _under(pyzbar.__file__, VENDOR), str(zb[1])[:80])
    import et_xmlfile
    import openpyxl
    check("openpyxl 3.1.5 from engine\\vendor", openpyxl.__version__ == "3.1.5" and _under(openpyxl.__file__, VENDOR))
    check("et_xmlfile from engine\\vendor", _under(et_xmlfile.__file__, VENDOR))
    record = json.loads((VENDOR / "VENDOR.json").read_text(encoding="utf-8"))
    for name in ("openpyxl", "et_xmlfile"):
        tree, files = tree_sha256(VENDOR / name)
        rec = record["packages"][name]
        check(f"{name} matches VENDOR.json", tree == rec["tree_sha256"] and files == rec["file_sha256"],
              f"{len(files)} file(s)")

    from_bot = sorted(n for n, m in list(sys.modules.items())
                      if getattr(m, "__file__", None) and _under(m.__file__, safety.BOT))
    check("nothing imported from C:\\Hangeul\\BOT", not from_bot, ",".join(from_bot[:5]))
    check("no guarded event blocked", not state.blocked and state.bot_opens == 0,
          f"{len(state.blocked)} blocked, {state.bot_opens} bot open(s)")

    failed = [k for k, v in checks.items() if not v["ok"]]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps({"checks": checks, "failed": failed}, indent=1), encoding="utf-8")
    for k, v in checks.items():
        print(f"{'ok  ' if v['ok'] else 'MISS'} {k}" + (f"  ({v['detail']})" if v["detail"] else ""))
    print("venv_check OK" if not failed else f"venv_check FAILED: {len(failed)} check(s)")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
