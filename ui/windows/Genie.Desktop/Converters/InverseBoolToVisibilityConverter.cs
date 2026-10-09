using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace Genie.Desktop.Converters;

/// <summary>Show a control when a flag is FALSE — used for empty-state hints and
/// placeholders that should disappear once real content exists.</summary>
public sealed class InverseBoolToVisibilityConverter : IValueConverter
{
    public object Convert(object? value, System.Type targetType, object? parameter,
                          CultureInfo culture)
        => value is bool b && b ? Visibility.Collapsed : Visibility.Visible;

    public object ConvertBack(object? value, System.Type targetType, object? parameter,
                              CultureInfo culture)
        => value is Visibility v && v != Visibility.Visible;
}
