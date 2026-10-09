using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

public sealed class LearnedRow
{
    public string Kind { get; init; } = "";
    public string Label { get; init; } = "";
    public string Support { get; init; } = "";
}

/// <summary>
/// Experience as something the owner can read and use.
///
/// A raw trajectory dump is a debug view. What matters is durable learning:
/// what worked, what failed, what the verifier taught us, what each agent
/// profile learned, which provider configuration is reusable, and how often we
/// actually succeed.
///
/// Everything shown is counted from recorded rows. When nothing is recorded the
/// surface says so plainly instead of inventing a lesson, because a fabricated
/// "learning" is worse than an honest empty state.
/// </summary>
public sealed class ExperienceViewModel : ObservableObject
{
    private readonly BackendClient _client;
    private string _summary = "";
    private string _status = "";
    private string _missionId = "", _capability = "", _agent = "", _provider = "";
    private bool _busy;

    public ExperienceViewModel(BackendClient client)
    {
        _client = client;
        _ = RefreshAsync();
    }

    public ObservableCollection<LearnedRow> Successful { get; } = new();
    public ObservableCollection<LearnedRow> Failed { get; } = new();
    public ObservableCollection<LearnedRow> Agents { get; } = new();
    public ObservableCollection<LearnedRow> Config { get; } = new();
    public ObservableCollection<LearnedRow> VerifierLessons { get; } = new();

    // A section heading with nothing under it reads as a broken page, so each
    // heading is shown only when its own list has content.
    public bool HasSuccessful => Successful.Count > 0;
    public bool HasFailed => Failed.Count > 0;
    public bool HasAgents => Agents.Count > 0;
    public bool HasConfig => Config.Count > 0;
    public bool HasVerifierLessons => VerifierLessons.Count > 0;
    public bool HasNothing => Successful.Count == 0 && Failed.Count == 0 &&
                              Agents.Count == 0 && Config.Count == 0 &&
                              VerifierLessons.Count == 0;

    private void NotifySectionVisibility()
    {
        OnPropertyChanged(nameof(HasSuccessful));
        OnPropertyChanged(nameof(HasFailed));
        OnPropertyChanged(nameof(HasAgents));
        OnPropertyChanged(nameof(HasConfig));
        OnPropertyChanged(nameof(HasVerifierLessons));
        OnPropertyChanged(nameof(HasNothing));
    }

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

    public string Capability
    {
        get => _capability;
        set => SetProperty(ref _capability, value);
    }

    public string Agent
    {
        get => _agent;
        set => SetProperty(ref _agent, value);
    }

    public string Provider
    {
        get => _provider;
        set => SetProperty(ref _provider, value);
    }

    public bool Busy
    {
        get => _busy;
        set => SetProperty(ref _busy, value);
    }

    public IAsyncRelayCommand RefreshCommand => new AsyncRelayCommand(RefreshAsync);
    public IRelayCommand ClearFiltersCommand => new RelayCommand(ClearFilters);

    private void ClearFilters()
    {
        MissionId = "";
        Capability = "";
        Agent = "";
        Provider = "";
        _ = RefreshAsync();
    }

    private async Task RefreshAsync()
    {
        Busy = true;
        try
        {
            using var doc = await _client.GetExperienceDigestAsync(
                MissionId.Trim(), Capability.Trim(), Agent.Trim(), Provider.Trim());
            if (doc is null)
            {
                Status = "Experience unavailable (backend unreachable).";
                ClearAll();
                return;
            }
            var root = doc.RootElement;

            if (root.Str("available") == "false")
            {
                Status = root.Str("reason") ?? "Experience is not available.";
                ClearAll();
                Summary = "";
                return;
            }

            var os = root.TryGetProperty("outcome_summary", out var s) ? s : default;
            var rate = s.ValueKind == JsonValueKind.Object ? s.Str("success_rate") : null;
            var total = int.TryParse(root.Str("count"), out var n) ? n : 0;
            var noun = total == 1 ? "recorded outcome" : "recorded outcomes";
            Summary = string.IsNullOrWhiteSpace(rate)
                ? $"{total} {noun}."
                : $"{total} {noun} · success rate {rate}";

            Fill(Successful, root, "successful_strategies", "worked");
            Fill(Failed, root, "failed_approaches", "failed");
            Fill(Agents, root, "agent_profile_learning", "agent");
            Fill(Config, root, "reusable_configuration", "provider");

            VerifierLessons.Clear();
            if (root.TryGetProperty("verifier_lessons", out var lessons))
            {
                foreach (var l in lessons.EnumerateArray())
                {
                    var what = l.Str("strategy") ?? "";
                    var reasons = new List<string>();
                    if (l.TryGetProperty("failures", out var f))
                    {
                        foreach (var r in f.EnumerateArray())
                        {
                            var text = r.ValueKind == JsonValueKind.String
                                ? r.GetString()
                                : r.ToString();
                            if (!string.IsNullOrWhiteSpace(text)) reasons.Add(text!);
                        }
                    }
                    VerifierLessons.Add(new LearnedRow
                    {
                        Kind = "verifier",
                        Label = string.IsNullOrWhiteSpace(what) ? "(unnamed)" : what,
                        Support = string.Join("; ", reasons),
                    });
                }
            }

            Status = HasNothing
                ? "Nothing learned yet — GENIE has no recorded outcomes for these filters."
                : "";
            NotifySectionVisibility();
        }
        catch (Exception ex)
        {
            Status = $"Experience unavailable: {ex.Message}";
            ClearAll();
            NotifySectionVisibility();
        }
        finally
        {
            Busy = false;
        }
    }

    private static void Fill(ObservableCollection<LearnedRow> target,
                             JsonElement root, string property, string kind)
    {
        target.Clear();
        if (!root.TryGetProperty(property, out var arr)) return;
        foreach (var item in arr.EnumerateArray())
        {
            // Each entry is [name, count].
            if (item.ValueKind != JsonValueKind.Array) continue;
            var parts = item.EnumerateArray().ToList();
            var name = parts.Count > 0 ? parts[0].ToString() : "";
            var count = parts.Count > 1 ? parts[1].ToString() : "";
            if (string.IsNullOrWhiteSpace(name)) name = "(unnamed)";
            target.Add(new LearnedRow
            {
                Kind = kind,
                Label = name,
                Support = count,
            });
        }
    }

    private void ClearAll()
    {
        Successful.Clear();
        Failed.Clear();
        Agents.Clear();
        Config.Clear();
        VerifierLessons.Clear();
    }
}
