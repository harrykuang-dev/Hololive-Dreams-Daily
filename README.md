# hololive-Dreams Daily

A Windows daily-routine program built with MaaFramework, MFAAvalonia and isolated Holodori-Auto modules. Live gameplay is restricted to the game's built-in Auto. Resource consumption is configurable; automatic replenishment is disabled.

Development is in progress. The current source includes the task configuration interface, recording-based recognition, Auto checks, result journals, navigation primitives and isolated worker supervision. Full daily execution and portable packaging are not yet accepted. Only Connect is selected by default.

Cooking, solo Live and multiplayer Live each require a user-entered round count. Fishing uses the second location shown in the reference recording. Once-daily features can be validated at their interaction entry without consuming a play.

Game-window recordings and time-aligned step logs are required for live task execution. Recording failure stops further actions while still releasing held keys. Recordings remain local; development staging places them beside the staged application under `recordings`. Once-daily full tests use fresh attempts after04:05 UTC+8; the game day resets at04:00.

Recorded recovery checks have verified the date-change notice, Halloween and standard login bonuses, received-reward pages and the November membership announcement. Each recognized page is confirmed once before waiting for its next state. These checks used separate recovery runs; an uninterrupted reset sequence remains pending. Other announcement layouts stop for validation.

The game must already be open. Its Login and TAP TO START title steps have passed separate recorded checks; TAP TO START has led through park navigation to a complete Auto Live run. An uninterrupted run through both title steps remains pending.

Live testing has verified map selection, teleport, bounded approach and the fishing setup entry at the second site, 菈米的冰天雪堡. A recorded foreground test from that setup caught three fish with infinite practice bait. A subsequent uninterrupted test navigated from the park, caught one fish at that site, stopped at the configured target and returned to the park. Other bait remains disabled. Navigation verifies the place name before teleporting and handles the player marker covering the fishing icon.

Cooking, Jump Rope and solo Live have reached their final setup screens in entry checks. Cooking gameplay remains disabled. Jump Rope has passed an uninterrupted recorded round with a ten-point stop score, fresh resource authorization, score and reward confirmation, the additional event page and return to setup. A following reward task verified returning directly from the minigame to the park. Multi-round acceptance remains pending. User settings are stored in independent Maa nodes; native integration checks verify that round counts and budgets survive multiple UI option overrides.

High & Low has passed recorded map navigation and interaction-entry verification. The route selects the arena's play-area map tab, confirms Shiori's table before teleporting and stops at the interaction prompt. It does not open or play the daily game; configurable-cap worker integration remains pending.

Solo Live has passed recorded single-round foreground tests with one-energy, nine-energy and zero-energy consumption, each verifying built-in Auto, its remaining uses, the Auto Live result and return to song selection. A zero resource budget permits play only when the observed effective cost is zero; it does not replenish energy or waive Auto-use checks. The game's consumption multiplier remains positive. Multi-round execution and per-round budget checks are implemented; multi-round live testing remains pending. The current song, difficulty and team must be retained. Multiplayer has passed recorded navigation from the park to its mode-selection page. Matchmaking, the ready/start entry and multiplayer play remain unverified and disabled.

Live result handling checks received GET pages and achievement details before advancing, and waits through transient frames without speculative input. A later live test exposed these extra rewards after one confirmed Auto song. The updated handling has passed offline recognition and regression checks; its next foreground run remains pending.

holoWork has passed an uninterrupted recorded collection-to-redispatch test, including reward confirmation, retained team settings and fresh countdowns while existing work continues. Built-in team recommendation is available for fully empty assignments; partially filled visible teams stop for review. The task is skipped when no completed-work notification is available.

Park gifts have recorded GET evidence at all three locations in separate runs. The holo-house run reached its gift prompt and received GET, then stopped because the return prompt changed to the character's name. Return handling was corrected, and a later uninterrupted run collected the remaining two locations. An uninterrupted three-location run remains pending. The route waits through teleport fades and brief marker animation, and supports changing daily characters. Camera corrections remain bounded and stop if fresh marker evidence moves in the wrong direction. A fresh-day lottery test verified entry and the first interaction, then a recorded recovery advanced the same open dialogue, scratched once, confirmed GET and returned to the park. Uninterrupted lottery execution remains pending.

Once-daily attempts are recorded before the first interaction. A recorded failed lottery attempt can resume its exact still-open introduction once, after verifying the finalized original recording and a new active recording. It does not repeat the initial interaction. Other uncertain attempts remain blocked for that game day, and are excluded when looking for remaining gift locations.

Reward collection checks daily, weekly and season missions, the pass, achievements, the gift box and the current event's daily and free missions. An uninterrupted recorded run confirmed seven available reward groups, including actual event rewards, and returned to the park. A claim is clicked once and requires a confirmed received or disabled state before proceeding. Empty-page checks are also verified; future event layouts require validation.

## Development

Use Python 3.13 x64 and .NET 10. Install `requirements-dev.txt`, then run:

```powershell
python tools/build_resources.py
python tools/validate_resources.py
python -m pytest -q
```

Clone with submodules. `dependencies.lock.json` records the selected upstream commits. Upstream modules require their own runtime dependencies and models; the Maa agent environment is kept separate. See `THIRD_PARTY_NOTICES.md` and `upstream/holodori/LICENSES.md` for component notices.

For the separate Jump Rope interpreter, install `requirements-modules-jump.txt` and run `python tools/check_jump_runtime.py`. This performs DirectML model warmup without game input; optional `--sample` arguments verify rope and player-marker detection on local game-only frames. Fifteen warmup frames and three reference frames passed on the development machine. This does not validate gameplay timing or enable the task's worker.

The Jump Rope worker requests a separate parent authorization for each round after its first captured frame. The parent rechecks the actual cost, resource balance and budget before starting. Adapter tests cover readiness, cancellation and stale authorization; the native test verified the handshake and the configured stop score. The optional stop score accepts1–999. Zero-energy Jump Rope still requires native acceptance.

