"""Task 1: safety.py (RUNNER_SPEC section 0 and Task 1 acceptance).

The audit hook cannot be removed once added, so every armed-guard test runs in a CHILD process (the
runner venv, -I -B) and reports its results as JSON. The real bot folder is never probed: the child
points safety.BOT_ROOT at a made-up folder under private-runner, and the write-root tests narrow
safety.WRITE_ROOT to a folder under private-runner, so not a single write outside private-runner is ever
attempted even if the guard failed. Made-up data only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import unittest
import uuid
from pathlib import Path

from src.verify.runner import botimport, safety

PRIVATE = botimport.PRIVATE
SCRATCH = PRIVATE / "tmp"
FORBIDDEN_PROBE = PRIVATE / "selftest" / "forbidden_probe"


def _rmtree(p: Path) -> None:
    """Remove a scratch folder under private-runner; retried, because a virus scanner may hold a file
    that was just written for a moment."""
    import time
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not Path(p).exists():
            return
        time.sleep(0.5)


def _child(code: str, *args: str, cwd: Path = PRIVATE) -> dict:
    r = subprocess.run([sys.executable, "-I", "-B", "-c", textwrap.dedent(code), *args], cwd=str(cwd),
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise AssertionError(f"child failed ({r.returncode}): {r.stderr[-2000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


_ATTEMPT = """
import json, os, shutil, socket, sys, tempfile, urllib.request
from pathlib import Path
from src.verify.runner import safety
res = {}
def attempt(name, fn):
    try:
        fn()
        res[name] = "ok"
    except PermissionError:
        res[name] = "PermissionError"
    except Exception as e:
        res[name] = type(e).__name__
