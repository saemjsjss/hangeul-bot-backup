"""The bot's checking code, imported from THIS worktree with a stub src.config (RUNNER_SPEC 2.3)."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

WORKTREE = Path(r"C:\Hangeul\JARVIS\prototype\page-types")
PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
DEFAULT_CACHE_ROOT = PRIVATE / "runner-data"
_loaded = None


def load(cache_root: Path = DEFAULT_CACHE_ROOT) -> SimpleNamespace:
    """SimpleNamespace(dv, PC, TC, R, C, FC, OV, P, cache_root). Idempotent. A second call with another
    cache_root raises. Every folder the bot code can write to lies under cache_root, which must be
    under PRIVATE."""
    global _loaded
    cache_root = Path(cache_root).resolve()
    if _loaded is not None:
        if _loaded.cache_root != cache_root:
            raise RuntimeError("bot code already loaded with another cache root")
        return _loaded
    if not cache_root.is_relative_to(PRIVATE):
        raise RuntimeError(f"cache root must be under {PRIVATE}")
    sys.dont_write_bytecode = True
    import src
    if not Path(src.__file__).resolve().is_relative_to(WORKTREE):
        raise RuntimeError(f"src is imported from {src.__file__}, not from the worktree")
    old = sys.modules.get("src.config")
    if old is not None and not getattr(old, "_RUNNER_STUB", False):
        raise RuntimeError("the real src.config is already imported")
    stub = types.ModuleType("src.config")
    stub._RUNNER_STUB = True

    class _Settings:
        def docs_root(self) -> Path:
            return cache_root / "no-docs-root"

        def konyang_root(self) -> Path:
            return cache_root / "no-docs-root"

        def verification_dir(self) -> Path:
            return cache_root / "verification"

    stub.settings = _Settings()
    sys.modules["src.config"] = stub
    src.config = stub
    from src.verify import common as C
    from src.verify import doc_verifier as dv
    from src.verify import field_check as FC
    from src.verify import page_checks as PC
    from src.verify import prototype_page_types as P
    from src.verify import rules as R
    from src.verify import text_cache as TC
    from src.scraper import ocr_validator as OV
    for p in (dv.REPORT_DIR, TC.TEXT_DIR, TC.IMAGES_DIR, PC.APOSTILLE_SUBJECTS, *dv.DOCS_ROOTS):
        if not Path(p).resolve().is_relative_to(cache_root):
            raise RuntimeError(f"bot path outside the cache root: {p}")
    for m in (C, dv, FC, PC, P, R, TC, OV):
        if not Path(m.__file__).resolve().is_relative_to(WORKTREE / "src"):
            raise RuntimeError(f"{m.__name__} not imported from the worktree: {m.__file__}")
    _loaded = SimpleNamespace(dv=dv, PC=PC, TC=TC, R=R, C=C, FC=FC, OV=OV, P=P, cache_root=cache_root)
    return _loaded
