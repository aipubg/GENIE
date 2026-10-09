using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace Genie.Desktop.Converters;

/// <summary>Collapse a panel when a flag is off — used for Settings > Advanced.
/// Collapsed, not Hidden, so the layout does not reserve the space.</summary>
public sealed class BoolToVisibilityConverter : IValueConverter
{
    public object Convert(object? value, System.Type targetType, object? parameter,
                          CultureInfo culture)
        => value is bool b && b ? Visibility.Visible : Visibility.Collapsed;

    public object ConvertBack(object? value, System.Type targetType, object? parameter,
                              CultureInfo culture)
        => value is Visibility v && v == Visibility.Visible;
}
