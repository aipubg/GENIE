using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Threading;

namespace Genie.Desktop.Services;

/// <summary>
/// One authoritative lifecycle owner for the desktop client.
///
///  * single instance via a named Mutex; a second launch activates the running
///    window instead of starting a second frontend or a second backend
///  * starts the embedded backend with pythonw.exe only, always hidden
///  * remembers the exact PID and stops only that process tree
///  * never kills by image name
/// </summary>
public sealed class BackendLifecycle : IDisposable
{
    private const string MutexName = @"Local\Genie.Desktop.SingleInstance";
    private const string ActivateEventName = @"Local\Genie.Desktop.Activate";
    private const string ShutdownEventName = @"Local\Genie.Desktop.Shutdown";
    // A request that arrives before any instance is listening must not be
    // lost. It is parked here, and the starting instance sees it the moment it
    // begins listening - rather than the caller hammering requests and hoping.
    private const string PendingShutdownEventName = @"Local\Genie.Desktop.ShutdownPending";

    private Mutex? _mutex;
    private string? _foreignBackendRoot;
    private EventWaitHandle? _activateEvent;
    private EventWaitHandle? _shutdownEvent;
    private EventWaitHandle? _pendingShutdownEvent;
    private CancellationTokenSource? _listenerCts;
    private readonly GenieBackend _backend = new();
    private readonly BackendClient _client = new();
    private Thread? _listenerThread;

    public bool IsPrimaryInstance { get; private set; }
    public bool BackendRunning { get; private set; }
    public int? BackendPid => _backend.Pid;
    public Action? ActivateRequested { get; set; }

    /// <summary>A remote request to close (installer / uninstaller). The running
    /// instance performs its own canonical full exit rather than being killed,
    /// because killing leaves the backend running and files locked.</summary>
    public Action? ShutdownRequested { get; set; }

