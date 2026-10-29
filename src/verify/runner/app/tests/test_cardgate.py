"""Task E tests: app/cardgate.py (APP_SPEC revision 2, R2.2, R2.3.3 gpu_wait events; G6).

Runner venv, no torch, no graphics card, no bot. The real bot lock and its folder are NEVER stat'ed: every lock
file and every "bot folder" is made up under C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\E (removed
afterwards), and ``BOT_LOCK_DEFAULT`` is patched to a made-up path wherever a test needs the default. Time is a
fake clock and a fake sleep. No personal data.

    set HDC_TEST_NO_CARD=1
    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\app\\tests -p test_cardgate.py -t <worktree>
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.verify.runner.app import cardgate as C

PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
TASK_DIR = PRIVATE / "selftest" / "app" / "E"
SPEC_LOCK = "C:\\Hangeul\\BOT\\data\\verification\\auto_verify.lock"


class FakeCancelled(BaseException):
    """Like hooks.Cancelled: a BaseException raised by the listener."""


class FakeTime:
    """A monotonic clock, a wall clock and a sleep that only moves both clocks. ``on_sleep(n)`` runs after the
    n-th sleep (1-based), so a test can make or remove a lock file "while the gate waits"."""

    def __init__(self, wall0: float):
        self.mono = 5000.0
        self.wall = wall0
        self.sleeps = []
        self.on_sleep = None

    def clock(self):
        return self.mono

    def wall_time(self):
        return self.wall

    def advance(self, s):
        self.mono += s
        self.wall += s

    def sleep(self, s):
        assert s >= 0
        self.sleeps.append(s)
        self.advance(s)
        if self.on_sleep:
            self.on_sleep(len(self.sleeps))


class Recorder:
    def __init__(self, ft=None, raise_on=None):
        self.events = []
        self.ft = ft
        self.raise_on = raise_on          # (state, why) -> raise FakeCancelled there
        self.exc = FakeCancelled("cancel")

    def __call__(self, ev, **fields):
        self.events.append((ev, dict(fields), self.ft.mono if self.ft else None))
        if self.raise_on and (fields.get("state"), fields.get("why")) == self.raise_on:
            raise self.exc

    def states(self):
        return [(f.get("why"), f.get("state")) for _e, f, _t in self.events]


class GateTestCase(unittest.TestCase):
    def setUp(self):
        TASK_DIR.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="cardgate_", dir=str(TASK_DIR)))
        self.ft = FakeTime(wall0=os.stat(self.dir).st_mtime)
        self.lock = self.dir / "auto_verify.lock"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_lock(self, path=None, age_s=60.0):
        path = Path(path or self.lock)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        t = self.ft.wall - age_s
        os.utime(path, (t, t))
        return path

    def gate(self, paths, emit=None, **kw):
        kw.setdefault("clock", self.ft.clock)
        kw.setdefault("wall", self.ft.wall_time)
        kw.setdefault("sleep", self.ft.sleep)
        return C.CardGate([str(p) if p else p for p in paths], emit or Recorder(self.ft), **kw)


# --------------------------------------------------------------------------------------- module functions
class ConstantTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(C.BOT_LOCK_DEFAULT, SPEC_LOCK)
        self.assertEqual(C.LOCK_STALE_S, 4 * 3600)
        self.assertEqual(C.CHECK_EVERY_S, 0.25)
        self.assertEqual(C.POLL_S, 2.0)
        self.assertEqual(C.MEMORY_WAIT_S, 15 * 60)
        self.assertEqual(C.MEMORY_POLL_S, 5.0)

    def test_lock_kind_needs_no_file(self):
        with mock.patch("os.stat", side_effect=AssertionError("stat")):
            self.assertEqual(C.lock_kind(SPEC_LOCK), "bot")
            self.assertEqual(C.lock_kind(SPEC_LOCK.upper()), "bot")
            self.assertEqual(C.lock_kind(SPEC_LOCK.replace("\\", "/")), "bot")
            self.assertEqual(C.lock_kind(r"C:\Hangeul\BOT\data\verification\other.lock"), "other")
            self.assertEqual(C.lock_kind(r"D:\locks\auto_verify.lock"), "other")


class LockActiveTests(GateTestCase):
    def test_fresh_stale_missing(self):
        now = self.ft.wall
        self.make_lock(age_s=60)
        self.assertTrue(C.lock_active(str(self.lock), now))
        self.make_lock(age_s=4 * 3600 - 1)
        self.assertTrue(C.lock_active(str(self.lock), now))
        self.make_lock(age_s=4 * 3600)
        self.assertFalse(C.lock_active(str(self.lock), now))                 # 4 h old: stale
        self.make_lock(age_s=5 * 3600)
        self.assertFalse(C.lock_active(str(self.lock), now))
        self.make_lock(age_s=-120)                                            # a clock skew: still active
        self.assertTrue(C.lock_active(str(self.lock), now))
        self.assertFalse(C.lock_active(str(self.dir / "absent.lock"), now))
        self.assertFalse(C.lock_active("", now))
        self.assertFalse(C.lock_active(None, now))
        self.assertFalse(C.lock_active(str(self.dir / "bad\0name.lock"), now))

    def test_stat_only(self):
        self.make_lock()
        real_stat = os.stat
        seen = []

        def counting_stat(p, *a, **k):
            seen.append(str(p))
            return real_stat(p, *a, **k)
        no = AssertionError("the lock must only be stat'ed")
        with mock.patch("builtins.open", side_effect=no), mock.patch("io.open", side_effect=no), \
                mock.patch("os.open", side_effect=no), mock.patch("os.listdir", side_effect=no), \
                mock.patch("os.scandir", side_effect=no), mock.patch("os.remove", side_effect=no), \
                mock.patch("os.utime", side_effect=no), mock.patch("os.stat", side_effect=counting_stat):
            self.assertTrue(C.lock_active(str(self.lock), self.ft.wall))
        self.assertEqual(seen, [str(self.lock)])


class DefaultSettingTests(GateTestCase):
    def test_only_when_the_folder_exists(self):
        fake = self.dir / "BOT" / "data" / "verification" / "auto_verify.lock"
        asked = []
        real_isdir = os.path.isdir

        def isdir(p):
            asked.append(str(p))
            return real_isdir(p)
        with mock.patch.object(C, "BOT_LOCK_DEFAULT", str(fake)), mock.patch("os.path.isdir", side_effect=isdir), \
                mock.patch("builtins.open", side_effect=AssertionError("open")):
            self.assertIsNone(C.default_lock_setting())
            self.assertFalse(fake.parent.exists())                          # never made
            fake.parent.mkdir(parents=True)
            self.assertEqual(C.default_lock_setting(), str(fake))           # the lock file itself need not exist
            self.assertFalse(fake.exists())
            self.assertEqual(C.lock_kind(C.default_lock_setting()), "bot")
        self.assertEqual(asked, [str(fake.parent)] * 3)                     # the folder only, never the lock


# --------------------------------------------------------------------------------------------- wait_turn
class WaitTurnTests(GateTestCase):
    def test_no_lock_returns_at_once(self):
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec)
        g.on_pause = mock.Mock(return_value=99)
        g.wait_turn("page")
        self.assertEqual((rec.events, self.ft.sleeps), ([], []))
        g.on_pause.assert_not_called()
        self.assertEqual(g.stats()["waits"], 0)

    def test_no_lock_paths_never_stat(self):
        for paths in ([], [None, ""]):
            g = self.gate(paths)
            with mock.patch("os.stat", side_effect=AssertionError("stat")):
                g.wait_turn("start")
                g.wait_turn("page")
            self.assertEqual(g.stats()["lock_checks"], 0)

    def test_throttle(self):
        real_stat = os.stat
        seen = []

        def counting_stat(p, *a, **k):
            seen.append(str(p))
            return real_stat(p, *a, **k)
        g = self.gate([self.lock, self.dir / "second.lock"])
        with mock.patch("os.stat", side_effect=counting_stat):
            g.wait_turn("det")
            self.assertEqual(len(seen), 2)                                   # both paths
            for step in (0.125, 0.0625, 0.0546875):                          # up to 0.2421875 s after the check
                self.ft.advance(step)                                        # (binary-exact steps)
                g.wait_turn("rec")
            self.assertEqual(len(seen), 2)
            self.ft.advance(0.0078125)                                       # 0.25 s: checked again
            g.wait_turn("det")
            self.assertEqual(len(seen), 4)
        self.assertEqual(g.stats()["lock_checks"], 2)

    def test_a_lock_made_inside_the_throttle_window_is_seen_at_the_next_check(self):
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec)
        g.wait_turn("det")
        self.make_lock()
        self.ft.advance(0.125)
        g.wait_turn("rec")                                                   # within 0.25 s: not looked at
        self.assertEqual(rec.events, [])
        self.ft.advance(0.125)
        self.ft.on_sleep = lambda n: self.lock.unlink()
        g.wait_turn("det")
        self.assertEqual(rec.states(), [("lock", "start"), ("lock", "end")])

    def test_start_tick_end(self):
        self.make_lock()
        rec = Recorder(self.ft)
        g = self.gate([self.dir / "absent.lock", self.lock], rec)            # the second path is the active one
        g.on_pause = mock.Mock(return_value=180)
        self.ft.on_sleep = lambda n: self.lock.unlink() if n == 3 else None
        t0 = self.ft.mono
        no = AssertionError("the lock must only be stat'ed")
        with mock.patch("builtins.open", side_effect=no), mock.patch("os.listdir", side_effect=no), \
                mock.patch("os.scandir", side_effect=no):
            g.wait_turn("page")
        self.assertEqual([(e, f) for e, f, _t in rec.events], [
            ("gpu_wait", {"state": "start", "why": "lock", "lock": "other", "where": "page", "reserved_mb": 180}),
            ("gpu_wait", {"state": "tick", "why": "lock", "lock": "other", "waited_s": 2.0}),
            ("gpu_wait", {"state": "tick", "why": "lock", "lock": "other", "waited_s": 4.0}),
            ("gpu_wait", {"state": "end", "why": "lock", "waited_s": 6.0}),
        ])
        self.assertEqual([t - t0 for _e, _f, t in rec.events], [0.0, 2.0, 4.0, 6.0])
        self.assertEqual(self.ft.sleeps, [C.POLL_S] * 3)
        g.on_pause.assert_called_once_with()
        st = g.stats()
        self.assertEqual((st["waits"], st["waited_s"]), (1, 6.0))
        self.assertEqual(st["by_why"], {"lock": {"waits": 1, "waited_s": 6.0}})
        self.assertEqual(st["by_where"], {"page": {"waits": 1, "waited_s": 6.0}})
        g.wait_turn("read")                                                  # right after the wait: throttled
        self.assertEqual(len(rec.events), 4)
        self.assertEqual(st["lock_checks"], 4)

    def test_bot_kind_and_default_pause(self):
        self.make_lock()
        rec = Recorder(self.ft)
        with mock.patch.object(C, "BOT_LOCK_DEFAULT", str(self.lock)):
            g = self.gate([self.lock], rec)
            self.ft.on_sleep = lambda n: self.lock.unlink()
            g.wait_turn("start")
            self.assertEqual(g.stats()["locks"], ["bot"])
        start = rec.events[0][1]
        self.assertEqual((start["lock"], start["where"], start["reserved_mb"]), ("bot", "start", 0))

    def test_stale_lock_is_ignored(self):
        self.make_lock(age_s=5 * 3600)
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec)
        g.on_pause = mock.Mock(return_value=1)
        g.wait_turn("page")
        self.assertEqual((rec.events, self.ft.sleeps), ([], []))
        g.on_pause.assert_not_called()

    def test_a_lock_that_turns_stale_during_the_wait_ends_it(self):
        self.make_lock(age_s=4 * 3600 - 3)
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec)
        g.wait_turn("oom")
        self.assertEqual(rec.states(), [("lock", "start"), ("lock", "tick"), ("lock", "end")])
        self.assertTrue(self.lock.exists())                                  # never removed by the gate

    def test_cancelled_passes_through_a_tick(self):
        self.make_lock()
        rec = Recorder(self.ft, raise_on=("tick", "lock"))
        g = self.gate([self.lock], rec)
        with self.assertRaises(FakeCancelled) as cm:
            g.wait_turn("det")
        self.assertIs(cm.exception, rec.exc)
        self.assertEqual(rec.states(), [("lock", "start"), ("lock", "tick")])
        self.assertEqual((g.stats()["waits"], g.stats()["waited_s"]), (1, 2.0))
        self.assertTrue(self.lock.exists())

    def test_cancelled_passes_through_the_start_and_the_end(self):
        for state, unlink_at in (("start", None), ("end", 1)):
            with self.subTest(state=state):
                self.make_lock()
                rec = Recorder(self.ft, raise_on=(state, "lock"))
                g = self.gate([self.lock], rec)
                self.ft.on_sleep = (lambda n: self.lock.unlink()) if unlink_at else None
                with self.assertRaises(FakeCancelled) as cm:
                    g.wait_turn("page")
                self.assertIs(cm.exception, rec.exc)
                self.ft.on_sleep = None
                if self.lock.exists():
                    self.lock.unlink()

    def test_on_pause_errors_pass_through(self):
        self.make_lock()
        g = self.gate([self.lock])
        g.on_pause = mock.Mock(side_effect=RuntimeError("CUDA error: made up"))
        with self.assertRaises(RuntimeError):
            g.wait_turn("page")

    def test_duplicate_paths_and_stats_hold_no_path(self):
        self.make_lock()
        g = self.gate([self.lock, str(self.lock).upper(), str(self.lock).replace("\\", "/")])
        self.ft.on_sleep = lambda n: self.lock.unlink()
        g.wait_turn("page")
        st = g.stats()
        self.assertEqual(st["locks"], ["other"])
        text = json.dumps(st)
        self.assertNotIn(str(self.dir).lower().replace("\\", "\\\\"), text.lower())
        self.assertNotIn("\\", text)
        self.assertNotIn("hangeul", text.lower())


# ------------------------------------------------------------------------------------------- wait_memory
class WaitMemoryTests(GateTestCase):
    def free_seq(self, values):
        it = iter(values)
        reads = []

        def free():
            reads.append(self.ft.mono)
            return next(it)
        free.reads = reads
        return free

    def test_unknown_or_enough_returns_at_once(self):
        for value, want in ((None, None), (6000, 6000), (4096, 4096)):
            rec = Recorder(self.ft)
            g = self.gate([], rec, free_mb=lambda v=value: v)
            self.assertEqual(g.wait_memory(4096), want)
            self.assertEqual((rec.events, self.ft.sleeps), ([], []))
        g = self.gate([], Recorder(self.ft), free_mb=mock.Mock(side_effect=RuntimeError("nvml")))
        self.assertIsNone(g.wait_memory(4096))
        self.assertIsNone(self.gate([]).wait_memory(4096))                   # the default free_mb: unknown

    def test_waits_then_goes_on(self):
        free = self.free_seq([2900, 2900, 5000])
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec, free_mb=free)
        t0 = self.ft.mono
        self.assertEqual(g.wait_memory(4096), 5000)
        self.assertEqual([t - t0 for t in free.reads], [0.0, 5.0, 10.0])     # read again every 5 s
        self.assertEqual(rec.events[0][1], {"state": "start", "why": "memory", "free_mb": 2900, "need_mb": 4096})
        self.assertEqual(rec.events[-1][1], {"state": "end", "why": "memory", "free_mb": 5000, "need_mb": 4096,
                                             "waited_s": 10.0})
        ticks = [f for _e, f, _t in rec.events if f["state"] == "tick"]
        self.assertTrue(ticks and all(set(f) == {"state", "why", "free_mb", "need_mb", "waited_s"} for f in ticks))
        times = [t for _e, _f, t in rec.events]
        self.assertLessEqual(max(b - a for a, b in zip(times, times[1:])), C.POLL_S)   # Cancel within 2 s
        st = g.stats()
        self.assertEqual((st["waits"], st["waited_s"], st["free_mb"]), (1, 10.0, 5000))
        self.assertEqual(st["by_why"], {"memory": {"waits": 1, "waited_s": 10.0}})

    def test_timeout_raises_card_memory_short(self):
        reads = []

        def free():
            reads.append(self.ft.mono)
            return 2900
        rec = Recorder(self.ft)
        g = self.gate([], rec, free_mb=free)
        with self.assertRaises(C.CardMemoryShort) as cm:
            g.wait_memory(4096)
        e = cm.exception
        self.assertEqual((e.free_mb, e.need_mb, e.waited_s), (2900, 4096, 900.0))
        self.assertAlmostEqual(sum(self.ft.sleeps), C.MEMORY_WAIT_S)
        self.assertEqual(len(reads), 1 + int(C.MEMORY_WAIT_S / C.MEMORY_POLL_S))
        self.assertEqual(rec.events[-1][1], {"state": "end", "why": "memory", "free_mb": 2900, "need_mb": 4096,
                                             "waited_s": 900.0})
        self.assertEqual(g.stats()["by_why"]["memory"], {"waits": 1, "waited_s": 900.0})
        self.assertNotIn("\\", str(e))

    def test_short_limit(self):
        g = self.gate([], Recorder(self.ft), free_mb=lambda: 100)
        with self.assertRaises(C.CardMemoryShort):
            g.wait_memory(4096, limit_s=7)
        self.assertAlmostEqual(sum(self.ft.sleeps), 7.0)
        with self.assertRaises(C.CardMemoryShort):
            self.gate([], Recorder(self.ft), free_mb=lambda: 100).wait_memory(4096, limit_s=0)

    def test_a_lock_that_appears_is_waited_for_and_does_not_count(self):
        bot_done = []

        def on_sleep(n):
            if n == 1:
                self.make_lock()                                             # the bot starts (memory is low)
            elif n == 4:
                self.lock.unlink()                                           # the bot ends and frees its memory
                bot_done.append(True)
        self.ft.on_sleep = on_sleep
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec, free_mb=lambda: 5000 if bot_done else 2900)
        g.on_pause = mock.Mock(return_value=150)
        self.assertEqual(g.wait_memory(4096, limit_s=5), 5000)               # 8 s in all, 2 s of it for memory
        self.assertEqual(rec.states(), [("memory", "start"), ("lock", "start"), ("lock", "tick"),
                                        ("lock", "tick"), ("lock", "end"), ("memory", "end")])
        lock_start = rec.events[1][1]
        self.assertEqual((lock_start["where"], lock_start["reserved_mb"]), ("memory", 150))
        self.assertEqual(rec.events[-1][1]["waited_s"], 2.0)
        st = g.stats()
        self.assertEqual(st["by_why"], {"memory": {"waits": 1, "waited_s": 2.0}, "lock": {"waits": 1, "waited_s": 6.0}})
        self.assertEqual((st["waits"], st["waited_s"]), (2, 8.0))

    def test_cancelled_passes_through_a_memory_tick(self):
        rec = Recorder(self.ft, raise_on=("tick", "memory"))
        g = self.gate([], rec, free_mb=lambda: 2900)
        with self.assertRaises(FakeCancelled) as cm:
            g.wait_memory(4096)
        self.assertIs(cm.exception, rec.exc)
        self.assertEqual(rec.states(), [("memory", "start"), ("memory", "tick")])
        self.assertEqual(g.stats()["by_why"]["memory"]["waits"], 1)


def collapsed(states):
    """The states with repeated ticks counted once (a memory wait ticks every <= POLL_S between its reads)."""
    out = []
    for x in states:
        if not out or out[-1] != x:
            out.append(x)
    return out


class RoomTests(GateTestCase):
    """G1 review finding 2: after a lock wait the gate waits for room on the card (free memory of what this process
    may still take up to its cap, plus headroom: ``on_room``). The program that held the lock may hold its memory a
    little longer. The room wait never fails a check."""

    def free_seq(self, values):
        it = iter(values)
        reads = []

        def free():
            reads.append(self.ft.mono)
            return next(it)
        free.reads = reads
        return free

    def test_after_a_lock_wait_the_gate_waits_for_room(self):
        self.make_lock()
        free = self.free_seq([2000, 2000, 3600])                          # the bot's process still holds memory
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec, free_mb=free)
        g.on_pause = mock.Mock(return_value=110)
        g.on_room = mock.Mock(return_value=3474)
        self.ft.on_sleep = lambda n: self.lock.unlink() if n == 1 else None
        g.wait_turn("det")
        self.assertEqual(collapsed(rec.states()), [("lock", "start"), ("lock", "end"), ("memory", "start"),
                                                   ("memory", "tick"), ("memory", "end")])
        self.assertEqual([t - free.reads[0] for t in free.reads], [0.0, 5.0, 10.0])
        self.assertEqual(rec.events[2][1], {"state": "start", "why": "memory", "free_mb": 2000, "need_mb": 3474})
        self.assertEqual(rec.events[-1][1], {"state": "end", "why": "memory", "free_mb": 3600, "need_mb": 3474,
                                             "waited_s": 10.0})
        g.on_room.assert_called_once_with()
        st = g.stats()
        self.assertEqual((st["room_checks"], st["room_short"]), (1, 0))
        self.assertEqual(st["by_why"], {"lock": {"waits": 1, "waited_s": 2.0}, "memory": {"waits": 1,
                                                                                          "waited_s": 10.0}})
        self.assertEqual(st["by_where"], {"det": {"waits": 2, "waited_s": 12.0}})

    def test_enough_room_goes_on_at_once(self):
        self.make_lock()
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec, free_mb=lambda: 6000)
        g.on_room = lambda: 3474
        self.ft.on_sleep = lambda n: self.lock.unlink()
        g.wait_turn("page")
        self.assertEqual(rec.states(), [("lock", "start"), ("lock", "end")])
        self.assertEqual(self.ft.sleeps, [C.POLL_S])
        self.assertEqual((g.stats()["room_checks"], g.stats()["free_mb"]), (1, 6000))

    def test_the_room_wait_gives_up_without_failing_the_check(self):
        self.make_lock()
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec, free_mb=lambda: 2000)
        g.on_room = lambda: 3474
        self.ft.on_sleep = lambda n: self.lock.unlink() if n == 1 else None
        g.wait_turn("rec")                                                # no CardMemoryShort
        self.assertEqual(rec.events[-1][1], {"state": "end", "why": "memory", "free_mb": 2000, "need_mb": 3474,
                                             "waited_s": 900.0, "short": True})
        self.assertAlmostEqual(sum(self.ft.sleeps), C.POLL_S + C.MEMORY_WAIT_S)
        self.assertEqual(g.stats()["room_short"], 1)

    def test_no_room_check_before_the_card_starts(self):
        self.make_lock()
        free = mock.Mock(return_value=100)
        g = self.gate([self.lock], Recorder(self.ft), free_mb=free)       # on_room not set yet: no card
        self.ft.on_sleep = lambda n: self.lock.unlink()
        g.wait_turn("start")
        free.assert_not_called()
        self.assertEqual(g.stats()["room_checks"], 0)
        self.assertIsNone(g.wait_room("oom"))

    def test_a_lock_inside_a_memory_wait_asks_for_no_room_of_its_own(self):
        bot_done = []

        def on_sleep(n):
            if n == 1:
                self.make_lock()
            elif n == 4:
                self.lock.unlink()
                bot_done.append(True)
        self.ft.on_sleep = on_sleep
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec, free_mb=lambda: 5000 if bot_done else 2900)
        g.on_room = mock.Mock(return_value=3474)
        self.assertEqual(g.wait_memory(4096, limit_s=5), 5000)
        self.assertEqual(rec.states(), [("memory", "start"), ("lock", "start"), ("lock", "tick"),
                                        ("lock", "tick"), ("lock", "end"), ("memory", "end")])
        g.on_room.assert_not_called()

    def test_wait_room_before_an_oom_retry(self):
        free = self.free_seq([3000, 3500])
        rec = Recorder(self.ft)
        g = self.gate([], rec, free_mb=free)
        g.on_room = lambda: 3474
        self.assertEqual(g.wait_room("oom"), 3500)
        self.assertEqual(collapsed(rec.states()), [("memory", "start"), ("memory", "tick"), ("memory", "end")])
        self.assertEqual(g.stats()["by_where"], {"oom": {"waits": 1, "waited_s": 5.0}})
        self.assertEqual(self.gate([], Recorder(self.ft), free_mb=lambda: None).wait_room("oom"), None)

    def test_the_start_memory_wait_still_fails(self):
        g = self.gate([], Recorder(self.ft), free_mb=lambda: 100)
        g.on_room = lambda: 3474
        with self.assertRaises(C.CardMemoryShort):
            g.wait_memory(4096, limit_s=7)
        self.assertEqual(g.stats()["room_short"], 0)

    def test_room_events_are_protocol_events(self):
        try:
            from src.verify.runner.app import protocol
        except ImportError:
            self.skipTest("Task C's protocol is not built")
        self.make_lock()
        rec = Recorder(self.ft)
        g = self.gate([self.lock], rec, free_mb=lambda: 2000)
        g.on_room = lambda: 3474
        self.ft.on_sleep = lambda n: self.lock.unlink() if n == 1 else None
        g.wait_turn("page")
        for ev, fields, _t in rec.events:
            self.assertEqual(protocol.validate(protocol.make(ev, **fields)), [], fields)


class SourceRuleTests(unittest.TestCase):
    """Standard library only; never torch, never hooks; exactly ONE C:\\Hangeul string (BOT_LOCK_DEFAULT): the line
    the bundle scan allows (R2.6 8 b)."""

    def test_imports_and_strings(self):
        tree = ast.parse(Path(C.__file__).read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0] if node.level == 0 else "<relative>")
        self.assertLessEqual(mods, {"__future__", "os", "time", "typing"})
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertEqual([s for s in strings if "hangeul" in s.lower()], [SPEC_LOCK])


def tearDownModule():
    try:
        os.rmdir(TASK_DIR)                                   # only when empty; the shared selftest\app stays
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
