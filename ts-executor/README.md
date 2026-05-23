# ts-executor — Base chain executor

**Placeholder.** This directory will be populated by the validated copy-back step
from `.archive/ts-executor/` per the blueprint's archive-first protocol:

1. Run existing tests in `.archive/ts-executor/` — confirm green.
2. Copy to this directory.
3. Re-run tests here — confirm still green.
4. Extend `shared/schemas/` and message envelopes with the `chain` discriminator.
5. Wire to `execution:orders:base` / `execution:results:base` partitioned channels.

The v4.2 ts-executor is launch-ready (per `.archive/harness/features.json`,
all EXEC-* and LISTEN-* features pass). Copy-back budget: ~half a day for
the type extension. See `docs/blueprint.md` §"Build sequence" week 1 and
§"Archive-first protocol" in `CLAUDE.md`.
