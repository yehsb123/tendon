"""A measured scale for confidence, and the difference between a scale and a threshold.

`services/confidence.py` scores a chunk against a *reference spread*. Every caller had to
supply one and none could: `api/app.py` passes 0.004, fitted to the synthetic policy it
drives, and the CLI passed zero — which makes `estimate_from_samples` answer `NONE` with
"no reference spread configured, so the measurement has no scale". A real checkpoint could
run and could not report confidence, which is design decision 2 not working.

ADR 0003 says calibration waits for v0.3 and intervention outcomes. That is true of the
**threshold** and was being read as true of both:

- *How much disagreement is typical here?* A property of the policy and the body, measured
  by running them. No labels. Available now, and this is what the module supplies.
- *How much means ask for help?* A property of what goes wrong when you do not. Needs
  episodes where somebody took over. Still v0.3, still the loop's own data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tendon.services.calibration import (
    MINIMUM_SAMPLES,
    Calibration,
    calibration_path,
    from_spreads,
    load,
    save,
)


def _spreads(count: int, *, start: float = 0.001, step: float = 0.0001) -> list[float]:
    return [start + step * index for index in range(count)]


def _measure(spreads: list[float], **kwargs: str) -> Calibration:
    return from_spreads(
        spreads,
        skill=kwargs.get("skill", "grasp/cube-sim"),
        body=kwargs.get("body", "mujoco:arm"),
        policy=kwargs.get("policy", "lerobot/smolvla_base+adapter"),
        measured_at=kwargs.get("measured_at", "2026-08-31T00:00:00Z"),
    )


# ------------------------------------------------------------------------ measuring


def test_the_reference_is_the_middle_of_what_was_seen() -> None:
    """A typical step then scores 0.5, which is what "typical" has to mean for the number
    to be readable. Not the mean, which one wild sample drags."""
    measured = _measure([0.001] * 20 + [10.0])

    assert measured.reference_spread == pytest.approx(0.001)


def test_too_few_samples_is_refused_rather_than_averaged() -> None:
    """The alternative is a number that looks like a measurement and is not — the failure
    this module exists to remove, reintroduced one level up."""
    with pytest.raises(ValueError, match=str(MINIMUM_SAMPLES)):
        _measure(_spreads(MINIMUM_SAMPLES - 1))


def test_a_deterministic_policy_yields_nothing_to_measure() -> None:
    """Its samples are identical, so every spread is zero and there is no disagreement.
    Scoring it 1.0 is exactly how ACT came to look certain."""
    with pytest.raises(ValueError, match="deterministic"):
        _measure([0.0] * 100)


def test_the_distribution_is_recorded_beside_the_middle() -> None:
    """So a reader can judge whether the middle means much."""
    measured = _measure(_spreads(100))

    assert measured.p10 < measured.reference_spread < measured.p90
    assert measured.samples == 100


def test_a_wide_distribution_is_reported_not_refused() -> None:
    """A policy whose disagreement varies by orders of magnitude has no typical behaviour
    to speak of. That is a real property of the policy, not a failed measurement — so it is
    named rather than rejected."""
    tight = _measure([0.001 + 0.00001 * i for i in range(100)])
    wide = _measure([0.0001 * (1.1**i) for i in range(100)])

    assert tight.is_tight
    assert not wide.is_tight


# -------------------------------------------------------------------------- the store


def test_a_measurement_survives_the_round_trip(tmp_path: Path) -> None:
    measured = _measure(_spreads(50))
    save(tmp_path, measured)

    assert load(tmp_path, "grasp/cube-sim", "mujoco:arm") == measured


def test_names_with_slashes_and_colons_are_stored_and_come_back(tmp_path: Path) -> None:
    """`grasp/cube-sim` and `mujoco:arm` both contain characters Windows refuses in a path,
    and a store that works on one platform is not a store. The names come back from inside
    the file, so a skill with an underscore is not confused with one that had a slash."""
    measured = _measure(_spreads(50), skill="grasp/cube_sim", body="so101:COM3")
    save(tmp_path, measured)

    path = calibration_path(tmp_path, "grasp/cube_sim", "so101:COM3")
    assert path.is_file()

    read = load(tmp_path, "grasp/cube_sim", "so101:COM3")
    assert read is not None
    assert read.skill == "grasp/cube_sim"
    assert read.body == "so101:COM3"


def test_a_missing_measurement_is_absence_not_an_error(tmp_path: Path) -> None:
    assert load(tmp_path, "grasp/cube-sim", "mujoco:arm") is None


def test_an_unreadable_measurement_is_absence_too(tmp_path: Path) -> None:
    """A calibration that cannot be read must not become one that is guessed. None means
    "no scale", which `confidence.py` already answers by reporting NONE rather than a
    number — the safe direction."""
    path = calibration_path(tmp_path, "grasp/cube-sim", "mujoco:arm")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    assert load(tmp_path, "grasp/cube-sim", "mujoco:arm") is None


def test_a_file_from_another_format_is_ignored_rather_than_interpreted(tmp_path: Path) -> None:
    """Read wrongly is worse than missing: the first produces confident-looking scores on
    the wrong scale, and the interrupt threshold is read against them."""
    path = calibration_path(tmp_path, "grasp/cube-sim", "mujoco:arm")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": "something.else", "reference_spread": 9.9}), "utf-8")

    assert load(tmp_path, "grasp/cube-sim", "mujoco:arm") is None


def test_the_measurement_records_which_policy_it_came_from(tmp_path: Path) -> None:
    """A reference measured from one policy says nothing about another. Without this the
    file could not be checked against the policy being run, and a stale scale would produce
    confident-looking scores in the wrong units."""
    save(tmp_path, _measure(_spreads(30), policy="lerobot/act_aloha+v2"))

    read = load(tmp_path, "grasp/cube-sim", "mujoco:arm")
    assert read is not None
    assert read.policy == "lerobot/act_aloha+v2"
