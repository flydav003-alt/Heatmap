"""
build_semiconductor_ai_chain.py
台灣半導體 × AI 產業鏈熱力圖 — 靜態 HTML 生成腳本
────────────────────────────────────────────────────
資料抓取邏輯完整保留（TWSE / TPEX 公開 API + xls）。
HTML 輸出全面重設計：更清晰的層次結構、更現代的視覺風格。
"""
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


APP_DIR       = Path(__file__).resolve().parent
ROOT          = APP_DIR.parent
VENDOR        = ROOT / ".vendor_py"
CACHE_DIR     = APP_DIR / ".cache_sem_ai"
FIN_CACHE_DIR = CACHE_DIR / "listed_financial"
OUT_FILE      = APP_DIR / "index.html"
REPRESENTATIVE_JSON = APP_DIR / "representative_chain_data.json"

if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import xlrd  # type: ignore


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
)
SSL_CTX = ssl._create_unverified_context()

LISTED_PRICE_URL   = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
LISTED_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
OTC_PRICE_URL      = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"


def _find_otc_revenue_url() -> str:
    """OTC 月報 URL。每月 ~10 日後才有上月資料，最多往前查 2 個月。"""
    now = datetime.now()
    for months_back in range(3):
        year, month = now.year, now.month - months_back
        while month <= 0:
            month += 12
            year -= 1
        ym  = f"{year}{month:02d}"
        url = f"https://www.tpex.org.tw/storage/statistic/sales_revenue/en-us/O_{ym}.xls"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=10) as resp:
                if resp.status == 200:
                    print(f"OTC revenue URL → {url}")
                    return url
        except Exception:
            continue
    ym = now.strftime("%Y%m")
    return f"https://www.tpex.org.tw/storage/statistic/sales_revenue/en-us/O_{ym}.xls"


OTC_CAP_URL    = "https://www.tpex.org.tw/www/en-us/company/rankCap"
OTC_EPS_URL    = "https://www.tpex.org.tw/www/en-us/company/rankEPS"
LISTED_FIN_URL = "https://www.twse.com.tw/rwd/zh/IIH/company/financial?code={code}"

RELEVANT_LISTED_INDUSTRIES = {"半導體業"}

GROUP_ORDER = [
    "IC設計 / IP / ASIC",
    "晶圓代工 / 功率半導體",
    "先進封裝 / CoWoS",
    "封測 / 測試介面",
    "記憶體 / HBM",
    "矽晶圓 / 材料設備 / 廠務",
    "PCB / 載板 / CCL",
    "AI伺服器 / 機櫃組裝",
    "散熱",
    "電源 / BBU",
    "網通 / 光通訊 / CPO",
    "高速互連 / 連接器 / 線材",
    "半導體其他",
]

GROUP_META = {
    "IC設計 / IP / ASIC":      {"stage": "上游", "desc": "AI GPU、交換晶片、BMC、IP 與客製 ASIC 的邏輯源頭。",            "color": "#8b5cf6"},
    "晶圓代工 / 功率半導體":    {"stage": "上游", "desc": "把設計真正做成晶片，涵蓋晶圓代工、功率元件與控制晶片量產。",   "color": "#3b82f6"},
    "先進封裝 / CoWoS":         {"stage": "中游", "desc": "承接 CoWoS、先進封裝、封裝材料與相關設備耗材。",              "color": "#ec4899"},
    "封測 / 測試介面":           {"stage": "中游", "desc": "後段封裝、測試、Probe Card、Socket 與可靠度驗證。",           "color": "#f59e0b"},
    "記憶體 / HBM":             {"stage": "上游", "desc": "AI 算力密度向上時，HBM、DRAM、NAND 與控制晶片一起受惠。",      "color": "#7c3aed"},
    "矽晶圓 / 材料設備 / 廠務": {"stage": "上游", "desc": "矽晶圓、再生晶圓、鑽石碟、CMP、清洗、無塵室與機電工程。",     "color": "#10b981"},
    "PCB / 載板 / CCL":         {"stage": "中游", "desc": "ABF 載板、高速 PCB、CCL 與伺服器 / 交換器板材的訊號主幹。",   "color": "#06b6d4"},
    "AI伺服器 / 機櫃組裝":      {"stage": "下游", "desc": "GPU / ASIC、主機板、電源、散熱與機構整合成整機與機櫃。",       "color": "#f43f5e"},
    "散熱":                      {"stage": "中游", "desc": "高瓦數 GPU 機櫃的風冷、液冷、均熱與機構散熱模組。",           "color": "#38bdf8"},
    "電源 / BBU":                {"stage": "中游", "desc": "伺服器 PSU、電源管理、BBU 與備援電力。",                     "color": "#f97316"},
    "網通 / 光通訊 / CPO":       {"stage": "下游", "desc": "交換器、光模組、矽光子與 CPO，讓 AI 叢集真正跑得起來。",      "color": "#0ea5e9"},
    "高速互連 / 連接器 / 線材":  {"stage": "中游", "desc": "板內、板間、機櫃間的高速與高功率傳輸。",                      "color": "#22c55e"},
    "半導體其他":                {"stage": "補充", "desc": "官方半導體產業別完整保留，但未手動歸到前述主題。",            "color": "#64748b"},
}