"""


class Pure(unittest.TestCase):
    def test_is_forbidden(self):
        yes = [r"C:\Hangeul\BOT", r"C:\Hangeul\BOT\data\verification\x.json", "c:/hangeul/bot/src/x.py",
               r"\\?\C:\Hangeul\BOT\x", r"C:\Hangeul\VERIFIED STUDENT DOCUMENTS\x\y.pdf",
               r"C:\Hangeul\SAMPLE STUDENT DOCUMENTS\y.pdf", r"C:\Hangeul\DOCCHECK\lab\onnx\.venv-pkg-numpy\Lib\x.py",
               r"C:\Hangeul\BOT\.venv\Lib\site-packages\x.py"]
        no = [r"C:\Hangeul\BOTANY\x", r"C:\Hangeul\DOCCHECK\lab\private-runner\runs\x",
              r"C:\Hangeul\JARVIS\prototype\page-types\src\verify\runner\x.py",
              r"C:\Hangeul\STUDENT DOCUMENTS NOTES\x", r"C:\Hangeul\DOCCHECK\lab\onnx\models\x.onnx"]
        for p in yes:
            with self.subTest(p=p):
                self.assertTrue(safety.is_forbidden(Path(p)))
        for p in no:
            with self.subTest(p=p):
                self.assertFalse(safety.is_forbidden(Path(p)))

    def test_check_out_dir(self):
        inp = Path(r"C:\Users\Public\made-up-input\BUNDLE.pdf")
        ok = safety.check_out_dir(PRIVATE / "runs" / "CASE_X", inp)
        self.assertEqual(ok, (PRIVATE / "runs" / "CASE_X").resolve())
        folder_in = PRIVATE / "selftest" / "test_safety" / "in"
        bad = [(Path(r"C:\Users\Public\runner-out"), inp),                 # outside private-runner
               (Path(r"runs\CASE_X"), inp),                                 # relative
               (PRIVATE, inp),                                              # the root itself
               (PRIVATE / ".." / "escaped", inp),                           # escapes by ..
               (Path(r"C:\Hangeul\BOT\data\out"), inp),                     # the bot
               (folder_in / "out", folder_in),                              # inside the input
               (folder_in, folder_in),                                      # the input itself
               (PRIVATE / "selftest", folder_in),                           # the input inside --out
               (PRIVATE / "runs" / "CASE_X", Path(r"relative\BUNDLE.pdf"))]  # a relative input
        for out, i in bad:
            with self.subTest(out=str(out), inp=str(i)):
                with self.assertRaises(safety.SafetyError):
                    safety.check_out_dir(out, i)

    def test_write_decision_default_roots(self):
        pol = safety._Policy(write_roots=[safety._norm(PRIVATE)], allowed_files=[], forbidden=[],
                             bot_root=safety._norm(safety.BOT), state=safety.SafetyState(None, None, {}))
        allowed = [PRIVATE / "runs" / "X" / "summary.txt", PRIVATE / "tmp" / "a.json"]
        refused = [Path(r"C:\Users\Public\a.txt"), Path(r"C:\Hangeul\BOT\data\a.json"), PRIVATE / ".." / "a.txt",
                   Path(r"C:\Hangeul\DOCCHECK\gpu-dev.lock"), Path(r"C:\Hangeul\VERIFIED STUDENT DOCUMENTS\x.pdf")]
        for p in allowed:
            with self.subTest(p=str(p)):
                self.assertIsNone(safety._write_refusal(pol, safety._norm(p)))
        for p in refused:
            with self.subTest(p=str(p)):
                self.assertIsNotNone(safety._write_refusal(pol, safety._norm(p)))
        pol.allowed_files = [safety._norm(Path(r"C:\Hangeul\DOCCHECK\gpu-dev.lock"))]
        self.assertIsNone(safety._write_refusal(pol, safety._norm(Path(r"C:\Hangeul\DOCCHECK\gpu-dev.lock"))))
        self.assertIsNotNone(safety._write_refusal(pol, safety._norm(Path(r"C:\Hangeul\DOCCHECK\gpu-dev.lock2"))))

    def test_assert_writable(self):
        safety.assert_writable(PRIVATE / "runs" / "X" / "docs")
        for p in (Path(r"C:\Users\Public\x"), Path(r"C:\Hangeul\BOT\data"), Path("relative\\x")):
            with self.subTest(p=str(p)):
                with self.assertRaises(safety.SafetyError):
                    safety.assert_writable(p)

    def test_snapshot(self):
        base = SCRATCH / f"test_safety-snap-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        base.mkdir(parents=True)
        try:
            (base / "a.pdf").write_bytes(b"made-up")
            s1 = safety.snapshot([base])
            self.assertEqual(list(s1), [str(base / "a.pdf")])
            size, mtime_ns, sha = s1[str(base / "a.pdf")]
            self.assertEqual(size, 7)
            self.assertEqual(len(sha), 64)
            self.assertEqual(safety.snapshot([base / "a.pdf"]), s1)
            self.assertEqual(safety.snapshot([base / "missing.pdf"]), {})
        finally:
            _rmtree(base)


class ArmedRunnerGuard(unittest.TestCase):
    """prepare(): the runner's guard, in a child process."""

    def setUp(self):
        self.base = SCRATCH / f"test_safety-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        (self.base / "fakebot").mkdir(parents=True)
        (self.base / "fakebot" / "a.txt").write_text("made-up", encoding="utf-8")
        (self.base / "input.pdf").write_bytes(b"%PDF-1.4 made-up")
        self.probe_existed = FORBIDDEN_PROBE.exists()

    def tearDown(self):
        _rmtree(self.base)

    def test_prepare_guard(self):
        code = _ATTEMPT + """
base, probe = Path(sys.argv[1]), Path(sys.argv[2])
safety.BOT_ROOT = base / "fakebot"          # stands in for C:\\Hangeul\\BOT, which is never probed
state = safety.prepare(base / "out", base / "cache", base / "input.pdf", extra_forbidden=[probe])
res["armed"] = safety.armed() is state
attempt("write_probe_file", lambda: (probe / "x.txt").write_text("x"))
attempt("mkdir_probe", lambda: probe.mkdir(parents=True))
attempt("os_open_probe", lambda: os.close(os.open(str(probe / "y.txt"), os.O_CREAT | os.O_WRONLY)))
attempt("write_out", lambda: (base / "out" / "ok.txt").write_text("x"))
attempt("replace_in_out", lambda: os.replace(base / "out" / "ok.txt", base / "out" / "ok2.txt"))
attempt("read_input", lambda: (base / "input.pdf").read_bytes())
attempt("read_bot", lambda: (base / "fakebot" / "a.txt").read_text())
attempt("list_bot", lambda: os.listdir(base / "fakebot"))
attempt("write_bot", lambda: (base / "fakebot" / "b.txt").write_text("x"))
attempt("socket", lambda: socket.create_connection(("127.0.0.1", 9), timeout=1))
attempt("dns", lambda: socket.gethostbyname("localhost"))
attempt("urllib", lambda: urllib.request.urlopen("http://127.0.0.1:9/", timeout=1))
attempt("tempfile", lambda: tempfile.NamedTemporaryFile().close())
attempt("mkdir_existing", lambda: (base / "out").mkdir(parents=True, exist_ok=True))
res["tempdir"] = tempfile.gettempdir()
res["dont_write_bytecode"] = sys.dont_write_bytecode
res["bot_opens"] = state.bot_opens
res["blocked"] = len(state.blocked)
res["finish_unchanged"] = safety.finish(state)["input_unchanged"]
attempt("write_input", lambda: (base / "input.pdf").write_bytes(b"%PDF-1.4 changed"))
safety.install_write_guard()            # a policy without the input: the test changes it on purpose
(base / "input.pdf").write_bytes(b"%PDF-1.4 changed")
try:
    safety.finish(state)
    res["finish_after_change"] = "no error"
except safety.SafetyError:
    res["finish_after_change"] = "SafetyError"
print(json.dumps(res))
"""
        r = _child(code, str(self.base), str(FORBIDDEN_PROBE))
        self.assertTrue(r["armed"])
        for k in ("write_probe_file", "os_open_probe", "read_bot", "list_bot", "write_bot",
                  "socket", "dns", "urllib", "write_input"):
            with self.subTest(k=k):
                self.assertEqual(r[k], "PermissionError")
        self.assertEqual(r["mkdir_probe"], "CreateRefused")                # a folder creation: never retried
        for k in ("write_out", "replace_in_out", "read_input", "tempfile", "mkdir_existing"):
            with self.subTest(k=k):
                self.assertEqual(r[k], "ok")
        self.assertEqual(Path(r["tempdir"]).resolve(), (PRIVATE / "tmp").resolve())
        self.assertTrue(r["dont_write_bytecode"])
        self.assertEqual(r["bot_opens"], 3)
        self.assertGreaterEqual(r["blocked"], 9)
        self.assertTrue(r["finish_unchanged"])
        self.assertEqual(r["finish_after_change"], "SafetyError")
        if not self.probe_existed:
            self.assertFalse(FORBIDDEN_PROBE.exists())
        self.assertFalse((FORBIDDEN_PROBE / "x.txt").exists())
        self.assertFalse((FORBIDDEN_PROBE / "y.txt").exists())
        self.assertFalse((self.base / "fakebot" / "b.txt").exists())
        self.assertTrue((self.base / "out" / "ok2.txt").exists())

    def test_prepare_refuses(self):
        code = _ATTEMPT + """
base = Path(sys.argv[1])
# a narrowed guard first: if a check broke, nothing outside the scratch folder could be created, and the real
# bot folder is never looked up (a fake one stands in for it)
safety.WRITE_ROOT = base
safety.BOT_ROOT = base / "fakebot"
safety.install_write_guard()
(base / "out_is_a_file").write_text("made-up")
for name, args in [("out_outside", (Path(r"C:\\Users\\Public\\runner-out"), base / "cache", base / "input.pdf")),
                   ("cache_outside", (base / "out", Path(r"C:\\Users\\Public\\runner-cache"), base / "input.pdf")),
                   ("input_missing", (base / "out", base / "cache", base / "missing.pdf")),
                   ("input_under_bot", (base / "out", base / "cache", base / "fakebot" / "a.txt")),
                   ("out_is_a_file", (base / "out_is_a_file", base / "cache", base / "input.pdf"))]:
    try:
        safety.prepare(*args)
        res[name] = "no error"
    except safety.SafetyError:
        res[name] = "SafetyError"
res["armed_by_prepare"] = safety.armed().mode == "runner"
print(json.dumps(res))
"""
        r = _child(code, str(self.base))
        for k in ("out_outside", "cache_outside", "input_missing", "input_under_bot", "out_is_a_file"):
            with self.subTest(k=k):
                self.assertEqual(r[k], "SafetyError")
        self.assertFalse(r["armed_by_prepare"])
        self.assertFalse(Path(r"C:\Users\Public\runner-out").exists())

    def test_runner_guard_extras(self):
        """Hard links, junctions, device and local-share spellings, spawns, sqlite, CreateFile and
        CreateJunction, the input itself, and a refused temp file that tempfile must not retry."""
        code = _ATTEMPT + """
import _winapi, sqlite3, subprocess
base = Path(sys.argv[1])
safety.BOT_ROOT = base / "fakebot"
safety.WRITE_ROOT = base                # "outside" is the scratch folder's parent, still inside private-runner
jn = base / "junction_to_bot"           # made before the guard is armed, inside the scratch folder
_winapi.CreateJunction(str(base / "fakebot"), str(jn))
state = safety.prepare(base / "out", base / "cache", base / "input.pdf")
bot_file = base / "fakebot" / "a.txt"
attempt("read_via_junction", lambda: (jn / "a.txt").read_text())
attempt("list_via_junction", lambda: os.listdir(jn))
attempt("unc_long_spelling", lambda: open(chr(92) * 2 + "?" + chr(92) + "UNC" + chr(92) + "localhost" + chr(92)
                                          + str(bot_file)[0] + "$" + str(bot_file)[2:]).read())
try:
    safety.check_input(jn / "a.txt")
    res["check_input_via_junction"] = "ok"
except safety.SafetyError:
    res["check_input_via_junction"] = "SafetyError"
attempt("hardlink_from_bot", lambda: os.link(bot_file, base / "out" / "linked.txt"))
bs = chr(92)
attempt("read_device_spelling", lambda: open(bs + bs + "." + bs + str(bot_file)).read())
drive, rest = str(bot_file)[0], str(bot_file)[2:]
attempt("read_share_spelling", lambda: open(bs + bs + "localhost" + bs + drive + "$" + rest).read())
attempt("spawn", lambda: subprocess.run([sys.executable, "-c", "pass"]))
attempt("sqlite_outside", lambda: sqlite3.connect(str(base.parent / "x-never.db")))
attempt("sqlite_bot", lambda: sqlite3.connect(str(base / "fakebot" / "x.db")))
attempt("createfile_bot", lambda: _winapi.CloseHandle(_winapi.CreateFile(str(bot_file), 0x80000000, 1, 0, 3, 0, 0)))
attempt("junction", lambda: _winapi.CreateJunction(str(base / "fakebot"), str(base / "out" / "j")))
attempt("write_input", lambda: (base / "input.pdf").write_bytes(b"changed"))
attempt("temp_outside", lambda: tempfile.mkstemp(dir=str(base.parent)))          # O_EXCL: no retry loop
attempt("named_temp_outside", lambda: tempfile.NamedTemporaryFile(dir=str(base.parent)).close())
attempt("mkdtemp_outside", lambda: tempfile.mkdtemp(dir=str(base.parent)))      # os.mkdir: no retry loop either
attempt("tempdir_outside", lambda: tempfile.TemporaryDirectory(dir=str(base.parent)).cleanup())
attempt("rmdir_junction_to_bot", lambda: os.rmdir(jn))   # its real path is the bot: refused (the parent removes it)
res["blocked_total"] = state.blocked_total
res["blocked_kept"] = len(state.blocked)
res["spawned"] = len(state.spawned)
print(json.dumps(res))
"""
        try:
            r = _child(code, str(self.base))
        finally:
            jn = self.base / "junction_to_bot"
            if os.path.lexists(jn) and os.path.isjunction(jn):
                os.rmdir(jn)                    # the junction itself (no guard here), never its target
        for k in ("hardlink_from_bot", "read_device_spelling", "read_share_spelling", "spawn", "sqlite_outside",
                  "sqlite_bot", "createfile_bot", "junction", "write_input", "read_via_junction",
                  "list_via_junction", "unc_long_spelling", "rmdir_junction_to_bot"):
            with self.subTest(k=k):
                self.assertEqual(r[k], "PermissionError")
        self.assertEqual(r["check_input_via_junction"], "SafetyError")
        for k in ("temp_outside", "mkdtemp_outside", "tempdir_outside"):
            with self.subTest(k=k):
                self.assertEqual(r[k], "CreateRefused")
        self.assertTrue((self.base / "fakebot" / "a.txt").is_file())
        self.assertEqual(r["named_temp_outside"], "PermissionError")
        self.assertEqual(r["spawned"], 1)
        self.assertLessEqual(r["blocked_kept"], safety.BLOCKED_KEEP)
        self.assertGreaterEqual(r["blocked_total"], 10)
        self.assertFalse((self.base / "out" / "linked.txt").exists())
        self.assertFalse((self.base / "out" / "j").exists())
        self.assertEqual((self.base / "input.pdf").read_bytes(), b"%PDF-1.4 made-up")
        self.assertFalse((self.base.parent / "x-never.db").exists())

    def test_hardlink_write_refused(self):
        """A file inside the write root that has another hard link cannot be written (the other name may lie
        anywhere): the link is made here, before the guard is armed, inside the scratch folder."""
        os.link(self.base / "fakebot" / "a.txt", self.base / "twin.txt")
        code = _ATTEMPT + """
base = Path(sys.argv[1])
safety.BOT_ROOT = base / "fakebot"
state = safety.prepare(base / "out", base / "cache", base / "input.pdf")
attempt("write_twin", lambda: (base / "twin.txt").write_text("x"))
attempt("read_twin", lambda: (base / "twin.txt").read_text())
print(json.dumps(res))
"""
        r = _child(code, str(self.base))
        self.assertEqual(r["write_twin"], "PermissionError")
        self.assertEqual(r["read_twin"], "ok")
        self.assertEqual((self.base / "fakebot" / "a.txt").read_text(encoding="utf-8"), "made-up")


