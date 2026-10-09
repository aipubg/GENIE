"""High-level mission planner (spec section 9).

Canonical path:

    objective -> deliverables -> acceptance criteria -> dependency DAG -> workers

This is deliberately NOT computer/planner.py, which chooses capabilities for a
single computer action. This module turns an owner objective into ordered tasks
that carry explicit completion criteria, so "done" is checkable rather than
implied.

Decomposition is deterministic and offline: an expensive model is not spent on
figuring out that "write tests" comes after "write code". The per-kind
templates encode real ordering constraints (verify after implement, cite after
gather). A later model-backed pass may enrich criteria, but the skeleton and
its dependencies must never depend on a model being reachable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------- kinds
KIND_CODING = "coding"
KIND_RESEARCH = "research"
KIND_CONTENT = "content"
KIND_COMPUTER = "computer"
KIND_GENERAL = "general"

_CODING = re.compile(
    r"\b(code|coding|implement|refactor|fix|bug|patch|function|class|module|"
    r"test|unittest|pytest|repo|repository|build|compile|debug|api|endpoint|"
    r"script|refactoring|feature)\b", re.I)
_RESEARCH = re.compile(
    r"\b(research|investigate|find out|compare|analyze|analyse|survey|study|"
    r"explore|what is|why does|summar\w*|report on|literature|sources|"
    r"benchmark|evaluate options)\b", re.I)
_CONTENT = re.compile(
    r"\b(write|draft|article|blog|post|script|video|youtube|caption|newsletter|"
    r"story|copy|content|outline|email|thread|social|tweet|thumbnail|title)\b",
    re.I)
_COMPUTER = re.compile(
    r"\b(open|click|type|screenshot|desktop|window|application|app|browser|"
    r"file|folder|download|install|launch|keyboard|mouse|screen|automate)\b",
    re.I)


def classify_objective(objective: str) -> str:
    text = (objective or "").strip()
    if not text:
        return KIND_GENERAL
    scores = {
        KIND_CODING: len(_CODING.findall(text)),
        KIND_RESEARCH: len(_RESEARCH.findall(text)),
        KIND_CONTENT: len(_CONTENT.findall(text)),
        KIND_COMPUTER: len(_COMPUTER.findall(text)),
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] == 0:
        return KIND_GENERAL
    # "write a test for X" is coding, not content writing
    if best == KIND_CONTENT and scores[KIND_CODING] >= scores[KIND_CONTENT]:
        return KIND_CODING
    return best


# --------------------------------------------------------------------- tasks
@dataclass
class Task:
    task_id: str
    objective: str
    completion_criteria: str
    depends_on: List[str] = field(default_factory=list)
    required_role: str = "worker"
    requires_review: bool = False
    kind: str = KIND_GENERAL


@dataclass
class Plan:
    objective: str
    kind: str
    deliverables: List[str]
    tasks: List[Task]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective": self.objective,
            "kind": self.kind,
            "deliverables": list(self.deliverables),
            "tasks": [
                {
                    "task_id": t.task_id,
                    "objective": t.objective,
                    "completion_criteria": t.completion_criteria,
                    "depends_on": list(t.depends_on),
                    "required_role": t.required_role,
                    "requires_review": t.requires_review,
                }
                for t in self.tasks
            ],
        }


# Templates: (suffix, role, criteria, review)
_CODING_STEPS = [
    ("understand", "worker",
     "Requirements restated: affected files/modules identified and the desired "
     "behaviour is unambiguous.", False),
    ("inspect", "worker",
     "Existing relevant code located and current behaviour documented, including "
     "constraints that limit the change.", False),
    ("implement", "worker",
     "The change is written and the code parses/compiles without new errors.",
     True),
    ("test", "tester",
     "Tests covering the change exist and pass; regressions in touched areas "
     "were checked.", True),
    ("verify", "reviewer",
     "Acceptance criteria of the original request are checked one by one against "
     "the running result.", True),
    ("summarise", "worker",
     "A short summary states what changed, what was verified, and anything left "
     "unfinished.", False),
]

_RESEARCH_STEPS = [
    ("frame", "worker",
     "The question is narrowed to specific, answerable sub-questions with the "
     "decision they inform.", False),
    ("gather", "researcher",
     "Sources are collected, each with its origin recorded; conflicting claims "
     "are noted rather than smoothed over.", False),
    ("verify", "worker",
     "Key claims are checked against a second source or primary evidence; "
     "unverifiable claims are labelled as such.", True),
    ("synthesise", "worker",
     "Findings are organised into a coherent answer that directly addresses the "
     "original question.", True),
    ("cite", "reviewer",
     "Every substantive claim carries a source reference; gaps and uncertainty "
     "are stated explicitly.", True),
]

_CONTENT_STEPS = [
    ("brief", "worker",
     "Audience, purpose, tone and length are fixed, plus anything that must or "
     "must not be said.", False),
    ("outline", "worker",
     "A structure exists with the key points in a logical order.", False),
    ("draft", "writer",
     "A complete draft exists in the agreed voice and length.", True),
    ("review", "reviewer",
     "Draft is checked against the brief: accuracy, tone, clarity, and claims "
     "that need support.", True),
    ("finalise", "writer",
     "Final version incorporates review notes and is ready to publish/send.",
     False),
]

_COMPUTER_STEPS = [
    ("inspect", "worker",
     "Current UI/environment state is known: what is open, what the target "
     "looks like, and what would confirm success.", False),
    ("dry_run", "worker",
     "The action sequence is validated without side effects (dry run where the "
     "capability supports it).", True),
    ("execute", "operator",
     "Actions are performed and each step's result is recorded as evidence.",
     True),
    ("confirm", "reviewer",
     "The intended end state is confirmed from the result, not assumed from the "
     "command succeeding.", True),
    ("report", "worker",
     "Outcome reported, including anything that did not happen as expected.",
     False),
]

_GENERAL_STEPS = [
    ("clarify", "worker",
     "The goal and its constraints are explicit; ambiguity is resolved or "
     "flagged.", False),
    ("plan", "worker",
     "Concrete steps with dependencies are listed.", False),
    ("execute", "worker",
     "Steps are carried out and results recorded.", True),
    ("verify", "reviewer",
     "The result is checked against the original goal.", True),
]

_TEMPLATES = {
    KIND_CODING: (_CODING_STEPS, [
        "Working code change",
        "Tests demonstrating the behaviour",
        "Summary of what was verified",
    ]),
    KIND_RESEARCH: (_RESEARCH_STEPS, [
        "Answer to the question",
        "Source references for each key claim",
        "Explicit statement of uncertainty",
    ]),
    KIND_CONTENT: (_CONTENT_STEPS, [
        "Finished draft ready to publish/send",
        "Notes on tone/audience decisions",
    ]),
    KIND_COMPUTER: (_COMPUTER_STEPS, [
        "The intended environment/UI end state",
        "Evidence of each significant step",
    ]),
    KIND_GENERAL: (_GENERAL_STEPS, [
        "Result matching the stated goal",
    ]),
}


class MissionPlanner:
    """Deterministic objective -> ordered tasks with acceptance criteria."""

    def plan(self, objective: str, *, kind: Optional[str] = None,
             complexity: float = 0.5, needs: Optional[List[str]] = None) -> Plan:
        objective = (objective or "").strip()
        kind = kind or classify_objective(objective)
        steps, deliverables = _TEMPLATES.get(kind, _TEMPLATES[KIND_GENERAL])

        tasks: List[Task] = []
        previous: Optional[str] = None
        for suffix, role, criteria, review in steps:
            task_id = f"t{len(tasks) + 1}-{suffix}"
            tasks.append(Task(
                task_id=task_id,
                objective=f"{suffix.replace('_', ' ').capitalize()}: {objective}"
                          if objective else suffix.replace("_", " ").capitalize(),
                completion_criteria=criteria,
                depends_on=[previous] if previous else [],
                required_role=role,
                requires_review=review or complexity >= 0.6,
                kind=kind,
            ))
            previous = task_id

        return Plan(objective=objective, kind=kind,
                    deliverables=list(deliverables), tasks=tasks)


def plan_from_objective(objective: str, **kwargs) -> Dict[str, Any]:
    """Convenience for IPC handlers."""
    return MissionPlanner().plan(objective, **kwargs).to_dict()


__all__ = ["MissionPlanner", "Plan", "Task", "classify_objective",
           "plan_from_objective", "KIND_CODING", "KIND_RESEARCH",
           "KIND_CONTENT", "KIND_COMPUTER", "KIND_GENERAL"]
