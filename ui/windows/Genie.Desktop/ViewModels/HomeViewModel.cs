using System.Text.Json;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

/// <summary>
/// Visual state of the Home core. Drives both the Storyboard pulse and the
/// short status label. Default is Idle.
/// </summary>
public enum HomeState { Idle, Listening, Thinking, Speaking, Error }

/// <summary>
/// Home is the voice/presence surface. The central GENIE core IS the
/// microphone control: click to start listening, click again to stop.
/// Text interaction lives on the dedicated Chat page - it is deliberately not
/// duplicated here.
///
/// Nothing is faked: if the backend reports no audio input, Home says the
/// microphone is unavailable rather than animating a listening state.
/// </summary>
public sealed class HomeViewModel : ObservableObject, IDisposable
{
    private readonly BackendClient _client;
    private readonly DispatcherTimer _clock;
    private readonly DispatcherTimer _voicePoll;
    private readonly DispatcherTimer _ampPoll;

    private string _greeting = "";
    private bool _busy;
    private HomeState _state = HomeState.Idle;
    private double _micLevel, _ttsLevel;
    private bool _advanced = UiDiagnostics.Advanced;
    private bool _microphoneAvailable;
    private string _micUnavailableReason = "Microphone unavailable.";
    private bool _capturing;
    private bool _pollingVoice, _pollingAmplitude;
    private string _voiceError = "", _transcript = "", _voiceReply = "";
    private string _awarenessStatus = "";
    private bool _screenSharing, _screenBusy;
    public bool ScreenSharing { get => _screenSharing; private set => SetProperty(ref _screenSharing, value); }
    public IRelayCommand ToggleScreenCommand => new AsyncRelayCommand(async () =>
    {
        if (_screenBusy) return;
        _screenBusy = true;
        try
        {
            var enable = !ScreenSharing;
            using var result = await _client.PostJsonAsync("/api/desktop/settings", new {
                settings = new { enabled = true, local_preview = true, remote_visual_consent = enable } });
            if (result is not null && result.RootElement.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.True)
                ScreenSharing = enable;
            else VoiceError = "Could not update screen sharing. Check Desktop awareness in Settings.";
        }
        finally { _screenBusy = false; OnPropertyChanged(nameof(ScreenSharing)); }
    });
    public string AwarenessStatus { get => _awarenessStatus; set => SetProperty(ref _awarenessStatus, value); }
    public string VoiceError { get => _voiceError; set => SetProperty(ref _voiceError, value); }
    public string Transcript { get => _transcript; set => SetProperty(ref _transcript, value); }
    public string VoiceReply { get => _voiceReply; set => SetProperty(ref _voiceReply, value); }
    public IRelayCommand InterruptVoiceCommand => new AsyncRelayCommand(async () =>
    {
        using var result = await _client.PostJsonAsync("/api/voice/barge-in", new { });
    });

    public HomeViewModel(BackendClient client)
    {
        _client = client;
        UpdateGreeting();
        _clock = new DispatcherTimer { Interval = TimeSpan.FromSeconds(30) };
        _clock.Tick += (_, _) => UpdateGreeting();
        _clock.Start();

        // Voice status drives the core: capturing -> Listening, and
        // input.available -> whether the mic control is usable at all.
        _voicePoll = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _voicePoll.Tick += async (_, _) => await PollVoiceAsync();
        _voicePoll.Start();

        // Real amplitude meters (mic + playback) at a sensible sample rate.
        // Only runs while Home is visible — Pause() stops it.
        _ampPoll = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(150) };
        _ampPoll.Tick += async (_, _) => await PollAmplitudeAsync();
        _ampPoll.Start();

        _ = LoadStatusAsync();
        _ = PollVoiceAsync();
        _ = PollAmplitudeAsync();

