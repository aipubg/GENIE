namespace Genie.Desktop.ViewModels;

/// <summary>
/// Process-wide UI diagnostics switch.
///
/// Advanced mode is owned by Settings (it is a diagnostics toggle, not a
/// product mode), but Home needs to honour it too so the owner can watch the
/// REAL amplitude meters while speaking. A tiny static flag keeps that
/// consistent without plumbing a service through every view model.
///
/// Diagnostics only — it never changes behaviour, only what is displayed.
/// </summary>
public static class UiDiagnostics
{
    private static bool _advanced;

    /// <summary>True when the owner has switched Advanced mode on in Settings.</summary>
    public static bool Advanced
    {
        get => _advanced;
        set
        {
            if (_advanced == value) return;
            _advanced = value;
            AdvancedChanged?.Invoke(value);
        }
    }

    /// <summary>Raised so visible surfaces can show/hide their diagnostics
    /// block immediately rather than on the next navigation.</summary>
    public static event System.Action<bool>? AdvancedChanged;

    /// <summary>Compact build identity for diagnostics surfaces (never shown in
    /// normal mode).</summary>
    public static string ExePath { get; set; } = "";
}
