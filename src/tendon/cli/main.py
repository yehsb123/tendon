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
    help="Pass key=value to the driver, e.g. --driver-arg port=COM3. Repeatable.",
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
        "scripted", help="scripted | replay:<episode.json> | the skill's own policy"
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
) -> None:
    """Run a policy on a body under the kernel.

    Loads the skill, checks it against the body before anything moves, and executes one
    episode. The run is recorded. There is no flag for that — design decision 1 — and the
    only thing `--store` changes is where it lands.

    For most of this project's life that was not true. The bus was created and handed to
    the scheduler, and nothing ever subscribed to it, so `tendon run` completed and the
    store stayed empty. The milestone this command is the acceptance test for reads
    "`tendon run` executes a policy in simulation and episodes appear".
    """
    console = Console()

    from tendon.kernel.bus import Bus
    from tendon.kernel.scheduler import Scheduler, StepRecord
    from tendon.services.bodies import BodyUnavailable, PhysicalBodyRefused, open_body
    from tendon.services.skill import IncompatibleBody, SkillError, load_skill, require_compatible

    try:
        loaded = load_skill(skill)
    except SkillError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        body = open_body(driver, allow_physical=physical, **_driver_kwargs(driver_arg))
    except PhysicalBodyRefused as exc:
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

    if policy == "scripted":
        running = _baseline_policy(loaded, capability)
    else:
        console.print(f"[red]policy {escape(policy)!r} is not available yet.[/red]")
        console.print(
            "[dim]Only 'scripted' runs today. A LeRobot adapter for "
            f"{escape(loaded.policy_base or 'the skill policy')} is Track A work "
            "(docs/collaboration.md).[/dim]"
        )
        body.close()
        raise typer.Exit(code=1)

    bus: Bus[StepRecord] = Bus()
    scheduler = Scheduler(
        driver=body,
        limits=loaded.limits,
        confidence_threshold=loaded.confidence_threshold,
        bus=bus,
    )

    console.print(
        f"[dim]{escape(loaded.ref)} {loaded.version} on {escape(capability.body_id)} "
        f"({capability.dof} axes, {capability.control_hz:g} Hz) via {escape(policy)}[/dim]"
    )

    recorder, root = _attach_recorder(console, bus, loaded, store)
    if recorder is not None:
        recorder.start(loaded.ref, capability)

    try:
        result = scheduler.run_episode(running, max_steps=steps, seed=seed)
    finally:
        # Nested so that a recorder which fails to close still leaves the body closed. A
        # half-written dataset is recoverable; a driver left holding a port is not.
        try:
            if recorder is not None:
                recorder.finish()
        finally:
            body.close()

    _report(console, result, bus, root)

    if result.subscriber_failures:
        # The bus isolates a failing subscriber so a body never stops moving because of a
        # consumer, and that is right for the kernel. It is wrong for a command: a run
        # whose recorder died collected nothing, and exiting zero says the opposite to
        # every script and CI job that only reads the status.
        raise typer.Exit(code=1)


def _baseline_policy(loaded, capability):
    """The scripted policy both `run` and `eval` use when no model is loaded.

    One function because there were two copies, and only one of them was fixed. `run`
    learned to command the jaw of a body that has one — without it the recorder's schema
    is a channel wider than the action and every episode dies at step 0 — and `eval` kept
    the old constructor. The bug was repaired and still present, in the command that runs
    thirty episodes instead of one.
    """
    from tendon.services.policies import ScriptedPolicy, sine_sweep

    return ScriptedPolicy(
        sine_sweep(dof=capability.dof),
        control_hz=capability.control_hz,
        dof=capability.dof,
        name=loaded.ref,
        # A body with a jaw has to be told what the jaw is doing, even by a baseline that
        # only sweeps one joint. Held open: this policy has no notion of grasping
        # anything, and a jaw that closes on nothing is the more surprising default.
        gripper=_HELD_OPEN if capability.gripper.value != "none" else None,
    )


def _attach_recorder(console: Console, bus, loaded, store: str):
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
            "[dim]this episode will not be kept — " + escape('pip install -e ".[robot]"') + "[/dim]"
        )
        # No path either: naming a store nothing was written to is the same lie in a
        # quieter form.
        return None, None

    # Recorded under the skill's own reference rather than the recorder's default
    # `tendon/local`. Episodes are grouped by what was being done, which is what the
    # store's "skill" column claims to show, what `store.py` decodes a directory name
    # back into, and the only grouping a training run can use.
    recorder = Recorder(root=root, repo_id=loaded.ref)
    recorder.attach_to(bus)
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
        console.print("[red]interrupt faulted — context could not support a resume:[/red]")
        for reason in result.fault_reason:
            console.print(f"  {escape(reason)}")

    for failure in result.subscriber_failures:
        console.print(
            f"[red]subscriber {escape(failure.name)} died at step {failure.step}:[/red] "
            f"{escape(failure.error)}"
        )

    slowest = bus.slowest()
    if slowest is not None:
        console.print(
            f"[dim]recording cost {bus.mean_publish_cost() * 1000:.4f} ms per step "
            f"(slowest subscriber: {escape(slowest[0])})[/dim]"
        )


