"""The tendon command.

Mirrors the OS metaphor so the commands are guessable. Every one of them must work
against the MuJoCo driver with no hardware attached.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from tendon import __version__
from tendon.cli.doctor import Status, run_checks, summarise

#: Repeatable driver arguments. Defined once at module level because a `typer.Option`
#: call in a default is a mutable default in disguise, and ruff is right to flag it.
_DRIVER_ARG_OPTION = typer.Option(
    [],
    "--driver-arg",
    help=(
        "Pass key=value to the driver, e.g. --driver-arg port=COM3. Repeatable. "
        "Values are converted to whatever the driver's signature declares, so a "
        "sequence takes a comma-separated list: --driver-arg render_cameras=wrist,scene"
    ),
)

app = typer.Typer(
    name="tendon",
    help="The operating layer for physical AI.",
    no_args_is_help=True,
)


def _not_yet(command: str, milestone: str, detail: str) -> None:
    """Say a command is not available yet, without a traceback.

    A `NotImplementedError` reaching a user is the tool telling them its own source is
    incomplete, in a format meant for whoever wrote it. They asked what the command does;
    the useful answer is when it will do it and what is already there.
    """
    console = Console()
    console.print(f"[yellow]{escape(command)} is not available yet[/yellow] ({milestone})")
    console.print(f"[dim]{escape(detail)}[/dim]")
    console.print("[dim]See docs/roadmap.md for what each milestone has to show.[/dim]")
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the tendon version."""
    typer.echo(__version__)


_STATUS_STYLE = {
    Status.OK: ("ok", "green"),
    Status.LIMITED: ("limited", "yellow"),
    Status.BLOCKED: ("blocked", "red"),
}


@app.command()
def doctor() -> None:
    """Check what works here, and what each missing piece costs.

    Read-only and touches no hardware, so it is safe to run on a machine with a robot
    attached. Exits non-zero when something is blocking, so it can gate a script.
    """
    console = Console()
    checks = run_checks()

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("")
    table.add_column("check")
    table.add_column("detail")

    for check in checks:
        label, colour = _STATUS_STYLE[check.status]
        table.add_row(f"[{colour}]{label}[/{colour}]", check.name, escape(check.detail))

    console.print(table)

    remedies = [c for c in checks if c.remedy]
    if remedies:
        console.print()
        for check in remedies:
            console.print(f"  [dim]{check.name}:[/dim] {escape(check.remedy)}")

    overall, message = summarise(checks)
    console.print()
    console.print(escape(message))

    if overall is Status.BLOCKED:
        raise typer.Exit(code=1)


#: Jaw position the baseline policy holds. Open, because a scripted sweep is not grasping
#: anything and a jaw closing on nothing is the more surprising default.
_HELD_OPEN = 1.0


@app.command()
def run(
    skill: str = typer.Argument(
        ..., help="Skill reference (grasp/cube-sim) or a path to a skill directory"
    ),
    driver: str = typer.Option("mujoco", help="Which body to run on"),
    policy: str = typer.Option(
        "scripted",
        help=(
            "scripted | replay:<skill>#<episode> | the skill's own policy. "
            "replay: takes a recording from the store, not a file - nothing writes "
            "episode JSON."
        ),
    ),
    steps: int = typer.Option(500, help="Maximum control steps"),
    seed: int | None = typer.Option(None, help="Seed the body for a repeatable start"),
    physical: bool = typer.Option(
        False,
        "--physical",
        help="Allow a body that moves real hardware. Read SECURITY.md first.",
    ),
    driver_arg: list[str] = _DRIVER_ARG_OPTION,
    store: str = typer.Option(
        "", help="Where episodes are written. Defaults to ~/.tendon/episodes"
    ),
    view: bool = typer.Option(
        False, "--view", help="Open a Rerun viewer and stream this run into it."
    ),
    view_save: str = typer.Option(
        "", help="Write a Rerun recording here (.rrd) instead of, or as well as, viewing."
    ),
) -> None:
    """Run a policy on a body under the kernel.

    Loads the skill, checks it against the body before anything moves, and executes one
    episode. The run is recorded. There is no flag for that - design decision 1 - and the
    only thing `--store` changes is where it lands.

    For most of this project's life that was not true. The bus was created and handed to
    the scheduler, and nothing ever subscribed to it, so `tendon run` completed and the
    store stayed empty. The milestone this command is the acceptance test for reads
    "`tendon run` executes a policy in simulation and episodes appear".
    """
    console = Console()

    from tendon.kernel.bus import Bus
    from tendon.kernel.scheduler import Scheduler, StepRecord
    from tendon.services.bodies import (
        BodyUnavailable,
        MissingDriverArgument,
        PhysicalBodyRefused,
        open_body,
    )
    from tendon.services.skill import IncompatibleBody, SkillError, load_skill, require_compatible

    try:
        loaded = load_skill(skill)
    except SkillError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    # Before the body, not after. Whether `--policy` names something this build can run is
    # a question about the skill and a string, and answering it second meant a typo opened
    # a body first - with `--physical`, a real arm, to then be told the policy name was
    # misspelled. `bodies.py` already argues this rule for its own refusal: "Checked before
    # construction, not after... touching the hardware in order to decide whether to touch
    # it." Same rule, one layer up.
    _check_policy_name(console, loaded, policy)

    try:
        body = open_body(driver, allow_physical=physical, **_driver_kwargs(driver_arg))
    except PhysicalBodyRefused as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    except MissingDriverArgument as exc:
        # A body that is present and under-specified is not a missing install, and the
        # message already names what to pass. Suggesting an extra here would send somebody
        # to reinstall a driver they have.
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    except BodyUnavailable as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        console.print(
            "[dim]install a driver extra, e.g. " + escape('pip install -e ".[sim]"') + "[/dim]"
        )
        raise typer.Exit(code=1) from exc

    # Before anything moves. Discovering an incompatibility mid-episode means a robot is
    # already in motion when the mismatch is found.
    try:
        require_compatible(loaded, body)
    except IncompatibleBody as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        body.close()
        raise typer.Exit(code=1) from exc

    capability = body.capability

    try:
        running = _choose_policy(console, loaded, capability, policy, store)
    except typer.Exit:
        # The body was opened before the policy was chosen, because compatibility is
        # checked against it first. A refused policy still has to give it back.
        body.close()
        raise

    bus: Bus[StepRecord] = Bus()
    viewer = _attach_viewer(console, bus, loaded, view=view, save=view_save)

    scheduler = Scheduler(
        driver=body,
        limits=_effective_limits(console, loaded),
        confidence_threshold=loaded.confidence_threshold,
        bus=bus,
        # Only when something is watching. The chunk and its confidence are the half of
        # the picture the step stream does not carry, and plotting confidence against the
        # threshold is one of the three things this logger exists for.
        on_intent=(
            None if viewer is None else lambda obs, intent: viewer.log_intent(intent, step=obs.step)
        ),
    )

    console.print(
        f"[dim]{escape(loaded.ref)} {loaded.version} on {escape(capability.body_id)} "
        f"({capability.dof} axes, {capability.control_hz:g} Hz) via {escape(policy)}[/dim]"
    )

    _report_policy_rate(console, loaded, capability)

    recorder, root = _attach_recorder(console, bus, loaded, store, body)
    if recorder is not None:
        cameras, frame_size = _video_schema(body)
        _report_video(console, cameras, capability, driver)
        recorder.start(loaded.ref, capability, cameras=cameras, frame_size=frame_size)

    try:
        result = scheduler.run_episode(running, max_steps=steps, seed=seed)
    finally:
        # Nested so that a recorder which fails to close still leaves the body closed. A
        # half-written dataset is recoverable; a driver left holding a port is not.
        try:
            if recorder is not None:
                recorder.finish()
        finally:
            try:
                # Flushed before the body closes: an unflushed recording is a file that
                # exists and cannot be opened, which is worse than not asking for one.
                if viewer is not None:
                    viewer.close()
            finally:
                body.close()

    _report(console, result, bus, root)

    if result.subscriber_failures:
        # The bus isolates a failing subscriber so a body never stops moving because of a
        # consumer, and that is right for the kernel. It is wrong for a command: a run
        # whose recorder died collected nothing, and exiting zero says the opposite to
        # every script and CI job that only reads the status.
        raise typer.Exit(code=1)


