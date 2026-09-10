"""
backtest/metrics.py

Standard performance metrics computed from a BacktestResult.
"""

import math
from dataclasses import dataclass

from backtest.engine import BacktestResult


@dataclass
class Metrics:
    total_return_pct: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float | None   # gross profit / gross loss; None if no losing trades
    max_drawdown_pct: float
    sharpe_ratio: float | None    # annualized, None if insufficient data
    total_commission_paid: float
    avg_win: float
    avg_loss: float

    def summary(self) -> str:
        pf = f"{self.profit_factor:.2f}" if self.profit_factor is not None else "n/a (no losing trades)"
        sharpe = f"{self.sharpe_ratio:.2f}" if self.sharpe_ratio is not None else "n/a"
        return (
            f"Total return:       {self.total_return_pct:+.2f}%\n"
            f"Total trades:       {self.total_trades}\n"
            f"Win rate:           {self.win_rate_pct:.1f}%\n"
            f"Profit factor:      {pf}\n"
            f"Max drawdown:       {self.max_drawdown_pct:.2f}%\n"
            f"Sharpe ratio (ann): {sharpe}\n"
            f"Total commission:   {self.total_commission_paid:.2f}\n"
            f"Avg win / avg loss: {self.avg_win:.2f} / {self.avg_loss:.2f}"
        )


def compute_metrics(result: BacktestResult, periods_per_year: int = 252) -> Metrics:
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
