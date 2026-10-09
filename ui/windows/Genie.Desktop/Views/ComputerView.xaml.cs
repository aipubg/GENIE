using System;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using Genie.Desktop.ViewModels;

namespace Genie.Desktop.Views;

public partial class ComputerView : UserControl
{
    private ComputerViewModel? _vm;

    public ComputerView()
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

    private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (_vm is not null) _vm.FrameReady -= OnFrameReady;
        if (e.NewValue is ComputerViewModel vm)
        {
            _vm = vm;
            vm.FrameReady += OnFrameReady;
        }
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        var win = Window.GetWindow(this);
        if (win is not null) win.StateChanged += OnWindowStateChanged;
    }

    private void OnUnloaded(object sender, RoutedEventArgs e)
    {
        var win = Window.GetWindow(this);
        if (win is not null) win.StateChanged -= OnWindowStateChanged;
        if (_vm is not null) _vm.FrameReady -= OnFrameReady;
        _vm?.Pause();
    }

    /// <summary>Preview refresh is monitoring, not video: stop it when the page
    /// is not visible or the window is minimised.</summary>
    private void OnWindowStateChanged(object? sender, EventArgs e)
    {
        var win = Window.GetWindow(this);
        if (win is null) return;
        if (win.WindowState == WindowState.Minimized) _vm?.Pause();
        else if (IsVisible) _vm?.Resume();
    }

    /// <summary>Decode a preview PNG into both the inline and expanded images.
    /// A null frame simply clears the preview. The preview is read-only: no
    /// owner input is ever forwarded to the browser.</summary>
    private void OnFrameReady(byte[]? frame)
    {
        if (frame is null || frame.Length == 0)
        {
            PreviewImage.Source = null;
            ExpandedImage.Source = null;
            return;
        }
        try
        {
            var bmp = new BitmapImage();
            bmp.BeginInit();
            bmp.CacheOption = BitmapCacheOption.OnLoad;
            bmp.StreamSource = new MemoryStream(frame);
            bmp.EndInit();
            bmp.Freeze();
            PreviewImage.Source = bmp;
            ExpandedImage.Source = bmp;
        }
        catch (Exception)
        {
            // A partial/corrupt frame must never break the page.
        }
    }
}
