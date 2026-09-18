# Voicitool: Einrichtungs-Assistent (Installieren, Prüfen und Reparieren, Deinstallieren)
#   ohne Schalter  Installation in diesem Ordner prüfen und reparieren
#   -Auto          nach einem Update: Prüfung läuft ohne Klick durch, danach startet Voicitool
#   -Install       Neuinstallation: Speicherort, Sprachpakete und Verknüpfungen wählen (von Voicitool.exe gestartet)
#   -Uninstall     Deinstallation der Installation in -Target (von Voicitool.exe aus einer Kopie gestartet)
#   -Quiet         mit -Auto: ohne Fenster, Fortschritt fürs Startfenster; nur bei Fehlern oder Hinweisen sichtbar
param([switch]$Auto, [switch]$Install, [switch]$Uninstall, [switch]$Quiet, [string]$Target = '', [string]$Exe = '')
#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Windows.Forms
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Mode = if ($Uninstall) { 'uninstall' } elseif ($Install) { 'install' } elseif ($Auto) { 'auto' } else { 'check' }
$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$DefaultTarget = Join-Path $env:LOCALAPPDATA 'Programs\Voicitool'
$Root = if ($Mode -in @('install', 'uninstall')) { if ($Target) { $Target } else { $DefaultTarget } } else { $SourceRoot }
$LogDir = if ($Mode -eq 'check' -or $Mode -eq 'auto') { Join-Path $Root 'daten\logs' } else { Join-Path $env:TEMP 'Voicitool-Logs' }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$RegKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Voicitool'
# Testschalter: VOICITOOL_TEST=<Ordner> legt Verknüpfungen dort ab und nutzt einen eigenen Windows-Eintrag
$DesktopDir = [Environment]::GetFolderPath('Desktop'); $MenuDir = [Environment]::GetFolderPath('Programs')
if ($env:VOICITOOL_TEST) { $RegKey += '-Test'; $DesktopDir = $env:VOICITOOL_TEST; $MenuDir = $env:VOICITOOL_TEST }

# ------------------------------------------------------------------ Sprache
# Voreinstellung: Sprache aus den Voicitool-Einstellungen (Prüfen, Deinstallieren), sonst die von Windows, sonst Englisch.
# Oben rechts umschaltbar. Deutsch und Englisch stehen hier, die übrigen in sprachen.json (fehlende Texte: Englisch).
$Langs = [ordered]@{ en = 'English'; de = 'Deutsch'; es = 'Español'; fr = 'Français'; pt = 'Português'; it = 'Italiano'
    ru = 'Русский'; pl = 'Polski'; tr = 'Türkçe'; nl = 'Nederlands'; uk = 'Українська'; id = 'Bahasa Indonesia'
    ja = '日本語'; zh = '中文（简体）'; ko = '한국어'; hi = 'हिन्दी'; cs = 'Čeština'; sk = 'Slovenčina'; sr = 'Srpski'
    sv = 'Svenska'; da = 'Dansk'; ro = 'Română'; hu = 'Magyar'; el = 'Ελληνικά'; vi = 'Tiếng Việt'; th = 'ไทย' }
function Read-UiLang($dir) {
    try { return [string](Get-Content -LiteralPath (Join-Path $dir 'daten\einstellungen.json') -Raw -Encoding UTF8 | ConvertFrom-Json).ui_lang }
    catch { return '' }
}
$Lang = @($env:VOICITOOL_LANG, (Read-UiLang $Root), (Get-UICulture).TwoLetterISOLanguageName, 'en') |
    Where-Object { $_ -and $Langs.Contains($_) } | Select-Object -First 1
