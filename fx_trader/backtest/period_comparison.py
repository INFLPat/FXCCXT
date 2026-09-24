"""
backtest/period_comparison.py

Runs ONE strategy/params/instrument combination once per Period (from
backtest/periods.py) and collects comparable metrics into one structured
result - the shared data layer behind visualize_period_comparison.py's
static and interactive rendering. Deliberately separate from rendering:
this module has no plotting/HTML code at all, so a future consumer (a
live dashboard, ROADMAP.md Section 5) can reuse the SAME comparison data
without importing matplotlib or any rendering dependency.
"""

from dataclasses import dataclass

from backtest.engine import BacktestEngine, CostModel
from backtest.metrics import Metrics, compute_metrics
from backtest.periods import Period
from data.store import FxStore
from strategy.base import Strategy


@dataclass
class PeriodComparisonRow:
    period: Period
    metrics: Metrics | None   # None if this period had no candles at all
    total_candles: int


@dataclass
class PeriodComparisonResult:
    strategy_name: str
    instrument: str
    params: dict
    rows: list[PeriodComparisonRow]

    def valid_rows(self) -> list[PeriodComparisonRow]:
        return [r for r in self.rows if r.metrics is not None]


def compare_periods(
    periods: list[Period], store: FxStore, instrument: str, granularity: str,
    strategy_factory: type[Strategy], params: dict, strategy_name: str,
    cost_model: CostModel, periods_per_year: int,
    starting_balance: float = 10_000.0, target_notional: float = 1_000.0,
) -> PeriodComparisonResult:
    """Runs a FRESH backtest per period (never carries state across
    periods - each period is judged entirely on its own data, matching
    the out-of-time validation philosophy of not letting information leak
    across period boundaries)."""
    assert periods, "compare_periods requires at least one period"
    assert target_notional > 0, "target_notional must be positive"

    rows: list[PeriodComparisonRow] = []
    for period in periods:
        candles = store.get_candles(instrument, granularity, start=period.start.isoformat(), end=period.end.isoformat())
        if not candles:
            rows.append(PeriodComparisonRow(period=period, metrics=None, total_candles=0))
            continue
        units_per_trade = target_notional / candles[0].mid_close
        strategy = strategy_factory(**params)
        engine = BacktestEngine(strategy, cost_model, starting_balance, units_per_trade)
        result = engine.run(candles)
        metrics = compute_metrics(result, periods_per_year=periods_per_year)
        rows.append(PeriodComparisonRow(period=period, metrics=metrics, total_candles=len(candles)))

    return PeriodComparisonResult(strategy_name=strategy_name, instrument=instrument, params=params, rows=rows)