    /// <summary>True when this process owns the single-instance mutex.</summary>
    public bool TryAcquireSingleInstance()
    {
        _mutex = new Mutex(true, MutexName, out var created);
        IsPrimaryInstance = created;
        if (!created) return false;

        _activateEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ActivateEventName);
        _shutdownEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ShutdownEventName);
        // ManualReset: if a caller parked a request before we existed, this
        // handle is already signalled and the listener fires immediately.
        _pendingShutdownEvent = new EventWaitHandle(false, EventResetMode.ManualReset,
                                                    PendingShutdownEventName);
        StartActivationListener();
        return true;
    }

    /// <summary>True when a shutdown was requested before (or during) startup.
    /// The startup path checks this and performs the canonical exit instead of
    /// carrying on and showing a window.</summary>
    public bool ShutdownWasRequested { get; private set; }

    /// <summary>Clear the parked request. Called once the instance is up, so a
    /// request from a previous uninstall cannot kill a later normal launch.
    /// </summary>
    public void ClearPendingShutdown()
    {
        try { _pendingShutdownEvent?.Reset(); } catch { /* already released */ }
    }

    /// <summary>Ask an already-running instance to show itself.</summary>
    public static bool SignalExistingInstance()
        => Signal(ActivateEventName);

    /// <summary>Ask the running instance to close itself cleanly.
    /// Returns false when no instance is listening.</summary>
    public static bool SignalShutdown()
        => Signal(ShutdownEventName);

    /// <summary>
    /// Ask GENIE to close, whether or not it is listening yet.
    ///
    /// When an instance is up the request is delivered directly. When it is not
    /// - GENIE mid-startup, or not yet launched - the request is parked in the
    /// pending event so the starting instance honours it as soon as it can.
    /// Returns true if either path succeeded.
    /// </summary>
    public static bool RequestShutdown()
    {
        if (SignalShutdown()) return true;
        try
        {
            using var pending = new EventWaitHandle(false, EventResetMode.ManualReset,
                                                    PendingShutdownEventName);
            pending.Set();
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static bool Signal(string name)
    {
        try
        {
            using var ev = EventWaitHandle.OpenExisting(name);
            ev.Set();
            return true;
        }
        catch (WaitHandleCannotBeOpenedException)
        {
            return false;
        }
    }

    private void StartActivationListener()
    {
        _listenerCts = new CancellationTokenSource();
        var ct = _listenerCts.Token;
        _listenerThread = new Thread(() =>
        {
            var handles = new WaitHandle[]
            {
                _activateEvent!, _shutdownEvent!, _pendingShutdownEvent!
            };
            while (!ct.IsCancellationRequested)
            {
                switch (WaitHandle.WaitAny(handles, 500))
                {
                    case 0: ActivateRequested?.Invoke(); break;
                    case 1:
                    case 2:
                        // Parked or direct shutdown request.
                        ShutdownWasRequested = true;
                        ClearPendingShutdown();
                        if (ShutdownRequested is not null)
                        {
                            // Hand the exit back to the UI thread: a
                            // dispatcher shutdown from a worker thread is not
                            // allowed.
                            ShutdownRequested.Invoke();
                            return;
                        }
                        // No handler yet - we are still starting up. Do NOT
                        // return: leaving the loop here would mean this
                        // instance never listens again, and every later
                        // shutdown request would be silently dropped (the
                        // frontend then ignores the installer and stays up).
                        // The flag is recorded and the startup path honours it
                        // as soon as the backend is up.
                        break;
                }
            }
        })
        { IsBackground = true, Name = "Genie.ActivationListener" };
        _listenerThread.Start();
    }

    /// <summary>Start the backend unless one is already healthy, then wait for health.</summary>
    public async Task<bool> EnsureBackendAsync(TimeSpan timeout)
    {
        // A healthy backend on the port is only reused if it is THIS build's
        // backend. Reusing a foreign one is exactly how the owner ended up with
        // a new UI on top of a stale runtime: the window looked current while
        // every provider/voice answer came from the old code.
        if (await _client.HealthAsync())
        {
            if (await IsCurrentBuildBackendAsync())
            {
                BackendRunning = true;
                return true;
            }
            if (!StopForeignBackend()) return false;
            await Task.Delay(700);
        }
        if (!_backend.Start()) return false;
        BackendRunning = await _backend.WaitForHealthAsync(timeout);
        return BackendRunning;
    }

    /// <summary>True when the backend already listening is running from the same
    /// runtime root this frontend would have started itself.</summary>
    private async Task<bool> IsCurrentBuildBackendAsync()
    {
        // The root this build would start its own backend from. Resolved here
        // because the backend has not been started yet at this point.
        var expected = GenieBackend.FindRuntimeRoot();
        _foreignBackendRoot = null;
        if (string.IsNullOrEmpty(expected)) return false;

        using var status = await _client.GetStatusAsync();
        if (status is null) return false;

        var root = status.RootElement;

        // A GENIE daemon that cannot even describe its build predates build
        // identity — it cannot be this build's backend, so it must not be reused.
        if (!root.TryGetProperty("build", out var build) ||
            build.ValueKind != System.Text.Json.JsonValueKind.Object)
            return false;

        // Strongest signal: where the running backend lives.
        var actual = build.TryGetProperty("backend_root", out var br)
            ? br.GetString() ?? ""
            : "";
        if (!string.IsNullOrWhiteSpace(actual))
            _foreignBackendRoot = actual;

        var theirsFingerprint = build.TryGetProperty("source_fingerprint", out var fp)
            ? fp.GetString() ?? "" : "";
        var oursFingerprint = LocalRuntimeCommit(expected!);
        return !string.IsNullOrWhiteSpace(actual)
            && string.Equals(Norm(actual), Norm(expected!), StringComparison.OrdinalIgnoreCase)
            && oursFingerprint.Length == 64 && string.Equals(theirsFingerprint, oursFingerprint, StringComparison.Ordinal);

    }

    /// <summary>The source-file fingerprint recorded in this build's runtime manifest.</summary>
    private static string LocalRuntimeCommit(string runtimeRoot)
    {
        try
        {
            var path = Path.Combine(runtimeRoot, "RUNTIME_MANIFEST.json");
            if (!File.Exists(path)) return "";
            using var doc = System.Text.Json.JsonDocument.Parse(File.ReadAllText(path));
            return doc.RootElement.TryGetProperty("source_fingerprint", out var c)
                ? (c.GetString() ?? "").Trim()
                : "";
        }
        catch { return ""; }
    }

    private static string Norm(string p)
        => Path.GetFullPath(p).TrimEnd('\\', '/').Replace('/', '\\');

    /// <summary>
    /// Stops a GENIE backend that belongs to a DIFFERENT build.
    ///
    /// Safety: the process is only touched after the endpoint proves it is a
    /// GENIE daemon (it answers /api/status with a GENIE fingerprint), and it is
    /// stopped by the exact PID that owns the port. No image-name kills, so an
    /// unrelated python/browser process is never in scope.
    /// </summary>
    private bool StopForeignBackend()
    {
        var pid = PidListeningOnPort(8787);
        if (pid is null || pid.Value <= 0 || string.IsNullOrWhiteSpace(_foreignBackendRoot)) return false;
        try
        {
            using var target = Process.GetProcessById(pid.Value);
            var executable = target.MainModule?.FileName;
            var allowed = new[] { "python.exe", "pythonw.exe" }.Any(name =>
                string.Equals(Norm(executable ?? ""), Norm(Path.Combine(_foreignBackendRoot!, "python", name)), StringComparison.OrdinalIgnoreCase));
            if (!allowed || !File.Exists(Path.Combine(_foreignBackendRoot!, "app", "core", "lifecycle.py"))) return false;
            if (PidListeningOnPort(8787) != pid) return false;
            using var p = Process.Start(new ProcessStartInfo("taskkill")
            {
                Arguments = $"/PID {pid.Value} /T /F",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            });
            p?.WaitForExit(5000);
            return p is not null && p.HasExited && p.ExitCode == 0;
        }
        catch { return false; }
    }

    /// <summary>The PID listening on a TCP port, or null. netstat is used only to
    /// resolve the owner of GENIE's own port; it never selects processes by name.</summary>
    private static int? PidListeningOnPort(int port)
    {
        try
        {
            using var p = Process.Start(new ProcessStartInfo("netstat")
            {
                Arguments = "-ano -p TCP",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            });
            if (p is null) return null;
            var text = p.StandardOutput.ReadToEnd();
            p.WaitForExit(5000);
            foreach (var raw in text.Split('\n'))
            {
                var line = raw.Trim();
                if (!line.StartsWith("TCP", StringComparison.OrdinalIgnoreCase)) continue;
                if (!line.Contains("LISTENING", StringComparison.OrdinalIgnoreCase)) continue;
                var parts = line.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length < 5) continue;
                var local = parts[1];
                if (!local.EndsWith(":" + port)) continue;
                if (int.TryParse(parts[^1], out var pid)) return pid;
            }
        }
        catch { /* unresolvable — leave it alone */ }
        return null;
    }

    /// <summary>Stop everything this lifecycle owns. Returns true only when
    /// every owned process is confirmed gone, so a caller can distinguish
    /// "stopped" from "still running" instead of assuming success.</summary>
    public bool StopBackend()
    {
        var stopped = _backend.Stop();
        BackendRunning = false;
        return stopped;
    }

    /// <summary>Every backend process currently owned by this lifecycle.</summary>
    public IReadOnlyCollection<int> OwnedPids => _backend.OwnedPids;

    public void Dispose()
    {
        _listenerCts?.Cancel();
        try { _listenerThread?.Join(TimeSpan.FromSeconds(1)); } catch { /* ignore */ }
        _listenerCts?.Dispose();
        // Both events must be released. Leaking the shutdown event leaves the
        // kernel object alive after exit, so an installer's "is GENIE running?"
        // check would keep succeeding - and its close request would appear to
        // be delivered to nobody.
        _activateEvent?.Dispose();
        _shutdownEvent?.Dispose();
        _pendingShutdownEvent?.Dispose();
        _mutex?.Dispose();
        _backend.Dispose();
        _client.Dispose();
    }
}
