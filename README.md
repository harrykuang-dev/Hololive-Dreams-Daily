# hololive-Dreams Daily

A Windows daily-routine program built with MaaFramework, MFAAvalonia and isolated Holodori-Auto modules. Live gameplay is restricted to the game's built-in Auto. Resource consumption is configurable; automatic replenishment is disabled.

Development is in progress. The current source includes the task configuration interface, recording-based recognition, Auto checks, result journals, navigation primitives and isolated worker supervision. Full daily execution and portable packaging are not yet accepted. Only Connect is selected by default.

Cooking, solo Live and multiplayer Live each require a user-entered round count. Fishing uses the second location shown in the reference recording. Once-daily features can be validated at their interaction entry without consuming a play.

Game-window recordings and time-aligned step logs are required for live task execution. Recording failure stops further actions while still releasing held keys. Recordings remain local; development staging places them beside the staged application under `recordings`. Once-daily full tests use fresh attempts after04:05 UTC+8; the game day resets at04:00.

Live testing has verified map selection, teleport, bounded approach and the fishing setup entry at the second site, 菈米的冰天雪堡. Validation stops before Start; fishing gameplay has not been accepted. Navigation verifies the place name before teleporting and handles the player marker covering the fishing icon.

Cooking, Jump Rope and solo Live have reached their final setup screens in entry checks. Cooking and Jump Rope gameplay modules remain disabled. User settings are stored in independent Maa nodes; native integration checks verify that round counts and budgets survive multiple UI option overrides.

Solo Live has passed a one-round foreground test: verifying built-in Auto and its remaining uses, setting a one-energy cost, waiting for the game's Auto play, verifying the Auto Live result and returning to song selection. Multi-round execution and per-round budget checks are implemented; multi-round live testing remains pending. The current song, difficulty and team must be retained. Multiplayer matchmaking and play remain disabled.

holoWork collection, reward confirmation, built-in team recommendation and redispatch are implemented. Recorded foreground tests verified collection and a subsequent recovered dispatch, including fresh countdowns. A single uninterrupted collection-to-dispatch test remains pending. Existing filled teams are retained; partially filled visible teams stop for review. Navigation stops when no completed-work notification is available.

## Development

Use Python 3.13 x64 and .NET 10. Install `requirements-dev.txt`, then run:

```powershell
python tools/build_resources.py
python tools/validate_resources.py
python -m pytest -q
```

Clone with submodules. `dependencies.lock.json` records the selected upstream commits. Upstream modules require their own runtime dependencies and models; the Maa agent environment is kept separate. See `THIRD_PARTY_NOTICES.md` and `upstream/holodori/LICENSES.md` for component notices.

