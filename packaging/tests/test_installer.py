"""Unit tests of Task G (APP_SPEC r2 R2.5, R2.6 P3): hdc.iss, build_installer.py, README_INSTALL.txt,
install_cycle.py.

Run (runner venv, no card, no ISCC, no install, nothing outside private-runner\\selftest\\app\\G):

    set HDC_TEST_NO_CARD=1
    C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\.venv-runner\\Scripts\\python.exe -I -B -m unittest discover
        -s C:\\Hangeul\\JARVIS\\prototype\\page-types\\packaging\\tests -p test_installer.py

Every folder and file here is made up. The only real processes are tiny Python children (the job-object runner).
With HDC_TEST_ISCC=1, one optional test also compiles hdc.iss with ``/O-`` (syntax only, no Setup.exe written)
against a made-up 3-file app folder: a few seconds at BELOW_NORMAL.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("HDC_TEST_NO_CARD", "1")
TESTS = Path(__file__).resolve().parent
PKG = TESTS.parent
WORKTREE = PKG.parent
for _p in (str(PKG / "installer"), str(TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_installer as bi  # noqa: E402
import install_cycle as ic  # noqa: E402

G_ROOT = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\G")
BID = "20261029.3-abc1234"            # made-up build id
BID_DIRTY = "20261029.12-0123abcd-dirty"


def scratch(name: str) -> Path:
    G_ROOT.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=name + "-", dir=str(G_ROOT)))


def remove(path: Path) -> None:
    p = Path(path).resolve()
    assert G_ROOT.resolve() in p.parents, "refusing to delete outside the G test folder"
    shutil.rmtree(p, ignore_errors=True)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_app(root: Path, build_id: str = BID, *, manifest_shape: str = "list") -> Path:
    """A made-up 'frozen app' folder: exe, _internal\\BUILD.json, one data file, _internal\\MANIFEST.json."""
    app = root / bi.APP_NAME
    files = {
        bi.EXE_NAME: b"MZ made-up launcher bytes",
        "_internal/BUILD.json": json.dumps({"app_version": "1.0.0", "build_id": build_id}).encode("ascii"),
        "_internal/engine/made_up_weights.bin": bytes(range(256)) * 8,
    }
    for rel, data in files.items():
        p = app / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    if manifest_shape == "list":
        manifest: Any = {"build_id": build_id, "files": [{"path": rel, "size": len(d), "sha256": sha(d)}
                                                         for rel, d in files.items()]}
    elif manifest_shape == "map":
        manifest = {"files": {rel.replace("/", "\\"): {"size": len(d), "sha256": sha(d)} for rel, d in files.items()}}
    else:
        manifest = {rel: [len(d), sha(d)] for rel, d in files.items()}
    (app / "_internal" / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return app


def make_lnk(target: str, *, unicode_info: bool = True, relative: Optional[str] = None,
             with_link_info: bool = True) -> bytes:
    """A minimal made-up Windows shortcut ([MS-SHLLINK]) pointing at target."""
    flags = 0x80 | (0x02 if with_link_info else 0) | (0x08 if relative else 0)
    header = struct.pack("<I16sIIQQQIiIHHII", 0x4C, ic.LNK_CLSID, flags, 0x20, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0)
    assert len(header) == 0x4C
    body = b""
    if with_link_info:
        hsize = 0x24 if unicode_info else 0x1C
        volume = struct.pack("<IIII", 0x11, 3, 0, 0x10) + b"\0"
        ansi = target.encode("mbcs", "replace") + b"\0"
        vol_off = hsize
        base_off = vol_off + len(volume)
        suffix_off = base_off + len(ansi)
        tail = volume + ansi + b"\0"
        extra = b""
        if unicode_info:
            ubase = suffix_off + 1
            utarget = target.encode("utf-16-le") + b"\0\0"
            usuffix = ubase + len(utarget)
            tail += utarget + b"\0\0"
            extra = struct.pack("<II", ubase, usuffix)
        size = hsize + len(tail)
        body = struct.pack("<IIIIIII", size, hsize, 1, vol_off, base_off, 0, suffix_off) + extra + tail
    if relative:
        body += struct.pack("<H", len(relative)) + relative.encode("utf-16-le")
    return header + body + b"\0\0\0\0"


ISS_TEXT = bi.ISS.read_text(encoding="ascii")


def iss_section(name: str) -> List[str]:
    lines = ISS_TEXT.splitlines()
    start = lines.index("[%s]" % name) + 1
    out = []
    for ln in lines[start:]:
        if re.match(r"^\[[A-Za-z]+\]$", ln):
            break
        out.append(ln)
    return out


# ================================================================================================================
# hdc.iss (static)

class TestIssStatic(unittest.TestCase):
    EXPECTED = {
        "AppId": "{{767599DF-1DFD-490C-A03A-32EE783D7301}",
        "AppName": "Hangeul Document Checker",
        "AppVersion": "{#AppVersion}",
        "AppVerName": "Hangeul Document Checker {#AppVersion} (GPU, build {#BuildId})",
        "AppPublisher": "Hangeul",
        "PrivilegesRequired": "lowest",
        "DefaultDirName": "{autopf}\\Hangeul Document Checker",
        "DisableProgramGroupPage": "yes",
        "ArchitecturesAllowed": "x64os",
        "ArchitecturesInstallIn64BitMode": "x64os",
        "MinVersion": "10.0.17763",
        "Compression": "lzma2/ultra64",
        "SolidCompression": "yes",
        "LZMAUseSeparateProcess": "yes",
        "LZMANumBlockThreads": "2",
        "AppMutex": "HangeulDocumentChecker",
        "WizardStyle": "modern",
        "SetupLogging": "yes",
        "UninstallDisplayName": "Hangeul Document Checker (GPU)",
        "UninstallDisplayIcon": "{app}\\Hangeul Document Checker.exe",
        "OutputBaseFilename": "Hangeul Document Checker Setup {#AppVersion} GPU",
        "VersionInfoVersion": "{#FileVersion}",
        "VersionInfoProductVersion": "{#AppVersion}",
        "VersionInfoProductName": "Hangeul Document Checker",
        "VersionInfoCompany": "Hangeul",
        "VersionInfoDescription": "Hangeul Document Checker Setup (GPU)",
        # R2.5's "VersionInfoComments=build {#BuildId}" does not exist in Inno Setup 6.7.4 (ISCC stops on it).
        "VersionInfoProductTextVersion": "{#AppVersion} (GPU, build {#BuildId})",
    }

    def setup_lines(self) -> List[str]:
        return [ln for ln in iss_section("Setup") if ln.strip() and not ln.lstrip().startswith(";")]

    def test_plain_ascii_no_dev_paths(self):
        self.assertTrue(ISS_TEXT.isascii())
        low = ISS_TEXT.lower()
        for bad in ("c:\\hangeul", "c:/hangeul", "jarvis", "private-runner", ".venv", "\\users\\"):
            self.assertNotIn(bad, low)

    def test_setup_directives_exact(self):
        outside, span, nospan, mode = {}, {}, {}, None
        for ln in self.setup_lines():
            s = ln.strip()
            if s.startswith("#"):
                mode = {"#ifdef Span": "span", "#else": "nospan", "#endif": None}[s]
                continue
            self.assertNotIn(";", s, "inline comments are part of an Inno value: " + s)
            k, _, v = s.partition("=")
            target = {"span": span, "nospan": nospan, None: outside}[mode]
            self.assertNotIn(k, target, "directive twice: " + k)
            target[k] = v
        self.assertEqual(outside, self.EXPECTED)
        self.assertEqual(span, {"DiskSpanning": "yes", "DiskSliceSize": "2000000000"})  # pii-scan: not a number
        self.assertEqual(nospan, {"DiskSpanning": "no"})

    def test_directives_known_to_inno_6_7_4(self):
        dll = bi.ISCC.parent / "ISCmplr.dll"
        if not dll.is_file():
            self.skipTest("Inno Setup is not installed here")
        known = set(re.findall(r"ss([A-Za-z0-9]+)", dll.read_bytes().decode("latin-1")))
        for ln in self.setup_lines():
            if not ln.startswith("#"):
                name = ln.partition("=")[0].strip()
                self.assertIn(name, known, "ISCC 6.7.4 does not know [Setup] %s" % name)

    def test_required_defines_guarded(self):
        for name in ("AppFolder", "AppVersion", "FileVersion", "BuildId"):
            self.assertRegex(ISS_TEXT, r"(?m)^#ifndef %s\n#error %s is missing" % (name, name))

    def test_sections_and_order(self):
        heads = re.findall(r"(?m)^\[([A-Za-z]+)\]$", ISS_TEXT)
        self.assertEqual(heads, ["Setup", "InstallDelete", "Files", "Tasks", "Icons", "Run", "Code"])

        def entries(name):
            return [ln for ln in iss_section(name) if ln.strip() and not ln.startswith(";")]

        self.assertEqual(entries("InstallDelete"), ['Type: filesandordirs; Name: "{app}\\_internal"'])
        self.assertEqual(entries("Files"), ['Source: "{#AppFolder}\\*"; DestDir: "{app}"; '
                                            'Flags: ignoreversion recursesubdirs createallsubdirs'])
        self.assertEqual(entries("Tasks"), ['Name: desktopicon; Description: "Create a desktop shortcut"'])
        self.assertEqual(entries("Icons"), [
            'Name: "{autoprograms}\\Hangeul Document Checker"; Filename: "{app}\\Hangeul Document Checker.exe"',
            'Name: "{autodesktop}\\Hangeul Document Checker"; Filename: "{app}\\Hangeul Document Checker.exe"; '
            'Tasks: desktopicon'])
        self.assertEqual(entries("Run"), ['Filename: "{app}\\Hangeul Document Checker.exe"; Description: '
                                          '"Open the checker now"; Flags: postinstall nowait skipifsilent'])

    def code(self) -> str:
        return ISS_TEXT[ISS_TEXT.index("[Code]"):]

    def test_code_driver_warning(self):
        code = self.code()
        self.assertIn("NoDriverText = 'This PC has no NVIDIA graphics driver. The checker needs an NVIDIA graphics "
                      "card (GeForce RTX 20 series or newer) to check documents. Install anyway?';", code)
        self.assertIn("function InitializeSetup(): Boolean;", code)
        self.assertIn("ExpandConstant('{sys}\\nvml.dll')", code)
        self.assertIn("ExpandConstant('{win}\\Sysnative\\nvml.dll')", code)   # Setup is a 32-bit program
        init = code[code.index("function InitializeSetup"):code.index("procedure CurUninstallStepChanged")]
        self.assertIn("Result := True;", init)
        self.assertIn("if WizardSilent() then", init)
        self.assertIn("Log('Silent install: going on without asking.')", init)
        self.assertIn("SuppressibleMsgBox(NoDriverText, mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES",
                      init)

    def test_code_uninstall_data_question(self):
        code = self.code()
        self.assertIn("DeleteDataText = 'Also delete the app''s saved data (saved page texts, recent checks, "
                      "settings)? Your reports in Documents are kept.';", code)
        un = code[code.index("procedure CurUninstallStepChanged"):]
        self.assertIn("if CurUninstallStep <> usPostUninstall then", un)
        self.assertIn("ExpandConstant('{localappdata}\\Hangeul Document Checker')", un)
        self.assertRegex(un, r"if UninstallSilent\(\) then\s+DeleteIt := True")
        self.assertIn("SuppressibleMsgBox(DeleteDataText, mbConfirmation, MB_YESNO, IDYES) = IDYES", un)
        self.assertNotIn("MB_DEFBUTTON2", un)                                       # default button: Yes
        self.assertIn("DelTree(DataDir, True, True, True)", un)

    @staticmethod
    def pascal_code(src: str) -> str:
        """The [Code] text without string literals and comments, scanned the way Pascal Script reads it."""
        out, i, n = [], 0, len(src)
        while i < n:
            if src[i] == "'":
                j = i + 1
                while j < n and not (src[j] == "'" and src[j + 1:j + 2] != "'"):
                    j += 2 if src[j] == "'" else 1
                out.append("''")
                i = j + 1
            elif src[i] == "{":
                i = src.index("}", i) + 1              # Pascal ends a { } comment at the FIRST }
            elif src.startswith("(*", i):
                i = src.index("*)", i) + 2
            elif src.startswith("//", i):
                j = src.find("\n", i)
                i = n if j < 0 else j
            else:
                out.append(src[i])
                i += 1
        return "".join(out)

    def test_code_blocks_balanced(self):
        code = self.pascal_code(self.code()[len("[Code]"):])
        self.assertNotIn("}", code, "a } outside comments: a { } comment held another { }")
        self.assertEqual(len(re.findall(r"\bbegin\b", code)), len(re.findall(r"\bend\b", code)))
        self.assertEqual(re.findall(r"\b(function|procedure) (\w+)", code),
                         [("function", "NvidiaDriverPresent"), ("function", "InitializeSetup"),
                          ("procedure", "CurUninstallStepChanged")])

    def test_app_id_shared_with_scripts(self):
        self.assertEqual(bi.read_app_id(ISS_TEXT), bi.APP_GUID)
        self.assertEqual(bi.APP_ID, "{767599DF-1DFD-490C-A03A-32EE783D7301}")
        self.assertTrue(bi.UNINSTALL_KEY.endswith("\\Uninstall\\{767599DF-1DFD-490C-A03A-32EE783D7301}_is1"))
        self.assertIn(bi.APP_GUID, ISS_TEXT.split("[Setup]")[0])     # documented in the header
        with self.assertRaises(bi.BuildError):
            bi.read_app_id(ISS_TEXT.replace("AppId={{", "AppIdX={{"))


# ================================================================================================================
# README_INSTALL.txt

class TestReadme(unittest.TestCase):
    def test_template_is_the_spec_text(self):
        spec = WORKTREE / "APP_SPEC.md"
        if not spec.is_file():
            self.skipTest("APP_SPEC.md not found")
        lines = spec.read_text(encoding="utf-8").splitlines()
        i = next(n for n, ln in enumerate(lines) if "filled from `packaging\\installer\\README_INSTALL.txt`" in ln)
        start = next(n for n in range(i, len(lines)) if lines[n].strip() == "```") + 1
        end = next(n for n in range(start, len(lines)) if lines[n].strip() == "```")
        template = bi.README_TEMPLATE.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")
        self.assertEqual(template, "\n".join(lines[start:end]))

    def values(self, **kw) -> Dict[str, str]:
        v = {"APP_VERSION": "1.0.1", "BUILD_ID": BID, "SETUP_GB": "1.8", "SETUP_BYTES": "1,939,911,235",
             "INSTALLED_GB": "4.4", "DISK_GB": "7"}
        v.update(kw)
        return v

    def test_fill(self):
        text = bi.fill_readme(bi.README_TEMPLATE.read_text(encoding="utf-8"), self.values())
        self.assertNotIn("{", text)
        self.assertNotIn("}", text)
        self.assertNotIn("\n", text.replace("\r\n", ""))                     # CRLF everywhere
        self.assertTrue(text.endswith("\r\n"))
        self.assertTrue(text.isascii())
        self.assertTrue(text.startswith("HANGEUL DOCUMENT CHECKER 1.0.1 (GPU) - build %s\r\n" % BID))
        flat = " ".join(text.split())
        self.assertIn("Hangeul Document Checker Setup 1.0.1 GPU.exe: the installer, 1.8 GB (1,939,911,235 bytes).",
                      flat)
        self.assertIn("About 7 GB of free disk space: 1.8 GB for the Setup file, 4.4 GB for the installed "
                      "checker, and some room for reports.", flat)
        self.assertIn('Get-FileHash -Algorithm SHA256 "Hangeul Document Checker Setup 1.0.1 GPU.exe"', flat)

    def test_the_release_readme_says_what_the_owner_needs(self):
        """1.0.1 (APP_SPEC R2.10 I30): plain English for the owner. The self-check is the app's own Self-test (the
        window's "Self-test" button), not CHECK_0001.pdf, which is not shipped; the PC's needs, SmartScreen, the
        first start and the uninstall are there; the window's own words are quoted as the window says them."""
        flat = " ".join(bi.fill_readme(bi.README_TEMPLATE.read_text(encoding="utf-8"), self.values()).split())
        self.assertNotIn("CHECK_0001", flat)
        self.assertNotIn("Result code", flat)
        for want in ("An NVIDIA graphics card with 6 GB of graphics memory or more",
                     "A recent NVIDIA driver: version 570 or newer",
                     '"Windows protected your PC"', 'Click "More info", then "Run anyway".',
                     "not digitally signed", "THE FIRST START", 'Click the "Self-test" button',
                     '"every part is OK"', '"finished with problems"', 'send the newest file whose name starts with "SELFTEST"', "That file holds no student data",
                     "Smart App Control", "The checker needs no internet connection",
                     "HOW TO UNINSTALL", '"Hangeul Document Checker (GPU)"', "Waiting for the graphics card",
                     "SHA256SUMS.txt"):
            self.assertIn(want, flat)
        from src.verify.runner.app import messages as M                   # the window's own words
        self.assertIn("every part is OK", M.render(M.SELFTEST_OK).text)
        self.assertIn("finished with problems", M.render(M.SELFTEST_FAILED).text)
        self.assertIn("Waiting for the graphics card", M.WAIT_TITLE)
        iss = bi.ISS.read_text(encoding="ascii")
        self.assertIn("UninstallDisplayName=Hangeul Document Checker (GPU)", iss)
        self.assertIn('Description: "Create a desktop shortcut"', iss)
        self.assertIn('Description: "Open the checker now"', iss)
        self.assertIn("Install anyway?", iss)

    def test_sizes_come_from_the_build(self):
        setup, app = 1_939_911_235, 4_711_594_649                         # build 20261029.3 (1.0.0)
        self.assertEqual((bi.setup_gb(setup), bi.setup_gb(app), bi.disk_gb(setup, app)), ("1.8", "4.4", "7"))
        self.assertEqual(bi.disk_gb(0, 0), "1")
        self.assertEqual(bi.disk_gb(1 << 30, 3 << 30), "5")               # 4.0 + 0.5 -> 5
        with self.assertRaises(ValueError):
            bi.disk_gb(-1, 0)
        self.assertEqual(bi.bytes_text(setup), "1,939,911,235")
        self.assertEqual(bi.bytes_text(7), "7")

    def test_fill_refuses(self):
        tpl = bi.README_TEMPLATE.read_text(encoding="utf-8")
        for bad in (self.values(SETUP_GB=""), self.values(DISK_GB="7\nX"), self.values(BUILD_ID="{x}"),
                    self.values(INSTALLED_GB="\u00e9")):
            with self.assertRaises(bi.BuildError):
                bi.fill_readme(tpl, bad)
        with self.assertRaises(bi.BuildError):
            bi.fill_readme(tpl + "{EXTRA}\n", self.values())
        with self.assertRaises(bi.BuildError):
            bi.fill_readme(tpl.replace("{DISK_GB}", "X"), self.values())