class ArmedWorkerGuard(unittest.TestCase):
    """install_write_guard(): the GPU worker's guard, in a child process, with the write root narrowed to a
    folder under private-runner so that "outside" is still inside private-runner."""

    def setUp(self):
        self.base = SCRATCH / f"test_safety-w-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        for d in ("root", "outside", "outside/keepdir", "fakebot", "fakebot/.venv"):
            (self.base / d).mkdir(parents=True, exist_ok=True)
        (self.base / "outside" / "keep.txt").write_text("made-up", encoding="utf-8")
        (self.base / "fakebot" / "a.txt").write_text("made-up", encoding="utf-8")
        (self.base / "fakebot" / ".venv" / "lib.txt").write_text("made-up", encoding="utf-8")

    def tearDown(self):
        _rmtree(self.base)

    def test_write_guard(self):
        code = _ATTEMPT + """
base = Path(sys.argv[1])
lock, sibling = base / "outside" / "dev.lock", base / "outside" / "other.lock"
safety.WRITE_ROOT = base / "root"
safety.BOT_ROOT = base / "fakebot"
state = safety.install_write_guard(extra_allowed=[lock], block_bot_opens=False)
def make_lock(p):
    fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, b"1")
    os.close(fd)
attempt("lock_create", lambda: make_lock(lock))
attempt("lock_touch", lambda: os.utime(lock))
attempt("sibling_create", lambda: make_lock(sibling))
attempt("sibling_write", lambda: sibling.write_text("x"))
attempt("rename_to_sibling", lambda: os.rename(lock, sibling))
attempt("lock_unlink", lambda: lock.unlink())
attempt("write_root", lambda: (base / "root" / "a.txt").write_text("x"))
attempt("write_outside", lambda: (base / "outside" / "b.txt").write_text("x"))
attempt("remove_outside", lambda: os.remove(base / "outside" / "keep.txt"))
attempt("rmtree_outside", lambda: shutil.rmtree(base / "outside" / "keepdir"))
attempt("mkdir_outside", lambda: os.mkdir(base / "outside" / "newdir"))
attempt("copy_outside", lambda: shutil.copyfile(base / "root" / "a.txt", base / "outside" / "c.txt"))
attempt("mkdir_existing_outside", lambda: (base / "outside").mkdir(exist_ok=True))
attempt("read_bot_allowed", lambda: (base / "fakebot" / ".venv" / "lib.txt").read_text())
attempt("read_bot_data", lambda: (base / "fakebot" / "a.txt").read_text())
attempt("socket", lambda: socket.create_connection(("127.0.0.1", 9), timeout=1))
res["blocked"] = len(state.blocked)
state2 = safety.install_write_guard(block_bot_opens=True)
attempt("read_bot_blocked", lambda: (base / "fakebot" / "a.txt").read_text())
res["bot_opens"] = state2.bot_opens
print(json.dumps(res))
"""
        r = _child(code, str(self.base))
        for k in ("lock_create", "lock_touch", "lock_unlink", "write_root", "mkdir_existing_outside",
                  "read_bot_allowed"):
            with self.subTest(k=k):
                self.assertEqual(r[k], "ok")
        for k in ("sibling_write", "rename_to_sibling", "write_outside", "remove_outside",
                  "rmtree_outside", "copy_outside", "socket", "read_bot_blocked", "read_bot_data"):
            with self.subTest(k=k):
                self.assertEqual(r[k], "PermissionError")
        self.assertEqual(r["mkdir_outside"], "CreateRefused")
        self.assertEqual(r["sibling_create"], "CreateRefused")       # tempfile never retries it
        self.assertEqual(r["bot_opens"], 1)
        out = self.base / "outside"
        self.assertFalse((out / "dev.lock").exists())
        self.assertFalse((out / "other.lock").exists())
        self.assertFalse((out / "b.txt").exists())
        self.assertFalse((out / "c.txt").exists())
        self.assertFalse((out / "newdir").exists())
        self.assertTrue((out / "keep.txt").exists())
        self.assertTrue((out / "keepdir").is_dir())
        self.assertTrue((self.base / "root" / "a.txt").exists())


