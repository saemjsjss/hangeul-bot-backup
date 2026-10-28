"""DEV-only literals of the runner (APP_SPEC rev 1 section 4.1): this PC's folders, moved verbatim from botimport,
safety and engine. Imported only in DEV: by places.current(), and by safety.install_write_guard (the GPU worker's
read rule for the bot's venv, never used by the app). The app's build never stages it (the bundle scan proves it is
absent). Standard library only. No personal data.
"""
from pathlib import Path

WORKTREE = Path(r"C:\Hangeul\JARVIS\prototype\page-types")
PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
BOT = Path(r"C:\Hangeul\BOT")
HANGEUL = Path(r"C:\Hangeul")
VERIFIED = Path(r"C:\Hangeul\VERIFIED STUDENT DOCUMENTS")
NUMPY_VENV = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\.venv-pkg-numpy")
ONNX_LAB = Path(r"C:\Hangeul\DOCCHECK\lab\onnx")
BOT_VENV_NAME = ".venv"                  # safety.install_write_guard: the GPU worker reads <bot>\.venv only
WRITE_LABEL = "private-runner"          # message words, exactly as the DEV messages always printed them
NO_OPEN_LABEL = r"C:\Hangeul\BOT"
