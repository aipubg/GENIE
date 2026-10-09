using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

/// <summary>One mission in the dashboard.</summary>
public sealed class MissionRow
{
    public string Id { get; init; } = "";
    public string Goal { get; init; } = "";
    public string State { get; init; } = "";
    public string Schedule { get; init; } = "";
    public int ProgressDone { get; init; }
    public int ProgressTotal { get; init; }
    public string CurrentStep { get; init; } = "";
    public long NextRunMs { get; init; }
    public bool Continuous { get; init; }

    private string _s => (State ?? "").Trim().ToUpperInvariant();

    public bool CanCancel => _s is not ("COMPLETED" or "FAILED" or "CANCELLED");
    public bool IsActive => CanCancel && _s is not ("WAITING" or "PAUSED");
    public bool IsScheduled => !string.IsNullOrWhiteSpace(Schedule) && CanCancel;
    public bool IsWaiting => _s is "WAITING" or "PAUSED";
    public bool NeedsAttention => _s is "FAILED" or "BLOCKED";

    public bool IsHistory => !CanCancel;

    public string StateLabel => _s switch
    {
        "CREATED" => "Created",
        "PLANNED" => "Planned",
        "RUNNING" => "Running",
        "WAITING" => "Waiting",
        "BLOCKED" => "Blocked",
        "PAUSED" => "Paused",
        "VERIFYING" => "Verifying",
        "COMPLETED" => "Completed",
        "FAILED" => "Needs attention",
        "CANCELLED" => "Cancelled",
        _ => State,
    };

    public string ProgressText => ProgressTotal > 0
        ? $"{ProgressDone}/{ProgressTotal}"
        : "—";

    public string CurrentText => string.IsNullOrWhiteSpace(CurrentStep)
        ? (CanCancel ? "Waiting" : "Finished")
        : CurrentStep;

    public string NextRunText
    {
        get
        {
            if (NextRunMs <= 0) return string.IsNullOrWhiteSpace(Schedule) ? "One-off" : Schedule;
            try
            {
                var when = DateTimeOffset.FromUnixTimeMilliseconds(NextRunMs).ToLocalTime();
                return "Next: " + when.ToString("dd MMM HH:mm");
            }
            catch { return Schedule; }
        }
    }
}

/// <summary>One step of the human-readable plan (never raw internal JSON).</summary>
public sealed class PlanRow
{
    public string Objective { get; init; } = "";
    public string Status { get; init; } = "";
    public string Role { get; init; } = "";
    public string Criteria { get; init; } = "";

    public string StatusLabel => (Status ?? "").ToLowerInvariant() switch
    {
        "completed" => "Done",
        "running" => "Running",
        "ready" => "Ready",
        "pending" => "Pending",
        "failed" => "Failed",
        "needs_revision" => "Needs revision",
        "blocked" => "Blocked",
        "waiting" => "Waiting",
        _ => Status ?? "",
    };
}

/// <summary>
/// Missions is a DASHBOARD. Missions are normally created by talking to GENIE;
/// this page shows status, progress, schedule, the plan, and a detail view, and
/// keeps a clearly-labelled manual fallback.
/// </summary>
public sealed class MissionsViewModel : ObservableObject
{
    private readonly BackendClient _client;
    private string _message = "";
    private bool _busy;

    private string _filter = "active";
    private bool _newMissionOpen;
    private string _newMissionGoal = "";

    private MissionRow? _selected;
    private string _detailTitle = "";
    private string _detailMeta = "";

    public MissionsViewModel(BackendClient client)
    {
        _client = client;
        _ = RefreshAsync();
    }

    public ObservableCollection<MissionRow> Missions { get; } = new();
    public ObservableCollection<PlanRow> PlanRows { get; } = new();

    public IEnumerable<MissionRow> FilteredMissions => _filter switch
    {
        "scheduled" => Missions.Where(m => m.IsScheduled),
        "waiting" => Missions.Where(m => m.IsWaiting),
        "attention" => Missions.Where(m => m.NeedsAttention),
        "history" => Missions.Where(m => m.IsHistory),
        _ => Missions.Where(m => m.IsActive),
    };

