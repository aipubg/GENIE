using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

public sealed class TeamRow
{
    public string MissionId { get; init; } = "";
    public string Status { get; init; } = "";
    public string Workers { get; init; } = "";
}

/// <summary>A specialist agent spawned by a live mission (Pass 3).</summary>
public sealed class MissionAgentRow
{
    public string Agent { get; init; } = "";
    public string Role { get; init; } = "";
    public string Mission { get; init; } = "";
    public string Task { get; init; } = "";
    public string State { get; init; } = "";
    public string Provider { get; init; } = "";

    public string TaskText => string.IsNullOrWhiteSpace(Task) ? "between steps" : Task;
    public string ProviderText => string.IsNullOrWhiteSpace(Provider) ? "" : "via " + Provider;
}

/// <summary>
/// Real agent runtime state. Never shows "Ready" just because an adapter class
/// exists - only what the backend reports.
/// </summary>
public sealed class AgentsViewModel : ObservableObject
{
    private readonly BackendClient _client;
    private string _summary = "";
    private string _detail = "";
    private TeamRow? _selected;

    public AgentsViewModel(BackendClient client)
    {
        _client = client;
        Teams.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(HasTeams));
            OnPropertyChanged(nameof(HasNoTeams));
        };
        MissionAgents.CollectionChanged += (_, _) =>
        {
            OnPropertyChanged(nameof(HasMissionAgents));
            OnPropertyChanged(nameof(HasNoMissionAgents));
        };
        _ = RefreshAsync();
    }

    public ObservableCollection<TeamRow> Teams { get; } = new();

    /// <summary>Specialist agents the live missions spawned.</summary>
    public ObservableCollection<MissionAgentRow> MissionAgents { get; } = new();

    /// <summary>True while there are no teams running. Drives the empty card.</summary>
    public bool HasTeams => Teams.Count > 0;
    public bool HasNoTeams => Teams.Count == 0;

    public bool HasMissionAgents => MissionAgents.Count > 0;
    public bool HasNoMissionAgents => MissionAgents.Count == 0;

    public string Summary
    {
        get => _summary;
        set => SetProperty(ref _summary, value);
    }

    public string Detail
    {
        get => _detail;
        set => SetProperty(ref _detail, value);
    }

    public TeamRow? Selected
    {
        get => _selected;
        set
        {
            if (SetProperty(ref _selected, value)) _ = LoadDetailAsync();
        }
    }

    public IRelayCommand RefreshCommand => new AsyncRelayCommand(RefreshAsync);

    private async Task RefreshAsync()
    {
        using var status = await _client.GetAgentsAsync();
        Summary = status is null
            ? "Agents unavailable — backend did not respond."
            : DescribeAgents(status.RootElement);

        using var teams = await _client.GetAgentTeamsAsync();
        Teams.Clear();
        if (teams is not null)
        {
            foreach (var t in teams.RootElement.Array("teams"))
            {
                Teams.Add(new TeamRow
                {
                    MissionId = t.First("mission_id", "id"),
                    Status = t.First("state", "status"),
                    Workers = t.First("worker_count", "workers", "agents"),
                });
            }
        }

        // Pass 3 — specialists spawned by live missions.
        using var missionAgents = await _client.GetMissionAgentsAsync();
        MissionAgents.Clear();
        if (missionAgents is not null)
        {
            foreach (var a in missionAgents.RootElement.Array("agents"))
            {
                MissionAgents.Add(new MissionAgentRow
                {
                    Agent = a.First("agent", "name") ?? "agent",
                    Role = a.First("role") ?? "",
                    Mission = a.First("mission") ?? "",
                    Task = a.First("task") ?? "",
                    State = a.First("state", "status") ?? "",
                    Provider = a.First("provider", "model") ?? "",
                });
            }
        }
    }

    private static string DescribeAgents(System.Text.Json.JsonElement root)
    {
        // Owner-facing summary: only "available / specialists / running" if the
        // backend actually reports them, in plain product wording. Never raw
        // backend keys, never the endpoint name.
        var running = root.Str("running") ?? root.Str("active") ?? "";
        var specialists = root.Str("specialists") ?? "";
        var available = root.Str("available");
        if (available == "false")
            return "Agent runtime is currently offline.";
        if (!string.IsNullOrWhiteSpace(specialists))
            return $"Available specialists — {specialists}";
        if (!string.IsNullOrWhiteSpace(running))
            return $"Running agents — {running}";
        return "Agent runtime ready.";
    }

    private async Task LoadDetailAsync()
    {
        if (Selected is null || string.IsNullOrEmpty(Selected.MissionId))
        {
            Detail = "";
            return;
        }
        using var team = await _client.GetAgentTeamAsync(Selected.MissionId);
        if (team is null)
        {
            Detail = "No team detail available.";
            return;
        }
        var root = team.RootElement;
        if (root.TryGetProperty("error", out _))
        {
            Detail = "Team not found for this mission.";
            return;
        }
        // Owner-facing view, not a debug dump. Keys we would never show the
        // owner (e.g. internal protocol/state) are simply not mentioned.
        var state = root.First("state", "status");
        var lines = new List<string>();
        if (!string.IsNullOrWhiteSpace(state)) lines.Add($"State — {state}");
        var workers = root.Array("workers");
        foreach (var w in workers)
        {
            var role = w.First("name", "id");
            var wstate = w.First("state", "status");
            var task = w.Str("task");
            var model = w.Str("model") ?? w.Str("provider");
            var artifact = w.Str("artifact");
            var err = w.Str("error");
            var bits = new List<string>();
            if (!string.IsNullOrWhiteSpace(wstate)) bits.Add(wstate!);
            if (!string.IsNullOrWhiteSpace(task)) bits.Add($"on \"{task}\"");
            if (!string.IsNullOrWhiteSpace(model)) bits.Add($"via {model}");
            var head = string.IsNullOrWhiteSpace(role) ? "agent" : role;
            var tail = bits.Count > 0 ? " — " + string.Join(", ", bits) : "";
            lines.Add($"• {head}{tail}");
            if (!string.IsNullOrWhiteSpace(artifact)) lines.Add($"    artifact: {artifact}");
            if (!string.IsNullOrWhiteSpace(err)) lines.Add($"    error: {err}");
        }
        Detail = lines.Count == 0 ? "No agent activity yet." : string.Join("\n", lines);
    }
}