$LangChosen = $false   # im Installer umgestellt: dann auch für Voicitool übernehmen
$Extra = $null
foreach ($f in @((Join-Path $PSScriptRoot 'sprachen.json'), (Join-Path $Root 'app\setup\sprachen.json'))) {
    # Deinstallation läuft aus einer Kopie ohne sprachen.json: dann die der Installation lesen
    if (-not $Extra -and (Test-Path -LiteralPath $f)) { try { $Extra = (Get-Content -LiteralPath $f -Raw -Encoding UTF8 | ConvertFrom-Json).installer } catch {} }
}
$Text = @{
  de = @{
    title_setup = 'Einrichtung'; title_install = 'Installation'; title_uninstall = 'Deinstallation'
    pc_check = 'PC-Check'; options = 'Optionen'; location = 'Speicherort'; change = 'Ändern …'
    packs = 'Sprachpakete für die Texterkennung'; pack_en = 'Englisch (immer dabei, 1,5 GB)'
    pack_turbo = 'Alle Sprachen, schnell (+1,6 GB)'; pack_large = 'Alle Sprachen, beste Qualität (+3,1 GB)'
    packs_later = 'Weitere Pakete kannst du später in Voicitool unter Einstellungen laden.'
    desktop = 'Verknüpfung auf dem Desktop'; startmenu = 'Eintrag im Startmenü'; launch = 'Voicitool danach starten'
    what = 'Was installiert wird'
    what_text = 'Python 3.11, KI-Pakete (PyTorch u. a.) und die KI-Modelle für Spracherkennung, Stimmen-Trennung, Sprecher- und Lach-Erkennung ({0}). Alles landet nur im gewählten Ordner. Dauer je nach Internet 10 bis 40 Minuten.'
    free = '{0} GB frei auf {1}'
    btn_checking = 'PC wird geprüft …'; btn_install = 'Installieren'; btn_repair = 'Prüfen und reparieren'
    btn_installing = 'Installiert …'; btn_retry = 'Erneut versuchen'; btn_start = 'Voicitool starten'
    btn_blocked = 'PC erfüllt die Voraussetzungen nicht'; btn_cancel = 'Abbrechen'; btn_close = 'Schließen'; btn_log = 'Log öffnen'
    btn_uninstall = 'Deinstallieren'; btn_uninstalling = 'Wird entfernt …'
    installing = 'Installation'; total = 'Gesamt'; since = 'seit {0}'
    ready = 'Voicitool ist bereit'
    ready_text = 'Alles installiert. Voicitool läuft {0}.'; ready_desktop = 'Du findest es ab jetzt auf dem Desktop.'
    gpu_text = @{ cu128 = 'mit Grafikkarte (CUDA 12.8)'; cu126 = 'mit Grafikkarte (CUDA 12.6)'; cpu = 'auf dem Prozessor' }
    hint = 'Hinweis: {0}'
    failed = 'Installation fehlgeschlagen'; cancelled = 'Installation abgebrochen'
    failed_more = 'Mit »Erneut versuchen« geht es dort weiter, wo es aufgehört hat.'
    impossible = 'Einrichtung nicht möglich'; confirm_cancel = 'Installation wirklich abbrechen? Sie kann später fortgesetzt werden.'
    bad_folder = 'In diesen Ordner kann nicht geschrieben werden. Bitte einen anderen wählen.'
    un_title = 'Voicitool deinstallieren'
    un_text = 'Voicitool wird von diesem PC entfernt, samt Python-Umgebung, Verknüpfungen und dem Eintrag in den Windows-Einstellungen.'
    un_keep_projects = 'Projekte, Exporte und Eingang behalten'
    un_keep_models = 'KI-Modelle behalten (spart den Download bei einer Neuinstallation)'
    un_done = 'Voicitool wurde entfernt'; un_done_text = 'Danke fürs Ausprobieren!'; un_kept = 'Behalten in: {0}'
    un_failed = 'Deinstallation nicht vollständig'
    un_step_stop = 'Voicitool beenden'; un_step_files = 'Dateien entfernen'; un_step_links = 'Verknüpfungen entfernen'; un_step_reg = 'Windows-Eintrag entfernen'
    step = @{ files = 'Dateien kopieren'; uv = 'Installer vorbereiten'; deno = 'YouTube-Unterstützung'; webview = 'Fenster-Komponente'
              python = 'Python 3.11'; venv = 'Python-Umgebung'; packages = 'KI-Pakete, {0}'; models = 'KI-Modelle'
              check = 'Installation prüfen'; shortcut = 'Verknüpfungen'; register = 'In Windows eintragen' }
    variant = @{ cu128 = 'Grafikkarte (CUDA 12.8)'; cu126 = 'Grafikkarte (CUDA 12.6)'; cpu = 'nur Prozessor' }
    already = 'bereits vorhanden'; done = 'fertig'; not_wanted = 'nicht gewünscht'
    chk = @{ os = 'Windows'; gpu = 'Grafikkarte'; driver = 'NVIDIA-Treiber'; cpu = 'Prozessor'; ram = 'Arbeitsspeicher'
             disk = 'Speicherplatz'; folder = 'Speicherort'; webview = 'Fenster-Komponente (WebView2)'; net = 'Internet' }
    os_ok = '{0} (64-Bit, Build {1})'; os_fail = '{0}: benötigt wird Windows 10 (1809 oder neuer) oder 11, 64-Bit'
    gpu_low = ' (wenig Grafikspeicher, Qualität »Schnell« empfohlen)'
    drv_ok = 'Version {0}, CUDA {1}'; drv_old = 'Version {0} ist zu alt. Bitte den Treiber aktualisieren (nvidia.com/drivers), sonst läuft alles langsam auf dem Prozessor.'
    gpu_other = '{0}: wird nicht beschleunigt (nur NVIDIA). Voicitool läuft auf dem Prozessor, klappt, dauert aber deutlich länger.'
    gpu_old = '{0}: zu alt für die KI-Beschleunigung. Voicitool läuft auf dem Prozessor, klappt, dauert aber länger.'
    cpu_ok = '{0}, {1} Threads. Qualität »Schnell« empfohlen.'; cpu_few = '{0}, {1} Threads. Wenige Kerne: lange Videos dauern sehr lange, Qualität »Schnell« empfohlen.'
    ram_warn = '{0} GB: lange Videos können knapp werden (16 GB empfohlen)'; ram_fail = '{0} GB: mindestens 8 GB nötig'
    disk_ok = '{0} GB frei auf {1} (benötigt ca. {2} GB)'; disk_warn = '{0} GB frei: knapp (benötigt ca. {1} GB, später kommen Projekte dazu)'; disk_fail = '{0} GB frei: benötigt ca. {1} GB'
    folder_onedrive = 'Ordner liegt in OneDrive. Besser z. B. C:\Voicitool wählen (große Dateien).'
    folder_special = 'Pfad enthält Sonderzeichen. Falls Probleme auftreten, z. B. C:\Voicitool wählen.'
    wv_ok = 'vorhanden'; wv_missing = 'fehlt, wird automatisch installiert'
    net_ok = 'Verbindung zu pypi.org, huggingface.co und github.com klappt'
    net_offline = 'keine Verbindung: nur Prüfung der vorhandenen Installation möglich'
    net_fail = 'keine Verbindung zu pypi.org, huggingface.co oder github.com, für die Einrichtung nötig'
    d_uv = 'uv (Paket-Installer) wird geladen …'; d_deno = 'deno (für YouTube) wird geladen …'; d_wv = 'WebView2 wird geladen …'; d_wv2 = 'WebView2 wird installiert …'
    d_py = 'Python 3.11 wird bereitgestellt …'; d_py_have = 'Python {0} bereits vorhanden'; d_venv = 'Python-Umgebung wird angelegt …'
    d_pk = 'Pakete werden aufgelöst …'; d_pk_load = 'Lade {0} ({1}) …'; d_models = 'KI-Modelle werden geladen …'; d_check = 'Installation wird geprüft …'
    d_files = 'Programmdateien werden kopiert …'; step_failed = 'Schritt »{0}« fehlgeschlagen (Code {1}):'
    shortcut_desc = 'Voicitool: Dub Packs für Choicer Voicer'; shortcut_check = 'Voicitool prüfen'
    script_error = 'Fehler im Skript ({0}), Zeile {1}: {2}'; aborted = 'Abgebrochen'; lang = 'Sprache'
  }
  en = @{
    title_setup = 'Setup'; title_install = 'Installation'; title_uninstall = 'Uninstall'
    pc_check = 'PC check'; options = 'Options'; location = 'Install location'; change = 'Change …'
    packs = 'Language packs for speech recognition'; pack_en = 'English (always included, 1.5 GB)'
    pack_turbo = 'All languages, fast (+1.6 GB)'; pack_large = 'All languages, best quality (+3.1 GB)'
    packs_later = 'You can add more packs later in Voicitool under Settings.'
    desktop = 'Desktop shortcut'; startmenu = 'Start menu entry'; launch = 'Start Voicitool afterwards'
    what = 'What gets installed'
    what_text = 'Python 3.11, AI packages (PyTorch and more) and the AI models for speech recognition, voice separation, speaker and laughter detection ({0}). Everything stays in the chosen folder. Takes 10 to 40 minutes depending on your internet.'
    free = '{0} GB free on {1}'
    btn_checking = 'Checking your PC …'; btn_install = 'Install'; btn_repair = 'Check and repair'
    btn_installing = 'Installing …'; btn_retry = 'Try again'; btn_start = 'Start Voicitool'
    btn_blocked = 'This PC does not meet the requirements'; btn_cancel = 'Cancel'; btn_close = 'Close'; btn_log = 'Open log'
    btn_uninstall = 'Uninstall'; btn_uninstalling = 'Removing …'
    installing = 'Installation'; total = 'Total'; since = 'for {0}'
    ready = 'Voicitool is ready'
    ready_text = 'Everything is installed. Voicitool runs {0}.'; ready_desktop = 'You will also find it on your desktop.'
    gpu_text = @{ cu128 = 'on your graphics card (CUDA 12.8)'; cu126 = 'on your graphics card (CUDA 12.6)'; cpu = 'on the processor' }
    hint = 'Note: {0}'
    failed = 'Installation failed'; cancelled = 'Installation cancelled'
    failed_more = '»Try again« continues where it stopped.'
    impossible = 'Setup not possible'; confirm_cancel = 'Really cancel the installation? You can continue it later.'
    bad_folder = 'Voicitool cannot write to this folder. Please pick another one.'
    un_title = 'Uninstall Voicitool'
    un_text = 'Voicitool will be removed from this PC, including its Python environment, shortcuts and the entry in Windows settings.'
    un_keep_projects = 'Keep projects, exports and inbox'
    un_keep_models = 'Keep AI models (saves the download if you reinstall)'
    un_done = 'Voicitool has been removed'; un_done_text = 'Thanks for trying it!'; un_kept = 'Kept in: {0}'
    un_failed = 'Uninstall incomplete'
    un_step_stop = 'Close Voicitool'; un_step_files = 'Remove files'; un_step_links = 'Remove shortcuts'; un_step_reg = 'Remove Windows entry'
    step = @{ files = 'Copy files'; uv = 'Prepare installer'; deno = 'YouTube support'; webview = 'Window component'
              python = 'Python 3.11'; venv = 'Python environment'; packages = 'AI packages, {0}'; models = 'AI models'
              check = 'Check installation'; shortcut = 'Shortcuts'; register = 'Register with Windows' }
    variant = @{ cu128 = 'graphics card (CUDA 12.8)'; cu126 = 'graphics card (CUDA 12.6)'; cpu = 'processor only' }
    already = 'already there'; done = 'done'; not_wanted = 'not selected'
    chk = @{ os = 'Windows'; gpu = 'Graphics card'; driver = 'NVIDIA driver'; cpu = 'Processor'; ram = 'Memory'
             disk = 'Disk space'; folder = 'Location'; webview = 'Window component (WebView2)'; net = 'Internet' }
    os_ok = '{0} (64-bit, build {1})'; os_fail = '{0}: Windows 10 (1809 or newer) or 11, 64-bit, is required'
    gpu_low = ' (little video memory, quality »Fast« recommended)'
    drv_ok = 'Version {0}, CUDA {1}'; drv_old = 'Version {0} is too old. Please update the driver (nvidia.com/drivers), otherwise everything runs slowly on the processor.'
    gpu_other = '{0}: not accelerated (NVIDIA only). Voicitool runs on the processor. It works, but takes much longer.'
    gpu_old = '{0}: too old for AI acceleration. Voicitool runs on the processor. It works, but takes longer.'
    cpu_ok = '{0}, {1} threads. Quality »Fast« recommended.'; cpu_few = '{0}, {1} threads. Few cores: long videos take very long, quality »Fast« recommended.'
    ram_warn = '{0} GB: long videos may get tight (16 GB recommended)'; ram_fail = '{0} GB: at least 8 GB needed'
    disk_ok = '{0} GB free on {1} (about {2} GB needed)'; disk_warn = '{0} GB free: tight (about {1} GB needed, projects come on top)'; disk_fail = '{0} GB free: about {1} GB needed'
    folder_onedrive = 'Folder is inside OneDrive. Better pick e.g. C:\Voicitool (large files).'
    folder_special = 'Path contains special characters. If problems occur, pick e.g. C:\Voicitool.'
    wv_ok = 'present'; wv_missing = 'missing, will be installed automatically'
    net_ok = 'pypi.org, huggingface.co and github.com are reachable'
    net_offline = 'no connection: only the existing installation can be checked'
    net_fail = 'no connection to pypi.org, huggingface.co or github.com, needed for setup'
    d_uv = 'Downloading uv (package installer) …'; d_deno = 'Downloading deno (for YouTube) …'; d_wv = 'Downloading WebView2 …'; d_wv2 = 'Installing WebView2 …'
    d_py = 'Setting up Python 3.11 …'; d_py_have = 'Python {0} already there'; d_venv = 'Creating Python environment …'
    d_pk = 'Resolving packages …'; d_pk_load = 'Downloading {0} ({1}) …'; d_models = 'Downloading AI models …'; d_check = 'Checking installation …'
    d_files = 'Copying program files …'; step_failed = 'Step »{0}« failed (code {1}):'
    shortcut_desc = 'Voicitool: dub packs for Choicer Voicer'; shortcut_check = 'Voicitool check'
    script_error = 'Script error ({0}), line {1}: {2}'; aborted = 'Cancelled'; lang = 'Language'
  }
}
function To-Hash($o) {
    if ($o -is [Management.Automation.PSCustomObject]) { $h = @{}; foreach ($p in $o.PSObject.Properties) { $h[$p.Name] = To-Hash $p.Value }; return $h }
    return $o
}
function Get-Texts($lang) {
    # Englisch als Grundlage, darüber die gewählte Sprache (verschachtelte Tabellen Eintrag für Eintrag)
    $t = @{}
    foreach ($k in $Text.en.Keys) { $v = $Text.en[$k]; $t[$k] = if ($v -is [hashtable]) { $v.Clone() } else { $v } }
    $over = if ($Text.ContainsKey($lang)) { $Text[$lang] } elseif ($Extra -and $Extra.$lang) { To-Hash $Extra.$lang } else { @{} }
    foreach ($k in $over.Keys) {
        if ($over[$k] -is [hashtable] -and $t[$k] -is [hashtable]) { foreach ($k2 in $over[$k].Keys) { $t[$k][$k2] = $over[$k][$k2] } }
        elseif ($over[$k]) { $t[$k] = $over[$k] }
    }
    return $t
}
$T = Get-Texts $Lang
function L($key) { $T[$key] }

$sync = [hashtable]::Synchronized(@{
    Root = $Root; SourceRoot = $SourceRoot; Exe = $Exe; Mode = $Mode; LogDir = $LogDir; T = $T; RegKey = $RegKey
    DesktopDir = $DesktopDir; MenuDir = $MenuDir
    Checks = [System.Collections.ArrayList]::Synchronized((New-Object System.Collections.ArrayList))
    ChecksDone = $false; CanInstall = $false; Variant = 'cpu'; Ready = $false
    Steps = $null; StepIndex = -1; StepPct = 0.0; Overall = 0.0; Detail = ''
    Done = $false; Error = $null; Cancel = $false; Started = $null
    Desktop = $true; StartMenu = $true; Launch = $true; Models = ''
    KeepProjects = $true; KeepModels = $false
})

