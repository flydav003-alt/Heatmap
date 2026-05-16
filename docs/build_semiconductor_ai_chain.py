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


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
)
SSL_CTX = ssl._create_unverified_context()

LISTED_PRICE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
LISTED_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
OTC_PRICE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
OTC_REVENUE_XLS_URL = "https://www.tpex.org.tw/storage/statistic/sales_revenue/en-us/O_202604.xls"
OTC_CAP_URL = "https://www.tpex.org.tw/www/en-us/company/rankCap"
OTC_EPS_URL = "https://www.tpex.org.tw/www/en-us/company/rankEPS"
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
    "IC設計 / IP / ASIC": {"stage": "上游", "desc": "AI GPU、交換晶片、BMC、IP 與客製 ASIC 的邏輯源頭。", "color": "#8b5cf6"},
    "晶圓代工 / 功率半導體": {"stage": "上游", "desc": "把設計真正做成晶片，涵蓋晶圓代工、功率元件與控制晶片量產。", "color": "#3b82f6"},
    "先進封裝 / CoWoS": {"stage": "中游", "desc": "承接 CoWoS、先進封裝、封裝材料與相關設備耗材。", "color": "#ec4899"},
    "封測 / 測試介面": {"stage": "中游", "desc": "後段封裝、測試、Probe Card、Socket 與可靠度驗證。", "color": "#f59e0b"},
    "記憶體 / HBM": {"stage": "上游", "desc": "AI 算力密度向上時，HBM、DRAM、NAND 與控制晶片一起受惠。", "color": "#7c3aed"},
    "矽晶圓 / 材料設備 / 廠務": {"stage": "上游", "desc": "矽晶圓、再生晶圓、鑽石碟、CMP、清洗、無塵室與機電工程。", "color": "#10b981"},
    "PCB / 載板 / CCL": {"stage": "中游", "desc": "ABF 載板、高速 PCB、CCL 與伺服器 / 交換器板材的訊號主幹。", "color": "#06b6d4"},
    "AI伺服器 / 機櫃組裝": {"stage": "下游", "desc": "GPU / ASIC、主機板、電源、散熱與機構整合成整機與機櫃。", "color": "#f43f5e"},
    "散熱": {"stage": "中游", "desc": "高瓦數 GPU 機櫃的風冷、液冷、均熱與機構散熱模組。", "color": "#38bdf8"},
    "電源 / BBU": {"stage": "中游", "desc": "伺服器 PSU、電源管理、BBU 與備援電力。", "color": "#f97316"},
    "網通 / 光通訊 / CPO": {"stage": "下游", "desc": "交換器、光模組、矽光子與 CPO，讓 AI 叢集真正跑得起來。", "color": "#0ea5e9"},
    "高速互連 / 連接器 / 線材": {"stage": "中游", "desc": "板內、板間、機櫃間的高速與高功率傳輸。", "color": "#22c55e"},
    "半導體其他": {"stage": "補充", "desc": "官方半導體產業別完整保留，但未手動歸到前述主題。", "color": "#64748b"},
}

STAGE_FLOW = [
    ("上游", ["IC設計 / IP / ASIC", "晶圓代工 / 功率半導體", "記憶體 / HBM", "矽晶圓 / 材料設備 / 廠務"]),
    ("中游", ["先進封裝 / CoWoS", "封測 / 測試介面", "PCB / 載板 / CCL", "散熱", "電源 / BBU", "高速互連 / 連接器 / 線材"]),
    ("下游", ["AI伺服器 / 機櫃組裝", "網通 / 光通訊 / CPO"]),
]

REPRESENTATIVE_GROUPS = {
    "ASIC": ["2454", "3443", "3035", "5274", "6643"],
    "CoWoS": ["1560", "3583", "6187", "6640", "3131"],
    "HBM": ["2337", "2408", "8299", "3260", "6531"],
    "CPO": ["4979", "4908", "3163", "3450", "3596"],
    "BBU": ["2308", "6409", "6412", "6121"],
    "伺服器": ["2317", "2382", "3231", "6669", "2356"],
    "散熱": ["3017", "3324", "2421", "3653"],
    "載板PCB": ["3037", "8046", "2383", "2368", "6274"],
}

