using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Genie.Desktop.ViewModels;

namespace Genie.Desktop.Views;

public partial class KnowledgeView : UserControl
{
    public KnowledgeView() => InitializeComponent();

    /// <summary>Enter in the search box runs the search; Shift+Enter is a
    /// newline (but the box is single-line so this is belt-and-braces).</summary>
    private void Search_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Enter) return;
        if (Keyboard.Modifiers.HasFlag(ModifierKeys.Shift)) return;
        if (DataContext is KnowledgeViewModel vm && vm.SearchCommand.CanExecute(null))
        {
            vm.SearchCommand.Execute(null);
            e.Handled = true;
        }
    }
}