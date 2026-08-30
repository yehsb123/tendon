"""What the interface calls "safety limits" has to be what gets enforced.

Adding a machine-level ceiling last round created this: the scheduler started checking
against the tightened limits, and `/api/skills/{ns}/{name}` went on reporting the numbers in
`skill.yaml`. The `Skills` view exists so somebody deciding whether to approve a motion can
read what that motion is not allowed to do — and it was answering with the looser figure.

A view that is wrong in the safe direction is a nuisance. This one was wrong in the other
direction: it showed more freedom than the system would actually permit.

## Why `declared` is kept

Removing it would leave an operator comparing this screen against the file and finding two
different numbers with no explanation. The screen says which is enforced, shows what was
asked for, and says a ceiling narrowed it — so the difference reads as a control rather
than as a bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tendon.api.app import create_app

REPO = Path(__file__).resolve().parents[2]


def app_with_ceiling(tmp_path: Path, text: str | None):
    """An app whose machine-level limits file holds `text`, or none at all."""
    import tendon.services.limits as limits_module

    path = tmp_path / "limits.yaml"
    if text is not None:
        path.write_text(text, encoding="utf-8")

    patch = pytest.MonkeyPatch()
    patch.setattr(limits_module, "DEFAULT_LIMITS_PATH", path)
    return TestClient(create_app(skill_root=REPO / "skills")), patch


def detail(client: TestClient) -> dict:
    response = client.get("/api/skills/grasp/cube-sim")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------- with a ceiling


def test_it_reports_the_tightened_limit(tmp_path: Path) -> None:
    """The number the scheduler will check against, not the one the file asked for."""
    client, patch = app_with_ceiling(tmp_path, "safety:\n  max_joint_velocity: 0.5\n")
    try:
        body = detail(client)
    finally:
        patch.undo()

    assert body["safety"]["max_joint_velocity"] == 0.5


def test_it_still_says_what_the_skill_asked_for(tmp_path: Path) -> None:
    """So the difference from `skill.yaml` reads as a control rather than a bug."""
    client, patch = app_with_ceiling(tmp_path, "safety:\n  max_joint_velocity: 0.5\n")
    try:
        body = detail(client)
    finally:
        patch.undo()

    assert body["declared"]["max_joint_velocity"] > body["safety"]["max_joint_velocity"]
    assert body["capped"] is True


# ------------------------------------------------------------ and without one


def test_without_a_ceiling_the_two_agree(tmp_path: Path) -> None:
    """Every installation before this existed. An absent file is not a ceiling of zero and
    not a licence — it is the skill's own limits, unchanged."""
    client, patch = app_with_ceiling(tmp_path, None)
    try:
        body = detail(client)
    finally:
        patch.undo()

    assert body["safety"] == body["declared"]
    assert body["capped"] is False


def test_a_broken_ceiling_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    """The failure that matters most here.

    Falling back to the declared limits would answer the question confidently and wrongly
    while a broken ceiling sat on disk — and the operator would have no way to tell that
    the bound they configured was not in force.
    """
    client, patch = app_with_ceiling(tmp_path, "safety: [not, a, mapping]\n")
    try:
        response = client.get("/api/skills/grasp/cube-sim")
    finally:
        patch.undo()

    assert response.status_code == 500
    assert "limits" in response.text


def test_the_list_reports_the_tightened_limit_too(tmp_path: Path) -> None:
    """The route this file missed the first time.

    Only the detail route was fixed, and only the detail route was tested, so the list —
    which is where somebody looks first — went on answering with the skill's own numbers
    while the scheduler enforced something tighter. A bug can survive being found if the
    test written for it is as narrow as the fix.
    """
    client, patch = app_with_ceiling(tmp_path, "safety:\n  max_joint_velocity: 0.5\n")
    try:
        listed = client.get("/api/skills").json()
    finally:
        patch.undo()

    entry = next(s for s in listed if s.get("ref") == "grasp/cube-sim")
    assert entry["safety"]["max_joint_velocity"] == 0.5

    # No `capped` here. The first version of this fix added one, and nothing reads it: the
    # shell's list does not show limits, and the explanation of *why* a number narrowed
    # belongs on the detail view where the number is read. Correcting a wrong figure is the
    # job; adding a field nobody looks at is a different one.
    assert "capped" not in entry


def test_every_route_that_reports_limits_reports_the_effective_ones() -> None:
    """Checked against the routes rather than against a list I remember to update.

    The fix that missed the list route was written by somebody who knew about the ceiling
    and still only thought of one place. Naming the shape instead: no handler serialises a
    skill's own limits under a key called `safety`.
    """
    source = (REPO / "src/tendon/api/app.py").read_text(encoding="utf-8")

    assert '"safety": loaded.limits' not in source, (
        "a route reports a skill's declared limits as its safety limits, which is the "
        "looser number and not the one that will be enforced"
    )


def test_a_broken_ceiling_fails_the_list_as_well(tmp_path: Path) -> None:
    """Consistent with the detail route. A list that quietly showed declared limits while a
    broken ceiling sat on disk would be the same wrong answer in the place people read
    first."""
    client, patch = app_with_ceiling(tmp_path, "safety: [not, a, mapping]\n")
    try:
        response = client.get("/api/skills")
    finally:
        patch.undo()

    assert response.status_code == 500


def test_a_session_and_the_view_use_the_same_calculation() -> None:
    """One function, because the two would otherwise drift.

    The session route decides what is enforced and the detail route describes it. A second
    copy of the tightening is how the description would eventually stop matching the thing
    it describes — which is what this whole file is about.
    """
    source = (REPO / "src/tendon/api/app.py").read_text(encoding="utf-8")

    # Assignments only. Counting every occurrence includes the definition, which is the
    # same mistake this project made once before when checking that a policy was built in
    # one place.
    # One definition and at least two callers. Pinning the *number* of callers was wrong
    # and failed the moment a third route was corrected — which is the change this file
    # exists to encourage, so the check must not punish it.
    assert source.count("def _effective(") == 1
    assert source.count("= _effective(loaded)") >= 2

    # `tighten` appears once, inside `_effective`. A second call would be a route doing the
    # calculation itself, which is the drift this is about — but the assertion has to allow
    # the one legitimate use rather than forbid the name outright.
    assert source.count("tighten(loaded.limits") == 1
