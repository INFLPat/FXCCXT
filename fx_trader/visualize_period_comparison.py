"""
visualize_period_comparison.py

Renders BOTH a static image (matplotlib PNG - cheap, default, bronze-tier
per backtest/service_tiers.py's convention) and an interactive chart
(self-contained HTML + Chart.js - gold-tier, richer, reusable pattern for
a future live dashboard per ROADMAP.md Section 5) from the SAME
underlying comparison data (backtest/period_comparison.py). Two views per
(strategy, instrument, params), matching the "long phases and short
phases" framing:

- SHORT PHASES: one bar-group per Period (calendar year for now - see
  backtest/periods.py for how to swap in quarters/custom/regime-labeled
  periods later without touching this script) - total_return_pct and
  max_drawdown_pct side by side, so period-to-period differences are
  visually obvious.
- LONG PHASES: one continuous equity curve + rolling Sharpe across the
  ENTIRE available history for the instrument, ignoring period
  boundaries - the "did this hold up steadily the whole time" view,
  reusing backtest/rolling.py rather than reimplementing it.

Currently only ONE Period exists (2025 H2, the only data loaded so far) -
the short-phase chart will show a single bar group until multi-year data
is loaded (ROADMAP.md Section 1 / fetch_sandbox_data.py). The mechanism
is what's being proven here, not an interesting comparison yet - re-run
this unchanged once data/sandbox_history.db exists for a real multi-year
view.

Run: python visualize_period_comparison.py
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no display needed - this generates files, never shows a window
import matplotlib.pyplot as plt

from backtest.engine import BacktestEngine, CostModel
from backtest.period_comparison import compare_periods
from backtest.periods import calendar_year_periods
from backtest.rolling import compute_rolling_metrics
from data.store import FxStore
from strategy.rsi_strategy import RsiStrategy

# Config - matches this repo's existing run_*.py convention (hardcoded at
# top, not argparse). Change these three lines to chart a different
# survivor; everything else is instrument/strategy-agnostic.
STRATEGY_NAME = "RsiStrategy"
STRATEGY_FACTORY = RsiStrategy
PARAMS = {"period": 14, "oversold": 30, "overbought": 75}
INSTRUMENT = "GBP_JPY"
GRANULARITY = "H1"
COST_MODEL = CostModel(commission_per_unit=0.00002, slippage_pips=1.0, pip_size=0.01)
PERIODS_PER_YEAR = 252 * 24

SANDBOX_DB = "sqlite:///data/sandbox_2025h2.db"  # swap to sandbox_history.db once it exists
OUTPUT_DIR = Path("charts")
ROLLING_WINDOW = 500


def render_short_phase_static(comparison, output_path: Path) -> None:
    """One PNG: bar-group per period, return% and drawdown% side by side."""
    valid = comparison.valid_rows()
    labels = [r.period.label for r in valid]
    returns = [r.metrics.total_return_pct for r in valid]
    drawdowns = [r.metrics.max_drawdown_pct for r in valid]

    fig, ax1 = plt.subplots(figsize=(max(6, len(labels) * 1.2), 5))
    x = range(len(labels))
    width = 0.35
    ax1.bar([i - width / 2 for i in x], returns, width, label="Total return %", color="#2a6f97")
    ax1.set_ylabel("Total return %")
    ax1.axhline(0, color="black", linewidth=0.5)

    ax2 = ax1.twinx()
    ax2.bar([i + width / 2 for i in x], drawdowns, width, label="Max drawdown %", color="#c1121f", alpha=0.7)
    ax2.set_ylabel("Max drawdown %")

    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels)
    ax1.set_title(f"{comparison.strategy_name} / {comparison.instrument} — period comparison\n{comparison.params}")
    fig.legend(loc="upper right", bbox_to_anchor=(0.9, 0.88))
    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def render_long_phase_static(store: FxStore, comparison, output_path: Path) -> None:
    """One PNG: full-history equity curve + rolling Sharpe, ignoring
    period boundaries entirely - the "steady the whole way through, or
    one good patch carrying an otherwise flat line" view."""
    candles = store.get_candles(comparison.instrument, GRANULARITY)
    units_per_trade = 1_000.0 / candles[0].mid_close
    strategy = STRATEGY_FACTORY(**comparison.params)
    engine = BacktestEngine(strategy, COST_MODEL, 10_000.0, units_per_trade)
    result = engine.run(candles)

    equity = [e for _, e in result.equity_curve]
    window = min(ROLLING_WINDOW, len(result.equity_curve))
    rolling = compute_rolling_metrics(result.equity_curve, window=window, periods_per_year=PERIODS_PER_YEAR) if window >= 3 else None

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax1.plot(range(len(equity)), equity, color="#2a6f97")
    ax1.set_ylabel("Equity")
    ax1.set_title(f"{comparison.strategy_name} / {comparison.instrument} — full-history equity curve\n{comparison.params}")

    if rolling is not None:
        ax2.plot(range(len(rolling.rolling_sharpe)), [v if v is not None else float("nan") for v in rolling.rolling_sharpe], color="#588157")
    ax2.set_ylabel(f"Rolling Sharpe (window={window})")
    ax2.set_xlabel("Candle index")
    ax2.axhline(0, color="black", linewidth=0.5)

    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def render_interactive_html(comparison, store: FxStore, output_path: Path) -> None:
    """One self-contained HTML file (Chart.js via CDN) with BOTH views as
    toggleable tabs - gold-tier per service_tiers.py's convention (see
    SUBTASK_VISUALIZATION.md), same underlying data as the two PNGs."""
    valid = comparison.valid_rows()
    period_labels = [r.period.label for r in valid]
    period_returns = [r.metrics.total_return_pct for r in valid]
    period_drawdowns = [r.metrics.max_drawdown_pct for r in valid]

    candles = store.get_candles(comparison.instrument, GRANULARITY)
    units_per_trade = 1_000.0 / candles[0].mid_close
    strategy = STRATEGY_FACTORY(**comparison.params)
    engine = BacktestEngine(strategy, COST_MODEL, 10_000.0, units_per_trade)
    result = engine.run(candles)
    equity = [e for _, e in result.equity_curve]
    # Downsample the equity curve for the browser if it's large - no point
    # shipping tens of thousands of points to render a line chart.
    stride = max(1, len(equity) // 2000)
    equity_sampled = equity[::stride]

    data = {
        "period_labels": period_labels, "period_returns": period_returns, "period_drawdowns": period_drawdowns,
        "equity_curve": equity_sampled,
        "title": f"{comparison.strategy_name} / {comparison.instrument} ({comparison.params})",
    }

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{data['title']} - period comparison</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 1000px; margin: 2rem auto; padding: 0 1rem; }}
  .tabs {{ display: flex; gap: 0.5rem; margin-bottom: 1rem; }}
  .tab {{ padding: 0.5rem 1rem; border: 1px solid #ccc; border-radius: 6px; cursor: pointer; background: #f5f5f5; }}
  .tab.active {{ background: #2a6f97; color: white; }}
  .view {{ display: none; }}
  .view.active {{ display: block; }}
  canvas {{ max-height: 420px; }}
  h1 {{ font-size: 1.1rem; }}
</style>
</head>
<body>
<h1>{data['title']}</h1>
<div class="tabs">
  <div class="tab active" onclick="showView('short')">Short phases (per-period)</div>
  <div class="tab" onclick="showView('long')">Long phase (full history)</div>
</div>
<div id="short" class="view active"><canvas id="shortChart"></canvas></div>
<div id="long" class="view"><canvas id="longChart"></canvas></div>
<script>
const DATA = {json.dumps(data)};

function showView(name) {{
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(name).classList.add('active');
  event.target.classList.add('active');
}}

new Chart(document.getElementById('shortChart'), {{
  type: 'bar',
  data: {{
    labels: DATA.period_labels,
    datasets: [
      {{ label: 'Total return %', data: DATA.period_returns, backgroundColor: '#2a6f97' }},
      {{ label: 'Max drawdown %', data: DATA.period_drawdowns, backgroundColor: '#c1121f' }}
    ]
  }},
  options: {{ responsive: true, plugins: {{ title: {{ display: true, text: 'Period-by-period comparison' }} }} }}
}});

new Chart(document.getElementById('longChart'), {{
  type: 'line',
  data: {{
    labels: DATA.equity_curve.map((_, i) => i),
    datasets: [{{ label: 'Equity', data: DATA.equity_curve, borderColor: '#2a6f97', pointRadius: 0 }}]
  }},
  options: {{ responsive: true, plugins: {{ title: {{ display: true, text: 'Full-history equity curve' }} }} }}
}});
</script>
</body>
</html>"""
    output_path.write_text(html)


def main():
    store = FxStore(database_url=SANDBOX_DB)
    OUTPUT_DIR.mkdir(exist_ok=True)

    periods = calendar_year_periods(store, INSTRUMENT, GRANULARITY)
    print(f"{len(periods)} calendar-year period(s) found for {INSTRUMENT}: {[p.label for p in periods]}")
    if len(periods) < 2:
        print("Only one period available (single-window sandbox) - the short-phase chart will show one bar group.")
        print("Re-run once data/sandbox_history.db (multi-year) exists for a real comparison.")

    comparison = compare_periods(
        periods, store, INSTRUMENT, GRANULARITY, STRATEGY_FACTORY, PARAMS, STRATEGY_NAME,
        COST_MODEL, PERIODS_PER_YEAR,
    )

    prefix = f"{STRATEGY_NAME}_{INSTRUMENT.replace('/', '-')}"
    render_short_phase_static(comparison, OUTPUT_DIR / f"{prefix}_period_comparison.png")
    render_long_phase_static(store, comparison, OUTPUT_DIR / f"{prefix}_long_run.png")
    render_interactive_html(comparison, store, OUTPUT_DIR / f"{prefix}_interactive.html")

    print(f"\nWritten to {OUTPUT_DIR}/:")
    for f in sorted(OUTPUT_DIR.glob(f"{prefix}*")):
        print(f"  {f}")


if __name__ == "__main__":
    main()
