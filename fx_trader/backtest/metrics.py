"""
backtest/metrics.py

Standard + extended performance/risk metrics computed from a BacktestResult.

EXTENDED THIS SESSION. Everything from the original file (total_return_pct,
total_trades, win_rate_pct, profit_factor, max_drawdown_pct, sharpe_ratio,
total_commission_paid, avg_win, avg_loss) is computed identically to before
- same formulas, same edge-case handling (None where undefined). Nothing
existing changed behavior; only new fields were added.

TWO DIFFERENT DENOMINATORS ARE USED ON PURPOSE:
- Sharpe, Sortino, Omega are computed from PERIOD RETURNS (the equity curve,
  point to point) - consistent with the existing Sharpe implementation.
- VaR/CVaR/tail_ratio/skewness/kurtosis are computed from the TRADE P&L
  DISTRIBUTION (one value per closed trade) - more meaningful for a
  strategy that only takes a handful of trades a year, where the period-
  return series is mostly flat zeros between trades.
Each docstring below says which one it uses - don't assume.

COST-TIER: every metric here is free relative to the backtest that already
ran (no extra BacktestEngine.run() calls) - this whole module stays inside
Tier 0/1 of VALIDATION_HIERARCHY.md. Rolling metrics (backtest/rolling.py)
and portfolio metrics (backtest/portfolio.py) are kept in separate modules
specifically because they are NOT this cheap - see those files' docstrings.

SERVICE-TIER: which of these fields a bronze/silver/gold subscriber sees is
NOT decided here - see backtest/service_tiers.py. That's a deliberate
separation: cost-tier (when something is computed) and service-tier (who's
allowed to see it) are independent axes and will keep changing independently
as the subscription product gets designed.
"""

import math
from dataclasses import dataclass, fields
from datetime import datetime

from backtest.engine import BacktestResult, Trade
from backtest.service_tiers import ServiceTier, metrics_for_tier
from data.store import Candle


