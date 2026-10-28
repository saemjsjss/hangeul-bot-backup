"""Where the runner reads and writes: the two path modes (APP_SPEC rev 1 section 4, rev 2 R2.1.6).

    DEV  this PC, today's folders and guards, value for value (the literals live in devplaces.py);
    APP  the installed app: writes only under DATA (%LOCALAPPDATA%\\Hangeul Document Checker) and the report folder
         the user picked; code, weights and glue data come from the bundle (sys._MEIPASS).

The mode is APP when sys.frozen is set (the PyInstaller build) or HDC_MODE=app (unit tests); otherwise DEV. It is
decided once, at the first current() call, before any runner module binds its constants (botimport, safety and engine
read current() at import), and never changes in a process. Test-only overrides (ignored in DEV): HDC_DATA_DIR (DATA);
HDC_BUNDLE_DIR and HDC_CODE_ROOT (only when not frozen: a frozen app always uses its own bundle).

This module holds no path of this PC: devplaces.py does, and it is imported only in DEV. Standard library only (the
GPU worker imports safety, and so this module, in the bot venv). No personal data.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

DEV, APP = "dev", "app"
APP_NAME = "Hangeul Document Checker"            # the folder under %LOCALAPPDATA%
MODE_ENV = "HDC_MODE"
DATA_ENV = "HDC_DATA_DIR"
BUNDLE_ENV = "HDC_BUNDLE_DIR"
CODE_ENV = "HDC_CODE_ROOT"
APP_WRITE_LABEL = "the app's data folder"
APP_NO_OPEN_LABEL = "protected folders"
OUT_UNDER_WRITE_ROOT = "under-write-root"        # DEV: --out strictly under the write root (as today)
OUT_APP = "app"                                  # APP: any folder the user picked, under rev 1 section 4.2's rules


@dataclass(frozen=True)
class Places:
    """The folders and message words of one mode. DEV values equal today's constants exactly."""
    mode: str                                  # DEV | APP
    write_root: Path                           # DEV: the private root; APP: DATA
    cache_root: Path                           # write_root\runner-data (DEV) | DATA\cache (APP)
    temp_dir: Path                             # write_root\tmp (DEV) | DATA\tmp (APP)
    models_dir: Path                           # DEV: the lab's ONNX models; APP: <bundle>\engine\easyocr\model (.pth)
    glue_data: Path                            # DEV: the lab's glue_data.json; APP: <bundle>\engine\glue_data.json
    code_root: Path                            # where `src` must be imported from: the worktree | the bundle
    forbidden: Tuple[Path, ...]                # never written, never inside an out folder (DEV: bot, verified docs)
    no_install: Tuple[Path, ...]               # never written either (DEV: the numpy venv)
    sibling_root: Optional[Path]               # DEV: the "<root>\* STUDENT DOCUMENTS" sibling rule's root; APP: None
    no_open_root: Optional[Path]               # DEV: the bot folder (nothing opened there); APP: None
    write_label: str                           # message words for the write root
    no_open_label: str                         # message words for the no-open root
    out_rule: str                              # OUT_UNDER_WRITE_ROOT | OUT_APP
    bundle: Optional[Path] = None              # APP: the bundle folder; DEV: None


_CURRENT: Optional[Places] = None


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def mode() -> str:
    """APP when frozen or HDC_MODE=app (any case); otherwise DEV."""
    if frozen():
        return APP
    return APP if os.environ.get(MODE_ENV, "").strip().lower() == APP else DEV


def _source_root() -> Path:
    """The folder that holds this `src` package (the worktree when run from source, the bundle when frozen)."""
    return Path(os.path.abspath(__file__)).parents[3]


def app_data_dir() -> Path:
    """DATA: HDC_DATA_DIR when set (made absolute), else %LOCALAPPDATA%\\Hangeul Document Checker."""
    v = os.environ.get(DATA_ENV, "").strip()
    if v:
        return Path(os.path.abspath(v))
    base = os.environ.get("LOCALAPPDATA", "").strip() or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return Path(os.path.abspath(base)) / APP_NAME


def bundle_dir() -> Path:
    """The bundle: sys._MEIPASS when frozen; else HDC_BUNDLE_DIR (tests); else the folder holding `src`."""
    if frozen():
        return Path(os.path.abspath(getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)))
    v = os.environ.get(BUNDLE_ENV, "").strip()
    return Path(os.path.abspath(v)) if v else _source_root()


def app_install_dir() -> Path:
    """The app's own folder (never written): the exe's folder when frozen; the bundle otherwise (tests)."""
    if frozen():
        return Path(os.path.abspath(os.path.dirname(sys.executable)))
    return bundle_dir()


def _code_root(bundle: Path) -> Path:
    if not frozen():
        v = os.environ.get(CODE_ENV, "").strip()
        if v:
            return Path(os.path.abspath(v))
    return bundle


def _dev() -> Places:
    from . import devplaces as D
    return Places(mode=DEV, write_root=D.PRIVATE, cache_root=D.PRIVATE / "runner-data", temp_dir=D.PRIVATE / "tmp",
                  models_dir=D.ONNX_LAB / "models", glue_data=D.ONNX_LAB / "pkg" / "glue_data.json",
                  code_root=D.WORKTREE, forbidden=(D.BOT, D.VERIFIED), no_install=(D.NUMPY_VENV,),
                  sibling_root=D.HANGEUL, no_open_root=D.BOT, write_label=D.WRITE_LABEL,
                  no_open_label=D.NO_OPEN_LABEL, out_rule=OUT_UNDER_WRITE_ROOT, bundle=None)


def _app() -> Places:
    data = app_data_dir()
    bundle = bundle_dir()
    engine_dir = bundle / "engine"
    return Places(mode=APP, write_root=data, cache_root=data / "cache", temp_dir=data / "tmp",
                  models_dir=engine_dir / "easyocr" / "model", glue_data=engine_dir / "glue_data.json",
                  code_root=_code_root(bundle), forbidden=(), no_install=(), sibling_root=None, no_open_root=None,
                  write_label=APP_WRITE_LABEL, no_open_label=APP_NO_OPEN_LABEL, out_rule=OUT_APP, bundle=bundle)


def current() -> Places:
    """The Places of this process: decided at the first call, then cached (a frozen dataclass)."""
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = _app() if mode() == APP else _dev()
    return _CURRENT


def is_app() -> bool:
    return current().mode == APP
