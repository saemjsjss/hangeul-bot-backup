"""A scripted fake worker for the controller, entry and spawn tests (Task C). Made-up data only; no card, no torch,
no runner, no real case.

Three uses:
- As the worker MODULE: ``run_job(job, listener)`` plays the scenario named in ``job["fake"]`` through the real
  ``protocol.WorkerListener`` (the entry tests point ``entry.WORKER_MODULE`` here).
- As a raw script: ``python -I -B fake_worker.py raw <scenario>`` writes the protocol stream by hand (hello, card,
  gpu_wait, page, stage, done, error ...) and exits with the scenario's code; it reads ``cancel`` on stdin.
- Through the real entry: ``python -I -B fake_worker.py entry <entry flags>`` installs a fake gpuinfo and a fake
  cardgate, points ``entry.WORKER_MODULE`` at this module and runs ``entry.main`` (``--worker JOB`` / ``--cli``).

Every loop has a hard time limit, so a fake can never outlive a failed test for long.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import types
from typing import Any, Dict, List, Mapping, MutableMapping, Optional

THIS_MODULE = "src.verify.runner.app.tests.fake_worker"
HARD_LIMIT_S = 60.0
TICK_S = 0.05
CARD_NAME = "NVIDIA GeForce RTX 0000"
SCRATCH_ROOT = r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\C"    # Task C's only test folder
FAKE_PATH = os.path.abspath(__file__)


# ------------------------------------------------------------------------------------- test helpers
def scratch(name: str) -> str:
    """A fresh, empty scratch folder under Task C's test folder."""
    import shutil
    p = os.path.join(SCRATCH_ROOT, name)
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not os.path.exists(p):
            break
        time.sleep(0.25)
    os.makedirs(p, exist_ok=True)
    return p


def remove(p: str) -> None:
    import shutil
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not os.path.exists(p):
            return
        time.sleep(0.25)


def child_env(data_dir: str, **extra: str) -> Dict[str, str]:
    """The environment of a test child: no card (HDC_TEST_NO_CARD), DATA under the scratch folder."""
    env = dict(os.environ)
    env["HDC_TEST_NO_CARD"] = "1"
    env["HDC_DATA_DIR"] = data_dir
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def fake_cmd(*args: str) -> List[str]:
    return [sys.executable, "-I", "-B", FAKE_PATH, *args]


