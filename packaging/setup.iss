#ifndef AppVersion
  #error AppVersion must come from the Python release builder
#endif

[Setup]
AppId={{8F44790E-9DDC-4B95-BAA5-85BD2D35C5FA}
AppName=Wuwa Tappo Plus
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
AppPublisher=Wuwa Tappo Plus contributors
DefaultDirName={localappdata}\Programs\WuwaTappoPlus
UsePreviousAppDir=no
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=WuwaTappoPlus-Setup
SetupIconFile=WuwaTappoPlus.ico
UninstallDisplayIcon={app}\WuwaTappoPlus.exe
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupMutex=WuwaTappoPlusSetup
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=Wuwa Tappo Plus

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\build\payload\WuwaTappoPlus\*"; DestDir: "{tmp}\helper"; Flags: dontcopy recursesubdirs createallsubdirs
Source: "..\build\payload\WuwaTappoPlus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "WuwaTappoPlus"; ValueData: """{app}\WuwaTappoPlus.exe"" run --daemon"; Flags: uninsdeletevalue

[Icons]
Name: "{userprograms}\Wuwa Tappo Plus"; Filename: "{app}\WuwaTappoPlus.exe"

[UninstallDelete]
Type: files; Name: "{app}\install.json"

[Code]
var
  Prepared: Boolean;
  HaveAppState: Boolean;
  AppWasRunning: Boolean;
  FilesInstalled: Boolean;
  Installed: Boolean;
  PurgeData: Boolean;

function Quote(const Value: String): String;
begin
  Result := '"' + Value + '"';
end;

function TemporaryHelper: String;
begin
  Result := ExpandConstant('{tmp}\helper\WuwaTappoPlus.exe');
end;

procedure RunHelper(const Executable, Action: String; Elevated: Boolean);
var
  Params, LogFile: String;
  Details: AnsiString;
  ExitCode: Integer;
  Started: Boolean;
begin
  LogFile := ExpandConstant('{tmp}\plus-' + Action + '.txt');
  Params := '--data-dir ' + Quote(ExpandConstant('{localappdata}\WuwaTappoPlus')) +
    ' --program-dir ' + Quote(ExpandConstant('{app}')) +
    ' --output ' + Quote(LogFile) + ' --installer ' + Action;
  if Action = 'configure' then
    Params := Params + ' --user ' + Quote(GetEnv('USERDOMAIN') + '\' + GetUserNameString);
  if Action = 'start' then
    Params := Params + ' --setup-source ' + Quote(ExpandConstant('{srcexe}'));
  if AppWasRunning and ((Action = 'start') or (Action = 'resume')) then
    Params := Params + ' --restart-app';
  DeleteFile(LogFile);
  if Elevated then
    Started := ShellExec('runas', Executable, Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode)
  else
    Started := Exec(Executable, Params, '', SW_HIDE, ewWaitUntilTerminated, ExitCode);
  Details := '';
  LoadStringFromFile(LogFile, Details);
  if Started and (Action = 'prepare') and (not HaveAppState) then
  begin
    if Pos('installer: app_was_running=1', Details) > 0 then
    begin
      AppWasRunning := True;
      HaveAppState := True;
    end
    else if Pos('installer: app_was_running=0', Details) > 0 then
    begin
      AppWasRunning := False;
      HaveAppState := True;
    end;
  end;
  if (not Started) or (ExitCode <> 0) then
    RaiseException('Plus 操作未完成：' + Action + #13#10 + UTF8Decode(Details) + #13#10 +
      '个人数据会保留。请重新运行安装包修复。错误码：' + IntToStr(ExitCode));
  if (Action = 'prepare') and (not HaveAppState) then
  begin
    RaiseException('无法确认桌宠原先的运行状态，已中止安装，请重试。');
  end;
end;

function InitializeSetup: Boolean;
var
  Report: String;
  ExitCode: Integer;
begin
  Result := True;
  // Packaging smoke only: extract and probe, without touching a real install.
  Report := ExpandConstant('{param:VALIDATEONLY|}');
  if Report <> '' then
  begin
    ExtractTemporaryFiles('{tmp}\helper\*');
    Exec(TemporaryHelper, '--output ' + Quote(Report) + ' --probe', '', SW_HIDE,
      ewWaitUntilTerminated, ExitCode);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  try
    ExtractTemporaryFiles('{tmp}\helper\*');
    Prepared := True;
    RunHelper(TemporaryHelper, 'prepare', False);
  except
    Result := GetExceptionMessage;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    FilesInstalled := True;
    RunHelper(ExpandConstant('{app}\WuwaTappoPlus.exe'), 'configure', True);
    RunHelper(ExpandConstant('{app}\WuwaTappoPlus.exe'), 'start', False);
    Installed := True;
  end;
end;

procedure DeinitializeSetup;
begin
  if Prepared and HaveAppState and (not Installed) and (not FilesInstalled) then
  begin
    try
      RunHelper(TemporaryHelper, 'resume', False);
    except
      Log('无法自动恢复后台，请重新运行安装包修复。');
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and Installed and (not AppWasRunning) then
    WizardForm.FinishedLabel.Caption := 'Plus 安装完成。桌宠原本未运行，已保持关闭。' + #13#10 +
      '请手动打开 Wuwa Tappo，Plus 后台会自动连接。';
end;

function InitializeUninstall: Boolean;
var
  Choice: Integer;
begin
  Result := True;
  PurgeData := ExpandConstant('{param:PURGEDATA|no}') <> 'no';
  if not UninstallSilent then
  begin
    if PurgeData then
      Choice := MsgBox('确认永久清除 Plus 配置、统计、房间历史、配件调整和日志？',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2)
    else
      Choice := MsgBox('默认保留个人数据。是否同时永久清除 Plus 配置、统计、房间历史、配件调整和日志？' + #13#10 +
        '选“否”保留数据；选“取消”退出卸载。',
        mbConfirmation, MB_YESNOCANCEL or MB_DEFBUTTON2);
    if Choice = IDCANCEL then
      Result := False
    else if PurgeData and (Choice <> IDYES) then
      Result := False
    else
      PurgeData := Choice = IDYES;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Executable: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Executable := ExpandConstant('{app}\WuwaTappoPlus.exe');
    RunHelper(Executable, 'stop', False);
    RunHelper(Executable, 'remove-system', True);
    RunHelper(Executable, 'detach', False);
    if PurgeData then
      RunHelper(Executable, 'purge', False);
  end;
end;
