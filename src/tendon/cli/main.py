"""The tendon command.

Mirrors the OS metaphor so the commands are guessable. Every one of them must work
against the MuJoCo driver with no hardware attached.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from tendon import __version__
from tendon.cli import observers, policies, reporting
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

#: Policy names this build can run. One set, consulted by the name check and named in the
#: refusal, so the list a person is shown cannot drift from the list that is accepted.
policies.RUNNABLE_POLICIES = frozenset({"scripted", "replay", "adapter"})

#: Reference spread for a loaded checkpoint: none, until somebody measures one.
#:
#: The number is the scale confidence is measured against, and ADR 0003 is explicit that
#: nothing has calibrated it — it is "the caller's guess" until v0.3 measures spread
#: against intervention outcomes. `api/app.py` passes 0.004, tuned to the synthetic policy
#: it drives; using that here would be borrowing a constant fitted to something else and
#: presenting the result as a measurement of this.
#:
#: Zero is not a disabled feature. `services/confidence.py` answers it with
#: `ConfidenceSource.NONE` and the reason "no reference spread configured, so the
#: measurement has no scale", which is what an operator should be told. A guessed number
#: would produce a confident-looking score with nothing behind it, and this project's whole
#: interrupt path keys off that score.
_UNCALIBRATED_SPREAD = 0.0


@app.command()
def run(
    skill: str = typer.Argument(
        ..., help="Skill reference (grasp/cube-sim) or a path to a skill directory"
    ),
    driver: str = typer.Option("mujoco", help="Which body to run on"),
    policy: str = typer.Option(
        "scripted",
        help=(
            "scripted | replay:<skill>#<episode> | adapter[:<path>]. "
            "replay: takes a recording from the store, not a file - nothing writes "
            "episode JSON. adapter runs what `tendon train` produced."
        ),
    ),
    adapter: str = typer.Option(
        "",
        help=(
            "Adapter directory for --policy adapter. Defaults to policy.adapter in "
            "skill.yaml; the base checkpoint is read from the adapter, not from the skill."
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
    policies.check_policy_name(console, loaded, policy, adapter)

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
        running = policies.choose_policy(
            console, loaded, capability, policy, store, adapter, body, driver
        )
    except typer.Exit:
        # The body was opened before the policy was chosen, because compatibility is
        # checked against it first. A refused policy still has to give it back.
        body.close()
        raise

    bus: Bus[StepRecord] = Bus()
    viewer = observers.attach_viewer(console, bus, loaded, view=view, save=view_save)

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

    reporting.report_policy_rate(console, loaded, capability)

    recorder, root = observers.attach_recorder(console, bus, loaded, store, body)
    if recorder is not None:
        cameras, frame_size = observers.video_schema(body)
        reporting.report_video(console, cameras, capability, driver)
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

    _record_progress(console, loaded, capability, result, store)
    reporting.report(console, result, bus, root)

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


def _judge(loaded, result) -> bool | None:
    """Whether the episode achieved what the skill declares as success, or None.

    None when nobody could tell — no criteria declared, or the body does not report the
    quantity they need. `tendon eval grasp/cube-sim` currently answers None for every
    episode, because `skill.yaml` asks for `cube_height` and the MuJoCo driver does not put
    it in `Observation.extra`.

    That is exactly why this is recorded rather than left to `eval`. The v0.3 graph plots
    intervention rate against corrections, and **a policy that stops asking because it
    stopped trying draws the same falling line as one that learned.** Without a verdict
    beside each point the two readings are indistinguishable, and `examples/04_improve`
    prints PASS on the fall alone.

    Uses the same `judge` the evaluator does, so a run and an evaluation cannot disagree
    about whether the same episode succeeded.
    """
    from tendon.services.evaluator import SuccessCriterion, judge

    criteria = [SuccessCriterion.parse(name, value) for name, value in loaded.success_criteria]
    if not criteria:
        return None

    # `result.final_world`, not the last observation. A skill judges the world; an
    # observation is what the policy saw, and ground truth read from there is ground truth
    # a policy could learn to use — working in simulation and failing on hardware that
    # cannot supply it, with no simulation test able to catch it.
    verdict, _ = judge(result.final_world, criteria)
    return verdict


def _record_progress(console: Console, loaded, capability, result, store: str) -> None:
    """Append one finished episode to the progress log.

    The log is what `tendon progress` draws, and that graph is the whole of v0.3: *after N
    human corrections, the intervention rate drops*. Until now only `api/app.py` wrote to
    it, so an episode counted towards the proof only if it was started from the shell.
    `tendon eval --episodes 50` produced fifty episodes and an empty log, and `tendon
    progress` answered "nothing has run yet — start an episode from the shell", which was
    true and reads as a limitation of the store rather than of who writes to it.

    That left the control arm unrecordable. A run with no operator is not an absent data
    point: it is the intervention rate at zero corrections, which is the left end of the
    line everything else is measured against.

    `corrections_known` is read from the store rather than counted from this run, because
    it means "corrections held for this skill and body", not "corrections given just now".
    An evaluation after an afternoon of teaching belongs at the x position that teaching
    reached, not at zero.

    Isolated the same way the API's copy is: a log that cannot be appended to must not turn
    a finished run into a failed one, and a line that silently never appeared is a hole in
    the graph that nobody can see.
    """
    from tendon.services import progress
    from tendon.services.memory_store import DEFAULT_MEMORY_ROOT, load_memory

    root = Path(store).parent / "progress" if store else progress.DEFAULT_PROGRESS_ROOT
    succeeded = _judge(loaded, result)

    try:
        known = len(load_memory(DEFAULT_MEMORY_ROOT, loaded.ref, capability.body_id))
    except Exception:  # noqa: BLE001 - an unreadable memory is not a reason to lose the point
        known = 0

    try:
        progress.append(
            root,
            loaded.ref,
            capability.body_id,
            progress.EpisodeRecord(
                skill=loaded.ref,
                body=capability.body_id,
                episode_id=result.episode_id,
                ended_at=progress.now(),
                steps=result.steps,
                interventions=result.interventions,
                corrections=result.corrections,
                corrections_known=known,
                succeeded=succeeded,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - isolation, not silence
        console.print(f"[yellow]could not record progress: {escape(str(exc))}[/yellow]")


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
        # Both, now that both write. This named the shell alone, which was accurate and
        # read as a fact about the store rather than about who filled it — so somebody who
        # had just run fifty episodes concluded the log was broken.
        console.print("[dim]run some: tendon run <skill>, or tendon eval <skill>[/dim]")
        console.print("[dim]corrections come from an operator: tendon serve[/dim]")
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
            for line in reporting.chart(curve):
                console.print(f"[dim]{escape(line)}[/dim]")
            console.print(f"[dim]  intervention rate over a trailing {window} episodes[/dim]")

        reporting.report_success(console, records)
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
def calibrate(
    skill: str,
    driver: str = typer.Option("mujoco", help="Which body to measure on"),
    policy: str = typer.Option("adapter", help="Which policy to measure. adapter[:<path>]"),
    adapter: str = typer.Option("", help="Adapter directory. Defaults to skill.yaml"),
    steps: int = typer.Option(400, help="Control steps to sample over"),
    seed: int | None = typer.Option(None, help="Seed the body for a repeatable measurement"),
    physical: bool = typer.Option(
        False, "--physical", help="Allow a body that moves real hardware. Read SECURITY.md."
    ),
    driver_arg: list[str] = _DRIVER_ARG_OPTION,
    out: str = typer.Option("", help="Where to write it. Defaults to ~/.tendon/calibration"),
) -> None:
    """Measure what counts as typical disagreement for this policy on this body.

    Without it a loaded checkpoint reports no confidence at all: `services/confidence.py`
    scores a chunk against a reference spread, every caller had to supply that number, and
    none could. So the policy could run and could not raise its own hand, which is design
    decision 2 not working.

    This measures the scale. It does not set the threshold, and the difference is the whole
    reason it can be done today: how much disagreement is *typical* is a property of the
    policy and the body, measurable by running them; how much disagreement means *ask for
    help* is a property of what goes wrong when you do not, and needs episodes where a
    human took over. That second one is still v0.3 and still needs the loop's own data
    (ADR 0003).

    Nothing is recorded and no episode is written. This drives the body to produce
    observations, not to collect data.
    """
    console = Console()

    from tendon.kernel.bus import Bus
    from tendon.kernel.scheduler import Scheduler, StepRecord
    from tendon.services import calibration as calibration_module
    from tendon.services.bodies import (
        BodyUnavailable,
        MissingDriverArgument,
        PhysicalBodyRefused,
        open_body,
    )
    from tendon.services.progress import now
    from tendon.services.skill import IncompatibleBody, SkillError, load_skill, require_compatible

    try:
        loaded = load_skill(skill)
    except SkillError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    policies.check_policy_name(console, loaded, policy, adapter)

    try:
        body = open_body(driver, allow_physical=physical, **_driver_kwargs(driver_arg))
    except (PhysicalBodyRefused, MissingDriverArgument, BodyUnavailable) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        require_compatible(loaded, body)
    except IncompatibleBody as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        body.close()
        raise typer.Exit(code=1) from exc

    capability = body.capability

    try:
        running = policies.choose_policy(
            console, loaded, capability, policy, "", adapter, body, driver
        )
    except typer.Exit:
        body.close()
        raise

    if not hasattr(running, "last_spread"):
        console.print(f"[red]{escape(policy)} does not report a sample spread.[/red]")
        console.print(
            "[dim]Only a policy that samples has disagreement to measure. A scripted "
            "baseline produces one chunk and the same chunk every time.[/dim]"
        )
        body.close()
        raise typer.Exit(code=1)

    spreads: list[float] = []

    # No recorder and no bus subscriber. The point is to observe the policy, not to collect
    # an episode; writing 400 steps into the store would put a run nobody asked for in
    # front of the curator.
    bus: Bus[StepRecord] = Bus()
    scheduler = Scheduler(
        driver=body,
        limits=_effective_limits(console, loaded),
        confidence_threshold=loaded.confidence_threshold,
        bus=bus,
    )
    bus.subscribe("calibration", lambda _record: _collect(running, spreads))

    console.print(f"[dim]measuring {escape(running.name)} over {steps} steps[/dim]")
    try:
        scheduler.run_episode(running, max_steps=steps, seed=seed)
    finally:
        body.close()

    try:
        measured = calibration_module.from_spreads(
            spreads,
            skill=loaded.ref,
            body=capability.body_id,
            policy=running.name,
            measured_at=now(),
        )
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        console.print(f"[dim]try more steps: --steps {steps * 4}[/dim]")
        raise typer.Exit(code=1) from exc

    root = Path(out) if out else calibration_module.DEFAULT_CALIBRATION_ROOT
    calibration_module.save(root, measured)

    console.print()
    console.print(f"[green]reference spread {measured.reference_spread:.6f}[/green]")
    console.print(
        f"[dim]p10 {measured.p10:.6f}  p90 {measured.p90:.6f}  "
        f"from {measured.samples} predictions[/dim]"
    )
    if not measured.is_tight:
        console.print(
            "[yellow]this distribution is wide[/yellow] [dim]- p90 is more than ten times "
            "p10, so 'typical' describes this policy loosely and a score built on it is a "
            "weaker signal than the number suggests.[/dim]"
        )
    written = calibration_module.calibration_path(root, loaded.ref, capability.body_id)
    console.print(f"[dim]written to {escape(str(written))}[/dim]")

    reporting.report_thresholds(console, measured, loaded.confidence_threshold)


def _collect(policy, spreads: list[float]) -> None:
    """Take the spread of the last prediction, if there was one.

    Subscribed to the step bus rather than wrapping `predict`, because the scheduler
    predicts at the deliberation rate and steps at the control rate: a chunk covers many
    steps, so the same spread is read repeatedly and duplicates have to go. Comparing
    against the last value is enough — two consecutive predictions with byte-identical
    spread would be a policy that is not sampling.
    """
    value = policy.last_spread
    if value is not None and (not spreads or spreads[-1] != value):
        spreads.append(value)


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


def _ensure_writable(console: Console, destination: Path) -> None:
    """Prove the adapter can be written before spending a night producing it.

    `Trainer.fine_tune` creates the output directory after the training loop, which is the
    correct place for it to happen and the worst possible place to *discover* it cannot.
    A path that is a file, a directory without permission, or a disk with nothing left on
    it costs the entire run and yields nothing — for a 700KB write.

    Same rule as refusing a `--policy` name before opening a body, at the other end of the
    command and with far more at stake: the question "can this be written" is answerable
    now, and answering it later throws away work that cannot be recovered.

    Writes and removes a probe rather than inspecting permission bits, because permission
    is not the only reason a write fails and the only reliable test of a write is a write.
    """
    probe = destination / ".tendon-write-test"
    try:
        destination.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
    except OSError as exc:
        console.print(f"[red]cannot write the adapter to {escape(str(destination))}: {exc}[/red]")
        console.print("[dim]pass --out somewhere writable. Checked now rather than after[/dim]")
        console.print("[dim]the run, because a finished run that cannot be saved is lost.[/dim]")
        raise typer.Exit(code=1) from exc
    finally:
        # The directory is left in place. It is where the adapter is about to go, and
        # `fine_tune` would create it anyway; removing it here to put it back seconds later
        # would only add a way for the two to disagree.
        with contextlib.suppress(OSError):
            probe.unlink()


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
    _ensure_writable(console, destination)

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
    # This said "nothing can load this adapter yet" for as long as that was true, which is
    # no longer. The suggestion is checked by a test that follows it: whatever appears
    # after `--policy` here has to be a policy `tendon run` accepts.
    console.print(
        f"[dim]run it: tendon run {escape(loaded.ref)} --policy adapter "
        f"--adapter {escape(str(destination))}[/dim]"
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
        help=(
            "scripted | replay:<skill>#<episode> | adapter[:<path>]. "
            "The same choice `tendon run` takes."
        ),
    ),
    adapter: str = typer.Option(
        "", help="Adapter directory for --policy adapter. Defaults to skill.yaml's policy.adapter."
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
    policies.check_policy_name(console, loaded, policy, adapter)

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

    reporting.report_policy_rate(console, loaded, capability)

    bus: Bus[StepRecord] = Bus()
    recorder, root = observers.attach_recorder(console, bus, loaded, store, body)
    # Asked once for the sweep, not once per episode: the body renders the same cameras at
    # the same size throughout, and `render()` costs a frame each time it is called.
    cameras, frame_size = observers.video_schema(body)
    if recorder is not None:
        reporting.report_video(console, cameras, capability, driver)

    outcomes: list[EpisodeOutcome] = []
    unknown = 0
    failures: list[str] = []
    try:
        for index in range(count):
            # Rebuilt each episode so a replay starts from the beginning of the recording
            # rather than continuing where the last one stopped. `ReplayPolicy.reset` does
            # the same, and building it here keeps the two commands' loops identical.
            running = policies.choose_policy(
                console, loaded, capability, policy, store, adapter, body, driver
            )
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

            # Every episode, not the sweep. Thirty evaluation episodes are thirty points on
            # the graph, and a sweep recorded as one would hide the thing the graph is for:
            # whether the rate moves across them.
            _record_progress(console, loaded, capability, result, store)

            # The world at the end, not the policy's last observation. See `_judge`.
            verdict, reason = judge(result.final_world, criteria)
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
                    confidence_source=reporting.episode_source(result),
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


if __name__ == "__main__":
    app()
