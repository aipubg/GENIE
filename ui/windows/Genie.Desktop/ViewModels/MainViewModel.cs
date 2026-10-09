using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.ComponentModel;
using Genie.Desktop.Services;

namespace Genie.Desktop.ViewModels;

public sealed class NavItem
{
    public string Title { get; init; } = "";
    public object? Target { get; init; }
    // Segoe MDL2 Assets glyph. Consistent Windows iconography without shipping
    // raster icons for every entry.
    public string Glyph { get; init; } = "\uE10F";
}

/// <summary>
/// Shell state only: which surface is showing and whether the backend is up.
/// No GENIE business state lives here - that stays authoritative in the backend.
/// </summary>
public sealed class MainViewModel : ObservableObject, IDisposable
{
    private readonly BackendClient _client;
    private readonly BackendLifecycle _lifecycle;
    private readonly DispatcherTimer _healthTimer;
    private readonly DispatcherTimer _approvalTimer;
    private bool _checkingApprovals;
    private bool _disposed;

    private object? _current;
    private string _statusText = "Starting…";
    private bool _connected;

    public MainViewModel(BackendClient client, BackendLifecycle lifecycle)
    {
        _client = client;
        _lifecycle = lifecycle;

        Home = new HomeViewModel(client);
        Chat = new ChatViewModel(client);
        Missions = new MissionsViewModel(client);
        Agents = new AgentsViewModel(client);
        Computer = new ComputerViewModel(client);

        // Operational surfaces. Each projects its own payload into owner-facing
        // rows: a shared envelope dump put backend vocabulary ("registered =
        // True", "summary = total=0 by_severity={...}") in front of the owner.
        Skills = new SkillsViewModel(client);
        Devices = new DevicesViewModel(client);
        Memory = new MemoryViewModel(client);
        Forecast = new ForecastViewModel(client);
        Security = new SecurityViewModel(client);

        // W6 surfaces. Competition and Experience have real authorities behind
        // them (the competition engine / experience bank). Knowledge and Media
        // gained real authorities later; they answer honestly when empty.
        // Competition gets a real workflow: run candidates against a task, watch
        // the outcome, cancel a long run. A generic row list cannot express that.
        Competition = new CompetitionViewModel(client);
        // Experience gets a real reading surface: the digest, filterable by
        // mission / capability / agent / provider.
        Experience = new ExperienceViewModel(client);
        Knowledge = new KnowledgeViewModel(client);
        Media = new MediaViewModel(client);

        Settings = new SettingsViewModel(client);

        Items = new ObservableCollection<NavItem>
        {
            new() { Title = "Home", Glyph = "\uE10F", Target = Home },
            new() { Title = "Chat", Glyph = "\uE8BD", Target = Chat },
            new() { Title = "Missions", Glyph = "\uE9D9", Target = Missions },
            new() { Title = "Agents", Glyph = "\uE99A", Target = Agents },
            new() { Title = "Computer", Glyph = "\uE7F4", Target = Computer },
            new() { Title = "Skills", Glyph = "\uE945", Target = Skills },
            new() { Title = "Devices", Glyph = "\uE8EA", Target = Devices },
            new() { Title = "Memory", Glyph = "\uE8B7", Target = Memory },
            new() { Title = "Forecast", Glyph = "\uE81C", Target = Forecast },
            new() { Title = "Security", Glyph = "\uEA83", Target = Security },
            new() { Title = "Competition", Glyph = "\uE7C3", Target = Competition },
            new() { Title = "Experience", Glyph = "\uE1CE", Target = Experience },
            new() { Title = "Knowledge", Glyph = "\uE736", Target = Knowledge },
            new() { Title = "Media", Glyph = "\uE8B9", Target = Media },
            new() { Title = "Settings", Glyph = "\uE713", Target = Settings },
        };
        // Set via the property setter so PropertyChanged fires - otherwise the
        // ListBox's SelectedValue binding never sees the initial value and the
        // nav opens on whatever item happens to be selected by default.
        Current = Home;

        _healthTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(10) };
        _healthTimer.Tick += async (_, _) => await RefreshAsync();
        _healthTimer.Start();
        _approvalTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _approvalTimer.Tick += async (_, _) => await CheckApprovalsAsync();
        _approvalTimer.Start();
        _ = RefreshAsync();
    }

    public HomeViewModel Home { get; }
    public ChatViewModel Chat { get; }
    public MissionsViewModel Missions { get; }
    public AgentsViewModel Agents { get; }
    public ComputerViewModel Computer { get; }
    public SkillsViewModel Skills { get; }
    public DevicesViewModel Devices { get; }
    public MemoryViewModel Memory { get; }
    public ForecastViewModel Forecast { get; }
    public SecurityViewModel Security { get; }
    public CompetitionViewModel Competition { get; }
    public ExperienceViewModel Experience { get; }
    public KnowledgeViewModel Knowledge { get; }
    public MediaViewModel Media { get; }
    public SettingsViewModel Settings { get; }
    public ObservableCollection<NavItem> Items { get; }

    public object? Current
    {
        get => _current;
        set => SetProperty(ref _current, value);
    }

    public string StatusText
    {
        get => _statusText;
        set => SetProperty(ref _statusText, value);
    }

    public bool Connected
    {
        get => _connected;
        set
        {
            if (SetProperty(ref _connected, value))
            {
                OnPropertyChanged(nameof(StatusColor));
            }
        }
    }

    /// <summary>Theme brush key used by the shell status pill, dot, etc.
    /// The visual layer maps the key to the actual brush so colours stay
    /// in one place (Themes/GeniePalette.xaml).</summary>
    public string StatusColor => Connected ? "G.OK" : "G.Error";

    /// <summary>The backend PID. Diagnostics only: the owner-facing shell
    /// status pill says "Ready", never a raw pid. Surfaced in Settings
    /// Advanced alongside the other raw internals.</summary>
    public string BackendPidText =>
        _lifecycle.BackendPid?.ToString() ?? "external";

    public async Task RefreshAsync()
    {
        Connected = await _client.HealthAsync();
        // Normal mode says Ready. A pid is a developer detail; the owner has
        // never asked for it and it reads as debug output in a status pill.
        StatusText = Connected ? "Ready" : "Backend unavailable";
        OnPropertyChanged(nameof(BackendPidText));
    }

    private async Task CheckApprovalsAsync()
    {
        if (_checkingApprovals || _disposed || !Connected) return;
        _checkingApprovals = true;
        try
        {
            using var response = await _client.GetJsonAsync("/api/action/approvals");
            if (response is null || _disposed) return;
            foreach (var item in response.RootElement.Array("pending"))
            {
                var id = item.Str("id");
                if (string.IsNullOrEmpty(id)) continue;
                var owner = Application.Current.MainWindow;
                if (owner is null || !owner.IsVisible) return;
                var answer = MessageBox.Show(owner, item.Str("summary") ?? "Allow this action?",
                    "GENIE - Confirm action", MessageBoxButton.YesNo,
                    MessageBoxImage.Question, MessageBoxResult.No);
                using var decision = await _client.PostJsonAsync("/api/action/approvals/resolve",
                    new { id, allow = answer == MessageBoxResult.Yes });
                if (decision is null || !decision.RootElement.Bool("ok"))
                    StatusText = "Confirmation expired. Ask GENIE to inspect the target again.";
                break;
            }
        }
        finally { _checkingApprovals = false; }
    }

    public void Dispose()
    {
        _disposed = true;
        _approvalTimer.Stop();
        _healthTimer.Stop();
        Home.Dispose();
    }
}
