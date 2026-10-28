"""Task E tests: app/gpuinfo.py (APP_SPEC revision 2, R2.3.1, R2.1.2 environment row; G5, G7).

Runner venv, no torch, no graphics card: the real nvml.dll is NEVER loaded. NVML is a fake DLL object handed in
through ``probe(loader=...)``; the tests of the real loader point ``NVML_DLL`` at a made-up file under
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\E and replace ``ctypes.CDLL`` with a recorder. Card names
and driver numbers are placeholders. No personal data.

    set HDC_TEST_NO_CARD=1
    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\app\\tests -p test_gpuinfo.py -t <worktree>
"""
from __future__ import annotations

import ast
import ctypes
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.verify.runner.app import gpuinfo as G

PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
TASK_DIR = PRIVATE / "selftest" / "app" / "E"
MIB = 1024 * 1024
SPEC_ARCH = ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"]
NVML_FUNCS = ("nvmlInit_v2", "nvmlShutdown", "nvmlSystemGetDriverVersion", "nvmlSystemGetCudaDriverVersion_v2",
              "nvmlDeviceGetCount_v2", "nvmlDeviceGetHandleByIndex_v2", "nvmlDeviceGetName",
              "nvmlDeviceGetCudaComputeCapability", "nvmlDeviceGetMemoryInfo")


# ------------------------------------------------------------------------------------------------ fakes
class FakeNvml:
    """A stand-in for the ctypes DLL object: the nine NVML functions with the real calling shapes (pointer
    out-arguments, char buffers with a length). ``fail`` maps a function to its NVML return code, ``boom``
    maps a function to an exception it raises, ``missing`` hides functions (an old driver)."""

    HANDLE = 0x1000

    def __init__(self, *, driver=b"000.00", cuda=12090, plain_cuda=None, count=1,
                 name=b"NVIDIA GeForce RTX 0000", cc=(12, 0), total_mb=8151, free_mb=6120,
                 fail=None, boom=None, missing=()):
        self.driver, self.cuda, self.plain_cuda, self.count = driver, cuda, plain_cuda, count
        self.name, self.cc, self.total_mb, self.free_mb = name, cc, total_mb, free_mb
        self.fail = dict(fail or {})
        self.boom = dict(boom or {})
        self.missing = set(missing)
        self.calls = []
        self.indexes = []
        self.handles = []

    def __getattribute__(self, attr):
        if attr.startswith("nvml") and attr in object.__getattribute__(self, "missing"):
            raise AttributeError(attr)
        return object.__getattribute__(self, attr)

    def _enter(self, fn):
        self.calls.append(fn)
        if fn in self.boom:
            raise self.boom[fn]
        return self.fail.get(fn, 0)

    def _handle_ok(self, handle):
        self.handles.append(handle.value)
        assert handle.value == self.HANDLE, "the handle from nvmlDeviceGetHandleByIndex_v2 must be passed on"

    def nvmlInit_v2(self):
        return self._enter("nvmlInit_v2")

    def nvmlShutdown(self):
        return self._enter("nvmlShutdown")

    def nvmlSystemGetDriverVersion(self, buf, length):
        rc = self._enter("nvmlSystemGetDriverVersion")
        assert isinstance(length, ctypes.c_uint) and length.value >= 80
        if rc == 0:
            buf.value = self.driver
        return rc

    def nvmlSystemGetCudaDriverVersion_v2(self, ptr):
        rc = self._enter("nvmlSystemGetCudaDriverVersion_v2")
        if rc == 0:
            ptr.contents.value = self.cuda
        return rc

    def nvmlSystemGetCudaDriverVersion(self, ptr):
        rc = self._enter("nvmlSystemGetCudaDriverVersion")
        if rc == 0:
            ptr.contents.value = self.plain_cuda if self.plain_cuda is not None else self.cuda
        return rc

    def nvmlDeviceGetCount_v2(self, ptr):
        rc = self._enter("nvmlDeviceGetCount_v2")
        if rc == 0:
            ptr.contents.value = self.count
        return rc

    def nvmlDeviceGetHandleByIndex_v2(self, index, ptr):
        rc = self._enter("nvmlDeviceGetHandleByIndex_v2")
        self.indexes.append(index.value)
        if rc == 0:
            ptr.contents.value = self.HANDLE
        return rc

    def nvmlDeviceGetName(self, handle, buf, length):
        rc = self._enter("nvmlDeviceGetName")
        self._handle_ok(handle)
        assert isinstance(length, ctypes.c_uint) and length.value >= 96
        if rc == 0:
            buf.value = self.name
        return rc

    def nvmlDeviceGetCudaComputeCapability(self, handle, major, minor):
        rc = self._enter("nvmlDeviceGetCudaComputeCapability")
        self._handle_ok(handle)
        if rc == 0:
            major.contents.value, minor.contents.value = self.cc
        return rc

    def nvmlDeviceGetMemoryInfo(self, handle, ptr):
        rc = self._enter("nvmlDeviceGetMemoryInfo")
        self._handle_ok(handle)
        if rc == 0:
            ptr.contents.total = self.total_mb * MIB
            ptr.contents.free = self.free_mb * MIB
            ptr.contents.used = (self.total_mb - self.free_mb) * MIB
        return rc