STAGE_FLOW = [
    ("上游", ["IC設計 / IP / ASIC", "晶圓代工 / 功率半導體", "記憶體 / HBM", "矽晶圓 / 材料設備 / 廠務"]),
    ("中游", ["先進封裝 / CoWoS", "封測 / 測試介面", "PCB / 載板 / CCL", "散熱", "電源 / BBU", "高速互連 / 連接器 / 線材"]),
    ("下游", ["AI伺服器 / 機櫃組裝", "網通 / 光通訊 / CPO"]),
]

REPRESENTATIVE_GROUPS = {
    "ASIC":    ["2454", "3443", "3035", "5274", "6643"],
    "CoWoS":   ["1560", "3583", "6187", "6640", "3131"],
    "HBM":     ["2337", "2408", "8299", "3260", "6531"],
    "CPO":     ["4979", "4908", "3163", "3450", "3596"],
    "BBU":     ["2308", "6409", "6412", "6121"],
    "伺服器":   ["2317", "2382", "3231", "6669", "2356"],
    "散熱":    ["3017", "3324", "2421", "3653"],
    "載板PCB": ["3037", "8046", "2383", "2368", "6274"],
}

MANUAL_GROUPS = {
    "IC設計 / IP / ASIC":      ["2454", "3035", "3034", "2379", "3443", "3661", "6526", "4961", "5269", "6415", "3529", "4919", "2401", "3041", "3592", "3545", "3227", "8081", "8016", "5274", "2363", "6643", "8227", "6533"],
    "晶圓代工 / 功率半導體":    ["2330", "2303", "5347", "6770", "2344", "2481", "8261", "3707", "5425", "6435", "3675", "5299", "6719"],
    "先進封裝 / CoWoS":         ["1560", "3583", "6187", "6640", "3131", "3551", "3413", "8028", "4770", "3016", "5536", "5543", "3663", "6953"],
    "封測 / 測試介面":           ["3711", "2449", "6239", "6147", "3264", "6510", "6223", "6515", "2360", "6271", "8150", "6257", "8110", "8131", "3265", "6683", "6788", "7734"],
    "記憶體 / HBM":             ["2337", "2408", "3006", "2451", "4967", "8271", "8299", "3260", "6531", "8088", "3268", "6732"],
    "矽晶圓 / 材料設備 / 廠務": ["6488", "3532", "6182", "5483", "3680", "4749", "6532", "8091", "3029"],
    "PCB / 載板 / CCL":         ["3037", "8046", "3189", "2383", "2368", "6274", "4958", "6269", "2313", "6191", "5469", "2367"],
    "AI伺服器 / 機櫃組裝":      ["2317", "3231", "2382", "6669", "2356", "3706", "4938", "8210", "3013", "2395", "6414", "6166", "3088", "8050", "3022", "3416", "2324"],
    "散熱":                      ["3017", "3324", "2421", "3653", "4931"],
    "電源 / BBU":                ["2308", "6409", "6412", "6282", "6121", "3211"],
    "網通 / 光通訊 / CPO":       ["2345", "5388", "3596", "6285", "4906", "3450", "4979", "3163", "3363", "3081", "4908", "6442", "6451"],
    "高速互連 / 連接器 / 線材":  ["3023", "3665", "6279", "6205", "3217", "3376", "6805"],
}

GROUP_BY_CODE = {code: group for group, codes in MANUAL_GROUPS.items() for code in codes}


@dataclass
class StockRow:
    code:          str
    name:          str
    market:        str
    group:         str
    stage:         str
    price:         float | None
    change_pct:    float | None
    volume_shares: float | None
    capital_100m:  float | None
    eps:           float | None
    yoy:           float | None
    mom:           float | None
    scope:         str


# ════════════════════════════════════════════════════════════════════════════
#  資料抓取（原版完整保留）
# ════════════════════════════════════════════════════════════════════════════

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


def roc_date_to_ad(roc_date: str) -> str:
    roc_date = roc_date.strip()
    return roc_date if len(roc_date) < 7 else f"{int(roc_date[:3]) + 1911}-{roc_date[3:5]}-{roc_date[5:7]}"


