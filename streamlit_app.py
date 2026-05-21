from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf


ROOT      = Path(__file__).resolve().parent
HTML_PATH = ROOT / "docs" / "index.html"

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
    now = datetime.now()
    latest_time = now.strftime("%H:%M:%S")
    latest_date = now.strftime("%Y-%m-%d")

    def _batch_download(tickers: list[str], period: str = "7d") -> Any:
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

            # 5日收盤序列（供折線圖）: [{date, close}, ...]
            history_5d = []
            close_tail = close.tail(5)
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
                "history_5d":  history_5d,
                "time":        latest_time,
                "date":        latest_date,
            }
        except Exception:
            return None

    # period="7d" 抓7天確保有5個交易日
    raw_tw  = _batch_download(tw_tickers,  period="7d")
    raw_two = _batch_download(two_tickers, period="7d")

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
        "errors":          [],
    }


def estimate_page_height(base_html: str) -> int:
    num_rows   = base_html.count('data-code="')
    num_groups = base_html.count('class="group-card"')
    return max(5200, 1400 + num_groups * 68 + num_rows * 38)


# ── GROUP META ─────────────────────────────────────────────────────────────────
GROUP_STAGE_MAP = {
    "IC設計 / IP / ASIC":      "上游",
    "晶圓代工 / 功率半導體":    "上游",
    "記憶體 / HBM":             "上游",
    "矽晶圓 / 材料設備 / 廠務": "上游",
    "先進封裝 / CoWoS":         "中游",
    "封測 / 測試介面":           "中游",
    "PCB / 載板 / CCL":         "中游",
    "被動元件":                  "中游",
    "散熱":                      "中游",
    "電源 / BBU":                "中游",
    "高速互連 / 連接器 / 線材": "中游",
    "AI伺服器 / 機櫃組裝":      "下游",
    "網通 / 光通訊 / CPO":      "下游",
    "低軌衛星 / SpaceX":        "下游",
    "半導體其他":                "補充",
}
GROUP_COLOR_MAP = {
    "IC設計 / IP / ASIC":      "#8b5cf6",
    "晶圓代工 / 功率半導體":    "#3b82f6",
    "先進封裝 / CoWoS":         "#ec4899",
    "封測 / 測試介面":           "#f59e0b",
    "記憶體 / HBM":              "#7c3aed",
    "矽晶圓 / 材料設備 / 廠務": "#10b981",
    "PCB / 載板 / CCL":          "#06b6d4",
    "被動元件":                   "#a855f7",
    "AI伺服器 / 機櫃組裝":       "#f43f5e",
    "散熱":                       "#38bdf8",
    "電源 / BBU":                 "#f97316",
    "網通 / 光通訊 / CPO":       "#0ea5e9",
    "低軌衛星 / SpaceX":         "#6366f1",
    "高速互連 / 連接器 / 線材":  "#22c55e",
    "半導體其他":                 "#64748b",
}

