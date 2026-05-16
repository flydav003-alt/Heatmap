from __future__ import annotations

import concurrent.futures
import html
import json
import math
import urllib.error
import urllib.request
from pathlib import Path
from textwrap import dedent
from typing import Any

import streamlit as st


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "docs" / "representative_chain_data.json"

THEMES: list[dict[str, Any]] = [
    {
        "id": "ASIC",
        "label": "IC設計 / IP / ASIC",
        "stage": "上游",
        "accent": "#7c3aed",
        "stocks": [
            {"code": "2454", "name": "聯發科", "market": "上市"},
            {"code": "3443", "name": "創意", "market": "上市"},
            {"code": "3035", "name": "智原", "market": "上市"},
            {"code": "5274", "name": "信驊", "market": "上櫃"},
            {"code": "6643", "name": "M31", "market": "上櫃"},
        ],
    },
    {
        "id": "HBM",
        "label": "記憶體 / HBM",
        "stage": "上游",
        "accent": "#8b5cf6",
        "stocks": [
            {"code": "2408", "name": "南亞科", "market": "上市"},
            {"code": "2337", "name": "旺宏", "market": "上市"},
            {"code": "8299", "name": "群聯", "market": "上櫃"},
            {"code": "3260", "name": "威剛", "market": "上櫃"},
            {"code": "6531", "name": "愛普*", "market": "上市"},
        ],
    },
    {
        "id": "COWOS",
        "label": "先進封裝 / CoWoS",
        "stage": "中游",
        "accent": "#ec4899",
        "stocks": [
            {"code": "1560", "name": "中砂", "market": "上市"},
            {"code": "3583", "name": "辛耘", "market": "上市"},
            {"code": "6187", "name": "萬潤", "market": "上市"},
            {"code": "6640", "name": "均華", "market": "上櫃"},
            {"code": "3131", "name": "弘塑", "market": "上市"},
        ],
    },
    {
        "id": "THERMAL",
        "label": "散熱",
        "stage": "中游",
        "accent": "#38bdf8",
        "stocks": [
            {"code": "3017", "name": "奇鋐", "market": "上市"},
            {"code": "3324", "name": "雙鴻", "market": "上櫃"},
            {"code": "3653", "name": "健策", "market": "上櫃"},
            {"code": "2421", "name": "建準", "market": "上市"},
        ],
    },
    {
        "id": "BBU",
        "label": "電源 / BBU",
        "stage": "中游",
        "accent": "#f97316",
        "stocks": [
            {"code": "2308", "name": "台達電", "market": "上市"},
            {"code": "6409", "name": "旭隼", "market": "上市"},
            {"code": "6412", "name": "群電", "market": "上市"},
            {"code": "6121", "name": "新普", "market": "上市"},
        ],
    },
    {
        "id": "PCB",
        "label": "PCB / 載板",
        "stage": "中游",
        "accent": "#06b6d4",
        "stocks": [
            {"code": "3037", "name": "欣興", "market": "上市"},
            {"code": "8046", "name": "南電", "market": "上市"},
            {"code": "2383", "name": "台光電", "market": "上市"},
            {"code": "2368", "name": "金像電", "market": "上市"},
            {"code": "6274", "name": "台燿", "market": "上市"},
        ],
    },
    {
        "id": "SERVER",
        "label": "AI伺服器 / 機櫃組裝",
        "stage": "下游",
        "accent": "#f43f5e",
        "stocks": [
            {"code": "2317", "name": "鴻海", "market": "上市"},
            {"code": "2382", "name": "廣達", "market": "上市"},
            {"code": "3231", "name": "緯創", "market": "上市"},
            {"code": "6669", "name": "緯穎", "market": "上市"},
            {"code": "2356", "name": "英業達", "market": "上市"},
        ],
    },
    {
        "id": "CPO",
        "label": "網通 / 光通訊 / CPO",
        "stage": "下游",
        "accent": "#0ea5e9",
        "stocks": [
            {"code": "4979", "name": "華星光", "market": "上櫃"},
            {"code": "4908", "name": "前鼎", "market": "上市"},
            {"code": "3163", "name": "波若威", "market": "上櫃"},
            {"code": "3450", "name": "聯鈞", "market": "上市"},
            {"code": "3596", "name": "智易", "market": "上市"},
        ],
    },
]