def _effective_limits(console: Console, loaded):
    """The skill's limits under the machine's ceiling, if one is configured.

    One function because there are three places a scheduler is built and a fourth will be
    added by somebody who has not read `SECURITY.md`. The last time this project had the
    same construction in two places, only one of them was fixed.

    A ceiling that cannot be read stops the run. A site that wrote one believes it has a
    bound, and proceeding without it would be proceeding under limits they did not choose.
    """
    from tendon.services.limits import LocalLimitsError, load_local_limits, tighten

    try:
        ceiling = load_local_limits()
    except LocalLimitsError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        console.print("[dim]fix the file or remove it; running without it is not the same[/dim]")
        raise typer.Exit(code=1) from exc

    limits = tighten(loaded.limits, ceiling)
    if ceiling is not None and limits != loaded.limits:
        console.print("[dim]local limits are tighter than the skill's; using the tighter[/dim]")
    return limits


def _choose_policy(console: Console, loaded, capability, policy: str, store: str):
    """Build whichever policy `--policy` asked for.

    One function because `run` and `eval` both take the choice and this project has shipped
    the same bug twice from two copies of a policy construction. `eval` had no choice at all
    until now, which was its own version of the problem: `ReplayPolicy` describes itself as
    the fixed baseline *every evaluation* needs, and evaluation was the one command that
    could not use it.
    """
    _check_policy_name(console, loaded, policy)

    if policy == "scripted":
        _warn_about_an_ignored_adapter(console, loaded)
        return _baseline_policy(loaded, capability)

    return _replay_policy(console, loaded, capability, policy.partition(":")[2], store)


def _check_policy_name(console: Console, loaded, policy: str) -> None:
    """Refuse a `--policy` this build cannot run, using only the skill and the string.

    Split out of `_choose_policy` so it can be called before a body is opened. Building a
    policy needs the body's `Capability`; deciding whether the *name* is one we can run
    does not, and running that check second meant `--policy scriptd` opened a body — with
    `--physical`, a real arm — before saying the name was misspelled.

    Still called from `_choose_policy` as well, so the set of runnable names is written
    down once. A second copy that drifted would refuse a name one command accepts.
    """
    if policy == "scripted" or policy == "replay" or policy.startswith("replay:"):
        return

    if policy == "adapter":
        # Answered separately from a typo because the field is real: `skill.yaml` has a
        # `policy.adapter` slot, `tendon train` fills it, and asking to run it is the
        # obvious next thing. "not available yet" alongside a misspelling would suggest the
        # adapter is as imaginary as the typo.
        console.print("[red]nothing here can load a trained adapter yet.[/red]")
        console.print(
            f"[dim]`tendon train` writes one and {escape(loaded.policy_adapter or 'skill.yaml')} "
            "is where it goes. Loading it back is the missing half - it needs PEFT applied "
            "to a LeRobot policy, which lives in services/policy_lerobot.py "
            "(docs/collaboration.md).[/dim]"
        )
        raise typer.Exit(code=1)

    console.print(f"[red]policy {escape(policy)!r} is not available yet.[/red]")
    console.print(
        "[dim]'scripted' and 'replay:<skill>#<episode>' run today. A LeRobot adapter for "
        f"{escape(loaded.policy_base or 'the skill policy')} is Track A work "
        "(docs/collaboration.md).[/dim]"
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
            f"[dim]({escape(str(loaded.policy_adapter))}) - nothing can load one yet. "
            f"Running the scripted baseline instead.[/dim]"
        )


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
        gripper=_HELD_OPEN if capability.gripper.value != "none" else None,
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


