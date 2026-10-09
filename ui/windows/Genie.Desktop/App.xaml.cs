using System.IO;
using System.Windows;
using System.Windows.Threading;
using Genie.Desktop.Services;

namespace Genie.Desktop;

public partial class App : Application
{
    private BackendLifecycle? _lifecycle;
    private readonly ThemeService _theme = new();

    /// <summary>Appearance service, exposed so Settings can offer
    /// System / Light / Dark without reaching into internals.</summary>
    public ThemeService Theme => _theme;

    /// <summary>
    /// A GUI app that dies silently is undiagnosable: the owner sees a window
    /// that never appears and there is nothing to investigate. Every unhandled
    /// exception is written to a log before the process goes down.
    /// </summary>
    private static void LogCrash(string kind, Exception ex)
    {
        try
        {
            var dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "GENIE", "logs");
            Directory.CreateDirectory(dir);
            File.AppendAllText(Path.Combine(dir, "desktop-crash.log"),
                $"{DateTime.Now:O} [{kind}] {ex}\n\n");
        }
        catch
        {
            // Nothing can be done if the crash log itself cannot be written.
        }
    }

    protected override async void OnStartup(StartupEventArgs e)
    {
        AppDomain.CurrentDomain.UnhandledException += (_, a) =>
            LogCrash("AppDomain", a.ExceptionObject as Exception ?? new Exception("unknown"));
        DispatcherUnhandledException += (_, a) => LogCrash("Dispatcher", a.Exception);
        TaskScheduler.UnobservedTaskException += (_, a) => LogCrash("Task", a.Exception);
        base.OnStartup(e);

        // Diagnostics: the exact executable the owner is looking at. This is the
        // one value that settles "which build is running?" — recorded before
        // anything else so an Advanced panel can simply display it.
        try
        {
            var exe = Environment.ProcessPath;
            if (string.IsNullOrEmpty(exe))
                exe = System.Reflection.Assembly.GetEntryAssembly()?.Location ?? "";
            Genie.Desktop.ViewModels.UiDiagnostics.ExePath =
                string.IsNullOrEmpty(exe) ? "(unknown)" : exe;
        }
        catch { /* diagnostics must never break startup */ }

        _lifecycle = new BackendLifecycle();

        // Build-identity takeover. The frozen rc26 install and the Preview build
        // share the single-instance mutex, so launching Preview while the stale
        // install was running used to hand activation to the OLD window — the
        // owner believed Preview had opened when it had not. If a *different*
        // Genie.Desktop binary is already running, ask it to close and wait, so
        // the binary the owner launched is the binary that ends up on screen.
        // Only GENIE's own frontend process is touched: no python, no browser.
        CloseOtherGenieFrontends();

        // --genie-shutdown: the installer/uninstaller asks a running GENIE to
        // close itself instead of being force-killed. Killing it leaves the
        // backend alive and files locked, which is what produced
        // "Failed to uninstall old application files".
        if (Array.Exists(e.Args, a => string.Equals(a, "--genie-shutdown",
                                                    StringComparison.OrdinalIgnoreCase)))
        {
            if (!_lifecycle.TryAcquireSingleInstance())
            {
                // Another instance owns the mutex. If it is already listening,
                // deliver the request; if it is mid-startup and has not created
                // the event yet, park the request so it is honoured the moment
                // it starts listening instead of being dropped.
                if (!BackendLifecycle.SignalShutdown())
                {
                    BackendLifecycle.RequestShutdown();
                }
            }
            // If WE acquired the mutex there is nothing to close. Critically,
            // we must NOT park a request here: that would sit armed and shut
            // down the owner's next perfectly normal launch.
            // Nothing was running, or the request was delivered: either way
            // this process has nothing to do.
            Shutdown();
            return;
        }

        if (!_lifecycle.TryAcquireSingleInstance())
        {
            // Another GENIE is already running: ask it to show itself and leave.
            BackendLifecycle.SignalExistingInstance();
            Shutdown();
            return;
        }

        // Assigned BEFORE the backend starts and before any window exists.
        // Binding it later (after the window) leaves a real gap: a shutdown
        // requested during startup has no handler to call, so the request is
        // dropped and the frontend stays up - the installer then proceeds over
        // a live install.
        _lifecycle.ShutdownRequested = () => Dispatcher.Invoke(() =>
        {
            // Canonical full exit even from mid-startup: stop what we own.
            _lifecycle.StopBackend();
            Shutdown();
        });

        // Crash diagnostics. A second Chat turn was terminating the whole
        // frontend with no visible error, which is undiagnosable from a
        // screenshot. Every unhandled exception is now written to disk with its
        // full stack before anything else happens.
        InstallCrashLogging();

        var healthy = await _lifecycle.EnsureBackendAsync(TimeSpan.FromSeconds(30));

        // A shutdown asked for while we were still starting must not be lost:
        // the listener may have fired before the backend existed, and going on
        // to show a window would leave GENIE running when the caller believes
        // it closed.
        if (_lifecycle.ShutdownWasRequested)
        {
            _lifecycle.StopBackend();
            Shutdown();
            return;
        }

        // Appearance follows Windows by default (System), with an owner
        // override of Light/Dark when set. The palette dictionary is swapped
        // BEFORE MainWindow is constructed: views use StaticResource, which is
        // fixed at parse time, so the theme must already be in place (and this
        // avoids a dark->light flash).
        _theme.SetMode(LoadThemePreference());
        _theme.ApplyStartupResources();

        var window = new MainWindow();
        // Native chrome + live change notifications (WM_SETTINGCHANGE).
        _theme.Watch(window);
        window.Attach(_lifecycle, healthy);
        _lifecycle.ActivateRequested = () => window.Dispatcher.Invoke(() =>
        {
            if (!window.IsVisible) window.Show();
            if (window.WindowState == WindowState.Minimized) window.WindowState = WindowState.Normal;
            window.Activate();
            window.Focus();
        });
        _lifecycle.ShutdownRequested = () => window.Dispatcher.Invoke(() =>
        {
            // Canonical full exit: OnExit stops the backend we own.
            Shutdown();
        });
        window.Show();

        // We are up and healthy: any parked request from an earlier session is
        // stale by definition and must not linger for the next launch.
        _lifecycle.ClearPendingShutdown();
        if (healthy)
            await Views.GeminiSetupWindow.ShowIfNeededAsync(window);
    }

    /// <summary>
    /// Asks any other running Genie.Desktop frontend to close, then waits for it
    /// to actually go. GENIE-owned frontend processes only.
    ///
    /// Without this, Preview launches could be swallowed by a stale installed
    /// instance holding the single-instance mutex: the old window would activate
    /// and the owner would test the wrong build while believing otherwise.
    /// </summary>
    private static void CloseOtherGenieFrontends()
    {
        try
        {
            var me = Genie.Desktop.ViewModels.UiDiagnostics.ExePath;
            var mine = System.Diagnostics.Process.GetCurrentProcess();
            var others = System.Diagnostics.Process.GetProcessesByName("Genie.Desktop")
                .Where(p => p.Id != mine.Id)
                .ToList();
            if (others.Count == 0) return;

            // A different path means a different build — the dangerous case.
            // The same path means a duplicate launch, which the mutex handles.
            foreach (var p in others)
            {
                try
                {
                    var path = p.MainModule?.FileName ?? "";
                    if (!string.IsNullOrEmpty(me) &&
                        string.Equals(path, me, StringComparison.OrdinalIgnoreCase))
                        continue;
                    BackendLifecycle.SignalShutdown();
                }
                catch { /* process gone or access denied — nothing to do */ }
                break;
            }

            // Wait for the old instance to release the mutex and exit.
            for (var i = 0; i < 40; i++)
            {
                var alive = System.Diagnostics.Process.GetProcessesByName("Genie.Desktop")
                    .Any(p => p.Id != mine.Id);
                if (!alive) break;
                System.Threading.Thread.Sleep(250);
            }
        }
        catch
        {
            // Never let build-identity housekeeping prevent the app from opening.
        }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        // Full exit: stop the daemon we own, then release the locks.
        _lifecycle?.StopBackend();
        _lifecycle?.Dispose();
        base.OnExit(e);
    }

    /// <summary>Crash hooks are installed in OnStartup. Dispatcher exceptions
    /// are logged AND handled so one bad turn cannot end the owner's whole
    /// session; everything else is recorded on the way out.</summary>
    /// <summary>Owner appearance preference. Missing/invalid falls back to
    /// System, which is the required default.</summary>
    private static ThemeMode LoadThemePreference()
    {
        try
        {
            var path = System.IO.Path.Combine(
                System.Environment.GetFolderPath(
                    System.Environment.SpecialFolder.LocalApplicationData),
                "GENIE", "appearance.txt");
            if (!System.IO.File.Exists(path)) return ThemeMode.System;
            return (System.IO.File.ReadAllText(path).Trim().ToLowerInvariant()) switch
            {
                "light" => ThemeMode.Light,
                "dark" => ThemeMode.Dark,
                _ => ThemeMode.System,
            };
        }
        catch
        {
            return ThemeMode.System;
        }
    }

    private void InstallCrashLogging()
    {
        DispatcherUnhandledException += (_, a) =>
        {
            LogCrash("Dispatcher", a.Exception);

            // Policy (NOT a blanket swallow):
            //   known non-fatal presentation failure -> recover locally
            //   anything else                        -> tell the owner, then let
            //                                           the process end honestly
            if (IsRecoverableUiException(a.Exception))
            {
                a.Handled = true;      // visual glitch only; state is intact
                return;
            }

            // Unexpected: never pretend nothing happened. Say so plainly and
            // point at the full stack on disk, then let the process exit.
            try
            {
                System.Windows.MessageBox.Show(
                    "GENIE hit an unexpected error and must close.\n\n"
                  + $"{a.Exception.GetType().Name}: {a.Exception.Message}\n\n"
                  + $"Full details: {CrashLogPath}",
                    "GENIE — unexpected error",
                    System.Windows.MessageBoxButton.OK,
                    System.Windows.MessageBoxImage.Error);
            }
            catch
            {
                // If even the dialog fails, the crash log is already written.
            }
            // Deliberately NOT setting Handled: silently continuing could leave
            // the app in a corrupted state that is far harder to diagnose.
        };
        TaskScheduler.UnobservedTaskException += (_, a) =>
        {
            LogCrash("Task", a.Exception);
            a.SetObserved();
        };
    }

    /// <summary>Path of the crash log, so the owner can find the evidence.</summary>
    private static string CrashLogPath => System.IO.Path.Combine(
        System.Environment.GetFolderPath(
            System.Environment.SpecialFolder.LocalApplicationData),
        "GENIE", "logs", "desktop-crash.log");

    /// <summary>
    /// Presentation-only failures whose recovery cannot corrupt GENIE state.
    ///
    /// The second-turn crash was exactly this class: ScrollIntoView fired from
    /// inside a CollectionChanged notification throws "An ItemsControl is
    /// inconsistent with its items source" while the control is mid-reconcile.
    /// The conversation data is untouched, so recovering is correct. Anything
    /// outside this narrow set is treated as real and reported.
    /// </summary>
    private static bool IsRecoverableUiException(Exception? ex)
    {
        while (ex is not null)
        {
            if (ex is InvalidOperationException)
            {
                var msg = ex.Message ?? "";
                if (msg.Contains("ItemsControl is inconsistent with its items source",
                                 System.StringComparison.OrdinalIgnoreCase) ||
                    msg.Contains("Collection was modified",
                                 System.StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            // A stale layout pass on a disposed/removed visual.
            if (ex is System.Windows.Data.ValueUnavailableException) return true;
            ex = ex.InnerException;
        }
        return false;
    }

}
