"""GENIE spec pipeline — requirements → plan → tasks → implement → validate
(P5, Spec Kit gap).

Spec Kit's valuable idea is that a feature moves through *explicit, gated
phases* and each phase leaves an artifact, so implementation can be traced back
to a requirement and validation means "every requirement is covered by evidence"
rather than "it seems to work".

GENIE had missions and steps, but no explicit requirement records, no
traceability from task back to requirement, and no gate that refuses to advance
while a requirement is uncovered.

This adds that, by contract (Spec Kit is a CLI/template toolkit; we do not
import it).

Phase rule: a phase cannot be entered until the previous one validates.
Validation is mechanical — a requirement with no task, or a task with no
outcome, blocks progress. Nothing is assumed to pass.
"""
