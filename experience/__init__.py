"""GENIE experience — trajectory summarisation, semantic group advantage,
and a textual experience bank (P5, Youtu-Agent gap).

Youtu-Agent's valuable idea is *Training-Free GRPO*: run a group of rollouts for
a task, compare the successes against the failures, extract the difference as
**semantic** (natural-language) advantage rather than a numeric gradient, and
accumulate those findings in a **textual experience bank** that is injected into
future runs. No parameter updates, no model call.

GENIE already had `ExperienceStore` (agents/factory.py), which records
structured per-task outcomes but does NOT do the three things above. This
package adds them on top, by contract (Youtu-Agent is a separate framework; we
do not import it).

Honesty rule: advantages are DERIVED FROM RECORDED OUTCOMES, never invented. If
there are not enough trajectories, the bank reports insufficient evidence
instead of producing a plausible-sounding lesson.
"""
