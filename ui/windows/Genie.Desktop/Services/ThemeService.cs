using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using Microsoft.Win32;

namespace Genie.Desktop.Services;

/// <summary>How GENIE picks its appearance.</summary>
public enum ThemeMode
{
    /// <summary>Follow the Windows apps theme (default).</summary>
    System,
    Light,
    Dark,
}

/// <summary>
/// Applies the light/dark palette and the native title-bar chrome.
///
/// The brushes live in ONE shared ResourceDictionary (GeniePalette.xaml) and
/// views bind to the brush objects. Recolouring those objects in place
/// therefore repaints every surface immediately - no dictionary swapping and no
/// changes to the hundreds of existing bindings.
/// </summary>
public sealed class ThemeService
{
    private const string PersonalizeKey =
        @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize";
    private const string AppsUseLightTheme = "AppsUseLightTheme";

    // DWMWA_USE_IMMERSIVE_DARK_MODE (20 on Windows 10 1809+ / 11; 19 on some
    // earlier Insider builds). Both are attempted.
    private const int DwmwaUseImmersiveDarkMode = 20;
    private const int DwmwaUseImmersiveDarkModeLegacy = 19;

    [DllImport("dwmapi.dll", PreserveSig = true)]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr,
                                                    ref int value, int attrSize);

    public ThemeMode Mode { get; private set; } = ThemeMode.System;

    /// <summary>True when the currently applied palette is the dark one.</summary>
    public bool IsDark { get; private set; } = true;

    public event Action? ThemeChanged;

    // ------------------------------------------------------------- detection
    /// <summary>Authoritative Windows "apps" theme preference. 1 = light.</summary>
    private static bool SystemPrefersLight()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(PersonalizeKey);
            var v = key?.GetValue(AppsUseLightTheme);
            return v is int i && i != 0;
        }
        catch
        {
            return false;   // unknown -> keep the existing dark identity
        }
    }

    private bool EffectiveDark() => Mode switch
    {
        ThemeMode.Light => false,
        ThemeMode.Dark => true,
        _ => !SystemPrefersLight(),
    };

    // ----------------------------------------------------------------- apply
    public void SetMode(ThemeMode mode)
    {
        Mode = mode;
        Apply();
    }

    /// <summary>Re-read the preference and repaint if it changed. Cheap and
    /// safe to call from a WM_SETTINGCHANGE notification.</summary>
    public void Refresh() => Apply();

    /// <summary>
    /// Swap the palette dictionary BEFORE any window is created.
    ///
    /// Views resolve brushes with StaticResource, which is fixed at parse
    /// time - so the theme must be in place before MainWindow is built. Doing
    /// it here also means no dark->light flash on startup.
    /// </summary>
    public void ApplyStartupResources()
    {
        var app = Application.Current;
        if (app is null) return;

        IsDark = EffectiveDark();
        _applied = true;

        var dicts = app.Resources.MergedDictionaries;
        for (var i = dicts.Count - 1; i >= 0; i--)
        {
            var src = dicts[i].Source?.OriginalString ?? "";
            if (src.EndsWith("Theme.Light.xaml", StringComparison.OrdinalIgnoreCase) ||
                src.EndsWith("Theme.Dark.xaml", StringComparison.OrdinalIgnoreCase) ||
                src.EndsWith("GeniePalette.xaml", StringComparison.OrdinalIgnoreCase))
            {
                dicts.RemoveAt(i);
            }
        }
        // Insert at 0 so the control styles (which reference these brushes)
        // still resolve, and no lookup happens before the palette exists.
        dicts.Insert(0, new ResourceDictionary
        {
            Source = new Uri(IsDark
                ? "Themes/Theme.Dark.xaml"
                : "Themes/Theme.Light.xaml",
                UriKind.Relative),
        });
    }

    private void Apply()
    {
        var dark = EffectiveDark();
        if (dark == IsDark && _applied) return;

        IsDark = dark;
        _applied = true;

        var app = Application.Current;
        if (app is null) return;

        app.Dispatcher.Invoke(() =>
        {
            SetColor(app, "G.Bg", dark ? "#FF060912" : "#FFF5F7FB");
            SetColor(app, "G.BgDeep", dark ? "#FF030611" : "#FFE9EEF7");
            SetColor(app, "G.Surface", dark ? "#FF0E1726" : "#FFFFFFFF");
            SetColor(app, "G.Surface2", dark ? "#FF142036" : "#FFF2F5FB");
            SetColor(app, "G.Line", dark ? "#FF22324F" : "#FFD3DCEB");
            SetColor(app, "G.LineSoft", dark ? "#FF1B2742" : "#FFE3E9F4");

            SetColor(app, "G.Text", dark ? "#FFEDF2FF" : "#FF0F1B2D");
            SetColor(app, "G.TextMid", dark ? "#FFA9B7D9" : "#FF4A5A75");
            SetColor(app, "G.TextDim", dark ? "#FF6E7F9C" : "#FF74849E");

            SetColor(app, "G.Primary", dark ? "#FF4DA3FF" : "#FF1F6FEB");
            SetColor(app, "G.PrimaryDark", dark ? "#FF2F7BE0" : "#FF1554B8");
            SetColor(app, "G.Cyan", dark ? "#FF55E1FF" : "#FF0B7C93");
            SetColor(app, "G.Violet", dark ? "#FF8C7DFF" : "#FF6A55E0");
            SetColor(app, "G.Gold", dark ? "#FFE6B86A" : "#FF9A6410");

            SetColor(app, "G.OK", dark ? "#FF6DD58A" : "#FF1E8E3E");
            SetColor(app, "G.Warn", dark ? "#FFE8B25A" : "#FFB06000");
            SetColor(app, "G.Error", dark ? "#FFE0707A" : "#FFC5221F");

            ApplyGradients(app, dark);

            foreach (Window w in app.Windows) ApplyTitleBar(w, dark);
            ThemeChanged?.Invoke();
        });
    }

    private bool _applied;

    private static void SetColor(Application app, string key, string hex)
    {
        try
        {
            if (app.Resources[key] is SolidColorBrush brush)
            {
                if (brush.IsFrozen) return;   // cannot repaint a frozen brush
                brush.Color = (Color)ColorConverter.ConvertFromString(hex);
            }
        }
        catch
        {
            // One unresolvable brush must not stop the rest of the theme.
        }
    }

    private static void ApplyGradients(Application app, bool dark)
    {
        try
        {
            if (app.Resources["G.HeroBg"] is LinearGradientBrush hero)
            {
                if (hero.IsFrozen) return;
                hero.GradientStops[0].Color =
                    (Color)ColorConverter.ConvertFromString(dark ? "#FF0E1726" : "#FFFFFFFF");
                hero.GradientStops[1].Color =
                    (Color)ColorConverter.ConvertFromString(dark ? "#FF060912" : "#FFF5F7FB");
            }
            if (app.Resources["G.Wave"] is LinearGradientBrush wave && !wave.IsFrozen)
            {
                wave.GradientStops[0].Color =
                    (Color)ColorConverter.ConvertFromString(dark ? "#FF55E1FF" : "#FF0B7C93");
                wave.GradientStops[1].Color =
                    (Color)ColorConverter.ConvertFromString(dark ? "#FF4DA3FF" : "#FF1F6FEB");
                wave.GradientStops[2].Color =
                    (Color)ColorConverter.ConvertFromString(dark ? "#FF8C7DFF" : "#FF6A55E0");
            }
        }
        catch
        {
            // ignore
        }
    }

    // -------------------------------------------------------------- title bar
    /// <summary>Match the native window chrome to the palette. The minimize /
    /// maximize / close buttons stay native - only their colour changes.</summary>
    public static void ApplyTitleBar(Window window, bool dark)
    {
        try
        {
            var hwnd = new WindowInteropHelper(window).Handle;
            if (hwnd == IntPtr.Zero) return;
            // 1 = immersive dark, 0 = light
            int value = dark ? 1 : 0;
            var hr = DwmSetWindowAttribute(hwnd, DwmwaUseImmersiveDarkMode,
                                           ref value, sizeof(int));
            if (hr < 0)
            {
                DwmSetWindowAttribute(hwnd, DwmwaUseImmersiveDarkModeLegacy,
                                      ref value, sizeof(int));
            }
        }
        catch
        {
            // Unsupported on this OS build - the client theme still applies.
        }
    }

    /// <summary>Listen for Windows theme changes (no polling). Fires on
    /// WM_SETTINGCHANGE, which is what Windows sends when the apps theme
    /// flips.</summary>
    public void Watch(Window window)
    {
        try
        {
            window.SourceInitialized += (_, _) =>
            {
                var src = PresentationSource.FromVisual(window) as HwndSource;
                src?.AddHook(WndProc);
                ApplyTitleBar(window, IsDark);
            };
            if (new WindowInteropHelper(window).Handle != IntPtr.Zero)
            {
                var src = PresentationSource.FromVisual(window) as HwndSource;
                src?.AddHook(WndProc);
                ApplyTitleBar(window, IsDark);
            }
        }
        catch
        {
            // ignore
        }
    }

    private const int WmSettingChange = 0x001A;

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam,
                           ref bool handled)
    {
        if (msg == WmSettingChange)
        {
            var prev = IsDark;
            Apply();
            handled = false;      // let WPF process it too
            if (prev != IsDark) ThemeChanged?.Invoke();
        }
        return IntPtr.Zero;
    }
}
