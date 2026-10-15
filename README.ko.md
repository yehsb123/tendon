# tendon

[English](README.md) · **한국어**

tendon은 피지컬 AI를 위한 운영 계층입니다. 학습된 정책(policy)을 로봇 몸에 돌리면서 모든 실행을 학습 데이터로 기록하고, 정책이 확신하지 못하면 사람에게 제어권을 넘깁니다. 사람이 고친 동작은 다음 학습에 들어갑니다.

이름은 근육과 뼈를 잇는 힘줄(tendon)에서 왔습니다. 이 프로젝트는 정책과 로봇을 잇고, 둘이 만나는 지점에 사람을 둡니다.

## 하는 일

로봇 쪽에는 이미 좋은 오픈소스가 많습니다. ROS는 노드 사이에서 메시지를 옮기는 통신 미들웨어이고, 학습된 AI 정책을 운영한다는 개념은 없습니다. tendon은 일반 OS가 주는 기능 가운데 로봇에 아직 없는 부분을 채웁니다.

| 일반 OS의 기능 | 지금 로봇 쪽에 있는 도구 | tendon이 채우는 부분 |
| --- | --- | --- |
| 디바이스 드라이버 | ROS 2 | 로봇마다 다른 액션 공간을 하나로 묶는 추상화 |
| 프로세스 관리 | 없음 | 여러 정책과 스킬의 스케줄링 |
| 파일 시스템 | 없음 | 경험을 저장하고 버전을 관리하는 저장소 |
| 시스템 로그 | 없음 | 로봇이 왜 그렇게 움직였는지의 기록 |
| 셸 | 없음 | 사람이 보고 개입하는 화면 |
| 인터럽트 | 비상정지(전원 차단) | 상황을 보존한 채 제어권 넘기기 |
| 패키지 매니저 | 없음 | 스킬 설치와 공유 |

설계는 네 가지 결정에서 나옵니다.

1. 실행이 곧 수집입니다. 데이터 수집 모드가 따로 없고, 모든 실행이 에피소드로 기록되어 선별을 거쳐 학습으로 돌아갑니다.
2. 정책이 먼저 도움을 요청합니다. 확신도가 낮으면 인터럽트가 걸리고, 상태를 보존한 채 제어권이 사람에게 넘어갑니다. 교정이 기록되면 실행이 다시 이어집니다.
3. 몸은 드라이버입니다. 정책은 특정 로봇을 지정하지 않고, 드라이버가 MuJoCo 시뮬레이션, 책상 위의 SO-101, 사람의 시연 영상에 맞게 명령을 옮깁니다. 시뮬레이션에서 실물로 옮겨도 정책 코드는 바뀌지 않습니다.
4. 스킬은 패키지입니다. 지금 되는 명령은 `tendon eval` 하나이고, `install`, `fork`, `publish` 는 [로드맵](docs/roadmap.md)의 v0.4 목표입니다.

LeRobot의 DAgger 방식도 사람이 실행 중에 개입하고 교정을 기록합니다. 차이는 개입을 누가 시작하는지에 있습니다. LeRobot에서는 지켜보던 사람이 시작하고, tendon에서는 정책이 먼저 요청합니다([ADR 0004](docs/decisions/0004-lerobot-already-does-half-of-this.md)).

tendon이 직접 만드는 부분은 다섯 가지입니다. 나머지는 아래 기반 오픈소스를 조합합니다.

- Embodiment HAL: 몸을 바꿔 끼울 수 있게 하는 드라이버 규약
- 인터럽트 프로토콜: 제어권을 사람에게 넘기고 되받는 방법
- 큐레이션 지표: 어떤 에피소드가 학습에 도움이 되는지 매기는 기준
- 셸: 사람이 로봇의 의도를 읽고 개입하는 화면
- 확신도 추정: 정책은 자기 확신도를 알려 주지 않으므로 tendon이 따로 계산합니다. 지금 쓰는 신호는 같은 상황에서 뽑은 예측들이 서로 얼마나 어긋나는지입니다. 비슷한 상태에서 시작한 성공 시연에서 얼마나 벗어났는지를 재는 두 번째 신호는 추정기로만 있고, 성공 판정이 저장된 뒤 런타임에 연결합니다([ADR 0003](docs/decisions/0003-confidence-has-no-upstream-source.md), [ADR 0007](docs/decisions/0007-demonstration-distance-is-a-second-confidence-source.md))

## 실행 결과

`examples/04_improve/run.py` 를 기본 설정으로 한 번 돌린 출력입니다. 가로축은 누적 교정 수, 세로축은 최근 10 에피소드 동안 사람이 개입한 비율입니다.

