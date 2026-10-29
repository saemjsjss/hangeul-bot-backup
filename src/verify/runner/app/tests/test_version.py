"""Task C: version.py (APP_SPEC rev 1 section 6.3, revision 2 R2.4.4). Made-up BUILD.json files under Task C's
scratch folder; one child process (the runner venv, -I -B) checks the module constants with HDC_BUNDLE_DIR."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from src.verify.runner import model
from src.verify.runner.app import version as V
from src.verify.runner.app.tests import fake_worker as F

GOOD = {
    "app_version": "1.0.0", "build_id": "20261029.1-abc1234", "flavour": "gpu-cu128",
    "runner_version": model.RUNNER_VERSION, "base_rules_version": model.BASE_RULES_VERSION,
    "runner_rules_version": model.RUNNER_RULES_VERSION, "spec": "RUNNER_SPEC r2.7 / APP_SPEC r2",
    "baseline": "r8 (runner 0.2.7, 344b342)", "built": "2026-10-29T12:00:00",
    "torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "easyocr": "1.7.2", "cuda": "12.8", "cudnn": "9.19.0",
    "arch_flags": "sm_75 sm_80 sm_86 sm_90 sm_100 sm_120", "cuda_driver_min": 12080, "cap_gb": 3.0,
    "need_free_mb": 4096, "min_total_mb": 5800,
    "models": {"craft_mlt_25k.pth": {"size": 1, "md5": "0" * 32, "sha256": "0" * 64}},
    "vcrt": {"msvcp140.dll": "14.50"},
}


class Load(unittest.TestCase):
    def setUp(self):
        self.dir = F.scratch("version")

    def tearDown(self):
        F.remove(self.dir)

    def write(self, d, name="BUILD.json"):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            if isinstance(d, str):
                f.write(d)
            else:
                json.dump(d, f)
        return p

    def test_dev_defaults(self):
        info = V.load(frozen=False, environ={})
        self.assertEqual(info.source, "dev")
        self.assertEqual(info.problems, ())
        v = info.values
        self.assertEqual(v["app_version"], "1.0.1")
        self.assertEqual(v["build_id"], "dev")
        self.assertEqual(v["arch_flags"], ("sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"))
        self.assertEqual((v["cuda_driver_min"], v["cap_gb"], v["need_free_mb"], v["min_total_mb"]),
                         (12080, 3.0, 4096, 5800))
        self.assertEqual((v["torch"], v["cuda"]), ("2.11.0+cu128", "12.8"))
        self.assertEqual(v["runner_version"], model.RUNNER_VERSION)
        self.assertEqual(v["runner_rules_version"], model.RUNNER_RULES_VERSION)

    def test_module_constants_in_this_dev_process(self):
        if os.environ.get("HDC_BUNDLE_DIR"):
            self.skipTest("HDC_BUNDLE_DIR is set for this process")
        self.assertEqual(V.SOURCE, "dev")
        self.assertEqual(V.APP_VERSION, "1.0.1")
        self.assertEqual(V.BUILD_ID, "dev")
        self.assertIsInstance(V.ARCH_FLAGS, tuple)
        self.assertEqual(V.CUDA_DRIVER_MIN, 12080)
        self.assertEqual(V.CAP_GB, 3.0)
        self.assertEqual(V.NEED_FREE_MB, 4096)
        self.assertEqual(V.MIN_TOTAL_MB, 5800)
        self.assertEqual(V.TORCH, "2.11.0+cu128")
        self.assertEqual(V.CUDA, "12.8")
        self.assertEqual(V.BASE_RULES_VERSION, model.BASE_RULES_VERSION)
        self.assertFalse(V.FROZEN)

    def test_bundle_file(self):
        p = self.write(GOOD)
        info = V.load(p, frozen=True)
        self.assertEqual(info.source, "bundle")
        self.assertEqual(info.problems, ())
        v = info.values
        self.assertEqual(v["build_id"], "20261029.1-abc1234")
        self.assertEqual(v["arch_flags"], ("sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"))
        self.assertEqual(v["built"], "2026-10-29T12:00:00")
        self.assertEqual(v["models"]["craft_mlt_25k.pth"]["size"], 1)
        self.assertEqual(v["vcrt"], {"msvcp140.dll": "14.50"})            # unknown keys are kept

    def test_paths(self):
        self.assertEqual(V.build_json_path(frozen=True, meipass=r"C:\made\up\_internal"),
                         (os.path.join(r"C:\made\up\_internal", "BUILD.json"), "bundle"))
        self.assertEqual(V.build_json_path(frozen=False, environ={"HDC_BUNDLE_DIR": self.dir}),
                         (os.path.join(self.dir, "BUILD.json"), "bundle-dir"))
        self.assertEqual(V.build_json_path(frozen=False, environ={}), (None, "dev"))

    def test_bundle_dir_with_and_without_build_json(self):
        self.write(dict(GOOD, build_id="20261029.2-def5678", arch_flags=["sm_86", "sm_120"]))
        info = V.load(frozen=False, environ={"HDC_BUNDLE_DIR": self.dir})
        self.assertEqual((info.source, info.values["build_id"]), ("bundle-dir", "20261029.2-def5678"))
        self.assertEqual(info.values["arch_flags"], ("sm_86", "sm_120"))
        empty = F.scratch("version_empty")
        try:
            info = V.load(frozen=False, environ={"HDC_BUNDLE_DIR": empty})
            self.assertEqual((info.source, info.values["build_id"], info.problems), ("dev", "dev", ()))
        finally:
            F.remove(empty)

    def test_frozen_missing_or_damaged(self):
        info = V.load(frozen=True, meipass=self.dir)
        self.assertEqual(info.source, "missing")
        self.assertEqual(info.values["build_id"], "unknown")
        self.assertEqual(info.problems, ("BUILD.json is missing",))
        self.write("{not json")
        info = V.load(frozen=True, meipass=self.dir)
        self.assertEqual(info.source, "missing")
        self.assertIn("cannot be read", info.problems[0])
        self.assertNotIn(self.dir, info.problems[0])
        self.write("[1, 2]")
        self.assertEqual(V.load(frozen=True, meipass=self.dir).source, "missing")

    def test_bad_values_keep_defaults(self):
        p = self.write(dict(GOOD, cap_gb="3", need_free_mb=0, arch_flags=[1, 2], cuda="", models=[],
                            min_total_mb=True))
        info = V.load(p, frozen=True)
        v = info.values
        self.assertEqual((v["cap_gb"], v["need_free_mb"], v["min_total_mb"], v["cuda"]), (3.0, 4096, 5800, "12.8"))
        self.assertEqual(v["arch_flags"][0], "sm_75")
        self.assertEqual(v["models"], {})
        self.assertEqual(len(info.problems), 6)

    def test_frozen_build_without_ids_and_drifted_rules(self):
        d = dict(GOOD)
        del d["build_id"]
        d["runner_rules_version"] = "1999-01-01.r0"
        info = V.load(self.write(d), frozen=True)
        self.assertEqual(info.values["build_id"], "unknown")
        self.assertIn("BUILD.json has no build_id", info.problems)
        self.assertIn("BUILD.json runner_rules_version differs from the bundled runner", info.problems)

    def test_labels(self):
        info = V.load(self.write(GOOD), frozen=True)
        self.assertEqual(V.arch_range(("sm_75", "sm_86", "sm_120")), ("7.5", "12.0"))
        self.assertEqual(V.arch_range("compute_90 sm_80"), ("8.0", "9.0"))
        self.assertEqual(V.arch_range([]), ("?", "?"))
        self.assertEqual(V.rules_label(info),
                         f"bot {model.BASE_RULES_VERSION} + runner {model.RUNNER_RULES_VERSION}")
        self.assertEqual(V.flavour_label(info), "GPU (CUDA 12.8)")
        self.assertIn("App 1.0.0 (build 20261029.1-abc1234, GPU (CUDA 12.8))", V.footer_text(info))
        self.assertIn(f"runner {model.RUNNER_VERSION}", V.version_line(info))
        facts = V.facts(info)
        self.assertEqual(facts["arch_flags"], list(GOOD["arch_flags"].split()))
        self.assertEqual(facts["cap_gb"], 3.0)
        json.dumps(facts)
        for value in facts.values():
            self.assertNotIn(self.dir, str(value))

    def test_constants_in_a_child_with_a_bundle_dir(self):
        self.write(dict(GOOD, build_id="20261029.3-0000000", cap_gb=2.5))
        code = ("import json\nfrom src.verify.runner.app import version as V\n"
                "print(json.dumps([V.SOURCE, V.BUILD_ID, V.CAP_GB, list(V.ARCH_FLAGS), V.NEED_FREE_MB, V.TORCH]))")
        env = F.child_env(os.path.join(self.dir, "data"), HDC_BUNDLE_DIR=self.dir)
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=120,
                           env=env, cwd=self.dir)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(got, ["bundle-dir", "20261029.3-0000000", 2.5,
                               ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"], 4096, "2.11.0+cu128"])


if __name__ == "__main__":
    unittest.main()
