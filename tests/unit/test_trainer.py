"""`services/trainer.py`, the arrow from collecting back to running.

The half that decides *what a policy learns from* is separated from the half that needs a
GPU, precisely so it can be tested. These cover the first half and the guards around the
second.

`fine_tune` itself is not exercised here: it needs a checkpoint and a dataset of the same
shape, and this repository records a five-joint arm with no pretrained policy to match.
What was verified by hand against `lerobot/smolvla_base` is written down in
`docs/collaboration.md` — LoRA attaches, and the adapter is 607x smaller than the model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tendon.services.trainer import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_LORA_RANK,
    Trainer,
    TrainerError,
    TrainingRun,
)


def test_an_empty_selection_is_refused(tmp_path: Path) -> None:
    """A curator that ranked nothing is not a training set.

    Refused before anything is loaded, because discovering it after a checkpoint is in
    memory wastes the interesting part of a nightly run.
    """
    with pytest.raises(TrainerError) as caught:
        Trainer(root=tmp_path).build_dataloader([])
    assert "empty ranking" in str(caught.value)


def test_a_missing_store_is_refused_by_path(tmp_path: Path) -> None:
    """Names the path, because the usual cause is a `root` pointing somewhere else."""
    pytest.importorskip("torch")
    pytest.importorskip("lerobot")

    trainer = Trainer(root=tmp_path / "not-here")
    with pytest.raises(TrainerError) as caught:
        trainer.build_dataloader([0])
    assert "no episode store" in str(caught.value)


def test_the_missing_extra_is_named_rather_than_traced(tmp_path: Path, monkeypatch) -> None:
    """A missing dependency should say which install fixes it.

    An ImportError from four frames inside a vendor package tells a user that something
    is wrong, not what to do about it.
    """
    import builtins

    real_import = builtins.__import__

    def refuse_lerobot(name, *args, **kwargs):
        if name.startswith("lerobot"):
            raise ImportError("No module named 'lerobot'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_lerobot)

    with pytest.raises(TrainerError) as caught:
        Trainer(root=tmp_path).build_dataloader([0])
    assert "tendon-os[robot,train]" in str(caught.value)


def test_the_defaults_are_the_ones_the_constraint_implies() -> None:
    """Both exist because the loop has to close overnight on one consumer card.

    A rank or a batch size that drifts upward turns a nightly run into a weekend one, and
    design decision 1 requires the loop to close every night to be real.
    """
    assert DEFAULT_LORA_RANK == 16
    assert DEFAULT_BATCH_SIZE == 8


def test_a_run_reports_how_much_of_the_model_it_trained() -> None:
    """The number that says whether LoRA attached at all.

    A run that quietly trained everything would still converge and would still be useless
    as a per-site adapter, and nothing downstream would notice.
    """
    run = TrainingRun(
        adapter_path=Path("adapter"),
        skill="grasp/cube-sim",
        base_policy="lerobot/smolvla_base",
        episodes=(0, 2),
        frames=240,
        steps=2000,
        final_loss=0.12,
        trainable_parameters=742_656,
        total_parameters=450_046_176,
    )

    assert run.trainable_fraction == pytest.approx(0.00165, abs=1e-4)
    assert run.trainable_fraction < 0.01, "an adapter this size is not shippable in a skill"


def test_a_run_with_no_parameter_counts_reports_zero_rather_than_dividing_by_zero() -> None:
    run = TrainingRun(
        adapter_path=Path("adapter"),
        skill="s",
        base_policy="p",
        episodes=(),
        frames=0,
        steps=0,
        final_loss=float("nan"),
    )
    assert run.trainable_fraction == 0.0


def test_the_selection_is_carried_into_the_result() -> None:
    """Which episodes trained a policy is the part tendon adds; losing it loses the claim."""
    run = TrainingRun(
        adapter_path=Path("adapter"),
        skill="grasp/cube-sim",
        base_policy="lerobot/smolvla_base",
        episodes=(3, 1, 7),
        frames=120,
        steps=100,
        final_loss=0.4,
    )
    assert run.episodes == (3, 1, 7)
