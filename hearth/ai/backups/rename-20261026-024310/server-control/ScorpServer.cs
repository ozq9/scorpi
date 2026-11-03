// Scorp Server: a small window to start, stop and restart the Hearth server (ai/server.py).
// Closing the window leaves the server running; opening it again picks the running server back up.
// Without the window: "Scorp Server.exe" --start | --stop | --restart  (exit code 0 = done).
// Build with build.bat (uses the C# compiler that ships with Windows).

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("Scorp Server")]
[assembly: AssemblyProduct("Scorp Server")]
[assembly: AssemblyVersion("1.0.0.0")]

namespace ScorpServer
{
    static class Program
    {
        [STAThread]
        static int Main(string[] args)
        {
            Native.SetProcessDPIAware();
            string root = Server.FindRoot();
            if (root == null)
            {
                MessageBox.Show("Put Scorp Server.exe in Hearth's \"ai\" folder (next to server.py).", ControlForm.Title,
                                MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return 2;
            }
            var server = new Server(root);
            if (args.Length > 0)
                return RunHeadless(server, args[0].TrimStart('-', '/').ToLowerInvariant());

            bool first;
            using (new Mutex(true, "Local\\ScorpServerControl", out first))
            {
                if (!first)
                {
                    Native.BringToFront(ControlForm.Title);  // already open: show that window instead
                    return 0;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new ControlForm(server));
            }
            return 0;
        }

        static int RunHeadless(Server server, string action)
        {
            string error;
            switch (action)
            {
                case "start": error = server.Start(); break;
                case "stop": error = server.Stop(); break;
                case "restart": error = server.Restart(); break;
                default: error = "Unknown option. Use --start, --stop or --restart."; break;
            }
            return error == null ? 0 : 1;
        }
    }

    class Status
    {
        public bool Up;        // answering HTTP
        public bool Booting;   // the process runs but does not answer yet
        public int Pid;
        public DateTime? Started;
        public int Port;
        public string Pin;
        public string[] Addresses;
        public bool AppRunning;  // the Scorp app window (desktop.py), shown or in the tray
        public string Log;     // null when the log did not change
    }

    class Server
    {
        public readonly string Root;
        long logSeen = -1;

        public Server(string root) { Root = root; }

        public static string FindRoot()
        {
            string dir = AppDomain.CurrentDomain.BaseDirectory;
            foreach (string c in new[] { dir, Path.Combine(dir, "ai"), Path.Combine(dir, ".."), Path.Combine(dir, "..", "ai") })
            {
                string full = Path.GetFullPath(c).TrimEnd('\\');
                if (File.Exists(Path.Combine(full, "server.py")) && Directory.Exists(Path.Combine(full, "hearth")))
                    return full;
            }
            return null;
        }

        public string DataDir
        {
            get
            {
                string env = Environment.GetEnvironmentVariable("HEARTH_DATA");
                return string.IsNullOrEmpty(env) ? Path.Combine(Root, "data") : Path.GetFullPath(env);
            }
        }

        public string LogPath { get { return Path.Combine(DataDir, "server.log"); } }

        Dictionary<string, object> Settings()
        {
            try
            {
                string json = File.ReadAllText(Path.Combine(DataDir, "settings.json"));
                return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json) ?? new Dictionary<string, object>();
            }
            catch { return new Dictionary<string, object>(); }
        }

        public int Port
        {
            get
            {
                object v;
                int port;
                return Settings().TryGetValue("port", out v) && v != null && int.TryParse(v.ToString(), out port) ? port : 8000;
            }
        }

        // ---------- state ----------

