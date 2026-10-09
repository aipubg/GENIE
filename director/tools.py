"""GENIE tool catalogue for NEDLE2 (director/tools).

SINGLE SOURCE OF TRUTH for what the local director may route to. Nothing else in the
codebase declares Needle tools (owner requirement: no scattered Needle-specific calls).

Tools are plain Python functions decorated with `@needle.tool` — that is the official
Cactus Needle 2 contract (typed args + Field constraints + docstring descriptions).

Important: a tool call is only a ROUTING DECISION. It never means the action happened —
services own truth (master spec §2.2). The callables below return routing echoes; real
execution happens in the capability layer behind PTE + verification + audit.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional

try:  # the runtime may not be provisioned yet — catalogue must still be importable
    import needle
    from needle import Field
    _HAS_NEEDLE = True
except Exception:  # pragma: no cover
    needle = None

    class Field:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    _HAS_NEEDLE = False


def _tool(fn):
    """Apply @needle.tool when available, else return the function unchanged."""
    if _HAS_NEEDLE and hasattr(needle, "tool"):
        return needle.tool(fn)
    fn._needle_tool = {  # minimal fallback schema so the catalogue is still introspectable
        "name": fn.__name__,
        "description": (fn.__doc__ or "").strip(),
    }
    return fn


KNOWN_APPS = ("chrome", "notepad", "vscode", "blender", "spotify", "explorer",
              "terminal", "calculator", "steam", "discord", "vlc")
# The router's device *vocabulary*. The device registry is the truth about which devices
# actually exist; this set only tells the router which names it may emit. Paired devices are
# added at boot through register_device_ids() so the vocabulary grows with the mesh.
KNOWN_DEVICES = {"pc_main", "phone_main", "laptop_main", "pc_secondary", "rpi_main"}


def register_device_ids(device_ids) -> None:
    """Extend the router vocabulary with devices that are actually paired."""
    for device_id in device_ids or []:
        name = str(device_id or "").strip().lower().replace("-", "_")
        if name:
            KNOWN_DEVICES.add(name)


# --------------------------------------------------------------------------- tools
@_tool
def open_application(
    target: Annotated[str, Field(description="Application name, lowercase, e.g. chrome", min_length=1, max_length=40)],
):
    """Open a desktop application on the main PC.

    Args:
        target: The application to open, e.g. chrome, notepad, blender, spotify.
    """
    return {"ok": True, "target": target}


@_tool
def close_application(
    target: Annotated[str, Field(description="Application name, lowercase", min_length=1, max_length=40)],
):
    """Close an already running desktop application on the main PC.

    Args:
        target: The application to close, e.g. chrome, blender.
    """
    return {"ok": True, "target": target}


@_tool
def set_system_volume(
    level: Annotated[int, Field(ge=0, le=100, description="Volume level 0-100")],
):
    """Set the PC master output volume to an absolute level.

    Args:
        level: Target volume from 0 to 100.
    """
    return {"ok": True, "level": level}


@_tool
def system_volume_up():
    """Increase the PC master volume by one step. Takes no arguments."""
    return {"ok": True, "direction": "up"}


@_tool
def system_volume_down():
    """Decrease the PC master volume by one step. Takes no arguments."""
    return {"ok": True, "direction": "down"}


@_tool
def mute_audio(
    muted: Annotated[bool, Field(description="True to mute, False to unmute")] = True,
):
    """Mute or unmute the PC master audio. Use for "mute", "silent", "unmute".

    Args:
        muted: True mutes the audio, False unmutes it.
    """
    return {"ok": True, "muted": muted}


_DEVICE_DESC = ("The device exactly as the user said it, e.g. phone, pc, laptop, "
                "second pc, raspberry pi. Copy the user's own word.")


@_tool
def media_next(
    device: Annotated[str, Field(description=_DEVICE_DESC, min_length=1, max_length=32)] = "pc",
):
    """Skip to the next track on a device that is currently playing media.

    Args:
        device: The device the user named, e.g. phone or pc.
    """
    return {"ok": True, "device": device}


@_tool
def media_previous(
    device: Annotated[str, Field(description=_DEVICE_DESC, min_length=1, max_length=32)] = "pc",
):
    """Go back to the previous track on a device that is playing media.

    Args:
        device: The device the user named, e.g. phone or pc.
    """
    return {"ok": True, "device": device}


@_tool
def media_pause(
    device: Annotated[str, Field(description=_DEVICE_DESC, min_length=1, max_length=32)] = "pc",
):
    """Pause media that is currently playing on a device.

    Args:
        device: The device the user named, e.g. phone or pc.
    """
    return {"ok": True, "device": device}


@_tool
def media_resume(
    device: Annotated[str, Field(description=_DEVICE_DESC, min_length=1, max_length=32)] = "pc",
):
    """Un-pause media that is paused on a device. Only for music, video or audio playback.
    Never use this for work, projects, tasks, files or applications.

    Args:
        device: The device the user named, e.g. phone or pc.
    """
    return {"ok": True, "device": device}


@_tool
def memory_lookup(
    text: Annotated[str, Field(description="What to look up in GENIE memory", min_length=2, max_length=200)],
):
    """Search GENIE's memory, past missions, projects and stored preferences. Use this for
    any request about earlier work, previous sessions, an existing project, saved habits or
    things the user asked GENIE to remember. This is the correct choice whenever the request
    is not a direct media, volume or application action.

    Args:
        text: The thing to recall, e.g. "latest project" or "Blender export method".
    """
    return {"ok": True, "text": text}


@_tool
def start_mission(
    goal: Annotated[str, Field(description="The task that needs planning or a larger model", min_length=2, max_length=200)],
):
    """Hand a complex or multi-step request to the mission engine and a remote model.
    Use this when no single declared tool can satisfy the request.

    Args:
        goal: The complex goal, copied from the request.
    """
    return {"ok": True, "goal": goal}


@_tool
def read_processes():
    """List the running processes on the main PC. Takes no arguments."""
    return {"ok": True}


@_tool
def run_shell(
    command: Annotated[str, Field(description="Shell command to run on the main PC", min_length=1, max_length=300)],
):
    """Run a shell command on the main PC. Only use when the user explicitly asks for a
    command to be executed.

    Args:
        command: The exact command to run.
    """
    return {"ok": True, "command": command}


@_tool
def skill_execute(
    goal: Annotated[str, Field(description="What the user wants accomplished, in their own words",
                               min_length=2, max_length=300)],
):
    """Run a learned multi-step procedure (a Skill) that GENIE already knows.

    Use ONLY when the user asks for a remembered, saved or taught workflow, or for a
    multi-step procedure GENIE has learned before. Do NOT use for simple single actions
    (open an app, set volume, play music) - those have their own tools.

    The Skill Registry chooses the actual procedure and each step still passes permission
    checks and verification. If no compatible learned skill exists this simply does nothing.

    Args:
        goal: What the user wants accomplished, e.g. "create a dated note".
    """
    return {"ok": True, "goal": goal}


# ------------------------------------------------------------------------ catalogue
TOOLS: List[Any] = [
    skill_execute,
    open_application,
    close_application,
    set_system_volume,
    system_volume_up,
    system_volume_down,
    mute_audio,
    media_next,
    media_previous,
    media_pause,
    media_resume,
    memory_lookup,
    start_mission,
    read_processes,
    run_shell,
]

# Keep this SHORT and focused — the model is a 14MB router and long prompts measurably
# degrade routing (verified: a 6-clause prompt dropped the smoke pass rate from 11/12 to
# 9/12). Disambiguation belongs in the tool descriptions, not here.
SYSTEM = (
    "Map each explicit supported action to exactly one declared call; never duplicate an action. "
    "Copy user values word for word. Do not guess missing values. "
    "Media tools are only for music, video or audio that is playing. "
    "Unsupported, invalid, ambiguous and negated requests return no call."
)

# Needle tool name -> GENIE routing target (single mapping table, no scattered logic)
TOOL_TO_TASK: Dict[str, Dict[str, Any]] = {
    "skill_execute":      {"type": "computer_action",    "capability": "skill.execute", "arg": "goal"},
    "open_application":    {"type": "application_action", "capability": "application.open", "arg": "target"},
    "close_application":   {"type": "application_action", "capability": "application.close", "arg": "target"},
    "set_system_volume":   {"type": "computer_action",    "capability": "system.volume.set", "arg": "level"},
    "system_volume_up":    {"type": "computer_action",    "capability": "system.volume.up"},
    "system_volume_down":  {"type": "computer_action",    "capability": "system.volume.down"},
    "media_next":          {"type": "device_action",      "capability": "media.next", "device_arg": "device"},
    "media_previous":      {"type": "device_action",      "capability": "media.previous", "device_arg": "device"},
    "media_pause":         {"type": "device_action",      "capability": "media.pause", "device_arg": "device"},
    "media_resume":        {"type": "device_action",      "capability": "media.play", "device_arg": "device"},
    "read_processes":      {"type": "computer_action",    "capability": "processes.list"},
    "run_shell":           {"type": "computer_action",    "capability": "shell.run", "arg": "command"},
    "mute_audio":          {"type": "computer_action",    "capability": "system.volume.mute",
                            "param_map": {"muted": "muted"}},
}

MEMORY_TOOLS = {"memory_lookup": "memory_query"}
MISSION_TOOLS = {"start_mission": "goal"}

# device words the model may emit -> canonical device ids
DEVICE_WORDS = {
    "phone": "phone_main", "mobile": "phone_main", "android": "phone_main",
    "pc": "pc_main", "computer": "pc_main", "desktop": "pc_main", "laptop": "laptop_main",
    "second pc": "pc_secondary", "raspberry": "rpi_main", "pi": "rpi_main",
}


def infer_device(text: str, fallback: str = "pc_main") -> str:
    """Resolve a device from the user's own words (owner requirement: target inferred
    from context). Used when the model omits the device argument."""
    if not text:
        return fallback
    low = text.lower()
    for word, device in sorted(DEVICE_WORDS.items(), key=lambda kv: -len(kv[0])):
        if word in low:
            return device
    return fallback


def canonical_device(value: str | None, fallback: str = "pc_main") -> str:
    if not value:
        return fallback
    v = value.strip().lower().replace("-", "_").replace(" ", "_")
    if v in KNOWN_DEVICES:
        return v
    for word, device in DEVICE_WORDS.items():
        if word in value.lower():
            return device
    return fallback


def tool_names() -> List[str]:
    return [t["name"] if isinstance(t, dict) else getattr(t, "__name__", "?") for t in TOOLS]
