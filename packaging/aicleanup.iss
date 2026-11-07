; Inno Setup script for the AICleanUp Windows installer.
;
; Build it after PyInstaller has produced dist\AICleanUp.exe:
;   ISCC.exe /DAppVersion=1.0.0 packaging\aicleanup.iss
; The result is dist\AICleanUp-Setup-<version>.exe.
;
; The installer is per-user by default (no admin prompt, no service, no
; background component). Uninstalling removes the program; scanned folders,
; Trash contents, and ~\.aicleanup\audit manifests are left untouched.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName "AICleanUp"
#define AppPublisher "AICleanUp"
#define AppExeName "AICleanUp.exe"
#define AppUrl "https://github.com/SeanShaffer88/AICleanUp"

[Setup]
; A stable AppId keeps upgrades and uninstall entries on the same identity.
AppId={{2F6B0F7C-4D1E-4C39-9B2A-6EE3A0D5C417}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
AppUpdatesURL={#AppUrl}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=AICleanUp-Setup-{#AppVersion}
SetupIconFile=aicleanup.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user install: nothing needs administrator rights and nothing is left in
; Program Files behind an admin-only uninstaller.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "--gui"; Comment: "Open the AICleanUp workspace"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "--gui"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "--gui"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
