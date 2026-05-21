from __future__ import annotations

import concurrent.futures
import html
import json
import math
import os
import re
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
VENDOR = ROOT / ".vendor_py"
CACHE_DIR = APP_DIR / ".cache_sem_ai"
FIN_CACHE_DIR = CACHE_DIR / "listed_financial"
OUT_FILE = APP_DIR / "index.html"
REPRESENTATIVE_JSON = APP_DIR / "representative_chain_data.json"

if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import xlrd  # type: ignore

# yfinance import (installed at runtime via pip install yfinance)
try:
    import yfinance as yf  # type: ignore
    HAS_YF = True
except ImportError:
    HAS_YF = False
    print("WARNING: yfinance not installed. Historical dataset attributes will be skipped.")


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
)
SSL_CTX = ssl._create_unverified_context()

# ── API URLs ──────────────────────────────────────────────────────────────────
LISTED_PRICE_URL   = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
LISTED_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
OTC_PRICE_URL      = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
OTC_CAP_URL        = "https://www.tpex.org.tw/www/en-us/company/rankCap"
OTC_EPS_URL        = "https://www.tpex.org.tw/www/en-us/company/rankEPS"
LISTED_FIN_URL     = "https://www.twse.com.tw/rwd/zh/IIH/company/financial?code={code}"


def _find_otc_revenue_url() -> str | None:
    XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    now = datetime.now()
    for months_back in range(4):
        year, month = now.year, now.month - months_back
        while month <= 0:
            month += 12
            year -= 1
        ym  = f"{year}{month:02d}"
        url = f"https://www.tpex.org.tw/storage/statistic/sales_revenue/en-us/O_{ym}.xls"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
                header = resp.read(8)
            if header == XLS_MAGIC:
                print(f"OTC revenue → {url}")
                return url
            print(f"OTC revenue {ym}: not a real XLS ({header[:8]!r}), skipping")
        except Exception as exc:
            print(f"OTC revenue {ym}: error ({exc}), skipping")
    return None


# ── Static config ─────────────────────────────────────────────────────────────
RELEVANT_LISTED_INDUSTRIES = {"半導體業"}

GROUP_ORDER = [
    "IC設計 / IP / ASIC",
    "晶圓代工 / 功率半導體",
    "先進封裝 / CoWoS",
    "封測 / 測試介面",
    "記憶體 / HBM",
    "矽晶圓 / 材料設備 / 廠務",
    "PCB / 載板 / CCL",
    "被動元件",
    "AI伺服器 / 機櫃組裝",
    "散熱",
    "電源 / BBU",
    "網通 / 光通訊 / CPO",
    "低軌衛星 / SpaceX",
    "高速互連 / 連接器 / 線材",
    "半導體其他",
]

GROUP_META = {
    "IC設計 / IP / ASIC":       {"stage": "上游", "desc": "AI GPU、交換晶片、BMC、IP 與客製 ASIC 的邏輯源頭。",           "color": "#8b5cf6"},
    "晶圓代工 / 功率半導體":     {"stage": "上游", "desc": "把設計真正做成晶片，涵蓋晶圓代工、功率元件與控制晶片量產。", "color": "#3b82f6"},
    "先進封裝 / CoWoS":          {"stage": "中游", "desc": "承接 CoWoS、先進封裝、封裝材料與相關設備耗材。",             "color": "#ec4899"},
    "封測 / 測試介面":            {"stage": "中游", "desc": "後段封裝、測試、Probe Card、Socket 與可靠度驗證。",           "color": "#f59e0b"},
    "記憶體 / HBM":               {"stage": "上游", "desc": "AI 算力密度向上時，HBM、DRAM、NAND 與控制晶片一起受惠。",   "color": "#7c3aed"},
    "矽晶圓 / 材料設備 / 廠務":  {"stage": "上游", "desc": "矽晶圓、再生晶圓、鑽石碟、CMP、清洗、無塵室與機電工程。",  "color": "#10b981"},
    "PCB / 載板 / CCL":          {"stage": "中游", "desc": "ABF 載板、高速 PCB、CCL 與伺服器 / 交換器板材的訊號主幹。",  "color": "#06b6d4"},
    "被動元件":                   {"stage": "中游", "desc": "MLCC、電阻、電感、電容等被動元件，AI 伺服器與車用電子需求帶動。",  "color": "#a855f7"},
    "AI伺服器 / 機櫃組裝":       {"stage": "下游", "desc": "GPU / ASIC、主機板、電源、散熱與機構整合成整機與機櫃。",    "color": "#f43f5e"},
    "散熱":                       {"stage": "中游", "desc": "高瓦數 GPU 機櫃的風冷、液冷、均熱與機構散熱模組。",          "color": "#38bdf8"},
    "電源 / BBU":                 {"stage": "中游", "desc": "伺服器 PSU、電源管理、BBU 與備援電力。",                     "color": "#f97316"},
    "網通 / 光通訊 / CPO":       {"stage": "下游", "desc": "交換器、光模組、矽光子與 CPO，讓 AI 叢集真正跑得起來。",    "color": "#0ea5e9"},
    "低軌衛星 / SpaceX":         {"stage": "下游", "desc": "Starlink / Kuiper 衛星本體、射頻元件、地面接收站與雷射通訊，台廠深度切入全球低軌衛星供應鏈。", "color": "#6366f1"},
    "高速互連 / 連接器 / 線材":  {"stage": "中游", "desc": "板內、板間、機櫃間的高速與高功率傳輸。",                    "color": "#22c55e"},
    "半導體其他":                 {"stage": "補充", "desc": "官方半導體產業別完整保留，但未手動歸到前述主題。",            "color": "#64748b"},
}

STAGE_FLOW = [
    ("上游", ["IC設計 / IP / ASIC", "晶圓代工 / 功率半導體", "記憶體 / HBM", "矽晶圓 / 材料設備 / 廠務"]),
    ("中游", ["先進封裝 / CoWoS", "封測 / 測試介面", "PCB / 載板 / CCL", "被動元件", "散熱", "電源 / BBU", "高速互連 / 連接器 / 線材"]),
    ("下游", ["AI伺服器 / 機櫃組裝", "網通 / 光通訊 / CPO", "低軌衛星 / SpaceX"]),
]

REPRESENTATIVE_GROUPS = {
    "ASIC":   ["2454", "3443", "3035", "5274", "6643"],
    "CoWoS":  ["1560", "3583", "6187", "6640", "3131"],
    "HBM":    ["2337", "2408", "8299", "3260", "6531"],
    "CPO":    ["4979", "4908", "3163", "3450", "3596"],
    "BBU":    ["2308", "6409", "6412", "6121"],
    "伺服器": ["2317", "2382", "3231", "6669", "2356"],
    "散熱":   ["3017", "3324", "2421", "3653"],
    "載板PCB":["3037", "8046", "2383", "2368", "6274"],
    "低軌衛星":["3491", "7717", "2485", "3138", "2314"],
}