# ------------------------------------------------------------------ Oberfläche
[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Voicitool" Width="780" Height="800" WindowStartupLocation="CenterScreen"
        WindowStyle="None" AllowsTransparency="True" Background="Transparent" ResizeMode="NoResize"
        FontFamily="Segoe UI" Foreground="#E7E9EE">
  <Window.Resources>
    <SolidColorBrush x:Key="Muted" Color="#8D94A3"/>
    <LinearGradientBrush x:Key="Accent" StartPoint="0,0" EndPoint="1,1">
      <GradientStop Color="#3F7CF0" Offset="0"/><GradientStop Color="#A855F7" Offset="1"/>
    </LinearGradientBrush>
    <Style TargetType="Button" x:Key="Btn">
      <Setter Property="Foreground" Value="#E7E9EE"/><Setter Property="Background" Value="#20242D"/>
      <Setter Property="BorderBrush" Value="#2C313C"/><Setter Property="Padding" Value="18,9"/>
      <Setter Property="FontSize" Value="14"/><Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" CornerRadius="8" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="1" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="b" Property="BorderBrush" Value="#6AA0FF"/></Trigger>
              <Trigger Property="IsEnabled" Value="False"><Setter TargetName="b" Property="Opacity" Value="0.45"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style TargetType="Button" x:Key="Primary" BasedOn="{StaticResource Btn}">
      <Setter Property="Background" Value="{StaticResource Accent}"/><Setter Property="BorderBrush" Value="Transparent"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
    </Style>
    <Style TargetType="Button" x:Key="Danger" BasedOn="{StaticResource Btn}">
      <Setter Property="Background" Value="#B83B4A"/><Setter Property="BorderBrush" Value="Transparent"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
    </Style>
    <Style TargetType="Button" x:Key="LangItem">
      <Setter Property="Foreground" Value="#E7E9EE"/><Setter Property="Background" Value="Transparent"/>
      <Setter Property="FontSize" Value="13"/><Setter Property="Padding" Value="12,7"/><Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" CornerRadius="7" Background="{TemplateBinding Background}" Padding="{TemplateBinding Padding}">
              <ContentPresenter HorizontalAlignment="Left" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True"><Setter TargetName="b" Property="Background" Value="#232833"/></Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style TargetType="ProgressBar">
      <Setter Property="Height" Value="10"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="ProgressBar">
            <Grid>
              <Border CornerRadius="5" Background="#20242D"/>
              <Border x:Name="PART_Track" CornerRadius="5"/>
              <Border x:Name="PART_Indicator" CornerRadius="5" HorizontalAlignment="Left" Background="{StaticResource Accent}"/>
            </Grid>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style TargetType="CheckBox">
      <Setter Property="Foreground" Value="#E7E9EE"/><Setter Property="FontSize" Value="13"/><Setter Property="Margin" Value="0,3,0,3"/>
    </Style>
    <Style TargetType="TextBox">
      <Setter Property="Background" Value="#0E1016"/><Setter Property="Foreground" Value="#E7E9EE"/>
      <Setter Property="BorderBrush" Value="#2C313C"/><Setter Property="Padding" Value="8,6"/><Setter Property="FontSize" Value="13"/>
    </Style>
  </Window.Resources>

  <Border CornerRadius="16" Background="#0E1016" BorderBrush="#2C313C" BorderThickness="1">
    <Grid>
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/><RowDefinition Height="*"/><RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <Grid x:Name="TitleBar" Grid.Row="0" Margin="28,22,18,8" Background="Transparent">
        <StackPanel Orientation="Horizontal">
          <Image x:Name="Logo" Width="58" Height="58"/>
          <StackPanel Margin="16,2,0,0" VerticalAlignment="Center">
            <TextBlock Text="Voicitool" FontSize="28" FontWeight="Bold"/>
            <TextBlock x:Name="SubTitle" FontSize="13" Foreground="{StaticResource Muted}"/>
          </StackPanel>
        </StackPanel>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Top">
          <!-- Sprachwahl: nur vor dem Installieren -->
          <Button x:Name="BtnLang" Style="{StaticResource Btn}" Height="34" Padding="11,0" Margin="0,0,6,0" FontSize="13"
                  Background="Transparent" BorderBrush="#2C313C">
            <StackPanel Orientation="Horizontal">
              <TextBlock Text="&#xE774;" FontFamily="Segoe MDL2 Assets" FontSize="14" VerticalAlignment="Center" Foreground="{StaticResource Muted}"/>
              <TextBlock x:Name="LangName" Margin="8,0,8,0" VerticalAlignment="Center"/>
              <TextBlock Text="&#xE70D;" FontFamily="Segoe MDL2 Assets" FontSize="10" VerticalAlignment="Center" Foreground="{StaticResource Muted}"/>
            </StackPanel>
          </Button>
          <Button x:Name="BtnClose" Content="✕" Width="34" Height="34"
                  Style="{StaticResource Btn}" Padding="0" Background="Transparent" BorderBrush="Transparent"/>
        </StackPanel>
        <Popup x:Name="LangPopup" PlacementTarget="{Binding ElementName=BtnLang}" Placement="Bottom" StaysOpen="False"
               AllowsTransparency="True" PopupAnimation="Fade">
          <Border Width="380" CornerRadius="12" Background="#161920" BorderBrush="#2C313C" BorderThickness="1" Padding="6" Margin="0,6,0,0">
            <UniformGrid x:Name="LangList" Columns="2"/>
          </Border>
        </Popup>
      </Grid>

      <!-- Seite 1: PC-Check und Optionen -->
      <ScrollViewer x:Name="PageCheck" Grid.Row="1" Margin="28,6,20,0" VerticalScrollBarVisibility="Auto">
        <StackPanel Margin="0,0,8,0">
          <TextBlock x:Name="HCheck" FontSize="18" FontWeight="SemiBold" Margin="0,0,0,10"/>
          <Border CornerRadius="12" Background="#161920" BorderBrush="#232833" BorderThickness="1" Padding="16,8">
            <StackPanel x:Name="CheckList"/>
          </Border>
          <Border x:Name="OptionsCard" CornerRadius="12" Background="#161920" BorderBrush="#232833" BorderThickness="1" Padding="16,12" Margin="0,14,0,0">
            <StackPanel>
              <TextBlock x:Name="HOptions" FontWeight="SemiBold" FontSize="14"/>
              <StackPanel x:Name="LocationRow" Margin="0,8,0,0">
                <TextBlock x:Name="LLocation" FontSize="12" Foreground="{StaticResource Muted}"/>
                <Grid Margin="0,4,0,0">
                  <Grid.ColumnDefinitions><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/></Grid.ColumnDefinitions>
                  <TextBox x:Name="TxtLocation" Grid.Column="0"/>
                  <Button x:Name="BtnBrowse" Grid.Column="1" Style="{StaticResource Btn}" Padding="12,6" Margin="8,0,0,0" FontSize="13"/>
                </Grid>
                <TextBlock x:Name="LFree" FontSize="12" Foreground="{StaticResource Muted}" Margin="0,4,0,0"/>
              </StackPanel>
              <StackPanel x:Name="PacksRow" Margin="0,10,0,0">
                <TextBlock x:Name="LPacks" FontSize="12" Foreground="{StaticResource Muted}"/>
                <CheckBox x:Name="ChkPackEn" IsChecked="True" IsEnabled="False"/>
                <CheckBox x:Name="ChkPackTurbo"/>
                <CheckBox x:Name="ChkPackLarge"/>
                <TextBlock x:Name="LPacksLater" FontSize="12" Foreground="{StaticResource Muted}" TextWrapping="Wrap"/>
              </StackPanel>
              <StackPanel Margin="0,10,0,0">
                <CheckBox x:Name="ChkDesktop" IsChecked="True"/>
                <CheckBox x:Name="ChkStartMenu" IsChecked="True"/>
                <CheckBox x:Name="ChkLaunch" IsChecked="True"/>
              </StackPanel>
            </StackPanel>
          </Border>
          <Border CornerRadius="12" Background="#161920" BorderBrush="#232833" BorderThickness="1" Padding="16,12" Margin="0,14,0,14">
            <StackPanel>
              <TextBlock x:Name="HWhat" FontWeight="SemiBold" FontSize="14"/>
              <TextBlock x:Name="InstallInfo" TextWrapping="Wrap" Foreground="{StaticResource Muted}" FontSize="13" Margin="0,4,0,0"/>
            </StackPanel>
          </Border>
        </StackPanel>
      </ScrollViewer>

      <!-- Seite 2: Fortschritt -->
      <Grid x:Name="PageInstall" Grid.Row="1" Margin="28,6,28,0" Visibility="Collapsed">
        <StackPanel>
          <TextBlock x:Name="HInstall" FontSize="18" FontWeight="SemiBold" Margin="0,0,0,10"/>
          <Border CornerRadius="12" Background="#161920" BorderBrush="#232833" BorderThickness="1" Padding="16,8">
            <StackPanel x:Name="StepList"/>
          </Border>
          <Border CornerRadius="12" Background="#161920" BorderBrush="#232833" BorderThickness="1" Padding="16,14" Margin="0,14,0,0">
            <StackPanel>
              <Grid>
                <TextBlock x:Name="StepTitle" FontWeight="SemiBold" FontSize="14"/>
                <TextBlock x:Name="Elapsed" HorizontalAlignment="Right" Foreground="{StaticResource Muted}" FontSize="12"/>
              </Grid>
              <ProgressBar x:Name="StepBar" Minimum="0" Maximum="1" Margin="0,8,0,4" Height="6"/>
              <TextBlock x:Name="StepDetail" Foreground="{StaticResource Muted}" FontSize="12" TextTrimming="CharacterEllipsis"/>
              <Grid Margin="0,14,0,4">
                <TextBlock x:Name="LTotal" FontSize="13"/>
                <TextBlock x:Name="OverallText" Text="0 %" HorizontalAlignment="Right" FontSize="13" FontWeight="SemiBold"/>
              </Grid>
              <ProgressBar x:Name="OverallBar" Minimum="0" Maximum="1" Height="12"/>
            </StackPanel>
          </Border>
        </StackPanel>
      </Grid>

      <!-- Seite: Deinstallieren -->
      <Grid x:Name="PageUninstall" Grid.Row="1" Margin="28,6,28,0" Visibility="Collapsed">
        <StackPanel>
          <TextBlock x:Name="HUninstall" FontSize="18" FontWeight="SemiBold" Margin="0,0,0,10"/>
          <Border CornerRadius="12" Background="#161920" BorderBrush="#232833" BorderThickness="1" Padding="16,14">
            <StackPanel>
              <TextBlock x:Name="UnText" TextWrapping="Wrap" FontSize="13" Foreground="{StaticResource Muted}"/>
              <TextBlock x:Name="UnPath" TextWrapping="Wrap" FontSize="12" Margin="0,8,0,8"/>
              <CheckBox x:Name="ChkKeepProjects" IsChecked="True"/>
              <CheckBox x:Name="ChkKeepModels" IsChecked="False"/>
            </StackPanel>
          </Border>
        </StackPanel>
      </Grid>

      <!-- Seite 3: Fertig / Fehler -->
      <Grid x:Name="PageDone" Grid.Row="1" Margin="28,6,28,0" Visibility="Collapsed">
        <StackPanel VerticalAlignment="Center">
          <Border x:Name="DoneBadge" Width="86" Height="86" CornerRadius="43" Background="{StaticResource Accent}" HorizontalAlignment="Center">
            <TextBlock x:Name="DoneIcon" Text="✓" FontSize="44" HorizontalAlignment="Center" VerticalAlignment="Center" Foreground="White"/>
          </Border>
          <TextBlock x:Name="DoneTitle" FontSize="24" FontWeight="Bold" HorizontalAlignment="Center" Margin="0,18,0,6"/>
          <TextBlock x:Name="DoneText" TextWrapping="Wrap" TextAlignment="Center" Foreground="{StaticResource Muted}" FontSize="13" MaxWidth="560"/>
        </StackPanel>
      </Grid>

      <Grid Grid.Row="2" Margin="28,14,28,24">
        <TextBlock x:Name="Footer" Text="Voicitool by Jan1653" VerticalAlignment="Center" Foreground="#5D6472" FontSize="12"/>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
          <Button x:Name="BtnLog" Style="{StaticResource Btn}" Margin="0,0,10,0" Visibility="Collapsed"/>
          <Button x:Name="BtnCancel" Style="{StaticResource Btn}" Margin="0,0,10,0"/>
          <Button x:Name="BtnMain" Style="{StaticResource Primary}" IsEnabled="False" MinWidth="170"/>
        </StackPanel>
      </Grid>
    </Grid>
  </Border>
</Window>
'@

$win = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $xaml))
function C($name) { $win.FindName($name) }
$iconPath = Join-Path $SourceRoot 'app\static\icon.png'
if (Test-Path $iconPath) {
    $bmp = New-Object System.Windows.Media.Imaging.BitmapImage
    $bmp.BeginInit(); $bmp.UriSource = New-Object Uri $iconPath; $bmp.CacheOption = 'OnLoad'; $bmp.EndInit()
    (C 'Logo').Source = $bmp
    $win.Icon = $bmp
}
(C 'TitleBar').Add_MouseLeftButtonDown({ $win.DragMove() })

