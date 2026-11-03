// Scorpi-Setup.exe: installs Scorpi for the current user, with nothing else to do first.
//   1. unpacks the app (built into this exe as app.zip) to %LOCALAPPDATA%\Programs\Scorpi (no admin needed)
//   2. gets a private Python (python.org's build, from NuGet) into runtime\python, and makes .venv from it
//   3. installs the packages from requirements.txt one by one (a failure only turns that tool off)
//   4. Start menu and desktop shortcuts, optionally start with Windows, and an entry in Settings → Apps to remove it
// Running it again updates Scorpi in place and keeps chats, settings and files (data\).
//   Scorpi-Setup.exe --uninstall      what Settings → Apps runs (from the copy kept as uninstall.exe)
//   Scorpi-Setup.exe --quiet [--dir <folder>] [--no-desktop] [--startup]   no window (exit 0 done, 3 done without some
//                                     extras, 1 failed; install.log in the folder says why)
//   Scorpi-Setup.exe --update         what the app runs when scorpi.dev has a newer version: no window, into the installed
//                                     folder, keeps the shortcuts as they are, skips pip when requirements.txt is
//                                     unchanged, then starts Scorpi again (in the tray if its window was closed)
// Built by build.bat (called by scripts\make_release.ps1 in the app folder).

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.IO.Compression;
using System.Management;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("Scorpi Setup")]
[assembly: AssemblyProduct("Scorpi")]
[assembly: AssemblyDescription("Installs Scorpi, your own AI on your own PC")]
// AssemblyVersion comes from obj\Version.cs, written by build.bat

