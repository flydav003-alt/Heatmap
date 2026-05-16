from __future__ import annotations

import concurrent.futures
import html
import json
import math
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

import streamlit as st


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "docs" / "representative_chain_data.json"

DISPLAY_META = {
    "ASIC": {
        "label": "IC設計 / IP / ASIC",
        "stage": "上游",
        "accent": "#7c3aed",
        "codes": ["2454", "3443", "3035", "5274", "6643"],
    },
    "CoWoS": {
        "label": "先進封裝 / CoWoS",
        "stage": "中游",
        "accent": "#ec4899",
        "codes": ["1560", "3583", "6187", "6640", "3131"],
    },
    "HBM": {
        "label": "記憶體 / HBM",
        "stage": "上游",
        "accent": "#8b5cf6",
        "codes": ["2408", "2337", "8299", "3260", "6531"],
    },
    "SERVER": {
        "label": "AI伺服器 / 機櫃組裝",
        "stage": "下游",
        "accent": "#f43f5e",
        "codes": ["2382", "2317", "3231", "6669", "2356"],
    },
    "THERMAL": {
        "label": "散熱",
        "stage": "中游",
        "accent": "#38bdf8",
        "codes": ["3017", "3324", "3653", "2421"],
    },
    "BBU": {
        "label": "電源 / BBU",
        "stage": "中游",
        "accent": "#f97316",
        "codes": ["2308", "6409", "6412", "6121"],
    },
    "PCB": {
        "label": "PCB / 載板",
        "stage": "中游",
        "accent": "#06b6d4",
        "codes": ["3037", "8046", "2383", "2368", "6274"],
    },
    "CPO": {
        "label": "網通 / 光通訊 / CPO",
        "stage": "下游",
        "accent": "#0ea5e9",
        "codes": ["4979", "4908", "3163", "3450", "3596"],
    },
}