@dataclass
class Metrics:
    # --- Original fields (unchanged formulas) ---
    total_return_pct: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float | None
    max_drawdown_pct: float
    sharpe_ratio: float | None
    total_commission_paid: float
    avg_win: float
    avg_loss: float

    # --- Risk-adjusted return ratios ---
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    omega_ratio: float | None = None
    recovery_factor: float | None = None

    # --- Drawdown shape ---
    max_drawdown_duration: int = 0     # periods (candles), not wall-clock time
    avg_drawdown_duration: float = 0.0  # periods, mean of completed+ongoing underwater streaks
    still_in_drawdown_at_end: bool = False
    ulcer_index: float | None = None
    downside_deviation: float | None = None  # per-period, not annualized

    # --- Per-trade economics ---
    trade_expectancy: float | None = None       # account-currency P&L per trade
    trade_expectancy_pct: float | None = None    # as % of starting_balance
    payoff_ratio: float | None = None            # avg_win / |avg_loss|
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_trade_duration_hours: float | None = None
    cost_drag_pct: float | None = None            # commission as % of gross (pre-cost) P&L

    # --- Tail / distribution risk (trade-P&L based) ---
    var_95: float | None = None    # positive = loss magnitude, account currency
    cvar_95: float | None = None   # positive = loss magnitude, account currency
    tail_ratio: float | None = None
    skewness: float | None = None
    kurtosis: float | None = None  # excess kurtosis (0 = normal-like tails)

    # --- Position sizing (informational - NOT a leverage recommendation) ---
    kelly_fraction: float | None = None

    # --- Capital efficiency ---
    exposure_pct: float | None = None  # % of tested periods with a position open

    # --- Benchmark (only populated if compute_metrics() was given `candles`) ---
    buy_hold_return_pct: float | None = None
    alpha_vs_buy_hold_pct: float | None = None

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def for_service_tier(self, tier: ServiceTier) -> dict:
        """Every field is always computed (it's cheap - see module
        docstring); this is a display/entitlement filter only, not a
        recomputation. See backtest/service_tiers.py for the registry this
        reads from and why it's kept separate from cost-tier."""
        return metrics_for_tier(self.as_dict(), tier)

    def summary(self) -> str:
        pf = f"{self.profit_factor:.2f}" if self.profit_factor is not None else "n/a (no losing trades)"
        sharpe = f"{self.sharpe_ratio:.2f}" if self.sharpe_ratio is not None else "n/a"
        sortino = f"{self.sortino_ratio:.2f}" if self.sortino_ratio is not None else "n/a"
        calmar = f"{self.calmar_ratio:.2f}" if self.calmar_ratio is not None else "n/a"
        omega = f"{self.omega_ratio:.2f}" if self.omega_ratio is not None else "n/a"
        recovery = f"{self.recovery_factor:.2f}" if self.recovery_factor is not None else "n/a"
        expectancy = f"{self.trade_expectancy:+.2f} ({self.trade_expectancy_pct:+.3f}%)" if self.trade_expectancy is not None else "n/a"
        payoff = f"{self.payoff_ratio:.2f}" if self.payoff_ratio is not None else "n/a"
        kelly = f"{self.kelly_fraction:+.3f}" if self.kelly_fraction is not None else "n/a"
        var95 = f"{self.var_95:.2f}" if self.var_95 is not None else "n/a"
        cvar95 = f"{self.cvar_95:.2f}" if self.cvar_95 is not None else "n/a"
        tail = f"{self.tail_ratio:.2f}" if self.tail_ratio is not None else "n/a"
        skew = f"{self.skewness:+.2f}" if self.skewness is not None else "n/a"
        kurt = f"{self.kurtosis:+.2f}" if self.kurtosis is not None else "n/a"
        exposure = f"{self.exposure_pct:.1f}%" if self.exposure_pct is not None else "n/a"
        avg_dur = f"{self.avg_trade_duration_hours:.1f}h" if self.avg_trade_duration_hours is not None else "n/a"
        cost_drag = f"{self.cost_drag_pct:.1f}%" if self.cost_drag_pct is not None else "n/a"
        buy_hold = f"{self.buy_hold_return_pct:+.2f}%" if self.buy_hold_return_pct is not None else "n/a"
        alpha = f"{self.alpha_vs_buy_hold_pct:+.2f}%" if self.alpha_vs_buy_hold_pct is not None else "n/a"
        ulcer = f"{self.ulcer_index:.2f}" if self.ulcer_index is not None else "n/a"

        return (
            f"Total return:       {self.total_return_pct:+.2f}%\n"
            f"Total trades:       {self.total_trades}\n"
            f"Win rate:           {self.win_rate_pct:.1f}%\n"
            f"Profit factor:      {pf}\n"
            f"Max drawdown:       {self.max_drawdown_pct:.2f}%  "
            f"(longest {self.max_drawdown_duration} periods, avg {self.avg_drawdown_duration:.1f}"
            f"{', still underwater at end' if self.still_in_drawdown_at_end else ''})\n"
            f"Sharpe / Sortino:   {sharpe} / {sortino}\n"
            f"Calmar / Recovery:  {calmar} / {recovery}\n"
            f"Omega ratio:        {omega}\n"
            f"Ulcer index:        {ulcer}\n"
            f"Expectancy/trade:   {expectancy}\n"
            f"Payoff ratio:       {payoff}\n"
            f"Consecutive W/L:    {self.max_consecutive_wins} / {self.max_consecutive_losses}\n"
            f"Avg trade duration: {avg_dur}\n"
            f"Cost drag:          {cost_drag}\n"
            f"VaR95 / CVaR95:     {var95} / {cvar95}  (loss magnitude, account ccy)\n"
            f"Tail ratio:         {tail}\n"
            f"Skew / Kurtosis:    {skew} / {kurt}\n"
            f"Kelly fraction:     {kelly}  (informational only - see docstring caveats)\n"
            f"Exposure:           {exposure}\n"
            f"Total commission:   {self.total_commission_paid:.2f}\n"
            f"Avg win / avg loss: {self.avg_win:.2f} / {self.avg_loss:.2f}\n"
            f"Buy & hold / alpha: {buy_hold} / {alpha}"
        )


