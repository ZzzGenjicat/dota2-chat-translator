#ifndef AppVersion
  #error AppVersion must be supplied by the native builder
#endif
#ifndef AppSource
  #error AppSource must point to the verified frozen app
#endif
#ifndef ReleaseDirectory
  #error ReleaseDirectory must point to the release output directory
#endif

[Setup]
AppId={code:ApplicationId}
AppName=Dota 2 本地聊天翻译
AppVersion={#AppVersion}
AppPublisher=Dota2 Chat Translator
AppPublisherURL=https://github.com/ZzzGenjicat/dota2-chat-translator
DefaultDirName={localappdata}\Programs\Dota2ChatTranslator
DefaultGroupName=Dota 2 本地聊天翻译
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=yes
ShowLanguageDialog=no
UsePreviousLanguage=no
OutputDir={#ReleaseDirectory}
OutputBaseFilename=Dota2ChatTranslator-{#AppVersion}-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Dota2ChatTranslator.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl,ChineseSimplified.isl"

[Messages]
ReadyLabel1=点击“安装”即可安装 Dota 2 本地聊天翻译。
ReadyLabel2a=本地模型和运行环境已经包含，安装与翻译无需下载文件。安装完成后，按程序提示安装一次游戏直读配置并重启 Dota 2。

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{code:StartMenuDirectory}\Dota 2 本地聊天翻译"; Filename: "{app}\Dota2ChatTranslator.exe"; WorkingDir: "{app}"
Name: "{code:DesktopDirectory}\Dota 2 本地聊天翻译"; Filename: "{app}\Dota2ChatTranslator.exe"; WorkingDir: "{app}"
Name: "{code:InstructionsDirectory}\首次使用说明"; Filename: "{app}\_internal\WINDOWS_FIRST_USE.txt"

[Run]
Filename: "{app}\Dota2ChatTranslator.exe"; Description: "打开 Dota 2 本地聊天翻译"; Flags: nowait postinstall skipifsilent

[Code]
function IsInstallerTest: Boolean;
begin
  Result := ExpandConstant('{param:INSTALLERTEST|0}') = '1';
end;

function ApplicationId(Param: String): String;
begin
  Result := '{9D69E4A5-B037-475A-B271-D20229C6DFA8}';
  if IsInstallerTest then
    Result := Result + '.InstallerTest';
end;

function TestShortcutDirectory: String;
begin
  Result := ExpandConstant('{param:TESTSHORTCUTDIR|}');
  if Result = '' then
    RaiseException('Installer tests require an isolated shortcut directory');
end;

function StartMenuDirectory(Param: String): String;
begin
  if IsInstallerTest then
    Result := TestShortcutDirectory + '\start-menu'
  else
    Result := ExpandConstant('{userprograms}\Dota 2 本地聊天翻译');
end;

function DesktopDirectory(Param: String): String;
begin
  if IsInstallerTest then
    Result := TestShortcutDirectory + '\desktop'
  else
    Result := ExpandConstant('{userdesktop}');
end;

function InstructionsDirectory(Param: String): String;
begin
  if IsInstallerTest then
    Result := TestShortcutDirectory + '\instructions'
  else
    Result := ExpandConstant('{userprograms}\Dota 2 本地聊天翻译');
end;