_R2_CODE = _ATTEMPT + """
import ctypes, shutil, stat, time, _winapi
base, mode = Path(sys.argv[1]), sys.argv[2]
fakebot, root, outside = base / "fakebot", base / "root", base / "outside"
k32 = ctypes.WinDLL("kernel32")
buf = ctypes.create_unicode_buffer(1024)
vol = buf.value.rstrip(chr(92)) if k32.GetVolumeNameForVolumeMountPointW(str(base)[:3], buf, 1024) else ""
buf2 = ctypes.create_unicode_buffer(1024)
dev = buf2.value if k32.QueryDosDeviceW(str(base)[:2], buf2, 1024) else ""
bs = chr(92)
glob = bs * 2 + "?" + bs + "GLOBALROOT" + dev
def tail(p):
    return str(p)[2:]                      # without the drive letter and its colon
safety.BOT_ROOT = fakebot
safety.WRITE_ROOT = root
inp = root / "input.pdf"
res["cwd_in_write_root"] = str(Path.cwd()).lower().startswith(str(root).lower())
for name, p in (("check_input_folder_with_junction", root / "infolder"),
                ("check_input_volume_spelling", vol + tail(inp))):
    try:
        safety.check_input(Path(p))
        res[name] = "ok"
    except safety.SafetyError:
        res[name] = "SafetyError"
if mode == "runner":
    try:
        safety.prepare(root / "out_f", root / "cache", root / "infolder")
        res["prepare_folder_with_junction"] = "ok"
    except safety.SafetyError:
        res["prepare_folder_with_junction"] = "SafetyError"
    state = safety.prepare(root / "out", root / "cache", inp)
else:
    state = safety.install_write_guard(block_bot_opens=False, extra_forbidden=[inp])
def read(p):
    with open(p, "rb") as fh:
        return fh.read()
def createfile_write(p):
    h = _winapi.CreateFile(str(p), 0x40000000, 0, 0, 3, 0, 0)       # GENERIC_WRITE, OPEN_EXISTING
    try:
        _winapi.WriteFile(h, b"CHANGED")
    finally:
        _winapi.CloseHandle(h)
res["volume_found"] = bool(vol and dev)
attempt("vol_write_outside", lambda: open(vol + tail(outside / "v.txt"), "w").close())
attempt("vol_write_bot", lambda: open(vol + tail(fakebot / "v.txt"), "w").close())
attempt("vol_append_input", lambda: open(vol + tail(inp), "ab").close())
attempt("vol_read_bot", lambda: read(vol + tail(fakebot / "a.txt")))
attempt("glob_read_bot", lambda: read(glob + tail(fakebot / "a.txt")))
attempt("glob_write_outside", lambda: open(glob + tail(outside / "g.txt"), "w").close())
attempt("vol_mkdir_outside", lambda: os.mkdir(vol + tail(outside / "vdir")))
attempt("vol_link_from_bot", lambda: os.link(vol + tail(fakebot / "t_link.txt"), root / "vol_link.txt"))
attempt("vol_remove_bot", lambda: os.remove(vol + tail(fakebot / "a2.txt")))
attempt("glob_rename_bot", lambda: os.rename(glob + tail(fakebot / "a3.txt"), glob + tail(root / "moved.txt")))
attempt("share_read_bot", lambda: read(bs * 2 + "127.0.0.2" + bs + str(fakebot)[0] + "$" + tail(fakebot / "a.txt")))
attempt("twin_truncate", lambda: os.truncate(root / "twin_trunc.txt", 0))
attempt("twin_utime", lambda: os.utime(root / "twin_utime.txt", (1_000_000_000, 1_000_000_000)))
attempt("twin_chmod", lambda: os.chmod(root / "twin_chmod.txt", stat.S_IREAD))
attempt("twin_createfile_write", lambda: createfile_write(root / "twin_cf.txt"))
attempt("twin_copy2_onto", lambda: shutil.copy2(root / "src.txt", root / "twin_copy2.txt"))
attempt("copy2_outside", lambda: shutil.copy2(root / "src.txt", outside / "copy2.txt"))
attempt("copyfile2_outside", lambda: _winapi.CopyFile2(str(root / "src.txt"), str(outside / "copyfile2.txt"), 0))
attempt("copy2_from_bot", lambda: shutil.copy2(fakebot / "a.txt", root / "from_bot.txt"))
attempt("copy2_onto_input", lambda: shutil.copy2(root / "src.txt", inp))
attempt("copy2_inside", lambda: shutil.copy2(root / "src.txt", root / "copy_ok.txt"))
attempt("stream_write_input", lambda: open(str(inp) + ":ads", "w").close())
t0 = time.perf_counter()
attempt("mkstemp_under_bot", lambda: os.close(tempfile.mkstemp(dir=str(fakebot))[0]))
res["mkstemp_seconds"] = time.perf_counter() - t0
def createprocess():
    hp, ht, pid, tid = _winapi.CreateProcess(None, '"' + sys.executable + '" -I -c "pass"', None, None, False, 0,
                                             None, None, None)
    _winapi.WaitForSingleObject(hp, 20000)
    _winapi.CloseHandle(ht)
    _winapi.CloseHandle(hp)
attempt("createprocess", createprocess)
res["spawned"] = len(state.spawned)
res["blocked_total"] = state.blocked_total
print(json.dumps(res))
"""


