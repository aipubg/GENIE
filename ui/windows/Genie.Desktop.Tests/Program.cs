using System.IO;
using System.Net;
using System.Text;
using System.Text.Json;
using Genie.Desktop.Services;

namespace Genie.Desktop.Tests;

/// <summary>
/// A real, runnable test suite for the native client. Exit code 0 = all green.
///
/// It is deliberately dependency-free (no test runner package): it runs as a
/// console executable so "dotnet test is not installed" can never be a reason
/// the client is untested.
///
/// Every test that touches the network talks to a stub HTTP server started on a
/// free loopback port, so nothing here depends on a live backend or on machine
/// state.
/// </summary>
internal static class Program
{
    private static int _passed;
    private static readonly List<string> Failures = new();

    private static async Task<int> Main()
    {
        await RunAll();
        Console.WriteLine();
        Console.WriteLine($"passed {_passed}, failed {Failures.Count}");
        foreach (var f in Failures) Console.WriteLine($"  FAIL {f}");
        return Failures.Count == 0 ? 0 : 1;
    }

    private static async Task RunAll()
    {
        JsonExtTests();
        await ApiParsingTests();
        await SseStreamingTests();
        await ErrorStateTests();
        await PersistenceClientTests();
        await W6SurfaceTests();
        SingleInstanceTests();
        NavigationTests();
    }

    // ------------------------------------------------------------------ assert
    private static void Check(string name, bool ok, string? detail = null)
    {
        if (ok) { _passed++; return; }
        Failures.Add(detail is null ? name : $"{name}: {detail}");
    }

    private static void Eq(string name, object? actual, object? expected)
        => Check(name, Equals(actual, expected), $"expected {expected}, got {actual}");

    // -------------------------------------------------------------- stub server
    private sealed class Stub : IDisposable
    {
        private readonly HttpListener _l = new();
        public string BaseUrl { get; }
        public readonly List<(string Method, string Path, string Body)> Seen = new();
        private readonly Dictionary<string, Func<string, (int, string, string)>> _routes = new();

        public Stub()
        {
            var port = FreePort();
            BaseUrl = $"http://127.0.0.1:{port}";
            _l.Prefixes.Add(BaseUrl + "/");
            _l.Start();
            _ = Task.Run(Loop);
        }

        public void Route(string path, Func<string, (int, string, string)> handler)
            => _routes[path] = handler;

        public void Json(string path, string payload)
            => Route(path, _ => (200, payload, "application/json"));

        private static int FreePort()
        {
            var l = new System.Net.Sockets.TcpListener(IPAddress.Loopback, 0);
            l.Start();
            var p = ((IPEndPoint)l.LocalEndpoint).Port;
            l.Stop();
            return p;
        }

        private async Task Loop()
        {
            while (_l.IsListening)
            {
                HttpListenerContext ctx;
                try { ctx = await _l.GetContextAsync(); }
                catch { return; }

                var path = ctx.Request.Url!.AbsolutePath;
                var body = "";
                if (ctx.Request.HasEntityBody)
                {
                    using var sr = new StreamReader(ctx.Request.InputStream, Encoding.UTF8);
                    body = await sr.ReadToEndAsync();
                }
                lock (Seen) Seen.Add((ctx.Request.HttpMethod, path, body));

                var key = _routes.ContainsKey(path) ? path
                        : _routes.Keys.FirstOrDefault(k => path.StartsWith(k, StringComparison.Ordinal));
                (int code, string payload, string ctype) = key is null
                    ? (404, "{\"error\":\"not found\"}", "application/json")
                    : _routes[key](body);

                var bytes = Encoding.UTF8.GetBytes(payload);
                ctx.Response.StatusCode = code;
                ctx.Response.ContentType = ctype;
                ctx.Response.ContentLength64 = bytes.Length;
                await ctx.Response.OutputStream.WriteAsync(bytes);
                ctx.Response.Close();
            }
        }

        public void Dispose()
        {
            try { _l.Stop(); _l.Close(); } catch { }
        }
    }

