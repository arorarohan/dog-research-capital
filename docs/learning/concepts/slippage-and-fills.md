# Slippage and fill realism

**Slippage**: the gap between the price you expected and the price you got, mostly because
your order consumes several levels of the book or the book moved before you arrived.

Paper trading flatters because naive simulators fill at mid or at the best price for any
size. Ours must walk the book, charge fees, add latency, and assume our own orders have
impact. If a strategy only works under optimistic fills, it doesn't work.
