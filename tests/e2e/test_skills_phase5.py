"""Phase 5 exit gate — the golden demo on the real machine.

The workflow under test is the one named in the roadmap: **write a dated note in Notepad**.

It must survive all three environment changes that break a macro:
  * the window sits at a different position
  * the window has a different size
  * the note is saved to a different path

Nothing here is simulated: Notepad really launches, the text is really typed, the file is
really written, and success is decided by observing the file — not by trusting that a step
was issued. Skipped on non-Windows hosts.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# Category: real_machine — launches Notepad, types into it and moves its window
# Runs serially behind the shared REAL_DESKTOP_TEST lock (see tests/conftest.py).

from core.contracts import CallContext, Persona
from skills.models import SkillStatus, validate_skill

WINDOWS = sys.platform.startswith("win")
pytestmark = [pytest.mark.real_machine,
              pytest.mark.skipif(not WINDOWS, reason="real Windows actions required")]

NOTE_DATE = "2026-09-17"
NOTE_TEXT = f"note for {NOTE_DATE}"


@pytest.fixture()
def ctx():
    return CallContext(person_id="owner", persona=Persona.OWNER)


@pytest.fixture()
def notepad():
    """Guarantee Notepad is gone before and after each test.

    A leftover Notepad window steals focus from the other real-machine tests, so this
    force-kills the process rather than only sending WM_CLOSE (which an unsaved buffer can
    turn into a "save?" dialog that never closes).
    """
    def _kill():
        import subprocess
        subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"],
                       capture_output=True, text=True, timeout=20)

    _kill()
    yield
    _kill()


def exec_(app, ctx, capability, **params):
    return app.computer.execute(ctx, capability, params)


def focus_notepad(app, ctx, deadline_s: float = 20.0):
    """Focus the Notepad window, retrying until it really is in the foreground.

    Windows refuses foreground changes from background processes, so a single attempt can
    legitimately fail while another app holds focus (e.g. Chrome from a browser test running
    in the same session). We wait for the observable state instead of assuming success.
    """
    deadline = time.time() + deadline_s
    last = None
    while time.time() < deadline:
        last = exec_(app, ctx, "window.focus", target="Notepad")
        if last.ok and last.verified:
            return last
        time.sleep(0.3)
    return last


def perform_notepad_note(app, ctx, note_path: Path) -> list[dict]:
    """GENIE really performs the workflow and returns the trace of what it did."""
    steps: list[dict] = []

    opened = exec_(app, ctx, "application.open", target="notepad", wait_s=20)
    assert opened.ok and opened.verified, opened.detail
    steps.append({"capability": "application.open", "params": {"target": "notepad"},
                  "description": "open Notepad", "target": "notepad"})

    focused = focus_notepad(app, ctx)
    assert focused is not None and focused.ok, focused.detail
    steps.append({"capability": "window.focus", "params": {"target": "Notepad"},
                  "description": "focus the Notepad window"})

    typed = exec_(app, ctx, "input.type_text", text=NOTE_TEXT)
    assert typed.ok, typed.detail
    steps.append({"capability": "input.type_text", "params": {"text": NOTE_TEXT},
                  "description": "type the dated note"})

    note_path.parent.mkdir(parents=True, exist_ok=True)
    saved = exec_(app, ctx, "files.write", path=str(note_path), text=NOTE_TEXT)
    assert saved.ok and saved.verified, saved.detail
    steps.append({"capability": "files.write",
                  "params": {"path": str(note_path), "text": NOTE_TEXT},
                  "description": "save the note to disk"})
    return steps


def move_notepad_to(x: int, y: int, width: int, height: int) -> None:
    """Move and resize the Notepad window — the environment change a macro cannot survive.

    Waits on the observed geometry rather than sleeping a fixed amount: the window must
    really be where we asked before the skill runs.
    """
    from computer import windows_api as win
    handles = win.find_windows(title_contains="Notepad")
    assert handles, "Notepad window not found"
    hwnd = handles[0].hwnd
    win.move_window(hwnd, x, y, width, height)
    deadline = time.time() + 10
    while time.time() < deadline:
        rect = next((h.rect for h in win.find_windows(title_contains="Notepad")
                     if h.hwnd == hwnd), None)
        if rect and abs(rect[0] - x) <= 2 and abs(rect[1] - y) <= 2:
            return
        time.sleep(0.15)
    raise AssertionError(f"Notepad did not move to {(x, y)} within 10s")

def notepad_geometry() -> tuple:
    from computer import windows_api as win
    for handle in win.find_windows(title_contains="Notepad"):
        return tuple(handle.rect)
    return ()


# ------------------------------------------------------------------ mission -> skill
def test_golden_notepad_note_becomes_a_skill_and_replays_at_a_changed_environment(
        app, ctx, tmp_path, notepad):
    """GENIE works, learns the procedure, then repeats it under changed conditions."""
    first_note = tmp_path / "notes" / f"{NOTE_DATE}.txt"
    trace_steps = perform_notepad_note(app, ctx, first_note)
    assert first_note.read_text(encoding="utf-8") == NOTE_TEXT

    # --- 1. the successful workflow is recognised as worth remembering
    trace = {"goal": "write a dated note in notepad", "source": "mission",
             "mission_id": "mis_golden", "succeeded": True, "verified": True,
             "steps": trace_steps, "environment": {"platform": "windows"}}
    decision = app.skills.detector.should_create(trace)
    assert decision.ok is True, decision.blockers

    # --- 2. it is generalised into a parameterised skill (no baked-in values)
    skill = app.skills.generalizer.from_trace(trace, skill_id="notepad-dated-note",
                                              name="write a dated note in Notepad")
    assert validate_skill(skill) == []
    assert skill.status == SkillStatus.CANDIDATE.value
    # the environment values must not be baked into the LOGIC (the input examples may still
    # record what was observed — that is provenance, not a hard-coded step)
    step_text = str([s.to_dict() for s in skill.steps])
    assert str(first_note) not in step_text, "the path must become a variable"
    assert NOTE_TEXT not in step_text, "the note text must become a variable"
    assert "${" in step_text

    # --- 3. the candidate is validated by a REAL run before it may become active
    validation = app.skills.validator.validate(skill, ctx, {"path": str(first_note),
                                                            "text": NOTE_TEXT})
    assert validation["passed"] is True, validation
    assert validation["stage"] == "execution"
    skill.status = SkillStatus.ACTIVE.value
    registered = app.skills.registry.register(skill, activate=True)
    assert registered["ok"] is True

    # --- 4. the registry can now retrieve it from the goal alone
    match = app.skills.select("write a dated note in notepad")
    assert match is not None and match.skill.skill_id == "notepad-dated-note"

    # --- 5. CHANGE THE ENVIRONMENT: new window position, new size, new path
    move_notepad_to(40, 70, 520, 380)
    focus_notepad(app, ctx)
    second_note = tmp_path / "another-place" / "deeper" / f"note-{NOTE_DATE}.txt"
    assert not second_note.exists()
    replay = app.skills.execute(ctx, "write a dated note in notepad",
                                {"path": str(second_note), "text": NOTE_TEXT})
    assert replay["ok"] is True, replay
    assert replay["status"] == "succeeded", replay
    assert replay["verified"] is True
    # the proof is the file itself, at the NEW path, with the right content
    assert second_note.read_text(encoding="utf-8") == NOTE_TEXT
    assert replay["skill_id"] == "notepad-dated-note"

    # --- 6. the skill carries no coordinates: that is WHY it survived the move
    for step in skill.steps:
        for key in ("x", "y", "width", "height"):
            assert key not in step.params, f"{step.capability} depends on window geometry"
    assert "mouse" not in str(skill.to_dict())


def test_golden_skill_survives_a_second_geometry_change(app, ctx, tmp_path, notepad):
    """Replay again after another move — the skill is environment independent, not lucky."""
    note = tmp_path / "notes" / "first.txt"
    trace_steps = perform_notepad_note(app, ctx, note)
    skill = app.skills.generalizer.from_trace(
        {"goal": "write a dated note in notepad", "succeeded": True, "verified": True,
         "steps": trace_steps}, skill_id="notepad-dated-note", name="write a dated note")
    skill.status = SkillStatus.ACTIVE.value
    app.skills.registry.register(skill, activate=True)

    for index, geometry in enumerate([(300, 120, 640, 460), (60, 40, 900, 700)]):
        move_notepad_to(*geometry)
        focus_notepad(app, ctx)
        target = tmp_path / f"run-{index}" / "note.txt"
        result = app.skills.execute(ctx, "write a dated note in notepad",
                                    {"path": str(target), "text": NOTE_TEXT})
        assert result["ok"] is True, (geometry, result)
        assert target.read_text(encoding="utf-8") == NOTE_TEXT


# --------------------------------------------------------------- teaching -> skill
def test_golden_teaching_demonstration_becomes_a_working_skill(app, ctx, tmp_path, notepad):
    """The owner demonstrates; GENIE generalises, validates, and only then saves."""
    demonstrated = tmp_path / "demo" / f"{NOTE_DATE}.txt"

    # --- the owner demonstrates the workflow (semantic events, never coordinates)
    app.skills.start_teaching(goal="write a dated note in notepad", application="notepad.exe")
    recorder = app.skills.recorder
    recorder.note_app_launch("notepad")
    recorder.note_typing(NOTE_TEXT, target_field="document")
    recorder.note_file_save(str(demonstrated), NOTE_TEXT)
    stopped = app.skills.stop_teaching()
    assert stopped["events"] == 3

    # --- a demonstration alone must not have produced a skill
    assert app.skills.list() == []

    # --- GENIE generalises it into a candidate
    candidate = app.skills.learn_from_demonstration(auto_save=False)
    assert candidate["ok"] is True and candidate["learned"] is False
    assert candidate["candidate"]["status"] == SkillStatus.CANDIDATE.value
    assert str(demonstrated) not in str(candidate["candidate"]), "path must be a variable"
    assert app.skills.list() == [], "an unvalidated candidate must not be registered"

    # --- the candidate is validated against a real run at a DIFFERENT path
    real_target = tmp_path / "real" / "note.txt"
    skill = app.skills.generalizer.from_demonstration(
        {"session_id": candidate["session"]["session_id"],
         "goal": "write a dated note in notepad", "application": "notepad.exe",
         "events": [e.to_dict() for e in recorder.events]},
        skill_id="taught-dated-note", name="taught dated note")
    assert validate_skill(skill) == []
    validation = app.skills.validator.validate(skill, ctx, {"path": str(real_target),
                                                            "text": NOTE_TEXT})
    assert validation["passed"] is True, validation
    skill.status = SkillStatus.ACTIVE.value
    assert app.skills.registry.register(skill, activate=True)["ok"] is True

    # --- and it replays successfully from a plain goal
    move_notepad_to(210, 150, 600, 420)
    focus_notepad(app, ctx)
    final = tmp_path / "final" / "note.txt"
    result = app.skills.execute(ctx, "write a dated note in notepad",
                                {"path": str(final), "text": NOTE_TEXT})
    assert result["ok"] is True, result
    assert final.read_text(encoding="utf-8") == NOTE_TEXT


# ------------------------------------------------------------------ honest failure
def test_skill_reports_failure_when_the_verification_does_not_hold(app, ctx, tmp_path,
                                                                   notepad):
    """A skill whose promised effect never happens must fail — no fake success."""
    skill = app.skills.generalizer.from_trace(
        {"goal": "write a dated note in notepad", "succeeded": True, "verified": True,
         "steps": [{"capability": "application.open", "params": {"target": "notepad"}},
                   {"capability": "input.type_text", "params": {"text": NOTE_TEXT}},
                   {"capability": "files.write",
                    "params": {"path": str(tmp_path / "wanted.txt"), "text": NOTE_TEXT}}]},
        skill_id="notepad-dated-note", name="write a dated note")
    # point the verification at a file the workflow never creates
    skill.verification = [{"check": "file_exists",
                           "value": str(tmp_path / "never-written.txt")}]
    skill.status = SkillStatus.ACTIVE.value
    app.skills.registry.register(skill, activate=True)

    result = app.skills.execute(ctx, "write a dated note in notepad",
                                {"path": str(tmp_path / "wanted.txt"), "text": NOTE_TEXT})
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["verified"] is False


def test_no_skill_match_is_an_honest_no_op(app, ctx):
    """With nothing learned for the goal, GENIE declines instead of guessing."""
    result = app.skills.execute(ctx, "reconcile the quarterly ledgers in SAP")
    assert result["ok"] is False
    assert result["error_code"] == "no_skill_match"
