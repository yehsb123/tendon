"""What counts as typical disagreement for one policy on one body.

`services/confidence.py` scores a chunk by comparing its spread against a *reference
spread* — "the disagreement considered typical for this skill on this body". Every caller
had to supply that number and none could: `api/app.py` passes 0.004, fitted to the
synthetic policy it drives, and the CLI passed zero, which makes `estimate_from_samples`
answer `NONE` with "no reference spread configured, so the measurement has no scale".

So a real checkpoint could run and could not report confidence. Design decision 2 is *the
policy raises its own hand*, and it could not.

## Scale is not a threshold, and only one of them needs labels

ADR 0003 says v0.3 calibrates confidence against intervention outcomes, and that is still
true — but it is true of the **threshold**. Two separable questions were being treated as
one:

- *How much disagreement is typical here?* A property of the policy and the body. Measured
  by running the policy and looking at what it does. No labels, no human, available now.
- *How much disagreement means ask for help?* A property of what goes wrong when you do
  not. Needs episodes where somebody took over and what happened after — the labelled data
  the loop is supposed to produce.

This module answers the first. The threshold in `skill.yaml` stays what ADR 0003 calls it:
a starting point rather than a recommendation.

## Why the median

The reference is the middle of the distribution, so a typical step scores 0.5 — which is
what "typical" has to mean for the number to be readable. Not the mean, which one wild
sample drags; not a low percentile, which would make ordinary steps look alarming. The
spread of the distribution is recorded alongside it so a reader can see whether the middle
means much: a policy whose p10 and p90 are far apart has no typical behaviour to speak of,
and its score is a weaker signal than the same number from a tight one.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_CALIBRATION_ROOT",
    "Calibration",
    "calibration_path",
    "from_spreads",
    "load",
    "save",
]

#: Where measurements live. Beside `episodes`, `memory` and `progress` under `~/.tendon`.
DEFAULT_CALIBRATION_ROOT = Path.home() / ".tendon" / "calibration"

#: Format marker, so a file written by a future version is ignored rather than
#: misinterpreted. A calibration read wrongly is worse than one missing: the first produces
#: confident-looking scores on the wrong scale, the second says it has no scale.
_FORMAT = "tendon.calibration.v1"

#: Below this a median is not a distribution. Each sample costs `DEFAULT_SAMPLES` forward
#: passes, so this is not free, and a reference measured from a handful of steps would put
#: a number that looks authoritative in front of an operator.
MINIMUM_SAMPLES = 20


@dataclass(frozen=True)
class Calibration:
    """A measured scale for one policy on one body.

    Carries `skill`, `body` and `policy` even though the filename encodes the first two:
    the name is sanitised, and recovering `grasp/cube-sim` from `grasp_cube-sim` means
    guessing which underscore used to be a slash. The same reasoning
    `progress.EpisodeRecord` gives.
    """

    skill: str
    body: str
    #: What was measured — a checkpoint id, or `scripted` for a baseline. A reference
    #: measured from one policy says nothing about another, and a file that did not record
    #: which policy it came from could not be checked against the one being run.
    policy: str
    #: Every spread that was measured, sorted. The raw observations rather than a summary
    #: of them, so the summary cannot drift from what it summarises — and so a threshold
    #: can be asked what it would actually do, which needs the distribution and not its
    #: middle. Twenty-six floats for a typical run; a thousand is still a small file.
    spreads: tuple[float, ...]
    measured_at: str

    @property
    def samples(self) -> int:
        return len(self.spreads)

    @property
    def reference_spread(self) -> float:
        """Typical disagreement, in action units. What `reference_spread` takes.

        The median, so a typical step scores 0.5. Not the mean, which one wild sample
        drags; not a low percentile, which would make ordinary steps look alarming.
        """
        return statistics.median(self.spreads)

    @property
    def p10(self) -> float:
        return self.spreads[max(0, int(self.samples * 0.10) - 1)]

    @property
    def p90(self) -> float:
        return self.spreads[min(self.samples - 1, int(self.samples * 0.90))]

    @property
    def is_tight(self) -> bool:
        """Whether the distribution has a middle worth calling typical.

        An order of magnitude between p10 and p90 means the policy's disagreement varies
        more than the thing being measured, and a score derived from the median is then a
        weaker signal than the same number from a tight distribution. Reported rather than
        refused: a wide distribution is a real property of a policy, not a failed
        measurement.
        """
        return self.p90 <= self.p10 * 10.0 if self.p10 > 0 else False

    def ask_rate(self, threshold: float) -> float:
        """Fraction of these predictions that would have raised a hand at `threshold`.

        The number that connects the scale to the threshold, and it is not obvious from
        either alone. `estimate_from_samples` scores a chunk `1 / (1 + spread / reference)`,
        so a step scores below the threshold exactly when its spread exceeds
        `reference * (1/threshold - 1)`. With the reference at the median, **a threshold of
        0.5 asks on half of everything** — which is not a defect in the scale or in the
        threshold, but is what the two mean together, and nobody would find it out except
        by running and getting an episode that stopped at step zero.

        Measured against the observations, not derived from an assumed distribution: these
        are the spreads that actually occurred.
        """
        if not 0.0 < threshold < 1.0:
            return 0.0
        reference = self.reference_spread
        if reference <= 0:
            return 0.0
        limit = reference * (1.0 / threshold - 1.0)
        return sum(1 for value in self.spreads if value > limit) / self.samples


def from_spreads(
    spreads: list[float], *, skill: str, body: str, policy: str, measured_at: str
) -> Calibration:
    """Turn measured spreads into a reference, or refuse to.

    Raises `ValueError` below `MINIMUM_SAMPLES`, because the alternative is a number that
    looks like a measurement and is not — which is the failure this whole module exists to
    remove, reintroduced one level up.
    """
    usable = [value for value in spreads if value > 0]
    if len(usable) < MINIMUM_SAMPLES:
        raise ValueError(
            f"{len(usable)} usable spread(s); at least {MINIMUM_SAMPLES} are needed before "
            f"a median means anything. A deterministic policy produces none at all: its "
            f"samples are identical, so there is no disagreement to measure."
        )

    return Calibration(
        skill=skill,
        body=body,
        policy=policy,
        spreads=tuple(sorted(usable)),
        measured_at=measured_at,
    )


def _safe(part: str) -> str:
    """A filename component. `grasp/cube-sim` and `mujoco:arm` both contain characters
    Windows refuses in a path, and a store that works on one platform is not a store."""
    return "".join(
        character if character.isalnum() or character in "-." else "_" for character in part
    )


def calibration_path(root: Path, skill: str, body: str) -> Path:
    return Path(root) / f"{_safe(skill)}__{_safe(body)}.json"


def save(root: Path, calibration: Calibration) -> None:
    """Write a measurement, atomically.

    Temporary file and a move, because the alternative is a truncated file where a
    calibration used to be — and `load` would then correctly decide it has no scale,
    silently discarding a measurement that cost hundreds of forward passes.
    """
    path = calibration_path(root, calibration.skill, calibration.body)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {"format": _FORMAT, **asdict(calibration)}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    temporary.replace(path)


def load(root: Path, skill: str, body: str) -> Calibration | None:
    """Read a measurement, or None.

    Never raises. None means "no scale", which `services/confidence.py` already handles by
    reporting `NONE` rather than a number — the safe direction. A calibration that cannot
    be read must not become a calibration that is guessed.
    """
    path = calibration_path(root, skill, body)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not isinstance(raw, dict) or raw.get("format") != _FORMAT:
        return None

    try:
        spreads = tuple(sorted(float(value) for value in raw["spreads"]))
        if not spreads:
            return None
        return Calibration(
            skill=str(raw["skill"]),
            body=str(raw["body"]),
            policy=str(raw["policy"]),
            spreads=spreads,
            measured_at=str(raw["measured_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
