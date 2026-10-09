using System.ComponentModel;
using System.Diagnostics;
using System.Text.Json;
using System.Windows;
using Genie.Desktop.Services;

namespace Genie.Desktop.Views;

public partial class GeminiSetupWindow : Window
{
    private readonly BackendClient _client = new();
    private bool _busy;

    public GeminiSetupWindow()
    {
        InitializeComponent();
        Loaded += (_, _) => ApiKey.Focus();
        Closed += (_, _) => { ApiKey.Clear(); _client.Dispose(); };
        Closing += (_, e) => { if (_busy) e.Cancel = true; };
    }

    public static async Task ShowIfNeededAsync(Window owner)
    {
        using var client = new BackendClient();
        using var state = await client.GetJsonAsync("/api/voice/setup");
        if (state is not null && state.RootElement.TryGetProperty("completed", out var done)
            && done.ValueKind == JsonValueKind.True) return;
        if (owner.IsVisible) new GeminiSetupWindow { Owner = owner }.ShowDialog();
    }

    private async void Connect_Click(object sender, RoutedEventArgs e)
    {
        if (_busy) return;
        var key = ApiKey.Password.Trim();
        if (key.Length < 10) { SetupStatus.Text = "Enter your Gemini API key."; return; }
        _busy = true;
        Connect.IsEnabled = Later.IsEnabled = ApiKey.IsEnabled = false;
        SetupStatus.Text = "Verifying Gemini access...";
        try
        {
            using var result = await _client.PostJsonAsync("/api/voice/setup", new { key });
            if (result is not null && result.RootElement.TryGetProperty("ok", out var ok)
                && ok.ValueKind == JsonValueKind.True)
            {
                _busy = false;
                DialogResult = true;
                return;
            }
            SetupStatus.Text = result?.RootElement.Str("error") ?? "GENIE could not reach its backend. Try again.";
        }
        catch { SetupStatus.Text = "Setup could not finish. Check the connection and retry."; }
        finally
        {
            key = "";
            ApiKey.Clear();
            _busy = false;
            Connect.IsEnabled = Later.IsEnabled = ApiKey.IsEnabled = true;
        }
    }

    private void Later_Click(object sender, RoutedEventArgs e) => DialogResult = false;
    private void GetKey_Click(object sender, RoutedEventArgs e)
        => Process.Start(new ProcessStartInfo("https://aistudio.google.com/apikey") { UseShellExecute = true });
}
