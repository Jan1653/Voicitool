// Voicitool.exe: die einzige Datei zum Weitergeben.
//
// Ablauf bei jedem Start:
//   1. Installation suchen: neben der exe, im Windows-Eintrag (Apps & Features) oder am Standardort
//   2. keine gefunden: App in einen Temp-Ordner auspacken und den Installations-Assistenten öffnen
//      (Speicherort, Sprachpakete, Verknüpfungen wählen)
//   3. sonst: mitgelieferte App auspacken, falls neuer; auf GitHub nach einer höheren Build-Nummer schauen
//      (app/version.json im Zweig main). Ist dort „launcher“ höher, kommt auch eine neue exe aus dem neuesten Release.
//      Klappt das Update nicht, startet die bisherige Version.
//   4. passt die Build-Nummer nicht zur letzten Einrichtung: Installation still prüfen (Fortschritt im Startfenster)
//   5. Voicitool starten; das Startfenster bleibt, bis das App-Fenster da ist, und holt es nach vorne
//
// Gebaut mit dem csc.exe von .NET Framework 4 (in jedem Windows 10/11 vorhanden), deshalb nur C# 5.
// Argumente: --setup (Assistent erzwingen), --uninstall (Deinstallation), --no-update (GitHub nicht fragen),
//            --after-exit (aus der App „Jetzt aktualisieren“: erst warten, bis Voicitool beendet ist)
// Testhilfen: VOICITOOL_HOME (Installationsordner), VOICITOOL_UPDATE_BASE (eigener Update-Server), VOICITOOL_PORT

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace Voicitool
{
    static class Program
    {
        // Ordner mit Nutzerdaten oder großen Downloads: werden beim Aktualisieren nie angefasst
        static readonly string[] Protected = { "projekte", "eingang", "export", "modelle", "daten", ".venv", "tools" };
        static readonly int AppPort = EnvPort();   // wie config.PORT (Tests: VOICITOOL_PORT)
        static int EnvPort()
        {
            int p;
            return int.TryParse(Environment.GetEnvironmentVariable("VOICITOOL_PORT"), out p) ? p : 7863;
        }
        const string RegPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Voicitool";
        // Sprache: eigene Wahl aus den Voicitool-Einstellungen, sonst die von Windows, sonst Englisch.
        // Übersetzungen (außer Deutsch) als eingebettete Tabelle texts.txt, Schlüssel ist der englische Text.
        static string lang = "en";
        static readonly Dictionary<string, string> texts = new Dictionary<string, string>();
        public static string L(string de, string en)
        {
            if (lang == "de") return de;
            string s;
            return texts.TryGetValue(en, out s) ? s : en;
        }

        static void InitLang()
        {
            string want = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName.ToLowerInvariant();
            try
            {
                string root = FindInstall(Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location));
                string settings = root != null ? Path.Combine(root, "daten", "einstellungen.json") : null;
                if (settings != null && File.Exists(settings))
                {
                    var m = Regex.Match(File.ReadAllText(settings), "\"ui_lang\"\\s*:\\s*\"([a-z]{2})\"");
                    if (m.Success) want = m.Groups[1].Value;
                }
            }
            catch { }
            if (want == "de" || want == "en") { lang = want; return; }
            byte[] raw = ReadEmbedded("texts.txt");
            if (raw == null) return;
            foreach (string line in System.Text.Encoding.UTF8.GetString(raw).Split('\n'))
            {
                string[] p = line.Split('\t');
                if (p.Length == 3 && p[0] == want) texts[Unescape(p[1])] = Unescape(p[2]);
            }
            if (texts.Count > 0) lang = want;
        }

        static string Unescape(string s)
        {
            var sb = new System.Text.StringBuilder();
            for (int i = 0; i < s.Length; i++)
            {
                if (s[i] == '\\' && i + 1 < s.Length) { i++; sb.Append(s[i] == 'n' ? '\n' : s[i]); }
                else sb.Append(s[i]);
            }
            return sb.ToString();
        }

        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

        [STAThread]
        static int Main(string[] args)
        {
            try { SetProcessDPIAware(); } catch { }
            InitLang();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072; // TLS 1.2

            var splash = new Splash();
            int exit = 0;
            var worker = new Thread(delegate ()
            {
                try { exit = Run(splash, args); }
                catch (Exception e)
                {
                    splash.Fail(L("Voicitool konnte nicht gestartet werden:\n\n", "Voicitool could not be started:\n\n") + e.Message);
                    exit = 1;
                }
                splash.CloseSafe();
            });
            worker.IsBackground = true;
            splash.Shown += delegate { worker.Start(); };
            Application.Run(splash);
            return exit;
        }

        static int Run(Splash ui, string[] args)
        {
            bool forceSetup = Array.IndexOf(args, "--setup") >= 0;
            bool noUpdate = Array.IndexOf(args, "--no-update") >= 0;
            bool uninstall = Array.IndexOf(args, "--uninstall") >= 0;
            bool afterExit = Array.IndexOf(args, "--after-exit") >= 0;
            string exePath = Assembly.GetExecutingAssembly().Location;
            string exeDir = Path.GetDirectoryName(exePath);
            string root = FindInstall(exeDir);

            if (uninstall)
            {
                if (root == null) throw new Exception(L("Keine Voicitool-Installation gefunden.", "No Voicitool installation found."));
                ui.Status(L("Deinstallation wird geöffnet …", "Opening uninstaller …"), -1);
                StartUninstaller(root);
                return 0;
            }

            byte[] embedded = ReadEmbedded("app.zip");
            if (root == null)
            {
                if (embedded == null) throw new Exception(L("In dieser Datei fehlt die App. Bitte Voicitool.exe neu herunterladen.",
                                                             "The app is missing from this file. Please download Voicitool.exe again."));
                ui.Status(L("Installation wird vorbereitet …", "Preparing installation …"), -1);
                StartFreshInstall(embedded, exePath);
                return 0;
            }

            if (afterExit)
            {
                // aus der App gestartet („Jetzt aktualisieren“): warten, bis sie sich beendet hat (höchstens 30 s)
                ui.Status(L("Voicitool wird beendet …", "Closing Voicitool …"), -1);
                for (int i = 0; i < 150 && PortOpen(AppPort); i++) Thread.Sleep(200);
                Thread.Sleep(500);
            }

            ui.Status(L("Voicitool wird vorbereitet …", "Getting Voicitool ready …"), -1);
            CleanupOldExe(root);
            string localVersion = Path.Combine(root, "app", "version.json");
            int localBuild = ReadBuild(File.Exists(localVersion) ? File.ReadAllText(localVersion) : null);

            // 1) mitgelieferte App auspacken, falls neuer als die installierte
            int embeddedBuild = embedded != null ? ReadBuild(ReadZipText(embedded, "app/version.json")) : -1;
            if (embedded != null && embeddedBuild > localBuild)
            {
                if (localBuild >= 0 && PortOpen(AppPort))
                    throw new Exception(L("Diese Voicitool.exe bringt eine neuere Version mit.\nBitte schließe zuerst das offene Voicitool-Fenster und starte dann erneut.",
                                          "This Voicitool.exe brings a newer version.\nPlease close the open Voicitool window first, then start again."));
                ui.Status(L("Voicitool wird aktualisiert …", "Updating Voicitool …"), -1);
                InstallZip(embedded, root);
                localBuild = embeddedBuild;
            }

            // die exe selbst im Installationsordner ablegen (für Verknüpfungen und spätere Starts)
            string installedExe = Path.Combine(root, "Voicitool.exe");
            if (!SamePath(exePath, installedExe) && embeddedBuild >= localBuild)
            {
                try { File.Copy(exePath, installedExe, true); } catch { }
            }

            // 2) Update von GitHub
            bool appRunning = PortOpen(AppPort);
            // in den Einstellungen abschaltbar: „Beim Start nach Updates suchen“
            string settingsPath = Path.Combine(root, "daten", "einstellungen.json");
            if (File.Exists(settingsPath) && Regex.IsMatch(File.ReadAllText(settingsPath), "\"check_updates\"\\s*:\\s*false"))
                noUpdate = true;
            if (!noUpdate && !appRunning && File.Exists(localVersion))
            {
                // ein fehlgeschlagenes Update darf den Start nie verhindern: dann die bisherige Version starten
                try { localBuild = UpdateFromGitHub(ui, root, localVersion, localBuild); }
                catch (Exception e)
                {
                    Log(root, "Update fehlgeschlagen: " + e.Message);
                    ui.Status(L("Update hat nicht geklappt, Voicitool startet in der bisherigen Version …",
                                "Update failed, starting the current version …"), -1);
                    Thread.Sleep(1800);
                }
            }

            // 3) Einrichtung nötig? (unvollständig, andere Build-Nummer oder ausdrücklich gewünscht)
            string pythonw = Path.Combine(root, ".venv", "Scripts", "pythonw.exe");
            string setupJson = Path.Combine(root, "daten", "setup.json");
            int setupBuild = File.Exists(setupJson) ? ReadBuild(File.ReadAllText(setupJson)) : -1;
            bool installed = File.Exists(pythonw) && File.Exists(setupJson);
            string script = Path.Combine(root, "app", "setup", "setup.ps1");
            if (forceSetup || !installed)
            {
                ui.Status(L("Einrichtung wird gestartet …", "Starting setup …"), -1);
                StartSetup(script, "", root);   // der Nutzer entscheidet im Assistenten
                Thread.Sleep(1200);
                return 0;
            }
            if (setupBuild != localBuild)
            {
                // nach einem Update: Installation still prüfen, Fortschritt hier im Startfenster.
                // Nur bei Fehlern oder Hinweisen erscheint der Assistent.
                ui.Status(L("Neue Version wird eingerichtet …", "Setting up the new version …"), -1);
                if (RunSetupQuiet(ui, script, root) != 0) return 0;
            }

            // 4) starten und warten, bis das Fenster da ist (dann nach vorne holen)
            ui.Status(L("Voicitool startet …", "Starting Voicitool …"), -1);
            StartApp(root, pythonw);
            return 0;
        }

        // ------------------------------------------------------------------ Updates

        // Version der exe selbst (aus version.json „launcher“, beim Bauen eingetragen)
        static int LauncherVersion() { return Assembly.GetExecutingAssembly().GetName().Version.Major; }

        static int UpdateFromGitHub(Splash ui, string root, string localVersion, int localBuild)
        {
            string repo = ReadRepo(File.ReadAllText(localVersion));
            string baseUrl = Environment.GetEnvironmentVariable("VOICITOOL_UPDATE_BASE");
            string versionUrl, zipUrl, exeUrl;
            if (!string.IsNullOrEmpty(baseUrl))
            {
                baseUrl = baseUrl.TrimEnd('/');
                versionUrl = baseUrl + "/version.json";
                zipUrl = baseUrl + "/app.zip";
                exeUrl = baseUrl + "/Voicitool.exe";
            }
            else if (!string.IsNullOrEmpty(repo))
            {
                versionUrl = "https://raw.githubusercontent.com/" + repo + "/main/app/version.json";
                zipUrl = "https://codeload.github.com/" + repo + "/zip/refs/heads/main";
                exeUrl = "https://github.com/" + repo + "/releases/latest/download/Voicitool.exe";
            }
            else return localBuild;

            ui.Status(L("Suche nach Updates …", "Checking for updates …"), -1);
            string remote;
            try { remote = Download(versionUrl, 6000); }
            catch { return localBuild; }   // offline oder GitHub nicht erreichbar: einfach starten

            int remoteLauncher = ReadInt(remote, "launcher");
            if (remoteLauncher > LauncherVersion()) TrySelfUpdate(ui, root, exeUrl);

            int remoteBuild = ReadBuild(remote);
            if (remoteBuild <= localBuild) return localBuild;
            ui.Status(string.Format(L("Update auf Build {0} wird geladen …", "Downloading update to build {0} …"), remoteBuild), 0);
            byte[] zip = DownloadBytes(zipUrl, delegate (double p) { ui.Status(null, p); });
            int zipBuild = ReadBuild(ReadZipText(zip, "app/version.json"));
            if (zipBuild <= localBuild)
            {
                Log(root, "Update übersprungen: Archiv hat Build " + zipBuild + ", installiert ist " + localBuild);
                return localBuild;   // GitHub liefert noch den alten Stand (Zwischenspeicher), beim nächsten Start erneut
            }
            ui.Status(L("Update wird installiert …", "Installing update …"), -1);
            InstallZip(zip, root);
            Log(root, "Update installiert: Build " + localBuild + " -> " + zipBuild);
            return ReadBuild(File.ReadAllText(localVersion));
        }

        // Neue Voicitool.exe aus dem neuesten Release holen. Die laufende exe lässt sich nicht überschreiben,
        // aber umbenennen: alte wird zu Voicitool.old.exe (beim nächsten Start gelöscht), neue gilt ab dem nächsten Start.
        static void TrySelfUpdate(Splash ui, string root, string exeUrl)
        {
            string target = Path.Combine(root, "Voicitool.exe");
            string fresh = Path.Combine(root, "Voicitool.new.exe");
            try
            {
                ui.Status(L("Voicitool.exe wird aktualisiert …", "Updating Voicitool.exe …"), 0);
                byte[] data = DownloadBytes(exeUrl, delegate (double p) { ui.Status(null, p); });
                if (data.Length < 100000 || data[0] != 'M' || data[1] != 'Z') throw new Exception("keine gültige exe");
                File.WriteAllBytes(fresh, data);
                // Release noch nicht aktualisiert? Dann nichts tauschen (sonst Download bei jedem Start)
                if (AssemblyName.GetAssemblyName(fresh).Version.Major <= LauncherVersion())
                {
                    File.Delete(fresh);
                    return;
                }
                string old = Path.Combine(root, "Voicitool.old.exe");
                if (File.Exists(old)) File.Delete(old);
                if (File.Exists(target)) File.Move(target, old);
                File.Move(fresh, target);
                Log(root, "Voicitool.exe aktualisiert");
            }
            catch (Exception e)
            {
                Log(root, "Voicitool.exe nicht aktualisiert: " + e.Message);
                try { if (File.Exists(fresh)) File.Delete(fresh); } catch { }
            }
        }

        static void CleanupOldExe(string root)
        {
            try { string old = Path.Combine(root, "Voicitool.old.exe"); if (File.Exists(old)) File.Delete(old); } catch { }
        }

        // Prüfung nach einem Update ohne Fenster; Fortschritt kommt über daten\setup_fortschritt.txt.
        // 0 = alles gut, Voicitool starten. Sonst zeigt der Assistent selbst, was los ist.
        static int RunSetupQuiet(Splash ui, string script, string root)
        {
            string progress = Path.Combine(root, "daten", "setup_fortschritt.txt");
            try { if (File.Exists(progress)) File.Delete(progress); } catch { }
            Process p = StartSetup(script, "-Auto -Quiet", root);
            string last = null;
            while (!p.WaitForExit(250))
            {
                string s = ReadShared(progress);
                if (s == null || s == last) continue;
                last = s;
                if (s.StartsWith("window")) return 1;   // Assistent ist sichtbar geworden (Fehler oder Hinweis)
                string[] parts = s.Split('\t');
                double pct;
                if (parts.Length == 2 && double.TryParse(parts[0], NumberStyles.Float, CultureInfo.InvariantCulture, out pct))
                    ui.Status(parts[1], pct);
            }
            return p.ExitCode;
        }

        static string ReadShared(string path)
        {
            try
            {
                using (var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (var r = new StreamReader(fs)) return r.ReadToEnd().Trim();
            }
            catch { return null; }
        }

        static void Log(string root, string text)
        {
            try
            {
                string dir = Path.Combine(root, "daten", "logs");
                Directory.CreateDirectory(dir);
                File.AppendAllText(Path.Combine(dir, "launcher.log"), DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + text + Environment.NewLine);
            }
            catch { }
        }

        // ------------------------------------------------------------------ App starten

        [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int pid);
        [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hWnd);
        [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hWnd, int cmd);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hWnd);
        [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder s, int n);
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
        delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
        [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr lParam);

        // sichtbare Fenster mit dem Titel „Voicitool“ (nicht dieses Startfenster)
        static List<IntPtr> AppWindows()
        {
            var list = new List<IntPtr>();
            uint me = (uint)Process.GetCurrentProcess().Id;
            EnumWindows(delegate (IntPtr h, IntPtr l)
            {
                if (!IsWindowVisible(h)) return true;
                var sb = new System.Text.StringBuilder(64);
                GetWindowText(h, sb, 64);
                uint pid;
                GetWindowThreadProcessId(h, out pid);
                if (pid != me && sb.ToString() == "Voicitool") list.Add(h);
                return true;
            }, IntPtr.Zero);
            return list;
        }

        // Das Startfenster bleibt, bis das App-Fenster erscheint (Python und die Oberfläche brauchen einige Sekunden).
        // Sonst wirkt es, als würde nichts passieren, und Windows öffnet das Fenster oft im Hintergrund.
        static void StartApp(string root, string pythonw)
        {
            var before = AppWindows();
            AllowSetForegroundWindow(-1);   // ASFW_ANY: das neue Fenster darf sich nach vorne holen
            var psi = new ProcessStartInfo(pythonw, "\"" + Path.Combine(root, "app", "desktop.py") + "\"");
            psi.WorkingDirectory = root;
            psi.UseShellExecute = false;
            Process p = Process.Start(psi);
            var sw = Stopwatch.StartNew();
            while (sw.Elapsed.TotalSeconds < 90)
            {
                foreach (IntPtr h in AppWindows())
                {
                    if (before.Contains(h)) continue;
                    if (IsIconic(h)) ShowWindow(h, 9);   // SW_RESTORE
                    SetForegroundWindow(h);
                    Thread.Sleep(300);
                    return;
                }
                if (p.HasExited)
                {
                    if (p.ExitCode == 0) return;
                    throw new Exception(L("Voicitool hat sich beim Start beendet. Details stehen in daten\\logs\\voicitool.log.",
                                          "Voicitool closed while starting. Details are in daten\\logs\\voicitool.log."));
                }
                Thread.Sleep(200);
            }
        }

        // ------------------------------------------------------------------ Installation finden

        static bool HasApp(string dir)
        {
            return !string.IsNullOrEmpty(dir) && (File.Exists(Path.Combine(dir, "app", "version.json")) ||
                                                  File.Exists(Path.Combine(dir, "app", "desktop.py")));
        }

        static string FindInstall(string exeDir)
        {
            string env = Environment.GetEnvironmentVariable("VOICITOOL_HOME");
            if (!string.IsNullOrEmpty(env)) return HasApp(env) ? Path.GetFullPath(env) : null;
            if (HasApp(exeDir)) return exeDir;   // exe liegt in einer (auch älteren) Installation
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(RegPath))
                {
                    string loc = key != null ? key.GetValue("InstallLocation") as string : null;
                    if (HasApp(loc)) return loc;
                }
            }
            catch { }
            string def = DefaultLocation();
            return HasApp(def) ? def : null;
        }

        static string DefaultLocation()
        {
            return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Voicitool");
        }

        // Neuinstallation: App in einen Temp-Ordner auspacken, der Assistent kopiert sie an den gewählten Ort
        static void StartFreshInstall(byte[] embedded, string exePath)
        {
            string temp = Path.Combine(Path.GetTempPath(), "Voicitool-Setup");
            if (Directory.Exists(temp)) Directory.Delete(temp, true);
            Directory.CreateDirectory(temp);
            using (var za = new ZipArchive(new MemoryStream(embedded), ZipArchiveMode.Read))
            {
                foreach (var e in za.Entries)
                {
                    string rel = Strip(za, e.FullName);
                    if (rel.Length == 0 || rel.EndsWith("/")) continue;
                    string target = Path.Combine(temp, rel.Replace('/', Path.DirectorySeparatorChar));
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    e.ExtractToFile(target, true);
                }
            }
            string env = Environment.GetEnvironmentVariable("VOICITOOL_HOME");
            string targetArg = string.IsNullOrEmpty(env) ? "" : " -Target \"" + env + "\"";
            StartSetup(Path.Combine(temp, "app", "setup", "setup.ps1"), "-Install -Exe \"" + exePath + "\"" + targetArg, temp);
            Thread.Sleep(1200);
        }

        // Deinstallation aus einer Kopie starten, damit sich der Programmordner vollständig löschen lässt
        static void StartUninstaller(string root)
        {
            string temp = Path.Combine(Path.GetTempPath(), "Voicitool-Uninstall");
            if (Directory.Exists(temp)) Directory.Delete(temp, true);
            Directory.CreateDirectory(Path.Combine(temp, "app", "setup"));
            Directory.CreateDirectory(Path.Combine(temp, "app", "static"));
            File.Copy(Path.Combine(root, "app", "setup", "setup.ps1"), Path.Combine(temp, "app", "setup", "setup.ps1"), true);
            string icon = Path.Combine(root, "app", "static", "icon.png");
            if (File.Exists(icon)) File.Copy(icon, Path.Combine(temp, "app", "static", "icon.png"), true);
            StartSetup(Path.Combine(temp, "app", "setup", "setup.ps1"), "-Uninstall -Target \"" + root + "\"", temp);
            Thread.Sleep(1200);
        }

        static Process StartSetup(string script, string extra, string workDir)
        {
            string ps = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
            var psi = new ProcessStartInfo(ps, "-NoProfile -ExecutionPolicy Bypass -STA -File \"" + script + "\" " + extra);
            psi.WorkingDirectory = workDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            AllowSetForegroundWindow(-1);   // Assistent darf nach vorne
            return Process.Start(psi);
        }

        // ------------------------------------------------------------------ Hilfen

        static bool SamePath(string a, string b)
        {
            return string.Equals(Path.GetFullPath(a).TrimEnd('\\'), Path.GetFullPath(b).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
        }

        static int ReadBuild(string json) { return ReadInt(json, "build"); }

        static int ReadInt(string json, string key)
        {
            if (json == null) return -1;
            var m = Regex.Match(json, "\"" + key + "\"\\s*:\\s*(\\d+)");
            return m.Success ? int.Parse(m.Groups[1].Value) : -1;
        }

        static string ReadRepo(string json)
        {
            var m = Regex.Match(json ?? "", "\"repo\"\\s*:\\s*\"([^\"]*)\"");
            return m.Success ? m.Groups[1].Value.Trim() : "";
        }

        static byte[] ReadEmbedded(string name)
        {
            using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
            {
                if (s == null) return null;
                var ms = new MemoryStream();
                s.CopyTo(ms);
                return ms.ToArray();
            }
        }

        static string ReadZipText(byte[] zip, string path)
        {
            using (var za = new ZipArchive(new MemoryStream(zip), ZipArchiveMode.Read))
            {
                foreach (var e in za.Entries)
                {
                    if (Strip(za, e.FullName).Equals(path, StringComparison.OrdinalIgnoreCase))
                        using (var r = new StreamReader(e.Open())) return r.ReadToEnd();
                }
            }
            return null;
        }

        // GitHub-Archive haben einen gemeinsamen Oberordner („Voicitool-main/…“), den entfernen
        static string Strip(ZipArchive za, string name)
        {
            name = name.Replace('\\', '/');
            string prefix = CommonPrefix(za);
            return prefix.Length > 0 && name.StartsWith(prefix) ? name.Substring(prefix.Length) : name;
        }

        static string cachedPrefix;
        static ZipArchive cachedFor;
        static string CommonPrefix(ZipArchive za)
        {
            if (cachedFor == za) return cachedPrefix;
            string prefix = null;
            bool hasAppAtTop = false;
            foreach (var e in za.Entries)
            {
                string n = e.FullName.Replace('\\', '/');
                if (n.StartsWith("app/")) hasAppAtTop = true;
                int slash = n.IndexOf('/');
                string top = slash > 0 ? n.Substring(0, slash + 1) : "";
                if (prefix == null) prefix = top;
                else if (prefix != top) prefix = "";
            }
            cachedFor = za;
            cachedPrefix = (hasAppAtTop || prefix == null) ? "" : prefix;
            return cachedPrefix;
        }

        // App-Dateien ersetzen: erst in einen Nebenordner auspacken, dann austauschen (nie ein halber Stand)
        static void InstallZip(byte[] zip, string root)
        {
            string staging = Path.Combine(root, "_update");
            if (Directory.Exists(staging)) Directory.Delete(staging, true);
            Directory.CreateDirectory(staging);
            bool hasApp = false;
            using (var za = new ZipArchive(new MemoryStream(zip), ZipArchiveMode.Read))
            {
                foreach (var e in za.Entries)
                {
                    string rel = Strip(za, e.FullName);
                    if (rel.Length == 0 || rel.EndsWith("/")) continue;
                    string top = rel.Split('/')[0];
                    if (Array.IndexOf(Protected, top.ToLowerInvariant()) >= 0) continue;
                    if (rel.EndsWith("Voicitool.exe", StringComparison.OrdinalIgnoreCase)) continue;
                    if (top == "app") hasApp = true;
                    string target = Path.Combine(staging, rel.Replace('/', Path.DirectorySeparatorChar));
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    e.ExtractToFile(target, true);
                }
            }
            if (!hasApp) { Directory.Delete(staging, true); throw new Exception(L("Das Update enthält keine App-Dateien.", "The update contains no app files.")); }

            string app = Path.Combine(root, "app");
            string old = Path.Combine(root, "_app_alt");
            if (Directory.Exists(old)) Directory.Delete(old, true);
            if (Directory.Exists(app)) Directory.Move(app, old);
            try { Directory.Move(Path.Combine(staging, "app"), app); }
            catch
            {
                if (!Directory.Exists(app) && Directory.Exists(old)) Directory.Move(old, app);   // alten Stand zurück
                throw;
            }
            foreach (var f in Directory.GetFiles(staging))   // README, LICENSE usw.
                File.Copy(f, Path.Combine(root, Path.GetFileName(f)), true);
            Directory.Delete(staging, true);
            try { if (Directory.Exists(old)) Directory.Delete(old, true); } catch { }
            // alte Startdateien aus der Zeit vor Voicitool.exe entfernen (die exe ersetzt sie)
            foreach (var legacy in new[] { "Voicitool.bat", "Einrichtung.bat", "Teilen-Paket erstellen.bat", "Voicitool-zum-Teilen.zip" })
            {
                try { File.Delete(Path.Combine(root, legacy)); } catch { }
            }
        }

        static string Download(string url, int timeoutMs)
        {
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = timeoutMs;
            req.UserAgent = "Voicitool-Updater";
            using (var resp = req.GetResponse())
            using (var r = new StreamReader(resp.GetResponseStream())) return r.ReadToEnd();
        }

        static byte[] DownloadBytes(string url, Action<double> progress)
        {
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = 30000;
            req.UserAgent = "Voicitool-Updater";
            using (var resp = req.GetResponse())
            using (var s = resp.GetResponseStream())
            {
                long total = resp.ContentLength;
                var ms = new MemoryStream();
                var buf = new byte[65536];
                int n;
                while ((n = s.Read(buf, 0, buf.Length)) > 0)
                {
                    ms.Write(buf, 0, n);
                    if (total > 0 && progress != null) progress((double)ms.Length / total);
                }
                return ms.ToArray();
            }
        }

        static bool PortOpen(int port)
        {
            try
            {
                using (var c = new TcpClient())
                {
                    var r = c.BeginConnect("127.0.0.1", port, null, null);
                    return r.AsyncWaitHandle.WaitOne(300) && c.Connected;
                }
            }
            catch { return false; }
        }
    }

    // ---------------------------------------------------------------------- kleines Startfenster
    class Splash : Form
    {
        readonly Label title = new Label();
        readonly Label status = new Label();
        readonly Label sub = new Label();
        double progress = -1;
        double pulse;
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        static readonly Color Bg = Color.FromArgb(14, 16, 22);
        static readonly Color Line = Color.FromArgb(44, 49, 60);

        public Splash()
        {
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Bg;
            ForeColor = Color.FromArgb(231, 233, 238);
            ShowInTaskbar = true;
            Text = "Voicitool";
            try { Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location); } catch { }
            float s = DeviceDpi() / 96f;
            ClientSize = new Size((int)(460 * s), (int)(170 * s));
            DoubleBuffered = true;

            title.Text = "Voicitool";
            title.Font = new Font("Segoe UI", 20f, FontStyle.Bold);
            title.AutoSize = true;
            title.Location = new Point((int)(96 * s), (int)(34 * s));
            sub.Text = Program.L("Dub Packs für Choicer Voicer", "Dub packs for Choicer Voicer");
            sub.Font = new Font("Segoe UI", 9f);
            sub.ForeColor = Color.FromArgb(141, 148, 163);
            sub.AutoSize = true;
            sub.Location = new Point((int)(99 * s), (int)(74 * s));
            status.Text = "";
            status.Font = new Font("Segoe UI", 10f);
            status.ForeColor = Color.FromArgb(141, 148, 163);
            status.AutoSize = false;
            status.Location = new Point((int)(28 * s), (int)(108 * s));
            status.Size = new Size((int)(404 * s), (int)(22 * s));
            Controls.Add(title);
            Controls.Add(sub);
            Controls.Add(status);

            timer.Interval = 30;
            timer.Tick += delegate { pulse = (pulse + 0.018) % 1.0; Invalidate(); };
            timer.Start();
        }

        static float DeviceDpi()
        {
            using (var g = Graphics.FromHwnd(IntPtr.Zero)) return g.DpiX;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            float s = DeviceDpi() / 96f;
            using (var pen = new Pen(Line)) g.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
            // Logo: abgerundetes Verlaufsquadrat mit Mikrofon
            var logo = new RectangleF(28 * s, 32 * s, 52 * s, 52 * s);
            using (var path = Rounded(logo, 12 * s))
            using (var br = new LinearGradientBrush(logo, Color.FromArgb(63, 124, 240), Color.FromArgb(168, 85, 247), 45f))
                g.FillPath(br, path);
            using (var w = new SolidBrush(Color.White))
            {
                var mic = new RectangleF(logo.X + 19 * s, logo.Y + 11 * s, 14 * s, 21 * s);
                using (var p = Rounded(mic, 7 * s)) g.FillPath(w, p);
                using (var pen = new Pen(Color.White, 2.6f * s))
                {
                    g.DrawArc(pen, logo.X + 14 * s, logo.Y + 17 * s, 24 * s, 21 * s, 0, 180);
                    g.DrawLine(pen, logo.X + 26 * s, logo.Y + 38 * s, logo.X + 26 * s, logo.Y + 43 * s);
                }
            }
            var bar = new RectangleF(28 * s, 138 * s, 404 * s, 6 * s);
            using (var track = Rounded(bar, 3 * s))
            using (var bg = new SolidBrush(Color.FromArgb(32, 36, 45))) g.FillPath(bg, track);
            RectangleF fill;
            if (progress >= 0) fill = new RectangleF(bar.X, bar.Y, Math.Max(6 * s, bar.Width * (float)progress), bar.Height);
            else fill = new RectangleF(bar.X + (bar.Width - 110 * s) * (float)(0.5 - 0.5 * Math.Cos(pulse * 2 * Math.PI)), bar.Y, 110 * s, bar.Height);
            using (var fp = Rounded(fill, 3 * s))
            using (var br = new LinearGradientBrush(bar, Color.FromArgb(63, 124, 240), Color.FromArgb(168, 85, 247), 0f))
                g.FillPath(br, fp);
        }

        static GraphicsPath Rounded(RectangleF r, float rad)
        {
            var p = new GraphicsPath();
            float d = Math.Min(rad * 2, Math.Min(r.Width, r.Height));
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        // Text null = nur Fortschritt ändern; Fortschritt < 0 = unbestimmt (Lauflicht)
        public void Status(string text, double p)
        {
            if (IsDisposed) return;
            try
            {
                BeginInvoke((MethodInvoker)delegate
                {
                    if (text != null) status.Text = text;
                    progress = p;
                    Invalidate();
                });
            }
            catch { }
        }

        public void Fail(string message)
        {
            try { Invoke((MethodInvoker)delegate { MessageBox.Show(this, message, "Voicitool", MessageBoxButtons.OK, MessageBoxIcon.Error); }); }
            catch { MessageBox.Show(message, "Voicitool"); }
        }

        public void CloseSafe()
        {
            try { BeginInvoke((MethodInvoker)delegate { Close(); }); } catch { }
        }
    }
}