MANUAL_GROUPS = {
    "IC設計 / IP / ASIC": ["2454", "3035", "3034", "2379", "3443", "3661", "6526", "4961", "5269", "6415", "3529", "4919", "2401", "3041", "3592", "3545", "3227", "8081", "8016", "5274", "2363", "6643", "8227", "6533"],
    "晶圓代工 / 功率半導體": ["2330", "2303", "5347", "6770", "2344", "2481", "8261", "3707", "5425", "6435", "3675", "5299", "6719"],
    "先進封裝 / CoWoS": ["1560", "3583", "6187", "6640", "3131", "3551", "3413", "8028", "4770", "3016", "5536", "5543", "3663", "6953"],
    "封測 / 測試介面": ["3711", "2449", "6239", "6147", "3264", "6510", "6223", "6515", "2360", "6271", "8150", "6257", "8110", "8131", "3265", "6683", "6788", "7734"],
    "記憶體 / HBM": ["2337", "2408", "3006", "2451", "4967", "8271", "8299", "3260", "6531", "8088", "3268", "6732"],
    "矽晶圓 / 材料設備 / 廠務": ["6488", "3532", "6182", "5483", "3680", "4749", "6532", "8091", "3029"],
    "PCB / 載板 / CCL": ["3037", "8046", "3189", "2383", "2368", "6274", "4958", "6269", "2313", "6191", "5469", "2367"],
    "AI伺服器 / 機櫃組裝": ["2317", "3231", "2382", "6669", "2356", "3706", "4938", "8210", "3013", "2395", "6414", "6166", "3088", "8050", "3022", "3416", "2324"],
    "散熱": ["3017", "3324", "2421", "3653", "4931"],
    "電源 / BBU": ["2308", "6409", "6412", "6282", "6121", "3211"],
    "網通 / 光通訊 / CPO": ["2345", "5388", "3596", "6285", "4906", "3450", "4979", "3163", "3363", "3081", "4908", "6442", "6451"],
    "高速互連 / 連接器 / 線材": ["3023", "3665", "6279", "6205", "3217", "3376", "6805"],
}

GROUP_BY_CODE = {code: group for group, codes in MANUAL_GROUPS.items() for code in codes}


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
    return "—" if value is None else f"{value / 1000:,.0f}"


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
        return "rgba(100,116,139,.20)"
    magnitude = min(abs(value) / 6.0, 1.0)
    alpha = 0.18 + magnitude * 0.38
    if value > 0:
        return f"rgba(244,63,94,{alpha:.3f})"
    if value < 0:
        return f"rgba(34,197,94,{alpha:.3f})"
    return "rgba(251,191,36,.22)"


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
        close = parse_float(row.get("ClosingPrice"))
        change = parse_float(row.get("Change"))
        prev_close = close - change if close is not None and change is not None else None
        change_pct = (change / prev_close * 100) if prev_close not in (None, 0) and change is not None else None
        ad_date = roc_date_to_ad(str(row.get("Date", "")))
        latest_date = latest_date or ad_date
        out[code] = {"name": str(row.get("Name", "")).strip(), "price": close, "change_pct": change_pct, "volume_shares": parse_float(row.get("TradeVolume")), "date": ad_date}
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
        current = parse_float(row.get("營業收入-當月營收"))
        prev = parse_float(row.get("營業收入-上月營收"))
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
        close = parse_float(row.get("Close"))
        change = parse_float(row.get("Change"))
        prev_close = close - change if close is not None and change is not None else None
        change_pct = (change / prev_close * 100) if prev_close not in (None, 0) and change is not None else None
        ad_date = roc_date_to_ad(str(row.get("Date", "")))
        latest_date = latest_date or ad_date
        out[code] = {"name": str(row.get("CompanyName", "")).strip(), "price": close, "change_pct": change_pct, "volume_shares": parse_float(row.get("TradingShares")), "date": ad_date}
    return out, latest_date


