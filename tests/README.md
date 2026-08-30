# tests/

```
unit/           pure logic — action encoding, curation metrics, interrupt state machine
integration/    kernel + a real driver (MuJoCo), no GPU required
fixtures/       small recorded episodes committed to git for deterministic tests
```

CI runs `unit/` and `integration/` on CPU only. Anything needing a GPU or physical
hardware is marked `@pytest.mark.hardware` and skipped by default.

Curation metrics get the strictest tests: a metric that silently mislabels good
episodes as bad will quietly poison training, and nothing downstream will catch it.
