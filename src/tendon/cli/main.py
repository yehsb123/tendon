"""The tendon command.

Mirrors the OS metaphor so the commands are guessable. Every one of them must work
against the MuJoCo driver with no hardware attached.
"""

from __future__ import annotations

import typer

from tendon import __version__

app = typer.Typer(
    name="tendon",
    help="The operating layer for physical AI.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the tendon version."""
    typer.echo(__version__)


@app.command()
def doctor() -> None:
    """Check drivers, GPU, disk and Hub auth before anything else is attempted."""
    raise NotImplementedError("v0.1")


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
