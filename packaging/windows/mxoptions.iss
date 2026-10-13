; The installer of MX Options for Windows, made with Inno Setup (https://jrsoftware.org/isinfo.php).
; tools\build_windows.py runs it over the folder it has built:  ISCC /DAppVersion=... /DSource=... mxoptions.iss
;
; It installs for the user who runs it, into that user's own program folder, and asks for no administrator.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Source
  #define Source "..\..\dist\mxoptions"
#endif
#ifndef Output
  #define Output "..\..\dist"
#endif
#ifndef Icon
  #define Icon "..\..\build\windows\icon.ico"
#endif

[Setup]
AppId={{6F0B6D2E-8C0C-4B5A-9B0E-4D584F505431}
AppName=MX Options
AppVersion={#AppVersion}
AppPublisher=MX Options
AppPublisherURL=https://github.com/nictonus/mxoptions
AppSupportURL=https://github.com/nictonus/mxoptions/issues
DefaultDirName={autopf}\MX Options
DefaultGroupName=MX Options
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
OutputDir={#Output}
OutputBaseFilename=mxoptions-{#AppVersion}-setup
SetupIconFile={#Icon}
UninstallDisplayIcon={app}\mxoptions.exe
UninstallDisplayName=MX Options
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#Source}\LICENSE
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"

[CustomMessages]
english.Autostart=Start MX Options when I sign in
ukrainian.Autostart=Запускати MX Options під час входу в систему
english.OptionsPlus=Logi Options+ is running. It and MX Options undo each other's settings of the mouse: close it, and stop its service (OptionsPlusUpdaterService), which would start it again. MX Options is installed all the same.
ukrainian.OptionsPlus=Logi Options+ зараз працює. Він і MX Options скасовують налаштування миші один одного: закрийте його й зупиніть його службу (OptionsPlusUpdaterService), яка запустить його знову. MX Options усе одно буде встановлено.

[Tasks]
Name: "autostart"; Description: "{cm:Autostart}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\MX Options"; Filename: "{app}\mxoptions.exe"
Name: "{autodesktop}\MX Options"; Filename: "{app}\mxoptions.exe"; Tasks: desktopicon

[Registry]
; The same entry the program's own "Start at login" switch writes and removes (mxoptions\windows.py).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "mxoptions"; ValueData: """{app}\mxoptions.exe"" --background"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\mxoptions.exe"; Description: "{cm:LaunchProgram,MX Options}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The settings (in the user's roaming folder) are left alone; what was fetched or made for this computer goes.
Type: filesandordirs; Name: "{localappdata}\mxoptions\cache"

[Code]
// A copy that is running holds its files open: it is asked to leave before they are replaced or removed.
procedure StopRunningCopy();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM mxoptions.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function OptionsPlusRunning(): Boolean;
var
  Code: Integer;
begin
  // tasklist prints the program if it runs; find says with its exit status whether it did
  Result := Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq logioptionsplus_agent.exe" /NH | find /I "logioptionsplus_agent" >NUL', '',
                 SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningCopy();
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not WizardSilent()) and OptionsPlusRunning() then
    MsgBox(CustomMessage('OptionsPlus'), mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopRunningCopy();
end;
