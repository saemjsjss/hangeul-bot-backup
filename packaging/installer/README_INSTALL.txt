HANGEUL DOCUMENT CHECKER {APP_VERSION} (GPU) - INSTALL (build {BUILD_ID})

NEEDS: Windows 10 (1809 or newer) or 11, 64-bit; an NVIDIA graphics card, GeForce RTX 20 series or newer, with 6 GB
or more; NVIDIA driver 570 or newer; about 5 GB of free disk. No internet and no admin rights are needed.

1. COPY "Hangeul Document Checker Setup {APP_VERSION} GPU.exe" (about {SETUP_GB} GB) to the PC, for example with a
   USB stick. If there are also files ending in "-1.bin", "-2.bin", copy them into the same folder.
2. RUN it. If Windows says "Windows protected your PC": click "More info", then "Run anyway".
   Your antivirus may take a few minutes to scan it.
3. OPEN "Hangeul Document Checker" from the Start menu. If it says the graphics card or driver is not suitable,
   do what the message says (usually: update the NVIDIA driver) and open it again.
4. SELF-TEST: click "Self-test" and wait about 2 minutes. It saves SELFTEST.txt in Documents\Hangeul Document Checks.
5. CHECK ONE CASE AND COMPARE: click "Choose PDF..." and pick CHECK_0001.pdf, program KLP. Open "Options", choose
   "Check date: fixed" and type {CHECK_DATE}. Click "Check" and look at "Result code".
   The office PC's code is {THIS_PC_CODE}. The same code means the same verdicts. Another graphics card model can
   read a few characters differently: if the code differs, send checkfacts.json too.
6. SEND BACK SELFTEST.txt and the code. Never send summary.txt, report.xlsx or run.json: they hold student data.

ON THE OFFICE PC (where the Telegram bot runs): while the bot is checking documents, the checker shows "Waiting for
the graphics card" and goes on by itself when the bot is done.

TO REMOVE: Settings > Apps > Hangeul Document Checker > Uninstall. It asks whether to delete the saved page texts and
settings too (Yes is the default). Your reports in Documents\Hangeul Document Checks stay.