MANUAL_GROUPS = {
    "IC設計 / IP / ASIC": [
        "2454", "3035", "3034", "2379", "3443", "3661", "6526", "4961", "5269", "6415",
        "3529", "4919", "2401", "3041", "3592", "3545", "3227", "8081", "8016", "5274",
        "2363", "6643", "8227", "6533",
        "2388", "2458", "3014", "3094", "3122", "3135", "3141", "3169", "3228", "3259",
        "3317", "3438", "3527", "3530", "3556", "3588", "4952", "4968", "5236", "5272",
        "5471", "6103", "6104", "6129", "6138", "6202", "6229", "6233", "6237", "6243",
        "6291", "6462", "6485", "6494", "6651", "6679", "6684", "6693", "6695", "6708",
        "6716", "6756", "6799", "6909", "6962", "6996", "7556", "8054", "8277",
    ],
    "晶圓代工 / 功率半導體": [
        "2330", "2303", "5347", "6770", "2344", "2481", "8261", "3707", "5425", "6435",
        "3675", "5299", "6719",
        "2302", "2329", "2340", "2342", "3105", "3686", "4923", "6552", "6937", "7712",
        "8086", "8162",
    ],
    "先進封裝 / CoWoS": [
        "1560", "3583", "6187", "6640", "3131", "3551", "3413", "8028", "4770", "3016",
        "5536", "5543", "3663", "6953",
        "2338", "3374", "3467", "6261", "6548", "6854",
    ],
    "封測 / 測試介面": [
        "3711", "2449", "6239", "6147", "3264", "6510", "6223", "6515", "2360", "6271",
        "8150", "6257", "8110", "8131", "3265", "6683", "6788", "7734",
        "2351", "2369", "2434", "2441", "3178", "3372", "3581", "5302", "5344", "6208",
        "6411", "6423", "6525", "7768", "7822", "8383",
    ],
    "記憶體 / HBM": [
        "2337", "2408", "3006", "2451", "4967", "8271", "8299", "3260", "6531", "8088",
        "3268", "6732",
        "4973", "5351", "8040",
    ],
    "矽晶圓 / 材料設備 / 廠務": [
        "6488", "3532", "6182", "5483", "3680", "4749", "6532", "8091", "3029",
        "3150", "3555", "3567", "4951", "5443", "6573", "6720", "6823", "6829", "6895",
        "6921", "7704", "7749", "7751", "7769", "7810", "8024", "8102",
    ],
    "PCB / 載板 / CCL": [
        "3037", "8046", "3189", "2383", "2368", "6274", "4958", "6269", "2313", "6191",
        "5469", "2367",
    ],
    "AI伺服器 / 機櫃組裝": [
        "2317", "3231", "2382", "6669", "2356", "3706", "4938", "8210", "3013", "2395",
        "6414", "6166", "3088", "8050", "3022", "3416", "2324",
    ],
    "散熱": [
        "3017", "3324", "2421", "3653", "4931",
        "3257",
    ],
    "電源 / BBU": [
        "2308", "6409", "6412", "6282", "6121", "3211",
    ],
    "網通 / 光通訊 / CPO": [
        "2345", "5388", "3596", "6285", "4906", "3450", "4979", "3163", "3363", "3081",
        "4908", "6442", "6451",
        "5222", "5487", "7770", "7772",
    ],
    "高速互連 / 連接器 / 線材": [
        "3023", "3665", "6279", "6205", "3217", "3376", "6805",
    ],
    "低軌衛星 / SpaceX": [
        "3491", "7717", "2485", "3138", "3062", "2314", "2312", "2419", "2355", "4916", "6443",
    ],
    "被動元件": [
        "2327", "3624", "6207", "2478", "8042", "3117", "3026", "2472", "8043", "6173",
        "6127", "2492", "3236", "6155", "6834", "6204", "6862", "3090", "2375", "6449",
        "6432", "2428", "6224", "3191", "4760", "6175", "6284", "5328", "3357", "8121",
        "5228", "7912",
    ],
}

GROUP_BY_CODE = {code: group for group, codes in MANUAL_GROUPS.items() for code in codes}


# ── Data classes ───────────────────────────────────────────────────────────────
@dataclass
class StockRow:
    code: str
    name: str
    market: str
    group: str
    stage: str
    price: float | None
    change_pct: float | None
    volume_shares: float | None
    capital_100m: float | None
    eps: float | None
    yoy: float | None
    mom: float | None
    scope: str
    # ── New historical attributes (added in v2) ──────────────────────────────
    avg_vol20:   float | None       = field(default=None)   # 過去20日平均成交張數
    p5_close:    float | None       = field(default=None)   # 5日前收盤價
    p20_high:    float | None       = field(default=None)   # 過去20日最高收盤價
    # history_5d: 過去5個交易日收盤價序列 [{d:"MM/DD", c:float}, ...]
    # 供盤中 JS 折線圖使用；預埋在 data-history-5d JSON 屬性中
    history_5d:  list[dict] | None  = field(default=None)


# ── Utilities ──────────────────────────────────────────────────────────────────
def ensure_dirs() -> None:
    APP_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)
    FIN_CACHE_DIR.mkdir(exist_ok=True)


def fetch_bytes(url: str, *, method: str = "GET", data: dict[str, Any] | None = None) -> bytes:
    payload = None
    headers = {"User-Agent": UA, "Accept": "application/json,text/plain,*/*,text/html,application/xhtml+xml"}
    if data is not None:
        payload = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        headers["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    last_err: Exception | None = None
    for _ in range(4):
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=45) as resp:
                return resp.read()
        except Exception as exc:
            last_err = exc
            time.sleep(1.2)
    raise RuntimeError(f"Fetch failed: {url}") from last_err


def fetch_json(url: str, *, method: str = "GET", data: dict[str, Any] | None = None) -> Any:
    return json.loads(fetch_bytes(url, method=method, data=data).decode("utf-8"))


def parse_float(value: Any) -> float | None:
    if value in (None, "", "--", "---", "----"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fmt_num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None or (isinstance(value, float) and math.isnan(value)) else f"{value:,.{digits}f}"


def fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,.{digits}f}%"


def fmt_volume_lots(value: float | None) -> str:
    """保留舊函數供 avg_vol20 等內部計算使用（張數）"""
    return "—" if value is None else f"{value / 1000:,.0f}"


def fmt_turnover(volume_shares: float | None, price: float | None) -> str:
    """
    成交金額（億元）= 成交股數 × 收盤價 / 1_000_000_00
    台股 1 張 = 1000 股，所以：
      成交金額(元) = volume_shares × price
      成交金額(億) = volume_shares × price / 1e8
    顯示規則：
      < 0.1億  → 顯示 "<0.1億"
      < 10億   → 1位小數，如 "3.2億"
      ≥ 10億   → 0位小數，如 "147億"
    """
    if volume_shares is None or price is None:
        return "—"
    amount_yi = volume_shares * price / 1e8
    if amount_yi < 0.1:
        return "<0.1億"
    if amount_yi < 10:
        return f"{amount_yi:.1f}億"
    return f"{amount_yi:.0f}億"