def load_otc_revenue() -> tuple[dict[str, dict[str, Any]], set[str], str]:
    raw = fetch_bytes(OTC_REVENUE_XLS_URL)
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
            code = match.group(1)
            prev_month = parse_float(row[2])
            current_month = parse_float(row[3])
            last_year_same = parse_float(row[5])
            mom = (current_month / prev_month - 1) * 100 if current_month not in (None, 0) and prev_month not in (None, 0) else None
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
    payload = fetch_json(LISTED_FIN_URL.format(code=code))
    info = payload.get("info", {})
    chart = payload.get("chart", {})
    data = info.get("data", {})
    eps_series = (((chart.get("eps") or {}).get("series") or [{}])[0]).get("data") or []
    result = {"code": code, "name": data.get("shortName") or data.get("name") or code, "capital_amt": parse_float(data.get("capitalAmt")), "eps": parse_float(eps_series[-1]) if eps_series else None}
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def build_rows() -> tuple[list[StockRow], dict[str, Any]]:
    ensure_dirs()
    listed_prices, listed_price_date = load_listed_prices()
    listed_revenue, listed_month = load_listed_revenue()
    otc_prices, otc_price_date = load_otc_prices()
    otc_revenue, otc_semi_codes, otc_month = load_otc_revenue()
    otc_cap_million = load_otc_rank(OTC_CAP_URL)
    otc_eps = load_otc_rank(OTC_EPS_URL)

    latest_price_date = max(x for x in [listed_price_date, otc_price_date] if x)
    latest_revenue_month = max(x for x in [listed_month, otc_month] if x)

    listed_semi_codes = {code for code, row in listed_revenue.items() if row.get("industry") in RELEVANT_LISTED_INDUSTRIES}
    selected_codes = listed_semi_codes | otc_semi_codes | set(GROUP_BY_CODE)
    listed_codes = sorted(code for code in selected_codes if code in listed_prices)

    listed_financial: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(load_listed_financial, code): code for code in listed_codes}
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                listed_financial[code] = future.result()
            except Exception:
                listed_financial[code] = {"code": code, "name": listed_prices.get(code, {}).get("name", code), "capital_amt": None, "eps": None}

    rows: list[StockRow] = []
    for code in sorted(selected_codes):
        market = "上市" if code in listed_prices else "上櫃"
        is_core_semi = code in listed_semi_codes or code in otc_semi_codes
        group = GROUP_BY_CODE.get(code) or ("半導體其他" if is_core_semi else None)
        if not group:
            continue
        stage = GROUP_META[group]["stage"]
        if market == "上市":
            px, rev, fin = listed_prices.get(code, {}), listed_revenue.get(code, {}), listed_financial.get(code, {})
            capital_amt = parse_float(fin.get("capital_amt"))
            rows.append(StockRow(code, str(fin.get("name") or px.get("name") or code), market, group, stage, parse_float(px.get("price")), parse_float(px.get("change_pct")), parse_float(px.get("volume_shares")), capital_amt / 100_000_000 if capital_amt is not None else None, parse_float(fin.get("eps")), parse_float(rev.get("yoy")), parse_float(rev.get("mom")), "官方半導體全覆蓋" if is_core_semi else "AI延伸硬體鏈"))
        else:
            px, rev = otc_prices.get(code, {}), otc_revenue.get(code, {})
            capital_million = parse_float(otc_cap_million.get(code))
            rows.append(StockRow(code, str(px.get("name") or code), market, group, stage, parse_float(px.get("price")), parse_float(px.get("change_pct")), parse_float(px.get("volume_shares")), capital_million / 100 if capital_million is not None else None, parse_float(otc_eps.get(code)), parse_float(rev.get("yoy")), parse_float(rev.get("mom")), "官方半導體全覆蓋" if is_core_semi else "AI延伸硬體鏈"))

    meta = {"latest_price_date": latest_price_date, "latest_revenue_month": latest_revenue_month, "listed_count": sum(1 for r in rows if r.market == "上市"), "otc_count": sum(1 for r in rows if r.market == "上櫃")}
    return rows, meta


def summarize_group(rows: list[StockRow]) -> dict[str, Any]:
    changes = [r.change_pct for r in rows if r.change_pct is not None]
    volumes = [r.volume_shares for r in rows if r.volume_shares is not None]
    return {
        "count": len(rows),
        "change_avg": sum(changes) / len(changes) if changes else None,
        "volume_sum": sum(volumes) if volumes else None,
    }


