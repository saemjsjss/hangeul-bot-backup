HANGEUL DOCUMENT CHECKER {APP_VERSION} (GPU) - build {BUILD_ID}

WHAT IS IN THIS FOLDER
  - Hangeul Document Checker Setup {APP_VERSION} GPU.exe: the installer, {SETUP_GB} GB ({SETUP_BYTES} bytes).
    If there are also files ending in "-1.bin", "-2.bin" and so on, they belong to it: keep them in the same folder.
  - This text file.
  - SHA256SUMS.txt: checksums, to confirm that a copy is complete and unchanged.

WHAT THE PC NEEDS
  - Windows 10 (version 1809 or newer) or Windows 11, 64-bit.
  - An NVIDIA graphics card with 6 GB of graphics memory or more (GeForce RTX 20 series or newer).
    The checker reads documents only on this card. It does not work on a PC without one.
  - A recent NVIDIA driver: version 570 or newer. If the checker says the driver is too old, update it with the
    NVIDIA app (or from nvidia.com), restart the PC and open the checker again.
  - About {DISK_GB} GB of free disk space: {SETUP_GB} GB for the Setup file and {INSTALLED_GB} GB for the installed
    checker. After installing, the Setup file can be deleted from the PC (keep a copy on the USB stick).
  - No internet connection and no administrator rights.

HOW TO INSTALL
  1. Copy the Setup file to the PC, for example with a USB stick.
  2. Double-click it.
  3. Windows may show a blue box: "Windows protected your PC". This happens because the installer is
     not digitally signed (signing needs a paid certificate), not because something is wrong with it.
     Click "More info", then "Run anyway". Your antivirus may also take a few minutes to scan the file.
  4. If the PC has no NVIDIA driver, Setup asks "Install anyway?". Choose No, install the NVIDIA driver, then run
     Setup again.
  5. Click "Next" and "Install". Setup installs the checker for your Windows user only. "Create a desktop shortcut"
     is ticked; untick it if you do not want one. At the end, leave "Open the checker now" ticked and click
     "Finish".

THE FIRST START
  - The first start can take a few minutes: Windows and the antivirus look at about {INSTALLED_GB} GB of program
    files. Later starts are quicker.
  - The checker looks at the graphics card and its driver. If something is not suitable, a message at the top of
    the window says what to do, and the "Check" button stays off.
  - Reports go to Documents\Hangeul Document Checks. If Documents is synced by OneDrive, they go to
    C:\Users\<your name>\Hangeul Document Checks instead, so that student documents are not uploaded.
  - The checker keeps its settings and saved page texts (they make a second check of the same documents faster)
    in its own data folder; saved page texts are deleted after 30 days.

CHECK THAT IT WORKS (once on every new PC)
  1. Open "Hangeul Document Checker" from the desktop shortcut or the Start menu.
  2. Click the "Self-test" button at the bottom of the window and wait: about 2 minutes, longer while the
     graphics card is busy. The self-test uses only the test pages that come with the checker, never student
     documents.
  3. If it says "every part is OK", the PC is ready. If it says "finished with problems", send the file
     SELFTEST.txt from the report folder (see "Reports go to" above). SELFTEST.txt holds no student data.
  Never send summary.txt, report.xlsx or run.json to anyone: they hold student data.

ON THE OFFICE PC (where the Telegram bot runs)
  While the bot is checking documents, the checker shows "Waiting for the graphics card" and goes on by itself
  when the bot is done. A check uses at most 3 GB of the graphics card's memory.

HOW TO UNINSTALL
  Close the checker first. Then Settings > Apps > "Hangeul Document Checker (GPU)" > Uninstall (or Control Panel >
  Programs and Features). The uninstaller asks whether to delete the saved page texts and settings too; Yes is
  the default. Your reports in Documents\Hangeul Document Checks are kept; delete them yourself if you no longer
  need them.

CHECKING A COPY (optional)
  In PowerShell, in this folder:
      Get-FileHash -Algorithm SHA256 "Hangeul Document Checker Setup {APP_VERSION} GPU.exe"
  The hash must equal the one in SHA256SUMS.txt (upper or lower case does not matter).