# ================================================================================================================
# build_installer helpers

class TestHelpers(unittest.TestCase):
    def test_build_id_and_file_version(self):
        self.assertEqual(bi.parse_build_id(BID), {"date": "20261029", "n": 3, "git": "abc1234", "dirty": False})
        self.assertTrue(bi.parse_build_id(BID_DIRTY)["dirty"])
        self.assertEqual(bi.file_version("1.0.0", BID), "1.0.0.3")
        self.assertEqual(bi.file_version("1.0.0", BID_DIRTY), "1.0.0.12")
        for bad in ("", "20261029-abc1234", "2026102.1-abc1234", "20261329.1-abc1234", "20261029.1-ABC1234",
                    "20261029.70000-abc1234", "20261029.1-abc1234-clean"):
            with self.assertRaises(ValueError, msg=bad):
                bi.parse_build_id(bad)
        with self.assertRaises(ValueError):
            bi.file_version("1.0", BID)

    def test_date_and_code(self):
        self.assertEqual(bi.check_date_ok("2026-10-29"), "2026-10-29")
        self.assertEqual(bi.pc_code_ok("51C2-0A7E"), "51C2-0A7E")
        for bad in ("2026-13-01", "29-10-2026", "2026-1-29"):
            with self.assertRaises(ValueError):
                bi.check_date_ok(bad)
        for bad in ("51c2-0a7e", "51C20A7E", "51C2-0A7", "G1C2-0A7E"):
            with self.assertRaises(ValueError):
                bi.pc_code_ok(bad)

    def test_setup_gb_and_names(self):
        self.assertEqual(bi.setup_gb(2_900_000_000), "2.7")
        self.assertEqual(bi.setup_gb(1 << 30), "1.0")
        self.assertEqual(bi.setup_base("1.0.0"), "Hangeul Document Checker Setup 1.0.0 GPU")

    def test_needs_span(self):
        msg = ("Error on line 0: Disk spanning must be enabled to create an installation larger than 4200000000 "  # pii-scan: not a number
               "bytes in size for a single Setup.exe, as this approaches the maximum supported by Windows")
        self.assertTrue(bi.needs_span(2, msg))
        self.assertTrue(bi.needs_span(2, msg.replace(" ", "\n", 3).upper()))
        self.assertFalse(bi.needs_span(0, msg))
        self.assertFalse(bi.needs_span(None, msg))
        self.assertFalse(bi.needs_span(2, "Error on line 75: Unknown identifier"))
        self.assertFalse(bi.needs_span(2, ""))

    def test_iscc_command(self):
        cmd = bi.iscc_command(Path(r"X:\tools\ISCC.exe"), Path(r"X:\pkg\hdc.iss"), Path(r"X:\b\dist\Hangeul Document Checker"),
                              "1.0.0", "1.0.0.3", BID, Path(r"X:\b\installer"))
        self.assertEqual(cmd, '"X:\\tools\\ISCC.exe" /Qp /DAppFolder="X:\\b\\dist\\Hangeul Document Checker" '
                              '/DAppVersion=1.0.0 /DFileVersion=1.0.0.3 /DBuildId=%s /O"X:\\b\\installer" '
                              '"X:\\pkg\\hdc.iss"' % BID)
        span = bi.iscc_command(Path("I.exe"), Path("h.iss"), Path("A"), "1.0.0", "1.0.0.3", BID, Path("O"), span=True)
        self.assertIn(" /DSpan=1 ", span)
        self.assertNotIn("/DSpan", cmd)
        syntax = bi.iscc_command(Path("I.exe"), Path("h.iss"), Path("A"), "1.0.0", "1.0.0.3", BID, None)
        self.assertIn(" /O- ", syntax)
        with self.assertRaises(ValueError):
            bi.iscc_command(Path("I.exe"), Path("h.iss"), Path('A"B'), "1.0.0", "1.0.0.3", BID, None)
        with self.assertRaises(ValueError):
            bi.iscc_command(Path("I.exe"), Path("h.iss"), Path("A"), "1.0.0", "1.0.0.3 /DSpan=1", BID, None)

    def test_output_files_and_sha256(self):
        d = scratch("outputs")
        try:
            base = bi.setup_base("1.0.0")
            for name, data in ((base + "-10.bin", b"c"), (base + "-2.bin", b"b"), (base + ".exe", b"a"),
                               (base + "-1.bin", b"d"), ("other.bin", b"x")):
                (d / name).write_bytes(data)
            files = bi.output_files(d, base)
            self.assertEqual([f.name for f in files], [base + ".exe", base + "-1.bin", base + "-2.bin",
                                                       base + "-10.bin"])
            sums = bi.write_sha256(d, files)
            lines = (d / "setup.sha256").read_text(encoding="ascii").splitlines()
            self.assertEqual(lines[0], "%s *%s.exe" % (sha(b"a"), base))
            self.assertEqual(len(lines), 4)
            self.assertEqual(sums[base + "-2.bin"], sha(b"b"))
        finally:
            remove(d)