    // ------------------------------------------------------------------- tests
    private static void JsonExtTests()
    {
        using var doc = JsonDocument.Parse("{\"a\":\"x\",\"n\":5,\"t\":true,\"f\":false,\"o\":{}}");
        var root = doc.RootElement;

        Eq("JsonExt.Str reads a string", root.Str("a"), "x");
        Eq("JsonExt.Str renders a number", root.Str("n"), "5");
        Eq("JsonExt.Str renders true", root.Str("t"), "true");
        Eq("JsonExt.Str renders false", root.Str("f"), "false");

        // The honesty rule: a missing field is null, never an invented value.
        Check("JsonExt.Str missing field is null", root.Str("missing") is null);
        Check("JsonExt.Str complex value is null", root.Str("o") is null);

        Eq("JsonExt.First picks the first present", root.First("missing", "a"), "x");
        Eq("JsonExt.First returns blank when none present", root.First("q", "z"), "");

        using var arr = JsonDocument.Parse("{\"items\":[1,2,3]}");
        Eq("JsonExt.Array counts elements", arr.RootElement.Array("items").Count(), 3);
        Eq("JsonExt.Array missing is empty", arr.RootElement.Array("nope").Count(), 0);
    }

    private static async Task ApiParsingTests()
    {
        using var stub = new Stub();
        stub.Json("/api/status", "{\"ready\":true,\"instance_id\":\"test\"}");
        stub.Json("/api/competition", """
            {"available":true,"reason":null,"max_candidates":4,
             "outcomes":["winner","needs_revision","cancelled","no_candidates"],
             "commit_policy":"winner_only","recent":[]}
            """);
        using var client = new BackendClient(stub.BaseUrl);

        using var status = await client.StatusAsync();
        Check("status parses", status is not null);
        Eq("status ready", status!.RootElement.Str("ready"), "true");
        Eq("status instance", status.RootElement.Str("instance_id"), "test");

        using var comp = await client.GetCompetitionAsync();
        Check("competition parses", comp is not null);
        Eq("competition commit policy is winner-only",
           comp!.RootElement.Str("commit_policy"), "winner_only");
        Eq("competition max candidates", comp.RootElement.Str("max_candidates"), "4");
        Eq("competition recent is empty", comp.RootElement.Array("recent").Count(), 0);
    }

    private static async Task SseStreamingTests()
    {
        using var stub = new Stub();
        stub.Route("/api/chat/stream", _ =>
            (200, "event: start\ndata: {\"kind\":\"start\"}\n\n" +
                  "data: {\"text\":\"Hel\",\"kind\":\"answer\"}\n\n" +
                  "data: {\"text\":\"lo\",\"kind\":\"answer\"}\n\n" +
                  "data: {\"deltas\":2,\"mode\":\"chat\",\"streamed\":true}\n\n",
             "text/event-stream"));
        using var client = new BackendClient(stub.BaseUrl);

        var deltas = new List<string>();
        await client.StreamChatAsync("hi", t => deltas.Add(t));

        // Two answer deltas only: start/done carry no answer text.
        Eq("SSE yields both answer deltas", deltas.Count, 2);
        Eq("SSE first delta", deltas.Count > 0 ? deltas[0] : "", "Hel");
        Eq("SSE second delta", deltas.Count > 1 ? deltas[1] : "", "lo");
    }

    private static async Task ErrorStateTests()
    {
        using var stub = new Stub();
        stub.Route("/api/boom", _ => (500, "{\"error\":\"kaboom\"}", "application/json"));
        using var client = new BackendClient(stub.BaseUrl);

        var doc = await client.GetJsonAsync("/api/boom");
        Check("a 500 yields null rather than throwing", doc is null);

        var missing = await client.GetJsonAsync("/api/does-not-exist");
        Check("a 404 yields null", missing is null);

        // Backend down entirely: health must be false, not an exception.
        using var dead = new BackendClient("http://127.0.0.1:1");
        Check("health is false when nothing is listening", !await dead.HealthAsync());
    }

    private static async Task PersistenceClientTests()
    {
        using var stub = new Stub();
        stub.Json("/api/models/roles", "{\"ok\":true}");
        using var client = new BackendClient(stub.BaseUrl);

        await client.SetModelRoleAsync("deep_work", "openai", "gpt-4");

        var post = stub.Seen.FirstOrDefault(s => s.Path == "/api/models/roles" && s.Method == "POST");
        Check("role change is POSTed", post.Path == "/api/models/roles", $"seen: {post.Path}");
        using var sent = JsonDocument.Parse(post.Body);
        Eq("role body carries role", sent.RootElement.Str("role"), "deep_work");
        Eq("role body carries provider", sent.RootElement.Str("provider_id"), "openai");
        Eq("role body carries model", sent.RootElement.Str("model_id"), "gpt-4");
    }

