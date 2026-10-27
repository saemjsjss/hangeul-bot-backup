"""Test runner for the owner's desktop document checker (prototype; RUNNER_SPEC.md, revision 2).

It takes one bundle PDF or one folder of bot-named files plus the program (KLP / EAP / BACHELOR /
MASTER, given on the command line), checks only those files against the hard rules, and writes a
chat-ready summary, a page-by-page Excel report and a JSON of everything under
C:\\Hangeul\\DOCCHECK\\lab\\private-runner.

    python -I -B -m src.verify.runner "<bundle.pdf | folder>" --program KLP --out <dir under private-runner>

The shared contract is model.py (data types), strict.py (the one strict name comparison and the dot
rule) and botimport.py (the bot's checking code from this worktree with a stub src.config). This
module imports nothing but model, so importing the package never loads the bot code or the OCR stack.
No personal data.
"""
from .model import RUNNER_VERSION

__all__ = ["RUNNER_VERSION"]