# ================================================================================================================
# MANIFEST helpers

class TestManifest(unittest.TestCase):
    def setUp(self):
        self.root = scratch("manifest")

    def tearDown(self):
        remove(self.root)

    def test_shapes(self):
        for shape in ("list", "map", "bare"):
            app = make_app(self.root / shape, manifest_shape=shape)
            m = bi.load_manifest(bi.find_manifest(app))
            self.assertEqual(sorted(e.rel for e in m.values()),
                             [bi.EXE_NAME, "_internal/BUILD.json", "_internal/engine/made_up_weights.bin"], shape)
            rep = bi.compare_tree(app, m, allow_extra=lambda r: r == "_internal/MANIFEST.json")
            self.assertTrue(rep.ok, rep.summary())
            self.assertEqual(rep.checked, 3)
            self.assertEqual(rep.allowed_extra, ["_internal/MANIFEST.json"])

    def test_malformed(self):
        good = {"path": "a.txt", "size": 1, "sha256": "0" * 64}
        bad_entries = [dict(good, path="C:/x/a.txt"), dict(good, path="../a.txt"), dict(good, path="a/./b"),
                       dict(good, size=-1), dict(good, size=True), dict(good, sha256="xyz"), {"size": 1}]
        for e in bad_entries:
            with self.assertRaises(bi.ManifestError, msg=str(e)):
                bi.parse_manifest({"files": [e]})
        with self.assertRaises(bi.ManifestError):
            bi.parse_manifest({"files": [good, dict(good, path="A.TXT")]})            # twice, case-insensitive
        with self.assertRaises(bi.ManifestError):
            bi.parse_manifest({"files": []})
        with self.assertRaises(bi.ManifestError):
            bi.parse_manifest("not a manifest")
        (self.root / "broken.json").write_text("{", encoding="utf-8")
        with self.assertRaises(bi.ManifestError):
            bi.load_manifest(self.root / "broken.json")

    def test_compare_finds_every_difference(self):
        app = make_app(self.root)
        m = bi.load_manifest(bi.find_manifest(app))
        (app / "_internal/engine/made_up_weights.bin").write_bytes(b"\xff" + bytes(range(1, 256)) + bytes(range(256)) * 7)
        (app / "_internal/BUILD.json").write_bytes(b"{}")
        (app / bi.EXE_NAME).unlink()
        (app / "_internal/extra.dll").write_bytes(b"x")
        (app / "unins000.exe").write_bytes(b"x")
        rep = bi.compare_tree(app, m, allow_extra=lambda r: bi.inno_own_file(r) or r.endswith("MANIFEST.json"))
        self.assertFalse(rep.ok)
        self.assertEqual(rep.missing, [bi.EXE_NAME])
        self.assertEqual(sorted(rep.changed), ["_internal/BUILD.json (size)",
                                               "_internal/engine/made_up_weights.bin (sha256)"])
        self.assertEqual(rep.extra, ["_internal/extra.dll"])
        self.assertIn("unins000.exe", rep.allowed_extra)
        sizes_only = bi.compare_tree(app, m, hash_files=False, allow_extra=lambda r: True)
        self.assertEqual(sizes_only.changed, ["_internal/BUILD.json (size)"])
        self.assertIn("1 missing", rep.summary())

    def test_inno_own_files(self):
        for ok in ("unins000.exe", "UNINS000.DAT", "unins001.msg"):
            self.assertTrue(bi.inno_own_file(ok))
        for no in ("_internal/unins000.exe", "unins000.dll", "uninstall.exe", "unins0000.exe"):
            self.assertFalse(bi.inno_own_file(no))


# ================================================================================================================
# build_installer.main with a fake ISCC

LIMIT_MSG = ("Error: Disk spanning must be enabled to create an installation larger than 4200000000 bytes in size "  # pii-scan: not a number
             "for a single Setup.exe, as this approaches the maximum supported by Windows.\n")


class FakeIscc:
    """Stands in for run_tree(ISCC ...): writes what ISCC would write into /O and the log."""

    def __init__(self, script: List[str]):
        self.script = list(script)       # per call: "ok", "limit", "error", "timeout", "span-ok"
        self.calls: List[str] = []

    def __call__(self, cmd: str, *, timeout_s: float, log_path: Path) -> bi.RunResult:
        self.calls.append(cmd)
        what = self.script.pop(0)
        out = Path(re.search(r'/O"([^"]+)"', cmd).group(1))
        base = re.search(r"/DAppVersion=(\S+)", cmd).group(1)
        name = bi.setup_base(base)
        out.mkdir(parents=True, exist_ok=True)
        if what == "ok":
            (out / (name + ".exe")).write_bytes(b"made-up setup " * 100)
            log_path.write_text("Successful compile\n", encoding="utf-8")
            return bi.RunResult(0, 12.0, in_job=True, peak_job_mb=3000)
        if what == "span-ok":
            assert "/DSpan=1" in cmd
            (out / (name + ".exe")).write_bytes(b"stub")
            (out / (name + "-1.bin")).write_bytes(b"slice one " * 10)
            (out / (name + "-2.bin")).write_bytes(b"slice two")
            log_path.write_text("Successful compile\n", encoding="utf-8")
            return bi.RunResult(0, 14.0, in_job=True)
        if what == "limit":
            (out / (name + ".exe")).write_bytes(b"partial")
            log_path.write_text(LIMIT_MSG, encoding="utf-8")
            return bi.RunResult(2, 11.0, in_job=True)
        if what == "timeout":
            log_path.write_text("", encoding="utf-8")
            return bi.RunResult(1, 99.0, timed_out=True, in_job=True)
        log_path.write_text("Error on line 75 in hdc.iss: Unknown identifier\n", encoding="utf-8")
        return bi.RunResult(2, 1.0, in_job=True)


