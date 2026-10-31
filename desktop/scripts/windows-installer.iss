#ifndef AppSource
  #error AppSource must point to the prepared portable directory.
#endif

[Setup]
AppId=com.csswitch.next.windows
AppName=CSSwitch Next
AppVersion=0.8.7
AppPublisher=Vonfre
AppPublisherURL=https://github.com/Vonfre/CSSwitch-Next
DefaultDirName={localappdata}\Programs\CSSwitch Next
DefaultGroupName=CSSwitch Next
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=CSSwitch-Next-0.8.7-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src-tauri\icons\icon.ico
UninstallDisplayIcon={app}\CSSwitch Next.exe
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Icons]
Name: "{group}\CSSwitch Next"; Filename: "{app}\CSSwitch Next.exe"
Name: "{userdesktop}\CSSwitch Next"; Filename: "{app}\CSSwitch Next.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\CSSwitch Next.exe"; Description: "Launch CSSwitch Next"; Flags: nowait postinstall skipifsilent
