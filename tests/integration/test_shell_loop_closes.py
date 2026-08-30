"""The claim of the project, through the interface rather than through a script.

`examples/04_improve` shows the intervention rate falling, and `tests/integration/
test_improve_example.py` holds that example to it. But that example wires the loop by hand
in a file nobody runs in production. The question this file asks is whether the same thing
happens when episodes are started the way an operator starts them: through the API, with a
correction sent from the shell.

Until three rounds ago it could not have. `create_app` dropped the step bus, then wired no
`on_intervention`, then built a fresh correction memory per session — so the rate through
the shell was flat by construction, whatever the operator did.

## Why there is a control

A falling rate on its own proves very little. A policy that stopped handing over for any
reason would produce the same line, and so would one that never handed over at all. So the
same episodes are run twice: once correcting, once only approving. Approving is the null
treatment — `learn_from` stores nothing for it, deliberately, because an approval says the
policy was right rather than saying what to do instead.

If the rate falls in both arms, the fall is not caused by teaching and this file is
measuring something else.

## What is not asserted

How many interrupts happen, or how far the rate falls. Those depend on where the uncertain
region sits, the recall radius and the shape of the sweep; pinning them would make this a
test of those constants, failing whenever somebody tunes one for a reason that says nothing
about whether the loop closes. Only the direction is checked, and only against a control.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mujoco", reason="needs the sim extra: pip install -e '.[sim]'")
pytest.importorskip("lerobot", reason="needs the recording extra: pip install -e '.[robot]'")

from tendon.api.app import create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

#: Short enough to keep this affordable, long enough that the sweep reaches the uncertain
#: region the policy hands over in. The effect shows up immediately when it shows up at all.
EPISODES = 6
STEPS = 40


def corrected_intent(context: dict) -> dict:
    """What the shell's correction editor produces: the policy's own plan, nudged."""
    intent = dict(context["intent"])
    intent["actions"] = [
        {**action, "values": [v + 0.01 for v in action["values"]]} for action in intent["actions"]
    ]
    return intent


def run_arm(tmp_path: Path, *, correct: bool) -> list[int]:
    """Run a series of episodes through one app, answering every interrupt.

    One app for the whole series, because the correction memory lives on it — that is the
    thing under test. A fresh app per episode would reproduce the bug this checks for.
    """
    client = TestClient(
        create_app(
            skill_root=REPO / "skills", episode_root=tmp_path / ("taught" if correct else "told")
        )
    )

    counts: list[int] = []
    for index in range(EPISODES):
        response = client.post(
            "/api/sessions",
            json={
                "skill": "grasp/cube-sim",
                "body": "mujoco",
                "max_steps": STEPS,
                # A different start each episode, deterministic in the index so a failure
                # here is reproducible rather than a thing that happened once.
                "seed": index,
            },
        )
        assert response.status_code == 200, response.text
        session_id = response.json()["session_id"]

        interrupts = 0
        with client.websocket_connect(f"/ws/{session_id}") as socket:
            deadline = time.time() + 90
            while time.time() < deadline:
                message = socket.receive_json()

                if message.get("type") == "interrupt":
                    interrupts += 1
                    body = (
                        {
                            "resolution": "corrected",
                            "correction": corrected_intent(message["context"]),
                        }
                        if correct
                        else {"resolution": "approved"}
                    )
                    client.post(f"/api/sessions/{session_id}/decide", json=body)

                if message.get("type") == "finished":
                    break

        counts.append(interrupts)

    return counts


@pytest.fixture(scope="module")
def arms(tmp_path_factory):
    """Both arms, run once. The expensive fixture in this suite, and the point of it."""
    root = tmp_path_factory.mktemp("loop")
    return {"taught": run_arm(root, correct=True), "told": run_arm(root, correct=False)}


# ------------------------------------------------------------------ it starts high


def test_the_policy_asks_for_help_to_begin_with(arms) -> None:
    """Nothing below means anything without this. A run that never hands over draws a flat
    line at zero and looks exactly like a solved problem."""
    assert sum(arms["taught"][:2]) > 0
    assert sum(arms["told"][:2]) > 0


# ------------------------------------------------------------------- and then falls


def test_teaching_it_makes_it_ask_less(arms) -> None:
    """The claim.

    Compared as first episodes against last rather than by fitting a line: the question is
    whether teaching changed how often it asks, and that answers it without inventing a
    model of the curve.
    """
    taught = arms["taught"]
    before = sum(taught[:2]) / 2
    after = sum(taught[2:]) / (EPISODES - 2)

    assert after < before, f"interventions did not fall through the shell: {taught}"


def test_only_approving_never_stops_it_asking(arms) -> None:
    """The control, and the reason the test above is worth anything.

    An approval is stored nowhere on purpose, so a policy that is only approved keeps
    handing over.

    This first said the control arm was *flat* — `after >= before` — and that is not true.
    Each episode starts from a different seed, so how many times the sweep crosses the
    uncertain region varies by one either way, and a run of `[2, 1, 1, 1, 1, 1]` failed an
    assertion that nothing had been taught. Nothing had been. The assertion was measuring
    the start state.

    A test that passes on about half its runs is worse than no test, because the half that
    passes is the half people remember. So this asserts what is actually true of the
    control: it goes on asking. The causal claim lives in the comparison below, where it
    belongs.
    """
    told = arms["told"]

    assert sum(told[2:]) > 0, f"the policy stopped asking without being taught: {told}"


def test_the_two_arms_end_apart(arms) -> None:
    """Stated as the comparison rather than as two separate trends, because that is the
    sentence the README makes: correct it and it asks you less often than if you had not."""
    taught_end = sum(arms["taught"][2:])
    told_end = sum(arms["told"][2:])

    assert taught_end < told_end, (
        f"correcting ended no better than approving: {taught_end} vs {told_end}"
    )
