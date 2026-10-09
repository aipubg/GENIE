"""Hinglish normalisation layer (director/normalize) — the language layer in front of NEDLE2."""
from __future__ import annotations

from director.normalize import normalize, normalize_decision_input


def test_open_command_hinglish():
    assert "open" in normalize("Chrome kholo")
    assert "chrome" in normalize("Chrome kholo")


def test_volume_hinglish():
    out = normalize("awaz 30")
    assert "volume" in out and "30" in out


def test_media_next_hinglish():
    out = normalize("phone ka next song")
    assert "phone" in out and "next" in out and "song" in out


def test_close_command():
    assert "close" in normalize("blender band karo")


def test_play_and_pause():
    assert "play" in normalize("gana chalao")
    assert "pause" in normalize("music roko")


def test_english_passthrough_is_unchanged():
    text = "volume 30"
    out, changed = normalize_decision_input(text)
    assert out == "volume 30"
    assert changed is False


def test_devanagari_command_words():
    out = normalize("क्रोम खोलो")
    assert "open" in out


def test_meaningful_values_are_preserved():
    out = normalize("open blender and play Purple Rain")
    assert "blender" in out
    assert "Purple Rain" in out


def test_negation_words_are_kept():
    out = normalize("chrome mat kholo")
    # 'mat' is not a command word and must survive so the model can see the negation
    assert "mat" in out


def test_empty_input_is_safe():
    assert normalize("") == ""
    out, changed = normalize_decision_input("")
    assert changed is False