def _attach_viewer(console: Console, bus, loaded, *, view: bool, save: str):
    """Stream the run into Rerun, when somebody asked for it.

    **Opt-in, unlike recording, and that difference is the point.** The recorder costs
    0.04 ms per step and is always attached because of it — design decision 1 is only
    structural because nobody would want it off. This costs about eighty times that, since
    it encodes frames a person will look at, and `services/viz.py` says so in its own
    docstring: attach it to a run being watched, not to every run being collected.

    So there is a flag here and none for recording. A flag on the wrong one of these would
    be the difference between a project that collects data and a project that means to.
    """
    if not view and not save:
        return None

    from tendon.services.viz import RerunLogger, VizError

    try:
        viewer = RerunLogger(
            session_name=f"tendon/{loaded.ref}",
            spawn=view,
            save_path=save or None,
            confidence_threshold=loaded.confidence_threshold,
        )
    except VizError as exc:
        # Not fatal. The run is still worth doing and still recorded; what is missing is
        # somewhere to watch it. Refusing would make an optional extra decide whether a
        # body moves.
        console.print(f"[yellow]not viewing: {escape(str(exc))}[/yellow]")
        return None

    viewer.attach_to(bus)
    if save:
        console.print(f"[dim]writing a Rerun recording to {escape(save)}[/dim]")
    return viewer


#: LeRobot's default when nothing is rendered. Never used to size a real frame — that comes
#: from the body — only to fill an argument the schema requires when there is no video.
_NO_VIDEO_SIZE = (480, 640)


def _video_schema(body) -> tuple[tuple[str, ...], tuple[int, int]]:
    """Which cameras this body is rendering, and at what size, asked once.

    Read from a real frame rather than from the body's declared `Capability.cameras`,
    because those are different questions: a body exposes cameras it is not rendering, and
    `features_for` is explicit that declaring one that will not be supplied turns every
    `add_frame` into an error. The frame is the only thing that knows.

    Nothing here is driver-specific. `RendersFrames` is the contract, `render()` names its
    own cameras, and the array says how big they are, so a driver written after this works
    without being added to a list.
    """
    from tendon.kernel.protocols import RendersFrames

    if not isinstance(body, RendersFrames):
        return (), _NO_VIDEO_SIZE

    frames = body.render()
    if not frames:
        return (), _NO_VIDEO_SIZE

    sample = next(iter(frames.values()))
    height, width = int(sample.shape[0]), int(sample.shape[1])
    return tuple(frames), (height, width)


def _report_policy_rate(console: Console, loaded, capability) -> None:
    """State both rates when a skill declares one for its policy, before anything moves.

    `requires.control_hz` is how fast the body accepts setpoints. `policy.hz` is how fast
    the policy's chunk was meant to be played. Assuming they are equal was a live defect:
    a 30 Hz policy on a 100 Hz body ran its trajectory more than three times too fast,
    silently, and in proportion to how fast the body happened to be — so the faster the
    machine, the more wrong the motion, which is the opposite of what anybody debugging it
    would assume.

    The two numbers only, no arithmetic. Deciding how many ticks to hold each action is
    `LeRobotPolicy`'s, and this project has twice shipped one bug from two copies of the
    same calculation. Stating the inputs cannot go out of step with the thing that uses
    them.
    """
    policy_hz = getattr(loaded, "policy_hz", None)
    if not policy_hz or policy_hz == capability.control_hz:
        return

    console.print(
        f"[dim]policy actions are for {policy_hz:g} Hz; this body runs at "
        f"{capability.control_hz:g} Hz[/dim]"
    )


def _report_video(console: Console, cameras: tuple[str, ...], capability, driver_name: str) -> None:
    """Say what video this episode will contain, while it can still be changed.

    Recording without it is a legitimate run, so this is not a warning about a mistake. It
    is here because the cost of not knowing is paid much later: a vision-language-action
    policy cannot be trained on state alone, and `tendon train` is where that surfaced —
    four minutes into loading a checkpoint, about episodes recorded weeks earlier.

    Only when the body has cameras it is not rendering. A body with none is not withholding
    anything and does not need a line every run saying so.
    """
    if cameras:
        console.print(f"[dim]recording video from {', '.join(cameras)}[/dim]")
        return
    if not capability.cameras:
        return

    from tendon.services.bodies import camera_parameter

    line = (
        f"[dim]no video: {escape(capability.body_id)} has "
        f"{', '.join(capability.cameras)} and is rendering none."
    )
    # Named only when this driver really takes it. A suggestion that is right for MuJoCo
    # and wrong for the next body is worse than no suggestion, and which one it is can be
    # asked rather than assumed.
    parameter = camera_parameter(driver_name)
    if parameter:
        line += f" --driver-arg {parameter}={capability.cameras[0]} to record one."
    console.print(line + "[/dim]")


