"""MuJoCo driver — the only body v0.1 needs.

Chosen as the default because it installs with pip on any machine, runs without an
NVIDIA GPU, and has the contact physics the manipulation literature trusts. Every CLI
command and every example must work against this driver with no hardware attached, so
that a contributor can run the whole project on a laptop. See docs/stack.md.

Requires the sim extra:  pip install "tendon-os[sim]"
"""

from __future__ import annotations

from tendon.drivers.base import Driver, register
from tendon.kernel.types import Action, ActionSpace, Capability, Observation


@register("mujoco")
class MujocoDriver(Driver):
    """A MuJoCo model exposed as a tendon body."""

    def __init__(self, model_path: str, *, control_hz: float = 100.0) -> None:
        self._model_path = model_path
        self._control_hz = control_hz

    @property
    def capability(self) -> Capability:
        raise NotImplementedError("v0.1")

    @property
    def accepts(self) -> tuple[ActionSpace, ...]:
        return (ActionSpace.JOINT_POSITION, ActionSpace.JOINT_VELOCITY)

    def reset(self, *, seed: int | None = None) -> Observation:
        raise NotImplementedError("v0.1")

    def observe(self) -> Observation:
        raise NotImplementedError("v0.1")

    def apply(self, action: Action) -> None:
        raise NotImplementedError("v0.1")

    def close(self) -> None:
        raise NotImplementedError("v0.1")
