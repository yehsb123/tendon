"""Demonstration distance — a second confidence source, from outside the policy.

`services/confidence.py` has one estimator: sample the policy several times and call the
spread uncertainty. ADR 0003 says that is the only source there is, and its own docstring
names the hole: **a confidently wrong policy** samples tightly around the wrong motion and
reports high confidence. Sampling also needs a stochastic policy and several forward passes
per chunk, and a deterministic one cannot use it at all.

This asks a different question, from data the loop already produces: *has anything like
this chunk, from about here, ever worked?* Index the action chunks from successful
episodes; at run time, find the nearest one that started from a similar state, and measure
how far the predicted chunk is from it. Far means the policy is about to do something no
success did, and that is a reason to slow down and ask. It needs no sampling and nothing
from inside the policy — only the chunk it was going to execute anyway — so it works for a
deterministic policy and for a checkpoint nobody can open. ADR 0007.

## What is compared

A window is `horizon` consecutive actions from one episode, with the state the first was
issued from. Two windows are compared as two things, both in joint space:

- **where the arm was**: the start states, RMS per joint
- **what it did from there**: each chunk relative to its own start state, RMS per joint
  and step

combined as `sqrt(state**2 + motion**2)`. Comparing raw chunks would ignore where the arm
is: the same joint targets are a small adjustment from one state and a lunge from another,
and a guard that could not tell those apart would be measuring nothing. Splitting the two
also lets the reasons say which one is unfamiliar — *no success started near here* is a
different situation from *successes from here moved differently*, and an operator deciding
in two seconds needs to know which.

Joint-position actions only. The state is joint positions, and subtracting joint angles
from a pose in metres would produce a number with no meaning. The jaw is not compared:
`Action.gripper` is a 0–1 command against radians, and weighting the two is a choice this
module has no data to make.

## Which episodes count

An episode is indexed when the evaluator judged it a success (`succeeded is True`) and no
operator took over (`interventions == 0`) — the two fields `progress.EpisodeRecord` already
carries. Nothing new is labelled here. Unknown is not success: an episode the rig could not
judge, or one where nobody can say whether a human stepped in, is left out and listed in
`excluded` with the reason.

**Episodes with interventions are left out, including their corrections.** The corrections
are recovery data, the most valuable rows in the store, and leaving them out is a decision
rather than an oversight. Before the handover the trajectory is the policy's own motion
that somebody stopped; after it, the operator's. The sidecar records the frame an interrupt
was raised at, not where the correction ended, so the store cannot yet separate the two —
and indexing the motion an operator rejected as "like a success" would teach this guard to
wave through exactly what needed a human. Corrections are not lost: `adaptive.
CorrectionMemory` recalls them by state already.

From a store, `read_demonstrations` takes the verdict from the sidecar's `episodes` table
(`success`, and `interrupts` as the intervention count) and the states and actions from the
parquet, joined on `episode_index`. Episodes recorded before that table have no verdict and
are left out, not counted as failures.

## The scale

Distance has no absolute meaning for the same reason spread does, so it is scored against
what is typical, the way `calibration.py` scores spread: for every window, the distance to
the nearest window *from another episode*, and the median of those. Leave-one-episode-out
because a window's nearest neighbour in its own episode is the next step, a fraction of a
control period away, and that would measure the frame rate. Distances of exactly zero are
left out of the median, as `calibration.from_spreads` leaves out zero spreads: a home pose
every episode shares would otherwise make the scale zero. The score is
`1 / (1 + distance / reference)`, the formula `estimate_from_samples` uses, so a chunk as
far from the demonstrations as they typically are from each other scores 0.5 — and the
caveat `Calibration.ask_rate` states applies here unchanged: a threshold of 0.5 asks on
about half of what the demonstrations themselves would score.

## What it cannot see

**Unlike the demonstrations is not the same as wrong.** A novel motion that would work —
a better grasp, a recovery the demonstrations never needed — is flagged, because nothing
here can tell new-and-right from new-and-wrong. This guard can only ever say *unfamiliar*.

**A recovery is always unfamiliar.** Corrections are left out (above), so a policy that has
learned to recover is flagged at the moment it recovers. Until the store records where a
correction ends, that is the price of not indexing rejected motion.

**A wrong motion that is in the demonstrations passes.** Success is judged per episode, so
an episode that hesitated, overshot and still met the criteria contributes every one of its
windows. The guard is as good as the success criteria and no better.

**Coverage is the number of demonstrations.** With few of them most of the workspace is
far from all of them, everything scores low, and an operator learns to approve without
looking — ADR 0003's warning about defaulting to 0.0, arrived at by a different road. The
minimums below refuse to answer rather than answer from too little, and they are floors,
not evidence of enough.

**It is not the sample-spread estimator and does not replace it.** The two are blind in
different places: spread misses a confidently wrong policy, distance misses a familiar
mistake. When both are measured, a chunk should hand over if *either* is below the
threshold — never an average, which lets one confident signal hide the other's alarm.
`combine` is that rule. Nothing in the runtime calls it yet; ADR 0007 says what has to be
decided first.

## Cost

Brute force with numpy, no index structure. Building is quadratic in windows (the
leave-one-out scale compares every window with every other); a query is linear. Three
thousand windows of six joints and ten steps build in a fifth of a second on a laptop.
Past that the answer is fewer windows, not a vector database.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from tendon.kernel.types import Action, ActionSpace, Confidence, ConfidenceSource
from tendon.services.calibration import MINIMUM_SAMPLES

__all__ = [
    "MINIMUM_EPISODES",
    "MINIMUM_WINDOWS",
    "Demonstration",
    "DemonstrationIndex",
    "Nearest",
    "combine",
    "estimate_from_demonstrations",
    "index_from_store",
    "read_demonstrations",
]

#: Below this a median is not a distribution. The same floor `calibration.py` sets for
#: spreads, for the same reason: a reference from a handful of windows looks
#: authoritative and is not.
MINIMUM_WINDOWS = MINIMUM_SAMPLES

#: Leave-one-episode-out needs another episode to leave the window out to.
MINIMUM_EPISODES = 2

#: Distances per block when comparing every window with every other. 32 MB of float64,
#: so building stays inside memory a laptop has while still vectorising.
_BLOCK_ELEMENTS = 1 << 22


@dataclass(frozen=True)
class Demonstration:
    """One recorded episode, as much of it as the index needs.

    `states[t]` is the joint position the arm was at when `actions[t]` was issued — the
    pairing the recorder writes as `observation.state` and `action`.
    """

    episode_id: str
    states: Sequence[Sequence[float]]
    actions: Sequence[Action]
    #: The evaluator's verdict. None when the rig could not judge it.
    succeeded: bool | None
    #: How many times an operator took over. None when the store cannot say.
    interventions: int | None


@dataclass(frozen=True)
class Nearest:
    """The closest indexed window to a chunk, and how far it is."""

    distance: float
    #: How far apart the start states were [rad, RMS per joint].
    state_part: float
    #: How differently the two moved from their start states [rad, RMS per joint and step].
    motion_part: float
    episode_id: str
    #: Index of the window's first action within its episode.
    step: int


class DemonstrationIndex:
    """Windows from successful, unaided episodes, and the distance typical between them.

    Build with `DemonstrationIndex.build`. Immutable after that: an index that changed
    under a running episode would change the scale its scores were read against.
    """

    def __init__(
        self,
        *,
        horizon: int,
        dof: int | None,
        states: np.ndarray,
        motions: np.ndarray,
        owners: tuple[str, ...],
        steps: tuple[int, ...],
        episodes: tuple[str, ...],
        excluded: tuple[tuple[str, str], ...],
    ) -> None:
        self.horizon = horizon
        self.dof = dof
        self._states = states
        self._motions = motions
        self._owners = owners
        self._steps = steps
        self.episodes = episodes
        self.excluded = excluded
        self.reference_distance, self.unscaled_because = self._scale()

    @classmethod
    def build(cls, demonstrations: Sequence[Demonstration], *, horizon: int) -> DemonstrationIndex:
        """Index every window of `horizon` steps from the episodes that qualify.

        Raises `ValueError` on data that cannot be compared — mismatched lengths, a width
        that differs between episodes, an action space other than joint position. Those
        are malformed demonstrations, not unsuccessful ones, and skipping them quietly
        would shrink the index without anybody knowing why.
        """
        if horizon < 1:
            raise ValueError(f"horizon must be at least 1, got {horizon}")

        dof: int | None = None
        states: list[np.ndarray] = []
        motions: list[np.ndarray] = []
        owners: list[str] = []
        steps: list[int] = []
        admitted: list[str] = []
        excluded: list[tuple[str, str]] = []

        for demo in demonstrations:
            refused = _why_excluded(demo)
            if refused is not None:
                excluded.append((demo.episode_id, refused))
                continue
            if not demo.states and not demo.actions:
                # Succeeded unaided and holds no window; nothing to say about its width.
                admitted.append(demo.episode_id)
                continue

            state, action = _arrays(demo)
            if dof is None:
                dof = state.shape[1]
            elif state.shape[1] != dof:
                raise ValueError(
                    f"demonstration {demo.episode_id} is {state.shape[1]} joints wide; "
                    f"earlier ones are {dof}"
                )

            admitted.append(demo.episode_id)
            for start in range(len(action) - horizon + 1):
                states.append(state[start])
                motions.append(action[start : start + horizon] - state[start])
                owners.append(demo.episode_id)
                steps.append(start)

        width = dof or 0
        return cls(
            horizon=horizon,
            dof=dof,
            states=np.array(states, dtype=float).reshape(len(states), width),
            motions=np.array(motions, dtype=float).reshape(len(motions), horizon, width),
            owners=tuple(owners),
            steps=tuple(steps),
            episodes=tuple(admitted),
            excluded=tuple(excluded),
        )

    @property
    def windows(self) -> int:
        return len(self._owners)

    def nearest(self, state: Sequence[float], actions: Sequence[Action]) -> Nearest | None:
        """The closest window to this chunk issued from this state, or None if empty.

        Raises `ValueError` when the chunk cannot be compared at all; see
        `estimate_from_demonstrations`, which answers `NONE` for those instead.
        """
        refused = self._why_incomparable(state, actions)
        if refused is not None:
            raise ValueError(refused)
        if self.windows == 0:
            return None

        here = np.asarray(state, dtype=float)
        motion = np.array([a.values for a in actions[: self.horizon]], dtype=float) - here

        dof = self._states.shape[1]
        # Finite inputs can still square past float range. That comes back as an infinite
        # distance, which `estimate_from_demonstrations` turns into NONE, so the warning
        # numpy would print says nothing the result does not.
        with np.errstate(over="ignore"):
            state_sq = ((self._states - here) ** 2).sum(axis=1) / dof
            motion_sq = ((self._motions - motion) ** 2).sum(axis=(1, 2)) / (dof * self.horizon)
        best = int(np.argmin(state_sq + motion_sq))

        return Nearest(
            distance=math.sqrt(float(state_sq[best] + motion_sq[best])),
            state_part=math.sqrt(float(state_sq[best])),
            motion_part=math.sqrt(float(motion_sq[best])),
            episode_id=self._owners[best],
            step=self._steps[best],
        )

    def _why_incomparable(self, state: Sequence[float], actions: Sequence[Action]) -> str | None:
        if len(actions) < self.horizon:
            return f"chunk has {len(actions)} step(s); the index compares windows of {self.horizon}"
        compared = actions[: self.horizon]
        if any(a.space is not ActionSpace.JOINT_POSITION for a in compared):
            return "chunk is not joint positions, so it cannot be compared with a joint state"
        values = [*state, *(v for a in compared for v in a.values)]
        if not all(math.isfinite(v) for v in values):
            # Checked before anything is subtracted: one NaN makes every distance NaN,
            # `argmin` returns the first window, and `min(1.0, nan)` is 1.0 -- a sensor
            # dropout scored as full confidence, the one case that must hand over.
            return "chunk or state holds a value that is not finite, so it cannot be measured"
        if self.dof is not None:
            widths = {len(state), *(len(a.values) for a in compared)}
            if widths != {self.dof}:
                return f"chunk is {max(widths)} joints wide; the demonstrations are {self.dof}"
        return None

    def _scale(self) -> tuple[float | None, str | None]:
        """Median distance from each window to the nearest window of another episode."""
        if self.windows == 0:
            return None, "no successful demonstrations are indexed"

        distinct = len(set(self._owners))
        if distinct < MINIMUM_EPISODES:
            return None, (
                f"{distinct} episode(s) indexed; at least {MINIMUM_EPISODES} are needed to "
                f"measure how far demonstrations are from each other"
            )
        if self.windows < MINIMUM_WINDOWS:
            return None, (
                f"{self.windows} window(s) indexed; at least {MINIMUM_WINDOWS} are needed "
                f"before a median distance means anything"
            )

        dof = self._states.shape[1]
        vectors = np.concatenate(
            [
                self._states / math.sqrt(dof),
                self._motions.reshape(self.windows, -1) / math.sqrt(dof * self.horizon),
            ],
            axis=1,
        )
        # Centred, which moves no distance. The expansion below subtracts two squared
        # norms; far from zero they are large, the difference between neighbours is lost
        # to rounding, and the wrong partner is chosen.
        vectors = vectors - vectors.mean(axis=0)
        _, owner = np.unique(np.array(self._owners), return_inverse=True)

        norms = (vectors**2).sum(axis=1)
        partner = np.empty(self.windows, dtype=int)
        rows = max(1, _BLOCK_ELEMENTS // self.windows)
        for begin in range(0, self.windows, rows):
            block = slice(begin, begin + rows)
            # |x|^2 + |y|^2 - 2xy, one matrix product per block: the direct difference
            # is a (rows, windows, width) array and took six seconds for three thousand
            # windows. The expansion only chooses the partner; its distance is computed
            # directly below, because the expansion leaves rounding residue where two
            # windows are identical, and identical demonstrations must measure zero, not
            # 1e-9, or "no scale" becomes a scale of nothing.
            squared = norms[block, None] + norms[None, :] - 2.0 * vectors[block] @ vectors.T
            squared[owner[block, None] == owner[None, :]] = np.inf
            partner[block] = squared.argmin(axis=1)
        nearest = np.sqrt(((vectors - vectors[partner]) ** 2).sum(axis=1))

        # Zero distances are left out, as `calibration.from_spreads` leaves out zero spreads.
        # A window with an exact twin in another episode -- a shared home pose, a replayed
        # approach -- says nothing about how far demonstrations typically are apart, and
        # counted, a home pose held for most of each episode makes the median zero.
        moving = nearest[nearest > 0.0]
        if moving.size == 0:
            return None, (
                "demonstrations are identical to each other, so distance from them has no scale"
            )
        if moving.size < MINIMUM_WINDOWS:
            return None, (
                f"{moving.size} window(s) differ from every other episode; at least "
                f"{MINIMUM_WINDOWS} are needed before a median distance means anything"
            )

        reference = float(np.median(moving))
        if not math.isfinite(reference):  # pragma: no cover - `_arrays` refuses non-finite
            return None, "the distance between demonstrations is not finite"
        return reference, None


def estimate_from_demonstrations(
    index: DemonstrationIndex,
    state: Sequence[float],
    actions: Sequence[Action],
) -> Confidence:
    """Score a chunk by its distance from the nearest successful demonstration.

    `state` is the joint position the chunk is issued from —
    `observation.proprio.joint_positions`. Returns `ConfidenceSource.NONE` with the reason
    whenever there is nothing to measure against: no index, too small an index, or a chunk
    that cannot be compared. Never a default score.
    """
    reference = index.reference_distance
    if reference is None:
        return _unmeasured(index.unscaled_because or "the index has no scale")

    refused = index._why_incomparable(state, actions)
    if refused is not None:
        return _unmeasured(refused)

    nearest = index.nearest(state, actions)
    if nearest is None:  # pragma: no cover - a scaled index has windows
        return _unmeasured("no successful demonstrations are indexed")

    ratio = nearest.distance / reference
    if not math.isfinite(ratio):
        # Every input was checked finite; an overflow is still not a measurement, and
        # `min(1.0, nan)` would score it as certainty.
        return _unmeasured("distance from the demonstrations is not finite")
    reasons: list[str] = []
    if ratio > 2.0:
        reasons.append(
            f"chunk is {ratio:.1f}x farther from the nearest successful demonstration than "
            f"demonstrations are from each other ({len(index.episodes)} episodes)"
        )
        if nearest.state_part >= nearest.motion_part:
            reasons.append(
                f"no successful demonstration started near this state "
                f"(nearest is episode {nearest.episode_id} at step {nearest.step}, "
                f"{nearest.state_part:.4f} rad away)"
            )
        else:
            reasons.append(
                f"demonstrations that started near here moved differently "
                f"(nearest is episode {nearest.episode_id} at step {nearest.step}, "
                f"{nearest.motion_part:.4f} rad apart)"
            )

    return Confidence(
        score=max(0.0, min(1.0, 1.0 / (1.0 + ratio))),
        source=ConfidenceSource.DEMONSTRATION,
        reasons=tuple(reasons),
    )


def combine(spread: Confidence, demonstration: Confidence | None) -> Confidence:
    """The rule from ADR 0007 for two sources: the lower measured score, never an average.

    The scheduler hands over when a score is strictly below the threshold, so returning the
    lower of two measured scores hands over exactly when either alone would have. An
    average would let a tight sample spread hide a chunk no success ever resembled, which
    is the failure the second source exists to catch.

    Returned as it was, not relabelled or merged: evaluation reports which estimator
    produced a rate (ADR 0003), and a score whose source named the other one would be
    filed under the wrong estimator.

    With no index, or one that cannot measure this chunk, `spread` comes back unchanged
    and is the same object. Adding a source that has nothing to say must not move a
    single decision the scheduler was already making. A tie keeps `spread` for the same
    reason.
    """
    if demonstration is None or not demonstration.is_measured:
        return spread
    if not spread.is_measured:
        # A deterministic policy has no spread. Distance needs no samples, which is half
        # of why it was added.
        return demonstration
    return demonstration if demonstration.score < spread.score else spread


def read_demonstrations(directory: Path) -> tuple[Demonstration, ...]:
    """Every episode in a dataset directory, with the verdict the sidecar holds for it.

    The parquet supplies `observation.state` and `action` per frame; the sidecar's
    `episodes` table supplies `success` and `interrupts`, joined on `episode_index`. Read
    with duckdb, as `episodes.read_episodes` reads, so building an index needs neither
    LeRobot nor torch.

    An episode with no row in `episodes` comes back with `succeeded` and `interventions`
    None: it was recorded before the verdict was written, or the sidecar is missing, and
    `DemonstrationIndex.build` leaves it out with the reason rather than guessing.

    Raises `EpisodeReadError` when the parquet cannot be read, as `read_episodes` does,
    and `ValueError` when the sidecar holds two verdicts for one episode, which no writer
    produces and which no choice between them would make right.
    """
    import duckdb

    from tendon.services.episodes import EpisodeReadError, _action, _has_gripper, _info

    files = sorted(str(p) for p in (directory / "data").rglob("*.parquet"))
    if not files:
        raise EpisodeReadError(f"no episode data under {directory / 'data'}")
    has_gripper = _has_gripper(_info(directory))
    verdicts = _verdicts(directory)

    con = duckdb.connect()
    try:
        rows = con.execute(
            'SELECT episode_index, "observation.state", action FROM read_parquet(?) '
            "ORDER BY episode_index, frame_index",
            [files],
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - duckdb raises its own hierarchy
        raise EpisodeReadError(f"could not read {directory}: {exc}") from exc
    finally:
        con.close()

    grouped: dict[int, tuple[list[list[float]], list[Action]]] = {}
    for episode_index, state, values in rows:
        states, actions = grouped.setdefault(int(episode_index), ([], []))
        states.append([float(v) for v in state])
        actions.append(_action(values, has_gripper))

    demonstrations = []
    for index, (states, actions) in sorted(grouped.items()):
        success, interrupts = verdicts.get(index, (None, None))
        demonstrations.append(
            Demonstration(
                episode_id=str(index),
                states=states,
                actions=actions,
                succeeded=success,
                interventions=interrupts,
            )
        )
    return tuple(demonstrations)


def index_from_store(directory: Path, *, horizon: int) -> DemonstrationIndex:
    """`DemonstrationIndex.build` over what `read_demonstrations` finds in `directory`."""
    return DemonstrationIndex.build(read_demonstrations(directory), horizon=horizon)


def _verdicts(directory: Path) -> dict[int, tuple[bool | None, int | None]]:
    """`episode_index` to `(success, interrupts)` from the sidecar, empty when it has none.

    Empty rather than an error for a missing sidecar or a missing table: both describe a
    store recorded before the verdict was written, and every episode in it is then
    unjudged, which `build` already reports per episode.
    """
    sidecar = directory / "tendon_sidecar.duckdb"
    if not sidecar.is_file():
        return {}

    import duckdb

    con = duckdb.connect(str(sidecar), read_only=True)
    try:
        present = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'episodes'"
        ).fetchone()
        if not present or present[0] == 0:
            return {}
        rows = con.execute(
            "SELECT episode_index, success, interrupts FROM episodes "
            "WHERE episode_index IS NOT NULL"
        ).fetchall()
    finally:
        con.close()

    verdicts: dict[int, tuple[bool | None, int | None]] = {}
    for index, success, interrupts in rows:
        if int(index) in verdicts:
            raise ValueError(
                f"{sidecar} holds more than one verdict for episode_index {index}; "
                f"which one is true cannot be read from the store"
            )
        verdicts[int(index)] = (success, None if interrupts is None else int(interrupts))
    return verdicts


def _unmeasured(reason: str) -> Confidence:
    return Confidence(score=0.0, source=ConfidenceSource.NONE, reasons=(reason,))


def _why_excluded(demo: Demonstration) -> str | None:
    if demo.succeeded is None:
        return "success was not measured"
    if not demo.succeeded:
        return "did not succeed"
    if demo.interventions is None:
        return "whether an operator took over is not known"
    if demo.interventions > 0:
        return f"an operator took over {demo.interventions} time(s)"
    return None


def _arrays(demo: Demonstration) -> tuple[np.ndarray, np.ndarray]:
    if len(demo.states) != len(demo.actions):
        raise ValueError(
            f"demonstration {demo.episode_id} has {len(demo.states)} states and "
            f"{len(demo.actions)} actions; each action needs the state it was issued from"
        )
    if any(a.space is not ActionSpace.JOINT_POSITION for a in demo.actions):
        raise ValueError(
            f"demonstration {demo.episode_id} is not joint positions; the index compares "
            f"motion in joint space"
        )

    state = np.array([list(s) for s in demo.states], dtype=float)
    action = np.array([a.values for a in demo.actions], dtype=float)
    if len(demo.states) and (state.ndim != 2 or action.shape != state.shape):
        raise ValueError(
            f"demonstration {demo.episode_id} has states {state.shape} and actions "
            f"{action.shape}; they must be the same width"
        )
    if not (np.isfinite(state).all() and np.isfinite(action).all()):
        # Malformed, not unsuccessful. One NaN makes the median NaN, `NaN <= 0` is False,
        # and the index would count as scaled while scoring every chunk 1.0.
        raise ValueError(
            f"demonstration {demo.episode_id} holds a value that is not finite; a dropout "
            f"in a recording has to be cleaned or the episode left out"
        )
    return state.reshape(len(demo.states), -1), action.reshape(len(demo.actions), -1)
