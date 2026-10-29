"""The card gate: wait politely while another program uses the graphics card (APP_SPEC revision 2, R2.2; G6).

On the owner's PC the Telegram bot's document check starts every 15 minutes and uses the same card; while
it runs, its lock file exists. Before every model call the app asks the gate for its turn. The lock files
are only ``os.stat``'ed (never opened, listed or written), at most every ``CHECK_EVERY_S`` while the card
works. When a lock is active the gate frees this process's card cache (``on_pause``), reports
``gpu_wait`` start / tick / end events through ``emit`` and resumes by itself when the lock is gone.
Waiting never fails a check, and there is no time limit for a lock (a lock older than ``LOCK_STALE_S``
is a leftover of a crashed check and is ignored).

After a lock wait the gate also waits for ROOM (``wait_room``): free card memory of at least what this process may
still take up to its cap, plus headroom (``on_room``, set by ``gpuengine.start_card``). The program that held the
lock may keep its card memory for a while after its lock is gone (the bot's check sends its messages and writes its
reports first). ``wait_room`` never fails a check: after ``MEMORY_WAIT_S`` it gives up and the check goes on (an
out-of-memory error is then handled, counted and reported). GatedReader and TorchSession call it before their
out-of-memory retry too.

Standard library only: no torch and no ``hooks`` import (``emit`` is passed in). A ``Cancelled`` (or any
other exception) raised by ``emit`` or ``on_pause`` passes through untouched; the gate never catches it.

Events (``emit("gpu_wait", **fields)``; R2.3.3):
    state="start", why="lock",   lock="bot"|"other", where=<where>, reserved_mb=<on_pause()>
    state="tick",  why="lock",   lock="bot"|"other", waited_s=<s>                       (every POLL_S)
    state="end",   why="lock",   waited_s=<s>
    state="start", why="memory", free_mb=<MiB>, need_mb=<MiB>
    state="tick",  why="memory", free_mb=<MiB>, need_mb=<MiB>, waited_s=<s>             (every <= POLL_S)
    state="end",   why="memory", free_mb=<MiB>, need_mb=<MiB>, waited_s=<s>   (also just before a timeout;
                   short=True when a room wait gave up)
No event and no stat holds a path. No personal data.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

BOT_LOCK_DEFAULT = r"C:\Hangeul\BOT\data\verification\auto_verify.lock"   # the one C:\Hangeul string in the bundle
LOCK_STALE_S = 4 * 3600      # = gpu_guard.BOT_LOCK_STALE_S: an older lock is a leftover of a crashed check
CHECK_EVERY_S = 0.25         # while the card works, the lock files are stat'ed at most this often
POLL_S = 2.0                 # while waiting
MEMORY_WAIT_S = 15 * 60      # the longest wait for free card memory
MEMORY_POLL_S = 5.0          # while waiting for memory, free memory is read again this often


def default_lock_setting() -> Optional[str]:
    """BOT_LOCK_DEFAULT when its FOLDER exists (``os.path.isdir``: a stat only), else None. The window
    calls it once, at the first start, and stores the answer in settings.json "gpu_lock_path"."""
    folder = os.path.dirname(BOT_LOCK_DEFAULT)
    try:
        return BOT_LOCK_DEFAULT if os.path.isdir(folder) else None
    except (OSError, ValueError):
        return None


def lock_active(path: Optional[str], now: float) -> bool:
    """``os.stat`` only (never open, list or write): the lock exists and ``now - st_mtime < LOCK_STALE_S``.
    ``now`` is wall-clock time (``time.time()``), like the file's mtime. A bad or empty path is False."""
    if not path:
        return False
    try:
        st = os.stat(path)
    except (OSError, ValueError, TypeError):
        return False
    return (float(now) - float(st.st_mtime)) < LOCK_STALE_S


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def lock_kind(path: str) -> str:
    """"bot" when ``path`` is BOT_LOCK_DEFAULT (compared with normcase; no file access), else "other"."""
    try:
        return "bot" if _norm(path) == _norm(BOT_LOCK_DEFAULT) else "other"
    except (TypeError, ValueError):
        return "other"


