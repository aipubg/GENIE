using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

public sealed class ChatMessage
{
    public string Role { get; init; } = "";      // "You" | "GENIE"
    public string Text { get; init; } = "";

    /// <summary>True for the owner's own turn, so the view can align and
    /// colour the two sides differently.</summary>
    public bool IsUser => string.Equals(Role, "You", StringComparison.Ordinal);

    /// <summary>Marks a turn that failed, so the view can offer Retry on that
    /// message rather than a dead end.</summary>
    public bool IsError { get; init; }

    /// <summary>True while this turn is still receiving deltas.</summary>
    public bool IsStreaming { get; init; }
    public string Progress { get; init; } = "Thinking";
}

/// <summary>
/// Chat over the real SSE contract. Streaming is genuine: deltas are appended as
/// they arrive, and Stop cancels the request.
/// </summary>
public sealed class ChatViewModel : ObservableObject
{
    private readonly BackendClient _client;
    private CancellationTokenSource? _cts;
    private string? _requestId;
    private string _input = "";
    private bool _streaming;
    private long _oldestHistoryId;
    public bool LoadingHistory { get; private set; }
    public bool CanLoadOlder => !Streaming && !LoadingHistory && _oldestHistoryId > 0;
    public IRelayCommand OlderHistoryCommand => new AsyncRelayCommand(() => LoadHistoryAsync(true));

    public async Task LoadHistoryAsync(bool older = false)
    {
        if (Streaming || LoadingHistory || (older && _oldestHistoryId == 0)) return;
        LoadingHistory = true;
        OnPropertyChanged(nameof(CanSend));
        try
        {
            using var doc = await _client.GetJsonAsync("/api/chat/history" +
                (older ? "?before=" + _oldestHistoryId : ""));
            if (Streaming || doc is null || !doc.RootElement.TryGetProperty("messages", out var rows)) return;
            var loaded = rows.EnumerateArray().ToArray();
            if (!older) Messages.Clear();
            for (var i = loaded.Length - 1; i >= 0; i--)
            {
                var row = loaded[i];
                if (string.IsNullOrWhiteSpace(row.GetProperty("text").GetString())) continue;
                Messages.Insert(0, new ChatMessage {
                    Role = row.GetProperty("role").GetString() == "user" ? "You" : "GENIE",
                    Text = row.GetProperty("text").GetString() ?? "" });
            }
            _oldestHistoryId = loaded.Length == 100 ? loaded[0].GetProperty("id").GetInt64() : 0;
            OnPropertyChanged(nameof(HasMessages));
        }
        catch (Exception) { /* Keep the current transcript if the backend is reconnecting. */ }
        finally { LoadingHistory = false; OnPropertyChanged(nameof(CanSend)); OnPropertyChanged(nameof(CanLoadOlder)); }
    }

    public ChatViewModel(BackendClient client) => _client = client;

    public ObservableCollection<ChatMessage> Messages { get; } = new();

    public string Input
    {
        get => _input;
        set
        {
            if (SetProperty(ref _input, value)) OnPropertyChanged(nameof(CanSend));
        }
    }

    public bool Streaming
    {
        get => _streaming;
        set { if (SetProperty(ref _streaming, value)) OnPropertyChanged(nameof(CanLoadOlder)); }
    }

    private void AddMessage(ChatMessage message)
    {
        Messages.Add(message);
        OnPropertyChanged(nameof(HasMessages));
    }

    /// <summary>Drives the empty-state hint. An empty conversation should say
    /// something useful rather than show a blank panel.</summary>
    public bool HasMessages => Messages.Count > 0;

    public IRelayCommand SendCommand => new AsyncRelayCommand(SendAsync);
    public IRelayCommand StopCommand => new RelayCommand(Stop);

    /// <summary>The prompt that produced the last GENIE turn. Retry re-sends it
    /// without making the owner retype.</summary>
    private string _lastPrompt = "";

