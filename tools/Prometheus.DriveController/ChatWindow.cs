using System.Runtime.InteropServices;
namespace Prometheus.DriveController;
internal static class ChatWindow
{
    [DllImport("user32.dll", CharSet=CharSet.Unicode)]
    static extern IntPtr FindWindow(string? className, string windowName);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    public static bool Focus(string drive)
    {
        var handle=FindWindow(null,"Prometheus Chat "+drive.TrimEnd('\\'));
        if(handle==IntPtr.Zero)return false;
        ShowWindow(handle,9);
        return SetForegroundWindow(handle);
    }
}
