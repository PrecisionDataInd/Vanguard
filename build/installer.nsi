; Vanguard per-user installer (no admin rights needed).
; Used when building on Linux; GitHub Actions builds the standard one on Windows.
Unicode true
!include "MUI2.nsh"
!define APP "Vanguard"
!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Vanguard"

Name "${APP} ${VERSION}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\${APP}"
InstallDirRegKey HKCU "Software\${APP}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
ManifestDPIAware true

!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\Vanguard.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Start Vanguard"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${SRC}/*.*"
  WriteUninstaller "$INSTDIR\Uninstall Vanguard.exe"
  CreateShortcut "$DESKTOP\Vanguard.lnk" "$INSTDIR\Vanguard.exe" "" "$INSTDIR\resources\icon.ico"
  CreateDirectory "$SMPROGRAMS\Vanguard"
  CreateShortcut "$SMPROGRAMS\Vanguard\Vanguard.lnk" "$INSTDIR\Vanguard.exe" "" "$INSTDIR\resources\icon.ico"
  CreateShortcut "$SMPROGRAMS\Vanguard\Uninstall Vanguard.lnk" "$INSTDIR\Uninstall Vanguard.exe"
  WriteRegStr HKCU "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNKEY}" "DisplayName" "Vanguard"
  WriteRegStr HKCU "${UNKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNKEY}" "Publisher" "Precision Data Industries"
  WriteRegStr HKCU "${UNKEY}" "DisplayIcon" "$INSTDIR\resources\icon.ico"
  WriteRegStr HKCU "${UNKEY}" "UninstallString" '"$INSTDIR\Uninstall Vanguard.exe"'
  WriteRegDWORD HKCU "${UNKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNKEY}" "NoRepair" 1
SectionEnd

; Removes the program only. Your saved data and keys stay in
; %APPDATA%\Vanguard so a reinstall picks up where you left off.
Section "Uninstall"
  Delete "$DESKTOP\Vanguard.lnk"
  RMDir /r "$SMPROGRAMS\Vanguard"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNKEY}"
  DeleteRegKey HKCU "Software\${APP}"
SectionEnd
