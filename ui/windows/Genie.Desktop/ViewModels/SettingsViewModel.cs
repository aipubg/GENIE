using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

/// <summary>One saved model, for the saved-model list and discovery selection.
/// Only what the backend reports; capabilities are never inferred from the
/// model name.</summary>
public sealed class ModelOption
{
    public string ModelId { get; init; } = "";
    public string DisplayName { get; init; } = "";
    public string Capabilities { get; init; } = "";

    /// <summary>Owning provider, so the Remove button on a saved model can act
    /// without a second selection. Set when the model is loaded.</summary>
    public string ProviderId { get; init; } = "";

    public string Label => string.IsNullOrWhiteSpace(DisplayName)
        ? ModelId
        : $"{DisplayName} ({ModelId})";

    /// <summary>Capability text is only rendered when the backend actually
    /// reported capabilities — an empty string must not render a stray dash.</summary>
    public string CapabilityText => string.IsNullOrWhiteSpace(Capabilities)
        ? ""
        : " · " + Capabilities;
}

/// <summary>
/// A model returned by a discovery run, with its relationship to what is
/// already saved made explicit. Discovery never deletes: it reports.
/// </summary>
public sealed class DiscoveredModel : ObservableObject
{
    private bool _isSelected;
    public string ModelId { get; init; } = "";
    public string DisplayName { get; init; } = "";

    /// <summary>Already in this provider's saved list.</summary>
    public bool IsSaved { get; init; }

    /// <summary>True when the endpoint itself marks this model as default or
    /// preferred. Used only to seed the initial three-model selection.</summary>
    public bool Preferred { get; init; }

    /// <summary>Saved here, but NOT reported by this discovery run. Shown so
    /// the owner can decide — a discovery result that omits a model must never
    /// silently delete it.</summary>
    public bool MissingFromProvider { get; init; }

    public bool IsSelected
    {
        get => _isSelected;
        set { if (SetProperty(ref _isSelected, value)) SelectionChanged?.Invoke(); }
    }

    /// <summary>§8 — lets the owning ViewModel recompute "N selected" the
    /// instant a checkbox changes, without polling.</summary>
    public Action? SelectionChanged { get; set; }

    /// <summary>Only new models are selectable. An already-saved model cannot
    /// be added twice, and a missing one is removed from the saved list
    /// explicitly, never by implication.</summary>
    public bool CanSelect => !IsSaved && !MissingFromProvider;

    public string Label => IsSaved
        ? $"{DisplayName} ({ModelId}) — already saved"
        : MissingFromProvider
            ? $"{DisplayName} ({ModelId}) — saved, not reported now"
            : $"{DisplayName} ({ModelId})";
}

/// <summary>
/// One provider row in Settings. Every configurable provider supports the same
/// actions: test, edit, delete, replace the API key, rediscover models,
/// add/remove saved models. The routing layer decides which provider serves a
/// given request at runtime, so there is no owner Enable/Disable toggle — the
/// automatic eligibility state is shown as a status badge instead.
/// </summary>
public sealed class ProviderRow : ObservableObject
{
    // ---- identity reported by the backend -----------------------------------
    private string _id = "";
    private string _displayName = "";
    private string _baseUrl = "";
    private bool _hasCredential;

    public string Id
    {
        get => _id;
        set { if (SetProperty(ref _id, value)) OnPropertyChanged(nameof(IsBuiltIn)); }
    }

    public string DisplayName
    {
        get => _displayName;
        set => SetProperty(ref _displayName, value);
    }

    public string BaseUrl
    {
        get => _baseUrl;
        set => SetProperty(ref _baseUrl, value);
    }

    public string Protocol { get; set; } = "openai_chat";

    public bool HasCredential
    {
        get => _hasCredential;
        set
        {
            if (SetProperty(ref _hasCredential, value))
                OnPropertyChanged(nameof(CredentialText));
        }
    }

    /// <summary>Saved models for this provider, from the backend registry.</summary>
    public ObservableCollection<ModelOption> Models { get; } = new();

    public string CredentialText => HasCredential ? "Key stored" : "No key";

    public string ModelCountText => Models.Count == 1 ? "1 model" : $"{Models.Count} models";

    /// <summary>Built-in catalog providers keep their canonical URL; the owner
    /// edits the key, not the endpoint. Custom providers are fully editable.</summary>
    public bool IsBuiltIn { get; set; }

    /// <summary>True for an owner-added custom / OpenAI-compatible provider.
    /// Its Advanced settings are shown and persisted (Point 0).</summary>
    public bool Custom { get; set; }

    /// <summary>Mock/stub provider. Never mixed with real provider choices — it
    /// is shown only under a diagnostics section in Advanced mode.</summary>
    public bool IsTest { get; set; }

    // ---- persisted Advanced settings (Point 0) ------------------------------
    // Restored from the authoritative provider record on load and written back
    // on Save, so Add -> Save -> relaunch -> Edit shows the same values, and
    // Rediscover / Test reuse them. The API key is never among these — it lives
    // only in the Vault.
    private string _authScheme = "bearer";
    public string AuthScheme
    {
        get => _authScheme;
        set => SetProperty(ref _authScheme, value);
    }

    private string _discoveryUrl = "";
    public string DiscoveryUrl
    {
        get => _discoveryUrl;
        set => SetProperty(ref _discoveryUrl, value);
    }

    private string _timeoutText = "20";
    public string TimeoutText
    {
        get => _timeoutText;
        set => SetProperty(ref _timeoutText, value);
    }

    private string _headersText = "";
    public string HeadersText
    {
        get => _headersText;
        set => SetProperty(ref _headersText, value);
    }

    // ---- runtime eligibility (Point 1): GENIE decides, no owner toggle ------
    private string _runtimeStatus = "ready";
    private string _runtimeDetail = "";
    private bool _runtimeEligible = true;

    /// <summary>Authoritative state from the backend eligibility tracker:
    /// ready / in_use / rate_limited / quota_exhausted / auth_required /
    /// temporarily_unavailable / connection_failed.</summary>
    public string RuntimeStatus
    {
        get => _runtimeStatus;
        set
        {
            if (SetProperty(ref _runtimeStatus, value))
                OnPropertyChanged(nameof(StatusLabel));
        }
    }

    public string RuntimeDetail
    {
        get => _runtimeDetail;
        set => SetProperty(ref _runtimeDetail, value);
    }

    public bool RuntimeEligible
    {
        get => _runtimeEligible;
        set => SetProperty(ref _runtimeEligible, value);
    }

    /// <summary>Owner-facing wording for the runtime status badge.</summary>
    public string StatusLabel => _runtimeStatus switch
    {
        "ready" => "Ready",
        "in_use" => "In use",
        "rate_limited" => "Rate limited",
        "quota_exhausted" => "Quota exhausted",
        "auth_required" => "Key required",
        "temporarily_unavailable" => "Unavailable",
        "connection_failed" => "Connection failed",
        _ => _runtimeStatus
    };

    /// <summary>Extra detail (the reason), only shown when present.</summary>
    public string StatusDetail => _runtimeDetail;

    // ---- inline editor state ------------------------------------------------
    private bool _isExpanded;
    public bool IsExpanded
    {
        get => _isExpanded;
        set => SetProperty(ref _isExpanded, value);
    }

    private string _editDisplayName = "";
    public string EditDisplayName
    {
        get => _editDisplayName;
        set => SetProperty(ref _editDisplayName, value);
    }

    private string _editBaseUrl = "";
    public string EditBaseUrl
    {
        get => _editBaseUrl;
        set => SetProperty(ref _editBaseUrl, value);
    }

    /// <summary>Replacement credential. Lives only until it is pushed to the
    /// Vault, then is cleared. It is never read back and never displayed.</summary>
    public string NewKey { get; set; } = "";

    private bool _keyDirty;
    /// <summary>True once the owner typed a replacement key, so Save knows to
    /// write it. A blank box must never overwrite a stored key with nothing.</summary>
    public bool KeyDirty
    {
        get => _keyDirty;
        set
        {
            if (SetProperty(ref _keyDirty, value))
                OnPropertyChanged(nameof(KeyHint));
        }
    }

    public string KeyHint => KeyDirty ? "New key entered — Save writes it to the vault."
                                      : HasCredential ? "A key is stored. Leave blank to keep it."
                                                      : "No key stored yet.";

    private string _rowStatus = "";
    public string RowStatus
    {
        get => _rowStatus;
        set => SetProperty(ref _rowStatus, value);
    }

    private bool _confirmingDelete;
    /// <summary>Deletion is destructive, so it takes two clicks. The first arms
    /// the button and says exactly what will happen; only the second act
    /// actually removes. Never a silent one-click delete.</summary>
    public bool ConfirmingDelete
    {
        get => _confirmingDelete;
        set
        {
            if (SetProperty(ref _confirmingDelete, value))
                OnPropertyChanged(nameof(DeleteLabel));
        }
    }

    public string DeleteLabel => ConfirmingDelete ? "Confirm delete" : "Delete";

    /// <summary>What the owner is actually about to lose. Built-in providers
    /// lose their configuration only — the shipped catalog entry stays.</summary>
    public string DeleteWarning => IsBuiltIn
        ? $"Delete your configuration for {DisplayName}? The built-in provider stays in the catalog. Saved models and the stored key are removed."
        : $"Delete {DisplayName}? This deletes the provider, its saved models and its stored key.";

    // ---- add / rediscover models -------------------------------------------
    private string _manualModelId = "", _manualModelName = "";
    public string ManualModelId
    {
        get => _manualModelId;
        set => SetProperty(ref _manualModelId, value);
    }
    public string ManualModelName
    {
        get => _manualModelName;
        set => SetProperty(ref _manualModelName, value);
    }

    /// <summary>Models returned by a rediscovery run, with selection state.</summary>
    public ObservableCollection<DiscoveredModel> Rediscovered { get; } = new();

    private bool _rediscoverOpen;
    public bool RediscoverOpen
    {
        get => _rediscoverOpen;
        set => SetProperty(ref _rediscoverOpen, value);
    }

    private string _rediscoverSummary = "";
    public string RediscoverSummary
    {
        get => _rediscoverSummary;
        set => SetProperty(ref _rediscoverSummary, value);
    }

    /// <summary>A provider is always deletable now that roles are gone — nothing
    /// depends on it. The two-click confirm still applies.</summary>
    public bool CanRemove => true;

    /// <summary>Copies the committed values into the editor fields, so opening
    /// Edit always starts from what the backend actually holds.</summary>
    public void BeginEdit()
    {
        EditDisplayName = DisplayName;
        EditBaseUrl = BaseUrl;
        NewKey = "";
        KeyDirty = false;
        ManualModelId = "";
        ManualModelName = "";
        Rediscovered.Clear();
        RediscoverOpen = false;
        RediscoverSummary = "";
        RowStatus = "";
        ConfirmingDelete = false;
        IsExpanded = true;
    }