# ------------------------------------------------------------------------------------------ fake modules
def make_fake_gpuinfo(index: int = 0, nvml: bool = False, with_apply: bool = True) -> types.ModuleType:
    """A stand-in for Task E's gpuinfo with the contract boot and spawn use: probe, card_env, apply_card_env."""
    m = types.ModuleType("src.verify.runner.app.gpuinfo")
    prefixes = ("PYTORCH_", "TORCH_", "CUBLAS_", "CUDNN_", "CUDA_")

    def removed(k: str) -> bool:
        k = k.upper()
        return k.startswith(prefixes) or k == "NVIDIA_TF32_OVERRIDE"

    def probe(loader: Any = None) -> Any:
        m.calls.append("probe")
        return types.SimpleNamespace(nvml=nvml, index=index, error="" if nvml else "no NVML (test)")

    def card_env(environ: Mapping[str, str], idx: int) -> Dict[str, str]:
        m.calls.append(f"card_env:{idx}")
        out = {k: v for k, v in environ.items() if not removed(k)}
        out["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        out["CUDA_VISIBLE_DEVICES"] = str(int(idx))
        return out

    def apply_card_env(environ: MutableMapping[str, str], idx: int) -> List[str]:
        m.calls.append(f"apply_card_env:{idx}")
        gone = sorted(k for k in list(environ.keys()) if removed(k))
        for k in gone:
            del environ[k]
        environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        environ["CUDA_VISIBLE_DEVICES"] = str(int(idx))
        return gone

    m.calls = []
    m.probe = probe
    m.card_env = card_env
    if with_apply:
        m.apply_card_env = apply_card_env
    return m


def make_fake_cardgate(default: Optional[str] = None) -> types.ModuleType:
    m = types.ModuleType("src.verify.runner.app.cardgate")
    m.default_lock_setting = lambda: default
    return m


def install_fakes(lock_default: Optional[str] = None) -> None:
    """Puts the fake gpuinfo and cardgate in place of Task E's modules (this process only)."""
    import importlib
    pkg = importlib.import_module("src.verify.runner.app")
    gi = make_fake_gpuinfo(index=int(os.environ.get("FAKE_CARD_INDEX", "0")))
    cg = make_fake_cardgate(lock_default)
    sys.modules["src.verify.runner.app.gpuinfo"] = gi
    sys.modules["src.verify.runner.app.cardgate"] = cg
    setattr(pkg, "gpuinfo", gi)
    setattr(pkg, "cardgate", cg)


# --------------------------------------------------------------------------- the worker module: run_job
def _cancelled_type() -> type:
    from src.verify.runner import hooks
    return hooks.Cancelled


def _gpu_block(waits: int = 0, waited: float = 0.0) -> Dict[str, Any]:
    return {"card": CARD_NAME, "peak_alloc_gb": 2.41, "peak_reserved_gb": 2.8, "cpu_calls": 0, "waits": waits,
            "waited_s": waited}


def _done(listener: Any, exit_code: int = 0, overall: str = "PASS", waits: int = 0, waited: float = 0.0) -> None:
    listener("done", exit=exit_code, overall=overall, headline="PASS - made-up", code="0000-0000",
             out="<case>", lines=[["PASS", "MADE-UP DOC (p1): PASS"]], peak_mb=100,
             gpu=_gpu_block(waits, waited))


def run_job(job: Dict[str, Any], listener: Any) -> int:
    """Plays job["fake"]. Uses the listener exactly as the real worker would (keyword fields)."""
    scenario = str(job.get("fake") or os.environ.get("FAKE_SCENARIO") or "done")
    Cancelled = _cancelled_type()
    if scenario == "echo_job":
        listener("jobinfo", program=job["program"], today=job["today"], gpu=job["gpu"],
                 cache_tail=os.path.basename(job["cache_root"]), cwd_is_data=(
                     os.path.normcase(os.getcwd()) == os.path.normcase(os.environ.get("HDC_DATA_DIR", "?"))),
                 visible=os.environ.get("CUDA_VISIBLE_DEVICES"), order=os.environ.get("CUDA_DEVICE_ORDER"),
                 openblas=os.environ.get("OPENBLAS_NUM_THREADS"), leftover=os.environ.get("PYTORCH_FAKE_SETTING"),
                 tmp_is_data=os.environ.get("TMP", "").lower().startswith(
                     os.environ.get("HDC_DATA_DIR", "?").lower()))
        _done(listener)
        return 0
    if scenario == "done":
        listener("stage", key="card", state="start")
        listener("card", state="found", name=CARD_NAME, cc="12.0", driver="000.00", total_mb=8151, free_mb=6120)
        listener("card", state="ready", torch="2.11.0+cu128", cuda="12.8", seconds=1.5)
        listener("stage", key="card", state="end", seconds=1.5)
        listener("stage", key="firstread", state="start")
        listener("page", done=1, of=2, seconds=0.5, where="card")
        listener("page", done=2, of=2, seconds=0.6, where="card")
        listener("stage", key="firstread", state="end", seconds=1.1)
        listener("stage", key="ocr", state="start")
        listener("file", name="made-up.pdf", pages=2, ocr=1)
        listener("ocr", phase="start")
        listener("ocr", phase="end", seconds=0.7, memo=False)
        listener("ocr", phase="end", seconds=0.0, memo=True)
        listener("budget", where="birth.qr", page=1)
        listener("stage", key="ocr", state="end", seconds=0.7)
        print("a stray print that must land in the log, not in the protocol pipe")
        sys.stdout.flush()
        _done(listener)
        return 0
    if scenario == "stop":
        _done(listener, exit_code=3, overall="STOP")
        return 3
    if scenario == "wait_cancel":
        listener("stage", key="card", state="start")
        start = time.monotonic()
        try:
            listener("gpu_wait", state="start", why="lock", lock="bot", where="start", reserved_mb=0)
            while time.monotonic() - start < HARD_LIMIT_S:
                time.sleep(TICK_S)
                listener("gpu_wait", state="tick", why="lock", lock="bot",
                         waited_s=round(time.monotonic() - start, 2))
        except Cancelled:
            listener("cancelled")
            return 4
        listener("error", code="CRASH", text="the fake wait was never cancelled")
        return 1
    if scenario == "card_error":
        listener("stage", key="card", state="start")
        listener("error", code="DRIVER_TOO_OLD", values={"driver": "000.00"}, text="made-up driver")
        return 6
    if scenario == "card_error_unknown":
        listener("error", code="NOT_A_CARD_CODE", values={}, text="made-up")
        return 6
    if scenario == "raise":
        raise RuntimeError("a made-up failure inside run_job")
    if scenario == "no_terminal":
        listener("stage", key="ocr", state="start")
        return 0
    raise ValueError(f"unknown fake scenario {scenario}")


# ---------------------------------------------------------------------------------------- raw script mode
def _exit_status(code: int) -> None:
    """Ends this process with a Windows status code (ExitProcess takes the full 32-bit value)."""
    sys.stdout.flush()
    ctypes.WinDLL("kernel32").ExitProcess(ctypes.c_uint(code))


def raw(scenario: str) -> int:
    from src.verify.runner.app import protocol, spawn
    if scenario == "no_hello_exit1":
        return 1
    if scenario == "status_illegal":
        _exit_status(0xC000001D)
    if scenario == "status_other":
        _exit_status(0xC0000005)
    w = protocol.Writer(spawn.claim_protocol_stdout())
    flag = protocol.CancelFlag()
    protocol.watch_stdin(flag)
    w.send(protocol.hello("1.0.0", os.getpid(), "test"))
    if scenario == "done":
        w.send(protocol.stage("card", "start"))
        w.send(protocol.card_found(CARD_NAME, "12.0", "000.00", 8151, 6120))
        w.send(protocol.page(1, 1, 0.4, "card"))
        w.send(protocol.done(0, "PASS", "PASS - made-up", "0000-0000", "<case>", [["PASS", "x"]], 100,
                             protocol.gpu_block(CARD_NAME, 2.4, 2.8, 0, 0, 0.0)))
        return 0
    if scenario == "junk":
        os.write(1, b"this goes to fd 1, which now points at the log\n")
        print("a print after the claim")
        sys.stdout.flush()
        w._stream.write(b"not json at all\n")
        w.send(protocol.done(0, "PASS", "x", "0000-0000", "<case>", [], 1))
        return 0
    if scenario == "exit6_driver":
        w.send(protocol.stage("card", "start"))
        w.send(protocol.error("DRIVER_TOO_OLD", "made-up", {"driver": "000.00"}))
        return 6
    if scenario == "exit6_unknown":
        w.send(protocol.error("SOMETHING_ELSE", "made-up"))
        return 6
    if scenario == "exit6_noerror":
        w.send(protocol.stage("firstread", "start"))
        return 6
    if scenario in ("wait_cancel", "wait_ignore_cancel"):
        start = time.monotonic()
        w.send(protocol.stage("firstread", "start"))
        w.send(protocol.gpu_wait("start", "lock", lock="bot", where="page", reserved_mb=120))
        while time.monotonic() - start < HARD_LIMIT_S:
            time.sleep(TICK_S)
            if flag.is_set() and scenario == "wait_cancel":
                w.send(protocol.cancelled())
                return 4
            w.send(protocol.gpu_wait("tick", "lock", lock="bot", waited_s=round(time.monotonic() - start, 2)))
        return 1
    if scenario == "wait_then_done":
        w.send(protocol.gpu_wait("start", "memory", free_mb=2900, need_mb=4096))
        for i in range(3):
            time.sleep(TICK_S)
            w.send(protocol.gpu_wait("tick", "memory", free_mb=2900, need_mb=4096, waited_s=0.05 * (i + 1)))
        w.send(protocol.gpu_wait("end", "memory", free_mb=5000, need_mb=4096, waited_s=0.2))
        w.send(protocol.page(1, 1, 0.3, "card"))
        w.send(protocol.done(0, "FLAG", "x", "0000-0000", "<case>", [], 1,
                             protocol.gpu_block(CARD_NAME, 2.0, 2.5, 0, 1, 0.2)))
        return 0
    if scenario == "print_env":
        keys = ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "CUDA_PATH", "PYTORCH_FAKE_SETTING",
                "OPENBLAS_NUM_THREADS", "PYMUPDF_MESSAGE", "HDC_DATA_DIR")
        w.send(protocol.make("env", **{k: os.environ.get(k) for k in keys}))
        w.send(protocol.done(0, "PASS", "x", "0000-0000", "<case>", [], 1))
        return 0
    return 1


def main(argv: List[str]) -> int:
    if not argv:
        return 2
    mode, rest = argv[0], argv[1:]
    if mode == "raw":
        return raw(rest[0] if rest else "done")
    if mode == "entry":
        install_fakes(os.environ.get("FAKE_LOCK_DEFAULT") or None)
        from src.verify.runner.app import entry
        entry.WORKER_MODULE = THIS_MODULE
        return entry.main(rest)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