def loader_for(fake):
    seen = []

    def loader(path):
        seen.append(path)
        return fake
    loader.seen = seen
    return loader


def info(**kw):
    base = dict(nvml=True, error="", count=1, index=0, name="NVIDIA GeForce RTX 0000", driver="000.00",
                cuda_driver=12090, cc=(12, 0), total_mb=8151, free_mb=6120)
    base.update(kw)
    return G.CardInfo(**base)


def _no_real_cdll(*_a, **_k):
    raise AssertionError("the real ctypes.CDLL must not be called here")


# ------------------------------------------------------------------------------------------------ tests
class ProbeTests(unittest.TestCase):
    def test_constants(self):
        self.assertTrue(os.path.isabs(G.NVML_DLL))
        self.assertTrue(G.NVML_DLL.lower().endswith(os.path.join("system32", "nvml.dll")))
        self.assertEqual((G.CUDA_DRIVER_MIN, G.MIN_TOTAL_MB, G.NEED_FREE_MB), (12080, 5800, 4096))
        self.assertEqual(G.NO_CARD_ENV, "HDC_TEST_NO_CARD")
        self.assertEqual(G.CARD_CODES, ("NO_NVIDIA_CARD", "DRIVER_TOO_OLD", "CARD_NOT_SUPPORTED", "CARD_TOO_SMALL",
                                        "LOW_CARD_MEMORY", "CARD_FAILED"))

    def test_probe_good_card(self):
        fake = FakeNvml()
        load = loader_for(fake)
        got = G.probe(loader=load)
        self.assertEqual(load.seen, [G.NVML_DLL])                     # the full path, nothing else
        self.assertEqual(got, G.CardInfo(nvml=True, error="", count=1, index=0, name="NVIDIA GeForce RTX 0000",
                                         driver="000.00", cuda_driver=12090, cc=(12, 0), total_mb=8151,
                                         free_mb=6120))
        self.assertEqual(fake.calls[0], "nvmlInit_v2")
        self.assertEqual(fake.calls[-1], "nvmlShutdown")
        self.assertEqual(fake.calls.count("nvmlShutdown"), 1)
        self.assertEqual(set(fake.calls), set(NVML_FUNCS))
        self.assertEqual(fake.indexes, [0])                           # card 0 in PCI order
        self.assertEqual(G.problems(got, SPEC_ARCH), [])

    def test_no_card_env_loads_nothing(self):
        with mock.patch.dict(os.environ, {G.NO_CARD_ENV: "1"}), \
                mock.patch.object(G, "_system_loader", side_effect=AssertionError("loader called")) as sl, \
                mock.patch.object(ctypes, "CDLL", side_effect=_no_real_cdll) as cdll:
            got = G.probe()
            self.assertIsNone(G.free_mb())
            self.assertIsNone(G.free_mb(0))
        sl.assert_not_called()
        cdll.assert_not_called()
        self.assertFalse(got.nvml)
        self.assertIn(G.NO_CARD_ENV, got.error)
        self.assertEqual([p.code for p in G.problems(got, SPEC_ARCH)], ["NO_NVIDIA_CARD"])

    def test_injected_loader_is_used_even_with_the_test_switch(self):
        with mock.patch.dict(os.environ, {G.NO_CARD_ENV: "1"}):
            got = G.probe(loader=loader_for(FakeNvml()))
            free = G.free_mb(0, loader=loader_for(FakeNvml(free_mb=3000)))
        self.assertTrue(got.nvml)
        self.assertEqual(free, 3000)

    def test_init_failure(self):
        fake = FakeNvml(fail={"nvmlInit_v2": 9})
        got = G.probe(loader=loader_for(fake))
        self.assertFalse(got.nvml)
        self.assertEqual(got.error, "nvmlInit_v2 failed (NVML error 9)")
        self.assertEqual(fake.calls, ["nvmlInit_v2"])                 # no shutdown after a failed init
        self.assertEqual([p.code for p in G.problems(got, SPEC_ARCH)], ["NO_NVIDIA_CARD"])

    def test_loader_errors_are_scrubbed(self):
        secret = r"C:\Some\Private\Folder\nvml.dll"
        for exc, want in ((FileNotFoundError(secret), "nvml.dll not found"),
                          (OSError(secret), "nvml.dll could not be loaded (OSError)"),
                          (RuntimeError(secret), "nvml.dll could not be loaded (RuntimeError)")):
            got = G.probe(loader=mock.Mock(side_effect=exc))
            self.assertFalse(got.nvml)
            self.assertEqual(got.error, want)
            self.assertNotIn("\\", got.error)
        self.assertEqual(G.probe(loader=lambda _p: None).error, "nvml.dll not found")

    def test_no_device(self):
        fake = FakeNvml(count=0)
        got = G.probe(loader=loader_for(fake))
        self.assertTrue(got.nvml)
        self.assertEqual((got.count, got.driver, got.cuda_driver), (0, "000.00", 12090))
        self.assertEqual(fake.calls[-1], "nvmlShutdown")
        self.assertNotIn("nvmlDeviceGetHandleByIndex_v2", fake.calls)
        self.assertEqual([p.code for p in G.problems(got, SPEC_ARCH)], ["NO_NVIDIA_CARD"])

    def test_required_device_calls(self):
        for fn in ("nvmlDeviceGetCount_v2", "nvmlDeviceGetHandleByIndex_v2", "nvmlDeviceGetCudaComputeCapability",
                   "nvmlDeviceGetMemoryInfo"):
            with self.subTest(fn=fn):
                fake = FakeNvml(fail={fn: 15})
                got = G.probe(loader=loader_for(fake))
                self.assertFalse(got.nvml)
                self.assertEqual(got.error, f"{fn} failed (NVML error 15)")
                self.assertEqual(fake.calls[-1], "nvmlShutdown")      # shut down after a successful init
                self.assertEqual([p.code for p in G.problems(got, SPEC_ARCH)], ["NO_NVIDIA_CARD"])

    def test_soft_calls(self):
        got = G.probe(loader=loader_for(FakeNvml(fail={"nvmlDeviceGetName": 3, "nvmlSystemGetDriverVersion": 3})))
        self.assertTrue(got.nvml)
        self.assertEqual((got.name, got.driver, got.total_mb), ("", "", 8151))

    def test_cuda_version_fallback(self):
        fake = FakeNvml(missing={"nvmlSystemGetCudaDriverVersion_v2"}, plain_cuda=12080)
        got = G.probe(loader=loader_for(fake))
        self.assertEqual(got.cuda_driver, 12080)
        self.assertIn("nvmlSystemGetCudaDriverVersion", fake.calls)
        fake = FakeNvml(missing={"nvmlSystemGetCudaDriverVersion_v2", "nvmlSystemGetCudaDriverVersion"})
        got = G.probe(loader=loader_for(fake))
        self.assertTrue(got.nvml)
        self.assertEqual(got.cuda_driver, 0)
        self.assertEqual([p.code for p in G.problems(got, SPEC_ARCH)], ["DRIVER_TOO_OLD"])

    def test_missing_required_function(self):
        got = G.probe(loader=loader_for(FakeNvml(missing={"nvmlDeviceGetCount_v2"})))
        self.assertFalse(got.nvml)
        self.assertEqual(got.error, "nvmlDeviceGetCount_v2 is missing")

    def test_probe_never_raises(self):
        secret = r"C:\Private\x"
        for fn in ("nvmlInit_v2", "nvmlDeviceGetMemoryInfo", "nvmlShutdown", "nvmlDeviceGetName"):
            with self.subTest(fn=fn):
                got = G.probe(loader=loader_for(FakeNvml(boom={fn: RuntimeError(secret)})))
                self.assertNotIn("\\", got.error)
                if fn == "nvmlInit_v2":
                    self.assertEqual(got.error, "nvmlInit_v2 failed (RuntimeError)")
                elif fn == "nvmlDeviceGetMemoryInfo":
                    self.assertEqual(got.error, "nvmlDeviceGetMemoryInfo failed (RuntimeError)")
                else:                                                  # a broken shutdown or name is harmless
                    self.assertTrue(got.nvml)

    def test_name_is_cleaned(self):
        got = G.probe(loader=loader_for(FakeNvml(name=b"NVIDIA GeForce RTX 0000\x07\x1b ")))
        self.assertEqual(got.name, "NVIDIA GeForce RTX 0000")

    def test_free_mb(self):
        fake = FakeNvml(free_mb=4321)
        self.assertEqual(G.free_mb(0, loader=loader_for(fake)), 4321)
        self.assertEqual(fake.calls, ["nvmlInit_v2", "nvmlDeviceGetHandleByIndex_v2", "nvmlDeviceGetMemoryInfo",
                                      "nvmlShutdown"])
        fake = FakeNvml()
        G.free_mb(1, loader=loader_for(fake))
        self.assertEqual(fake.indexes, [1])
        self.assertIsNone(G.free_mb(0, loader=loader_for(FakeNvml(fail={"nvmlInit_v2": 9}))))
        fake = FakeNvml(fail={"nvmlDeviceGetMemoryInfo": 15})
        self.assertIsNone(G.free_mb(0, loader=loader_for(fake)))
        self.assertEqual(fake.calls[-1], "nvmlShutdown")
        self.assertIsNone(G.free_mb(0, loader=mock.Mock(side_effect=OSError("x"))))
        self.assertIsNone(G.free_mb(0, loader=loader_for(FakeNvml(boom={"nvmlDeviceGetMemoryInfo": ValueError()}))))


