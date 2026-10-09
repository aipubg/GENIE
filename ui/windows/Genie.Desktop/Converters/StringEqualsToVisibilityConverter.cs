using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace Genie.Desktop.Converters;

/// <summary>Show a panel when a string equals the converter parameter.
/// Used by the Add Provider sheet to switch between its steps
/// (picker / credentials / models) without code-behind.</summary>
public sealed class StringEqualsToVisibilityConverter : IValueConverter
{
    public object Convert(object? value, System.Type targetType, object? parameter,
                          CultureInfo culture)
    {
        var actual = value as string ?? "";
        var expected = parameter as string ?? "";
        return string.Equals(actual, expected, System.StringComparison.OrdinalIgnoreCase)
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    public object ConvertBack(object? value, System.Type targetType, object? parameter,
                              CultureInfo culture)
        => value is Visibility v && v == Visibility.Visible
            ? (parameter as string ?? "")
            : "";
}