def trend_class(value: float | None) -> str:
    if value is None:
        return "na"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def taiwan_heat_color(value: float | None) -> str:
    if value is None:
        return "rgba(12,22,40,0.25)"
    magnitude = min(abs(value) / 6.0, 1.0)
    alpha = 0.14 + magnitude * 0.46
    if value > 0:
        return f"rgba(255,45,84,{alpha:.3f})"
    if value < 0:
        return f"rgba(0,210,110,{alpha:.3f})"
    return "rgba(240,180,41,0.20)"


def roc_date_to_ad(roc_date: str) -> str:
    roc_date = roc_date.strip()
    return roc_date if len(roc_date) < 7 else f"{int(roc_date[:3]) + 1911}-{roc_date[3:5]}-{roc_date[5:7]}"


# ── Historical data via yfinance batch download ────────────────────────────────
def fetch_historical_attrs(all_codes: list[str]) -> dict[str, dict[str, float | None]]:
    """
    批次下載所有個股近1個月日K，計算三個歷史指標。
    使用 yf.download(group_by='ticker') 一次性下載，避免逐一 rate-limit。
    回傳 {code: {avg_vol20, p5_close, p20_high}}
    """
    if not HAS_YF:
        return {}

    # 先嘗試 .TW 後綴，再嘗試 .TWO
    # 組合 ticker 清單：每個 code 都嘗試兩個後綴
    tw_tickers  = [f"{c}.TW"  for c in all_codes]
    two_tickers = [f"{c}.TWO" for c in all_codes]
    all_tickers = tw_tickers + two_tickers

    print(f"[yf] 批次下載 {len(all_tickers)} 個 ticker 近1個月日K…")
    result: dict[str, dict[str, float | None]] = {}

    try:
        # 一次性批次下載，避免 rate limit
        raw = yf.download(
            tickers=all_tickers,
            period="1mo",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        print(f"[yf] 批次下載失敗: {exc}")
        return {}

    import pandas as pd  # type: ignore

    def _extract(ticker: str) -> dict[str, float | None] | None:
        try:
            if len(all_tickers) == 1:
                df = raw
            elif ticker in raw.columns.get_level_values(0):
                df = raw[ticker]
            else:
                return None

            if df is None or df.empty:
                return None

            close = df["Close"].dropna()
            volume = df["Volume"].dropna()

            if len(close) < 2:
                return None

            # avg_vol20: 過去20日平均成交張數（Yahoo volume 是股，除以1000換算張）
            vol_series = volume.tail(20)
            avg_vol20 = float(vol_series.mean() / 1000) if len(vol_series) >= 1 else None

            # p5_close: 5個交易日前的收盤價（倒數第6根，index -6）
            if len(close) >= 6:
                p5_close = float(close.iloc[-6])
            elif len(close) >= 2:
                p5_close = float(close.iloc[0])
            else:
                p5_close = None

            # p20_high: 過去20日最高收盤價
            high_series = close.tail(20)
            p20_high = float(high_series.max()) if len(high_series) >= 1 else None

            # history_5d: 過去5個交易日收盤序列（供 JS 折線圖用）
            history_5d: list[dict] = []
            for ts, v in close.tail(5).items():
                try:
                    d = ts.strftime("%m/%d") if hasattr(ts, "strftime") else str(ts)[:10]
                    history_5d.append({"d": d, "c": round(float(v), 2)})
                except Exception:
                    pass

            return {
                "avg_vol20":  avg_vol20,
                "p5_close":   p5_close,
                "p20_high":   p20_high,
                "history_5d": history_5d if len(history_5d) >= 2 else None,
            }
        except Exception:
            return None

    # 優先取 .TW，fallback 到 .TWO
    for code in all_codes:
        attrs = _extract(f"{code}.TW")
        if attrs is None:
            attrs = _extract(f"{code}.TWO")
        if attrs is not None:
            result[code] = attrs

    print(f"[yf] 成功取得 {len(result)}/{len(all_codes)} 檔歷史指標")
    return result


# ── Data fetching ──────────────────────────────────────────────────────────────
def load_listed_prices() -> tuple[dict[str, dict[str, Any]], str]:
    rows = fetch_json(LISTED_PRICE_URL)
    out: dict[str, dict[str, Any]] = {}
    latest_date = ""
    for row in rows:
        code = str(row.get("Code", "")).strip()
        if not re.fullmatch(r"\d{4}", code):
            continue
        close  = parse_float(row.get("ClosingPrice"))
        change = parse_float(row.get("Change"))
        prev_close  = close - change if close is not None and change is not None else None
        change_pct  = (change / prev_close * 100) if prev_close not in (None, 0) and change is not None else None
        ad_date     = roc_date_to_ad(str(row.get("Date", "")))
        latest_date = latest_date or ad_date
        out[code]   = {"name": str(row.get("Name", "")).strip(), "price": close, "change_pct": change_pct, "volume_shares": parse_float(row.get("TradeVolume")), "date": ad_date}
    return out, latest_date


def load_listed_revenue() -> tuple[dict[str, dict[str, Any]], str]:
    rows = fetch_json(LISTED_REVENUE_URL)
    out: dict[str, dict[str, Any]] = {}
    latest_month = ""
    for row in rows:
        code = str(row.get("公司代號", "")).strip()
        if not re.fullmatch(r"\d{4}", code):
            continue
        ym = str(row.get("資料年月", "")).strip()
        if ym and len(ym) == 5:
            latest_month = latest_month or f"{int(ym[:3]) + 1911}-{ym[3:5]}"
        current   = parse_float(row.get("營業收入-當月營收"))
        prev      = parse_float(row.get("營業收入-上月營收"))
        last_year = parse_float(row.get("營業收入-去年當月營收"))
        mom = parse_float(row.get("營業收入-上月比較增減(%)"))
        yoy = parse_float(row.get("營業收入-去年同月增減(%)"))
        if mom is None and current not in (None, 0) and prev not in (None, 0):
            mom = (current / prev - 1) * 100
        if yoy is None and current not in (None, 0) and last_year not in (None, 0):
            yoy = (current / last_year - 1) * 100
        out[code] = {"industry": str(row.get("產業別", "")).strip(), "mom": mom, "yoy": yoy}
    return out, latest_month


def load_otc_prices() -> tuple[dict[str, dict[str, Any]], str]:
    rows = fetch_json(OTC_PRICE_URL)
    out: dict[str, dict[str, Any]] = {}
    latest_date = ""
    for row in rows:
        code = str(row.get("SecuritiesCompanyCode", "")).strip()
        if not re.fullmatch(r"\d{4}", code):
            continue
        close  = parse_float(row.get("Close"))
        change = parse_float(row.get("Change"))
        prev_close = close - change if close is not None and change is not None else None
        change_pct = (change / prev_close * 100) if prev_close not in (None, 0) and change is not None else None
        ad_date    = roc_date_to_ad(str(row.get("Date", "")))
        latest_date = latest_date or ad_date
        out[code]  = {"name": str(row.get("CompanyName", "")).strip(), "price": close, "change_pct": change_pct, "volume_shares": parse_float(row.get("TradingShares")), "date": ad_date}
    return out, latest_date


def load_otc_revenue() -> tuple[dict[str, dict[str, Any]], set[str], str]:
    url = _find_otc_revenue_url()
    if url is None:
        print("OTC revenue: no valid XLS found, skipping")
        return {}, set(), ""
    raw = fetch_bytes(url)
    fd, path = tempfile.mkstemp(suffix=".xls")
    os.close(fd)
    Path(path).write_bytes(raw)
    try:
        sheet = xlrd.open_workbook(path).sheet_by_index(0)
        rows: dict[str, dict[str, Any]] = {}
        semi_codes: set[str] = set()
        latest_month = ""
        month_row = str(sheet.row_values(2)[0]).strip()
        m = re.match(r"([A-Za-z]+)\s+(\d{4})", month_row)
        if m:
            latest_month = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%B %Y").strftime("%Y-%m")
        current_section = ""
        for idx in range(sheet.nrows):
            row = sheet.row_values(idx)
            head = str(row[0]).strip() if row else ""
            if re.match(r"^\d{2}\s", head):
                current_section = head
                continue
            match = re.match(r"^(\d{4})\s+(.+?)\s*$", head)
            if not match:
                continue
            code           = match.group(1)
            prev_month     = parse_float(row[2])
            current_month  = parse_float(row[3])
            last_year_same = parse_float(row[5])
            mom = (current_month / prev_month    - 1) * 100 if current_month not in (None, 0) and prev_month    not in (None, 0) else None
            yoy = (current_month / last_year_same - 1) * 100 if current_month not in (None, 0) and last_year_same not in (None, 0) else None
            rows[code] = {"mom": mom, "yoy": yoy}
            if current_section.startswith("24 Semiconductor"):
                semi_codes.add(code)
        return rows, semi_codes, latest_month
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def load_otc_rank(url: str) -> dict[str, float]:
    payload = fetch_json(url, method="POST", data={"choice": "domestic"})
    return {str(row[1]).strip(): parse_float(row[3]) for row in payload["tables"][0]["data"]}


def load_listed_financial(code: str) -> dict[str, Any]:
    cache_file = FIN_CACHE_DIR / f"{code}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    payload  = fetch_json(LISTED_FIN_URL.format(code=code))
    info     = payload.get("info", {})
    chart    = payload.get("chart", {})
    data     = info.get("data", {})
    eps_series = (((chart.get("eps") or {}).get("series") or [{}])[0]).get("data") or []
    result   = {
        "code":        code,
        "name":        data.get("shortName") or data.get("name") or code,
        "capital_amt": parse_float(data.get("capitalAmt")),
        "eps":         parse_float(eps_series[-1]) if eps_series else None,
    }
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def build_rows() -> tuple[list[StockRow], dict[str, Any]]:
    ensure_dirs()
    listed_prices,  listed_price_date = load_listed_prices()
    listed_revenue, listed_month      = load_listed_revenue()
    otc_prices,     otc_price_date    = load_otc_prices()
    otc_revenue,    otc_semi_codes, otc_month = load_otc_revenue()
    otc_cap_million = load_otc_rank(OTC_CAP_URL)
    otc_eps         = load_otc_rank(OTC_EPS_URL)

    latest_price_date   = max(x for x in [listed_price_date, otc_price_date] if x)
    latest_revenue_month = max(x for x in [listed_month, otc_month] if x)

    listed_semi_codes = {code for code, row in listed_revenue.items() if row.get("industry") in RELEVANT_LISTED_INDUSTRIES}
    selected_codes    = listed_semi_codes | otc_semi_codes | set(GROUP_BY_CODE)
    listed_codes      = sorted(code for code in selected_codes if code in listed_prices)

    listed_financial: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(load_listed_financial, code): code for code in listed_codes}
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                listed_financial[code] = future.result()
            except Exception:
                listed_financial[code] = {"code": code, "name": listed_prices.get(code, {}).get("name", code), "capital_amt": None, "eps": None}

    # ── 批次下載歷史日K（一次性，避免 rate limit）────────────────────────────
    all_codes_list = sorted(selected_codes)
    hist_attrs = fetch_historical_attrs(all_codes_list)

    rows: list[StockRow] = []
    for code in sorted(selected_codes):
        market      = "上市" if code in listed_prices else "上櫃"
        is_core_semi = code in listed_semi_codes or code in otc_semi_codes
        group       = GROUP_BY_CODE.get(code) or ("半導體其他" if is_core_semi else None)
        if not group:
            continue
        stage = GROUP_META[group]["stage"]

        # 歷史指標（可能為 None，若 yfinance 無資料）
        h = hist_attrs.get(code, {})
        avg_vol20  = h.get("avg_vol20")
        p5_close   = h.get("p5_close")
        p20_high   = h.get("p20_high")
        history_5d = h.get("history_5d")   # list[{d,c}] 或 None

        if market == "上市":
            px, rev, fin = listed_prices.get(code, {}), listed_revenue.get(code, {}), listed_financial.get(code, {})
            capital_amt  = parse_float(fin.get("capital_amt"))
            rows.append(StockRow(
                code, str(fin.get("name") or px.get("name") or code), market, group, stage,
                parse_float(px.get("price")), parse_float(px.get("change_pct")),
                parse_float(px.get("volume_shares")),
                capital_amt / 100_000_000 if capital_amt is not None else None,
                parse_float(fin.get("eps")), parse_float(rev.get("yoy")), parse_float(rev.get("mom")),
                "官方半導體全覆蓋" if is_core_semi else "AI延伸硬體鏈",
                avg_vol20=avg_vol20, p5_close=p5_close, p20_high=p20_high, history_5d=history_5d,
            ))
        else:
            px, rev      = otc_prices.get(code, {}), otc_revenue.get(code, {})
            capital_million = parse_float(otc_cap_million.get(code))
            rows.append(StockRow(
                code, str(px.get("name") or code), market, group, stage,
                parse_float(px.get("price")), parse_float(px.get("change_pct")),
                parse_float(px.get("volume_shares")),
                capital_million / 100 if capital_million is not None else None,
                parse_float(otc_eps.get(code)), parse_float(rev.get("yoy")), parse_float(rev.get("mom")),
                "官方半導體全覆蓋" if is_core_semi else "AI延伸硬體鏈",
                avg_vol20=avg_vol20, p5_close=p5_close, p20_high=p20_high, history_5d=history_5d,
            ))

    meta = {
        "latest_price_date":    latest_price_date,
        "latest_revenue_month": latest_revenue_month,
        "listed_count":  sum(1 for r in rows if r.market == "上市"),
        "otc_count":     sum(1 for r in rows if r.market == "上櫃"),
    }
    return rows, meta


