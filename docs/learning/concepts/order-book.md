# Order book

An exchange keeps two sorted lists of standing orders: **bids** (offers to buy, highest
first) and **asks** (offers to sell, lowest first). Each entry is a price and a quantity.
A trade happens when a buyer and seller agree on a price, usually when a new order matches
a standing one.

Analogy: a live, two-sided auction whose current state is a pair of histograms over price.
Prices move because this state changes, so recording the book (not just trades) is how we
study *why* prices move.

In this repo: the recorder stores book messages from every venue verbatim.
