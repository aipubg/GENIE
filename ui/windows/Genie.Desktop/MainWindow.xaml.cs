using System.Windows;
using Genie.Desktop.Services;
using Genie.Desktop.ViewModels;

namespace Genie.Desktop;

public partial class MainWindow : Window
{
    private MainViewModel? _vm;
    private BackendClient? _client;

    public MainWindow() => InitializeComponent();

    public void Attach(BackendLifecycle lifecycle, bool backendHealthy)
    {
        _client = new BackendClient();
        _vm = new MainViewModel(_client, lifecycle);
        DataContext = _vm;
        if (!backendHealthy) _vm.StatusText = "Backend unavailable";
    }

    protected override void OnClosed(System.EventArgs e)
    {
        _vm?.Dispose();
        _client?.Dispose();
        base.OnClosed(e);
    }
}
