# Development

- Windows x64, MaaFramework pipelines, Project Interface V2 and Python agent.
- Every Live must verify the game-provided Auto before starting. Never synthesize rhythm note input.
- Keep minigame implementations in pinned upstream repositories and execute each in its own child process.
- Never replenish game resources automatically. User-entered counts for cooking, solo Live and multiplayer Live must not have a fixed initial value.
- Every action must check its scene and verify its result. The only unknown-state recovery input is one blank-area click during park or login recovery; continue only after recognizing the resulting scene. Never apply that fallback during active gameplay or settlement.
- Foreground execution is the initial support target. Release all held inputs on cancellation and failures.
- Daily limited features may be validated at their interaction entry. Fresh-day full tests are authorized from 04:01 UTC+8 (daily reset 04:00); never repeat exhausted attempts before the next reset. Report entry-only and complete-run evidence separately.
- Record live validation of changed code before its first game input through success, failure or cancellation. Routine runs may use local timestamped action logs without video. For recorded runs, capture failure must prevent further input except release of owned keys. Keep videos and logs locally.
- Keep English and Traditional Chinese documentation aligned. Preserve upstream source, notices, models, settings and records.
- Commit and push source checkpoints to the independent GitHub repository after relevant checks pass and before quota pauses. Keep progress claims accurate and local recordings, settings, logs and diagnostics out of commits. Binary releases require a separate request.
