"""2020 起 point-in-time 動能回測的 CLI、合成資料與報表輸出。

本模組只負責資料來源接線及呈現；選股、成交與績效計算由
``momentum_engine.run_backtest`` 負責。
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import html
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Callable, Sequence


DEFAULT_OUT = Path("backtest/momentum_pit")
LIST_SECTIONS = ("signals", "trades", "cohorts", "equity", "issues")
EVIDENCE_KEYS = ("universe", "events", "execution", "coverage")


def build_demo_data() -> dict[str, Any]:
    """建立固定、可重現且明確標示為合成的最小完整資料集。"""
    first = date(2018, 12, 1)
    last = date(2023, 2, 28)
    calendar: list[str] = []
    cursor = first
    while cursor <= last:
        if cursor.weekday() < 5:
            calendar.append(cursor.isoformat())
        cursor += timedelta(days=1)

    # 不使用 random；每檔的趨勢與週期都由交易日序號決定，跨平台可重現。
    specs = {
        "SYN1": (80.0, 0.00075, 0.018, 19.0),
        "SYN2": (105.0, 0.00045, 0.025, 27.0),
        "SYN3": (62.0, 0.00015, 0.032, 35.0),
        "SYN4": (130.0, -0.00005, 0.020, 23.0),
    }
    prices: dict[str, dict[str, dict[str, Any]]] = {}
    for stock_no, (stock_id, (base, drift, amplitude, period)) in enumerate(specs.items()):
        rows: dict[str, dict[str, Any]] = {}
        for index, day in enumerate(calendar):
            close = base * math.exp(drift * index) * (
                1.0 + amplitude * math.sin((index + stock_no * 4) / period)
            )
            open_price = close * (1.0 + 0.002 * math.sin(index / 7.0 + stock_no))
            rows[day] = {
                "open": round(open_price, 4),
                "close": round(close, 4),
                "volume": 1_000_000 + stock_no * 100_000 + (index % 17) * 1_000,
                "buyable": True,
                "sellable": True,
            }
        prices[stock_id] = rows

    evidence = {key: "synthetic-demo: deterministic fixture" for key in EVIDENCE_KEYS}
    return {
        "synthetic": True,
        "dataset_label": "合成展示資料（非真實市場績效）",
        "calendar": calendar,
        "prices": prices,
        "securities": [
            {
                "stock_id": stock_id,
                "start": "2018-12-01",
                "end": None,
                "known_at": "2018-12-01",
                "source": "synthetic-demo",
            }
            for stock_id in specs
        ],
        "events": [],
        "verified": {key: True for key in EVIDENCE_KEYS},
        "evidence": evidence,
    }


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"輸入 JSON 根節點必須是物件：{path}")
    return value


def _load_engine() -> Callable[..., dict[str, Any]]:
    from momentum_engine import run_backtest

    return run_backtest


def _load_data_functions() -> tuple[Callable[..., dict[str, Any]], Callable[..., dict[str, Any]]]:
    from momentum_data import audit_cache, load_cache

    return audit_cache, load_cache


def _as_rows(value: Any) -> list[dict[str, Any]]:
    """將契約中的 list 轉成可安全輸出 CSV 的列。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("報表區段必須是 list")
    rows: list[dict[str, Any]] = []
    for item in value:
        rows.append(dict(item) if isinstance(item, dict) else {"value": item})
    return rows