class ReviewTwoGuard(unittest.TestCase):
    """Review 2 (S1, S2, S4, S5, S6, S8): the volume-GUID and GLOBALROOT spellings with the current folder inside
    the write root (as run_case starts its children), writes through a hard link made before the guard,
    _winapi.CopyFile2 (shutil.copy2), _winapi.CreateProcess, a temp file under the bot, an NTFS stream of the input
    and an input folder holding a junction. Runner and worker mode, in children; stand-in bot, narrowed root."""

    TWINS = ("trunc", "utime", "chmod", "cf", "copy2", "link")

    def setUp(self):
        import _winapi
        self.base = SCRATCH / f"test_safety-r2-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        fb, root = self.base / "fakebot", self.base / "root"
        for d in (fb, fb / ".venv", root, self.base / "outside", root / "infolder"):
            d.mkdir(parents=True, exist_ok=True)
        for n in ("a.txt", "a2.txt", "a3.txt", ".venv/lib.txt"):
            (fb / n).write_text("made-up", encoding="utf-8")
        for t in self.TWINS:
            (fb / f"t_{t}.txt").write_text("made-up", encoding="utf-8")
            if t != "link":
                os.link(fb / f"t_{t}.txt", root / f"twin_{t}.txt")      # made here, before any guard
        (root / "input.pdf").write_bytes(b"%PDF-1.4 made-up")
        (root / "src.txt").write_text("CHANGED-BY-COPY", encoding="utf-8")
        (root / "infolder" / "PASSPORT.pdf").write_bytes(b"%PDF-1.4 made-up")
        _winapi.CreateJunction(str(fb), str(root / "infolder" / "j_inner"))

    def tearDown(self):
        j = self.base / "root" / "infolder" / "j_inner"
        if os.path.lexists(j) and os.path.isjunction(j):
            os.rmdir(j)                                   # the junction itself, never its target
        for f in self.base.rglob("*"):
            try:
                if f.is_file():
                    os.chmod(f, 0o666)
            except OSError:
                pass
        _rmtree(self.base)

    def _bot_state(self):
        import stat
        out = {}
        for f in sorted((self.base / "fakebot").rglob("*")):
            if f.is_file():
                st = f.stat()
                out[f.name] = (f.read_bytes(), st.st_mtime_ns,
                               bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY))
        return out

    def test_runner_and_worker_modes(self):
        for mode in ("runner", "worker"):
            with self.subTest(mode=mode):
                before, inp = self._bot_state(), (self.base / "root" / "input.pdf").read_bytes()
                r = _child(_R2_CODE, str(self.base), mode, cwd=self.base / "root")
                self.assertTrue(r["cwd_in_write_root"])
                self.assertTrue(r["volume_found"])
                self.assertEqual(r["check_input_folder_with_junction"], "SafetyError")
                self.assertEqual(r["check_input_volume_spelling"], "SafetyError")
                if mode == "runner":
                    self.assertEqual(r["prepare_folder_with_junction"], "SafetyError")
                for k in ("vol_write_outside", "vol_write_bot", "vol_append_input", "vol_read_bot", "glob_read_bot",
                          "glob_write_outside", "vol_link_from_bot", "vol_remove_bot", "glob_rename_bot",
                          "share_read_bot", "twin_truncate", "twin_utime", "twin_chmod", "twin_createfile_write",
                          "twin_copy2_onto", "copy2_outside", "copyfile2_outside", "copy2_from_bot",
                          "copy2_onto_input", "stream_write_input"):
                    self.assertEqual(r[k], "PermissionError", k)
                self.assertEqual(r["vol_mkdir_outside"], "CreateRefused")
                self.assertEqual(r["mkstemp_under_bot"], "CreateRefused")       # never retried
                self.assertLess(r["mkstemp_seconds"], 5.0)
                self.assertEqual(r["copy2_inside"], "ok")
                self.assertEqual(r["createprocess"], "PermissionError" if mode == "runner" else "ok")
                self.assertEqual(r["spawned"], 1)                               # recorded in both modes
                self.assertEqual(self._bot_state(), before)                     # the stand-in bot is untouched
                self.assertEqual((self.base / "root" / "input.pdf").read_bytes(), inp)
                self.assertEqual(sorted(p.name for p in (self.base / "outside").iterdir()), [])
                self.assertFalse((self.base / "root" / "vol_link.txt").exists())
                self.assertFalse((self.base / "root" / "moved.txt").exists())
                self.assertFalse((self.base / "root" / "from_bot.txt").exists())
                for f in ("copy_ok.txt",):
                    p = self.base / "root" / f
                    if p.exists():
                        p.unlink()


if __name__ == "__main__":
    unittest.main()
