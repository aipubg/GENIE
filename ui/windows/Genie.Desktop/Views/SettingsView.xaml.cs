using System.Windows;
using System.Windows.Controls;
using Genie.Desktop.ViewModels;

namespace Genie.Desktop.Views;

public partial class SettingsView : UserControl
{
    private readonly System.Windows.Threading.DispatcherTimer _previewTimer = new() { Interval = TimeSpan.FromSeconds(3) };
    private bool _polling;

    public SettingsView()
    {
        InitializeComponent();
        Loaded += (_, _) => _previewTimer.Start();
        Unloaded += (_, _) => _previewTimer.Stop();
        _previewTimer.Tick += async (_, _) =>
        {
            if (_polling || DataContext is not SettingsViewModel vm) return;
            _polling = true;
            try { await vm.LoadLocalAsync(reloadSettings: false); }
            finally { _polling = false; }
        };
    }

    /// <summary>PasswordBox.Password is deliberately not bindable, so the value
    /// is pushed to the view model here. It is held only until the Vault write;
    /// the client never persists it and never reads it back.</summary>
    private void AddKey_PasswordChanged(object sender, RoutedEventArgs e)
    {
        if (sender is PasswordBox box && DataContext is SettingsViewModel vm)
            vm.AddApiKey = box.Password;
    }

    /// <summary>Replacement key for an existing provider. The box starts empty
    /// and is never populated from stored state, so a stored secret can never be
    /// echoed back. An untouched box leaves the existing key alone.</summary>
    private void EditKey_PasswordChanged(object sender, RoutedEventArgs e)
    {
        if (sender is not PasswordBox box) return;
        if (box.DataContext is not ProviderRow row) return;
        row.NewKey = box.Password;
        // Only a non-empty entry counts as an intent to replace, so clearing the
        // box again does not silently schedule a write.
        row.KeyDirty = !string.IsNullOrEmpty(box.Password);
    }
}
