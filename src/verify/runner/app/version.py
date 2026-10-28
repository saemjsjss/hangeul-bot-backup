"""The app's version facts (APP_SPEC rev 1 section 6.3, revision 2 R2.4.4 and R2.5 "Version stamp").

Frozen, everything comes from ``<bundle>\\BUILD.json`` (``sys._MEIPASS``), written by the build from the staged
``model.py`` and the build environment, so it cannot drift from the frozen code. Unfrozen (DEV and the unit tests),
``HDC_BUNDLE_DIR\\BUILD.json`` is read when it exists, else the DEV defaults below are used. Importing this module
never fails and never loads numpy or torch: a missing or damaged BUILD.json gives the defaults plus a line in
``PROBLEMS`` (frozen: ``BUILD_ID`` "unknown").

Exposed: APP_VERSION, BUILD_ID, RUNNER_VERSION, BASE_RULES_VERSION, RUNNER_RULES_VERSION, SPEC, BASELINE, BUILT,
FLAVOUR, TORCH, TORCHVISION, EASYOCR, CUDA, CUDNN, ARCH_FLAGS (a tuple such as ("sm_75", ..., "sm_120")),
CUDA_DRIVER_MIN, CAP_GB, NEED_FREE_MB, MIN_TOTAL_MB, MODELS, BUILD (the whole BUILD.json dict), SOURCE ("bundle",
"bundle-dir", "dev" or "missing"), PROBLEMS, FROZEN. No personal data; no path is exposed.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .. import model as _model

BUILD_FILE = "BUILD.json"
BUNDLE_ENV = "HDC_BUNDLE_DIR"
APP_NAME = "Hangeul Document Checker"

# DEV defaults (R2.0 G11, R2.3.1, R2.4.4; the lab engine's versions read on 29 Oct)
DEV_DEFAULTS: Dict[str, Any] = {
    "app_version": "1.0.0",
    "build_id": "dev",
    "flavour": "gpu-cu128",
    "runner_version": _model.RUNNER_VERSION,
    "base_rules_version": _model.BASE_RULES_VERSION,
    "runner_rules_version": _model.RUNNER_RULES_VERSION,
    "spec": "RUNNER_SPEC r2.7 / APP_SPEC r2",
    "baseline": "r8 (runner 0.2.7, 344b342)",
    "built": None,
    "torch": "2.11.0+cu128",
    "torchvision": "0.26.0+cu128",
    "easyocr": "1.7.2",
    "cuda": "12.8",
    "cudnn": "9.19.0",
    "arch_flags": ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"],
    "cuda_driver_min": 12080,
    "cap_gb": 3.0,
    "need_free_mb": 4096,
    "min_total_mb": 5800,
    "models": {},
}

_STR_KEYS = ("app_version", "build_id", "flavour", "runner_version", "base_rules_version", "runner_rules_version",
             "spec", "baseline", "torch", "torchvision", "easyocr", "cuda", "cudnn")
_INT_KEYS = ("cuda_driver_min", "need_free_mb", "min_total_mb")
_MODEL_CHECKED = ("runner_version", "base_rules_version", "runner_rules_version")


@dataclass(frozen=True)
class BuildInfo:
    values: Dict[str, Any]
    source: str                                  # "bundle", "bundle-dir", "dev", "missing"
    problems: Tuple[str, ...] = field(default_factory=tuple)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]


def _arch_tuple(v: Any) -> Optional[Tuple[str, ...]]:
    if isinstance(v, str):
        parts = v.replace(",", " ").split()
    elif isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
        parts = [x.strip() for x in v if x.strip()]
    else:
        return None
    return tuple(parts) if parts else None


def _clean(raw: Mapping[str, Any], frozen: bool) -> Tuple[Dict[str, Any], List[str]]:
    """BUILD.json values over the DEV defaults, type-checked; a bad value keeps the default and adds a problem."""
    out = dict(DEV_DEFAULTS)
    out["arch_flags"] = tuple(DEV_DEFAULTS["arch_flags"])
    problems: List[str] = []
    for k, v in raw.items():
        out.setdefault(k, v)                     # unknown keys are kept as they are (BUILD carries them)
    for k in _STR_KEYS:
        if k in raw:
            if isinstance(raw[k], str) and raw[k].strip():
                out[k] = raw[k].strip()
            else:
                problems.append(f"BUILD.json {k} is not a text")
                out[k] = DEV_DEFAULTS[k]
    for k in _INT_KEYS:
        if k in raw:
            v = raw[k]
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                out[k] = v
            else:
                problems.append(f"BUILD.json {k} is not a positive integer")
                out[k] = DEV_DEFAULTS[k]
    if "cap_gb" in raw:
        v = raw["cap_gb"]
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out["cap_gb"] = float(v)
        else:
            problems.append("BUILD.json cap_gb is not a positive number")
            out["cap_gb"] = DEV_DEFAULTS["cap_gb"]
    if "arch_flags" in raw:
        a = _arch_tuple(raw["arch_flags"])
        if a is None:
            problems.append("BUILD.json arch_flags is not a list of flags")
            out["arch_flags"] = tuple(DEV_DEFAULTS["arch_flags"])
        else:
            out["arch_flags"] = a
    if "models" in raw:
        if isinstance(raw["models"], dict):
            out["models"] = dict(raw["models"])
        else:
            problems.append("BUILD.json models is not an object")
            out["models"] = {}
    if "built" in raw:
        out["built"] = None if raw["built"] is None else str(raw["built"])
    if frozen:
        for k in ("app_version", "build_id"):
            if k not in raw:
                problems.append(f"BUILD.json has no {k}")
                if k == "build_id":
                    out[k] = "unknown"
    # The rules and runner versions must be the frozen code's own (BUILD.json is written from the staged model.py)
    for k in _MODEL_CHECKED:
        if k in raw and out[k] != DEV_DEFAULTS[k]:
            problems.append(f"BUILD.json {k} differs from the bundled runner")
    return out, problems


def build_json_path(*, frozen: Optional[bool] = None, meipass: Optional[str] = None,
                    environ: Optional[Mapping[str, str]] = None) -> Tuple[Optional[str], str]:
    """(path or None, source): frozen -> <_MEIPASS>\\BUILD.json ("bundle"); else HDC_BUNDLE_DIR\\BUILD.json
    ("bundle-dir") when the variable is set; else (None, "dev")."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    env = os.environ if environ is None else environ
    if frozen:
        base = meipass if meipass is not None else getattr(sys, "_MEIPASS", None)
        if not base:
            base = os.path.dirname(os.path.abspath(sys.executable))
        return os.path.join(base, BUILD_FILE), "bundle"
    bdir = env.get(BUNDLE_ENV)
    if bdir:
        return os.path.join(bdir, BUILD_FILE), "bundle-dir"
    return None, "dev"


