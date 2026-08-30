# tendon

[English](README.md) · **한국어**

**피지컬 AI를 위한 운영 계층.**
모든 실행이 데이터가 되고, 모든 사람의 교정이 학습이 됩니다.

> 힘줄(tendon)은 근육과 뼈를 잇습니다. 이 프로젝트는 **정책(policy)**과 **로봇**을 잇고,
> 그 접점에 **사람**을 세웁니다.

---

## 비어 있는 층

`ROS`는 이름에 OS가 붙어 있지만 실제로는 **통신 미들웨어**입니다. 노드 사이에서 메시지를
옮길 뿐, "AI 정책을 운영한다"는 개념이 없습니다.

그 층이 아직 존재하지 않습니다.

| 일반 OS가 주는 것 | 지금 로봇 판에 있는 것 | 비어 있는 것 |
| --- | --- | --- |
| 디바이스 드라이버 | ROS 2 | 로봇마다 다른 액션 공간의 **통일 추상화** |
| 프로세스 관리 | — | 여러 정책·스킬의 **동시 스케줄링** |
| 파일 시스템 | — | 경험을 저장·버전관리하는 **1급 저장소** |
| 시스템 로그 | — | **왜** 그렇게 움직였는지 |
| 셸 | — | 사람이 **보고 개입**하는 자리 |
| 인터럽트 | 비상정지 (전원 차단) | **문맥을 보존한 채** 제어권 이양 |
| 패키지 매니저 | — | 스킬을 **설치·공유** |

`tendon`이 그 층입니다.

## 네 개의 설계 결정

이 넷이 프로젝트의 전부입니다. 각각이 업계가 당연하게 여기는 가정을 하나씩 부정합니다.

### 1. 실행이 곧 수집이다
별도의 "데이터 수집 모드"가 없습니다. 모든 실행이 에피소드로 기록되고, 자동으로 선별되어,
학습으로 되돌아갑니다. `journalctl`이 로그를 남긴다면 tendon은 **경험**을 남깁니다.

### 2. 정책이 스스로 손을 든다
비상정지는 전원을 끊습니다. 문맥이 사라지고 아무것도 학습되지 않습니다. LeRobot의
DAgger 전략은 그보다 낫습니다. 사람이 정책 실행 중간에 개입하고 교정이 기록됩니다.
다만 **거기서 모든 핸드오버는 이미 지켜보고 있던 사람이 시작합니다.**

tendon에서는 **정책이 먼저 요청합니다.** 확신도가 낮으면 인터럽트가 발생하고, 상태를
보존한 채 제어권이 넘어가고, 교정이 기록되고, 실행이 재개됩니다. 이것이 확장 가능한
감독과 로봇 한 대당 사람 한 명이 필요한 감독의 차이입니다.
[ADR 0004](docs/decisions/0004-lerobot-already-does-half-of-this.md) 참고.

### 3. 신체는 드라이버다
정책은 특정 로봇을 지정하지 않습니다. 드라이버가 통일된 의도를 각 신체에 맞게 번역합니다.
시뮬레이션의 MuJoCo, 책상 위의 SO-101, 사람의 시연 영상까지 같은 자리에 놓입니다.
시뮬에서 개발하고 실물로 옮길 때 정책 코드는 바뀌지 않습니다.

### 4. 스킬은 패키지다
```
tendon install  grasp/deformable-bag@1.2
tendon fork     grasp/deformable-bag        # 우리 현장 데이터로 파인튜닝
tendon eval     grasp/deformable-bag --episodes 50
tendon publish  mysite/bag-handling
```

## tendon이 실제로 만드는 것

**tendon은 재발명이 아니라 오케스트레이션 층입니다.** 시뮬레이션, 정책 아키텍처,
데이터셋 포맷, 3D 시각화 같은 어려운 부분은 이미 훌륭한 오픈소스가 풀었습니다.
tendon은 아직 아무도 만들지 않은 다섯 가지만 직접 씁니다.

1. **Embodiment HAL** — 신체를 교체 가능하게 만드는 드라이버 규약
2. **인터럽트 프로토콜** — 제어권을 사람에게 넘기고 되받는 방법
3. **큐레이션 지표** — 어떤 에피소드가 학습에 도움이 되고 어떤 것이 해로운가
4. **셸** — 사람이 로봇의 의도를 읽고 개입하는 인터페이스
5. **확신도 추정** — 어떤 정책도 자기가 얼마나 확신하는지 알려주지 않는데,
   그 숫자가 개입을 발생시킵니다 ([ADR 0003](docs/decisions/0003-confidence-has-no-upstream-source.md))

나머지는 전부 조합합니다.

## 기반 오픈소스