THEME_IDS = [theme["id"] for theme in THEMES]
THEME_BY_ID = {theme["id"]: theme for theme in THEMES}
CODE_TO_META = {
    stock["code"]: {**stock, "theme_id": theme["id"], "theme_label": theme["label"], "stage": theme["stage"], "accent": theme["accent"]}
    for theme in THEMES
    for stock in theme["stocks"]
}


def load_static_data() -> dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def get_fugle_settings() -> tuple[str | None, bool]:
    fugle_conf = st.secrets.get("fugle", {})
    api_key = fugle_conf.get("api_key") or None
    use_snapshot = bool(fugle_conf.get("use_snapshot", False))
    return api_key, use_snapshot


def fetch_json(url: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "X-API-KEY": api_key,
            "User-Agent": "Heatmap/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_intraday_quote(code: str, api_key: str) -> dict[str, Any] | None:
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{code}"
    try:
        data = fetch_json(url, api_key)
    except urllib.error.URLError:
        return None
    total = data.get("total", {})
    return {
        "code": code,
        "price": data.get("lastPrice") or data.get("closePrice") or data.get("referencePrice"),
        "change_pct": data.get("changePercent"),
        "volume_lots": (total.get("tradeVolume") or 0) / 1000,
        "trade_value_m": (total.get("tradeValue") or 0) / 1_000_000,
        "last_updated": data.get("lastUpdated"),
        "name": data.get("name"),
    }


def fetch_snapshot_market(market: str, api_key: str) -> dict[str, dict[str, Any]]:
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/snapshot/quotes/{market}"
    data = fetch_json(url, api_key)
    rows: dict[str, dict[str, Any]] = {}
    for row in data.get("data", []):
        code = row.get("symbol", "")
        rows[code] = {
            "code": code,
            "price": row.get("closePrice"),
            "change_pct": row.get("changePercent"),
            "volume_lots": (row.get("tradeVolume") or 0) / 1000,
            "trade_value_m": (row.get("tradeValue") or 0) / 1_000_000,
            "last_updated": row.get("lastUpdated"),
            "name": row.get("name"),
        }
    return rows


def trend_class(value: float | None) -> str:
    if value is None:
        return "flat"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def fmt_pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"{value:+.2f}%"


def fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"{value:,.{digits}f}"


def heat_color(value: float | None) -> str:
    if value is None:
        return "rgba(100,116,139,.18)"
    strength = min(abs(value) / 6, 1)
    alpha = 0.16 + strength * 0.42
    if value > 0:
        return f"rgba(244,63,94,{alpha:.3f})"
    if value < 0:
        return f"rgba(34,197,94,{alpha:.3f})"
    return "rgba(251,191,36,.20)"


def normalize_static_payload(raw: dict[str, Any]) -> dict[str, Any]:
    by_code: dict[str, dict[str, Any]] = {}
    for theme in raw.get("themes", {}).values():
        for stock in theme.get("stocks", []):
            code = stock.get("code")
            if code:
                by_code[code] = stock

    themes: list[dict[str, Any]] = []
    for theme in THEMES:
        stocks = []
        changes = []
        volumes = []
        for stock_meta in theme["stocks"]:
            source = by_code.get(stock_meta["code"], {})
            merged = {
                **stock_meta,
                "price": source.get("price"),
                "change_pct": source.get("change_pct"),
                "volume_lots": source.get("volume_lots"),
                "trade_value_m": source.get("trade_value_m"),
            }
            stocks.append(merged)
            if merged["change_pct"] is not None:
                changes.append(merged["change_pct"])
            if merged["volume_lots"] is not None:
                volumes.append(merged["volume_lots"])
        themes.append(
            {
                "id": theme["id"],
                "label": theme["label"],
                "stage": theme["stage"],
                "accent": theme["accent"],
                "stocks": stocks,
                "avg_change_pct": (sum(changes) / len(changes)) if changes else None,
                "volume_lots": sum(volumes) if volumes else None,
            }
        )
    return {
        "updated_at": raw.get("updated_at", "--"),
        "latest_price_date": raw.get("latest_price_date", "--"),
        "themes": themes,
    }


def apply_live_quotes(data: dict[str, Any], api_key: str, use_snapshot: bool) -> dict[str, Any]:
    codes = list(CODE_TO_META.keys())
    live_by_code: dict[str, dict[str, Any]] = {}

    if use_snapshot:
        live_by_code = {
            **fetch_snapshot_market("TSE", api_key),
            **fetch_snapshot_market("OTC", api_key),
        }
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(fetch_intraday_quote, code, api_key) for code in codes]
            for future in concurrent.futures.as_completed(futures):
                payload = future.result()
                if payload and payload.get("code"):
                    live_by_code[payload["code"]] = payload

    merged = json.loads(json.dumps(data))
    last_updated = None
    for theme in merged["themes"]:
        changes = []
        volumes = []
        values = []
        for stock in theme["stocks"]:
            live = live_by_code.get(stock["code"])
            if live:
                stock["price"] = live.get("price", stock.get("price"))
                stock["change_pct"] = live.get("change_pct", stock.get("change_pct"))
                stock["volume_lots"] = live.get("volume_lots", stock.get("volume_lots"))
                stock["trade_value_m"] = live.get("trade_value_m")
                if live.get("name"):
                    stock["name"] = live["name"]
                last_updated = live.get("last_updated") or last_updated
            if stock.get("change_pct") is not None:
                changes.append(stock["change_pct"])
            if stock.get("volume_lots") is not None:
                volumes.append(stock["volume_lots"])
            if stock.get("trade_value_m") is not None:
                values.append(stock["trade_value_m"])
        theme["avg_change_pct"] = (sum(changes) / len(changes)) if changes else None
        theme["volume_lots"] = sum(volumes) if volumes else None
        theme["trade_value_m"] = sum(values) if values else None
    if last_updated:
        merged["updated_at"] = str(last_updated)
    return merged