# Texte setzen (auch nach dem Umschalten der Sprache)
function Apply-Texts {
    (C 'SubTitle').Text = @{ install = (L 'title_install'); uninstall = (L 'title_uninstall') }[$Mode]
    if (-not (C 'SubTitle').Text) { (C 'SubTitle').Text = L 'title_setup' }
    $win.Title = "Voicitool · $((C 'SubTitle').Text)"
    (C 'HCheck').Text = L 'pc_check'; (C 'HOptions').Text = L 'options'; (C 'LLocation').Text = L 'location'
    (C 'BtnBrowse').Content = L 'change'; (C 'LPacks').Text = L 'packs'
    (C 'ChkPackEn').Content = L 'pack_en'; (C 'ChkPackTurbo').Content = L 'pack_turbo'; (C 'ChkPackLarge').Content = L 'pack_large'
    (C 'LPacksLater').Text = L 'packs_later'
    (C 'ChkDesktop').Content = L 'desktop'; (C 'ChkStartMenu').Content = L 'startmenu'; (C 'ChkLaunch').Content = L 'launch'
    (C 'HWhat').Text = L 'what'; (C 'HInstall').Text = L 'installing'; (C 'LTotal').Text = L 'total'
    (C 'HUninstall').Text = L 'un_title'; (C 'UnText').Text = L 'un_text'
    (C 'ChkKeepProjects').Content = L 'un_keep_projects'; (C 'ChkKeepModels').Content = L 'un_keep_models'
    (C 'BtnLog').Content = L 'btn_log'; (C 'BtnCancel').Content = L 'btn_cancel'
    (C 'LangName').Text = $Langs[$script:Lang]; (C 'BtnLang').ToolTip = L 'lang'
}
Apply-Texts
(C 'BtnMain').Content = L 'btn_checking'
(C 'UnPath').Text = $Root
(C 'TxtLocation').Text = $Root

# Optionen nur bei der Neuinstallation vollständig; beim Prüfen nur die Verknüpfungen
if ($Mode -ne 'install') {
    (C 'LocationRow').Visibility = 'Collapsed'; (C 'PacksRow').Visibility = 'Collapsed'; (C 'ChkLaunch').Visibility = 'Collapsed'
}

$brushes = @{
    ok   = [Windows.Media.BrushConverter]::new().ConvertFrom('#3ECF8E')
    warn = [Windows.Media.BrushConverter]::new().ConvertFrom('#F5A524')
    fail = [Windows.Media.BrushConverter]::new().ConvertFrom('#FF6B6B')
    run  = [Windows.Media.BrushConverter]::new().ConvertFrom('#6AA0FF')
    wait = [Windows.Media.BrushConverter]::new().ConvertFrom('#3A404C')
    skip = [Windows.Media.BrushConverter]::new().ConvertFrom('#3ECF8E')
    muted = [Windows.Media.BrushConverter]::new().ConvertFrom('#8D94A3')
}
$glyph = @{ ok = '✓'; warn = '!'; fail = '✕'; run = '•'; wait = ''; skip = '✓' }

function New-Row($panel, $title) {
    $grid = New-Object Windows.Controls.Grid
    $grid.Margin = '0,4,0,4'
    $c0 = New-Object Windows.Controls.ColumnDefinition; $c0.Width = '34'
    $c1 = New-Object Windows.Controls.ColumnDefinition; $c1.Width = '*'
    $grid.ColumnDefinitions.Add($c0); $grid.ColumnDefinitions.Add($c1)
    $dot = New-Object Windows.Controls.Border
    $dot.Width = 22; $dot.Height = 22; $dot.CornerRadius = 11; $dot.Background = $brushes.wait
    $dot.VerticalAlignment = 'Top'; $dot.Margin = '0,1,0,0'
    $icon = New-Object Windows.Controls.TextBlock
    $icon.HorizontalAlignment = 'Center'; $icon.VerticalAlignment = 'Center'; $icon.FontSize = 12
    $icon.FontWeight = 'Bold'; $icon.Foreground = 'White'
    $dot.Child = $icon
    [Windows.Controls.Grid]::SetColumn($dot, 0)
    $sp = New-Object Windows.Controls.StackPanel
    [Windows.Controls.Grid]::SetColumn($sp, 1)
    $t = New-Object Windows.Controls.TextBlock; $t.Text = $title; $t.FontSize = 14
    $d = New-Object Windows.Controls.TextBlock; $d.FontSize = 12; $d.Foreground = $brushes.muted; $d.TextWrapping = 'Wrap'
    $sp.Children.Add($t) | Out-Null; $sp.Children.Add($d) | Out-Null
    $grid.Children.Add($dot) | Out-Null; $grid.Children.Add($sp) | Out-Null
    $panel.Children.Add($grid) | Out-Null
    return @{ Dot = $dot; Icon = $icon; Title = $t; Detail = $d }
}
function Set-Row($row, $state, $detail) {
    $row.Dot.Background = $brushes[$state]; $row.Icon.Text = $glyph[$state]
    if ($null -ne $detail) { $row.Detail.Text = $detail }
}

# ------------------------------------------------------------------ Hintergrund-Aufgaben
$bgJobs = @()
function Start-Background([string]$script, [string]$name) {
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseInput($script, [ref]$null, [ref]$errors)
    if ($errors.Count) {
        $sync.Error = (L 'script_error') -f $name, $errors[0].Extent.StartLineNumber, $errors[0].Message
        $sync.Done = $true
        return $null
    }
    $ps = [powershell]::Create()
    $ps.AddScript($script).AddArgument($sync) | Out-Null
    $handle = $ps.BeginInvoke()
    $script:bgJobs += , @{ PS = $ps; Handle = $handle; Name = $name; Seen = $false }
    return $ps
}
function Check-Background {
    # abgestürzte Hintergrund-Aufgabe melden, statt stumm stehenzubleiben
    foreach ($j in $script:bgJobs) {
        if ($j.Handle.IsCompleted -and -not $j.Seen) {
            $j.Seen = $true
            $err = $j.PS.Streams.Error
            if ($err.Count -and -not $sync.Error) {
                $sync.Error = "$($j.Name): $($err[0].ToString())"
                $sync.Done = $true
            }
        }
    }
}

# ------------------------------------------------------------------ PC-Check
$checkScript = @'
param($sync)
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# Text erst in der Oberfläche zusammensetzen (Msg = Schlüssel, Args = Werte), damit er beim Sprachwechsel mitgeht
function Add-Check($key, $state, $msg, $a = @(), $raw = '', $suffix = '') {
    $sync.Checks.Add(@{ Key = $key; State = $state; Msg = $msg; Args = @($a); Raw = $raw; Suffix = $suffix }) | Out-Null
}
$root = $sync.Root
$fail = $false

$os = Get-CimInstance Win32_OperatingSystem
$build = [int]$os.BuildNumber
if ([Environment]::Is64BitOperatingSystem -and $build -ge 17763) { Add-Check 'os' 'ok' 'os_ok' @($os.Caption, $build) }
else { Add-Check 'os' 'fail' 'os_fail' @($os.Caption); $fail = $true }