    public void EndEdit()
    {
        IsExpanded = false;
        NewKey = "";       // never retain the secret after the flow closes
        KeyDirty = false;
        Rediscovered.Clear();
        RediscoverOpen = false;
        RowStatus = "";
    }
}

/// <summary>
/// Settings. The backend is the single authority for providers, routing and
/// secrets - nothing is stored in the client. The API key is written through to
/// the backend and never retained here.
/// </summary>
public sealed record VoiceDeviceOption(int? Id, string Name);

public sealed class SettingsViewModel : ObservableObject
{
    private readonly BackendClient _client;
    private string _message = "";
    private string _voiceState = "";
    private bool _liveEnabled = true, _headsetMode, _testingLive;
    private string _liveModel = "gemini-3.8-live", _liveVoice = "Aoede", _liveTestStatus = "";
    private int? _liveInput, _liveOutput;
    public bool LiveEnabled { get => _liveEnabled; set => SetProperty(ref _liveEnabled, value); }
    public bool HeadsetMode { get => _headsetMode; set => SetProperty(ref _headsetMode, value); }
    public string LiveModel { get => _liveModel; set => SetProperty(ref _liveModel, value); }
    public string LiveVoice { get => _liveVoice; set => SetProperty(ref _liveVoice, value); }
    public string LiveTestStatus { get => _liveTestStatus; set => SetProperty(ref _liveTestStatus, value); }
    public int? LiveInput { get => _liveInput; set => SetProperty(ref _liveInput, value); }
    public int? LiveOutput { get => _liveOutput; set => SetProperty(ref _liveOutput, value); }
    public string[] LiveVoices { get; } = ["Aoede", "Kore", "Leda", "Zephyr", "Puck", "Charon", "Fenrir", "Orus"];
    public ObservableCollection<VoiceDeviceOption> LiveInputs { get; } = new();
    public ObservableCollection<VoiceDeviceOption> LiveOutputs { get; } = new();
    public IRelayCommand LiveApiSetupCommand => new RelayCommand(OpenGeminiSetup);
    public IRelayCommand SaveLiveCommand => new AsyncRelayCommand(async () => { await SaveLiveAsync(); });
    public IRelayCommand TestLiveCommand => new AsyncRelayCommand(async () =>
    {
        if (_testingLive) return;
        _testingLive = true;
        try
        {
            if (!await SaveLiveAsync()) return;
            LiveTestStatus = "Testing Live connection...";
            using var result = await _client.PostJsonAsync("/api/voice/test", new { });
            LiveTestStatus = result?.RootElement.First("detail", "error", "state") ?? "Live test unavailable.";
        }
        finally { _testingLive = false; }
    });
    private async Task<bool> SaveLiveAsync()
    {
        using var result = await _client.PostJsonAsync("/api/voice/settings", new
        {
            enabled = LiveEnabled, model = LiveModel.Trim(), voice = LiveVoice,
            input_device = LiveInput, output_device = LiveOutput, headset_mode = HeadsetMode
        });
        var ok = result is not null && result.RootElement.TryGetProperty("ok", out var node) && node.ValueKind == JsonValueKind.True;
        LiveTestStatus = ok ? "Voice settings saved." : result?.RootElement.Str("error") ?? "Could not save voice settings.";
        return ok;
    }

    private bool _localDesktopMemory, _localCamera, _localAutoStart, _localCameraCloud;
    private string _localCameraIndex = "0", _localZone = "office", _localStatus = "", _localEvents = "";
    private System.Windows.Media.Imaging.BitmapImage? _cameraPreview;
    public bool LocalDesktopMemory { get => _localDesktopMemory; set => SetProperty(ref _localDesktopMemory, value); }
    public bool LocalCamera { get => _localCamera; set => SetProperty(ref _localCamera, value); }
    public bool LocalAutoStart { get => _localAutoStart; set => SetProperty(ref _localAutoStart, value); }
    public bool LocalCameraCloud { get => _localCameraCloud; set => SetProperty(ref _localCameraCloud, value); }
    public string[] CameraIndices { get; } = Enumerable.Range(0, 16).Select(i => i.ToString()).ToArray();
    public string LocalCameraIndex { get => _localCameraIndex; set => SetProperty(ref _localCameraIndex, value); }
    public string LocalZone { get => _localZone; set => SetProperty(ref _localZone, value); }
    public string LocalStatus { get => _localStatus; set => SetProperty(ref _localStatus, value); }
    public string LocalEvents { get => _localEvents; set => SetProperty(ref _localEvents, value); }
    public System.Windows.Media.Imaging.BitmapImage? CameraPreview { get => _cameraPreview; set => SetProperty(ref _cameraPreview, value); }
    public string[] LocalZones { get; } = ["office", "workshop", "living", "kitchen", "hallway", "bedroom", "bathroom"];
    public IRelayCommand SaveLocalCommand => new AsyncRelayCommand(async () =>
    {
        if (!int.TryParse(LocalCameraIndex, out var index) || index < 0 || index > 15)
        { LocalStatus = "Camera index must be between 0 and 15."; return; }
        using var result = await _client.PostJsonAsync("/api/local-awareness/settings", new
        {
            desktop_memory = LocalDesktopMemory, camera_enabled = LocalCamera,
            auto_start = LocalAutoStart, camera_index = index, camera_zone = LocalZone,
            cloud_camera_consent = LocalCameraCloud
        });
        if (result is null) { LocalStatus = "Local awareness unavailable."; return; }
        if (result.RootElement.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.False)
        { LocalStatus = result.RootElement.Str("error") ?? "Settings rejected."; return; }
        await LoadLocalAsync();
    });
    public IRelayCommand PauseLocalCommand => new AsyncRelayCommand(async () =>
    {
        using var result = await _client.PostJsonAsync("/api/local-awareness/stop", new { });
        await LoadLocalAsync();
    });
    public IRelayCommand RefreshLocalCommand => new AsyncRelayCommand(() => LoadLocalAsync());
    public async Task LoadLocalAsync(bool reloadSettings = true)
    {
        using var doc = await _client.GetJsonAsync("/api/local-awareness");
        if (doc is null) { LocalStatus = "Local awareness unavailable."; return; }
        var root = doc.RootElement;
        if (reloadSettings && root.TryGetProperty("settings", out var config))
        {
            LocalDesktopMemory = config.TryGetProperty("desktop_memory", out var dm) && dm.ValueKind == JsonValueKind.True;
            LocalCamera = config.TryGetProperty("camera_enabled", out var cam) && cam.ValueKind == JsonValueKind.True;
            LocalAutoStart = config.TryGetProperty("auto_start", out var au) && au.ValueKind == JsonValueKind.True;
            LocalCameraCloud = config.TryGetProperty("cloud_camera_consent", out var net) && net.ValueKind == JsonValueKind.True;
            LocalZone = config.Str("camera_zone") ?? "office";
            LocalCameraIndex = config.First("camera_index") ?? "0";
        }
        var running = root.TryGetProperty("running", out var run) && run.ValueKind == JsonValueKind.True;
        LocalStatus = (running ? "Local monitoring active. " : "Local monitoring paused. ")
            + root.Str("camera_error") + " " + root.Str("memory_error") + " " + root.Str("last_event");
        using var events = await _client.GetJsonAsync("/api/local-awareness/events");
        LocalEvents = events is null ? "" : string.Join("\n", events.RootElement.Array("events").Take(12).Select(e =>
        {
            var evidence = e.GetProperty("evidence");
            var time = DateTimeOffset.FromUnixTimeSeconds(e.GetProperty("ts").GetInt64()).ToLocalTime().ToString("MM-dd HH:mm");
            return $"{time}  {e.Str("kind")}  {evidence.Str("process")} {evidence.Str("title")} {evidence.Str("message")}";
        }));
        var frame = running && LocalCamera ? await _client.GetPreviewFrameAsync("/api/local-awareness/frame") : null;
        if (frame is { Length: > 0 })
        {
            using var stream = new System.IO.MemoryStream(frame);
            var bitmap = new System.Windows.Media.Imaging.BitmapImage();
            bitmap.BeginInit(); bitmap.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
            bitmap.StreamSource = stream; bitmap.EndInit(); bitmap.Freeze(); CameraPreview = bitmap;
        }
        else CameraPreview = null;
    }
    private bool _advanced;
    private bool _busy;

    public SettingsViewModel(BackendClient client)
    {
        _client = client;
        _ = RefreshAsync();
    }

    public ObservableCollection<ProviderRow> Providers { get; } = new();

    /// <summary>Real, owner-facing providers — never mixed with test/stub ones.</summary>
    public IEnumerable<ProviderRow> ProductionProviders => Providers.Where(p => !p.IsTest);

    /// <summary>Mock/stub providers, shown only under a diagnostics heading in
    /// Advanced mode. They are never production routing choices.</summary>
    public IEnumerable<ProviderRow> TestProviders => Providers.Where(p => p.IsTest);
    public bool HasTestProviders => Providers.Any(p => p.IsTest);

    public string Message
    {
        get => _message;
        set => SetProperty(ref _message, value);
    }

    // ---------------------------------------------------------------- advanced
    // Diagnostics a normal owner never needs: instance identity, the NEDLE2
    // engine actually in use, the raw endpoint, the STT/TTS implementations and
    // runtime counters. Every value comes from the backend; nothing is guessed
    // and nothing is shown as a number it is not.
    private string _instanceId = "", _engineDetail = "", _runtimeSummary = "",
                   _sttImpl = "", _ttsImpl = "", _micInput = "",
                   _micRmsText = "", _ttsRmsText = "", _voiceStateText = "",
                   _executablePath = "";

    public string InstanceId
    {
        get => _instanceId;
        set => SetProperty(ref _instanceId, value);
    }

    public string EngineDetail
    {
        get => _engineDetail;
        set => SetProperty(ref _engineDetail, value);
    }

    public string RuntimeSummary
    {
        get => _runtimeSummary;
        set => SetProperty(ref _runtimeSummary, value);
    }

    public string SttImpl
    {
        get => _sttImpl;
        set => SetProperty(ref _sttImpl, value);
    }

    public string TtsImpl
    {
        get => _ttsImpl;
        set => SetProperty(ref _ttsImpl, value);
    }

    /// <summary>The microphone/input implementation actually in use. Normal and
    /// Advanced must never contradict each other, so both read the same
    /// authoritative backend value (<c>pipeline.input.name</c>).</summary>
    public string MicInput
    {
        get => _micInput;
        set => SetProperty(ref _micInput, value);
    }

