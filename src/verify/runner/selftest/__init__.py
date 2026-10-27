"""Self-tests of the document-check test runner (RUNNER_SPEC section 10).

Run as  python -I -B -m src.verify.runner.selftest.<name> [args]. Self-tests read real data read-only
and write only under C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\<name>\\. They print counts,
rates, ok/MISS and sids, never names, numbers or text.
"""
