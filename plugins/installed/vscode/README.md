# vscode plugin

Opens projects and files through VS Code's official `code` CLI.

| capability | method |
|---|---|
| `open_project` | `code --reuse-window <folder>` |
| `open_file` | `code -g <file>[:line]` |
| `focus` | `code --reuse-window` |
| `inspect_workspace` | reads the folder (git, `.vscode/tasks.json`, languages) |
| `health` | reports whether the CLI exists |

Permissions: `application.vscode.control`, `filesystem.workspace.read`.

If the CLI is absent every capability returns UNAVAILABLE — GENIE then falls back to generic
automation (OS → UIA → input), so a missing plugin never blocks the user.