```
$ python examples/04_improve/run.py

intervention rate over a trailing window of 10 episodes

100% │█
 88% │█████
 75% │████████
 62% │██████████ █
 50% │█████████████
 38% │████████████████
 25% │████████████████████████  ███                    ██
 12% │████████████████████████████████████         ██████
     └───────────────────────────────────────────────────
      0                                            52 corrections

  first 10 episodes : 100% interrupted
  last  10 episodes :  20% interrupted
  corrections stored : 52

  Success rate:   0% over the first 10 judged
                  0% over the last 10 judged
```

개입률은 100%에서 20%로 떨어졌지만 과제 성공률은 처음과 끝 모두 0%입니다. 이 예제의 정책은 큐브를 집으러 가지 않는 관절 스윕이고, 불확실한 구간은 관절 공간의 한 지점에 일부러 넣었습니다. 이 그래프가 보여 주는 결과는 인터럽트, 교정, 기억이 하나의 순환으로 이어진다는 점이고, 정책이 과제를 배웠다는 뜻은 아닙니다.

[`tests/integration/test_shell_loop_closes.py`](tests/integration/test_shell_loop_closes.py) 는 셸에서 같은 에피소드를 두 번 돌려 대조합니다. 교정하면 정책이 덜 묻고, 승인만 하면 처음과 같은 빈도로 묻습니다. 테스트는 정확한 숫자가 아니라 개입률이 떨어지는 모양을 확인합니다.

스케줄러, 안전 검사, 인터럽트 상태 머신, 평가기는 학습된 정책을 돌릴 때와 같은 코드입니다.

임시로 대신 넣은 부분은 둘입니다. 불확실한 구간은 실제 모델이 낯선 물체나 처음 보는 시야에서 보이는 불확실성을 흉내 낸 대역(stand-in)이고, 교정하는 사람도 스크립트입니다. 실제 정책과 실제 조작자로 바꾸는 작업이 v0.3 실험입니다.

## 설치

GPU, 로봇, 시뮬레이터 없이 단위 테스트까지 돌릴 수 있습니다.

```bash
git clone https://github.com/yehsb123/tendon.git
cd tendon
python -m pip install -e ".[dev]"
pytest tests/unit
```

시뮬레이터와 기록 기능까지 쓰려면 extra를 더 설치합니다. `sim` 은 MuJoCo, `robot` 은 LeRobot, `train` 은 PyTorch와 PEFT를 설치합니다.

```bash
python -m pip install -e ".[sim,robot,dev]"
```

## 사용

### 화면에서 개입하기

```bash
cd shell && npm install && npm run build && cd ..
python -m pip install -e ".[sim]"
tendon serve                               # http://127.0.0.1:8000
```

명령 하나로 런타임과 셸 화면이 함께 뜹니다. 화면에서 에피소드를 시작하면 몸이 움직이는 대로 궤적이 갱신되고, 정책이 확신하지 못하면 제어권을 넘기고 기다립니다.

화면에서 시작한 에피소드도 명령줄에서 시작한 에피소드와 같은 데이터셋에 기록됩니다.

셸 탭은 다섯 개입니다.

- Live: 지금 실행 중인 에피소드와, 이 스킬에 대해 지금까지 가르친 교정 수
- Episodes: 기록된 에피소드 목록
- Skills: 설치된 스킬
- Curate: 학습 가치 순으로 매긴 에피소드 순위와 그 이유
- Progress: 누적 교정에 따른 개입률 그래프. 에피소드가 10개 쌓이기 전에는 몇 개가 더 필요한지 표시합니다

사람이 한 교정은 세 곳에 남습니다. 로봇의 다음 동작, 정책의 기억(`~/.tendon/memory`), 에피소드의 `interrupts` 테이블입니다.

기억은 스킬과 몸마다 따로 쌓이고 재시작해도 남습니다. 기억을 지워도 에피소드 기록은 그대로입니다.

셸을 고칠 때는 dev 서버를 씁니다. 수정이 바로 반영되고 `/api` 와 `/ws` 요청은 런타임으로 넘깁니다.

```bash
tendon serve                  # 터미널 1
cd shell && npm run dev       # 터미널 2, http://localhost:5273
```

### 기록하고 학습하기

```bash
tendon run grasp/cube-sim --driver-arg render_cameras=wrist   # 영상과 함께 기록
tendon train grasp/cube-sim                                   # 큐레이션 후 LoRA 파인튜닝
tendon run grasp/cube-sim --policy adapter \
  --driver-arg render_cameras=wrist                           # 학습한 어댑터로 실행
```

`tendon run` 과 `tendon eval` 은 항상 기록합니다. 기록을 켜거나 끄는 플래그는 없습니다. `examples/01_record/run.py` 를 한 번 돌렸을 때 기록에 든 시간은 스텝당 0.027 ms, 10 ms 제어 주기의 0.27%였습니다.