namespace ScorpiSetup
{
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            Native.SetProcessDPIAware();
            ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072;  // TLS 1.2 for python.org / NuGet
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            bool uninstall = Array.IndexOf(args, "--uninstall") >= 0;
            int at = Array.IndexOf(args, "--dir");
            string given = at >= 0 && at + 1 < args.Length ? Path.GetFullPath(args[at + 1]) : null;
            if (Array.IndexOf(args, "--update") >= 0 && !uninstall)
            {
                string dir = given ?? Installer.InstalledDir();
                if (dir == null) return 1;  // nothing installed to update
                bool alone;
                using (new Mutex(true, "Scorpi.Setup.Update", out alone))  // one update at a time
                {
                    if (!alone) return 0;
                    var job = new Installer(dir) { Updating = true, DesktopShortcut = File.Exists(Installer.DesktopLink), StartWithWindows = File.Exists(Installer.StartupLink) };
                    try { job.Install(); job.Relaunch(); return job.Skipped.Count == 0 ? 0 : 3; }
                    catch { job.Relaunch(); return 1; }
                }
            }
            if (Array.IndexOf(args, "--quiet") >= 0)
            {
                // no window: --quiet [--dir <folder>] [--no-desktop] [--startup] | --uninstall --quiet [--dir <folder>] [--delete-data]
                var job = new Installer(given ?? Installer.InstalledDir() ?? Installer.DefaultDir)
                {
                    DesktopShortcut = Array.IndexOf(args, "--no-desktop") < 0,
                    StartWithWindows = Array.IndexOf(args, "--startup") >= 0,
                    DeleteData = Array.IndexOf(args, "--delete-data") >= 0,
                };
                try
                {
                    if (uninstall) job.Uninstall(); else job.Install();
                    return job.Skipped.Count == 0 ? 0 : 3;
                }
                catch { return 1; }
            }
            if (uninstall)
            {
                string dir = given ?? Installer.InstalledDir() ?? Path.GetDirectoryName(Application.ExecutablePath);
                // Windows can't delete a running exe: work from a copy in %TEMP%, which removes itself at the end
                string self = Application.ExecutablePath;
                if (self.StartsWith(dir.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase))
                {
                    string temp = Path.Combine(Path.GetTempPath(), "scorpi-uninstall-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".exe");
                    File.Copy(self, temp, true);
                    Process.Start(new ProcessStartInfo(temp, "--uninstall --dir \"" + dir + "\"") { UseShellExecute = false });
                    return 0;
                }
                Application.Run(new SetupForm(dir, true));
                return 0;
            }
            Application.Run(new SetupForm(Installer.InstalledDir() ?? Installer.DefaultDir, false));
            return 0;
        }
    }

    // =====================================================================================================================
    // the work
    // =====================================================================================================================

    class Installer
    {
        public const string PythonUrl = "https://www.nuget.org/api/v2/package/python/3.12.10";
        const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\Scorpi";
        const string AppId = "Scorpi.Desktop";  // the app window's taskbar identity (desktop.py)

        public static string DefaultDir
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Scorpi"); }
        }

        public static string Version
        {
            get { Version v = Assembly.GetExecutingAssembly().GetName().Version; return v.Major + "." + v.Minor + (v.Build > 0 ? "." + v.Build : ""); }
        }

        public static string InstalledDir()
        {
            try
            {
                using (var key = Registry.CurrentUser.OpenSubKey(UninstallKey))
                {
                    string dir = key == null ? null : key.GetValue("InstallLocation") as string;
                    return !string.IsNullOrEmpty(dir) && File.Exists(Path.Combine(dir, "server.py")) ? dir : null;
                }
            }
            catch { return null; }
        }

        public readonly string Dir;
        public bool DesktopShortcut = true, StartWithWindows, DeleteData, Updating;
        bool appWasOpen, serverWasRunning;
        public readonly List<string> Skipped = new List<string>();  // packages that would not install
        public volatile bool Cancelled;
        public string LogPath;
        Process current;
        StreamWriter log;

        /// step title, overall progress 0..1, a detail line (null keeps the last)
        public Action<string, double, string> Report = delegate { };

        public Installer(string dir) { Dir = dir; }

        string Py { get { return Path.Combine(Dir, "runtime", "python", "python.exe"); } }
        string VenvPy { get { return Path.Combine(Dir, ".venv", "Scripts", "python.exe"); } }

        void Log(string line)
        {
            try { if (log != null) { log.WriteLine(DateTime.Now.ToString("HH:mm:ss") + "  " + line); log.Flush(); } } catch { }
        }

        public void Install()
        {
            Directory.CreateDirectory(Dir);
            LogPath = Path.Combine(Dir, "install.log");
            using (log = new StreamWriter(LogPath, false, new UTF8Encoding(false)))
            {
                Log("Scorpi " + Version + " setup into " + Dir);
                Report("Getting ready", 0.01, "Closing Scorpi if it is open");
                StopRunning();

                string reqPath = Path.Combine(Dir, "requirements.txt");
                string before = File.Exists(reqPath) ? File.ReadAllText(reqPath) : null;
                Report("Unpacking Scorpi", 0.03, "");
                Unpack();
                // an update with the same packages as before skips pip (a minute of "already satisfied")
                bool samePackages = Updating && before != null && before == File.ReadAllText(reqPath) && File.Exists(VenvPy);

                if (!File.Exists(Py))
                {
                    Report("Getting Python", 0.08, "python.org's Python 3.12, only for Scorpi");
                    GetPython();
                }
                Check();
                if (!File.Exists(VenvPy))
                {
                    Report("Preparing Python", 0.2, "A private environment for Scorpi's packages");
                    Run(Py, "-m venv \"" + Path.Combine(Dir, ".venv") + "\"", true);
                }
                var packages = samePackages ? new List<string[]>() : Requirements();
                if (samePackages) Log("same packages as before: pip skipped");
                else
                {
                    Report("Preparing Python", 0.23, "Updating pip");
                    Run(VenvPy, "-m pip install --upgrade pip --disable-pip-version-check --no-input -q", false);
                }
                for (int i = 0; i < packages.Count; i++)
                {
                    Check();
                    string[] p = packages[i];
                    Report("Installing " + p[1], 0.26 + 0.66 * i / packages.Count, p[0] + "  \u00B7  " + (i + 1) + " of " + packages.Count);
                    int code = Run(VenvPy, "-m pip install --disable-pip-version-check --no-input --prefer-binary \"" + p[0] + "\"", false);
                    if (code != 0) { Skipped.Add(p[0]); Log("could not install " + p[0]); }
                }

                Check();
                Report("Finishing", 0.94, "Shortcuts and the entry in Settings \u2192 Apps");
                Shortcuts();
                Register();
                Report("Done", 1, "");
                Log("done" + (Skipped.Count > 0 ? ", without: " + string.Join(", ", Skipped.ToArray()) : ""));
            }
        }

        void Check()
        {
            if (Cancelled) throw new OperationCanceledException();
        }

        // ---------- steps ----------

        /// Scorpi's own window and server from this folder (an update replaces their files).
        public void StopRunning()
        {
            NoteRunning();
            string exe = Path.Combine(Dir, "Scorpi.exe");
            if (File.Exists(exe))
            {
                try
                {
                    using (var p = Process.Start(new ProcessStartInfo(exe, "--stop") { UseShellExecute = false, CreateNoWindow = true }))
                        p.WaitForExit(40000);
                }
                catch { }
            }
            for (int round = 0; round < 3; round++)
            {
                var pids = new List<int>();
                try
                {
                    using (var search = new ManagementObjectSearcher("SELECT ProcessId, CommandLine, ExecutablePath FROM Win32_Process"))
                    {
                        foreach (ManagementObject o in search.Get())
                        {
                            string cmd = (o["CommandLine"] as string) ?? "", path = (o["ExecutablePath"] as string) ?? "";
                            int pid = Convert.ToInt32(o["ProcessId"]);
                            if (pid == Process.GetCurrentProcess().Id) continue;
                            if (path.StartsWith(Dir + "\\", StringComparison.OrdinalIgnoreCase) || cmd.IndexOf(Dir + "\\", StringComparison.OrdinalIgnoreCase) >= 0)
                                pids.Add(pid);
                        }
                    }
                }
                catch { }
                if (pids.Count == 0) return;
                foreach (int pid in pids)
                {
                    try { Process.GetProcessById(pid).Kill(); } catch { }
                }
                Thread.Sleep(800);
            }
        }

        /// What was running before an update, so Relaunch() can bring back the same.
        void NoteRunning()
        {
            try
            {
                using (var search = new ManagementObjectSearcher("SELECT CommandLine FROM Win32_Process"))
                {
                    foreach (ManagementObject o in search.Get())
                    {
                        string cmd = (o["CommandLine"] as string) ?? "";
                        if (cmd.IndexOf(Dir + "\\", StringComparison.OrdinalIgnoreCase) < 0) continue;
                        // an app started into the tray (--hidden) comes back there, not as a window out of nowhere
                        if (cmd.IndexOf("desktop.py", StringComparison.OrdinalIgnoreCase) >= 0 && cmd.IndexOf("--hidden", StringComparison.OrdinalIgnoreCase) < 0) appWasOpen = true;
                        if (cmd.IndexOf("server.py", StringComparison.OrdinalIgnoreCase) >= 0 || cmd.IndexOf("desktop.py", StringComparison.OrdinalIgnoreCase) >= 0) serverWasRunning = true;
                    }
                }
            }
            catch { }
        }

        /// After an update: Scorpi again, as it was (its window, or only the server in the tray).
        public void Relaunch()
        {
            if (!appWasOpen && !serverWasRunning) return;
            try
            {
                string exe = Path.Combine(Dir, "Scorpi.exe");
                Process.Start(new ProcessStartInfo(exe, appWasOpen ? "" : "--hidden") { UseShellExecute = false, WorkingDirectory = Dir });
            }
            catch { }
        }

        void Unpack()
        {
            string root = Path.GetFullPath(Dir).TrimEnd('\\') + "\\";
            using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("app.zip"))
            using (var zip = new ZipArchive(s, ZipArchiveMode.Read))
            {
                int n = 0;
                foreach (ZipArchiveEntry entry in zip.Entries)
                {
                    string rel = entry.FullName.Replace('/', '\\');
                    string dest = Path.GetFullPath(Path.Combine(Dir, rel));
                    if (!dest.StartsWith(root, StringComparison.OrdinalIgnoreCase)) continue;  // never outside the folder
                    if (rel.EndsWith("\\")) { Directory.CreateDirectory(dest); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(dest));
                    for (int attempt = 0; ; attempt++)
                    {
                        try { entry.ExtractToFile(dest, true); break; }
                        catch (IOException) { if (attempt >= 10) throw; Thread.Sleep(500); }  // a file still in use for a moment
                    }
                    if (++n % 20 == 0) Report("Unpacking Scorpi", 0.03 + 0.04 * n / zip.Entries.Count, rel);
                }
                Log("unpacked " + n + " files");
            }
        }

        void GetPython()
        {
            string tmp = Path.Combine(Path.GetTempPath(), "scorpi-python-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".zip");
            try
            {
                Download(PythonUrl, tmp, 0.08, 0.17);
                Report("Getting Python", 0.17, "Unpacking");
                string target = Path.Combine(Dir, "runtime", "python");
                if (Directory.Exists(target)) Directory.Delete(target, true);
                using (var zip = ZipFile.OpenRead(tmp))
                {
                    foreach (ZipArchiveEntry entry in zip.Entries)
                    {
                        if (!entry.FullName.StartsWith("tools/") || entry.FullName.EndsWith("/")) continue;
                        string dest = Path.GetFullPath(Path.Combine(target, entry.FullName.Substring(6).Replace('/', '\\')));
                        if (!dest.StartsWith(target + "\\", StringComparison.OrdinalIgnoreCase)) continue;
                        Directory.CreateDirectory(Path.GetDirectoryName(dest));
                        entry.ExtractToFile(dest, true);
                    }
                }
                if (!File.Exists(Py)) throw new Exception("The Python download was incomplete. Check the internet connection and try again.");
                Log("python ready");
            }
            finally { try { File.Delete(tmp); } catch { } }
        }

        void Download(string url, string path, double from, double to)
        {
            Exception last = null;
            for (int attempt = 0; attempt < 3; attempt++)
            {
                try
                {
                    var req = (HttpWebRequest)WebRequest.Create(url);
                    req.UserAgent = "Scorpi-Setup/" + Version;
                    req.Timeout = 30000;
                    req.ReadWriteTimeout = 60000;
                    using (var res = (HttpWebResponse)req.GetResponse())
                    using (Stream input = res.GetResponseStream())
                    using (var output = File.Create(path))
                    {
                        long total = res.ContentLength, got = 0;
                        var buf = new byte[81920];
                        int read, ticks = 0;
                        while ((read = input.Read(buf, 0, buf.Length)) > 0)
                        {
                            Check();
                            output.Write(buf, 0, read);
                            got += read;
                            if (++ticks % 16 == 0 && total > 0)
                                Report("Getting Python", from + (to - from) * got / total, string.Format("{0:0.0} of {1:0.0} MB", got / 1048576.0, total / 1048576.0));
                        }
                    }
                    return;
                }
                catch (OperationCanceledException) { throw; }
                catch (Exception ex) { last = ex; Log("download failed: " + ex.Message); Thread.Sleep(1500); }
            }
            throw new Exception("Couldn't download Python (" + last.Message + "). Check the internet connection and try again.");
        }

        /// requirements.txt as {package, what it is for}: the comment above a group names it ("# voice: free neural TTS\u2026")
        List<string[]> Requirements()
        {
            var list = new List<string[]>();
            string group = "extras";
            foreach (string raw in File.ReadAllLines(Path.Combine(Dir, "requirements.txt")))
            {
                string line = raw.Trim();
                if (line.Length == 0) continue;
                if (line.StartsWith("#"))
                {
                    string label = line.TrimStart('#', ' ');
                    int cut = label.IndexOfAny(new[] { ':', '(' });
                    if (cut > 0) label = label.Substring(0, cut);
                    label = label.Trim();
                    if (label.Length > 0 && label.Length < 40) group = label.Substring(0, 1).ToLower() + label.Substring(1);
                    continue;
                }
                list.Add(new[] { line, group });
            }
            return list;
        }

        /// Runs a program hidden, writes what it says to the log; throws when `must` and it fails.
        int Run(string exe, string args, bool must)
        {
            Log("> " + Path.GetFileName(exe) + " " + args);
            var psi = new ProcessStartInfo(exe, args)
            {
                UseShellExecute = false, CreateNoWindow = true, WorkingDirectory = Dir,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
            };
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PIP_NO_WARN_SCRIPT_LOCATION"] = "1";
            using (var p = new Process { StartInfo = psi })
            {
                DataReceivedEventHandler onLine = (s, e) => { if (e.Data != null) Log("  " + e.Data); };
                p.OutputDataReceived += onLine;
                p.ErrorDataReceived += onLine;
                p.Start();
                current = p;
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                while (!p.WaitForExit(250))
                {
                    if (Cancelled) { try { p.Kill(); } catch { } throw new OperationCanceledException(); }
                }
                p.WaitForExit();
                current = null;
                Log("  exit " + p.ExitCode);
                if (must && p.ExitCode != 0) throw new Exception("A setup step failed (" + Path.GetFileName(exe) + "). The log has the details.");
                return p.ExitCode;
            }
        }

        public void Cancel()
        {
            Cancelled = true;
            try { if (current != null && !current.HasExited) current.Kill(); } catch { }
        }

        // ---------- shortcuts and Settings → Apps ----------

        public static string DesktopLink { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Scorpi.lnk"); } }
        static string MenuLink { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Scorpi.lnk"); } }
        public static string StartupLink { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "Scorpi.lnk"); } }

        void Shortcuts()
        {
            string exe = Path.Combine(Dir, "Scorpi.exe");
            Shortcut.Create(MenuLink, exe, "", Dir, "Scorpi, your own AI", AppId);
            if (DesktopShortcut) Shortcut.Create(DesktopLink, exe, "", Dir, "Scorpi, your own AI", AppId);
            else TryDelete(DesktopLink);
            if (StartWithWindows) Shortcut.Create(StartupLink, exe, "--hidden", Dir, "Scorpi in the tray", null);
            else TryDelete(StartupLink);
        }

        void Register()
        {
            string uninstaller = Path.Combine(Dir, "uninstall.exe");
            if (!string.Equals(Application.ExecutablePath, uninstaller, StringComparison.OrdinalIgnoreCase))
                File.Copy(Application.ExecutablePath, uninstaller, true);
            using (var key = Registry.CurrentUser.CreateSubKey(UninstallKey))
            {
                key.SetValue("DisplayName", "Scorpi");
                key.SetValue("DisplayVersion", Version);
                key.SetValue("Publisher", "Scorpi");
                key.SetValue("DisplayIcon", Path.Combine(Dir, "Scorpi.exe") + ",0");
                key.SetValue("InstallLocation", Dir);
                key.SetValue("UninstallString", "\"" + uninstaller + "\" --uninstall");
                key.SetValue("URLInfoAbout", "https://scorpi.dev");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                key.SetValue("EstimatedSize", (int)(FolderSize(Dir) / 1024), RegistryValueKind.DWord);
                key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
            }
        }

        static long FolderSize(string dir)
        {
            long total = 0;
            try { foreach (string f in Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories)) { try { total += new FileInfo(f).Length; } catch { } } }
            catch { }
            return total;
        }

        static void RunHidden(string exe, string args)
        {
            using (var p = Process.Start(new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true }))
                p.WaitForExit(300000);
        }

        static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); } catch { }
        }

        // ---------- removing ----------

        public void Uninstall()
        {
            Report("Closing Scorpi", 0.05, "");
            StopRunning();
            Report("Removing shortcuts", 0.15, "");
            foreach (string link in new[] { DesktopLink, MenuLink, StartupLink })
                TryDelete(link);
            TryDelete(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Scorpi Voice.lnk"));
            try { Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false); } catch { }
            if (Directory.Exists(Dir))
            {
                var items = new List<string>(Directory.GetFileSystemEntries(Dir));
                for (int i = 0; i < items.Count; i++)
                {
                    string item = items[i], name = Path.GetFileName(item);
                    if (!DeleteData && string.Equals(name, "data", StringComparison.OrdinalIgnoreCase)) continue;
                    Report("Removing Scorpi", 0.2 + 0.78 * i / items.Count, name);
                    for (int attempt = 0; attempt < 5; attempt++)
                    {
                        try
                        {
                            // rd with the \\?\ prefix also removes paths past 260 characters (deep inside .venv), which
                            // .NET Framework's Directory.Delete can't
                            if (Directory.Exists(item)) RunHidden("cmd.exe", "/c rd /s /q \"\\\\?\\" + item + "\"");
                            else if (File.Exists(item)) { File.SetAttributes(item, FileAttributes.Normal); File.Delete(item); }
                            if (!Directory.Exists(item) && !File.Exists(item)) break;
                        }
                        catch { }
                        Thread.Sleep(600);
                    }
                }
                try { if (Directory.GetFileSystemEntries(Dir).Length == 0) Directory.Delete(Dir); } catch { }
            }
            Report("Done", 1, "");
        }
    }

    /// Shortcuts with WScript.Shell, plus the AppUserModelID that ties the pinned icon to the app window.
    static class Shortcut
    {
        public static void Create(string path, string target, string args, string workDir, string description, string appId)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            object shell = Activator.CreateInstance(shellType);
            object link = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
            Type t = link.GetType();
            t.InvokeMember("TargetPath", BindingFlags.SetProperty, null, link, new object[] { target });
            t.InvokeMember("Arguments", BindingFlags.SetProperty, null, link, new object[] { args });
            t.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, link, new object[] { workDir });
            t.InvokeMember("IconLocation", BindingFlags.SetProperty, null, link, new object[] { target + ",0" });
            t.InvokeMember("Description", BindingFlags.SetProperty, null, link, new object[] { description });
            t.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
            if (appId != null)
            {
                try { SetAppId(path, appId); } catch { }
            }
        }

        [StructLayout(LayoutKind.Sequential, Pack = 4)] struct PropertyKey { public Guid fmtid; public uint pid; }
        [StructLayout(LayoutKind.Explicit)] struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }

        [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IPropertyStore
        {
            void GetCount(out uint c); void GetAt(uint i, out PropertyKey k); void GetValue(ref PropertyKey k, out PropVariant v);
            void SetValue(ref PropertyKey k, ref PropVariant v); void Commit();
        }

        [ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IPersistFile
        {
            void GetClassID(out Guid g); [PreserveSig] int IsDirty(); void Load([MarshalAs(UnmanagedType.LPWStr)] string f, uint mode);
            void Save([MarshalAs(UnmanagedType.LPWStr)] string f, bool remember); void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f);
            void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f);
        }

        static void SetAppId(string path, string id)
        {
            object link = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046")));
            ((IPersistFile)link).Load(path, 2);
            var key = new PropertyKey { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
            var value = new PropVariant { vt = 31, p = Marshal.StringToCoTaskMemUni(id) };
            try { ((IPropertyStore)link).SetValue(ref key, ref value); ((IPropertyStore)link).Commit(); ((IPersistFile)link).Save(path, true); }
            finally { Marshal.FreeCoTaskMem(value.p); }
        }
    }

    // =====================================================================================================================
    // the window
    // =====================================================================================================================

    static class Theme
    {
        // black and white like the app and scorpi.dev: pure black, #0c0c0c cells, #232323 rules, one monospace face
        public static readonly Color Bg = Color.FromArgb(0, 0, 0);
        public static readonly Color Card = Color.FromArgb(12, 12, 12);
        public static readonly Color Edge = Color.FromArgb(35, 35, 35);
        public static readonly Color Fg = Color.FromArgb(237, 237, 237);
        public static readonly Color Muted = Color.FromArgb(143, 143, 143);
        public static readonly Color Faint = Color.FromArgb(92, 92, 92);
        public const string Mono = "Consolas";

        public static Font Text(float size, bool bold = false) { return new Font(Mono, size, bold ? FontStyle.Bold : FontStyle.Regular); }

        public static GraphicsPath Round(RectangleF r, float radius)
        {
            var p = new GraphicsPath();
            float d = radius * 2;
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        public static Image Picture(string name)
        {
            try { return Image.FromStream(Assembly.GetExecutingAssembly().GetManifestResourceStream(name)); }
            catch { return null; }
        }
    }

    class SetupForm : Form
    {
        readonly bool uninstall;
        string dir;
        Installer job;
        Thread worker;
        readonly Image idle = Theme.Picture("scorpi.png"), working = Theme.Picture("scorpi-working.png"), happy = Theme.Picture("scorpi-happy.png");
        Image picture;
        readonly System.Windows.Forms.Timer anim;
        int frame;
        double bobSpeed = 2.2;

        // what is drawn depends on the page; controls are made per page
        string title = "", subtitle = "", step = "", detail = "";
        double progress = -1, shownProgress;  // -1: no bar
        readonly List<Control> pageControls = new List<Control>();

        public SetupForm(string dir, bool uninstall)
        {
            this.dir = dir;
            this.uninstall = uninstall;
            AutoScaleMode = AutoScaleMode.None;
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(Px(460), Px(600));
            BackColor = Theme.Bg;
            ForeColor = Theme.Fg;
            Font = Theme.Text(9.75F);
            Text = uninstall ? "Remove Scorpi" : "Scorpi Setup";
            DoubleBuffered = true;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            var close = new Label { Text = "\u2715", Font = new Font("Segoe UI Symbol", 11F), ForeColor = Theme.Faint, AutoSize = false,
                                    TextAlign = ContentAlignment.MiddleCenter, Size = new Size(Px(36), Px(36)), Location = new Point(ClientSize.Width - Px(44), Px(8)),
                                    Cursor = Cursors.Hand, BackColor = Color.Transparent };
            close.MouseEnter += (s, e) => close.ForeColor = Theme.Fg;
            close.MouseLeave += (s, e) => close.ForeColor = Theme.Faint;
            close.Click += (s, e) => Close();
            Controls.Add(close);

            anim = new System.Windows.Forms.Timer { Interval = 16 };
            anim.Tick += (s, e) =>
            {
                frame++;
                if (progress >= 0) shownProgress += (progress - shownProgress) * 0.08;
                if (frame % 2 != 0) return;  // Scorpi steps; nothing here needs 60 frames a second
                Invalidate(new Rectangle(0, 0, ClientSize.Width, Px(260)));
                if (progress >= 0) Invalidate(new Rectangle(0, Px(330), ClientSize.Width, Px(120)));
            };
            anim.Start();

            if (uninstall) ShowRemove(); else ShowWelcome();
        }

        static int Px(double v) { return (int)Math.Round(v * Native.Scale); }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            int round = 3, dark = 1;
            Native.DwmSetWindowAttribute(Handle, 33, ref round, 4);  // small rounded corners (Windows 11)
            Native.DwmSetWindowAttribute(Handle, 20, ref dark, 4);
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            if (e.Button == MouseButtons.Left) Native.DragWindow(Handle);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (worker != null && worker.IsAlive)
            {
                var answer = MessageBox.Show(this, uninstall ? "Stop removing Scorpi?" : "Stop the setup? Scorpi isn't finished yet; running Scorpi-Setup.exe again picks it up.",
                                             Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (answer != DialogResult.Yes) { e.Cancel = true; return; }
                job.Cancel();
            }
            base.OnFormClosing(e);
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            base.OnFormClosed(e);
            if (uninstall && Application.ExecutablePath.IndexOf("scorpi-uninstall-", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                // the temporary copy removes itself once it has exited
                try
                {
                    Process.Start(new ProcessStartInfo("cmd.exe", "/c ping 127.0.0.1 -n 3 >nul & del /f /q \"" + Application.ExecutablePath + "\"")
                                  { UseShellExecute = false, CreateNoWindow = true });
                }
                catch { }
            }
        }

        // ---------- pages ----------

        void Clear()
        {
            foreach (Control c in pageControls) { Controls.Remove(c); c.Dispose(); }
            pageControls.Clear();
            step = detail = "";
            progress = -1;
        }

        T Add<T>(T c) where T : Control
        {
            pageControls.Add(c);
            Controls.Add(c);
            return c;
        }

        void ShowWelcome()
        {
            Clear();
            bool update = Installer.InstalledDir() != null;
            picture = idle;
            bobSpeed = 2.2;
            title = "Scorpi";
            subtitle = update ? "A new version is ready. Your chats stay." : "Your own AI, on your own PC.";

            var card = Add(new RoundPanel { Location = new Point(Px(28), Px(318)), Size = new Size(Px(404), Px(150)) });
            card.Controls.Add(new Label { Text = "Install to", ForeColor = Theme.Muted, AutoSize = true, BackColor = Color.Transparent, Location = new Point(Px(18), Px(16)) });
            var path = new Label { Text = Shorten(dir), ForeColor = Theme.Fg, AutoSize = false, AutoEllipsis = true, BackColor = Color.Transparent,
                                   Location = new Point(Px(18), Px(38)), Size = new Size(Px(300), Px(22)), Font = Theme.Text(9F) };
            var change = new LinkLabel { Text = "Change", AutoSize = true, LinkColor = Theme.Fg, ActiveLinkColor = Theme.Muted, BackColor = Color.Transparent,
                                         LinkBehavior = LinkBehavior.AlwaysUnderline, Location = new Point(Px(332), Px(38)) };
            change.Visible = !update;
            change.LinkClicked += (s, e) =>
            {
                using (var pick = new FolderBrowserDialog { Description = "Where should Scorpi live? A folder named Scorpi is made inside.", ShowNewFolderButton = true })
                {
                    if (pick.ShowDialog(this) != DialogResult.OK) return;
                    string chosen = pick.SelectedPath;
                    dir = string.Equals(Path.GetFileName(chosen.TrimEnd('\\')), "Scorpi", StringComparison.OrdinalIgnoreCase) ? chosen : Path.Combine(chosen, "Scorpi");
                    path.Text = Shorten(dir);
                }
            };
            card.Controls.Add(path);
            card.Controls.Add(change);
            var desktop = new Toggle { Text = "Shortcut on the desktop", Checked = true, Location = new Point(Px(16), Px(76)), Size = new Size(Px(372), Px(28)) };
            var startup = new Toggle { Text = "Start with Windows, waiting in the tray", Checked = false, Location = new Point(Px(16), Px(108)), Size = new Size(Px(372), Px(28)) };
            card.Controls.Add(desktop);
            card.Controls.Add(startup);

            var go = Add(new PillButton { Text = update ? "Update Scorpi" : "Install Scorpi", Primary = true, Location = new Point(Px(28), Px(488)), Size = new Size(Px(404), Px(48)) });
            go.Click += (s, e) => StartInstall(desktop.Checked, startup.Checked);
            Add(new Label { Text = "Downloads about 300 MB \u00B7 a few minutes \u00B7 no admin needed", ForeColor = Theme.Faint, Font = Theme.Text(8.25F),
                            AutoSize = false, TextAlign = ContentAlignment.MiddleCenter, BackColor = Color.Transparent,
                            Location = new Point(0, Px(548)), Size = new Size(ClientSize.Width, Px(24)) });
            Invalidate();
        }

        static string Shorten(string path)
        {
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return path.StartsWith(home, StringComparison.OrdinalIgnoreCase) ? "~" + path.Substring(home.Length) : path;
        }

        void ShowProgress(string heading)
        {
            Clear();
            picture = working;
            bobSpeed = 6;
            title = heading;
            subtitle = "";
            progress = shownProgress = 0;
            Add(new Label { Text = "You can keep working. Scorpi is unpacking its things.", ForeColor = Theme.Faint, Font = Theme.Text(8.25F),
                            AutoSize = false, TextAlign = ContentAlignment.MiddleCenter, BackColor = Color.Transparent,
                            Location = new Point(0, Px(548)), Size = new Size(ClientSize.Width, Px(24)) });
            Invalidate();
        }

        void ShowDone()
        {
            Clear();
            picture = happy;
            bobSpeed = 3;
            title = "Scorpi is ready";
            subtitle = "It's in the Start menu" + (File.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Scorpi.lnk")) ? " and on your desktop." : ".");
            if (job.Skipped.Count > 0)
            {
                Add(new Label { Text = job.Skipped.Count + (job.Skipped.Count == 1 ? " extra" : " extras") + " couldn't be installed (" + string.Join(", ", job.Skipped.ToArray()) +
                                       "). Those tools stay off; running the setup again tries once more.",
                                ForeColor = Theme.Muted, Font = Theme.Text(9F), AutoSize = false, TextAlign = ContentAlignment.TopCenter,
                                BackColor = Color.Transparent, Location = new Point(Px(40), Px(330)), Size = new Size(ClientSize.Width - Px(80), Px(80)) });
            }
            var open = Add(new PillButton { Text = "Open Scorpi", Primary = true, Location = new Point(Px(28), Px(488)), Size = new Size(Px(404), Px(48)) });
            open.Click += (s, e) =>
            {
                try { Process.Start(new ProcessStartInfo(Path.Combine(dir, "Scorpi.exe")) { UseShellExecute = false, WorkingDirectory = dir }); } catch { }
                Close();
            };
            var close = Add(new LinkLabel { Text = "Close", AutoSize = false, TextAlign = ContentAlignment.MiddleCenter, LinkColor = Theme.Muted, ActiveLinkColor = Theme.Fg,
                                            LinkBehavior = LinkBehavior.AlwaysUnderline, BackColor = Color.Transparent,
                                            Location = new Point(0, Px(548)), Size = new Size(ClientSize.Width, Px(24)) });
            close.LinkClicked += (s, e) => Close();
            Invalidate();
        }

        void ShowError(string message)
        {
            Clear();
            picture = idle;
            bobSpeed = 1.2;
            title = "That didn't work";
            subtitle = "";
            Add(new Label { Text = message, ForeColor = Theme.Muted, Font = Theme.Text(9.5F), AutoSize = false, TextAlign = ContentAlignment.TopCenter,
                            BackColor = Color.Transparent, Location = new Point(Px(40), Px(300)), Size = new Size(ClientSize.Width - Px(80), Px(120)) });
            var again = Add(new PillButton { Text = "Try again", Primary = true, Location = new Point(Px(28), Px(488)), Size = new Size(Px(404), Px(48)) });
            again.Click += (s, e) => { if (uninstall) ShowRemove(); else ShowWelcome(); };
            if (job != null && job.LogPath != null && File.Exists(job.LogPath))
            {
                var logLink = Add(new LinkLabel { Text = "Open the log", AutoSize = false, TextAlign = ContentAlignment.MiddleCenter, LinkColor = Theme.Muted,
                                                  ActiveLinkColor = Theme.Fg, LinkBehavior = LinkBehavior.AlwaysUnderline, BackColor = Color.Transparent,
                                                  Location = new Point(0, Px(548)), Size = new Size(ClientSize.Width, Px(24)) });
                string logPath = job.LogPath;
                logLink.LinkClicked += (s, e) => { try { Process.Start(new ProcessStartInfo(logPath) { UseShellExecute = true }); } catch { } };
            }
            Invalidate();
        }

        void ShowRemove()
        {
            Clear();
            picture = idle;
            bobSpeed = 1.6;
            title = "Remove Scorpi?";
            subtitle = "Scorpi will miss you.";
            var card = Add(new RoundPanel { Location = new Point(Px(28), Px(340)), Size = new Size(Px(404), Px(100)) });
            var data = new Toggle { Text = "Also delete my chats, settings and files", Checked = false, Location = new Point(Px(16), Px(16)), Size = new Size(Px(372), Px(28)) };
            card.Controls.Add(data);
            card.Controls.Add(new Label { Text = "Off keeps them in " + Shorten(Path.Combine(dir, "data")) + " for a later install.", ForeColor = Theme.Faint,
                                          Font = Theme.Text(8.25F), AutoSize = false, AutoEllipsis = true, BackColor = Color.Transparent,
                                          Location = new Point(Px(18), Px(54)), Size = new Size(Px(372), Px(36)) });
            var go = Add(new PillButton { Text = "Remove Scorpi", Primary = true, Location = new Point(Px(28), Px(488)), Size = new Size(Px(404), Px(48)) });
            go.Click += (s, e) => StartRemove(data.Checked);
            var keep = Add(new LinkLabel { Text = "Keep Scorpi", AutoSize = false, TextAlign = ContentAlignment.MiddleCenter, LinkColor = Theme.Muted, ActiveLinkColor = Theme.Fg,
                                           LinkBehavior = LinkBehavior.AlwaysUnderline, BackColor = Color.Transparent,
                                           Location = new Point(0, Px(548)), Size = new Size(ClientSize.Width, Px(24)) });
            keep.LinkClicked += (s, e) => Close();
            Invalidate();
        }

        // ---------- running ----------

        void Bind(Installer j)
        {
            j.Report = (s, p, d) =>
            {
                try
                {
                    BeginInvoke((Action)(() =>
                    {
                        step = s;
                        progress = Math.Max(progress, p);
                        if (d != null) detail = d;
                    }));
                }
                catch { }
            };
        }

        void StartInstall(bool desktopShortcut, bool startWithWindows)
        {
            job = new Installer(dir) { DesktopShortcut = desktopShortcut, StartWithWindows = startWithWindows };
            Bind(job);
            ShowProgress("Setting Scorpi up");
            worker = new Thread(() =>
            {
                string error = null;
                try { job.Install(); }
                catch (OperationCanceledException) { return; }
                catch (UnauthorizedAccessException) { error = "Windows didn't let the setup write to " + dir + ". Pick another folder and try again."; }
                catch (Exception ex) { error = ex.Message; }
                try { BeginInvoke((Action)(() => { if (error == null) ShowDone(); else ShowError(error); })); } catch { }
            }) { IsBackground = true };
            worker.Start();
        }

        void StartRemove(bool deleteData)
        {
            job = new Installer(dir) { DeleteData = deleteData };
            Bind(job);
            ShowProgress("Removing Scorpi");
            worker = new Thread(() =>
            {
                string error = null;
                try { job.Uninstall(); }
                catch (Exception ex) { error = ex.Message; }
                try
                {
                    BeginInvoke((Action)(() =>
                    {
                        if (error != null) { ShowError(error); return; }
                        Clear();
                        picture = idle;
                        bobSpeed = 1.2;
                        title = "Scorpi is removed";
                        subtitle = deleteData ? "Everything is gone. Bye for now." : "Your chats and settings are still in the data folder.";
                        var close = Add(new PillButton { Text = "Close", Primary = true, Location = new Point(Px(28), Px(488)), Size = new Size(Px(404), Px(48)) });
                        close.Click += (s, e) => Close();
                        Invalidate();
                    }));
                }
                catch { }
            }) { IsBackground = true };
            worker.Start();
        }

        // ---------- drawing ----------

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.None;
            g.InterpolationMode = InterpolationMode.NearestNeighbor;
            g.PixelOffsetMode = PixelOffsetMode.Half;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            int w = ClientSize.Width;

            using (var pen = new Pen(Theme.Edge))
                g.DrawRectangle(pen, 0, 0, w - 1, ClientSize.Height - 1);
            if (picture != null)
            {
                // Scorpi's picture is 8 screen pixels per sprite pixel at 96 DPI; it hops one sprite pixel, faster while busy
                int k = Math.Max(1, (int)Math.Round(Native.Scale));
                int pw = picture.Width * k, ph = picture.Height * k;
                int hop = (int)(frame / (120 / bobSpeed)) % 2 == 0 ? 0 : 8 * k;
                g.DrawImage(picture, (w - pw) / 2, Px(208) - ph - hop, pw, ph);
            }

            using (var center = new StringFormat { Alignment = StringAlignment.Center, Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap })
            {
                using (var font = Theme.Text(19F, true))
                using (var brush = new SolidBrush(Theme.Fg))
                    g.DrawString(title, font, brush, new RectangleF(Px(20), Px(232), w - Px(40), Px(50)), center);
                if (subtitle.Length > 0)
                {
                    using (var font = Theme.Text(10F))
                    using (var brush = new SolidBrush(Theme.Muted))
                        g.DrawString(subtitle, font, brush, new RectangleF(Px(20), Px(280), w - Px(40), Px(28)), center);
                }
                if (progress >= 0)
                {
                    using (var font = Theme.Text(10F, true))
                    using (var brush = new SolidBrush(Theme.Fg))
                        g.DrawString(step, font, brush, new RectangleF(Px(28), Px(342), w - Px(56), Px(26)), center);
                    var track = new RectangleF(Px(48), Px(381), w - Px(96), Px(2));
                    using (var brush = new SolidBrush(Theme.Edge))
                        g.FillRectangle(brush, track);
                    float fill = (float)Math.Max(Px(2), track.Width * Math.Min(1, shownProgress));
                    using (var brush = new SolidBrush(Theme.Fg))
                        g.FillRectangle(brush, track.X, track.Y, fill, track.Height);
                    using (var font = Theme.Text(8.25F))
                    using (var brush = new SolidBrush(Theme.Faint))
                        g.DrawString(detail, font, brush, new RectangleF(Px(28), Px(400), w - Px(56), Px(22)), center);
                }
            }
        }
    }

    // ---------- small controls in Scorpi's look ----------

    class RoundPanel : Panel
    {
        public RoundPanel() { DoubleBuffered = true; BackColor = Theme.Bg; }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            var r = new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f);
            using (var p = Theme.Round(r, 6 * Native.Scale))
            {
                using (var brush = new SolidBrush(Theme.Card)) e.Graphics.FillPath(brush, p);
                using (var pen = new Pen(Theme.Edge)) e.Graphics.DrawPath(pen, p);
            }
        }

        protected override void OnControlAdded(ControlEventArgs e)
        {
            base.OnControlAdded(e);
            if (e.Control.BackColor == Color.Transparent || e.Control is Toggle) e.Control.BackColor = Theme.Card;
        }
    }

    class PillButton : Control
    {
        public bool Primary;
        bool hover, down;

        public PillButton()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint | ControlStyles.SupportsTransparentBackColor, true);
            BackColor = Color.Transparent;
            Cursor = Cursors.Hand;
            Font = Theme.Text(10.5F, true);
        }

        protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover = down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { down = true; Invalidate(); base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            var r = new RectangleF(0.5f, 0.5f, Width - 1.5f, Height - 1.5f);
            // the site's button: a white fill with black text and 4px corners (secondary: a 1px rule)
            Color fill = Primary ? (down ? Color.FromArgb(207, 207, 207) : hover ? Color.White : Theme.Fg) : (hover ? Theme.Card : Theme.Bg);
            using (var p = Theme.Round(r, 4 * Native.Scale))
            {
                using (var brush = new SolidBrush(fill))
                    g.FillPath(brush, p);
                if (!Primary)
                    using (var pen = new Pen(hover ? Theme.Faint : Theme.Edge))
                        g.DrawPath(pen, p);
            }
            using (var brush = new SolidBrush(Primary ? Color.Black : Theme.Fg))
            using (var f = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                g.DrawString(Text, Font, brush, new RectangleF(0, 0, Width, Height), f);
        }
    }

    class Toggle : Control
    {
        bool on;

        public Toggle()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint, true);
            Cursor = Cursors.Hand;
            Font = Theme.Text(9.5F);
            ForeColor = Theme.Fg;
        }

        public bool Checked { get { return on; } set { on = value; Invalidate(); } }

        protected override void OnClick(EventArgs e) { Checked = !on; base.OnClick(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
            float s = Native.Scale;
            using (var f = new StringFormat { LineAlignment = StringAlignment.Center })
            {
                float box = g.MeasureString("[x] ", Font).Width;
                using (var brush = new SolidBrush(on ? Theme.Fg : Theme.Faint))
                    g.DrawString(on ? "[x]" : "[ ]", Font, brush, new RectangleF(2 * s, 0, box, Height), f);
                using (var brush = new SolidBrush(on ? Theme.Fg : Theme.Muted))
                    g.DrawString(Text, Font, brush, new RectangleF(2 * s + box, 0, Width - box - 4 * s, Height), f);
            }
        }
    }

    static class Native
    {
        [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
        [DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
        [DllImport("user32.dll")] static extern bool ReleaseCapture();
        [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hwnd, int msg, IntPtr w, IntPtr l);
        [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr hwnd);
        [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);
        [DllImport("gdi32.dll")] static extern int GetDeviceCaps(IntPtr dc, int index);

        static float scale;

        /// Screen pixels per design pixel (1 at 96 DPI).
        public static float Scale
        {
            get
            {
                if (scale > 0) return scale;
                IntPtr dc = GetDC(IntPtr.Zero);
                try { scale = Math.Max(1f, GetDeviceCaps(dc, 88) / 96f); }  // LOGPIXELSX
                finally { ReleaseDC(IntPtr.Zero, dc); }
                return scale;
            }
        }

        public static void DragWindow(IntPtr hwnd)
        {
            ReleaseCapture();
            SendMessage(hwnd, 0xA1, (IntPtr)2, IntPtr.Zero);  // WM_NCLBUTTONDOWN on the caption
        }
    }
}