class TestBuildMain(unittest.TestCase):
    def setUp(self):
        self.root = scratch("build")
        self.builds = self.root / "builds"
        self.app = make_app(self.builds / BID / "dist")
        self.iscc = self.root / "tools" / "ISCC.exe"
        self.iscc.parent.mkdir(parents=True)
        self.iscc.write_bytes(b"made-up compiler")
        self.out = self.builds / BID / "installer"
        self.printed: List[str] = []

    def tearDown(self):
        remove(self.root)

    def argv(self, *extra: str) -> List[str]:
        return ["--build-id", BID, "--check-date", "2026-10-29", "--this-pc-code", "0A1B-2C3D",
                "--builds-root", str(self.builds), "--iscc", str(self.iscc)] + list(extra)

    def run_main(self, fake, *extra):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bi.main(self.argv(*extra), runner=fake)
        self.printed = buf.getvalue().splitlines()
        return rc

    def test_builds_one_setup(self):
        fake = FakeIscc(["ok"])
        self.assertEqual(self.run_main(fake), 0, self.printed)
        self.assertEqual(len(fake.calls), 1)
        cmd = fake.calls[0]
        self.assertIn('/DAppFolder="%s"' % self.app, cmd)
        self.assertIn("/DFileVersion=1.0.0.3", cmd)
        self.assertIn("/DBuildId=%s" % BID, cmd)
        self.assertNotIn("/DSpan", cmd)
        self.assertTrue(cmd.endswith('"%s"' % bi.ISS))
        info = json.loads((self.out / "installer.json").read_text(encoding="utf-8"))
        exe = self.out / "Hangeul Document Checker Setup 1.0.0 GPU.exe"
        self.assertEqual(info["setup"], exe.name)
        self.assertEqual(info["bytes"], exe.stat().st_size)
        self.assertEqual(info["files"], [{"name": exe.name, "bytes": exe.stat().st_size,
                                          "sha256": bi.sha256_file(exe)}])
        self.assertFalse(info["spanning"])
        self.assertEqual((info["build_id"], info["app_version"], info["file_version"], info["app_id"]),
                         (BID, "1.0.0", "1.0.0.3", bi.APP_ID))
        self.assertEqual(info["app_files"], 3)
        self.assertEqual(len(info["passes"]), 1)
        self.assertEqual((self.out / "setup.sha256").read_text(encoding="ascii"),
                         "%s *%s\n" % (bi.sha256_file(exe), exe.name))
        readme_path = self.out / "README_INSTALL.txt"
        readme = readme_path.read_bytes().decode("ascii")
        size = exe.stat().st_size
        self.assertIn("the installer, %s GB (%s bytes)." % (bi.setup_gb(size), bi.bytes_text(size)), readme)
        self.assertIn("About %s GB of free disk space: %s GB for the Setup file, %s GB for the installed"
                      % (bi.disk_gb(size, info["app_bytes"]), bi.setup_gb(size), bi.setup_gb(info["app_bytes"])),
                      readme)
        self.assertTrue(readme.startswith("HANGEUL DOCUMENT CHECKER 1.0.0 (GPU) - build %s\r\n" % BID))
        self.assertNotIn("0A1B-2C3D", readme)                             # P1's code is in installer.json only
        self.assertNotIn("CHECK_0001", readme)                            # not shipped (1.0.1, I30)
        self.assertEqual((info["this_pc_code"], info["check_date"]), ("0A1B-2C3D", "2026-10-29"))
        self.assertEqual((info["installed_gb"], info["disk_gb"]),
                         (bi.setup_gb(info["app_bytes"]), bi.disk_gb(size, info["app_bytes"])))
        self.assertEqual((self.out / "SHA256SUMS.txt").read_text(encoding="ascii"),
                         "%s *%s\n%s *README_INSTALL.txt\n" % (bi.sha256_file(exe), exe.name,
                                                               bi.sha256_file(readme_path)))
        self.assertTrue((self.builds / BID / "reports" / "iscc.log").is_file())

    def test_second_pass_only_on_the_limit(self):
        fake = FakeIscc(["limit", "span-ok"])
        self.assertEqual(self.run_main(fake), 0, self.printed)
        self.assertEqual(len(fake.calls), 2)
        self.assertNotIn("/DSpan=1", fake.calls[0])
        self.assertIn("/DSpan=1", fake.calls[1])
        info = json.loads((self.out / "installer.json").read_text(encoding="utf-8"))
        self.assertTrue(info["spanning"])
        self.assertEqual([f["name"] for f in info["files"]], ["Hangeul Document Checker Setup 1.0.0 GPU.exe",
                                                              "Hangeul Document Checker Setup 1.0.0 GPU-1.bin",
                                                              "Hangeul Document Checker Setup 1.0.0 GPU-2.bin"])
        self.assertEqual(info["bytes"], sum(f["bytes"] for f in info["files"]))
        self.assertEqual([p["limit_hit"] for p in info["passes"]], [True, False])
        self.assertEqual(len((self.out / "setup.sha256").read_text(encoding="ascii").splitlines()), 3)
        self.assertEqual(len((self.out / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()), 4)
        self.assertTrue((self.builds / BID / "reports" / "iscc-span.log").is_file())

    def test_other_error_has_no_second_pass(self):
        fake = FakeIscc(["error"])
        self.assertEqual(self.run_main(fake), 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse((self.out / "installer.json").exists())
        self.assertTrue(any("Unknown identifier" in ln for ln in self.printed))

    def test_timeout_fails(self):
        self.assertEqual(self.run_main(FakeIscc(["timeout"])), 1)

    def test_refuses_a_changed_app_folder(self):
        (self.app / "_internal" / "engine" / "made_up_weights.bin").write_bytes(b"changed")
        fake = FakeIscc([])
        self.assertEqual(self.run_main(fake), 1)
        self.assertEqual(fake.calls, [])
        self.assertTrue(any("differs from MANIFEST.json" in ln for ln in self.printed))
        (self.app / "_internal" / "engine" / "made_up_weights.bin").unlink()
        (self.app / "_internal" / "engine" / "x.bin").write_bytes(b"x")
        self.assertEqual(self.run_main(fake), 1)

    def test_refuses_other_build_or_missing_parts(self):
        (self.app / "_internal" / "BUILD.json").write_text(json.dumps({"build_id": BID_DIRTY}), encoding="ascii")
        self.assertEqual(self.run_main(FakeIscc([])), 1)
        self.assertEqual(self.run_main(FakeIscc([]), "--iscc", str(self.root / "missing.exe")), 1)
        (self.app / bi.EXE_NAME).unlink()
        self.assertEqual(self.run_main(FakeIscc([])), 1)

    def test_existing_setup_needs_replace(self):
        self.assertEqual(self.run_main(FakeIscc(["ok"])), 0)
        fake = FakeIscc(["ok"])
        self.assertEqual(self.run_main(fake), 1)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.run_main(fake, "--replace"), 0)
        self.assertEqual(len(fake.calls), 1)

    def test_bad_arguments(self):
        import contextlib
        import io
        for index, bad in ((1, "not-a-build-id"), (3, "2026/10/29"), (5, "51c2-0a7e")):
            argv = self.argv()
            argv[index] = bad
            fake = FakeIscc([])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bi.main(argv, runner=fake), 2, bad)
            self.assertEqual(fake.calls, [])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bi.main(["--build-id", BID], runner=FakeIscc([])), 2)

    def test_dry_run(self):
        fake = FakeIscc([])
        self.assertEqual(self.run_main(fake, "--dry-run"), 0)
        self.assertEqual(fake.calls, [])
        self.assertTrue(any("/DFileVersion=1.0.0.3" in ln for ln in self.printed))
        self.assertFalse(self.out.exists())


# ================================================================================================================
# run_tree: the job object (tiny real Python children; nothing else)

def _pid_alive(pid: int, wait_ms: int = 0) -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(ctypes.c_void_p(h), wait_ms) == 0x102    # WAIT_TIMEOUT: still running
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


@unittest.skipUnless(os.name == "nt", "Windows job objects")
class TestRunTree(unittest.TestCase):
    def setUp(self):
        self.d = scratch("tree")

    def tearDown(self):
        remove(self.d)

    def script(self, name: str, text: str) -> Path:
        p = self.d / name
        p.write_text(text, encoding="ascii")
        return p

    def test_waits_for_a_grandchild_and_keeps_the_log(self):
        grand = self.script("grand.py", "import pathlib, sys, time\ntime.sleep(1.5)\n"
                                        "pathlib.Path(sys.argv[1]).write_text('done')\n")
        parent = self.script("parent.py", "import subprocess, sys\nprint('made-up line', flush=True)\n"
                                          "subprocess.Popen([sys.executable, '-I', '-B', sys.argv[1], sys.argv[2]])\n"
                                          "sys.exit(3)\n")
        marker = self.d / "marker.txt"
        log = self.d / "run.log"
        r = bi.run_tree([sys.executable, "-I", "-B", str(parent), str(grand), str(marker)], timeout_s=60,
                        log_path=log, poll_s=0.1)
        if not r.in_job:
            self.skipTest("this process may not create nested job objects")
        self.assertEqual(r.returncode, 3)
        self.assertFalse(r.timed_out)
        self.assertTrue(marker.is_file(), "run_tree returned before the grandchild ended")
        self.assertIn("made-up line", log.read_text(encoding="utf-8", errors="replace"))

    def test_timeout_ends_the_whole_tree(self):
        pidfile = self.d / "pid.txt"
        sleeper = self.script("sleeper.py", "import os, pathlib, sys, time\n"
                                            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
                                            "time.sleep(60)\n")
        t0 = time.monotonic()
        r = bi.run_tree([sys.executable, "-I", "-B", str(sleeper), str(pidfile)], timeout_s=5, poll_s=0.1)
        self.assertTrue(r.timed_out)
        self.assertLess(time.monotonic() - t0, 40)
        self.assertTrue(pidfile.is_file())
        self.assertFalse(_pid_alive(int(pidfile.read_text())), "a process of the tree outlived run_tree")


# ================================================================================================================
# Optional: ISCC syntax check of hdc.iss (HDC_TEST_ISCC=1; /O- writes no Setup.exe)

@unittest.skipUnless(os.environ.get("HDC_TEST_ISCC") == "1" and bi.ISCC.is_file(), "set HDC_TEST_ISCC=1 to run ISCC")
class TestIsccSyntax(unittest.TestCase):
    def setUp(self):
        self.d = scratch("iscc")
        self.app = make_app(self.d)
        # ISCC makes its default "Output" folder next to the script even with /O-: compile a copy in G.
        self.iss = self.d / "hdc.iss"
        self.iss.write_text(ISS_TEXT, encoding="ascii")

    def tearDown(self):
        remove(self.d)

    def compile(self, cmd: str, name: str):
        log = self.d / name
        r = bi.run_tree(cmd, timeout_s=120, log_path=log, poll_s=0.2)
        return r, log.read_text(encoding="utf-8", errors="replace")

    def test_compiles_both_ways(self):
        for span in (False, True):
            cmd = bi.iscc_command(bi.ISCC, self.iss, self.app, "1.0.0", "1.0.0.3", BID, None, span=span)
            r, text = self.compile(cmd, "span.log" if span else "plain.log")
            self.assertEqual(r.returncode, 0, text[-2000:])
            self.assertFalse(r.timed_out)
        self.assertEqual(list(self.d.rglob("*.exe")), [self.app / bi.EXE_NAME])    # /O- wrote no Setup
        self.assertFalse((bi.ISS.parent / "Output").exists())

    def test_code_section_is_compiled_too(self):
        broken = self.d / "broken.iss"                          # a copy with one Pascal error in [Code]
        broken.write_text(ISS_TEXT.replace("  Result := True;\n", "  Result := Tru;\n", 1), encoding="ascii")
        cmd = bi.iscc_command(bi.ISCC, broken, self.app, "1.0.0", "1.0.0.3", BID, None)
        r, text = self.compile(cmd, "broken.log")
        self.assertNotEqual(r.returncode, 0, "ISCC /O- did not compile the [Code] section")
        self.assertIn("Tru", text)

    def test_missing_define_stops(self):
        cmd = '"%s" /Qp /DAppVersion=1.0.0 /DFileVersion=1.0.0.3 /DBuildId=%s /O- "%s"' % (bi.ISCC, BID, self.iss)
        r, text = self.compile(cmd, "missing.log")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("AppFolder is missing", text)


