---
name: ship-dev
description: The commit/push ritual for life-dashboard. Use when the user says "ship", "push to dev", "commit and push", or when a discrete unit of work is complete. ruff → pytest → CHANGELOG → agent logs → push to dev only. Not for mid-task checkpoints.
---

# ship-dev

1. **Lint / format:** `uv run ruff check --fix . && uv run ruff format .`. Revert formatter side effects on files you did not touch.
2. **Tests:** `uv run pytest -q` on the tree you will push. Failed output goes in the report verbatim.
3. **Spend readback** if the round called the model: print the month-to-date figure from the spend counter and compare to the cap in `config.toml`.
4. **CHANGELOG:** prepend `## YYYY-MM-DD · <Title> (<Author>)` + terse bullets. Name the fixture combo (day type × completeness × streak state × season) for any render or summary cycle.
5. **Agent logs:** one bullet per contributing agent under `## Contributions` in `agents/[Role] Name.md`.
6. **Branch check:** `git rev-parse --abbrev-ref HEAD` must be `dev`. Never commit to `main`; the pre-push hook blocks it.
7. **Stage explicit paths**, never `git add -A` once `data/` or scratch files exist. `git diff --staged --stat` before committing.
8. **Commit** non-interactively with the message body and the attribution footer the session provides. **Push** to `dev` if a remote exists.
9. **Issues:** comment on the relevant issue with what shipped and how it was verified. Close only when the Lead's closure rule is met.
