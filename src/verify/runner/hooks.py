"""Progress and cancel hooks of the runner (APP_SPEC rev 1 sections 2 and 4.3).

The runner calls emit(event, **data) at a few points: "stage" (pipeline._Clock: key, state "start" / "end",
seconds), "file" (engine.read_file: name, pages, ocr), "ocr" (engine.PageLog: phase "start" / "end", seconds,
memo) and "budget" (birth and tin, when a wall-clock budget ran out: where, page). Without a listener emit does
nothing, so DEV runs are unchanged. The app's worker sets LISTENER to its event translator, which is called as
LISTENER(event, **data).

Cancel: the listener raises Cancelled at the next event after the user pressed Cancel. emit lets every exception of
the listener through. Cancelled is a BaseException, so neither the bot's nor the pipeline's `except Exception`
swallows it; the `finally` blocks still run (Engine.case saves the case cache). Standard library only. No personal
data.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

LISTENER: Optional[Callable[..., None]] = None


class Cancelled(BaseException):
    """The user cancelled the check (raised by the listener; never caught by the runner)."""


def emit(event: str, **data: Any) -> None:
    """LISTENER(event, **data) when a listener is set; a no-op otherwise. The listener's exceptions pass through."""
    listener = LISTENER
    if listener is None:
        return
    listener(event, **data)