    /// <summary>True when the last GENIE turn failed, so the view can show a
    /// retry affordance.</summary>
    public bool LastFailed
    {
        get => _lastFailed;
        set => SetProperty(ref _lastFailed, value);
    }
    private bool _lastFailed;

    public IRelayCommand RetryCommand => new AsyncRelayCommand(RetryAsync);

    private async Task RetryAsync()
    {
        if (string.IsNullOrWhiteSpace(_lastPrompt)) return;
        // Drop the failed GENIE turn so the retry does not stack errors.
        if (Messages.Count > 0 && Messages[^1] is { IsError: true })
            Messages.RemoveAt(Messages.Count - 1);
        Input = _lastPrompt;
        await SendAsync();
    }

    private async Task SendAsync()
    {
        var text = Input?.Trim();
        if (string.IsNullOrEmpty(text) || Streaming || LoadingHistory) return;
        await RunTurnAsync(text);
    }

    private async Task RunTurnAsync(string text)
    {
        AddMessage(new ChatMessage { Role = "You", Text = text });
        Input = "";
        _lastPrompt = text;
        LastFailed = false;
        Streaming = true;
        OnPropertyChanged(nameof(CanSend));
        _cts = new CancellationTokenSource();
        _requestId = Guid.NewGuid().ToString("N");

        var buffer = new System.Text.StringBuilder();
        var index = Messages.Count;
        AddMessage(new ChatMessage { Role = "GENIE", Text = "", IsStreaming = true });

        try
        {
            await _client.StreamChatAsync(text,
                delta =>
                {
                    buffer.Append(delta);
                    Messages[index] = new ChatMessage
                    {
                        Role = "GENIE",
                        Text = buffer.ToString(),
                        IsStreaming = true,
                    };
                },
                _cts.Token,
                progress => Messages[index] = new ChatMessage {
                    Role = "GENIE", Text = buffer.ToString(), IsStreaming = true,
                    Progress = progress.Trim() }, requestId: _requestId);

            // Settled: same text, no streaming marker.
            Messages[index] = new ChatMessage { Role = "GENIE", Text = buffer.ToString() };
        }
        catch (OperationCanceledException)
        {
            // Owner pressed Stop. Say so plainly; it is not an error.
            Messages[index] = new ChatMessage
            {
                Role = "GENIE",
                Text = buffer.Length > 0 ? buffer + "\n\n(stopped)" : "(stopped)",
            };
        }
        catch (TimeoutException)
        {
            // Bounded wait elapsed: say what happened instead of failing
            // silently or freezing.
            Messages[index] = new ChatMessage
            {
                Role = "GENIE",
                Text = "GENIE did not receive a response in time. "
                     + "The provider may be slow or unreachable.",
                IsError = true,
            };
            LastFailed = true;
            if (buffer.Length == 0 && string.IsNullOrWhiteSpace(Input))
                Input = text;
        }
        catch (Exception)
        {
            Messages[index] = new ChatMessage
            {
                Role = "GENIE",
                Text = "GENIE could not complete that request.",
                IsError = true,
            };
            LastFailed = true;
            // Nothing was accepted: never silently destroy the owner's text.
            // (Once any content has streamed in, the turn is real and the
            // prompt lives in the "You" row + Retry.)
            if (buffer.Length == 0 && string.IsNullOrWhiteSpace(Input))
                Input = text;
        }
        finally
        {
            Streaming = false;
            OnPropertyChanged(nameof(CanSend));
            _cts.Dispose();
            _cts = null;
        }
    }

    /// <summary>Send is available only when there is text and nothing is in
    /// flight. The composer reflects this so the owner is never clicking a
    /// dead button.</summary>
    public bool CanSend => !Streaming && !LoadingHistory && !string.IsNullOrWhiteSpace(Input);

    private void Stop()
    {
        _cts?.Cancel();
        // Also stop a running bounded multi-step action on the backend between its
        // steps: closing the stream does not stop server-side work.
        if (_requestId is not null)
            _ = _client.CancelActionAsync(requestId: _requestId);
    }
}
