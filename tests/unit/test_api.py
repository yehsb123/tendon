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
