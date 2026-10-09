using System.Text.Json;

namespace Genie.Desktop.Services;

/// <summary>
/// Defensive JSON reading. The UI must render real backend state without
/// assuming a field exists or a type - a missing field degrades to blank, never
/// to a fabricated value.
/// </summary>
public static class JsonExt
{
    public static string? Str(this JsonElement e, string name)
    {
        if (e.ValueKind != JsonValueKind.Object) return null;
        if (!e.TryGetProperty(name, out var v)) return null;
        return v.ValueKind switch
        {
            JsonValueKind.String => v.GetString(),
            JsonValueKind.Number => v.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            _ => null,
        };
    }

    /// <summary>First non-empty value among the candidate field names.</summary>
    public static string First(this JsonElement e, params string[] names)
    {
        foreach (var n in names)
        {
            var v = e.Str(n);
            if (!string.IsNullOrWhiteSpace(v)) return v!;
        }
        return "";
    }

    public static IEnumerable<JsonElement> Array(this JsonElement e, string name)
    {
        if (e.ValueKind != JsonValueKind.Object) return Enumerable.Empty<JsonElement>();
        if (!e.TryGetProperty(name, out var v) || v.ValueKind != JsonValueKind.Array)
            return Enumerable.Empty<JsonElement>();
        return v.EnumerateArray();
    }

    public static bool Bool(this JsonElement e, string name, bool fallback = false)
    {
        if (e.ValueKind != JsonValueKind.Object) return fallback;
        if (!e.TryGetProperty(name, out var v)) return fallback;
        return v.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.String => bool.TryParse(v.GetString(), out var b) && b,
            _ => fallback,
        };
    }

    public static int Int(this JsonElement e, string name, int fallback = 0)
    {
        if (e.ValueKind != JsonValueKind.Object) return fallback;
        if (!e.TryGetProperty(name, out var v)) return fallback;
        return v.ValueKind switch
        {
            JsonValueKind.Number => v.TryGetInt32(out var n) ? n : fallback,
            JsonValueKind.String => int.TryParse(v.GetString(), out var n) ? n : fallback,
            _ => fallback,
        };
    }
}
