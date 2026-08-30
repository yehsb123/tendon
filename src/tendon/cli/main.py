"""The tendon command.

Mirrors the OS metaphor so the commands are guessable. Every one of them must work
against the MuJoCo driver with no hardware attached.
"""

from __future__ import annotations

import contextlib

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from tendon import __version__
from tendon.cli.doctor import Status, run_checks, summarise

app = typer.Typer(
    name="tendon",
    help="The operating layer for physical AI.",
    no_args_is_help=True,
)


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


@app.command()
def run(
    skill: str = typer.Argument(..., help="Path to a skill directory or skill.yaml"),
    driver: str = typer.Option("mujoco", help="Which body to run on"),
    policy: str = typer.Option(
        "scripted", help="scripted | replay:<episode.json> | the skill's own policy"
    ),
    steps: int = typer.Option(500, help="Maximum control steps"),
    seed: int | None = typer.Option(None, help="Seed the body for a repeatable start"),
) -> None:
    """Run a policy on a body under the kernel.

    Loads the skill, checks it against the body before anything moves, and executes one
    episode. Every step is published to the bus, so a recorder attached here would capture
    the run with no flag set — design decision 1.
    """
    console = Console()

    from tendon.drivers import base as driver_base
    from tendon.kernel.bus import Bus
    from tendon.kernel.scheduler import Scheduler, StepRecord
    from tendon.services.policies import ScriptedPolicy, sine_sweep
    from tendon.services.skill import IncompatibleBody, SkillError, load_skill, require_compatible

    try:
        loaded = load_skill(skill)
    except SkillError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    # Importing is how a driver registers itself. Absence is expected on a machine
    # without the sim extra, and `driver_base.load` below reports it properly.
    with contextlib.suppress(ImportError):
        import tendon.drivers.mujoco  # noqa: F401

    try:
        body = driver_base.load(driver)
    except driver_base.DriverError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        console.print('[dim]install a driver extra, e.g. pip install -e ".[sim]"[/dim]')
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
        running = ScriptedPolicy(
            sine_sweep(dof=capability.dof),
            control_hz=capability.control_hz,
            dof=capability.dof,
            name=loaded.ref,
        )
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

    try:
        result = scheduler.run_episode(running, max_steps=steps, seed=seed)
    finally:
        body.close()

    _report(console, result, bus)


def _report(console: Console, result, bus) -> None:
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
def shell(port: int = 8080) -> None:
    """Serve the intervention interface."""
    raise NotImplementedError("v0.2")


@app.command()
def episodes() -> None:
    """List recorded episodes."""
    raise NotImplementedError("v0.1")


@app.command()
def curate(skill: str) -> None:
    """Score and select episodes worth training on."""
    raise NotImplementedError("v0.3")


@app.command()
def train(skill: str) -> None:
    """LoRA fine-tune on curated data."""
    raise NotImplementedError("v0.3")


@app.command("eval")
def evaluate_skill(
    skill: str = typer.Argument(..., help="Path to a skill directory or skill.yaml"),
    driver: str = typer.Option("mujoco", help="Which body to evaluate on"),
    episodes: int = typer.Option(0, help="Override the episode count in skill.yaml"),
    steps: int = typer.Option(300, help="Maximum control steps per episode"),
    seed: int = typer.Option(0, help="First seed; each episode increments it"),
) -> None:
    """Run a skill repeatedly and report what happened.

    Success is judged from `Observation.extra` at the end of each episode, against the
    conditions the skill declares. When the body does not report the quantity, the verdict
    is *unknown* rather than *failed* — nobody measured, and recording that as failure
    would make an unmeasurable setup look like a broken policy.
    """
    console = Console()

    from tendon.drivers import base as driver_base
    from tendon.kernel.scheduler import Scheduler
    from tendon.services.evaluator import EpisodeOutcome, SuccessCriterion, evaluate, judge
    from tendon.services.policies import ScriptedPolicy, sine_sweep
    from tendon.services.skill import IncompatibleBody, SkillError, load_skill, require_compatible

    try:
        loaded = load_skill(skill)
    except SkillError as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    with contextlib.suppress(ImportError):
        import tendon.drivers.mujoco  # noqa: F401

    try:
        body = driver_base.load(driver)
        require_compatible(loaded, body)
    except (driver_base.DriverError, IncompatibleBody) as exc:
        console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc

    capability = body.capability
    criteria = [SuccessCriterion.parse(name, t) for name, t in loaded.success_criteria]
    count = episodes or loaded.eval_episodes

    console.print(
        f"[dim]{escape(loaded.ref)} on {escape(capability.body_id)}, "
        f"{count} episodes of up to {steps} steps[/dim]"
    )

    outcomes: list[EpisodeOutcome] = []
    unknown = 0
    try:
        for index in range(count):
            policy = ScriptedPolicy(
                sine_sweep(dof=capability.dof),
                control_hz=capability.control_hz,
                dof=capability.dof,
                name=loaded.ref,
            )
            scheduler = Scheduler(
                driver=body,
                limits=loaded.limits,
                confidence_threshold=loaded.confidence_threshold,
            )
            result = scheduler.run_episode(policy, max_steps=steps, seed=seed + index)

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