def compute_metrics(
    result: BacktestResult, periods_per_year: int = 252, candles: list[Candle] | None = None,
) -> Metrics:
    """
    candles: optional - the SAME candle list the backtest was run on. Only
    used for buy_hold_return_pct/alpha_vs_buy_hold_pct. Omit it and those
    two fields come back None; every other field is unaffected.
    """
    assert result is not None, "compute_metrics requires a BacktestResult"
    assert periods_per_year > 0, "periods_per_year must be positive"
    closed = result.closed_trades
    total_trades = len(closed)

    total_return_pct = (
        (result.ending_balance - result.starting_balance) / result.starting_balance * 100
        if result.starting_balance else 0.0
    )

    wins = [t.realized_pnl() for t in closed if t.realized_pnl() > 0]
    losses = [t.realized_pnl() for t in closed if t.realized_pnl() <= 0]
    win_rate_pct = (len(wins) / total_trades * 100) if total_trades else 0.0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    avg_win = (gross_profit / len(wins)) if wins else 0.0
    avg_loss = (-gross_loss / len(losses)) if losses else 0.0

    total_commission_paid = sum(t.commission_paid for t in result.trades)

    dd_series = _drawdown_pct_series(result.equity_curve)
    max_drawdown_pct = max(dd_series) if dd_series else 0.0
    max_dd_dur, avg_dd_dur, still_in_dd = _drawdown_durations(dd_series)
    ulcer = _ulcer_index(dd_series)

    sharpe = _sharpe_ratio(result.equity_curve, periods_per_year)
    returns = _period_returns(result.equity_curve)
    downside_dev = _downside_deviation(returns)
    sortino = _sortino_ratio(returns, downside_dev, periods_per_year)
    omega = _omega_ratio(returns)
    calmar = _calmar_ratio(result, max_drawdown_pct, periods_per_year)
    recovery = _recovery_factor(total_return_pct, max_drawdown_pct)

    win_rate_frac = win_rate_pct / 100
    expectancy = win_rate_frac * avg_win + (1 - win_rate_frac) * avg_loss if total_trades else None
    expectancy_pct = (expectancy / result.starting_balance * 100) if (expectancy is not None and result.starting_balance) else None
    payoff_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else None
    max_win_streak, max_loss_streak = _consecutive_streaks(closed)
    avg_duration_hours = _avg_trade_duration_hours(closed)
    cost_drag = _cost_drag_pct(closed, total_commission_paid)

    var_95, cvar_95 = _trade_var_cvar_95(closed)
    tail_ratio = _tail_ratio(closed)
    skew, kurt = _skew_kurtosis(closed)

    kelly = _kelly_fraction(win_rate_pct, payoff_ratio)
    exposure_pct = (
        result.periods_in_market / len(result.equity_curve) * 100
        if result.equity_curve else None
    )

    buy_hold = _buy_hold_return_pct(candles) if candles else None
    alpha = (total_return_pct - buy_hold) if buy_hold is not None else None

    return Metrics(
        total_return_pct=total_return_pct, total_trades=total_trades, win_rate_pct=win_rate_pct,
        profit_factor=profit_factor, max_drawdown_pct=max_drawdown_pct, sharpe_ratio=sharpe,
        total_commission_paid=total_commission_paid, avg_win=avg_win, avg_loss=avg_loss,
        sortino_ratio=sortino, calmar_ratio=calmar, omega_ratio=omega, recovery_factor=recovery,
        max_drawdown_duration=max_dd_dur, avg_drawdown_duration=avg_dd_dur,
        still_in_drawdown_at_end=still_in_dd, ulcer_index=ulcer, downside_deviation=downside_dev,
        trade_expectancy=expectancy, trade_expectancy_pct=expectancy_pct, payoff_ratio=payoff_ratio,
        max_consecutive_wins=max_win_streak, max_consecutive_losses=max_loss_streak,
        avg_trade_duration_hours=avg_duration_hours, cost_drag_pct=cost_drag,
        var_95=var_95, cvar_95=cvar_95, tail_ratio=tail_ratio, skewness=skew, kurtosis=kurt,
        kelly_fraction=kelly, exposure_pct=exposure_pct,
        buy_hold_return_pct=buy_hold, alpha_vs_buy_hold_pct=alpha,
    )