class RealLoaderTests(unittest.TestCase):
    """The default loader, with NVML_DLL pointed at made-up files and ctypes.CDLL replaced: never the real DLL."""

    def setUp(self):
        TASK_DIR.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="gpuinfo_", dir=str(TASK_DIR)))
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        os.environ.pop(G.NO_CARD_ENV, None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_missing_file_is_not_loaded(self):
        fake_path = str(self.dir / "System32" / "nvml.dll")
        with mock.patch.object(G, "NVML_DLL", fake_path), \
                mock.patch.object(ctypes, "CDLL", side_effect=_no_real_cdll) as cdll:
            got = G.probe()
            free = G.free_mb()
        cdll.assert_not_called()
        self.assertEqual((got.nvml, got.error), (False, "nvml.dll not found"))
        self.assertIsNone(free)

    def test_loads_by_full_path(self):
        fake_path = self.dir / "nvml.dll"
        fake_path.write_bytes(b"not a real dll")
        fake = FakeNvml()
        with mock.patch.object(G, "NVML_DLL", str(fake_path)), \
                mock.patch.object(ctypes, "CDLL", return_value=fake) as cdll:
            got = G.probe()
        cdll.assert_called_once_with(str(fake_path))
        self.assertTrue(os.path.isabs(cdll.call_args[0][0]))
        self.assertTrue(got.nvml)
        self.assertEqual(got.name, "NVIDIA GeForce RTX 0000")


class ArchTests(unittest.TestCase):
    def test_arch_ok(self):
        yes = [(7, 5), (8, 0), (8, 6), (8, 7), (8, 9), (9, 0), (10, 0), (10, 3), (12, 0), (12, 1)]
        no = [(3, 5), (5, 2), (6, 1), (7, 0), (7, 2), (11, 0), (13, 0), (0, 0)]
        for cc in yes:
            self.assertTrue(G.arch_ok(cc, SPEC_ARCH), cc)
        for cc in no:
            self.assertFalse(G.arch_ok(cc, SPEC_ARCH), cc)

    def test_arch_ok_forms(self):
        self.assertTrue(G.arch_ok((8, 6), "sm_75 sm_80 sm_86"))
        self.assertTrue(G.arch_ok([8, 6], "sm_75,sm_86"))
        self.assertTrue(G.arch_ok((13, 0), ["sm_75", "compute_80"]))          # PTX: any newer card
        self.assertTrue(G.arch_ok((8, 0), ["compute_80"]))
        self.assertFalse(G.arch_ok((7, 5), ["compute_80"]))
        self.assertTrue(G.arch_ok((9, 0), ["sm_90a"]))                         # arch-specific: exact only
        self.assertFalse(G.arch_ok((9, 1), ["sm_90a"]))
        self.assertTrue(G.arch_ok((10, 3), ["sm_100f"]))
        self.assertFalse(G.arch_ok((8, 6), []))
        self.assertFalse(G.arch_ok((8, 6), None))
        self.assertFalse(G.arch_ok((8, 6), ["junk", "sm_x"]))
        self.assertFalse(G.arch_ok(None, SPEC_ARCH))

    def test_arch_range(self):
        self.assertEqual(G.arch_range(SPEC_ARCH), ((7, 5), (12, 0)))
        self.assertEqual(G.arch_range(None), ((7, 5), (12, 0)))
        self.assertEqual(tuple(G.TORCH_ARCH_FLAGS), tuple(SPEC_ARCH))


class ProblemTests(unittest.TestCase):
    def codes(self, i, flags=SPEC_ARCH):
        return [p.code for p in G.problems(i, flags)]

    def test_order_and_values(self):
        bad = info(cuda_driver=12020, cc=(6, 1), total_mb=4096, driver="000.01", name="NVIDIA GeForce GTX 0000")
        got = G.problems(bad, SPEC_ARCH)
        self.assertEqual([p.code for p in got], ["DRIVER_TOO_OLD", "CARD_NOT_SUPPORTED", "CARD_TOO_SMALL"])
        self.assertEqual(got[0].values, {"driver": "000.01"})
        self.assertEqual(got[1].values, {"name": "NVIDIA GeForce GTX 0000", "which": "old"})
        self.assertEqual(got[2].values, {"name": "NVIDIA GeForce GTX 0000", "total": "4.0"})
        for p in got:
            for v in p.values.values():
                self.assertIsInstance(v, str)
                self.assertNotIn("\\", v)

    def test_no_card_comes_alone(self):
        self.assertEqual(self.codes(G.CardInfo(nvml=False, error="nvml.dll not found")), ["NO_NVIDIA_CARD"])
        self.assertEqual(self.codes(info(count=0, cuda_driver=0, cc=(0, 0), total_mb=0)), ["NO_NVIDIA_CARD"])
        self.assertEqual(G.problems(G.CardInfo(nvml=False), SPEC_ARCH)[0].values, {})

    def test_thresholds(self):
        self.assertEqual(self.codes(info(cuda_driver=12080)), [])
        self.assertEqual(self.codes(info(cuda_driver=12079)), ["DRIVER_TOO_OLD"])
        self.assertEqual(self.codes(info(total_mb=5800)), [])
        self.assertEqual(self.codes(info(total_mb=5799)), ["CARD_TOO_SMALL"])
        self.assertEqual(self.codes(info(total_mb=6144, cc=(7, 5))), [])          # an RTX 20 series 6 GB card
        self.assertEqual(self.codes(info(cc=(8, 9))), [])                         # RTX 40 series

    def test_not_supported_old_and_new(self):
        self.assertEqual(G.problems(info(cc=(7, 0)), SPEC_ARCH)[0].values["which"], "old")
        self.assertEqual(G.problems(info(cc=(13, 0)), SPEC_ARCH)[0].values["which"], "new")
        self.assertEqual(G.problems(info(cc=(11, 0)), SPEC_ARCH)[0].values["which"], "new")

    def test_without_arch_flags_uses_the_torch_list(self):
        self.assertEqual(self.codes(info(cc=(12, 0)), None), [])
        self.assertEqual(self.codes(info(cc=(12, 0)), []), [])
        self.assertEqual(self.codes(info(cc=(6, 1)), ()), ["CARD_NOT_SUPPORTED"])


class EnvTests(unittest.TestCase):
    ENV = {"PATH": r"C:\Windows", "OPENBLAS_NUM_THREADS": "1", "SystemRoot": r"C:\Windows",
           "PYTORCH_CUDA_ALLOC_CONF": "max_split_size_mb:256", "pytorch_no_cuda_memory_caching": "1",
           "TORCH_HOME": r"C:\x", "Torch_Cudnn_V8_Api": "1", "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
           "CUDNN_LOGINFO_DBG": "1", "CUDA_PATH": r"C:\cuda", "CUDA_PATH_V12_8": r"C:\cuda",
           "cuda_visible_devices": "1,0", "CUDA_DEVICE_ORDER": "FASTEST_FIRST", "CUDA_LAUNCH_BLOCKING": "1",
           "NVIDIA_TF32_OVERRIDE": "0", "nvidia_tf32_override": "1", "NVIDIA_OTHER": "kept",
           "TORCHINDUCTOR_CACHE_DIR": "kept", "MY_CUDA_THING": "kept", "HDC_DATA_DIR": "kept"}

    def test_card_env(self):
        env = dict(self.ENV)
        got = G.card_env(env, 0)
        self.assertEqual(env, self.ENV)                                           # the input is not changed
        kept = {"PATH", "OPENBLAS_NUM_THREADS", "SystemRoot", "NVIDIA_OTHER", "TORCHINDUCTOR_CACHE_DIR",
                "MY_CUDA_THING", "HDC_DATA_DIR"}
        self.assertEqual(set(got), kept | {"CUDA_DEVICE_ORDER", "CUDA_VISIBLE_DEVICES"})   # sets nothing else
        for k in kept:
            self.assertEqual(got[k], self.ENV[k])
        self.assertEqual(got["CUDA_DEVICE_ORDER"], "PCI_BUS_ID")
        self.assertEqual(got["CUDA_VISIBLE_DEVICES"], "0")
        self.assertEqual(G.card_env({}, 2), {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": "2"})

    def test_apply_card_env_in_place(self):
        env = dict(self.ENV)
        gone = G.apply_card_env(env, 0)
        self.assertEqual(env, G.card_env(self.ENV, 0))
        self.assertIn("CUDA_PATH", gone)
        self.assertIn("pytorch_no_cuda_memory_caching", gone)
        self.assertNotIn("PATH", gone)
        self.assertEqual(gone, sorted(gone))


class TextTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(G.gb_text(8192), "8.0")
        self.assertEqual(G.gb_text(6120), "6.0")
        self.assertEqual(G.gb_text(None), "?")
        self.assertEqual([G.cuda_text(v) for v in (12090, 12080, 12010, 13000, 11080, 0)],
                         ["12.9", "12.8", "12.1", "13.0", "11.8", "?"])
        self.assertEqual(G.cc_text((12, 0)), "12.0")

    def test_card_fields(self):
        self.assertEqual(G.card_fields(info()), {"name": "NVIDIA GeForce RTX 0000", "cc": "12.0", "driver": "000.00",
                                                 "total_mb": 8151, "free_mb": 6120})

    def test_selftest_line(self):
        self.assertEqual(G.selftest_line(info(total_mb=8192, free_mb=6144), SPEC_ARCH),
                         "Graphics card: NVIDIA GeForce RTX 0000 | 8.0 GB total, 6.0 GB free | driver 000.00 "
                         "(CUDA 12.9) | compute 12.0 (this build: 7.5-12.0)")
        self.assertEqual(G.selftest_line(info(total_mb=8192, free_mb=6144)),
                         G.selftest_line(info(total_mb=8192, free_mb=6144), SPEC_ARCH))
        self.assertTrue(G.selftest_line(info(count=2)).endswith("| 2 NVIDIA cards: the first in PCI order is used"))
        self.assertEqual(G.selftest_line(G.CardInfo(nvml=False, error="nvml.dll not found")),
                         "Graphics card: none found (nvml.dll not found)")
        self.assertEqual(G.selftest_line(info(count=0)),
                         "Graphics card: none found (the NVIDIA driver reports no card) | driver 000.00 (CUDA 12.9)")


class SourceRuleTests(unittest.TestCase):
    """Standard library and ctypes only; never torch, never hooks; no C:\\Hangeul string in the module."""

    def test_imports_and_strings(self):
        src = Path(G.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0] if node.level == 0 else "<relative>")
        self.assertLessEqual(mods, {"__future__", "ctypes", "os", "re", "dataclasses", "typing"})
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertFalse([s for s in strings if "hangeul" in s.lower()])

    def test_import_does_not_load_torch(self):
        self.assertNotIn("torch", G.__dict__)
        self.assertNotIn("hooks", G.__dict__)                # the AST test above is the real check (another
                                                             # test module may register a fake torch)


def tearDownModule():
    try:
        os.rmdir(TASK_DIR)                                   # only when empty; the shared selftest\app stays
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
