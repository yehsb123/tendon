"""Choosing and building the policy a command will run.

Split out of `cli/main.py`. Eight functions, three hundred lines, and one question: given
a skill, a body and a `--policy` string, what runs? Every command that moves a body asks
it, and asking it in one place is the reason `run` and `eval` cannot disagree about what
"the baseline" means — they did, once, and the same bug shipped twice.

Nothing here prints a result. Refusals are printed because a refusal *is* the result.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from tendon.cli import observers

#: Jaw position the baseline policy holds. Open, because a scripted sweep is not grasping
#: anything and a jaw closing on nothing is the more surprising default.
HELD_OPEN = 1.0

#: Policy names this build can run. One set, consulted by the name check and named in the
#: refusal, so the list a person is shown cannot drift from the list that is accepted.
RUNNABLE_POLICIES = frozenset({"scripted", "replay", "adapter"})

#: Reference spread for a loaded checkpoint that has never been measured: none.
#:
#: Zero is not a disabled feature. `services/confidence.py` answers it with
#: `ConfidenceSource.NONE` and the reason "no reference spread configured, so the
#: measurement has no scale", which is what an operator should be told. A guessed number
#: would produce a confident-looking score with nothing behind it, and this project's whole
#: interrupt path keys off that score. `tendon calibrate` is how a real one is obtained.
UNCALIBRATED_SPREAD = 0.0


def choose_policy(
    console: Console,
    loaded,
    capability,
    policy: str,
    store: str,
    adapter: str = "",
    body=None,
    driver_name: str = "",
):
    """Build whichever policy `--policy` asked for.

    One function because `run` and `eval` both take the choice and this project has shipped
    the same bug twice from two copies of a policy construction. `eval` had no choice at all
    until now, which was its own version of the problem: `ReplayPolicy` describes itself as
    the fixed baseline *every evaluation* needs, and evaluation was the one command that
    could not use it.
    """
    check_policy_name(console, loaded, policy, adapter)

    if policy == "scripted":
        _warn_about_an_ignored_adapter(console, loaded)
        return _baseline_policy(loaded, capability)

    if policy == "adapter" or policy.startswith("adapter:"):
        return _adapter_policy(
            console,
            loaded,
            capability,
            policy.partition(":")[2],
            adapter,
            body,
            driver_name,
        )

    return _replay_policy(console, loaded, capability, policy.partition(":")[2], store)


def check_policy_name(console: Console, loaded, policy: str, adapter: str = "") -> None:
    """Refuse a `--policy` this build cannot run, using only the skill and the string.

    Split out of `_choose_policy` so it can be called before a body is opened. Building a
    policy needs the body's `Capability`; deciding whether the *name* is one we can run
    does not, and running that check second meant `--policy scriptd` opened a body — with
    `--physical`, a real arm — before saying the name was misspelled.

    Still called from `_choose_policy` as well, so the set of runnable names is written
    down once. A second copy that drifted would refuse a name one command accepts.
    """
    if policy == "adapter" or policy.startswith("adapter:"):
        # Resolved here, before a body exists, and thrown away: this call is for its
        # refusals. Which adapter and which base are questions about the skill and two
        # strings, and answering them after `open_body` would mean a serial port opened to
        # be told a path was misspelled.
        resolve_adapter(console, loaded, policy.partition(":")[2], adapter)
        return

    if policy in RUNNABLE_POLICIES or policy.partition(":")[0] in RUNNABLE_POLICIES:
        return

    console.print(f"[red]policy {escape(policy)!r} is not available yet.[/red]")
    console.print(
        f"[dim]{', '.join(sorted(RUNNABLE_POLICIES))} run today. "
        "replay: takes <skill>#<episode>, adapter: takes a directory.[/dim]"
    )
    raise typer.Exit(code=1)


def _warn_about_an_ignored_adapter(console: Console, loaded) -> None:
    """Say when a skill names an adapter that this run is not using.

    `skill.yaml` carries a `policy.adapter` field, commented in the file itself as "a LoRA
    adapter appears here after `tendon train`". Nothing reads it. So somebody could train
    an adapter, write its path in exactly where the format tells them to, run the skill,
    and get the scripted baseline — with one word of output, `via scripted`, standing
    between them and the belief that they were watching their own model.

    Not a refusal. Running the baseline on a skill that has weights is legitimate and is
    how every evaluation gets its control arm. What is not legitimate is doing it silently
    while the file says otherwise.
    """
    if getattr(loaded, "policy_adapter", None):
        console.print(
            f"[yellow]not using the adapter this skill names[/yellow] "
            f"[dim]({escape(str(loaded.policy_adapter))}). Running the scripted baseline. "
            f"To run the adapter: --policy adapter[/dim]"
        )


def resolve_adapter(console: Console, loaded, spec: str, adapter: str) -> tuple[Path, str]:
    """Which adapter, and which checkpoint it belongs to, using no body.

    Everything here is decidable from the skill and two strings, so it runs before
    `open_body` — with `--physical` that is a real arm, and opening one to then say a path
    is misspelled is the defect this project already fixed once for policy names.
    `bodies.py` argues the rule against itself: deciding whether to touch the hardware
    should not require touching it.

    Precedence is `--adapter`, then `--policy adapter:<path>`, then `skill.yaml`'s
    `policy.adapter`. Anything explicit beats the file, because the reason to type a path
    is that it is not the one the file names.

    Called twice — once early to validate, once inside `_adapter_policy` to use — rather
    than passed along. It reads two strings and one JSON file; a second parameter threaded
    through three signatures to save that is a worse trade than the work.
    """
    from tendon.services.policy_lerobot import PolicyError, adapter_base

    path = adapter or spec or (loaded.policy_adapter or "")
    if not path:
        console.print("[red]no adapter to run.[/red]")
        console.print(
            f"[dim]train one: tendon train {escape(loaded.ref)}. Then pass --adapter "
            "<path>, or set policy.adapter in skill.yaml.[/dim]"
        )
        raise typer.Exit(code=1)

    directory = Path(path).expanduser()
    if not (directory / "adapter_config.json").is_file():
        # The file rather than the directory: a path that exists but holds no adapter is
        # the common mistake — a store, a checkpoint, or the parent of the right directory
        # — and "not found" for a path that is plainly there reads as a bug in the tool.
        console.print(f"[red]no adapter at {escape(str(directory))}[/red]")
        console.print("[dim]expected adapter_config.json there, as `tendon train` writes it[/dim]")
        raise typer.Exit(code=1)

    try:
        base = adapter_base(directory)
    except PolicyError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    if loaded.policy_base and base and loaded.policy_base != base:
        console.print(
            f"[red]this adapter was trained on {escape(base)}, and "
            f"{escape(loaded.ref)} declares {escape(loaded.policy_base)}.[/red]"
        )
        console.print(
            "[dim]A LoRA is a delta against particular weights. On a different base it "
            "loads, runs, and is wrong. Fix policy.base, or point --adapter at the "
            "adapter trained for it.[/dim]"
        )
        raise typer.Exit(code=1)

    return directory, base


def _adapter_policy(
    console: Console, loaded, capability, spec: str, adapter: str, body=None, driver_name: str = ""
):
    """Build the policy a `tendon train` run produced.

    The adapter comes from `--adapter`, then `--policy adapter:<path>`, then `skill.yaml`'s
    `policy.adapter` — the last being the field the format documents as "a LoRA adapter
    appears here after `tendon train`". Anything explicit beats the file, because the
    reason to type a path is that it is not the one the file names.

    The base checkpoint is read from the adapter's own `adapter_config.json` rather than
    from `skill.yaml`. A LoRA is a delta against particular weights: applied to a different
    base it loads, runs, and is wrong, with no error anywhere. The skill's `policy.base` is
    compared against it and a disagreement is refused rather than resolved, because
    resolving it means choosing which of two stated intentions to ignore.

    `policy.hz` is passed through. `LeRobotPolicy` refuses a rate it cannot reconcile with
    the body's, and that refusal is the point: a chunk played at the wrong rate is a
    trajectory nobody trained.
    """
    from tendon.kernel.protocols import RendersFrames
    from tendon.kernel.types import GripperKind
    from tendon.services.policy_lerobot import (
        PolicyError,
        declared_image_features,
        load_adapter,
    )

    directory, base = resolve_adapter(console, loaded, spec, adapter)

    # What the body is actually rendering, against what the checkpoint says it needs, both
    # read before any weights are fetched. `declared_image_features` reads a JSON config;
    # `_video_schema` calls `render()` once. Getting this wrong costs minutes of loading
    # and then fails inside the model with "All image features are missing from the batch",
    # which names neither the body nor the flag that would have supplied them.
    cameras, _ = observers.video_schema(body) if body is not None else ((), (0, 0))
    try:
        needed = declared_image_features(base)
    except PolicyError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    if needed and not cameras:
        console.print(f"[red]{escape(base)} needs {len(needed)} camera input(s) and this[/red]")
        console.print("[red]body is rendering none, so no batch it produces can be used.[/red]")
        if capability.cameras:
            from tendon.services.bodies import camera_parameter

            parameter = camera_parameter(driver_name)
            flag = f"--driver-arg {parameter}=" if parameter else "--driver-arg <cameras>="
            console.print(
                f"[dim]this body has {', '.join(capability.cameras)}. "
                f"Render one: {flag}{capability.cameras[0]}[/dim]"
            )
        else:
            console.print("[dim]this body declares no cameras at all[/dim]")
        raise typer.Exit(code=1)

    # Said before the run, not buried in the report afterwards. The interrupt path keys off
    # confidence, so an operator watching this needs to know whether the policy can raise
    # its own hand at all — and when it cannot, that anything happening here is a safety
    # trip or their own decision.
    from tendon.services.calibration import DEFAULT_CALIBRATION_ROOT
    from tendon.services.calibration import load as load_calibration

    measured = load_calibration(DEFAULT_CALIBRATION_ROOT, loaded.ref, capability.body_id)
    spread = UNCALIBRATED_SPREAD

    if measured is None:
        console.print(
            "[yellow]no measured scale for this policy on this body[/yellow] [dim]- no "
            "score will be reported and the policy cannot raise an interrupt. "
            f"Measure one: tendon calibrate {escape(loaded.ref)}[/dim]"
        )
    elif measured.policy != f"{base}+{directory.name}":
        # A reference measured from one policy says nothing about another. Refusing to use
        # it beats using it: a stale scale produces confident-looking scores on the wrong
        # units, and the interrupt threshold is read against them.
        console.print(
            f"[yellow]the measured scale is for {escape(measured.policy)}[/yellow] "
            f"[dim]and this is a different policy, so it is not being used. "
            f"Re-measure: tendon calibrate {escape(loaded.ref)}[/dim]"
        )
    else:
        spread = measured.reference_spread
        console.print(
            f"[dim]reference spread {spread:.6f}, measured over {measured.samples} "
            f"predictions on {escape(measured.measured_at)}[/dim]"
        )

    console.print(f"[dim]loading {escape(base)} + adapter from {escape(str(directory))}[/dim]")

    try:
        return load_adapter(
            directory,
            task=loaded.summary or loaded.ref,
            dof=capability.dof,
            control_hz=capability.control_hz,
            policy_hz=loaded.policy_hz,
            reference_spread=spread,
            has_gripper=capability.gripper is not GripperKind.NONE,
            # The pixels. `services` may not import `drivers` and an `Observation` carries
            # frame references rather than arrays, so a caller that wants image-conditioned
            # prediction has to hand over the source — the same injection the recorder
            # takes, for the same reason. Omitting it is not an error anywhere: the policy
            # simply predicts from state alone, which for a VLA is a different policy.
            frames=body.render if isinstance(body, RendersFrames) else None,
        )
    except PolicyError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc


def _replay_policy(console: Console, loaded, capability, spec: str, store: str):
    """Play a recorded episode back, from `replay:<skill>` or `replay:<skill>#<index>`.

    `ReplayPolicy` has existed and been tested since early on, described in its own module
    as "the fixed baseline every evaluation needs: a run whose behaviour cannot drift". The
    `--policy` help has advertised `replay:` for as long. Nothing called it, and the format
    the help named — `<episode.json>` — was never written by anything: the store holds
    LeRobotDataset parquet.

    So the spec names a skill and an episode in the store rather than a file, which is where
    recordings actually are, and `services/episodes` already reads them.
    """
    from tendon.services.episodes import EpisodeReadError, read_episodes
    from tendon.services.policies import ReplayPolicy
    from tendon.services.store import DEFAULT_ROOT

    reference, _, index_text = spec.partition("#")
    reference = reference or loaded.ref

    try:
        index = int(index_text) if index_text else 0
    except ValueError as exc:
        raise typer.BadParameter(f"{index_text!r} is not an episode number") from exc

    root = Path(store) if store else DEFAULT_ROOT
    try:
        episodes = read_episodes(root / reference.replace("/", "__"))
    except EpisodeReadError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        console.print(f"[dim]record some first: tendon run {escape(reference)}[/dim]")
        raise typer.Exit(code=1) from exc

    if index >= len(episodes):
        console.print(
            f"[red]{escape(reference)} has {len(episodes)} episodes; "
            f"there is no episode {index}[/red]"
        )
        raise typer.Exit(code=1)

    episode = episodes[index]
    console.print(
        f"[dim]replaying {escape(reference)} episode {index}, {len(episode.actions)} steps[/dim]"
    )
    return ReplayPolicy(
        episode.actions,
        # The rate the episode was recorded at, not the rate this body runs at. A replay
        # played at a different rate is a different motion, and the shell would draw a
        # trajectory over the wrong span.
        control_hz=1.0 / episode.dt_s,
        name=f"replay:{reference}#{index}",
    )


def _baseline_policy(loaded, capability):
    """The scripted policy both `run` and `eval` use when no model is loaded.

    One function because there were two copies, and only one of them was fixed. `run`
    learned to command the jaw of a body that has one — without it the recorder's schema
    is a channel wider than the action and every episode dies at step 0 — and `eval` kept
    the old constructor. The bug was repaired and still present, in the command that runs
    thirty episodes instead of one.

    Which policy depends on what the skill declares. `policy.baseline` names something that
    attempts the task; without it there is nothing to attempt and the fallback is a joint
    sweep. That distinction is the difference between evaluating a skill and evaluating a
    motion that happens to run on the same body — `tendon eval grasp/cube-sim` reported an
    intervention rate and failure modes for a sweep that never reached for the cube.
    """
    from tendon.services.policies import FunctionPolicy, sine_sweep

    if loaded.policy_baseline:
        return _named_baseline(loaded, capability)

    return FunctionPolicy(
        sine_sweep(dof=capability.dof),
        control_hz=capability.control_hz,
        dof=capability.dof,
        name=loaded.ref,
        # A body with a jaw has to be told what the jaw is doing, even by a baseline that
        # only sweeps one joint. Held open: this policy has no notion of grasping
        # anything, and a jaw that closes on nothing is the more surprising default.
        gripper=HELD_OPEN if capability.gripper.value != "none" else None,
    )


#: Baselines a skill can name in `policy.baseline`. Deliberately a small closed set rather
#: than an import path: a skill file naming a Python object would let a downloaded skill
#: choose what code runs, and skills are meant to be shareable (v0.4).
_BASELINES = {"cube-pick"}


def _named_baseline(loaded, capability):
    """Build the baseline a skill asked for by name."""
    from tendon.services.policy_scripted import CUBE_PICK, ScriptedPolicy

    if loaded.policy_baseline not in _BASELINES:
        raise typer.BadParameter(
            f"{loaded.ref} asks for baseline {loaded.policy_baseline!r}, which this tendon "
            f"does not have. Known: {sorted(_BASELINES)}"
        )

    return ScriptedPolicy(
        name=f"{loaded.ref}/baseline",
        stages=CUBE_PICK,
        control_hz=capability.control_hz,
    )
