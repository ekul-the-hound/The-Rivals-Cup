"""Leaders & laggards: the weekly candidate book for the manual long/short workflow.

Strategy (research only): in each of the five target sectors, find the strongest stock (the long
candidate) and, among that stock's direct competitors, the weakest (the short candidate). Five longs,
five shorts. You review the book and enter anything yourself in Trader View; nothing here places,
queues or records a trade.
"""

SECTOR_ETF = {
    "HEALTH_CARE": "XLV",
    "INDUSTRIALS": "XLI",
    "FINANCIALS": "XLF",
    "UTILITIES": "XLU",
    "REAL_ESTATE": "XLRE",
}
MARKET_BENCHMARK = "SPY"
BENCHMARK_TICKERS = [MARKET_BENCHMARK, *SECTOR_ETF.values()]
