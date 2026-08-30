# design/ — tokens and primitives

A small system, because the shell has few screens and one job.

```
tokens.css     colour, spacing, type scale, motion
primitives/    Button, Badge, Meter, Panel, Timeline
```

## Constraints from the room, not from taste

**Readable at arm length on a tablet, in bad light.** Minimum body size is larger than a
desktop tool would use.

**Colour is never the only signal.** Confidence, safety state and interrupt state each
carry a shape or a label as well. Factory floors have glare, and operators have colour
vision deficiency at the same rate as everyone else.

**Motion is informative or absent.** A trajectory animating along its path is information.
A panel sliding in is delay.

**One accent.** The interrupt state owns it. If anything else competes for attention, the
thing that actually needs a human gets slower to find.