# ---------------------------------------------------------------------------
# Drawdown-series helpers (shared by max_drawdown_pct, duration, Ulcer Index)
# ---------------------------------------------------------------------------

def _drawdown_pct_series(equity_curve: list[tuple[str, float]]) -> list[float]:
    """One drawdown% per equity_curve point: how far below the running
    peak-to-date that point sits. Computed once and reused by every
    drawdown-shaped metric below, instead of each one re-walking the peak."""
    if not equity_curve:
        return []
    peak = equity_curve[0][1]
    series = []
    for _, equity in equity_curve:
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        series.append(dd)
    return series


def _drawdown_durations(dd_series: list[float]) -> tuple[int, float, bool]:
    """(max_duration, avg_duration, still_in_drawdown_at_end). Duration is
    counted in PERIODS (candles), not wall-clock time - candle spacing
    isn't guaranteed uniform across every instrument in this project
    (Kraken only records hours with an actual trade)."""
    assert dd_series is not None, "_drawdown_durations requires a dd_series (possibly empty)"
    durations = []
    current = 0
    for dd in dd_series:
        if dd > 0:
            current += 1
        else:
            if current > 0:
                durations.append(current)
            current = 0
    still_in_drawdown = current > 0
    if still_in_drawdown:
        durations.append(current)
    if not durations:
        return 0, 0.0, False
    return max(durations), sum(durations) / len(durations), still_in_drawdown


def _ulcer_index(dd_series: list[float]) -> float | None:
    """RMS of the drawdown% series - penalizes deep AND long drawdowns
    together, unlike max_drawdown_pct which only sees the single worst
    point regardless of how long the strategy stayed underwater."""
    if not dd_series:
        return None
    return math.sqrt(sum(dd ** 2 for dd in dd_series) / len(dd_series))


# ---------------------------------------------------------------------------
# Period-return helpers (Sharpe, Sortino, Omega - all from the equity curve)
# ---------------------------------------------------------------------------

def _period_returns(equity_curve: list[tuple[str, float]]) -> list[float]:
    """One simple return per consecutive equity_curve pair. Points where
    the prior equity was exactly 0 are skipped (return undefined)."""
    if len(equity_curve) < 2:
        return []
    values = [e for _, e in equity_curve]
    return [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]


def _sharpe_ratio(equity_curve: list[tuple[str, float]], periods_per_year: int) -> float | None:
    if len(equity_curve) < 3:
        return None
    returns = _period_returns(equity_curve)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return (mean / std) * math.sqrt(periods_per_year)


def _downside_deviation(returns: list[float], mar: float = 0.0) -> float | None:
    """Per-period (NOT annualized) semi-deviation: RMS of returns below
    `mar`, dividing by the full period count (standard convention) rather
    than just the count of losing periods - so a strategy that's almost
    always flat/positive correctly shows near-zero downside risk."""
    if not returns:
        return None
    downside_sq = sum(min(r - mar, 0) ** 2 for r in returns)
    return math.sqrt(downside_sq / len(returns))


def _sortino_ratio(
    returns: list[float], downside_deviation: float | None, periods_per_year: int, mar: float = 0.0,
) -> float | None:
    """Sharpe's denominator swapped for downside deviation: upside
    volatility (which no trader minds) stops being penalized."""
    if len(returns) < 2 or downside_deviation is None or downside_deviation == 0:
        return None
    mean = sum(returns) / len(returns)
    return ((mean - mar) / downside_deviation) * math.sqrt(periods_per_year)


def _omega_ratio(returns: list[float], threshold: float = 0.0) -> float | None:
    """Probability-weighted ratio of gains to losses relative to
    `threshold` (default 0), over period returns. Makes no assumption the
    return distribution is symmetric/normal, unlike Sharpe/Sortino."""
    if not returns:
        return None
    gains = sum(r - threshold for r in returns if r > threshold)
    losses = sum(threshold - r for r in returns if r < threshold)
    if losses == 0:
        return None
    return gains / losses


