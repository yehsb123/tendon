"""`/api/memory` lists what is on disk, not only what this process has loaded.

The endpoint is titled "what the operator has taught". It listed `memories`, an in-process
dict filled when a session starts, so after a restart it returned an empty list while the
corrections sat in `~/.tendon/memory` — a view of teaching that showed none of the teaching.

Its docstring explained why: "the store does not have it". That stopped being true when
`memory_store.py` was written. `_learn_and_keep` saves after every correction and a
starting session loads what is there, so the explanation outlived the gap and left a real
one behind: the reasoning was stale, and the behaviour it justified was not revisited.

The same shape as `tendon shell` printing dev-server instructions because the runtime "does
not serve static files" after it had started serving them. A rationale nobody rechecks
keeps its conclusion alive.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]


def _memory_with(count: int):
    from tendon.kernel.types import (
        Action,
        ActionSpace,
        Confidence,
        ConfidenceSource,
        Intent,
        Observation,
        Proprioception,
    )
    from tendon.services.adaptive import CorrectionMemory

    memory = CorrectionMemory()
    for index in range(count):
        memory.remember(
            Observation(step=index, proprio=Proprioception(joint_positions=[float(index)] * 5)),
            Intent(
                actions=[Action(space=ActionSpace.JOINT_POSITION, values=[0.1] * 5)],
                horizon_s=0.1,
                confidence=Confidence(score=1.0, source=ConfidenceSource.NONE),
            ),
        )
    return memory


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    from tendon.api.app import create_app

    return TestClient(
        create_app(
            skill_root=REPO / "skills",
            episode_root=tmp_path / "episodes",
            memory_root=tmp_path / "memory",
            progress_root=tmp_path / "progress",
        )
    )


def test_corrections_taught_before_this_runtime_started_are_listed(
    client: TestClient, tmp_path: Path
) -> None:
    """No session has been started. The runtime came up after the teaching, which is the
    ordinary case: an operator restarts and looks at what they have."""
    from tendon.services.memory_store import save_memory

    save_memory(tmp_path / "memory", "grasp/cube-sim", "mujoco:so_arm100", _memory_with(3))

    listed = client.get("/api/memory").json()

    assert len(listed) == 1, "the store was not read"
    assert listed[0]["skill"] == "grasp/cube-sim"
    assert listed[0]["body"] == "mujoco:so_arm100"
    assert listed[0]["corrections"] == 3


def test_a_skill_with_an_underscore_keeps_its_name(client: TestClient, tmp_path: Path) -> None:
    """The filename is sanitised: `grasp/cube_sim` and `grasp_cube_sim` become the same
    thing. Skill and body are read from inside the file for that reason, so a name with an
    underscore comes back as it was written rather than as a guess about which underscore
    used to be a slash."""
    from tendon.services.memory_store import save_memory

    save_memory(tmp_path / "memory", "grasp/cube_sim", "mujoco:arm_100", _memory_with(1))

    listed = client.get("/api/memory").json()

    assert listed[0]["skill"] == "grasp/cube_sim"
    assert listed[0]["body"] == "mujoco:arm_100"


def test_an_unreadable_memory_does_not_hide_the_others(client: TestClient, tmp_path: Path) -> None:
    """Matching `load_memory`, which never raises. One corrupt file must not make the rest
    unlistable — the view exists to show an operator what they have, and showing them
    nothing because of a neighbouring file is the worst available answer."""
    from tendon.services.memory_store import save_memory

    root = tmp_path / "memory"
    save_memory(root, "grasp/cube-sim", "mujoco:one", _memory_with(2))
    (root / "corrupt.json").write_text("{ not json", encoding="utf-8")

    listed = client.get("/api/memory").json()

    assert len(listed) == 1
    assert listed[0]["corrections"] == 2


def test_nothing_taught_is_still_an_empty_list(client: TestClient) -> None:
    assert client.get("/api/memory").json() == []
