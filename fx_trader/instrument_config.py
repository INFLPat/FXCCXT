"""
instrument_config.py

Single source of truth for the 16-instrument sandbox universe, cost
models, and sizing constants - previously duplicated verbatim across
run_full_sweep.py, run_validation_hierarchy_real_data.py (full list),
and run_out_of_time_validation.py / visualize_period_comparison.py
(subsets). A value changed here is changed everywhere; a value that
silently drifted between copies (the original bug sandbox_config.py was
built to prevent for the dataset window) can't happen for cost models or
instrument lists either, going forward.

Add a new instrument here once, not once per consuming script.
"""

from backtest.engine import CostModel

TARGET_NOTIONAL = 1_000.0     # 10% of STARTING_BALANCE, per instrument
STARTING_BALANCE = 10_000.0
WINDOW_SIZES = (1000, 300)    # (in_sample_size, out_of_sample_size) for walk-forward

FX_COST_MODEL = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.0001)
FX_COST_MODEL_JPY = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.01)
CRYPTO_COST_MODEL = CostModel(commission_pct=0.001, slippage_pct=0.0005)

FX_PPY = 252 * 24
CRYPTO_PPY = 365 * 24

# (instrument, granularity, cost_model, periods_per_year) - the 16-instrument
# sandbox universe. UK-based business: GBP/USD-leaning FX majors + crosses,
# EUR represented; same 4 crypto assets on both USD and GBP quote currencies.
INSTRUMENTS = [
    ("GBP_USD", "H1", FX_COST_MODEL, FX_PPY),
    ("EUR_GBP", "H1", FX_COST_MODEL, FX_PPY),
    ("GBP_JPY", "H1", FX_COST_MODEL_JPY, FX_PPY),
    ("GBP_CHF", "H1", FX_COST_MODEL, FX_PPY),
    ("EUR_USD", "H1", FX_COST_MODEL, FX_PPY),
    ("USD_JPY", "H1", FX_COST_MODEL_JPY, FX_PPY),
    ("USD_CHF", "H1", FX_COST_MODEL, FX_PPY),
    ("USD_CAD", "H1", FX_COST_MODEL, FX_PPY),
    ("BTC/USDT", "1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
    ("ETH/USDT", "1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
    ("XRP/USDT", "1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
    ("LTC/USDT", "1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
    ("BTC/GBP", "1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
    ("ETH/GBP", "1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
    ("XRP/GBP", "1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
    ("LTC/GBP", "1h", CRYPTO_COST_MODEL, CRYPTO_PPY),
]
assert len(INSTRUMENTS) == 16, "expected exactly the 16 sandbox instruments"

# instrument -> (granularity, cost_model, periods_per_year) - same data,
# dict-keyed shape for scripts that look up one instrument at a time
# rather than iterating the full list (e.g. run_out_of_time_validation.py).
INSTRUMENT_INFO = {name: (gran, cost, ppy) for name, gran, cost, ppy in INSTRUMENTS}
