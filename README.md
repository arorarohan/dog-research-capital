# Dog Research Capital

Quant research and paper-trading stack for crypto and prediction markets.
See `CLAUDE.md` for project conventions and `docs/workflow.md` for how we work
(issues, branches, PRs). New here? Start with `docs/onboarding.md`.

Contributors: Rohan (`arorarohan`).

## Quickstart
```
uv sync
uv run pytest
uv run drc-record --config config/recorder.toml
# weather-bracket spread session (Kalshi KXHIGH* series, own data dir data/raw_weather):
uv run drc-record --config config/recorder-weather.toml
# forward-test session: Polymarket deadline universe + Kalshi weather brackets, own dir data/raw_forward:
uv run drc-record --config config/recorder-forward.toml
```

## Recorder
`drc-record` captures raw exchange messages (Coinbase, Kraken, Polymarket, Kalshi) into
`data/raw/<venue>/<channel>/<date>/<HH>.jsonl[.zst]`. Each line is `{"t": recv_ns, "m": <message>}`;
recorder events (`connect`, `disconnect`, `shutdown`) go to each venue's `_meta` channel and mark
gaps. Configure venues, markets, and retention in `config/recorder.toml`.
`--only kraken,polymarket` restricts which venues run.
