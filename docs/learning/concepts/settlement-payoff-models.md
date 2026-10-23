# Settlement payoff models

Status: new. Introduced in the structural-alpha research memo; not yet taught in a final reply.

A payoff function maps a complete state of the world, including the applicable resolution
rules, to the dollars a contract ultimately pays. Similar titles can have different payoff
functions because announcement, implementation, publication and certification are different
states. Fractional or exceptional settlements also belong in the state table.

A payoff basket is a collection of contracts whose payments add. If B logically implies A,
YES A plus NO B pays one dollar in every binary state, plus one extra dollar when A occurs
without B. This isolates the disagreement between the rules. It requires proof of the
implication under all relevant settlement states, not just similarity of the questions.

Capital occupation is the money unavailable for other opportunities while the position is
held. A basket costing $1.04 with expected payoff $1.12 has eight cents expected profit on
$1.04 of committed capital. Its return is not eight cents divided by the four cents above
its guaranteed payout. Delay and exceptional settlement states can change this assessment.

A pricing model may also compensate investors for risk or unavailable capital. Consequently,
a market price need not equal the physical probability estimated from an outcome model.
That difference alone is insufficient evidence of a forecasting mistake.

See [the research memo](../../research/2026-10-23-structural-alpha.md), especially ideas 4 and 7.
The current ledger supports settlement, but a complete settlement-aware simulation is still
listed as future work in the roadmap.