def _attach_recorder(console: Console, bus, loaded, store: str, body=None):
    """Subscribe a recorder to the step bus, or say why nothing is being recorded.

    Returns the recorder (None when unavailable) and the store path it is writing to.
    The caller opens each episode with `recorder.start(...)` and closes it with
    `finish()`: subscribing is per-run, but an episode is per-episode, and `eval` runs
    thirty of them through one subscription.

    Recording is not optional and there is no flag to turn it off, but LeRobot is an
    optional extra and the kernel and the simulator both work without it. So the one
    honest thing to do when it is missing is to run anyway and say plainly that this
    episode is not being kept. Failing the run would make an optional dependency
    mandatory; staying quiet would let someone collect nothing for an afternoon.
    """
    from tendon.services.store import DEFAULT_ROOT

    root = Path(store) if store else DEFAULT_ROOT

    try:
        from tendon.services.recorder import Recorder
    except ImportError:
        console.print("[yellow]not recording: LeRobot is not installed[/yellow]")
        console.print(
            "[dim]this episode will not be kept - " + escape('pip install -e ".[robot]"') + "[/dim]"
        )
        # No path either: naming a store nothing was written to is the same lie in a
        # quieter form.
        return None, None

    # Recorded under the skill's own reference rather than the recorder's default
    # `tendon/local`. Episodes are grouped by what was being done, which is what the
    # store's "skill" column claims to show, what `store.py` decodes a directory name
    # back into, and the only grouping a training run can use.
    from tendon.kernel.protocols import RendersFrames

    recorder = Recorder(root=root, repo_id=loaded.ref)
    # Pixels come from the body, not from the step: a `StepRecord` carries an
    # `Observation`, and an observation carries frame references rather than frames.
    # `services/` cannot import `drivers/` to go and fetch them, which is why the contract
    # this checks lives in the kernel.
    renders = body is not None and isinstance(body, RendersFrames)
    recorder.attach_to(bus, frames=body.render if renders else None)
    return recorder, root


def _report(console: Console, result, bus, root: Path | None = None) -> None:
    """What happened, and anything that would otherwise be found later.

    Unchecked limits and dropped subscribers are printed even on a clean run. A caller who
    has to infer that an episode ran partly unverified will not infer it.
    """
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_row("episode", result.episode_id[:12])
    table.add_row("steps", str(result.steps))
    table.add_row("ended", "policy exhausted" if result.exhausted else result.state.value)
    table.add_row("interventions", f"{result.interventions} ({result.corrections} corrections)")
    table.add_row("clamped", str(sum(1 for r in result.records if r.clamped)))
    if root is not None:
        # On the table rather than in a closing line, because "where did it go" is part of
        # what happened. A run that recorded and a run that did not used to print
        # identically, which is how this command passed for a milestone it did not meet.
        table.add_row("recorded to", str(root))
    console.print(table)

    if result.unchecked:
        console.print()
        console.print("[yellow]limits that could not be evaluated:[/yellow]")
        for item, count in result.unchecked.items():
            share = f"{count} of {result.steps} steps" if result.steps else f"{count} steps"
            console.print(f"  [dim]{share}[/dim]  {escape(item)}")

    if result.fault_reason:
        console.print()
        console.print("[red]interrupt faulted - context could not support a resume:[/red]")
        for reason in result.fault_reason:
            console.print(f"  {escape(reason)}")

    for failure in result.subscriber_failures:
        console.print(
            f"[red]subscriber {escape(failure.name)} died at step {failure.step}:[/red] "
            f"{escape(failure.error)}"
        )

    slowest = bus.slowest()
    if slowest is not None:
        # "Subscribers" rather than "recording": with `--view` attached there are two, and
        # the expensive one is usually the viewer. Naming the total after the cheap half
        # would put the recorder's name on the viewer's cost, which is exactly the reading
        # that would get design decision 1 blamed for something it does not do.
        console.print(
            f"[dim]subscribers cost {bus.mean_publish_cost() * 1000:.4f} ms per step "
            f"(slowest: {escape(slowest[0])})[/dim]"
        )


