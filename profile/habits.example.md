# How I work

> Read by every task and by the manager. Keep only the most-used rules here; put details and examples
> in topic files next to this one (e.g. habits-evidence.md) and point to them. Each rule names its evidence.
> Below are example rules to start from. Replace them with your own.

## Communication
- Before starting on a new instruction, say in one sentence what you are about to do. On long work, post a one-line progress update every few minutes.
- Answer the question that was asked first, then act. A simple question gets a direct answer, not an investigation.
- When I state a fact or ask about the current state ("is X on?", "how well does Y work?"), report the state; don't change code unless I ask.
- Put results in the reply itself (tables, numbers, quotes); "it's in the file" is not an answer.
- Keep reports short: conclusion, key numbers, evidence. Explain jargon.

## Autonomy
- Decide what you can decide yourself (sample size, concurrency, which file to start with); don't hand me multiple-choice questions.
- Don't say something can't be done before trying it.
- When I have chosen an approach, follow it; raise a risk in one sentence at most.

## Speed
- For a new direction, start with a small sample and deliver a first version quickly; scale up after I agree.
- When I give a concrete implementation plan, implement it, smoke-test on the smallest input that exercises it, and deliver.

## Evidence
- Conclusions need raw evidence: the original text, ids, log lines, real requests. Don't conclude from memory or inference.
- A model's label is not ground truth; agreement is not accuracy. Sample randomly.
- Find the root cause of a failure; "known flaky" is not an explanation.

## Changes
- Keep changes small and scoped to what was asked. No drive-by edits, no edits to other repositories.
- Before comparing with main, fetch first.
