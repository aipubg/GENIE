using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

public sealed class CompetitionRow
{
    public string Outcome { get; init; } = "";
    public string Winner { get; init; } = "";
    public string Summary { get; init; } = "";
    public string When { get; init; } = "";
}

/// <summary>
/// Competition as a workflow the owner can actually drive, not an API buried
/// behind a debug surface.
///
/// The safety rule is shown, not just enforced server-side: only the verified
/// winner commits, candidates cannot select themselves, and anything that fails
/// verification becomes needs_revision rather than a quiet success. The Cancel
/// button is what makes a long run safe to start.
/// </summary>
public sealed class CompetitionViewModel : ObservableObject
{
    private readonly BackendClient _client;
    private string _summary = "";
    private string _status = "";
    private string _missionId = "";
    private string _taskId = "";
    private string _result = "";
    private bool _busy;
    private bool _running;

    public CompetitionViewModel(BackendClient client)
    {
        _client = client;
        _ = RefreshAsync();
    }

    public ObservableCollection<CompetitionRow> Recent { get; } = new();

    public string Summary
    {
        get => _summary;
        set => SetProperty(ref _summary, value);
    }

    public string Status
    {
        get => _status;
        set => SetProperty(ref _status, value);
    }

    public string MissionId
    {
        get => _missionId;
        set => SetProperty(ref _missionId, value);
    }

    public string TaskId
    {
        get => _taskId;
        set => SetProperty(ref _taskId, value);
    }

    public string Result
    {
        get => _result;
        set => SetProperty(ref _result, value);
    }

    public bool Busy
    {
        get => _busy;
        set
        {
            if (SetProperty(ref _busy, value)) RunCommand.NotifyCanExecuteChanged();
        }
    }

    public bool Running
    {
        get => _running;
        set
        {
            if (SetProperty(ref _running, value))
            {
                CancelCommand.NotifyCanExecuteChanged();
                RunCommand.NotifyCanExecuteChanged();
            }
        }
    }

    public IAsyncRelayCommand RefreshCommand => new AsyncRelayCommand(RefreshAsync);

    public IAsyncRelayCommand RunCommand =>
        new AsyncRelayCommand(RunAsync, () => !Busy && !string.IsNullOrWhiteSpace(MissionId));

    public IAsyncRelayCommand CancelCommand =>
        new AsyncRelayCommand(CancelAsync, () => Running);

    private async Task RefreshAsync()
    {
        try
        {
            using var doc = await _client.GetCompetitionAsync();
            if (doc is null) { Status = "Competition unavailable (backend unreachable)."; return; }
            var root = doc.RootElement;

            if (root.Str("available") == "false")
            {
                Status = root.Str("reason") ?? "Competition is not available.";
                Summary = "";
                return;
            }

            // "commit_policy: winner_only" is backend vocabulary. Only the
            // winner's side effect is ever applied, and that is what the owner
            // needs to know.
            var max = root.Str("max_candidates");
            var policy = root.Str("commit_policy");
            var outcome = string.IsNullOrWhiteSpace(policy) || policy == "winner_only"
                ? "only the verified winner takes effect"
                : policy.Replace('_', ' ');
            Summary = string.IsNullOrWhiteSpace(max)
                ? $"Several approaches run in parallel — {outcome}."
                : $"Up to {max} approaches run in parallel — {outcome}.";

            Recent.Clear();
            if (root.TryGetProperty("recent", out var recent))
            {
                foreach (var e in recent.EnumerateArray())
                {
                    Recent.Add(new CompetitionRow
                    {
                        Outcome = e.Str("result") ?? e.Str("outcome") ?? "",
                        Winner = e.Str("winner") ?? "",
                        Summary = e.Str("why") ?? e.Str("detail") ?? "",
                        When = e.Str("ts") ?? "",
                    });
                }
            }
            Status = Recent.Count == 0
                ? "No competition runs recorded yet."
                : $"{Recent.Count} recorded run(s).";
        }
        catch (Exception ex)
        {
            Status = $"Competition unavailable: {ex.Message}";
        }
    }

    private async Task RunAsync()
    {
        if (string.IsNullOrWhiteSpace(MissionId)) { Result = "A mission id is required."; return; }
        Busy = true;
        Running = true;
        Result = "Running competing approaches…";
        try
        {
            using var doc = await _client.RunCompetitionAsync(MissionId.Trim(), TaskId.Trim());
            Result = doc is null ? "No answer from the backend."
                                 : Describe(doc.RootElement);
        }
        catch (Exception ex)
        {
            Result = $"Run failed: {ex.Message}";
        }
        finally
        {
            Busy = false;
            Running = false;
            await RefreshAsync();
        }
    }

    /// <summary>Render the outcome honestly: a winner, a needs_revision, or a
    /// cancellation. Never present a failure as a success.</summary>
    private static string Describe(JsonElement root)
    {
        var outcome = root.Str("outcome") ?? root.Str("result") ?? "";
        var winner = root.Str("winner") ?? "";
        var reason = root.Str("reason") ?? root.Str("error") ?? "";
        var parts = new List<string>();
        if (!string.IsNullOrWhiteSpace(outcome)) parts.Add($"outcome: {outcome}");
        if (!string.IsNullOrWhiteSpace(winner)) parts.Add($"winner: {winner}");
        if (!string.IsNullOrWhiteSpace(reason)) parts.Add(reason);
        return parts.Count > 0 ? string.Join(" · ", parts) : "completed (no detail reported)";
    }

    private async Task CancelAsync()
    {
        try
        {
            using var doc = await _client.CancelCompetitionAsync(MissionId.Trim());
            Result = doc is null ? "Cancel did not reach the backend."
                                 : "Cancellation requested — the run will stop at the next checkpoint.";
        }
        catch (Exception ex)
        {
            Result = $"Cancel failed: {ex.Message}";
        }
        finally
        {
            Running = false;
        }
    }
}
