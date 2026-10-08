; PhotoVault - instalador (Inno Setup 6, fase 11)
;
; Empaqueta la carpeta que genera PyInstaller (dist\PhotoVault\). Uso:
;   ISCC.exe /DMyAppVersion=3.1.0 installer\PhotoVault.iss
; (build.bat lo hace solo si encuentra Inno Setup). Sale en dist\.
;
; - Se instala por usuario (%LOCALAPPDATA%\Programs\PhotoVault), sin pedir
;   permisos de administrador; el usuario puede elegir instalar para todos.
; - Actualizar = instalar la versión nueva encima (mismo AppId).
; - Desinstalar NO borra los datos del usuario (%USERPROFILE%\.photovault:
;   base de datos, miniaturas, backups, modelos de IA).

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "PhotoVault"
#define MyAppExe "PhotoVault.exe"

[Setup]
AppId={{63E4F3D2-6902-4EFE-A1E3-4D0DCB218F82}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Z4MU
AppPublisherURL=https://github.com/Z4MU/Photo-vault
AppSupportURL=https://github.com/Z4MU/Photo-vault/issues
AppUpdatesURL=https://github.com/Z4MU/Photo-vault/releases
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=PhotoVault-{#MyAppVersion}-instalador
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Si PhotoVault está abierto, se pide cerrarlo antes de actualizar
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Al actualizar: quitar los archivos de la versión anterior (DLL que ya no se usan)
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\PhotoVault\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Messages]
spanish.FinishedLabel=Se instaló PhotoVault.%n%nTus fotos, etiquetas y configuración se guardan en la carpeta .photovault de tu usuario; desinstalar el programa no las borra.
