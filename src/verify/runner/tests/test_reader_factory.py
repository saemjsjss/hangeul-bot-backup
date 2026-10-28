"""APP_SPEC rev 2 R2.1.6: engine.READER_FACTORY. With a factory set, Engine() and closer._reader() use its reader and
onnxruntime is never imported (checked in a fresh CHILD process: the runner venv, -I -B, DEV mode). Without one,
make_reader() builds today's two CPU-only ONNX sessions from today's model files (checked here with a fake
onnxruntime and a fake glue_numpy, so no model is loaded). No card, no real reading. Made-up data only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

from src.verify.runner import botimport, engine

TODAY_ONNX_LAB = Path(r"C:\Hangeul\DOCCHECK\lab\onnx")

_CHILD = r'''
import json, sys
from src.verify.runner import botimport, closer, engine
res = {"before": ["onnxruntime" in sys.modules, "glue_numpy" in sys.modules]}
calls = []

class FakeReader:
    det = rec = object()
    character = "0123456789abcdefghijklmnopqrstuvwxyz"

    def readtext(self, image, *a, **kw):
        return []

fake = FakeReader()

def factory(threads=0):
    calls.append(threads)
    return fake

engine.READER_FACTORY = factory
eng = engine.Engine(botimport.DEFAULT_CACHE_ROOT, threads=3)
res["engine_reader"] = eng.reader.inner is fake
res["dv_reader"] = eng.bot.dv._reader is eng.reader
res["closer_reader"] = closer._reader() is fake
res["bind"] = closer.bind(eng) and closer._reader() is eng.reader and closer._reader().inner is fake
res["calls"] = calls
res["after"] = ["onnxruntime" in sys.modules, "glue_numpy" in sys.modules]
res["heavy"] = engine.heavy_modules()
print(json.dumps(res))
'''


class TestWithAFactory(unittest.TestCase):
    def test_engine_and_closer_use_the_factory_reader(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("HDC_")}       # DEV mode
        r = subprocess.run([sys.executable, "-I", "-B", "-c", textwrap.dedent(_CHILD)], cwd=str(botimport.PRIVATE),
                           capture_output=True, text=True, timeout=300, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        res = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(res["before"], [False, False])
        self.assertTrue(res["engine_reader"])
        self.assertTrue(res["dv_reader"])
        self.assertTrue(res["closer_reader"])
        self.assertTrue(res["bind"])
        self.assertEqual(res["calls"], [3, 0])                  # Engine(threads=3), then closer's make_reader()
        self.assertEqual(res["after"], [False, False])          # onnxruntime (and the ONNX glue) never imported
        self.assertEqual(res["heavy"], [])

    def test_make_reader_returns_the_factory_reader_first(self):
        poisoned = types.ModuleType("onnxruntime")

        def boom(name):
            raise AssertionError("onnxruntime must not be used when READER_FACTORY is set")
        poisoned.__getattr__ = boom
        sentinel = object()
        with mock.patch.dict(sys.modules, {"onnxruntime": poisoned, "glue_numpy": poisoned}), \
                mock.patch.object(engine, "READER_FACTORY", lambda threads=0: (sentinel, threads)):
            self.assertEqual(engine.make_reader(), (sentinel, 0))
            self.assertEqual(engine.make_reader(4), (sentinel, 4))


class _Options:
    pass


class TestWithoutAFactory(unittest.TestCase):
    """DEV unchanged: two InferenceSessions on today's model files, CPU execution provider only, both arenas off,
    then glue_numpy.LightReader(det, rec, GLUE_DATA)."""

    def _fakes(self, providers=("CPUExecutionProvider",)):
        made = []
        ort = types.ModuleType("onnxruntime")
        ort.SessionOptions = _Options

        class InferenceSession:
            def __init__(self, path, so, providers):
                self.path, self.so, self.asked = path, so, list(providers)
                made.append(self)

            def get_providers(self):
                return list(fake_providers)

        fake_providers = list(providers)
        ort.InferenceSession = InferenceSession
        glue = types.ModuleType("glue_numpy")

        class LightReader:
            def __init__(self, det, rec, data):
                self.det, self.rec, self.data = det, rec, data
        glue.LightReader = LightReader
        return ort, glue, made

    def test_dev_reader_is_todays(self):
        self.assertIsNone(engine.READER_FACTORY)
        ort, glue, made = self._fakes()
        with mock.patch.dict(sys.modules, {"onnxruntime": ort, "glue_numpy": glue}):
            reader = engine.make_reader(2)
        self.assertIsInstance(reader, glue.LightReader)
        self.assertEqual([s.path for s in made], [str(TODAY_ONNX_LAB / "models" / "craft_mlt_25k.onnx"),
                                                   str(TODAY_ONNX_LAB / "models" / "english_g2.onnx")])
        self.assertEqual([s.asked for s in made], [["CPUExecutionProvider"]] * 2)
        self.assertEqual([(s.so.intra_op_num_threads, s.so.enable_cpu_mem_arena, s.so.log_severity_level)
                          for s in made], [(2, False, 3), (2, False, 3)])
        self.assertIs(reader.det, made[0])
        self.assertIs(reader.rec, made[1])
        self.assertEqual(reader.data, TODAY_ONNX_LAB / "pkg" / "glue_data.json")

    def test_dev_refuses_a_session_that_is_not_cpu_only(self):
        ort, glue, _made = self._fakes(providers=("DmlExecutionProvider", "CPUExecutionProvider"))
        with mock.patch.dict(sys.modules, {"onnxruntime": ort, "glue_numpy": glue}):
            with self.assertRaises(RuntimeError):
                engine.make_reader()


if __name__ == "__main__":
    unittest.main()
