"""The real corpus for the self-tests (RUNNER_SPEC section 4.4; a port of the lab's order_data.py).

Measurement only, READ-ONLY: it reads the verified student folders (file names, sizes, mtimes) and the
bot's text cache (C:\\Hangeul\\BOT\\data\\verification\\text\\<id>.json) and never writes anything. It
mirrors prototype_page_types.load() and walks the same folders in the same order with the same skips, so
it yields the same student ids (S000 ...) and the same tune / held-out halves (seed 20261027).

As in order_data.py, a student's `files` holds only the files whose name gives a bot document key (the
prototype's "typed" files); check_same_as_prototype() asserts that the counts agree.

It never prints a folder name, file name, cache file name or any text. Callers print counts and sids only.
Run as a module, it prints counts only:

    python -I -B -m src.verify.runner.selftest.corpus
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.verify import prototype_page_types as P

SEED = P.SEED                     # 20261027


@dataclass
class CorpusFile:
    name: str            # kept in memory only, never printed
    path: Path           # the real file (read-only)
    key: Optional[str]   # prototype classify_name()
    truth: Optional[str] # prototype truth_label()
    image: bool
    pages: List[str]     # prototype _pages_for() (bot cache text, 200 dpi pages)
    blob: bool


@dataclass
class CorpusStudent:
    sid: str             # "S000" ...
    program: str         # KLP | EAP | BACHELOR | MASTER (prototype mapping)
    folder: Path         # the real student folder (read-only)
    cache_json: Path     # C:\Hangeul\BOT\data\verification\text\<id>.json (read-only)
    images_json: Path    # ...\images\<id>.json (read-only; may not exist)
    files: List[CorpusFile] = field(default_factory=list)

    def by_key(self) -> Dict[str, CorpusFile]:
        """key -> file (the last file of a key wins, as in order_data.Stu.by_key)."""
        return {f.key: f for f in self.files if f.key}


def program_of(folder_name: str) -> str:
    """The program folder's name -> KLP | EAP | BACHELOR | MASTER (order_data's mapping of the
    prototype's first word)."""
    p = folder_name.split()[0].upper()
    if p.startswith("BACHELOR"):
        return "BACHELOR"
    if p.startswith("MASTER"):
        return "MASTER"
    if p.startswith("KOREAN"):
        return "KLP"
    return p


def load_corpus(docs_root: Path = P.DOCS_ROOT, text_dir: Path = P.TEXT_DIR) -> List[CorpusStudent]:
    """Every student folder with a readable bot text cache, in prototype_page_types.load() order."""
    out: List[CorpusStudent] = []
    images_dir = Path(text_dir).parent / "images"
    for prog in sorted(p for p in Path(docs_root).iterdir() if p.is_dir()):
        for sd in sorted(p for p in prog.iterdir() if p.is_dir()):
            m = re.search(r"\(([^)]+)\)\s*$", sd.name)
            if not m:
                continue
            cid = m.group(1).strip()
            cp = Path(text_dir) / f"{cid}.json"
            if not cp.exists():
                continue
            try:
                cache = json.loads(cp.read_text(encoding="utf-8"))
            except Exception:
                continue
            s = CorpusStudent(sid=f"S{len(out):03d}", program=program_of(prog.name), folder=sd,
                              cache_json=cp, images_json=images_dir / f"{cid}.json")
            for f in sorted(sd.rglob("*")):
                if not f.is_file():
                    continue
                image = f.suffix.lower() in P.IMAGE_SUFFIXES
                pages, blob = P._pages_for(cache, f)
                if pages is None:
                    continue
                key = P.classify_name(f.name)
                if key is None:
                    continue
                s.files.append(CorpusFile(name=f.name, path=f, key=key, truth=P.truth_label(f.name),
                                          image=image, pages=pages, blob=blob))
            out.append(s)
    return out


def halves(students: List[CorpusStudent], seed: int = SEED
           ) -> Tuple[List[CorpusStudent], List[CorpusStudent]]:
    """(tune, heldout): prototype_page_types.split() on the sids, seed 20261027."""
    ids = sorted(s.sid for s in students)
    random.Random(seed).shuffle(ids)
    tune = set(ids[: len(ids) // 2])
    return [s for s in students if s.sid in tune], [s for s in students if s.sid not in tune]


def check_same_as_prototype(students: Optional[List[CorpusStudent]] = None) -> None:
    """The prototype's loader gives the same students: sids, programs and typed-file counts, and the
    same halves. Raises AssertionError (with sids and counts only) on any difference."""
    a = P.load()
    b = load_corpus() if students is None else students
    assert len(a) == len(b), ("students", len(a), len(b))
    for x, y in zip(a, b):
        assert x.sid == y.sid, ("sid", x.sid, y.sid)
        assert program_of(x.program) == y.program, ("program", x.sid)
        assert sum(1 for f in x.files if f.truth) == len(y.files), ("typed files", x.sid)
        assert [f.truth for f in x.files if f.truth] == [f.truth for f in y.files], ("truth order", x.sid)
    pa, pb = P.split(a, SEED), halves(b, SEED)
    assert [s.sid for s in pa[0]] == [s.sid for s in pb[0]], "tune half"
    assert [s.sid for s in pa[1]] == [s.sid for s in pb[1]], "held-out half"


def main() -> None:
    studs = load_corpus()
    tune, held = halves(studs)
    progs: Dict[str, int] = {}
    for s in studs:
        progs[s.program] = progs.get(s.program, 0) + 1
    print(f"students {len(studs)}  files {sum(len(s.files) for s in studs)}  "
          f"blob files {sum(f.blob for s in studs for f in s.files)}  tune {len(tune)}  held-out {len(held)}")
    print("programs " + "  ".join(f"{k} {v}" for k, v in sorted(progs.items())))
    check_same_as_prototype(studs)
    print("check_same_as_prototype ok")


if __name__ == "__main__":
    main()
