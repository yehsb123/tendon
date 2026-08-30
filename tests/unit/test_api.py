"""The API is a boundary, so these tests check that it stays one.

Nothing here should decide anything. What is tested is that it reports what the runtime
actually has — including the awkward cases, since a shell that is told a skill exists when
it does not load is a shell that shows an operator a run they cannot start.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tendon import __version__
from tendon.api.app import create_app

SKILLS = Path(__file__).resolve().parents[2] / "skills"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(skill_root=SKILLS))


def test_health_reports_the_runtime_version(client: TestClient) -> None:
    """The shell shows this because a shell built against a different contract is the
    failure that looks like a bug everywhere else."""
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_bodies_lists_registered_drivers(client: TestClient) -> None:
    """Registered, not installed. A driver whose backend is missing never registers."""
    names = {b["name"] for b in client.get("/api/bodies").json()}
    assert isinstance(names, set)


def test_skills_lists_the_shipped_skill(client: TestClient) -> None:
    refs = {s.get("ref") for s in client.get("/api/skills").json()}
    assert "grasp/cube-sim" in refs


def test_a_listed_skill_carries_what_the_shell_needs(client: TestClient) -> None:
    skill = next(s for s in client.get("/api/skills").json() if s.get("ref") == "grasp/cube-sim")

    assert skill["version"]
    assert skill["confidence_threshold"] == pytest.approx(0.5)
    assert skill["requires"]["dof"] == 5
    assert skill["safety"]["max_joint_velocity"] == pytest.approx(1.5)


def test_a_broken_skill_is_listed_with_its_error(tmp_path: Path) -> None:
    """Silently dropping it leaves someone staring at a directory that exists and a list
    that does not mention it."""
    broken = tmp_path / "ns" / "broken"
    broken.mkdir(parents=True)
    (broken / "skill.yaml").write_text("apiVersion: nope\nkind: Skill\n", encoding="utf-8")

    listed = TestClient(create_app(skill_root=tmp_path)).get("/api/skills").json()
    assert len(listed) == 1
    assert "error" in listed[0]
    assert "apiVersion" in listed[0]["error"]


def test_an_empty_skill_root_lists_nothing_rather_than_failing(tmp_path: Path) -> None:
    assert TestClient(create_app(skill_root=tmp_path)).get("/api/skills").json() == []


def test_skill_detail_includes_success_criteria(client: TestClient) -> None:
    detail = client.get("/api/skills/grasp/cube-sim").json()
    conditions = {c["condition"] for c in detail["success_criteria"]}
    assert "cube_height_above" in conditions


def test_an_unknown_skill_is_a_404(client: TestClient) -> None:
    assert client.get("/api/skills/nope/missing").status_code == 404


def test_compatibility_is_exposed_so_the_shell_can_grey_out_a_body(client: TestClient) -> None:
    """Better than letting an operator start a run that fails at load."""
    pytest.importorskip("mujoco", reason="needs the sim extra")

    result = client.get("/api/skills/grasp/cube-sim/compatibility/mujoco").json()
    assert result["compatible"] is True
    assert result["reasons"] == []


def test_compatibility_with_an_unknown_body_is_a_404(client: TestClient) -> None:
    assert client.get("/api/skills/grasp/cube-sim/compatibility/nosuch").status_code == 404


def test_the_driver_hint_survives_rich_markup() -> None:
    """The same bug as the doctor remedy, in a hardcoded CLI hint.

    `pip install -e ".[sim]"` printed as `pip install -e "."` — a command that runs,
    installs the wrong thing, and gives no sign anything was lost. Caught once in doctor
    and reintroduced here, which is why it now has a test on both paths.
    """
    from typer.testing import CliRunner

    from tendon.cli.main import app

    result = CliRunner().invoke(app, ["run", "skills/grasp/cube-sim", "--driver", "nosuch"])
    assert 'pip install -e ".[sim]"' in result.output


# ------------------------------------------------------- simulated versus in the room


def test_bodies_report_whether_they_move_real_hardware(client: TestClient) -> None:
    """The shell shows this before anything else. An operator approving a motion needs to
    know which kind of body they are approving it for."""
    for body in client.get("/api/bodies").json():
        assert "simulated" in body, f"{body['name']} does not say whether it is a simulator"


def test_starting_a_physical_body_is_refused_with_a_reason(client: TestClient) -> None:
    """403 rather than 500.

    Letting `PhysicalBodyRefused` escape as a server error made a safety decision look
    like a bug, and left the shell with nothing to show — the operator saw a generic
    failure where the runtime had a specific and correct objection.
    """
    physical = [b["name"] for b in client.get("/api/bodies").json() if not b["simulated"]]
    if not physical:
        pytest.skip("no physical driver is registered in this environment")

    response = client.post(
        "/api/sessions",
        json={"skill": "skills/grasp/cube-sim", "body": physical[0]},
    )

    assert response.status_code == 403
    assert "SECURITY.md" in response.json()["detail"]


def test_reaching_hardware_is_never_something_a_request_does_by_omission(
    client: TestClient,
) -> None:
    """`allow_physical` defaults to false in the request model.

    A body that moves in the room must require someone to have said so, not merely to have
    left a field out.
    """
    from tendon.api.app import StartRequest

    assert StartRequest(skill="x").allow_physical is False


# ------------------------------------------------- what the shell calls, the server has


def test_every_path_the_shell_calls_exists_on_the_server(client: TestClient) -> None:
    """A path the shell calls and the server does not serve is a 404 the operator sees as
    "something went wrong", with no way to tell a typo from an outage.

    Read from the client source rather than maintained by hand, because a list of paths
    kept in a test is one more copy to drift.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    source = (repo / "shell" / "src" / "api" / "client.ts").read_text(encoding="utf-8")

    called = set()
    for raw in re.findall(r"[\"`](/api/[^\"`]*)", source):
        # Template placeholders become path parameters.
        called.add(re.sub(r"\$\{[^}]+\}", "{}", raw).rstrip("/"))

    served = set()
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api"):
            served.add(re.sub(r"\{[^}]+\}", "{}", path).rstrip("/"))

    missing = sorted(called - served)
    assert not missing, f"the shell calls {missing}, which the server does not serve"


def test_the_compatibility_endpoint_is_actually_used() -> None:
    """It was built so the shell could refuse to offer an impossible run, and then went
    unused while the shell hardcoded a skill and a body.

    An endpoint nobody calls is a maintained thing that verifies nothing — the same shape
    as the declarations deleted in the previous round.
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    source = (repo / "shell" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    assert "compatibility" in source
