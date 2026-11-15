; 历史开发版的 Windows 机器级离线安装包。
; 公开正式版使用 genshin_installer.spec 构建当前用户小安装器。
; 前置：先运行 scripts/build_dist.py；构建脚本会固定本次版本和候选目录
; 构建：Inno Setup 6 编译本脚本（iscc installer.iss 或 IDE 打开编译）

#define AppName "原神拉表"
#include "build-py\app-version.iss"
#include "build-py\app-layout.iss"
#define AppExe "GenshinCalc.exe"
#define GuiExe "GenshinCalcGUI.exe"
#define UpdaterInstallDir "{commonappdata}\GenshinCalc\Updater"
#define UninstallerInstallDir "{commonappdata}\GenshinCalc\Uninstall"

[Setup]
AppId={{7F3A2B1C-9E4D-4C8A-A5B6-1D2E3F4A5B6C}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\GenshinCalc
DefaultGroupName={#AppName}
OutputDir={#InstallerOutputDir}
; The App semantic version remains 0.45.0.  r9 is the packaging revision
; containing the UAC-safe database activation handoff.
OutputBaseFilename=GenshinCalc-Setup-{#AppVersion}-r9
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; PACKAGE-FILES.sha256 authenticates the complete App directory.  Inno's
; generated uninstaller is machine state rather than a reviewed App artifact,
; so keep it outside {app} just like the privileged updater.
UninstallFilesDir={#UninstallerInstallDir}

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Dirs]
; 更新器必须位于活动App目录之外，才能在GUI退出后安全替换{app}。
Name: "{#UpdaterInstallDir}"; Permissions: admins-full system-full users-readexec
Name: "{#UninstallerInstallDir}"; Permissions: admins-full system-full users-readexec

[InstallDelete]
; r7 and older installers used Inno's default {app} uninstaller location.
; Remove only those generated legacy artifacts so the reviewed App inventory
; remains exact after a repair/upgrade installation.
Type: files; Name: "{app}\unins*.exe"
Type: files; Name: "{app}\unins*.dat"
Type: files; Name: "{app}\unins*.msg"

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#UpdaterSourceFile}"; DestDir: "{#UpdaterInstallDir}"; DestName: "GenshinCalcUpdater.exe"; Flags: ignoreversion
Source: "{#UpdaterReceiptFile}"; DestDir: "{#UpdaterInstallDir}"; DestName: "updater-artifact.json"; Flags: ignoreversion

[Registry]
; 外置更新器提升权限后只接受这里记录的活动App目录，不能相信用户目录中的请求文件。
Root: HKLM; Subkey: "Software\GenshinCalc"; ValueType: string; ValueName: "InstallLocation"; ValueData: "{app}"; Flags: uninsdeletekey

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#GuiExe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#GuiExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#GuiExe}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent; Check: DatabaseActivationSucceeded

[Code]
var
  DatabaseActivationWasSuccessful: Boolean;

function DatabaseActivationSucceeded(): Boolean;
begin
  Result := DatabaseActivationWasSuccessful;
end;

function UserDatabaseDirectory(): String;
begin
  Result := ExpandConstant('{localappdata}\GenshinCalc\data');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExistingDatabase: String;
  Answer: Integer;
begin
  Result := '';
  DatabaseActivationWasSuccessful := False;
  ExistingDatabase := AddBackslash(UserDatabaseDirectory()) + 'genshin.db';
  if not FileExists(ExistingDatabase) then
    Exit;

  { Installing a new App/DB pair is the user-consent boundary.  Do not let a
    frozen App silently replace a database later during ordinary startup. }
  if WizardSilent then
  begin
    Result := '检测到旧数据库；静默安装不能代替用户确认数据库升级。请改用交互式安装。';
    Exit;
  end;

  Answer := MsgBox(
    '检测到已有数据库。' + #13#10 + #13#10 +
    '继续安装会将它升级为本版本 App 配套的数据库，' +
    '并在替换前进行完整性校验和可恢复备份。' + #13#10 +
    '白板、方案、Agent 会话、导出文件和设置不会被删除。' + #13#10 + #13#10 +
    '完整校验会读取大型数据库和 App 文件，在当前开发构建中可能需要十余分钟。' + #13#10 + #13#10 +
    '是否继续？',
    mbConfirmation,
    MB_YESNO
  );
  if Answer <> IDYES then
  begin
    Result := '用户未同意升级配套数据库，安装已停止。';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Parameters: String;
  Started: Boolean;
begin
  { PrepareToInstall aborts before file installation when an existing database
    was not approved.  Do not carry that decision in a global Boolean: Inno's
    UAC handoff can run the post-install phase in a different process, where
    process-local variables are reset.  Reaching ssPostInstall therefore is
    itself the durable proof that the consent gate passed (or no old database
    existed), and activation must never be skipped here. }
  if CurStep <> ssPostInstall then
    Exit;

  WizardForm.StatusLabel.Caption :=
    '正在验证并安装配套数据库（可能需要十余分钟，请勿关闭）…';
  Parameters :=
    '--data-dir "' + UserDatabaseDirectory() +
    '" db activate-bundled --consent-source installer';
  ResultCode := -1;
  Started := ExecAsOriginalUser(
    ExpandConstant('{app}\{#AppExe}'),
    Parameters,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  if not Started then
    Started := Exec(
      ExpandConstant('{app}\{#AppExe}'),
      Parameters,
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    );

  DatabaseActivationWasSuccessful := Started and (ResultCode = 0);
  if not DatabaseActivationWasSuccessful then
  begin
    Log('Bundled database activation failed (exit=' +
      IntToStr(ResultCode) + ')');
    if not WizardSilent then
      MsgBox(
        '应用文件已安装，但配套数据库未能通过校验或激活。' + #13#10 +
        '为避免继续使用不兼容的旧数据库，安装程序不会启动 App。' + #13#10 +
        '请保留安装日志并运行修复安装。',
        mbError,
        MB_OK
      );
  end;
end;
