; Zabbix Report Studio — Inno Setup installer script
; Build:  ISCC.exe installer.iss   (output: installer\ZabbixReportStudio-Setup.exe)
; First run:  pyinstaller desktop.spec --noconfirm   (creates dist\Zabbix Report Studio)

#define AppName "Zabbix Report Studio"
#define AppVersion "1.0.0"
#define AppPublisher "Zabbix Report Studio"
#define AppExeName "Zabbix Report Studio.exe"

[Setup]
AppId={{3D7F1C22-9A4B-4E58-B0E1-ZRS0STUDIO0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Zabbix Report Studio
DefaultGroupName=Zabbix Report Studio
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=ZabbixReportStudio-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install -> no administrator (UAC) prompt required.
; In this mode {autopf}/{autodesktop}/{group} resolve to per-user folders
; (%LOCALAPPDATA%\Programs, the user's desktop / start menu).
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=static\favicon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; The whole PyInstaller onedir output (exe + _internal)
Source: "dist\Zabbix Report Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Zabbix Report Studio"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,Zabbix Report Studio}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Zabbix Report Studio"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,Zabbix Report Studio}"; Flags: nowait postinstall skipifsilent