class CardMemoryShort(Exception):
    """Free card memory stayed under ``need_mb`` for the whole memory wait (the worker reports
    LOW_CARD_MEMORY, exit 6)."""

    def __init__(self, free_mb: Optional[int], need_mb: int, waited_s: float = 0.0):
        self.free_mb = free_mb
        self.need_mb = int(need_mb)
        self.waited_s = float(waited_s)
        super().__init__(f"free card memory {free_mb} MB, {self.need_mb} MB needed (waited {self.waited_s:.0f} s)")


def _no_pause() -> int:
    return 0


def _no_room() -> Optional[int]:
    return None


class CardGate:
    """Ask ``wait_turn(where)`` before every model call (``where``: "start", "page", "read", "det", "rec"
    or "oom"; the worker also asks "stage" at the start of every runner stage) and ``wait_memory(need_mb)``
    once before the card is started. ``wait_room(where)`` waits for free card memory after a lock wait (the
    gate does it itself) and before an out-of-memory retry (the readers do it)."""

    def __init__(self, lock_paths: Sequence[Optional[str]], emit: Callable[..., None], *,
                 free_mb: Callable[[], Optional[int]] = lambda: None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        paths: List[str] = []
        seen = set()
        for p in lock_paths or ():
            if not p:
                continue
            key = _norm(str(p))
            if key not in seen:
                seen.add(key)
                paths.append(str(p))
        self._paths = paths
        self._emit = emit
        self._free_mb = free_mb
        self._clock = clock
        self._wall = wall
        self._sleep = sleep
        #: set by gpuengine.start_card: card.pause (synchronize, gc, empty_cache; returns torch reserved MB)
        self.on_pause: Callable[[], int] = _no_pause
        #: set by gpuengine.start_card: card.room_mb (the free MiB this process needs before it resumes after a
        #: wait: what it may still take up to its cap, plus headroom); the default None = no room check
        self.on_room: Callable[[], Optional[int]] = _no_room
        self._in_memory_wait = False
        self._room_checks = 0
        self._room_short = 0
        self._clear_at: Optional[float] = None     # clock time of the last lock check that found none
        self._checks = 0
        self._waits = 0
        self._waited_s = 0.0
        self._by_why: Dict[str, Dict[str, Any]] = {}
        self._by_where: Dict[str, Dict[str, Any]] = {}
        self._last_free: Optional[int] = None

    # ------------------------------------------------------------------------------------ lock checks
    def _active_lock(self) -> Optional[str]:
        """The first watched lock that is active now, or None. Stat only."""
        if not self._paths:
            return None
        self._checks += 1
        now = self._wall()
        for p in self._paths:
            if lock_active(p, now):
                return p
        return None

    def _record(self, why: str, where: str, seconds: float) -> None:
        self._waits += 1
        self._waited_s += seconds
        for table, key in ((self._by_why, why), (self._by_where, where)):
            row = table.setdefault(key, {"waits": 0, "waited_s": 0.0})
            row["waits"] += 1
            row["waited_s"] = round(row["waited_s"] + seconds, 1)

    def _pause(self) -> int:
        reserved = self.on_pause()
        try:
            return int(reserved or 0)
        except (TypeError, ValueError):
            return 0

    def _wait_lock(self, path: str, where: str) -> float:
        """Pause the card, then wait until no watched lock is active; then wait for room on the card
        (``wait_room``; not inside a memory wait, which reads free memory again itself). Returns the
        seconds of the lock wait."""
        reserved = self._pause()
        start = self._clock()
        try:
            self._emit("gpu_wait", state="start", why="lock", lock=lock_kind(path), where=where,
                       reserved_mb=reserved)
            while True:
                self._sleep(POLL_S)
                active = self._active_lock()
                waited = round(self._clock() - start, 1)
                if active is None:
                    break
                self._emit("gpu_wait", state="tick", why="lock", lock=lock_kind(active), waited_s=waited)
        finally:
            seconds = max(0.0, self._clock() - start)
            self._record("lock", where, seconds)
        self._clear_at = self._clock()
        self._emit("gpu_wait", state="end", why="lock", waited_s=round(seconds, 1))
        if not self._in_memory_wait:
            self.wait_room(where)
        return seconds

    # -------------------------------------------------------------------------------------- the API
    def wait_turn(self, where: str) -> None:
        """Return at once when no watched lock is active (checked at most every CHECK_EVERY_S);
        otherwise pause the card and wait until the lock is gone. No time limit."""
        if self._clear_at is not None and (self._clock() - self._clear_at) < CHECK_EVERY_S:
            return
        active = self._active_lock()
        if active is None:
            if self._paths:
                self._clear_at = self._clock()
            return
        self._wait_lock(active, where)

    def _read_free(self) -> Optional[int]:
        try:
            value = self._free_mb()
        except Exception:
            value = None
        if value is not None:
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = None
        self._last_free = value
        return value

    def wait_memory(self, need_mb: int, limit_s: float = MEMORY_WAIT_S, *, fatal: bool = True,
                    where: str = "memory") -> Optional[int]:
        """Return the free card memory (MiB) once it is at least ``need_mb``, or None at once when it is
        unknown. Otherwise wait with ``gpu_wait`` why="memory" events, reading free memory again every
        MEMORY_POLL_S; a lock that appears meanwhile is waited for too (that time does not count
        against ``limit_s``). After ``limit_s`` seconds of memory wait: CardMemoryShort, or with
        ``fatal=False`` the last free MiB read (its end event then says short=True)."""
        need = int(need_mb)
        free = self._read_free()
        if free is None or free >= need:
            return free
        self._pause()
        start = self._clock()
        lock_s = 0.0
        next_read = start + MEMORY_POLL_S
        finished = False
        outer = self._in_memory_wait
        self._in_memory_wait = True
        try:
            self._emit("gpu_wait", state="start", why="memory", free_mb=free, need_mb=need)
            while True:
                now = self._clock()
                waited = now - start - lock_s
                if waited >= limit_s:
                    break
                self._sleep(max(0.0, min(POLL_S, next_read - now, limit_s - waited)))
                path = self._active_lock()
                if path is not None:
                    lock_s += self._wait_lock(path, "memory")
                    next_read = self._clock()            # read again right after the lock wait
                now = self._clock()
                if now >= next_read:
                    free = self._read_free()
                    next_read = now + MEMORY_POLL_S
                    if free is None or free >= need:
                        finished = True
                        break
                self._emit("gpu_wait", state="tick", why="memory", free_mb=free, need_mb=need,
                           waited_s=round(now - start - lock_s, 1))
        finally:
            self._in_memory_wait = outer
            seconds = max(0.0, self._clock() - start - lock_s)
            self._record("memory", where, seconds)
        if finished or fatal:
            self._emit("gpu_wait", state="end", why="memory", free_mb=free, need_mb=need,
                       waited_s=round(seconds, 1))
            if not finished:
                raise CardMemoryShort(free, need, seconds)
            return free
        self._room_short += 1
        self._emit("gpu_wait", state="end", why="memory", free_mb=free, need_mb=need, waited_s=round(seconds, 1),
                   short=True)
        return free

    def wait_room(self, where: str) -> Optional[int]:
        """Wait until free card memory is at least ``on_room()`` MiB (what this process may still take up to
        its cap, plus headroom), so that it does not resume into memory another program still holds. Called
        by the gate after every lock wait and by the readers before an out-of-memory retry. Never fails the
        check: after MEMORY_WAIT_S it returns and the check goes on (counted as "room_short"). Returns the
        free MiB read, or None when nothing was checked (no ``on_room`` yet: the card is not started)."""
        need = self.on_room()
        if need is None:
            return None
        self._room_checks += 1
        return self.wait_memory(max(0, int(need)), MEMORY_WAIT_S, fatal=False, where=where)

    def stats(self) -> Dict[str, Any]:
        """Counts only, no paths: waits, waited_s, by why and by where, the last free MiB read, the number
        of lock checks and room checks, the room waits that gave up, and the kinds of the watched locks."""
        return {
            "waits": self._waits,
            "waited_s": round(self._waited_s, 1),
            "by_why": {k: dict(v) for k, v in self._by_why.items()},
            "by_where": {k: dict(v) for k, v in self._by_where.items()},
            "free_mb": self._last_free,
            "lock_checks": self._checks,
            "room_checks": self._room_checks,
            "room_short": self._room_short,
            "locks": [lock_kind(p) for p in self._paths],
        }
