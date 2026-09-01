"""`tendon run` and `tendon eval` write to the log `tendon progress` draws.

That graph is the whole of v0.3 — *after N human corrections, the intervention rate drops*
— and only `api/app.py` wrote to it. An episode counted towards the proof only if it had
been started from the shell, so `tendon eval --episodes 50` produced fifty episodes and an
empty log, and `tendon progress` answered "nothing has run yet, start an episode from the
shell". True, and it reads as a fact about the store rather than about who fills it: the
person who had just run fifty episodes concludes the log is broken.

The control arm was the part that could not be recorded at all. A run with no operator is
not a missing data point — it is the intervention rate at zero corrections, which is the
left end of the line every other point is measured against.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tendon.cli.main import app

pytest.importorskip("mujoco")

RUNNER = CliRunner()
REPO = Path(__file__).resolve().parents[2]
SKILL = str(REPO / "skills" / "grasp" / "cube-sim")


def _records(progress_root: Path) -> list[dict]:
    import json

    return [
        json.loads(line)
        for path in sorted(progress_root.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_a_single_run_lands_in_the_log(tmp_path: Path) -> None:
    result = RUNNER.invoke(
        app, ["run", SKILL, "--steps", "5", "--store", str(tmp_path / "episodes")]
    )
    assert result.exit_code == 0, result.output

    records = _records(tmp_path / "progress")
    assert len(records) == 1
    assert records[0]["skill"] == "grasp/cube-sim"
    assert records[0]["steps"] > 0


def test_an_evaluation_records_every_episode_not_the_sweep(tmp_path: Path) -> None:
    """Thirty evaluation episodes are thirty points. A sweep recorded as one would hide
    exactly what the graph is for: whether the rate moves across them."""
    result = RUNNER.invoke(
        app,
        [
            "eval",
            SKILL,
            "--episodes",
            "3",
            "--steps",
            "5",
            "--store",
            str(tmp_path / "episodes"),
        ],
    )
    assert result.exit_code == 0, result.output

    records = _records(tmp_path / "progress")
    assert len(records) == 3, "an evaluation collapsed into one point"
    assert len({record["episode_id"] for record in records}) == 3


def test_corrections_known_is_read_from_the_store_not_counted_from_the_run(
    tmp_path: Path, monkeypatch
) -> None:
    """It means "corrections held for this skill and body", not "corrections given just
    now". An evaluation run after an afternoon of teaching belongs at the x position that
    teaching reached, not back at zero — otherwise the control arm and the taught arm sit
    on top of each other and the graph shows no movement at all.
    """
    import tendon.services.memory_store as memory_store
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
    for index in range(4):
        memory.remember(
            Observation(step=index, proprio=Proprioception(joint_positions=[0.0] * 5)),
            Intent(
                actions=[Action(space=ActionSpace.JOINT_POSITION, values=[0.1] * 5)],
                horizon_s=0.1,
                confidence=Confidence(score=1.0, source=ConfidenceSource.NONE),
            ),
        )

    taught = tmp_path / "taught"
    monkeypatch.setattr(memory_store, "DEFAULT_MEMORY_ROOT", taught)
    memory_store.save_memory(taught, "grasp/cube-sim", "mujoco:so_arm100_cube", memory)

    result = RUNNER.invoke(
        app, ["run", SKILL, "--steps", "5", "--store", str(tmp_path / "episodes")]
    )
    assert result.exit_code == 0, result.output

    records = _records(tmp_path / "progress")
    assert records[0]["corrections_known"] == 4, "the run reported its own count, not the store's"


def test_a_log_that_cannot_be_written_does_not_fail_the_run(tmp_path: Path) -> None:
    """A finished episode is a finished episode. Losing the run because the graph could not
    be appended to would trade the thing that happened for the record of it."""
    blocked = tmp_path / "progress"
    blocked.write_text("", encoding="utf-8")  # a file where the directory has to go

    result = RUNNER.invoke(
        app, ["run", SKILL, "--steps", "5", "--store", str(tmp_path / "episodes")]
    )

    assert result.exit_code == 0, result.output
    assert "could not record progress" in result.output, "it failed silently"


def test_every_command_that_runs_episodes_records_them() -> None:
    """The property, so the next command to run an episode cannot quietly skip the graph.

    `api/app.py` had this right and the CLI did not, for no reason anybody decided — which
    is how a proof ends up depending on which door an episode came through.
    """
    source = (REPO / "src" / "tendon" / "cli" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    def calls(node: ast.AST, name: str) -> bool:
        return any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == name
            for inner in ast.walk(node)
        )

    def calls_method(node: ast.AST, name: str) -> bool:
        return any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == name
            for inner in ast.walk(node)
        )

    runners = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and calls_method(node, "run_episode")
    ]

    assert runners, "no command runs an episode; the walk is broken"
    missing = [node.name for node in runners if not calls(node, "_record_progress")]
    assert not missing, f"{missing} run episodes without recording them on the v0.3 graph"