def make_table_rows(rows: list[StockRow]) -> str:
    rendered = []
    for r in rows:
        rendered.append(
            f"""
            <tr data-code="{r.code}" data-name="{html.escape(r.name)}" data-group="{html.escape(r.group)}"
                data-price="{'' if r.price is None else r.price}" data-change="{'' if r.change_pct is None else r.change_pct}"
                data-volume="{'' if r.volume_shares is None else r.volume_shares}" data-capital="{'' if r.capital_100m is None else r.capital_100m}"
                data-eps="{'' if r.eps is None else r.eps}" data-yoy="{'' if r.yoy is None else r.yoy}" data-mom="{'' if r.mom is None else r.mom}">
              <td class="code-cell"><span class="code">{r.code}</span><span class="market">{r.market}</span></td>
              <td class="name-cell"><div class="name">{html.escape(r.name)}</div></td>
              <td><span class="role-tag">{html.escape(r.group)}</span></td>
              <td class="num">{fmt_num(r.price, 2)}</td>
              <td class="num {trend_class(r.change_pct)}">{fmt_pct(r.change_pct)}</td>
              <td class="num">{fmt_volume_lots(r.volume_shares)}</td>
              <td class="num">{fmt_num(r.capital_100m, 1)}</td>
              <td class="num">{fmt_num(r.eps, 2)}</td>
              <td class="num {trend_class(r.yoy)}">{fmt_pct(r.yoy)}</td>
              <td class="num {trend_class(r.mom)}">{fmt_pct(r.mom)}</td>
            </tr>
            """
        )
    return "".join(rendered)


