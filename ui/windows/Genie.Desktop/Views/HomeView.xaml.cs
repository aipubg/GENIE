using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Genie.Desktop.ViewModels;

namespace Genie.Desktop.Views;

public partial class HomeView : UserControl
{
    private HomeViewModel? _vm;

    // Smoothed values so the rings ease toward the live amplitude instead of
    // snapping every frame (a snappy core reads as jitter, not "alive").
    private double _scale = 1.0;
    private double _glow = 0.15;

    public HomeView()
    {
        InitializeComponent();
        DataContextChanged += OnDataContextChanged;
        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
        IsVisibleChanged += (_, _) =>
        {
            if (IsVisible) _vm?.Resume();
            else _vm?.Pause();
        };
    }

    /// <summary>Bind the HomeState -> VisualStateManager bridge once the VM
    /// is available. Listening/Thinking/Speaking/Error each have their own
    /// Storyboard pulse; Idle is the calm default with no animation.</summary>
    private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (e.OldValue is HomeViewModel oldVm)
            oldVm.StateChanged -= OnStateChanged;
        if (e.NewValue is HomeViewModel newVm)
        {
            _vm = newVm;
            newVm.StateChanged += OnStateChanged;
            OnStateChanged(newVm.State);
        }
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        var win = Window.GetWindow(this);
        if (win is not null) win.StateChanged += OnWindowStateChanged;
        // Drive ring transforms per-frame from the real amplitude the backend
        // reports. CompositionTarget.Rendering is the WPF-correct hook for this.
        CompositionTarget.Rendering += OnRender;
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        CompositionTarget.Rendering -= OnRender;
        var win = Window.GetWindow(this);
        if (win is not null) win.StateChanged -= OnWindowStateChanged;
        _vm?.Pause();
    }

    /// <summary>When the window is minimised the core is not visible, so stop
    /// the amplitude polling and the 2s voice poll to save work. Resume when
    /// it comes back.</summary>
    private void OnWindowStateChanged(object? sender, EventArgs e)
    {
        var win = Window.GetWindow(this);
        if (win is null) return;
        if (win.WindowState == WindowState.Minimized) _vm?.Pause();
        else if (IsVisible) _vm?.Resume();
    }

    private void OnStateChanged(HomeState s)
    {
        var name = s switch
        {
            HomeState.Listening => nameof(HomeState.Listening),
            HomeState.Thinking  => nameof(HomeState.Thinking),
            HomeState.Speaking  => nameof(HomeState.Speaking),
            HomeState.Error     => nameof(HomeState.Error),
            _ => "Idle",
        };
        VisualStateManager.GoToElementState(this, name, true);
    }

    /// <summary>Per-frame ring animation. The active VisualState storyboard owns
    /// the base rhythm (breathing / rotation / opacity drift); this layer adds
    /// the REAL amplitude reactive response on top, on elements the storyboards
    /// do not touch (RingsHostScale for the whole ring cluster, RingGlow for the
    /// inner glow, and Ring4Rotate when not in the Thinking state).
    ///
    /// No fabricated values: the amplitude comes straight from the backend's
    /// live mic / playback meters.</summary>
    private void OnRender(object? sender, EventArgs e)
    {
        if (_vm is null) return;

        // Which real signal drives the core right now.
        var amp = _vm.State switch
        {
            HomeState.Listening => _vm.MicLevel,
            HomeState.Speaking  => _vm.TtsLevel,
            _ => 0.0,
        };

        // Perceptual curve + gain. Real speech sits around level 0.05–0.3, so a
        // linear map made the core move ~1–2 px — technically reactive, visibly
        // dead. The curve makes quiet speech clearly move the rings without
        // fabricating anything: it is still the real meter, just shaped.
        var level = Math.Min(1.0, Math.Max(0.0, amp));
        var shaped = Math.Pow(Math.Min(1.0, level * 7.0), 0.55);

        // Idle gets a slow, calm breathing so the core is alive but resting.
        var t = DateTime.Now.TimeOfDay.TotalSeconds;
        var breathe = _vm.State == HomeState.Idle ? 0.020 * Math.Sin(t * 0.8) : 0.0;

        // Visible floors. These are deliberately large so a still Listening
        // core is unmistakably different from Idle on a 1100x720 window.
        var floor = _vm.State switch
        {
            HomeState.Listening => 0.22,   // rings clearly expand
            HomeState.Speaking  => 0.18,
            HomeState.Thinking  => 0.14,
            _ => 0.0,
        };
        var target = 1.0 + breathe + floor + shaped * 0.75;
        _scale += (target - _scale) * 0.35;
        RingsHostScale.ScaleX = RingsHostScale.ScaleY = _scale;

        // Inner glow brightens with the same real amplitude. Idle stays dim;
        // Listening / Speaking jump to a clearly visible glow even when silent.
        var glowTarget = _vm.State switch
        {
            HomeState.Idle     => 0.10,
            HomeState.Listening => 0.75 + shaped * 0.8,
            HomeState.Speaking  => 0.75 + shaped * 0.8,
            HomeState.Thinking  => 0.60 + shaped * 0.7,
            _ => 0.10,
        };
        _glow += (glowTarget - _glow) * 0.35;
        RingGlow.Opacity = Math.Min(1.0, Math.Max(0.0, _glow));

        // Ring 4 spins faster the louder it is — but only when the Thinking
        // storyboard is not already driving its angle.
        if (_vm.State != HomeState.Thinking)
        {
            var step = 0.6 + shaped * 14.0;
            Ring4Rotate.Angle = (Ring4Rotate.Angle + step) % 360.0;
        }
    }
}
