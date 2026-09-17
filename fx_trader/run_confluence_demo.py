"""
run_confluence_demo.py

End-to-end demo: synthetic FX data -> RsiMacdConfluenceStrategy (RSI and
MACD fire independently, combined only on agreement within a confirmation
window) -> backtest engine -> metrics. Also prints standalone RsiStrategy
and MacdStrategy(require_rsi_confirmation=False) trade counts on the same
data, and the confluence strategy at a few window sizes, so the tradeoff
(fewer, more-agreed-upon trades as the window tightens) is visible
directly rather than assumed.

Run: python run_confluence_demo.py
"""

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import compute_metrics
from data.store import FxStore
from strategy.macd_strategy import MacdStrategy
from strategy.rsi_macd_confluence import RsiMacdConfluenceStrategy
from strategy.rsi_strategy import RsiStrategy
from tests.synthetic_data import generate_synthetic_candles


def _run(strategy, candles, cost_model):
    engine = BacktestEngine(strategy=strategy, cost_model=cost_model, starting_balance=10_000.0, units_per_trade=1_000.0)
    result = engine.run(candles)
    return result, compute_metrics(result, periods_per_year=252 * 24, candles=candles)


def main():
    instrument, granularity = "EUR_USD", "H1"

    print("Generating synthetic candle data...")
    candles = generate_synthetic_candles(instrument=instrument, granularity=granularity, n_candles=3000)
    store = FxStore()
    store.upsert_candles(candles)
    loaded = store.get_candles(instrument, granularity)

    cost_model = CostModel(commission_per_unit=0.00002, commission_flat=0.0, slippage_pips=1.0, pip_size=0.0001)

    print(f"\n{'strategy':<38} {'trades':>7} {'return':>9} {'sharpe':>8}")
    print("-" * 66)

    _, m = _run(RsiStrategy(), loaded, cost_model)
    print(f"{'RSI alone':<38} {m.total_trades:>7} {m.total_return_pct:>8.2f}% {m.sharpe_ratio or float('nan'):>8.2f}")

    _, m = _run(MacdStrategy(require_rsi_confirmation=False), loaded, cost_model)
    print(f"{'MACD alone':<38} {m.total_trades:>7} {m.total_return_pct:>8.2f}% {m.sharpe_ratio or float('nan'):>8.2f}")

    _, m = _run(MacdStrategy(require_rsi_confirmation=True), loaded, cost_model)
    print(f"{'MACD + RSI filter (primary/filter)':<38} {m.total_trades:>7} {m.total_return_pct:>8.2f}% {m.sharpe_ratio or float('nan'):>8.2f}")

    for window in (0, 3, 5, 10, 20):
        result, m = _run(RsiMacdConfluenceStrategy(confirmation_window=window), loaded, cost_model)
        label = f"Confluence (window={window})"
        print(f"{label:<38} {m.total_trades:>7} {m.total_return_pct:>8.2f}% {m.sharpe_ratio or float('nan'):>8.2f}")

    print("\n--- Detail: confluence, confirmation_window=5 ---")
    result, m = _run(RsiMacdConfluenceStrategy(confirmation_window=5), loaded, cost_model)
    print(f"Starting balance: {result.starting_balance:,.2f}")
    print(f"Ending balance:   {result.ending_balance:,.2f}")
    print()
    print(m.summary())


if __name__ == "__main__":
    main()
