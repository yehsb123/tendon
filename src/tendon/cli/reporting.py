"""What the commands print, and why each line is there.

Split out of `cli/main.py`, which had grown to nearly two thousand lines holding the app,
eleven commands, policy construction, observer wiring and every message any of them
produced. Reporting is the part with the least coupling to the rest — it takes a finished
thing and renders it — and it is the part most often changed, because almost every defect
this project has found ended as a sentence somebody needed to read.

Nothing here decides anything. A function that would have to choose belongs with what it
chooses about; these only say what is already true.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

#: Rate levels the terminal chart draws, top to bottom. Eight rows: enough to read a fall
#: at a glance, short enough to sit above a prompt without scrolling.
CHART_ROWS = (1.0, 0.875, 0.75, 0.625, 0.5, 0.375, 0.25, 0.125)

#: Widest the chart gets. Points are sampled down to this rather than truncated, so a long
#: history still shows its whole shape.
CHART_WIDTH = 52

#: Thresholds the confidence report walks through. Fixed rather than derived from the
#: measurement, so two runs of the same skill can be read against each other.
THRESHOLD_CHOICES = (0.5, 0.4, 0.3, 0.2, 0.1)


# ------------------------------------------------------------------- before a run starts


def report_policy_rate(console: Console, loaded, capability) -> None:
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


def report_video(console: Console, cameras: tuple[str, ...], capability, driver_name: str) -> None:
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


# -------------------------------------------------------------------- after a run ends


def report(console: Console, result, bus, root: Path | None = None) -> None:
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

    if result.stopped_because:
        # First, and yellow, because it is the difference between an episode that ended and
        # one that was stopped. A policy raising its own hand with nobody to answer prints
        # `steps 0`, `ended running`, `interventions 0` otherwise — three true statements
        # adding up to "nothing happened", for the one event design decision 2 exists to
        # produce.
        console.print()
        console.print(f"[yellow]stopped:[/yellow] {escape(result.stopped_because)}")

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


def episode_source(result):
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


# ------------------------------------------------------------------------ the v0.3 graph


def report_success(console: Console, records) -> None:
    """Whether the task was still being achieved while the intervention rate fell.

    The graph is the whole claim of this project, and by itself it is ambiguous in a way
    that favours the claim. **A policy that stops asking for help because it stopped trying
    draws exactly the same falling line as one that learned.**

    So this says which case is in front of you. When nothing measured success, it says that
    rather than nothing — a graph whose other half is missing should not look complete.
    """
    verdicts = [record.succeeded for record in records]
    measured = [verdict for verdict in verdicts if verdict is not None]

    if not measured:
        console.print(
            "[yellow]success was not measured on any of these episodes[/yellow] [dim]- so a "
            "falling rate here is 'asked less often', which a policy that stopped trying "
            "would also produce. Have the body report what the skill's success criteria "
            "name.[/dim]"
        )
        return

    rate = sum(1 for verdict in measured if verdict) / len(measured)
    unknown = len(verdicts) - len(measured)
    line = f"[dim]succeeded on {rate:.0%} of {len(measured)} judged episodes[/dim]"
    if unknown:
        # Named rather than folded in. An episode nobody could judge is not a failure, and
        # counting it as one would understate a policy that works on a rig that cannot say.
        line += f"[dim], {unknown} could not be judged[/dim]"
    console.print(line)


def chart(points: tuple[tuple[int, float], ...]) -> list[str]:
    """The curve, in ASCII.

    ASCII rather than block characters. `tests/unit/test_console_output.py` exists because
    this project keeps crashing on a cp949 console, and a chart that raises
    `UnicodeEncodeError` while reporting progress would be a fitting way to lose the
    argument. The README draws the same shape with block characters, which is fine: markdown
    is not a terminal.
    """
    if not points:
        return []

    if len(points) > CHART_WIDTH:
        # Sampled, not truncated: the interesting part of this line is usually the end,
        # and showing the first 52 points of a long history would hide exactly that.
        #
        # Spread across the whole range rather than stepping by a fixed stride. A stride
        # walks off the end and drops the last point, which is the one that says where
        # things currently stand — the first version of this did precisely that.
        last = len(points) - 1
        points = tuple(points[round(i * last / (CHART_WIDTH - 1))] for i in range(CHART_WIDTH))

    width = len(points)
    lines = []
    for level in CHART_ROWS:
        bar = "".join("#" if rate >= level else " " for _, rate in points)
        lines.append(f"{level:>4.0%} |{bar}")

    lines.append("     +" + "-" * width)

    # The two ends of the x-axis, padded to the chart's own width so the label cannot grow
    # wider than the thing it labels.
    start, end = str(points[0][0]), str(points[-1][0])
    gap = max(1, width - len(start) - len(end))
    lines.append(f"      {start}{' ' * gap}{end} corrections")
    return lines


# ------------------------------------------------------------------------- confidence


def report_thresholds(console: Console, measured, declared: float) -> None:
    """What the skill's threshold does against what was just measured.

    Neither number says this on its own, and their product is surprising: the reference is
    the median, so **a threshold of 0.5 asks for help on half of everything**. That is not
    a defect in the scale or in the threshold — it is what the two mean together, and the
    only way to find it out was to run and get an episode that stopped at step zero with
    nothing on screen explaining why.

    Which threshold is right is not answerable from here. It depends on what goes wrong
    when nobody is asked, which is visible only in episodes where somebody took over —
    ADR 0003, still v0.3. What this gives is the other half of that decision: what each
    choice costs in interruptions, measured on the predictions that actually happened.
    """
    console.print()
    console.print(
        f"[bold]at this skill's threshold of {declared:g}, "
        f"{measured.ask_rate(declared):.0%} of these predictions would have asked for "
        f"help[/bold]"
    )

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("threshold")
    table.add_column("asks on", justify="right")
    for candidate in THRESHOLD_CHOICES:
        marker = "  <- skill.yaml" if abs(candidate - declared) < 1e-9 else ""
        table.add_row(f"{candidate:g}", f"{measured.ask_rate(candidate):.0%}{marker}")
    console.print(table)

    console.print()
    console.print(
        "[dim]Which of these is right is not a question this measurement can answer. It "
        "depends on what goes wrong when nobody is asked, which is only visible in "
        "episodes where somebody took over - ADR 0003, still v0.3. What the table gives "
        "you is what each choice costs in interruptions.[/dim]"
    )
