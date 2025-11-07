#ifndef MyAppVersion
  #define MyAppVersion GetEnv("APP_VERSION")
#endif
#if MyAppVersion == ""
  #define MyAppVersion "0.0.0"
#endif

; Allow CI to control the output filename. Defaults to release-style naming.
#ifndef MyAppFilename
  #define MyAppFilename GetEnv("APP_FILENAME")
#endif
#if MyAppFilename == ""
  #define MyAppFilename "clepsy-desktop-" + MyAppVersion + "-windows"
#endif

[Setup]
AppName=Clepsy
AppVersion={#MyAppVersion}
DefaultDirName={pf}\Clepsy
OutputBaseFilename={#MyAppFilename}
Compression=lzma
SolidCompression=yes
SetupIconFile=..\..\media\dist\logo.ico

;--------------------------------------- Tasks the user can pick
[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked
Name: "autorun";    Description: "Run Clepsy when Windows starts"

;--------------------------------------- Main payload
[Files]
Source: "..\..\dist\clepsy_desktop_source.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\media\dist\logo.ico"; DestDir: "{app}"

;--------------------------------------- Shortcuts
[Icons]
Name: "{group}\Clepsy";               Filename: "{app}\clepsy_desktop_source.exe"; IconFilename: "{app}\logo.ico"
Name: "{group}\Uninstall Clepsy";     Filename: "{uninstallexe}"
Name: "{commondesktop}\Clepsy";       Filename: "{app}\clepsy_desktop_source.exe"; Tasks: desktopicon; IconFilename: "{app}\logo.ico"

;--------------------------------------- Auto-start via registry
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "Clepsy"; \
  ValueData: """{app}\clepsy_desktop_source.exe"""; \
  Tasks: autorun; Flags: uninsdeletevalue

[Run]
Filename: "{app}\clepsy_desktop_source.exe"; Description: "Launch Clepsy"; Flags: nowait postinstall skipifsilent
