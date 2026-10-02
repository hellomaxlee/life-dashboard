# Claude Code status-line fixtures (SYNTHETIC)

Written from the documented status-line JSON (code.claude.com/docs/en/statusline), not captured
from a session. These are what Claude Code pipes to the hook's stdin, not the hook's output.

| File | What it exercises |
|---|---|
| `statusline_week_41.json` | Both windows present: `seven_day` 41.2 % resetting 2026-10-05T16:00:00Z, `five_hour` 73.5 %. Golden case for issue #2; the five-hour mutant must flip it |
| `statusline_no_limits.json` | The feed before the first model response: no `rate_limits`. The hook must leave the last reading in place |