def render_css() -> None:
    st.markdown(
        dedent(
            """
            <style>
              .stApp {
                background:
                  linear-gradient(rgba(14,31,49,.74) 1px, transparent 1px),
                  linear-gradient(90deg, rgba(14,31,49,.74) 1px, transparent 1px),
                  linear-gradient(180deg,#07111d 0%, #091423 100%);
                background-size: 54px 54px, 54px 54px, auto;
                color: #eaf2ff;
              }
              .block-container { max-width: 1540px; padding-top: 1rem; padding-bottom: 2rem; }
              [data-testid="stHeader"] { background: transparent; }
              .hero { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:14px; flex-wrap:wrap; }
              .hero-left { min-width: 320px; }
              .hero-title { font-size:2rem; font-weight:900; line-height:1.1; margin:0; }
              .hero-sub { color:#8aa1c2; font-size:.94rem; margin-top:.3rem; }
              .hero-kpis { display:flex; gap:10px; flex-wrap:wrap; }
              .hero-kpi { min-width:140px; background:rgba(10,22,38,.92); border:1px solid #17304a; border-radius:16px; padding:12px 14px; }
              .hero-kpi-label { color:#7f93b2; font-size:.78rem; }
              .hero-kpi-value { margin-top:6px; font-size:1.16rem; font-weight:900; }
              .mode-pill { margin:10px 0 16px; display:inline-block; color:#7f93b2; font-size:.86rem; background:rgba(10,22,38,.92); border:1px solid #17304a; border-radius:999px; padding:10px 14px; }
              .section-title { margin:12px 0 10px; font-size:1.14rem; font-weight:900; }
              .heatmap-wrap { display:grid; gap:10px; margin-bottom:16px; }
              .stage-row { display:grid; grid-template-columns:72px 28px 1fr; gap:10px; align-items:stretch; }
              .stage-label { writing-mode:vertical-rl; text-orientation:mixed; border:1px solid #17304a; border-radius:14px; background:rgba(10,22,38,.92); color:#4cc3ff; font-weight:900; text-align:center; padding:10px 7px; }
              .stage-arrow { display:grid; place-items:center; color:#7f93b2; font-size:22px; }
              .stage-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; }
              .heat-card { border-left:4px solid #44c7ff; border-radius:16px; padding:12px; color:#f6fbff; box-shadow: inset 0 1px 0 rgba(255,255,255,.03); min-height:116px; }
              .heat-card-name { font-size:.92rem; font-weight:900; line-height:1.25; }
              .heat-card-change { margin-top:.45rem; font-size:1.58rem; font-weight:900; }
              .heat-card-meta { margin-top:.18rem; color:rgba(234,242,255,.82); font-size:.8rem; }
              .leaders { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:10px; margin-bottom:14px; }
              .leader-card { border-radius:16px; padding:12px; border:1px solid rgba(255,255,255,.06); }
              .leader-name { font-size:.92rem; font-weight:900; }
              .leader-code { color:#9fb1cc; font-size:.8rem; margin-left:4px; }
              .leader-change { margin-top:.28rem; font-size:1.3rem; font-weight:900; }
              .leader-meta { margin-top:.2rem; color:rgba(234,242,255,.82); font-size:.78rem; }
              .theme-card { background:linear-gradient(180deg, rgba(13,23,40,.98), rgba(10,20,35,.98)); border:1px solid #17304a; border-radius:20px; overflow:hidden; margin-bottom:14px; }
              .theme-head { display:flex; justify-content:space-between; align-items:center; gap:10px; padding:14px 16px; border-bottom:1px solid rgba(23,48,74,.88); border-left:4px solid #44c7ff; }
              .theme-stage { color:#8aa1ff; font-size:.82rem; font-weight:900; }
              .theme-title { margin-top:4px; font-size:1.28rem; font-weight:900; }
              .theme-kpis { display:flex; align-items:center; gap:10px; }
              .theme-chip { min-width:96px; text-align:center; border-radius:14px; padding:10px 12px; background:rgba(255,255,255,.06); font-size:1rem; font-weight:900; }
              .theme-count { color:#9fb1cc; font-size:.95rem; }
              .table-wrap { overflow:auto; }
              .quote-table { width:100%; min-width:760px; border-collapse:collapse; table-layout:fixed; }
              .quote-table th { background:rgba(8,17,31,.88); color:#9fb1cc; text-align:left; padding:12px 10px; font-size:.92rem; white-space:nowrap; border-bottom:1px solid rgba(23,48,74,.9); }
              .quote-table td { padding:12px 10px; border-bottom:1px solid rgba(23,48,74,.66); vertical-align:middle; font-size:.95rem; }
              .quote-table tbody tr:hover { background:rgba(14,31,49,.34); }
              .code-cell { white-space:nowrap; }
              .code { color:#44c7ff; font-weight:900; font-size:1.08rem; }
              .market { color:#8aa1c2; font-size:.78rem; margin-left:6px; }
              .name-cell { font-weight:900; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
              .role-tag { display:inline-flex; max-width:148px; align-items:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; border:1px solid rgba(68,199,255,.18); background:rgba(16,28,49,.9); color:#8ab8f0; border-radius:12px; padding:6px 10px; font-size:.84rem; }
              .num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
              .up { color:#f43f5e; }
              .down { color:#22c55e; }
              .flat { color:#cbd5e1; }
              .secrets-box { background:rgba(10,22,38,.9); border:1px solid #17304a; border-radius:16px; padding:14px 16px; color:#d9e7fb; font-size:.92rem; line-height:1.65; }
              .secrets-code { display:block; margin-top:8px; padding:10px 12px; background:#0b1524; border:1px solid #17304a; border-radius:12px; color:#8ed7ff; white-space:pre-wrap; }
              @media (max-width: 920px) {
                .hero-title { font-size:1.6rem; }
                .stage-row { grid-template-columns:1fr; }
                .stage-label { writing-mode:horizontal-tb; }
                .stage-arrow { display:none; }
                .theme-title { font-size:1.1rem; }
                .theme-head { padding:12px; }
                .quote-table th, .quote-table td { padding:10px 8px; }
              }
            </style>
            """
        ).strip(),
        unsafe_allow_html=True,
    )