$smi = Join-Path $env:WINDIR 'System32\nvidia-smi.exe'
$gpu = $null
if (Test-Path $smi) {
    try {
        $out = & $smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        if ($out) { $p = $out.Split(','); $gpu = @{ Name = $p[0].Trim(); VramGB = [math]::Round([double]$p[1] / 1024, 1); Driver = $p[2].Trim(); Cap = 0.0 } }
        # Rechenfähigkeit (ältere Treiber kennen die Abfrage nicht, dann bleibt sie 0 = unbekannt)
        $capOut = & $smi --query-gpu=compute_cap --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        $capVal = 0.0
        if ($gpu -and $capOut -and [double]::TryParse($capOut.Trim(), [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$capVal)) { $gpu.Cap = $capVal }
    } catch {}
}
if ($gpu) {
    $state = if ($gpu.VramGB -ge 6) { 'ok' } else { 'warn' }
    $note = if ($gpu.VramGB -ge 6) { '' } else { 'gpu_low' }
    Add-Check 'gpu' $state '' @() "$($gpu.Name), $($gpu.VramGB) GB" $note
    $drv = [version]($gpu.Driver + '.0')
    # CUDA 12.8 unterstützt erst ab Rechenfähigkeit 7.5 (GTX 16xx/RTX); ältere Karten nehmen 12.6, sehr alte den Prozessor
    $oldCard = $gpu.Cap -gt 0 -and $gpu.Cap -lt 7.5
    if ($gpu.Cap -gt 0 -and $gpu.Cap -lt 5.0) { $sync.Variant = 'cpu'; Add-Check 'driver' 'warn' 'gpu_old' @($gpu.Name) }
    elseif ($drv -ge [version]'570.65.0' -and -not $oldCard) { $sync.Variant = 'cu128'; Add-Check 'driver' 'ok' 'drv_ok' @($gpu.Driver, '12.8') }
    elseif ($drv -ge [version]'560.76.0') { $sync.Variant = 'cu126'; Add-Check 'driver' 'ok' 'drv_ok' @($gpu.Driver, '12.6') }
    else { $sync.Variant = 'cpu'; Add-Check 'driver' 'warn' 'drv_old' @($gpu.Driver) }
} else {
    # AMD/Intel/ohne Grafikkarte: Whisper und PyTorch beschleunigen unter Windows nur NVIDIA
    $cards = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notmatch 'Basic|Remote|Virtual|Parsec|Meta' } | ForEach-Object Name)
    $names = if ($cards) { $cards -join ', ' } else { '-' }
    $sync.Variant = 'cpu'
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $cores = [int]$cpu.NumberOfLogicalProcessors
    Add-Check 'gpu' 'warn' 'gpu_other' @($names)
    if ($cores -ge 8) { Add-Check 'cpu' 'ok' 'cpu_ok' @($cpu.Name.Trim(), $cores) }
    else { Add-Check 'cpu' 'warn' 'cpu_few' @($cpu.Name.Trim(), $cores) }
}

$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
if ($ram -ge 16) { Add-Check 'ram' 'ok' '' @() "$ram GB" }
elseif ($ram -ge 8) { Add-Check 'ram' 'warn' 'ram_warn' @($ram) }
else { Add-Check 'ram' 'fail' 'ram_fail' @($ram); $fail = $true }

