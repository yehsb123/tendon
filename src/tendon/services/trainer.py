"""LoRA fine-tuning on curated episodes and recorded corrections.

Sized for one consumer GPU overnight, which is a design constraint rather than a
limitation to be lifted later. Design decision 1 requires the loop to close every night;
a training run that needs a cluster does not close nightly, and the loop is the project.

Uses PEFT and transformers. We do not write a training framework. See docs/stack.md.
"""

from __future__ import annotations


class Trainer:
    def fine_tune(self, skill: str, episode_ids: list[str]) -> str:
        """Train a LoRA adapter and return its path or Hub id."""
        raise NotImplementedError("v0.3")
