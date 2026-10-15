#!/usr/bin/env python
"""Write the parts of README.md and README.ko.md that the code already knows.

Three sections were kept by hand in two languages: the command list, the *Built on* table
and the folder listing. Each had drifted. The English listing named a `Training` view that
has no file, left out the `so101` driver and `assets/`, and its command list had no
`tendon version` while the Korean one did. Two copies of a fact, and nothing that made
either notice the other.

So the facts come from where they live: command names and their first help line from the
Typer app, the drivers and views from the files on disk, and the libraries from
`pyproject.toml`. What the code cannot supply, a sentence in Korean or the reason a layer
is not built here, stays human-written below. When the two disagree this stops rather than
fills the gap: a command with no Korean description, a library the table names and nothing
installs, a directory no one described. An empty cell would be a README that looks
finished and is not.

Only the text between `<!-- BEGIN generated:NAME -->` and `<!-- END generated:NAME -->` is
rewritten. Everything outside the markers is prose and stays as written.

    python scripts/sync_readme.py           # rewrite the sections
    python scripts/sync_readme.py --check   # print the diff and exit 1 if out of date

Needs only the base install: importing the CLI pulls in typer and rich, not the
simulator or the training stack, because every command imports those lazily.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

#: README per language. The language key selects every piece of human text below.
READMES = {"en": "README.md", "ko": "README.ko.md"}


class SyncError(Exception):
    """The code and the human-written data disagree, and guessing would hide it."""


# --- commands ---------------------------------------------------------------------------

#: One line per command in the Korean README. English uses the help text as written, so
#: there is nothing to keep in step there; Korean cannot be derived, so it is listed, and a
#: command missing from here stops the run rather than appearing with an empty column.
KO_COMMANDS = {
    "version": "버전 출력",
    "doctor": "이 환경에서 되는 기능과 빠진 부분, 빠졌을 때 잃는 기능",
    "run": "커널 위에서 정책을 몸에 실행하고 기록",
    "serve": "런타임 API, 셸이 빌드돼 있으면 셸 화면까지",
    "shell": "serve와 같고, 이 환경에서 화면을 여는 방법을 안내",
    "progress": "개입률이 줄고 있는지 터미널에 그래프로 표시",
    "episodes": "기록된 에피소드 목록",
    "calibrate": "보통의 예측 불일치를 측정해 확신도 기준 생성",
    "curate": "기록된 에피소드를 학습 가치 순으로 정렬",
    "train": "큐레이션한 에피소드로 베이스 정책을 LoRA 파인튜닝",
    "eval": "반복 실행하고 기록한 뒤 결과 보고",
}

#: Where the description column starts. `tendon calibrate <skill>` is the longest
#: invocation today and fits with one space to spare; a longer one still gets that space.
_INVOCATION_WIDTH = 24


@dataclass(frozen=True)
class Command:
    name: str
    #: Required arguments as typed, e.g. `<skill>`. Empty when there are none.
    arguments: str
    #: The first line of the command's help.
    summary: str


def commands_of(app: Any) -> list[Command]:
    """Every visible command, in the order the app registers them.

    Read through Typer's own conversion to a command group rather than from the decorated
    functions, so the names and arguments are the ones a user actually types, `eval`
    included.

    By attribute rather than by class. Typer 0.27 vendors click as `typer._click` and no
    longer depends on it, so its group is not a `click.Group` even where click is installed
    (uvicorn brings it). An isinstance check against `click` passed on 0.24 here and failed
    on the first runner that resolved 0.27.
    """
    import typer.main

    group = typer.main.get_command(app)
    if not hasattr(group, "commands"):
        raise SyncError("the tendon app has a single command, not a group of them")

    found = []
    for name, command in group.commands.items():
        if command.hidden:
            continue
        lines = [line.strip() for line in (command.help or "").splitlines() if line.strip()]
        if not lines:
            raise SyncError(f"`tendon {name}` has no help text to list it with")
        arguments = " ".join(
            f"<{param.name}>"
            for param in command.params
            if param.param_type_name == "argument" and param.required
        )
        found.append(Command(name, arguments, lines[0]))
    return found


def commands_block(commands: list[Command], language: str) -> str:
    if language == "ko":
        names = {command.name for command in commands}
        missing = [command.name for command in commands if command.name not in KO_COMMANDS]
        if missing:
            raise SyncError(
                f"no Korean description for {', '.join(f'`tendon {n}`' for n in missing)}. "
                "scripts/sync_readme.py 의 KO_COMMANDS 에 한국어 설명을 추가하세요."
            )
        stale = sorted(set(KO_COMMANDS) - names)
        if stale:
            raise SyncError(
                f"KO_COMMANDS describes commands that do not exist: {', '.join(stale)}. "
                "Remove them, or rename them to the command they became."
            )

    lines = []
    for command in commands:
        invocation = f"tendon {command.name}"
        if command.arguments:
            invocation += f" {command.arguments}"
        summary = KO_COMMANDS[command.name] if language == "ko" else command.summary
        lines.append(f"{invocation:<{_INVOCATION_WIDTH}} {summary}")
    return "```\n" + "\n".join(lines) + "\n```"


# --- built on ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layer:
    """One row of the table: (layer, what we use, what we do not build) per language."""

    #: Distribution names that row depends on, as `pyproject.toml` spells them.
    packages: tuple[str, ...]
    en: tuple[str, str, str]
    ko: tuple[str, str, str]
    #: The milestone a row is for when nothing installs it yet. Its layer cell has to say
    #: so in both languages, the way a future command in a code block has to.
    planned: str | None = None


BUILT_ON = [
    Layer(
        packages=("lerobot",),
        en=(
            "Robot control & datasets",
            "[LeRobot](https://github.com/huggingface/lerobot)",
            "our own dataset format",
        ),
        ko=(
            "로봇 제어, 데이터셋",
            "[LeRobot](https://github.com/huggingface/lerobot)",
            "자체 데이터셋 포맷",
        ),
    ),
    Layer(
        packages=("mujoco",),
        en=(
            "Simulation",
            "[MuJoCo](https://github.com/google-deepmind/mujoco)",
            "a physics engine",
        ),
        ko=("시뮬레이션", "[MuJoCo](https://github.com/google-deepmind/mujoco)", "물리 엔진"),
    ),
    Layer(
        packages=("lerobot",),
        en=("Policies", "LeRobot policies: ACT, Diffusion, pi0, SmolVLA", "a foundation model"),
        ko=("정책", "LeRobot 정책: ACT, Diffusion, pi0, SmolVLA", "파운데이션 모델"),
    ),
    Layer(
        packages=("torch",),
        en=(
            "Training & inference",
            "[PyTorch](https://github.com/pytorch/pytorch)",
            "a tensor library",
        ),
        ko=("학습, 추론", "[PyTorch](https://github.com/pytorch/pytorch)", "텐서 연산 라이브러리"),
    ),
    Layer(
        packages=("peft",),
        en=(
            "Fine-tuning",
            "[PEFT](https://github.com/huggingface/peft) / LoRA",
            "a training framework",
        ),
        ko=("파인튜닝", "[PEFT](https://github.com/huggingface/peft) / LoRA", "학습 프레임워크"),
    ),
    Layer(
        packages=("rerun-sdk",),
        en=("Visualization", "[Rerun](https://github.com/rerun-io/rerun)", "a 3D renderer"),
        ko=("시각화", "[Rerun](https://github.com/rerun-io/rerun)", "3D 렌더러"),
    ),
    # `tendon doctor` reports whether huggingface_hub is present, and nothing declares it:
    # the registry is v0.4. The row stays because the choice is already made, and the
    # check below turns on it the day the dependency is added and the marker is not removed.
    Layer(
        packages=("huggingface-hub",),
        planned="v0.4",
        en=(
            "Registry (v0.4)",
            "[Hugging Face Hub](https://huggingface.co/docs/hub)",
            "our own registry",
        ),
        ko=(
            "레지스트리 (v0.4 목표)",
            "[Hugging Face Hub](https://huggingface.co/docs/hub)",
            "자체 레지스트리",
        ),
    ),
    Layer(
        packages=("fastapi", "pydantic"),
        en=("Runtime / API", "FastAPI, Pydantic", "an RPC framework"),
        ko=("런타임, API", "FastAPI, Pydantic", "RPC 프레임워크"),
    ),
]

_TABLE_HEADER = {
    "en": ("Layer", "We use", "Not building"),
    "ko": ("층", "사용", "tendon이 만들지 않는 부분"),
}

_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_packages(pyproject: str) -> set[str]:
    """Distribution names in `dependencies` and every optional extra.

    Read with regular expressions rather than `tomllib`, which is 3.11+ while this project
    supports 3.10 (`tests/unit/test_install_hints.py` made the same call). Lists are cut at
    a newline followed by `]` or a section header rather than at the first `]`, because
    `uvicorn[standard]` and `lerobot[dataset]` carry brackets of their own.
    """
    text = "\n".join(line.split("#", 1)[0] for line in pyproject.splitlines())

    regions = []
    dependencies = re.search(r"^dependencies\s*=\s*\[(.*?)\n\]", text, re.MULTILINE | re.DOTALL)
    if dependencies is None:
        raise SyncError("pyproject.toml has no `dependencies = [...]` list to read")
    regions.append(dependencies.group(1))

    optional = re.search(
        r"^\[project\.optional-dependencies\]\n(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL
    )
    if optional is None:
        raise SyncError("pyproject.toml has no [project.optional-dependencies] to read")
    regions.append(optional.group(1))

    names = set()
    for region in regions:
        for requirement in re.findall(r'"([^"]+)"', region):
            match = _REQUIREMENT_NAME.match(requirement)
            if match:
                names.add(_normalise(match.group(0)))
    return names


def check_built_on(layers: list[Layer], declared: set[str]) -> None:
    for layer in layers:
        named = layer.en[1]
        if not layer.packages:
            raise SyncError(f"Built on row {named!r} names no package, so nothing checks it")
        packages = {_normalise(package) for package in layer.packages}
        if layer.planned is None:
            missing = sorted(packages - declared)
            if missing:
                raise SyncError(
                    f"Built on row {named!r} needs {', '.join(missing)}, which pyproject.toml "
                    "does not declare. Add the dependency or remove the row."
                )
            continue
        for language in ("en", "ko"):
            cell = getattr(layer, language)[0]
            if layer.planned not in cell:
                raise SyncError(
                    f"Built on row {named!r} is planned for {layer.planned} and its "
                    f"{language} layer cell {cell!r} does not say so"
                )
        shipped = sorted(packages & declared)
        if shipped:
            raise SyncError(
                f"Built on row {named!r} is marked {layer.planned} and {', '.join(shipped)} "
                "is declared now. Drop `planned` and the marker in the layer cell."
            )


def built_on_block(language: str) -> str:
    rows = [_TABLE_HEADER[language], ("---", "---", "---")]
    rows += [getattr(layer, language) for layer in BUILT_ON]
    return "\n".join(f"| {' | '.join(row)} |" for row in rows)


# --- repository layout ------------------------------------------------------------------

#: Column the description starts at, after a two-space indent and the directory name.
_LAYOUT_WIDTH = 17

#: `src/tendon/<dir>/` descriptions, in the order they are listed. Every directory there
#: has to appear and nothing else may, so a new package fails here until it is described.
#: `{drivers}` is filled with the driver modules on disk.
SRC_TENDON = {
    "en": {
        "kernel": (
            "scheduler, step bus, interrupt, safety, and the contracts",
            "other layers implement (types.py, protocols.py)",
        ),
        "drivers": ("the embodiment HAL — {drivers}",),
        "services": (
            "recorder, curator, confidence, demonstration, adaptive, evaluator,",
            "policies, skill, bodies, trainer, registry",
        ),
        "api": ("REST, WebSocket, and the session that bridges the two",),
        "cli": ("the tendon command, listed under Commands",),
        "assets": ("robot models and scene files",),
    },
    "ko": {
        "kernel": (
            "스케줄러, 스텝 버스, 인터럽트, 안전 검사, 다른 계층이 구현할 계약",
            "(types.py, protocols.py)",
        ),
        "drivers": ("몸 추상화(HAL): {drivers}",),
        "services": (
            "recorder, curator, confidence, demonstration, calibration, adaptive,",
            "evaluator, policies, skill, bodies, trainer, progress, registry 등",
        ),
        "api": ("REST, WebSocket, 둘을 잇는 세션",),
        "cli": ("tendon 명령",),
        "assets": ("로봇 모델과 장면 파일",),
    },
}

#: What a driver module is called in the listing when its file name is not enough.
#: Any other driver is listed under its module name, so a new one appears without an edit.
DRIVER_LABELS = {
    "en": {"human": "human video"},
    "ko": {"human": "사람 시연 영상"},
}

#: Driver modules that are not bodies.
_NOT_DRIVERS = {"__init__", "base"}

#: `shell/src/<dir>/` descriptions, held to the same rule as SRC_TENDON. `{views}` is
#: filled with the view components on disk.
SHELL_SRC = {
    "en": {
        "views": ("{views}",),
        "panels": ("IntentPreview.tsx — the centre; fixed grid so nothing shifts",),
        "rerun": ("embedded Rerun viewer, clock-aligned to the episode",),
        "api": ("typed client mirroring kernel/types.py",),
        "state": ("connection, episode, pending decision",),
        "design": ("tokens.css + app.css — light and dark, sized for a floor tablet",),
    },
    "ko": {
        "views": ("{views}",),
        "panels": ("의도 미리보기, 궤적 미리보기, 교정 편집기",),
        "rerun": ("Rerun 뷰어 임베드, 에피소드 타임라인과 시계 동기화",),
        "api": ("kernel/types.py와 같은 타입을 쓰는 클라이언트",),
        "state": ("연결, 에피소드, 대기 중인 결정",),
        "design": ("tokens.css, app.css (라이트와 다크, 현장 태블릿 기준)",),
    },
}

#: The rest of the listing as (path checked to exist, label, description).
TOP_LEVEL = {
    "en": (
        ("docs", "docs/", "concepts, architecture, stack, roadmap, glossary, collaboration"),
        ("docs/decisions", "  decisions/", "ADRs — one per irreversible choice"),
        ("skills", "skills/", "installable capabilities, distributed via the HF Hub"),
        ("examples", "examples/", "01_record → 04_improve, ordered by what each one proves"),
        ("tests", "tests/", "unit (CPU-only, runs on a bare checkout) + integration"),
    ),
    "ko": (
        ("docs", "docs/", "개념, 아키텍처, 스택, 로드맵, 용어집, 협업"),
        ("docs/decisions", "  decisions/", "ADR (되돌리기 어려운 결정 하나당 파일 하나)"),
        ("skills", "skills/", "설치할 수 있는 스킬 (grasp/cube-sim)"),
        ("examples", "examples/", "01_record부터 04_improve까지, 순서대로 하나씩 증명"),
        ("tests", "tests/", "unit(CPU만, 설치 직후 실행 가능), integration"),
    ),
}


def _subdirectories(directory: Path) -> set[str]:
    return {
        path.name
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith(("__", "."))
    }


def _check_described(described: dict[str, Any], directory: Path, where: str) -> None:
    present = _subdirectories(directory)
    missing = sorted(present - set(described))
    if missing:
        raise SyncError(
            f"{where}/{', '.join(missing)} has no description in scripts/sync_readme.py. "
            "Say what belongs in it."
        )
    gone = sorted(set(described) - present)
    if gone:
        raise SyncError(
            f"scripts/sync_readme.py describes {where}/{', '.join(gone)}, which does not exist"
        )


def _labelled_drivers(root: Path, language: str) -> str:
    modules = sorted(
        path.stem
        for path in (root / "src/tendon/drivers").glob("*.py")
        if path.stem not in _NOT_DRIVERS
    )
    labels = DRIVER_LABELS[language]
    stale = sorted(set(labels) - set(modules))
    if stale:
        raise SyncError(f"DRIVER_LABELS names {', '.join(stale)}, which is not a driver module")
    return ", ".join(labels.get(module, module) for module in modules)


def _views(root: Path) -> str:
    # A stem with a dot in it is `Live.test` or similar, not a screen.
    names = sorted(
        path.stem for path in (root / "shell/src/views").glob("*.tsx") if "." not in path.stem
    )
    if not names:
        raise SyncError("shell/src/views has no .tsx views; the listing would be empty")
    return ", ".join(names)


def _entry(label: str, lines: tuple[str, ...]) -> list[str]:
    first, *rest = lines
    return [f"{label:<{_LAYOUT_WIDTH}}{first}"] + [" " * _LAYOUT_WIDTH + line for line in rest]


def layout_block(root: Path, language: str) -> str:
    src = SRC_TENDON[language]
    shell = SHELL_SRC[language]
    _check_described(src, root / "src/tendon", "src/tendon")
    _check_described(shell, root / "shell/src", "shell/src")

    fill = {"drivers": _labelled_drivers(root, language), "views": _views(root)}

    lines = ["src/tendon/"]
    for name, description in src.items():
        lines += _entry(f"  {name}/", tuple(line.format(**fill) for line in description))
    lines += ["", "shell/src/"]
    for name, description in shell.items():
        lines += _entry(f"  {name}/", tuple(line.format(**fill) for line in description))
    lines.append("")
    for path, label, description in TOP_LEVEL[language]:
        if not (root / path).is_dir():
            raise SyncError(f"the listing names {path}/, which does not exist")
        lines += _entry(label, (description,))
    return "```\n" + "\n".join(lines) + "\n```"


# --- writing ----------------------------------------------------------------------------


def replace_section(text: str, name: str, body: str) -> str:
    """Put `body` between the markers for `name`, and touch nothing else."""
    begin = f"<!-- BEGIN generated:{name} -->"
    end = f"<!-- END generated:{name} -->"
    if text.count(begin) != 1 or text.count(end) != 1:
        raise SyncError(f"expected exactly one {begin} and one {end}")
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    if stop < start:
        raise SyncError(f"{end} comes before {begin}")
    # Blank lines either side, so a table or a fence never touches the comment and is
    # parsed as its own block.
    return text[:start] + "\n\n" + body + "\n\n" + text[stop:]


def sections(root: Path, language: str) -> dict[str, str]:
    sys.path.insert(0, str(root / "src"))
    try:
        from tendon.cli.main import app
    finally:
        sys.path.pop(0)

    check_built_on(BUILT_ON, declared_packages((root / "pyproject.toml").read_text("utf-8")))
    return {
        "commands": commands_block(commands_of(app), language),
        "built-on": built_on_block(language),
        "layout": layout_block(root, language),
    }


def generated(root: Path) -> dict[Path, tuple[str, str]]:
    """(current, regenerated) text per README."""
    out = {}
    for language, filename in READMES.items():
        path = root / filename
        current = path.read_text(encoding="utf-8")
        text = current
        for name, body in sections(root, language).items():
            text = replace_section(text, name, body)
        out[path] = (current, text)
    return out


def pending_changes(root: Path) -> dict[Path, str]:
    """A unified diff per README that is out of date. Empty when both match."""
    pending = {}
    for path, (current, text) in generated(root).items():
        if current != text:
            name = path.relative_to(root).as_posix()
            pending[path] = "".join(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile=f"a/{name}",
                    tofile=f"b/{name}",
                )
            )
    return pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="print the diff and exit 1 instead of writing"
    )
    args = parser.parse_args(argv)

    # The diff carries Korean and box-drawing characters, and a cp949 console cannot
    # print all of them. See `printable` in scripts/check.py for the same problem.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    try:
        pending = pending_changes(REPO)
        if args.check:
            for diff in pending.values():
                sys.stdout.write(diff)
            if pending:
                print("README sections are out of date: run python scripts/sync_readme.py")
                return 1
            print("README sections are up to date")
            return 0

        for path, (_, text) in generated(REPO).items():
            if path in pending:
                path.write_text(text, encoding="utf-8", newline="\n")
                print(f"updated {path.relative_to(REPO).as_posix()}")
        if not pending:
            print("README sections are up to date")
        return 0
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
