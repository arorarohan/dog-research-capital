# Snapshot and diff

Exchanges stream a book by sending one **snapshot** (the full book) and then **updates**
(diffs: "price X now has quantity Q", where Q = 0 means the level is gone). To know the
book at time t you apply every diff since the last snapshot in order.

Consequence: if we miss any message the reconstructed book is silently wrong. So the
recorder logs `connect`/`disconnect` events, and every new connection starts with a fresh
snapshot. Treat each `connect` as a hard reset; never bridge a gap.
