#!/usr/bin/env python3
"""
半导体景气度驾驶舱 v2.0
六级指标：存储价格 → TSMC → MU → HBM → 云Capex → 综合热度
"""

import datetime, json, os, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv(Path(__file__).parent / ".env")

OUTPUT_DIR = Path(__file__).parent
CACHE_FILE = OUTPUT_DIR / ".semi_cache.json"
HISTORY_DIR = OUTPUT_DIR / "history"
MA = 20

# ═══════════════ 数据源 ═══════════════

CHIP_STOCKS  = {"NVDA":"NVDA","AMD":"AMD","INTC":"INTC","AVGO":"AVGO","QCOM":"QCOM","MU":"MU","TSM":"TSM","ASML":"ASML","AMAT":"AMAT","LRCX":"LRCX"}
ETF_TICKERS = {"SMH":"SMH","SOXX":"SOXX","QQQ":"QQQ"}
MACRO       = {"DXY":"DX-Y.NYB","TNX":"^TNX","VIX":"^VIX"}
CLOUD_CAPEX = {"MSFT":"MSFT","AMZN":"AMZN","GOOGL":"GOOGL"}

# ── 一级：存储价格（TrendForce 难直接抓取，用 DRAM 现货代理 + 手动更新） ──
DRAM_DATA = {
    "2026-06-26": {"DDR5_16Gb": 46.5, "DDR4_16Gb": 70.6, "trend": "↑"},
    "2026-06-19": {"DDR5_16Gb": 45.8, "DDR4_16Gb": 70.1, "trend": "↑"},
    "2026-06-12": {"DDR5_16Gb": 45.2, "DDR4_16Gb": 69.5, "trend": "↑"},
}

NAND_DATA = {
    "2026-06-26": {"Wafer_512Gb": 2.85, "trend": "↑"},
    "2026-06-19": {"Wafer_512Gb": 2.78, "trend": "→"},
    "2026-06-12": {"Wafer_512Gb": 2.72, "trend": "↑"},
}

# ── 二级：TSMC 月营收 ──
TSMC_REVENUE = {
    "2026-05": {"revenue": 4169.75, "yoy": 30.1},
    "2026-04": {"revenue": 3890.52, "yoy": 28.7},
    "2026-03": {"revenue": 3712.31, "yoy": 27.3},
    "2026-02": {"revenue": 3560.28, "yoy": 25.8},
    "2026-01": {"revenue": 3487.13, "yoy": 24.5},
    "2025-12": {"revenue": 3412.10, "yoy": 22.1},
}

# ── 三级：MU 财报 ──
MU_EARNINGS = {
    "latest_quarter": "Q3 FY2026",
    "revenue_beat": 3.2,
    "eps_beat": 5.1,
    "guidance_beat": 2.8,
    "hbm_outlook": "Positive",
    "hbm_detail": "供给紧张可能持续至2027年·长期供应协议~220亿美元",
}

# ── 四、五、六级：HBM / 云Capex / 热度 ──
HBM_SCORE = {
    "hbm_price_up": True,     # +20
    "hbm_tight": True,        # +20
    "cowos_expand": True,     # +20
    "nvda_guide_up": False,    # +20 (待下一份财报)
    "cloud_capex_up": True,   # +20
}

CLOUD_CAPEX_LATEST = {
    "MSFT":   {"growth": 45.2, "note": "Azure AI"},
    "AMZN":   {"growth": 38.7, "note": "AWS"},
    "GOOGL":  {"growth": 62.1, "note": "TPU+Cloud"},
}

# ═══════════════ 数据抓取 ═══════════════

def fetch_all():
    data = {}
    for group in [CHIP_STOCKS, ETF_TICKERS, MACRO, CLOUD_CAPEX]:
        for name, tkr in group.items():
            try:
                raw = yf.download(tkr, period="1y", progress=False, timeout=20)
                if raw is not None and not raw.empty:
                    c = raw["Close"].dropna() if "Close" in raw.columns else raw.squeeze().dropna()
                    if len(c) > 20: data[name] = c
            except: pass
    return data

def fetch_sox():
    try:
        raw = yf.download("^SOX", period="3y", progress=False, timeout=20)
        if raw is not None and not raw.empty:
            return raw["Close"].dropna() if "Close" in raw.columns else raw.squeeze().dropna()
    except: pass
    return pd.Series(dtype=float)

# ═══════════════ 工具函数 ═══════════════

def f(x, fmt=""):
    if x is None: return "—"
    try: return format(x, fmt) if fmt else str(x)
    except: return "—"

def ma_trend(s):
    if s is None or len(s) < MA: return None, None, "—"
    l, m = float(s.iloc[-1]), float(s.iloc[-MA:].mean())
    return l, m, "高于MA20" if l > m else "低于MA20"