    public string Filter
    {
        get => _filter;
        private set
        {
            if (SetProperty(ref _filter, value))
            {
                OnPropertyChanged(nameof(FilteredMissions));
                OnPropertyChanged(nameof(IsActiveTab));
                OnPropertyChanged(nameof(IsScheduledTab));
                OnPropertyChanged(nameof(IsWaitingTab));
                OnPropertyChanged(nameof(IsAttentionTab));
                OnPropertyChanged(nameof(IsHistoryTab));
                OnPropertyChanged(nameof(EmptyText));
            }
        }
    }

    public bool IsActiveTab => _filter == "active";
    public bool IsScheduledTab => _filter == "scheduled";
    public bool IsWaitingTab => _filter == "waiting";
    public bool IsAttentionTab => _filter == "attention";
    public bool IsHistoryTab => _filter == "history";

    public string EmptyText => _filter switch
    {
        "scheduled" => "No scheduled missions yet. Ask GENIE for something recurring.",
        "waiting" => "Nothing is waiting right now.",
        "attention" => "Nothing needs your attention.",
        "history" => "No finished missions yet.",
        _ => "No active missions. Talk to GENIE to start one.",
    };

    public MissionRow? Selected
    {
        get => _selected;
        set
        {
            if (SetProperty(ref _selected, value))
            {
                OnPropertyChanged(nameof(HasSelection));
                if (value is not null) _ = LoadDetailAsync(value.Id);
            }
        }
    }

    /// <summary>Drives the detail panel's visibility without a converter.</summary>
    public bool HasSelection => _selected is not null;

    public string DetailTitle
    {
        get => _detailTitle;
        private set => SetProperty(ref _detailTitle, value);
    }

    public string DetailMeta
    {
        get => _detailMeta;
        private set => SetProperty(ref _detailMeta, value);
    }

    public bool NewMissionOpen
    {
        get => _newMissionOpen;
        set => SetProperty(ref _newMissionOpen, value);
    }

    public string NewMissionGoal
    {
        get => _newMissionGoal;
        set => SetProperty(ref _newMissionGoal, value);
    }

    public string Message
    {
        get => _message;
        set => SetProperty(ref _message, value);
    }

    public bool Busy
    {
        get => _busy;
        set => SetProperty(ref _busy, value);
    }

    public IRelayCommand RefreshCommand => new AsyncRelayCommand(RefreshAsync);
    public IRelayCommand<string> SetFilterCommand => new RelayCommand<string>(f => Filter = f ?? "active");
    public IRelayCommand OpenNewMissionCommand => new RelayCommand(() => NewMissionOpen = true);
    public IRelayCommand CancelNewMissionCommand => new RelayCommand(() =>
    {
        NewMissionOpen = false;
        NewMissionGoal = "";
    });
    public IRelayCommand CreateCommand => new AsyncRelayCommand(CreateAsync);
    public IRelayCommand<MissionRow> CancelCommand => new AsyncRelayCommand<MissionRow>(CancelAsync);
    public IRelayCommand<MissionRow> DeleteCommand => new AsyncRelayCommand<MissionRow>(DeleteAsync);

    private async Task DeleteAsync(MissionRow? row)
    {
        if (row is null || Busy) return;
        Busy = true;
        try
        {
            using var result = await _client.PostJsonAsync($"/api/missions/{row.Id}/delete", new { });
            if (result is null) { Message = "Backend unavailable."; return; }
            if (result.RootElement.TryGetProperty("error", out var error))
            { Message = error.GetString() ?? "Could not delete mission."; return; }
            if (Selected?.Id == row.Id) CloseDetailCommand.Execute(null);
            await RefreshAsync();
            Message = "Mission deleted.";
        }
        catch (Exception) { Message = "Could not delete mission. Please retry."; }
        finally { Busy = false; }
    }
    public IRelayCommand<MissionRow> PauseCommand => new AsyncRelayCommand<MissionRow>(PauseAsync);
    public IRelayCommand<MissionRow> ResumeCommand => new AsyncRelayCommand<MissionRow>(ResumeAsync);
    public IRelayCommand CloseDetailCommand => new RelayCommand(() =>
    {
        Selected = null;
        PlanRows.Clear();
        DetailTitle = "";
        DetailMeta = "";
    });

