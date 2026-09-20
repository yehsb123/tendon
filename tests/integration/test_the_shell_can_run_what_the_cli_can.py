"""The operator's seat can supervise the policies the command line can run.

`POST /api/sessions` built one policy and only one: a synthetic joint sweep with an
`UncertainRegion` placed by hand. It read neither `policy.base` nor `policy.adapter` from
the skill, and the request had nowhere to ask.

So the single screen where a human supervises a policy **could never supervise a real
one**, every correction collected through the shell was a correction to a sweep, and the
v0.3 experiment — a trained policy with a real operator — could not be run through the
interface it exists for. The CLI grew `--policy adapter` and this did not.

The asymmetry is the property under test. Which policies exist will change; that the two
surfaces offer the same ones should not.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    from tendon.api.app import create_app

    return TestClient(
        create_app(
            skill_root=REPO / "skills",
            episode_root=tmp_path / "episodes",
            memory_root=tmp_path / "memory",
            progress_root=tmp_path / "progress",
        )
    )


def test_the_request_can_name_a_policy() -> None:
    from tendon.api.app import StartRequest

    assert "policy" in StartRequest.model_fields
    assert StartRequest(skill="grasp/cube-sim").policy == "scripted", (
        "the default has to need no weights, so a clean checkout still opens the shell"
    )


def test_both_surfaces_offer_the_same_policies() -> None:
    """The CLI's set is the definition; the API is checked against it.

    Not a hardcoded pair of lists — two lists would drift, which is how this gap opened in
    the first place. `replay:` is CLI-only by design: it takes a store reference and there
    is nothing for an operator to supervise in a recording being played back.
    """
    from tendon.api.app import _adapter_for, _scripted_for
    from tendon.cli.policies import RUNNABLE_POLICIES

    supervisable = RUNNABLE_POLICIES - {"replay"}
    offered = {"scripted", "adapter"}

    assert supervisable == offered, (
        f"the CLI can run {sorted(supervisable)} and the shell can start {sorted(offered)}; "
        f"a policy a person cannot supervise is one the v0.3 experiment cannot use"
    )
    assert callable(_scripted_for) and callable(_adapter_for)


def test_the_api_loads_an_adapter_through_the_same_code_as_the_cli() -> None:
    """A policy built differently here than on the command line would make a session and a
    `tendon run` incomparable — and comparing them is what the v0.3 graph does."""
    from tendon.api.app import _adapter_for, resolve_adapter

    assert "load_adapter" in inspect.getsource(_adapter_for), (
        "the API is constructing a policy of its own"
    )
    assert "adapter_base" in inspect.getsource(resolve_adapter), (
        "the base has to come from the adapter, not from the skill a person edits"
    )


def test_the_refusals_happen_before_a_session_exists() -> None:
    """The policy is built on the episode thread, where nothing raised can be returned.

    Every check that needs no weights therefore has to run in the handler, or the caller
    gets `200 OK` and a session that dies quietly a moment later — which is exactly what
    the first version of this did. `tendon run` needed the same surgery for the same
    reason, one layer down: its adapter check ran after `open_body`.
    """
    from tendon.api.app import create_app, resolve_adapter

    handler = inspect.getsource(create_app)
    start = handler.partition("async def start_session")[2].partition("\n    @app.")[0]

    assert "resolve_adapter(" in start, "the adapter is resolved on the episode thread"
    assert start.index("resolve_adapter(") < start.index("open_body("), (
        "a misspelled path opens a body before it is refused"
    )
    assert "HTTPException" in inspect.getsource(resolve_adapter)


def test_asking_for_an_adapter_with_none_available_is_a_request_error(client: TestClient) -> None:
    """400 rather than 500: the runtime is fine, the request asked for something it did
    not supply. And the message says how to get one, in the words `tendon run` uses."""
    response = client.post(
        "/api/sessions", json={"skill": "grasp/cube-sim", "body": "mujoco", "policy": "adapter"}
    )

    assert response.status_code == 400, response.text
    assert "tendon train" in response.json()["detail"]


def test_a_path_holding_no_adapter_is_named(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/sessions",
        json={
            "skill": "grasp/cube-sim",
            "body": "mujoco",
            "policy": "adapter",
            "adapter": str(tmp_path / "nothing"),
        },
    )

    assert response.status_code == 400
    assert "no adapter at" in response.json()["detail"]


def test_an_adapter_for_another_checkpoint_is_refused(client: TestClient, tmp_path: Path) -> None:
    """A LoRA on different weights loads, runs, and is wrong with nothing to see. The same
    refusal the CLI makes, for the same reason."""
    import json

    directory = tmp_path / "wrong-base"
    directory.mkdir()
    (directory / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": "lerobot/act_aloha"}), encoding="utf-8"
    )

    response = client.post(
        "/api/sessions",
        json={
            "skill": "grasp/cube-sim",
            "body": "mujoco",
            "policy": "adapter",
            "adapter": str(directory),
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "lerobot/act_aloha" in detail
    assert "lerobot/smolvla_base" in detail


def test_the_shell_sends_the_choice() -> None:
    """A field the API accepts and the shell never sets is the same defect one layer over:
    the capability exists and the operator cannot reach it."""
    client_ts = (REPO / "shell" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    store = (REPO / "shell" / "src" / "state" / "session.ts").read_text(encoding="utf-8")
    view = (REPO / "shell" / "src" / "views" / "Live.tsx").read_text(encoding="utf-8")

    assert "policy," in client_ts or "policy:" in client_ts, "the request never carries it"
    assert "chosenPolicy" in store
    assert "onChoosePolicy" in view, "nothing on screen can change it"


def test_the_scripted_default_still_says_its_uncertainty_is_placed() -> None:
    """Adding a real policy does not make the stand-in honest. Whichever is selected, the
    screen has to say which one is running and what its uncertainty is (ADR 0003)."""
    from tendon.api.app import _scripted_for

    source = inspect.getsource(_scripted_for)

    assert "UncertainRegion" in source
    assert "stand-in" in source, "the placeholder is no longer named as one"