def load_listed_prices() -> tuple[dict[str, dict[str, Any]], str]:
    rows = fetch_json(LISTED_PRICE_URL)
    out: dict[str, dict[str, Any]] = {}
    latest_date = ""
    for row in rows:
        code = str(row.get("Code", "")).strip()
        if not re.fullmatch(r"\d{4}", code):
            continue
        close       = parse_float(row.get("ClosingPrice"))
        change      = parse_float(row.get("Change"))
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
        current    = parse_float(row.get("營業收入-當月營收"))
        prev       = parse_float(row.get("營業收入-上月營收"))
        last_year  = parse_float(row.get("營業收入-去年當月營收"))
        mom        = parse_float(row.get("營業收入-上月比較增減(%)"))
        yoy        = parse_float(row.get("營業收入-去年同月增減(%)"))
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
        close      = parse_float(row.get("Close"))
        change     = parse_float(row.get("Change"))
        prev_close = close - change if close is not None and change is not None else None
        change_pct = (change / prev_close * 100) if prev_close not in (None, 0) and change is not None else None
        ad_date    = roc_date_to_ad(str(row.get("Date", "")))
        latest_date = latest_date or ad_date
        out[code]  = {"name": str(row.get("CompanyName", "")).strip(), "price": close, "change_pct": change_pct, "volume_shares": parse_float(row.get("TradingShares")), "date": ad_date}
    return out, latest_date


def load_otc_revenue() -> tuple[dict[str, dict[str, Any]], set[str], str]:
    raw = fetch_bytes(_find_otc_revenue_url())
    fd, path = tempfile.mkstemp(suffix=".xls")
    os.close(fd)
    Path(path).write_bytes(raw)
    try:
        sheet        = xlrd.open_workbook(path).sheet_by_index(0)
        rows: dict[str, dict[str, Any]] = {}
        semi_codes: set[str] = set()
        latest_month = ""
        month_row    = str(sheet.row_values(2)[0]).strip()
        m = re.match(r"([A-Za-z]+)\s+(\d{4})", month_row)
        if m:
            latest_month = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%B %Y").strftime("%Y-%m")
        current_section = ""
        for idx in range(sheet.nrows):
            row  = sheet.row_values(idx)
            head = str(row[0]).strip() if row else ""
            if re.match(r"^\d{2}\s", head):
                current_section = head
                continue
            match = re.match(r"^(\d{4})\s+(.+?)\s*$", head)
            if not match:
                continue
            code            = match.group(1)
            prev_month      = parse_float(row[2])
            current_month   = parse_float(row[3])
            last_year_same  = parse_float(row[5])
            mom = (current_month / prev_month - 1) * 100     if current_month not in (None, 0) and prev_month     not in (None, 0) else None
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
    payload    = fetch_json(LISTED_FIN_URL.format(code=code))
    info       = payload.get("info", {})
    chart      = payload.get("chart", {})
    data       = info.get("data", {})
    eps_series = (((chart.get("eps") or {}).get("series") or [{}])[0]).get("data") or []
    result     = {
        "code":        code,
        "name":        data.get("shortName") or data.get("name") or code,
        "capital_amt": parse_float(data.get("capitalAmt")),
        "eps":         parse_float(eps_series[-1]) if eps_series else None,
    }
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def build_rows() -> tuple[list[StockRow], dict[str, Any]]:
    ensure_dirs()
    listed_prices,  listed_price_date  = load_listed_prices()
    listed_revenue, listed_month       = load_listed_revenue()
    otc_prices,     otc_price_date     = load_otc_prices()
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
                listed_financial[code] = {
                    "code": code,
                    "name": listed_prices.get(code, {}).get("name", code),
                    "capital_amt": None,
                    "eps": None,
                }

    rows: list[StockRow] = []
    for code in sorted(selected_codes):
        market      = "上市" if code in listed_prices else "上櫃"
        is_core_semi = code in listed_semi_codes or code in otc_semi_codes
        group       = GROUP_BY_CODE.get(code) or ("半導體其他" if is_core_semi else None)
        if not group:
            continue
        stage = GROUP_META[group]["stage"]
        if market == "上市":
            px, rev, fin = listed_prices.get(code, {}), listed_revenue.get(code, {}), listed_financial.get(code, {})
            capital_amt  = parse_float(fin.get("capital_amt"))
            rows.append(StockRow(
                code, str(fin.get("name") or px.get("name") or code),
                market, group, stage,
                parse_float(px.get("price")), parse_float(px.get("change_pct")),
                parse_float(px.get("volume_shares")),
                capital_amt / 100_000_000 if capital_amt is not None else None,
                parse_float(fin.get("eps")), parse_float(rev.get("yoy")), parse_float(rev.get("mom")),
                "官方半導體全覆蓋" if is_core_semi else "AI延伸硬體鏈",
            ))
        else:
            px, rev       = otc_prices.get(code, {}), otc_revenue.get(code, {})
            capital_million = parse_float(otc_cap_million.get(code))
            rows.append(StockRow(
                code, str(px.get("name") or code),
                market, group, stage,
                parse_float(px.get("price")), parse_float(px.get("change_pct")),
                parse_float(px.get("volume_shares")),
                capital_million / 100 if capital_million is not None else None,
                parse_float(otc_eps.get(code)), parse_float(rev.get("yoy")), parse_float(rev.get("mom")),
                "官方半導體全覆蓋" if is_core_semi else "AI延伸硬體鏈",
            ))

    meta = {
        "latest_price_date":    latest_price_date,
        "latest_revenue_month": latest_revenue_month,
        "listed_count":         sum(1 for r in rows if r.market == "上市"),
        "otc_count":            sum(1 for r in rows if r.market == "上櫃"),
    }
    return rows, meta