    /// <summary>Live microphone RMS, straight from the backend meter. This is a
    /// real measurement — it is never synthesised.</summary>
    public string MicRmsText
    {
        get => _micRmsText;
        set => SetProperty(ref _micRmsText, value);
    }

    /// <summary>Live playback (TTS) RMS from the backend meter.</summary>
    public string TtsRmsText
    {
        get => _ttsRmsText;
        set => SetProperty(ref _ttsRmsText, value);
    }

    /// <summary>Authoritative voice pipeline state (IDLE / LISTENING / SPEAKING…)
    /// as reported by the backend turn controller.</summary>
    public string VoiceStateText
    {
        get => _voiceStateText;
        set => SetProperty(ref _voiceStateText, value);
    }

    /// <summary>Exact frontend executable path. This exists so "which build am I
    /// looking at?" is a fact rather than an argument.</summary>
    public string ExecutablePath
    {
        get => _executablePath;
        set => SetProperty(ref _executablePath, value);
    }

    // ---- build identity (Pass 3.5): "which build am I running?" -------------
    private string _uiBuildText = "", _backendBuildText = "", _versionWarning = "";

    public string UiBuildText
    {
        get => _uiBuildText;
        set => SetProperty(ref _uiBuildText, value);
    }

    public string BackendBuildText
    {
        get => _backendBuildText;
        set => SetProperty(ref _backendBuildText, value);
    }

    /// <summary>Non-empty when the UI build is newer than the synced backend
    /// runtime — i.e. the frontend and backend are not from the same source.</summary>
    public string VersionWarning
    {
        get => _versionWarning;
        set => SetProperty(ref _versionWarning, value);
    }

    /// <summary>The backend the client talks to. Shown so "raw endpoint" is a
    /// fact rather than a claim in a doc.</summary>
    public string Endpoint => _client.Endpoint;

    // ---- P1.1 source-of-truth reality screen --------------------------------
    // Answers, from the running backend itself: which data directory, which
    // session, which STT model, which model actually answered, why degraded.
    private string _sotBackendRoot = "", _sotDataDir = "", _sotSession = "",
                   _sotStt = "", _sotActive = "", _sotDegraded = "";

    public string SotBackendRoot
    {
        get => _sotBackendRoot;
        set => SetProperty(ref _sotBackendRoot, value);
    }

    public string SotDataDir
    {
        get => _sotDataDir;
        set => SetProperty(ref _sotDataDir, value);
    }

    public string SotSession
    {
        get => _sotSession;
        set => SetProperty(ref _sotSession, value);
    }

    public string SotStt
    {
        get => _sotStt;
        set => SetProperty(ref _sotStt, value);
    }

    /// <summary>Which provider/model actually served the last turn.</summary>
    public string SotActive
    {
        get => _sotActive;
        set => SetProperty(ref _sotActive, value);
    }

    public string SotDegraded
    {
        get => _sotDegraded;
        set => SetProperty(ref _sotDegraded, value);
    }

    private async Task LoadAdvancedAsync()
    {
        using var status = await _client.GetStatusAsync();
        if (status is not null)
        {
            var root = status.RootElement;
            InstanceId = root.Str("instance_id") ?? "(unreported)";
            if (root.TryGetProperty("director", out var dir))
            {
                var engine = dir.Str("engine") ?? "";
                var needle = dir.Str("needle") ?? "";
                EngineDetail = string.Join(" · ", new[] { engine, needle }
                    .Where(s => !string.IsNullOrWhiteSpace(s)));
            }
            var counts = new List<string>();
            foreach (var key in new[] { "missions", "memory", "agents", "devices",
                                        "plugins", "providers" })
            {
                if (!root.TryGetProperty(key, out var node)) continue;
                var n = node.ValueKind == JsonValueKind.Object && node.TryGetProperty("count", out var c)
                    ? c.ToString()
                    : node.ValueKind == JsonValueKind.Number ? node.ToString() : "";
                if (!string.IsNullOrEmpty(n)) counts.Add($"{key}: {n}");
            }
            RuntimeSummary = counts.Count > 0 ? string.Join(" · ", counts)
                                              : "no runtime counters reported";
        }

        // Voice diagnostics. The authoritative values live under `pipeline`
        // (pipeline.input.name / pipeline.stt.name / pipeline.tts.name) — the
        // older top-level `stt`/`tts` keys do not exist, which is why Advanced
        // used to print "not reported" while the normal Voice section correctly
        // showed the real implementations. Both surfaces now read the same
        // source, so they can no longer contradict each other.
        using var voice = await _client.GetVoiceAsync();
        if (voice is not null)
        {
            var v = voice.RootElement;
            var pipe = v.TryGetProperty("pipeline", out var pl) ? pl : default;

            MicInput = _impl(v, pipe, "input", "microphone");
            SttImpl = _impl(v, pipe, "stt", "stt");
            TtsImpl = _impl(v, pipe, "tts", "tts");
            VoiceStateText = _state(v, pipe);
            await RefreshAmplitudeAsync();
        }

        StartAmpPoll();

        // Build identity (Pass 3.5): prove which UI and which backend are running.
        string uiTimeText = "unknown";
        DateTime uiTime = DateTime.MinValue;
        try
        {
            var asmPath = System.Reflection.Assembly.GetExecutingAssembly().Location;
            if (!string.IsNullOrEmpty(asmPath) && System.IO.File.Exists(asmPath))
            {
                uiTime = System.IO.File.GetLastWriteTime(asmPath);
                uiTimeText = uiTime.ToString("yyyy-MM-dd HH:mm");
            }
        }
        catch { }
        UiBuildText = "UI built: " + uiTimeText;

        var commit = "";
        var syncedAt = "";
        if (status is not null &&
            status.RootElement.TryGetProperty("build", out var build) &&
            build.ValueKind == JsonValueKind.Object)
        {
            commit = build.First("source_commit") ?? "";
            syncedAt = build.First("runtime_synced_at") ?? "";
        }
        var shortCommit = string.IsNullOrWhiteSpace(commit)
            ? "unknown"
            : (commit.Length >= 8 ? commit[..8] : commit);
        BackendBuildText = $"Backend: {shortCommit} · runtime synced: "
                           + (string.IsNullOrWhiteSpace(syncedAt) ? "unknown" : syncedAt);

        // The UI was rebuilt after the backend runtime was last synced → the two
        // halves are not from the same source state.
        var beTime = DateTime.TryParse(syncedAt, out var t) ? t : DateTime.MinValue;
        VersionWarning = (uiTime != DateTime.MinValue && beTime != DateTime.MinValue &&
                          uiTime > beTime)
            ? "Backend/UI version mismatch — this UI build is newer than the synced "
              + "backend runtime. Run scripts/sync_backend_runtime.py."
            : "";

        // Exact running executable — the ultimate answer to "which build is this?"
        ExecutablePath = UiDiagnostics.ExePath;

        // ---- P1.1 source-of-truth reality screen ---------------------------
        // Everything below comes from the backend's own source_of_truth block,
        // so the owner can answer: which backend, which data dir, which session,
        // which STT model, which model answered, why is something degraded.
        if (status is not null &&
            status.RootElement.TryGetProperty("source_of_truth", out var sot) &&
            sot.ValueKind == JsonValueKind.Object)
        {
            SotBackendRoot = "Backend root: " + (sot.First("backend_root") ?? "unknown");

            var dir = sot.First("data_directory") ?? "unknown";
            var dirSrc = sot.First("data_directory_source") ?? "unknown";
            SotDataDir = $"Data directory: {dir}  (source: {dirSrc})";

            SotSession = "Owner session: " + (sot.First("session_id") ?? "unknown");

            var sttName = sot.First("stt_provider") ?? "";
            var sttModel = sot.First("stt_model_id") ?? "";
            var ready = sot.First("stt_ready") == "True" || sot.First("stt_ready") == "true";
            var loaded = sot.First("stt_loaded") == "True" || sot.First("stt_loaded") == "true";
            SotStt = string.IsNullOrWhiteSpace(sttName)
                ? "STT: not configured"
                : $"STT: {sttName} / {sttModel} — ready={(ready ? "yes" : "no")}, "
                  + $"loaded={(loaded ? "yes" : "no")}";

            var prov = sot.First("active_provider") ?? "";
            var model = sot.First("active_model") ?? "";
            SotActive = string.IsNullOrWhiteSpace(prov)
                ? "Last model: (none recorded yet)"
                : $"Active provider/model: {prov} / {model}";

            var degraded = new List<string>();
            if (sot.TryGetProperty("degraded", out var deg) &&
                deg.ValueKind == JsonValueKind.Array)
            {
                foreach (var d in deg.EnumerateArray())
                {
                    var s = d.ValueKind == JsonValueKind.String ? d.GetString() : d.ToString();
                    if (!string.IsNullOrWhiteSpace(s)) degraded.Add(s!);
                }
            }
            SotDegraded = degraded.Count == 0
                ? ""
                : "Degraded: " + string.Join("; ", degraded);
        }
        else
        {
            SotBackendRoot = "Backend root: unknown (backend predates source_of_truth)";
            SotDataDir = "";
            SotSession = "";
            SotStt = "";
            SotActive = "";
            SotDegraded = "";
        }
    }

    /// <summary>First string value for <paramref name="name"/> in a JSON object.
    /// Returns "" when absent — never a placeholder that looks like a fact.</summary>
    private static string _first(JsonElement obj, string name)
        => obj.ValueKind == JsonValueKind.Object && obj.TryGetProperty(name, out var v)
            ? (v.ValueKind == JsonValueKind.String ? v.GetString() ?? "" : v.ToString())
            : "";

    /// <summary>Reads one implementation name from the authoritative
    /// <c>pipeline.*</c> block, falling back to a top-level key and finally to a
    /// truthful "Not configured" — never a misleading placeholder.</summary>
    private static string _impl(JsonElement root, JsonElement pipe, string key,
                                string legacyKey)
    {
        string? name = null;
        if (pipe.ValueKind == JsonValueKind.Object &&
            pipe.TryGetProperty(key, out var node))
        {
            // Provider status shapes differ: sounddevice/SAPI report "name",
            // but the multilingual faster-whisper recognizer reports "provider".
            // Reading only "name" made a ready STT engine display "Not configured".
            if (node.ValueKind == JsonValueKind.Object) name = node.First("name", "provider");
            else if (node.ValueKind == JsonValueKind.String) name = node.GetString();
        }
        name ??= root.Str(legacyKey);
        if (!string.IsNullOrWhiteSpace(name)) return name!;
        // A section that exists but reports no implementation is "Not configured";
        // only a genuinely absent section is "Unavailable".
        var known = pipe.ValueKind == JsonValueKind.Object && pipe.TryGetProperty(key, out _);
        return known ? "Not configured" : "Unavailable";
    }