    private static async Task W6SurfaceTests()
    {
        using var stub = new Stub();
        stub.Json("/api/knowledge",
                  "{\"available\":false,\"reason\":\"no knowledge authority is registered\"," +
                  "\"documents\":[],\"sources\":[]}");
        stub.Json("/api/media",
                  "{\"available\":false,\"reason\":\"no media authority is registered\",\"items\":[]}");
        stub.Json("/api/experience",
                  "{\"available\":true,\"lessons\":[],\"task_types\":[],\"lesson_count\":0}");
        using var client = new BackendClient(stub.BaseUrl);

        using var k = await client.GetKnowledgeAsync();
        Check("knowledge reports no authority", k!.RootElement.Str("available") == "false");
        Check("knowledge gives a reason", !string.IsNullOrWhiteSpace(k.RootElement.Str("reason")));

        using var m = await client.GetMediaAsync();
        Check("media reports no authority", m!.RootElement.Str("available") == "false");

        using var e = await client.GetExperienceAsync();
        Check("experience reports available", e!.RootElement.Str("available") == "true");
        Eq("experience lesson count", e.RootElement.Str("lesson_count"), "0");

        // A competition run must be routed to the backend, not executed locally.
        stub.Json("/api/competition/run", "{\"ok\":false,\"outcome\":\"no_candidates\"}");
        await client.RunCompetitionAsync("m1", "t1", requested: 2);
        var run = stub.Seen.FirstOrDefault(s => s.Path == "/api/competition/run");
        Check("competition run is POSTed", run.Path == "/api/competition/run");
        using var rb = JsonDocument.Parse(run.Body);
        Eq("run body mission", rb.RootElement.Str("mission_id"), "m1");
        Eq("run body task", rb.RootElement.Str("task_id"), "t1");
    }

    private static void SingleInstanceTests()
    {
        // The single-instance mutex is a named kernel object, so a second
        // acquire inside this same process must fail exactly like a second
        // launched copy would.
        var first = new BackendLifecycle();
        Check("first instance acquires the mutex", first.TryAcquireSingleInstance());
        Check("first instance is marked primary", first.IsPrimaryInstance);

        // A second acquirer must be refused. Calling it again on the SAME object
        // would replace its own mutex handle, so a separate instance is used -
        // this is exactly what a second launched copy does.
        var second = new BackendLifecycle();
        Check("second instance is refused", !second.TryAcquireSingleInstance());
        Check("second instance is not primary", !second.IsPrimaryInstance);
        second.Dispose();

        // With the primary still holding the mutex, signalling must succeed;
        // that is how a second copy asks the running one to show itself.
        Check("an existing instance can be signalled", BackendLifecycle.SignalExistingInstance());

        // The installer depends on this: it asks a running GENIE to close
        // itself instead of force-killing it.
        Check("a running instance can be asked to shut down",
              BackendLifecycle.SignalShutdown());
        first.Dispose();

        // With nothing running, both signals must report "nobody listening"
        // rather than pretending the request was delivered.
        Check("activate with no instance reports false",
              !BackendLifecycle.SignalExistingInstance());
        Check("shutdown with no instance reports false",
              !BackendLifecycle.SignalShutdown());
    }

    private static void NavigationTests()
    {
        using var stub = new Stub();
        foreach (var p in new[] { "/api/skills", "/api/devices", "/api/memory",
                                  "/api/forecast/calibration", "/api/security/findings",
                                  "/api/competition", "/api/experience",
                                  "/api/knowledge", "/api/media" })
        {
            stub.Json(p, "{\"available\":true,\"items\":[]}");
        }
        stub.Json("/api/models/roles", "{\"roles\":{}}");
        stub.Json("/api/providers", "{\"providers\":[]}");
        stub.Json("/api/voice", "{\"available\":false}");

        using var client = new BackendClient(stub.BaseUrl);
        var shell = new Genie.Desktop.ViewModels.MainViewModel(client,
            new BackendLifecycle());

        var titles = shell.Items.Select(i => i.Title).ToList();
        foreach (var t in new[] { "Home", "Chat", "Missions", "Agents", "Computer",
                                  "Skills", "Devices", "Memory", "Forecast", "Security",
                                  "Competition", "Experience", "Knowledge", "Media",
                                  "Settings" })
        {
            Check($"nav contains {t}", titles.Contains(t), $"have: {string.Join(",", titles)}");
        }
        Check("no duplicate nav entries",
              titles.Count == titles.Distinct().Count());
        shell.Dispose();
    }
}
