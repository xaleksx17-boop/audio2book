; Inno Setup 6: iscc /DMyAppVersion=1.0.0 packaging\installer.iss
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "Аудиокнига → Электронная книга"
#define MyAppExe "Audio2Book.exe"

[Setup]
AppId={{6E2B6C1A-5D0F-4C9E-A2B4-7A2B0B0C1D45}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Кузнецов А.А.
DefaultDirName={autopf}\Audio2Book
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExe}
OutputDir=..\dist
OutputBaseFilename=Audio2Book-Setup-{#MyAppVersion}
SetupIconFile=icon.ico
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Дополнительно:"
Name: "sendto"; Description: "Пункт «Отправить → Аудиокнига в текст» в контекстном меню"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\dist\Audio2Book\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon
Name: "{usersendto}\Аудиокнига в текст"; Filename: "{app}\{#MyAppExe}"; Tasks: sendto

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Запустить программу"; Flags: nowait postinstall skipifsilent
