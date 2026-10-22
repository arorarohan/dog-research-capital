# Dog Research Capital

Quant research and paper-trading stack for crypto and prediction markets.
See `CLAUDE.md` for project conventions.

## Quickstart
```
uv sync
uv run pytest
uv run drc-record --config config/recorder.toml
```

## Recorder
`drc-record` captures raw exchange messages (Coinbase, Kraken, Polymarket, Kalshi) into
`data/raw/<venue>/<channel>/<date>/<HH>.jsonl[.zst]`. Each line is `{"t": recv_ns, "m": <message>}`;
recorder events (`connect`, `disconnect`, `shutdown`) go to each venue's `_meta` channel and mark
gaps. Configure venues, markets, and retention in `config/recorder.toml`.
`--only kraken,polymarket` restricts which venues run.
