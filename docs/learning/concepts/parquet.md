# Parquet (and columnar storage)

A file format for tables. Unlike a CSV or a row-oriented database, it stores each *column*
contiguously, typed and compressed. Analytical queries touch a few columns across millions of
rows, so this is much faster and smaller than row storage. No server: it's just files.

Not SQL: SQL is a query language, Parquet is a storage format. You can run SQL over Parquet
with DuckDB, or use a dataframe library (Polars) as we do.

In this repo: raw recordings are JSONL (verbatim, easy to debug); normalized tables
(trades, book events, bars) are Parquet under `data/norm/`.