def pct_change(s):
    if s is None or len(s) < MA: return None
    return int((s.iloc[-1] / s.iloc[-MA:].mean() - 1) * 100)

def ytd_ret(s):
    if s is None or len(s) < 50: return "—"
    sub = s[s.index >= str(s.index[-1].year)]
    return f"{(float(s.iloc[-1])/float(sub.iloc[0])-1)*100:+.1f}%" if len(sub) > 2 else "—"

# ═══════════════ 评分引擎 ═══════════════

def compute_heat_index():
    """半导体热度指数（0-100）"""
    score = 0

    # DRAM (25%)
    dram_keys = sorted(DRAM_DATA.keys())
    if len(dram_keys) >= 2:
        curr = DRAM_DATA[dram_keys[-1]]["DDR5_16Gb"]
        prev = DRAM_DATA[dram_keys[-2]]["DDR5_16Gb"]
        if curr > prev: score += 25
        elif curr == prev: score += 12

    # NAND (20%)
    nand_keys = sorted(NAND_DATA.keys())
    if len(nand_keys) >= 2:
        curr = NAND_DATA[nand_keys[-1]]["Wafer_512Gb"]
        prev = NAND_DATA[nand_keys[-2]]["Wafer_512Gb"]
        if curr > prev: score += 20
        elif curr == prev: score += 10

    # TSMC (20%)
    tsmc_keys = sorted(TSMC_REVENUE.keys())
    if len(tsmc_keys) >= 2:
        yoy = TSMC_REVENUE[tsmc_keys[-1]]["yoy"]
        if yoy > 25: score += 20
        elif yoy > 15: score += 10
        elif yoy > 0: score += 5

    # HBM (20%)
    hbm = sum(20 for v in HBM_SCORE.values() if v)  # max 100 → scale to 20
    score += int(hbm / 100 * 20)

    # Cloud Capex (15%)
    avg_growth = sum(CLOUD_CAPEX_LATEST[k]["growth"] for k in CLOUD_CAPEX_LATEST) / len(CLOUD_CAPEX_LATEST)
    if avg_growth > 40: score += 15
    elif avg_growth > 25: score += 10
    elif avg_growth > 10: score += 5

    return min(score, 100)

def heat_verdict(h):
    if h >= 85: return "🟢🟢 强烈景气——未来1-2季度财报大概率继续超预期"
    elif h >= 70: return "🟢 景气上行——产业处于上升周期"
    elif h >= 50: return "🟡 观察——产业方向不明"
    return "🔴 周期下行——谨慎"

def compute_hbm_score():
    raw = sum(20 for v in HBM_SCORE.values() if v)
    if raw >= 80: return raw, "🟢🟢 牛市"
    elif raw >= 60: return raw, "🟢 偏多"
    elif raw >= 40: return raw, "🟡 中性"
    return raw, "🔴 风险"

# ═══════════════ 报告生成 ═══════════════

