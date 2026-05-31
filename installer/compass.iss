#pragma codepage 65001

#define MyAppName "Компас"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Компас"
#define MyAppExeName "Compass.exe"
#define MyOutputBaseFilename "KompasSetup"
#if GetEnv("COMPASS_BUILD_SOURCE") != ""
#define MyBuildSource GetEnv("COMPASS_BUILD_SOURCE")
#else
#define MyBuildSource "..\dist\Compass"
#endif

[Setup]
AppId={{4A8DF258-13E5-4D5A-8CB9-6C092AA6E7F2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename={#MyOutputBaseFilename}
SetupIconFile=..\src\Icon_Compass.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Запускать Компас при входе в Windows"; GroupDescription: "Автозапуск:"; Flags: unchecked

[Files]
Source: "{#MyBuildSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "prepare_components.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Компас"; ValueData: """{app}\{#MyAppExeName}"" --start-minimized"; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "Компас"; Flags: deletevalue; Tasks: not startup

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\prepare_components.ps1"" -InstallDir ""{app}"" {code:GetPrepareParams}"; StatusMsg: "Загрузка Ollama, Whisper, Silero и выбранной модели..."; Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить Компас"; Flags: nowait postinstall skipifsilent

[Code]
var
  ModelPage: TWizardPage;
  Model2B: TNewRadioButton;
  Model4B: TNewRadioButton;
  Model9B: TNewRadioButton;
  ModelSkip: TNewRadioButton;

procedure AddModelOption(var Button: TNewRadioButton; Top: Integer; Caption: String);
begin
  Button := TNewRadioButton.Create(ModelPage);
  Button.Parent := ModelPage.Surface;
  Button.Left := ScaleX(0);
  Button.Top := ScaleY(Top);
  Button.Width := ModelPage.SurfaceWidth;
  Button.Height := ScaleY(32);
  Button.Caption := Caption;
end;

procedure InitializeWizard;
var
  InfoLabel: TNewStaticText;
begin
  ModelPage := CreateCustomPage(
    wpSelectTasks,
    'Выбор LLM-модели',
    'Выберите локальную модель Ollama или установку без локальной LLM.'
  );

  AddModelOption(Model2B, 0, 'qwen3.5:2b — 2.7GB, быстрее и легче');
  AddModelOption(Model4B, 38, 'qwen3.5:4b — 3.4GB, рекомендуемый баланс');
  AddModelOption(Model9B, 76, 'qwen3.5:9b — 6.6GB, лучше качество, дольше установка');
  AddModelOption(ModelSkip, 114, 'Без локальной модели / API key — не скачивать Ollama LLM');
  Model4B.Checked := True;

  InfoLabel := TNewStaticText.Create(ModelPage);
  InfoLabel.Parent := ModelPage.Surface;
  InfoLabel.Left := ScaleX(0);
  InfoLabel.Top := ScaleY(160);
  InfoLabel.Width := ModelPage.SurfaceWidth;
  InfoLabel.Height := ScaleY(80);
  InfoLabel.WordWrap := True;
  InfoLabel.Caption :=
    'Ollama, Whisper и Silero устанавливаются в любом варианте. ' +
    'Если выбрать API key, установка пройдет быстрее: модель можно будет подключить позже через OpenAI, DeepSeek, OpenRouter или другой провайдер в интерфейсе.';
end;

function GetPrepareParams(Param: String): String;
begin
  if ModelSkip.Checked then
    Result := '-SkipOllamaModel'
  else if Model2B.Checked then
    Result := '-OllamaModel qwen3.5:2b'
  else if Model9B.Checked then
    Result := '-OllamaModel qwen3.5:9b'
  else
    Result := '-OllamaModel qwen3.5:4b';
end;