STAGE_ORDER = ["上游", "中游", "下游"]
THEME_ORDER = ["ASIC", "HBM", "CoWoS", "THERMAL", "BBU", "PCB", "SERVER", "CPO"]
MARKET_BY_CODE = {
    "1560": "TSE",
    "2308": "TSE",
    "2317": "TSE",
    "2337": "TSE",
    "2356": "TSE",
    "2382": "TSE",
    "2408": "TSE",
    "2421": "TSE",
    "2454": "TSE",
    "3017": "TSE",
    "3035": "TSE",
    "3037": "TSE",
    "3131": "TSE",
    "3163": "OTC",
    "3231": "TSE",
    "3260": "OTC",
    "3324": "OTC",
    "3443": "TSE",
    "3450": "TSE",
    "3583": "TSE",
    "3596": "TSE",
    "3653": "OTC",
    "4908": "TSE",
    "4979": "OTC",
    "5274": "OTC",
    "6121": "TSE",
    "6187": "TSE",
    "6274": "TSE",
    "6409": "TSE",
    "6412": "TSE",
    "6531": "TSE",
    "6640": "OTC",
    "6643": "OTC",
    "6669": "TSE",
    "8046": "TSE",
    "8299": "OTC",
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


def fetch_intraday_quote(symbol: str, api_key: str) -> dict[str, Any] | None:
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{symbol}"
    try:
        data = fetch_json(url, api_key)
    except urllib.error.URLError:
        return None
    total = data.get("total", {})
    return {
        "code": symbol,
        "name": data.get("name", symbol),
        "price": data.get("lastPrice") or data.get("closePrice") or data.get("referencePrice"),
        "change_pct": data.get("changePercent"),
        "volume_lots": (total.get("tradeVolume") or 0) / 1000,
        "trade_value_m": (total.get("tradeValue") or 0) / 1_000_000,
        "last_updated": data.get("lastUpdated"),
    }


def fetch_snapshot_market(market: str, api_key: str) -> dict[str, dict[str, Any]]:
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/snapshot/quotes/{market}"
    data = fetch_json(url, api_key)
    rows: dict[str, dict[str, Any]] = {}
    for row in data.get("data", []):
        symbol = row.get("symbol", "")
        rows[symbol] = {
            "code": symbol,
            "name": row.get("name", symbol),
            "price": row.get("closePrice"),
            "change_pct": row.get("changePercent"),
            "volume_lots": (row.get("tradeVolume") or 0) / 1000,
            "trade_value_m": (row.get("tradeValue") or 0) / 1_000_000,
            "last_updated": row.get("lastUpdated"),
        }
    return rows


def normalize_theme_alias(theme_name: str, stocks: list[dict[str, Any]]) -> str:
    codes = {stock.get("code", "") for stock in stocks}
    if "ASIC" in theme_name:
        return "ASIC"
    if "CoWoS" in theme_name:
        return "CoWoS"
    if "HBM" in theme_name:
        return "HBM"
    if "CPO" in theme_name:
        return "CPO"
    if "BBU" in theme_name:
        return "BBU"
    if "PCB" in theme_name:
        return "PCB"
    if codes & set(DISPLAY_META["SERVER"]["codes"]):
        return "SERVER"
    if codes & set(DISPLAY_META["THERMAL"]["codes"]):
        return "THERMAL"
    return "ASIC"


def reorder_stocks(alias: str, stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order_map = {code: index for index, code in enumerate(DISPLAY_META[alias]["codes"])}
    return sorted(
        stocks,
        key=lambda stock: (
            order_map.get(stock.get("code", ""), 999),
            -(stock.get("trade_value_m") or 0),
            -(stock.get("volume_lots") or 0),
        ),
    )


def hydrate_live_data(static_data: dict[str, Any], api_key: str, use_snapshot: bool) -> dict[str, Any]:
    stock_codes = sorted(
        {
            code
            for meta in DISPLAY_META.values()
            for code in meta["codes"]
        }
    )

    live_by_code: dict[str, dict[str, Any]] = {}
    if use_snapshot:
        live_by_code = {
            **fetch_snapshot_market("TSE", api_key),
            **fetch_snapshot_market("OTC", api_key),
        }
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(fetch_intraday_quote, code, api_key) for code in stock_codes]
            for future in concurrent.futures.as_completed(futures):
                payload = future.result()
                if payload and payload.get("code"):
                    live_by_code[payload["code"]] = payload

    merged = json.loads(json.dumps(static_data))
    for theme_name, theme in merged["themes"].items():
        alias = normalize_theme_alias(theme_name, theme["stocks"])
        changes = []
        volumes = []
        values = []
        for stock in theme["stocks"]:
            live = live_by_code.get(stock["code"])
            if live:
                stock.update({key: value for key, value in live.items() if value is not None})
            if stock.get("change_pct") is not None:
                changes.append(stock["change_pct"])
            if stock.get("volume_lots") is not None:
                volumes.append(stock["volume_lots"])
            if stock.get("trade_value_m") is not None:
                values.append(stock["trade_value_m"])
        theme["alias"] = alias
        theme["label"] = DISPLAY_META[alias]["label"]
        theme["stage"] = DISPLAY_META[alias]["stage"]
        theme["accent"] = DISPLAY_META[alias]["accent"]
        theme["avg_change_pct"] = sum(changes) / len(changes) if changes else None
        theme["volume_lots"] = sum(volumes) if volumes else None
        theme["trade_value_m"] = sum(values) if values else None
        theme["stocks"] = reorder_stocks(alias, theme["stocks"])
    return merged


def enrich_static_data(static_data: dict[str, Any]) -> dict[str, Any]:
    enriched = json.loads(json.dumps(static_data))
    for theme_name, theme in enriched["themes"].items():
        alias = normalize_theme_alias(theme_name, theme["stocks"])
        theme["alias"] = alias
        theme["label"] = DISPLAY_META[alias]["label"]
        theme["stage"] = DISPLAY_META[alias]["stage"]
        theme["accent"] = DISPLAY_META[alias]["accent"]
        theme["trade_value_m"] = None
        theme["stocks"] = reorder_stocks(alias, theme["stocks"])
    return enriched


def fmt_pct(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"{value:+.2f}%"


def fmt_num(value: float | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"{value:,.{digits}f}"


def trend_class(value: float | None) -> str:
    if value is None:
        return "flat"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def heat_color(value: float | None) -> str:
    if value is None:
        return "rgba(100,116,139,.16)"
    strength = min(abs(value) / 6, 1)
    alpha = 0.16 + strength * 0.44
    if value > 0:
        return f"rgba(244,63,94,{alpha:.3f})"
    if value < 0:
        return f"rgba(34,197,94,{alpha:.3f})"
    return "rgba(251,191,36,.20)"


def collect_display_themes(data: dict[str, Any]) -> list[dict[str, Any]]:
    themes: list[dict[str, Any]] = []
    for _, theme in data["themes"].items():
        themes.append(theme)
    alias_seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for alias in THEME_ORDER:
        for theme in themes:
            if theme["alias"] == alias and alias not in alias_seen:
                deduped.append(theme)
                alias_seen.add(alias)
                break
    return deduped


def build_heatmap_html(themes: list[dict[str, Any]]) -> str:
    stage_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for theme in themes:
        stage_groups[theme["stage"]].append(theme)

    blocks: list[str] = []
    for stage in STAGE_ORDER:
        rows = []
        for theme in stage_groups.get(stage, []):
            rows.append(
                f"""
                <div class="heat-card" style="background:{heat_color(theme.get('avg_change_pct'))}; border-left-color:{theme['accent']}">
                  <div class="heat-card-name">{html.escape(theme['label'])}</div>
                  <div class="heat-card-change {trend_class(theme.get('avg_change_pct'))}">{fmt_pct(theme.get('avg_change_pct'))}</div>
                  <div class="heat-card-meta">成交量 {fmt_num(theme.get('volume_lots'), 0)} 張</div>
                </div>
                """
            )
        blocks.append(
            f"""
            <section class="stage-row">
              <div class="stage-label">{stage}</div>
              <div class="stage-arrow">→</div>
              <div class="stage-grid">{''.join(rows)}</div>
            </section>
            """
        )
    return "".join(blocks)


def build_leader_cards_html(themes: list[dict[str, Any]]) -> str:
    leaders: list[dict[str, Any]] = []
    for theme in themes:
        for stock in theme["stocks"]:
            leaders.append(
                {
                    "theme": theme["label"],
                    "code": stock["code"],
                    "name": stock.get("name", stock["code"]),
                    "change_pct": stock.get("change_pct"),
                    "trade_value_m": stock.get("trade_value_m"),
                    "volume_lots": stock.get("volume_lots"),
                }
            )
    leaders.sort(key=lambda item: ((item.get("trade_value_m") or 0), (item.get("volume_lots") or 0)), reverse=True)
    top_items = leaders[:8]
    cards = []
    for item in top_items:
        cards.append(
            f"""
            <div class="leader-card" style="background:{heat_color(item.get('change_pct'))}">
              <div class="leader-head">{html.escape(item['name'])} <span>{item['code']}</span></div>
              <div class="leader-change {trend_class(item.get('change_pct'))}">{fmt_pct(item.get('change_pct'))}</div>
              <div class="leader-meta">{fmt_num(item.get('trade_value_m'), 0)} 百萬 / {fmt_num(item.get('volume_lots'), 0)} 張</div>
            </div>
            """
        )
    return "".join(cards)


def build_theme_section_html(theme: dict[str, Any]) -> str:
    rows = []
    for stock in theme["stocks"]:
        market = "上市" if MARKET_BY_CODE.get(stock["code"]) == "TSE" else "上櫃"
        rows.append(
            f"""
            <tr>
              <td class="code-cell"><span class="code">{stock['code']}</span><span class="market">{market}</span></td>
              <td class="name-cell">{html.escape(str(stock.get('name', stock['code'])))}</td>
              <td class="tag-cell"><span class="role-tag">{html.escape(theme['label'])}</span></td>
              <td class="num">{fmt_num(stock.get('price'), 2)}</td>
              <td class="num {trend_class(stock.get('change_pct'))}">{fmt_pct(stock.get('change_pct'))}</td>
              <td class="num">{fmt_num(stock.get('volume_lots'), 0)}</td>
              <td class="num">{fmt_num(stock.get('trade_value_m'), 0)}</td>
            </tr>
            """
        )
    return f"""
    <section class="theme-card">
      <div class="theme-head" style="border-left-color:{theme['accent']}">
        <div>
          <div class="theme-stage">{theme['stage']}</div>
          <div class="theme-title">{html.escape(theme['label'])}</div>
        </div>
        <div class="theme-kpis">
          <span class="theme-chip {trend_class(theme.get('avg_change_pct'))}">{fmt_pct(theme.get('avg_change_pct'))}</span>
          <span class="theme-count">{len(theme['stocks'])} 檔</span>
        </div>
      </div>
      <div class="quote-wrap">
        <table class="quote-table">
          <colgroup>
            <col style="width:108px">
            <col style="width:128px">
            <col style="width:168px">
            <col style="width:104px">
            <col style="width:102px">
            <col style="width:110px">
            <col style="width:118px">
          </colgroup>
          <thead>
            <tr>
              <th>代號</th>
              <th>公司名稱</th>
              <th>主題定位</th>
              <th>最新股價</th>
              <th>漲跌幅</th>
              <th>成交量(張)</th>
              <th>成交值(百萬)</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows)}
          </tbody>
        </table>
      </div>
    </section>
    """


st.set_page_config(page_title="Heatmap", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
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
      [data-testid="stSidebar"] { background: #0b1524; }
      .hero { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom: 14px; flex-wrap:wrap; }
      .hero-title { font-size: 2rem; font-weight: 900; line-height: 1.1; margin: 0; }
      .hero-sub { color:#8aa1c2; font-size:.9rem; margin-top:.28rem; letter-spacing:.03em; }
      .hero-kpis { display:flex; gap:10px; flex-wrap:wrap; }
      .hero-kpi {
        min-width: 118px; background: rgba(10,22,38,.92); border:1px solid #17304a;
        border-radius: 16px; padding: 12px 14px;
      }
      .hero-kpi-label { color:#7f93b2; font-size:.76rem; }
      .hero-kpi-value { margin-top: 6px; font-size: 1.18rem; font-weight: 900; }
      .control-bar {
        display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap;
        margin: 12px 0 16px;
      }
      .data-mode {
        color:#7f93b2; font-size:.86rem; background: rgba(10,22,38,.92);
        border:1px solid #17304a; border-radius:999px; padding:10px 14px;
      }
      .stage-row { display:grid; grid-template-columns:72px 28px 1fr; gap:10px; align-items:stretch; margin-bottom:10px; }
      .stage-label {
        writing-mode: vertical-rl; text-orientation:mixed; border:1px solid #17304a; border-radius:14px;
        background: rgba(10,22,38,.92); color:#4cc3ff; font-weight:900; text-align:center; padding:10px 7px;
      }
      .stage-arrow { display:grid; place-items:center; color:#7f93b2; font-size:22px; }
      .stage-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; }
      .heat-card {
        border-left:4px solid #44c7ff; border-radius:16px; padding:12px; color:#f6fbff;
        box-shadow: inset 0 1px 0 rgba(255,255,255,.03);
      }
      .heat-card-name { font-size:.92rem; font-weight:900; line-height:1.25; }
      .heat-card-change { margin-top:.45rem; font-size:1.6rem; font-weight:900; }
      .heat-card-meta { margin-top:.15rem; color:rgba(234,242,255,.82); font-size:.8rem; }
      .leader-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(168px,1fr)); gap:10px; margin: 14px 0 16px; }
      .leader-card { border-radius:16px; padding:12px; border:1px solid rgba(255,255,255,.06); }
      .leader-head { font-size:.92rem; font-weight:900; }
      .leader-head span { color:#9fb1cc; font-size:.8rem; margin-left:4px; }
      .leader-change { margin-top:.3rem; font-size:1.35rem; font-weight:900; }
      .leader-meta { margin-top:.2rem; color:rgba(234,242,255,.8); font-size:.78rem; }
      .section-title { margin: 10px 0 10px; font-size: 1.15rem; font-weight: 900; }
      .theme-card {
        background: linear-gradient(180deg, rgba(13,23,40,.98), rgba(10,20,35,.98));
        border:1px solid #17304a; border-radius:20px; overflow:hidden; margin-bottom:14px;
      }
      .theme-head {
        display:flex; justify-content:space-between; align-items:center; gap:10px;
        padding:14px 16px; border-bottom:1px solid rgba(23,48,74,.88); border-left:4px solid #44c7ff;
      }
      .theme-stage { color:#8aa1ff; font-size:.82rem; font-weight:900; }
      .theme-title { margin-top:4px; font-size:1.35rem; font-weight:900; }
      .theme-kpis { display:flex; align-items:center; gap:10px; }
      .theme-chip {
        min-width:90px; text-align:center; border-radius:14px; padding:10px 12px;
        background:rgba(255,255,255,.06); font-size:1rem; font-weight:900;
      }
      .theme-count { color:#9fb1cc; font-size:.95rem; }
      .quote-wrap { overflow:auto; }
      .quote-table {
        width:100%; min-width:760px; border-collapse:collapse; table-layout:fixed;
      }
      .quote-table th {
        background:rgba(8,17,31,.88); color:#9fb1cc; text-align:left; padding:12px 10px;
        font-size:.92rem; white-space:nowrap; border-bottom:1px solid rgba(23,48,74,.9);
      }
      .quote-table td {
        padding:12px 10px; border-bottom:1px solid rgba(23,48,74,.66); vertical-align:middle;
        font-size:.95rem;
      }
      .quote-table tbody tr:hover { background:rgba(14,31,49,.36); }
      .code-cell { white-space:nowrap; }
      .code { color:#44c7ff; font-weight:900; font-size:1.08rem; }
      .market { color:#8aa1c2; font-size:.78rem; margin-left:6px; }
      .name-cell { font-weight:900; font-size:1rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .role-tag {
        display:inline-flex; max-width:148px; align-items:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
        border:1px solid rgba(68,199,255,.18); background:rgba(16,28,49,.9); color:#8ab8f0;
        border-radius:12px; padding:6px 10px; font-size:.84rem;
      }
      .num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
      .up { color:#f43f5e; }
      .down { color:#22c55e; }
      .flat { color:#cbd5e1; }
      .help-box {
        background: rgba(10,22,38,.9); border:1px solid #17304a; border-radius:16px;
        padding:14px 16px; color:#d9e7fb; font-size:.92rem; line-height:1.65;
      }
      .help-box code { color:#8ed7ff; }
      @media (max-width: 920px) {
        .hero-title { font-size: 1.6rem; }
        .stage-row { grid-template-columns:1fr; }
        .stage-label { writing-mode: horizontal-tb; }
        .stage-arrow { display:none; }
        .theme-title { font-size:1.15rem; }
        .theme-head { padding:12px; }
        .quote-table th, .quote-table td { padding:10px 8px; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

static_data = enrich_static_data(load_static_data())
api_key, use_snapshot = get_fugle_settings()
enable_live = st.toggle("使用 Fugle 即時資料", value=bool(api_key))
data_mode = "靜態收盤資料"
provider_label = "Fugle Snapshot Quotes" if use_snapshot else "Fugle Intraday Quote"

if enable_live and api_key:
    try:
        data = hydrate_live_data(static_data, api_key, use_snapshot)
        data_mode = provider_label
    except Exception as exc:  # noqa: BLE001
        data = static_data
        data_mode = f"改用靜態資料：{exc}"
else:
    data = static_data

themes = collect_display_themes(data)
theme_labels = ["全部主題"] + [theme["label"] for theme in themes]
selected_label = st.radio("主題篩選", theme_labels, horizontal=True, label_visibility="collapsed")

filtered_themes = themes if selected_label == "全部主題" else [theme for theme in themes if theme["label"] == selected_label]
total_volume = sum((theme.get("volume_lots") or 0) for theme in filtered_themes)
valid_changes = [theme.get("avg_change_pct") for theme in filtered_themes if theme.get("avg_change_pct") is not None]
avg_change = sum(valid_changes) / len(valid_changes) if valid_changes else None

st.markdown(
    f"""
    <section class="hero">
      <div>
        <div class="hero-title">台灣半導體 × AI 盤中熱力圖</div>
        <div class="hero-sub">代表股精簡版｜GitHub 管原始碼，Streamlit Community Cloud 執行｜紅漲綠跌</div>
      </div>
      <div class="hero-kpis">
        <div class="hero-kpi">
          <div class="hero-kpi-label">資料模式</div>
          <div class="hero-kpi-value">{html.escape(data_mode)}</div>
        </div>
        <div class="hero-kpi">
          <div class="hero-kpi-label">更新時間</div>
          <div class="hero-kpi-value">{html.escape(str(data.get('updated_at', '--')))}</div>
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
    <div class="control-bar">
      <div class="data-mode">即時來源：{html.escape(provider_label if api_key else '尚未設定 Fugle API，顯示靜態資料')}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(build_heatmap_html(filtered_themes), unsafe_allow_html=True)
st.markdown('<div class="section-title">即時熱股排行</div>', unsafe_allow_html=True)
st.markdown(f'<div class="leader-grid">{build_leader_cards_html(filtered_themes)}</div>', unsafe_allow_html=True)
st.markdown('<div class="section-title">代表股看盤表</div>', unsafe_allow_html=True)

for theme in filtered_themes:
    st.markdown(build_theme_section_html(theme), unsafe_allow_html=True)

with st.expander("Fugle API 要填哪裡"):
    st.markdown(
        """
        <div class="help-box">
          你在 Streamlit Community Cloud 不需要把 API key 寫進 GitHub repo。<br>
          只要到 App 設定的 <code>Secrets</code> 貼上以下內容即可：<br><br>
          <code>[fugle]<br>api_key = "你的 Fugle API Key"<br>use_snapshot = false</code><br><br>
          <b>說明</b><br>
          1. <code>use_snapshot = false</code>：逐檔抓代表股，較適合現在這版。<br>
          2. <code>use_snapshot = true</code>：一次抓市場快照，較適合更完整熱力圖，但通常需要較高方案。
        </div>
        """,
        unsafe_allow_html=True,
    )
