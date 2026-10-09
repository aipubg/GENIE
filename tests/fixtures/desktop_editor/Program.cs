using System;
using System.IO;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

public static class Program
{
    [STAThread]
    public static void Main()
    {
        var app = new Application();
        var window = new Window { Title = "GENIE Acceptance Editor", Width = 620, Height = 620 };
        var panel = new StackPanel { Margin = new Thickness(20) };
        var editor = new TextBox { Height = 170, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap };
        AutomationProperties.SetName(editor, "Acceptance text");
        var save = new Button { Content = "Save fixture", Width = 120, Margin = new Thickness(0, 12, 0, 12) };
        var status = new TextBlock { Text = "Not saved" };
        save.Click += (_, __) => {
            File.WriteAllText(Path.Combine(AppContext.BaseDirectory, "acceptance.txt"), editor.Text);
            status.Text = "Saved fixture: " + editor.Text;
        };
        panel.Children.Add(editor);
        panel.Children.Add(save);
        panel.Children.Add(status);
        var recipient = new TextBlock { Text = "Fixture recipient", Margin = new Thickness(0, 20, 0, 8) };
        AutomationProperties.SetAutomationId(recipient, "fixture-recipient");
        var composer = new TextBox { Height = 50, TextWrapping = TextWrapping.Wrap };
        AutomationProperties.SetName(composer, "Message draft");
        var send = new Button { Content = "Send", Width = 120, Margin = new Thickness(0, 8, 0, 8) };
        var messages = new StackPanel();
        send.Click += (_, __) => {
            if (string.IsNullOrEmpty(composer.Text)) return;
            var message = composer.Text;
            messages.Children.Add(new TextBlock { Text = message });
            composer.Clear();
            File.AppendAllText(Path.Combine(AppContext.BaseDirectory, "messages.txt"), recipient.Text + ": " + message + Environment.NewLine);
        };
        panel.Children.Add(recipient);
        panel.Children.Add(composer);
        panel.Children.Add(send);
        panel.Children.Add(messages);
        window.Content = panel;
        app.Run(window);
    }
}
