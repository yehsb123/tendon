"""Run a skill against its evaluation set and report what happened.

Two outputs. Success rate is the one people ask for. Failure mode breakdown is the one
that changes what you do next, and it is what the shell shows.

This module also produces the graph that decides the project: cumulative human
corrections on x, intervention rate on y. See docs/roadmap.md, v0.3.
"""

from __future__ import annotations


class Evaluator:
    def evaluate(self, skill: str, episodes: int = 50) -> dict:
        raise NotImplementedError("v0.3")

    def intervention_curve(self, skill: str) -> list[tuple[int, float]]:
        """(cumulative corrections, intervention rate) — the proof, or the refutation."""
        raise NotImplementedError("v0.3")
