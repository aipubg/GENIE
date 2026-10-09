using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;

namespace Genie.Desktop.Converters;

/// <summary>Maps a friendly status word ("ok", "working", "failed", "warning",
/// "unknown") to the matching theme brush. Keeps the colour decision in the
/// visual layer rather than scattered across view models.</summary>
public sealed class StatusToBrushConverter : IValueConverter
{
    public object Convert(object? value, System.Type targetType, object? parameter,
                          CultureInfo culture)
    {
        var key = value switch
        {
            string s when s.Equals("ok", StringComparison.OrdinalIgnoreCase)        => "G.OK",
            string s when s.Equals("working", StringComparison.OrdinalIgnoreCase)  => "G.Cyan",
            string s when s.Equals("active", StringComparison.OrdinalIgnoreCase)  => "G.Cyan",
            string s when s.Equals("running", StringComparison.OrdinalIgnoreCase) => "G.Cyan",
            string s when s.Equals("failed", StringComparison.OrdinalIgnoreCase)  => "G.Error",
            string s when s.Equals("error", StringComparison.OrdinalIgnoreCase)   => "G.Error",
            string s when s.Equals("cancelled", StringComparison.OrdinalIgnoreCase)=> "G.Warn",
            string s when s.Equals("paused", StringComparison.OrdinalIgnoreCase)   => "G.Warn",
            string s when s.Equals("warning", StringComparison.OrdinalIgnoreCase)  => "G.Warn",
            string s when s.Equals("needs_revision", StringComparison.OrdinalIgnoreCase) => "G.Warn",
            string s when s.Equals("unknown", StringComparison.OrdinalIgnoreCase)  => "G.TextDim",
            string s when s.Equals("unconfigured", StringComparison.OrdinalIgnoreCase) => "G.TextDim",
            string s when s.Equals("available", StringComparison.OrdinalIgnoreCase) => "G.OK",
            string s when s.Equals("ready", StringComparison.OrdinalIgnoreCase)     => "G.OK",
            string s when s.Equals("in_use", StringComparison.OrdinalIgnoreCase)     => "G.Cyan",
            string s when s.Equals("rate_limited", StringComparison.OrdinalIgnoreCase)=> "G.Warn",
            string s when s.Equals("quota_exhausted", StringComparison.OrdinalIgnoreCase)=> "G.Error",
            string s when s.Equals("auth_required", StringComparison.OrdinalIgnoreCase)=> "G.Warn",
            string s when s.Equals("temporarily_unavailable", StringComparison.OrdinalIgnoreCase)=> "G.Warn",
            string s when s.Equals("connection_failed", StringComparison.OrdinalIgnoreCase)=> "G.Error",
            _ => "G.Text"
        };
        return Application.Current.TryFindResource(key) ?? Brushes.White;
    }

    public object ConvertBack(object? value, System.Type targetType, object? parameter,
                              CultureInfo culture) => throw new NotSupportedException();
}