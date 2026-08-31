"""LoRA fine-tuning on curated episodes — where the loop closes.

`docs/concepts.md` claims running is collecting and that corrections become lessons. This
module is the arrow from the second back to the first. Without it the recorder is a very
careful way of filling a disk.

## The constraint that shapes everything here

Sized for one consumer GPU overnight, which is a design constraint rather than a limit to
be lifted later. Design decision 1 requires the loop to close *every night*; a training run
that needs a cluster does not close nightly, and a loop that does not close is not a loop.
That rules out full fine-tuning and rules in LoRA, whose adapters are also small enough to
version per site and ship inside a skill package.

## What is ours and what is not

Almost none of this is ours, which is the point of `docs/stack.md`.

`PreTrainedPolicy.wrap_with_peft()` freezes the base model, builds the adapter config and
returns the adapted policy. `get_optim_params()` gives the parameter groups the policy
wants trained. `LeRobotDataset(episodes=[...])` takes a subset by index.
`save_pretrained()` writes an adapter. The training step is the standard four lines.

What this module contributes is the one thing upstream has no opinion about: **which
episodes go in.** `services/curator.py` produces a ranking, and this consumes it. Upstream
trains on a dataset; tendon trains on a selection, and the selection is the claim.

## Why the split into two methods

`build_dataloader` is separate from `fine_tune` because the first half is checkable without
a GPU or a downloaded checkpoint, and the half that decides what a policy learns from
should not be the half that is hard to test. `benchmarks/` exercises it directly.

Requires the robot and train extras:  pip install "tendon-os[robot,train]"
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tendon.services.recorder import DEFAULT_REPO_ID, DEFAULT_ROOT

# LoRA rank. Small on purpose: the adapter is versioned per site and shipped inside a
# skill package, and rank is the term that decides how big that file is. 16 is the usual
# starting point for adapting a VLA to one task on one body.
DEFAULT_LORA_RANK = 16

# Batch size that fits a 12GB consumer card with a ~500M-parameter VLA and one camera.
# Raise it when the card is bigger; the nightly constraint is wall clock, not batches.
DEFAULT_BATCH_SIZE = 8


class TrainerError(RuntimeError):
    """Raised when a run cannot be set up or has nothing to learn from."""


#: Prefix the recorder writes camera frames under. Mirrors `services/policy_lerobot`.
_OBS_IMAGES = "observation.images"


def _camera_rename(policy: Any, batch_keys: Iterable[str]) -> dict[str, str]:
    """Map this store's camera keys onto the ones the checkpoint declares.

    A driver names its camera `wrist` and the recorder writes
    `observation.images.wrist`. `lerobot/smolvla_base` declares
    `observation.images.camera1`, `camera2` and `camera3`, so a batch built straight from
    the store contains no image the policy recognises, and it refuses the forward pass
    with "All image features are missing from the batch".

    The inference half of this loop already solved this: `LeRobotPolicy._image_key` sends a
    frame to whatever key the checkpoint asks for. Training did not, so a recording that
    ran fine through a policy could not be trained on -- same dataset, same checkpoint, two
    different answers about what a camera is called.

    Matched by name first, so a store already using the checkpoint's names is left alone,
    then by position. Position is the honest fallback: the names mean nothing to each other
    and refusing on a mismatch would only be right if they did.

    Cameras the checkpoint declares and this store does not have are left out. SmolVLA
    wants at least one, not all three.
    """
    features = getattr(getattr(policy, "config", None), "input_features", {}) or {}
    declared = [
        key for key, feature in features.items() if "VISUAL" in str(getattr(feature, "type", ""))
    ]
    ours = [key for key in batch_keys if key.startswith(_OBS_IMAGES)]
    if not declared or not ours:
        return {}

    rename: dict[str, str] = {}
    taken: set[str] = set()
    for key in ours:
        suffix = key[len(_OBS_IMAGES) :]
        exact = next((d for d in declared if d.endswith(suffix) and d not in taken), None)
        if exact is not None:
            taken.add(exact)
            if exact != key:
                rename[key] = exact
            continue
        spare = next((d for d in declared if d not in taken), None)
        if spare is not None:
            taken.add(spare)
            rename[key] = spare
    return rename


@dataclass(frozen=True)
class TrainingRun:
    """What a finished run produced.

    Returned rather than logged, because the caller — `tendon train`, or a nightly job —
    has to decide whether the result is worth publishing as a new skill version.
    """

    adapter_path: Path
    skill: str
    base_policy: str
    episodes: tuple[int, ...]
    frames: int
    steps: int
    final_loss: float
    #: Trainable parameters against total, which is the number that says whether LoRA
    #: actually attached. A run that quietly trained everything would still converge, and
    #: would still be useless as a per-site adapter.
    trainable_parameters: int = 0
    total_parameters: int = 0

    @property
    def trainable_fraction(self) -> float:
        return self.trainable_parameters / self.total_parameters if self.total_parameters else 0.0


class Trainer:
    """Fine-tunes a policy on a selection of recorded episodes."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        repo_id: str = DEFAULT_REPO_ID,
        device: str | None = None,
    ) -> None:
        """
        Args:
            root: Episode store, matching what `Recorder` wrote.
            repo_id: Dataset identifier inside that store.
            device: `cuda`, `cpu`, or None to pick whichever is available.
        """
        self._root = Path(root) if root is not None else DEFAULT_ROOT
        self._repo_id = repo_id
        self._device = device

    # ------------------------------------------------------------------ data

    def build_dataloader(
        self,
        episodes: list[int],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        num_workers: int = 0,
        shuffle: bool = True,
        action_horizon: int | None = None,
    ) -> tuple[Any, Any]:
        """Open the curated subset and wrap it in a loader.

        Returns `(dataset, dataloader)`. The dataset is returned as well because its
        `features` and `num_frames` are what a caller needs to decide whether a run is
        worth starting — a selection of four episodes is not a training set, and finding
        that out after loading a checkpoint wastes the interesting part of the night.

        Args:
            episodes: Episode indices, in the order `services/curator.py` ranked them.
                Passed to LeRobot as a subset filter, so nothing outside the selection is
                read off disk at all.
            batch_size: Frames per step.
            num_workers: Loader processes. Zero by default — on Windows each worker
                re-imports the module, and the gain is small next to a forward pass.
            shuffle: Shuffle frames across the selection. On, because consecutive frames
                from one episode are almost identical and a batch of them is effectively a
                batch of one.
        """
        if not episodes:
            raise TrainerError("no episodes selected; a curator returned an empty ranking")

        try:
            import torch
            from lerobot.datasets.lerobot_dataset import LeRobotDataset, LeRobotDatasetMetadata
        except ImportError as exc:  # pragma: no cover - depends on the extras
            raise TrainerError(
                "LeRobot and torch are required. Install the extras: "
                'pip install "tendon-os[robot,train]"'
            ) from exc

        dataset_root = self._root / self._repo_id.replace("/", "__")
        if not dataset_root.exists():
            raise TrainerError(f"no episode store at {dataset_root}")

        # A chunked policy is trained on a window of future actions, not one. SmolVLA
        # predicts 50 at a time; given a batch holding a single action its loss compares
        # tensors of different lengths and fails inside the model with a size mismatch that
        # names neither the dataset nor the horizon.
        #
        # `delta_timestamps` is how LeRobotDataset returns that window, and it is in
        # seconds, so the frame rate has to be read before the dataset is opened. Metadata
        # alone is enough for that and does not load any frames.
        delta_timestamps = None
        if action_horizon and action_horizon > 1:
            try:
                fps = LeRobotDatasetMetadata(self._repo_id, root=dataset_root).fps
            except Exception as exc:
                raise TrainerError(
                    f"could not read the frame rate at {dataset_root}: {exc}"
                ) from exc
            delta_timestamps = {"action": [i / fps for i in range(action_horizon)]}

        try:
            dataset = LeRobotDataset(
                self._repo_id,
                root=dataset_root,
                episodes=list(episodes),
                delta_timestamps=delta_timestamps,
            )
        except Exception as exc:
            raise TrainerError(f"could not open episodes {episodes}: {exc}") from exc

        if dataset.num_frames == 0:
            raise TrainerError(f"episodes {episodes} contain no frames")

        loader = torch.utils.data.DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=shuffle,
            num_workers=num_workers,
            pin_memory=self._resolve_device() != "cpu",
            drop_last=dataset.num_frames >= batch_size,
        )
        return dataset, loader

    # -------------------------------------------------------------- training

    def fine_tune(
        self,
        skill: str,
        episodes: list[int],
        *,
        base_policy: str,
        output_dir: str | Path,
        steps: int = 2000,
        batch_size: int = DEFAULT_BATCH_SIZE,
        lora_rank: int = DEFAULT_LORA_RANK,
        target_modules: str | list[str] | None = None,
        grad_clip_norm: float = 1.0,
        log_every: int = 100,
    ) -> TrainingRun:
        """Train a LoRA adapter on the selected episodes and write it out.

        Args:
            skill: Skill reference, recorded on the run.
            episodes: Curated episode indices, best first.
            base_policy: Hub id of the policy to adapt, from `skill.yaml`'s `policy.base`.
            output_dir: Where the adapter is written.
            steps: Optimiser steps. The nightly budget, not a convergence criterion.
            batch_size: Frames per step.
            lora_rank: Adapter rank.
            target_modules: Which submodules the adapter attaches to, as a regex or a list
                of names. None uses the policy's own default, which only the VLA families
                declare: SmolVLA, pi-0, pi-0.5 and MolmoAct. ACT and Diffusion Policy do
                not, and `wrap_with_peft` refuses rather than guessing, because attaching
                LoRA to the wrong layers trains something that converges and does not
                transfer. `skill.yaml` names `lerobot/smolvla_base`, which is why the
                default path needs nothing here.
            grad_clip_norm: Gradient clipping, matching LeRobot's own training loop.
            log_every: Print interval.

        Note: this path needs a checkpoint and, realistically, a GPU. It has not been run
        against either here — `benchmarks/` exercises `build_dataloader`, which is the half
        that decides what a policy learns from. Treat a failure inside the training loop as
        a version mismatch to look up rather than a bug in the caller.
        """
        try:
            import torch
            from lerobot.configs.policies import PreTrainedConfig
            from lerobot.policies.factory import get_policy_class, make_pre_post_processors
        except ImportError as exc:  # pragma: no cover - depends on the extras
            raise TrainerError(
                "LeRobot and torch are required. Install the extras: "
                'pip install "tendon-os[robot,train]"'
            ) from exc

        device = self._resolve_device()

        # The config is read before the loader is built, because the loader needs the
        # policy's action horizon and the policy is the only thing that knows it.
        try:
            config = PreTrainedConfig.from_pretrained(base_policy)
        except Exception as exc:
            raise TrainerError(f"could not load base policy {base_policy!r}: {exc}") from exc

        horizon = getattr(config, "chunk_size", None) or getattr(config, "horizon", None)
        dataset, loader = self.build_dataloader(
            episodes, batch_size=batch_size, action_horizon=horizon
        )

        try:
            policy = get_policy_class(config.type).from_pretrained(base_policy)
        except Exception as exc:
            raise TrainerError(f"could not load base policy {base_policy!r}: {exc}") from exc

        # `from_pretrained` does not record where the weights came from, and
        # `wrap_with_peft` validates against exactly that: without it the run is refused
        # with "Training from scratch using PEFT is unlikely to yield good results". The
        # check is right — an adapter over random weights learns nothing transferable —
        # and the value it wants is the id we just loaded from.
        if not getattr(policy.config, "pretrained_path", None):
            policy.config.pretrained_path = base_policy

        # The whole reason full fine-tuning is off the table. `wrap_with_peft` freezes the
        # base and returns a model where only adapter parameters require gradients.
        overrides: dict[str, Any] = {"r": lora_rank}
        if target_modules is not None:
            overrides["target_modules"] = target_modules
        try:
            policy = policy.wrap_with_peft(peft_cli_overrides=overrides)
        except ValueError as exc:
            # Verified against `lerobot/act_aloha_sim_transfer_cube_human`, which raises
            # exactly this: only the VLA families ship default targets. Re-raised with the
            # way out, because the upstream message names a CLI flag that does not exist
            # here.
            raise TrainerError(
                f"{base_policy} does not declare where LoRA should attach: {exc} "
                f"Only SmolVLA, pi-0, pi-0.5 and MolmoAct define defaults. Either train "
                f"against one of those, which `tendon train --base` can select, or name "
                f"the modules with target_modules=, which only the Python API takes."
            ) from exc
        # A raw batch out of the store is not what a policy consumes. SmolVLA reads
        # `observation.language.tokens`, which nothing in the dataset writes: the task
        # string is tokenised by the policy's own preprocessor, along with normalisation
        # from the checkpoint's statistics. Calling `forward` on the store's batch fails
        # with a bare KeyError for a key no recording was ever supposed to contain.
        #
        # Built from `pretrained_path` so the normalisation is the checkpoint's own. New
        # statistics computed from two episodes of one site's data would quietly move the
        # inputs away from what the base model was trained on.
        # The rename goes through the pipeline's own step rather than rewriting batches by
        # hand. LeRobot has `rename_observations_processor` for this and uses it in
        # `rollout/context.py`; matching which camera is which is the part it cannot do.
        #
        # `device_processor` is overridden because the checkpoint saved one. smolvla_base
        # ships `{"device": "cuda"}`, so building this pipeline on a machine without CUDA
        # fails before a single batch is read -- which is every CPU fine-tune, not an
        # unusual case.
        rename = _camera_rename(policy, getattr(dataset, "features", {}) or {})
        processor_overrides: dict[str, dict[str, Any]] = {
            "device_processor": {"device": str(device)}
        }
        if rename:
            processor_overrides["rename_observations_processor"] = {"rename_map": rename}
            print(f"  cameras renamed for {base_policy}: {rename}")

        try:
            preprocessor, _ = make_pre_post_processors(
                config,
                pretrained_path=base_policy,
                preprocessor_overrides=processor_overrides,
            )
        except Exception as exc:
            raise TrainerError(
                f"could not build the input pipeline for {base_policy!r}: {exc}"
            ) from exc

        policy.to(device)
        policy.train()

        trainable = sum(p.numel() for p in policy.parameters() if p.requires_grad)
        total = sum(p.numel() for p in policy.parameters())
        if trainable == total:
            # Loud, because it converges either way. An adapter that turned out to be the
            # whole model is not shippable inside a skill package and not versionable per
            # site, and nothing downstream would notice.
            raise TrainerError(
                "every parameter is trainable, so PEFT did not attach; "
                "this would train the base model rather than an adapter"
            )

        optimizer = torch.optim.AdamW([p for p in policy.parameters() if p.requires_grad], lr=1e-4)

        final_loss = float("nan")
        step = 0
        while step < steps:
            for batch in loader:
                if step >= steps:
                    break
                batch = {k: (v.to(device) if hasattr(v, "to") else v) for k, v in batch.items()}
                loss, _ = policy.forward(preprocessor(batch))
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    [p for p in policy.parameters() if p.requires_grad], grad_clip_norm
                )
                optimizer.step()
                optimizer.zero_grad()

                final_loss = float(loss.item())
                step += 1
                if log_every and step % log_every == 0:
                    print(f"  step {step}/{steps}  loss {final_loss:.4f}")

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        policy.save_pretrained(str(out))

        return TrainingRun(
            adapter_path=out,
            skill=skill,
            base_policy=base_policy,
            episodes=tuple(episodes),
            frames=int(dataset.num_frames),
            steps=step,
            final_loss=final_loss,
            trainable_parameters=trainable,
            total_parameters=total,
        )

    # -------------------------------------------------------------- internals

    def _resolve_device(self) -> str:
        if self._device is not None:
            return self._device
        try:
            import torch
        except ImportError:  # pragma: no cover - depends on the train extra
            return "cpu"
        return "cuda" if torch.cuda.is_available() else "cpu"
