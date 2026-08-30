# services/ — background daemons

Design decisions 1 and 4. These run alongside execution and close the loop.

| Module | What it does |
| --- | --- |
| `recorder` | Every run is written as a LeRobot-format episode. No separate collection mode. |
| `curator` | Scores episodes and selects what is worth training on. The metrics live here. |
| `trainer` | LoRA fine-tuning on curated episodes and recorded corrections. |
| `evaluator` | Runs a skill against its eval set and reports success rate and failure modes. |
| `registry` | Resolves, installs and publishes skills via the Hugging Face Hub. |

## Why curation is the hard part

Collecting is easy; the recorder is a few hundred lines. Deciding **which** of the
day's 300 episodes should shape tomorrow's policy is the open problem. A metric that
mislabels good episodes as bad poisons training silently, and no downstream test
will catch it.

Intervention episodes are the most valuable data in the store. They are the only
place where a recovery from failure is recorded.
