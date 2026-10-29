"""Version facts of the app build (APP_SPEC r2 G11; r1 6.1 step 8, 6.3). Build side only; never bundled.

The app itself reads these facts from BUILD.json through `src\\verify\\runner\\app\\version.py` (Task C). This module
makes the build id, the exe's FileVersion and the PyInstaller version resource (`stage\\version_info.txt`).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Dict, Tuple

APP_VERSION = "1.0.1"   # 1.0.1 (29 Oct 2026): APP_SPEC R2.10 I28-I30
FLAVOUR = "gpu-cu128"
FLAVOUR_LABEL = "GPU (CUDA 12.8)"
PRODUCT_NAME = "Hangeul Document Checker"
COMPANY = "Hangeul"
EXE_NAME = PRODUCT_NAME + ".exe"
FILE_DESCRIPTION = PRODUCT_NAME + " (GPU)"

# YYYYMMDD.N-<git short>[-dirty]; N is the build number of that day (1..9999, it becomes the 4th FileVersion part)
BUILD_ID_RE = re.compile(r"^(?P<date>\d{8})\.(?P<n>[1-9]\d{0,3})-(?P<git>[0-9a-f]{7,40})(?P<dirty>-dirty)?$")


def parse_build_id(build_id: str) -> Dict[str, object]:
    """{"date": "YYYYMMDD", "n": N, "git": short, "dirty": bool}; ValueError for anything else."""
    m = BUILD_ID_RE.match(build_id or "")
    if not m:
        raise ValueError(f"not a build id (YYYYMMDD.N-<git short>[-dirty]): {build_id!r}")
    return {"date": m.group("date"), "n": int(m.group("n")), "git": m.group("git"), "dirty": bool(m.group("dirty"))}


def file_version_tuple(build_id: str) -> Tuple[int, int, int, int]:
    major, minor, patch = (int(x) for x in APP_VERSION.split("."))
    return (major, minor, patch, int(parse_build_id(build_id)["n"]))


def file_version(build_id: str) -> str:
    """"1.0.1.N" (the exe's FileVersion and the Setup's /DFileVersion)."""
    return ".".join(str(x) for x in file_version_tuple(build_id))


def git_facts(worktree: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Tuple[str, bool]:
    """(short commit, dirty) from `git rev-parse` and `git status --porcelain`, both read-only."""
    head = run(["git", "-C", str(worktree), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
               timeout=60)
    if head.returncode != 0:
        raise RuntimeError("git rev-parse failed in the worktree")
    short = head.stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", short):
        raise RuntimeError(f"unexpected git short hash {short!r}")
    st = run(["git", "-C", str(worktree), "status", "--porcelain"], capture_output=True, text=True, timeout=120)
    if st.returncode != 0:
        raise RuntimeError("git status failed in the worktree")
    return short, bool(st.stdout.strip())


def next_build_id(builds_root: Path, day: str, git_short: str, dirty: bool) -> str:
    """The next free id of that day: N = 1 + the highest N already used under builds_root (any commit)."""
    if not re.fullmatch(r"\d{8}", day):
        raise ValueError(f"day must be YYYYMMDD: {day!r}")
    used = []
    if Path(builds_root).is_dir():
        for p in Path(builds_root).iterdir():
            try:
                info = parse_build_id(p.name)
            except ValueError:
                continue
            if info["date"] == day:
                used.append(int(info["n"]))
    n = max(used, default=0) + 1
    build_id = f"{day}.{n}-{git_short}{'-dirty' if dirty else ''}"
    parse_build_id(build_id)
    return build_id


def version_info_text(build_id: str, runner_version: str, base_rules_version: str, runner_rules_version: str) -> str:
    """PyInstaller's VSVersionInfo text for `stage\\version_info.txt` (r1 6.1 step 8, "GPU" in FileDescription)."""
    fv = file_version_tuple(build_id)
    pv = tuple(int(x) for x in APP_VERSION.split(".")) + (0,)
    comments = (f"rules {base_rules_version} + {runner_rules_version}; runner {runner_version}; "
                f"{FLAVOUR_LABEL}; build {build_id}")
    strings = [
        ("CompanyName", COMPANY),
        ("FileDescription", FILE_DESCRIPTION),
        ("FileVersion", file_version(build_id)),
        ("InternalName", PRODUCT_NAME),
        ("LegalCopyright", COMPANY),
        ("OriginalFilename", EXE_NAME),
        ("ProductName", PRODUCT_NAME),
        ("ProductVersion", APP_VERSION),
        ("Comments", comments),
    ]
    rows = ",\n".join(f"        StringStruct({k!r}, {v!r})" for k, v in strings)
    return (
        "# UTF-8\n"
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers={fv!r},\n"
        f"    prodvers={pv!r},\n"
        "    mask=0x3f,\n"
        "    flags=0x0,\n"
        "    OS=0x40004,\n"
        "    fileType=0x1,\n"
        "    subtype=0x0,\n"
        "    date=(0, 0)\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('040904B0', [\n"
        f"{rows}\n"
        "      ])\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )
