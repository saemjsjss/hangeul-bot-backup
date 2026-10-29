"""A fake torch namespace (and a fake easyocr) for test_gpuengine: no torch, no CUDA, no graphics card.

FakeTorch has the surface gpuengine uses: ``cuda.*`` (availability, capability, arch list, properties, the memory
fraction, the allocator backend, synchronize, empty_cache, memory figures), ``OutOfMemoryError``,
``inference_mode()``, ``from_numpy``, ``backends`` flags that record every assignment, ``nn.Module``, ``float32``,
``get_float32_matmul_precision`` and the version strings. Tensors wrap numpy arrays and carry a device name.

FakeEasyOCR.Reader records its arguments and returns a reader whose ``detector`` (a CRAFT stand-in) and
``recognizer`` (an english_g2 stand-in with ``Prediction.out_features`` and ``AdaptiveAvgPool``) are fake modules
computing made-up outputs with numpy. Every value is made up; no personal data.
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

MIB = 1024 * 1024
GIB = 1024 ** 3
SM_LIST = ("sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120")     # torch 2.11.0+cu128's targets (no PTX)
CARD_NAME = "NVIDIA GeForce RTX 0000"                                  # a placeholder card name

float32 = "torch.float32"
float64 = "torch.float64"


class OutOfMemoryError(RuntimeError):
    """torch.OutOfMemoryError (= torch.cuda.OutOfMemoryError), a RuntimeError."""


class AcceleratorError(RuntimeError):
    """torch.AcceleratorError: a CUDA error other than out-of-memory."""


def oom(what: str = "made-up") -> OutOfMemoryError:
    return OutOfMemoryError(f"CUDA out of memory. Tried to allocate 1.00 GiB ({what})")


def _dtype_of(arr: np.ndarray) -> str:
    return {np.float32: float32, np.float64: float64}.get(arr.dtype.type, f"torch.{arr.dtype}")


# ---------------------------------------------------------------------------------------------- tensors
class Tensor:
    def __init__(self, arr: Any, device: str = "cpu") -> None:
        self.arr = np.asarray(arr)
        self.device = device

    @property
    def dtype(self) -> str:
        return _dtype_of(self.arr)

    @property
    def shape(self) -> Tuple[int, ...]:
        return tuple(self.arr.shape)

    def is_floating_point(self) -> bool:
        return self.arr.dtype.kind == "f"

    def to(self, device: str) -> "Tensor":
        return Tensor(np.array(self.arr, copy=True), str(device))

    def contiguous(self) -> "Tensor":
        return Tensor(np.ascontiguousarray(self.arr), self.device)

    def cpu(self) -> "Tensor":
        return Tensor(self.arr, "cpu")

    def numpy(self) -> np.ndarray:
        if self.device != "cpu":
            raise TypeError("can't convert a cuda tensor to numpy (made-up check)")
        return self.arr

    def mean(self, dim: int, keepdim: bool = False) -> "Tensor":
        return Tensor(self.arr.mean(axis=dim, keepdims=keepdim).astype(self.arr.dtype), self.device)


class Module:
    """torch.nn.Module's surface used here: __call__ -> forward, eval, to (recursive), parameters, buffers."""

    def __init__(self) -> None:
        self.training = True
        self._device = "cpu"

    def __call__(self, *args, **kw):
        return self.forward(*args, **kw)

    def forward(self, *args, **kw):                      # pragma: no cover - every fake module has its own
        raise NotImplementedError

    def _children(self) -> List["Module"]:
        return [v for k, v in vars(self).items() if isinstance(v, Module)]

    def eval(self) -> "Module":
        self.training = False
        for c in self._children():
            c.eval()
        return self

    def to(self, device: str) -> "Module":
        for k, v in list(vars(self).items()):
            if isinstance(v, Tensor):
                setattr(self, k, v.to(device))
            elif isinstance(v, Module):
                v.to(device)
        self._device = str(device)
        return self

    @property
    def device(self) -> str:
        return self._device

    def parameters(self) -> List[Tensor]:
        out = [v for v in vars(self).values() if isinstance(v, Tensor)]
        for c in self._children():
            out += c.parameters()
        return out

    def buffers(self) -> List[Tensor]:
        return []


class FakeNet(Module):
    """A network whose output is ``fn(input array, device)`` (a function: deepcopy shares it, so a test's script of
    out-of-memory errors is the same object in the processor module and its card copy)."""

    def __init__(self, fn: Callable[[np.ndarray, str], np.ndarray]) -> None:
        super().__init__()
        self.fn = fn
        self.weight = Tensor(np.zeros(4, dtype=np.float32))

    def forward(self, x: Tensor) -> Tensor:
        if x.device != self.device:
            raise RuntimeError("input and weights are on different devices (made-up check)")
        return Tensor(self.fn(x.arr, x.device), x.device)


# --------------------------------------------------------------------------------------------- flags
class Flags:
    """A flag namespace that logs every assignment into ``log`` as (name, value)."""

    def __init__(self, log: List[Tuple[str, Any]], prefix: str, **values: Any) -> None:
        object.__setattr__(self, "_log", log)
        object.__setattr__(self, "_prefix", prefix)
        for k, v in values.items():
            object.__setattr__(self, k, v)

    def __setattr__(self, name: str, value: Any) -> None:
        self._log.append((f"{self._prefix}.{name}", value))
        object.__setattr__(self, name, value)


# ---------------------------------------------------------------------------------------------- cuda
class FakeCuda:
    OutOfMemoryError = OutOfMemoryError

    def __init__(self, *, available: bool = True, cc: Tuple[int, int] = (12, 0),
                 arch_list: Sequence[str] = SM_LIST, total_mb: int = 8151, name: str = CARD_NAME,
                 backend: str = "native", reserved_mb: int = 180, peak_alloc_gb: float = 2.41,
                 peak_reserved_gb: float = 2.80) -> None:
        self.available = available
        self.cc = tuple(cc)
        self.arch_list = list(arch_list)
        self.total_mb = int(total_mb)
        self.name = name
        self.backend = backend
        self.reserved = int(reserved_mb) * MIB
        self.peak_alloc = int(peak_alloc_gb * GIB)
        self.peak_reserved = int(peak_reserved_gb * GIB)
        self.calls: List[Any] = []
        self.fraction: Optional[Tuple[float, Any]] = None
        self.empties = 0
        self.syncs = 0
        self.after_empty_mb: Optional[int] = None      # when set, empty_cache() leaves this much reserved

    def is_available(self) -> bool:
        self.calls.append("is_available")
        return self.available

    def get_device_properties(self, index: int):
        self.calls.append(("get_device_properties", index))
        return SimpleNamespace(name=self.name, total_memory=self.total_mb * MIB)

    def get_device_capability(self, index: int = 0) -> Tuple[int, int]:
        self.calls.append(("get_device_capability", index))
        return self.cc

    def get_arch_list(self) -> List[str]:
        return list(self.arch_list)

    def get_device_name(self, index: int = 0) -> str:
        return self.name

    def set_per_process_memory_fraction(self, fraction: float, device: Any = None) -> None:
        self.calls.append(("set_per_process_memory_fraction", fraction, device))
        self.fraction = (fraction, device)

    def get_allocator_backend(self) -> str:
        return self.backend

    def synchronize(self) -> None:
        self.syncs += 1
        self.calls.append("synchronize")

    def empty_cache(self) -> None:
        self.empties += 1
        self.calls.append("empty_cache")
        if self.after_empty_mb is not None:
            self.reserved = int(self.after_empty_mb) * MIB

    def memory_reserved(self) -> int:
        return self.reserved

    def max_memory_allocated(self) -> int:
        return self.peak_alloc

    def max_memory_reserved(self) -> int:
        return self.peak_reserved

    def is_initialized(self) -> bool:
        return bool(self.calls)


class FakeTorch:
    """One fake torch per test (its state is the test's own)."""

    OutOfMemoryError = OutOfMemoryError
    AcceleratorError = AcceleratorError
    float32 = float32
    float64 = float64
    Tensor = Tensor

    def __init__(self, *, flags: Optional[Dict[str, Any]] = None, precision: str = "highest",
                 version: str = "2.11.0+cu128", cuda_version: str = "12.8", **cuda_kw: Any) -> None:
        f = {"cudnn_allow_tf32": True, "matmul_allow_tf32": False, "cudnn_deterministic": False,
             "cudnn_benchmark": False}
        f.update(flags or {})
        self.__version__ = version
        self.version = SimpleNamespace(cuda=cuda_version)
        self.flag_sets: List[Tuple[str, Any]] = []
        self.backends = SimpleNamespace(
            cudnn=Flags(self.flag_sets, "cudnn", allow_tf32=f["cudnn_allow_tf32"],
                        deterministic=f["cudnn_deterministic"], benchmark=f["cudnn_benchmark"]),
            cuda=SimpleNamespace(matmul=Flags(self.flag_sets, "cuda.matmul", allow_tf32=f["matmul_allow_tf32"])))
        self.cuda = FakeCuda(**cuda_kw)
        self.nn = SimpleNamespace(Module=Module)
        self._precision = precision
        self.inference_entries = 0
        self.inference_depth = 0
        self.from_numpy_args: List[Dict[str, Any]] = []

    @contextlib.contextmanager
    def _inference(self):
        self.inference_entries += 1
        self.inference_depth += 1
        try:
            yield
        finally:
            self.inference_depth -= 1

    def inference_mode(self):
        return self._inference()

    def from_numpy(self, arr: np.ndarray) -> Tensor:
        self.from_numpy_args.append({"writeable": bool(arr.flags.writeable),
                                     "c_contiguous": bool(arr.flags.c_contiguous), "shape": tuple(arr.shape),
                                     "in_inference": self.inference_depth > 0})
        return Tensor(arr, "cpu")

    def get_float32_matmul_precision(self) -> str:
        return self._precision


# ------------------------------------------------------------------------------------------- easyocr
# english_g2's 96 characters, in the order of easyocr 1.7.2 (and of the lab's glue_data.json); U+20AC is the euro sign
CHARACTERS = ("0123456789!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ €"
              "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


class FakeCraft(Module):
    """CRAFT's stand-in: [B, 3, H, W] -> ([B, H/2, W/2, 2] score maps, feature). With ``blob`` (y0, y1, x0, x1 in
    the score map's pixels) the region score is 0.95 there, so the glue finds one text box."""

    def __init__(self, blob: Optional[Tuple[int, int, int, int]] = None) -> None:
        super().__init__()
        self.blob = blob
        self.weight = Tensor(np.ones(8, dtype=np.float32))
        self.bias = Tensor(np.zeros(2, dtype=np.float32))

    def forward(self, x: Tensor):
        b, _c, h, w = x.arr.shape
        y = np.zeros((b, max(1, h // 2), max(1, w // 2), 2), dtype=np.float32)
        if self.blob is not None:
            y0, y1, x0, x1 = self.blob
            y[:, y0:y1, x0:x1, 0] = 0.95
        return Tensor(y, x.device), Tensor(np.zeros((b, 1), dtype=np.float32), x.device)


class FakeRecognizer(Module):
    """english_g2's stand-in: forward(x, text) -> [B, T, 97] logits whose argmax spells ``word`` (each letter twice,
    a blank between letters, blanks after), with the visual features pooled by ``self.AdaptiveAvgPool``."""

    def __init__(self, word: str = "HI") -> None:
        super().__init__()
        self.word = word
        self.Prediction = SimpleNamespace(out_features=97)
        self.AdaptiveAvgPool = _OriginalPool()
        self.weight = Tensor(np.ones(16, dtype=np.float32))
        self.pool_calls = 0

    def forward(self, x: Tensor, text: Any) -> Tensor:
        if text is not None:
            raise RuntimeError("the recogniser's text input is unused for CTC (made-up check)")
        b, _c, _h, w = x.arr.shape
        t = max(8, w // 4)
        feats = Tensor(np.ones((b, t, 4, 3), dtype=np.float32), x.device)
        pooled = self.AdaptiveAvgPool(feats)               # [B, T, C, 1]
        self.pool_calls += 1
        if pooled.arr.shape != (b, t, 4, 1):
            raise RuntimeError("the pool did not give [B, T, C, 1]")
        logits = np.zeros((b, t, 97), dtype=np.float32)
        steps: List[int] = []
        for ch in self.word:
            k = CHARACTERS.index(ch) + 1
            steps += [k, k, 0]
        for s in range(t):
            logits[:, s, steps[s] if s < len(steps) else 0] = 20.0
        return Tensor(logits, x.device)


class _OriginalPool(Module):
    """AdaptiveAvgPool2d((None, 1)) stand-in (the swap replaces it with MeanPool)."""

    def forward(self, x: Tensor) -> Tensor:
        return x.mean(dim=3, keepdim=True)


class FakeEasyReader:
    def __init__(self, gpu: Any, *, word: str = "HI", blob=None, float64_param: bool = False) -> None:
        self.device = "cuda" if gpu is True else "cpu"
        self.detector = FakeCraft(blob)
        self.recognizer = FakeRecognizer(word)
        if float64_param:
            self.recognizer.extra = Tensor(np.zeros(2, dtype=np.float64))
        self.character = CHARACTERS
        self.lang_char = CHARACTERS


class FakeEasyOCR:
    """easyocr's module surface: Reader(lang_list, **kw) records the call; ``device`` overrides the reader's device
    (a reader that ends on the processor although gpu=True)."""

    def __init__(self, *, device: Optional[str] = None, **reader_kw: Any) -> None:
        self.calls: List[Tuple[List[str], Dict[str, Any]]] = []
        self.readers: List[FakeEasyReader] = []
        self.device = device
        self.reader_kw = reader_kw

    def Reader(self, lang_list, **kw):
        self.calls.append((list(lang_list), dict(kw)))
        r = FakeEasyReader(kw.get("gpu", True), **self.reader_kw)
        if self.device is not None:
            r.device = self.device
        self.readers.append(r)
        return r
