# 03_intervene — interrupt, correct, resume

Force a low-confidence situation, take control, correct it, and find the correction in the
episode store afterwards.

**Proves:** design decision 2.
**Needs:** `[sim]` plus the shell.

**Check:** after resuming, the episode is one continuous record with the intervention marked
inside it, not two truncated fragments.
