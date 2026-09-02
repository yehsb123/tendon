# services/ — background daemons

Design decisions 1 and 4. These run alongside execution and close the loop.

| Module | What it does |
| --- | --- |
| `recorder` | Every run is written as a LeRobot-format episode. No separate collection mode. |
| `curator` | Scores episodes and selects what is worth training on. The metrics live here. |
| `trainer` | LoRA fine-tuning on curated episodes and recorded corrections. |
| `evaluator` | Runs a skill against its eval set and reports success rate and failure modes. |
| `registry` | Resolves, installs and publishes skills via the Hugging Face Hub. **v0.4, a stub.** |

Reading what was written, without the stack that wrote it. Each of these works on a machine
that cannot record:

| Module | What it does |
| --- | --- |
| `store` | What has been recorded, from the directory layout. Never imports the recorder. |
| `episodes` | What happened inside one, from the parquet via duckdb. Also ranks them, since the command line and the API both need one ranking. |
| `progress` | One line per finished episode: how often it asked, and how much it had been taught by then. The two axes of the graph. |
| `memory_store` | What an operator taught, kept across restarts. Derived state, beside the episodes rather than inside them. |

Running something on a body:

| Module | What it does |
| --- | --- |
| `skill` | Loads `skill.yaml` and checks it against a body **before** anything moves. |
| `limits` | The machine's ceiling over what a skill asks for. Tightens only — a file that could loosen a skill's own bound would be a way to turn a safety limit off by editing a config. |
| `bodies` | Finds drivers by scanning the package, and refuses a physical one unless asked. |
| `policies` | `FunctionPolicy` and `ReplayPolicy`: no model, used to exercise the loop and as the fixed baseline an evaluation needs. |
| `policy_scripted` | `ScriptedPolicy`: plays a real grasp. What a skill names in `policy.baseline`. |
| `policy_lerobot` | Adapts a LeRobot checkpoint to the `Policy` protocol. |
| `adaptive` | The correction memory and the policy that recalls from it. Where the intervention rate falls. |
| `confidence` | Estimates confidence from sample spread — the only estimator there is, since a policy rarely reports its own (ADR 0003). |
| `calibration` | Measures what sample spread is *typical* for one policy on one body, so `confidence` has a scale to score against. The scale, not the threshold: how much disagreement means ask for help needs intervention outcomes and stays v0.3. |
| `viz` | Streams a run into Rerun. Attached when somebody is watching, not to every run: it costs about ten times what the recorder does. |

## Why curation is the hard part

Collecting is easy; the recorder is a few hundred lines. Deciding **which** of the
day's 300 episodes should shape tomorrow's policy is the open problem. A metric that
mislabels good episodes as bad poisons training silently, and no downstream test
will catch it.

Intervention episodes are the most valuable data in the store. They are the only
place where a recovery from failure is recorded.
