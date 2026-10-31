# Development

- Windows x64, MaaFramework pipelines, Project Interface V2 and Python agent.
- Every Live must verify the game-provided Auto before starting. Never synthesize rhythm note input.
- Keep minigame implementations in pinned upstream repositories and execute each in its own child process.
- Never replenish game resources automatically. User-entered counts for cooking, solo Live and multiplayer Live must not have a fixed initial value.
- Every action must check its scene and verify its result. Unknown states stop without speculative clicking.
- Foreground execution is the initial support target. Release all held inputs on cancellation and failures.
- Daily limited features may be validated at their interaction entry; report entry-only and complete-run evidence separately.
- Keep English and Traditional Chinese documentation aligned. Preserve upstream source, notices, models, settings and records.
- Do not publish or push without user authorization.
