using System.Collections.ObjectModel;
using System.Text.Json;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

/// <summary>One recent action GENIE performed on this machine.</summary>
public sealed class ActionRow
{
    public string When { get; init; } = "";
    public string Kind { get; init; } = "";
    public string Outcome { get; init; } = "";
    public string OutcomeTone { get; init; } = "G.TextMid";
}

/// <summary>
/// Computer observability. This page observes; it does not drive Computer.
///
/// It previously printed the raw metrics envelope ("wrong: 0") and dumped every
/// isolation field as `name: value`. Both are backend vocabulary; the owner now
/// gets a sentence and a labelled list.
/// </summary>
public sealed class ComputerViewModel : ObservableObject
{
    private readonly BackendClient _client;

    private string _status = "Loading…";
    private string _statusTone = "G.TextMid";
    private string _emptyText = "";
    private string _isolationHeadline = "";
    private string _isolationCaveat = "";
    private bool _busy;

    // ---- dedicated browser (Point 7) ---------------------------------------
    private string _browserState = "inactive";
    private string _browserMessage = "No browser task is active.";
    private string _browserTitle = "";
    private string _browserUrl = "";
    private bool _hasBrowserSession;
    private bool _expanded;
    private readonly DispatcherTimer _previewTimer;
    private bool _desktopPreview, _polling;
    private int _displayIndex;
    public ObservableCollection<int> DisplayIndices { get; } = new();
    public int DisplayIndex
    {
        get => _displayIndex;
        set { if (SetProperty(ref _displayIndex, value)) FrameReady?.Invoke(null); }
    }
    public bool DesktopPreview
    {
        get => _desktopPreview;
        set
        {
            if (!SetProperty(ref _desktopPreview, value)) return;
            FrameReady?.Invoke(null);
            _previewTimer.Start();
            _ = LoadBrowserAsync();
        }
    }

    /// <summary>Raised with a new preview PNG (or null when there is no frame).
    /// The VIEW converts the bytes to a BitmapImage, keeping imaging out of the
    /// view model.</summary>
    public event Action<byte[]?>? FrameReady;

