from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf


ROOT      = Path(__file__).resolve().parent
HTML_PATH = ROOT / "docs" / "index.html"
LOCAL_HTML_PATH = ROOT / "index.html"

# ── Helpers ────────────────────────────────────────────────────────────────────
def parse_float(value: Any) -> float | None:
    if value in (None, "", "-", "--", "---", "----", "N/A"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def extract_symbols(base_html: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r'<tr data-code="(?P<code>\d{4})"[\s\S]*?'
        r'class="mkt-badge">(?P<market>.*?)</span>',
        re.S,
    )
    seen: set[str] = set()
    symbols: list[dict[str, str]] = []
    for m in pattern.finditer(base_html):
        code = m.group("code")
        if code in seen:
            continue
        seen.add(code)
        market_text = re.sub(r"<.*?>", "", html.unescape(m.group("market")))
        exchange = "otc" if "櫃" in market_text else "tse"
        symbols.append({"code": code, "exchange": exchange})
    return symbols


# ── 批次下載報價 + 5日歷史（供折線圖）────────────────────────────────────────
@st.cache_data(ttl=180, show_spinner=False)
def fetch_all_quotes(symbols: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    import pandas as pd

    code_to_exchange = {code: exchange for code, exchange in symbols}
    all_codes = list(code_to_exchange.keys())
    tw_tickers  = [f"{c}.TW"  for c in all_codes]
    two_tickers = [f"{c}.TWO" for c in all_codes]

    quotes: dict[str, dict[str, Any]] = {}
    TW = timezone(timedelta(hours=8))
    now = datetime.now(TW)
    latest_time = now.strftime("%H:%M:%S")
    latest_date = now.strftime("%Y-%m-%d")

    def _batch_download(tickers: list[str], period: str = "45d") -> Any:
        try:
            return yf.download(
                tickers=tickers, period=period,
                group_by="ticker", auto_adjust=True,
                progress=False, threads=True,
            )
        except Exception as exc:
            print(f"[yf] 批次下載失敗: {exc}")
            return None

    def _extract_quote(raw: Any, ticker: str) -> dict[str, Any] | None:
        try:
            if raw is None or raw.empty:
                return None
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    return None
                df = raw[ticker]
            else:
                df = raw
            if df is None or df.empty:
                return None
            close  = df["Close"].dropna()
            volume = df["Volume"].dropna()
            if len(close) < 1:
                return None
            price = float(close.iloc[-1])
            if price == 0 or not pd.notna(price):
                return None
            prev_close = float(close.iloc[-2]) if len(close) >= 2 else None
            chg_pct = ((price / prev_close - 1) * 100) if prev_close and prev_close != 0 else None
            vol_shares = float(volume.iloc[-1]) if len(volume) >= 1 else None
            vol_lots   = vol_shares / 1000 if vol_shares else None
            vol_tail = volume.tail(20)
            avg_vol20 = float(vol_tail.mean() / 1000) if len(vol_tail) >= 5 else None
            if len(close) >= 11:
                p5_close = float(close.iloc[-11])
            elif len(close) >= 2:
                p5_close = float(close.iloc[0])
            else:
                p5_close = None
            close_tail = close.tail(20)
            p20_high = float(close_tail.max()) if len(close_tail) >= 1 else None

            # 10日收盤序列（供折線圖）: [{date, close}, ...]
            history_5d = []
            close_tail = close.tail(10)
            for ts, v in close_tail.items():
                try:
                    d = ts.strftime("%m/%d") if hasattr(ts, "strftime") else str(ts)[:10]
                    history_5d.append({"d": d, "c": round(float(v), 2)})
                except Exception:
                    pass

            return {
                "price":       price,
                "change_pct":  chg_pct,
                "volume_lots": vol_lots,
                "avg_vol20":   avg_vol20,
                "p5_close":    p5_close,
                "p20_high":    p20_high,
                "history_5d":  history_5d,
                "time":        latest_time,
                "date":        latest_date,
            }
        except Exception:
            return None

    def _extract_history_5d(raw: Any, ticker: str) -> list[dict[str, Any]]:
        try:
            if raw is None or raw.empty:
                return []
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    return []
                df = raw[ticker]
            else:
                df = raw
            close = df["Close"].dropna()
            history_5d = []
            for ts, v in close.tail(10).items():
                d = ts.strftime("%m/%d") if hasattr(ts, "strftime") else str(ts)[:10]
                history_5d.append({"d": d, "c": round(float(v), 2)})
            return history_5d
        except Exception:
            return []

    # period="45d" 同時供即時報價、20日均量、5日輪動與20日高點使用
    raw_tw  = _batch_download(tw_tickers,  period="45d")
    raw_two = _batch_download(two_tickers, period="45d")
    raw_idx = _batch_download(["^TWII"], period="45d")
    market_history_5d = _extract_history_5d(raw_idx, "^TWII")

    for code in all_codes:
        q = _extract_quote(raw_tw,  f"{code}.TW")
        if q is None:
            q = _extract_quote(raw_two, f"{code}.TWO")
        if q is not None:
            quotes[code] = q

    return {
        "quotes":          quotes,
        "latest_time":     latest_time,
        "latest_date":     latest_date,
        "fetched_count":   len(quotes),
        "requested_count": len(symbols),
        "market_history_5d": market_history_5d,
        "errors":          [],
    }


def estimate_page_height(base_html: str) -> int:
    num_rows   = len(re.findall(r"<tr\s+data-code=", base_html))
    num_groups = len(re.findall(r'class="group-card"', base_html))
    # Initial iframe height only. The injected ResizeObserver below reports the
    # exact content height after render where Streamlit accepts the message. Keep
    # the fallback generous enough to avoid clipping, without the old huge tail.
    return 860 + num_groups * 108 + num_rows * 42 + 16


# ── GROUP META ─────────────────────────────────────────────────────────────────
GROUP_STAGE_MAP = {
    "矽智財與高階 ASIC":       "上游",
    "消費性 / 常規 IC設計":    "上游",
    "晶圓代工 / 功率半導體":    "上游",
    "記憶體 / HBM":             "上游",
    "電源管理 / 類比 IC":        "上游",
    "矽晶圓 / 材料設備 / 廠務": "上游",
    "先進封裝 / CoWoS":         "中游",
    "封測 / 測試介面":           "中游",
    "PCB / 載板 / CCL":         "中游",
    "被動元件":                  "中游",
    "散熱":                      "中游",
    "電源 / BBU":                "中游",
    "高速互連 / 連接器 / 線材": "中游",
    "AI伺服器 / 機櫃組裝":      "下游",
    "CPO / 矽光子":              "下游",
    "網通 / 交換器 / 寬頻設備": "下游",
    "光學 / 影像 / 顯示":       "下游",
    "低軌衛星 / SpaceX":        "下游",
}
GROUP_COLOR_MAP = {
    "矽智財與高階 ASIC":       "#8b5cf6",
    "消費性 / 常規 IC設計":    "#a78bfa",
    "晶圓代工 / 功率半導體":    "#3b82f6",
    "先進封裝 / CoWoS":         "#ec4899",
    "封測 / 測試介面":           "#f59e0b",
    "記憶體 / HBM":              "#7c3aed",
    "電源管理 / 類比 IC":         "#84cc16",
    "矽晶圓 / 材料設備 / 廠務": "#10b981",
    "PCB / 載板 / CCL":          "#06b6d4",
    "被動元件":                   "#a855f7",
    "AI伺服器 / 機櫃組裝":       "#f43f5e",
    "散熱":                       "#38bdf8",
    "電源 / BBU":                 "#f97316",
    "CPO / 矽光子":               "#0ea5e9",
    "網通 / 交換器 / 寬頻設備":  "#0284c7",
    "光學 / 影像 / 顯示":        "#14b8a6",
    "低軌衛星 / SpaceX":         "#6366f1",
    "高速互連 / 連接器 / 線材":  "#22c55e",
}

CORE_UPSTREAM     = {"矽智財與高階 ASIC", "晶圓代工 / 功率半導體"}
LAGGARD_GROUPS    = {"被動元件"}
DOWNSTREAM_GROUPS = {"AI伺服器 / 機櫃組裝", "CPO / 矽光子"}


# ── 建立「個股群組對應」快取（從 HTML 解析）──────────────────────────────────
def extract_stock_groups(base_html: str) -> dict[str, str]:
    """回傳 {code: group_name}"""
    pattern = re.compile(r'<tr data-code="(\d{4})"[^>]*data-group="([^"]+)"')
    return {m.group(1): html.unescape(m.group(2)) for m in pattern.finditer(base_html)}


def inject_live_script(base_html: str, payload: dict[str, Any],
                       stock_groups: dict[str, str]) -> str:

    live_json        = json.dumps(payload, ensure_ascii=False)
    stage_map_json   = json.dumps(GROUP_STAGE_MAP, ensure_ascii=False)
    color_map_json   = json.dumps(GROUP_COLOR_MAP, ensure_ascii=False)
    core_up_json     = json.dumps(list(CORE_UPSTREAM), ensure_ascii=False)
    laggard_json     = json.dumps(list(LAGGARD_GROUPS), ensure_ascii=False)
    downstream_json  = json.dumps(list(DOWNSTREAM_GROUPS), ensure_ascii=False)
    stock_groups_json = json.dumps(stock_groups, ensure_ascii=False)

    script = f"""
<script>
(() => {{
  "use strict";
  const payload     = {live_json};
  const quotes      = payload.quotes || {{}};
  const MARKET_HISTORY_5D = payload.market_history_5d || [];
  const GROUP_STAGE = {stage_map_json};
  const GROUP_COLOR = {color_map_json};
  const STOCK_GROUP = {stock_groups_json};
  const CORE_UP     = new Set({core_up_json});
  const LAGGARD_GRP = new Set({laggard_json});
  const DOWNSTREAM  = new Set({downstream_json});

  /* ── 格式化工具 ──────────────────────────────────────────────────────────── */
  const fmtPrice = v => (v==null||!isFinite(+v)) ? "--"
    : (+v).toLocaleString("en-US",{{minimumFractionDigits:2,maximumFractionDigits:2}});
  const fmtInt   = v => (v==null||!isFinite(+v)) ? "--" : Math.round(+v).toLocaleString("en-US");

  // 成交額億元格式化：張數 × 股價 / 1e5（張數已是千股，再 ×1000 → 股，×price / 1e8 = 億）
  // volume_lots 單位是張，price 單位是元
  // 億元 = volume_lots(張) × 1000(股/張) × price / 1e8 = volume_lots × price / 1e5
  const fmtYi = (lots, price) => {{
    if(lots==null||!isFinite(+lots)||price==null||!isFinite(+price)) return "--";
    const yi=(+lots)*(+price)/1e5;
    if(yi<0.1)  return "<0.1億";
    if(yi<10)   return yi.toFixed(1)+"億";
    return Math.round(yi)+"億";
  }};
  const fmtPct   = v => {{
    if(v==null||!isFinite(+v)) return "--";
    return (+v>0?"+":"")+( +v).toFixed(2)+"%";
  }};
  const trend = v => (v==null||!isFinite(+v))?"na": +v>0?"up": +v<0?"down":"flat";
  const heat  = v => {{
    if(v==null||!isFinite(+v)) return "rgba(12,22,40,0.25)";
    const s=Math.min(Math.abs(+v)/6,1), a=(0.14+s*0.46).toFixed(3);
    return +v>0?`rgba(255,45,84,${{a}})`:`rgba(0,210,110,${{a}})`;
  }};
  const safe = v => {{ const n=parseFloat(v); return (isFinite(n)&&n!==0)?n:null; }};

  /* ── 主迴圈：掃描個股 → 累計族群統計 ───────────────────────────────────── */
  const groupStats = new Map();
  // 額外儲存個股資料，供「入場雷達」使用
  const stockData = [];
  let totalChange=0, totalCount=0, totalVolume=0;

  document.querySelectorAll("tr[data-code]").forEach(row => {{
    const code  = row.dataset.code;
    const quote = quotes[code];

    let price     = (quote&&quote.price     !=null)?+quote.price     :null;
    let changePct = (quote&&quote.change_pct!=null)?+quote.change_pct:null;
    let volume    = (quote&&quote.volume_lots!=null)?+quote.volume_lots:null;

    if(!isFinite(price))     price=null;
    if(!isFinite(changePct)) changePct=null;
    if(!isFinite(volume))    volume=null;
    if(changePct==null){{const r=parseFloat(row.dataset.change);if(isFinite(r))changePct=r;}}

    if(price    !=null) row.dataset.price =price;
    if(changePct!=null) row.dataset.change=changePct;
    if(volume   !=null) row.dataset.volume=volume;

    const nums=row.querySelectorAll("td.num");
    if(price    !=null&&nums[0]) nums[0].textContent=fmtPrice(price);
    if(changePct!=null&&nums[1]){{nums[1].textContent=fmtPct(changePct);nums[1].className=`num ${{trend(changePct)}}`;}}
    if(volume   !=null&&nums[2]) nums[2].textContent=fmtYi(volume, price);

    let avgVol20=safe(row.dataset.avgVol20);
    let p5close =safe(row.dataset.p5Close);
    let p20high =safe(row.dataset.p20High);
    if(avgVol20==null && quote && quote.avg_vol20!=null && isFinite(+quote.avg_vol20)) avgVol20=+quote.avg_vol20;
    if(p5close ==null && quote && quote.p5_close !=null && isFinite(+quote.p5_close))  p5close =+quote.p5_close;
    if(p20high ==null && quote && quote.p20_high !=null && isFinite(+quote.p20_high))  p20high =+quote.p20_high;

    // history_5d 讀取優先順序：
    // 1. build 預埋的 data-history-5d（最穩定，每天 build 時固定）
    // 2. 盤中 quote.history_5d（streamlit 當日即時抓取的 fallback）
    let hist5d = null;
    try {{
      const raw5d = row.dataset.history5d;  // data-history-5d → camelCase: history5d
      if (raw5d && raw5d.length > 4) hist5d = JSON.parse(raw5d);
    }} catch(e) {{}}
    if (!hist5d && quote && quote.history_5d && quote.history_5d.length >= 2) {{
      hist5d = quote.history_5d;
    }}

    const group=row.dataset.group||"";
    if(!groupStats.has(group)) groupStats.set(group,{{
      sum:0,count:0,volume:0,upCount:0,totalCount:0,
      sumAvgVol20:0,cntVol20:0,sumPrice:0,cntPrice:0,
      sumP5close:0,cntP5:0,sumP20high:0,cntP20:0,
      history5d:[],   // 族群每日均漲幅序列 (10日)
    }});
    const stat=groupStats.get(group);

    if(changePct!=null){{
      stat.sum+=changePct; stat.count++; stat.totalCount++;
      if(changePct>0)stat.upCount++;
      totalChange+=changePct; totalCount++;
    }}
    if(volume!=null){{stat.volume+=volume; totalVolume+=volume;}}
    if(avgVol20!=null){{stat.sumAvgVol20+=avgVol20;stat.cntVol20++;}}
    if(price   !=null){{stat.sumPrice+=price;stat.cntPrice++;}}
    if(p5close !=null){{stat.sumP5close+=p5close;stat.cntP5++;}}
    if(p20high !=null){{stat.sumP20high+=p20high;stat.cntP20++;}}

    // 收集個股歷史（供折線圖累計族群均漲幅）
    // hist5d 已在上方解析（build預埋優先 > 盤中fallback）
    if(hist5d&&hist5d.length>=2&&hist5d[0]&&hist5d[0].c>0){{
      stat.history5d.push(hist5d);
    }}

    // 存個股資料供入場雷達
    stockData.push({{
      code, group,
      name: row.dataset.name||code,
      price, changePct, volume,
      avgVol20, p5close, p20high,
    }});
  }});

  /* ── 台灣時間（JS 瀏覽器直取，不依賴 Python cache 的 latest_time）──────── */
  const _nowTW  = new Date(new Date().toLocaleString("en-US", {{timeZone:"Asia/Taipei"}}));
  const _twHour = _nowTW.getHours();
  const _twMin  = _nowTW.getMinutes();
  const _twDay  = _nowTW.getDay(); // 0=日,6=六
  const _twTimeStr = `${{String(_twHour).padStart(2,"0")}}:${{String(_twMin).padStart(2,"0")}}`;
  const _isTradingDay  = _twDay >= 1 && _twDay <= 5;
  const _minsSinceOpen = (_twHour - 9) * 60 + _twMin;
  const _isIntraday    = _isTradingDay && _minsSinceOpen >= 0 && _minsSinceOpen < 270;
  const _isAfterClose  = _isTradingDay && _minsSinceOpen >= 270;

  /* ── 盤中量能修正係數（優先用板塊實際進度，fallback 時間推估）────────── */
  //
  // 【方法 A】板塊實際進度法（主要方法）
  //   原理：今日全板塊已成交量 ÷ 全板塊20日均量 = 板塊完成進度
  //   例：10:30 板塊已跑到昨日全日的 35% → 係數 = 1/0.35 = 2.86x
  //   優點：天然反映 U 型量能分佈，開盤大量→高係數低，午盤縮量→係數適度放大
  //         所有族群用同一基準，族群間相對強弱才有意義
  //
  // 【方法 B】時間線性推估法（fallback）
  //   當板塊均量資料不足時（avgVol20 覆蓋率 < 30%）才啟用
  //   公式：270 / (已過分鐘 × 0.9)，上限 4x
  //   缺點：假設量能平均分佈，早盤會高估
  //
  let _intradayFactor = 1.0;
  let _factorMethod   = "盤後/非交易日（係數=1）";

  if (_isIntraday) {{
    // 計算全板塊今日已成交量（張）vs 20日均量加總
    let _totalVolToday = 0;   // 今日已成交（來自 quotes，盤中即時）
    let _totalAvgVol20 = 0;   // 20日均量加總（來自 build 預埋 data-avg-vol20）
    let _avgVol20Count = 0;   // 有 avgVol20 資料的個股數
    let _totalStockCount = 0; // 所有個股數

    document.querySelectorAll("tr[data-code]").forEach(row => {{
      _totalStockCount++;
      const q = quotes[row.dataset.code];
      if (q && q.volume_lots != null && isFinite(+q.volume_lots)) {{
        _totalVolToday += +q.volume_lots;
      }}
      let av = parseFloat(row.dataset.avgVol20);
      const qAvg = q && q.avg_vol20!=null && isFinite(+q.avg_vol20) ? +q.avg_vol20 : null;
      if(!isFinite(av) && qAvg!=null) av = qAvg;
      if (isFinite(av) && av > 0) {{
        _totalAvgVol20 += av;
        _avgVol20Count++;
      }}
    }});

    // avgVol20 覆蓋率：有資料的股數 / 總股數
    const _avgVol20Coverage = _totalStockCount > 0 ? _avgVol20Count / _totalStockCount : 0;
    // 板塊今日進度：今日量 / 20日均量（代表今天跑到幾成）
    const _boardProgress = (_totalAvgVol20 > 0 && _totalVolToday > 0)
      ? _totalVolToday / _totalAvgVol20
      : null;

    if (_boardProgress != null && _avgVol20Coverage >= 0.3) {{
      // 【方法 A】板塊實際進度：係數 = 1 / 進度，上限 5x（防開盤前幾分鐘爆炸）
      _intradayFactor = Math.min(1 / _boardProgress, 5.0);
      _factorMethod   = `板塊進度法（今日/均量=${{(_boardProgress*100).toFixed(1)}}% avgVol覆蓋${{(_avgVol20Coverage*100).toFixed(0)}}%）`;
    }} else {{
      // 【方法 B】時間線性推估 fallback
      _intradayFactor = Math.min(270 / (_minsSinceOpen * 0.9 + 1), 4.0);
      _factorMethod   = `時間推估法fallback（avgVol覆蓋率${{(_avgVol20Coverage*100).toFixed(0)}}%不足）`;
    }}
  }}

  const _sessionLabel = !_isTradingDay ? "非交易日"
    : _minsSinceOpen < 0 ? "盤前"
    : _isIntraday        ? `盤中 ${{_twTimeStr}}`
    : "盤後收盤";

  /* ── 第一輪：收集各族群原始指標 ────────────────────────────────────────── */
  // 先把所有族群的指標算完，才能算「相對板塊」的強弱
  const groupRaw = new Map();
  let _boardAvgChange = 0, _boardChangeCount = 0;
  let _boardAvgBreadth = 0, _boardBreadthCount = 0;
  let _boardAvgVolRatio = 0, _boardVolRatioCount = 0;

  groupStats.forEach((stat,group)=>{{
    if(!stat.count) return;
    const avgChange = stat.sum / stat.count;
    const breadth   = stat.totalCount>0 ? (stat.upCount/stat.totalCount)*100 : null;
    const avgVol20PerStock = stat.cntVol20>0 ? stat.sumAvgVol20/stat.cntVol20 : null;
    const totalStocks = stat.totalCount||stat.count||1;
    const estVol20 = avgVol20PerStock!=null ? avgVol20PerStock*totalStocks : null;
    const adjVol   = stat.volume * _intradayFactor;
    const volRatio    = (estVol20!=null&&estVol20>0&&stat.volume>0) ? stat.volume/estVol20    : null;
    const volRatioAdj = (estVol20!=null&&estVol20>0&&stat.volume>0) ? adjVol/estVol20         : null;
    const vr4grade    = _isIntraday ? volRatioAdj : volRatio;
    const hasVol      = volRatio!=null;
    const avgPrice   = stat.cntPrice>0 ? stat.sumPrice/stat.cntPrice   : null;
    const avgP5close = stat.cntP5>0   ? stat.sumP5close/stat.cntP5    : null;
    const avgP20high = stat.cntP20>0  ? stat.sumP20high/stat.cntP20   : null;
    const mom5d      = (avgPrice!=null&&avgP5close!=null&&avgP5close>0) ? (avgPrice/avgP5close-1)*100 : null;
    const drawdown   = (avgPrice!=null&&avgP20high!=null&&avgP20high>0) ? (avgPrice/avgP20high-1)*100 : null;

    groupRaw.set(group, {{avgChange,breadth,volRatio,volRatioAdj,vr4grade,hasVol,mom5d,drawdown,stat}});

    // 累積板塊均值（排除「補充」分類，避免半導體其他拉歪基準）
    if(GROUP_STAGE[group] && GROUP_STAGE[group]!=="補充"){{
      _boardAvgChange += avgChange; _boardChangeCount++;
      if(breadth!=null){{ _boardAvgBreadth+=breadth; _boardBreadthCount++; }}
      if(vr4grade!=null){{ _boardAvgVolRatio+=vr4grade; _boardVolRatioCount++; }}
    }}
  }});

  // 板塊基準值
  const _bChange  = _boardChangeCount>0  ? _boardAvgChange/_boardChangeCount   : 0;
  const _bBreadth = _boardBreadthCount>0 ? _boardAvgBreadth/_boardBreadthCount : 50;
  const _bVolR    = _boardVolRatioCount>0? _boardAvgVolRatio/_boardVolRatioCount: 1.0;

  /* ── 第二輪：計算相對強度 → 等級 ──────────────────────────────────────── */
  // 設計原則：
  //   A = 「絕對強」且「相對板塊明顯領先」→ 真正的領漲主力
  //   B = 「絕對不弱」且「相對板塊稍強或持平」→ 擴散跟漲
  //   C = 量能異動但位階低（低基期蓄力）
  //   D = 橫盤整理（預設）
  //   E = 退潮（強制）
  //
  // 絕對門檻（不受大盤影響的硬底線）：
  //   A: 漲幅 ≥ 1.5%，廣度 ≥ 60%
  //   B: 漲幅 ≥ 0.5%，廣度 ≥ 45%
  //
  // 相對門檻（相對板塊均值的超越幅度）：
  //   A: 漲幅超過板塊均值 +0.8%，且廣度超過板塊均值 +5%
  //   B: 漲幅超過板塊均值 -0.3%（不能比板塊差太多）

  const groupGrades = new Map();

  groupRaw.forEach((raw, group)=>{{
    const {{avgChange,breadth,volRatio,volRatioAdj,vr4grade,hasVol,mom5d,drawdown,stat}} = raw;

    // 相對板塊的超越幅度
    const relChange  = avgChange - _bChange;
    const relBreadth = breadth!=null ? breadth - _bBreadth : null;
    const relVolR    = vr4grade!=null ? vr4grade - _bVolR : null;

    // 量比輔助分（上限20，只加分不硬卡）
    let sVol = 0;
    if(vr4grade!=null){{
      if     (vr4grade>=2.0) sVol=20;
      else if(vr4grade>=1.5) sVol=14;
      else if(vr4grade>=1.2) sVol=8;
      else if(vr4grade>=1.0) sVol=3;
    }} else {{ sVol=5; }} // 無資料給小中性分

    const isExit =
      avgChange<-1.0 ||
      (breadth!=null&&breadth<25);

    const isLeader =
      avgChange>=1.5 &&
      (breadth==null||breadth>=60) &&
      relChange>=0.8 &&
      (relBreadth==null||relBreadth>=-5);

    const isLowBaseAccumulation =
      vr4grade!=null && vr4grade>=1.2 &&
      drawdown!=null && drawdown<-8 &&
      (mom5d==null || mom5d<5);

    const isBroadening =
      avgChange>=0.8 &&
      (breadth==null||breadth>=55) &&
      relChange>=0 &&
      (relBreadth==null||relBreadth>=-5);

    let grade="D";
    let gradeReason="整理觀察";

    // E 級：退潮（強制，最優先）
    // 條件：均跌 > 1% 或廣度 < 25%（超過四分之三的股在跌）
    if(isExit){{
      grade="E";
      gradeReason="退潮";
    }}
    // A 級：「絕對強」＋「相對明顯領先板塊」
    // 條件：漲幅≥1.5% + 廣度≥60% + 超板塊均值0.8%以上
    else if(isLeader){{
      grade="A";
      gradeReason="領漲";
    }}
    // B 級：「絕對不弱」＋「不落後板塊太多」
    // 條件收緊：漲幅≥0.8% + 廣度≥55% + 至少不輸板塊均值
    else if(isBroadening){{
      grade="B";
      gradeReason="擴散";
    }}
    // C 級：低基期量能蓄力型
    // 定義：量能相對放大 + 距20日高點回檔深 + 近5日未爆發。
    // 放在 B 後面，維持 A/B/C/D 的強弱層級；B 收緊後，低基期族群不會被寬鬆 B 全吃掉。
    // 代表意義：資金悄悄進場，可能是下一棒輪動目標
    // 注意：drawdown/mom5d 來自 build 預埋的歷史資料，yfinance沒跑到就是null→條件失敗→維持D
    //       vr4grade 是推估量比，盤中用時間修正後的值
    else if(isLowBaseAccumulation){{
      grade="C";
      gradeReason="低基期蓄力";
    }}
    // D 級：橫盤整理（預設）
    // 代表意義：量縮、廣度低、沒有明顯方向，靜待觀察

    groupGrades.set(group,{{
      grade, sVol,
      stage:GROUP_STAGE[group]||"",
      avgChange, breadth,
      volRatio, volRatioAdj, vr4grade,
      relChange, relBreadth, relVolR,
      mom5d, drawdown, hasVol, gradeReason,
      stat
    }});
  }});

  // 診斷輸出（按 F12 → Console 查看）
  console.log(`%c[輪動等級] ${{_sessionLabel}} | 係數x${{_intradayFactor.toFixed(2)}} | ${{_factorMethod}} | JS台灣時間 ${{_twTimeStr}} 週${{["日","一","二","三","四","五","六"][_twDay]}}`, "color:#22d3ee;font-weight:bold");
  const debugRows=[];
  groupGrades.forEach((gi,g)=>{{
    debugRows.push({{
      族群:g.split(" / ")[0],
      等級:gi.grade,
      原因:gi.gradeReason,
      量比分:`${{gi.sVol}}` ,相對漲幅:`${{gi.relChange!=null?gi.relChange.toFixed(2)+"%":"--"}}` ,相對廣度:`${{gi.relBreadth!=null?gi.relBreadth.toFixed(0)+"%":"--"}}`,
      均漲幅:gi.avgChange!=null?gi.avgChange.toFixed(2)+"%":"--",
      廣度:gi.breadth!=null?gi.breadth.toFixed(0)+"%":"--",
      原始量比:gi.volRatio!=null?gi.volRatio.toFixed(2)+"x":"--",
      推估量比:gi.volRatioAdj!=null?gi.volRatioAdj.toFixed(2)+"x":"--",
      有歷史量:gi.hasVol?"✅":"❌",
    }});
  }});
  console.table(debugRows);

  function jumpToGroup(group){{
    if(!group)return;
    const pill=document.querySelector(`.pill[data-filter="${{CSS.escape(group)}}"]`);
    if(pill) pill.click();
    setTimeout(()=>{{
      const target=document.querySelector(`.group-card[data-group="${{CSS.escape(group)}}"]`);
      if(!target)return;
      target.scrollIntoView({{behavior:"smooth",block:"start"}});
      target.classList.add("jump-focus");
      setTimeout(()=>target.classList.remove("jump-focus"),1400);
    }},80);
  }}

  document.querySelectorAll(".stage-heat-cell[data-filter]").forEach(cell=>{{
    cell.addEventListener("click",()=>jumpToGroup(cell.dataset.filter));
  }});

  /* ── KPI ─────────────────────────────────────────────────────────────────── */
  const avg=totalCount?totalChange/totalCount:null;
  const kpiVals=document.querySelectorAll(".kpi .value");
  if(kpiVals[2]){{kpiVals[2].textContent=fmtPct(avg);kpiVals[2].className=`value ${{trend(avg)}}`;}}
  if(kpiVals[3]) kpiVals[3].textContent=fmtInt(totalVolume)+"張";
  if(kpiVals[4]) kpiVals[4].textContent=payload.latest_time||"--:--:--";
  const meta=document.querySelector(".toolbar-meta");
  if(meta) meta.innerHTML=`共 <b>${{payload.requested_count}}</b> 檔 | 已抓 <b>${{payload.fetched_count}}</b> 檔 | 即時 <b>${{payload.latest_date}} ${{payload.latest_time}}</b>`;

  /* ── 等級標籤常數 ─────────────────────────────────────────────────────────── */
  const GRADE_COLOR  ={{A:"#ff6680",B:"#fbbf24",C:"#c084fc",D:"#3d5470",E:"#00d26e"}};
  const GRADE_BG     ={{A:"rgba(255,45,84,0.15)",B:"rgba(245,158,11,0.15)",C:"rgba(168,85,247,0.15)",D:"rgba(61,84,112,0.08)",E:"rgba(0,210,110,0.1)"}};
  const GRADE_SUFFIX ={{A:"領漲",B:"擴散",C:"補漲",D:"",E:"退潮"}};
  const STAGE_LABEL  ={{上游:"上游",中游:"中游",下游:"下游",補充:"補充"}};

  /* ── Group chip + rotation info ─────────────────────────────────────────── */
  document.querySelectorAll(".group-card").forEach(card=>{{
    const stat =groupStats.get(card.dataset.group);
    const ginfo=groupGrades.get(card.dataset.group);
    if(!stat||!stat.count)return;
    const a=stat.sum/stat.count;
    const chip=card.querySelector(".group-chip");
    if(chip){{chip.textContent=fmtPct(a);chip.className=`group-chip ${{trend(a)}}`;}}
    let infoEl=card.querySelector(".group-rotation-info");
    if(!infoEl){{infoEl=document.createElement("span");infoEl.className="group-rotation-info";const gr=card.querySelector(".g-right");if(gr)gr.insertBefore(infoEl,gr.firstChild);}}
    if(ginfo){{
      const stage=STAGE_LABEL[ginfo.stage]||ginfo.stage;
      const sfx=GRADE_SUFFIX[ginfo.grade]||"";
      const lbl=sfx?`【${{ginfo.grade}}級 · ${{stage}}${{sfx}}】`:`【${{ginfo.grade}}級】`;
      // 盤中顯示推估量比+標記 *，盤後顯示實際量比
      const vrVal = _isIntraday ? ginfo.volRatioAdj : ginfo.volRatio;
      const vrTag = _isIntraday ? "*" : "";
      const vr=vrVal!=null?`量比${{vrVal.toFixed(2)}}x${{vrTag}}`:"量比--";
      const bd=ginfo.breadth!=null?`廣度${{ginfo.breadth.toFixed(0)}}%`:"";
      const relC = ginfo.relChange!=null ? (ginfo.relChange>=0?"+":"")+ginfo.relChange.toFixed(1)+"%" : "";
      const sc = relC ? `超板塊${{relC}}` : "";
      infoEl.textContent=[lbl,vr,bd,sc].filter(Boolean).join(" ");
      infoEl.style.color=GRADE_COLOR[ginfo.grade]||"#7a9bbb";
      infoEl.style.borderColor=(GRADE_COLOR[ginfo.grade]||"#7a9bbb")+"44";
    }}
  }});

  /* ── Stage 熱力格：等級大字顯眼版 ──────────────────────────────────────── */
  document.querySelectorAll(".stage-heat-cell").forEach(cell=>{{
    const group=cell.dataset.filter;
    const stat =groupStats.get(group);
    const ginfo=groupGrades.get(group);
    if(!stat||!stat.count)return;
    const a=stat.sum/stat.count;
    cell.classList.remove("up","down","flat","na");
    cell.classList.add(trend(a));
    cell.style.setProperty("--heat",heat(a));
    const ce=cell.querySelector(".stage-heat-change");
    const ve=cell.querySelector(".stage-heat-volume");
    const ge=cell.querySelector(".heat-grade");
    if(ce) ce.textContent=fmtPct(a);
    if(ve) {{
      // 族群成交額(億) = Σ(個股張數 × 個股價格) / 1e5
      // stat.volume 是族群張數加總，但要配上各股價，這裡用族群均價估算
      const avgPx = stat.cntPrice>0 ? stat.sumPrice/stat.cntPrice : null;
      ve.textContent = avgPx ? fmtYi(stat.volume, avgPx) : "--";
    }}
    if(ge&&ginfo){{
      const gr=ginfo.grade;
      const sfx=GRADE_SUFFIX[gr]||"";
      const vrVal = _isIntraday ? ginfo.volRatioAdj : ginfo.volRatio;
      const vrTag = _isIntraday ? "*" : "";
      const vrSmall = vrVal!=null ? `<span class="heat-vr">量比 ${{vrVal.toFixed(2)}}x${{vrTag}}</span>` : `<span class="heat-vr">量比 --</span>`;
      // A 級特殊處理：黑底白字+紅邊框+發光，避免紅字疊紅底看不清
      const gradeSpanStyle = gr==="A"
        ? `font-size:16px;font-weight:900;color:#ffffff;
           background:rgba(10,0,0,0.82);
           border:2px solid ${{GRADE_COLOR[gr]}};
           border-radius:5px;padding:2px 8px;letter-spacing:0.06em;
           text-shadow:0 0 8px rgba(255,102,128,0.7);
           box-shadow:0 0 10px rgba(255,45,84,0.45),inset 0 0 6px rgba(255,45,84,0.12);`
        : `font-size:16px;font-weight:900;color:${{GRADE_COLOR[gr]}};
           background:${{GRADE_BG[gr]}};border:1px solid ${{GRADE_COLOR[gr]}}44;
           border-radius:5px;padding:2px 7px;letter-spacing:0.04em;`;
      ge.innerHTML=`<span style="${{gradeSpanStyle}}">${{gr}}</span>${{sfx?`<span style="font-size:10px;color:${{GRADE_COLOR[gr]}};margin-left:4px;opacity:0.85">${{sfx}}</span>`:""}}${{vrSmall}}`; 
    }}
  }});

  /* ── 熱力格依即時漲幅排序（上中下游各自高到低）────────────────────────── */
  document.querySelectorAll(".stage-row").forEach(row=>{{
    const grid=row.querySelector(".heat-grid");
    if(!grid)return;
    const cells=Array.from(grid.querySelectorAll(".stage-heat-cell"));
    cells.sort((a,b)=>{{
      const ga=groupGrades.get(a.dataset.filter);
      const gb=groupGrades.get(b.dataset.filter);
      const va=ga&&ga.avgChange!=null?ga.avgChange:-999;
      const vb=gb&&gb.avgChange!=null?gb.avgChange:-999;
      return vb-va;
    }});
    cells.forEach(c=>grid.appendChild(c));
  }});

  /* ── 輪動主控台 Badges ───────────────────────────────────────────────────── */
  const badgesEl=document.getElementById("rotationBadges");
  if(badgesEl){{
    const signals=[];
    groupGrades.forEach((gi,group)=>{{
      if(!"ABC".includes(gi.grade))return;
      const sfx=GRADE_SUFFIX[gi.grade]||"";
      const stage=STAGE_LABEL[gi.stage]||gi.stage;
      const lbl=sfx?`${{gi.grade}}級·${{stage}}${{sfx}}`:`${{gi.grade}}級`;
      const vrVal2 = _isIntraday ? gi.volRatioAdj : gi.volRatio;
      const vrTag2 = _isIntraday ? "*" : "";
      const vr=vrVal2!=null?` 量比${{vrVal2.toFixed(2)}}x${{vrTag2}}`:"";
      const bd=gi.breadth!=null?` 廣${{gi.breadth.toFixed(0)}}%`:"";
      signals.push({{grade:gi.grade,html:
        `<span class="r-badge grade-${{gi.grade.toLowerCase()}}">
         ${{lbl}} ▸ ${{group.split(" / ")[0]}}
         <span class="r-sub">${{vr}}${{bd}}</span></span>`
      }});
    }});
    signals.sort((x,y)=>x.grade.localeCompare(y.grade));
    badgesEl.innerHTML=signals.length
      ?signals.map(s=>s.html).join("")
      :`<span class="r-empty" style="color:#3d5470">目前無 A/B/C 級訊號，盤面平靜</span>`;
  }}

  /* ── 下一棒候選：把 C/B 族群轉成可掃描的觀察清單 ───────────────────────── */
  const nextWaveEl=document.getElementById("next-wave-cards");
  if(nextWaveEl){{
    const gradeBase={{C:70,B:56,A:38,D:18,E:0}};
    const thesis={{C:"低位階放量，還沒大漲，適合盯是否升級成 B。",B:"族群擴散轉強，適合找同族補漲與龍頭續航。",A:"已是主流領漲，偏確認行情，不是早期卡位。",D:"訊號不足，先等量能或廣度改善。",E:"退潮風險優先，暫不列入下一棒。"}};
    const candidates=[];
    groupGrades.forEach((gi,group)=>{{
      if(gi.grade==="E") return;
      const vr = _isIntraday ? gi.volRatioAdj : gi.volRatio;
      const volScore = vr!=null ? Math.min(vr*8,22) : 4;
      const ddScore = gi.drawdown!=null && gi.drawdown<0 ? Math.min(Math.abs(gi.drawdown),18) : 0;
      const breadthScore = gi.breadth!=null ? Math.min(Math.max(gi.breadth-40,0)/2,18) : 6;
      const relScore = gi.relChange!=null ? Math.max(Math.min(gi.relChange*4,12),-8) : 0;
      const notOverheated = gi.mom5d==null || gi.mom5d<5 ? 8 : -10;
      const score = (gradeBase[gi.grade]||0)+volScore+ddScore+breadthScore+relScore+notOverheated;
      candidates.push({{group,gi,score,vr}});
    }});
    candidates.sort((a,b)=>b.score-a.score);
    const top=candidates.slice(0,4);
    nextWaveEl.innerHTML=top.length?top.map((x,i)=>{{
      const gi=x.gi;
      const gradeColor=GRADE_COLOR[gi.grade]||"#7a9bbb";
      const stage=STAGE_LABEL[gi.stage]||gi.stage||"--";
      const vr=x.vr!=null?x.vr.toFixed(2)+"x":"--";
      const dd=gi.drawdown!=null?gi.drawdown.toFixed(1)+"%":"--";
      const bd=gi.breadth!=null?gi.breadth.toFixed(0)+"%":"--";
      const reason=thesis[gi.grade]||"觀察族群量價是否同步轉強。";
      return `
        <div class="next-wave-card" data-jump-group="${{x.group}}" style="border-top-color:${{gradeColor}}">
          <div class="next-wave-top">
            <span class="next-wave-rank" style="background:${{gradeColor}}">${{i+1}}</span>
            <span class="next-wave-grade" style="color:${{gradeColor}};border-color:${{gradeColor}}55">${{gi.grade}}級 · ${{gi.gradeReason||""}}</span>
          </div>
          <div class="next-wave-title">${{x.group}}</div>
          <div class="next-wave-meta"><span>${{stage}}</span><span>Score ${{Math.round(x.score)}}</span></div>
          <div class="next-wave-thesis">${{reason}}</div>
          <div class="next-wave-metrics">
            <div class="nw-metric"><div class="nw-k">量比</div><div class="nw-v">${{vr}}</div></div>
            <div class="nw-metric"><div class="nw-k">廣度</div><div class="nw-v">${{bd}}</div></div>
            <div class="nw-metric"><div class="nw-k">位階</div><div class="nw-v">${{dd}}</div></div>
          </div>
        </div>`;
    }}).join(""):`<div style="color:#3d5470;font-size:12px;padding:12px 0">目前沒有明確下一棒候選。</div>`;
    nextWaveEl.querySelectorAll(".next-wave-card[data-jump-group]").forEach(card=>{{
      card.addEventListener("click",()=>jumpToGroup(card.dataset.jumpGroup));
    }});
  }}

  /* ── 末升段警戒 ──────────────────────────────────────────────────────────── */
  const warnBox=document.getElementById("warningBox");
  const warnDetail=document.getElementById("warningDetail");
  if(warnBox){{
    let coreE=0,laggardAC=0,downAC=0;
    groupGrades.forEach((gi,g)=>{{
      if(CORE_UP.has(g)    &&gi.grade==="E")coreE++;
      if(LAGGARD_GRP.has(g)&&"AC".includes(gi.grade))laggardAC++;
      if(DOWNSTREAM.has(g) &&"AC".includes(gi.grade))downAC++;
    }});
    if(coreE>=1&&(laggardAC>=1||downAC>=2)){{
      warnBox.style.display="block";
      const parts=[];
      if(coreE)   parts.push(`上游核心 ${{coreE}} 族群 E 退潮`);
      if(laggardAC)parts.push(`被動元件低基期族群發動`);
      if(downAC)  parts.push(`${{downAC}} 個下游同步拉升`);
      if(warnDetail) warnDetail.textContent=parts.join(" ｜ ");
    }}else warnBox.style.display="none";
  }}

  /* ── 成交量排行 ──────────────────────────────────────────────────────────── */
  const lb=document.getElementById("vol-leaderboard");
  if(lb){{
    const ranked=Array.from(document.querySelectorAll("tr[data-code]")).map(row=>{{
      const q=quotes[row.dataset.code];
      const vol=q&&q.volume_lots!=null?+q.volume_lots:null;
      if(!vol||!isFinite(vol))return null;
      let chg=(q&&q.change_pct!=null&&isFinite(+q.change_pct))?+q.change_pct:parseFloat(row.dataset.change);
      if(!isFinite(chg))chg=null;
      return{{code:row.dataset.code,name:row.dataset.name||row.dataset.code,vol,chg,price:q&&q.price!=null?+q.price:null}};
    }}).filter(Boolean).sort((a,b)=>b.vol-a.vol).slice(0,12);
    lb.innerHTML=ranked.map(s=>`
      <div class="vol-card">
        <div class="vol-top"><span class="vol-code">${{s.code}}</span><span class="vol-chg ${{trend(s.chg)}}">${{fmtPct(s.chg)}}</span></div>
        <div class="vol-name">${{s.name}}</div>
        <div class="vol-vol">${{fmtYi(s.vol, s.price)}}</div>
      </div>`).join("");
  }}

  /* ══════════════════════════════════════════════════════════════════════════
     ① 族群輪動時序圖（全面重設計版）
     - 左側 Y 軸刻度標籤
     - 網格線輔助閱讀
     - 線條依終點漲幅排序，前三名加粗+顯示標籤
     - 滑鼠懸停顯示 tooltip
     - 零線明顯區分漲跌區域
  ══════════════════════════════════════════════════════════════════════════ */
  const chartEl=document.getElementById("rotation-chart");
  if(chartEl){{
    const groupLines=[];
    const marketPcts = (MARKET_HISTORY_5D && MARKET_HISTORY_5D.length>=2 && MARKET_HISTORY_5D[0].c>0)
      ? MARKET_HISTORY_5D.map(h => (h && h.c!=null) ? (h.c/MARKET_HISTORY_5D[0].c-1)*100 : null)
      : [];
    const hasMarketBase = marketPcts.length>=2;
    groupStats.forEach((stat,group)=>{{
      if(!stat.count||!stat.history5d.length)return;
      const allHists=stat.history5d;
      if(!allHists.length)return;
      const nDays=allHists[0].length;
      if(nDays<2)return;
      const labels=allHists[0].map(h=>h.d);
      const dailyPcts=[];
      for(let i=0;i<nDays;i++){{
        const vals=allHists.filter(h=>h.length>i&&h[0].c>0).map(h=>(h[i].c/h[0].c-1)*100);
        dailyPcts.push(vals.length?vals.reduce((a,b)=>a+b,0)/vals.length:null);
      }}
      const relPcts=dailyPcts.map((v,i)=>(v!=null&&marketPcts[i]!=null)?v-marketPcts[i]:v);
      const points=labels.map((d,i)=>({{d,pct:relPcts[i],rawPct:dailyPcts[i],marketPct:marketPcts[i]}}));
      const finalPct=relPcts[relPcts.length-1]??0;
      const gi=groupGrades.get(group)||{{}};
      groupLines.push({{
        group, shortName:group.split(" / ")[0],
        color:GROUP_COLOR[group]||"#7a9bbb",
        points, finalPct,
        grade:gi.grade||"D",
        stage:gi.stage||"",
      }});
    }});

    if(!groupLines.length){{
      chartEl.innerHTML='<div style="color:#3d5470;padding:24px;font-size:12px;font-style:italic">歷史資料尚未載入，請確認 build 腳本已成功執行並安裝 yfinance</div>';
    }}else{{
      // 依終點相對大盤漲幅排序（高到低）
      groupLines.sort((a,b)=>b.finalPct-a.finalPct);

      // 大盤排名插入：算大盤最終相對表現（0%，因為是基準）
      const marketFinalPct = 0;
      let marketRank = groupLines.findIndex(l=>l.finalPct < marketFinalPct);
      if(marketRank === -1) marketRank = groupLines.length;

      // Y 軸範圍
      let yMin=0,yMax=0;
      groupLines.forEach(l=>l.points.forEach(p=>{{
        if(p.pct!=null){{yMin=Math.min(yMin,p.pct);yMax=Math.max(yMax,p.pct);}}
      }}));
      const yRange=yMax-yMin||1;
      const yPad=Math.max(1.5,yRange*0.10);
      yMin-=yPad; yMax+=yPad;

      // 畫布尺寸：右側改成 panel，SVG 不再內嵌標籤
      const W=820, H=280, PL=46, PR=10, PT=20, PB=36;
      const cW=W-PL-PR, cH=H-PT-PB;
      const nDays=groupLines[0].points.length;
      const xS=i=>(nDays<2?cW/2:i*(cW/(nDays-1)));
      const yS=v=>(v==null?null:cH-(v-yMin)/(yMax-yMin)*cH);

      // ── 網格線 & Y 軸刻度 ──────────────────────────────────────────────
      const nGridY=5;
      let gridLines="", yTickLabels="";
      for(let g=0;g<=nGridY;g++){{
        const val=yMin+(yMax-yMin)*(g/nGridY);
        const y=yS(val);
        if(y==null)continue;
        const isZero=Math.abs(val)<(yMax-yMin)*0.05;
        gridLines+=`<line x1="0" y1="${{y.toFixed(1)}}" x2="${{cW}}" y2="${{y.toFixed(1)}}"
          stroke="${{isZero?"rgba(255,255,255,0.18)":"rgba(255,255,255,0.05)"}}"
          stroke-width="${{isZero?1.5:1}}"
          ${{isZero?'stroke-dasharray="none"':'stroke-dasharray="3,4"'}}/>`;
        const sign=val>0?"+":"";
        yTickLabels+=`<text x="-8" y="${{(+y.toFixed(1)+4)}}" fill="${{isZero?"rgba(255,255,255,0.3)":val>0?"rgba(255,104,128,0.6)":"rgba(0,210,110,0.6)"}}"
          font-size="9" text-anchor="end" font-family="IBM Plex Mono,monospace">${{sign+val.toFixed(1)}}%</text>`;
      }}

      const y0=yS(0)??cH/2;
      const upZone  =`<rect x="0" y="0" width="${{cW}}" height="${{y0.toFixed(1)}}" fill="rgba(255,45,84,0.03)"/>`;
      const downZone=`<rect x="0" y="${{y0.toFixed(1)}}" width="${{cW}}" height="${{(cH-y0).toFixed(1)}}" fill="rgba(0,210,110,0.03)"/>`;

      let xLabels="";
      groupLines[0].points.forEach((p,i)=>{{
        xLabels+=`<text x="${{xS(i).toFixed(1)}}" y="${{cH+24}}" fill="rgba(122,155,187,0.7)"
          font-size="10" text-anchor="middle" font-family="IBM Plex Mono,monospace">${{p.d}}</text>`;
      }});

      // ── 折線：全部都畫，前4強亮（sw=2.5/opacity=1），其他淡（sw=1/opacity=0.35）
      // 同時準備 hover 互動資料
      const TOP_N=4;
      let pathsAll="";
      const lineMetaArr=[]; // 供 JS hover 用

      groupLines.forEach((line,li)=>{{
        const isTop=li<TOP_N;
        const pts=line.points
          .map((p,i)=>{{const y=yS(p.pct);return y!=null?`${{xS(i).toFixed(1)}},${{y.toFixed(1)}}`:null;}})
          .filter(Boolean);
        if(pts.length<2)return;

        const pathD="M"+pts.join("L");
        const opacity=isTop?1:0.35;
        const sw=isTop?2.5:1.2;

        // data-li 用於 hover 識別
        pathsAll+=`<path d="${{pathD}}" fill="none" stroke="${{line.color}}"
          stroke-width="${{sw}}" stroke-opacity="${{opacity}}"
          stroke-linejoin="round" stroke-linecap="round"
          class="rot-line" data-li="${{li}}"
          style="cursor:pointer;transition:stroke-opacity .15s,stroke-width .15s"/>`;

        // 前4強端點 dot
        if(isTop){{
          const lastPt=line.points[line.points.length-1];
          const rawY=yS(lastPt.pct);
          if(rawY!=null){{
            pathsAll+=`<circle cx="${{xS(nDays-1).toFixed(1)}}" cy="${{rawY.toFixed(1)}}"
              r="4" fill="${{line.color}}" stroke="#04080f" stroke-width="1.5"
              class="rot-dot" data-li="${{li}}" style="cursor:pointer"/>`;
          }}
        }}

        // 記錄每條線的 y 值序列和 meta（供 hover 計算最近點）
        lineMetaArr.push({{
          li, group:line.group, shortName:line.shortName,
          color:line.color, grade:line.grade,
          finalPct:line.finalPct,
          ys: line.points.map((p,i)=>yS(p.pct)),
          isTop,
        }});
      }});

      // ── 組裝 SVG + 右側 Panel ──────────────────────────────────────────
      // 右側 panel 顯示全部族群 + 大盤插入（捲動）
      const TOP_PANEL=groupLines.length; // 顯示全部族群
      let panelRows="";
      let insertedMarket=false;
      for(let ri=0;ri<groupLines.length+1;ri++){{
        // 在正確位置插入大盤行
        if(!insertedMarket && ri===marketRank){{
          insertedMarket=true;
          panelRows+=`
            <div style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.06)">
              <span style="width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,0.3);flex-shrink:0"></span>
              <span style="flex:1;font-size:11px;color:rgba(255,255,255,0.45);font-style:italic">── 加權指數</span>
              <span style="font-size:10px;font-family:'IBM Plex Mono',monospace;color:rgba(255,255,255,0.35)">±0.0%</span>
            </div>`;
        }}
        if(ri>=groupLines.length)break;
        const l=groupLines[ri];
        const sign=l.finalPct>0?"+":"";
        const pctStr=sign+l.finalPct.toFixed(1)+"%";
        const gradeC=GRADE_COLOR[l.grade]||l.color;
        const rank=ri+1+(ri>=marketRank?1:0);
        panelRows+=`
          <div class="rot-panel-row" data-li="${{ri}}"
            style="display:flex;align-items:center;gap:6px;padding:3px 2px;border-radius:4px;cursor:pointer;transition:background .12s">
            <span style="width:8px;height:8px;border-radius:50%;background:${{l.color}};flex-shrink:0"></span>
            <span style="flex:1;font-size:11px;color:#c8daf0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${{l.shortName}}</span>
            <span style="font-size:10px;font-family:'IBM Plex Mono',monospace;color:${{l.finalPct>0?"#ff8096":"#00d26e"}}">${{pctStr}}</span>
            <span style="font-size:9px;padding:1px 4px;border-radius:3px;background:${{GRADE_BG[l.grade]||"rgba(61,84,112,0.15)"}};color:${{gradeC}};border:1px solid ${{gradeC}}44">${{l.grade}}</span>
          </div>`;
      }}
      if(!insertedMarket){{
        panelRows+=`
          <div style="display:flex;align-items:center;gap:6px;padding:3px 0;margin-top:2px">
            <span style="width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,0.3);flex-shrink:0"></span>
            <span style="flex:1;font-size:11px;color:rgba(255,255,255,0.45);font-style:italic">── 加權指數</span>
            <span style="font-size:10px;font-family:'IBM Plex Mono',monospace;color:rgba(255,255,255,0.35)">±0.0%</span>
          </div>`;
      }}

      // tooltip div（絕對定位，初始隱藏）
      const tooltipId=`rot-tip-${{Date.now()}}`;

      chartEl.style.position="relative";
      chartEl.innerHTML=`
      <div style="display:flex;gap:0;align-items:flex-start">
        <div style="flex:1;min-width:0;position:relative">
          <svg id="rot-svg" viewBox="0 0 ${{W}} ${{H}}" width="100%" style="display:block;overflow:visible"
               xmlns="http://www.w3.org/2000/svg">
            <defs>
              <clipPath id="chartClip">
                <rect x="0" y="0" width="${{cW}}" height="${{cH}}"/>
              </clipPath>
            </defs>
            <g transform="translate(${{PL}},${{PT}})">
              ${{upZone}}${{downZone}}
              ${{gridLines}}
              <g clip-path="url(#chartClip)">${{pathsAll}}</g>
              ${{xLabels}}
              ${{yTickLabels}}
              <line x1="0" y1="${{cH}}" x2="${{cW}}" y2="${{cH}}" stroke="rgba(255,255,255,0.06)"/>
              <line x1="0" y1="0" x2="0" y2="${{cH}}" stroke="rgba(255,255,255,0.06)"/>
            </g>
          </svg>
          <!-- hover tooltip -->
          <div id="${{tooltipId}}" style="
            display:none;position:absolute;pointer-events:none;
            background:rgba(4,8,15,0.92);border:1px solid rgba(255,255,255,0.15);
            border-radius:8px;padding:8px 12px;min-width:140px;z-index:99;
            box-shadow:0 4px 20px rgba(0,0,0,0.6)">
          </div>
        </div>
        <!-- 右側排名 Panel -->
        <div style="width:156px;flex-shrink:0;padding:4px 0 4px 12px;border-left:1px solid rgba(255,255,255,0.07);margin-left:8px">
          <div style="font-size:9px;letter-spacing:.1em;color:#3d5470;margin-bottom:6px;text-transform:uppercase">族群排名（相對大盤）</div>
          <div id="rot-panel-list" style="max-height:260px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#3d5470 transparent">${{panelRows}}</div>
        </div>
      </div>`;

      // ── hover / click 互動邏輯 ─────────────────────────────────────────
      const svgEl=chartEl.querySelector('#rot-svg');
      const tipEl=chartEl.querySelector(`#${{tooltipId}}`);
      const svgWrap=chartEl.querySelector('div[style*="flex:1"]');

      // 紀錄當前 hover/click 狀態
      let activeIdx=null;
      let clickLocked=false;

      function highlightLine(li){{
        svgEl.querySelectorAll('.rot-line').forEach(p=>{{
          const pli=+p.dataset.li;
          const isTop=lineMetaArr[pli]&&lineMetaArr[pli].isTop;
          if(pli===li){{
            p.style.strokeOpacity='1';
            p.style.strokeWidth=isTop?'3':'2.2';
          }}else{{
            p.style.strokeOpacity='0.12';
            p.style.strokeWidth=isTop?'2.5':'1.2';
          }}
        }});
        // panel row 高亮
        chartEl.querySelectorAll('.rot-panel-row').forEach(r=>{{
          r.style.background=+r.dataset.li===li?'rgba(255,255,255,0.06)':'';
        }});
      }}

      function resetLines(){{
        svgEl.querySelectorAll('.rot-line').forEach(p=>{{
          const pli=+p.dataset.li;
          const isTop=lineMetaArr[pli]&&lineMetaArr[pli].isTop;
          p.style.strokeOpacity=isTop?'1':'0.35';
          p.style.strokeWidth=isTop?'2.5':'1.2';
        }});
        chartEl.querySelectorAll('.rot-panel-row').forEach(r=>r.style.background='');
      }}

      function showTip(li, ex, ey){{
        const meta=lineMetaArr[li];
        if(!meta)return;
        const sign=meta.finalPct>0?"+":"";
        const gradeC=GRADE_COLOR[meta.grade]||meta.color;
        tipEl.innerHTML=`
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <span style="width:8px;height:8px;border-radius:50%;background:${{meta.color}};flex-shrink:0"></span>
            <span style="font-size:12px;font-weight:700;color:#e8f4ff">${{meta.shortName}}</span>
          </div>
          <div style="font-size:11px;font-family:'IBM Plex Mono',monospace;color:${{meta.finalPct>0?"#ff8096":"#00d26e"}};margin-bottom:2px">
            相對大盤 ${{sign}}${{meta.finalPct.toFixed(2)}}%
          </div>
          <div style="font-size:10px;padding:1px 5px;border-radius:3px;display:inline-block;
            background:${{GRADE_BG[meta.grade]||"rgba(61,84,112,0.15)"}};
            color:${{gradeC}};border:1px solid ${{gradeC}}44">
            ${{meta.grade}}級 ${{meta.group}}
          </div>`;
        // 定位：靠近滑鼠但不超出
        const wrapRect=svgWrap.getBoundingClientRect();
        const tipW=160, tipH=80;
        let tx=ex+14, ty=ey-10;
        if(tx+tipW>wrapRect.width) tx=ex-tipW-10;
        if(ty+tipH>wrapRect.height) ty=ey-tipH-10;
        tipEl.style.left=Math.max(0,tx)+'px';
        tipEl.style.top=Math.max(0,ty)+'px';
        tipEl.style.display='block';
        tipEl.style.borderColor=meta.color+'66';
      }}

      function hideTip(){{
        tipEl.style.display='none';
      }}

      // 找最近的線（以 path 的像素距離近似，用 y 軸距離判斷）
      function findNearestLine(mx, my, svgRect){{
        // 把滑鼠座標轉換成 SVG 內部座標
        const svgW=svgRect.width, svgH=svgRect.height;
        const vbW=W, vbH=H;
        const scaleX=vbW/svgW, scaleY=vbH/svgH;
        const svgX=(mx-svgRect.left)*scaleX - PL;
        const svgY=(my-svgRect.top)*scaleY - PT;

        // 找最近的 x 日期索引
        const step=nDays<2?1:cW/(nDays-1);
        let xi=Math.round(svgX/step);
        xi=Math.max(0,Math.min(nDays-1,xi));

        let minDist=Infinity, nearLi=-1;
        lineMetaArr.forEach(meta=>{{
          const py=meta.ys[xi];
          if(py==null)return;
          const dist=Math.abs(svgY-py);
          if(dist<minDist){{minDist=dist;nearLi=meta.li;}}
        }});
        return {{li:nearLi, dist:minDist}};
      }}

      // mousemove on SVG wrapper
      svgWrap.addEventListener('mousemove',e=>{{
        if(clickLocked)return;
        const svgRect=svgEl.getBoundingClientRect();
        const wrapRect=svgWrap.getBoundingClientRect();
        const {{li,dist}}=findNearestLine(e.clientX,e.clientY,svgRect);
        if(li>=0 && dist<30){{
          if(activeIdx!==li){{
            activeIdx=li;
            highlightLine(li);
          }}
          showTip(li, e.clientX-wrapRect.left, e.clientY-wrapRect.top);
        }}else{{
          if(activeIdx!==null){{activeIdx=null;resetLines();}}
          hideTip();
        }}
      }});

      svgWrap.addEventListener('mouseleave',e=>{{
        if(clickLocked)return;
        activeIdx=null;
        resetLines();
        hideTip();
      }});

      svgWrap.addEventListener('click',e=>{{
        const svgRect=svgEl.getBoundingClientRect();
        const wrapRect=svgWrap.getBoundingClientRect();
        const {{li,dist}}=findNearestLine(e.clientX,e.clientY,svgRect);
        if(li>=0 && dist<30){{
          if(clickLocked && activeIdx===li){{
            // 再點同一條：解鎖
            clickLocked=false; activeIdx=null;
            resetLines(); hideTip();
          }}else{{
            clickLocked=true; activeIdx=li;
            highlightLine(li);
            showTip(li, e.clientX-wrapRect.left, e.clientY-wrapRect.top);
          }}
        }}else{{
          clickLocked=false; activeIdx=null;
          resetLines(); hideTip();
        }}
      }});

      // panel row hover & click
      chartEl.querySelectorAll('.rot-panel-row').forEach(row=>{{
        const li=+row.dataset.li;
        row.addEventListener('mouseenter',()=>{{
          if(clickLocked)return;
          activeIdx=li; highlightLine(li);
          // tooltip 顯示在 panel 旁
          const meta=lineMetaArr[li];
          if(meta){{
            const wrapRect=svgWrap.getBoundingClientRect();
            const rowRect=row.getBoundingClientRect();
            showTip(li, wrapRect.width-180, rowRect.top-wrapRect.top+chartEl.getBoundingClientRect().top-chartEl.getBoundingClientRect().top);
          }}
        }});
        row.addEventListener('mouseleave',()=>{{
          if(clickLocked)return;
          activeIdx=null; resetLines(); hideTip();
        }});
        row.addEventListener('click',()=>{{
          if(clickLocked && activeIdx===li){{
            clickLocked=false; activeIdx=null; resetLines(); hideTip();
          }}else{{
            clickLocked=true; activeIdx=li; highlightLine(li);
          }}
        }});
      }});
    }}
  }}

  /* ══════════════════════════════════════════════════════════════════════════
     ② 🎯 入場雷達：低位階 + 量能異動個股
     條件：drawdown(距20日高點) < -8%（位階低）
           volume > avgVol20 * 1.5（今日成交量 > 均量1.5倍）
           changePct > 0（今日上漲，排除下跌中繼）
           5日漲幅 < 5%（尚未大漲）
  ══════════════════════════════════════════════════════════════════════════ */
  const radarEl=document.getElementById("entry-radar");
  if(radarEl){{
    const candidates=stockData
      .filter(s=>{{
        if(s.price==null||s.changePct==null||s.volume==null) return false;
        if(s.changePct<=0)return false;               // 今日需上漲
        if(s.avgVol20==null||s.avgVol20<=0)return false;
        const volRatioToday=s.volume/s.avgVol20;
        if(volRatioToday<1.5)return false;             // 量能 > 1.5倍均量
        if(s.p20high==null||s.p20high<=0)return false;
        const dd=(s.price/s.p20high-1)*100;
        if(dd>-8)return false;                         // 距20日高點回檔 > 8%
        if(s.p5close!=null&&s.p5close>0){{
          const mom5=(s.price/s.p5close-1)*100;
          if(mom5>5)return false;                      // 5日漲幅 < 5%（未爆發）
        }}
        return true;
      }})
      .map(s=>{{
        const volRatioToday=s.volume/s.avgVol20;
        const dd=(s.price/s.p20high-1)*100;
        const mom5=s.p5close&&s.p5close>0?(s.price/s.p5close-1)*100:null;
        // 評分：量比越大 + 位階越低 → 分越高
        const score=volRatioToday*0.6+Math.abs(dd)*0.4;
        return{{...s,volRatioToday,dd,mom5,score,
          groupName:s.group||"",
          groupColor:GROUP_COLOR[s.group]||"#7a9bbb",
        }};
      }})
      .sort((a,b)=>b.score-a.score)
      .slice(0,12);

    if(!candidates.length){{
      radarEl.innerHTML=`<div style="color:#3d5470;font-size:12px;padding:12px 0;font-style:italic">
        目前無符合條件個股（需：今漲 + 量>1.5倍均量 + 距高點回檔>8%）
      </div>`;
    }}else{{
      radarEl.innerHTML=candidates.map(s=>{{
        const ddStr=s.dd!=null?s.dd.toFixed(1)+"%":"--";
        const mom5Str=s.mom5!=null?(s.mom5>0?"+":"")+s.mom5.toFixed(1)+"%":"--";
        const volRStr=s.volRatioToday.toFixed(1)+"x";
        const gradeInfo=groupGrades.get(s.group)||{{}};
        const gc=GRADE_COLOR[gradeInfo.grade]||"#3d5470";
        const gn=s.group?(s.group.split(" / ")[0]):"";
        return `
        <div class="radar-card">
          <div class="radar-top">
            <span class="radar-code">${{s.code}}</span>
            <span class="radar-chg up">+${{s.changePct.toFixed(2)}}%</span>
          </div>
          <div class="radar-name">${{s.name}}</div>
          <div class="radar-group" style="color:${{s.groupColor}}">${{gn}}</div>
          <div class="radar-metrics">
            <span class="rm-item" title="量比（今日量/20日均量）">📦 ${{volRStr}}</span>
            <span class="rm-item" title="距20日高點回檔幅度">📉 ${{ddStr}}</span>
            <span class="rm-item" title="5日漲幅">5日 ${{mom5Str}}</span>
          </div>
        </div>`;
      }}).join("");
    }}
  }}

  // 通知靜態 JS 重整篩選顯示（補充區需重算 visible）
  document.dispatchEvent(new CustomEvent("quotesReady"));
}})();
</script>
"""

    # ── 額外 CSS（熱力格大字等級 + 折線圖 + 雷達卡片）──────────────────────
    extra_css = """
<style>
html, body {
  height:auto!important;
  min-height:0!important;
  overflow:visible!important;
}
body { margin:0!important; }
.wrap { padding-bottom:8px!important; }
#groupList { margin-bottom:0!important; padding-bottom:0!important; }
.group-card:last-child { margin-bottom:0!important; }
.group-card.jump-focus {
  box-shadow:0 0 0 1px rgba(34,211,238,0.55),0 0 28px rgba(34,211,238,0.22)!important;
}

/* 熱力格 grade 覆寫：讓 [A] 等文字更大更顯眼（已在 JS innerHTML 處理，這裡補 flex） */
.heat-grade { display:flex; align-items:center; gap:4px; margin-top:6px; flex-wrap:wrap; }
.heat-vr {
  font-family:'IBM Plex Mono',monospace;
  font-size:10px;
  color:#9fb6d3;
  background:rgba(255,255,255,0.04);
  border:1px solid rgba(255,255,255,0.07);
  border-radius:5px;
  padding:2px 5px;
}

/* ── 族群輪動圖 ────────────────────────────────────────────────────────── */
.rotation-chart-wrap {
  padding: 0 18px 16px;
}
.chart-label {
  font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:#22d3ee;margin-bottom:10px;display:flex;align-items:center;gap:8px;
}
.chart-label .chart-sub {
  font-size:10px;font-weight:400;letter-spacing:0;text-transform:none;
  color:#3d5470;font-style:italic;
}
#next-wave-board {
  padding: 0 18px 16px;
}
.next-wave-label {
  font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:#22d3ee;margin-bottom:10px;display:flex;align-items:center;gap:8px;
}
.next-wave-label .nw-sub {
  font-size:10px;font-weight:400;letter-spacing:0;text-transform:none;
  color:#3d5470;font-style:italic;
}
#next-wave-cards {
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
  gap:10px;
}
.next-wave-card {
  background:
    linear-gradient(155deg, rgba(34,211,238,0.08) 0%, rgba(34,211,238,0.01) 42%, transparent 80%),
    #070d1a;
  border:1px solid rgba(255,255,255,0.07);
  border-top:2px solid #22d3ee;
  border-radius:12px;
  padding:12px 13px 11px;
  min-height:142px;
  cursor:pointer;
  transition:transform .12s,box-shadow .12s,border-color .12s;
}
.next-wave-card:hover {
  transform:translateY(-2px);
  box-shadow:0 10px 24px rgba(34,211,238,0.14);
}
.next-wave-top {
  display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:8px;
}
.next-wave-rank {
  width:24px;height:24px;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;
  font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:700;
  color:#04080f;background:#22d3ee;flex:0 0 24px;
}
.next-wave-grade {
  font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:700;
  padding:3px 7px;border-radius:999px;border:1px solid rgba(255,255,255,0.12);
  white-space:nowrap;
}
.next-wave-title {
  font-size:15px;font-weight:700;line-height:1.25;margin-bottom:4px;
}
.next-wave-meta {
  font-size:10px;color:#7a9bbb;display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;
}
.next-wave-thesis {
  font-size:11px;color:#d7e6fa;line-height:1.45;min-height:32px;margin-bottom:9px;
}
.next-wave-metrics {
  display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;
}
.nw-metric {
  background:rgba(255,255,255,0.03);
  border:1px solid rgba(255,255,255,0.05);
  border-radius:8px;
  padding:6px 7px;
  min-width:0;
}
.nw-k {
  font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:#58708f;margin-bottom:3px;
}
.nw-v {
  font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;color:#ddeeff;
}
#rotation-chart {
  background: #070d1a;
  border:1px solid rgba(255,255,255,0.06);
  border-radius:12px;
  padding:8px 0 0 0;
  overflow:visible;
}

/* ── 入場雷達 ──────────────────────────────────────────────────────────── */
.radar-wrap { padding: 0 18px 16px; }
.radar-label {
  font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:#22d3ee;margin-bottom:9px;display:flex;align-items:center;gap:6px;
}
.radar-label .rl-sub {
  font-size:10px;font-weight:400;letter-spacing:0;text-transform:none;
  color:#3d5470;font-style:italic;
}
#entry-radar {
  display:flex;flex-wrap:wrap;gap:8px;
}
.radar-card {
  flex:0 0 148px;background:#070d1a;
  border:1px solid rgba(255,255,255,0.07);border-top:2px solid #a855f7;
  border-radius:10px;padding:10px 12px 10px;min-width:0;
  transition:transform .12s,box-shadow .12s;
}
.radar-card:hover { transform:translateY(-2px);box-shadow:0 8px 20px rgba(168,85,247,0.18); }
.radar-top { display:flex;justify-content:space-between;align-items:baseline;gap:4px;margin-bottom:2px; }
.radar-code { font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:700;color:#a855f7; }
.radar-chg  { font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600; }
.radar-name { font-size:12px;font-weight:600;margin:3px 0 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }
.radar-group{ font-size:10px;margin-bottom:6px;font-weight:500;opacity:0.85; }
.radar-metrics { display:flex;flex-direction:column;gap:2px; }
.rm-item { font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:#7a9bbb; }
</style>
"""

    vol_html = (
        "<style>"
        ".vol-strip{padding:0 18px 12px}"
        ".vol-label{font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#22d3ee;margin-bottom:9px}"
        "#vol-leaderboard{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;scrollbar-width:thin;scrollbar-color:#3d5470 transparent}"
        "#vol-leaderboard::-webkit-scrollbar{height:4px}"
        "#vol-leaderboard::-webkit-scrollbar-thumb{background:#3d5470;border-radius:2px}"
        ".vol-card{flex:0 0 116px;background:#070d1a;border:1px solid rgba(255,255,255,0.07);border-top:2px solid #22d3ee;border-radius:10px;padding:10px 12px 9px;min-width:0}"
        ".vol-top{display:flex;justify-content:space-between;align-items:baseline;gap:4px}"
        ".vol-code{font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;color:#22d3ee}"
        ".vol-chg{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600}"
        ".vol-name{font-size:12px;font-weight:600;margin:5px 0 3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        ".vol-vol{font-size:11px;color:#7a9bbb}"
        "</style>"
        '<div class="vol-strip">'
        '<div class="vol-label">🔥 成交量排行</div>'
        '<div id="vol-leaderboard"><div style="color:#3d5470;font-size:12px;padding:8px">載入中…</div></div>'
        "</div>"
    )

    next_wave_html = (
        '<div id="next-wave-board">'
        '<div class="next-wave-label">下一棒候選'
        '<span class="nw-sub">依等級、量比、廣度、低位階與近期漲幅排序</span></div>'
        '<div id="next-wave-cards"><div style="color:#3d5470;font-size:12px;padding:8px">計算中…</div></div>'
        "</div>"
    )

    # 折線圖區塊
    chart_html = (
        '<div class="rotation-chart-wrap">'
        '<div class="chart-label">📈 10日相對大盤輪動'
        '<span class="chart-sub">Y軸=族群10日累計漲幅 - 加權指數10日累計漲幅 · 粗線=前4強族群</span></div>'
        '<div id="rotation-chart"><div style="color:#3d5470;font-size:12px;padding:24px;font-style:italic">計算中…</div></div>'
        "</div>"
    )

    # 入場雷達區塊
    radar_html = (
        '<div class="radar-wrap">'
        '<div class="radar-label">🎯 入場雷達 — 低位階量能異動個股'
        '<span class="rl-sub">（今漲 · 量>1.5倍均量 · 距20日高點回檔>8% · 5日漲幅<5%）</span></div>'
        '<div id="entry-radar"><div style="color:#3d5470;font-size:12px;padding:8px">計算中…</div></div>'
        "</div>"
    )

    # ── 精準回報內容高度，讓 Streamlit iframe 剛好等於內容（無多餘空白）──
    auto_resize_script = (
        "<script>"
        "(function(){"
        "var raf=0;"
        "function contentHeight(){"
        "var el=document.querySelector('.wrap')||document.body;"
        "var rect=el.getBoundingClientRect();"
        "return Math.ceil(Math.max(el.scrollHeight,rect.bottom))+2;"
        "}"
        "function reportHeight(){"
        "var h=contentHeight();"
        "window.parent.postMessage({isStreamlitMessage:true,type:'streamlit:setFrameHeight',height:h},'*');"
        "}"
        "function schedule(){"
        "if(raf)cancelAnimationFrame(raf);"
        "raf=requestAnimationFrame(reportHeight);"
        "}"
        # 頁面載入完畢立即回報
        "if(document.readyState==='complete'){schedule();}"
        "else{window.addEventListener('load',schedule);}"
        # quotesReady 後再回報一次（JS 動態塞完內容後）
        "document.addEventListener('quotesReady',function(){setTimeout(schedule,400);});"
        "document.addEventListener('input',function(){setTimeout(schedule,80);},true);"
        "document.addEventListener('click',function(){setTimeout(schedule,80);},true);"
        # ResizeObserver 監聽 body 尺寸變化（動態內容展開/收合）
        "if(window.ResizeObserver){"
        "var _ro=new ResizeObserver(schedule);"
        "_ro.observe(document.body);"
        "var _wrap=document.querySelector('.wrap');"
        "if(_wrap)_ro.observe(_wrap);"
        "}"
        "setTimeout(schedule,50);"
        "setTimeout(schedule,500);"
        "})();"
        "</script>"
    )

    out = base_html.replace("</head>", extra_css + "</head>", 1)
    out = out.replace('<main id="groupList">',
                      next_wave_html + chart_html + radar_html + vol_html + '<main id="groupList">', 1)
    out = out.replace("</body>", script + auto_resize_script + "</body>", 1)
    return out


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(
        page_title="台灣半導體 × AI 產業鏈｜智慧輪動儀表板",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(
        """<style>
          [data-testid="stHeader"],[data-testid="stToolbar"],
          [data-testid="stDecoration"],[data-testid="stSidebar"],
          #MainMenu,footer,.stApp>header { display:none!important; }
          [data-testid="stAppViewContainer"]{padding:0!important;}
          [data-testid="stVerticalBlock"]{gap:0!important;padding:0!important;}
          [data-testid="element-container"]{padding:0!important;margin:0!important;}
          .block-container{padding:0!important;max-width:100%!important;}
          /* 讓整個 Streamlit 頁面可以跟著 iframe 高度自然撐開並捲動 */
          html,body{overflow:auto!important;height:auto!important;min-height:0!important;}
          .stApp{overflow:visible!important;height:auto!important;min-height:0!important;}
          [data-testid="stAppViewContainer"]{overflow:visible!important;height:auto!important;min-height:0!important;}
          section.main{overflow:visible!important;}
          /* iframe 本身不要有邊框或多餘空間 */
          iframe{display:block!important;border:none!important;margin:0!important;padding:0!important;}
        </style>""",
        unsafe_allow_html=True,
    )

    html_path = HTML_PATH if HTML_PATH.exists() else LOCAL_HTML_PATH
    if not html_path.exists():
        st.error(f"❌ 找不到 `{HTML_PATH}` 或 `{LOCAL_HTML_PATH}`\n\n請先產生 index.html。")
        st.stop()

    html_content = html_path.read_text(encoding="utf-8")
    symbols = tuple((item["code"], item["exchange"]) for item in extract_symbols(html_content))
    stock_groups = extract_stock_groups(html_content)

    if not symbols:
        st.error("HTML 裡沒有找到任何 data-code，靜態檔案可能有問題。")
        st.stop()

    with st.spinner(f"正在批次抓取 {len(symbols)} 檔即時報價及10日歷史…"):
        payload = fetch_all_quotes(symbols)

    if payload["fetched_count"] == 0:
        st.warning("⚠️ 報價暫時無法取得（非交易時間或 Yahoo 無回應）。顯示靜態快照。")
    if payload["errors"]:
        with st.expander("API 錯誤詳情", expanded=False):
            for e in payload["errors"]:
                st.caption(e)

    html_content = inject_live_script(html_content, payload, stock_groups)
    page_height  = estimate_page_height(html_content)
    # scrolling=False：iframe 本身不捲，高度由 postMessage 動態撐開，捲動交給瀏覽器整頁
    components.html(html_content, height=page_height, scrolling=False)


if __name__ == "__main__":
    main()
