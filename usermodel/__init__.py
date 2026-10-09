"""GENIE user model — cross-session refinement (P5, Hermes gap).

Hermes's valuable idea is "a deepening model of who you are across sessions".
GENIE had general memory (facts, preferences, records) but no dedicated,
continuously refined model of the *person*: nothing accumulated evidence per
trait across sessions, and nothing strengthened or superseded a trait when new
information arrived.

This module adds that, integrated by contract (Hermes is MIT but a Node/Python
agent app — we do not import it).

Honesty rule: observations are extracted **deterministically** from explicit
statements the person actually made ("call me Ada", "I prefer dark mode").
Nothing is inferred by a model and nothing is invented to look insightful. If a
statement does not match a known pattern, no fact is recorded.
"""
