using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace Genie.Desktop.Services;

/// <summary>
/// Talks to the authoritative GENIE backend over the existing localhost HTTP +
/// SSE contracts. The client holds no GENIE state of its own.
/// </summary>
public sealed class BackendClient : IDisposable
{
    private readonly HttpClient _http;

    public BackendClient(string baseUrl = "http://127.0.0.1:8787")
    {
        _http = new HttpClient { BaseAddress = new Uri(baseUrl), Timeout = TimeSpan.FromSeconds(15) };
    }

    public async Task<bool> HealthAsync(CancellationToken ct = default)
    {
        try
        {
            using var resp = await _http.GetAsync("/health", ct);
            return resp.IsSuccessStatusCode;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public async Task<JsonDocument?> StatusAsync(CancellationToken ct = default)
        => await GetJsonAsync("/api/status", ct);

    public async Task<JsonDocument?> GetJsonAsync(string path, CancellationToken ct = default)
    {
        try
        {
            using var resp = await _http.GetAsync(path, ct);
            if (!resp.IsSuccessStatusCode) return null;
            var text = await resp.Content.ReadAsStringAsync(ct);
            return JsonDocument.Parse(text);
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>Send a chat message and return the full reply text.</summary>
    public async Task<string> ChatAsync(string text, CancellationToken ct = default)
    {
        var payload = JsonSerializer.Serialize(new { text, session_id = "owner" });
        using var content = new StringContent(payload, Encoding.UTF8, "application/json");
        using var resp = await _http.PostAsync("/api/chat", content, ct);
        resp.EnsureSuccessStatusCode();
        var body = await resp.Content.ReadAsStringAsync(ct);
        using var doc = JsonDocument.Parse(body);
        foreach (var prop in new[] { "reply", "text", "message", "content" })
        {
            if (doc.RootElement.TryGetProperty(prop, out var v) && v.ValueKind == JsonValueKind.String)
                return v.GetString() ?? string.Empty;
        }
        return body;
    }

    /// <summary>
    /// Stream a chat reply over SSE. The backend emits start / delta / done /
    /// error; deltas carry kind so the UI never presents progress as tokens.
    /// </summary>
    /// <summary>No SSE frame for this long means the turn is dead. Bounds the
    /// wait so an unreachable or stalled provider can never leave the owner
    /// waiting forever.</summary>
    private static readonly TimeSpan StreamIdleTimeout = TimeSpan.FromSeconds(120);

    public async Task StreamChatAsync(string text,
                                      Action<string> onDelta,
                                      CancellationToken ct = default,
        Action<string>? onProgress = null, string? requestId = null)
    {
        var payload = JsonSerializer.Serialize(new { text, session_id = "owner", request_id = requestId });
        using var content = new StringContent(payload, Encoding.UTF8, "application/json");
        using var req = new HttpRequestMessage(HttpMethod.Post, "/api/chat/stream") { Content = content };

        // Captured on the caller (UI) thread before any await, so streamed
        // deltas can be marshalled back to it without ever running the read
        // loop there.
        var ui = SynchronizationContext.Current;

        using var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct)
                                    .ConfigureAwait(false);
        resp.EnsureSuccessStatusCode();

        // Stop must be immediate. ReadLineAsync cannot be cancelled, so closing
        // the response is what actually breaks a read that is already parked on
        // the socket. Without this, "Stop" would only take effect once the next
        // frame happened to arrive.
        using var cancelHook = ct.Register(() =>
        {
            try { resp.Dispose(); } catch { /* already closing */ }
        });

        await using var stream = await resp.Content.ReadAsStreamAsync(ct)
                                           .ConfigureAwait(false);

        try
        {
            // Run the SSE pump on a POOL THREAD.
            //
            // It used to run on the WPF dispatcher: `while (!reader.EndOfStream)`
            // looks harmless but StreamReader.EndOfStream performs a SYNCHRONOUS
            // read on the network stream. While the provider was slow the UI
            // thread sat inside Winsock.recv, the message pump stopped, and
            // Windows marked the window "(Not Responding)". Confirmed by dump:
            //   Dispatcher.ProcessQueue -> DispatcherOperation.Invoke
            //     -> BackendClient.StreamChatAsync (line 85)
            //       -> StreamReader.get_EndOfStream -> StreamReader.ReadBuffer
            //         -> NetworkStream.Read -> Socket.Receive -> Winsock.recv
            await Task.Run(async () =>
            {
                using var reader = new StreamReader(stream, Encoding.UTF8);
                // The backend emits `event:` lines (delta / done / error) ahead of
                // each `data:` payload. The turn is finished at `done`/`error` -
                // waiting for the socket to close instead left the composer stuck
                // on "Thinking" even after the answer had arrived.
                string? lastEvent = null;
                while (!ct.IsCancellationRequested)
                {
                    // ReadLineAsync has no CancellationToken, so bound the wait
                    // explicitly. Never use EndOfStream here - it blocks.
                    var read = reader.ReadLineAsync();
                    var idle = Task.Delay(StreamIdleTimeout, ct);
                    var first = await Task.WhenAny(read, idle).ConfigureAwait(false);

                    if (first == idle)
                    {
                        // Distinguish "owner pressed Stop" from "provider went
                        // silent" so the UI can say the right thing. Stop must
                        // THROW, not break: breaking returns normally and the
                        // turn is rendered as an empty reply instead of
                        // "(stopped)".
                        if (ct.IsCancellationRequested)
                            throw new OperationCanceledException(ct);
                        // Unblock the pending read before leaving.
                        try { resp.Dispose(); } catch { /* already closing */ }
                        throw new TimeoutException(
                            $"No response from GENIE for {(int)StreamIdleTimeout.TotalSeconds}s.");
                    }

                    var line = await read.ConfigureAwait(false);
                    if (line is null) break;                    // stream ended
                    if (string.IsNullOrWhiteSpace(line)) continue;
                    if (line.StartsWith("event:", StringComparison.Ordinal))
                    {
                        lastEvent = line.Substring(6).Trim();
                        continue;
                    }
                    if (!line.StartsWith("data:", StringComparison.Ordinal)) continue;

                    var data = line.Substring(5).Trim();
                    if (data.Length == 0) continue;

                    string? delta = null;
                    string? progress = null;
                    try
                    {
                        using var doc = JsonDocument.Parse(data);
                        if (doc.RootElement.TryGetProperty("text", out var t) &&
                            t.ValueKind == JsonValueKind.String)
                        {
                            var kind = doc.RootElement.TryGetProperty("kind", out var k) ? k.GetString() : null;
                            if (lastEvent == "progress" || kind == "progress")
                                progress = t.GetString() ?? string.Empty;
                            else if (lastEvent == "delta" && kind is "token" or "final")
                                delta = t.GetString() ?? string.Empty;
                        }
                        else if (lastEvent == "error" && doc.RootElement.TryGetProperty("error", out var error))
                        {
                            delta = "GENIE could not complete this request: " + error.ToString();
                        }
                    }
                    catch (JsonException)
                    {
                        // ignore a partial frame
                    }

                    // Marshal each delta back to the UI thread. Post (not Send)
                    // keeps the dispatcher free.
                    if (delta is not null)
                    {
                        if (ui is null) onDelta(delta);
                        else ui.Post(_ => onDelta(delta), null);
                    }
                    if (progress is not null && onProgress is not null)
                    {
                        if (ui is null) onProgress(progress);
                        else ui.Post(_ => onProgress(progress), null);
                    }

                    // Terminal SSE event: the turn is over. Stop reading instead
                    // of waiting for the connection to close.
                    if (lastEvent is "done" or "error") break;
                }

                // Cancelled between frames: report it as a cancellation so the
                // UI closes the turn cleanly rather than showing a blank reply.
                if (ct.IsCancellationRequested) throw new OperationCanceledException(ct);
            }, ct).ConfigureAwait(false);
        }
        catch (Exception) when (ct.IsCancellationRequested)
        {
            // Stop was pressed: closing the response aborts the pending read,
            // which surfaces as IO/ObjectDisposed rather than a cancellation.
            // Report it as a cancellation so the UI shows "(stopped)".
            throw new OperationCanceledException(ct);
        }
    }

    public async Task<JsonDocument?> PostJsonAsync(string path, object payload,
                                                   CancellationToken ct = default)
    {
        try
        {
            var json = JsonSerializer.Serialize(payload);
            using var content = new StringContent(json, Encoding.UTF8, "application/json");
            using var resp = await _http.PostAsync(path, content, ct);
            var body = await resp.Content.ReadAsStringAsync(ct);
            return JsonDocument.Parse(body);
        }
        catch (Exception)
        {
            return null;
        }
    }

    // ---------------------------------------------------------------- missions
    public Task<JsonDocument?> GetMissionsAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/missions", ct);

    public Task<JsonDocument?> GetMissionAsync(string id, CancellationToken ct = default)
        => GetJsonAsync($"/api/missions/{Uri.EscapeDataString(id)}", ct);

    public Task<JsonDocument?> CreateMissionAsync(string goal, CancellationToken ct = default)
        => PostJsonAsync("/api/missions/create", new { goal }, ct);

    public Task<JsonDocument?> CancelMissionAsync(string id, CancellationToken ct = default)
        => PostJsonAsync($"/api/missions/{Uri.EscapeDataString(id)}/cancel", new { }, ct);

    /// <summary>Stop a running BOUNDED multi-step action between its steps.
    /// Closing the SSE stream alone does not stop server-side work, so the Stop
    /// button must tell the backend too.</summary>
    public Task<JsonDocument?> CancelActionAsync(CancellationToken ct = default, string? requestId = null)
        => PostJsonAsync("/api/action/cancel", new { request_id = requestId }, ct);

    // ------------------------------------------------------------------ agents
    public Task<JsonDocument?> GetAgentsAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/agents", ct);

    public Task<JsonDocument?> GetAgentTeamsAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/agents/teams", ct);

    /// <summary>Specialist agents spawned by live missions (Pass 3). Empty when
    /// no mission is running — the page then says so rather than inventing rows.</summary>
    public Task<JsonDocument?> GetMissionAgentsAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/agents/missions", ct);

    public Task<JsonDocument?> GetAgentTeamAsync(string missionId, CancellationToken ct = default)
        => GetJsonAsync($"/api/agents/teams/{Uri.EscapeDataString(missionId)}", ct);

    public Task<JsonDocument?> GetContinuationAsync(string missionId, CancellationToken ct = default)
        => GetJsonAsync($"/api/agents/continuation/{Uri.EscapeDataString(missionId)}", ct);

    // ---------------------------------------------------------------- computer
    /// <summary>Computer action metrics and recent actions. Honest when unavailable.</summary>
    public Task<JsonDocument?> GetActionMetricsAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/metrics/actions", ct);

    /// <summary>Truthful isolation availability - never claims more than exists.</summary>
    public Task<JsonDocument?> GetIsolationAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/isolation", ct);

    // --------------------------------------------------------- browser (Point 7)
    /// <summary>Honest dedicated-browser status: state, message, page title/url.</summary>
    public Task<JsonDocument?> GetBrowserStatusAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/browser/status", ct);

    /// <summary>Fetch ONE read-only preview frame as PNG bytes. Returns null when
    /// there is no active session (HTTP 204) or the backend is unreachable. This
    /// is observation only — the owner's input is never sent to the browser.</summary>
    public async Task<byte[]?> GetBrowserFrameAsync(CancellationToken ct = default)
        => await GetPreviewFrameAsync("/api/browser/frame", ct);

    public async Task<byte[]?> GetPreviewFrameAsync(string path, CancellationToken ct = default)
    {
        try
        {
            using var resp = await _http.GetAsync(path, ct);
            if (!resp.IsSuccessStatusCode) return null;
            var bytes = await resp.Content.ReadAsByteArrayAsync(ct);
            return bytes.Length > 0 ? bytes : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    // --------------------------------------------------- models / roles (W5)
    /// <summary>Backend-authoritative roles: everyday / fast / deep_work.</summary>
    public Task<JsonDocument?> GetModelRolesAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/models/roles", ct);

    public Task<JsonDocument?> SetModelRoleAsync(string role, string providerId, string modelId,
                                                 CancellationToken ct = default)
        => PostJsonAsync("/api/models/roles",
                         new { role, provider_id = providerId, model_id = modelId }, ct);

    // --------------------------------------------------------- providers (W5)
    /// <summary>Built-in provider catalog for the Add Provider picker. Single
    /// source of truth (backend config defaults), excludes test/stub providers
    /// so the owner is only offered real production providers.</summary>
    public Task<JsonDocument?> GetProviderCatalogAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/providers/catalog", ct);

    public Task<JsonDocument?> GetProvidersAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/providers", ct);

    public Task<JsonDocument?> TestProviderAsync(string id, CancellationToken ct = default)
        => PostJsonAsync("/api/providers/test", new { id }, ct);

    public Task<JsonDocument?> UpdateProviderAsync(string id, Dictionary<string, object> patch,
                                                   CancellationToken ct = default)
        => PostJsonAsync("/api/providers/update", new { id, patch }, ct);

    /// <summary>Create a provider. For built-in catalog providers the id and
    /// base_url come from the catalog so the owner never types a known URL.
    /// The key is NOT sent here — it goes to the Vault via SetProviderKeyAsync.
    /// </summary>
    public Task<JsonDocument?> AddProviderAsync(string id, string displayName,
                                                string baseUrl,
                                                string protocol = "openai_chat",
                                                Dictionary<string, string>? headers = null,
                                                string authScheme = "bearer",
                                                string discoveryUrl = "",
                                                double timeout = 20.0,
                                                bool custom = false,
                                                CancellationToken ct = default)
        => PostJsonAsync("/api/providers",
                         new { id, display_name = displayName, base_url = baseUrl,
                               protocol, enabled = true,
                               // Point 0 — persisted custom-provider Advanced settings.
                               // The API key is NOT here: it goes to the Vault separately.
                               headers, auth_scheme = authScheme,
                               discovery_url = discoveryUrl, timeout, custom }, ct);

    public Task<JsonDocument?> RemoveProviderAsync(string id, CancellationToken ct = default)
        => PostJsonAsync("/api/providers/remove", new { id }, ct);

    /// <summary>Store or replace a provider credential in the Vault.
    /// An empty key REMOVES the credential. The vault never returns the value,
    /// so a replaced key can never be read back by the client.</summary>
    public Task<JsonDocument?> SetProviderKeyAsync(string id, string key,
                                                   CancellationToken ct = default)
        => PostJsonAsync($"/api/providers/{Uri.EscapeDataString(id)}/key",
                         new { key }, ct);

    public Task<JsonDocument?> AddModelAsync(string providerId, string modelId,
                                             string displayName,
                                             IEnumerable<string>? capabilities = null,
                                             CancellationToken ct = default)
        => PostJsonAsync($"/api/providers/{Uri.EscapeDataString(providerId)}/models",
                         new { model_id = modelId, display_name = displayName,
                               capabilities = capabilities ?? Array.Empty<string>() }, ct);

    public Task<JsonDocument?> RemoveModelAsync(string providerId, string modelId,
                                                CancellationToken ct = default)
        => PostJsonAsync("/api/providers/models/remove",
                         new { provider_id = providerId, model_id = modelId }, ct);

    // ------------------------------------------------------------- voice (W5)
    public Task<JsonDocument?> GetVoiceAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/voice", ct);

    public Task<JsonDocument?> GetVoiceLevelsAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/voice/levels", ct);

    public Task<JsonDocument?> GetVoiceDevicesAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/voice/devices", ct);

    /// <summary>Enumerate the models a provider exposes (Add Provider flow).
    /// The key is used for the single discovery request and is never returned
    /// or stored by the client.</summary>
    public Task<JsonDocument?> DiscoverModelsAsync(string providerId = "",
                                                   string baseUrl = "",
                                                   string apiKey = "",
                                                   string discoveryUrl = "",
                                                   Dictionary<string, string>? headers = null,
                                                   string authScheme = "bearer",
                                                   double timeout = 20.0,
                                                   CancellationToken ct = default)
        => PostJsonAsync("/api/providers/discover",
                         new { provider_id = providerId, base_url = baseUrl,
                               api_key = apiKey, discovery_url = discoveryUrl,
                               headers = headers, auth_scheme = authScheme,
                               timeout = timeout }, ct);

    /// <summary>Begin capturing from the microphone. Home's core toggles this.
    /// The pipeline reports honest degradation, so a failure here is reported
    /// rather than simulated as "listening".</summary>
    public Task<JsonDocument?> StartListeningAsync(string? mode = null,
                                                   CancellationToken ct = default)
        => PostJsonAsync("/api/voice/start", new { mode }, ct);

    /// <summary>Stop capturing. Home's core toggles this off.</summary>
    public Task<JsonDocument?> StopListeningAsync(CancellationToken ct = default)
        => PostJsonAsync("/api/voice/stop", new { }, ct);

    // ------------------------------------------------------- competition (W6)
    /// <summary>Engine state plus outcomes recorded by the engine itself.</summary>
    public Task<JsonDocument?> GetCompetitionAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/competition", ct);

    /// <summary>Run candidates against one task. The backend commits the
    /// verified winner only, so a loser can never perform the side effect.</summary>
    public Task<JsonDocument?> RunCompetitionAsync(string missionId, string taskId,
                                                   int? requested = null,
                                                   CancellationToken ct = default)
        => PostJsonAsync("/api/competition/run",
                         new { mission_id = missionId, task_id = taskId, requested }, ct);

    public Task<JsonDocument?> CancelCompetitionAsync(string missionId,
                                                      CancellationToken ct = default)
        => PostJsonAsync("/api/competition/cancel", new { mission_id = missionId }, ct);

    // -------------------------------------------------------- experience (W6)
    /// <summary>Trajectory lessons. Answers "no authority" rather than an
    /// empty list that looks like "nothing learned".</summary>
    public Task<JsonDocument?> GetExperienceAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/experience", ct);

    /// <summary>Durable learning, filterable by mission / capability / agent /
    /// provider. Every number counts recorded rows; an empty store answers
    /// honestly rather than with plausible-looking advice.</summary>
    public Task<JsonDocument?> GetExperienceDigestAsync(string missionId = "",
                                                        string capability = "",
                                                        string agent = "",
                                                        string provider = "",
                                                        CancellationToken ct = default)
    {
        var parts = new List<string>();
        if (!string.IsNullOrWhiteSpace(missionId))
            parts.Add("mission=" + Uri.EscapeDataString(missionId));
        if (!string.IsNullOrWhiteSpace(capability))
            parts.Add("capability=" + Uri.EscapeDataString(capability));
        if (!string.IsNullOrWhiteSpace(agent))
            parts.Add("agent=" + Uri.EscapeDataString(agent));
        if (!string.IsNullOrWhiteSpace(provider))
            parts.Add("provider=" + Uri.EscapeDataString(provider));
        var q = parts.Count > 0 ? "?" + string.Join("&", parts) : "";
        return GetJsonAsync("/api/experience/digest" + q, ct);
    }

    public Task<JsonDocument?> GetKnowledgeAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/knowledge", ct);

    /// <summary>Import a folder/file as a knowledge source. Only the reference is
    /// stored - GENIE never copies the owner's documents into its own store.</summary>
    public Task<JsonDocument?> AddKnowledgeSourceAsync(string path,
                                                       CancellationToken ct = default)
        => PostJsonAsync("/api/knowledge/add", new { path }, ct);

    /// <summary>Forget a source. Removes index entries only; files are untouched.</summary>
    public Task<JsonDocument?> RemoveKnowledgeSourceAsync(string sourceId,
                                                          CancellationToken ct = default)
        => PostJsonAsync("/api/knowledge/remove", new { source_id = sourceId }, ct);

    public Task<JsonDocument?> SearchKnowledgeAsync(string query, int limit = 50,
                                                    CancellationToken ct = default)
        => GetJsonAsync($"/api/knowledge/search?q={Uri.EscapeDataString(query)}&limit={limit}", ct);

    /// <summary>Produced artifacts, newest first. References only - no file bytes.</summary>
    /// <summary>Daemon status: instance identity, director engine, runtime
    /// counters. Used by Settings > Advanced.</summary>
    public Task<JsonDocument?> GetStatusAsync(CancellationToken ct = default)
        => GetJsonAsync("/api/status", ct);

    /// <summary>The authoritative endpoint this client talks to. Advanced
    /// Settings shows it so "which backend" is a fact, not a guess.</summary>
    public string Endpoint => _http.BaseAddress?.ToString() ?? "";

    public Task<JsonDocument?> GetMediaAsync(string missionId = "",
                                             CancellationToken ct = default)
        => GetJsonAsync(string.IsNullOrEmpty(missionId)
            ? "/api/media"
            : $"/api/media?mission={Uri.EscapeDataString(missionId)}", ct);

    public void Dispose() => _http.Dispose();
}
