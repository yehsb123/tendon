"""`test_boundaries.py` has to pass with none of the project's dependencies installed.

The CI job that runs it installs pytest and nothing else, on purpose: the file reads source
with `ast`, and a check of what the kernel depends on means more when it cannot lean on
those dependencies being present. That held until an autouse fixture in `tests/conftest.py`
imported `tendon.drivers.base` for every test, which pulls in pydantic through the kernel.
Every boundary test then errored at setup, before reading a single file, and the job stayed
red for weeks while the checks it exists for ran nowhere.

Nothing local could notice, because every machine that runs the suite has the dependencies
installed. So this runs the file in a child process where each runtime dependency named in
`pyproject.toml` is shadowed by a module that refuses to import, the way an absent package
does.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Distribution names whose import name differs.
IMPORT_NAMES = {"pyyaml": "yaml"}


def runtime_dependencies() -> set[str]:
    """Import names of `[project] dependencies`, read from `pyproject.toml`.

    Derived rather than listed, so a dependency added later is shadowed too. Regex rather
    than `tomllib` because the unit job still runs on 3.10.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    # Up to a `]` that opens a line, because `uvicorn[standard]` closes a bracket earlier.
    body = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.MULTILINE | re.DOTALL)
    assert body, "no [project] dependencies in pyproject.toml"
    names = set()
    for requirement in re.findall(r'"([A-Za-z][A-Za-z0-9_.\-]*)', body.group(1)):
        name = requirement.lower()
        names.add(IMPORT_NAMES.get(name, name.replace("-", "_")))
    return names


def test_the_shadow_list_knows_the_dependencies() -> None:
    """An empty set would shadow nothing and let the run below pass on a full install."""
    assert {"pydantic", "numpy", "yaml"} <= runtime_dependencies()


def test_boundary_checks_run_without_the_project_dependencies(tmp_path: Path) -> None:
    for name in runtime_dependencies():
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text(
            f'raise ModuleNotFoundError("No module named {name!r}", name={name!r})\n',
            encoding="utf-8",
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_boundaries.py",
            "-q",
            "-p",
            "no:randomly",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO,
        env={
            **os.environ,
            # PYTHONPATH comes before site-packages, so the shadows win over the real packages.
            "PYTHONPATH": str(tmp_path),
            # The CI job has pytest and no plugins. A plugin installed on this machine can
            # import a shadowed package itself (langsmith's imports pydantic) and fail the
            # run for a reason the job would never see.
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
