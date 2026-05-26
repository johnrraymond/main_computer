; MainComputer.experimental.iss
;
; Experimental native Windows installer definition for Main Computer.
; It is intentionally additive and does not replace the existing Python installer
; scripts or the existing runtime-builder scripts.

#ifndef AppVersion
#define AppVersion "0.1.0"
#endif

#ifndef StageRoot
#error StageRoot must be passed by build-main-computer-native-installer.experimental.ps1
#endif

#ifndef OutputDir
#define OutputDir AddBackslash(SourcePath) + "..\..\release_reports\installer-native-experimental"
#endif

[Setup]
AppId={{7D6089B2-A0C9-42D8-AE21-6E8F3F86B8B7}
AppName=Main Computer
AppVersion={#AppVersion}
AppPublisher=Main Computer
DefaultDirName={localappdata}\Programs\Main Computer
DefaultGroupName=Main Computer
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=MainComputer-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayName=Main Computer
SetupLogging=yes

[Files]
Source: "{#StageRoot}\Install-MainComputer-from-Package.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageRoot}\installer-package.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageRoot}\payload\main_computer_test\*"; DestDir: "{app}\payload\main_computer_test"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Main Computer\Run Main Computer Installer"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Install-MainComputer-from-Package.ps1"""; WorkingDir: "{app}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Install-MainComputer-from-Package.ps1"""; WorkingDir: "{app}"; Description: "Run the Main Computer Python installer"; Flags: postinstall skipifsilent unchecked