    /// <summary>Authoritative voice state from the turn controller.</summary>
    private static string _state(JsonElement root, JsonElement pipe)
    {
        if (pipe.ValueKind == JsonValueKind.Object &&
            pipe.TryGetProperty("turns", out var turns) &&
            turns.TryGetProperty("state", out var st))
        {
            var s = st.ValueKind == JsonValueKind.String ? st.GetString() : "";
            if (!string.IsNullOrWhiteSpace(s)) return s!;
        }
        return root.Str("state") ?? "Unavailable";
    }

    // ---- live amplitude meters (diagnostics only) ---------------------------
    private System.Windows.Threading.DispatcherTimer? _ampTimer;

    /// <summary>Starts a slow live poll of the real mic / playback meters while
    /// Advanced is open, so the owner can confirm the numbers actually move.
    /// Stopped again when Advanced is switched off — this is not a background
    /// service.</summary>
    private void StartAmpPoll()
    {
        if (_ampTimer is not null) return;
        _ampTimer = new System.Windows.Threading.DispatcherTimer
        {
            Interval = TimeSpan.FromMilliseconds(150),
        };
        _ampTimer.Tick += async (_, _) => await RefreshAmplitudeAsync();
        _ampTimer.Start();
    }

    private void StopAmpPoll()
    {
        if (_ampTimer is null) return;
        _ampTimer.Stop();
        _ampTimer = null;
    }

    private async Task RefreshAmplitudeAsync()
    {
        using var voice = await _client.GetVoiceAsync();
        if (voice is null) return;
        var v = voice.RootElement;

        var mic = v.TryGetProperty("amplitude", out var a) ? a : default;
        var micLevel = mic.ValueKind == JsonValueKind.Object &&
                       mic.TryGetProperty("level", out var ml) &&
                       ml.ValueKind == JsonValueKind.Number
            ? ml.GetDouble()
            : (mic.ValueKind == JsonValueKind.Number ? mic.GetDouble() : 0.0);
        MicRmsText = $"{micLevel:0.000}";

        var tts = v.TryGetProperty("tts_amplitude", out var ta) ? ta : default;
        if (tts.ValueKind == JsonValueKind.Object &&
            tts.TryGetProperty("level", out var tl) &&
            tl.ValueKind == JsonValueKind.Number)
        {
            TtsRmsText = tts.TryGetProperty("available", out var av) &&
                         av.ValueKind == JsonValueKind.False
                ? "unavailable"
                : $"{tl.GetDouble():0.000}";
        }
        else
        {
            TtsRmsText = "unavailable";
        }
    }

    public string VoiceState
    {
        get => _voiceState;
        set => SetProperty(ref _voiceState, value);
    }

    private bool _desktopEnabled, _desktopAutoStart, _desktopPreview, _desktopCloud;
    private bool _personalOwnerMode;
    private bool _geminiRedactedScreen;
    private string _screenApplication = "", _savedGeminiApplication = "";
    public bool GeminiRedactedScreen { get => _geminiRedactedScreen; set { SetProperty(ref _geminiRedactedScreen, value); if (value) PersonalOwnerMode = true; } }
    public string ScreenApplication { get => _screenApplication; set => SetProperty(ref _screenApplication, value); }
    private string _ownerGrants = "[]", _ownerPolicyStatus = "";
    public bool PersonalOwnerMode { get => _personalOwnerMode; set => SetProperty(ref _personalOwnerMode, value); }
    public string OwnerGrants { get => _ownerGrants; set => SetProperty(ref _ownerGrants, value); }
    public string OwnerPolicyStatus { get => _ownerPolicyStatus; set => SetProperty(ref _ownerPolicyStatus, value); }
    public IRelayCommand SaveOwnerPolicyCommand => new AsyncRelayCommand(async () =>
    {
        try
        {
            using var grants = JsonDocument.Parse(OwnerGrants);
            var items = grants.RootElement.EnumerateArray().Select(g => g.Clone()).ToList();
            if (GeminiRedactedScreen && string.IsNullOrWhiteSpace(ScreenApplication))
            { OwnerPolicyStatus = "Enter the exact observed application/window title for screen authorization."; return; }
            items.RemoveAll(g => g.ValueKind == JsonValueKind.Object && g.First("kind") == "screen_analysis"
                && g.First("provider") == "gemini" && g.First("destination") == "generativelanguage.googleapis.com"
                && (g.First("application") == _savedGeminiApplication || g.First("application") == ScreenApplication.Trim()));
            if (GeminiRedactedScreen)
                items.Add(JsonSerializer.SerializeToElement(new { kind = "screen_analysis", provider = "gemini",
                    destination = "generativelanguage.googleapis.com", scope = "cropped-redacted-window",
                    application = ScreenApplication.Trim(), expires_at = DateTimeOffset.UtcNow.AddDays(30).ToUnixTimeSeconds() }));
            using var result = await _client.PostJsonAsync("/api/owner-policy",
                new { enabled = PersonalOwnerMode, grants = items });
            if (result is not null && result.RootElement.TryGetProperty("ok", out var saved) && saved.ValueKind == JsonValueKind.True)
            { OwnerGrants = JsonSerializer.Serialize(items, new JsonSerializerOptions { WriteIndented = true }); _savedGeminiApplication = ScreenApplication.Trim(); }
            OwnerPolicyStatus = result is not null && result.RootElement.TryGetProperty("ok", out var ok)
                && ok.ValueKind == JsonValueKind.True ? "Owner policy saved. Grants can be revoked here."
                : result?.RootElement.First("error") ?? "Could not save owner policy.";
        }
        catch (JsonException) { OwnerPolicyStatus = "Grants must be a valid JSON array."; }
    });
    public bool DesktopCloud { get => _desktopCloud; set => SetProperty(ref _desktopCloud, value); }
    public bool DesktopPreview
    {
        get => _desktopPreview;
        set => SetProperty(ref _desktopPreview, value);
    }
    private string _desktopStatus = "Unavailable";
    public bool DesktopEnabled
    {
        get => _desktopEnabled;
        set => SetProperty(ref _desktopEnabled, value);
    }
    public bool DesktopAutoStart
    {
        get => _desktopAutoStart;
        set => SetProperty(ref _desktopAutoStart, value);
    }
    public string DesktopStatus
    {
        get => _desktopStatus;
        set => SetProperty(ref _desktopStatus, value);
    }
    public IRelayCommand SaveDesktopCommand => new AsyncRelayCommand(async () =>
    {
        using var result = await _client.PostJsonAsync("/api/desktop/settings",
            new { settings = new { enabled = DesktopEnabled, auto_start = DesktopAutoStart,
                                    local_preview = DesktopPreview, remote_visual_consent = DesktopCloud } });
        if (result is null) { DesktopStatus = "Could not save desktop settings."; return; }
        if (!result.RootElement.TryGetProperty("ok", out var ok) || ok.ValueKind != JsonValueKind.True)
        {
            DesktopStatus = result.RootElement.First("detail", "error") ?? "Could not save desktop settings.";
            return;
        }
        await LoadDesktopAsync();
    });
    public IRelayCommand PauseDesktopCommand => new AsyncRelayCommand(async () =>
    {
        using var result = await _client.PostJsonAsync("/api/desktop/pause", new { });
        await LoadDesktopAsync();
    });
    public IRelayCommand ResumeDesktopCommand => new AsyncRelayCommand(async () =>
    {
        using var result = await _client.PostJsonAsync("/api/desktop/resume", new { });
        await LoadDesktopAsync();
    });
    private async Task LoadDesktopAsync()
    {
        using var policy = await _client.GetJsonAsync("/api/owner-policy");
        if (policy is not null)
        {
            PersonalOwnerMode = policy.RootElement.TryGetProperty("enabled", out var enabled) && enabled.ValueKind == JsonValueKind.True;
            if (policy.RootElement.TryGetProperty("grants", out var grants))
            {
                OwnerGrants = JsonSerializer.Serialize(grants, new JsonSerializerOptions { WriteIndented = true });
                var screen = grants.EnumerateArray().FirstOrDefault(g => g.First("kind") == "screen_analysis"
                    && g.First("provider") == "gemini" && g.First("destination") == "generativelanguage.googleapis.com"
                    && g.First("application") != "legacy-unscoped-requires-reauthorization");
                if (screen.ValueKind == JsonValueKind.Object)
                {
                    ScreenApplication = _savedGeminiApplication = screen.First("application") ?? "";
                    _geminiRedactedScreen = PersonalOwnerMode && screen.TryGetProperty("expires_at", out var expiry)
                        && expiry.GetDouble() > DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                    OnPropertyChanged(nameof(GeminiRedactedScreen));
                }
            }
        }
        using var doc = await _client.GetJsonAsync("/api/desktop");
        if (doc is null) { DesktopStatus = "Desktop status unavailable."; return; }
        var root = doc.RootElement;
        if (!root.TryGetProperty("settings", out var settings) ||
            !root.TryGetProperty("status", out var status)) return;
        DesktopEnabled = settings.TryGetProperty("enabled", out var en) && en.ValueKind == JsonValueKind.True;
        DesktopAutoStart = settings.TryGetProperty("auto_start", out var au) && au.ValueKind == JsonValueKind.True;
        DesktopPreview = settings.TryGetProperty("local_preview", out var pv) && pv.ValueKind == JsonValueKind.True;
        DesktopCloud = settings.TryGetProperty("remote_visual_consent", out var cloud) && cloud.ValueKind == JsonValueKind.True;
        var running = status.TryGetProperty("running", out var run) && run.ValueKind == JsonValueKind.True;
        var paused = status.TryGetProperty("paused", out var pa) && pa.ValueKind == JsonValueKind.True;
        var locked = status.TryGetProperty("locked", out var lo) && lo.ValueKind == JsonValueKind.True;
        DesktopStatus = locked ? "Screen locked" : paused ? "Paused" : running
            ? $"Active locally | Displays: {status.First("display_count")} | Windows: {status.First("window_count")}"
            : "Stopped";
    }

    /// <summary>Advanced reveals developer/test providers and raw internals.</summary>
    public bool Advanced
    {
        get => _advanced;
        set
        {
            if (!SetProperty(ref _advanced, value)) return;
            // One switch, honoured by every diagnostics surface (including Home).
            UiDiagnostics.Advanced = value;
            if (value) _ = RefreshAsync(); else StopAmpPoll();
        }
    }

