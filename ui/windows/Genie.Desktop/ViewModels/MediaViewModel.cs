using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

public sealed class MediaArtifactRow
{
    public string Id { get; init; } = "";
    public string Title { get; init; } = "";
    public string Kind { get; init; } = "";
    public string Mission { get; init; } = "";
    public string Agent { get; init; } = "";
    public string Timestamp { get; init; } = "";
    public string Availability { get; init; } = "";
}

/// <summary>
/// Media page. Real artifact browser: each artifact shows type, mission,
/// agent, when it was produced and where it lives. The owner opens the
/// artifact from here. Raw backend envelopes stay out of the normal view.
/// </summary>
public sealed class MediaViewModel : ObservableObject
{
    private readonly BackendClient _client;
    private string _state = "Loading…";
    private string _filterMission = "";

    public MediaViewModel(BackendClient client)
    {
        _client = client;
        _ = RefreshAsync();
    }

    public ObservableCollection<MediaArtifactRow> Artifacts { get; } = new();

    public bool HasArtifacts => Artifacts.Count > 0;
    public bool HasNoArtifacts => Artifacts.Count == 0;

    public string State
    {
        get => _state;
        set => SetProperty(ref _state, value);
    }

    public string FilterMission
    {
        get => _filterMission;
        set => SetProperty(ref _filterMission, value);
    }

    public IRelayCommand RefreshCommand => new AsyncRelayCommand(RefreshAsync);

    private async Task RefreshAsync()
    {
        State = "Loading…";
        Artifacts.Clear();
        try
        {
            using var doc = await _client.GetMediaAsync(FilterMission ?? "");
            if (doc is null)
            {
                State = "No generated artifacts yet.";
                OnPropertyChanged(nameof(HasArtifacts));
                OnPropertyChanged(nameof(HasNoArtifacts));
                return;
            }
            var root = doc.RootElement;
            if (root.TryGetProperty("available", out var av) && av.ValueKind == JsonValueKind.False)
            {
                State = string.IsNullOrWhiteSpace(root.Str("reason"))
                    ? "No generated artifacts yet."
                    : root.Str("reason")!;
                OnPropertyChanged(nameof(HasArtifacts));
                OnPropertyChanged(nameof(HasNoArtifacts));
                return;
            }
            foreach (var a in root.Array("artifacts"))
            {
                Artifacts.Add(new MediaArtifactRow
                {
                    Id = a.First("id", "artifact_id"),
                    Title = a.First("title", "name"),
                    Kind = a.First("kind", "type", "format"),
                    Mission = a.First("mission", "mission_id"),
                    Agent = a.First("agent", "owner"),
                    Timestamp = a.First("timestamp", "created_at", "ts"),
                    Availability = a.First("availability", "location", "path"),
                });
            }
            OnPropertyChanged(nameof(HasArtifacts));
            OnPropertyChanged(nameof(HasNoArtifacts));
            State = Artifacts.Count == 0 ? "No generated artifacts yet." : "Ready";
        }
        catch (Exception ex)
        {
            State = $"Could not read media — {ex.Message}";
        }
    }
}