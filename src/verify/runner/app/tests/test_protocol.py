"""Task C: protocol.py (APP_SPEC rev 1 section 2, revision 2 R2.3.3). Made-up data only; no card and no child
process; job.json files go under Task C's scratch folder."""
from __future__ import annotations

import io
import json
import math
import os
import time
import unittest
from pathlib import Path

from src.verify.runner.app import protocol as P
from src.verify.runner.app.tests import fake_worker as F


class Boom(BaseException):
    """Stands in for hooks.Cancelled."""


class EncodeDecode(unittest.TestCase):
    def test_line_is_compact_ascii_and_v_ev_first(self):
        b = P.encode(P.make("page", done=3, of=14, seconds=1.2, where="card"))
        self.assertTrue(b.endswith(b"\n"))
        self.assertEqual(b.count(b"\n"), 1)
        b.decode("ascii")
        self.assertTrue(b.startswith(b'{"v":1,"ev":"page",'))
        self.assertNotIn(b" ", b)

    def test_non_ascii_is_escaped(self):
        b = P.encode(P.make("file", name="\u00dcn\u00efcode \u09a0.pdf", pages=1, ocr=1))
        b.decode("ascii")
        self.assertEqual(P.decode(b)["name"], "\u00dcn\u00efcode \u09a0.pdf")

    def test_values_made_json_safe(self):
        ev = P.make("x", p=Path(r"C:\made\up"), t=(1, 2), nan=float("nan"), inf=math.inf, b=b"ab",
                    obj=object(), nested={"k": (Path("a"),)})
        d = P.decode(P.encode(ev))
        self.assertEqual(d["p"], r"C:\made\up")
        self.assertEqual(d["t"], [1, 2])
        self.assertIsNone(d["nan"])
        self.assertIsNone(d["inf"])
        self.assertEqual(d["b"], "ab")
        self.assertEqual(d["obj"], "<object>")
        self.assertEqual(d["nested"], {"k": ["a"]})

    def test_numpy_like_scalar_uses_item(self):
        class Scalar:
            def item(self):
                return 2.5
        self.assertEqual(P.decode(P.encode(P.make("x", s=Scalar())))["s"], 2.5)

    def test_v_and_ev_cannot_be_overridden(self):
        d = P.decode(P.encode(P.make("page", v=9, done=1, of=1)))
        self.assertEqual(d["v"], 1)
        self.assertEqual(d["ev"], "page")

    def test_decode_rejects_non_events(self):
        for line in (b"", b"hello\n", b"[1,2]\n", b'{"v":2,"ev":"page"}\n', b'{"v":1}\n', b'{"v":1,"ev":""}\n',
                     b'{"v":1,"ev":"page"\n', "\u00e9".encode("utf-8"), b"x" * (P.MAX_LINE + 1)):
            with self.subTest(line=line[:20]):
                self.assertIsNone(P.decode(line))

    def test_decode_accepts_str_and_unknown_events(self):
        self.assertEqual(P.decode('{"v":1,"ev":"future","x":1}')["x"], 1)

    def test_encode_refuses_non_events(self):
        with self.assertRaises(P.ProtocolError):
            P.encode({"no": "ev"})
        with self.assertRaises(P.ProtocolError):
            P.make("")


