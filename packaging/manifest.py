"""MANIFEST.json of the frozen app folder (APP_SPEC r1 1.1, r2 G1): size and sha256 of every file.

The manifest lives at `<app folder>\\_internal\\MANIFEST.json` and lists every file of the app folder (the folder that
holds `Hangeul Document Checker.exe`) except itself. Paths are relative to the app folder, with "/" separators.

    {"schema": "hdc-manifest/1", "build_id": "...", "app_version": "1.0.1", "count": N, "bytes": B,
     "files": {"Hangeul Document Checker.exe": {"size": 123, "sha256": "<64 hex>"},
               "_internal/BUILD.json": {...}, ...}}

The self-test's `integrity` part, the window's quick size check and the install test (P3) read it; `load()` and
`verify()` below are the reference reader.

    python -I -B manifest.py write  <app folder> --build-id ID
    python -I -B manifest.py verify <app folder> [--sizes-only]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

SCHEMA = "hdc-manifest/1"
REL_PATH = "_internal/MANIFEST.json"
CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _is_link(p: Path) -> bool:
    try:
        return p.is_symlink() or p.is_junction()
    except OSError:
        return True


def iter_files(app_folder: Path) -> Iterator[Tuple[str, Path]]:
    """(relative posix path, path) of every regular file, sorted; refuses links and junctions."""
    root = Path(app_folder)
    if not root.is_dir() or _is_link(root):
        raise ValueError("the app folder is missing or is a link")
    found: List[Tuple[str, Path]] = []
    for dp, dns, fns in os.walk(root):
        for d in dns:
            if _is_link(Path(dp) / d):
                rel = Path(dp, d).relative_to(root).as_posix()
                raise ValueError(f"a link or junction inside the app folder: {rel}")
        dns.sort()
        for fn in fns:
            p = Path(dp) / fn
            if _is_link(p) or not p.is_file():
                raise ValueError(f"not a regular file: {p.relative_to(root).as_posix()}")
            found.append((p.relative_to(root).as_posix(), p))
    found.sort(key=lambda t: t[0].lower())
    yield from found


def build(app_folder: Path, *, build_id: str, app_version: str) -> Dict[str, Any]:
    files: Dict[str, Dict[str, Any]] = {}
    total = 0
    for rel, p in iter_files(app_folder):
        if rel.lower() == REL_PATH.lower():
            continue
        size = p.stat().st_size
        files[rel] = {"size": size, "sha256": sha256_file(p)}
        total += size
    return {"schema": SCHEMA, "build_id": build_id, "app_version": app_version, "count": len(files),
            "bytes": total, "files": files}


def write(app_folder: Path, *, build_id: str, app_version: str) -> Dict[str, Any]:
    """Builds the manifest and writes it atomically to <app folder>\\_internal\\MANIFEST.json."""
    data = build(app_folder, build_id=build_id, app_version=app_version)
    out = Path(app_folder) / REL_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=True), encoding="utf-8")
    os.replace(tmp, out)
    return data


def load(path: Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA or not isinstance(data.get("files"), dict):
        raise ValueError("not an hdc manifest")
    return data


def verify(app_folder: Path, manifest: Dict[str, Any], *, full: bool = True,
           allow_extra: bool = False) -> List[str]:
    """Problems as short lines ("missing <rel>", "size <rel>", "changed <rel>", "extra <rel>"); [] when it matches.
    full=False compares sizes only (the window's instant check)."""
    problems: List[str] = []
    want = manifest["files"]
    have = {rel: p for rel, p in iter_files(app_folder) if rel.lower() != REL_PATH.lower()}
    have_lower = {rel.lower(): rel for rel in have}
    for rel in sorted(want, key=str.lower):
        real = have_lower.get(rel.lower())
        if real is None:
            problems.append(f"missing {rel}")
            continue
        p = have[real]
        if p.stat().st_size != want[rel]["size"]:
            problems.append(f"size {rel}")
        elif full and sha256_file(p) != want[rel]["sha256"]:
            problems.append(f"changed {rel}")
    if not allow_extra:
        want_lower = {rel.lower() for rel in want}
        problems += [f"extra {rel}" for rel in sorted(have, key=str.lower) if rel.lower() not in want_lower]
    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="manifest.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("app_folder")
    w.add_argument("--build-id", required=True)
    w.add_argument("--app-version", default="1.0.1")
    v = sub.add_parser("verify")
    v.add_argument("app_folder")
    v.add_argument("--sizes-only", action="store_true")
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    if a.cmd == "write":
        d = write(Path(a.app_folder), build_id=a.build_id, app_version=a.app_version)
        print(f"MANIFEST.json: {d['count']} files, {d['bytes']} bytes")
        return 0
    problems = verify(Path(a.app_folder), load(Path(a.app_folder) / REL_PATH), full=not a.sizes_only)
    for line in problems:
        print(line)
    print("manifest OK" if not problems else f"manifest: {len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
