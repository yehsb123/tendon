"""LeRobot policies, adapted to the tendon `Policy` protocol.

This is where ADR 0005 lands — see `docs/decisions/`, the RTC one.
The kernel scheduler stays thin and abstract, and everything that knows about torch,
`PreTrainedPolicy` and LeRobot's batch conventions lives here, on the service side of the
boundary `tests/unit/test_boundaries.py` enforces.

## What the adapter actually adds

A `PreTrainedPolicy` returns a bare action tensor. A tendon `Intent` needs three things
that tensor does not carry, and supplying them is the entire job:

**Confidence.** No upstream policy reports how sure it is (ADR 0003). Flow-matching and
diffusion policies (SmolVLA, pi-0, pi-0.5) are stochastic: sampling the same observation
twice gives different chunks, and `predict_action_chunk` takes a `noise` argument precisely
so a caller can drive that. The adapter samples n times and hands the spread to
`services.confidence`.

Not every policy is stochastic, and the ones that are not are the dangerous case. ACT
returns the same chunk every time, so its spread is zero and a naive reading scores it 1.0:
a policy that can never raise an interrupt, wearing the number that means it never needs
to. Measured on a real checkpoint, which is how it was found. The adapter compares the
samples it already drew and reports `ConfidenceSource.NONE` when they are identical,
whether or not the caller knew to declare it.

**Typed actions.** A tensor row becomes an `Action` with a declared `ActionSpace` and the
gripper split out into its own normalised scalar, which is what makes a chunk renderable
and checkable against `SafetyLimits`.

**A horizon in seconds.** A chunk of 50 steps means nothing without a rate. At the body's
control rate it is 0.5 s, and that is what the shell needs to say when the plan ends.

## What it deliberately does not do

**It does not reach for pixels.** `services` may not import `drivers`, and an observation
carries frame *references* rather than arrays (see `kernel/types.py`). A caller that wants
image-conditioned prediction passes a `frames` callable at construction; the scheduler,
which holds both the driver and the policy, is the natural place to wire it.

**It does not average the samples.** One of the n chunks is executed, not their mean. For a
multimodal policy the mean of two valid plans — go left, go right — is a third plan that
goes straight into the obstacle. The spread across samples is used to score confidence;
the action executed is a sample the policy actually produced.

Requires the robot and train extras:  pip install "tendon-os[robot,train]"
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from tendon.kernel.types import (
    Action,
    ActionSpace,
    Confidence,
    ConfidenceSource,
    Intent,
    Observation,
)
from tendon.services.confidence import estimate_from_samples

# LeRobot batch keys, from `lerobot.utils.constants`. Mirrored rather than imported so this
# module can be read, type-checked and unit-tested without LeRobot installed.
_OBS_STATE = "observation.state"
_OBS_IMAGES = "observation.images"
_TASK = "task"
# The singular form. LeRobot has two camera conventions and checkpoints use both: a
# multi-camera policy declares `observation.images.top`, a single-camera one often declares
# a bare `observation.image`. `lerobot/act_aloha_sim_transfer_cube_human` is the first kind
# and `lerobot/diffusion_pusht` is the second, so an adapter that writes only one of them
# fails on half the Hub with a KeyError raised from inside the policy.
_OBS_IMAGE_SINGULAR = "observation.image"

# Samples per prediction. Three is the floor `services.confidence` accepts — below it a
# spread is noise rather than a measurement — and each one is a full forward pass, so this
# is the direct cost of having confidence at all.
DEFAULT_SAMPLES = 3


class PolicyError(RuntimeError):
    """Raised when a policy cannot be loaded or produces something unusable."""


def _import_torch() -> Any:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - depends on the train extra
        raise PolicyError(
            'torch is not installed. Install the train extra: pip install "tendon-os[train]"'
        ) from exc
    return torch


def _load_base(repo_id: str, device: str | None) -> Any:
    """Load a LeRobot checkpoint and put it in eval mode.

    Two steps rather than one, and the reason matters. `PreTrainedPolicy` is abstract, so
    calling `from_pretrained` on it has no class to instantiate. The concrete class is
    named by the checkpoint's own config, and reading that first is also what makes this
    work for any LeRobot policy — act, diffusion, pi0, smolvla — without keeping a table of
    names here that would go stale.

    One function because `from_pretrained` and `load_adapter` both need it, and this
    project has twice shipped one bug from two copies of the same construction.
    """
    torch = _import_torch()
    try:
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies.factory import get_policy_class
    except ImportError as exc:  # pragma: no cover - depends on the robot extra
        raise PolicyError(
            'LeRobot is not installed. Install the robot extra: pip install "tendon-os[robot]"'
        ) from exc

    try:
        config = PreTrainedConfig.from_pretrained(repo_id)
        policy = get_policy_class(config.type).from_pretrained(repo_id)
    except Exception as exc:
        raise PolicyError(f"could not load policy {repo_id!r}: {exc}") from exc

    resolved = device or ("cuda" if torch.cuda.is_available() else "cpu")
    policy.to(resolved)
    policy.eval()
    return policy


def build_preprocessor(repo_id: str, device: str) -> Any | None:
    """The checkpoint's own input pipeline, or None when it has none.

    A raw batch is not what a policy consumes. SmolVLA reads
    `observation.language.tokens`, which nothing builds by hand: the task string is
    tokenised by the checkpoint's preprocessor, along with normalisation from the
    statistics the checkpoint was trained under. Calling `predict_action_chunk` on a batch
    assembled here fails with a bare `KeyError` for a key no caller was ever supposed to
    write — and for a policy that does not tokenise, it succeeds on unnormalised inputs,
    which is worse: the numbers are on the wrong scale and the actions are wrong with no
    error anywhere.

    `pretrained_path` is passed so the normalisation is the checkpoint's own. Statistics
    recomputed from one site's data would quietly move the inputs away from what the model
    was trained on.

    `device_processor` is overridden because the checkpoint saved one: `smolvla_base` ships
    `{"device": "cuda"}`, so building this on a machine without CUDA fails before a single
    batch is read — which is every CPU run, not an unusual case. The same override the
    trainer needed, for the same reason, found the same way.
    """
    try:
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies import factory
        from lerobot.policies.factory import make_pre_post_processors
    except ImportError:  # pragma: no cover - depends on the robot extra
        return None

    assert factory is not None  # registration side effect; see `declared_image_features`

    try:
        config = PreTrainedConfig.from_pretrained(repo_id)
        preprocessor, _ = make_pre_post_processors(
            config,
            pretrained_path=repo_id,
            preprocessor_overrides={"device_processor": {"device": str(device)}},
        )
    except Exception as exc:
        raise PolicyError(f"could not build the input pipeline for {repo_id!r}: {exc}") from exc

    return preprocessor


def adapter_base(directory: Path | str) -> str:
    """The checkpoint an adapter was trained against, read from the adapter itself.

    A LoRA is a delta against particular weights. Applied to a different base it loads,
    runs, and is wrong — no exception, no warning, just a policy doing something nobody
    trained it to do. So the base is taken from `adapter_config.json`, which PEFT writes
    from the model it was attached to, rather than from a `skill.yaml` a person edits.

    Separate from `load_adapter` so a caller can compare it against what a skill declares
    before spending minutes on a checkpoint.
    """
    import json

    path = Path(directory) / "adapter_config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyError(f"no adapter config at {path}: {exc}") from exc
    except ValueError as exc:
        raise PolicyError(f"{path} is not readable as JSON: {exc}") from exc

    base = config.get("base_model_name_or_path")
    if not isinstance(base, str) or not base:
        raise PolicyError(
            f"{path} does not name the checkpoint it was trained against, so there is "
            f"nothing to apply it to. PEFT writes `base_model_name_or_path`; an adapter "
            f"without it was not written by `tendon train`."
        )
    return base


def declared_image_features(repo_id: str) -> tuple[str, ...]:
    """Image inputs a checkpoint declares, read from its config alone.

    No weights. `PreTrainedConfig.from_pretrained` fetches a JSON file, which is what makes
    this answerable before deciding to spend minutes loading 450 million parameters — and
    the answer decides whether the run can work at all. A vision-language-action policy on
    a body rendering no cameras fails inside the model with "All image features are missing
    from the batch", after the checkpoint has loaded, naming neither the body nor the flag
    that would have supplied them.

    Empty for a state-only checkpoint, which is a policy that legitimately needs no video.
    """
    try:
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies import factory
    except ImportError as exc:  # pragma: no cover - depends on the robot extra
        raise PolicyError(
            'LeRobot is not installed. Install the robot extra: pip install "tendon-os[robot]"'
        ) from exc

    # Imported for its registration side effect rather than to be called. `PreTrainedConfig`
    # resolves the checkpoint's `type` through a registry the factory module fills on
    # import, and without it a perfectly good checkpoint reports "Policy type 'smolvla' is
    # not registered. Available policy types: {}" — which reads as a broken checkpoint
    # rather than a missing import.
    assert factory is not None

    try:
        config = PreTrainedConfig.from_pretrained(repo_id)
    except Exception as exc:
        raise PolicyError(f"could not read the config of {repo_id!r}: {exc}") from exc

    features = getattr(config, "input_features", None) or {}
    return tuple(name for name in features if name.startswith(_OBS_IMAGE_SINGULAR))


def load_adapter(
    directory: Path | str,
    *,
    task: str,
    dof: int,
    control_hz: float,
    reference_spread: float,
    policy_hz: float | None = None,
    device: str | None = None,
    **kwargs: Any,
) -> LeRobotPolicy:
    """Load a LoRA adapter onto the base it was trained against, and wrap it.

    This is the return path for `tendon train`. LeRobot has `wrap_with_peft` and no
    counterpart, so loading is PEFT's own: build the base, then
    `PeftModel.from_pretrained(policy, directory)`.

    **The attachment is verified, not assumed.** PEFT applies an adapter by name-matching
    `target_modules` against the model, and a pattern that matches nothing is not an error
    there — it produces a model identical to the base, which runs perfectly and is not the
    thing that was trained. The parameter count has to grow, and by how much says whether
    the adapter arrived whole. This mirrors the trainer's own guard at the other end, where
    an adapter that failed to attach shows up as *every* parameter being trainable.

    Measured on `lerobot/smolvla_base` with an adapter from `tendon train`: 450,046,176
    parameters before, 450,788,832 after — a difference of 742,656, which is exactly what
    the training run reported as trainable.
    """
    resolved = Path(directory)
    base = adapter_base(resolved)

    try:
        from peft import PeftModel
    except ImportError as exc:  # pragma: no cover - depends on the train extra
        raise PolicyError(
            'PEFT is not installed. Install the train extra: pip install "tendon-os[train]"'
        ) from exc

    policy = _load_base(base, device)
    before = sum(parameter.numel() for parameter in policy.parameters())

    try:
        adapted = PeftModel.from_pretrained(policy, str(resolved))
    except Exception as exc:
        raise PolicyError(f"could not apply the adapter at {resolved} to {base}: {exc}") from exc

    after = sum(parameter.numel() for parameter in adapted.parameters())
    if after <= before:
        raise PolicyError(
            f"the adapter at {resolved} attached nothing to {base}: {before} parameters "
            f"before and {after} after. Its target_modules match no module in this "
            f"checkpoint, which usually means it was trained against a different one. "
            f"Running it would run the base model while reporting the adapter."
        )

    adapted.eval()

    # The device the base actually landed on, not the one that was asked for: `_load_base`
    # resolves `None` to cuda-or-cpu, and the preprocessor has to agree with the model or
    # every batch arrives on the wrong one.
    landed = str(next(adapted.parameters()).device)

    return LeRobotPolicy(
        adapted,
        name=f"{base}+{resolved.name}",
        task=task,
        dof=dof,
        control_hz=control_hz,
        policy_hz=policy_hz,
        reference_spread=reference_spread,
        preprocessor=build_preprocessor(base, landed),
        **kwargs,
    )


class LeRobotPolicy:
    """A LeRobot `PreTrainedPolicy` presented as a tendon `Policy`.

    Structurally conforms to `kernel.protocols.Policy`; it does not inherit, because that
    protocol is `runtime_checkable` and inheriting would tie a service to a kernel class
    for no benefit.
    """

    def __init__(
        self,
        policy: Any,
        *,
        name: str,
        task: str,
        dof: int,
        control_hz: float,
        policy_hz: float | None = None,
        reference_spread: float,
        samples: int = DEFAULT_SAMPLES,
        has_gripper: bool = True,
        frames: Callable[[], dict[str, Any]] | None = None,
        deterministic: bool = False,
        preprocessor: Any | None = None,
    ) -> None:
        """
        Args:
            policy: A loaded LeRobot `PreTrainedPolicy`, or anything with the same
                `predict_action_chunk(batch, noise=None)` and `reset()`.
            name: Skill reference this policy came from, recorded on every episode.
            task: Natural-language instruction passed to the policy and shown to an
                operator as the intent's goal.
            dof: Arm degrees of freedom on the body this will drive. The policy pads its
                action dimension to `max_action_dim` — 32 for SmolVLA — so the adapter has
                to know where the real values stop.
            policy_hz: The rate the checkpoint's actions were meant to be executed at [Hz].
                None means unknown, which is the honest default: none of
                `smolvla_base`, `act_aloha_sim_transfer_cube_human` or `diffusion_pusht`
                declares one. They give `chunk_size` and `n_action_steps` and say nothing
                about how fast those actions should be played, so a caller that knows has
                to say. When it differs from `control_hz` each action is held for the
                number of ticks that keeps the trajectory at the speed it was trained at.
            control_hz: The body's control rate [Hz], used to turn a chunk length into a
                horizon in seconds.
            reference_spread: Disagreement considered typical for this skill on this body,
                in action units. Spread has no absolute meaning; see
                `services/confidence.py`. Until v0.3 calibrates it against intervention
                outcomes this is a configured guess, and it is the caller's guess.
            samples: Chunks drawn per prediction. Each is a forward pass.
            has_gripper: Whether the body's last action channel is a gripper. When true the
                final column is split into `Action.gripper` rather than being one more
                joint.
            frames: Optional callable returning `{camera: array}` for the current step —
                `MujocoDriver.render` has this shape. Called once per prediction, not once
                per sample, since all samples condition on the same observation.
            deterministic: Set for a policy that returns the same chunk every time. Sample
                spread measures nothing for such a policy, and `services.confidence`
                reports `NONE` rather than dressing zero spread up as certainty.
            preprocessor: The checkpoint's own input pipeline, from `build_preprocessor`.
                None for a test double or a policy that consumes a raw batch. When present
                every batch goes through it before prediction, which is what tokenises the
                task and normalises the state to the scale the checkpoint was trained on.
        """
        self._policy = policy
        self._name = name
        self._task = task
        self._dof = int(dof)
        self._control_hz = float(control_hz)
        self._policy_hz = float(policy_hz) if policy_hz else None

        # Held ticks per chunk action. 1 when the rates agree or the policy's is unknown,
        # which is the same arithmetic as today and the same behaviour.
        self._hold = 1
        if self._policy_hz and self._policy_hz > 0:
            ratio = self._control_hz / self._policy_hz
            if ratio < 1:
                raise PolicyError(
                    f"{name} produces actions for {self._policy_hz:g} Hz and this body runs "
                    f"at {self._control_hz:g} Hz. Executing a chunk on a body slower than "
                    f"the policy would drop actions, and choosing which to drop is not "
                    f"something this adapter should decide."
                )
            self._hold = int(round(ratio))
            if abs(ratio - self._hold) > 1e-6:
                raise PolicyError(
                    f"{name} produces actions for {self._policy_hz:g} Hz and this body runs "
                    f"at {self._control_hz:g} Hz, which is not a whole multiple. Holding "
                    f"each action for {ratio:.3f} ticks would need interpolation, and a "
                    f"trajectory this adapter invented is not one the policy produced."
                )
        self._reference_spread = float(reference_spread)
        self._samples = max(1, int(samples))
        self._has_gripper = has_gripper
        self._frames = frames
        self._deterministic = deterministic
        self._preprocessor = preprocessor
        # What the checkpoint says it wants. Asking is better than guessing a convention:
        # `config.input_features` is how a policy declares its own inputs.
        self._image_keys = self._declared_image_keys(policy)

        # Observation history. A policy with n_obs_steps > 1 conditions on a window of
        # past observations, and the adapter builds a batch from one. Refused at
        # construction rather than at the first prediction, where it surfaces as an einops
        # shape error from three frames inside the policy that says nothing about the
        # cause. `lerobot/diffusion_pusht` wants 2; ACT and SmolVLA want 1.
        history = int(getattr(getattr(policy, "config", None), "n_obs_steps", 1) or 1)
        if history > 1:
            raise PolicyError(
                f"{name} conditions on {history} observation steps, and this adapter "
                f"builds a batch from one. Supporting it needs an observation buffer in "
                f"the adapter, which is not written yet."
            )
        self._history = history

        if self._control_hz <= 0:
            raise PolicyError(f"control_hz must be positive, got {control_hz}")
        if self._dof <= 0:
            raise PolicyError(f"dof must be positive, got {dof}")

    @staticmethod
    def _declared_image_keys(policy: Any) -> tuple[str, ...]:
        """Image inputs the checkpoint declares, in the order it declares them.

        Empty for a policy with no config, which is every hand-rolled test double. The
        batch builder then falls back to the plural convention, which is what tendon's own
        recorder writes.
        """
        features = getattr(getattr(policy, "config", None), "input_features", None) or {}
        return tuple(k for k in features if k.startswith(_OBS_IMAGE_SINGULAR))

    # ------------------------------------------------------------------ loading

    @classmethod
    def from_pretrained(
        cls,
        repo_id: str,
        *,
        task: str,
        dof: int,
        control_hz: float,
        reference_spread: float,
        device: str | None = None,
        policy_type: str | None = None,
        **kwargs: Any,
    ) -> LeRobotPolicy:
        """Load open weights from the Hub and wrap them.

        `repo_id` is what a `skill.yaml` names under `policy.base` — for example
        `lerobot/smolvla_base`. No weights live in this repository; a skill references
        them, which is design decision 4.

        Args:
            policy_type: LeRobot policy name, such as `smolvla` or `act`. Read from the
                checkpoint's config when omitted, which is the normal case; pass it only
                for a checkpoint whose config does not say.

        Verified against `lerobot/act_aloha_sim_transfer_cube_human` on LeRobot 0.6: the
        config resolves, `ACTPolicy` loads, and `predict` returns a 100-step chunk of
        14-dimensional actions. A failure here is more likely a version mismatch than a
        mistake in the caller.
        """
        if policy_type is None:
            policy = _load_base(repo_id, device)
        else:
            # The one case `_load_base` cannot serve: a checkpoint whose config does not
            # name its own type, where the caller has to. Kept here rather than pushed into
            # the shared loader, which would give every caller a parameter for a situation
            # that has not been met yet.
            torch = _import_torch()
            try:
                from lerobot.policies.factory import get_policy_class
            except ImportError as exc:  # pragma: no cover - depends on the robot extra
                raise PolicyError(
                    "LeRobot is not installed. Install the robot extra: "
                    'pip install "tendon-os[robot]"'
                ) from exc
            try:
                policy = get_policy_class(policy_type).from_pretrained(repo_id)
            except Exception as exc:
                raise PolicyError(f"could not load policy {repo_id!r}: {exc}") from exc
            policy.to(device or ("cuda" if torch.cuda.is_available() else "cpu"))
            policy.eval()

        return cls(
            policy,
            name=repo_id,
            task=task,
            dof=dof,
            control_hz=control_hz,
            reference_spread=reference_spread,
            **kwargs,
        )

    # ----------------------------------------------------------------- contract

    @property
    def name(self) -> str:
        return self._name

    @property
    def requires(self) -> tuple[ActionSpace, ...]:
        """Joint position, and only that.

        LeRobot stores action values without recording which space they are in, so this is
        asserted from how the dataset was collected rather than read from the checkpoint.
        Every body tendon currently writes is joint-position; a policy trained on
        end-effector deltas would be silently mislabelled here, which is worth revisiting
        the moment a second space is actually used.
        """
        return (ActionSpace.JOINT_POSITION,)

    def reset(self) -> None:
        """Clear the policy's internal action queue between episodes."""
        if hasattr(self._policy, "reset"):
            self._policy.reset()

    def predict(self, observation: Observation) -> Intent:
        """Sample n chunks, score their disagreement, and return the first as intent."""
        batch = self._build_batch(observation)
        chunks = [self._sample_chunk(batch) for _ in range(self._samples)]

        # Detected, not trusted to the caller. A deterministic policy returns the same
        # chunk every time, so its spread is zero and `estimate_from_samples` would score
        # it 1.0 — a policy that can never raise an interrupt, wearing the number that
        # means it never needs to.
        #
        # This is not hypothetical: `lerobot/act_aloha_sim_transfer_cube_human` is an ACT
        # checkpoint, ACT is deterministic, and against a real observation it produced
        # exactly that, confidence 1.0000 with source=chunk_variance.
        #
        # The samples are already drawn, so comparing them costs nothing and catches the
        # case whether or not the caller knew to declare it.
        identical = all(chunk == chunks[0] for chunk in chunks[1:])
        deterministic = self._deterministic or identical

        if self._samples >= 3 and not deterministic:
            confidence = estimate_from_samples(chunks, reference_spread=self._reference_spread)
        else:
            # Fewer than three samples cannot support a spread, and saying so is the point.
            # A score that is not a measurement must not be able to raise an interrupt.
            if deterministic:
                reason = (
                    "policy returned identical chunks from the same observation, so sample "
                    "spread measures nothing; confidence-based handover is disabled for it"
                    if identical and not self._deterministic
                    else "policy is deterministic, so sample spread measures nothing"
                )
            else:
                reason = (
                    f"{self._samples} sample(s) drawn; at least 3 are needed to measure "
                    f"disagreement"
                )
            confidence = Confidence(score=0.0, source=ConfidenceSource.NONE, reasons=(reason,))

        executed = chunks[0]
        if self._hold > 1:
            # Repeat rather than interpolate. A held position is what the body does between
            # commands anyway; a synthesised intermediate pose is a trajectory the policy
            # never produced, and on a real arm that is the difference between slow and
            # wrong.
            executed = [action for action in executed for _ in range(self._hold)]

        return Intent(
            horizon_s=len(executed) / self._control_hz,
            actions=tuple(executed),
            confidence=confidence,
            goal=self._task,
        )

    # ---------------------------------------------------------------- internals

    def _build_batch(self, observation: Observation) -> dict[str, Any]:
        """Turn a tendon observation into the dict LeRobot policies consume.

        Keys follow `lerobot.utils.constants`: `observation.state`, `observation.images.*`
        and `task`. Everything is given a leading batch dimension of one, because these
        models are trained batched and do not special-case a single sample.
        """
        torch = _import_torch()

        state = torch.tensor(observation.proprio.joint_positions, dtype=torch.float32).unsqueeze(0)
        if observation.proprio.gripper_open is not None and self._has_gripper:
            gripper = torch.tensor(
                [observation.proprio.gripper_open], dtype=torch.float32
            ).unsqueeze(0)
            state = torch.cat([state, gripper], dim=1)

        batch: dict[str, Any] = {_OBS_STATE: state, _TASK: [self._task]}

        if self._frames is not None:
            for index, (camera, pixels) in enumerate(self._frames().items()):
                # HWC uint8 from a driver; CHW float in [0, 1] for a vision encoder.
                image = torch.as_tensor(pixels)
                if image.ndim == 3 and image.shape[-1] in (1, 3, 4):
                    image = image.permute(2, 0, 1)
                if image.dtype == torch.uint8:
                    image = image.float() / 255.0
                batch[self._image_key(camera, index)] = image.unsqueeze(0)

        if self._preprocessor is None:
            return batch

        # Once per prediction, not once per sample: every sample conditions on the same
        # observation, and tokenising the same task string n times would be n times the
        # cost for the same tensor.
        try:
            return self._preprocessor(batch)
        except Exception as exc:
            raise PolicyError(
                f"{self._name} could not prepare an observation for its own preprocessor: {exc}"
            ) from exc

    def _image_key(self, camera: str, index: int) -> str:
        """Where this camera's pixels go in the batch.

        Matched against what the checkpoint declared: by name where the names line up, by
        position where they do not. A driver calls its camera `wrist` and a checkpoint
        trained elsewhere calls the same view `top`; refusing on that mismatch would be
        right only if the names meant something, and they do not.

        Falls back to the plural convention when the policy declares nothing, which is the
        case for a test double.
        """
        if not self._image_keys:
            return f"{_OBS_IMAGES}.{camera}"

        suffix = f".{camera}"
        for key in self._image_keys:
            if key.endswith(suffix):
                return key
        if index < len(self._image_keys):
            return self._image_keys[index]
        return self._image_keys[0]

    def _sample_chunk(self, batch: dict[str, Any]) -> list[Action]:
        """Draw one action chunk and convert it to typed actions.

        `noise` is left to the policy. Passing `None` makes it draw its own each call,
        which is what produces the variation the confidence estimate measures; supplying a
        fixed tensor here would silently make every sample identical and every score 1.0.
        """
        try:
            chunk = self._policy.predict_action_chunk(batch)
        except Exception as exc:
            raise PolicyError(f"policy {self._name!r} failed to produce a chunk: {exc}") from exc

        return self._to_actions(chunk)

    def _to_actions(self, chunk: Any) -> list[Action]:
        """Convert a `(batch, steps, dims)` tensor into `Action` objects.

        Trailing dimensions are dropped. SmolVLA pads its action head to
        `max_action_dim` — 32 by default — so a five-joint arm with a gripper occupies six
        columns and the remaining twenty-six are padding. Reading them as joints would
        command dimensions the body does not have.
        """
        values = chunk.detach().cpu().numpy() if hasattr(chunk, "detach") else chunk
        if values.ndim == 3:
            values = values[0]
        if values.ndim != 2:
            raise PolicyError(f"expected an action chunk shaped (steps, dims), got {values.shape}")

        width = self._dof + (1 if self._has_gripper else 0)
        if values.shape[1] < width:
            raise PolicyError(
                f"policy emits {values.shape[1]} action dimensions, body needs {width}"
            )

        actions: list[Action] = []
        for row in values:
            joints = [float(v) for v in row[: self._dof]]
            gripper = None
            if self._has_gripper:
                gripper = float(min(1.0, max(0.0, row[self._dof])))
            actions.append(Action(space=ActionSpace.JOINT_POSITION, values=joints, gripper=gripper))
        return actions


def horizon_seconds(chunk: Sequence[Action], control_hz: float) -> float:
    """Wall-clock span a chunk covers [s].

    Trivial, and separate so the scheduler and the shell agree on it rather than each
    dividing by a rate they looked up independently.
    """
    if control_hz <= 0:
        raise ValueError(f"control_hz must be positive, got {control_hz}")
    return len(chunk) / control_hz
