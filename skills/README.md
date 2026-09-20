# skills/ — installable robot capabilities

A skill is a policy plus everything needed to run and evaluate it:

```
skills/<namespace>/<name>/
  skill.yaml        everything the loader reads: identity, requirements, safety
                    limits, interrupt threshold, policy references, eval criteria
  policy/           README only today; a LoRA adapter lands here after `tendon train`
  eval/             README only today; criteria live in skill.yaml's eval block
  README.md
```

`skill.yaml` is the whole format. The two subdirectories describe where files will go and
hold none yet, which is worth saying here because the previous version of this list read
as though they did.

Keys inside `requires`, `safety`, `interrupt`, `policy` and `eval` are closed: an
unrecognised one is refused at load, naming both what you wrote and what is known. A
misspelled key is not read, and nothing downstream reports it as missing — the failure is
a system quietly doing something other than what the file says. New *blocks* at the top
level are still accepted, which is where a later version of the format would add things.

Skills are distributed through the Hugging Face Hub. tendon does not run its own
registry — `skill.yaml` points at a Hub repo. Resolving it is `tendon install`, which is
v0.4 and does not exist yet; today a base policy is fetched by `tendon run --policy
adapter` and `tendon train` through the Hub id in `policy.base`.

**Lives here:** reference skills maintained by this repo.
**Does not live here:** your site-specific forks. Those live in your own repo.