    public bool Busy
    {
        get => _busy;
        set => SetProperty(ref _busy, value);
    }

    public string Version => "0.1.0 (native WPF preview)";

    public IRelayCommand RefreshCommand => new AsyncRelayCommand(RefreshAsync);
    public IRelayCommand<ProviderRow> TestProviderCommand =>
        new AsyncRelayCommand<ProviderRow>(TestProviderAsync);

    // ------------------------------------------------ provider row actions
    public IRelayCommand<ProviderRow> EditProviderCommand =>
        new RelayCommand<ProviderRow>(EditProvider);
    public IRelayCommand<ProviderRow> CancelEditCommand =>
        new RelayCommand<ProviderRow>(CancelEdit);
    public IRelayCommand<ProviderRow> SaveProviderEditCommand =>
        new AsyncRelayCommand<ProviderRow>(SaveProviderEditAsync);
    public IRelayCommand<ProviderRow> DeleteProviderCommand =>
        new AsyncRelayCommand<ProviderRow>(DeleteProviderAsync);
    public IRelayCommand<ProviderRow> RediscoverCommand =>
        new AsyncRelayCommand<ProviderRow>(RediscoverAsync);
    public IRelayCommand<ProviderRow> AddManualModelToProviderCommand =>
        new AsyncRelayCommand<ProviderRow>(AddManualModelToProviderAsync);
    public IRelayCommand<ProviderRow> AddRediscoveredCommand =>
        new AsyncRelayCommand<ProviderRow>(AddRediscoveredAsync);
    public IRelayCommand<ProviderRow> SelectAllRediscoveredCommand =>
        new RelayCommand<ProviderRow>(r => SetRediscoverSelection(r, true));
    public IRelayCommand<ProviderRow> DeselectAllRediscoveredCommand =>
        new RelayCommand<ProviderRow>(r => SetRediscoverSelection(r, false));
    public IRelayCommand<ModelOption> RemoveModelCommand =>
        new AsyncRelayCommand<ModelOption>(RemoveModelAsync);

    public async Task RefreshAsync()
    {
        Busy = true;
        try
        {
            // The registry must load before providers so built-in detection works.
            await LoadCatalogAsync();
            await LoadProvidersAsync();
            await LoadVoiceAsync();
            await LoadLocalAsync();
            await LoadDesktopAsync();
            if (Advanced) await LoadAdvancedAsync();
        }
        finally
        {
            Busy = false;
        }
    }

    /// <summary>Built-in provider catalog from the backend. The client never
    /// duplicates provider URLs or definitions — it renders what the backend
    /// reports. "Custom / OpenAI-compatible" is appended by FilteredCatalog,
    /// not stored here.</summary>
    private async Task LoadCatalogAsync()
    {
        using var doc = await _client.GetProviderCatalogAsync();
        Catalog.Clear();
        if (doc is null) return;
        foreach (var c in doc.RootElement.Array("catalog"))
        {
            var id = c.First("id");
            if (string.IsNullOrWhiteSpace(id)) continue;
            Catalog.Add(new CatalogEntry
            {
                Id = id,
                DisplayName = c.First("display_name", "name") ?? id,
                BaseUrl = c.First("base_url") ?? "",
                Protocol = c.First("protocol") ?? "openai_chat",
                RequiresUserConfig = c.First("requires_user_config") == "true",
            });
        }
        OnPropertyChanged(nameof(FilteredCatalog));
    }

    private async Task LoadProvidersAsync()
    {
        using var doc = await _client.GetProvidersAsync();
        Providers.Clear();
        if (doc is null) return;
        var root = doc.RootElement;
        IEnumerable<JsonElement> items = root.ValueKind == JsonValueKind.Array
            ? root.EnumerateArray()
            : root.Array("providers");

        foreach (var p in items)
        {
            var kind = p.First("kind", "protocol");
            var isTest = kind.Contains("mock", StringComparison.OrdinalIgnoreCase)
                         || kind.Contains("stub", StringComparison.OrdinalIgnoreCase)
                         || p.First("id").Contains("stub", StringComparison.OrdinalIgnoreCase)
                         || p.First("id").Contains("mock", StringComparison.OrdinalIgnoreCase);
            // Test/stub providers stay out of normal Settings entirely; in
            // Advanced they are shown in a separate diagnostics section.
            if (isTest && !Advanced) continue;
            var pid = p.First("id");
            var builtIn = Catalog.Any(c => string.Equals(c.Id, pid, StringComparison.OrdinalIgnoreCase));
            var row = new ProviderRow
            {
                Id = pid,
                DisplayName = p.First("display_name", "name", "id"),
                BaseUrl = p.First("base_url", "baseUrl") ?? "",
                Protocol = p.First("protocol", "kind") ?? "openai_chat",
                HasCredential = p.First("has_secret_ref") == "true",
                // A catalog-backed provider keeps its shipped definition;
                // the owner edits their configuration, not the shipped catalog.
                IsBuiltIn = builtIn,
                Custom = p.First("custom") == "true" || !builtIn,
                IsTest = isTest,
                // Point 0 — persisted Advanced settings, restored from the record.
                AuthScheme = p.First("auth_scheme") ?? "bearer",
                DiscoveryUrl = p.First("discovery_url") ?? "",
                TimeoutText = p.TryGetProperty("timeout", out var tv) &&
                              tv.ValueKind != JsonValueKind.Null
                    ? tv.ToString() : "20",
                HeadersText = p.TryGetProperty("headers", out var hv) &&
                              hv.ValueKind == JsonValueKind.Object
                    ? FormatHeaders(hv) : "",
                // Point 1 — runtime eligibility from the backend eligibility tracker.
                RuntimeStatus = p.First("runtime_status") ?? "ready",
                RuntimeDetail = ProviderDetail(p.First("runtime_detail") ?? ""),
                RuntimeEligible = p.First("runtime_eligible") != "false",
            };
            // Saved models come from the registry summary.
            foreach (var m in p.Array("models"))
            {
                var mid = m.First("model_id", "id");
                if (string.IsNullOrWhiteSpace(mid)) continue;
                var caps = m.Array("capabilities")
                             .Select(c => c.ToString())
                             .Where(s => !string.IsNullOrWhiteSpace(s))
                             .ToList();
                row.Models.Add(new ModelOption
                {
                    ModelId = mid,
                    DisplayName = m.First("display_name", "name") ?? mid,
                    Capabilities = string.Join(", ", caps),
                    ProviderId = pid,
                });
            }
            Providers.Add(row);
        }
        OnPropertyChanged(nameof(ProductionProviders));
        OnPropertyChanged(nameof(TestProviders));
        OnPropertyChanged(nameof(HasTestProviders));
    }

    /// <summary>Reports what the voice pipeline actually says, in the owner's
    /// words. The backend nests this under "pipeline"; reading the top level
    /// produced an empty summary and a permanent (and false) "Voice ready".</summary>
    private static string ProviderDetail(string detail)
    {
        var value = detail.Trim();
        if (value.StartsWith("{") || value.StartsWith("["))
        {
            try
            {
                using var doc = JsonDocument.Parse(value);
                var root = doc.RootElement;
                if (root.ValueKind == JsonValueKind.Object && root.TryGetProperty("error", out var error)) root = error;
                value = root.ValueKind == JsonValueKind.String ? root.GetString() ?? "" :
                    root.ValueKind == JsonValueKind.Object && root.TryGetProperty("message", out var message)
                        ? message.GetString() ?? "" : "Provider returned an error. Check its connection and selected model.";
            }
            catch { value = "Provider returned an unreadable response. Check its connection and selected model."; }
        }
        if (value.Contains("<html", StringComparison.OrdinalIgnoreCase) || value.Contains("<!doctype", StringComparison.OrdinalIgnoreCase))
            value = "Provider returned a web page instead of an API response. Check the API base URL.";
        value = value.Replace('\r', ' ').Replace('\n', ' ');
        return value.Length > 240 ? value[..237] + "..." : value;
    }

    private async Task LoadVoiceAsync()
    {
        using var doc = await _client.GetVoiceAsync();
        if (doc is null) { VoiceState = "Voice unavailable (backend unreachable)."; return; }
        var root = doc.RootElement;
        if (root.TryGetProperty("live", out var live) && live.TryGetProperty("settings", out var liveSettings))
        {
            LiveEnabled = liveSettings.TryGetProperty("enabled", out var en) && en.ValueKind == JsonValueKind.True;
            HeadsetMode = liveSettings.TryGetProperty("headset_mode", out var hs) && hs.ValueKind == JsonValueKind.True;
            LiveModel = liveSettings.Str("model") ?? LiveModel;
            LiveVoice = liveSettings.Str("voice") ?? LiveVoice;
            using var devices = await _client.GetVoiceDevicesAsync();
            LiveInputs.Clear(); LiveOutputs.Clear();
            LiveInputs.Add(new VoiceDeviceOption(null, "System default"));
            LiveOutputs.Add(new VoiceDeviceOption(null, "System default"));
            if (devices is not null)
            {
                foreach (var item in devices.RootElement.Array("inputs"))
                    if (item.Str("source") == "sounddevice")
                        LiveInputs.Add(new VoiceDeviceOption(item.GetProperty("index").GetInt32(), item.Str("name") ?? "Microphone"));
                foreach (var item in devices.RootElement.Array("outputs"))
                    if (item.Str("source") == "sounddevice")
                        LiveOutputs.Add(new VoiceDeviceOption(item.GetProperty("index").GetInt32(), item.Str("name") ?? "Speaker"));
            }
            LiveInput = liveSettings.TryGetProperty("input_device", out var inp) && inp.ValueKind == JsonValueKind.Number ? inp.GetInt32() : null;
            LiveOutput = liveSettings.TryGetProperty("output_device", out var outp) && outp.ValueKind == JsonValueKind.Number ? outp.GetInt32() : null;
        }
        var pipeline = root.TryGetProperty("pipeline", out var p) &&
                       p.ValueKind == JsonValueKind.Object
            ? p
            : root;

        var parts = new List<string>();

        // Microphone
        if (pipeline.TryGetProperty("input", out var input) &&
            input.ValueKind == JsonValueKind.Object)
        {
            var name = input.Str("name") ?? "microphone";
            var avail = !(input.TryGetProperty("available", out var av) &&
                          av.ValueKind == JsonValueKind.False);
            parts.Add(avail ? $"Microphone: {name}" : $"Microphone unavailable ({name})");
        }

        // Engines actually in use, by name.
        foreach (var (key, label) in new[] { ("stt", "Speech to text"), ("tts", "Voice") })
        {
            if (!pipeline.TryGetProperty(key, out var node) ||
                node.ValueKind != JsonValueKind.Object) continue;
            var name = node.Str("name");
            if (string.IsNullOrWhiteSpace(name)) continue;
            var avail = !(node.TryGetProperty("available", out var av) &&
                          av.ValueKind == JsonValueKind.False);
            parts.Add(avail ? $"{label}: {name}" : $"{label} unavailable ({name})");
        }

        // Honest degradation reasons, when there are any.
        var degraded = pipeline.Array("degraded")
                               .Select(d => d.ToString())
                               .Where(s => !string.IsNullOrWhiteSpace(s))
                               .ToList();
        if (degraded.Count > 0) parts.Add(string.Join(" · ", degraded));

        VoiceState = parts.Count > 0
            ? string.Join(" · ", parts)
            : "Voice pipeline reported no detail.";
    }

