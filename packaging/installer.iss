#define MyAppName "RenpyThief 非官方翻译补丁"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "KangZENG50025543"
#define MyAppURL "https://github.com/KangZENG50025543/RenpyThief-Unofficial-Patch"
#define MyAppExeName "RenpyThiefPatch.exe"

#if Ver != EncodeVer(6, 7, 3)
  #error Inno Setup Compiler 6.7.3 is required to build this installer.
#endif

#ifndef SourceDirectory
  #error SourceDirectory must point to the prepared portable directory.
#endif

#ifndef OutputDirectory
  #error OutputDirectory must point to the release output directory.
#endif

[Setup]
AppId={{A3ABA200-1132-4440-9DE8-6C4326A26628}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\RenpyThiefUnofficialPatch
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDirectory}
OutputBaseFilename=RenpyThiefPatch-v1.0.1-setup-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile={#SourceDirectory}\LICENSE
InfoBeforeFile={#SourceDirectory}\QUICK_START.txt
UninstallDisplayIcon={app}\{#MyAppExeName}
AppMutex=RenpyThiefUnofficialPatch.Gui.v1
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion=1.0.1.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoOriginalFileName=RenpyThiefPatch-v1.0.1-setup-x64.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："

[Files]
Source: "{#SourceDirectory}\*"; DestDir: "{app}"; Excludes: "router\runtime\*,router\bridge_requests.log"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\使用说明"; Filename: "{app}\QUICK_START.txt"; WorkingDir: "{app}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\router\runtime"
Type: files; Name: "{app}\router\bridge_requests.log"