    private async Task RefreshAsync()
    {
        using var doc = await _client.GetMissionsAsync();
        Missions.Clear();
        if (doc is null)
        {
            Message = "Could not read missions (backend unavailable).";
            return;
        }
        foreach (var m in doc.RootElement.Array("missions"))
        {
            var prog = m.TryGetProperty("progress", out var p) ? p : default;
            Missions.Add(new MissionRow
            {
                Id = m.First("id", "mission_id"),
                Goal = m.First("goal", "objective", "title"),
                State = m.First("state", "status"),
                Schedule = m.First("schedule") ?? "",
                ProgressDone = IntOf(prog, "done"),
                ProgressTotal = IntOf(prog, "total"),
                CurrentStep = StrOf(prog, "current"),
                NextRunMs = LongOf(m, "next_run_ms"),
                Continuous = m.First("continuous") == "true",
            });
        }
        OnPropertyChanged(nameof(FilteredMissions));
        OnPropertyChanged(nameof(EmptyText));
        Message = $"{Missions.Count} mission(s)";
    }

    /// <summary>Human-readable plan for the selected mission.</summary>
    private async Task LoadDetailAsync(string id)
    {
        PlanRows.Clear();
        using var doc = await _client.GetMissionAsync(id);
        if (doc is null) { DetailTitle = "Could not load the mission."; DetailMeta = ""; return; }
        var root = doc.RootElement;
        DetailTitle = root.First("goal", "objective") ?? id;
        var prog = root.TryGetProperty("progress", out var p) ? p : default;
        DetailMeta = $"{root.First("state", "status")} · {IntOf(prog, "done")}/{IntOf(prog, "total")} steps";
        foreach (var s in root.Array("plan"))
        {
            PlanRows.Add(new PlanRow
            {
                Objective = s.First("objective") ?? "(step)",
                Status = s.First("status") ?? "pending",
                Role = s.First("role") ?? "worker",
                Criteria = s.First("criteria") ?? "",
            });
        }
    }

    private static int IntOf(JsonElement e, string name)
        => e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) &&
           v.ValueKind == JsonValueKind.Number ? v.GetInt32() : 0;

    private static string StrOf(JsonElement e, string name)
        => e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) &&
           v.ValueKind == JsonValueKind.String ? (v.GetString() ?? "") : "";

    private static long LongOf(JsonElement e, string name)
        => e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) &&
           v.ValueKind == JsonValueKind.Number ? v.GetInt64() : 0;

    private async Task CreateAsync()
    {
        var goal = NewMissionGoal?.Trim();
        if (string.IsNullOrEmpty(goal) || Busy) return;
        Busy = true;
        try
        {
            using var doc = await _client.CreateMissionAsync(goal);
            Message = doc is null ? "Create failed." : "Mission created.";
            NewMissionGoal = "";
            NewMissionOpen = false;
            Filter = "active";
            await RefreshAsync();
        }
        finally { Busy = false; }
    }

    private async Task CancelAsync(MissionRow? row)
    {
        if (row is null || string.IsNullOrEmpty(row.Id)) return;
        using var doc = await _client.CancelMissionAsync(row.Id);
        Message = doc is null ? "Cancel failed." : $"Cancelled {row.Id}";
        await RefreshAsync();
    }

    private async Task PauseAsync(MissionRow? row)
    {
        if (row is null) return;
        using var doc = await _client.PostJsonAsync(
            $"/api/missions/{Uri.EscapeDataString(row.Id)}/pause", new { });
        Message = doc is null ? "Pause failed." : $"Paused {row.Id}";
        await RefreshAsync();
    }

    private async Task ResumeAsync(MissionRow? row)
    {
        if (row is null) return;
        using var doc = await _client.PostJsonAsync(
            $"/api/missions/{Uri.EscapeDataString(row.Id)}/resume", new { });
        Message = doc is null ? "Resume failed." : $"Resumed {row.Id}";
        await RefreshAsync();
    }
}
