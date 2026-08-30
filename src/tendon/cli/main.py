"""The tendon command.

Mirrors the OS metaphor so the commands are guessable. Every one of them must work
against the MuJoCo driver with no hardware attached.
"""

from __future__ import annotations

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
def run(skill: str, driver: str = "mujoco") -> None:
    """Start a policy under the kernel."""
    raise NotImplementedError("v0.1")


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


@app.command()
def eval(skill: str, episodes: int = 50) -> None:
    """Run the evaluation set for a skill."""
    raise NotImplementedError("v0.3")


if __name__ == "__main__":
    app()