$qual = [IO.Path]::GetPathRoot($root)
$drive = New-Object IO.DriveInfo $qual
$freeGB = [math]::Round($drive.AvailableFreeSpace / 1GB)
$venvOk = Test-Path (Join-Path $root '.venv\Scripts\python.exe')
$needGB = if ($venvOk) { 3 } else { 12 }
if ($freeGB -ge $needGB + 5) { Add-Check 'disk' 'ok' 'disk_ok' @($freeGB, $qual.TrimEnd('\'), $needGB) }
elseif ($freeGB -ge $needGB) { Add-Check 'disk' 'warn' 'disk_warn' @($freeGB, $needGB) }
else { Add-Check 'disk' 'fail' 'disk_fail' @($freeGB, $needGB); $fail = $true }

if ($root -match 'OneDrive') { Add-Check 'folder' 'warn' 'folder_onedrive' }
elseif ($root -match '[^\x00-\x7F]') { Add-Check 'folder' 'warn' 'folder_special' }
else { Add-Check 'folder' 'ok' '' @() $root }

$wv = $false
foreach ($k in @('HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
                'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
                'HKCU:\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')) {
    try { $pv = (Get-ItemProperty -Path $k -ErrorAction Stop).pv; if ($pv -and $pv -ne '0.0.0.0') { $wv = $true } } catch {}
}
if (-not $wv -and (Test-Path "${env:ProgramFiles(x86)}\Microsoft\EdgeWebView\Application")) { $wv = $true }
$sync.NeedWebView2 = -not $wv
if ($wv) { Add-Check 'webview' 'ok' 'wv_ok' } else { Add-Check 'webview' 'warn' 'wv_missing' }

$net = $true
foreach ($u in @('https://pypi.org/simple/', 'https://huggingface.co/', 'https://github.com/')) {
    try { Invoke-WebRequest -Uri $u -Method Head -UseBasicParsing -TimeoutSec 12 | Out-Null } catch { $net = $false }
}
if ($net) { Add-Check 'net' 'ok' 'net_ok' }
elseif ($venvOk -and (Test-Path (Join-Path $root 'daten\setup.json'))) { Add-Check 'net' 'warn' 'net_offline' }
else { Add-Check 'net' 'fail' 'net_fail'; $fail = $true }

$sync.CanInstall = -not $fail
$sync.ChecksDone = $true
'@

# ------------------------------------------------------------------ Installation
$installScript = @'
param($sync)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$T = $sync.T
$root = $sync.Root
$tools = Join-Path $root 'tools'
$uvDir = Join-Path $tools 'uv'
$uv = Join-Path $uvDir 'uv.exe'
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
$env:UV_CACHE_DIR = Join-Path $tools 'uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $tools 'python'
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:UV_HTTP_TIMEOUT = '180'
$env:UV_NO_PROGRESS = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Dir-Size($p) {
    if (-not (Test-Path $p)) { return 0 }
    $s = 0; foreach ($f in [IO.Directory]::EnumerateFiles($p, '*', 'AllDirectories')) { try { $s += ([IO.FileInfo]$f).Length } catch {} }; return $s
}
function Set-Step($i, $state) { $sync.Steps[$i].State = $state }
function Update-Overall {
    $done = 0.0; $total = 0.0
    for ($k = 0; $k -lt $sync.Steps.Count; $k++) {
        $s = $sync.Steps[$k]; $total += $s.Weight
        if ($s.State -in @('ok', 'skip')) { $done += $s.Weight } elseif ($k -eq $sync.StepIndex) { $done += $s.Weight * $sync.StepPct }
    }
    $sync.Overall = [math]::Min(1.0, $done / [math]::Max(1, $total))
}

# Prozess starten, Ausgabe in Logdatei, regelmäßig pollen (neue Zeilen an Callback)
function Invoke-Logged($exe, [string]$arguments, $name, [scriptblock]$onPoll) {
    $log = Join-Path $sync.LogDir "setup_$name.log"
    Set-Content -Path $log -Value '' -Encoding UTF8
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = "/d /s /c `"`"$exe`" $arguments > `"$log`" 2>&1`""
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $p = [Diagnostics.Process]::Start($psi)
    $sync.Proc = $p
    $pos = 0
    while ($true) {
        $exited = $p.HasExited
        $lines = @()
        try {
            $fs = New-Object IO.FileStream($log, 'Open', 'Read', 'ReadWrite')
            if ($fs.Length -gt $pos) {
                $fs.Seek($pos, 'Begin') | Out-Null
                $buf = New-Object byte[] ($fs.Length - $pos)
                $n = $fs.Read($buf, 0, $buf.Length); $pos += $n
                $lines = [Text.Encoding]::UTF8.GetString($buf, 0, $n) -split "`r?`n" | Where-Object { $_.Trim() }
            }
            $fs.Close()
        } catch {}
        if ($onPoll) { & $onPoll $lines }
        Update-Overall
        if ($exited) { break }
        if ($sync.Cancel) { & taskkill /PID $p.Id /T /F | Out-Null; throw $T.aborted }
        Start-Sleep -Milliseconds 400
    }
    $sync.Proc = $null
    if ($p.ExitCode -ne 0) {
        $tail = (Get-Content $log -Tail 8 -ErrorAction SilentlyContinue) -join "`n"
        throw (($T.step_failed -f $name, $p.ExitCode) + "`n" + $tail)
    }
}

function Download($url, $dest, $expectedBytes, $name) {
    $script:dl = @{ Dest = $dest; Expected = $expectedBytes }
    Invoke-Logged 'curl.exe' "-L --fail --retry 3 -o `"$dest`" `"$url`"" $name {
        param($lines)
        if (Test-Path $script:dl.Dest) { $sync.StepPct = [math]::Min(0.99, (Get-Item $script:dl.Dest).Length / $script:dl.Expected) }
    }
}

function New-Shortcut($path, $target, $arguments, $root) {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($path)
    $lnk.TargetPath = $target; $lnk.Arguments = $arguments; $lnk.WorkingDirectory = $root
    $lnk.IconLocation = (Join-Path $root 'app\static\icon.ico'); $lnk.Description = $T.shortcut_desc
    $lnk.Save()
}

$variant = $sync.Variant
$pyX64 = 'cpython-3.11-windows-x86_64-none'
$torchIndex = "https://download.pytorch.org/whl/$variant"
$torchPkgs = "torch==2.11.0+$variant torchaudio==2.11.0+$variant torchvision==0.26.0+$variant"
$expectedCache = if ($variant -eq 'cpu') { 1.6GB } else { 5.5GB }

try {
    $sync.Started = Get-Date
    for ($i = 0; $i -lt $sync.Steps.Count; $i++) {
        $step = $sync.Steps[$i]
        $sync.StepIndex = $i; $sync.StepPct = 0.0; $sync.Detail = ''
        Set-Step $i 'run'
        $optional = $step.Key -in @('check', 'shortcut', 'webview', 'deno', 'register')
        try {
            switch ($step.Key) {
            'files' {
                # Neuinstallation: Programmdateien aus dem Entpack-Ordner an den gewählten Ort kopieren
                $sync.Detail = $T.d_files
                New-Item -ItemType Directory -Force -Path $root | Out-Null
                foreach ($item in @('app', 'README.md', 'LICENSE')) {
                    $src = Join-Path $sync.SourceRoot $item
                    if (Test-Path $src) { Copy-Item -Path $src -Destination $root -Recurse -Force }
                }
                if ($sync.Exe -and (Test-Path $sync.Exe)) { Copy-Item -Path $sync.Exe -Destination (Join-Path $root 'Voicitool.exe') -Force }
                foreach ($d in @('eingang', 'projekte', 'export', 'daten')) { New-Item -ItemType Directory -Force -Path (Join-Path $root $d) | Out-Null }
                $sync.LogDir = Join-Path $root 'daten\logs'
                New-Item -ItemType Directory -Force -Path $sync.LogDir | Out-Null
            }
            'uv' {
                New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
                if (Test-Path $uv) { Set-Step $i 'skip'; continue }
                $zip = Join-Path $uvDir 'uv.zip'
                $sync.Detail = $T.d_uv
                Download 'https://github.com/astral-sh/uv/releases/download/0.12.15/uv-x86_64-pc-windows-msvc.zip' $zip 17.6MB 'uv'
                Expand-Archive -Path $zip -DestinationPath $uvDir -Force
                Remove-Item $zip -Force
            }
            'deno' {
                # JavaScript-Laufzeit für yt-dlp: ohne sie bremst YouTube manche Downloads stark aus
                $denoDir = Join-Path $tools 'deno'
                if (Test-Path (Join-Path $denoDir 'deno.exe')) { Set-Step $i 'skip'; continue }
                New-Item -ItemType Directory -Force -Path $denoDir | Out-Null
                $zip = Join-Path $denoDir 'deno.zip'
                $sync.Detail = $T.d_deno
                Download 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' $zip 43MB 'deno'
                Expand-Archive -Path $zip -DestinationPath $denoDir -Force
                Remove-Item $zip -Force
            }
            'webview' {
                if (-not $sync.NeedWebView2) { Set-Step $i 'skip'; continue }
                $wvExe = Join-Path $tools 'MicrosoftEdgeWebview2Setup.exe'
                $sync.Detail = $T.d_wv
                Download 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' $wvExe 2MB 'webview2_download'
                $sync.Detail = $T.d_wv2
                Invoke-Logged $wvExe '/silent /install' 'webview2' { param($l) $sync.StepPct = [math]::Min(0.95, $sync.StepPct + 0.02) }
            }
            'python' {
                $sync.Detail = $T.d_py
                if (Test-Path $venvPy) {
                    $v = & $venvPy -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
                    if ($v -in @('3.11', '3.12')) { Set-Step $i 'skip'; $sync.Detail = $T.d_py_have -f $v; continue }
                }
                # immer x64: auf ARM-Laptops (Snapdragon) gibt es die KI-Pakete nur für x64, Windows emuliert das
                Invoke-Logged $uv "python install $pyX64" 'python' {
                    param($lines)
                    $sync.StepPct = [math]::Min(0.95, (Dir-Size $env:UV_PYTHON_INSTALL_DIR) / 80MB)
                    foreach ($l in $lines) { $sync.Detail = $l }
                }
            }
            'venv' {
                if (Test-Path $venvPy) { Set-Step $i 'skip'; continue }
                $sync.Detail = $T.d_venv
                Invoke-Logged $uv "venv `"$(Join-Path $root '.venv')`" --python $pyX64 --seed" 'venv' { param($l) $sync.StepPct = 0.5 }
            }
            'packages' {
                $req = Join-Path $root 'app\setup\requirements-lock.txt'
                $start = Dir-Size $env:UV_CACHE_DIR
                $script:pk = @{ Start = $start; Expected = $expectedCache }
                $sync.Detail = $T.d_pk
                # yt-dlp bewusst ohne feste Version: YouTube ändert sich ständig, alte Versionen werden langsam
                $args2 = "pip install --python `"$venvPy`" -r `"$req`" $torchPkgs `"yt-dlp[default]`" --upgrade-package yt-dlp --upgrade-package yt-dlp-ejs --extra-index-url $torchIndex --index-strategy unsafe-best-match"
                Invoke-Logged $uv $args2 'pakete' {
                    param($lines)
                    $grown = (Dir-Size $env:UV_CACHE_DIR) - $script:pk.Start
                    $sync.StepPct = [math]::Min(0.97, [math]::Max($sync.StepPct, $grown / $script:pk.Expected))
                    foreach ($l in $lines) {
                        if ($l -match 'Downloading (\S+) \(([^)]+)\)') { $sync.Detail = $T.d_pk_load -f $Matches[1], $Matches[2] }
                        elseif ($l -match '^(Resolved|Prepared|Installed|Audited|Uninstalled)') { $sync.Detail = $l.Trim() }
                    }
                }
            }
            'models' {
                $sync.Detail = $T.d_models
                $modelArgs = if ($sync.Models) { " --models $($sync.Models)" } else { '' }
                Invoke-Logged $venvPy "`"$(Join-Path $root 'app\setup\download_models.py')`"$modelArgs" 'modelle' {
                    param($lines)
                    foreach ($l in $lines) {
                        try {
                            $m = $l | ConvertFrom-Json
                            if ($m.type -eq 'progress') { $sync.StepPct = [double]$m.pct; $sync.Detail = "$($m.label) · $($m.mb) MB" }
                            elseif ($m.type -eq 'step') { $sync.Detail = $m.label }
                        } catch {}
                    }
                }
            }
            'check' {
                $sync.Detail = $T.d_check
                $verify = Join-Path (Join-Path (Join-Path $root 'app') 'setup') 'verify.py'
                # Ausgabe nur ins Log (technisch, nicht übersetzt); hier bleibt „Installation wird geprüft …“
                Invoke-Logged $venvPy "`"$verify`"" 'pruefung' { param($lines) $sync.StepPct = [math]::Min(0.95, $sync.StepPct + 0.04) }
            }
            'shortcut' {
                $exe = Join-Path $root 'Voicitool.exe'
                $pyw = Join-Path $root '.venv\Scripts\pythonw.exe'
                $target = if (Test-Path $exe) { $exe } else { $pyw }
                $arg = if (Test-Path $exe) { '' } else { "`"$(Join-Path $root 'app\desktop.py')`"" }
                $desk = Join-Path $sync.DesktopDir 'Voicitool.lnk'
                $menu = $sync.MenuDir
                # Prüfen-Eintrag in einer früheren Sprache und alte Namen aufräumen (alle heißen „Voicitool …“)
                Get-ChildItem $menu -Filter 'Voicitool *.lnk' -ErrorAction SilentlyContinue | Remove-Item -Force
                $oldCheck = Join-Path $menu 'Check Voicitool.lnk'
                if (Test-Path $oldCheck) { Remove-Item $oldCheck -Force }
                if ($sync.Desktop) { New-Shortcut $desk $target $arg $root } elseif (Test-Path $desk) { Remove-Item $desk -Force }
                if ($sync.StartMenu) {
                    New-Shortcut (Join-Path $menu 'Voicitool.lnk') $target $arg $root
                    if (Test-Path $exe) { New-Shortcut (Join-Path $menu "$($T.shortcut_check).lnk") $exe '--setup' $root }
                }
                if (-not $sync.Desktop -and -not $sync.StartMenu) { Set-Step $i 'skip' }
            }
            'register' {
                # Eintrag in „Apps & Features“, damit Voicitool wie jede App deinstalliert werden kann
                $exe = Join-Path $root 'Voicitool.exe'
                if (-not (Test-Path $exe)) { Set-Step $i 'skip'; continue }
                $ver = (Get-Content (Join-Path $root 'app\version.json') -Raw | ConvertFrom-Json)
                New-Item -Path $sync.RegKey -Force | Out-Null
                $props = @{ DisplayName = 'Voicitool'; DisplayVersion = "$($ver.version) (Build $($ver.build))"; Publisher = 'Jan1653'
                            InstallLocation = $root; DisplayIcon = "`"$exe`",0"; UninstallString = "`"$exe`" --uninstall"
                            ModifyPath = "`"$exe`" --setup"; NoRepair = 1 }
                foreach ($k in $props.Keys) { Set-ItemProperty -Path $sync.RegKey -Name $k -Value $props[$k] }
                if ($ver.repo) { Set-ItemProperty -Path $sync.RegKey -Name 'URLInfoAbout' -Value "https://github.com/$($ver.repo)" }
                $kb = [int]((Dir-Size $root) / 1KB)
                New-ItemProperty -Path $sync.RegKey -Name 'EstimatedSize' -Value $kb -PropertyType DWord -Force | Out-Null
            }
            }
        } catch {
            if (-not $optional -or $sync.Cancel) { throw }
            Set-Step $i 'warn'
            $sync.Warning = "$($step.Title): $($_.Exception.Message)"
        }
        if ($sync.Steps[$i].State -eq 'run') { Set-Step $i 'ok' }
        $sync.StepPct = 1.0
        Update-Overall
    }
    # Build-Nummer merken: Voicitool.exe startet den Check erneut, sobald sich die App-Build-Nummer ändert
    $appBuild = -1
    try { $appBuild = [int]((Get-Content (Join-Path $root 'app\version.json') -Raw | ConvertFrom-Json).build) } catch {}
    $info = @{ version = 1; build = $appBuild; variant = $variant; date = (Get-Date).ToString('s') } | ConvertTo-Json
    Set-Content -Path (Join-Path $root 'daten\setup.json') -Value $info -Encoding UTF8
    $sync.Overall = 1.0
    $sync.Done = $true
} catch {
    if ($sync.StepIndex -ge 0) { Set-Step $sync.StepIndex 'fail' }
    $sync.Error = "$($_.Exception.Message)"
    $sync.Done = $true
}
'@

# ------------------------------------------------------------------ Deinstallation
$uninstallScript = @'
param($sync)
$ErrorActionPreference = 'Continue'
$root = $sync.Root
function Set-Step($i, $state) { $sync.Steps[$i].State = $state }
try {
    $sync.Started = Get-Date
    # 1) Voicitool beenden (Fenster, Server, Verarbeitung)
    $sync.StepIndex = 0; Set-Step 0 'run'
    Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -and $_.ProcessId -ne $PID
    } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
    Start-Sleep -Milliseconds 800
    Set-Step 0 'ok'; $sync.Overall = 0.1

    # 2) Dateien entfernen (Nutzerdaten je nach Wahl behalten)
    $sync.StepIndex = 1; Set-Step 1 'run'
    $keep = @()
    if ($sync.KeepProjects) { $keep += @('projekte', 'eingang', 'export') }
    if ($sync.KeepModels) { $keep += 'modelle' }
    $items = @(Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue | Where-Object { $keep -notcontains $_.Name })
    $n = 0
    foreach ($it in $items) {
        $n++; $sync.Detail = $it.Name; $sync.StepPct = $n / [math]::Max(1, $items.Count)
        $sync.Overall = 0.1 + 0.75 * $sync.StepPct
        try { Remove-Item -LiteralPath $it.FullName -Recurse -Force -ErrorAction Stop }
        catch { cmd /c "rd /s /q `"$($it.FullName)`"" 2>$null; if (Test-Path -LiteralPath $it.FullName) { Remove-Item -LiteralPath $it.FullName -Force -ErrorAction SilentlyContinue } }
    }
    if (-not @(Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue).Count) { Remove-Item -LiteralPath $root -Force -ErrorAction SilentlyContinue }
    $left = @(Get-ChildItem -LiteralPath $root -Force -ErrorAction SilentlyContinue | Where-Object { $keep -notcontains $_.Name })
    if ($left.Count) { Set-Step 1 'warn'; $sync.Warning = ($left | ForEach-Object Name) -join ', ' } else { Set-Step 1 'ok' }

    # 3) Verknüpfungen
    $sync.StepIndex = 2; Set-Step 2 'run'
    $menu = $sync.MenuDir
    foreach ($lnk in @((Join-Path $sync.DesktopDir 'Voicitool.lnk'), (Join-Path $menu 'Voicitool.lnk'), (Join-Path $menu 'Check Voicitool.lnk'))) {
        if (Test-Path $lnk) { Remove-Item $lnk -Force -ErrorAction SilentlyContinue }
    }
    Get-ChildItem $menu -Filter 'Voicitool*.lnk' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
    Set-Step 2 'ok'; $sync.Overall = 0.95

    # 4) Windows-Eintrag
    $sync.StepIndex = 3; Set-Step 3 'run'
    if (Test-Path $sync.RegKey) { Remove-Item $sync.RegKey -Recurse -Force -ErrorAction SilentlyContinue }
    Set-Step 3 'ok'; $sync.Overall = 1.0
    $sync.Done = $true
} catch {
    $sync.Error = "$($_.Exception.Message)"
    $sync.Done = $true
}
'@

# ------------------------------------------------------------------ Ablauf
$checkRows = @{}
$stepRows = @()
$state = 'checking'

function Show-Page($name) {
    foreach ($p in 'PageCheck', 'PageInstall', 'PageUninstall', 'PageDone') { (C $p).Visibility = if ($p -eq $name) { 'Visible' } else { 'Collapsed' } }
}

function Update-Free {
    try {
        $path = (C 'TxtLocation').Text
        $drive = New-Object IO.DriveInfo ([IO.Path]::GetPathRoot($path))
        (C 'LFree').Text = (L 'free') -f [math]::Round($drive.AvailableFreeSpace / 1GB), $drive.Name.TrimEnd('\')
    } catch { (C 'LFree').Text = '' }
}

function Save-UiLang($root, $lang) {
    # im Installer gewählte Sprache auch für Voicitool übernehmen (daten\einstellungen.json, UTF-8 ohne BOM)
    $dir = Join-Path $root 'daten'; $file = Join-Path $dir 'einstellungen.json'
    try {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $s = if (Test-Path -LiteralPath $file) { Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json } else { New-Object PSObject }
        $s | Add-Member -NotePropertyName ui_lang -NotePropertyValue $lang -Force
        [IO.File]::WriteAllText($file, ($s | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding $false))
    } catch {}
}

function Selected-Models {
    $ids = @('whisper-en', 'resnet', 'laugh')
    if ((C 'ChkPackTurbo').IsChecked) { $ids += 'whisper-turbo' }
    if ((C 'ChkPackLarge').IsChecked) { $ids += 'whisper-large' }
    return ($ids -join ',')
}

function Start-Install {
    if ($Mode -eq 'install') {
        # gewählten Speicherort prüfen: beschreibbar?
        $path = (C 'TxtLocation').Text.Trim()
        try {
            New-Item -ItemType Directory -Force -Path $path | Out-Null
            $probe = Join-Path $path '.schreibtest'; Set-Content -Path $probe -Value 'x'; Remove-Item $probe -Force
        } catch { [Windows.MessageBox]::Show($win, (L 'bad_folder'), 'Voicitool', 'OK', 'Warning') | Out-Null; return }
        $script:Root = $path; $sync.Root = $path
        $sync.Models = Selected-Models
    }
    if ($script:LangChosen) { Save-UiLang $script:Root $script:Lang }
    (C 'BtnLang').Visibility = 'Collapsed'; (C 'LangPopup').IsOpen = $false
    $script:state = 'installing'
    $sync.Desktop = [bool](C 'ChkDesktop').IsChecked
    $sync.StartMenu = [bool](C 'ChkStartMenu').IsChecked
    $sync.Launch = [bool](C 'ChkLaunch').IsChecked
    $variantText = $T.variant[$sync.Variant]
    $keys = @()
    if ($Mode -eq 'install') { $keys += 'files' }
    $keys += @('uv', 'deno', 'webview', 'python', 'venv', 'packages', 'models', 'check', 'shortcut', 'register')
    $weights = @{ files = 1; uv = 2; deno = 2; webview = 2; python = 4; venv = 1; packages = 50; models = 36; check = 3; shortcut = 1; register = 1 }
    $sync.Steps = [System.Collections.ArrayList]::Synchronized((New-Object System.Collections.ArrayList))
    $panel = C 'StepList'; $panel.Children.Clear(); $script:stepRows = @()
    foreach ($k in $keys) {
        $title = if ($k -eq 'packages') { $T.step[$k] -f $variantText } else { $T.step[$k] }
        $sync.Steps.Add(@{ Key = $k; Title = $title; Weight = $weights[$k]; State = 'wait' }) | Out-Null
        $script:stepRows += , (New-Row $panel $title)
    }
    Show-Page 'PageInstall'
    (C 'BtnMain').IsEnabled = $false; (C 'BtnMain').Content = L 'btn_installing'
    (C 'BtnLog').Visibility = 'Visible'
    Start-Background $installScript 'Installation' | Out-Null
}

function Start-Uninstall {
    (C 'BtnLang').Visibility = 'Collapsed'; (C 'LangPopup').IsOpen = $false
    $script:state = 'uninstalling'
    $sync.KeepProjects = [bool](C 'ChkKeepProjects').IsChecked
    $sync.KeepModels = [bool](C 'ChkKeepModels').IsChecked
    $sync.Steps = [System.Collections.ArrayList]::Synchronized((New-Object System.Collections.ArrayList))
    $panel = C 'StepList'; $panel.Children.Clear(); $script:stepRows = @()
    foreach ($k in @('un_step_stop', 'un_step_files', 'un_step_links', 'un_step_reg')) {
        $sync.Steps.Add(@{ Key = $k; Title = (L $k); Weight = 1; State = 'wait' }) | Out-Null
        $script:stepRows += , (New-Row $panel (L $k))
    }
    (C 'HInstall').Text = L 'un_title'
    Show-Page 'PageInstall'
    (C 'BtnMain').IsEnabled = $false; (C 'BtnMain').Content = L 'btn_uninstalling'; (C 'BtnCancel').IsEnabled = $false
    Start-Background $uninstallScript 'Deinstallation' | Out-Null
}

function Start-App {
    $script:exitCode = 2
    $exe = Join-Path $script:Root 'Voicitool.exe'
    if (Test-Path $exe) { Start-Process -FilePath $exe -WorkingDirectory $script:Root }
    else { Start-Process -FilePath (Join-Path $script:Root '.venv\Scripts\pythonw.exe') -ArgumentList "`"$(Join-Path $script:Root 'app\desktop.py')`"" -WorkingDirectory $script:Root }
    $win.Close()
}

(C 'BtnBrowse').Add_Click({
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.SelectedPath = (C 'TxtLocation').Text
    if ($dlg.ShowDialog() -eq 'OK') {
        $p = $dlg.SelectedPath
        if ((Split-Path $p -Leaf) -ne 'Voicitool') { $p = Join-Path $p 'Voicitool' }
        (C 'TxtLocation').Text = $p
    }
})
(C 'TxtLocation').Add_TextChanged({ Update-Free })
Update-Free

(C 'BtnMain').Add_Click({
    switch ($script:state) {
        'ready' { if ($Mode -eq 'uninstall') { Start-Uninstall } else { Start-Install } }
        'done' { Start-App }
        'error' { Start-Install }
        'finished' { $win.Close() }
    }
})
$cancelHandler = {
    if ($script:state -eq 'installing') {
        $r = [Windows.MessageBox]::Show($win, (L 'confirm_cancel'), 'Voicitool', 'YesNo', 'Question')
        if ($r -ne 'Yes') { return }
        $sync.Cancel = $true
        return
    }
    if ($script:state -eq 'uninstalling') { return }
    $win.Close()
}
(C 'BtnCancel').Add_Click($cancelHandler)
(C 'BtnClose').Add_Click($cancelHandler)
(C 'BtnLog').Add_Click({ Start-Process explorer.exe $sync.LogDir })

function Show-Done($ok, $title, $text) {
    Show-Page 'PageDone'
    if ($ok) { (C 'DoneBadge').Background = [Windows.Media.BrushConverter]::new().ConvertFrom('#3F7CF0'); (C 'DoneIcon').Text = '✓' }
    else { (C 'DoneBadge').Background = $brushes.fail; (C 'DoneIcon').Text = '!' }
    (C 'DoneTitle').Text = $title
    (C 'DoneText').Text = $text
}

function Check-Text($c) {
    $d = if ($c.Msg) { $T[$c.Msg] -f @($c.Args) } else { [string]$c.Raw }
    if ($c.Suffix) { $d += $T[$c.Suffix] }
    return $d
}

# Texte, die vom Stand abhängen (Hauptknopf, Installationsumfang, PC-Check)
function Update-StateTexts {
    switch ($script:state) {
        'checking' { (C 'BtnMain').Content = L 'btn_checking' }
        'blocked' { (C 'BtnMain').Content = L 'btn_blocked' }
        'ready' {
            if ($Mode -eq 'uninstall') { (C 'BtnMain').Content = L 'btn_uninstall' }
            else {
                $installed = (Test-Path (Join-Path $Root '.venv\Scripts\python.exe'))
                (C 'BtnMain').Content = if ($Mode -eq 'install' -or -not $installed) { L 'btn_install' } else { L 'btn_repair' }
                $size = if ($sync.Variant -eq 'cpu') { '~4 GB' } else { '~9 GB' }
                (C 'InstallInfo').Text = (L 'what_text') -f $size
            }
        }
    }
    foreach ($c in @($sync.Checks)) {
        if ($checkRows.ContainsKey($c.Key)) { $checkRows[$c.Key].Title.Text = $T.chk[$c.Key]; $checkRows[$c.Key].Detail.Text = Check-Text $c }
    }
}

# ------------------------------------------------------------------ Sprachwahl
$packsTouched = $false
$langClosedAt = [DateTime]::MinValue
$selBrush = [Windows.Media.BrushConverter]::new().ConvertFrom('#1E2A44')
function Build-LangList {
    $list = C 'LangList'; $list.Children.Clear()
    foreach ($code in $Langs.Keys) {
        $b = New-Object Windows.Controls.Button
        $b.Style = $win.FindResource('LangItem'); $b.Content = $Langs[$code]; $b.Tag = $code
        if ($code -eq $script:Lang) { $b.Background = $selBrush; $b.FontWeight = 'SemiBold' }
        $b.Add_Click({ param($s, $e) (C 'LangPopup').IsOpen = $false; Set-Lang $s.Tag })
        $list.Children.Add($b) | Out-Null
    }
}
function Set-Lang($code) {
    if ($code -eq $script:Lang) { return }
    $script:Lang = $code; $script:LangChosen = $true
    $script:T = Get-Texts $code; $sync.T = $script:T
    Apply-Texts; Update-StateTexts; Update-Free; Build-LangList
    # ohne Englisch als eigene Sprache braucht es das Paket »Alle Sprachen« (solange nicht selbst gewählt)
    if (-not $script:packsTouched) { (C 'ChkPackTurbo').IsChecked = ($code -ne 'en') }
}
(C 'BtnLang').Add_Click({
    $p = C 'LangPopup'
    # Klick auf den Knopf bei offener Liste: die Liste hat sich gerade selbst geschlossen, nicht wieder öffnen
    if (([DateTime]::Now - $script:langClosedAt).TotalMilliseconds -lt 300) { return }
    $p.HorizontalOffset = (C 'BtnLang').ActualWidth - 380
    $p.IsOpen = $true
})
(C 'LangPopup').Add_Closed({ $script:langClosedAt = [DateTime]::Now })
foreach ($n in 'ChkPackTurbo', 'ChkPackLarge') { (C $n).Add_Click({ $script:packsTouched = $true }) }
Build-LangList
if ($Mode -eq 'install') { (C 'ChkPackTurbo').IsChecked = ($Lang -ne 'en') }

# ------------------------------------------------------------------ Stiller Modus (nach Updates, vom Startfenster)
# Fenster bleibt unsichtbar, der Fortschritt geht über daten\setup_fortschritt.txt ans Startfenster.
# Bei Fehlern oder Hinweisen erscheint der Assistent; die Zeile „window“ schließt das Startfenster.
$exitCode = 1   # 0 = geprüft, Startfenster startet Voicitool; 2 = Voicitool wurde von hier gestartet
$progFile = Join-Path $Root 'daten\setup_fortschritt.txt'
$lastProg = ''
function Write-Prog($text) {
    if (-not $script:Quiet -or $text -eq $script:lastProg) { return }
    $script:lastProg = $text
    try { [IO.File]::WriteAllText($progFile, $text, (New-Object Text.UTF8Encoding $false)) } catch {}
}
function Reveal {
    if (-not $script:Quiet) { return }
    Write-Prog 'window'
    $script:Quiet = $false
    $area = [Windows.SystemParameters]::WorkArea
    $win.Left = $area.Left + ($area.Width - $win.Width) / 2; $win.Top = $area.Top + [math]::Max(0, ($area.Height - $win.Height) / 2)
    $win.ShowInTaskbar = $true
    $win.Activate() | Out-Null
}

$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(200)
$timer.Add_Tick({
    Check-Background
    if ($sync.Error -and $script:state -in @('checking', 'ready')) {
        $script:state = 'error'
        Show-Done $false (L 'impossible') $sync.Error
        (C 'BtnMain').IsEnabled = $false; (C 'BtnLog').Visibility = 'Visible'
        Reveal
        return
    }
    if ($script:state -eq 'checking') {
        foreach ($c in @($sync.Checks)) {
            if (-not $checkRows.ContainsKey($c.Key)) { $checkRows[$c.Key] = New-Row (C 'CheckList') $T.chk[$c.Key] }
            Set-Row $checkRows[$c.Key] $c.State (Check-Text $c)
        }
        if ($sync.ChecksDone) {
            if ($sync.CanInstall) {
                $script:state = 'ready'
                (C 'BtnMain').IsEnabled = $true
                Update-StateTexts
                if ($Auto) { Start-Install }   # nach einem Update ohne Klick weiter
            } else {
                $script:state = 'blocked'
                Update-StateTexts
                Reveal
            }
        }
    } elseif ($script:state -in @('installing', 'uninstalling')) {
        for ($i = 0; $i -lt $sync.Steps.Count; $i++) {
            $s = $sync.Steps[$i]
            $st = if ($s.State -eq 'skip') { 'ok' } else { $s.State }
            $detail = if ($s.State -eq 'skip') { if ($s.Key -eq 'shortcut') { L 'not_wanted' } else { L 'already' } } elseif ($s.State -eq 'ok') { L 'done' } else { '' }
            Set-Row $stepRows[$i] $st $detail
        }
        if ($sync.StepIndex -ge 0 -and $sync.StepIndex -lt $sync.Steps.Count) {
            (C 'StepTitle').Text = $sync.Steps[$sync.StepIndex].Title
            Write-Prog ("{0}`t{1}" -f ([double]$sync.Overall).ToString('0.00', [Globalization.CultureInfo]::InvariantCulture), $sync.Steps[$sync.StepIndex].Title)
        }
        (C 'StepBar').Value = $sync.StepPct
        (C 'StepDetail').Text = $sync.Detail
        (C 'OverallBar').Value = $sync.Overall
        (C 'OverallText').Text = '{0:0} %' -f ($sync.Overall * 100)
        if ($sync.Started) { (C 'Elapsed').Text = (L 'since') -f ((Get-Date) - $sync.Started).ToString('mm\:ss') }
        if ($sync.Done -and $script:state -eq 'uninstalling') {
            $script:state = 'finished'
            $kept = @()
            if ($sync.KeepProjects) { $kept += 'projekte, eingang, export' }
            if ($sync.KeepModels) { $kept += 'modelle' }
            $text = L 'un_done_text'
            if ($kept) { $text += "`n" + ((L 'un_kept') -f "$Root ($($kept -join ', '))") }
            if ($sync.Warning) { $text += "`n" + ((L 'hint') -f $sync.Warning) }
            if ($sync.Error) { Show-Done $false (L 'un_failed') $sync.Error } else { Show-Done $true (L 'un_done') $text }
            (C 'BtnMain').Style = $win.FindResource('Btn')
            (C 'BtnMain').IsEnabled = $true; (C 'BtnMain').Content = L 'btn_close'; (C 'BtnCancel').Visibility = 'Collapsed'
        } elseif ($sync.Done) {
            if ($sync.Error) {
                $script:state = 'error'
                $title = if ($sync.Error -eq (L 'aborted')) { L 'cancelled' } else { L 'failed' }
                Show-Done $false $title ($sync.Error + "`n`n" + (L 'failed_more'))
                (C 'BtnMain').IsEnabled = $true; (C 'BtnMain').Content = L 'btn_retry'
                $sync.Error = $null; $sync.Done = $false; $sync.Cancel = $false
                Reveal
            } else {
                $script:state = 'done'
                if ($script:Quiet -and -not $sync.Warning) { $script:exitCode = 0; $win.Close(); return }   # alles gut: Startfenster startet Voicitool
                $gpuText = $T.gpu_text[$sync.Variant]
                $text = (L 'ready_text') -f $gpuText
                if ($sync.Desktop) { $text += "`n" + (L 'ready_desktop') }
                if ($sync.Warning) { $text += "`n`n" + ((L 'hint') -f $sync.Warning) }
                Show-Done $true (L 'ready') $text
                (C 'BtnMain').IsEnabled = $true; (C 'BtnMain').Content = L 'btn_start'
                (C 'BtnCancel').Content = L 'btn_close'
                $autoStart = ($Auto -and -not $sync.Warning) -or ($Mode -eq 'install' -and $sync.Launch)
                Reveal
                if ($autoStart -and -not $env:VOICITOOL_SETUP_SNAPSHOT) {
                    $go = New-Object Windows.Threading.DispatcherTimer
                    $go.Interval = [TimeSpan]::FromMilliseconds(1200)
                    $go.Add_Tick({ $go.Stop(); Start-App })
                    $go.Start()
                }
                if ($Mode -eq 'install') {   # Entpack-Ordner der exe aufräumen
                    try { if ($SourceRoot -like "$env:TEMP*") { Remove-Item -LiteralPath $SourceRoot -Recurse -Force -ErrorAction SilentlyContinue } } catch {}
                }
            }
        }
    }
})

if ($Mode -eq 'uninstall') {
    Show-Page 'PageUninstall'
    $state = 'ready'
    (C 'BtnMain').Style = $win.FindResource('Danger')
    (C 'BtnMain').IsEnabled = $true; (C 'BtnMain').Content = L 'btn_uninstall'
} else {
    Start-Background $checkScript 'PC-Check' | Out-Null
}
$timer.Start()
if ($env:VOICITOOL_SETUP_SNAPSHOT) {
    # nur für Tests: Fenster außerhalb des Bildschirms, am Ende (bzw. nach dem PC-Check) als Bild speichern
    $win.WindowStartupLocation = 'Manual'; $win.Left = -4000; $win.Top = 50; $win.ShowActivated = $false
    $snap = New-Object Windows.Threading.DispatcherTimer
    $snap.Interval = [TimeSpan]::FromMilliseconds(500)
    $snap.Add_Tick({
        if ($env:VOICITOOL_SETUP_AUTORUN -and $script:state -eq 'ready') {
            if ($Mode -eq 'uninstall') { Start-Uninstall } else { Start-Install }
            return
        }
        $final = $script:state -in @('done', 'error', 'finished', 'blocked')
        if ($final -or (-not $env:VOICITOOL_SETUP_AUTORUN -and $script:state -eq 'ready')) {
            $snap.Stop()
            if ($env:VOICITOOL_SETUP_SWITCH) { Set-Lang $env:VOICITOOL_SETUP_SWITCH }   # Sprachwechsel testen
            $win.UpdateLayout()
            $rtb = New-Object Windows.Media.Imaging.RenderTargetBitmap([int]$win.ActualWidth, [int]$win.ActualHeight, 96, 96, [Windows.Media.PixelFormats]::Pbgra32)
            $rtb.Render($win)
            $enc = New-Object Windows.Media.Imaging.PngBitmapEncoder
            $enc.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($rtb))
            $fs = [IO.File]::Create($env:VOICITOOL_SETUP_SNAPSHOT); $enc.Save($fs); $fs.Close()
            $win.Close()
        }
    })
    $snap.Start()
}
if ($Quiet) { $win.WindowStartupLocation = 'Manual'; $win.Left = -4000; $win.Top = 50; $win.ShowActivated = $false; $win.ShowInTaskbar = $false }
$win.Add_Closed({ $timer.Stop(); if ($sync.Proc) { try { & taskkill /PID $sync.Proc.Id /T /F | Out-Null } catch {} } })
$win.ShowDialog() | Out-Null
exit $exitCode