def render_hero(data: dict[str, Any], data_mode: str, filtered_themes: list[dict[str, Any]]) -> None:
    total_volume = sum((theme.get("volume_lots") or 0) for theme in filtered_themes)
    valid_changes = [theme.get("avg_change_pct") for theme in filtered_themes if theme.get("avg_change_pct") is not None]
    avg_change = (sum(valid_changes) / len(valid_changes)) if valid_changes else None
    html_block = dedent(
        f"""
        <section class="hero">
          <div class="hero-left">
            <div class="hero-title">台灣半導體 × AI 盤中熱力圖</div>
            <div class="hero-sub">代表股精簡版｜GitHub 管原始碼，Streamlit Community Cloud 執行｜紅漲綠跌</div>
            <div class="mode-pill">即時來源：{html.escape(data_mode)}</div>
          </div>
          <div class="hero-kpis">
            <div class="hero-kpi">
              <div class="hero-kpi-label">更新時間</div>
              <div class="hero-kpi-value">{html.escape(str(data.get("updated_at", "--")))}</div>
            </div>
            <div class="hero-kpi">
              <div class="hero-kpi-label">平均熱度</div>
              <div class="hero-kpi-value {trend_class(avg_change)}">{fmt_pct(avg_change)}</div>
            </div>
            <div class="hero-kpi">
              <div class="hero-kpi-label">總成交量</div>
              <div class="hero-kpi-value">{fmt_num(total_volume, 0)} 張</div>
            </div>
          </div>
        </section>
        """
    ).strip()
    st.markdown(html_block, unsafe_allow_html=True)