@app.command()
def serve(
    port: int = typer.Option(8000, help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", help="Interface to bind"),
    skills_dir: str = typer.Option("skills", help="Where to look for skill packages"),
) -> None:
    """Serve the runtime API the shell talks to.

    Binds to loopback by default. `SECURITY.md` records that there is no authentication
    between shell and runtime yet, so binding to anything wider is a deliberate act rather
    than a default someone inherits.

    Run the shell separately with `npm run dev` in `shell/`; it proxies /api and /ws here.
    """
    import uvicorn

    from tendon.api.app import create_app

    if host not in ("127.0.0.1", "localhost", "::1"):
        Console().print(
            f"[yellow]warning:[/yellow] binding to {escape(host)}. There is no "
            "authentication between the shell and the runtime yet — anyone who can reach "
            "this port can command the body. See SECURITY.md."
        )

    uvicorn.run(create_app(skill_root=Path(skills_dir)), host=host, port=port)


def _driver_kwargs(pairs: list[str]) -> dict[str, str]:
    """Turn `key=value` strings into driver arguments.

    Values stay strings. Guessing types here would mean deciding that `port=8` is an int
    on a body where it is a name, and a driver knows its own argument types.
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
    """Serve the runtime and print how to open the interface.

    The shell is a separate dev server that proxies to this one. Keeping them apart means
    the runtime does not have to serve static files, and the interface can be reloaded
    without restarting an episode.
    """
    console = Console()
    console.print(f"[dim]runtime on http://127.0.0.1:{port}[/dim]")
    console.print("[dim]then, in another terminal:[/dim]")
    console.print("  cd shell && npm install && npm run dev")
    console.print()
    serve(port=port, host="127.0.0.1", skills_dir=skills_dir)


@app.command()
def episodes(
    store: str = typer.Option("", help="Where episodes live. Defaults to ~/.tendon/episodes"),
) -> None:
    """List what has been recorded.

    Reads the layout on disk rather than opening datasets through LeRobot, so it works on
    a machine that cannot currently record — which is exactly when someone wants to know
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
def curate(skill: str) -> None:
    """[v0.3] Score and select episodes worth training on.

    Not available yet. `services/curator.py` computes the signals and is tested, but
    scoring real episodes means reading them, which needs LeRobot and the `[robot]` extra.
    """
    _not_yet(
        "curate",
        "v0.3",
        "services/curator.py has the signals and is tested. What is missing is reading "
        "recorded episodes back, which needs the [robot] extra.",
    )


@app.command()
def train(skill: str) -> None:
    """[v0.3] LoRA fine-tune on curated data.

    Not available yet. `services/trainer.py` exists; wiring it to the CLI waits until
    curation can select what to train on.
    """
    _not_yet(
        "train",
        "v0.3",
        "services/trainer.py exists. Wiring it up waits on curate, which has to choose "
        "what to train on first.",
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
) -> None:
    """Run a skill repeatedly and report what happened.

    Success is judged from `Observation.extra` at the end of each episode, against the
    conditions the skill declares. When the body does not report the quantity, the verdict
    is *unknown* rather than *failed* — nobody measured, and recording that as failure
    would make an unmeasurable setup look like a broken policy.

    Every episode is recorded, on the same terms as `tendon run`. This command produces
    thirty episodes where that one produces a single episode, so it was the larger hole in
    design decision 1 while it had no bus at all.
    """
    console = Console()

    from tendon.kernel.bus import Bus
    from tendon.kernel.scheduler import Scheduler, StepRecord
    from tendon.services.bodies import BodyUnavailable, PhysicalBodyRefused, open_body
    from tendon.services.evaluator import EpisodeOutcome, SuccessCriterion, evaluate, judge
    from tendon.services.skill import IncompatibleBody, SkillError, load_skill, require_compatible

    try:
        loaded = load_skill(skill)
    except SkillError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        body = open_body(driver, allow_physical=physical, **_driver_kwargs(driver_arg))
        require_compatible(loaded, body)
    except (BodyUnavailable, PhysicalBodyRefused, IncompatibleBody) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    capability = body.capability
    criteria = [SuccessCriterion.parse(name, t) for name, t in loaded.success_criteria]
    count = episodes or loaded.eval_episodes

    console.print(
        f"[dim]{escape(loaded.ref)} on {escape(capability.body_id)}, "
        f"{count} episodes of up to {steps} steps[/dim]"
    )

    bus: Bus[StepRecord] = Bus()
    recorder, root = _attach_recorder(console, bus, loaded, store)

    outcomes: list[EpisodeOutcome] = []
    unknown = 0
    failures: list[str] = []
    try:
        for index in range(count):
            policy = _baseline_policy(loaded, capability)
            scheduler = Scheduler(
                driver=body,
                limits=loaded.limits,
                confidence_threshold=loaded.confidence_threshold,
                bus=bus,
            )

            # Opened and closed around each episode rather than around the sweep: an
            # evaluation is thirty episodes, not one thirty times as long, and a store
            # that could not tell them apart would be useless for training.
            if recorder is not None:
                recorder.start(loaded.ref, capability)
            try:
                result = scheduler.run_episode(policy, max_steps=steps, seed=seed + index)
            finally:
                if recorder is not None:
                    recorder.finish()

            if result.subscriber_failures:
                for failure in result.subscriber_failures:
                    failures.append(
                        f"episode {index}: {failure.name} died at step "
                        f"{failure.step} — {failure.error}"
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
            "[yellow]note:[/yellow] this result cannot be compared against another run — "
            "see docs/decisions/0003-confidence-has-no-upstream-source.md"
        )

    if failures:
        # Same rule as `run`: an evaluation that measured thirty episodes and kept none of
        # them has not done half its job, and the numbers above are the half that is left.
        console.print()
        console.print("[red]recording stopped during this evaluation[/red]")
        for failure in failures:
            console.print(f"  [dim]{escape(failure)}[/dim]")
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
