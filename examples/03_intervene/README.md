# 03_intervene — interrupt, correct, resume

The policy raises its own hand, you take control, and the correction is in the episode
afterwards.

**Proves:** design decision 2.
**Needs:** `[sim]`, `[robot]` for the recording half, and the shell built.

No `run.py` here either: the thing being proved is that a human decision reaches the store,
and the human is the part a script cannot supply. The steps are below because an example
that needs one not in its README is broken by this repository's own rule, and these
instructions used to say "force a low-confidence situation" without saying how.

## Run it

```bash
python -m pip install -e ".[sim,robot,dev]"

cd shell && npm install && npm run build && cd ..

tendon serve
```

Open the address `serve` prints. In the **Live** view:

1. Choose `grasp/cube-sim` on `mujoco`, press start.
2. **Wait.** Within the first hundred steps the confidence falls and the run stops itself,
   before the body moves any further. You do not have to catch it — the interrupt waits
   for you.
3. Approve, reject, or open the editor and correct the trajectory.
4. Let the episode finish.

Then, from a terminal:

```bash
tendon episodes             # the episode is there
tendon curate grasp/cube-sim  # it is ranked, and the reasons name your note
```

## Where the uncertainty comes from

Honestly: it is placed there. `UncertainRegion` puts a low-confidence patch at a chosen
point in joint space so the loop has something to hand over about, and the shell says so on
screen. **Everything downstream of that moment is real** — the interrupt is raised before
motion, the decision is recorded, the memory outlives the episode, and a later run recalls
it. The placeholder is the trigger, not the mechanism (ADR 0003).

**Check:** after resuming, the episode is one continuous record with the intervention marked
inside it, not two truncated fragments. And if you wrote a note, `tendon curate` shows it
back to you — an operator's sentence is the only signal in that ranking a person authored.