    /// <summary>Backend-side connection test. No credential is read here.</summary>
    private async Task TestProviderAsync(ProviderRow? row)
    {
        if (row is null) return;
        using var doc = await _client.TestProviderAsync(row.Id);
        if (doc is null) { Message = $"Test failed for {row.Id}."; return; }
        var root = doc.RootElement;
        var ok = root.First("ok", "success");
        var detail = root.First("detail", "error", "message");
        Message = $"{row.Id}: {(ok == "true" ? "connected" : ok)} {detail}".Trim();
        // Re-read the runtime status so the badge reflects the test result.
        await LoadProvidersAsync();
    }

    // ===============================================================
    // Provider row lifecycle
    // Edit / Test / Replace key / Rediscover / Delete
    // and saved-model management. The backend owns all of it; the client
    // only renders state and forwards intent.
    // ===============================================================

    private void EditProvider(ProviderRow? row) => row?.BeginEdit();

    private void CancelEdit(ProviderRow? row) => row?.EndEdit();

    private async Task DeleteProviderAsync(ProviderRow? row)
    {
        if (row is null) return;
        if (!row.ConfirmingDelete)
        {
            row.ConfirmingDelete = true;
            row.RowStatus = row.DeleteWarning;
            return;
        }
        row.RowStatus = "Deleting…";
        using var doc = await _client.RemoveProviderAsync(row.Id);
        if (doc is null)
        {
            row.RowStatus = "Could not delete the provider.";
            return;
        }
        row.ConfirmingDelete = false;
        // A full reload keeps the runtime status badges honest.
        await RefreshAsync();
        Message = row.IsBuiltIn
            ? $"{row.DisplayName} configuration deleted. The built-in provider stays in the catalog."
            : $"{row.DisplayName} deleted.";
    }

    private async Task SaveProviderEditAsync(ProviderRow? row)
    {
        if (row is null) return;
        row.RowStatus = "Saving…";
        var patch = new Dictionary<string, object> { ["display_name"] = row.EditDisplayName };
        // A built-in provider's shipped base URL is not owner-editable.
        // Only custom endpoints are rewritten; built-ins keep the catalog URL.
        if (!row.IsBuiltIn && !string.IsNullOrWhiteSpace(row.EditBaseUrl))
            patch["base_url"] = row.EditBaseUrl;

        // Point 0 — persist the Advanced settings with the provider so they
        // survive a relaunch and are reused by Test / Rediscover. The API key is
        // never part of this patch; it goes to the Vault separately below.
        patch["auth_scheme"] = row.AuthScheme;
        patch["discovery_url"] = row.DiscoveryUrl;
        patch["headers"] = ParseHeaders(row.HeadersText) ?? new Dictionary<string, string>();
        if (double.TryParse((row.TimeoutText ?? "").Trim(), out var tmo) && tmo > 0)
            patch["timeout"] = tmo;

        using var doc = await _client.UpdateProviderAsync(row.Id, patch);
        if (doc is null)
        {
            row.RowStatus = "Could not save the provider.";
            return;
        }
        row.DisplayName = row.EditDisplayName;
        if (patch.TryGetValue("base_url", out var bu)) row.BaseUrl = bu.ToString() ?? row.BaseUrl;

        // The replacement key goes straight to the Vault. A blank box is
        // never written, so "leave blank to keep the current key" is literal.
        if (row.KeyDirty && !string.IsNullOrWhiteSpace(row.NewKey))
        {
            using var keyDoc = await _client.SetProviderKeyAsync(row.Id, row.NewKey);
            if (keyDoc is null)
            {
                row.RowStatus = "Provider saved, but the new key could not be stored.";
                return;
            }
            row.HasCredential = true;
        }
        row.NewKey = "";
        row.KeyDirty = false;
        row.RowStatus = "Saved.";
        row.IsExpanded = false;
        await RefreshAsync();
    }

    // ---- saved models -------------------------------------------------------

    private async Task RemoveModelAsync(ModelOption? m)
    {
        if (m is null || string.IsNullOrWhiteSpace(m.ProviderId)) return;
        using var doc = await _client.RemoveModelAsync(m.ProviderId, m.ModelId);
        Message = doc is null
            ? $"Could not remove {m.ModelId}."
            : $"{m.ModelId} removed.";
        await RefreshAsync();
    }

    private async Task AddManualModelToProviderAsync(ProviderRow? row)
    {
        if (row is null) return;
        var mid = (row.ManualModelId ?? "").Trim();
        if (string.IsNullOrWhiteSpace(mid))
        {
            row.RowStatus = "Enter a model ID.";
            return;
        }
        // Never duplicate an already-saved model.
        if (row.Models.Any(x => string.Equals(x.ModelId, mid, StringComparison.OrdinalIgnoreCase)))
        {
            row.RowStatus = "That model is already saved on this provider.";
            return;
        }
        var name = string.IsNullOrWhiteSpace(row.ManualModelName) ? mid : row.ManualModelName.Trim();
        using var doc = await _client.AddModelAsync(row.Id, mid, name);
        if (doc is null)
        {
            row.RowStatus = "Could not add the model.";
            return;
        }
        row.ManualModelId = "";
        row.ManualModelName = "";
        row.RowStatus = $"{mid} added.";
        await RefreshAsync();
    }

    // ---- rediscovery --------------------------------------------------------

    private void SetRediscoverSelection(ProviderRow? row, bool selected)
    {
        if (row is null) return;
        foreach (var m in row.Rediscovered) m.IsSelected = selected && m.CanSelect;
    }

    /// <summary>Rediscover using the key already in the Vault — the client
    /// never holds or resends the stored secret.</summary>
    private async Task RediscoverAsync(ProviderRow? row)
    {
        if (row is null) return;
        row.RowStatus = "Discovering models…";
        row.Rediscovered.Clear();
        using var doc = await _client.DiscoverModelsAsync(providerId: row.Id);
        if (doc is null)
        {
            row.RowStatus = "Discovery failed — backend unreachable.";
            return;
        }
        var root = doc.RootElement;
        var ok = root.TryGetProperty("ok", out var okNode) && okNode.ValueKind != JsonValueKind.False;
        if (!ok)
        {
            // The backend returns an owner-language reason; surface it and keep
            // the manual entry escape hatch open.
            row.RowStatus = (root.Str("error") ?? "Model discovery failed.")
                            + " You can still add a model manually.";
            row.RediscoverOpen = true;
            row.RediscoverSummary = "Discovery failed";
            return;
        }

        var savedIds = new HashSet<string>(row.Models.Select(m => m.ModelId),
                                          StringComparer.OrdinalIgnoreCase);
        var reported = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var found = 0;
        foreach (var m in root.Array("models"))
        {
            var mid = m.First("model_id", "id");
            if (string.IsNullOrWhiteSpace(mid)) continue;
            reported.Add(mid);
            found++;
            row.Rediscovered.Add(new DiscoveredModel
            {
                ModelId = mid,
                DisplayName = m.First("display_name", "name") ?? mid,
                IsSaved = savedIds.Contains(mid),
            });
        }
        // Saved but no longer reported: surfaced, never auto-deleted.
        foreach (var m in row.Models.Where(x => !reported.Contains(x.ModelId)))
        {
            row.Rediscovered.Add(new DiscoveredModel
            {
                ModelId = m.ModelId,
                DisplayName = m.DisplayName,
                IsSaved = true,
                MissingFromProvider = true,
            });
        }
        row.RediscoverSummary = found == 1 ? "1 model found" : $"{found} models found";
        row.RediscoverOpen = true;
        row.RowStatus = "";
    }

    private async Task AddRediscoveredAsync(ProviderRow? row)
    {
        if (row is null) return;
        var selected = row.Rediscovered.Where(m => m.IsSelected && m.CanSelect).ToList();
        if (selected.Count == 0)
        {
            row.RowStatus = "Select at least one new model.";
            return;
        }
        row.RowStatus = $"Adding {selected.Count} model(s)…";
        foreach (var m in selected)
        {
            using var _ = await _client.AddModelAsync(row.Id, m.ModelId, m.DisplayName);
        }
        row.RowStatus = $"{selected.Count} model(s) added.";
        row.RediscoverOpen = false;
        await RefreshAsync();
    }

    // ===============================================================
    // Add Provider flow (sections 3-8)
    // choose -> credentials (+ optional advanced) -> Test -> Discover -> select -> Save
    // ===============================================================

    public sealed class CatalogEntry
    {
        public string Id { get; init; } = "";
        public string DisplayName { get; init; } = "";
        public string BaseUrl { get; init; } = "";
        public string Protocol { get; init; } = "openai_chat";
        public bool RequiresUserConfig { get; init; }
        public bool IsCustom { get; init; }
        public string Hint => IsCustom
            ? "Bring your own OpenAI-compatible endpoint"
            : BaseUrl;
    }

    public ObservableCollection<CatalogEntry> Catalog { get; } = new();

    private string _catalogSearch = "";
    public string CatalogSearch
    {
        get => _catalogSearch;
        set
        {
            if (SetProperty(ref _catalogSearch, value))
                OnPropertyChanged(nameof(FilteredCatalog));
        }
    }

    /// <summary>The always-available Custom / OpenAI-compatible entry. It is
    /// pinned as its own visible option (not buried in the scrolling list) so the
    /// owner never has to discover it.</summary>
    public CatalogEntry CustomEntry { get; } =
        new CatalogEntry { Id = "custom", DisplayName = "Custom / OpenAI-compatible",
                           IsCustom = true };