```
$ tendon run grasp/cube-sim
grasp/cube-sim 0.1.0 on so_arm100_cube (5 axes, 100 Hz) via scripted
episode        e710613da75f
steps          500
recorded to    ~/.tendon/episodes
```

LeRobot이 설치되지 않은 환경에서는 기록할 수 없다고 알리고 실행만 계속합니다. 기록을 시작했다가 실패하면 명령은 0이 아닌 코드로 끝납니다.

`tendon train` 은 영상이 있는 녹화본을 큐레이션 순위대로 골라 스킬의 베이스 정책에 LoRA 어댑터를 학습합니다. `--policy adapter` 는 베이스 체크포인트를 `skill.yaml` 이 아니라 어댑터 파일에서 읽습니다. 다른 가중치에 얹은 LoRA는 오류 없이 로드되고 틀린 동작을 내기 때문에, 둘이 어긋나면 실행을 거부합니다.

### 개입 기준 정하기

`tendon calibrate` 는 그 정책이 그 몸에서 보이는 보통의 예측 불일치를 측정해 확신도의 기준을 만듭니다. 이 기준이 없으면 체크포인트는 확신도를 보고하지 못하고 스스로 인터럽트를 걸 수 없으며, 실행 전에 그렇게 알립니다.

기준은 중앙값이라서 `skill.yaml` 의 기본 임계값 0.5는 예측 두 번에 한 번 사람을 부릅니다. `smolvla_base` 와 어댑터로 잰 값입니다.

```
임계값    사람을 부르는 비율
0.5       50%  (skill.yaml 기본값)
0.4       19%
0.3        0%
```

어느 임계값이 맞는지는 이 측정으로 정할 수 없습니다. 사람을 부르지 않았을 때 무엇이 잘못되는지는 실제로 개입한 에피소드가 쌓여야 보이고, 이 판단은 v0.3 범위입니다.

### 실행 보기와 큐레이션

```bash
tendon run grasp/cube-sim --view                 # Rerun 뷰어를 엽니다
tendon run grasp/cube-sim --view-save run.rrd    # 나중에 볼 파일로 저장합니다
```

Rerun 뷰어에는 명령한 값과 실제 적용된 값이 같은 축에 그려집니다. 둘 사이가 벌어진 구간이 몸이 명령을 따르지 못한 곳입니다. 확신도, 넘김 임계값, 안전 검사가 잘라낸 지점도 함께 그려집니다.

`tendon curate` 는 저장된 에피소드를 저크, 멈춰 있던 비율, 그리퍼 흔들림, 다른 에피소드 대비 길이로 점수 매기고 점수마다 이유를 붙입니다. 에피소드를 지우거나 임계값으로 거르지 않습니다. 조작자가 제어권을 넘겨받은 에피소드는 실패에서 회복하는 장면이 담긴 기록이라 점수와 상관없이 맨 위에 둡니다.

큐레이션은 parquet를 LeRobot 대신 duckdb로 읽습니다. 그래서 시뮬레이터나 PyTorch가 없는 노트북에서도 다른 곳에서 모은 데이터를 큐레이션할 수 있습니다.

### 실물 로봇과 드라이버 인자

실제 하드웨어를 움직이는 몸은 `--physical` 없이는 실행되지 않고, 어느 몸이 그런지는 `tendon doctor` 가 알려 줍니다. 드라이버 인자는 `--driver-arg key=value` 로 넘기고, 여러 값은 쉼표로 잇습니다(`--driver-arg render_cameras=wrist,scene`).

스킬은 이름(`grasp/cube-sim`)이나 스킬 디렉터리 경로로 부릅니다. 스킬의 `policy.baseline` 에는 모델 없이 그 스킬을 시도하는 방법을 적고, 없으면 평가는 관절 스윕으로 돌아갑니다.

### 명령

<!-- BEGIN generated:commands -->

```
tendon version           버전 출력
tendon doctor            이 환경에서 되는 기능과 빠진 부분, 빠졌을 때 잃는 기능
tendon run <skill>       커널 위에서 정책을 몸에 실행하고 기록
tendon serve             런타임 API, 셸이 빌드돼 있으면 셸 화면까지
tendon shell             serve와 같고, 이 환경에서 화면을 여는 방법을 안내
tendon progress          개입률이 줄고 있는지 터미널에 그래프로 표시
tendon episodes          기록된 에피소드 목록
tendon calibrate <skill> 보통의 예측 불일치를 측정해 확신도 기준 생성
tendon curate <skill>    기록된 에피소드를 학습 가치 순으로 정렬
tendon train <skill>     큐레이션한 에피소드로 베이스 정책을 LoRA 파인튜닝
tendon eval <skill>      반복 실행하고 기록한 뒤 결과 보고
```

