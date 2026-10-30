; PDF Page Editor の Windows インストーラー（Inno Setup 6）
; ビルド: ISCC.exe /DAppVersion=1.1.0 installer\PdfPageEditor.iss
; 事前に dist\PdfPageEditor.exe（PyInstaller 出力）が必要。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "PDF Page Editor"
#define AppExe "PdfPageEditor.exe"

[Setup]
AppId={{7C2B5C1E-5B4E-4D5A-9C3F-2E1A7F0D8B21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Yorozu Solutions
AppPublisherURL=https://www.yorozusolutions.com/
AppCopyright=Copyright (c) 2026 Yorozu Solutions
AppSupportURL=https://github.com/hidetako/pdf-page-editor/issues
AppUpdatesURL=https://github.com/hidetako/pdf-page-editor/releases
; 管理者権限なしでユーザー領域にインストール（UAC が出ない。全ユーザー向けは選択可）
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\PdfPageEditor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=PdfPageEditor-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} をアンインストール"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; エクスプローラーの「プログラムから開く」に登録する（既定の PDF アプリは変更しない）
Root: HKA; Subkey: "Software\Classes\PdfPageEditor.pdf"; ValueType: string; ValueName: ""; ValueData: "PDF ({#AppName})"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\PdfPageEditor.pdf\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKA; Subkey: "Software\Classes\PdfPageEditor.pdf\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "PdfPageEditor.pdf"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