def render_heatmap(filtered_themes: list[dict[str, Any]]) -> None:
    st.markdown('<div class="section-title">產業熱力區</div>', unsafe_allow_html=True)
    rows: list[str] = []
    for stage in ["上游", "中游", "下游"]:
        cards = []
        for theme in filtered_themes:
            if theme["stage"] != stage:
                continue
            cards.append(
                (
                    f'<div class="heat-card" style="background:{heat_color(theme.get("avg_change_pct"))};border-left-color:{theme["accent"]}">'
                    f'<div class="heat-card-name">{html.escape(theme["label"])}</div>'
                    f'<div class="heat-card-change {trend_class(theme.get("avg_change_pct"))}">{fmt_pct(theme.get("avg_change_pct"))}</div>'
                    f'<div class="heat-card-meta">成交量 {fmt_num(theme.get("volume_lots"), 0)} 張</div>'
                    '</div>'
                )
            )
        rows.append(
            (
                '<section class="stage-row">'
                f'<div class="stage-label">{stage}</div>'
                '<div class="stage-arrow">→</div>'
                f'<div class="stage-grid">{"".join(cards)}</div>'
                '</section>'
            )
        )
    st.markdown(f'<div class="heatmap-wrap">{"".join(rows)}</div>', unsafe_allow_html=True)