<!-- END generated:commands -->

## 기반 오픈소스

<!-- BEGIN generated:built-on -->

| 층 | 사용 | tendon이 만들지 않는 부분 |
| --- | --- | --- |
| 로봇 제어, 데이터셋 | [LeRobot](https://github.com/huggingface/lerobot) | 자체 데이터셋 포맷 |
| 시뮬레이션 | [MuJoCo](https://github.com/google-deepmind/mujoco) | 물리 엔진 |
| 정책 | LeRobot 정책: ACT, Diffusion, pi0, SmolVLA | 파운데이션 모델 |
| 학습, 추론 | [PyTorch](https://github.com/pytorch/pytorch) | 텐서 연산 라이브러리 |
| 파인튜닝 | [PEFT](https://github.com/huggingface/peft) / LoRA | 학습 프레임워크 |
| 시각화 | [Rerun](https://github.com/rerun-io/rerun) | 3D 렌더러 |
| 레지스트리 (v0.4 목표) | [Hugging Face Hub](https://huggingface.co/docs/hub) | 자체 레지스트리 |
| 런타임, API | FastAPI, Pydantic | RPC 프레임워크 |

<!-- END generated:built-on -->

## 상태

v0.1이고 시뮬레이션 전용입니다. 위의 순환은 처음부터 끝까지 돕니다. 스케줄러는 모든 액션을 `kernel/safety` 에 통과시키고, 확신도가 낮으면 인터럽트를 걸고, 조작자의 교정도 정책 액션과 같은 한계 안에서 적용합니다.

실물 로봇에는 아직 연결하지 않습니다. SO-101 드라이버는 있지만 실제 몸으로 검증한 적이 없고, 안전 한계는 시뮬레이션에서만 확인했으며, 셸과 런타임 사이에 인증이 없습니다. 로봇 근처에서 쓰기 전에 [SECURITY.md](SECURITY.md)를 먼저 읽습니다.

이 프로젝트는 v0.3에서 증명되거나 접습니다. 사람이 교정한 횟수가 늘수록 개입률이 떨어지는 그래프를 실제 정책과 실제 조작자로 얻어야 다음 단계로 갑니다([docs/roadmap.md](docs/roadmap.md)).

## 폴더 구조

디렉터리마다 README가 있고, 그 디렉터리에 들어가는 코드와 들어가지 않는 코드를 적어 두었습니다.

<!-- BEGIN generated:layout -->

```
src/tendon/
  kernel/        스케줄러, 스텝 버스, 인터럽트, 안전 검사, 다른 계층이 구현할 계약
                 (types.py, protocols.py)
  drivers/       몸 추상화(HAL): 사람 시연 영상, mujoco, so101
  services/      recorder, curator, confidence, demonstration, calibration, adaptive,
                 evaluator, policies, skill, bodies, trainer, progress, registry 등
  api/           REST, WebSocket, 둘을 잇는 세션
  cli/           tendon 명령
  assets/        로봇 모델과 장면 파일

shell/src/
  views/         Curate, Episodes, Live, Progress, Skills
  panels/        의도 미리보기, 궤적 미리보기, 교정 편집기
  rerun/         Rerun 뷰어 임베드, 에피소드 타임라인과 시계 동기화
  api/           kernel/types.py와 같은 타입을 쓰는 클라이언트
  state/         연결, 에피소드, 대기 중인 결정
  design/        tokens.css, app.css (라이트와 다크, 현장 태블릿 기준)

docs/            개념, 아키텍처, 스택, 로드맵, 용어집, 협업
  decisions/     ADR (되돌리기 어려운 결정 하나당 파일 하나)
skills/          설치할 수 있는 스킬 (grasp/cube-sim)
examples/        01_record부터 04_improve까지, 순서대로 하나씩 증명
tests/           unit(CPU만, 설치 직후 실행 가능), integration
```

<!-- END generated:layout -->

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/concepts.md](docs/concepts.md) | 네 가지 설계 결정 |
| [docs/architecture.md](docs/architecture.md) | 계층, 두 개의 클럭, import 규칙 |
| [docs/stack.md](docs/stack.md) | 의존성과 대안, 다시 검토할 조건 |
| [docs/roadmap.md](docs/roadmap.md) | v0.1~v0.4와 각 단계를 중단시킬 조건 |
| [docs/glossary.md](docs/glossary.md) | 용어와 그 용어가 나온 분야 |
| [docs/collaboration.md](docs/collaboration.md) | 병렬 트랙과 파일 소유권 |
| [docs/decisions/](docs/decisions/) | ADR |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 규약, 이식 규칙, 커밋 형식 |
| [SECURITY.md](SECURITY.md) | 물리적 안전 수칙 |

## 라이선스

Apache-2.0
