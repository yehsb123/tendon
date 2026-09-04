"""A falling intervention rate is half a claim, and the graph says which half it has.

v0.3 is decided by one graph: cumulative human corrections against intervention rate, and
the line goes down. It does go down — `examples/04_improve` produces it and prints PASS.

**A policy that stops asking for help because it stopped trying draws exactly the same
line.** Nothing distinguished those two readings. The example passed on the fall alone, and
`tendon eval grasp/cube-sim` reports the verdict for every episode as unknown, because the
skill asks for `cube_height` and the MuJoCo driver does not put it in `Observation.extra`.

So the ambiguity was not merely unexamined — it was unexaminable, and the one number that
would settle it was missing everywhere. Recording the verdict beside each point does not
supply that number. What it does is stop the graph looking complete without it.
"""

from __future__ import annotations

from rich.console import Console

from tendon.cli.main import _report_success
from tendon.services.progress import EpisodeRecord, now


def _record(*, succeeded: bool | None, interventions: int = 0) -> EpisodeRecord:
    return EpisodeRecord(
        skill="grasp/cube-sim",
        body="mujoco:arm",
        episode_id="abc",
        ended_at=now(),
        steps=100,
        interventions=interventions,
        corrections=0,
        corrections_known=0,
        succeeded=succeeded,
    )


def _printed(records: list[EpisodeRecord]) -> str:
    console = Console(width=200, record=True)
    _report_success(console, records)
    return console.export_text()


def test_an_unmeasured_run_says_so_rather_than_nothing() -> None:
    """The current state of every episode this project can produce. A graph whose other
    half is missing should not look complete."""
    printed = _printed([_record(succeeded=None) for _ in range(10)])

    assert "not measured" in printed
    assert "stopped trying" in printed, "the specific misreading is what needs naming"


def test_a_measured_run_reports_the_rate() -> None:
    records = [_record(succeeded=True) for _ in range(8)] + [
        _record(succeeded=False) for _ in range(2)
    ]

    assert "80%" in _printed(records)


def test_unjudged_episodes_are_named_rather_than_counted_as_failures() -> None:
    """An episode nobody could judge is not a failure. Folding it in would understate a
    policy that works on a rig which cannot say whether it worked."""
    records = [_record(succeeded=True) for _ in range(5)] + [
        _record(succeeded=None) for _ in range(5)
    ]
    printed = _printed(records)

    assert "100%" in printed, "the judged episodes all succeeded"
    assert "5 could not be judged" in printed


def test_the_verdict_has_three_states_not_two() -> None:
    """ "failed" and "nobody measured" are opposite claims. A boolean would have to lie
    about one of them, and the one it would lie about is the one this project is in."""
    assert _record(succeeded=None).succeeded is None
    assert _record(succeeded=False).succeeded is False
    assert _record(succeeded=True).succeeded is True


def test_a_run_and_an_evaluation_judge_the_same_way() -> None:
    """`_judge` calls the evaluator's own `judge` rather than reimplementing the
    comparison, so a run and an evaluation cannot disagree about the same episode."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "src" / "tendon" / "cli" / "main.py").read_text(
        encoding="utf-8"
    )
    body = source.partition("def _judge(")[2].partition("\ndef ")[0]

    assert "from tendon.services.evaluator import" in body
    assert "judge(" in body

    # And nothing in the CLI compares a criterion by hand, which is how the two would drift.
    tree = ast.parse(source)
    comparisons = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "holds"
    ]
    assert not comparisons, "the CLI is evaluating success criteria itself"