# ── HTML helpers ───────────────────────────────────────────────────────────────
def summarize_group(rows: list[StockRow]) -> dict[str, Any]:
    changes = [r.change_pct for r in rows if r.change_pct is not None]
    volumes = [r.volume_shares for r in rows if r.volume_shares is not None]
    # 族群成交額（億）= Σ(股數 × 價格) / 1e8
    turnover_yi = sum(
        (r.volume_shares * r.price)
        for r in rows
        if r.volume_shares is not None and r.price is not None
    ) / 1e8
    return {
        "count":       len(rows),
        "change_avg":  sum(changes) / len(changes) if changes else None,
        "volume_sum":  sum(volumes) if volumes else None,
        "turnover_yi": turnover_yi,
    }


def make_table_rows(rows: list[StockRow]) -> str:
    """
    IMPORTANT: td.num order must be price[0], change_pct[1], volume[2]
    so that streamlit_app.py's inject_live_script can update them by index.
    Historical dataset attrs (avg-vol20, p5-close, p20-high) are embedded
    for the JS rotation engine to consume.
    """
    out = []
    for r in rows:
        p   = "" if r.price        is None else r.price
        ch  = "" if r.change_pct  is None else r.change_pct
        vol = "" if r.volume_shares is None else r.volume_shares
        cap = "" if r.capital_100m is None else r.capital_100m
        eps = "" if r.eps          is None else r.eps
        yoy = "" if r.yoy          is None else r.yoy
        mom = "" if r.mom          is None else r.mom
        tc  = trend_class(r.change_pct)
        ytc = trend_class(r.yoy)
        mtc = trend_class(r.mom)
        role = r.group.split(" / ")[-1] if " / " in r.group else r.group

        # Historical dataset attributes (empty string if None → JS skips safely)
        avg_vol20  = "" if r.avg_vol20  is None else round(r.avg_vol20, 2)
        p5_close   = "" if r.p5_close   is None else round(r.p5_close,  2)
        p20_high   = "" if r.p20_high   is None else round(r.p20_high,  2)
        # history_5d 序列：JSON 壓縮後寫入 data attribute，JS 端 JSON.parse 讀取
        history_5d_attr = "" if not r.history_5d else json.dumps(r.history_5d, ensure_ascii=False, separators=(",", ":"))

        out.append(
            f'<tr data-code="{r.code}" data-name="{html.escape(r.name)}" data-group="{html.escape(r.group)}"'
            f' data-price="{p}" data-change="{ch}" data-volume="{vol}"'
            f' data-capital="{cap}" data-eps="{eps}" data-yoy="{yoy}" data-mom="{mom}"'
            f' data-avg-vol20="{avg_vol20}" data-p5-close="{p5_close}" data-p20-high="{p20_high}"'
            f' data-history-5d=\'{history_5d_attr}\'>'
            f'<td class="c-code">'
            f'<a class="sym" href="https://tw.stock.yahoo.com/quote/{r.code}.{"TWO" if r.market == "上櫃" else "TW"}" target="_blank" rel="noopener">{r.code}</a>'
            f'<span class="mkt-badge">{r.market}</span>'
            f'</td>'
            f'<td class="c-name">{html.escape(r.name)}</td>'
            f'<td class="c-role"><span class="role-tag">{html.escape(role)}</span></td>'
            f'<td class="num">{fmt_num(r.price, 2)}</td>'
            f'<td class="num {tc}">{fmt_pct(r.change_pct)}</td>'
            f'<td class="num">{fmt_turnover(r.volume_shares, r.price)}</td>'
            f'<td class="num">{fmt_num(r.capital_100m, 1)}</td>'
            f'<td class="num">{fmt_num(r.eps, 2)}</td>'
            f'<td class="num {ytc}">{fmt_pct(r.yoy)}</td>'
            f'<td class="num {mtc}">{fmt_pct(r.mom)}</td>'
            f'</tr>'
        )
    return "".join(out)


