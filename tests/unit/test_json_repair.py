"""Structured-output repair (re-audit 14.5, donor: AgentScope).

Malformed model output is a real failure source. The repair must fix what is genuinely fixable and
**fail honestly** on what is not, rather than inventing an object the model never produced.
"""
from __future__ import annotations

from core.jsonrepair import loads_lenient, repair


def test_valid_json_parses_without_repair():
    ok, value, strategy = loads_lenient('{"a": 1, "b": [1, 2]}')
    assert ok is True and value == {"a": 1, "b": [1, 2]}
    assert strategy == "direct"


def test_json_inside_a_code_fence_is_repaired():
    ok, value, strategy = loads_lenient('```json\n{"cmd": "ls"}\n```')
    assert ok is True and value == {"cmd": "ls"}
    assert strategy == "strip_fences"


def test_prose_around_the_object_is_stripped():
    ok, value, _ = loads_lenient('Sure! Here you go:\n{"cmd": "ls"}\nhope that helps')
    assert ok is True and value == {"cmd": "ls"}


def test_trailing_commas_are_removed():
    ok, value, _ = loads_lenient('{"a": 1, "b": 2,}')
    assert ok is True and value == {"a": 1, "b": 2}


def test_a_truncated_object_is_closed():
    ok, value, strategy = loads_lenient('{"cmd": "ls", "args": ["-la"')
    assert ok is True, "a truncated response should be recoverable"
    assert value["cmd"] == "ls"
    assert strategy == "close_truncated"


def test_nested_structures_survive_repair():
    ok, value, _ = loads_lenient('```json\n{"a": {"b": [1, {"c": 2}]}}\n```')
    assert ok is True and value["a"]["b"][1]["c"] == 2


def test_garbage_is_reported_not_invented():
    """The honesty case: no plausible-looking object may be fabricated."""
    ok, value, strategy = loads_lenient("I'm afraid I can't do that.")
    assert ok is False
    assert value is None, "must not invent a value the model never produced"
    assert strategy == ""


def test_repair_reports_whether_it_changed_anything():
    assert repair('{"a": 1}')["repaired"] is False
    assert repair('```json\n{"a": 1}\n```')["repaired"] is True


def test_schema_validation_passes_for_a_valid_object():
    schema = {"type": "object", "required": ["cmd"],
              "properties": {"cmd": {"type": "string"}}}
    result = repair('```json\n{"cmd": "ls"}\n```', schema=schema)
    assert result["ok"] is True
    assert result["value"]["cmd"] == "ls"


def test_schema_validation_failure_is_reported():
    schema = {"type": "object", "required": ["cmd"],
              "properties": {"cmd": {"type": "string"}}}
    result = repair('{"other": 1}', schema=schema)
    assert result["ok"] is False
    assert "schema" in result["error"]


def test_empty_input_fails_cleanly():
    ok, value, _ = loads_lenient("")
    assert ok is False
    assert value is None


def test_a_bare_array_parses():
    ok, value, _ = loads_lenient('```json\n[1, 2, 3]\n```')
    assert ok is True and value == [1, 2, 3]