    public ComputerViewModel(BackendClient client)
    {
        _client = client;
        Recent.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(HasActions));
            OnPropertyChanged(nameof(IsEmpty));
        };
        // Low-frequency monitoring, not video. Paused when the page is hidden or
        // the window is minimised.
        _previewTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(1200) };
        _previewTimer.Tick += async (_, _) => await PollFrameAsync();
        _ = RefreshAsync();
    }

    public ObservableCollection<ActionRow> Recent { get; } = new();

    /// <summary>Where GENIE is actually running, one row per confinement level.</summary>
    public ObservableCollection<SurfaceItem> IsolationRows { get; } = new();

    public string Status
    {
        get => _status;
        set => SetProperty(ref _status, value);
    }

    public string StatusTone
    {
        get => _statusTone;
        set => SetProperty(ref _statusTone, value);
    }

    public string EmptyText
    {
        get => _emptyText;
        set
        {
            if (SetProperty(ref _emptyText, value))
                OnPropertyChanged(nameof(IsEmpty));
        }
    }

    public string IsolationHeadline
    {
        get => _isolationHeadline;
        set => SetProperty(ref _isolationHeadline, value);
    }

    public string IsolationCaveat
    {
        get => _isolationCaveat;
        set => SetProperty(ref _isolationCaveat, value);
    }

    public bool Busy
    {
        get => _busy;
        set
        {
            if (SetProperty(ref _busy, value))
                OnPropertyChanged(nameof(IsEmpty));
        }
    }

    public bool HasActions => Recent.Count > 0;
    public bool IsEmpty => Recent.Count == 0 && !Busy;

    // ---- dedicated browser (Point 7) ---------------------------------------
    public string BrowserState
    {
        get => _browserState;
        private set
        {
            if (SetProperty(ref _browserState, value))
                OnPropertyChanged(nameof(BrowserTone));
        }
    }

    public string BrowserMessage
    {
        get => _browserMessage;
        private set => SetProperty(ref _browserMessage, value);
    }

    public string BrowserTitle
    {
        get => _browserTitle;
        private set => SetProperty(ref _browserTitle, value);
    }

    public string BrowserUrl
    {
        get => _browserUrl;
        private set => SetProperty(ref _browserUrl, value);
    }

    public bool HasBrowserSession
    {
        get => _hasBrowserSession;
        private set => SetProperty(ref _hasBrowserSession, value);
    }

    /// <summary>True while the enlarged read-only viewer is open.</summary>
    public bool Expanded
    {
        get => _expanded;
        set => SetProperty(ref _expanded, value);
    }

    /// <summary>Badge tone for the browser state, in owner language.</summary>
    public string BrowserTone => _browserState switch
    {
        "working" => "G.OK",
        "starting" => "G.Cyan",
        "waiting" => "G.Cyan",
        "completed" => "G.OK",
        "failed" => "G.Error",
        "unavailable" => "G.Warn",
        _ => "G.TextDim",
    };

    public IRelayCommand ToggleExpandCommand => new RelayCommand(() => Expanded = !Expanded);
    public IRelayCommand CloseExpandCommand => new RelayCommand(() => Expanded = false);

    /// <summary>Stop preview polling (Computer hidden / window minimised).</summary>
    public void Pause() => _previewTimer.Stop();

    /// <summary>Resume preview polling when the page is visible again.</summary>
    public void Resume()
    {
        if (!_previewTimer.IsEnabled) _previewTimer.Start();
    }

    public IRelayCommand RefreshCommand => new AsyncRelayCommand(RefreshAsync);

    private async Task RefreshAsync()
    {
        if (Busy) return;
        Busy = true;
        try
        {
            await LoadActionsAsync();
            await LoadIsolationAsync();
            await LoadBrowserAsync();
        }
        finally
        {
            Busy = false;
        }
    }

    // ---- dedicated browser (Point 7) ---------------------------------------
    private async Task LoadBrowserAsync()
    {
        if (DesktopPreview) { await PollFrameAsync(); return; }
        using var doc = await _client.GetBrowserStatusAsync();
        if (doc is null)
        {
            BrowserState = "unavailable";
            BrowserMessage = "Browser status is unavailable (backend unreachable).";
            HasBrowserSession = false;
            _previewTimer.Stop();
            FrameReady?.Invoke(null);
            return;
        }
        var root = doc.RootElement;
        BrowserState = root.First("state") ?? "inactive";
        BrowserMessage = root.First("message") ?? "";
        BrowserTitle = root.First("title") ?? "";
        BrowserUrl = root.First("url") ?? "";
        HasBrowserSession = BrowserState == "working";
        if (HasBrowserSession)
        {
            if (!_previewTimer.IsEnabled) _previewTimer.Start();
            await PollFrameAsync();
        }
        else
        {
            _previewTimer.Stop();
            FrameReady?.Invoke(null);
        }
    }

    private async Task PollFrameAsync()
    {
        if (_polling) return;
        _polling = true;
        try
        {
        if (DesktopPreview)
        {
            using var doc = await _client.GetJsonAsync("/api/desktop");
            if (doc is null || !doc.RootElement.TryGetProperty("status", out var status))
            {
                HasBrowserSession = false;
                BrowserMessage = "Desktop status unavailable.";
                FrameReady?.Invoke(null);
                return;
            }
            var ids = status.Array("displays").Select(d => int.Parse(d.First("index") ?? "0")).ToList();
            if (!DisplayIndices.SequenceEqual(ids))
            {
                DisplayIndices.Clear();
                foreach (var id in ids) DisplayIndices.Add(id);
                if (!ids.Contains(DisplayIndex) && ids.Count > 0) DisplayIndex = ids[0];
            }
            bool Flag(string key) => status.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.True;
            HasBrowserSession = Flag("enabled") && Flag("running") && Flag("local_preview") && !Flag("paused") && !Flag("locked");
            BrowserState = HasBrowserSession ? "working" : "inactive";
            BrowserTitle = $"Display {DisplayIndex}";
            BrowserUrl = "Local screen preview";
            BrowserMessage = Flag("paused") ? "Desktop awareness paused." : Flag("locked") ? "Screen locked."
                : HasBrowserSession ? "Desktop awareness active locally." : "Desktop preview is off in Settings.";
            FrameReady?.Invoke(HasBrowserSession
                ? await _client.GetPreviewFrameAsync($"/api/desktop/frame?index={DisplayIndex}") : null);
            return;
        }
        if (!HasBrowserSession) { FrameReady?.Invoke(null); return; }
        var bytes = await _client.GetBrowserFrameAsync();
        FrameReady?.Invoke(bytes);
        }
        finally { _polling = false; }
    }

    private async Task LoadActionsAsync()
    {
        Recent.Clear();
        using var doc = await _client.GetActionMetricsAsync();
        if (doc is null)
        {
            Status = "Computer metrics are unavailable.";
            StatusTone = "G.Warn";
            EmptyText = "Computer metrics are unavailable because the backend did not answer.";
            return;
        }

        var root = doc.RootElement;
        if (root.Str("available") == "false")
        {
            var reason = root.First("reason");
            Status = string.IsNullOrWhiteSpace(reason)
                ? "Computer is not available."
                : "Computer is not available — " + reason;
            StatusTone = "G.Warn";
            EmptyText = "Computer is not available right now.";
            return;
        }

        var executed = ParseInt(root.Str("executed"));
        var wrong = ParseInt(root.Str("wrong"));
        var within = root.Str("within_target") == "true";

        if (executed == 0)
        {
            Status = "No actions have been run yet.";
            StatusTone = "G.TextMid";
        }
        else if (wrong == 0)
        {
            Status = $"{executed} action(s) run · every one verified as expected.";
            StatusTone = "G.OK";
        }
        else
        {
            Status = $"{executed} action(s) run · {wrong} with an unexpected result"
                     + (within ? " (still within target)." : ".");
            StatusTone = within ? "G.Warn" : "G.Error";
        }

        EmptyText = "GENIE has not performed any actions on this computer yet.";

        foreach (var a in root.Array("recent").Take(200))
        {
            var kind = a.First("kind");
            var ok = a.Str("ok") == "true";
            var verified = a.Str("verified") == "true";

            var (label, tone) = kind switch
            {
                "refused" => ("Refused", "G.TextMid"),
                "blocked" => ("Blocked", "G.Warn"),
                _ => verified ? ("Verified", "G.OK")
                              : ok ? ("Ran, not verified", "G.Warn")
                                   : ("Unexpected result", "G.Error"),
            };

            Recent.Add(new ActionRow
            {
                When = FormatTimestamp(a.Str("ts")),
                Kind = a.First("capability", "detail") ?? "(unnamed action)",
                Outcome = label,
                OutcomeTone = tone,
            });
        }
    }

    private async Task LoadIsolationAsync()
    {
        IsolationRows.Clear();
        using var doc = await _client.GetIsolationAsync();
        if (doc is null)
        {
            IsolationHeadline = "Where GENIE is running could not be determined.";
            IsolationCaveat = "";
            return;
        }

        var root = doc.RootElement;
        var effective = root.First("effective");

        IsolationHeadline = effective switch
        {
            "container" => "GENIE is running inside a container.",
            "vm" => "GENIE is running inside a virtual machine.",
            "isolated_desktop" => "GENIE has a dedicated isolated desktop.",
            "browser_profile" => "GENIE uses a dedicated browser profile.",
            "workspace_dir" => "GENIE works inside a dedicated workspace folder.",
            "host" => "GENIE is running directly on this PC.",
            _ => "Where GENIE is running is not reported.",
        };

        if (root.TryGetProperty("levels", out var levels) &&
            levels.ValueKind == JsonValueKind.Object)
        {
            foreach (var (name, label) in new[]
                     {
                         ("container", "Container"),
                         ("vm", "Virtual machine"),
                         ("isolated_desktop", "Isolated desktop"),
                         ("browser_profile", "Dedicated browser profile"),
                         ("workspace", "Workspace folder"),
                     })
            {
                if (!levels.TryGetProperty(name, out var node) ||
                    node.ValueKind != JsonValueKind.Object) continue;

                var active = node.Str("detected") == "true" ||
                             node.Str("state") == "active";
                var supported = node.Str("supported") != "false";

                IsolationRows.Add(new SurfaceItem
                {
                    Primary = label,
                    Secondary = active
                        ? "Active."
                        : supported ? "Not detected." : "Not supported on this machine.",
                    Badge = active ? "Active" : "Inactive",
                    BadgeTone = active ? "G.OK" : "G.TextDim",
                });
            }
        }

        // One caveat, not all four: the list above is already the detail.
        var caveats = root.Array("caveats").Select(c => c.ToString())
                          .Where(c => !string.IsNullOrWhiteSpace(c)).ToList();
        IsolationCaveat = caveats.Count > 0 ? caveats[0] : "";
    }

    private static int ParseInt(string? raw)
        => int.TryParse(raw, out var v) ? v : 0;

    /// <summary>Unix seconds or milliseconds, rendered as a local time.</summary>
    private static string FormatTimestamp(string? raw)
    {
        if (!long.TryParse(raw, out var ts) || ts <= 0) return "";
        try
        {
            var value = ts > 100_000_000_000
                ? DateTimeOffset.FromUnixTimeMilliseconds(ts)
                : DateTimeOffset.FromUnixTimeSeconds(ts);
            return value.ToLocalTime().ToString("HH:mm:ss");
        }
        catch
        {
            return "";
        }
    }
}