# ================================================================================================================
# install_cycle: pieces

class TestShortcut(unittest.TestCase):
    def test_unicode_link_info(self):
        target = "Z:\\made up\\\u09a0 folder\\Hangeul Document Checker.exe"
        info = ic.parse_lnk(make_lnk(target))
        self.assertEqual(info["target"], target)

    def test_ansi_link_info_and_relative(self):
        target = "Z:\\made up\\Hangeul Document Checker.exe"
        self.assertEqual(ic.parse_lnk(make_lnk(target, unicode_info=False))["target"], target)
        info = ic.parse_lnk(make_lnk(target, relative="..\\app\\x.exe"))
        self.assertEqual((info["target"], info["relative"]), (target, "..\\app\\x.exe"))

    def test_lnk_target_file(self):
        d = scratch("lnk")
        try:
            p = d / "a.lnk"
            p.write_bytes(make_lnk("", with_link_info=False, relative="..\\app\\x.exe"))
            self.assertEqual(ic.lnk_target(p), os.path.normpath(str(d.parent / "app" / "x.exe")))
            p.write_bytes(b"not a link")
            self.assertIsNone(ic.lnk_target(p))
            self.assertIsNone(ic.lnk_target(d / "missing.lnk"))
        finally:
            remove(d)
        with self.assertRaises(ValueError):
            ic.parse_lnk(b"\x4c\0\0\0" + b"\0" * 80)

    def test_same_path(self):
        self.assertTrue(ic.same_path("Z:\\A\\B\\", "z:\\a\\b"))
        self.assertTrue(ic.same_path("Z:/A/B", "Z:\\A\\B"))
        self.assertFalse(ic.same_path("Z:\\A\\B", "Z:\\A\\C"))
        self.assertFalse(ic.same_path(None, "Z:\\A"))


class TestCardrunUse(unittest.TestCase):
    def test_exit_code_of(self):
        class R:
            returncode = 0

        class E:
            exit_code = 6

        self.assertEqual(ic.exit_code_of(0), 0)
        self.assertEqual(ic.exit_code_of(R()), 0)
        self.assertEqual(ic.exit_code_of(E()), 6)
        self.assertEqual(ic.exit_code_of({"returncode": 1}), 1)
        self.assertEqual(ic.exit_code_of((4, "x")), 4)
        self.assertIsNone(ic.exit_code_of(None))
        self.assertIsNone(ic.exit_code_of(True))
        self.assertIsNone(ic.exit_code_of(object()))

    def test_cardrun_selftest(self):
        seen = {}

        class FakeCardrun:
            @staticmethod
            def run(cmd, timeout):
                seen["cmd"], seen["timeout"] = cmd, timeout
                return {"returncode": 0}

        code, why = ic.cardrun_selftest(Path("Z:\\t\\x.exe"), Path("Z:\\r"), 99.0, loader=lambda: FakeCardrun)
        self.assertEqual((code, why), (0, ""))
        self.assertEqual(seen, {"cmd": ["Z:\\t\\x.exe", "--selftest", "--report-dir", "Z:\\r"], "timeout": 99.0})

        def broken():
            raise ImportError("made-up")

        self.assertEqual(ic.cardrun_selftest(Path("x"), Path("r"), 1, loader=broken)[0], None)

        class Raises:
            @staticmethod
            def run(cmd, timeout):
                raise TimeoutError("made-up")

        code, why = ic.cardrun_selftest(Path("x"), Path("r"), 1, loader=lambda: Raises)
        self.assertIsNone(code)
        self.assertIn("TimeoutError", why)

    def test_cardrun_with_what_env_and_approval(self):
        seen = {}

        class NotApproved(RuntimeError):
            pass

        class Rich:
            approved = True

            @staticmethod
            def app_env():
                return {"MADE_UP": "1"}

            @classmethod
            def run(cls, cmd, timeout, *, what=None, env=None, cwd=None):
                if not cls.approved:
                    raise NotApproved("made-up")
                seen.update(what=what, env=env)

                class Result:
                    returncode = 0
                return Result()

        self.assertEqual(ic.cardrun_selftest(Path("x"), Path("r"), 5, loader=lambda: Rich), (0, ""))
        self.assertEqual(seen["env"], {"MADE_UP": "1"})
        self.assertIn("install cycle", seen["what"])
        Rich.approved = False
        code, why = ic.cardrun_selftest(Path("x"), Path("r"), 5, loader=lambda: Rich)
        self.assertIsNone(code)
        self.assertIn("HDC_CARDRUN_APPROVAL=Q-O1", why)

    def test_commands(self):
        self.assertEqual(ic.setup_command(Path("Z:\\i\\S.exe"), Path("Z:\\L\\Temp\\HDC install test"), Path("Z:\\r\\i.log")),
                         '"Z:\\i\\S.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="Z:\\L\\Temp\\HDC install test" '
                         '/TASKS="" /LOG="Z:\\r\\i.log"')
        self.assertEqual(ic.uninstall_command(Path("Z:\\t\\unins000.exe"), Path("Z:\\r\\u.log")),
                         '"Z:\\t\\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="Z:\\r\\u.log"')


# ================================================================================================================
# Contracts with the other tasks' real modules (read-only imports; skipped when a task has not delivered yet)

