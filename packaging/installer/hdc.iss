; Hangeul Document Checker - installer script for Inno Setup 6.7.4 (APP_SPEC revision 2, R2.5).
;
; Compiled only by packaging\installer\build_installer.py, never by hand:
;   ISCC.exe /Qp /DAppFolder="<dist>\Hangeul Document Checker" /DAppVersion=1.0.0 /DFileVersion=1.0.0.<N>
;            /DBuildId=<build id> /O"<builds>\<build id>\installer" hdc.iss
; and a second time with /DSpan=1 only when ISCC reports its 4,200,000,000-byte limit for one Setup.exe.
;
; AppId {767599DF-1DFD-490C-A03A-32EE783D7301} was chosen once, on 29 Oct 2026 (Task G), and is NEVER changed.
; Inno Setup and Windows (Settings > Apps, HKCU ...\Uninstall\{AppId}_is1) know an existing install only by it:
; another GUID would install a second copy next to the first instead of upgrading it.
; build_installer.APP_GUID holds the same value; a unit test keeps the two equal.
;
; Comments stand on their own lines: Inno Setup reads the rest of a directive line as part of its value.
; This file is plain ASCII.

#ifndef AppFolder
#error AppFolder is missing - compile with /DAppFolder=<dist folder of the app>
#endif
#ifndef AppVersion
#error AppVersion is missing - compile with /DAppVersion=1.0.0
#endif
#ifndef FileVersion
#error FileVersion is missing - compile with /DFileVersion=1.0.0.N
#endif
#ifndef BuildId
#error BuildId is missing - compile with /DBuildId=<build id>
#endif

[Setup]
AppId={{767599DF-1DFD-490C-A03A-32EE783D7301}
AppName=Hangeul Document Checker
AppVersion={#AppVersion}
AppVerName=Hangeul Document Checker {#AppVersion} (GPU, build {#BuildId})
AppPublisher=Hangeul
; Per user, no admin right.
PrivilegesRequired=lowest
; For a per-user install {autopf} is %LOCALAPPDATA%\Programs.
DefaultDirName={autopf}\Hangeul Document Checker
DisableProgramGroupPage=yes
; 64-bit Windows on x64 only: there is no CUDA on Arm64 Windows.
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
; Windows 10 1809 (build 17763) or newer.
MinVersion=10.0.17763
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
; About 4 threads and 3 GB of memory while compressing (about 742 MB per block thread); 64 MB to decompress.
LZMANumBlockThreads=2
#ifdef Span
; Only above the 4,200,000,000-byte limit of one Setup.exe: Setup.exe plus "-1.bin", "-2.bin" ..., kept in one folder.
DiskSpanning=yes
DiskSliceSize=2000000000
#else
DiskSpanning=no
#endif
; The window's single-instance mutex (APP_SPEC rev 1, 1.4): no install or uninstall while the checker runs.
AppMutex=HangeulDocumentChecker
WizardStyle=modern
SetupLogging=yes
UninstallDisplayName=Hangeul Document Checker (GPU)
UninstallDisplayIcon={app}\Hangeul Document Checker.exe
OutputBaseFilename=Hangeul Document Checker Setup {#AppVersion} GPU
VersionInfoVersion={#FileVersion}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductName=Hangeul Document Checker
VersionInfoCompany=Hangeul
VersionInfoDescription=Hangeul Document Checker Setup (GPU)
; APP_SPEC R2.5 asks for "VersionInfoComments=build <id>", but Inno Setup 6.7.4 has no such directive (ISCC stops on
; it). The build id goes into the product version text instead (Explorer: Properties > Details > Product version).
VersionInfoProductTextVersion={#AppVersion} (GPU, build {#BuildId})

[InstallDelete]
; An upgrade leaves exactly the new build's files: the old runtime folder goes first.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#AppFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"

[Icons]
Name: "{autoprograms}\Hangeul Document Checker"; Filename: "{app}\Hangeul Document Checker.exe"
Name: "{autodesktop}\Hangeul Document Checker"; Filename: "{app}\Hangeul Document Checker.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Hangeul Document Checker.exe"; Description: "Open the checker now"; Flags: postinstall nowait skipifsilent

[Code]
const
  NoDriverText = 'This PC has no NVIDIA graphics driver. The checker needs an NVIDIA graphics card (GeForce RTX 20 series or newer) to check documents. Install anyway?';
  DeleteDataText = 'Also delete the app''s saved data (saved page texts, recent checks, settings)? Your reports in Documents are kept.';

{ True when the NVIDIA driver's NVML library (what the checker reads the card with) is in the 64-bit System32
  folder. Setup itself is a 32-bit program: "Sysnative" reaches the 64-bit System32 while WOW64 file redirection
  is on, and the plain sys-constant path does when 64-bit install mode has turned redirection off for these calls.
  (No curly braces inside these comments: the first closing one would end the comment.) }
function NvidiaDriverPresent(): Boolean;
begin
  Result := FileExists(ExpandConstant('{win}\Sysnative\nvml.dll'))
    or FileExists(ExpandConstant('{sys}\nvml.dll'));
end;

{ R2.5: without the NVIDIA driver, ask "Install anyway?" (Yes / No, default No). A silent install only logs it
  and goes on. }
function InitializeSetup(): Boolean;
begin
  Result := True;
  if NvidiaDriverPresent() then
    Log('NVIDIA graphics driver found (nvml.dll).')
  else
  begin
    Log('No NVIDIA graphics driver: nvml.dll is not in the 64-bit System32 folder.');
    if WizardSilent() then
      Log('Silent install: going on without asking.')
    else
    begin
      Result := (SuppressibleMsgBox(NoDriverText, mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES);
      if Result then
        Log('The user chose to install without an NVIDIA driver.')
      else
        Log('The user chose not to install without an NVIDIA driver.');
    end;
  end;
end;

{ R2.5 and Q3: after the app's files are gone, ask whether to delete the app's own data folder
  (%LOCALAPPDATA%\Hangeul Document Checker: saved page texts, recent checks, settings). Default Yes; a silent
  uninstall takes Yes. The reports in Documents are never touched. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  LocalData: String;
  DataDir: String;
  DeleteIt: Boolean;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  LocalData := ExpandConstant('{localappdata}');
  if Length(LocalData) < 4 then
  begin
    Log('The local app data folder is unknown: the app data folder is kept.');
    Exit;
  end;
  DataDir := ExpandConstant('{localappdata}\Hangeul Document Checker');
  if not DirExists(DataDir) then
  begin
    Log('There is no app data folder to delete.');
    Exit;
  end;
  if UninstallSilent() then
    DeleteIt := True
  else
    DeleteIt := (SuppressibleMsgBox(DeleteDataText, mbConfirmation, MB_YESNO, IDYES) = IDYES);
  if DeleteIt then
  begin
    if DelTree(DataDir, True, True, True) then
      Log('Deleted the app data folder.')
    else
      Log('Could not delete the whole app data folder (a file may be in use).');
  end
  else
    Log('The user kept the app data folder.');
end;