        public bool IsUp(int port)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/api/auth/status");
                req.Timeout = 1500;
                req.Proxy = null;
                using (req.GetResponse()) { }
                return true;
            }
            catch (WebException ex)
            {
                return ex.Response != null;  // any HTTP answer means it is up
            }
            catch { return false; }
        }

        static bool IsPython(int pid)
        {
            try { return Process.GetProcessById(pid).ProcessName.StartsWith("python", StringComparison.OrdinalIgnoreCase); }
            catch { return false; }
        }

        /// The Python processes running this folder's `script`. A venv's pythonw.exe is a launcher that runs the real
        /// interpreter as its child, so children running the same script count too.
        List<int> FindScriptPids(string script)
        {
            var pids = new List<int>();
            var others = new List<int[]>();  // {pid, parent pid} of matching processes outside this folder
            Regex pattern = new Regex("(^|[\\\\/\"\\s])" + Regex.Escape(script) + "(\"|\\s|$)", RegexOptions.IgnoreCase);
            try
            {
                using (var search = new ManagementObjectSearcher(
                    "SELECT ProcessId, ParentProcessId, CommandLine, ExecutablePath FROM Win32_Process WHERE Name LIKE 'python%'"))
                {
                    foreach (ManagementObject o in search.Get())
                    {
                        string cmd = (o["CommandLine"] as string) ?? "";
                        string exe = (o["ExecutablePath"] as string) ?? "";
                        if (!pattern.IsMatch(cmd)) continue;
                        int pid = Convert.ToInt32(o["ProcessId"]);
                        if (cmd.IndexOf(Root, StringComparison.OrdinalIgnoreCase) >= 0 || exe.IndexOf(Root, StringComparison.OrdinalIgnoreCase) >= 0)
                            pids.Add(pid);
                        else
                            others.Add(new[] { pid, Convert.ToInt32(o["ParentProcessId"]) });
                    }
                }
            }
            catch { }
            for (bool grew = true; grew; )
            {
                grew = false;
                foreach (int[] o in others)
                {
                    if (!pids.Contains(o[0]) && pids.Contains(o[1])) { pids.Add(o[0]); grew = true; }
                }
            }
            return pids;
        }

        /// The Python processes running this folder's server.py, plus whichever Python listens on the port.
        public List<int> FindServerPids(int port)
        {
            List<int> pids = FindScriptPids("server.py");
            int owner = Native.PortOwner(port);
            if (owner > 0 && !pids.Contains(owner) && IsPython(owner))
                pids.Add(owner);
            return pids;
        }

        /// The Scorp app window (desktop.py).
        public List<int> FindAppPids()
        {
            return FindScriptPids("desktop.py");
        }

        public Status Check()
        {
            var st = new Status { Port = Port };
            st.Up = IsUp(st.Port);
            int owner = Native.PortOwner(st.Port);
            if (owner > 0 && IsPython(owner))
                st.Pid = owner;
            if (st.Pid == 0)
            {
                List<int> pids = FindServerPids(st.Port);
                if (pids.Count > 0)
                {
                    st.Pid = pids[0];
                    st.Booting = !st.Up;
                }
            }
            if (st.Pid > 0)
            {
                try { st.Started = Process.GetProcessById(st.Pid).StartTime; } catch { }
            }
            var s = Settings();
            object requirePin, pin;
            bool needsPin = !s.TryGetValue("require_pin", out requirePin) || !(requirePin is bool) || (bool)requirePin;
            st.Pin = !needsPin ? "not needed" : (s.TryGetValue("pin", out pin) && pin != null ? pin.ToString() : "—");
            st.Addresses = LanAddresses();
            st.AppRunning = FindAppPids().Count > 0;
            st.Log = ReadLogTail();
            return st;
        }

        static string[] LanAddresses()
        {
            var list = new List<string>();
            try
            {
                foreach (IPAddress a in Dns.GetHostAddresses(Dns.GetHostName()))
                {
                    if (a.AddressFamily != AddressFamily.InterNetwork || IPAddress.IsLoopback(a)) continue;
                    string ip = a.ToString();
                    if (ip.StartsWith("169.254.")) continue;
                    list.Add(ip);
                }
            }
            catch { }
            // home network first, then Tailscale; 172.16/12 is usually a virtual adapter (WSL, Hyper-V)
            list.Sort((a, b) => Rank(a).CompareTo(Rank(b)));
            return list.ToArray();
        }

        static int Rank(string ip)
        {
            if (ip.StartsWith("192.168.")) return 0;
            if (ip.StartsWith("10.")) return 1;
            if (Regex.IsMatch(ip, "^100\\.(6[4-9]|[7-9]\\d|1[01]\\d|12[0-7])\\.")) return 2;
            if (Regex.IsMatch(ip, "^172\\.(1[6-9]|2\\d|3[01])\\.")) return 4;
            return 3;
        }

        string ReadLogTail()
        {
            try
            {
                var info = new FileInfo(LogPath);
                if (!info.Exists) { logSeen = 0; return "(no log yet)"; }
                if (info.Length == logSeen) return null;
                logSeen = info.Length;
                using (var fs = new FileStream(LogPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                {
                    long start = Math.Max(0, fs.Length - 48000);
                    fs.Seek(start, SeekOrigin.Begin);
                    var buf = new byte[fs.Length - start];
                    int read = fs.Read(buf, 0, buf.Length);
                    string text = Encoding.UTF8.GetString(buf, 0, read).Replace("\r\n", "\n");
                    var lines = new List<string>(text.Split('\n'));
                    if (start > 0 && lines.Count > 1) lines.RemoveAt(0);  // the first line was cut in half
                    while (lines.Count > 0 && lines[lines.Count - 1].Trim() == "") lines.RemoveAt(lines.Count - 1);
                    if (lines.Count > 250) lines.RemoveRange(0, lines.Count - 250);
                    return string.Join("\r\n", lines);
                }
            }
            catch { return null; }
        }

        // ---------- actions (each returns null or an error message) ----------

        public string Start()
        {
            int port = Port;
            if (IsUp(port)) return null;
            if (FindServerPids(port).Count == 0)
            {
                string python = Path.Combine(Root, ".venv\\Scripts\\pythonw.exe");
                if (!File.Exists(python)) python = "pythonw.exe";  // Python on PATH
                var psi = new ProcessStartInfo(python, "\"" + Path.Combine(Root, "server.py") + "\"")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WorkingDirectory = Root,
                };
                psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                psi.EnvironmentVariables["PYTHONUTF8"] = "1";
                Process proc;
                try { proc = Process.Start(psi); }
                catch (Exception ex) { return "Could not start Python (" + ex.Message + "). Run setup.bat first."; }
                for (int i = 0; i < 360; i++)  // up to 90 s: tools and MCP servers load first
                {
                    if (IsUp(port)) return null;
                    if (proc.HasExited && FindServerPids(port).Count == 0)
                        return "The server stopped right after starting. The log shows why.";
                    Thread.Sleep(250);
                }
                return "The server is taking unusually long to start. The log shows what it is doing.";
            }
            for (int i = 0; i < 360; i++)  // already starting (from Scorp's window or sign-in)
            {
                if (IsUp(port)) return null;
                Thread.Sleep(250);
            }
            return "The server is running but does not answer on port " + port + ".";
        }

        public string Stop()
        {
            int port = Port;
            for (int round = 0; round < 3; round++)
            {
                List<int> pids = FindServerPids(port);
                if (pids.Count == 0) break;
                foreach (int pid in pids)
                    RunHidden("taskkill.exe", "/PID " + pid + " /T /F");  // with its MCP servers and coding engine
                for (int i = 0; i < 40 && FindServerPids(port).Count > 0; i++)
                    Thread.Sleep(250);
            }
            if (FindServerPids(port).Count > 0 || IsUp(port))
                return "Could not stop the server. It may have been started as administrator.";
            return null;
        }

        public string Restart()
        {
            string error = Stop();
            return error ?? Start();
        }

        // ---------- the Scorp app window (desktop.py starts the server itself when it is not running) ----------

        public string StartApp()
        {
            if (FindAppPids().Count > 0) return null;
            string python = Path.Combine(Root, ".venv\\Scripts\\pythonw.exe");
            if (!File.Exists(python)) python = "pythonw.exe";
            var psi = new ProcessStartInfo(python, "\"" + Path.Combine(Root, "desktop.py") + "\"")
            {
                UseShellExecute = false,
                WorkingDirectory = Root,
            };
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            Process proc;
            try { proc = Process.Start(psi); }
            catch (Exception ex) { return "Could not start the app (" + ex.Message + "). Run setup.bat first."; }
            Thread.Sleep(1500);
            if (proc.HasExited && FindAppPids().Count == 0)
                return "The app closed right after starting.";
            return null;
        }

        public string StopApp()
        {
            if (FindAppPids().Count == 0) return null;
            // ask it to quit like its tray menu does (removes the tray icon); the server keeps running
            try
            {
                string json = File.ReadAllText(Path.Combine(DataDir, "desktop-instance.json"));
                var info = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
                using (var client = new TcpClient())
                {
                    client.Connect("127.0.0.1", Convert.ToInt32(info["port"]));
                    var stream = client.GetStream();
                    byte[] msg = Encoding.UTF8.GetBytes("quit\n");
                    stream.Write(msg, 0, msg.Length);
                    stream.ReadTimeout = 2000;
                    stream.Read(new byte[16], 0, 16);
                }
            }
            catch { }
            for (int i = 0; i < 32 && FindAppPids().Count > 0; i++)
                Thread.Sleep(250);
            // still there (an older desktop.py, or stuck): end it, but not its children, which include a server it started
            foreach (int pid in FindAppPids())
                RunHidden("taskkill.exe", "/PID " + pid + " /F");
            for (int i = 0; i < 20 && FindAppPids().Count > 0; i++)
                Thread.Sleep(250);
            return FindAppPids().Count > 0 ? "Could not close the app." : null;
        }

        public string RestartApp()
        {
            string error = StopApp();
            return error ?? StartApp();
        }

        static void RunHidden(string exe, string args)
        {
            try
            {
                using (var p = Process.Start(new ProcessStartInfo(exe, args) { UseShellExecute = false, CreateNoWindow = true }))
                    p.WaitForExit(10000);
            }
            catch { }
        }

        // ---------- start at sign-in (the same shortcut setup.bat creates) ----------

        public static string StartupLink
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Startup), "Hearth server.lnk"); }
        }

        public void SetStartup(bool on)
        {
            if (!on)
            {
                try { File.Delete(StartupLink); } catch { }
                return;
            }
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            object shell = Activator.CreateInstance(shellType);
            object link = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { StartupLink });
            Type t = link.GetType();
            string exe = Application.ExecutablePath;
            t.InvokeMember("TargetPath", BindingFlags.SetProperty, null, link, new object[] { exe });
            t.InvokeMember("Arguments", BindingFlags.SetProperty, null, link, new object[] { "--start" });
            t.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, link, new object[] { Root });
            t.InvokeMember("IconLocation", BindingFlags.SetProperty, null, link, new object[] { exe + ",0" });
            t.InvokeMember("Description", BindingFlags.SetProperty, null, link, new object[] { "Starts the Scorp server at sign-in" });
            t.InvokeMember("Save", BindingFlags.InvokeMethod, null, link, null);
        }
    }

    class ControlForm : Form
    {
        public const string Title = "Scorp Server";

        static readonly Color Bg = Color.FromArgb(11, 11, 13);
        static readonly Color Card = Color.FromArgb(22, 22, 26);
        static readonly Color Edge = Color.FromArgb(40, 40, 46);
        static readonly Color Fg = Color.FromArgb(242, 242, 245);
        static readonly Color Muted = Color.FromArgb(140, 140, 150);
        static readonly Color Green = Color.FromArgb(52, 199, 89);
        static readonly Color Red = Color.FromArgb(255, 69, 58);
        static readonly Color Amber = Color.FromArgb(255, 176, 32);
        static readonly Color Link = Color.FromArgb(125, 175, 255);

        readonly Server server;
        readonly Label dot, statusText, netValue, pinValue, pidValue, appDot, appText;
        readonly LinkLabel urlLink, logLink;
        readonly FlatButton startBtn, stopBtn, restartBtn, appStartBtn, appStopBtn, appRestartBtn;
        readonly CheckBox autoStart;
        readonly TextBox logBox;
        readonly System.Windows.Forms.Timer timer;
        Status last;
        string busy, appBusy;  // what is happening right now ("Starting server…"), null when idle
        bool polling;

        public ControlForm(Server server)
        {
            this.server = server;
            SuspendLayout();
            AutoScaleDimensions = new SizeF(96F, 96F);
            AutoScaleMode = AutoScaleMode.Dpi;
            Text = Title;
            BackColor = Bg;
            ForeColor = Fg;
            Font = new Font("Segoe UI", 9.75F);
            ClientSize = new Size(480, 664);
            MinimumSize = new Size(460, 620);
            StartPosition = FormStartPosition.CenterScreen;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            Controls.Add(new Label { Text = Title, Font = new Font("Segoe UI Semibold", 17F), AutoSize = true, Location = new Point(20, 16) });
            dot = new Label { Text = "●", Font = new Font("Segoe UI", 12F), ForeColor = Muted, AutoSize = true, Location = new Point(20, 58) };
            statusText = new Label { Text = "Checking…", Font = new Font("Segoe UI Semibold", 10.5F), AutoSize = true, Location = new Point(42, 60) };
            Controls.Add(dot);
            Controls.Add(statusText);

            var card = new Panel { Location = new Point(20, 94), Size = new Size(440, 132), BackColor = Card, Anchor = AnchorStyles.Top | AnchorStyles.Left | AnchorStyles.Right };
            card.Paint += (s, e) => { using (var pen = new Pen(Edge)) e.Graphics.DrawRectangle(pen, 0, 0, card.Width - 1, card.Height - 1); };
            urlLink = new LinkLabel { AutoSize = true, LinkColor = Link, ActiveLinkColor = Fg, LinkBehavior = LinkBehavior.HoverUnderline };
            urlLink.LinkClicked += (s, e) => OpenUrl(urlLink.Text);
            netValue = new Label { AutoSize = true };
            pinValue = new Label { AutoSize = true };
            pidValue = new Label { AutoSize = true };
            AddRow(card, "On this PC", urlLink, 14);
            AddRow(card, "Network", netValue, 42);
            AddRow(card, "Pairing PIN", pinValue, 70);
            AddRow(card, "Process", pidValue, 98);
            Controls.Add(card);

            startBtn = MakeButton("Start", Green, 20, 242);
            stopBtn = MakeButton("Stop", Red, 172, 242);
            restartBtn = MakeButton("Restart", Fg, 324, 242);
            startBtn.Click += (s, e) => Run("Starting server…", server.Start, false);
            stopBtn.Click += (s, e) => Run("Stopping server…", server.Stop, false);
            restartBtn.Click += (s, e) => Run("Restarting server…", server.Restart, false);

            autoStart = new CheckBox { Text = "Start the server when Windows starts", AutoSize = true, Location = new Point(22, 292), Checked = File.Exists(Server.StartupLink) };
            autoStart.CheckedChanged += (s, e) =>
            {
                try { server.SetStartup(autoStart.Checked); }
                catch (Exception ex) { MessageBox.Show(this, "Could not change this: " + ex.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Warning); }
            };
            Controls.Add(autoStart);

            // the Scorp app window (desktop.py)
            appDot = new Label { Text = "●", Font = new Font("Segoe UI", 12F), ForeColor = Muted, AutoSize = true, Location = new Point(20, 334) };
            appText = new Label { Text = "Checking…", Font = new Font("Segoe UI Semibold", 10.5F), AutoSize = true, Location = new Point(42, 336) };
            Controls.Add(appDot);
            Controls.Add(appText);
            appStartBtn = MakeButton("Start app", Green, 20, 368);
            appStopBtn = MakeButton("Stop app", Red, 172, 368);
            appRestartBtn = MakeButton("Restart app", Fg, 324, 368);
            appStartBtn.Click += (s, e) => Run("Starting app…", server.StartApp, true);
            appStopBtn.Click += (s, e) => Run("Stopping app…", server.StopApp, true);
            appRestartBtn.Click += (s, e) => Run("Restarting app…", server.RestartApp, true);

            Controls.Add(new Label { Text = "Server log", ForeColor = Muted, AutoSize = true, Location = new Point(20, 426) });
            logLink = new LinkLabel { Text = "Open log file", AutoSize = true, LinkColor = Link, ActiveLinkColor = Fg, LinkBehavior = LinkBehavior.HoverUnderline,
                                      Anchor = AnchorStyles.Top | AnchorStyles.Right };
            logLink.LinkClicked += (s, e) => { if (File.Exists(server.LogPath)) OpenUrl(server.LogPath); };
            Controls.Add(logLink);
            logLink.Location = new Point(460 - logLink.PreferredWidth, 426);

            var logFrame = new Panel { Location = new Point(20, 450), Size = new Size(440, 170), BackColor = Card, Padding = new Padding(10, 8, 4, 8),
                                       Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right };
            logFrame.Paint += (s, e) => { using (var pen = new Pen(Edge)) e.Graphics.DrawRectangle(pen, 0, 0, logFrame.Width - 1, logFrame.Height - 1); };
            logBox = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Both, WordWrap = false, BorderStyle = BorderStyle.None,
                                   BackColor = Card, ForeColor = Color.FromArgb(196, 196, 206), Font = new Font("Consolas", 8.75F), Dock = DockStyle.Fill };
            logBox.HandleCreated += (s, e) => Native.SetWindowTheme(logBox.Handle, "DarkMode_Explorer", null);  // dark scrollbars
            logFrame.Controls.Add(logBox);
            Controls.Add(logFrame);

            Controls.Add(new Label { Text = "Closing this window keeps the server and the app running.", ForeColor = Muted, AutoSize = true,
                                     Location = new Point(20, 632), Anchor = AnchorStyles.Bottom | AnchorStyles.Left });
            ResumeLayout(false);
            PerformLayout();

            timer = new System.Windows.Forms.Timer { Interval = 2000 };
            timer.Tick += (s, e) => Poll();
            timer.Start();
            Shown += (s, e) => Poll();
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            int dark = 1;  // dark title bar on Windows 10 20H1+ / 11
            Native.DwmSetWindowAttribute(Handle, 20, ref dark, 4);
        }

        static void AddRow(Panel card, string name, Control value, int y)
        {
            card.Controls.Add(new Label { Text = name, ForeColor = Muted, AutoSize = true, Location = new Point(16, y) });
            value.Location = new Point(128, y);
            card.Controls.Add(value);
        }

        FlatButton MakeButton(string text, Color tint, int x, int y)
        {
            var b = new FlatButton(tint, Card, Edge, Fg, Muted)
            {
                Text = text, Location = new Point(x, y), Size = new Size(136, 40), Font = new Font("Segoe UI Semibold", 10F),
            };
            Controls.Add(b);
            return b;
        }

        static void OpenUrl(string target)
        {
            try { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); } catch { }
        }

        void Poll()
        {
            if (polling) return;
            polling = true;
            ThreadPool.QueueUserWorkItem(_ =>
            {
                Status st = null;
                try { st = server.Check(); } catch { }
                try { BeginInvoke((Action)(() => { polling = false; if (st != null) Render(st); })); } catch { }
            });
        }

        void Run(string label, Func<string> work, bool app)
        {
            if (app) appBusy = label; else busy = label;
            if (last != null) Render(last);
            ThreadPool.QueueUserWorkItem(_ =>
            {
                string error;
                try { error = work(); } catch (Exception ex) { error = ex.Message; }
                try
                {
                    BeginInvoke((Action)(() =>
                    {
                        if (app) appBusy = null; else busy = null;
                        if (error != null) MessageBox.Show(this, error, Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                        Poll();
                    }));
                }
                catch { }
            });
        }

        static string Uptime(DateTime started)
        {
            TimeSpan t = DateTime.Now - started;
            if (t.TotalMinutes < 1) return "just started";
            if (t.TotalHours < 1) return "up " + (int)t.TotalMinutes + " min";
            if (t.TotalDays < 1) return "up " + (int)t.TotalHours + " h " + t.Minutes + " min";
            return "up " + (int)t.TotalDays + " d " + t.Hours + " h";
        }

        void Render(Status st)
        {
            last = st;
            bool alive = st.Up || st.Booting;
            if (busy != null)
            {
                dot.ForeColor = Amber;
                statusText.Text = busy;
            }
            else if (st.Up)
            {
                dot.ForeColor = Green;
                statusText.Text = "Server running" + (st.Started.HasValue ? "  ·  " + Uptime(st.Started.Value) : "");
            }
            else if (st.Booting)
            {
                dot.ForeColor = Amber;
                statusText.Text = "Server starting…";
            }
            else
            {
                dot.ForeColor = Red;
                statusText.Text = "Server stopped";
            }
            startBtn.Available = busy == null && !alive;
            stopBtn.Available = busy == null && alive;
            restartBtn.Available = busy == null && alive;

            appDot.ForeColor = appBusy != null ? Amber : st.AppRunning ? Green : Red;
            appText.Text = appBusy ?? (st.AppRunning ? "App running" : "App closed");
            appStartBtn.Available = appBusy == null && !st.AppRunning;
            appStopBtn.Available = appBusy == null && st.AppRunning;
            appRestartBtn.Available = appBusy == null && st.AppRunning;

            urlLink.Text = "http://localhost:" + st.Port;
            var shown = new List<string>();
            for (int i = 0; i < st.Addresses.Length && i < 2; i++) shown.Add(st.Addresses[i] + ":" + st.Port);
            netValue.Text = shown.Count == 0 ? "—" : string.Join("  ·  ", shown);
            pinValue.Text = st.Pin;
            pidValue.Text = st.Pid > 0 ? "PID " + st.Pid : "—";

            if (st.Log != null)
            {
                logBox.Text = st.Log;
                logBox.SelectionStart = logBox.TextLength;
                logBox.ScrollToCaret();
            }
        }
    }

    /// A flat dark button that fades out while unavailable (the system's disabled text is unreadable on dark colours).
    class FlatButton : Button
    {
        readonly Color tint, card, edge, fg, muted;
        bool available = true;

        public FlatButton(Color tint, Color card, Color edge, Color fg, Color muted)
        {
            this.tint = tint; this.card = card; this.edge = edge; this.fg = fg; this.muted = muted;
            FlatStyle = FlatStyle.Flat;
            UseVisualStyleBackColor = false;
            Apply();
        }

        public bool Available
        {
            get { return available; }
            set { if (value != available) { available = value; Apply(); } }
        }

        void Apply()
        {
            BackColor = available ? Mix(card, tint, 0.16) : card;
            ForeColor = available ? fg : Mix(card, muted, 0.6);
            Cursor = available ? Cursors.Hand : Cursors.Default;
            FlatAppearance.BorderColor = available ? Mix(card, tint, 0.5) : edge;
            FlatAppearance.MouseOverBackColor = available ? Mix(card, tint, 0.28) : card;
            FlatAppearance.MouseDownBackColor = available ? Mix(card, tint, 0.4) : card;
        }

        protected override void OnClick(EventArgs e)
        {
            if (available) base.OnClick(e);
        }

        protected override bool ShowFocusCues { get { return false; } }

        static Color Mix(Color a, Color b, double t)
        {
            return Color.FromArgb((int)(a.R + (b.R - a.R) * t), (int)(a.G + (b.G - a.G) * t), (int)(a.B + (b.B - a.B) * t));
        }
    }

    static class Native
    {
        [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr FindWindow(string cls, string title);
        [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr hwnd, int cmd);
        [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr hwnd);
        [DllImport("uxtheme.dll", CharSet = CharSet.Unicode)] public static extern int SetWindowTheme(IntPtr hwnd, string app, string idList);
        [DllImport("dwmapi.dll")] public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
        [DllImport("iphlpapi.dll")] static extern uint GetExtendedTcpTable(IntPtr table, ref int size, bool sort, int af, int tableClass, uint reserved);

        public static void BringToFront(string title)
        {
            IntPtr hwnd = FindWindow(null, title);
            if (hwnd == IntPtr.Zero) return;
            if (IsIconic(hwnd)) ShowWindow(hwnd, 9);  // SW_RESTORE
            SetForegroundWindow(hwnd);
        }

        /// PID of the process listening on a TCP port (IPv4), or 0.
        public static int PortOwner(int port)
        {
            const int AF_INET = 2, TCP_TABLE_OWNER_PID_LISTENER = 3;
            int size = 0;
            GetExtendedTcpTable(IntPtr.Zero, ref size, false, AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0);
            if (size <= 0) return 0;
            IntPtr buf = Marshal.AllocHGlobal(size);
            try
            {
                if (GetExtendedTcpTable(buf, ref size, false, AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0) != 0) return 0;
                int rows = Marshal.ReadInt32(buf);
                for (int i = 0; i < rows; i++)
                {
                    // MIB_TCPROW_OWNER_PID: state, localAddr, localPort, remoteAddr, remotePort, owningPid
                    IntPtr row = IntPtr.Add(buf, 4 + i * 24);
                    int raw = Marshal.ReadInt32(row, 8);
                    int local = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF);  // network byte order
                    if (local == port) return Marshal.ReadInt32(row, 20);
                }
            }
            finally { Marshal.FreeHGlobal(buf); }
            return 0;
        }
    }
}