def _load_by_path(name: str, path: Path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                       # dataclasses look their module up while the class is made
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


class TestOtherTasksContracts(unittest.TestCase):
    def test_reads_task_f_manifest(self):
        path = PKG / "manifest.py"
        if not path.is_file():
            self.skipTest("Task F's manifest.py is not there yet")
        fm = _load_by_path("hdc_f_manifest_for_g_tests", path)
        d = scratch("fmanifest")
        try:
            app = make_app(d)
            (app / "_internal" / "MANIFEST.json").unlink()
            fm.write(app, build_id=BID, app_version="1.0.0")                 # F's writer, made-up folder
            mpath = bi.find_manifest(app)
            self.assertEqual(mpath.relative_to(app).as_posix(), fm.REL_PATH)
            m = bi.load_manifest(mpath)
            rep = bi.compare_tree(app, m, allow_extra=lambda r: r == fm.REL_PATH)
            self.assertTrue(rep.ok, rep.summary())
            self.assertEqual(rep.checked, 3)
            (app / bi.EXE_NAME).write_bytes(b"MZ made-up launcher bytez")       # same size, other bytes
            self.assertEqual(bi.compare_tree(app, m, allow_extra=lambda r: True).changed,
                             [bi.EXE_NAME + " (sha256)"])
        finally:
            remove(d)

    def test_reads_task_h_cardrun_result(self):
        path = TESTS / "cardrun.py"
        if not path.is_file():
            self.skipTest("Task H's cardrun.py is not there yet")
        dwb = sys.dont_write_bytecode
        try:
            cr = _load_by_path("hdc_h_cardrun_for_g_tests", path)
        finally:
            sys.dont_write_bytecode = dwb
        self.assertEqual(ic.exit_code_of(cr.CardRunResult(args=[], returncode=0)), 0)
        self.assertEqual(ic.exit_code_of(cr.CardRunResult(args=[], returncode=6)), 6)
        import inspect
        params = list(inspect.signature(cr.run).parameters)
        self.assertEqual(params[:2], ["cmd", "timeout"])
        self.assertIn("env", params)
        env = cr.app_env(base={"HDC_TEST_NO_CARD": "1", "HDC_DATA_DIR": "x", "PATH": "p"})
        self.assertEqual(env, {"PATH": "p"})          # the installed self-test then uses the real data folder


# ================================================================================================================
# install_cycle: the whole cycle against a simulated Inno Setup on made-up folders

class FakeInno:
    """Simulates Setup.exe, unins000.exe, the HKCU key, the shortcuts and the installed self-test."""

    def __init__(self, app_src: Path, pl: ic.Places, *, key: Optional[Dict[str, Any]] = None, setup_rc: int = 0,
                 install_delete: bool = True, extra_file: Optional[str] = None, selftest_rc: int = 0,
                 app_writes: bool = False, keeps_data: bool = False, location: Optional[str] = None,
                 version: str = "1.0.0", lnk_elsewhere: bool = False, desktop: bool = False,
                 case_facts: Optional[Dict[str, Any]] = None):
        self.app_src, self.pl, self.key = app_src, pl, key
        self.setup_rc, self.install_delete, self.extra_file = setup_rc, install_delete, extra_file
        self.selftest_rc, self.app_writes, self.keeps_data = selftest_rc, app_writes, keeps_data
        self.location, self.version, self.lnk_elsewhere, self.desktop = location, version, lnk_elsewhere, desktop
        self.case_facts = case_facts
        self.calls: List[str] = []
        self.selftests: List[Path] = []
        self.selftest_data: List[Optional[Path]] = []
        self.data_at_uninstall: List[bool] = []
        self.cases: List[Dict[str, Any]] = []
        self.installed: set = set()

    def uninstall_key(self):
        return dict(self.key) if self.key is not None else None

    def run(self, cmd: str, *, timeout_s: float) -> bi.RunResult:
        self.calls.append(cmd)
        if " /DIR=" in cmd:
            return self._install(cmd)
        if "unins000.exe" in cmd:
            return self._uninstall()
        raise AssertionError("unexpected command")

    def _install(self, cmd: str) -> bi.RunResult:
        for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", '/TASKS=""', "/LOG="):
            assert flag in cmd, flag
        d = Path(re.search(r'/DIR="([^"]+)"', cmd).group(1))
        assert d == self.pl.test_dir
        if self.install_delete and (d / "_internal").exists():
            shutil.rmtree(d / "_internal")
        for src in sorted(self.app_src.rglob("*")):
            if src.is_file():
                rel = src.relative_to(self.app_src).as_posix()
                dst = d / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                self.installed.add(rel)
        for name in ("unins000.exe", "unins000.dat", "unins000.msg"):
            (d / name).write_bytes(b"made-up")
            self.installed.add(name)
        if self.extra_file:
            (d / self.extra_file).write_bytes(b"made-up extra")
        self.key = {"DisplayVersion": self.version, "DisplayName": bi.UNINSTALL_DISPLAY_NAME,
                    "InstallLocation": (self.location or str(d)) + "\\",
                    "UninstallString": '"%s"' % (d / "unins000.exe")}
        self.pl.start_lnk.parent.mkdir(parents=True, exist_ok=True)
        target = str(d / ("other.exe" if self.lnk_elsewhere else bi.EXE_NAME))
        self.pl.start_lnk.write_bytes(make_lnk(target))
        if self.desktop:
            self.pl.desktop_lnk.parent.mkdir(parents=True, exist_ok=True)
            self.pl.desktop_lnk.write_bytes(make_lnk(target))
        return bi.RunResult(self.setup_rc, 2.0, in_job=True)

    def _uninstall(self) -> bi.RunResult:
        self.data_at_uninstall.append(self.pl.data_dir.exists())
        d = self.pl.test_dir
        for rel in sorted(self.installed, reverse=True):
            p = d / rel
            if p.exists():
                p.unlink()
        for dirpath, _dirs, _files in os.walk(d, topdown=False):
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        self.installed.clear()
        self.key = None
        for lnk in (self.pl.start_lnk, self.pl.desktop_lnk):
            if lnk.exists():
                lnk.unlink()
        if not self.keeps_data and self.pl.data_dir.exists():
            shutil.rmtree(self.pl.data_dir)
        return bi.RunResult(0, 1.0, in_job=True)

    def selftest(self, exe: Path, report_dir: Path, timeout_s: float, data_dir: Optional[Path] = None):
        self.selftests.append(exe)
        self.selftest_data.append(data_dir)
        assert exe == self.pl.exe and report_dir.is_dir()
        d = data_dir if data_dir is not None else self.pl.data_dir
        (d / "selftest").mkdir(parents=True, exist_ok=True)
        (d / "selftest" / "SELFTEST.txt").write_text("made-up", encoding="ascii")
        (report_dir / "SELFTEST 2026-10-29 1200.txt").write_text("made-up", encoding="ascii")
        if self.app_writes:
            (self.pl.test_dir / "written-by-app.txt").write_text("made-up", encoding="ascii")
        return self.selftest_rc, ""

    def check_case(self, exe: Path, spec: Any, out_dir: Path, data_dir: Path, override: bool = False
                   ) -> Dict[str, Any]:
        assert exe == self.pl.exe and out_dir.is_dir()
        assert (data_dir != self.pl.data_dir) == override
        self.cases.append({"label": spec.label, "installs": sum(" /DIR=" in c for c in self.calls),
                           "uninstalls": sum("unins000.exe" in c for c in self.calls),
                           "selftests": len(self.selftests), "out": out_dir, "data": data_dir,
                           "override": override})
        store = data_dir / "cache" / "pagetexts" / (spec.case_id + ".json")
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text('{"made-up": true}', encoding="ascii")
        (out_dir / "run.json").write_text("{}", encoding="ascii")
        facts = {"exit": 0, "timed_out": False, "code": spec.expect_code, "why": "", "verdicts_equal": True,
                 "summary_equal": True, "summary_lines": 22, "run_equal": True, "looks_equal": 11, "looks_r8": 11,
                 "store_in_data": True, "p1_equal": True, "p1_equal_pages": 29, "p1_pages": 29, "p1_differing": [],
                 "p1_problems": 0, "r8_equal_pages": 27, "r8_pages": 29, "r8_differing": [9, 14]}
        facts.update(self.case_facts or {})
        return facts

    def sleep(self, seconds: float) -> None:
        pass


class TestCycle(unittest.TestCase):
    def setUp(self):
        self.root = scratch("cycle")
        self.app = make_app(self.root / "dist")
        self.pl = ic.Places(self.root / "LocalAppData", self.root / "Programs", self.root / "Desktop")
        mpath = bi.find_manifest(self.app)
        self.ctx = ic.Ctx(build_id=BID, app_version="1.0.0", setup_exe=self.root / "made-up setup.exe",
                          manifest=bi.load_manifest(mpath), manifest_rel="_internal/MANIFEST.json",
                          manifest_sha256=bi.sha256_file(mpath), reports=self.root / "reports", grace_s=0)
        self.lines: List[str] = []

    def tearDown(self):
        remove(self.root)

    def cycle(self, fake: FakeInno) -> ic.Outcome:
        return ic.run_cycle(self.ctx, self.pl, fake, out=self.lines.append)

    def failed(self, o: ic.Outcome) -> List[str]:
        return ["%s/%s" % (c.step, c.name) for c in o.failed]

    def assert_nothing_left(self, fake: FakeInno):
        self.assertFalse(self.pl.test_dir.exists())
        self.assertIsNone(fake.key)
        self.assertFalse(self.pl.start_lnk.exists())
        self.assertFalse(self.pl.data_dir.exists())

    def test_pass(self):
        fake = FakeInno(self.app, self.pl)
        o = self.cycle(fake)
        self.assertEqual(o.status, "PASS", self.failed(o))
        self.assertEqual([("/DIR=" in c, "unins000.exe" in c) for c in fake.calls],
                         [(True, False), (True, False), (False, True)])
        self.assertEqual(len(fake.selftests), 2)
        names = [(c.step, c.name) for c in o.checks]
        for step in ("install-1", "install-2"):
            for name in ("setup", "files", "uninstaller", "MANIFEST.json", "uninstall key", "DisplayVersion",
                         "InstallLocation", "DisplayName", "Start menu", "desktop", "BUILD.json", "self-test"):
                self.assertIn((step, name), names)
        self.assertIn(("install-2", "upgrade"), names)
        for name in ("uninstaller", "folder gone", "key gone", "shortcut gone", "data gone"):
            self.assertIn(("uninstall", name), names)
        self.assert_nothing_left(fake)
        text = "\n".join(self.lines)
        self.assertNotIn(str(self.root), text)                 # no absolute path printed
        self.assertNotIn(str(self.root).replace("\\", "\\\\"), json.dumps(o.to_json()))

    def test_skipped_never_changes_anything(self):
        cases = {
            "key": lambda: {"DisplayVersion": "0.9"},
            "data": lambda: self.pl.data_dir.mkdir(parents=True),
            "default dir": lambda: self.pl.default_dir.mkdir(parents=True),
            "start lnk": lambda: (self.pl.programs.mkdir(parents=True, exist_ok=True),
                                  self.pl.start_lnk.write_bytes(b"x")),
            "desktop lnk": lambda: (self.pl.desktop.mkdir(parents=True, exist_ok=True),
                                    self.pl.desktop_lnk.write_bytes(b"x")),
            "test dir": lambda: (self.pl.test_dir.mkdir(parents=True, exist_ok=True),
                                 (self.pl.test_dir / "left.txt").write_bytes(b"x")),
        }
        for label, make in cases.items():
            with self.subTest(label):
                for p in (self.pl.local_appdata, self.pl.programs, self.pl.desktop):
                    shutil.rmtree(p, ignore_errors=True)
                made = make()
                fake = FakeInno(self.app, self.pl, key=made if label == "key" else None)
                o = self.cycle(fake)
                self.assertEqual(o.status, "SKIPPED")
                self.assertEqual(len(o.reasons), 1, o.reasons)
                self.assertEqual(fake.calls, [])
                self.assertEqual(fake.selftests, [])

    def test_empty_test_dir_is_fine(self):
        self.pl.test_dir.mkdir(parents=True)
        fake = FakeInno(self.app, self.pl)
        o = self.cycle(fake)
        self.assertEqual(o.status, "PASS", self.failed(o))
        self.assert_nothing_left(fake)

    def test_upgrade_must_remove_old_files(self):
        fake = FakeInno(self.app, self.pl, install_delete=False)
        o = self.cycle(fake)
        self.assertEqual(o.status, "FAIL")
        self.assertIn("install-2/upgrade", self.failed(o))
        self.assertIn("install-2/files", self.failed(o))
        self.assertNotIn("install-1/files", self.failed(o))

    def test_extra_or_wrong_values_fail_but_still_uninstall(self):
        for kw, expect in (({"extra_file": "_internal/extra.dll"}, "install-1/files"),
                           ({"version": "0.9.9"}, "install-1/DisplayVersion"),
                           ({"lnk_elsewhere": True}, "install-1/Start menu"),
                           ({"desktop": True}, "install-1/desktop"),
                           ({"selftest_rc": 1}, "install-1/self-test"),
                           ({"app_writes": True}, "uninstall/folder gone"),
                           ({"keeps_data": True}, "uninstall/data gone")):
            with self.subTest(kw):
                for p in (self.pl.local_appdata, self.pl.programs, self.pl.desktop):
                    shutil.rmtree(p, ignore_errors=True)
                fake = FakeInno(self.app, self.pl, **kw)
                o = self.cycle(fake)
                self.assertEqual(o.status, "FAIL")
                self.assertIn(expect, self.failed(o))
                self.assertTrue("unins000.exe" in fake.calls[-1], "the cycle did not uninstall")
                self.assertIsNone(fake.key)

    def test_setup_failure_cleans_up(self):
        fake = FakeInno(self.app, self.pl, setup_rc=4)
        o = self.cycle(fake)
        self.assertEqual(o.status, "FAIL")
        self.assertEqual(self.failed(o), ["install-1/setup"])
        self.assertEqual(len(fake.calls), 2)                     # one Setup, then the uninstaller
        self.assertEqual(fake.selftests, [])
        self.assert_nothing_left(fake)

    def test_never_uninstalls_an_install_elsewhere(self):
        fake = FakeInno(self.app, self.pl, location=str(self.root / "elsewhere"))
        o = self.cycle(fake)
        self.assertEqual(o.status, "FAIL")
        self.assertIn("uninstall/ownership", self.failed(o))
        self.assertFalse(any("unins000.exe" in c for c in fake.calls))
        shutil.rmtree(self.pl.test_dir)                          # clean up the simulated install by hand

    def test_partial_without_selftest(self):
        self.ctx.selftest = False
        fake = FakeInno(self.app, self.pl)
        o = self.cycle(fake)
        self.assertEqual(o.status, "PARTIAL", self.failed(o))
        self.assertEqual(fake.selftests, [])


CASE_ID = "F-0123456789abcdef"        # made-up case id
CASE_CODE = "ABCD-0123"               # made-up result code


def make_case_spec(root: Path, **kw: Any) -> "ic.CaseSpec":
    inp = root / "made-up input"
    inp.mkdir(parents=True, exist_ok=True)
    args = dict(label="owner_x_r8", input=str(inp), program="EAP", today="2026-10-28", case_id=CASE_ID, mode="folder",
                r8_dir=root / "runs" / "owner_x_r8", expect_code=CASE_CODE, apptest=root / "apptest" / BID,
                p1_store=root / "p1store.json", p1_r8_differing=[9, 14])
    args.update(kw)
    return ic.CaseSpec(**args)


class TestCycleCase(unittest.TestCase):
    """--case: one r8 case on the installed app after the upgrade (G3)."""

    def setUp(self):
        self.root = scratch("cyclecase")
        self.app = make_app(self.root / "dist")
        self.pl = ic.Places(self.root / "LocalAppData", self.root / "Programs", self.root / "Desktop")
        mpath = bi.find_manifest(self.app)
        self.ctx = ic.Ctx(build_id=BID, app_version="1.0.0", setup_exe=self.root / "made-up setup.exe",
                          manifest=bi.load_manifest(mpath), manifest_rel="_internal/MANIFEST.json",
                          manifest_sha256=bi.sha256_file(mpath), reports=self.root / "reports", grace_s=0,
                          case=make_case_spec(self.root))
        self.lines: List[str] = []

    def tearDown(self):
        remove(self.root)

    def cycle(self, fake: FakeInno) -> ic.Outcome:
        return ic.run_cycle(self.ctx, self.pl, fake, out=self.lines.append)

    def test_case_runs_after_the_upgrade_and_its_data_is_deleted(self):
        fake = FakeInno(self.app, self.pl)
        o = self.cycle(fake)
        self.assertEqual(o.status, "PASS", [c.name for c in o.failed])
        self.assertEqual(len(fake.cases), 1)
        self.assertEqual({k: fake.cases[0][k] for k in ("installs", "uninstalls", "selftests")},
                         {"installs": 2, "uninstalls": 0, "selftests": 2})
        out = fake.cases[0]["out"]
        self.assertEqual((out.name, out.parent.name), ("out", "owner_x_r8"))
        self.assertTrue(out.parent.parent.name.startswith("p3-"))
        self.assertEqual(out.parent.parent.parent, self.ctx.case.apptest)
        names = [(c.step, c.name) for c in o.checks]
        for name in ("run", "result code", "verdicts", "data folder", "page texts"):
            self.assertIn(("case", name), names)
        self.assertLess(names.index(("case", "run")), names.index(("uninstall", "data gone")))
        self.assertFalse(self.pl.data_dir.exists())             # the case's page texts went with the uninstall
        self.assertEqual(o.facts["case"]["expect_code"], CASE_CODE)
        self.assertTrue(any("the same pages as in P1" in n for n in o.notes))
        text = "\n".join(self.lines) + json.dumps(o.to_json())
        self.assertNotIn(str(self.root), text)
        self.assertNotIn(str(self.root).replace("\\", "\\\\"), text)
        self.assertNotIn("made-up input", text)

    def test_a_wrong_case_result_fails_but_still_uninstalls(self):
        for facts, expect in (({"code": "FFFF-0000"}, "result code"), ({"verdicts_equal": False}, "verdicts"),
                              ({"exit": 1}, "run"), ({"timed_out": True}, "run"),
                              ({"store_in_data": False}, "data folder"),
                              ({"p1_equal": False, "p1_differing": [3]}, "page texts"),
                              ({"exit": None, "code": None, "why": "cardrun.run raised NotApproved"}, "run")):
            with self.subTest(facts):
                for p in (self.pl.local_appdata, self.pl.programs, self.pl.desktop):
                    shutil.rmtree(p, ignore_errors=True)
                fake = FakeInno(self.app, self.pl, case_facts=facts)
                o = self.cycle(fake)
                self.assertEqual(o.status, "FAIL")
                self.assertIn("case/" + expect, ["%s/%s" % (c.step, c.name) for c in o.failed])
                self.assertTrue("unins000.exe" in fake.calls[-1])
                self.assertFalse(self.pl.data_dir.exists())

    def test_no_case_when_setup_fails_and_no_p1_store_is_a_note(self):
        fake = FakeInno(self.app, self.pl, setup_rc=4)
        o = self.cycle(fake)
        self.assertEqual(fake.cases, [])
        self.assertEqual(o.status, "FAIL")
        self.ctx.case = make_case_spec(self.root, p1_store=None)
        o = self.cycle(FakeInnoNoP1(self.app, self.pl))
        self.assertEqual(o.status, "PASS", [c.name for c in o.failed])
        self.assertNotIn(("case", "page texts"), [(c.step, c.name) for c in o.checks])
        self.assertTrue(any("compared with r8 only" in n for n in o.notes))

    def test_data_dir_for_a_redirected_appdata(self):
        self.ctx.app_data = self.root / "private data"
        fake = FakeInno(self.app, self.pl)
        o = self.cycle(fake)
        self.assertEqual(o.status, "PASS", [c.name for c in o.failed])
        self.assertEqual(fake.selftest_data, [self.ctx.app_data, self.ctx.app_data])
        self.assertEqual((fake.cases[0]["data"], fake.cases[0]["override"]), (self.ctx.app_data, True))
        self.assertEqual(fake.data_at_uninstall, [True])          # the made-up file was there for the uninstaller
        self.assertFalse(self.pl.data_dir.exists())
        self.assertTrue((self.ctx.app_data / "cache" / "pagetexts" / (CASE_ID + ".json")).is_file())
        self.assertTrue(any("made-up file was put in the app's data folder" in n for n in o.notes))
        fake = FakeInno(self.app, self.pl, keeps_data=True)       # an uninstaller that keeps the data: caught
        o = self.cycle(fake)
        self.assertIn("uninstall/data gone", ["%s/%s" % (c.step, c.name) for c in o.failed])
        shutil.rmtree(self.pl.data_dir)

    def test_selftest_command_with_a_data_dir(self):
        seen = {}

        class FakeCardrun:
            @staticmethod
            def app_env(data_dir=None):
                return {"D": str(data_dir)}

            @staticmethod
            def run(cmd, timeout, *, what=None, env=None):
                seen.update(cmd=cmd, env=env)
                return {"returncode": 0}

        self.assertEqual(ic.cardrun_selftest(Path("Z:\\t\\x.exe"), Path("Z:\\r"), 9, loader=lambda: FakeCardrun,
                                             data_dir=Path("Z:\\d")), (0, ""))
        self.assertEqual(seen["cmd"], ["Z:\\t\\x.exe", "--data-dir", "Z:\\d", "--selftest", "--report-dir", "Z:\\r"])
        self.assertEqual(seen["env"], {"D": "Z:\\d"})
        ic.cardrun_selftest(Path("x.exe"), Path("r"), 9, loader=lambda: FakeCardrun)
        self.assertEqual((seen["cmd"][1], seen["env"]), ("--selftest", {"D": "None"}))

    def test_case_command_has_no_data_dir(self):
        spec = make_case_spec(self.root)
        cmd = ic.case_command(Path("Z:\\t\\a.exe"), spec, Path("Z:\\o"))
        self.assertEqual(cmd, ["Z:\\t\\a.exe", "--cli", spec.input, "--program", "EAP", "--out", "Z:\\o",
                               "--today", "2026-10-28"])
        spec.today = None
        self.assertNotIn("--today", ic.case_command(Path("a.exe"), spec, Path("o")))
        self.assertNotIn("--data-dir", cmd)
        self.assertEqual(ic.case_command(Path("a.exe"), spec, Path("o"), Path("Z:\\d"))[-2:], ["--data-dir", "Z:\\d"])

    def test_cardrun_case_judges_with_parity(self):
        seen: Dict[str, Any] = {}
        spec = make_case_spec(self.root)
        data = self.root / "data"
        store = data / "cache" / "pagetexts" / (CASE_ID + ".json")
        store.parent.mkdir(parents=True)
        store.write_text('{"files": []}', encoding="ascii")
        spec.p1_store.write_text('{"files": []}', encoding="ascii")
        out = self.root / "p3" / "owner_x_r8" / "out"
        out.mkdir(parents=True)

        class Res:
            returncode, timed_out, lines = 0, False, [(1.0, "made-up")]

            @staticmethod
            def stats():
                return {"seconds": 300.0, "slot_wait_s": 0.1, "peak_working_set_mb": 2000.0,
                        "job_peak_process_commit_mb": 5400.0}

        class FakeCardrun:
            @staticmethod
            def app_env(data_dir=None):
                return {"MADE_UP": "1"} if data_dir is None else {"MADE_UP": "1", "HDC_DATA_DIR": str(data_dir)}

            @staticmethod
            def run(cmd, timeout, *, what=None, env=None, log_dir=None):
                seen.update(cmd=cmd, timeout=timeout, what=what, env=env, log_dir=log_dir)
                return Res()

            events_of = staticmethod(lambda lines: [(1.0, {"ev": "done"})])
            done_event = staticmethod(lambda ev: {"code": "0000-0000", "gpu": {"cpu_calls": 0, "waits": 1}})
            error_codes = staticmethod(lambda ev: [])
            read_json = staticmethod(lambda p: json.loads(Path(p).read_text(encoding="ascii")))

        class FakeParity:
            class R8Case:
                def __init__(self, **kw):
                    seen["case"] = kw

            @staticmethod
            def judge_outputs(case, out_dir, data_dir):
                seen["judged"] = (out_dir, data_dir)
                return {"code": CASE_CODE, "verdicts_equal": True,
                        "summary": {"equal": True, "lines_r8": 22, "diffs": []},
                        "run": {"equal": True, "diffs": []}, "looks": {"equal_looks": 11, "looks_r8": 11, "diffs": []},
                        "page_texts": {"equal_pages": 27, "pages_r8": 29, "differing": [{"page": 9}, {"page": 14}]}}

            find_store = staticmethod(lambda d, cid: store if cid == CASE_ID else None)

            @staticmethod
            def compare_page_texts(a, b):
                seen["p1_compare"] = (a, b)
                return {"equal": True, "equal_pages": 29, "pages_r8": 29, "differing": [], "problems": []}

        f = ic.cardrun_case(self.pl.exe, spec, out, data, loader=lambda: FakeCardrun, judge_loader=lambda: FakeParity)
        self.assertEqual(seen["cmd"], ic.case_command(self.pl.exe, spec, out))
        self.assertEqual((seen["env"], seen["log_dir"], seen["timeout"]), ({"MADE_UP": "1"}, out.parent, spec.timeout_s))
        self.assertIn("install cycle", seen["what"])
        self.assertEqual(seen["judged"], (out, data))
        self.assertEqual(seen["case"]["case_id"], CASE_ID)
        self.assertEqual(seen["p1_compare"], ({"files": []}, {"files": []}))
        self.assertEqual((f["exit"], f["code"], f["verdicts_equal"], f["store_in_data"], f["p1_equal"]),
                         (0, CASE_CODE, True, True, True))
        self.assertEqual((f["r8_differing"], f["cpu_calls"], f["peak_working_set_mb"]), ([9, 14], 0, 2000.0))
        f = ic.cardrun_case(self.pl.exe, spec, out, data, override=True, loader=lambda: FakeCardrun,
                            judge_loader=lambda: FakeParity)
        self.assertEqual(seen["cmd"][-2:], ["--data-dir", str(data)])
        self.assertEqual(seen["env"], {"MADE_UP": "1", "HDC_DATA_DIR": str(data)})
        self.assertTrue(f["store_in_data"])
        store.unlink()                                    # only another pagetexts file left: not the case's
        (store.parent / "selftest-made-up.json").write_text("{}", encoding="ascii")
        f = ic.cardrun_case(self.pl.exe, spec, out, data, loader=lambda: FakeCardrun, judge_loader=lambda: FakeParity)
        self.assertFalse(f["store_in_data"])
        self.assertNotIn("p1_equal", f)
        self.assertIsNone(ic.case_store(data, ""))
        nested = data / "x" / "pagetexts" / (CASE_ID + ".json")
        nested.parent.mkdir(parents=True)
        nested.write_text("{}", encoding="ascii")
        self.assertEqual(ic.case_store(data, CASE_ID), nested)

        class NotApproved(RuntimeError):
            pass

        class Refuses(FakeCardrun):
            @staticmethod
            def run(cmd, timeout, **kw):
                raise NotApproved("made-up")

        f = ic.cardrun_case(self.pl.exe, spec, out, data, loader=lambda: Refuses, judge_loader=lambda: FakeParity)
        self.assertIsNone(f["exit"])
        self.assertIn("HDC_CARDRUN_APPROVAL=Q-O1", f["why"])

    def test_load_case(self):
        runs, builds, apptest = self.root / "runs", self.root / "builds", self.root / "apptest"
        inp = self.root / "made-up input"
        inp.mkdir(exist_ok=True)                     # setUp's case spec made it already
        (runs / "owner_x_r8").mkdir(parents=True)
        (runs / "owner_x_r8" / "run.json").write_text(json.dumps(
            {"input": str(inp), "program": "EAP", "today": "2026-10-28", "case_id": CASE_ID, "mode": "folder"}),
            encoding="utf-8")
        (builds / BID / "reports").mkdir(parents=True)
        parity_json = builds / BID / "reports" / "parity.json"
        p1 = {"build_id": BID, "passed": True, "run_folder": "p1-20261029-073106",
              "cases": [{"label": "owner_x_r8", "ok": True, "code": CASE_CODE,
                         "page_texts": {"differing": [{"page": 9}, {"page": 14}]}}]}
        parity_json.write_text(json.dumps(p1), encoding="utf-8")
        store = apptest / BID / "p1-20261029-073106" / "owner_x_r8" / "data" / "cache" / "pagetexts" / (CASE_ID + ".json")
        store.parent.mkdir(parents=True)
        store.write_text("{}", encoding="ascii")
        spec = ic.load_case(BID, builds, "owner_x_r8", runs=runs, apptest=apptest)
        self.assertEqual((spec.input, spec.program, spec.today, spec.case_id, spec.expect_code),
                         (str(inp), "EAP", "2026-10-28", CASE_ID, CASE_CODE))
        self.assertEqual((spec.p1_store, spec.p1_r8_differing, spec.apptest), (store, [9, 14], apptest / BID))
        self.assertEqual(ic.load_case(BID, builds, "owner_x_r8", runs=runs, apptest=apptest,
                                      expect_code="0000-1111").expect_code, "0000-1111")
        bad = (({"label": "../x"}, "r8 run name"), ({"label": "owner_y_r8"}, "no readable run.json"),
               ({"expect_code": "abcd-0123"}, "XXXX-XXXX"), ({"case_input": "relative"}, "not there"))
        for kw, msg in bad:
            with self.subTest(kw):
                label = kw.pop("label", "owner_x_r8")
                with self.assertRaises(ValueError) as cm:
                    ic.load_case(BID, builds, label, runs=runs, apptest=apptest, **kw)
                self.assertIn(msg, str(cm.exception))
                self.assertNotIn(str(self.root), str(cm.exception))
        parity_json.write_text(json.dumps(dict(p1, passed=False)), encoding="utf-8")
        with self.assertRaises(ValueError):
            ic.load_case(BID, builds, "owner_x_r8", runs=runs, apptest=apptest)
        parity_json.write_text(json.dumps(dict(p1, build_id="20261029.4-abc1234")), encoding="utf-8")
        with self.assertRaises(ValueError):
            ic.load_case(BID, builds, "owner_x_r8", runs=runs, apptest=apptest, expect_code=CASE_CODE)
        parity_json.unlink()
        with self.assertRaises(ValueError):
            ic.load_case(BID, builds, "owner_x_r8", runs=runs, apptest=apptest)
        spec = ic.load_case(BID, builds, "owner_x_r8", runs=runs, apptest=apptest, expect_code=CASE_CODE)
        self.assertIsNone(spec.p1_store)


class FakeInnoNoP1(FakeInno):
    """The case's facts without a P1 page-texts comparison (P1's store was not found)."""

    def check_case(self, exe, spec, out_dir, data_dir):
        f = super().check_case(exe, spec, out_dir, data_dir)
        for k in ("p1_equal", "p1_equal_pages", "p1_pages", "p1_differing", "p1_problems"):
            f.pop(k, None)
        return f


class TestCycleMain(unittest.TestCase):
    def setUp(self):
        self.root = scratch("cyclemain")
        self.builds = self.root / "builds"
        self.app = make_app(self.builds / BID / "dist")
        inst = self.builds / BID / "installer"
        inst.mkdir(parents=True)
        self.setup = inst / "Hangeul Document Checker Setup 1.0.0 GPU.exe"
        self.setup.write_bytes(b"made-up setup")
        info = {"app_id": bi.APP_ID, "app_version": "1.0.0", "build_id": BID, "setup": self.setup.name,
                "files": [{"name": self.setup.name, "bytes": self.setup.stat().st_size,
                           "sha256": bi.sha256_file(self.setup)}]}
        (inst / "installer.json").write_text(json.dumps(info), encoding="utf-8")
        self.pl = ic.Places(self.root / "LocalAppData", self.root / "Programs", self.root / "Desktop")
        self.lines: List[str] = []

    def tearDown(self):
        remove(self.root)

    def main(self, fake, *extra):
        return ic.main(["--build-id", BID, "--builds-root", str(self.builds), "--grace-s", "0"] + list(extra),
                       system=fake, places=self.pl, out=self.lines.append)

    def test_pass_and_report(self):
        fake = FakeInno(self.app, self.pl)
        self.assertEqual(self.main(fake), ic.EXIT_PASS, self.lines)
        self.assertTrue(fake.calls[0].startswith('"%s" ' % self.setup))
        report = self.builds / BID / "reports" / "install_cycle" / "install_cycle.json"
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((data["status"], data["build_id"]), ("PASS", BID))
        self.assertNotIn(str(self.root).replace("\\", "\\\\"), report.read_text(encoding="utf-8"))
        self.assertTrue(self.lines[-1].startswith("RESULT: PASS"))

    def test_main_with_a_case(self):
        runs, apptest = self.root / "runs", self.root / "apptest"
        inp = self.root / "made-up input"
        inp.mkdir()
        (runs / "owner_x_r8").mkdir(parents=True)
        (runs / "owner_x_r8" / "run.json").write_text(json.dumps(
            {"input": str(inp), "program": "KLP", "today": "2026-10-28", "case_id": CASE_ID}), encoding="utf-8")
        args = ["--case", "owner_x_r8", "--runs-root", str(runs), "--apptest-root", str(apptest)]
        fake = FakeInno(self.app, self.pl)
        self.assertEqual(self.main(fake, *args), ic.EXIT_USAGE)          # no P1 result and no --expect-code
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.main(fake, "--expect-code", CASE_CODE), ic.EXIT_USAGE)   # needs --case
        self.assertEqual(self.main(fake, *args, "--expect-code", CASE_CODE), ic.EXIT_PASS, self.lines)
        self.assertEqual(len(fake.cases), 1)
        report = self.builds / BID / "reports" / "install_cycle" / "install_cycle.json"
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual((data["facts"]["case"]["expect_code"], data["facts"]["case"]["code"]), (CASE_CODE, CASE_CODE))
        self.assertNotIn(str(self.root).replace("\\", "\\\\"), report.read_text(encoding="utf-8"))
        self.assertEqual(len(list((apptest / BID).glob("p3-*"))), 1)
        busy = self.root / "busy data"
        busy.mkdir()
        (busy / "x.txt").write_text("made-up", encoding="ascii")
        la = os.environ.get("LOCALAPPDATA") or "C:\\made-up-la"
        for bad in ("relative data", str(busy), la + "\\made-up hdc test data"):
            with self.subTest(bad):
                self.assertEqual(self.main(FakeInno(self.app, self.pl), "--data-dir", bad), ic.EXIT_USAGE)
        self.assertFalse(os.path.exists(la + "\\made-up hdc test data"))
        fake = FakeInno(self.app, self.pl)
        self.assertEqual(self.main(fake, *args, "--expect-code", CASE_CODE, "--data-dir", str(self.root / "pd")),
                         ic.EXIT_PASS, self.lines)
        self.assertEqual(fake.selftest_data, [self.root / "pd", self.root / "pd"])

    def test_exit_codes(self):
        self.assertEqual(self.main(FakeInno(self.app, self.pl, key={"x": 1})), ic.EXIT_SKIPPED)
        self.assertEqual(self.main(FakeInno(self.app, self.pl), "--no-selftest"), ic.EXIT_PARTIAL)
        self.assertEqual(self.main(FakeInno(self.app, self.pl, selftest_rc=1)), ic.EXIT_FAIL)

    def test_refuses_a_changed_setup_or_missing_inputs(self):
        self.setup.write_bytes(b"made-up setup, changed")
        fake = FakeInno(self.app, self.pl)
        self.assertEqual(self.main(fake), ic.EXIT_FAIL)
        self.assertEqual(fake.calls, [])
        self.assertEqual(ic.main(["--build-id", "20261029.4-abc1234", "--builds-root", str(self.builds)],
                                 system=fake, places=self.pl, out=self.lines.append), ic.EXIT_USAGE)
        self.assertEqual(ic.main(["--build-id", "bad"], system=fake, places=self.pl, out=self.lines.append),
                         ic.EXIT_USAGE)


if __name__ == "__main__":
    unittest.main()
