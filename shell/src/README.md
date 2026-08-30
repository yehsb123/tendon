# shell/src/

```
views/       full screens: Live, Episodes, Skills, Training
panels/      composable pieces: IntentPreview, ConfidenceMeter, InterruptPrompt
rerun/       Rerun web viewer embedding and scene wiring
api/         typed client for src/tendon/api (REST + WebSocket)
state/       session, connection, pending-intent store
design/      tokens and primitives
```

`panels/IntentPreview` is the centre of the project. Everything else supports it.