def _calmar_ratio(result: BacktestResult, max_drawdown_pct: float, periods_per_year: int) -> float | None:
    """Annualized return / max drawdown. Annualization uses CAGR over the
    actual tested span (n_periods / periods_per_year years), NOT the
    conventional trailing-36-month window - this project's backtests are
    usually shorter than 3 years, so a fixed 36-month lookback would be
    meaningless here."""
    n_periods = len(result.equity_curve)
    if n_periods < 2 or periods_per_year <= 0 or max_drawdown_pct == 0:
        return None
    years = (n_periods - 1) / periods_per_year
    if years <= 0 or result.starting_balance <= 0:
        return None
    growth = result.ending_balance / result.starting_balance
    if growth <= 0:
        return None  # total account wipeout - CAGR undefined for a non-positive base
    annualized_return_pct = (growth ** (1 / years) - 1) * 100
    return annualized_return_pct / max_drawdown_pct


def _recovery_factor(total_return_pct: float, max_drawdown_pct: float) -> float | None:
    """Like Calmar but NOT annualized - total return actually achieved
    over the tested period, per unit of max drawdown suffered getting
    there. Useful alongside Calmar precisely because it doesn't assume the
    tested period is representative of a full year."""
    if max_drawdown_pct == 0:
        return None
    return total_return_pct / max_drawdown_pct


# ---------------------------------------------------------------------------
# Trade-P&L-distribution helpers (VaR, CVaR, tail ratio, skew, kurtosis)
# ---------------------------------------------------------------------------

