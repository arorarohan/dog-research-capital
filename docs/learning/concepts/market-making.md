# Market making

Continuously quoting a bid and an ask, earning the spread when both sides fill, while
managing inventory risk (you end up holding a position that can move against you).

The real enemy is **adverse selection**: fills happen disproportionately when someone who
knows more trades against you. Simulating this correctly is hard, so paper market making
tends to look better than reality. We'll treat any market-making result with suspicion.
