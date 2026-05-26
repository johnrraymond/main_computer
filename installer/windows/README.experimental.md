# Experimental native Windows installer

This folder contains the first native Windows installer layer for Main Computer.

It is intentionally separate from the existing Python installer and runtime build
scripts. The builder stages a copy of the repository payload, compiles an Inno
Setup installer, and the installed wrapper delegates to the existing
`bootstrap-main-computer-python-windows.ps1` inside the packaged payload.

Build from the repository root on Windows with Inno Setup 6 installed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\build-main-computer-native-installer.experimental.ps1
```

Expected output:

```text
release_reports\installer-native-experimental\MainComputer-0.1.0-Setup.exe
```

This is an experimental native installer path. It does not replace the existing
Python installer scripts.
