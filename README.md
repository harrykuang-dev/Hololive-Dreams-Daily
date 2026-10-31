# hololive-Dreams Daily

A Windows daily-routine program built with MaaFramework, MFAAvalonia and isolated Holodori-Auto modules. Live gameplay is restricted to the game's built-in Auto. Resource consumption is configurable; automatic replenishment is disabled.

Development is in progress. The current source includes the task configuration interface, recording-based recognition, Auto checks, result journals, navigation primitives and isolated worker supervision. Full daily execution and portable packaging are not yet accepted. Only Connect is selected by default.

Cooking, solo Live and multiplayer Live each require a user-entered round count. Fishing uses the second location shown in the reference recording. Once-daily features can be validated at their interaction entry without consuming a play.

Live testing has verified map selection, teleport, bounded approach and the fishing setup entry at the second site, 菈米的冰天雪堡. Validation stops before Start; fishing gameplay has not been accepted. Navigation verifies the place name before teleporting and handles the player marker covering the fishing icon.

Cooking, Jump Rope and solo Live have also reached their final setup screens in live entry-only checks, with no play started. User settings are stored in independent Maa nodes; native integration checks verify that round counts and budgets survive multiple UI option overrides.

## Development

Use Python 3.13 x64 and .NET 10. Install `requirements-dev.txt`, then run:

```powershell
python tools/build_resources.py
python tools/validate_resources.py
python -m pytest -q
```

Clone with submodules. `dependencies.lock.json` records the selected upstream commits. Upstream modules require their own runtime dependencies and models; the Maa agent environment is kept separate. See `THIRD_PARTY_NOTICES.md` and `upstream/holodori/LICENSES.md` for component notices.