def _percentile(sorted_values: list[float], p: float) -> float | None:
    """Dependency-free percentile via sorted interpolation. `sorted_values`
    must already be sorted ascending."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (p / 100) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (rank - lo)


def _trade_var_cvar_95(closed_trades: list[Trade]) -> tuple[float | None, float | None]:
    """Historical (non-parametric) VaR/CVaR at the 95% level, from the
    trade P&L distribution. Both returned as POSITIVE account-currency
    magnitudes (the conventional risk-reporting sign convention) - a
    var_95 of 42.00 means "5% of trades lost more than 42.00", not -42.00."""
    if len(closed_trades) < 2:
        return None, None
    pnls = sorted(t.realized_pnl() for t in closed_trades)
    threshold = _percentile(pnls, 5)
    var_95 = max(-threshold, 0.0)
    tail = [p for p in pnls if p <= threshold]
    cvar_95 = max(-(sum(tail) / len(tail)), 0.0) if tail else var_95
    return var_95, cvar_95


def _tail_ratio(closed_trades: list[Trade]) -> float | None:
    """abs(95th pct trade P&L) / abs(5th pct trade P&L). >1 means the best
    trades are bigger than the worst trades are bad."""
    if len(closed_trades) < 2:
        return None
    pnls = sorted(t.realized_pnl() for t in closed_trades)
    p95 = _percentile(pnls, 95)
    p5 = _percentile(pnls, 5)
    if p5 == 0:
        return None
    return abs(p95) / abs(p5)


def _skew_kurtosis(closed_trades: list[Trade]) -> tuple[float | None, float | None]:
    """Population (not bias-corrected) skewness and EXCESS kurtosis (kurtosis
    - 3, so 0 = normal-like tails) of the trade P&L distribution. Bias
    correction is skipped deliberately - trade counts here are typically in
    the tens, where a correction term adds false precision, not accuracy."""
    if len(closed_trades) < 3:
        return None, None
    pnls = [t.realized_pnl() for t in closed_trades]
    n = len(pnls)
    mean = sum(pnls) / n
    variance = sum((p - mean) ** 2 for p in pnls) / n
    std = math.sqrt(variance)
    if std == 0:
        return None, None
    skew = (sum((p - mean) ** 3 for p in pnls) / n) / (std ** 3)
    kurtosis = (sum((p - mean) ** 4 for p in pnls) / n) / (std ** 4) - 3.0
    return skew, kurtosis


# ---------------------------------------------------------------------------
# Per-trade economics
# ---------------------------------------------------------------------------

def _consecutive_streaks(closed_trades: list[Trade]) -> tuple[int, int]:
    """(max_consecutive_wins, max_consecutive_losses), walking trades in
    the order they closed."""
    max_win_streak = cur_win = 0
    max_loss_streak = cur_loss = 0
    for t in closed_trades:
        if t.realized_pnl() > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win_streak = max(max_win_streak, cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)
    return max_win_streak, max_loss_streak


def _avg_trade_duration_hours(closed_trades: list[Trade]) -> float | None:
    """Mean wall-clock hold time across closed trades, parsed from each
    trade's own entry_time/exit_time timestamps (not periods_per_year) -
    correct even when instruments have different granularities."""
    if not closed_trades:
        return None
    durations = []
    for t in closed_trades:
        if not t.entry_time or not t.exit_time:
            continue
        try:
            entry = datetime.fromisoformat(t.entry_time.replace("Z", "+00:00"))
            exit_ = datetime.fromisoformat(t.exit_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        durations.append((exit_ - entry).total_seconds() / 3600)
    if not durations:
        return None
    return sum(durations) / len(durations)


def _cost_drag_pct(closed_trades: list[Trade], total_commission_paid: float) -> float | None:
    """Commission as a % of GROSS (pre-commission) P&L - directly answers
    "how much of whatever edge this strategy has is being eaten by costs",
    separate from whether the strategy is net profitable at all."""
    if not closed_trades:
        return None
    gross_pnl_total = sum(t.realized_pnl() + t.commission_paid for t in closed_trades)
    if gross_pnl_total == 0:
        return None
    return total_commission_paid / abs(gross_pnl_total) * 100


def _kelly_fraction(win_rate_pct: float, payoff_ratio: float | None) -> float | None:
    """f* = W - (1-W)/R. CAVEATS (deliberately not suppressed, just
    flagged): assumes win_rate/payoff_ratio are stationary going forward,
    which a few dozen historical trades cannot establish; a negative
    result means negative edge, not "bet negatively"; this is NOT a
    leverage recommendation on its own."""
    if payoff_ratio is None or payoff_ratio == 0:
        return None
    w = win_rate_pct / 100
    return w - (1 - w) / payoff_ratio


def _buy_hold_return_pct(candles: list[Candle]) -> float | None:
    if not candles or len(candles) < 2:
        return None
    start_price = candles[0].mid_close
    end_price = candles[-1].mid_close
    if start_price == 0:
        return None
    return (end_price - start_price) / start_price * 100
        )


def compute_metrics(result: BacktestResult, periods_per_year: int = 252) -> Metrics:
    assert result is not None, "compute_metrics requires a BacktestResult"
    assert periods_per_year > 0, "periods_per_year must be positive"
    closed = result.closed_trades
    total_trades = len(closed)

    total_return_pct = (
        (result.ending_balance - result.starting_balance) / result.starting_balance * 100
        if result.starting_balance
        else 0.0
    )

    wins = [t.realized_pnl() for t in closed if t.realized_pnl() > 0]
    losses = [t.realized_pnl() for t in closed if t.realized_pnl() <= 0]
    win_rate_pct = (len(wins) / total_trades * 100) if total_trades else 0.0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    avg_win = (gross_profit / len(wins)) if wins else 0.0
    avg_loss = (-gross_loss / len(losses)) if losses else 0.0

    total_commission_paid = sum(t.commission_paid for t in result.trades)

    max_drawdown_pct = _max_drawdown_pct(result.equity_curve)
    sharpe = _sharpe_ratio(result.equity_curve, periods_per_year)

    return Metrics(
        total_return_pct=total_return_pct,
        total_trades=total_trades,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe,
        total_commission_paid=total_commission_paid,
        avg_win=avg_win,
        avg_loss=avg_loss,
    )


def _max_drawdown_pct(equity_curve: list[tuple[str, float]]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0][1]
    max_dd = 0.0
    for _, equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)
    return max_dd


def _sharpe_ratio(equity_curve: list[tuple[str, float]], periods_per_year: int) -> float | None:
    if len(equity_curve) < 3:
        return None
    values = [e for _, e in equity_curve]
    returns = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return (mean / std) * math.sqrt(periods_per_year)
