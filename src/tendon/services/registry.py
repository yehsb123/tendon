"""Skills as packages — design decision 4. Not implemented: v0.4.

Will resolve, install and publish skills through the Hugging Face Hub. tendon runs no
registry of its own: running one means running auth, storage, moderation and uptime,
none of which is this project. A skill.yaml points at a Hub repo, and resolving it is
`tendon install`, which does not exist yet.

Both methods raise `NotImplementedError` and nothing in the package calls them. vulture
reported the class as unused; the module said in the present tense that it resolves
skills, which is the same mismatch `SECURITY.md` and the README had about the command.
"""

from __future__ import annotations


class Registry:
    def install(self, ref: str) -> str:
        """Resolve namespace/name@version and return the local skill path."""
        raise NotImplementedError("v0.4")

    def publish(self, skill_path: str, ref: str) -> str:
        raise NotImplementedError("v0.4")