def load(path: Optional[str] = None, *, frozen: Optional[bool] = None, meipass: Optional[str] = None,
         environ: Optional[Mapping[str, str]] = None) -> BuildInfo:
    """The build facts. With ``path`` that file is read as a bundle's BUILD.json; else ``build_json_path``.
    Never raises."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if path is not None:
        source = "bundle" if is_frozen else "bundle-dir"
    else:
        path, source = build_json_path(frozen=is_frozen, meipass=meipass, environ=environ)
    if path is None:
        vals, _ = _clean({}, False)
        return BuildInfo(vals, "dev", ())
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("not an object")
    except FileNotFoundError:
        if not is_frozen:                        # a test bundle without BUILD.json: DEV defaults
            vals, _ = _clean({}, False)
            return BuildInfo(vals, "dev", ())
        vals, _ = _clean({}, False)
        vals["build_id"] = "unknown"
        return BuildInfo(vals, "missing", ("BUILD.json is missing",))
    except (OSError, ValueError) as e:
        vals, _ = _clean({}, False)
        if is_frozen:
            vals["build_id"] = "unknown"
        return BuildInfo(vals, "missing", (f"BUILD.json cannot be read ({type(e).__name__})",))
    vals, problems = _clean(raw, is_frozen)
    return BuildInfo(vals, source, tuple(problems))


def arch_range(arch_flags: Any = None) -> Tuple[str, str]:
    """("7.5", "12.0") for ("sm_75", ..., "sm_120"): the lowest and highest compute capability of the build."""
    flags = _arch_tuple(ARCH_FLAGS if arch_flags is None else arch_flags) or ()
    ccs = []
    for f in flags:
        kind, _, num = f.partition("_")
        digits = "".join(ch for ch in num if ch.isdigit())
        if kind in ("sm", "compute") and len(digits) >= 2:
            ccs.append((int(digits[:-1]), int(digits[-1])))
    if not ccs:
        return ("?", "?")
    lo, hi = min(ccs), max(ccs)
    return (f"{lo[0]}.{lo[1]}", f"{hi[0]}.{hi[1]}")


def rules_label(info: Optional[BuildInfo] = None) -> str:
    v = (info or CURRENT).values
    return f"bot {v['base_rules_version']} + runner {v['runner_rules_version']}"


def flavour_label(info: Optional[BuildInfo] = None) -> str:
    v = (info or CURRENT).values
    return f"GPU (CUDA {v['cuda']})"


def footer_text(info: Optional[BuildInfo] = None) -> str:
    """The window footer's version part: "App 1.0.0 (build <id>, GPU (CUDA 12.8)) · Rules: bot ... + runner ..."."""
    v = (info or CURRENT).values
    return (f"App {v['app_version']} (build {v['build_id']}, {flavour_label(info)}) · "
            f"Rules: {rules_label(info)}")


