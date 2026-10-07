using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Management;
using System.Runtime.InteropServices;
using System.Text;

public class CompanionPanel : Form {
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly string usb, python, controller, statusPath;
    readonly Button start = new Button(), stop = new Button(), eject = new Button();
    readonly Label status = new Label();
    readonly Timer timer = new Timer();
    Process operation;
    string action = "", notice = "Ready to start. First startup checks and copies files; allow a few minutes.";
    bool waiting;
    bool ejectRequested;
    static string Get(Dictionary<string,object> d,string key) { return d!=null && d.ContainsKey(key) ? Convert.ToString(d[key]) : ""; }
    static string Quote(string s) { return "\""+s.TrimEnd('\\')+"\""; }
    public CompanionPanel(string root) {
        usb=Path.GetFullPath(root);
        var config=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(usb,"portable.json")));
        string identity=Get(config,"identity");
        if (!System.Text.RegularExpressions.Regex.IsMatch(identity,"^[0-9a-f]{32}$")) throw new Exception("Invalid companion identity");
        statusPath=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"PrometheusPortable",identity,"status.json");
        python=Path.GetFullPath(Path.Combine(usb,Get(config,"release"),"runtime","python","python.exe"));
        controller=Path.Combine(usb,"Portable-Prometheus.py");
        bool hardDrive=Directory.GetParent(usb).Name.StartsWith("Prometheus-Portable-");
        Text=hardDrive ? "Prometheus — Hard Drive" : "Prometheus — Flash Drive"; ClientSize=new Size(500,235); StartPosition=FormStartPosition.CenterScreen;
        FormBorderStyle=FormBorderStyle.FixedSingle; MaximizeBox=false; Font=new Font("Segoe UI",10);
        var title=new Label {Text="Prometheus Companion",Location=new Point(18,14),Size=new Size(385,28),Font=new Font("Segoe UI",13,FontStyle.Bold)};
        status.Location=new Point(18,48);status.Size=new Size(464,68);status.Text=notice;
        start.Location=new Point(18,122);start.Size=new Size(138,42);start.Text="Start Prometheus\nCompanion";
        stop.Location=new Point(166,122);stop.Size=new Size(128,42);stop.Text="Stop Prometheus\nCompanion";stop.Enabled=false;
        eject.Location=new Point(304,122);eject.Size=new Size(178,42);eject.Text=hardDrive ? "Eject Hard Drive" : "Eject Flash Drive";
        var foot=new Label {Text="Eject stops and saves first, then requests Windows safe removal.\nClosing this panel leaves a running companion on.",Location=new Point(18,176),Size=new Size(464,45),Font=new Font("Segoe UI",8)};
        Controls.AddRange(new Control[] {title,status,start,stop,eject,foot});
        start.Click+=delegate {
            var state=State(); string phase=Phase(state);
            Begin(phase=="Running" ? "chat" : phase=="NeedsAttention" ? "recover" : "start");
        };
        stop.Click+=delegate { Begin("stop"); };
        eject.Click+=delegate {
            string phase=Phase(State());
            if(phase=="Running") {ejectRequested=true;Begin("eject");}
            else if(phase=="NeedsAttention") {ejectRequested=true;Begin("recover");}
            else {ejectRequested=true;Begin("verify-eject");}
        };
        timer.Interval=1000;timer.Tick+=delegate { RefreshState(); };timer.Start();
        FormClosed+=delegate {timer.Stop();timer.Dispose();};
    }
    void RequestWindowsEject() {
        try {
            string drive=Path.GetPathRoot(usb).TrimEnd('\\');
            SafeRemoval.Eject(drive);
            status.Text="Prometheus saved and stopped. Windows confirmed safe removal. You can unplug this drive.";
            start.Enabled=false;stop.Enabled=false;eject.Enabled=false;
            MessageBox.Show(this,status.Text,"Windows safe removal requested");
            Close();
        } catch(Exception e) {ejectRequested=false;MessageBox.Show(this,e.Message+"\nDo not unplug until Windows confirms safe removal.","Drive removal needs attention",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
    }
    Dictionary<string,object> State() {
        try { return File.Exists(statusPath) ? json.Deserialize<Dictionary<string,object>>(File.ReadAllText(statusPath)) : null; }
        catch { return null; }
    }
    string Phase(Dictionary<string,object> state) {
        string phase=Get(state,"state");
        DateTime stamp;
        if (phase=="Running" && (!DateTime.TryParse(Get(state,"checkedAt"),out stamp) || (DateTime.UtcNow-stamp.ToUniversalTime()).TotalSeconds>15)) return "NeedsAttention";
        string startupStamp=Get(state,"checkedAt");if(startupStamp=="") startupStamp=Get(state,"startedAt");
        if ((phase=="Staged" || phase=="Starting") && DateTime.TryParse(startupStamp,out stamp) && (DateTime.UtcNow-stamp.ToUniversalTime()).TotalSeconds>120) return "NeedsAttention";
        return phase;
    }
    void Begin(string name) {
        if (operation!=null) return;
        if (!File.Exists(python)) {status.Text="Drive unavailable. Reconnect the companion drive.";return;}
        try {
            var info=new ProcessStartInfo(python,"-X utf8 "+Quote(controller)+" "+(name=="eject" ? "stop" : name)+" --usb "+Quote(usb));
            info.WorkingDirectory=Path.GetTempPath();info.UseShellExecute=false;info.CreateNoWindow=true;
            info.RedirectStandardOutput=true;info.RedirectStandardError=true;
            operation=Process.Start(info);action=name;start.Enabled=false;stop.Enabled=false;eject.Enabled=false;
            notice=name=="start" ? "Checking and staging files. Please wait; first startup takes a few minutes."
                : (name=="stop" || name=="eject") ? "Stopping and verifying the save. Finish chat with /exit. Do not unplug yet."
                : name=="recover" ? "Checking and saving recovered work. Do not unplug yet." : "Opening your chat window...";
            status.Text=notice;
        } catch(Exception e) {operation=null;MessageBox.Show(this,e.Message,"Prometheus Companion");}
    }
    void RefreshState() {
        if (operation!=null) {
            if (!operation.HasExited) {
                var progress=State();
                if(Get(progress,"state")=="Stopping") status.Text="Stopping: "+Get(progress,"activeClients")+" chat window(s), up to "+Get(progress,"shutdownSecondsRemaining")+" seconds left. Finish chat with /exit. Do not unplug.";
                return;
            }
            string message=operation.StandardOutput.ReadToEnd()+operation.StandardError.ReadToEnd();
            int code=operation.ExitCode;operation.Dispose();operation=null;
            if (code!=0) {ejectRequested=false;notice="Action needs attention. Existing backups remain preserved.";MessageBox.Show(this,message,"Prometheus Companion",MessageBoxButtons.OK,MessageBoxIcon.Warning);}
            else if (action=="start") waiting=true;
            else if(ejectRequested) {
                string ejectPhase=Phase(State());
                if(ejectPhase=="ReadyToEject") {ejectRequested=false;RequestWindowsEject();return;}
                if(ejectPhase=="StoppedVerified" && action!="verify-eject") {Begin("verify-eject");return;}
                ejectRequested=false;notice="Windows release verification did not pass. Do not unplug.";
            }
        }
        string phase=Phase(State());start.Enabled=true;stop.Enabled=false;eject.Enabled=true;start.Text="Start Prometheus\nCompanion";
        if (phase=="Running") {waiting=false;stop.Enabled=true;status.Text="Prometheus is running. Start opens another chat. Stop saves your work; Eject stops and saves first.";}
        else if (phase=="ReadyToEject") status.Text="Saved and Windows release check passed. Eject is ready for the normal Windows removal request.";
        else if (phase=="StoppedVerified") status.Text="Prometheus is saved and stopped. Windows release verification is still required before unplugging.";
        else if (phase=="NeedsAttention") {waiting=false;start.Text="Recover Saved Work";status.Text="Companion stopped or lost contact. Reconnect its drive, then recover saved work.";}
        else if (waiting || phase=="Staged" || phase=="Starting" || phase=="Stopping") {start.Enabled=false;eject.Enabled=false;status.Text="Starting or saving. Please wait; do not unplug the drive.";}
        else status.Text=notice;
    }
    [STAThread]
    public static int Main(string[] args) {
        try {
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
            if (args.Length==0) throw new Exception("Open the Companion Window launcher on your drive.");
            using(var panel=new CompanionPanel(args[0])) {
                if (args.Length>1 && args[1]=="--self-test") {
                    if (panel.Controls.Count!=6 || !panel.start.Text.StartsWith("Start Prometheus") || !panel.stop.Text.StartsWith("Stop Prometheus") || panel.stop.Enabled || !panel.eject.Text.StartsWith("Eject ")) throw new Exception("Panel layout check failed");
                    var test=new Dictionary<string,object>();test["state"]="Running";test["checkedAt"]=DateTime.UtcNow.AddMinutes(-2).ToString("o");
                    if(panel.Phase(test)!="NeedsAttention") throw new Exception("Stale-health check failed");
                    test["checkedAt"]=DateTime.UtcNow.ToString("o");if(panel.Phase(test)!="Running") throw new Exception("Fresh-health check failed");
                    panel.Show(); panel.Refresh();
                    using(var preview=new Bitmap(panel.Width,panel.Height)) {panel.DrawToBitmap(preview,panel.Bounds);preview.Save(Path.Combine(Path.GetDirectoryName(Application.ExecutablePath),"Companion-Panel-Preview.png"));}
                    panel.Close();
                    File.WriteAllText(Path.Combine(Path.GetDirectoryName(Application.ExecutablePath),"panel-self-test.txt"),"PASS: native panel constructed; three buttons; safe initial state; stale-health recovery and fresh-health checks.");
                } else Application.Run(panel);
            }
            return 0;
        } catch(Exception e) {MessageBox.Show(e.Message,"Prometheus Companion",MessageBoxButtons.OK,MessageBoxIcon.Error);return 1;}
    }
}


public static class SafeRemoval {
    [DllImport("cfgmgr32.dll",CharSet=CharSet.Unicode)] static extern uint CM_Locate_DevNodeW(out uint node,string id,uint flags);
    [DllImport("cfgmgr32.dll")] static extern uint CM_Get_Parent(out uint parent,uint node,uint flags);
    [DllImport("cfgmgr32.dll",CharSet=CharSet.Unicode)] static extern uint CM_Get_Device_IDW(uint node,StringBuilder id,uint length,uint flags);
    [DllImport("cfgmgr32.dll",CharSet=CharSet.Unicode)] static extern uint CM_Request_Device_EjectW(uint node,out int veto,StringBuilder name,uint length,uint flags);
    public static uint Resolve(string drive) {
        if(!System.Text.RegularExpressions.Regex.IsMatch(drive,"^[D-Z]:$")) throw new Exception("Refusing to remove an unrecognized or system drive.");
        var disks=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        using(var logical=new ManagementObject("Win32_LogicalDisk.DeviceID='"+drive+"'")) {
            using(var partitions=logical.GetRelated("Win32_DiskPartition")) {
                foreach(ManagementObject partition in partitions) using(partition) using(var related=partition.GetRelated("Win32_DiskDrive")) {
                    foreach(ManagementObject disk in related) using(disk) {disks.Add(Convert.ToString(disk["PNPDeviceID"]));}
                }
            }
        }
        if(disks.Count!=1) throw new Exception("Cannot uniquely identify the physical drive. Use Windows Safely Remove Hardware.");
        string id="";foreach(string value in disks) id=value;
        uint node;if(CM_Locate_DevNodeW(out node,id,0)!=0) throw new Exception("Windows device is unavailable.");
        for(int depth=0;depth<8;depth++) {
            var name=new StringBuilder(1024);
            if(CM_Get_Device_IDW(node,name,1024,0)!=0) break;
            string current=name.ToString();
            if(current.StartsWith("USBSTOR\\",StringComparison.OrdinalIgnoreCase) || current.StartsWith("USB\\VID_",StringComparison.OrdinalIgnoreCase)) return node;
            uint parent;if(CM_Get_Parent(out parent,node,0)!=0) break;node=parent;
        }
        throw new Exception("This is not a uniquely identified USB storage device. Use Windows Safely Remove Hardware.");
    }
    public static void Eject(string drive) {
        uint node=Resolve(drive);int veto;var name=new StringBuilder(1024);
        uint result=CM_Request_Device_EjectW(node,out veto,name,1024,0);
        if(result!=0) throw new Exception("Windows blocked safe removal (code "+result+", reason "+veto+"): "+name+". Close files using this drive and try again.");
    }
}


