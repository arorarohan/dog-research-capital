# Structural-alpha research checkpoint

Task: develop unusual, logically grounded prediction-market strategy hypotheses; no thorough
real-data validation or trading implementation requested. User requires a separate worktree
and branch, and documentation during work so interruption does not lose research.

- Worktree: `/tmp/drc-structural-alpha`
- Branch: `research/structural-alpha-20261023`
- Base commit: `e5f2458`
- Main artifact: [Twelve research bets](2026-10-23-structural-alpha.md)

## Completed

- Read repository research agenda, findings, lookahead post-mortem, data and execution
  constraints, learning conventions, schemas/loaders, research/evaluation, recorder discovery,
  and fill/fee code.
- Read-only data catalog inspected. No outcome holdout or new empirical strategy study run.
- Checked primary sources for fee assumptions, resolution mechanics, weather measurement,
  economic-data revisions, and nearby semantic/combinatorial prior art.
- Saved complete twelve-idea memo, qualitative priorities, equations, hypothetical examples,
  proposed trades, competing explanations, failure criteria, data gaps and bounded handoffs.
- Left existing main-checkout changes to `src/drc/research/studies.py` untouched.

## Review/checkpoint status

Draft and conceptual review complete. All twelve idea sections and local Markdown links
checked; numerical examples and nested-basket payoff identity verified. Added the martingale
constraint explicitly: predictable calendar changes are not automatically expected profit.

Initial sandbox test run: 91 passed; one websocket test could not bind localhost under sandbox
network restrictions. The checkpoint command reruns the suite outside that restriction and
chains tests, lint, format checks and diff checks before committing. A successful checkpoint
commit means those gates passed. Next: push the research branch if available. Do not push or merge main. Do not run the existing calibration
holdout. No live orders, messages to others, or data changes are part of this task.

## Resume without repeating research

Read the main artifact first. Highest-priority mechanisms are procedural gate count (1),
rule-disagreement payoff baskets (4), and measurement-channel weather probabilities (5),
with evidence visibility (2) supporting gate models. The remaining proposals are deliberately
ranked lower when data or identification is difficult. Do not turn this into an engineering
project: the deliverable is a rigorous idea portfolio for other execution agents.

Any future empirical work needs its own point-in-time enrollment and applicable fee records.
The current repo's general-market holdout remains reserved for its existing hypothesis.
