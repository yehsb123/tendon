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


# ----------------------------------------------------- what a threshold would actually do


def test_a_threshold_at_the_middle_asks_on_half_of_everything() -> None:
    """The number neither the scale nor the threshold says on its own.

    `estimate_from_samples` scores `1 / (1 + spread / reference)`, and the reference is the
    median — so 0.5 is exactly the point half the observations fall past. The default in
    `skill.yaml` is 0.5, which means a policy measured this way asks for help on every
    other prediction. The only way to find that out was to run it and get an episode that
    stopped at step zero.
    """
    measured = _measure(_spreads(100))

    assert measured.ask_rate(0.5) == pytest.approx(0.5, abs=0.02)


def test_a_lower_threshold_asks_less() -> None:
    measured = _measure(_spreads(100))

    rates = [measured.ask_rate(threshold) for threshold in (0.5, 0.4, 0.3, 0.2, 0.1)]

    assert rates == sorted(rates, reverse=True), "a lower threshold asked for help more"
    assert rates[-1] < rates[0]


def test_it_is_measured_against_the_observations_not_an_assumed_shape() -> None:
    """Two distributions with the *same median* behave differently at the same threshold.

    An assumed shape would report them identically, and the difference is the one that
    matters: what a threshold costs depends on the tail, not on the middle. Both of these
    have a median of 0.001; only the second has anything out past three times it.
    """
    tight = _measure([0.001] * 100)
    skewed = _measure([0.001] * 80 + [0.01] * 20)

    assert tight.reference_spread == skewed.reference_spread
    assert tight.ask_rate(0.3) == pytest.approx(0.0), "nothing here is past the limit"
    assert skewed.ask_rate(0.3) == pytest.approx(0.2, abs=0.01)


def test_a_threshold_of_zero_never_asks() -> None:
    """`should_raise` is strictly-below, so a skill opting out of confidence-based handover
    must not be reported as interrupting on everything."""
    measured = _measure(_spreads(50))

    assert measured.ask_rate(0.0) == 0.0
    assert measured.ask_rate(1.0) == 0.0


def test_the_real_measurement_behaves_as_reported() -> None:
    """The numbers from the actual run on `smolvla_base` + an adapter, kept as a fixture.

    26 predictions over 1300 steps on `mujoco:so_arm100_cube`. Recorded here because the
    consequence — that the skill's default threshold would have asked on roughly half of
    them — is the finding, and a finding worth acting on is worth being able to re-check.
    """
    spreads = [
        0.0429,
        0.0447,
        0.0502,
        0.0538,
        0.0601,
        0.0644,
        0.0689,
        0.0702,
        0.0741,
        0.0755,
        0.0762,
        0.0771,
        0.0774,
        0.0781,
        0.0798,
        0.0823,
        0.0866,
        0.0912,
        0.0978,
        0.1044,
        0.1123,
        0.1201,
        0.1333,
        0.1477,
        0.1624,
        0.1802,
    ]
    measured = _measure(spreads)

    assert measured.reference_spread == pytest.approx(0.0778, abs=0.001)
    assert measured.is_tight, "p90 within ten times p10, so the middle means something"
    assert measured.ask_rate(0.5) == pytest.approx(0.5, abs=0.05)
    assert measured.ask_rate(0.3) < 0.1, "a lower threshold is what makes it runnable"


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