class Events(unittest.TestCase):
    def roundtrip(self, ev):
        d = P.decode(P.encode(ev))
        self.assertEqual(P.validate(d), [], d)
        return d

    def test_rev2_examples(self):
        self.roundtrip(P.card_found(F.CARD_NAME, "12.0", "000.00", 8151, 6120))
        self.roundtrip(P.card_ready("2.11.0+cu128", "12.8", 14.2))
        self.roundtrip(P.gpu_wait("start", "lock", lock="bot", where="page", reserved_mb=180))
        self.roundtrip(P.gpu_wait("tick", "lock", lock="bot", waited_s=42.0))
        self.roundtrip(P.gpu_wait("end", "lock", waited_s=180.5))
        self.roundtrip(P.gpu_wait("start", "memory", free_mb=2900, need_mb=4096))
        self.roundtrip(P.page(3, 14, 1.2, "card"))
        self.roundtrip(P.stage("firstread", "start"))
        self.roundtrip(P.stage("card", "end", 1.5))
        self.roundtrip(P.error("DRIVER_TOO_OLD", "made-up", {"driver": "000.00"}))
        self.roundtrip(P.cancelled())
        self.roundtrip(P.hello("1.0.0", 1234, "20261029.1-abc1234"))
        self.roundtrip(P.file_event("made-up.pdf", 14, 12))
        self.roundtrip(P.budget("birth.qr", 7))

    def test_done_gpu_block(self):
        g = P.gpu_block(F.CARD_NAME, 2.41, 2.8, 0, 1, 180.5)
        self.assertEqual(tuple(g), P.GPU_BLOCK_KEYS)
        d = self.roundtrip(P.done(0, "FAIL", "made-up", "3F2A-91C0", "<out>", [("FAIL", "x")], 1310, g))
        self.assertEqual(d["gpu"], {"card": F.CARD_NAME, "peak_alloc_gb": 2.41, "peak_reserved_gb": 2.8,
                                    "cpu_calls": 0, "waits": 1, "waited_s": 180.5})
        self.assertEqual(d["lines"], [["FAIL", "x"]])

    def test_validate_finds_problems(self):
        bad = [
            {"v": 1, "ev": "gpu_wait", "state": "begin", "why": "lock"},
            {"v": 1, "ev": "gpu_wait", "state": "start", "why": "lock", "lock": "mine"},
            {"v": 1, "ev": "gpu_wait", "state": "tick", "why": "memory", "need_mb": 1},
            {"v": 1, "ev": "card", "state": "lost"},
            {"v": 1, "ev": "card", "state": "found", "name": "x"},
            {"v": 1, "ev": "done", "exit": 0, "overall": "PASS", "lines": [], "gpu": {"card": "x"}},
            {"v": 1, "ev": "error", "code": ""},
            {"v": 1, "ev": "page", "done": "3", "of": 4},
            {"v": 2, "ev": "hello", "app": "1", "pid": 1},
        ]
        for ev in bad:
            with self.subTest(ev=ev):
                self.assertNotEqual(P.validate(ev), [])

    def test_codes_and_exits(self):
        self.assertEqual(P.CARD_CODES, ("NO_NVIDIA_CARD", "DRIVER_TOO_OLD", "CARD_NOT_SUPPORTED", "CARD_TOO_SMALL",
                                        "LOW_CARD_MEMORY", "CARD_FAILED"))
        self.assertEqual((P.EXIT_OK, P.EXIT_CRASH, P.EXIT_REFUSED, P.EXIT_STOP, P.EXIT_CANCELLED, P.EXIT_PLATFORM,
                          P.EXIT_CARD), (0, 1, 2, 3, 4, 5, 6))
        self.assertEqual(P.CANCEL_LINE, b"cancel\n")


