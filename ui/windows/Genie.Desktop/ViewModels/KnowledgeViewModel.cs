using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

public sealed class KnowledgeSourceRow
{
    public string SourceId { get; init; } = "";
    public string DisplayName { get; init; } = "";
    public string Path { get; init; } = "";
    public string Type { get; init; } = "";
    public string IndexedAt { get; init; } = "";
    public string ItemCount { get; init; } = "";
}

/// <summary>
/// Knowledge page. The backend is the authority for what's indexed, when and
/// where from. This view shows that authority to the owner in plain words:
/// import source, search, indexed items, and per-source detail. Raw JSON or
/// backend field names never appear in the normal owner view.
/// </summary>
public sealed class KnowledgeViewModel : ObservableObject
{
    private readonly BackendClient _client;
    private string _importPath = "";
    private string _searchQuery = "";
    private string _state = "Loading…";
    private string _indexedCount = "";
    private string _provenance = "";
    private string _message = "";
    private bool _busy;

    public KnowledgeViewModel(BackendClient client)
    {
        _client = client;
        _ = RefreshAsync();
    }

    public ObservableCollection<KnowledgeSourceRow> Sources { get; } = new();

    public bool HasSources => Sources.Count > 0;
    public bool HasNoSources => Sources.Count == 0;

    public string ImportPath
    {
        get => _importPath;
        set => SetProperty(ref _importPath, value);
    }

    public string SearchQuery
    {
        get => _searchQuery;
        set => SetProperty(ref _searchQuery, value);
    }

    public string State
    {
        get => _state;
        set => SetProperty(ref _state, value);
    }

    public string IndexedCount
    {
        get => _indexedCount;
        set => SetProperty(ref _indexedCount, value);
    }

    public string Provenance
    {
        get => _provenance;
        set => SetProperty(ref _provenance, value);
    }

    public string Message
    {
        get => _message;
        set => SetProperty(ref _message, value);
    }

    public IRelayCommand RefreshCommand => new AsyncRelayCommand(RefreshAsync);
    public IRelayCommand ImportCommand => new AsyncRelayCommand(ImportAsync);
    public IRelayCommand<string> RemoveCommand => new AsyncRelayCommand<string>(RemoveAsync);
    public IRelayCommand SearchCommand => new AsyncRelayCommand(SearchAsync);

    private async Task RefreshAsync()
    {
        if (_busy) return;
        _busy = true;
        State = "Loading…";
        Sources.Clear();
        try
        {
            using var doc = await _client.GetKnowledgeAsync();
            if (doc is null)
            {
                State = "No knowledge sources imported yet.";
                IndexedCount = "";
                Provenance = "";
                OnPropertyChanged(nameof(HasSources));
                OnPropertyChanged(nameof(HasNoSources));
                return;
            }
            var root = doc.RootElement;
            // Honest "no authority" handling, in product wording.
            if (root.TryGetProperty("available", out var av2) && av2.ValueKind == JsonValueKind.False)
            {
                var reason = root.Str("reason");
                State = string.IsNullOrWhiteSpace(reason)
                    ? "No knowledge sources imported yet."
                    : reason!;
                IndexedCount = "";
                Provenance = "";
                OnPropertyChanged(nameof(HasSources));
                OnPropertyChanged(nameof(HasNoSources));
                return;
            }
            IndexedCount = root.TryGetProperty("indexed", out var ix)
                ? ix.ValueKind == JsonValueKind.Number ? ix.ToString() + " items indexed"
                : (root.Str("indexed") ?? "")
                : "";
            Provenance = root.TryGetProperty("provenance", out var pv)
                ? pv.ToString()
                : (root.Str("provenance") ?? "");
            foreach (var s in root.Array("sources"))
            {
                Sources.Add(new KnowledgeSourceRow
                {
                    SourceId = s.First("id", "source_id"),
                    DisplayName = s.First("name", "display_name", "title"),
                    Path = s.First("path", "uri"),
                    Type = s.First("kind", "type"),
                    IndexedAt = s.First("indexed_at", "added_at"),
                    ItemCount = s.TryGetProperty("count", out var c) ? c.ToString() + " items" : "",
                });
            }
            OnPropertyChanged(nameof(HasSources));
            OnPropertyChanged(nameof(HasNoSources));
            State = Sources.Count == 0 ? "No knowledge sources imported yet." : "Ready";
        }
        catch (Exception ex)
        {
            State = $"Could not read knowledge — {ex.Message}";
        }
        finally
        {
            _busy = false;
        }
    }

    private async Task ImportAsync()
    {
        var path = ImportPath?.Trim();
        if (string.IsNullOrEmpty(path) || _busy) return;
        Message = "Importing…";
        using var doc = await _client.AddKnowledgeSourceAsync(path);
        Message = doc is null ? "Import failed." : "Imported. Refresh to see it indexed.";
        ImportPath = "";
        await RefreshAsync();
    }

    private async Task RemoveAsync(string? sourceId)
    {
        if (string.IsNullOrEmpty(sourceId) || _busy) return;
        Message = "Removing…";
        using var doc = await _client.RemoveKnowledgeSourceAsync(sourceId);
        Message = doc is null ? "Remove failed." : "Removed from index.";
        await RefreshAsync();
    }

    private async Task SearchAsync()
    {
        var q = SearchQuery?.Trim();
        if (string.IsNullOrEmpty(q) || _busy) return;
        Message = $"Searching for \"{q}\"…";
        using var doc = await _client.SearchKnowledgeAsync(q, 10);
        if (doc is null) { Message = "Search failed."; return; }
        var hits = doc.RootElement.Array("hits").ToList();
        var total = hits.Count;
        Message = total == 0 ? $"No matches for \"{q}\"." : $"{total} match{(total == 1 ? "" : "es")} for \"{q}\".";
    }
}