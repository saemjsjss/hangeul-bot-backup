"""recent.json: the window's last 15 checks (APP_SPEC rev 1 section 3.2).

<DATA>\\recent.json, schema "hdc-recent/1": {"schema": ..., "items": [newest first]}, each item
    time     local time of the check, "YYYY-MM-DDTHH:MM"
    input    the input's BASE name (never its folder)
    program  KLP | EAP | BACHELOR | MASTER
    overall  PASS | FLAG | FAIL | STOP | NOT JUDGED
    code     the result code "XXXX-XXXX" ("" when none)
    folder   the case folder (absolute; double-click opens it)
A damaged file reads as an empty list. Writes are atomic. Standard library only.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional, Union

SCHEMA = "hdc-recent/1"
FILE_NAME = "recent.json"
KEEP = 15
OVERALLS = ("PASS", "FLAG", "FAIL", "STOP", "NOT JUDGED")
PROGRAMS = ("KLP", "EAP", "BACHELOR", "MASTER")
_CODE = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_PROGRAM_LABELS = {"BACHELOR": "Bachelor's", "MASTER": "Master's"}


@dataclass(frozen=True)
class RecentItem:
    time: str
    input: str
    program: str
    overall: str
    code: str
    folder: str


def base_name(p: Any) -> str:
    """The last part of a path, for the "input" field (never a folder)."""
    s = str(p or "").rstrip("\\/")
    parts = re.split(r"[\\/]", s)
    name = parts[-1] if parts else ""
    return "" if re.match(r"^[A-Za-z]:$", name) else name


def stamp(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M")


def make(when: dt.datetime, input_path: Any, program: str, overall: str, code: Optional[str],
         folder: Union[str, Path]) -> RecentItem:
    return RecentItem(time=stamp(when), input=base_name(input_path)[:200], program=str(program),
                      overall=str(overall), code=str(code or ""), folder=str(folder))


def _valid(d: Any) -> Optional[RecentItem]:
    if not isinstance(d, dict):
        return None
    try:
        item = RecentItem(time=str(d["time"]), input=base_name(d["input"])[:200], program=str(d["program"]),
                          overall=str(d["overall"]), code=str(d.get("code") or ""), folder=str(d["folder"]))
    except (KeyError, TypeError):
        return None
    if not _TIME.match(item.time) or item.program not in PROGRAMS or item.overall not in OVERALLS:
        return None
    if item.code and not _CODE.match(item.code):
        return None
    f = item.folder
    if not f or "\x00" in f or not (re.match(r"^[A-Za-z]:[\\/]", f) or f.startswith(("\\\\", "//"))):
        return None
    return item


def path_of(data_dir: Union[str, Path]) -> Path:
    return Path(data_dir) / FILE_NAME


def load(data_dir: Union[str, Path]) -> List[RecentItem]:
    """The saved list, newest first; bad items are skipped; at most KEEP."""
    try:
        raw = json.loads(path_of(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("items") if isinstance(raw, dict) and raw.get("schema") == SCHEMA else None
    if not isinstance(items, list):
        return []
    out: List[RecentItem] = []
    for d in items:
        it = _valid(d)
        if it is not None:
            out.append(it)
        if len(out) >= KEEP:
            break
    return out


def save(data_dir: Union[str, Path], items: Iterable[RecentItem]) -> Path:
    p = path_of(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    data = {"schema": SCHEMA, "items": [asdict(i) for i in list(items)[:KEEP]]}
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, p)
    return p


def add(items: Iterable[RecentItem], item: RecentItem) -> List[RecentItem]:
    """`item` first, then the others without an older entry for the same case folder; at most KEEP."""
    key = os.path.normcase(os.path.normpath(item.folder))
    rest = [i for i in items if os.path.normcase(os.path.normpath(i.folder)) != key]
    return ([item] + rest)[:KEEP]


def remove(items: Iterable[RecentItem], index: int) -> List[RecentItem]:
    lst = list(items)
    if 0 <= index < len(lst):
        del lst[index]
    return lst


def display(item: RecentItem) -> str:
    """"28 Oct 14:12  CHECK_0001.pdf  KLP  FAIL  3F2A-91C0"."""
    try:
        t = dt.datetime.strptime(item.time, "%Y-%m-%dT%H:%M")
        when = f"{t.day} {_MONTHS[t.month - 1]} {t:%H:%M}"
    except ValueError:
        when = item.time
    parts = [when, item.input, _PROGRAM_LABELS.get(item.program, item.program), item.overall]
    if item.code:
        parts.append(item.code)
    return "  ".join(parts)