def _csv_columns(rows: Sequence[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    for row in rows:
        for key in row:
            name = str(key)
            if name not in columns:
                columns.append(name)
    return columns


def write_listdict_csv(path: Path, rows: Sequence[dict[str, Any]], *, synthetic: bool) -> None:
    """以所有列欄位的穩定聯集寫 CSV，並保留合成來源標記。"""
    tagged = [
        {"synthetic": synthetic, **{key: value for key, value in row.items() if key != "synthetic"}}
        for row in rows
    ]
    columns = _csv_columns(tagged) or ["synthetic"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(tagged)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _equity_series(rows: Sequence[dict[str, Any]], field: str) -> dict[str, list[tuple[str, float]]]:
    series: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        day = str(row.get("date", ""))
        value = _number(row.get(field))
        if not day or value is None:
            continue
        strategy = str(row.get("strategy") or "未命名策略")
        horizon_value = row.get("horizon")
        horizon = str(horizon_value if horizon_value not in (None, "") else "未指定期間")
        series.setdefault(f"{strategy} / {horizon}", []).append((day, value))
    for points in series.values():
        points.sort(key=lambda pair: pair[0])
    return series


def _svg_chart(
    series: dict[str, list[tuple[str, float]]],
    *,
    title: str,
    y_label: str,
    synthetic: bool,
) -> str:
    """產生可單獨開啟的零相依 SVG 折線圖。"""
    width, height = 1000, 560
    left, right, top, bottom = 82, 32, 72, 88
    plot_w, plot_h = width - left - right, height - top - bottom
    all_points = [point for points in series.values() for point in points]
    label = "合成展示資料｜非真實市場績效" if synthetic else "研究資料（認證狀態見報告）"
    if not all_points:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
            f'<title id="title">{html.escape(title)}</title>'
            f'<desc id="desc">沒有可繪製的淨值資料</desc><rect width="100%" height="100%" fill="#fff"/>'
            f'<text x="40" y="55" font-size="24">{html.escape(title)}</text>'
            f'<text x="40" y="92" fill="#9b2c2c">{html.escape(label)}</text>'
            '<text x="500" y="285" text-anchor="middle" fill="#666">沒有可繪製的資料</text></svg>'
        )

    dates = sorted({day for day, _ in all_points})
    date_index = {day: index for index, day in enumerate(dates)}
    values = [value for _, value in all_points]
    low, high = min(values), max(values)
    if math.isclose(low, high):
        padding = max(abs(low) * 0.05, 0.05)
        low -= padding
        high += padding
    else:
        padding = (high - low) * 0.08
        low -= padding
        high += padding

    def x_pos(day: str) -> float:
        denominator = max(len(dates) - 1, 1)
        return left + date_index[day] / denominator * plot_w

    def y_pos(value: float) -> float:
        return top + (high - value) / (high - low) * plot_h

    palette = ("#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2", "#475569")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{html.escape(title)}</title>',
        f'<desc id="desc">{html.escape(label)}；依策略與持有期間繪製的{html.escape(y_label)}折線圖。</desc>',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="34" font-family="sans-serif" font-size="24" font-weight="700">{html.escape(title)}</text>',
        f'<text x="{left}" y="58" font-family="sans-serif" font-size="14" fill="{("#9b2c2c" if synthetic else "#475569")}">{html.escape(label)}</text>',
    ]
    for tick in range(6):
        value = low + (high - low) * tick / 5
        y = y_pos(value)
        parts.extend(
            [
                f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e2e8f0"/>',
                f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="12" fill="#475569">{value:.3f}</text>',
            ]
        )
    parts.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#64748b"/>')
    parts.append(f'<text x="20" y="{top + plot_h / 2:.1f}" transform="rotate(-90 20 {top + plot_h / 2:.1f})" font-family="sans-serif" font-size="13">{html.escape(y_label)}</text>')
    for tick in range(5):
        index = round((len(dates) - 1) * tick / 4)
        day = dates[index]
        x = x_pos(day)
        parts.append(f'<text x="{x:.1f}" y="{top + plot_h + 24}" text-anchor="middle" font-family="sans-serif" font-size="12" fill="#475569">{html.escape(day)}</text>')

    legend_x, legend_y = left, height - 34
    for index, (name, points) in enumerate(sorted(series.items())):
        color = palette[index % len(palette)]
        coords = " ".join(f"{x_pos(day):.1f},{y_pos(value):.1f}" for day, value in points)
        parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"/>')
        x = legend_x + (index % 4) * 220
        y = legend_y + (index // 4) * 20
        parts.append(f'<line x1="{x}" y1="{y - 4}" x2="{x + 20}" y2="{y - 4}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{x + 26}" y="{y}" font-family="sans-serif" font-size="12">{html.escape(name)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _format_metric(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _format_percent(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number * 100:.2f}%"


def _strategy_name(value: str) -> str:
    return {"momentum": "動能前 25%", "universe": "全母體等權"}.get(value, value)


def _append_table(lines: list[str], headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |")


def _certification_text(certification: Any) -> str:
    if isinstance(certification, dict):
        for key in ("status", "certified", "label"):
            if key in certification:
                return _format_metric(certification[key])
        return json.dumps(certification, ensure_ascii=False, sort_keys=True)
    return _format_metric(certification)


def render_markdown_report(
    result: dict[str, Any],
    *,
    synthetic: bool,
    csv_names: dict[str, str],
    sensitivity_name: str | None = None,
) -> str:
    """建立可獨立閱讀的繁體中文研究報告。"""
    title = "2020 起 12−1 動能回測報告"
    if synthetic:
        title += "（合成展示資料）"
    lines = [f"# {title}", ""]
    if synthetic:
        lines.extend(
            [
                "> **合成展示資料：所有價格、證券與績效均為人工生成，非真實市場績效，不能作為投資依據。**",
                "",
            ]
        )
    lines.extend(
        [
            "## 資料與認證",
            "",
            f"- 認證狀態：{_certification_text(result.get('certification'))}",
            f"- 資料類型：{'合成展示資料' if synthetic else '已載入研究資料'}",
            f"- 訊號筆數：{len(_as_rows(result.get('signals')))}",
            f"- 交易筆數：{len(_as_rows(result.get('trades')))}",
            f"- cohort 筆數：{len(_as_rows(result.get('cohorts')))}",
            "",
            "策略在月底訊號固定後於下一個市場交易日嘗試成交；6／12 個月 cohort 彼此重疊，不能直接連乘成年化。CAGR 與最大回撤只應解讀逐日記帳的資金梯隊曲線。",
            "TAIEX／TPEx benchmark 是收盤參考總報酬曲線（horizon=0）；它不能提供與個股下一交易日開盤成交完全一致的精確超額報酬。",
            "",
            "## 摘要",
            "",
        ]
    )
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    main_rows: list[list[str]] = []
    for strategy in ("momentum", "universe"):
        for horizon in (6, 12):
            row = summary.get(f"{strategy}_{horizon}")
            if not isinstance(row, dict):
                continue
            main_rows.append(
                [
                    _strategy_name(strategy),
                    f"{horizon} 個月",
                    _format_metric(row.get("n")),
                    _format_percent(row.get("mean")),
                    _format_percent(row.get("median")),
                    _format_percent(row.get("positive_rate")),
                    _format_percent(row.get("CAGR")),
                    _format_percent(row.get("MDD")),
                    _format_percent(row.get("annual_volatility")),
                ]
            )
    if main_rows:
        _append_table(
            lines,
            ("策略", "持有期", "成熟批次", "平均", "中位數", "正報酬率", "CAGR", "MDD", "年化波動"),
            main_rows,
        )
    else:
        lines.append("無主要策略摘要資料。")

    year_rows: list[list[str]] = []
    year_pattern = re.compile(r"^(momentum|universe)_(6|12)_entry_year_(\d{4})$")
    for key, row in sorted(summary.items()):
        matched = year_pattern.match(str(key))
        if not matched or not isinstance(row, dict):
            continue
        strategy, horizon, year = matched.groups()
        year_rows.append(
            [
                _strategy_name(strategy),
                f"{horizon} 個月",
                year,
                _format_metric(row.get("n")),
                _format_percent(row.get("mean")),
                _format_percent(row.get("median")),
                _format_percent(row.get("worst_cohort")),
            ]
        )
    lines.extend(["", "### 依進場年度", ""])
    if year_rows:
        _append_table(lines, ("策略", "持有期", "年度", "成熟批次", "平均", "中位數", "最差批次"), year_rows)
    else:
        lines.append("無進場年度摘要資料。")

    common_rows: list[list[str]] = []
    for strategy in ("momentum", "universe"):
        row = summary.get(f"{strategy}_common_entries")
        if isinstance(row, dict):
            common_rows.append(
                [
                    _strategy_name(strategy),
                    _format_metric(row.get("n")),
                    _format_percent(row.get("mean_6m")),
                    _format_percent(row.get("mean_12m")),
                ]
            )
    lines.extend(["", "### 6／12 個月共同成熟月份", ""])
    if common_rows:
        _append_table(lines, ("策略", "共同月份數", "6 個月平均", "12 個月平均"), common_rows)
    else:
        lines.append("無共同成熟月份摘要資料。")

    excess_rows: list[list[str]] = []
    excess_pattern = re.compile(r"^(momentum|universe)_(6|12)_vs_universe$")
    for key, row in sorted(summary.items()):
        matched = excess_pattern.match(str(key))
        if not matched or not isinstance(row, dict):
            continue
        strategy, horizon = matched.groups()
        ci = row.get("excess_ci95")
        ci_text = "—"
        if isinstance(ci, (list, tuple)) and len(ci) == 2:
            ci_text = f"{_format_percent(ci[0])} ～ {_format_percent(ci[1])}"
        excess_rows.append(
            [
                _strategy_name(strategy),
                f"{horizon} 個月",
                _format_metric(row.get("n")),
                _format_percent(row.get("mean_excess")),
                _format_percent(row.get("beat_rate")),
                ci_text,
            ]
        )
    lines.extend(["", "### 相對全母體等權", ""])
    if excess_rows:
        _append_table(lines, ("策略", "持有期", "共同批次", "平均超額", "勝率", "超額 95% CI"), excess_rows)
    else:
        lines.append("無相對母體摘要資料。")
    lines.extend(["", "其他完整摘要欄位與共同月份日期清單保留在 `result.json`，不在此重複展開。"])

    lines.extend(["", "## 圖表", ""])
    prefix = "synthetic_" if synthetic else ""
    lines.extend(
        [
            f"- [資金梯隊淨值曲線]({prefix}equity.svg)",
            f"- [資金梯隊回撤曲線]({prefix}drawdown.svg)",
            "",
            "## 明細檔案",
            "",
        ]
    )
    for section in LIST_SECTIONS:
        lines.append(f"- `{csv_names[section]}`（{section}）")
    if sensitivity_name:
        lines.append(f"- `{sensitivity_name}`（固定策略的交易成本敏感度摘要）")

    issues = _as_rows(result.get("issues"))
    lines.extend(["", "## 未解決事項", ""])
    if not issues:
        lines.append("無。")
    else:
        for issue in issues:
            text = issue.get("message", issue.get("value", json.dumps(issue, ensure_ascii=False)))
            lines.append(f"- {_format_metric(text)}")
    lines.extend(
        [
            "",
            "## 解讀限制",
            "",
            "本報告依固定研究規格呈現回測結果。交易成本是研究假設；未成交部位保留現金；right_censored cohort 不列作完整持有期報酬；缺乏實際下市結算證據的曝險不得當成已實現績效。",
            "cohort 總報酬包含已有可靠證據的股利應收。持有期結束後才入帳的股利先留在原梯隊的現金準備，僅在該梯隊下一次輪轉時再投資，絕不在實際支付日前提前投入。",
            "",
        ]
    )
    return "\n".join(lines)


def write_report_bundle(
    result: dict[str, Any],
    out_dir: Path,
    *,
    synthetic: bool,
    cost_sensitivity: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    """寫出 JSON、各 list[dict] CSV、繁中報告及淨值／回撤 SVG。"""
    required = ("certification", "signals", "trades", "cohorts", "equity", "summary", "issues")
    missing = [key for key in required if key not in result]
    if missing:
        raise ValueError(f"回測結果缺少必要欄位：{', '.join(missing)}")

    normalized = dict(result)
    normalized["synthetic"] = synthetic
    normalized["dataset_label"] = "合成展示資料（非真實市場績效）" if synthetic else "研究資料"
    for section in LIST_SECTIONS:
        normalized[section] = [
            {"synthetic": synthetic, **{key: value for key, value in row.items() if key != "synthetic"}}
            for row in _as_rows(result.get(section))
        ]

    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "result.json"
    with result_path.open("w", encoding="utf-8") as handle:
        json.dump(normalized, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    prefix = "synthetic_" if synthetic else ""
    csv_names: dict[str, str] = {}
    paths: dict[str, Path] = {"result": result_path}
    for section in LIST_SECTIONS:
        filename = f"{prefix}{section}.csv"
        path = out_dir / filename
        write_listdict_csv(path, normalized[section], synthetic=synthetic)
        csv_names[section] = filename
        paths[section] = path

    equity_rows = normalized["equity"]
    equity_path = out_dir / f"{prefix}equity.svg"
    drawdown_path = out_dir / f"{prefix}drawdown.svg"
    equity_path.write_text(
        _svg_chart(
            _equity_series(equity_rows, "nav"),
            title="資金梯隊淨值曲線" + ("（合成展示）" if synthetic else ""),
            y_label="淨值",
            synthetic=synthetic,
        ),
        encoding="utf-8",
    )
    drawdown_path.write_text(
        _svg_chart(
            _equity_series(equity_rows, "drawdown"),
            title="資金梯隊回撤曲線" + ("（合成展示）" if synthetic else ""),
            y_label="回撤",
            synthetic=synthetic,
        ),
        encoding="utf-8",
    )
    paths.update({"equity_svg": equity_path, "drawdown_svg": drawdown_path})

    sensitivity_name: str | None = None
    if cost_sensitivity is not None:
        sensitivity_name = f"{prefix}cost_sensitivity.json"
        sensitivity_path = out_dir / sensitivity_name
        sensitivity_payload = {
            "synthetic": synthetic,
            "dataset_label": normalized["dataset_label"],
            "note": "同一份資料與固定策略只改變對稱買賣成本；不是策略參數搜尋。",
            "runs": cost_sensitivity,
        }
        sensitivity_path.write_text(
            json.dumps(sensitivity_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths["cost_sensitivity"] = sensitivity_path

    report_path = out_dir / "report.md"
    report_path.write_text(
        render_markdown_report(
            normalized,
            synthetic=synthetic,
            csv_names=csv_names,
            sensitivity_name=sensitivity_name,
        ),
        encoding="utf-8",
    )
    paths["report"] = report_path
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="執行固定合成展示資料")
    source.add_argument("--input", type=Path, help="讀取正規化輸入 JSON")
    source.add_argument("--raw-db", type=Path, help="讀取下載快取 SQLite")
    parser.add_argument("--audit", action="store_true", help="只盤點 raw cache，不計算績效")
    parser.add_argument("--manifest", type=Path, help="下載 manifest JSON")
    parser.add_argument("--evidence", type=Path, help="人工核對證據 JSON")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"輸出目錄（預設 {DEFAULT_OUT}）")
    parser.add_argument("--start", default="2020-01-01", help="首個允許建倉日")
    parser.add_argument("--end", default=None, help="資料估值截止日；也決定 cohort 成熟或右設限狀態")
    parser.add_argument("--buy-cost", type=float, default=0.003, help="買進成本率（預設 0.003）")
    parser.add_argument("--sell-cost", type=float, default=0.003, help="賣出成本率（預設 0.003）")
    parser.add_argument(
        "--cost-sensitivity",
        nargs="*",
        type=float,
        metavar="RATE",
        help="以相同買賣成本率重跑摘要；未列數值時使用 0.0015、0.003、0.006",
    )
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.audit and args.raw_db is None:
        parser.error("--audit 必須搭配 --raw-db")
    if args.audit and args.evidence is not None:
        parser.error("--audit 不讀取 --evidence")
    if args.raw_db is not None and args.manifest is None:
        parser.error("--raw-db 必須搭配 --manifest")
    if args.raw_db is not None and not args.audit and args.evidence is None:
        parser.error("真實資料回測必須提供 --evidence")
    if (args.demo or args.input is not None) and args.audit:
        parser.error("--audit 只適用於 --raw-db")
    if args.buy_cost < 0 or args.sell_cost < 0:
        parser.error("交易成本不得為負數")
    if args.cost_sensitivity is not None and any(rate < 0 for rate in args.cost_sensitivity):
        parser.error("成本敏感度費率不得為負數")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    try:
        if args.audit:
            audit_cache, _ = _load_data_functions()
            audit = audit_cache(args.raw_db, args.manifest)
            if not isinstance(audit, dict):
                raise ValueError("快取盤點必須回傳 dict")
            audit = {"audit_only": True, "returns_computed": False, **audit}
            args.out.mkdir(parents=True, exist_ok=True)
            audit_path = args.out / "audit.json"
            audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"快取盤點完成（未計算績效）：{audit_path}")
            return 0

        if args.demo:
            data = build_demo_data()
        elif args.input is not None:
            data = _load_json(args.input)
        else:
            _, load_cache = _load_data_functions()
            data = load_cache(args.raw_db, args.manifest, args.evidence)

        synthetic = bool(data.get("synthetic", False))
        engine = _load_engine()
        result = engine(
            data,
            start=args.start,
            end=args.end,
            buy_cost=args.buy_cost,
            sell_cost=args.sell_cost,
        )
        if not isinstance(result, dict):
            raise ValueError("回測引擎必須回傳 dict")

        requested_rates = args.cost_sensitivity
        if requested_rates == [] or (requested_rates is None and synthetic):
            requested_rates = [0.0015, 0.003, 0.006]
        sensitivity: list[dict[str, Any]] | None = None
        if requested_rates is not None:
            sensitivity = []
            for rate in dict.fromkeys(requested_rates):
                sensitivity_result = engine(
                    data,
                    start=args.start,
                    end=args.end,
                    buy_cost=rate,
                    sell_cost=rate,
                )
                if not isinstance(sensitivity_result, dict) or "summary" not in sensitivity_result:
                    raise ValueError("成本敏感度回測缺少 summary")
                sensitivity.append({"buy_cost": rate, "sell_cost": rate, "summary": sensitivity_result["summary"]})

        paths = write_report_bundle(
            result,
            args.out,
            synthetic=synthetic,
            cost_sensitivity=sensitivity,
        )
        mode = "合成展示" if synthetic else "研究資料"
        print(f"{mode}回測完成：{paths['report']}")
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"拒絕產生回測結果：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
