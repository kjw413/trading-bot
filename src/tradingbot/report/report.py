from __future__ import annotations

import base64
import math
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from tradingbot.engine.engine import BacktestResult
from tradingbot.report.metrics import calculate_metrics, closed_trades_frame


def generate_backtest_report(
    result: BacktestResult,
    *,
    strategy_name: str,
    market: str,
    symbols: list[str],
    reports_root: str | Path = "reports",
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = Path(reports_root) / f"{timestamp}_{strategy_name}_{market.upper()}"
    report_dir.mkdir(parents=True, exist_ok=True)

    metrics, closed_trades, drawdown = calculate_metrics(result)
    trades_df = closed_trades_frame(closed_trades)
    trades_path = report_dir / "trades.csv"
    trades_df.to_csv(trades_path, index=False)

    chart = _chart_base64(result, drawdown)
    html = _render_html(
        strategy_name=strategy_name,
        market=market,
        symbols=symbols,
        metrics=metrics,
        chart_base64=chart,
        trade_rows=trades_df.head(100).to_dict("records"),
        trades_csv_name=trades_path.name,
    )
    html_path = report_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def _chart_base64(result: BacktestResult, drawdown) -> str:
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    curve = result.equity_curve
    if not curve.empty:
        dates = curve["date"]
        axes[0].plot(dates, curve["equity"], color="#2563eb", linewidth=1.6)
        axes[0].set_ylabel("Equity")
        axes[0].grid(True, alpha=0.25)
        axes[1].fill_between(drawdown["date"], drawdown["drawdown"] * 100, 0, color="#dc2626", alpha=0.35)
        axes[1].set_ylabel("DD %")
        axes[1].grid(True, alpha=0.25)
    axes[0].set_title("Equity Curve")
    axes[1].set_title("Drawdown")
    fig.autofmt_xdate()
    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=140)
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _render_html(
    *,
    strategy_name,
    market,
    symbols,
    metrics,
    chart_base64,
    trade_rows,
    trades_csv_name,
) -> str:
    metric_rows = [
        ("Total Return", _pct(metrics.total_return_pct)),
        ("CAGR", _pct(metrics.cagr_pct)),
        ("Max Drawdown", _pct(metrics.max_drawdown_pct)),
        ("Sharpe", f"{metrics.sharpe:.2f}"),
        ("Win Rate", _pct(metrics.win_rate_pct)),
        ("Profit Factor", _number(metrics.profit_factor)),
        ("Closed Trades", str(metrics.closed_trades)),
    ]
    metrics_html = "".join(f"<tr><th>{escape(k)}</th><td>{escape(v)}</td></tr>" for k, v in metric_rows)
    trades_html = _trades_table(trade_rows)
    title = f"{strategy_name} {market.upper()} Backtest"
    return f"""<!doctype html>
<html lang=\"ko\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{escape(title)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #111827; }}
    h1 {{ margin-bottom: 4px; }}
    .subtle {{ color: #6b7280; margin-top: 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 18px 0; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 8px 10px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    .metrics {{ max-width: 620px; }}
    .metrics th {{ width: 45%; }}
    img {{ max-width: 100%; height: auto; border: 1px solid #e5e7eb; }}
    a {{ color: #2563eb; }}
  </style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <p class=\"subtle\">Symbols: {escape(', '.join(symbols))}</p>
  <h2>Metrics</h2>
  <table class=\"metrics\"><tbody>{metrics_html}</tbody></table>
  <h2>Equity & Drawdown</h2>
  <img alt=\"Equity and drawdown chart\" src=\"data:image/png;base64,{chart_base64}\">
  <h2>Closed Trades</h2>
  <p><a href=\"{escape(trades_csv_name)}\">Download trades.csv</a></p>
  {trades_html}
</body>
</html>
"""


def _trades_table(rows) -> str:
    if not rows:
        return "<p>No closed trades.</p>"
    headers = ["symbol", "entry_dt", "exit_dt", "qty", "entry_price", "exit_price", "pnl", "return_pct"]
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    row_html = []
    for row in rows:
        cells = []
        for header in headers:
            value = row.get(header, "")
            if isinstance(value, float):
                value = f"{value:,.4f}"
            cells.append(f"<td>{escape(str(value))}</td>")
        row_html.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(row_html)}</tbody></table>"


def _pct(value: float) -> str:
    return f"{value:,.2f}%"


def _number(value: float) -> str:
    if math.isinf(value):
        return "∞"
    return f"{value:,.2f}"
