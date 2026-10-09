using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text;

namespace Genie.Desktop.Services;

/// <summary>Read a process image path without loading MainModule (which throws
/// or hangs on processes we do not own).</summary>
internal static class NativeProcess
{
    private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint desiredAccess, bool inheritHandle, int pid);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool QueryFullProcessImageName(IntPtr hProcess, uint flags,
                                                         StringBuilder lpExeName, ref uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr hObject);

    public static string? TryGetPath(int pid)
    {
        var handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
        if (handle == IntPtr.Zero) return null;
        try
        {
            var sb = new StringBuilder(1024);
            uint size = (uint)sb.Capacity;
            return QueryFullProcessImageName(handle, 0, sb, ref size) ? sb.ToString() : null;
        }
        catch
        {
            return null;
        }
        finally
        {
            CloseHandle(handle);
        }
    }
}

/// <summary>
/// One Windows-native lifecycle authority for the GENIE backend.
///
/// Rules (from the migration audit, section H):
///   * launch pythonw.exe only - never python.exe, cmd.exe or any shell
///   * CREATE_NO_WINDOW + hidden: no console, ever
///   * remember exactly what we started and stop only that
///   * bounded startup with a health probe
///
/// Ownership model (revised after the upgrade-orphan defect):
///
///   Tracking ONE parent PID and calling Kill(entireProcessTree) is not
///   sufficient. If that parent has already exited, `HasExited` is true and the
///   kill is skipped - leaving its workers alive and reparented. Shutdown was
///   therefore timing-dependent: it worked when the parent happened to be alive
///   and leaked six pythonw.exe when it was not.
///
///   So this class tracks the full OWNED SET, and identifies it by the
///   executable path under our own embedded runtime - not by image name. Any
///   process running our embedded interpreter belongs to this install; a
///   pythonw.exe anywhere else on the machine is never touched.
/// </summary>
public sealed class GenieBackend : IDisposable
{
    private const uint CREATE_NO_WINDOW = 0x08000000;
    private const string HealthUrl = "http://127.0.0.1:8787/health";
    private static readonly TimeSpan StopTimeout = TimeSpan.FromSeconds(20);

    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(3) };

    private readonly HashSet<int> _owned = new();

    public int? Pid { get; private set; }
    public string? RuntimeRoot { get; private set; }
    public bool StartedByUs { get; private set; }

    /// <summary>Every backend process this lifecycle currently owns.</summary>
    public IReadOnlyCollection<int> OwnedPids
    {
        get { lock (_owned) { return _owned.ToArray(); } }
    }

    /// <summary>Find the packaged embedded runtime.</summary>
    ///
    /// Looks in three places, in order:
    ///   1. <c>resources/backend-runtime</c> beside the executable — the
    ///          layout the installer writes and that a <c>dotnet build</c>
    ///          may also stage via a post-build junction.
    ///   2. <c>backend-runtime</c> beside the executable — fallback for any
    ///          layout that drops the runtime directly into the output.
    ///   3. Walk up the directory tree looking for <c>backend-dist/backend-runtime</c>
    ///          — the source-tree layout. The csproj sits under
    ///          <c>ui/windows/Genie.Desktop/</c> and the build output is at most
    ///          5 levels below the repo root, so 8 levels up is a safe bound.
    ///          This makes the source build self-sufficient: no junction, no
    ///          post-build step, no manual staging.
    public static string? FindRuntimeRoot()
    {
        var baseDir = AppContext.BaseDirectory;
        var besideExe = new[]
        {
            Path.Combine(baseDir, "resources", "backend-runtime"),
            Path.Combine(baseDir, "backend-runtime"),
        };
        foreach (var c in besideExe)
        {
            if (File.Exists(Path.Combine(c, "python", "pythonw.exe"))) return c;
        }

        // Source-tree: walk up to find the repo-rooted backend-dist.
        var dir = new DirectoryInfo(baseDir);
        for (int i = 0; i < 8 && dir is not null; i++, dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "backend-dist", "backend-runtime");
            if (File.Exists(Path.Combine(candidate, "python", "pythonw.exe"))) return candidate;
        }
        return null;
    }

    public bool CanStart => FindRuntimeRoot() is not null;

    /// <summary>Directories every one of our backend processes may live under.
    /// Ownership is proven by path, never by image name.
    ///
    /// A junction is resolved as well as the literal path. In a build tree
    /// `resources\backend-runtime` is a junction to the repo's
    /// backend-dist\backend-runtime, and GetFullPath does NOT resolve it - so
    /// the anchor pointed at the junction while the processes' real image path
    /// was the target, ownership matched nothing, and Stop() killed nothing.
    /// </summary>
    private static List<string> PythonAnchors(string? runtimeRoot)
    {
        var anchors = new List<string>();
        if (string.IsNullOrEmpty(runtimeRoot)) return anchors;

        void Add(string? dir)
        {
            if (string.IsNullOrEmpty(dir)) return;
            try
            {
                var a = Path.GetFullPath(dir!).TrimEnd('\\') + '\\';
                if (!anchors.Contains(a, StringComparer.OrdinalIgnoreCase))
                {
                    anchors.Add(a);
                }
            }
            catch
            {
                // ignore unusable path
            }
        }

        string rootFull;
        try
        {
            rootFull = Path.GetFullPath(runtimeRoot!);
        }
        catch
        {
            return anchors;
        }

        Add(Path.Combine(rootFull, "python"));

        // The junction is on the runtime ROOT (resources\backend-runtime ->
        // backend-dist\backend-runtime), not on the python subdir, so the
        // resolution has to happen here. Without this the anchor points at the
        // junction while the processes report the target, ownership matches
        // nothing and Stop() silently kills nothing.
        try
        {
            var target = new DirectoryInfo(rootFull).ResolveLinkTarget(true);
            if (target is not null) Add(Path.Combine(target.FullName, "python"));
        }
        catch
        {
            // not a link, or unreadable - the literal anchor is still valid
        }
        return anchors;
    }

    private static bool IsUnderAnchor(string? path, List<string> anchors)
    {
        if (string.IsNullOrEmpty(path) || anchors.Count == 0) return false;
        try
        {
            var full = Path.GetFullPath(path!);
            return anchors.Any(a => full.StartsWith(a, StringComparison.OrdinalIgnoreCase));
        }
        catch
        {
            return false;
        }
    }

    /// <summary>A process is ours only if it is running OUR embedded
    /// interpreter. Nothing else on the machine is ever stopped.</summary>
    public static bool IsOurs(int pid, string? runtimeRoot)
        => IsUnderAnchor(NativeProcess.TryGetPath(pid), PythonAnchors(runtimeRoot));

    public bool Start()
    {
        if (Pid is not null) return true;                 // already ours
        var root = FindRuntimeRoot();
        if (root is null) return false;
        RuntimeRoot = root;

        var pythonw = Path.Combine(root, "python", "pythonw.exe");
        var entry = Path.Combine(root, "app", "backend_entry.py");
        if (!File.Exists(pythonw) || !File.Exists(entry)) return false;

        var psi = new ProcessStartInfo
        {
            FileName = pythonw,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            WorkingDirectory = Path.GetDirectoryName(pythonw)!,
        };
        psi.ArgumentList.Add(entry);

        var proc = Process.Start(psi);
        if (proc is null) return false;
        Pid = proc.Id;
        StartedByUs = true;
        DiscoverOwned();
        return true;
    }

    /// <summary>
    /// Refresh the owned set. Called at start, after health, and again
    /// immediately before stopping, so workers spawned at any point are known.
    /// </summary>
    public void DiscoverOwned()
    {
        var root = RuntimeRoot ?? FindRuntimeRoot();
        var anchors = PythonAnchors(root);
        if (anchors.Count == 0) return;

        if (Pid is int p) lock (_owned) { _owned.Add(p); }

        foreach (var proc in Process.GetProcesses())
        {
            try
            {
                var path = NativeProcess.TryGetPath(proc.Id);
                if (IsUnderAnchor(path, anchors))
                {
                    lock (_owned) { _owned.Add(proc.Id); }
                }
            }
            catch
            {
                // process vanished mid-enumeration
            }
            finally
            {
                proc.Dispose();
            }
        }
    }

    public async Task<bool> WaitForHealthAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            try
            {
                using var resp = await Http.GetAsync(HealthUrl);
                if (resp.IsSuccessStatusCode)
                {
                    // Workers are usually up by now; remember them while the
                    // tree is still intact.
                    DiscoverOwned();
                    return true;
                }
            }
            catch (Exception)
            {
                // backend still coming up
            }
            await Task.Delay(500);
        }
        return false;
    }

    private static bool IsAlive(int pid)
    {
        try
        {
            using var p = Process.GetProcessById(pid);
            return !p.HasExited;
        }
        catch
        {
            return false;
        }
    }

    /// <summary>
    /// Stop every process this lifecycle owns, deterministically.
    ///
    /// Not gated on the parent still being alive: the owned set is rediscovered
    /// here, each member is verified by executable path, and each is stopped
    /// individually. Returns true only when everything we own is confirmed gone,
    /// so a caller can tell "stopped" from "gave up".
    /// </summary>
    public bool Stop()
    {
        DiscoverOwned();

        List<int> targets;
        lock (_owned) { targets = _owned.ToList(); }
        if (targets.Count == 0)
        {
            ClearState();
            return true;
        }

        var root = RuntimeRoot ?? FindRuntimeRoot();
        var deadline = DateTime.UtcNow + StopTimeout;

        while (DateTime.UtcNow < deadline)
        {
            var alive = targets.Where(IsAlive).ToList();
            if (alive.Count == 0) break;

            foreach (var pid in alive)
            {
                if (!IsOurs(pid, root)) continue;      // never kill by image name
                TryKill(pid);
            }
            Thread.Sleep(250);
        }

        var remaining = targets.Where(IsAlive).ToList();
        if (remaining.Count > 0)
        {
            // Do not lie about it: state is retained so the caller can retry or
            // report honestly rather than believe the backend is down.
            return false;
        }

        ClearState();
        return true;
    }

    private static void TryKill(int pid)
    {
        try
        {
            using var p = Process.GetProcessById(pid);
            p.Kill(entireProcessTree: true);
        }
        catch
        {
            // already exited, or not ours
        }
    }

    private void ClearState()
    {
        lock (_owned) { _owned.Clear(); }
        Pid = null;
        StartedByUs = false;
    }

    public void Dispose() => Stop();
}