def build_report(data, sox):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    heat = compute_heat_index()
    hbm_score, hbm_verdict = compute_hbm_score()

    # SOX
    sox_v, sox_m, sox_p = ma_trend(sox)
    sox_ytd = ytd_ret(sox)
    sox_52w = "—"
    if len(sox) > 200:
        sox_52w = f"{int((sox[(sox.index>=sox.index[-1]-pd.DateOffset(years=1))]<sox.iloc[-1]).mean()*100)}%"

    # 芯片股表
    chip_rows = []
    for name in sorted(CHIP_STOCKS):
        s = data.get(name)
        if s is not None and len(s) > 20:
            l, m, p = ma_trend(s)
            pct = pct_change(s)
            chip_rows.append(f"| {name} | {f(l,'.0f')} | {f(p)} | {f(pct,'+d')+'%' if pct else '—'} | {ytd_ret(s)} |")
        else:
            chip_rows.append(f"| {name} | — | — | — | — |")

    # ETF
    etf_rows = []
    for name in sorted(ETF_TICKERS):
        s = data.get(name)
        if s is not None and len(s) > 20:
            etf_rows.append(f"| {name} | {f(float(s.iloc[-1]),'.0f')} | {ytd_ret(s)} |")
        else:
            etf_rows.append(f"| {name} | — | — |")

    # 宏观
    macro_rows = []
    for name in sorted(MACRO):
        s = data.get(name)
        if s is not None and len(s) > 20:
            l, m, p = ma_trend(s)
            macro_rows.append(f"| {name} | {f(l,'.1f')} | {f(m,'.1f')} | {f(p)} |")
        else:
            macro_rows.append(f"| {name} | — | — | — |")

    # 存储价格
    dram_keys = sorted(DRAM_DATA.keys())
    dram_curr = DRAM_DATA[dram_keys[-1]] if dram_keys else {}
    nand_keys = sorted(NAND_DATA.keys())
    nand_curr = NAND_DATA[nand_keys[-1]] if nand_keys else {}

    # TSMC
    tsmc_keys = sorted(TSMC_REVENUE.keys())
    tsmc_latest = TSMC_REVENUE[tsmc_keys[-1]] if tsmc_keys else {}
    tsmc_3m = sum(TSMC_REVENUE[k]["revenue"] for k in tsmc_keys[-3:]) if len(tsmc_keys)>=3 else 0
    tsmc_6m = sum(TSMC_REVENUE[k]["revenue"] for k in tsmc_keys[-6:]) if len(tsmc_keys)>=6 else 0
    tsmc_accel = "🟢 加速" if (tsmc_keys[-1] > tsmc_keys[-2] if len(tsmc_keys)>=2 else False) else "—"

    # 云Capex
    cloud_rows = []
    for name in ["MSFT","AMZN","GOOGL"]:
        d = CLOUD_CAPEX_LATEST.get(name, {})
        cloud_rows.append(f"| {name} | +{d.get('growth','—')}% | {d.get('note','—')} |")

    # 综合判断 score
    score = 0
    sigs = []
    if sox_p and sox_p=="低于MA20": score -= 2; sigs.append("SOX<MA20")
    elif sox_p: sigs.append("SOX>MA20")
    dxy_s = data.get("DXY")
    if dxy_s is not None and len(dxy_s)>MA:
        if float(dxy_s.iloc[-1]) > float(dxy_s.iloc[-MA:].mean()): score -= 1; sigs.append("DXY↑利空")
        else: score += 1; sigs.append("DXY↓利多")
    verdict = "🟢 偏多" if score >= 2 else ("🟡 中性" if score >= -1 else "🔴 偏空")

    return f"""# 半导体景气度驾驶舱

> 自动生成 · {now}

---

## 🔥 半导体热度指数

| 指数 | 评级 |
|------|------|
| **{heat}/100** | **{heat_verdict(heat)}** |

### 因子拆解

| 因子 | 权重 | 当前 | 得分 |
|------|------|------|------|
| DRAM (DDR5 16Gb ${dram_curr.get('DDR5_16Gb','—')}) | 25% | {dram_curr.get('trend','—')} | — |
| NAND (Wafer ${nand_curr.get('Wafer_512Gb','—')}) | 20% | {nand_curr.get('trend','—')} | — |
| TSMC (YoY {tsmc_latest.get('yoy','—')}%) | 20% | 加速中 | — |
| HBM | 20% | {hbm_verdict} | {hbm_score}/100 |
| 云Capex | 15% | 三位数增长 | — |

> **规则**：>85=强烈景气·70-85=上行·50-70=观察·<50=下行

---

## 📊 价格信号

### 费城半导体 (SOX)

| 现价 | MA20 | 方向 | 52周分位 | YTD |
|------|------|------|------|------|
| {f(sox_v,'.0f')} | {f(sox_m,'.0f')} | {f(sox_p)} | {sox_52w} | {sox_ytd} |

### 核心芯片股

| 代码 | 现价 | MA20方向 | 距MA20 | YTD |
|------|------|------|------|------|
{chr(10).join(chip_rows)}

### 半导体 ETF

| 代码 | 现价 | YTD |
|------|------|------|
{chr(10).join(etf_rows)}

### 宏观

| 指标 | 现价 | MA20 | 方向 |
|------|------|------|------|
{chr(10).join(macro_rows)}

---

## 🏭 一级：存储价格

| 类型 | 最新 | 前周 | 趋势 | 数据源 |
|------|------|------|------|------|
| DDR5 16Gb | ${dram_curr.get('DDR5_16Gb','—')} | ${DRAM_DATA.get(dram_keys[-2],{}).get('DDR5_16Gb','—') if len(dram_keys)>=2 else '—'} | {dram_curr.get('trend','—')} | TrendForce |
| NAND Wafer | ${nand_curr.get('Wafer_512Gb','—')} | ${NAND_DATA.get(nand_keys[-2],{}).get('Wafer_512Gb','—') if len(nand_keys)>=2 else '—'} | {nand_curr.get('trend','—')} | TrendForce |

> 📝 DRAM/NAND 需手动更新 | 数据源：[TrendForce DRAM](https://www.trendforce.com/price/dram) · [TrendForce NAND](https://www.trendforce.com/price/nand-flash)

---

## 🏗️ 二级：TSMC 月营收

| 月份 | 营收(亿NTD) | YoY |
|------|------|------|
{chr(10).join(f"| {k} | {TSMC_REVENUE[k]['revenue']:.0f} | +{TSMC_REVENUE[k]['yoy']}% |" for k in tsmc_keys[-6:])}

| 3M累计 | 6M累计 | 加速信号 |
|------|------|------|
| {tsmc_3m:.0f}亿 | {tsmc_6m:.0f}亿 | {tsmc_accel} |

> 绿灯条件：YoY>25% 且连续3月加速 → 当前: {"🟢" if tsmc_latest.get('yoy',0)>25 else "🟡"} | 数据源：[TSMC Monthly Revenue](https://investor.tsmc.com/english/monthly-revenue)

---

## 📈 三级：MU 财报

| 项目 | 数据 |
|------|------|
| 季度 | {MU_EARNINGS['latest_quarter']} |
| Revenue Beat | +{MU_EARNINGS['revenue_beat']}% |
| EPS Beat | +{MU_EARNINGS['eps_beat']}% |
| Guidance Beat | +{MU_EARNINGS['guidance_beat']}% |
| HBM Outlook | 🟢 {MU_EARNINGS['hbm_outlook']} |
| 详情 | {MU_EARNINGS['hbm_detail']} |

> 数据源：[Micron IR](https://investors.micron.com/) · 每季度更新

---

## 🧠 四级：HBM 景气度

| 项目 | 状态 | 得分 |
|------|------|------|
| HBM价格上涨 | {"🟢" if HBM_SCORE['hbm_price_up'] else "—"} | +20 |
| HBM供给紧张 | {"🟢" if HBM_SCORE['hbm_tight'] else "—"} | +20 |
| CoWoS扩产 | {"🟢" if HBM_SCORE['cowos_expand'] else "—"} | +20 |
| NVIDIA上修指引 | {"🟢" if HBM_SCORE['nvda_guide_up'] else "⏳"} | +20 |
| 云厂商Capex增长 | {"🟢" if HBM_SCORE['cloud_capex_up'] else "—"} | +20 |
| **总计** | | **{hbm_score}/100 → {hbm_verdict}** |

> 数据源：[SK Hynix IR](https://www.skhynix.com/en/ir) · [NVIDIA IR](https://investor.nvidia.com/) · [TSMC CoWoS](https://investor.tsmc.com/)

---

## ☁️ 五级：云厂商 Capex

| 公司 | Capex增速 | 备注 |
|------|------|------|
{chr(10).join(cloud_rows)}

> 数据源：[Microsoft IR](https://www.microsoft.com/en-us/Investor) · [Amazon IR](https://ir.aboutamazon.com/) · [Alphabet IR](https://abc.xyz/investor/) · 每季度更新

---

## 🎯 综合判断

| 信号 | 评分 |
|------|------|
| {chr(10).join(f'- {s}' for s in sigs)} | **{score:+d}** → {verdict} |

---
> 自动生成 · 非投资建议

### 数据源链接

| 板块 | 来源 | 链接 |
|------|------|------|
| 芯片股价/ETF | Yahoo Finance | [NVDA](https://finance.yahoo.com/quote/NVDA/) · [SOX](https://finance.yahoo.com/quote/%5ESOX/) |
| DRAM/NAND价格 | TrendForce | [DRAM](https://www.trendforce.com/price/dram) · [NAND](https://www.trendforce.com/price/nand-flash) |
| TSMC月营收 | TSMC IR | [Monthly Revenue](https://investor.tsmc.com/english/monthly-revenue) |
| MU财报 | Micron IR | [Earnings](https://investors.micron.com/) |
| HBM景气度 | SK Hynix IR | [IR Page](https://www.skhynix.com/en/ir) |
| NVIDIA指引 | NVIDIA IR | [IR Page](https://investor.nvidia.com/) |
| 云Capex | MSFT·AMZN·GOOGL | [MSFT](https://www.microsoft.com/en-us/Investor) · [AMZN](https://ir.aboutamazon.com/) · [GOOGL](https://abc.xyz/investor/) |
"""

# ═══════════════ 主流程 ═══════════════

def main():
    print("[1/3] 抓取数据...")
    data = fetch_all()
    sox = fetch_sox()
    print(f"      芯片:{sum(1 for k in data if k in CHIP_STOCKS)} ETF:{sum(1 for k in data if k in ETF_TICKERS)}")

    print("[2/3] 计算指标...")
    heat = compute_heat_index()
    print(f"      热度指数: {heat}/100")

    print("[3/3] 生成报告...")
    report = build_report(data, sox)
    (OUTPUT_DIR / "半导体看板.md").write_text(report, encoding="utf-8")
    HISTORY_DIR.mkdir(exist_ok=True)
    (HISTORY_DIR / f"半导体看板-{datetime.date.today()}.md").write_text(report, encoding="utf-8")
    print("✅ 已保存: 半导体看板.md")

if __name__ == "__main__":
    main()
