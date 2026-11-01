# Profile

The profile is what learnMe learns about **you**. Two files are loaded into prompts every time:

| File | Who reads it | What it holds |
|---|---|---|
| `habits.md` | every task and the manager | how you work: communication, autonomy, speed, evidence, how changes ship |
| `judgment.md` | the manager only | when you stop, continue, or redirect a task, and how you decide |

On first start learnMe copies `habits.example.md` and `judgment.example.md` to `habits.md` and
`judgment.md` (git-ignored). Edit them freely; the manager also edits them when it wraps up a task
and finds a rule worth keeping. Every change, by you or by an agent, is recorded in the memory
panel and can be reverted.

Format rules that keep these files useful:

- **Actionable rules, not a diary.** "Report in two sentences: conclusion, then evidence" beats
  "the user seemed annoyed on Tuesday".
- **Each rule carries its evidence**: the date, and your own words or the decision that taught it,
  e.g. `(2026-03-02: "don't ask me which model, just pick one")`.
- **Keep the always-loaded part short.** `habits.md` is budgeted at about 2,500 characters and
  `judgment.md` at about 4,000. When one outgrows its budget, the manager is asked to split the
  details into topic files next to it (e.g. `habits-evidence.md`, `judgment-privacy.md`) and keep
  only the most-used rules plus an index in the loaded file. Nothing is deleted.
- **Your corrections win.** If you edit or revert something an agent wrote, the manager treats it
  as the strongest signal and adjusts related rules.
