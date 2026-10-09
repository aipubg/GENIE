using System.Globalization;
using System.Windows.Data;

namespace Genie.Desktop.Converters;

/// <summary>Invert a boolean. Used to disable the Base URL field for built-in
/// providers, whose shipped endpoint is not owner-editable.</summary>
public sealed class InverseBoolConverter : IValueConverter
{
    public object Convert(object? value, System.Type targetType, object? parameter,
                          CultureInfo culture)
        => value is bool b ? !b : true;

    public object ConvertBack(object? value, System.Type targetType, object? parameter,
                              CultureInfo culture)
        => value is bool b ? !b : true;
}