class Jobs(unittest.TestCase):
    def setUp(self):
        self.dir = F.scratch("protocol_jobs")

    def tearDown(self):
        F.remove(self.dir)

    def job(self, **kw):
        base = dict(input=os.path.join(self.dir, "in", "MADE-UP.pdf"), program="KLP",
                    out=os.path.join(self.dir, "out"), cache_root=os.path.join(self.dir, "data", "cache"))
        base.update(kw)
        return P.make_job(**base)

    def test_make_write_read(self):
        lock = os.path.join(self.dir, "locks", "x.lock")
        j = self.job(today="2026-10-28", lock_paths=[lock, lock.upper(), None, ""], index=1, fake="done")
        self.assertEqual(j["schema"], "hdc-job/1")
        self.assertRegex(j["job"], r"^[0-9a-f]{32}$")
        self.assertEqual(j["gpu"]["lock_paths"], [lock])                  # duplicates (normcase) and empties out
        self.assertEqual(j["gpu"]["index"], 1)
        self.assertEqual(j["gpu"]["cap_gb"], 3.0)
        self.assertEqual(j["gpu"]["need_free_mb"], 4096)
        self.assertEqual(j["fake"], "done")
        p = P.write_job(os.path.join(self.dir, "jobs", j["job"], "job.json"), j)
        self.assertFalse(Path(str(p) + ".tmp").exists())
        self.assertEqual(P.read_job(p), j)

    def test_rev1_job_gets_the_gpu_defaults(self):
        j = self.job()
        del j["gpu"]
        p = os.path.join(self.dir, "job.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(j, f)
        g = P.read_job(p)["gpu"]
        self.assertEqual(g, {"lock_paths": [], "index": 0, "cap_gb": 3.0, "need_free_mb": 4096})

    def test_bad_jobs_are_refused(self):
        good = self.job()
        cases = {
            "schema": dict(good, schema="hdc-job/0"),
            "job": dict(good, job="XYZ"),
            "input": dict(good, input="relative.pdf"),
            "out": dict(good, out=""),
            "program": dict(good, program=" "),
            "today": dict(good, today="28-10-2026"),
            "today2": dict(good, today="2026-02-30"),
            "trace": dict(good, trace_dir="rel"),
            "locks": dict(good, gpu=dict(good["gpu"], lock_paths=["rel.lock"])),
            "index": dict(good, gpu=dict(good["gpu"], index=-1)),
            "cap": dict(good, gpu=dict(good["gpu"], cap_gb=0)),
            "need": dict(good, gpu=dict(good["gpu"], need_free_mb=True)),
        }
        for name, j in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(P.JobError):
                    P.check_job(json.loads(json.dumps(j)))
        with self.assertRaises(P.JobError):
            P.read_job(os.path.join(self.dir, "missing.json"))
        bad = os.path.join(self.dir, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertRaises(P.JobError) as cm:
            P.read_job(bad)
        self.assertNotIn(self.dir, str(cm.exception))                   # no path in the message


class Listener(unittest.TestCase):
    def make(self, **kw):
        buf = io.BytesIO()
        flag = P.CancelFlag()
        lst = P.WorkerListener(P.Writer(buf), flag, cancelled_exc=Boom, **kw)
        return buf, flag, lst

    @staticmethod
    def events(buf):
        return [P.decode(x) for x in buf.getvalue().splitlines()]

    def test_call_forms(self):
        buf, _, lst = self.make()
        lst("stage", key="ocr", state="start")
        lst("stage", {"key": "ocr", "state": "end", "seconds": 1.0})
        lst({"v": 1, "ev": "page", "done": 1, "of": 2})
        lst("x", data=1, event=2, ev="ignored")                            # field names are never parameters
        evs = self.events(buf)
        self.assertEqual([e["ev"] for e in evs], ["stage", "stage", "page", "x"])
        self.assertEqual(evs[1]["seconds"], 1.0)
        self.assertEqual(evs[2]["of"], 2)
        self.assertEqual((evs[3]["data"], evs[3]["event"]), (1, 2))

    def test_ocr_events_become_pages(self):
        buf, _, lst = self.make()
        lst("file", name="a.pdf", pages=3, ocr=2)
        lst("file", name="b.pdf", pages=1, ocr=1)
        lst("ocr", phase="start")
        lst("ocr", phase="end", seconds=1.5, memo=False)
        lst("ocr", phase="end", seconds=0.0, memo=True)
        lst("ocr", phase="end", seconds=2.5, memo=False)
        pages = [e for e in self.events(buf) if e["ev"] == "page"]
        self.assertEqual([(p["done"], p["of"], p["where"]) for p in pages], [(1, 3, "runner"), (2, 3, "runner")])
        self.assertNotIn("ocr", [e["ev"] for e in self.events(buf)])

    def test_cancel_raises_at_the_next_event_but_never_on_end_or_terminal(self):
        buf, flag, lst = self.make()
        flag.set()
        for name, fields in (("stage", {"key": "x", "state": "end"}), ("ocr", {"phase": "end", "memo": False}),
                             ("gpu_wait", {"state": "end", "why": "lock", "waited_s": 1.0}),
                             ("hello", {"app": "1", "pid": 1}), ("error", {"code": "CRASH"}), ("cancelled", {}),
                             ("done", {"exit": 0, "overall": "PASS", "lines": []})):
            with self.subTest(name=name):
                lst(name, **fields)                               # no exception
        for name, fields in (("page", {"done": 1, "of": 2}), ("stage", {"key": "x", "state": "start"}),
                             ("gpu_wait", {"state": "tick", "why": "lock", "lock": "bot", "waited_s": 1.0}),
                             ("card", {"state": "found", "name": "x", "total_mb": 1, "free_mb": 1})):
            with self.subTest(name=name):
                with self.assertRaises(Boom):
                    lst(name, **fields)
        # the event itself was still written before the raise
        self.assertIn("page", [e["ev"] for e in self.events(buf)])
        self.assertEqual(lst.terminal, "error")

    def test_default_cancelled_is_hooks_cancelled(self):
        from src.verify.runner import hooks
        lst = P.WorkerListener(P.Writer(io.BytesIO()))
        lst.cancel.set()
        with self.assertRaises(hooks.Cancelled):
            lst("page", done=1, of=1)

    def test_listener_works_as_hooks_listener(self):
        from src.verify.runner import hooks
        buf, _, lst = self.make()
        old = hooks.LISTENER
        hooks.LISTENER = lst
        try:
            hooks.emit("stage", key="names", state="start")
        finally:
            hooks.LISTENER = old
        self.assertEqual(self.events(buf)[0]["key"], "names")

    def test_broken_pipe_sets_cancel(self):
        class Broken:
            def write(self, b):
                raise BrokenPipeError()

            def flush(self):
                pass
        flag = P.CancelFlag()
        lst = P.WorkerListener(P.Writer(Broken()), flag, cancelled_exc=Boom)
        with self.assertRaises(Boom):
            lst("page", done=1, of=1)
        self.assertTrue(flag.is_set())
        self.assertEqual(flag.why, "broken pipe")
        flag2 = P.CancelFlag()
        lst2 = P.WorkerListener(P.Writer(Broken()), flag2, cancelled_exc=Boom, cancel_on_broken=False)
        lst2("page", done=1, of=1)
        self.assertFalse(flag2.is_set())

    def test_writer_without_stream_drops(self):
        w = P.Writer(None)
        self.assertFalse(w.send(P.cancelled()))
        self.assertTrue(w.broken)


class StdinWatcher(unittest.TestCase):
    def watch(self):
        r, w = os.pipe()
        flag = P.CancelFlag()
        stream = open(r, "rb", buffering=0)
        self.addCleanup(stream.close)
        return w, flag, P.watch_stdin(flag, stream)

    def test_cancel_line_sets_flag_and_eof_does_not(self):
        w, flag, t = self.watch()
        os.write(w, b"noise\n")
        time.sleep(0.1)
        self.assertFalse(flag.is_set())
        os.write(w, b"cancel\n")
        self.assertTrue(flag.wait(5.0))
        os.close(w)
        t.join(5.0)
        self.assertFalse(t.is_alive())

        w2, flag2, t2 = self.watch()
        os.close(w2)
        t2.join(5.0)
        self.assertFalse(flag2.is_set())

    def test_cancel_without_newline_at_end(self):
        w, flag, t = self.watch()
        os.write(w, b"cancel")
        os.close(w)
        t.join(5.0)
        self.assertTrue(flag.is_set())


if __name__ == "__main__":
    unittest.main()
