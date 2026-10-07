# tools.summary — the daily line

`uv run python -m tools.summary --date YYYY-MM-DD [--force]` writes the line shown on that
day (it describes the day before). The scheduler's `daily_summary` job does the same at
`[summary] time` (06:50 home time). Both go through `app.summary.run.write_summary`.

## Flow

Build the payload from the metrics tables, read the recent-lines memory, check the monthly
cap, call the model (two attempts: the gate's reason is fed back once), gate the line, store
it, else fall back to rule-based copy. The display never blanks.

## Idempotence and the one rewrite

A stored line is returned without a model call. One exception: the job runs at 06:50,
before Health Auto Export's morning push, so a line authored then describes a day whose
cell is `health-delayed` (or `sleep-missing`, or `workout-without-hr` when the scored
workout arrives later). When a later run finds the described day's cell more complete
than it was at authoring, the line is rewritten once, with a single model attempt
(ruling: Ingrid, 2026-10-07). The cell at authoring is stored in `summary_lines.attempts_json`
(`{"source": "cell", ...}`, last entry), so the check needs no stored payload. The rewrite
is recorded there too (`{"source": "rewrite", "superseded": <old line>}`), which is what
bounds it to once per day; a rewrite the gate rejects keeps the old line and is not retried.
The superseded line is shown to the model as a recent line and sits in the similarity
window for the rewrite, so the new line is not a near copy of it.

## Quotes

A gated line that quotes the bank verbatim-by-words (case, spacing and punctuation aside)
is stored with the bank's exact text for the quotation, never the model's spelling of it.

## Lenses

Seven lenses rotate over the weekdays with a one-step shift per Monday-to-Sunday week:
every week sees all seven and no lens is pinned to a weekday. Because a lens therefore
returns every six days, the fallback borrows from the other lenses' pools before its
last-resort fact clause.
