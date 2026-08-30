"""Skills as packages — design decision 4.

Resolves, installs and publishes skills through the Hugging Face Hub. tendon runs no
registry of its own: running one means running auth, storage, moderation and uptime,
none of which is this project. A skill.yaml points at a Hub repo and this module
resolves it.
"""

from __future__ import annotations


class Registry:
    def install(self, ref: str) -> str:
        """Resolve namespace/name@version and return the local skill path."""
        raise NotImplementedError("v0.4")

    def publish(self, skill_path: str, ref: str) -> str:
        raise NotImplementedError("v0.4")
