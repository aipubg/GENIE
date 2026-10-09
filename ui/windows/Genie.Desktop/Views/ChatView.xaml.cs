using System.Collections.Specialized;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Genie.Desktop.ViewModels;

namespace Genie.Desktop.Views;

public partial class ChatView : UserControl
{
    private ChatViewModel? _vm;

    public ChatView()
    {
        InitializeComponent();
        DataContextChanged += OnDataContextChanged;
        Loaded += async (_, _) =>
        {
            Detach();
            if (DataContext is ChatViewModel vm)
            {
                _vm = vm;
                ((INotifyCollectionChanged)vm.Messages).CollectionChanged += OnMessagesChanged;
                await vm.LoadHistoryAsync();
                if (MessagesList.Items.Count > 0)
                    MessagesList.ScrollIntoView(MessagesList.Items[^1]);
            }
        };
        Unloaded += (_, _) => Detach();
    }

    private void OnDataContextChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        Detach();
        if (e.NewValue is ChatViewModel vm)
        {
            _vm = vm;
            ((INotifyCollectionChanged)vm.Messages).CollectionChanged += OnMessagesChanged;
            if (IsLoaded)
                _ = vm.LoadHistoryAsync();
        }
    }

    private void Detach()
    {
        if (_vm is not null)
            ((INotifyCollectionChanged)_vm.Messages).CollectionChanged -= OnMessagesChanged;
        _vm = null;
    }

    /// <summary>Keep the newest turn in view as the conversation grows or a
    /// streaming reply is replaced with more text.</summary>
    private void OnMessagesChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        if (MessagesList.Items.Count == 0 || _vm?.LoadingHistory == true) return;

        // Deferred on purpose. Touching the ItemsControl from INSIDE the
        // CollectionChanged callback queries it while it is still reconciling
        // with its items source, which throws
        //   InvalidOperationException: An ItemsControl is inconsistent with its
        //   items source.
        // A turn replaces its message row (Messages[index] = ...), so the very
        // next turn hit this and terminated the whole frontend. Scrolling is
        // cosmetic - it must never be able to kill the app.
        MessagesList.Dispatcher.BeginInvoke(
            new Action(() =>
            {
                try
                {
                    if (MessagesList.Items.Count == 0) return;
                    MessagesList.ScrollIntoView(MessagesList.Items[^1]);
                }
                catch
                {
                    // Scrolling is best-effort only.
                }
            }),
            System.Windows.Threading.DispatcherPriority.Background);
    }

    /// <summary>Enter sends; Shift+Enter inserts a newline.
    ///
    /// PreviewKeyDown (tunnelling) is used deliberately: a bubbling KeyDown
    /// handler fires AFTER the TextBox has already inserted the newline for an
    /// Enter keystroke, so the newline appeared even when we set Handled. Here
    /// we consume Enter before the TextBox can act on it.
    ///
    /// Enter with an empty composer, or while a turn is in flight, does nothing
    /// (and never inserts a newline). Shift+Enter is left alone for a newline.</summary>
    private void Composer_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Enter) return;

        // Shift+Enter -> let the TextBox add the line break.
        if ((Keyboard.Modifiers & ModifierKeys.Shift) != 0) return;

        // Enter is always consumed so no stray newline is inserted.
        e.Handled = true;

        if (DataContext is not ChatViewModel vm) return;
        if (!vm.CanSend) return;                 // empty / whitespace / streaming
        vm.SendCommand.Execute(null);
    }
}
