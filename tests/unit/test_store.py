"""Reading the episode store.

Written against the layout on disk rather than through LeRobot, so it runs on a machine
that cannot record. That constraint is the point of the module and these tests hold it in
place: importing `recorder` here would pull in LeRobot and make the unit suite need Python
3.12 and an optional extra to answer "what have I recorded?".
"""

from __future__ import annotations

import json
from pathlib import Path

from tendon.services.store import human_size, list_datasets


def make_dataset(
    root: Path, name: str, *, episodes: int | None = 3, bytes_of_data: int = 128
) -> Path:
    directory = root / name
    (directory / "meta").mkdir(parents=True)
    (directory / "data.bin").write_bytes(b"x" * bytes_of_data)

    if episodes is not None:
        (directory / "meta" / "info.json").write_text(
            json.dumps({"total_episodes": episodes, "codebase_version": "v2.1"}),
            encoding="utf-8",
        )
    return directory


# ------------------------------------------------------------------------- listing


def test_an_empty_store_is_not_an_error(tmp_path: Path) -> None:
    """The normal state before anything has run."""
    assert list_datasets(tmp_path) == ()


def test_a_missing_store_is_not_an_error(tmp_path: Path) -> None:
    assert list_datasets(tmp_path / "never-created") == ()


def test_a_dataset_is_listed_with_its_episode_count(tmp_path: Path) -> None:
    make_dataset(tmp_path, "grasp__cube-sim", episodes=7)

    datasets = list_datasets(tmp_path)
    assert len(datasets) == 1
    assert datasets[0].episodes == 7
    assert datasets[0].readable


def test_the_directory_name_maps_back_to_a_skill_ref(tmp_path: Path) -> None:
    """The recorder encodes `grasp/cube-sim` as `grasp__cube-sim`, and a person asked to
    read the encoded form is being asked to do the computer's job."""
    make_dataset(tmp_path, "grasp__cube-sim")
    assert list_datasets(tmp_path)[0].ref == "grasp/cube-sim"


def test_size_is_measured_from_what_is_actually_there(tmp_path: Path) -> None:
    make_dataset(tmp_path, "a", bytes_of_data=1000)
    assert list_datasets(tmp_path)[0].size_bytes >= 1000


def test_files_that_are_not_directories_are_ignored(tmp_path: Path) -> None:
    make_dataset(tmp_path, "real")
    (tmp_path / "stray.txt").write_text("not a dataset", encoding="utf-8")

    assert [d.directory for d in list_datasets(tmp_path)] == ["real"]


def test_newest_first(tmp_path: Path) -> None:
    import os
    import time

    make_dataset(tmp_path, "older")
    time.sleep(0.01)
    newer = make_dataset(tmp_path, "newer")
    os.utime(newer, (time.time() + 10, time.time() + 10))

    assert [d.directory for d in list_datasets(tmp_path)][0] == "newer"


# --------------------------------------------------------------------- unreadable


def test_a_dataset_without_metadata_is_reported_not_skipped(tmp_path: Path) -> None:
    """Something on disk that cannot be read is a more useful thing to know about than a
    shorter list. A partial write looks exactly like this."""
    make_dataset(tmp_path, "partial", episodes=None)

    dataset = list_datasets(tmp_path)[0]
    assert dataset.episodes is None
    assert not dataset.readable
    assert "info.json" in (dataset.unreadable_because or "")


def test_broken_metadata_is_reported_with_the_reason(tmp_path: Path) -> None:
    directory = make_dataset(tmp_path, "corrupt", episodes=None)
    (directory / "meta" / "info.json").write_text("{not json", encoding="utf-8")

    dataset = list_datasets(tmp_path)[0]
    assert not dataset.readable
    assert "could not be read" in (dataset.unreadable_because or "")


def test_metadata_without_an_episode_count_is_reported(tmp_path: Path) -> None:
    directory = make_dataset(tmp_path, "odd", episodes=None)
    (directory / "meta" / "info.json").write_text(json.dumps({"fps": 30}), encoding="utf-8")

    dataset = list_datasets(tmp_path)[0]
    assert dataset.episodes is None
    assert "total_episodes" in (dataset.unreadable_because or "")


def test_an_unreadable_dataset_still_reports_its_size(tmp_path: Path) -> None:
    """Knowing that 4 GB of something unreadable is sitting there is the useful half."""
    make_dataset(tmp_path, "partial", episodes=None, bytes_of_data=2048)
    assert list_datasets(tmp_path)[0].size_bytes >= 2048


# -------------------------------------------------------------------------- format


def test_human_size_reads_at_a_glance() -> None:
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"
    assert human_size(3 * 1024**3) == "3.0 GB"


def test_the_store_module_does_not_import_lerobot() -> None:
    """The constraint the module exists under.

    Importing the recorder would pull in LeRobot, which needs Python 3.12 and an optional
    extra — and "what have I recorded?" is a question someone should be able to ask on a
    machine that cannot currently record anything.
    """
    source = (Path(__file__).resolve().parents[2] / "src/tendon/services/store.py").read_text(
        encoding="utf-8"
    )

    assert "import lerobot" not in source
    assert "from lerobot" not in source
    assert "from tendon.services.recorder" not in source