CORE_UPSTREAM     = {"IC設計 / IP / ASIC", "晶圓代工 / 功率半導體"}
LAGGARD_GROUPS    = {"被動元件"}
DOWNSTREAM_GROUPS = {"AI伺服器 / 機櫃組裝", "網通 / 光通訊 / CPO"}


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
    if(volume   !=null&&nums[2]) nums[2].textContent=fmtInt(volume);

    const avgVol20=safe(row.dataset.avgVol20);
    const p5close =safe(row.dataset.p5Close);
    const p20high =safe(row.dataset.p20High);

    const group=row.dataset.group||"";
    if(!groupStats.has(group)) groupStats.set(group,{{
      sum:0,count:0,volume:0,upCount:0,totalCount:0,
      sumAvgVol20:0,cntVol20:0,sumPrice:0,cntPrice:0,
      sumP5close:0,cntP5:0,sumP20high:0,cntP20:0,
      history5d:[],   // 族群每日均漲幅序列 (5日)
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
    const hist=(quote&&quote.history_5d)||[];
    if(hist.length>=2&&p5close!=null&&p5close>0){{
      stat.history5d.push(hist);
    }}

    // 存個股資料供入場雷達
    stockData.push({{
      code, group,
      name: row.dataset.name||code,
      price, changePct, volume,
      avgVol20, p5close, p20high,
    }});
  }});

  /* ── 族群指標 + 輪動等級 ─────────────────────────────────────────────────── */
  const groupGrades = new Map();

  groupStats.forEach((stat,group)=>{{
    if(!stat.count) return;
    const avgChange=stat.sum/stat.count;
    const breadth  =stat.totalCount>0?(stat.upCount/stat.totalCount)*100:null;
    const avgVol20PerStock=stat.cntVol20>0?stat.sumAvgVol20/stat.cntVol20:null;
    const estVol20=avgVol20PerStock!=null?avgVol20PerStock*stat.cntVol20:null;
    const volRatio=(estVol20!=null&&estVol20>0&&stat.volume>0)?stat.volume/estVol20:null;
    const avgPrice  =stat.cntPrice>0?stat.sumPrice/stat.cntPrice:null;
    const avgP5close=stat.cntP5>0?stat.sumP5close/stat.cntP5:null;
    const avgP20high=stat.cntP20>0?stat.sumP20high/stat.cntP20:null;
    const mom5d   =(avgPrice!=null&&avgP5close!=null&&avgP5close>0)?(avgPrice/avgP5close-1)*100:null;
    const drawdown=(avgPrice!=null&&avgP20high!=null&&avgP20high>0)?(avgPrice/avgP20high-1)*100:null;
    const hasVol=volRatio!=null;
    let grade="D";
    if(avgChange<-1.0||(breadth!=null&&breadth<25))grade="E";
    else if(avgChange>1.2&&breadth!=null&&breadth>=65&&(hasVol?volRatio>=1.35:avgChange>2.0))grade="A";
    else if(avgChange>0.6&&breadth!=null&&breadth>=50&&(hasVol?volRatio>=1.15:avgChange>1.2))grade="B";
    else if(hasVol&&volRatio>=1.35&&drawdown!=null&&drawdown<-10&&mom5d!=null&&mom5d<3)grade="C";
    groupGrades.set(group,{{grade,stage:GROUP_STAGE[group]||"",avgChange,breadth,volRatio,mom5d,drawdown,stat}});
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
      const vr=ginfo.volRatio!=null?`量比${{ginfo.volRatio.toFixed(2)}}x`:"量比--";
      const bd=ginfo.breadth!=null?`廣度${{ginfo.breadth.toFixed(0)}}%`:"";
      infoEl.textContent=[lbl,vr,bd].filter(Boolean).join(" ");
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
    if(ve) ve.textContent=fmtInt(stat.volume)+" 張";
    if(ge&&ginfo){{
      const gr=ginfo.grade;
      const sfx=GRADE_SUFFIX[gr]||"";
      // 大字顯眼：等級 + 說明文字
      ge.innerHTML=`<span style="font-size:16px;font-weight:900;color:${{GRADE_COLOR[gr]}};
        background:${{GRADE_BG[gr]}};border:1px solid ${{GRADE_COLOR[gr]}}44;
        border-radius:5px;padding:2px 7px;letter-spacing:0.04em;">
        ${{gr}}</span>${{sfx?`<span style="font-size:10px;color:${{GRADE_COLOR[gr]}};margin-left:4px;opacity:0.85">${{sfx}}</span>`:""}}`; 
    }}
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
      const vr=gi.volRatio!=null?` 量比${{gi.volRatio.toFixed(2)}}x`:"";
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
        <div class="vol-vol">${{fmtInt(s.vol)}} 張</div>
      </div>`).join("");
  }}

  /* ══════════════════════════════════════════════════════════════════════════
     ① 族群輪動時序圖（5日折線）
     用 SVG 手繪，每個族群一條彩色折線，顯示 5 日平均漲幅趨勢
  ══════════════════════════════════════════════════════════════════════════ */
  const chartEl=document.getElementById("rotation-chart");
  if(chartEl){{
    // 計算每個族群每天的均漲幅（依個股 history_5d 反推）
    // history_5d 是收盤價序列，算相對第1天的累計漲幅%
    const groupLines=[]; // {{group, color, points:[{{d,pct}}]}}
    groupStats.forEach((stat,group)=>{{
      if(!stat.count||!stat.history5d.length)return;
      const color=GROUP_COLOR[group]||"#7a9bbb";
      // 找最長的共同日期序列
      const allHists=stat.history5d;
      if(!allHists.length)return;
      const nDays=allHists[0].length;
      if(nDays<2)return;
      const labels=allHists[0].map(h=>h.d);
      const dailyPcts=[];
      for(let i=0;i<nDays;i++){{
        const pctsToday=allHists
          .filter(h=>h.length>i&&h[0].c>0)
          .map(h=>(h[i].c/h[0].c-1)*100);
        dailyPcts.push(pctsToday.length?pctsToday.reduce((a,b)=>a+b,0)/pctsToday.length:null);
      }}
      const points=labels.map((d,i)=>({d,pct:dailyPcts[i]}));
      const shortName=group.split(" / ")[0];
      groupLines.push({{group:shortName,color,points,grade:(groupGrades.get(group)||{{}}).grade||"D"}});
    }});

    if(!groupLines.length){{chartEl.innerHTML='<div style="color:#3d5470;padding:20px;font-size:12px">歷史資料載入中…</div>';}}
    else{{
      // 找 Y 軸範圍
      let yMin=0,yMax=0;
      groupLines.forEach(l=>l.points.forEach(p=>{{ if(p.pct!=null){{ yMin=Math.min(yMin,p.pct); yMax=Math.max(yMax,p.pct); }} }} ));
      const yPad=Math.max(0.5,(yMax-yMin)*0.15);
      yMin-=yPad; yMax+=yPad;

      const W=900,H=220,PL=8,PR=90,PT=14,PB=32;
      const cW=W-PL-PR, cH=H-PT-PB;
      const nDays=groupLines[0].points.length;
      const xScale=i=>(nDays<2)?cW/2:i*(cW/(nDays-1));
      const yScale=v=>(v==null)?null:cH-(v-yMin)/(yMax-yMin)*cH;

      let paths="", labels="", dots="";
      const GRADE_COLOR_LOCAL={{A:"#ff6680",B:"#fbbf24",C:"#c084fc",D:"#3d5470",E:"#00d26e"}};

      groupLines.forEach((line,li)=>{{
        const pts=line.points.map((p,i)=>{{const y=yScale(p.pct);return y!=null?`${{xScale(i).toFixed(1)}},${{y.toFixed(1)}}`:null;}}).filter(Boolean);
        if(pts.length<2)return;
        const d="M"+pts.join("L");
        const isTop=li<3;
        paths+=`<path d="${{d}}" fill="none" stroke="${{line.color}}" stroke-width="${{isTop?"2":"1.2"}}" stroke-opacity="${{isTop?"0.9":"0.45"}}" stroke-linejoin="round" stroke-linecap="round"/>`;
        // 最後一個點的 label
        const lastPt=line.points[line.points.length-1];
        const lx=xScale(nDays-1)+6;
        const ly=yScale(lastPt.pct);
        if(ly!=null){{
          const grC=GRADE_COLOR_LOCAL[line.grade]||line.color;
          labels+=`<text x="${{lx}}" y="${{ly+4}}" fill="${{grC}}" font-size="10" font-family="IBM Plex Mono,monospace" font-weight="${{isTop?"700":"400"}}">${{line.group}}</text>`;
          // 最後一天的點
          if(lastPt.pct!=null){{
            dots+=`<circle cx="${{xScale(nDays-1)}}" cy="${{ly}}" r="${{isTop?3.5:2}}" fill="${{line.color}}" opacity="${{isTop?1:0.6}}"/>`;
          }}
        }}
      }});

      // X 軸日期標籤
      let xLabels="";
      if(nDays>0){{
        groupLines[0].points.forEach((p,i)=>{{
          const x=xScale(i);
          xLabels+=`<text x="${{x}}" y="${{cH+20}}" fill="#3d5470" font-size="10" text-anchor="middle" font-family="IBM Plex Mono,monospace">${{p.d}}</text>`;
        }});
      }}

      // Y 軸零線
      const y0=yScale(0);
      const zeroLine=y0!=null?`<line x1="0" y1="${{y0.toFixed(1)}}" x2="${{cW}}" y2="${{y0.toFixed(1)}}" stroke="rgba(255,255,255,0.08)" stroke-dasharray="4,3"/>`:"";;

      chartEl.innerHTML=`<svg viewBox="0 0 ${{W}} ${{H}}" width="100%" height="${{H}}" xmlns="http://www.w3.org/2000/svg">
        <g transform="translate(${{PL}},${{PT}})">
          ${{zeroLine}}
          ${{paths}}
          ${{dots}}
          ${{xLabels}}
        </g>
        <g transform="translate(${{PL+cW+6}},${{PT}})">
          ${{labels}}
        </g>
      </svg>`;
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
}})();
</script>
"""

    # ── 額外 CSS（熱力格大字等級 + 折線圖 + 雷達卡片）──────────────────────
    extra_css = """
<style>
/* 熱力格 grade 覆寫：讓 [A] 等文字更大更顯眼（已在 JS innerHTML 處理，這裡補 flex） */
.heat-grade { display:flex; align-items:center; gap:4px; margin-top:6px; }

/* ── 族群輪動圖 ────────────────────────────────────────────────────────── */
.rotation-chart-wrap {
  padding: 0 18px 14px;
}
.chart-label {
  font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:#22d3ee;margin-bottom:8px;
}
#rotation-chart {
  background: linear-gradient(180deg,rgba(7,13,26,0.6) 0%,rgba(4,8,15,0.4) 100%);
  border:1px solid rgba(255,255,255,0.06);border-radius:10px;
  padding:12px 6px 4px;overflow:hidden;
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

    # 折線圖區塊
    chart_html = (
        '<div class="rotation-chart-wrap">'
        '<div class="chart-label">📈 族群輪動時序圖 — 5日累計漲幅趨勢（各族群均值）</div>'
        '<div id="rotation-chart"><div style="color:#3d5470;font-size:12px;padding:20px">計算中…</div></div>'
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

    out = base_html.replace("</head>", extra_css + "</head>", 1)
    out = out.replace('<main id="groupList">',
                      chart_html + radar_html + vol_html + '<main id="groupList">', 1)
    out = out.replace("</body>", script + "</body>", 1)
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
          .stApp{overflow:hidden;}
          iframe{display:block!important;border:none!important;margin:0!important;}
        </style>""",
        unsafe_allow_html=True,
    )

    if not HTML_PATH.exists():
        st.error(f"❌ 找不到 `{HTML_PATH}`\n\n請確認 GitHub Actions 已成功執行並 commit `docs/index.html`。")
        st.stop()

    html_content = HTML_PATH.read_text(encoding="utf-8")
    symbols = tuple((item["code"], item["exchange"]) for item in extract_symbols(html_content))
    stock_groups = extract_stock_groups(html_content)

    if not symbols:
        st.error("HTML 裡沒有找到任何 data-code，靜態檔案可能有問題。")
        st.stop()

    with st.spinner(f"正在批次抓取 {len(symbols)} 檔即時報價及5日歷史…"):
        payload = fetch_all_quotes(symbols)

    if payload["fetched_count"] == 0:
        st.warning("⚠️ 報價暫時無法取得（非交易時間或 Yahoo 無回應）。顯示靜態快照。")
    if payload["errors"]:
        with st.expander("API 錯誤詳情", expanded=False):
            for e in payload["errors"]:
                st.caption(e)

    html_content = inject_live_script(html_content, payload, stock_groups)
    page_height  = estimate_page_height(html_content)
    components.html(html_content, height=page_height, scrolling=False)


if __name__ == "__main__":
    main()