        // Advanced is a Settings-owned diagnostics switch; Home honours it so the
        // owner can watch the real meters while speaking.
        UiDiagnostics.AdvancedChanged += on =>
        {
            System.Windows.Application.Current?.Dispatcher.Invoke(() => Advanced = on);
        };
    }

    public string Greeting
    {
        get => _greeting;
        set => SetProperty(ref _greeting, value);
    }

    public bool Busy
    {
        get => _busy;
        set
        {
            if (SetProperty(ref _busy, value)) OnPropertyChanged(nameof(NotBusy));
        }
    }

    public bool NotBusy => !_busy;

    // ------------------------------------------------------------- core state

    /// <summary>Current core state. Property changed events drive the
    /// VisualStateManager on HomeView (see HomeView.xaml.cs).</summary>
    public HomeState State
    {
        get => _state;
        set
        {
            if (SetProperty(ref _state, value))
            {
                OnPropertyChanged(nameof(StateLabel));
                OnPropertyChanged(nameof(MicHint));
                OnPropertyChanged(nameof(MicBrush));
                OnPropertyChanged(nameof(AmplitudeText));
                StateChanged?.Invoke(value);
            }
        }
    }

    /// <summary>Fired when State changes so the view can call GoToState.</summary>
    public event Action<HomeState>? StateChanged;

    /// <summary>Short label shown under the core. NEVER a raw JSON dict.</summary>
    public string StateLabel => State switch
    {
        HomeState.Idle      => "Ready.",
        HomeState.Listening => "Listening…",
        HomeState.Thinking  => "Thinking…",
        HomeState.Speaking  => "Speaking…",
        HomeState.Error     => "Something went wrong.",
        _ => "",
    };

    // ------------------------------------------------------------ microphone

    /// <summary>True only when the backend reports a usable audio input.
    /// Home never animates a listening state without this.</summary>
    public bool MicrophoneAvailable
    {
        get => _microphoneAvailable;
        set
        {
            // MicHint is derived from this. Without the extra notification the
            // hint stayed on its first value ("Microphone unavailable") for the
            // whole session, contradicting the enabled core next to it.
            if (SetProperty(ref _microphoneAvailable, value))
                OnPropertyChanged(nameof(MicHint));
        }
    }

    public bool Capturing
    {
        get => _capturing;
        set
        {
            if (SetProperty(ref _capturing, value))
                OnPropertyChanged(nameof(MicHint));
        }
    }

    public string MicUnavailableReason
    {
        get => _micUnavailableReason;
        set => SetProperty(ref _micUnavailableReason, value);
    }

    /// <summary>Live microphone amplitude (0..1) from the backend voice
    /// pipeline. Drives the Home core's reactive glow/scale while listening.
    /// Never fabricated — sourced from the real STT amplitude meter.</summary>
    public double MicLevel
    {
        get => _micLevel;
        private set
        {
            if (!SetProperty(ref _micLevel, value))
                return;
            OnPropertyChanged(nameof(AmplitudeText));
        }
    }

    /// <summary>Live playback (TTS) amplitude (0..1) from the backend. Drives
    /// the reactive response while GENIE is speaking.</summary>
    public double TtsLevel
    {
        get => _ttsLevel;
        private set
        {
            if (!SetProperty(ref _ttsLevel, value))
                return;
            OnPropertyChanged(nameof(AmplitudeText));
        }
    }

    /// <summary>Real amplitude readout for Advanced diagnostics. This is the
    /// measured value, not a decoration: if this number does not move while the
    /// owner speaks, the microphone is genuinely not being heard.</summary>
    public string AmplitudeText =>
        $"Mic RMS {MicLevel:0.000}  ·  TTS RMS {TtsLevel:0.000}  ·  {StateLabel}";

    /// <summary>True when the owner has Advanced mode on. Home only shows its
    /// amplitude readout then; normal mode stays clean.</summary>
    public bool Advanced
    {
        get => _advanced;
        private set => SetProperty(ref _advanced, value);
    }

    /// <summary>Segoe MDL2 microphone glyph.</summary>
    public string MicGlyph => "\uE720";

    /// <summary>Brush key for the mic indicator: cyan while listening.</summary>
    public string MicBrush => State == HomeState.Listening ? "G.Cyan" : "G.TextMid";

    /// <summary>One-line affordance telling the owner what clicking does.</summary>
    public string MicHint => !MicrophoneAvailable
        ? "Microphone unavailable"
        : Capturing
            ? "Voice active - click GENIE to stop"
            : "Click GENIE to speak";

    /// <summary>Toggles the microphone. This is what the central core invokes.
    /// Start/stop go through the backend voice pipeline; the client never
    /// pretends to be capturing.</summary>
    public IRelayCommand ToggleMicCommand => new AsyncRelayCommand(ToggleMicAsync);

    private async Task ToggleMicAsync()
    {
        if (Busy) return;
        Busy = true;
        try
        {
            if (Capturing)
            {
                using var stop = await _client.StopListeningAsync();
                if (stop is null)
                {
                    State = HomeState.Error;
                    return;
                }
                if (stop.RootElement.TryGetProperty("ok", out var stopped) && stopped.ValueKind == JsonValueKind.False)
                {
                    VoiceError = stop.RootElement.Str("error") ?? "Voice is still stopping.";
                    return;
                }
                Capturing = false;
                State = HomeState.Idle;
            }
            else
            {
                using var start = await _client.StartListeningAsync();
                if (start is null)
                {
                    State = HomeState.Error;
                    return;
                }
                var root = start.RootElement;
                // Honest failure: the pipeline reports ok=false rather than
                // pretending to listen.
                var ok = root.TryGetProperty("ok", out var okNode) && okNode.ValueKind != JsonValueKind.False;
                if (!ok)
                {
                    MicUnavailableReason = root.Str("error") ?? "Could not start listening.";
                    VoiceError = MicUnavailableReason;
                    State = HomeState.Error;
                    return;
                }
                Capturing = true;
                VoiceError = "";
                State = HomeState.Thinking;
            }
        }
        catch (Exception ex)
        {
            MicUnavailableReason = "Microphone error — " + ex.Message;
            State = HomeState.Error;
        }
        finally
        {
            Busy = false;
        }
    }

    private void UpdateGreeting()
    {
        var hour = DateTime.Now.Hour;
        Greeting = hour switch
        {
            < 5 => "Good night",
            < 12 => "Good morning",
            < 17 => "Good afternoon",
            _ => "Good evening",
        };
    }

    private async Task LoadStatusAsync()
    {
        using var doc = await _client.StatusAsync();
        if (doc is null && State != HomeState.Listening)
            State = HomeState.Error;
    }

    /// <summary>Sync microphone availability and capture state from the
    /// backend voice pipeline. Runs on a slow timer; it never fabricates a
    /// listening state when the backend says the input is unavailable.</summary>
    private async Task PollVoiceAsync()
    {
        if (_pollingVoice) return;
        _pollingVoice = true;
        try
        {
            using var doc = await _client.GetVoiceAsync();
            if (doc is null)
            {
                MicrophoneAvailable = false;
                MicUnavailableReason = "Voice status unavailable (backend unreachable).";
                return;
            }
            var root = doc.RootElement;

            // The backend nests live voice state under "pipeline". Reading the
            // top level silently yielded nothing, which meant Home reverted to
            // Idle two seconds after every click and never noticed a missing
            // microphone. Accept a flat payload too so this cannot regress.
            var pipeline = root.TryGetProperty("pipeline", out var p) &&
                           p.ValueKind == JsonValueKind.Object
                ? p
                : root;

            // ---- microphone availability ----
            var inputAvailable = true;
            var haveInput = pipeline.TryGetProperty("input", out var input) &&
                            input.ValueKind == JsonValueKind.Object;
            if (!haveInput &&
                root.TryGetProperty("providers", out var providers) &&
                providers.ValueKind == JsonValueKind.Object &&
                providers.TryGetProperty("input", out var providerInput) &&
                providerInput.ValueKind == JsonValueKind.Object)
            {
                input = providerInput;
                haveInput = true;
            }
            if (haveInput && input.TryGetProperty("available", out var av))
                inputAvailable = av.ValueKind != JsonValueKind.False;
            MicrophoneAvailable = inputAvailable;

            if (!inputAvailable)
            {
                var reasons = pipeline.Array("degraded")
                                      .Select(d => d.ToString())
                                      .Where(s => !string.IsNullOrWhiteSpace(s))
                                      .ToList();
                var reason = string.Join(" · ", reasons);
                MicUnavailableReason = string.IsNullOrWhiteSpace(reason)
                    ? "No audio input detected."
                    : reason;
            }

            var capturing = pipeline.TryGetProperty("capturing", out var cap) &&
                            cap.ValueKind == JsonValueKind.True;
            Capturing = capturing;
            VoiceError = pipeline.Str("error") ?? "";
            Transcript = pipeline.Str("last_user") ?? "";
            VoiceReply = pipeline.Str("last_reply") ?? "";
            State = (pipeline.Str("state") ?? "").ToUpperInvariant() switch
            {
                "ERROR" => HomeState.Error,
                "SPEAKING" => HomeState.Speaking,
                "THINKING" or "CONNECTING" or "RECONNECTING" => HomeState.Thinking,
                _ => capturing ? HomeState.Listening : HomeState.Idle
            };
            using var awareness = await _client.GetJsonAsync("/api/local-awareness");
            if (awareness is not null)
            {
                var local = awareness.RootElement;
                var running = local.TryGetProperty("running", out var run) && run.ValueKind == JsonValueKind.True;
                var camera = local.TryGetProperty("camera_active", out var cam) && cam.ValueKind == JsonValueKind.True;
                AwarenessStatus = (running ? "Local awareness on" : "Local awareness paused")
                    + (camera ? " | Camera active" : "") + "\n" + local.Str("camera_error") + " " + local.Str("last_event");
            }
            using var desktop = await _client.GetJsonAsync("/api/desktop");
            if (!_screenBusy && desktop is not null && desktop.RootElement.TryGetProperty("settings", out var settings))
                ScreenSharing = settings.TryGetProperty("remote_visual_consent", out var consent) && consent.ValueKind == JsonValueKind.True;
        }
        catch (Exception ex)
        {
            MicrophoneAvailable = false;
            MicUnavailableReason = "Voice status error — " + ex.Message;
        }
        finally { _pollingVoice = false; }
    }

    /// <summary>Pull the live amplitude meters the backend already computes and
    /// smooth them. Runs on a fast timer but only while Home is visible (Pause
    /// stops it). The render loop reads MicLevel / TtsLevel directly.</summary>
    private async Task PollAmplitudeAsync()
    {
        if (_pollingAmplitude) return;
        _pollingAmplitude = true;
        try
        {
            using var doc = await _client.GetVoiceLevelsAsync();
            if (doc is null) return;
            var root = doc.RootElement;
            var pipeline = root.TryGetProperty("pipeline", out var p) &&
                           p.ValueKind == JsonValueKind.Object ? p : root;
            // SettingsViewModel already proved the correct contract: the backend
            // emits a dict {"level":0.123,"source":"microphone","synthetic":false}.
            // Home must read the "level" property, falling back to a plain number
            // if the contract ever changes, and defaulting to 0 when absent.
            var mic = 0.0;
            if (pipeline.TryGetProperty("amplitude", out var a))
            {
                if (a.ValueKind == JsonValueKind.Object &&
                    a.TryGetProperty("level", out var ml) &&
                    ml.ValueKind == JsonValueKind.Number)
                    mic = ml.GetDouble();
                else if (a.ValueKind == JsonValueKind.Number)
                    mic = a.GetDouble();
            }
            var tts = 0.0;
            if (pipeline.TryGetProperty("tts_amplitude", out var ta))
            {
                if (ta.ValueKind == JsonValueKind.Object &&
                    ta.TryGetProperty("level", out var tl) &&
                    tl.ValueKind == JsonValueKind.Number)
                    tts = tl.GetDouble();
                else if (ta.ValueKind == JsonValueKind.Number)
                    tts = ta.GetDouble();
            }
            // Clamp to a sane 0..1 and smooth so the visual eases, not jumps.
            mic = Math.Min(1.0, Math.Max(0.0, mic));
            tts = Math.Min(1.0, Math.Max(0.0, tts));
            MicLevel += (mic - MicLevel) * 0.5;
            TtsLevel += (tts - TtsLevel) * 0.5;
        }
        catch (Exception)
        {
            // Leave the last values; a transient fetch miss must not freeze the core.
        }
        finally { _pollingAmplitude = false; }
    }

    /// <summary>Stop the voice + amplitude polling. Called when Home is hidden
    /// or the window is minimised, so we are not polling a backend the owner
    /// cannot see.</summary>
    public void Pause()
    {
        _voicePoll.Stop();
        _ampPoll.Stop();
    }

    /// <summary>Resume polling after Pause (Home visible / window restored).</summary>
    public void Resume()
    {
        if (!_voicePoll.IsEnabled) _voicePoll.Start();
        if (!_ampPoll.IsEnabled) _ampPoll.Start();
        _ = PollVoiceAsync();
        _ = PollAmplitudeAsync();
    }

    public void Dispose()
    {
        _clock.Stop();
        _voicePoll.Stop();
        _ampPoll.Stop();
    }
}