    /// <summary>Searchable catalog. The Custom entry is pinned separately AND is
    /// matched by search for "custom" / "openai compatible".</summary>
    public IEnumerable<CatalogEntry> FilteredCatalog
    {
        get
        {
            var q = (_catalogSearch ?? "").Trim();
            var items = string.IsNullOrEmpty(q)
                ? Catalog
                : Catalog.Where(c =>
                    c.DisplayName.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                    c.Id.Contains(q, StringComparison.OrdinalIgnoreCase));
            var lq = q.ToLowerInvariant();
            var customMatches = string.IsNullOrEmpty(q)
                || "custom".Contains(lq) || lq.Contains("custom")
                || "openai compatible".Contains(lq) || lq.Contains("openai")
                || lq.Contains("compatible");
            // In the default view the Custom entry is the pinned button below the
            // list, so only surface it inside the list when the search asks for it.
            return (customMatches && !string.IsNullOrEmpty(q))
                ? items.Concat(new[] { CustomEntry }) : items;
        }
    }

    private bool _addOpen;
    public bool AddOpen
    {
        get => _addOpen;
        set => SetProperty(ref _addOpen, value);
    }

    /// <summary>"picker" -> "credentials" -> "models"</summary>
    private string _addStep = "picker";
    public string AddStep
    {
        get => _addStep;
        set => SetProperty(ref _addStep, value);
    }

    private CatalogEntry? _picked;
    public CatalogEntry? Picked
    {
        get => _picked;
        set => SetProperty(ref _picked, value);
    }

    // Credentials step
    private string _addDisplayName = "", _addBaseUrl = "", _addApiKey = "";
    public string AddDisplayName
    {
        get => _addDisplayName;
        set { if (SetProperty(ref _addDisplayName, value)) RefreshValidation(); }
    }
    public string AddBaseUrl
    {
        get => _addBaseUrl;
        set { if (SetProperty(ref _addBaseUrl, value)) RefreshValidation(); }
    }
    /// <summary>The key lives only long enough to be handed to the Vault. It is
    /// never persisted as client state and never read back.</summary>
    public string AddApiKey
    {
        get => _addApiKey;
        set { if (SetProperty(ref _addApiKey, value)) RefreshValidation(); }
    }

    // ---- §3 inline, field-specific validation ------------------------------
    // "Complete the required fields before saving." hid WHICH field was missing
    // even though the owner had filled URL and key. Each field now reports its
    // own actionable message and highlights its own control.
    /// <summary>Errors stay hidden until the owner tries to save or discover, so
    /// a freshly opened form is not covered in red.</summary>
    private bool _showValidation;
    public bool ShowValidation
    {
        get => _showValidation;
        set { if (SetProperty(ref _showValidation, value)) RefreshValidation(); }
    }

    public string DisplayNameError =>
        _showValidation && string.IsNullOrWhiteSpace(AddDisplayName)
            ? "Display Name is required." : "";

    public string BaseUrlError
    {
        get
        {
            if (!_showValidation) return "";
            var url = (AddBaseUrl ?? "").Trim();
            if (string.IsNullOrWhiteSpace(url)) return "Enter a valid Base URL.";
            if (!Uri.TryCreate(url, UriKind.Absolute, out var u)) return "Enter a valid Base URL.";
            return (u.Scheme == Uri.UriSchemeHttp || u.Scheme == Uri.UriSchemeHttps)
                ? "" : "Enter a valid Base URL.";
        }
    }

    public string ApiKeyError =>
        _showValidation && string.IsNullOrWhiteSpace(AddApiKey)
            ? "API key is required." : "";

    /// <summary>Recompute per-field errors whenever an input changes so the Save
    /// button and the inline messages always agree with what is typed.</summary>
    private void RefreshValidation()
    {
        OnPropertyChanged(nameof(DisplayNameError));
        OnPropertyChanged(nameof(BaseUrlError));
        OnPropertyChanged(nameof(ApiKeyError));
        OnPropertyChanged(nameof(CanSaveProvider));
    }

    /// <summary>First blocking error, phrased for the specific field (§3).</summary>
    private string FirstValidationError()
    {
        if (!string.IsNullOrEmpty(DisplayNameError)) return DisplayNameError;
        if (!string.IsNullOrEmpty(BaseUrlError)) return BaseUrlError;
        if (!string.IsNullOrEmpty(ApiKeyError)) return ApiKeyError;
        return "";
    }

    // ---- Add Provider Advanced (Point 2) ------------------------------------
    /// <summary>Auth schemes offered for non-standard / custom endpoints.</summary>
    public ObservableCollection<string> AuthSchemes { get; } = new() { "bearer", "basic", "custom" };

    private bool _addAdvanced;
    public bool AddAdvanced
    {
        get => _addAdvanced;
        set => SetProperty(ref _addAdvanced, value);
    }

    private string _addAuthScheme = "bearer";
    public string AddAuthScheme
    {
        get => _addAuthScheme;
        set => SetProperty(ref _addAuthScheme, value);
    }

    private string _addDiscoveryUrl = "";
    public string AddDiscoveryUrl
    {
        get => _addDiscoveryUrl;
        set => SetProperty(ref _addDiscoveryUrl, value);
    }

    private string _addTimeoutText = "20";
    public string AddTimeoutText
    {
        get => _addTimeoutText;
        set => SetProperty(ref _addTimeoutText, value);
    }

    private string _addHeadersText = "";
    public string AddHeadersText
    {
        get => _addHeadersText;
        set => SetProperty(ref _addHeadersText, value);
    }

    /// <summary>Parse the Advanced form into the discovery call parameters.
    /// Custom headers are lines of "Name: Value"; timeout is a positive
    /// seconds value (falls back to 20).</summary>
    private (Dictionary<string, string>? headers, string authScheme, double timeout) ParseAdvanced()
    {
        Dictionary<string, string>? headers = null;
        foreach (var raw in (_addHeadersText ?? "").Split('\n'))
        {
            var line = raw.Trim();
            if (line.Length == 0) continue;
            var idx = line.IndexOf(':');
            if (idx <= 0) continue;
            var k = line.Substring(0, idx).Trim();
            var v = line.Substring(idx + 1).Trim();
            if (k.Length == 0) continue;
            headers ??= new Dictionary<string, string>();
            headers[k] = v;
        }
        var auth = (_addAuthScheme ?? "bearer").Trim();
        double timeout = 20.0;
        if (double.TryParse((_addTimeoutText ?? "").Trim(), out var t) && t > 0) timeout = t;
        return (headers, auth, timeout);
    }

    public bool CanSaveProvider =>
        !string.IsNullOrWhiteSpace(AddDisplayName) &&
        !string.IsNullOrWhiteSpace(AddBaseUrl) &&
        !string.IsNullOrWhiteSpace(AddApiKey);

    private string _addStatus = "";
    public string AddStatus
    {
        get => _addStatus;
        set => SetProperty(ref _addStatus, value);
    }

    public ObservableCollection<DiscoveredModel> Discovered { get; } = new();

    /// <summary>§8 — makes the selection obvious: "3 selected".</summary>
    public int SelectedCount => Discovered.Count(m => m.IsSelected);

    public string SelectedSummary => $"{SelectedCount} selected";

    private void RaiseSelectionChanged()
    {
        OnPropertyChanged(nameof(SelectedCount));
        OnPropertyChanged(nameof(SelectedSummary));
    }

    private string _discoverySummary = "";
    public string DiscoverySummary
    {
        get => _discoverySummary;
        set => SetProperty(ref _discoverySummary, value);
    }

    private string _modelSearch = "";
    public string ModelSearch
    {
        get => _modelSearch;
        set
        {
            if (SetProperty(ref _modelSearch, value))
                OnPropertyChanged(nameof(FilteredDiscovered));
        }
    }

    public IEnumerable<DiscoveredModel> FilteredDiscovered
    {
        get
        {
            var q = (_modelSearch ?? "").Trim();
            return string.IsNullOrEmpty(q)
                ? Discovered
                : Discovered.Where(m =>
                    m.ModelId.Contains(q, StringComparison.OrdinalIgnoreCase) ||
                    m.DisplayName.Contains(q, StringComparison.OrdinalIgnoreCase));
        }
    }

    // Manual model entry
    private string _manualModelId = "", _manualModelName = "";
    public string ManualModelId
    {
        get => _manualModelId;
        set => SetProperty(ref _manualModelId, value);
    }
    public string ManualModelName
    {
        get => _manualModelName;
        set => SetProperty(ref _manualModelName, value);
    }

    public IRelayCommand OpenAddCommand => new RelayCommand(OpenAdd);
    public IRelayCommand CancelAddCommand => new RelayCommand(CancelAdd);
    public IRelayCommand<CatalogEntry> PickCatalogCommand => new RelayCommand<CatalogEntry>(PickCatalog);
    public IRelayCommand TestAddCommand => new AsyncRelayCommand(TestAddAsync);
    public IRelayCommand DiscoverAddCommand => new AsyncRelayCommand(DiscoverAddAsync);
    public IRelayCommand SelectAllModelsCommand => new RelayCommand(() => SetAllModelSelection(true));
    public IRelayCommand DeselectAllModelsCommand => new RelayCommand(() => SetAllModelSelection(false));
    public IRelayCommand AddManualModelCommand => new RelayCommand(AddManualModel);
    public IRelayCommand SaveProviderCommand => new AsyncRelayCommand(SaveProviderAsync);

    /// <summary>Voice setup is separate from adding specialist providers.</summary>
    public void OpenGeminiSetup()
    {
        new Genie.Desktop.Views.GeminiSetupWindow { Owner = System.Windows.Application.Current.MainWindow }.ShowDialog();
    }

    private void OpenAdd()
    {
        AddDisplayName = "";
        AddBaseUrl = "";
        AddApiKey = "";
        AddStatus = "";
        DiscoverySummary = "";
        ModelSearch = "";
        CatalogSearch = "";
        Discovered.Clear();
        Picked = null;
        ShowValidation = false;   // fresh form starts clean, not covered in red
        // §1, §2, §13 — skip the catalog picker entirely. The Add Provider
        // button opens a simple three-field form directly. No built-in presets
        // are shown to the owner in Normal mode.
        AddStep = "credentials";
        AddOpen = true;
        // Reset advanced options so each new provider starts clean.
        AddAdvanced = false;
        AddAuthScheme = "bearer";
        AddDiscoveryUrl = "";
        AddTimeoutText = "20";
        AddHeadersText = "";
    }

    private void CancelAdd()
    {
        AddOpen = false;
        Picked = null;
        AddApiKey = "";   // never keep the secret around
        Discovered.Clear();
    }

    private void PickCatalog(CatalogEntry? entry)
    {
        if (entry is null) return;
        Picked = entry;
        // Built-in providers preconfigure the canonical URL; the owner never
        // types a known provider's base URL by hand.
        AddDisplayName = entry.DisplayName;
        AddBaseUrl = entry.BaseUrl;
        AddStep = "credentials";
        AddStatus = entry.IsCustom
            ? "Enter a display name, base URL and API key."
            : $"{entry.DisplayName} is preconfigured. Enter your API key.";
    }