def build_representative_payload(rows: list[StockRow], meta: dict[str, Any]) -> dict[str, Any]:
    row_map = {r.code: r for r in rows}
    themes = {}
    for theme, codes in REPRESENTATIVE_GROUPS.items():
        picks = [row_map[c] for c in codes if c in row_map]
        if not picks:
            continue
        changes = [p.change_pct for p in picks if p.change_pct is not None]
        vol_sum = sum((p.volume_shares or 0) for p in picks)
        themes[theme] = {
            "avg_change_pct": round(sum(changes) / len(changes), 4) if changes else None,
            "volume_lots": round(vol_sum / 1000, 2),
            "stocks": [{"code": p.code, "name": p.name, "price": p.price, "change_pct": p.change_pct, "volume_lots": round((p.volume_shares or 0) / 1000, 2)} for p in picks],
        }
    return {"updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "latest_price_date": meta["latest_price_date"], "themes": themes}


def build_html(rows: list[StockRow], meta: dict[str, Any]) -> str:
    grouped: dict[str, list[StockRow]] = defaultdict(list)
    for row in rows:
        grouped[row.group].append(row)

    stage_blocks = []
    for stage_name, groups in STAGE_FLOW:
        cells = []
        for group in groups:
            g_rows = grouped.get(group, [])
            if not g_rows:
                continue
            g_sum = summarize_group(g_rows)
            bg = taiwan_heat_color(g_sum["change_avg"])
            border = GROUP_META[group]["color"]
            cells.append(
                f"""
                <button class="stage-heat-cell {trend_class(g_sum['change_avg'])}" data-filter="{html.escape(group)}" style="--accent:{border}; --heat:{bg}">
                  <div class="stage-heat-name">{html.escape(group)}</div>
                  <div class="stage-heat-change">{fmt_pct(g_sum['change_avg'])}</div>
                  <div class="stage-heat-volume">{fmt_volume_lots(g_sum['volume_sum'])} 張</div>
                </button>
                """
            )
        stage_blocks.append(
            f"""
            <section class="stage-block">
              <div class="stage-label">{stage_name}</div>
              <div class="stage-arrow">→</div>
              <div class="stage-grid">{''.join(cells)}</div>
            </section>
            """
        )

    sections = []
    for group in GROUP_ORDER:
        items = grouped.get(group, [])
        if not items:
            continue
        summary = summarize_group(items)
        color = GROUP_META[group]["color"]
        sections.append(
            f"""
            <section class="group-card" data-group="{html.escape(group)}" style="--accent:{color}">
              <div class="group-header">
                <div class="group-left">
                  <div class="group-stage">{GROUP_META[group]['stage']}</div>
                  <h2>{html.escape(group)}</h2>
                </div>
                <div class="group-right">
                  <span class="group-chip {trend_class(summary['change_avg'])}">{fmt_pct(summary['change_avg'])}</span>
                  <span class="group-count">{summary['count']} 檔</span>
                </div>
              </div>
              <div class="table-wrap">
                <table class="sortable-table">
                  <thead>
                    <tr>
                      <th>代號</th>
                      <th>公司名稱</th>
                      <th>角色定位</th>
                      <th data-sort="price" class="sortable">最新股價</th>
                      <th data-sort="change" class="sortable">漲跌幅</th>
                      <th data-sort="volume" class="sortable">成交量(張)</th>
                      <th data-sort="capital" class="sortable">資本額</th>
                      <th data-sort="eps" class="sortable">EPS</th>
                      <th data-sort="yoy" class="sortable">{meta['latest_revenue_month']} YoY</th>
                      <th data-sort="mom" class="sortable">{meta['latest_revenue_month']} MoM</th>
                    </tr>
                  </thead>
                  <tbody>{make_table_rows(items)}</tbody>
                </table>
              </div>
            </section>
            """
        )

    total_cap = sum(r.capital_100m or 0 for r in rows)
    changes = [r.change_pct for r in rows if r.change_pct is not None]
    volumes = [r.volume_shares for r in rows if r.volume_shares is not None]
    avg_change = sum(changes) / len(changes) if changes else None
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>台灣半導體 × AI 產業鏈全圖</title>
  <style>
    :root {{
      --bg:#08111f; --panel:#0d1728; --panel2:#101c31; --line:#17304a; --line2:#1b3a59;
      --text:#eaf2ff; --muted:#7f93b2; --cyan:#44c7ff; --up:#f43f5e; --down:#22c55e; --flat:#fbbf24;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; color:var(--text); font-family:"Noto Sans TC","Segoe UI",sans-serif;
      background:
        linear-gradient(rgba(14,31,49,.82) 1px, transparent 1px),
        linear-gradient(90deg, rgba(14,31,49,.82) 1px, transparent 1px),
        linear-gradient(180deg,#07111d 0%, #091423 100%);
      background-size:64px 64px,64px 64px,auto;
    }}
    .wrap {{ max-width:1580px; margin:0 auto; padding:18px 16px 34px; }}
    .hero {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:14px; flex-wrap:wrap; }}
    .title h1 {{ margin:0; font-size:24px; line-height:1.15; }}
    .subtitle {{ margin-top:5px; color:var(--muted); font-size:12px; letter-spacing:.08em; text-transform:uppercase; }}
    .kpis {{ display:flex; gap:8px; flex-wrap:wrap; }}
    .kpi {{ min-width:112px; background:rgba(12,22,38,.9); border:1px solid var(--line); border-radius:14px; padding:11px 13px; }}
    .kpi .label {{ color:var(--muted); font-size:11px; }}
    .kpi .value {{ margin-top:6px; font-size:19px; font-weight:900; }}
    .top-pills {{ display:flex; gap:8px; flex-wrap:wrap; margin:12px 0; }}
    .pill, .toolbar-btn {{
      border:1px solid var(--line2); background:rgba(13,23,40,.84); color:var(--muted);
      border-radius:999px; padding:10px 14px; font-size:13px; cursor:pointer; text-decoration:none;
    }}
    .pill.active {{ border-color:var(--cyan); color:var(--text); }}
    .stage-heat-map {{ display:grid; gap:10px; margin-bottom:16px; }}
    .stage-block {{ display:grid; grid-template-columns:68px 24px 1fr; gap:10px; align-items:stretch; }}
    .stage-label {{
      writing-mode:vertical-rl; text-orientation:mixed; background:rgba(13,23,40,.92); border:1px solid var(--line);
      border-radius:12px; padding:10px 6px; color:var(--cyan); font-weight:800; text-align:center;
    }}
    .stage-arrow {{ display:grid; place-items:center; color:var(--muted); font-size:22px; }}
    .stage-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(152px,1fr)); gap:10px; }}
    .stage-heat-cell {{
      background:var(--heat); border:1px solid rgba(255,255,255,.06); border-left:4px solid var(--accent);
      border-radius:14px; padding:12px 12px 11px; text-align:left; cursor:pointer; color:var(--text);
      box-shadow:inset 0 1px 0 rgba(255,255,255,.03);
    }}
    .stage-heat-name {{ font-size:13px; font-weight:800; line-height:1.25; }}
    .stage-heat-change {{ margin-top:8px; font-size:21px; font-weight:900; }}
    .stage-heat-volume {{ margin-top:5px; color:rgba(234,242,255,.82); font-size:12px; }}
    .toolbar {{ display:flex; justify-content:space-between; gap:10px; align-items:center; margin:10px 0 16px; flex-wrap:wrap; }}
    .search {{ flex:1 1 340px; display:flex; align-items:center; gap:8px; background:rgba(13,23,40,.9); border:1px solid var(--line); border-radius:14px; padding:12px 14px; }}
    .search input {{ width:100%; border:none; outline:none; background:transparent; color:var(--text); font-size:14px; }}
    .toolbar-right {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
    .toolbar-meta {{ color:var(--muted); font-size:13px; }}
    .group-card {{ background:linear-gradient(180deg, rgba(13,23,40,.98), rgba(11,20,35,.98)); border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:18px; overflow:hidden; margin-bottom:13px; }}
    .group-header {{ display:flex; justify-content:space-between; gap:10px; align-items:center; padding:13px 15px; border-bottom:1px solid rgba(23,48,74,.88); }}
    .group-stage {{ color:var(--accent); font-size:11px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }}
    .group-header h2 {{ margin:4px 0 0; font-size:17px; }}
    .group-right {{ display:flex; gap:9px; align-items:center; }}
    .group-chip {{ border-radius:10px; padding:7px 10px; background:rgba(255,255,255,.05); font-weight:900; }}
    .group-count {{ color:var(--muted); font-size:13px; }}
    .table-wrap {{ overflow:auto; }}
    table {{ width:100%; min-width:960px; border-collapse:collapse; }}
    thead th {{ background:rgba(8,17,31,.88); color:var(--muted); text-align:left; padding:11px 9px; font-size:12px; white-space:nowrap; border-bottom:1px solid rgba(23,48,74,.9); }}
    thead th.sortable {{ cursor:pointer; }}
    tbody td {{ padding:11px 9px; border-bottom:1px solid rgba(23,48,74,.66); font-size:13px; vertical-align:middle; }}
    tbody tr:hover {{ background:rgba(14,31,49,.45); }}
    .code-cell {{ width:66px; }}
    .code {{ display:block; color:var(--cyan); font-size:15px; font-weight:900; line-height:1.1; }}
    .market {{ display:block; margin-top:3px; color:var(--muted); font-size:11px; }}
    .name-cell {{ width:96px; }}
    .name {{ font-size:15px; font-weight:800; line-height:1.15; }}
    .role-tag {{ display:inline-flex; max-width:138px; padding:6px 8px; border-radius:10px; border:1px solid rgba(68,199,255,.16); background:rgba(16,28,49,.88); color:#88aee3; font-size:12px; line-height:1.2; }}
    td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; font-size:14px; }}
    .up {{ color:var(--up); }} .down {{ color:var(--down); }} .flat {{ color:var(--flat); }} .na {{ color:#7a8ea9; }}
    .footnote {{ margin-top:14px; color:var(--muted); font-size:12px; line-height:1.65; }}
    .footnote a {{ color:var(--cyan); }}
    @media (max-width:980px) {{ .stage-block {{ grid-template-columns:1fr; }} .stage-label {{ writing-mode:horizontal-tb; }} .stage-arrow {{ display:none; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="title">
        <h1>台灣半導體 × AI 產業鏈全圖</h1>
        <div class="subtitle">TAIWAN SEMICONDUCTOR &amp; AI SUPPLY CHAIN · 台股紅漲綠跌邏輯</div>
      </div>
      <div class="kpis">
        <div class="kpi"><div class="label">收錄檔數</div><div class="value">{len(rows)}</div></div>
        <div class="kpi"><div class="label">總資本額</div><div class="value">{fmt_num(total_cap, 0)}億</div></div>
        <div class="kpi"><div class="label">平均漲跌幅</div><div class="value {trend_class(avg_change)}">{fmt_pct(avg_change)}</div></div>
        <div class="kpi"><div class="label">總成交量</div><div class="value">{fmt_volume_lots(sum(volumes) if volumes else None)}張</div></div>
        <div class="kpi"><div class="label">更新</div><div class="value" style="font-size:14px">{updated_at[11:]}</div></div>
      </div>
    </section>
    <div class="top-pills">
      <button class="pill active" data-filter="all">全部產業鏈</button>
      {''.join(f'<button class="pill" data-filter="{html.escape(group)}">{html.escape(group)}</button>' for group in GROUP_ORDER if grouped.get(group))}
    </div>
    <section class="stage-heat-map">{''.join(stage_blocks)}</section>
    <div class="toolbar">
      <label class="search"><span>🔍</span><input id="searchInput" type="search" placeholder="搜尋股票代號、公司名稱、族群..."></label>
      <div class="toolbar-right">
        <button class="toolbar-btn" id="reloadBtn">重新整理頁面</button>
        <a class="toolbar-btn" href="./GITHUB_DEPLOY_GUIDE.md">如何更新資料</a>
        <div class="toolbar-meta">共 <b>{len(rows)}</b> 檔 | 上市 <b>{meta['listed_count']}</b> / 上櫃 <b>{meta['otc_count']}</b> | 股價基準 <b>{meta['latest_price_date']}</b></div>
      </div>
    </div>
    {''.join(sections)}
    <section class="footnote">
      <div><b>注意：</b> 紅色代表上漲、綠色代表下跌，已全面改成台股視覺邏輯。</div>
      <div><b>更新按鈕限制：</b> GitHub Pages 純靜態頁面不能安全地直接觸發 GitHub Actions workflow，否則需要把可觸發權限暴露在前端。</div>
      <div><b>純 GitHub Actions 精簡版：</b> 可用 GitHub Secrets 存 API key，Actions 手動或定時抓「代表股」，更新本頁與 <code>representative_chain_data.json</code>。</div>
    </section>
  </div>
  <script>
    const searchInput = document.getElementById('searchInput');
    const reloadBtn = document.getElementById('reloadBtn');
    const pills = Array.from(document.querySelectorAll('.pill'));
    const stageCells = Array.from(document.querySelectorAll('.stage-heat-cell'));
    const sections = Array.from(document.querySelectorAll('.group-card'));
    let activeFilter = 'all';
    reloadBtn.addEventListener('click', () => location.reload());
    function setFilter(filter) {{
      activeFilter = filter;
      pills.forEach(btn => btn.classList.toggle('active', btn.dataset.filter === filter));
      applyFilters();
    }}
    pills.forEach(btn => btn.addEventListener('click', () => setFilter(btn.dataset.filter)));
    stageCells.forEach(btn => btn.addEventListener('click', () => setFilter(btn.dataset.filter)));
    function applyFilters() {{
      const term = searchInput.value.trim().toLowerCase();
      sections.forEach(section => {{
        const group = section.dataset.group;
        const allowed = activeFilter === 'all' || activeFilter === group;
        let visible = 0;
        section.querySelectorAll('tbody tr').forEach(row => {{
          const ok = (!term || row.innerText.toLowerCase().includes(term)) && allowed;
          row.style.display = ok ? '' : 'none';
          if (ok) visible += 1;
        }});
        section.style.display = visible ? '' : 'none';
      }});
    }}
    searchInput.addEventListener('input', applyFilters);
    document.querySelectorAll('.sortable-table').forEach(table => {{
      table.querySelectorAll('th.sortable').forEach(th => {{
        th.addEventListener('click', () => {{
          const key = th.dataset.sort;
          const tbody = table.querySelector('tbody');
          const rows = Array.from(tbody.querySelectorAll('tr'));
          const next = th.dataset.order === 'asc' ? 'desc' : 'asc';
          table.querySelectorAll('th.sortable').forEach(x => delete x.dataset.order);
          th.dataset.order = next;
          rows.sort((a, b) => {{
            const av = a.dataset[key] === '' ? null : Number(a.dataset[key]);
            const bv = b.dataset[key] === '' ? null : Number(b.dataset[key]);
            if (av === null && bv === null) return 0;
            if (av === null) return 1;
            if (bv === null) return -1;
            return next === 'asc' ? av - bv : bv - av;
          }});
          rows.forEach(r => tbody.appendChild(r));
        }});
      }});
    }});
    applyFilters();
  </script>
</body>
</html>
"""


def main() -> None:
    rows, meta = build_rows()
    OUT_FILE.write_text(build_html(rows, meta), encoding="utf-8")
    REPRESENTATIVE_JSON.write_text(json.dumps(build_representative_payload(rows, meta), ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT_FILE)
    print(REPRESENTATIVE_JSON)
    print(f"rows={len(rows)}")


if __name__ == "__main__":
    main()
