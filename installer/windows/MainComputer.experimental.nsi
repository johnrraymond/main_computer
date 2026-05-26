; MainComputer.experimental.nsi
;
; Experimental NSIS Windows installer definition for Main Computer.
; This is intentionally additive and does not replace the Python installer,
; Inno experiments, or existing runtime builders.

!ifndef MainComputerVersion
  !define MainComputerVersion "0.1.0"
!endif

!ifndef StageRoot
  !error "StageRoot must be passed by build-main-computer-nsis-installer.experimental.ps1"
!endif

!ifndef OutputRoot
  !error "OutputRoot must be passed by build-main-computer-nsis-installer.experimental.ps1"
!endif

Unicode true
Name "Main Computer"
OutFile "${OutputRoot}\MainComputer-${MainComputerVersion}-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\Main Computer"
RequestExecutionLevel user
ShowInstDetails show
ShowUninstDetails show
SetCompressor /SOLID lzma
XPStyle on

Section "Install Main Computer" SecInstall
  SetShellVarContext current
  SetOutPath "$INSTDIR"

  DetailPrint "Installing Main Computer package files to $INSTDIR"
  RMDir /r "$INSTDIR\payload\main_computer_test"
  Delete "$INSTDIR\Install-MainComputer-from-Package.nsis-experimental.ps1"
  Delete "$INSTDIR\installer-package.json"

  File "${StageRoot}\Install-MainComputer-from-Package.nsis-experimental.ps1"
  File "${StageRoot}\installer-package.json"

  SetOutPath "$INSTDIR\payload"
  File /r "${StageRoot}\payload\main_computer_test"

  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\Main Computer"
  CreateShortCut "$SMPROGRAMS\Main Computer\Run Main Computer Installer.lnk" "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File $\"$INSTDIR\Install-MainComputer-from-Package.nsis-experimental.ps1$\"" "$INSTDIR\Uninstall.exe" 0 SW_SHOWNORMAL "" "Run the Main Computer packaged installer"
SectionEnd

Section "Run Main Computer Python installer" SecBootstrap
  SetShellVarContext current
  SetOutPath "$INSTDIR"

  DetailPrint "Running the packaged Main Computer Python installer."
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\Install-MainComputer-from-Package.nsis-experimental.ps1"' $0
  StrCmp $0 "0" bootstrap_ok
    MessageBox MB_ICONSTOP "Main Computer Python installer failed with exit code $0."
    Abort "Main Computer Python installer failed with exit code $0."
  bootstrap_ok:
SectionEnd

Section "Uninstall"
  SetShellVarContext current

  Delete "$SMPROGRAMS\Main Computer\Run Main Computer Installer.lnk"
  RMDir "$SMPROGRAMS\Main Computer"

  Delete "$INSTDIR\Install-MainComputer-from-Package.nsis-experimental.ps1"
  Delete "$INSTDIR\installer-package.json"
  RMDir /r "$INSTDIR\payload"

  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
