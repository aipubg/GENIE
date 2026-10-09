using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Genie.Desktop.Services;

/// <summary>
/// Owns the GENIE backend process. One authoritative daemon, launched with
/// pythonw.exe only, always hidden, and only ever stopped by PID we recorded.
/// </summary>
public sealed class BackendProcess : IDisposable
{
    // CREATE_NO_WINDOW: the backend must never surface a console.
    private const uint CREATE_NO_WINDOW = 0x08000000;

    public int? Pid { get; private set; }
    public bool StartedByUs { get; private set; }

    private readonly string _pythonw;
    private readonly string _entry;

    public BackendProcess(string runtimeRoot)
    {
        _pythonw = Path.Combine(runtimeRoot, "python", "pythonw.exe");
        _entry = Path.Combine(runtimeRoot, "app", "backend_entry.py");
    }

    public bool CanStart => File.Exists(_pythonw) && File.Exists(_entry);

    public bool Start()
    {
        if (!CanStart || Pid is not null) return false;

        var psi = new ProcessStartInfo
        {
            FileName = _pythonw,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            WorkingDirectory = Path.GetDirectoryName(_pythonw)!,
            RedirectStandardOutput = false,
            RedirectStandardError = false,
        };
        // Quoted arguments keep the script path intact.
        psi.ArgumentList.Add(_entry);

        var proc = Process.Start(psi);
        if (proc is null) return false;
        Pid = proc.Id;
        StartedByUs = true;
        return true;
    }

    /// <summary>Stop only the PID we recorded, and only if it is still ours.</summary>
    public void Stop()
    {
        if (Pid is not int pid) return;
        try
        {
            var proc = Process.GetProcessById(pid);
            if (!proc.HasExited) proc.Kill(entireProcessTree: true);
        }
        catch (ArgumentException)
        {
            // already gone
        }
        finally
        {
            Pid = null;
            StartedByUs = false;
        }
    }

    public bool IsRunning =>
        Pid is int pid && Process.GetProcessById(pid) is { HasExited: false };

    public void Dispose() => Stop();
}
