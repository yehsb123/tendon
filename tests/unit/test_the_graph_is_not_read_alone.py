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

from tendon.cli.reporting import report_success
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
    report_success(console, records)
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


def test_an_unjudged_episode_is_not_counted_as_a_failure() -> None:
    """`EpisodeOutcome.succeeded` was `bool`, and the caller filled it with
    `bool(verdict)` — which turns None into False.

    So `success_rate` divided successes by *every* episode, counting the unmeasurable ones
    as failures. Until the MuJoCo driver began reporting `cube_height` that was every
    episode this project could produce, and the number read 0%: *the policy fails every
    time*, where the truth was *nobody measured*.
    """
    from tendon.services.evaluator import EpisodeOutcome, evaluate

    def outcome(episode_id: str, succeeded: bool | None) -> EpisodeOutcome:
        return EpisodeOutcome(episode_id=episode_id, skill="grasp/cube-sim", succeeded=succeeded)

    result = evaluate(
        [outcome("a", True), outcome("b", None), outcome("c", None)], skill="grasp/cube-sim"
    )

    assert result.episodes == 3
    assert result.unjudged == 2
    assert result.judged == 1
    assert result.success_rate == 1.0, "the one judged episode succeeded"


def test_a_run_nobody_could_judge_has_no_success_rate_rather_than_zero() -> None:
    """None, not 0.0. A rate of zero is a measurement; having none is not, and the two
    read as opposite results about the same run."""
    from tendon.services.evaluator import EpisodeOutcome, evaluate

    result = evaluate(
        [EpisodeOutcome(episode_id=str(i), skill="s", succeeded=None) for i in range(3)],
        skill="s",
    )

    assert result.success_rate is None


def test_an_unjudged_episode_is_not_a_failure_mode() -> None:
    """They were grouped as one, under whatever reason `judge` gave for being unable to
    decide — so "body does not report 'cube_height'" appeared in a table headed *failure
    modes*, which reads as the policy failing that way."""
    from tendon.services.evaluator import EpisodeOutcome, evaluate

    result = evaluate(
        [
            EpisodeOutcome(
                episode_id="a",
                skill="s",
                succeeded=None,
                failure_mode="body does not report 'cube_height'",
            ),
            EpisodeOutcome(episode_id="b", skill="s", succeeded=False, failure_mode="too low"),
        ],
        skill="s",
    )

    assert result.failure_modes == {"too low": 1}


def test_everything_that_judges_an_episode_judges_it_the_same_way() -> None:
    """Three callers need the verdict: `tendon run`, `tendon eval`, and the API's session.

    Each would otherwise write the same four lines, and one of them wrote none — the API
    recorded no verdict at all, so an episode started from the shell landed on the v0.3
    graph unjudged while one from the command line landed judged. Two kinds of point on
    the axis the project is decided by.

    `judge_result` is the one place now. Asserted by who calls it rather than by reading a
    named function's body, which is what broke when that function moved.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "tendon"
    callers = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "judge_result(" in path.read_text(encoding="utf-8") and path.name != "evaluator.py"
    }

    assert callers == {"cli/main.py", "api/app.py"}, (
        f"{sorted(callers)} judge episodes; the CLI and the API are the two, and a third "
        f"place doing it means a third answer to the same question"
    )

    # And nobody compares a criterion by hand, which is how the copies would drift apart.
    hand_rolled = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if path.name != "evaluator.py"
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"holds", "met_by"}
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert not hand_rolled, f"{hand_rolled} evaluate success criteria themselves"