| 층 | 사용 | 만들지 않는 것 |
| --- | --- | --- |
| 로봇 제어·데이터셋 | [LeRobot](https://github.com/huggingface/lerobot) | 자체 데이터셋 포맷 |
| 시뮬레이션 | [MuJoCo](https://github.com/google-deepmind/mujoco) | 물리 엔진 |
| 정책 | [OpenVLA](https://github.com/openvla/openvla), SmolVLA, GR00T N1.5 | 파운데이션 모델 |
| 파인튜닝 | [PEFT](https://github.com/huggingface/peft) / LoRA | 학습 프레임워크 |
| 시각화 | [Rerun](https://github.com/rerun-io/rerun) | 3D 렌더러 |
| 레지스트리 | [Hugging Face Hub](https://huggingface.co/docs/hub) | 자체 레지스트리 |
| 런타임·API | FastAPI, Pydantic | RPC 프레임워크 |

## 빠른 시작

GPU도, 로봇도, 시뮬레이터도 필요 없습니다.

```bash
git clone https://github.com/yehsb123/tendon.git
cd tendon
python -m pip install -e ".[dev]"
pytest tests/unit
```

셸도 실행됩니다. 런타임이 연결되지 않았을 때 빈 화면을 라이브인 척 보여주지 않고,
연결되지 않았다고 분명히 말합니다.

```bash
cd shell && npm install && npm run dev     # http://localhost:5273
```

MuJoCo 드라이버가 붙은 뒤 시뮬레이터까지:

```bash
python -m pip install -e ".[sim,dev]"
python examples/01_record/run.py --overhead
```

## 상태

**v0.1 — 개발 중. 시뮬레이션 전용입니다.** 아직 아무것도 동작하지 않고, **실물 로봇에
연결하면 안 됩니다.** 안전 한계(`kernel/safety`)는 구현·테스트됐지만 아직 아무도
호출하지 않고, 인터럽트 경로는 미구현입니다. 로봇 근처에 가시기 전에 [SECURITY.md](SECURITY.md)를 먼저 읽어주세요.

이 프로젝트는 **v0.3에서 증명되거나 폐기됩니다.** 그래프 하나가 나와야 합니다.
*사람이 N번 교정한 뒤, 개입률이 떨어진다.*
그 그래프가 없으면 이후 단계는 의미가 없습니다. [docs/roadmap.md](docs/roadmap.md) 참고.

## 레포 구조

모든 디렉토리에 README가 있고, **무엇이 여기 살고 무엇이 살지 않는지**를 못박아 뒀습니다.
장식이 아니라 구조를 지탱하는 부분입니다.

```
src/tendon/
  kernel/        스케줄러 · 액션 버스 · 인터럽트 · 안전, 그리고 다른 계층이 구현할
                 계약 (types.py, protocols.py)
  drivers/       신체 추상화(HAL) — mujoco, lerobot, so101, 사람 영상
  services/      recorder · curator · trainer · evaluator · registry
  api/           REST(미리 계산 가능한 것) + WebSocket(실시간 의도)
  cli/           tendon 명령

shell/src/
  views/         Live · Episodes · Skills · Training
  panels/        IntentPreview.tsx — 중심 패널, 레이아웃이 흔들리지 않는 고정 그리드
  rerun/         Rerun 뷰어 임베드, 에피소드 타임라인과 시계 동기화
  api/           kernel/types.py를 그대로 미러링한 타입드 클라이언트
  state/         연결 · 에피소드 · 대기 중인 결정
  design/        tokens.css + app.css — 라이트/다크 양쪽, 현장 태블릿 기준

docs/            개념 · 아키텍처 · 스택 · 로드맵 · 용어집 · 협업
  decisions/     ADR — 되돌리기 어려운 결정 하나당 한 파일
skills/          설치 가능한 능력, HF Hub로 배포
examples/        01_record → 04_improve, 각각이 무엇을 증명하는지 순서대로
tests/           unit(CPU 전용, 맨 체크아웃에서 실행) + integration
```

## 문서

| | |
| --- | --- |
| [docs/concepts.md](docs/concepts.md) | 모든 것이 파생되는 네 개의 결정 |
| [docs/architecture.md](docs/architecture.md) | 계층 · 두 개의 클럭 · import 규칙 |
| [docs/stack.md](docs/stack.md) | 모든 의존성과 그 대안, 재검토 조건 |
| [docs/roadmap.md](docs/roadmap.md) | v0.1~v0.4, 각 단계를 무엇이 죽이는가 |
| [docs/glossary.md](docs/glossary.md) | 용어가 어느 분야에서 왔는지 기준으로 정리 |
| [docs/collaboration.md](docs/collaboration.md) | 병렬 트랙과 파일 소유권 |
| [docs/decisions/](docs/decisions/) | ADR |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 규약 · 이식 규칙 · 커밋 포맷 |
| [SECURITY.md](SECURITY.md) | 소프트웨어보다 물리적 안전이 먼저 |

## 라이선스

Apache-2.0
