from __future__ import annotations

import datetime as dt
import html
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import json
import streamlit as st
import streamlit.components.v1 as components


ROOT = Path(__file__).resolve().parent
HTML_PATH = ROOT / "docs" / "index.html"
SNAPSHOT_URL = "https://api.fugle.tw/marketdata/v1.0/stock/snapshot/quotes/{market}?type=COMMONSTOCK"


def fetch_json(url: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "X-API-KEY": api_key,
            "User-Agent": "Heatmap/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_live_quotes(api_key: str) -> tuple[dict[str, dict[str, Any]], str, str]:
    quotes: dict[str, dict[str, Any]] = {}
    latest_date = ""
    latest_time = ""
    for market in ("TSE", "OTC"):
        payload = fetch_json(SNAPSHOT_URL.format(market=market), api_key)
        latest_date = payload.get("date", latest_date)
        latest_time = payload.get("time", latest_time)
        for row in payload.get("data", []):
            code = str(row.get("symbol", "")).strip()
            if not re.fullmatch(r"\d{4}", code):
                continue
            quotes[code] = {
                "price": row.get("closePrice"),
                "change_pct": row.get("changePercent"),
                "volume_lots": row.get("tradeVolume"),
                "trade_value": row.get("tradeValue"),
            }
    return quotes, latest_date, latest_time


def fmt_pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"{value:+.2f}%"


def fmt_price(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"{value:,.2f}"


def fmt_int(value: float | int | None) -> str:
    if value is None:
        return "--"
    return f"{int(round(float(value))):,}"


def trend_class(value: float | None) -> str:
    if value is None:
        return "na"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def heat_color(value: float | None) -> str:
    if value is None:
        return "rgba(100,116,139,.18)"
    strength = min(abs(value) / 6.0, 1.0)
    alpha = 0.18 + strength * 0.38
    if value > 0:
        return f"rgba(244,63,94,{alpha:.3f})"
    if value < 0:
        return f"rgba(34,197,94,{alpha:.3f})"
    return "rgba(251,191,36,.22)"


def format_snapshot_time(date_text: str, time_text: str) -> tuple[str, str]:
    if re.fullmatch(r"\d{6}", time_text or ""):
        hhmmss = f"{time_text[:2]}:{time_text[2:4]}:{time_text[4:6]}"
    else:
        hhmmss = time_text or "--:--:--"
    return date_text or "--", hhmmss


def patch_row(match: re.Match[str], live_quotes: dict[str, dict[str, Any]], group_stats: dict[str, dict[str, float]]) -> str:
    code = match.group("code")
    group = match.group("group")
    name_html = match.group("name_html")
    market_html = match.group("market_html")
    role_html = match.group("role_html")
    tail_cells = list(match.group("tail_cells").split("|||CELL|||"))

    current_price = float(match.group("data_price"))
    current_change = float(match.group("data_change"))
    current_volume = float(match.group("data_volume"))

    live = live_quotes.get(code, {})
    price = live.get("price", current_price)
    change_pct = live.get("change_pct", current_change)
    volume_lots = live.get("volume_lots", current_volume)

    group_stat = group_stats.setdefault(group, {"sum_change": 0.0, "count": 0.0, "sum_volume": 0.0})
    if change_pct is not None:
        group_stat["sum_change"] += float(change_pct)
        group_stat["count"] += 1
    if volume_lots is not None:
        group_stat["sum_volume"] += float(volume_lots)

    cells = [
        f'<td class="code-cell"><span class="code">{code}</span><span class="market">{market_html}</span></td>',
        f'<td class="name-cell"><div class="name">{name_html}</div></td>',
        f'<td><span class="role-tag">{role_html}</span></td>',
        f'<td class="num">{fmt_price(price)}</td>',
        f'<td class="num {trend_class(change_pct)}">{fmt_pct(change_pct)}</td>',
        f'<td class="num">{fmt_int(volume_lots)}</td>',
    ]
    cells.extend([cell for cell in tail_cells if cell])
    return (
        f'<tr data-code="{code}" data-name="{html.escape(name_html)}" data-group="{html.escape(group)}" '
        f'data-price="{price}" data-change="{change_pct}" data-volume="{volume_lots}" '
        f'data-capital="{match.group("data_capital")}" data-eps="{match.group("data_eps")}" '
        f'data-yoy="{match.group("data_yoy")}" data-mom="{match.group("data_mom")}">'
        + "".join(cells)
        + "</tr>"
    )


def build_live_html(base_html: str, live_quotes: dict[str, dict[str, Any]], latest_date: str, latest_time: str) -> str:
    group_stats: dict[str, dict[str, float]] = {}

    row_pattern = re.compile(
        r'<tr data-code="(?P<code>\d+)" data-name="[^"]*" data-group="(?P<group>[^"]+)"\s+'
        r'data-price="(?P<data_price>[^"]+)" data-change="(?P<data_change>[^"]+)"\s+'
        r'data-volume="(?P<data_volume>[^"]+)" data-capital="(?P<data_capital>[^"]+)"\s+'
        r'data-eps="(?P<data_eps>[^"]+)" data-yoy="(?P<data_yoy>[^"]+)" data-mom="(?P<data_mom>[^"]+)">\s*'
        r'<td class="code-cell"><span class="code">\d+</span><span class="market">(?P<market_html>.*?)</span></td>\s*'
        r'<td class="name-cell"><div class="name">(?P<name_html>.*?)</div></td>\s*'
        r'<td><span class="role-tag">(?P<role_html>.*?)</span></td>\s*'
        r'<td class="num">.*?</td>\s*'
        r'<td class="num(?: [^"]+)?">.*?</td>\s*'
        r'<td class="num">.*?</td>\s*'
        r'(?P<tail_cells>(?:<td class="num(?: [^"]+)?">.*?</td>\s*){4})'
        r'</tr>',
        re.S,
    )

    html_text = row_pattern.sub(lambda match: patch_row(match, live_quotes, group_stats), base_html)

    total_volume = sum(stat["sum_volume"] for stat in group_stats.values())
    total_change_count = sum(stat["count"] for stat in group_stats.values())
    avg_change = (sum(stat["sum_change"] for stat in group_stats.values()) / total_change_count) if total_change_count else None
    live_date, live_time = format_snapshot_time(latest_date, latest_time)

    html_text = re.sub(
        r'(<div class="kpi"><div class="label">平均漲跌幅</div><div class="value )[^"]*(">[^\<]*</div></div>)',
        lambda m: f'{m.group(1)}{trend_class(avg_change)}{m.group(2).replace(m.group(2)[2:-13], fmt_pct(avg_change))}',
        html_text,
        count=1,
    )
    html_text = re.sub(
        r'(<div class="kpi"><div class="label">總成交量</div><div class="value">)([^<]*)(</div></div>)',
        lambda m: f'{m.group(1)}{fmt_int(total_volume)}張{m.group(3)}',
        html_text,
        count=1,
    )
    html_text = re.sub(
        r'(<div class="kpi"><div class="label">更新</div><div class="value" style="font-size:14px">)([^<]*)(</div></div>)',
        lambda m: f'{m.group(1)}{live_time}{m.group(3)}',
        html_text,
        count=1,
    )
    html_text = re.sub(
        r'(<div class="toolbar-meta">共 <b>\d+</b> 檔 \| 上市 <b>\d+</b> / 上櫃 <b>\d+</b> \| 股價基準 <b>)([^<]*)(</b></div>)',
        lambda m: f'{m.group(1)}{live_date} {live_time}{m.group(3)}',
        html_text,
        count=1,
    )

    for group, stat in group_stats.items():
        avg = (stat["sum_change"] / stat["count"]) if stat["count"] else None
        volume = stat["sum_volume"]
        group_pattern = re.compile(
            rf'(<section class="group-card" data-group="{re.escape(group)}"[\s\S]*?<span class="group-chip )[^"]*(">\s*)([^<]*)(</span>)',
            re.S,
        )
        html_text = group_pattern.sub(
            lambda m, avg=avg: f'{m.group(1)}{trend_class(avg)}{m.group(2)}{fmt_pct(avg)}{m.group(4)}',
            html_text,
            count=1,
        )

        heat_pattern = re.compile(
            rf'(<button class="stage-heat-cell )[^"]*(" data-filter="{re.escape(group)}" style="--accent:([^;]+); --heat:)([^"]*)(">\s*'
            r'<div class="stage-heat-name">.*?</div>\s*<div class="stage-heat-change">)([^<]*)(</div>\s*<div class="stage-heat-volume">)([^<]*)(</div>)',
            re.S,
        )
        html_text = heat_pattern.sub(
            lambda m, avg=avg, volume=volume: (
                f'{m.group(1)}{trend_class(avg)}{m.group(2)}{m.group(3)}; --heat:{heat_color(avg)}{m.group(5)}'
                f'{fmt_pct(avg)}{m.group(7)}{fmt_int(volume)} 張{m.group(9)}'
            ),
            html_text,
            count=1,
        )

    return html_text


def main() -> None:
    st.set_page_config(
        page_title="台灣半導體 × AI 產業鏈全圖",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    html_content = HTML_PATH.read_text(encoding="utf-8")
    fugle_conf = st.secrets.get("fugle", {})
    api_key = fugle_conf.get("api_key")

    if api_key:
        try:
            live_quotes, latest_date, latest_time = load_live_quotes(api_key)
            html_content = build_live_html(html_content, live_quotes, latest_date, latest_time)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Fugle 即時更新失敗，先顯示靜態頁：{exc}")

    st.markdown(
        """
        <style>
          .block-container {
            padding-top: 0.2rem;
            padding-bottom: 0;
            max-width: 100%;
          }
          [data-testid="stHeader"] {
            background: transparent;
          }
          [data-testid="stSidebar"] {
            display: none;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    components.html(html_content, height=4200, scrolling=True)


if __name__ == "__main__":
    main()
