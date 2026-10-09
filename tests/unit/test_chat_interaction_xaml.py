import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
XAML = "http://schemas.microsoft.com/winfx/2006/xaml"
PRESENTATION = "http://schemas.microsoft.com/winfx/2006/xaml/presentation"


def _read(relative):
    return ET.parse(ROOT / relative).getroot()


def test_chat_messages_keep_selection_and_explicit_dark_edit_menu():
    chat = _read("ui/windows/Genie.Desktop/Views/ChatView.xaml")
    messages = [node for node in chat.iter(f"{{{PRESENTATION}}}TextBox")
                if node.get("IsReadOnly") == "True"]
    assert len(messages) == 2
    for box in messages:
        assert box.get("ContextMenu") == "{StaticResource G.EditMenu}"
        assert box.get("IsInactiveSelectionHighlightEnabled") == "True"
        assert box.get("SelectionBrush") == "{StaticResource G.Primary}"

    controls = _read("ui/windows/Genie.Desktop/Themes/GenieControls.xaml")
    menu = next(node for node in controls.iter(f"{{{PRESENTATION}}}ContextMenu")
                if node.get(f"{{{XAML}}}Key") == "G.EditMenu")
    assert menu.get("Background") == "{StaticResource G.Surface}"
    assert menu.get("Foreground") == "{StaticResource G.Text}"
    assert {item.get("Header") for item in menu}
    assert {item.get("Header") for item in menu} >= {"Copy", "Paste", "Cut", "Select all"}


def test_mission_delete_is_available_only_for_settled_history_rows():
    missions = _read("ui/windows/Genie.Desktop/Views/MissionsView.xaml")
    buttons = [node for node in missions.iter(f"{{{PRESENTATION}}}Button")
               if node.get("ToolTip", "").startswith("Delete mission")]
    assert len(buttons) == 1
    assert buttons[0].get("IsEnabled") == "{Binding IsHistory}"
    assert "DeleteCommand" in buttons[0].get("Command", "")