@app.command()
def serve(
    port: int = typer.Option(8000, help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", help="Interface to bind"),
    skills_dir: str = typer.Option("skills", help="Where to look for skill packages"),
) -> None:
    """Serve the runtime, and the shell when it has been built.

    Binds to loopback by default. `SECURITY.md` records that there is no authentication
    between shell and runtime yet, so binding to anything wider is a deliberate act rather
    than a default someone inherits.

    The built shell is served from `shell/dist` **relative to where this is run**, so this
    is one command inside a checkout and an API on its own anywhere else. Which of the two
    happened is printed rather than left to be discovered from a blank page. While working
    on the shell itself, `npm run dev` in `shell/` proxies /api and /ws here instead.
    """
    import uvicorn

    from tendon.api.app import create_app, shell_root

    console = Console()
    built = shell_root()
    if built is None:
        console.print(
            "[yellow]serving the API only[/yellow] [dim]- no built shell at "
            f"{escape(str(Path('shell') / 'dist'))} relative to "
            f"{escape(str(Path.cwd()))}[/dim]"
        )
        console.print("[dim]build it with: cd shell && npm install && npm run build[/dim]")
    else:
        console.print(f"[dim]serving the shell from {escape(str(built))}[/dim]")

    if host not in ("127.0.0.1", "localhost", "::1"):
        Console().print(
            f"[yellow]warning:[/yellow] binding to {escape(host)}. There is no "
            "authentication between the shell and the runtime yet - anyone who can reach "
            "this port can command the body. See SECURITY.md."
        )

    uvicorn.run(create_app(skill_root=Path(skills_dir)), host=host, port=port)


def _driver_kwargs(pairs: list[str]) -> dict[str, str]:
    """Turn `key=value` strings into driver arguments.

    Values stay strings here. They used to stay strings everywhere, on the reasoning that
    guessing types would mean deciding `port=8` is an int on a body where it is a name -
    and a driver knows its own argument types.

    Right about the guessing, wrong about the conclusion: a driver knows, and its
    signature can be read. `services/bodies.coerce_driver_arguments` does that at the
    point of construction, so it applies to every caller rather than to this one command,
    and the question stays "what did this driver declare" rather than "what does this
    string look like".
    """
    kwargs: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise typer.BadParameter(f"expected key=value, got {pair!r}")
        kwargs[key.strip()] = value.strip()
    return kwargs


@app.command()
def shell(
    port: int = typer.Option(8000, help="Port the runtime listens on"),
    skills_dir: str = typer.Option("skills", help="Where to look for skill packages"),
) -> None:
    """Serve the runtime and say how to open the interface from here.

    The same server as `tendon serve`; what this adds is advice that fits the machine it is
    run on. Which of the two workflows applies depends on whether a build exists, and the
    command can see that.

    It used to print "run npm run dev in another terminal" unconditionally, and explain
    that keeping them apart meant the runtime did not have to serve static files. Neither
    survived: the runtime mounts `shell/dist` when it is there, so somebody in a checkout
    with a built shell was told to start a second server for a page already being served
    two lines below.
    """
    from tendon.api.app import shell_root

    console = Console()
    console.print(f"[dim]runtime on http://127.0.0.1:{port}[/dim]")

    if shell_root() is None:
        console.print("[dim]no built interface here. Either build it:[/dim]")
        console.print("  cd shell && npm install && npm run build")
        console.print("[dim]or run the dev server, which proxies to this one:[/dim]")
        console.print("  cd shell && npm install && npm run dev")
    else:
        console.print(f"[dim]open http://127.0.0.1:{port} - the interface is served here[/dim]")
        console.print(
            "[dim]to work on the shell itself, run its dev server instead; it reloads on "
            "edit and proxies here:[/dim]"
        )
        console.print("  cd shell && npm run dev")

    console.print()
    serve(port=port, host="127.0.0.1", skills_dir=skills_dir)


#: Rate levels the terminal chart draws, top to bottom. Eight rows: enough to read a fall
#: at a glance, short enough to sit above a prompt without scrolling.
_CHART_ROWS = (1.0, 0.875, 0.75, 0.625, 0.5, 0.375, 0.25, 0.125)

#: Widest the chart gets. Points are sampled down to this rather than truncated, so a long
#: history still shows its whole shape.
_CHART_WIDTH = 52


def _chart(points: tuple[tuple[int, float], ...]) -> list[str]:
    """The curve, in ASCII.

    ASCII rather than block characters. `tests/unit/test_console_output.py` exists because
    this project keeps crashing on a cp949 console, and a chart that raises
    `UnicodeEncodeError` while reporting progress would be a fitting way to lose the
    argument. The README draws the same shape with block characters, which is fine: markdown
    is not a terminal.
    """
    if not points:
        return []

    if len(points) > _CHART_WIDTH:
        # Sampled, not truncated: the interesting part of this line is usually the end,
        # and showing the first 52 points of a long history would hide exactly that.
        #
        # Spread across the whole range rather than stepping by a fixed stride. A stride
        # walks off the end and drops the last point, which is the one that says where
        # things currently stand — the first version of this did precisely that.
        last = len(points) - 1
        points = tuple(points[round(i * last / (_CHART_WIDTH - 1))] for i in range(_CHART_WIDTH))

    width = len(points)
    lines = []
    for level in _CHART_ROWS:
        bar = "".join("#" if rate >= level else " " for _, rate in points)
        lines.append(f"{level:>4.0%} |{bar}")

    lines.append("     +" + "-" * width)

    # The two ends of the x-axis, padded to the chart's own width so the label cannot grow
    # wider than the thing it labels.
    start, end = str(points[0][0]), str(points[-1][0])
    gap = max(1, width - len(start) - len(end))
    lines.append(f"      {start}{' ' * gap}{end} corrections")
    return lines


@app.command()
def progress(
    window: int = typer.Option(10, help="Episodes the intervention rate is measured over"),
    store: str = typer.Option("", help="Where progress lives. Defaults to ~/.tendon/progress"),
) -> None:
    """Is it asking less often than it used to.

    The graph `docs/roadmap.md` measures v0.3 by, for somebody who is not sitting in front
    of the shell. Watching a rig usually means an ssh session, and a line that only exists
    in a browser is a line that person does not have.

    Blank until a full window of episodes exists. A rate over three episodes is not a rate,
    and drawing one invites reading a trend off noise.
    """
    console = Console()

    from tendon.services.progress import DEFAULT_PROGRESS_ROOT, logs, rate_curve

    root = Path(store) if store else DEFAULT_PROGRESS_ROOT
    found = logs(root)

    if not found:
        console.print(f"[dim]nothing has run yet under {escape(str(root))}[/dim]")
        console.print("[dim]start an episode from the shell: tendon serve[/dim]")
        raise typer.Exit(code=1)

    for skill, body, records in found:
        console.print(
            f"[dim]{escape(skill)} on {escape(body)} - "
            f"{len(records)} episodes, {records[-1].corrections_known} corrections[/dim]"
        )

        curve = rate_curve(records, window=window)
        if not curve:
            console.print(
                f"[yellow]not enough yet[/yellow] [dim]- the rate is measured over "
                f"{window} episodes and there are {len(records)}[/dim]"
            )
        else:
            console.print()
            for line in _chart(curve):
                console.print(f"[dim]{escape(line)}[/dim]")
            console.print(f"[dim]  intervention rate over a trailing {window} episodes[/dim]")
        console.print()


@app.command()
def episodes(
    store: str = typer.Option("", help="Where episodes live. Defaults to ~/.tendon/episodes"),
) -> None:
    """List what has been recorded.

    Reads the layout on disk rather than opening datasets through LeRobot, so it works on
    a machine that cannot currently record - which is exactly when someone wants to know
    what they already have.
    """
    from tendon.services.store import DEFAULT_ROOT, human_size, list_datasets

    console = Console()
    root = Path(store) if store else DEFAULT_ROOT
    datasets = list_datasets(root)

    if not datasets:
        # Not an error. It is the normal state before anything has run, and saying so is
        # more useful than an empty table.
        console.print(f"[dim]nothing recorded under {escape(str(root))}[/dim]")
        console.print("[dim]run an episode: tendon run grasp/cube-sim[/dim]")
        return

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("skill")
    table.add_column("episodes", justify="right")
    table.add_column("size", justify="right")
    table.add_column("last written")

    for dataset in datasets:
        table.add_row(
            escape(dataset.ref),
            "?" if dataset.episodes is None else str(dataset.episodes),
            human_size(dataset.size_bytes),
            dataset.modified.astimezone().strftime("%Y-%m-%d %H:%M"),
        )

    console.print(table)

    # Reported rather than skipped: something on disk that cannot be read is a more useful
    # thing to know about than a shorter list.
    unreadable = [d for d in datasets if not d.readable]
    if unreadable:
        console.print()
        for dataset in unreadable:
            console.print(
                f"[yellow]{escape(dataset.directory)}:[/yellow] "
                f"{escape(dataset.unreadable_because or '')}"
            )


@app.command()
def curate(
    skill: str,
    store: str = typer.Option("", help="Where episodes live. Defaults to ~/.tendon/episodes"),
    limit: int = typer.Option(0, help="Show only the top N. Default shows every episode."),
) -> None:
    """Score recorded episodes and rank them by what is worth training on.

    Never deletes and never filters by a threshold. An automated curator that is wrong
    about an episode is wrong about it permanently, so this prints an ordering and leaves
    removal to a person, which is also why every score comes with its reasons.

    This said "not available yet, reading episodes back needs the [robot] extra" for
    months. That was an assumption nobody checked: a LeRobotDataset on disk is parquet with
    an ordinary schema, and duckdb reads it. So curation runs on a machine that cannot
    record, which is the machine somebody actually does this on.
    """
    console = Console()

    from tendon.services.episodes import EpisodeReadError, rank_episodes
    from tendon.services.skill import SkillError, load_skill
    from tendon.services.store import DEFAULT_ROOT

    try:
        loaded = load_skill(skill)
    except SkillError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    root = Path(store) if store else DEFAULT_ROOT
    directory = root / loaded.ref.replace("/", "__")

    try:
        ranking = rank_episodes(directory, limit=limit or None)
    except EpisodeReadError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        console.print(f"[dim]record some first: tendon run {escape(loaded.ref)}[/dim]")
        raise typer.Exit(code=1) from exc

    if not ranking.scored:
        console.print(f"[dim]nothing recorded for {escape(loaded.ref)} under {escape(str(root))}")
        raise typer.Exit(code=1)

    ranked = ranking.scored

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("episode")
    table.add_column("score", justify="right")
    table.add_column("steps", justify="right")
    table.add_column("why")

    for entry in ranked:
        table.add_row(
            entry.episode_id,
            f"{entry.score:.2f}",
            str(entry.signals.steps),
            escape(", ".join(entry.reasons)) if entry.reasons else "[dim]nothing notable[/dim]",
        )
    console.print(table)

    if not ranking.interrupts_known:
        # The signal the curator values most, and the store cannot attribute it. Said
        # loudly rather than left to be inferred from a ranking that looks complete.
        console.print()
        console.print(
            "[yellow]note:[/yellow] this store cannot say which episodes were interrupted, "
            "so none were promoted. Those are the episodes worth keeping most: they are "
            "the only recordings of recovery from failure."
        )


def _recorded_streams(directory: Path) -> list[str] | None:
    """Feature names in a LeRobot store, or None when they cannot be read.

    `meta/info.json` and nothing else: this runs before the decision to spend minutes on a
    checkpoint, so it must not need torch, LeRobot, or a single frame off disk.

    None rather than an empty list when the file is missing or unreadable, because "this
    store records no cameras" and "I could not tell" lead a reader to opposite conclusions,
    and the second one is not worth a warning.
    """
    import json

    try:
        info = json.loads((directory / "meta" / "info.json").read_text(encoding="utf-8"))
        return list(info["features"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


@app.command()
def train(
    skill: str,
    store: str = typer.Option("", help="Where episodes live. Defaults to ~/.tendon/episodes"),
    out: str = typer.Option(
        "", help="Where the adapter is written. Defaults to ~/.tendon/adapters"
    ),
    top: int = typer.Option(0, help="Train on the best N of the ranking. Default uses all of it."),
    base: str = typer.Option("", help="Override the skill's policy.base"),
    steps: int = typer.Option(2000, help="Optimiser steps. A budget, not a convergence test."),
    batch_size: int = typer.Option(8, help="Frames per step"),
    lora_rank: int = typer.Option(16, help="Adapter rank"),
) -> None:
    """LoRA fine-tune a skill's base policy on its curated episodes.

    The selection comes from the same ranking `tendon curate` prints, so what gets trained
    on is what you can already inspect. It is printed again here before the run starts,
    because a training set chosen silently is one nobody can dispute afterwards.

    Every episode by default. `services/curator.py` deliberately never filters by a
    threshold, because an automated curator that is wrong about an episode is wrong about
    it permanently, so the command that consumes its ranking does not invent one either.
    `--top` is that judgement, made by a person, on an ordering they have seen.

    Needs the `robot` and `train` extras and, realistically, a GPU.
    """
    console = Console()

    from tendon.services.episodes import EpisodeReadError, rank_episodes
    from tendon.services.skill import SkillError, load_skill
    from tendon.services.store import DEFAULT_ROOT
    from tendon.services.trainer import Trainer, TrainerError

    try:
        loaded = load_skill(skill)
    except SkillError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    base_policy = base or loaded.policy_base
    if not base_policy:
        # A skill is allowed to have no base policy - that is how a scripted baseline runs
        # without weights. There is simply nothing to adapt, and saying so beats letting
        # `fine_tune` fail on an empty Hub id further in.
        console.print(f"[red]{escape(loaded.ref)} declares no policy.base to fine-tune[/red]")
        console.print("[dim]add policy.base to skill.yaml, or pass --base <hub id>[/dim]")
        raise typer.Exit(code=1)

    root = Path(store) if store else DEFAULT_ROOT
    directory = root / loaded.ref.replace("/", "__")

    try:
        ranking = rank_episodes(directory, limit=top or None)
    except EpisodeReadError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        console.print(f"[dim]record some first: tendon run {escape(loaded.ref)}[/dim]")
        raise typer.Exit(code=1) from exc

    if not ranking.scored:
        console.print(f"[dim]nothing recorded for {escape(loaded.ref)} under {escape(str(root))}")
        raise typer.Exit(code=1)

    # `episode_id` is `episode_index` as a string; LeRobot's subset filter wants integers.
    selection = [int(entry.episode_id) for entry in ranking.scored]

    console.print(f"[bold]{escape(loaded.ref)}[/bold] on {escape(base_policy)}")
    console.print(f"[dim]{len(selection)} episodes, best first: {selection}[/dim]")
    if not ranking.interrupts_known:
        console.print(
            "[yellow]note:[/yellow] this store cannot say which episodes were interrupted, "
            "so none were promoted. The ordering is weaker than it looks."
        )

    # What the recordings actually contain, before a checkpoint is fetched and loaded.
    # Running this for real against the store `tendon run` had filled took four minutes to
    # reach `ValueError: All image features are missing from the batch`, raised inside the
    # model — which names neither the store nor the recording that produced it.
    #
    # The cause is not a defect in either: `MujocoDriver.render_cameras` is empty by
    # default because rendering costs milliseconds per frame, and the recorder writes the
    # schema of what is rendered. So the default path records no video, and a
    # vision-language-action policy cannot be trained on it. Stated rather than refused —
    # this reads `meta/info.json` and cannot know what any given base policy consumes, and
    # a state-only policy trains on exactly this data.
    streams = _recorded_streams(directory)
    if streams is not None:
        cameras = [name for name in streams if name.startswith("observation.images.")]
        if cameras:
            console.print(f"[dim]camera streams: {', '.join(sorted(cameras))}[/dim]")
        else:
            console.print(
                "[yellow]no camera streams in these recordings.[/yellow] [dim]`tendon run` "
                "renders none by default, so a policy that expects images will fail once "
                "the checkpoint has loaded, not now.[/dim]"
            )
    console.print()

    destination = Path(out) if out else DEFAULT_ROOT.parent / "adapters"
    destination = destination / loaded.ref.replace("/", "__")

    trainer = Trainer(root=root, repo_id=loaded.ref)
    try:
        result = trainer.fine_tune(
            loaded.ref,
            selection,
            base_policy=base_policy,
            output_dir=destination,
            steps=steps,
            batch_size=batch_size,
            lora_rank=lora_rank,
        )
    except TrainerError as exc:
        # Every failure inside `fine_tune` already names what to do about it - a missing
        # extra, a policy with no LoRA targets, a selection with no frames. Printing it is
        # the whole handling; a traceback on top would only bury it.
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]adapter written to {escape(str(result.adapter_path))}[/green]")
    console.print(
        f"[dim]{result.frames} frames, {result.steps} steps, final loss {result.final_loss:.4f}"
        f"[/dim]"
    )
    # The fraction, not the raw count: "4.2M trainable" reads as a large number either way,
    # while 1.2% of the model is immediately either an adapter or a mistake.
    console.print(
        f"[dim]trained {result.trainable_fraction:.2%} of the model "
        f"({result.trainable_parameters:,} of {result.total_parameters:,})[/dim]"
    )
    # Not "try it: tendon run --policy adapter". There is no such policy: `_choose_policy`
    # takes `scripted` and `replay:` and nothing else, and `skill.yaml`'s `policy.adapter`
    # is parsed and read by nothing. So this command can now produce an adapter that
    # nothing in the project can load, and saying so is better than a suggestion that
    # exits 1 the moment somebody follows it.
    console.print(
        "[yellow]nothing can load this adapter yet.[/yellow] [dim]`tendon run` accepts "
        "scripted and replay: only, and the policy.adapter field skill.yaml reserves for "
        "it is parsed and read by nothing. See docs/collaboration.md.[/dim]"
    )


@app.command("eval")
def evaluate_skill(
    skill: str = typer.Argument(..., help="Path to a skill directory or skill.yaml"),
    driver: str = typer.Option("mujoco", help="Which body to evaluate on"),
    episodes: int = typer.Option(0, help="Override the episode count in skill.yaml"),
    steps: int = typer.Option(300, help="Maximum control steps per episode"),
    seed: int = typer.Option(0, help="First seed; each episode increments it"),
    physical: bool = typer.Option(
        False,
        "--physical",
        help="Allow a body that moves real hardware. Read SECURITY.md first.",
    ),
    driver_arg: list[str] = _DRIVER_ARG_OPTION,
    store: str = typer.Option(
        "", help="Where episodes are written. Defaults to ~/.tendon/episodes"
    ),
    policy: str = typer.Option(
        "scripted",
        help="scripted | replay:<skill>#<episode>. The same choice `tendon run` takes.",
    ),
) -> None:
    """Run a skill repeatedly and report what happened.

    Success is judged from `Observation.extra` at the end of each episode, against the
    conditions the skill declares. When the body does not report the quantity, the verdict
    is *unknown* rather than *failed* - nobody measured, and recording that as failure
    would make an unmeasurable setup look like a broken policy.

    Every episode is recorded, on the same terms as `tendon run`. This command produces
    thirty episodes where that one produces a single episode, so it was the larger hole in
    design decision 1 while it had no bus at all.
    """
    console = Console()

    from tendon.kernel.bus import Bus
    from tendon.kernel.scheduler import Scheduler, StepRecord
    from tendon.services.bodies import (
        BodyUnavailable,
        MissingDriverArgument,
        PhysicalBodyRefused,
        open_body,
    )
    from tendon.services.evaluator import EpisodeOutcome, SuccessCriterion, evaluate, judge
    from tendon.services.skill import IncompatibleBody, SkillError, load_skill, require_compatible

    try:
        loaded = load_skill(skill)
    except SkillError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    # Same order as `run`, and for the same reason: a misspelled policy should not cost a
    # body. `eval` opens one and runs thirty episodes through it.
    _check_policy_name(console, loaded, policy)

    try:
        body = open_body(driver, allow_physical=physical, **_driver_kwargs(driver_arg))
        require_compatible(loaded, body)
    except (BodyUnavailable, MissingDriverArgument, PhysicalBodyRefused, IncompatibleBody) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    capability = body.capability
    criteria = [SuccessCriterion.parse(name, t) for name, t in loaded.success_criteria]
    count = episodes or loaded.eval_episodes

    console.print(
        f"[dim]{escape(loaded.ref)} on {escape(capability.body_id)}, "
        f"{count} episodes of up to {steps} steps[/dim]"
    )

    _report_policy_rate(console, loaded, capability)

    bus: Bus[StepRecord] = Bus()
    recorder, root = _attach_recorder(console, bus, loaded, store, body)
    # Asked once for the sweep, not once per episode: the body renders the same cameras at
    # the same size throughout, and `render()` costs a frame each time it is called.
    cameras, frame_size = _video_schema(body)
    if recorder is not None:
        _report_video(console, cameras, capability, driver)

    outcomes: list[EpisodeOutcome] = []
    unknown = 0
    failures: list[str] = []
    try:
        for index in range(count):
            # Rebuilt each episode so a replay starts from the beginning of the recording
            # rather than continuing where the last one stopped. `ReplayPolicy.reset` does
            # the same, and building it here keeps the two commands' loops identical.
            running = _choose_policy(console, loaded, capability, policy, store)
            scheduler = Scheduler(
                driver=body,
                limits=_effective_limits(console, loaded),
                confidence_threshold=loaded.confidence_threshold,
                bus=bus,
            )

            # Opened and closed around each episode rather than around the sweep: an
            # evaluation is thirty episodes, not one thirty times as long, and a store
            # that could not tell them apart would be useless for training.
            if recorder is not None:
                recorder.start(loaded.ref, capability, cameras=cameras, frame_size=frame_size)
            try:
                result = scheduler.run_episode(running, max_steps=steps, seed=seed + index)
            finally:
                if recorder is not None:
                    recorder.finish()

            if result.subscriber_failures:
                for failure in result.subscriber_failures:
                    failures.append(
                        f"episode {index}: {failure.name} died at step "
                        f"{failure.step} - {failure.error}"
                    )
                # The bus drops a subscriber that raises and never re-subscribes it, so
                # the remaining episodes would record nothing while still opening and
                # closing a dataset for each. Twenty-nine empty episodes are worse than
                # none: they look like a run that happened.
                recorder = None

            final = result.records[-1].observation.extra if result.records else {}
            verdict, reason = judge(final, criteria)
            if verdict is None:
                unknown += 1

            outcomes.append(
                EpisodeOutcome(
                    episode_id=result.episode_id,
                    skill=loaded.ref,
                    succeeded=bool(verdict),
                    interventions=result.interventions,
                    corrections=result.corrections,
                    faulted=result.state.value == "faulted",
                    failure_mode=reason,
                    confidence_source=_episode_source(result),
                )
            )
    finally:
        body.close()

    report = evaluate(outcomes, skill=loaded.ref)

    table = Table(show_header=False, box=None, pad_edge=False)
    if unknown == len(outcomes):
        # Every episode unjudged. Printing "0.0% success" here would be a number that
        # looks like a measurement and is not one.
        table.add_row("success rate", "[yellow]not measurable[/yellow]")
    else:
        table.add_row("success rate", f"{report.success_rate:.1%}")
    table.add_row("intervention rate", f"{report.intervention_rate:.1%}")
    table.add_row("corrections", str(report.corrections))
    table.add_row("faults", str(report.faults))
    table.add_row("episodes", str(report.episodes))
    if root is not None and not failures:
        table.add_row("recorded to", str(root))
    console.print(table)

    if report.failure_modes:
        console.print()
        console.print("[dim]failure modes[/dim]")
        for mode, n in report.failure_modes.items():
            console.print(f"  {n:>4}  {escape(mode)}")

    if report.caveats:
        console.print()
        for caveat in report.caveats:
            console.print(f"[yellow]note:[/yellow] {escape(caveat)}")

    if not report.is_comparable:
        console.print(
            "[yellow]note:[/yellow] this result cannot be compared against another run - "
            "see docs/decisions/0003-confidence-has-no-upstream-source.md"
        )

    if failures:
        # Same rule as `run`: an evaluation that measured thirty episodes and kept none of
        # them has not done half its job, and the numbers above are the half that is left.
        console.print()
        console.print("[red]recording stopped during this evaluation[/red]")
        for message in failures:
            console.print(f"  [dim]{escape(message)}[/dim]")
        raise typer.Exit(code=1)


def _episode_source(result):
    """Which estimator drove handovers in this episode.

    Read from the run rather than assumed: an evaluation that mislabels the estimator
    produces a rate that is not comparable to anything, while looking like it is.
    """
    from tendon.kernel.types import ConfidenceSource

    for record in result.records:
        intent = getattr(record, "intent", None)
        if intent is not None:
            return intent.confidence.source
    return ConfidenceSource.NONE


if __name__ == "__main__":
    app()
