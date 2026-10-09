using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace Genie.Desktop.Converters;

/// <summary>Show a control only when a string has content. Used so optional
/// secondary lines (role usage, status text) take no vertical space when the
/// backend reported nothing, keeping the provider list dense.</summary>
public sealed class StringToVisibilityConverter : IValueConverter
{
    public object Convert(object? value, System.Type targetType, object? parameter,
                          CultureInfo culture)
        => string.IsNullOrWhiteSpace(value as string)
            ? Visibility.Collapsed
            : Visibility.Visible;

    public object ConvertBack(object? value, System.Type targetType, object? parameter,
                              CultureInfo culture)
        => throw new System.NotSupportedException();
}
