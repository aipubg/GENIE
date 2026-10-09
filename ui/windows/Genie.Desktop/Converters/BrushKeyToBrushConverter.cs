using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;

namespace Genie.Desktop.Converters;

/// <summary>
/// Resolve a palette resource key (e.g. "G.OK") to the brush it names.
///
/// View models cannot hand a Brush to the view without referencing WPF, so a
/// row carries a tone KEY and the view resolves it here. An unknown key falls
/// back to the standard body colour rather than throwing or rendering black.
/// </summary>
public sealed class BrushKeyToBrushConverter : IValueConverter
{
    private const string FallbackKey = "G.TextMid";

    public object Convert(object? value, System.Type targetType, object? parameter,
                          CultureInfo culture)
    {
        var key = value as string;
        if (string.IsNullOrWhiteSpace(key)) key = FallbackKey;

        if (Application.Current?.TryFindResource(key) is Brush brush)
            return brush;

        if (Application.Current?.TryFindResource(FallbackKey) is Brush fallback)
            return fallback;

        return Brushes.Gray;
    }

    public object ConvertBack(object? value, System.Type targetType, object? parameter,
                              CultureInfo culture)
        => throw new System.NotSupportedException();
}