def summarize_group(rows: list[StockRow]) -> dict[str, Any]:
    changes = [r.change_pct    for r in rows if r.change_pct    is not None]
    volumes = [r.volume_shares for r in rows if r.volume_shares is not None]
    return {
        "count":      len(rows),
        "change_avg": sum(changes) / len(changes) if changes else None,
        "volume_sum": sum(volumes) if volumes else None,
    }


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
            "stocks": [
                {"code": p.code, "name": p.name, "price": p.price,
                 "change_pct": p.change_pct, "volume_lots": round((p.volume_shares or 0) / 1000, 2)}
                for p in picks
            ],
        }
    return {
        "updated_at":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_price_date": meta["latest_price_date"],
        "themes":            themes,
    }


# ════════════════════════════════════════════════════════════════════════════
#  HTML 生成（全面重設計）
# ════════════════════════════════════════════════════════════════════════════

def _fmt_num(v: float | None, digits: int = 2) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:,.{digits}f}"

def _fmt_pct(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"

def _fmt_vol(v: float | None) -> str:
    return "—" if v is None else f"{v / 1000:,.0f}"

def _trend(v: float | None) -> str:
    if v is None: return "na"
    return "up" if v > 0 else ("down" if v < 0 else "flat")

def _heat_color(v: float | None) -> str:
    if v is None:
        return "rgba(100,116,139,.18)"
    mag   = min(abs(v) / 6.0, 1.0)
    alpha = 0.13 + mag * 0.47
    return f"rgba(239,68,68,{alpha:.3f})" if v > 0 else (
        f"rgba(34,197,94,{alpha:.3f})"  if v < 0 else "rgba(245,158,11,.22)")


def _css_block() -> str:
    return """
  <style>
    /* ── Google Font ───────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;700;900&display=swap');

    /* ── CSS Variables ─────────────────────────────────── */
    :root {
      --bg:     #060d19;
      --sur:    #0a1526;
      --sur2:   #0d1c33;
      --bdr:    #162c47;
      --bdr2:   #1a3558;
      --text:   #e8f1ff;
      --muted:  #6b85a8;
      --cyan:   #38d1ff;
      --up:     #ef4444;
      --down:   #22c55e;
      --flat:   #f59e0b;
    }

    /* ── Reset ─────────────────────────────────────────── */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html { scroll-behavior: smooth; }
    body {
      background-color: var(--bg);
      background-image:
        radial-gradient(ellipse 60% 50% at 15% 10%, rgba(124,58,237,.07) 0%, transparent 60%),
        radial-gradient(ellipse 50% 40% at 85% 90%, rgba(56,209,255,.05) 0%, transparent 60%),
        linear-gradient(rgba(14,28,49,.7) 1px, transparent 1px),
        linear-gradient(90deg, rgba(14,28,49,.7) 1px, transparent 1px);
      background-size: auto, auto, 56px 56px, 56px 56px;
      color: var(--text);
      font-family: "Noto Sans TC", "Segoe UI", system-ui, sans-serif;
      font-size: 14px;
      line-height: 1.5;
    }

    /* ── Layout ────────────────────────────────────────── */
    .page { max-width: 1640px; margin: 0 auto; padding: 20px 20px 48px; }

    /* ── Header ────────────────────────────────────────── */
    .header { margin-bottom: 20px; }
    .header-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
    .header-title h1 {
      font-size: 30px; font-weight: 900; letter-spacing: -.5px;
      background: linear-gradient(135deg, #e8f1ff 0%, #38d1ff 100%);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .header-sub { margin-top: 6px; color: var(--muted); font-size: 11px; letter-spacing: .12em; text-transform: uppercase; }

    /* ── KPI Strip ─────────────────────────────────────── */
    .kpi-strip { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }
    .kpi {
      background: linear-gradient(135deg, rgba(10,21,38,.97), rgba(13,28,51,.97));
      border: 1px solid var(--bdr); border-radius: 16px;
      padding: 14px 20px; min-width: 116px;
      backdrop-filter: blur(6px);
    }
    .kpi .lbl { color: var(--muted); font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .09em; }
    .kpi .val { margin-top: 8px; font-size: 22px; font-weight: 900; line-height: 1; }

    /* ── Section Label ─────────────────────────────────── */
    .sec-label {
      display: flex; align-items: center; gap: 9px;
      margin: 22px 0 13px;
    }
    .sec-label::before { content: ""; display: block; width: 3px; height: 16px; background: var(--cyan); border-radius: 2px; }
    .sec-label span { color: var(--cyan); font-size: 11px; font-weight: 900; letter-spacing: .16em; text-transform: uppercase; }

    /* ── Filter Pills ──────────────────────────────────── */
    .pills { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
    .pill, .btn {
      border: 1px solid var(--bdr2); background: rgba(10,21,38,.9);
      color: var(--muted); border-radius: 999px;
      padding: 8px 16px; font-size: 12px; cursor: pointer;
      text-decoration: none; transition: all .15s; white-space: nowrap;
      font-family: inherit;
    }
    .pill:hover { border-color: var(--cyan); color: var(--text); }
    .pill.active {
      border-color: var(--cyan); color: var(--text);
      background: rgba(56,209,255,.1);
    }

    /* ── Stage Heatmap ─────────────────────────────────── */
    .stage-heatmap { display: flex; flex-direction: column; gap: 10px; margin-bottom: 20px; }
    .stage-row { display: grid; grid-template-columns: 52px 22px 1fr; gap: 10px; align-items: center; }
    .stage-badge {
      writing-mode: vertical-rl; text-orientation: mixed;
      background: rgba(10,21,38,.95); border: 1px solid var(--bdr);
      border-radius: 12px; padding: 12px 6px;
      color: var(--cyan); font-weight: 900; font-size: 13px;
      text-align: center; letter-spacing: .1em;
    }
    .stage-arrow { color: var(--bdr2); font-size: 20px; text-align: center; }
    .stage-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(164px, 1fr)); gap: 10px; }
    .heat-cell {
      position: relative;
      border: 1px solid rgba(255,255,255,.07);
      border-left: 4px solid var(--accent);
      border-radius: 14px; padding: 14px 14px 12px;
      cursor: pointer; overflow: hidden;
      transition: transform .12s, box-shadow .12s;
      background: var(--heat-bg);
    }
    .heat-cell:hover { transform: translateY(-3px); box-shadow: 0 10px 30px rgba(0,0,0,.5); }
    .heat-name  { font-size: 13px; font-weight: 800; line-height: 1.3; }
    .heat-pct   { font-size: 24px; font-weight: 900; margin-top: 8px; line-height: 1; }
    .heat-vol   { font-size: 11px; color: rgba(232,241,255,.6); margin-top: 6px; }

    /* ── Toolbar ───────────────────────────────────────── */
    .toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
    .search-wrap {
      flex: 1 1 300px; display: flex; align-items: center; gap: 10px;
      background: rgba(10,21,38,.95); border: 1px solid var(--bdr2);
      border-radius: 12px; padding: 11px 16px; transition: border-color .15s;
    }
    .search-wrap:focus-within { border-color: var(--cyan); }
    .search-wrap svg { flex-shrink: 0; color: var(--muted); }
    .search-wrap input {
      width: 100%; border: none; outline: none;
      background: transparent; color: var(--text); font-size: 14px;
      font-family: inherit;
    }
    .search-wrap input::placeholder { color: var(--muted); }
    .toolbar-right { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .toolbar-meta { color: var(--muted); font-size: 12px; }

    /* ── Group Cards ───────────────────────────────────── */
    .group-cards { display: flex; flex-direction: column; gap: 14px; }
    .group-card {
      background: linear-gradient(180deg, rgba(10,21,38,.98) 0%, rgba(7,14,27,.98) 100%);
      border: 1px solid var(--bdr);
      border-left: 4px solid var(--accent);
      border-radius: 18px; overflow: hidden;
    }
    .group-head {
      display: flex; justify-content: space-between; align-items: center;
      padding: 14px 20px; gap: 12px;
      border-bottom: 1px solid rgba(22,44,71,.8);
    }
    .group-head-left {}
    .stage-tag {
      display: inline-block; padding: 3px 8px; border-radius: 6px;
      font-size: 10px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase;
      background: rgba(56,209,255,.1); color: var(--cyan); margin-bottom: 4px;
    }
    .group-head h2 { font-size: 17px; font-weight: 900; margin: 0; }
    .group-desc { color: var(--muted); font-size: 12px; margin-top: 4px; line-height: 1.5; }
    .group-head-right { display: flex; gap: 10px; align-items: center; flex-shrink: 0; }
    .group-chip {
      padding: 8px 12px; border-radius: 10px;
      background: rgba(255,255,255,.05);
      font-weight: 900; font-size: 15px; white-space: nowrap;
    }
    .group-cnt { color: var(--muted); font-size: 13px; white-space: nowrap; }

    /* ── Table ─────────────────────────────────────────── */
    .tbl-wrap { overflow-x: auto; }
    table { width: 100%; min-width: 980px; border-collapse: collapse; }
    thead th {
      background: rgba(5,11,22,.92);
      color: var(--muted); text-align: left;
      padding: 10px 12px; font-size: 11px;
      letter-spacing: .06em; text-transform: uppercase;
      border-bottom: 1px solid var(--bdr);
      white-space: nowrap; cursor: default;
    }
    thead th.sort { cursor: pointer; user-select: none; }
    thead th.sort:hover { color: var(--cyan); }
    thead th.sort::after { content: " ⇅"; opacity: .4; }
    thead th.sort[data-order="asc"]::after  { content: " ↑"; opacity: 1; color: var(--cyan); }
    thead th.sort[data-order="desc"]::after { content: " ↓"; opacity: 1; color: var(--cyan); }
    tbody td {
      padding: 11px 12px;
      border-bottom: 1px solid rgba(22,44,71,.45);
      font-size: 13px; vertical-align: middle;
    }
    tbody tr:last-child td { border-bottom: none; }
    tbody tr:hover { background: rgba(14,31,49,.4); }
    .c-code { color: var(--cyan); font-size: 15px; font-weight: 900; display: block; line-height: 1.1; }
    .c-mkt  { color: var(--muted); font-size: 10px; margin-top: 2px; display: block; }
    .c-name { font-size: 14px; font-weight: 800; }
    .role-badge {
      display: inline-flex; padding: 5px 8px; border-radius: 8px;
      border: 1px solid rgba(56,209,255,.14);
      background: rgba(8,22,44,.9);
      color: #7aaddd; font-size: 11px; line-height: 1.3;
      max-width: 190px; word-break: break-all;
    }
    .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .up   { color: var(--up);   }
    .down { color: var(--down); }
    .flat { color: var(--flat); }
    .na   { color: #4f6680;     }

    /* ── Footer ────────────────────────────────────────── */
    .footer {
      margin-top: 28px; padding-top: 20px;
      border-top: 1px solid var(--bdr);
      color: var(--muted); font-size: 11px; line-height: 2;
    }
    .footer a { color: var(--cyan); text-decoration: none; }
    .footer a:hover { text-decoration: underline; }

    /* ── Responsive ────────────────────────────────────── */
    @media (max-width: 880px) {
      .stage-row { grid-template-columns: 1fr; }
      .stage-badge { writing-mode: horizontal-tb; }
      .stage-arrow { display: none; }
      .header-top { flex-direction: column; }
    }
  </style>
"""


def _make_table_rows(rows: list[StockRow], rev_month: str) -> str:
    parts = []
    for r in rows:
        parts.append(f"""
      <tr data-code="{r.code}" data-group="{html.escape(r.group)}"
          data-price="{'' if r.price         is None else r.price}"
          data-change="{'' if r.change_pct   is None else r.change_pct}"
          data-volume="{'' if r.volume_shares is None else r.volume_shares}"
          data-capital="{'' if r.capital_100m is None else r.capital_100m}"
          data-eps="{'' if r.eps is None else r.eps}"
          data-yoy="{'' if r.yoy is None else r.yoy}"
          data-mom="{'' if r.mom is None else r.mom}">
        <td>
          <span class="c-code">{r.code}</span>
          <span class="c-mkt">{r.market}</span>
        </td>
        <td><span class="c-name">{html.escape(r.name)}</span></td>
        <td><span class="role-badge">{html.escape(r.group)}</span></td>
        <td class="num">{_fmt_num(r.price)}</td>
        <td class="num {_trend(r.change_pct)}">{_fmt_pct(r.change_pct)}</td>
        <td class="num">{_fmt_vol(r.volume_shares)}</td>
        <td class="num">{_fmt_num(r.capital_100m, 1)}</td>
        <td class="num">{_fmt_num(r.eps)}</td>
        <td class="num {_trend(r.yoy)}">{_fmt_pct(r.yoy)}</td>
        <td class="num {_trend(r.mom)}">{_fmt_pct(r.mom)}</td>
      </tr>""")
    return "".join(parts)


def build_html(rows: list[StockRow], meta: dict[str, Any]) -> str:
    grouped: dict[str, list[StockRow]] = defaultdict(list)
    for r in rows:
        grouped[r.group].append(r)

    # ── KPI 計算 ────────────────────────────────────────────────────────
    total_cap  = sum(r.capital_100m or 0 for r in rows)
    changes    = [r.change_pct    for r in rows if r.change_pct    is not None]
    volumes    = [r.volume_shares for r in rows if r.volume_shares is not None]
    avg_change = sum(changes) / len(changes) if changes else None
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rev_month  = meta["latest_revenue_month"]

    # ── Filter Pills ────────────────────────────────────────────────────
    pills_html = '<button class="pill active" data-filter="all">全部</button>\n'
    for group in GROUP_ORDER:
        if grouped.get(group):
            pills_html += f'      <button class="pill" data-filter="{html.escape(group)}">{html.escape(group)}</button>\n'

    # ── Stage Heatmap ────────────────────────────────────────────────────
    stage_rows_html = ""
    for stage_name, groups in STAGE_FLOW:
        cells = ""
        for group in groups:
            g_rows = grouped.get(group, [])
            if not g_rows:
                continue
            gsum   = summarize_group(g_rows)
            accent = GROUP_META[group]["color"]
            bg     = _heat_color(gsum["change_avg"])
            tc     = _trend(gsum["change_avg"])
            cells += f"""
          <button class="heat-cell {tc}" data-filter="{html.escape(group)}"
                  style="--accent:{accent}; --heat-bg:{bg};">
            <div class="heat-name">{html.escape(group)}</div>
            <div class="heat-pct  {tc}">{_fmt_pct(gsum['change_avg'])}</div>
            <div class="heat-vol">{_fmt_vol(gsum['volume_sum'])} 張</div>
          </button>"""
        stage_rows_html += f"""
      <div class="stage-row">
        <div class="stage-badge">{stage_name}</div>
        <div class="stage-arrow">→</div>
        <div class="stage-grid">{cells}</div>
      </div>"""

    # ── Group Sections ────────────────────────────────────────────────────
    sections_html = ""
    for group in GROUP_ORDER:
        items = grouped.get(group, [])
        if not items:
            continue
        gsum   = summarize_group(items)
        meta_g = GROUP_META[group]
        accent = meta_g["color"]
        tc     = _trend(gsum["change_avg"])
        sections_html += f"""
    <section class="group-card" data-group="{html.escape(group)}" style="--accent:{accent};">
      <div class="group-head">
        <div class="group-head-left">
          <div class="stage-tag">{meta_g['stage']}</div>
          <h2>{html.escape(group)}</h2>
          <div class="group-desc">{meta_g['desc']}</div>
        </div>
        <div class="group-head-right">
          <span class="group-chip {tc}">{_fmt_pct(gsum['change_avg'])}</span>
          <span class="group-cnt">{gsum['count']} 檔</span>
        </div>
      </div>
      <div class="tbl-wrap">
        <table data-group="{html.escape(group)}">
          <thead>
            <tr>
              <th>代號</th>
              <th>公司名稱</th>
              <th>角色 / 族群</th>
              <th class="sort num" data-key="price">股價</th>
              <th class="sort num" data-key="change">漲跌幅</th>
              <th class="sort num" data-key="volume">成交量(張)</th>
              <th class="sort num" data-key="capital">資本額(億)</th>
              <th class="sort num" data-key="eps">EPS</th>
              <th class="sort num" data-key="yoy">{rev_month} YoY</th>
              <th class="sort num" data-key="mom">{rev_month} MoM</th>
            </tr>
          </thead>
          <tbody>
            {_make_table_rows(items, rev_month)}
          </tbody>
        </table>
      </div>
    </section>"""

    # ── JavaScript ────────────────────────────────────────────────────────
    js = """
  <script>
    // ── Filter ─────────────────────────────────────────────────────
    const pills    = [...document.querySelectorAll('.pill')];
    const heatCells= [...document.querySelectorAll('.heat-cell')];
    const cards    = [...document.querySelectorAll('.group-card')];
    let activeFilter = 'all';

    function setFilter(f) {
      activeFilter = f;
      pills.forEach(p => p.classList.toggle('active', p.dataset.filter === f));
      applyFilters();
    }

    pills.forEach(p => p.addEventListener('click', () => setFilter(p.dataset.filter)));
    heatCells.forEach(c => c.addEventListener('click', () => setFilter(c.dataset.filter)));

    // ── Search ─────────────────────────────────────────────────────
    const searchInput = document.getElementById('searchInput');
    searchInput.addEventListener('input', applyFilters);

    function applyFilters() {
      const term = searchInput.value.trim().toLowerCase();
      cards.forEach(card => {
        const grp     = card.dataset.group;
        const allowed = activeFilter === 'all' || activeFilter === grp;
        let vis = 0;
        card.querySelectorAll('tbody tr').forEach(row => {
          const match = !term ||
            row.dataset.code?.toLowerCase().includes(term) ||
            row.querySelector('.c-name')?.textContent.toLowerCase().includes(term);
          const ok = allowed && match;
          row.style.display = ok ? '' : 'none';
          if (ok) vis++;
        });
        card.style.display = vis ? '' : 'none';
      });
    }

    // ── Sort ───────────────────────────────────────────────────────
    document.querySelectorAll('th.sort').forEach(th => {
      th.addEventListener('click', () => {
        const table  = th.closest('table');
        const key    = th.dataset.key;
        const next   = th.dataset.order === 'asc' ? 'desc' : 'asc';
        table.querySelectorAll('th.sort').forEach(x => delete x.dataset.order);
        th.dataset.order = next;
        const tbody  = table.querySelector('tbody');
        const trows  = [...tbody.querySelectorAll('tr')];
        trows.sort((a, b) => {
          const av = a.dataset[key] === '' ? null : Number(a.dataset[key]);
          const bv = b.dataset[key] === '' ? null : Number(b.dataset[key]);
          if (av == null && bv == null) return 0;
          if (av == null) return 1;
          if (bv == null) return -1;
          return next === 'asc' ? av - bv : bv - av;
        });
        trows.forEach(r => tbody.appendChild(r));
      });
    });

    // ── Live injection hook (called by streamlit_app.py if needed) ──
    window.__injectLive = function(quotes) {
      const fmtP = v => v == null ? '—' : Number(v).toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2});
      const fmtI = v => v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US');
      const fmtPct = v => { if (v==null) return '—'; const s=Number(v)>0?'+':''; return `${s}${Number(v).toFixed(2)}%`; };
      const cls   = v => v==null?'na':Number(v)>0?'up':Number(v)<0?'down':'flat';

      document.querySelectorAll('tbody tr[data-code]').forEach(row => {
        const q = quotes[row.dataset.code]; if (!q) return;
        const nums = row.querySelectorAll('td.num');
        if (nums[0]) nums[0].textContent = fmtP(q.price);
        if (nums[1]) { nums[1].textContent = fmtPct(q.change_pct); nums[1].className = `num ${cls(q.change_pct)}`; }
        if (nums[2]) nums[2].textContent = fmtI(q.volume_lots);
      });
    };

    applyFilters();
  </script>
"""

    # ── KPI 格式化 ────────────────────────────────────────────────────────
    kpi_avg_cls = _trend(avg_change)
    kpi_avg     = _fmt_pct(avg_change)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>台灣半導體 × AI 產業鏈全圖</title>
{_css_block()}
</head>
<body>
  <div class="page">

    <!-- ── Header ─────────────────────────────────────────────── -->
    <header class="header">
      <div class="header-top">
        <div class="header-title">
          <h1>台灣半導體 × AI 產業鏈全圖</h1>
          <div class="header-sub">Taiwan Semiconductor &amp; AI Supply Chain · 台股紅漲綠跌邏輯</div>
        </div>
      </div>
      <div class="kpi-strip">
        <div class="kpi"><div class="lbl">收錄檔數</div><div class="val">{len(rows)}</div></div>
        <div class="kpi"><div class="lbl">上市 / 上櫃</div><div class="val">{meta['listed_count']} / {meta['otc_count']}</div></div>
        <div class="kpi"><div class="lbl">總資本額</div><div class="val">{_fmt_num(total_cap, 0)} 億</div></div>
        <div class="kpi"><div class="lbl">平均漲跌幅</div><div class="val {kpi_avg_cls}">{kpi_avg}</div></div>
        <div class="kpi"><div class="lbl">總成交量</div><div class="val">{_fmt_vol(sum(volumes) if volumes else None)} 張</div></div>
        <div class="kpi"><div class="lbl">股價基準</div><div class="val" style="font-size:14px;">{meta['latest_price_date']}</div></div>
        <div class="kpi"><div class="lbl">更新時間</div><div class="val" style="font-size:14px;">{updated_at[11:]}</div></div>
      </div>
    </header>

    <!-- ── Filter Pills ────────────────────────────────────────── -->
    <div class="sec-label"><span>篩選族群</span></div>
    <div class="pills">
      {pills_html}
    </div>

    <!-- ── Stage Heatmap ───────────────────────────────────────── -->
    <div class="sec-label"><span>產業鏈熱力圖</span></div>
    <div class="stage-heatmap">
      {stage_rows_html}
    </div>

    <!-- ── Toolbar ─────────────────────────────────────────────── -->
    <div class="toolbar">
      <label class="search-wrap">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input id="searchInput" type="search" placeholder="搜尋代號、公司名稱或族群…">
      </label>
      <div class="toolbar-right">
        <button class="btn" onclick="location.reload()">↺ 重新整理</button>
        <span class="toolbar-meta">
          共 <b>{len(rows)}</b> 檔 ·
          上市 <b>{meta['listed_count']}</b> / 上櫃 <b>{meta['otc_count']}</b> ·
          股價 <b>{meta['latest_price_date']}</b> ·
          月報 <b>{rev_month}</b>
        </span>
      </div>
    </div>

    <!-- ── Group Cards ─────────────────────────────────────────── -->
    <div class="sec-label"><span>各族群詳細列表</span></div>
    <div class="group-cards">
      {sections_html}
    </div>

    <!-- ── Footer ─────────────────────────────────────────────── -->
    <footer class="footer">
      <div><b style="color:var(--text)">注意：</b> 紅色代表上漲、綠色代表下跌，採用台股視覺邏輯。</div>
      <div><b style="color:var(--text)">資料來源：</b> TWSE 公開資料 / TPEX 公開資料 · 自動更新，僅供參考，不構成投資建議。</div>
      <div><b style="color:var(--text)">EPS：</b> 上市最近四季合計（快取 24hr）；上櫃來自 TPEX 排行榜，精確度有限。</div>
      <div>Generated: {updated_at}</div>
    </footer>

  </div>
{js}
</body>
</html>"""


# ════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ════════════════════════════════════════════════════════════════════════════

def main() -> None:
    rows, meta = build_rows()
    OUT_FILE.write_text(build_html(rows, meta), encoding="utf-8")
    REPRESENTATIVE_JSON.write_text(
        json.dumps(build_representative_payload(rows, meta), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✓  index.html            → {OUT_FILE}")
    print(f"✓  representative_chain  → {REPRESENTATIVE_JSON}")
    print(f"   rows={len(rows)}")


if __name__ == "__main__":
    main()