def version_line(info: Optional[BuildInfo] = None) -> str:
    """One plain line for SELFTEST.txt, log.txt and checkfacts: app, build, rules, runner."""
    v = (info or CURRENT).values
    return (f"App {v['app_version']}, build {v['build_id']} | rules: {rules_label(info)} | "
            f"runner {v['runner_version']}")


def facts(info: Optional[BuildInfo] = None) -> Dict[str, Any]:
    """The version facts for checkfacts.json and the done event (no paths)."""
    v = (info or CURRENT).values
    keys = ("app_version", "build_id", "flavour", "runner_version", "base_rules_version", "runner_rules_version",
            "spec", "baseline", "torch", "torchvision", "easyocr", "cuda", "cudnn", "cuda_driver_min", "cap_gb",
            "need_free_mb", "min_total_mb")
    out = {k: v.get(k) for k in keys}
    out["arch_flags"] = list(v["arch_flags"])
    return out


CURRENT = load()
FROZEN = bool(getattr(sys, "frozen", False))
BUILD: Dict[str, Any] = dict(CURRENT.values)
SOURCE: str = CURRENT.source
PROBLEMS: Tuple[str, ...] = CURRENT.problems

APP_VERSION: str = BUILD["app_version"]
BUILD_ID: str = BUILD["build_id"]
FLAVOUR: str = BUILD["flavour"]
RUNNER_VERSION: str = BUILD["runner_version"]
BASE_RULES_VERSION: str = BUILD["base_rules_version"]
RUNNER_RULES_VERSION: str = BUILD["runner_rules_version"]
SPEC: str = BUILD["spec"]
BASELINE: str = BUILD["baseline"]
BUILT: Optional[str] = BUILD.get("built")
TORCH: str = BUILD["torch"]
TORCHVISION: str = BUILD["torchvision"]
EASYOCR: str = BUILD["easyocr"]
CUDA: str = BUILD["cuda"]
CUDNN: str = BUILD["cudnn"]
ARCH_FLAGS: Tuple[str, ...] = tuple(BUILD["arch_flags"])
CUDA_DRIVER_MIN: int = int(BUILD["cuda_driver_min"])
CAP_GB: float = float(BUILD["cap_gb"])
NEED_FREE_MB: int = int(BUILD["need_free_mb"])
MIN_TOTAL_MB: int = int(BUILD["min_total_mb"])
MODELS: Dict[str, Any] = dict(BUILD.get("models") or {})