    private void SetAllModelSelection(bool selected)
    {
        foreach (var m in Discovered) m.IsSelected = selected && !m.IsSaved;
        RaiseSelectionChanged();
    }

    private async Task TestAddAsync()
    {
        if (string.IsNullOrWhiteSpace(AddApiKey) || string.IsNullOrWhiteSpace(AddBaseUrl))
        {
            AddStatus = "Enter a base URL and API key first.";
            return;
        }
        AddStatus = "Testing connection…";
        var (headers, auth, timeout) = ParseAdvanced();
        // Discovery doubles as the connection probe: it hits the provider with
        // the supplied key. A clear error here means the key/URL are wrong.
        using var doc = await _client.DiscoverModelsAsync("", AddBaseUrl, AddApiKey,
                                                          AddDiscoveryUrl, headers, auth, timeout);
        if (doc is null)
        {
            AddStatus = "Endpoint unreachable.";
            return;
        }
        var root = doc.RootElement;
        if (root.TryGetProperty("ok", out var ok) && ok.ValueKind != JsonValueKind.False)
        {
            AddStatus = $"Connected. {root.Str("count") ?? "0"} models available.";
            return;
        }
        AddStatus = (root.Str("error") ?? "Connection failed.")
                    + "\n" + DescribeRequest(root);
    }

    /// <summary>Describe the request the backend actually sent (URL, auth scheme,
    /// timeout — never the secret) so an auth failure can be diagnosed honestly
    /// rather than blamed on the key.</summary>
    private static string DescribeRequest(JsonElement root)
    {
        if (!root.TryGetProperty("request", out var req) ||
            req.ValueKind != JsonValueKind.Object) return "";
        var url = req.First("url") ?? "";
        var scheme = req.First("auth_scheme") ?? "";
        var timeout = req.TryGetProperty("timeout", out var t) ? t.ToString() : "";
        return string.IsNullOrWhiteSpace(url)
            ? ""
            : $"Request sent: {url} · auth: {scheme} · timeout: {timeout}s";
    }

    private async Task DiscoverAddAsync()
    {
        AddStatus = "Discovering models…";
        Discovered.Clear();
        var (headers, auth, timeout) = ParseAdvanced();
        using var doc = await _client.DiscoverModelsAsync("", AddBaseUrl, AddApiKey,
                                                          AddDiscoveryUrl, headers, auth, timeout);
        if (doc is null)
        {
            AddStatus = "Discovery failed — endpoint unreachable.";
            return;
        }
        var root = doc.RootElement;
        var ok = root.TryGetProperty("ok", out var okNode) && okNode.ValueKind != JsonValueKind.False;
        if (!ok)
        {
            // Owner-language reason (the backend provides it) + manual fallback.
            AddStatus = (root.Str("error") ?? "Model discovery failed.")
                        + " You can add a model manually."
                        + "\n" + DescribeRequest(root);
            return;
        }
        // Mark models the provider already has so they are never duplicated.
        var existing = new HashSet<string>(
            Providers.Where(p => string.Equals(p.Id, Picked?.Id, StringComparison.OrdinalIgnoreCase))
                     .SelectMany(p => p.Models.Select(m => m.ModelId)),
            StringComparer.OrdinalIgnoreCase);
        foreach (var m in root.Array("models"))
        {
            var mid = m.First("model_id", "id");
            if (string.IsNullOrWhiteSpace(mid)) continue;
            Discovered.Add(new DiscoveredModel
            {
                ModelId = mid,
                DisplayName = m.First("display_name", "name") ?? mid,
                IsSaved = existing.Contains(mid),
                // Endpoint-supplied preference metadata, used only for the
                // initial selection. GENIE never infers a capability from a
                // model name - the dynamic router decides later (spec §4).
                Preferred = m.Bool("preferred") || m.Bool("default"),
                SelectionChanged = RaiseSelectionChanged,
            });
        }
        // §9 — remember the auth scheme that actually worked. Discovery may
        // negotiate Bearer -> x-api-key -> api-key; whichever succeeded is
        // stored on the provider so Test/Discover/inference all reuse it and
        // the owner never has to configure it by hand.
        var negotiated = root.Str("auth_scheme");
        if (!string.IsNullOrWhiteSpace(negotiated)) AddAuthScheme = negotiated!;

        DiscoverySummary = $"{Discovered.Count} models found";
        // §4 — offer a neutral, editable name derived from the endpoint host so
        // the owner is not forced to type one. Never a commercial brand guess.
        SuggestDisplayNameIfBlank();
        AutoSelectDefaults();
        AddStep = "models";
        AddStatus = "";
    }

    /// <summary>§4 — derive a safe default name from the base-URL host. Falls
    /// back to "Custom Provider" when the URL cannot be parsed.</summary>
    private void SuggestDisplayNameIfBlank()
    {
        if (!string.IsNullOrWhiteSpace(AddDisplayName)) return;
        var url = (AddBaseUrl ?? "").Trim();
        if (Uri.TryCreate(url, UriKind.Absolute, out var u) &&
            !string.IsNullOrWhiteSpace(u.Host))
        {
            AddDisplayName = u.Host;
        }
        else if (!string.IsNullOrWhiteSpace(url))
        {
            AddDisplayName = "Custom Provider";
        }
    }

    /// <summary>
    /// Pre-select up to three models so the owner lands on a usable provider
    /// immediately. Three is a convenience default, never a requirement:
    /// the owner can keep, drop, swap or select-all freely afterwards (§4).
    /// Preference order is the endpoint's own metadata first, then list order.
    /// </summary>
    private void AutoSelectDefaults()
    {
        var selectable = Discovered.Where(m => m.CanSelect).ToList();
        if (selectable.Count == 0) return;
        var chosen = selectable.Where(m => m.Preferred).Take(3).ToList();
        if (chosen.Count < 3)
        {
            var rest = selectable.Where(m => !m.Preferred)
                                 .Take(3 - chosen.Count);
            chosen.AddRange(rest);
        }
        foreach (var m in selectable) m.IsSelected = false;
        foreach (var m in chosen) m.IsSelected = true;
    }

    private void AddManualModel()
    {
        var mid = ManualModelId?.Trim();
        if (string.IsNullOrWhiteSpace(mid))
        {
            AddStatus = "Enter a model ID.";
            return;
        }
        if (Discovered.Any(m => string.Equals(m.ModelId, mid, StringComparison.OrdinalIgnoreCase)))
        {
            AddStatus = "That model is already in the list.";
            return;
        }
        Discovered.Add(new DiscoveredModel
        {
            ModelId = mid,
            DisplayName = string.IsNullOrWhiteSpace(ManualModelName) ? mid : ManualModelName!.Trim(),
            IsSelected = true,
            SelectionChanged = RaiseSelectionChanged,
        });
        ManualModelId = "";
        ManualModelName = "";
        DiscoverySummary = $"{Discovered.Count} models found";
        AddStep = "models";
    }

    private async Task SaveProviderAsync()
    {
        if (!CanSaveProvider)
        {
            // §3 — name the actual missing/invalid field instead of a generic
            // "complete the required fields" message.
            ShowValidation = true;
            var err = FirstValidationError();
            AddStatus = string.IsNullOrEmpty(err)
                ? "Complete the required fields before saving." : err;
            return;
        }
        AddStatus = "Saving…";
        var id = Picked?.IsCustom == true || Picked is null
            ? MakeProviderId(AddDisplayName)
            : Picked!.Id;
        try
        {
            // Reuse an existing provider id rather than failing on duplicate.
            var exists = Providers.Any(p => string.Equals(p.Id, id, StringComparison.OrdinalIgnoreCase));
            if (!exists)
            {
                // Point 0 — the Advanced settings are persisted WITH the provider
                // so Add -> Save -> relaunch -> Edit restores them.
                var (advHeaders, advAuth, advTimeout) = ParseAdvanced();
                using var add = await _client.AddProviderAsync(
                    id, AddDisplayName, AddBaseUrl,
                    headers: advHeaders, authScheme: advAuth,
                    discoveryUrl: AddDiscoveryUrl, timeout: advTimeout,
                    custom: Picked?.IsCustom == true || Picked is null);
                if (add is null)
                {
                    AddStatus = "Could not create the provider.";
                    return;
                }
            }
            // Secret goes straight to the Vault; never returned to the client.
            using var keyDoc = await _client.SetProviderKeyAsync(id, AddApiKey);
            if (keyDoc is null)
            {
                AddStatus = "Provider created but the key could not be stored.";
                return;
            }
            var selected = Discovered.Where(m => m.IsSelected && !m.IsSaved).ToList();
            foreach (var m in selected)
            {
                using var _ = await _client.AddModelAsync(id, m.ModelId, m.DisplayName);
            }
            AddStatus = selected.Count == 0
                ? "Provider saved. No new models selected."
                : $"Provider saved. {selected.Count} model(s) added.";
            AddApiKey = "";
            await RefreshAsync();
            AddOpen = false;
        }
        catch (Exception ex)
        {
            AddStatus = "Save failed — " + ex.Message;
        }
    }

    private static string MakeProviderId(string displayName)
    {
        var sb = new System.Text.StringBuilder();
        foreach (var ch in displayName.Trim().ToLowerInvariant())
            sb.Append(char.IsLetterOrDigit(ch) ? ch : '_');
        var id = sb.ToString().Trim('_');
        while (id.Contains("__")) id = id.Replace("__", "_");
        return string.IsNullOrEmpty(id) ? "custom_provider" : id;
    }

    /// <summary>Parse "Name: Value" lines into a header map. Returns null when
    /// nothing valid is present, so an empty box stores an empty map rather than
    /// a stray entry. Never touches the API key — that lives in the Vault.</summary>
    private static Dictionary<string, string>? ParseHeaders(string? text)
    {
        Dictionary<string, string>? headers = null;
        foreach (var raw in (text ?? "").Split('\n'))
        {
            var line = raw.Trim();
            if (line.Length == 0) continue;
            var idx = line.IndexOf(':');
            if (idx <= 0) continue;
            var k = line.Substring(0, idx).Trim();
            var v = line.Substring(idx + 1).Trim();
            if (k.Length == 0) continue;
            headers ??= new Dictionary<string, string>();
            headers[k] = v;
        }
        return headers;
    }

    /// <summary>Render a stored header object back into editable "Name: Value"
    /// lines for the Edit form.</summary>
    private static string FormatHeaders(JsonElement headers)
    {
        var lines = new List<string>();
        foreach (var prop in headers.EnumerateObject())
            lines.Add($"{prop.Name}: {prop.Value}");
        return string.Join("\n", lines);
    }
}
