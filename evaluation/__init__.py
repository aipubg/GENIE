"""GENIE evaluation / dev lab (Phase 12.2).

Capability adapted from Qwen-AgentWorld: scenario-based evaluation with multi-dimensional
scoring, baselines and regression detection. Dev-lab layer — not part of the production runtime.
"""
from .lab import DEFAULT_TOLERANCE, EvaluationLab, Scenario

__all__ = ["EvaluationLab", "Scenario", "DEFAULT_TOLERANCE"]