def render_leaders(filtered_themes: list[dict[str, Any]]) -> None:
    leaders: list[dict[str, Any]] = []
    for theme in filtered_themes:
        for stock in theme["stocks"]:
            leaders.append(
                {
                    "code": stock["code"],
                    "name": stock["name"],
                    "change_pct": stock.get("change_pct"),
                    "volume_lots": stock.get("volume_lots"),
                    "trade_value_m": stock.get("trade_value_m") or 0,
                }
            )
    leaders.sort(key=lambda item: (item["trade_value_m"], item.get("volume_lots") or 0), reverse=True)
    cards = []
    for item in leaders[:8]:
        cards.append(
            (
                f'<div class="leader-card" style="background:{heat_color(item.get("change_pct"))}">'
                f'<div class="leader-name">{html.escape(item["name"])} <span class="leader-code">{item["code"]}</span></div>'
                f'<div class="leader-change {trend_class(item.get("change_pct"))}">{fmt_pct(item.get("change_pct"))}</div>'
                f'<div class="leader-meta">{fmt_num(item.get("trade_value_m"), 0)} 百萬 / {fmt_num(item.get("volume_lots"), 0)} 張</div>'
                '</div>'
            )
        )
    st.markdown('<div class="section-title">即時熱股排行</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="leaders">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_theme_table(theme: dict[str, Any]) -> None:
    table_rows = []
    for stock in theme["stocks"]:
        table_rows.append(
            (
                "<tr>"
                f'<td class="code-cell"><span class="code">{stock["code"]}</span><span class="market">{stock["market"]}</span></td>'
                f'<td class="name-cell">{html.escape(stock["name"])}</td>'
                f'<td><span class="role-tag">{html.escape(theme["label"])}</span></td>'
                f'<td class="num">{fmt_num(stock.get("price"), 2)}</td>'
                f'<td class="num {trend_class(stock.get("change_pct"))}">{fmt_pct(stock.get("change_pct"))}</td>'
                f'<td class="num">{fmt_num(stock.get("volume_lots"), 0)}</td>'
                f'<td class="num">{fmt_num(stock.get("trade_value_m"), 0)}</td>'
                "</tr>"
            )
        )

    card_html = (
        '<section class="theme-card">'
        f'<div class="theme-head" style="border-left-color:{theme["accent"]}">'
        '<div>'
        f'<div class="theme-stage">{theme["stage"]}</div>'
        f'<div class="theme-title">{html.escape(theme["label"])}</div>'
        '</div>'
        '<div class="theme-kpis">'
        f'<span class="theme-chip {trend_class(theme.get("avg_change_pct"))}">{fmt_pct(theme.get("avg_change_pct"))}</span>'
        f'<span class="theme-count">{len(theme["stocks"])} 檔</span>'
        '</div>'
        '</div>'
        '<div class="table-wrap">'
        '<table class="quote-table">'
        '<colgroup>'
        '<col style="width:108px"><col style="width:128px"><col style="width:168px">'
        '<col style="width:104px"><col style="width:102px"><col style="width:110px"><col style="width:118px">'
        '</colgroup>'
        '<thead><tr>'
        '<th>代號</th><th>公司名稱</th><th>主題定位</th><th>最新股價</th><th>漲跌幅</th><th>成交量(張)</th><th>成交值(百萬)</th>'
        '</tr></thead>'
        f'<tbody>{"".join(table_rows)}</tbody>'
        '</table>'
        '</div>'
        '</section>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


def render_secrets_help() -> None:
    st.markdown(
        (
            '<div class="section-title">Streamlit Secrets 正確填法</div>'
            '<div class="secrets-box">'
            '不要貼 <code>```toml</code> 和 <code>```</code>。<br>'
            'Secrets 視窗只接受「純 TOML」內容。<br>'
            '<span class="secrets-code">[fugle]\napi_key = "你的 Fugle API Key"\nuse_snapshot = false</span>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Heatmap", layout="wide", initial_sidebar_state="collapsed")
    render_css()

    raw_data = load_static_data()
    data = normalize_static_payload(raw_data)
    api_key, use_snapshot = get_fugle_settings()

    use_live = st.toggle("使用 Fugle 即時資料", value=bool(api_key))
    data_mode = "尚未設定 Fugle API，顯示靜態資料"
    if use_live and api_key:
        try:
            data = apply_live_quotes(data, api_key, use_snapshot)
            data_mode = "Fugle Snapshot Quotes" if use_snapshot else "Fugle Intraday Quote"
        except Exception as exc:  # noqa: BLE001
            data_mode = f"即時抓取失敗，改用靜態資料：{exc}"

    options = ["全部主題"] + [theme["label"] for theme in data["themes"]]
    selected = st.radio("主題篩選", options, horizontal=True, label_visibility="collapsed")
    filtered_themes = data["themes"] if selected == "全部主題" else [theme for theme in data["themes"] if theme["label"] == selected]

    render_hero(data, data_mode, filtered_themes)
    render_heatmap(filtered_themes)
    render_leaders(filtered_themes)
    st.markdown('<div class="section-title">代表股看盤表</div>', unsafe_allow_html=True)
    for theme in filtered_themes:
        render_theme_table(theme)
    render_secrets_help()


if __name__ == "__main__":
    main()