def build_representative_payload(rows: list[StockRow], meta: dict[str, Any]) -> dict[str, Any]:
    row_map = {r.code: r for r in rows}
    themes  = {}
    for theme, codes in REPRESENTATIVE_GROUPS.items():
        picks   = [row_map[c] for c in codes if c in row_map]
        if not picks:
            continue
        changes = [p.change_pct for p in picks if p.change_pct is not None]
        vol_sum = sum((p.volume_shares or 0) for p in picks)
        themes[theme] = {
            "avg_change_pct": round(sum(changes) / len(changes), 4) if changes else None,
            "volume_lots":    round(vol_sum / 1000, 2),
            "avg_vol20":      round(sum(p.avg_vol20 or 0 for p in picks) / len(picks), 2) if picks else None,
            "stocks": [{"code": p.code, "name": p.name, "price": p.price,
                        "change_pct": p.change_pct,
                        "volume_lots": round((p.volume_shares or 0) / 1000, 2),
                        "avg_vol20":  p.avg_vol20,
                        "p5_close":   p.p5_close,
                        "p20_high":   p.p20_high,
                        "history_5d": p.history_5d} for p in picks],
        }
    return {"updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "latest_price_date": meta["latest_price_date"], "themes": themes}


# ── HTML generation ────────────────────────────────────────────────────────────
def build_html(rows: list[StockRow], meta: dict[str, Any]) -> str:
    grouped: dict[str, list[StockRow]] = defaultdict(list)
    for row in rows:
        grouped[row.group].append(row)

    # ── Stage heatmap ────────────────────────────────────────────────────────
    stage_parts = []
    for stage_name, groups in STAGE_FLOW:
        cells = []
        for group in groups:
            g_rows = grouped.get(group, [])
            if not g_rows:
                continue
            g      = summarize_group(g_rows)
            heat   = taiwan_heat_color(g["change_avg"])
            accent = GROUP_META[group]["color"]
            tc     = trend_class(g["change_avg"])
            cells.append(
                f'<button class="stage-heat-cell {tc}" data-filter="{html.escape(group)}"'
                f' style="--accent:{accent};--heat:{heat}">'
                f'<div class="heat-name">{html.escape(group)}</div>'
                f'<div class="stage-heat-change">{fmt_pct(g["change_avg"])}</div>'
                f'<div class="stage-heat-volume">{(lambda t: f"{t:.0f}億" if t>=10 else f"{t:.1f}億")(g["turnover_yi"])}</div>'
                f'<div class="heat-grade" data-group="{html.escape(group)}"></div>'
                f'</button>'
            )
        stage_parts.append(
            f'<div class="stage-row">'
            f'<div class="stage-badge">{stage_name}</div>'
            f'<div class="stage-arrow">›</div>'
            f'<div class="heat-grid">{"".join(cells)}</div>'
            f'</div>'
        )

    # ── Filter pills ─────────────────────────────────────────────────────────
    pills = '<button class="pill active" data-filter="all">全部產業鏈</button>'
    for g in GROUP_ORDER:
        if grouped.get(g):
            pills += f'<button class="pill" data-filter="{html.escape(g)}">{html.escape(g)}</button>'

    # ── Group section tables ─────────────────────────────────────────────────
    sections = []
    for group in GROUP_ORDER:
        items = grouped.get(group, [])
        if not items:
            continue
        s      = summarize_group(items)
        accent = GROUP_META[group]["color"]
        tc     = trend_class(s["change_avg"])
        sections.append(
            f'<section class="group-card" data-group="{html.escape(group)}" style="--accent:{accent}">'
            f'<div class="group-header">'
            f'<div class="g-left">'
            f'<span class="stage-tag">{GROUP_META[group]["stage"]}</span>'
            f'<h2 class="g-name">{html.escape(group)}</h2>'
            f'</div>'
            f'<div class="g-right">'
            f'<span class="group-chip {tc}" data-group="{html.escape(group)}">{fmt_pct(s["change_avg"])}</span>'
            f'<span class="g-count">{s["count"]} 檔</span>'
            f'</div>'
            f'</div>'
            f'<div class="tbl-wrap">'
            f'<table class="stock-table">'
            f'<thead><tr>'
            f'<th class="al">代號</th>'
            f'<th class="al">公司名稱</th>'
            f'<th class="al">角色</th>'
            f'<th class="sortable" data-sort="price">股價</th>'
            f'<th class="sortable" data-sort="change">漲跌幅</th>'
            f'<th class="sortable" data-sort="volume">成交額(億)</th>'
            f'<th class="sortable" data-sort="capital">資本額(億)</th>'
            f'<th class="sortable" data-sort="eps">EPS</th>'
            f'<th class="sortable" data-sort="yoy">{meta["latest_revenue_month"]} YoY</th>'
            f'<th class="sortable" data-sort="mom">{meta["latest_revenue_month"]} MoM</th>'
            f'</tr></thead>'
            f'<tbody>{make_table_rows(items)}</tbody>'
            f'</table>'
            f'</div>'
            f'</section>'
        )

    # ── Summary stats ────────────────────────────────────────────────────────
    total_cap  = sum(r.capital_100m or 0 for r in rows)
    changes    = [r.change_pct for r in rows if r.change_pct is not None]
    volumes    = [r.volume_shares for r in rows if r.volume_shares is not None]
    avg_change = sum(changes) / len(changes) if changes else None
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    avg_tc     = trend_class(avg_change)
    # 總成交額（億）= Σ(成交股數 × 收盤價) / 1e8
    total_turnover_yi = sum(
        (r.volume_shares * r.price)
        for r in rows
        if r.volume_shares is not None and r.price is not None
    ) / 1e8

    # ── Assemble final HTML ──────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>台灣半導體 × AI 產業鏈｜智慧輪動儀表板</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=IBM+Plex+Mono:wght@400;500;600&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      --bg:       #04080f;
      --s1:       #070d1a;
      --s2:       #0b1425;
      --s3:       #0f1b30;
      --border:   rgba(255,255,255,0.06);
      --border2:  rgba(255,255,255,0.13);
      --text:     #ddeeff;
      --text2:    #7a9bbb;
      --muted:    #3d5470;
      --accent:   #22d3ee;
      --acc-bg:   rgba(34,211,238,0.07);
      --up:       #ff2d54;
      --down:     #00d26e;
      --flat:     #f0b429;
      --grade-a:  #ff2d54;
      --grade-b:  #f59e0b;
      --grade-c:  #a855f7;
      --grade-d:  #3d5470;
      --grade-e:  #00d26e;
    }}

    html {{ height: 100%; }}
    body {{
      min-height: 100%;
      background-color: var(--bg);
      background-image: radial-gradient(rgba(34,211,238,0.03) 1px, transparent 1px);
      background-size: 28px 28px;
      color: var(--text);
      font-family: 'DM Sans', 'Noto Sans TC', system-ui, sans-serif;
      font-size: 13px;
      -webkit-font-smoothing: antialiased;
      user-select: none;
      -webkit-user-select: none;
    }}

    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: var(--s1); }}
    ::-webkit-scrollbar-thumb {{ background: var(--muted); border-radius: 3px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: var(--text2); }}

    .wrap {{ max-width: 1680px; margin: 0 auto; padding: 16px 18px 24px; }}

    /* ── Header ────────────────────────────────────────────────────────────── */
    .header {{
      display: flex; justify-content: space-between; align-items: flex-start;
      gap: 16px; margin-bottom: 16px; flex-wrap: wrap;
    }}
    .brand h1 {{
      font-family: 'Syne', sans-serif;
      font-size: 22px; font-weight: 800; letter-spacing: -0.02em; line-height: 1.2;
    }}
    .brand h1 em {{ font-style: normal; color: var(--accent); }}
    .brand-sub {{
      margin-top: 5px; font-size: 10px; font-weight: 500;
      letter-spacing: 0.14em; text-transform: uppercase; color: var(--text2);
    }}
    .kpis {{ display: flex; gap: 6px; flex-wrap: wrap; align-items: flex-start; }}
    .kpi {{
      background: var(--s1); border: 1px solid var(--border);
      border-radius: 10px; padding: 10px 14px; min-width: 106px;
    }}
    .kpi .label {{ font-size: 10px; letter-spacing: 0.06em; color: var(--text2); }}
    .kpi .value {{
      font-family: 'IBM Plex Mono', monospace;
      font-size: 17px; font-weight: 600; margin-top: 5px; line-height: 1;
    }}

    /* ── Rotation Panel ────────────────────────────────────────────────────── */
    .rotation-panel {{
      background: linear-gradient(135deg, rgba(34,211,238,0.04) 0%, rgba(139,92,246,0.04) 100%);
      border: 1px solid rgba(34,211,238,0.18);
      border-radius: 14px; padding: 14px 18px; margin-bottom: 14px;
    }}
    .rotation-panel-title {{
      font-family: 'Syne', sans-serif; font-size: 11px; font-weight: 700;
      letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent);
      margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
    }}
    .rotation-panel-title::after {{
      content: ''; flex: 1; height: 1px; background: rgba(34,211,238,0.15);
    }}
    #rotationBadges {{
      display: flex; flex-wrap: wrap; gap: 8px; min-height: 32px;
      align-items: center;
    }}
    #rotationBadges .r-empty {{
      font-size: 12px; color: var(--muted); font-style: italic;
    }}

    /* Grade badges */
    .r-badge {{
      display: inline-flex; align-items: center; gap: 6px;
      border-radius: 8px; padding: 5px 11px;
      font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 600;
      border: 1px solid; white-space: nowrap; line-height: 1;
    }}
    .r-badge.grade-a {{ background: rgba(255,45,84,0.12);  border-color: rgba(255,45,84,0.4);  color: #ff6680; }}
    .r-badge.grade-b {{ background: rgba(245,158,11,0.12); border-color: rgba(245,158,11,0.4); color: #fbbf24; }}
    .r-badge.grade-c {{ background: rgba(168,85,247,0.12); border-color: rgba(168,85,247,0.4); color: #c084fc; }}
    .r-badge .r-sub {{ font-size: 10px; opacity: 0.75; }}

    /* ── Warning box ───────────────────────────────────────────────────────── */
    #warningBox {{
      display: none;
      background: linear-gradient(135deg, rgba(220,38,38,0.18) 0%, rgba(185,28,28,0.12) 100%);
      border: 1px solid rgba(239,68,68,0.5); border-left: 4px solid #ef4444;
      border-radius: 12px; padding: 14px 18px; margin-bottom: 14px;
      animation: pulse-warn 2s infinite;
    }}
    @keyframes pulse-warn {{
      0%, 100% {{ box-shadow: 0 0 0 0 rgba(239,68,68,0.2); }}
      50%       {{ box-shadow: 0 0 18px 4px rgba(239,68,68,0.25); }}
    }}
    #warningBox .warn-title {{
      font-family: 'Syne', sans-serif; font-size: 13px; font-weight: 700;
      color: #fca5a5; letter-spacing: 0.02em;
    }}
    #warningBox .warn-body {{
      font-size: 12px; color: #fca5a580; margin-top: 4px;
    }}

    /* ── Pills ─────────────────────────────────────────────────────────────── */
    .pills {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; }}
    .pill {{
      background: transparent; border: 1px solid var(--border2);
      border-radius: 999px; color: var(--text2);
      font-family: inherit; font-size: 12px; font-weight: 500;
      padding: 5px 13px; cursor: pointer; white-space: nowrap;
      transition: all 0.12s ease;
    }}
    .pill:hover {{ border-color: var(--accent); color: var(--accent); }}
    .pill.active {{ background: var(--acc-bg); border-color: var(--accent); color: var(--accent); }}

    /* ── Stage heatmap ─────────────────────────────────────────────────────── */
    .stage-map {{ display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }}
    .stage-row {{
      display: grid; grid-template-columns: 46px 18px 1fr;
      gap: 8px; align-items: stretch;
    }}
    .stage-badge {{
      display: flex; align-items: center; justify-content: center;
      writing-mode: vertical-rl; text-orientation: mixed;
      background: var(--s1); border: 1px solid var(--border);
      border-radius: 8px; padding: 10px 4px;
      font-family: 'Syne', sans-serif; font-size: 12px; font-weight: 700;
      color: var(--accent); letter-spacing: 0.1em;
    }}
    .stage-arrow {{ display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 16px; }}
    .heat-grid {{ display: flex; flex-wrap: wrap; gap: 7px; align-content: flex-start; }}

    .stage-heat-cell {{
      flex: 1 1 144px; max-width: 224px;
      background: linear-gradient(150deg, var(--heat) 0%, transparent 65%), var(--s1);
      border: 1px solid var(--border);
      border-top: 2px solid var(--accent);
      border-radius: 10px; padding: 12px 14px 11px;
      cursor: pointer; text-align: left;
      transition: transform 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease;
    }}
    .stage-heat-cell:hover {{
      transform: translateY(-2px);
      box-shadow: 0 8px 24px rgba(0,0,0,0.40);
      border-color: rgba(255,255,255,0.14);
    }}
    .heat-name {{
      font-size: 11px; font-weight: 600; color: var(--text2);
      line-height: 1.4; margin-bottom: 9px;
    }}
    .stage-heat-change {{
      font-family: 'IBM Plex Mono', monospace;
      font-size: 22px; font-weight: 600; line-height: 1;
    }}
    .stage-heat-volume {{ font-size: 11px; color: var(--text2); margin-top: 7px; }}
    .heat-grade {{
      margin-top: 6px; font-family: 'IBM Plex Mono', monospace;
      font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
    }}

    /* ── Toolbar ────────────────────────────────────────────────────────────── */
    .toolbar {{
      position: sticky; top: 0; z-index: 50;
      display: flex; gap: 10px; align-items: center;
      background: rgba(4,8,15,0.88);
      backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
      border: 1px solid var(--border); border-radius: 12px;
      padding: 10px 14px; margin-bottom: 14px; flex-wrap: wrap;
    }}
    .search-box {{
      flex: 1 1 260px;
      display: flex; align-items: center; gap: 8px;
      background: var(--s2); border: 1px solid var(--border2);
      border-radius: 8px; padding: 8px 12px;
    }}
    .search-box svg {{ flex-shrink: 0; color: var(--muted); }}
    .search-box input {{
      flex: 1; background: transparent; border: none; outline: none;
      color: var(--text); font-family: inherit; font-size: 13px;
      user-select: text; -webkit-user-select: text;
    }}
    .search-box input::placeholder {{ color: var(--muted); }}
    .toolbar-right {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .t-btn {{
      background: var(--s2); border: 1px solid var(--border2);
      border-radius: 8px; color: var(--text2);
      font-family: inherit; font-size: 12px; font-weight: 500;
      padding: 7px 12px; cursor: pointer;
      transition: all 0.12s;
    }}
    .t-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
    .toolbar-meta {{ font-size: 11px; color: var(--text2); }}

    /* ── Group cards ────────────────────────────────────────────────────────── */
    .group-card {{
      background: var(--s1);
      border: 1px solid var(--border);
      border-left: 3px solid var(--accent);
      border-radius: 12px; overflow: hidden;
      margin-bottom: 10px;
    }}
    .group-header {{
      display: flex; justify-content: space-between; align-items: center;
      padding: 12px 16px; gap: 12px;
      border-bottom: 1px solid var(--border);
      background: rgba(0,0,0,0.15);
    }}
    .g-left {{ display: flex; flex-direction: column; gap: 3px; }}
    .stage-tag {{
      font-size: 9px; font-weight: 700; letter-spacing: 0.14em;
      text-transform: uppercase; color: var(--accent);
    }}
    .g-name {{
      font-family: 'Syne', sans-serif;
      font-size: 15px; font-weight: 700;
    }}
    .g-right {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
    .group-chip {{
      font-family: 'IBM Plex Mono', monospace;
      font-size: 13px; font-weight: 600;
      padding: 4px 10px; border-radius: 6px;
      background: rgba(255,255,255,0.05);
    }}
    .group-rotation-info {{
      font-family: 'IBM Plex Mono', monospace;
      font-size: 11px; color: var(--text2);
      padding: 3px 8px; border-radius: 5px;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--border);
    }}
    .g-count {{ font-size: 12px; color: var(--text2); }}

    /* ── Stock table ────────────────────────────────────────────────────────── */
    .tbl-wrap {{ overflow-x: auto; }}
    .stock-table {{ width: 100%; min-width: 940px; border-collapse: collapse; }}
    .stock-table thead th {{
      background: var(--s2); padding: 8px 12px;
      font-size: 10px; font-weight: 600;
      letter-spacing: 0.08em; text-transform: uppercase;
      color: var(--text2); text-align: right;
      border-bottom: 1px solid var(--border2);
      white-space: nowrap;
    }}
    .stock-table thead th.al {{ text-align: left; }}
    .stock-table thead th.sortable {{ cursor: pointer; }}
    .stock-table thead th.sortable:hover {{ color: var(--accent); }}
    .stock-table tbody tr {{ border-bottom: 1px solid var(--border); transition: background 0.1s; }}
    .stock-table tbody tr:hover {{ background: rgba(34,211,238,0.035); }}
    .stock-table tbody td {{ padding: 8px 12px; vertical-align: middle; font-size: 13px; }}
    td.num {{
      text-align: right;
      font-family: 'IBM Plex Mono', monospace;
      font-size: 12.5px; letter-spacing: -0.01em;
      white-space: nowrap;
    }}
    .c-code {{ min-width: 66px; }}
    .sym {{ font-family: 'IBM Plex Mono', monospace; font-size: 14px; font-weight: 600; color: var(--accent); text-decoration: none; }}
    .sym:hover {{ text-decoration: underline; text-underline-offset: 2px; }}
    .mkt-badge {{
      display: inline-block; margin-left: 5px;
      font-size: 9px; color: var(--text2);
      border: 1px solid var(--border2); border-radius: 3px; padding: 1px 4px;
    }}
    .c-name {{ min-width: 80px; font-weight: 600; font-size: 13.5px; }}
    .role-tag {{
      display: inline-block; font-size: 11px;
      background: rgba(255,255,255,0.04);
      border: 1px solid var(--border2);
      border-radius: 4px; padding: 2px 6px;
      color: var(--text2); white-space: nowrap;
    }}

    .up   {{ color: var(--up);   }}
    .down {{ color: var(--down); }}
    .flat {{ color: var(--flat); }}
    .na   {{ color: var(--muted);}}

    @media (max-width: 860px) {{
      .stage-row {{ grid-template-columns: 1fr; }}
      .stage-badge {{ writing-mode: horizontal-tb; padding: 6px 12px; }}
      .stage-arrow {{ display: none; }}
      .header {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
<div class="wrap">

  <!-- Header -->
  <header class="header">
    <div class="brand">
      <h1>台灣半導體 <em>×</em> AI 產業鏈</h1>
      <div class="brand-sub">Taiwan Semiconductor &amp; AI Supply Chain · 智慧族群輪動儀表板</div>
    </div>
    <div class="kpis">
      <div class="kpi"><div class="label">收錄檔數</div><div class="value">{len(rows)}</div></div>
      <div class="kpi"><div class="label">總資本額</div><div class="value">{fmt_num(total_cap, 0)}億</div></div>
      <div class="kpi"><div class="label">平均漲跌幅</div><div class="value {avg_tc}">{fmt_pct(avg_change)}</div></div>
      <div class="kpi"><div class="label">總成交額</div><div class="value">{fmt_turnover(sum(volumes) if volumes else None, 1.0) if False else (f"{total_turnover_yi:.0f}億" if total_turnover_yi >= 10 else f"{total_turnover_yi:.1f}億")}</div></div>
      <div class="kpi"><div class="label">更新時間</div><div class="value" style="font-size:13px">{updated_at[11:]}</div></div>
    </div>
  </header>

  <!-- Warning box (shown by JS when exit signal detected) -->
  <div id="warningBox">
    <div class="warn-title">🚨 警訊：核心資金退潮，低基期落後補漲發動，請防範大盤拉回風險！</div>
    <div class="warn-body" id="warningDetail"></div>
  </div>

  <!-- Rotation panel -->
  <div class="rotation-panel">
    <div class="rotation-panel-title">⚡ 產業鏈大戶資金輪動即時訊號</div>
    <div id="rotationBadges"><span class="r-empty">計算中，請稍候…</span></div>
  </div>

  <!-- Filter pills -->
  <div class="pills">{pills}</div>

  <!-- Stage heatmap -->
  <div class="stage-map">{"".join(stage_parts)}</div>

  <!-- Toolbar -->
  <div class="toolbar">
    <div class="search-box">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
      <input id="searchInput" type="search" placeholder="搜尋代號、公司名稱、族群…">
    </div>
    <div class="toolbar-right">
      <button class="t-btn" id="reloadBtn">⟳ 重新整理</button>
      <div class="toolbar-meta">共 <b>{len(rows)}</b> 檔 &nbsp;|&nbsp; 上市 <b>{meta['listed_count']}</b> / 上櫃 <b>{meta['otc_count']}</b> &nbsp;|&nbsp; 股價基準 <b>{meta['latest_price_date']}</b></div>
    </div>
  </div>

  <!-- Group sections -->
  <main id="groupList">{"".join(sections)}</main>

</div><!-- /wrap -->

<script>
(function () {{
  "use strict";

  var searchInput = document.getElementById('searchInput');
  var reloadBtn   = document.getElementById('reloadBtn');
  var pills       = Array.from(document.querySelectorAll('.pill'));
  var heatCells   = Array.from(document.querySelectorAll('.stage-heat-cell'));
  var cards       = Array.from(document.querySelectorAll('.group-card'));
  var activeFilter = 'all';

  reloadBtn.addEventListener('click', function () {{ location.reload(); }});

  function setFilter(f) {{
    activeFilter = f;
    pills.forEach(function (b) {{ b.classList.toggle('active', b.dataset.filter === f); }});
    applyFilters();
  }}
  pills.forEach(function (b) {{ b.addEventListener('click', function () {{ setFilter(b.dataset.filter); }}); }});
  heatCells.forEach(function (b) {{ b.addEventListener('click', function () {{ setFilter(b.dataset.filter); }}); }});
  searchInput.addEventListener('input', applyFilters);

  function applyFilters() {{
    var term = searchInput.value.trim().toLowerCase();
    cards.forEach(function (card) {{
      var group   = card.dataset.group;
      var allowed = activeFilter === 'all' || activeFilter === group;
      var visible = 0;
      card.querySelectorAll('tbody tr').forEach(function (row) {{
        var ok = allowed && (!term || row.innerText.toLowerCase().includes(term));
        row.style.display = ok ? '' : 'none';
        if (ok) visible++;
      }});
      card.style.display = visible ? '' : 'none';
    }});
  }}

  /* Sortable columns */
  document.querySelectorAll('.stock-table').forEach(function (tbl) {{
    tbl.querySelectorAll('th.sortable').forEach(function (th) {{
      th.addEventListener('click', function () {{
        var key  = th.dataset.sort;
        var tbody = tbl.querySelector('tbody');
        var rows  = Array.from(tbody.querySelectorAll('tr'));
        var next  = th.dataset.order === 'asc' ? 'desc' : 'asc';
        tbl.querySelectorAll('th.sortable').forEach(function (x) {{
          delete x.dataset.order;
          x.textContent = x.textContent.replace(/ ▲$| ▼$/, '');
        }});
        th.dataset.order = next;
        th.textContent += next === 'asc' ? ' ▲' : ' ▼';
        rows.sort(function (a, b) {{
          var av = a.dataset[key] === '' ? null : Number(a.dataset[key]);
          var bv = b.dataset[key] === '' ? null : Number(b.dataset[key]);
          if (av === null && bv === null) return 0;
          if (av === null) return 1;
          if (bv === null) return -1;
          return next === 'asc' ? av - bv : bv - av;
        }});
        rows.forEach(function (r) {{ tbody.appendChild(r); }});
      }});
    }});
  }});

  applyFilters();
}})();
</script>
</body>
</html>"""


# ── Entry point ────────────────────────────────────────────────────────────────
def main() -> None:
    rows, meta = build_rows()
    OUT_FILE.write_text(build_html(rows, meta), encoding="utf-8")
    REPRESENTATIVE_JSON.write_text(
        json.dumps(build_representative_payload(rows, meta), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(OUT_FILE)
    print(REPRESENTATIVE_JSON)
    print(f"rows={len(rows)}")


if __name__ == "__main__":
    main()
