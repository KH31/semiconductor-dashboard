#!/usr/bin/env python3
"""
半导体景气度驾驶舱 v1.0
三层：价格·资金流·产业周期
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
LOOKBACK, MA = 120, 20

# ── 标的 ──
CHIP_STOCKS = {"NVDA":"NVDA","AMD":"AMD","INTC":"INTC","AVGO":"AVGO","QCOM":"QCOM","MU":"MU","TSM":"TSM","ASML":"ASML","AMAT":"AMAT","LRCX":"LRCX"}
ETF_TICKERS = {"SMH":"SMH","SOXX":"SOXX","QQQ":"QQQ"}
MACRO = {"DXY":"DX-Y.NYB","10Y":"^TNX","VIX":"^VIX"}

def load_cache(): return json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}

def save_cache(d): CACHE_FILE.write_text(json.dumps(d,ensure_ascii=False,default=str))

def pct_change(series):
    if series is None or len(series) < MA: return None
    return float((series.iloc[-1]/series.iloc[-MA:].mean()-1)*100)

def ma_trend(series):
    if series is None or len(series) < MA: return None,None,"—"
    latest = float(series.iloc[-1])
    ma = float(series.iloc[-MA:].mean())
    return latest, ma, "高于MA20" if latest>ma else "低于MA20"

# ── 数据抓取 ──
def fetch_all():
    data = {}
    for group, tickers in [("chip",CHIP_STOCKS),("etf",ETF_TICKERS),("macro",MACRO)]:
        for name, tkr in tickers.items():
            try:
                raw = yf.download(tkr, period="1y", progress=False, timeout=20)
                if raw is not None and not raw.empty:
                    close = raw["Close"].dropna() if "Close" in raw.columns else raw.squeeze().dropna()
                    if len(close)>20: data[name] = close
            except: pass
    return data

def fetch_sector():
    """下载费城半导体指数 SOX"""
    try:
        raw = yf.download("^SOX", period="3y", progress=False, timeout=30)
        if raw is not None and not raw.empty:
            return raw["Close"].dropna() if "Close" in raw.columns else raw.squeeze().dropna()
    except: pass
    return pd.Series(dtype=float)

# ── 报告生成 ──
def build_report(data, sox):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    rows_chip, rows_etf, rows_macro = [], [], []

    for name in sorted(CHIP_STOCKS):
        s = data.get(name)
        if s is not None and len(s) > 20:
            latest, ma, pos = ma_trend(s)
            pct = pct_change(s)
            ytd_sub = s[s.index >= str(s.index[-1].year)]
            ytd = f"{(float(s.iloc[-1])/float(ytd_sub.iloc[0])-1)*100:+.1f}%" if len(ytd_sub)>5 else "—"
            rows_chip.append(f"| {name} | ${latest:.0f} | {pos} | {pct:+d}% | {ytd} |")
        else:
            rows_chip.append(f"| {name} | — | — | — | — |")

    for name in sorted(ETF_TICKERS):
        s = data.get(name)
        if s is not None and len(s) > 20:
            latest, _, _ = ma_trend(s)
            ytd_sub = s[s.index >= str(s.index[-1].year)]
            ytd = f"{(float(s.iloc[-1])/float(ytd_sub.iloc[0])-1)*100:+.1f}%" if len(ytd_sub)>5 else "—"
            rows_etf.append(f"| {name} | ${latest:.0f} | {ytd} |")
        else:
            rows_etf.append(f"| {name} | — | — |")

    for name in sorted(MACRO):
        s = data.get(name)
        if s is not None and len(s) > 20:
            latest, ma, pos = ma_trend(s)
            rows_macro.append(f"| {name} | {latest:.1f} | {ma:.1f} | {pos} |")
        else:
            rows_macro.append(f"| {name} | — | — | — |")

    # SOX 费城半导体
    sox_val, sox_ma, sox_pos = ma_trend(sox)
    sox_ytd = "—"
    if len(sox)>50:
        sox_ytd = f"{(float(sox.iloc[-1])/float(sox[sox.index>=str(sox.index[-1].year)].iloc[0])-1)*100:+.1f}%"
    sox_52w = "—"
    if len(sox)>200:
        sox_52w_pct = int((sox[(sox.index>=sox.index[-1]-pd.DateOffset(years=1))]<sox.iloc[-1]).mean()*100)
        sox_52w = f"{sox_52w_pct}%"

    # 综合评分
    score = 0
    signals = []
    if sox_pos and sox_pos=="低于MA20": score-=2; signals.append("SOX<MA20 📉")
    else: score+=0; signals.append("SOX>MA20")
    dxy_s = data.get("DXY")
    vix_s = data.get("VIX")
    if dxy_s is not None and len(dxy_s)>MA:
        if float(dxy_s.iloc[-1]) > float(dxy_s.iloc[-MA:].mean()): score-=1; signals.append("DXY↑利空")
        else: score+=1; signals.append("DXY↓利多")
    if vix_s is not None and len(vix_s)>MA:
        if float(vix_s.iloc[-1]) > 22: score-=1; signals.append("VIX>22")

    verdict = "🟢 偏多" if score>=2 else ("🟡 中性" if score>=-1 else "🔴 偏空")

    chip_rows_str = "\n".join(rows_chip)
    etf_rows_str = "\n".join(rows_etf)
    macro_rows_str = "\n".join(rows_macro)
    signals_str = " · ".join(signals)

    def f(x, fmt=""):
        if x is None: return "—"
        try: return format(x, fmt) if fmt else str(x)
        except: return "—"

    return f"""# 半导体景气度驾驶舱

> 自动生成 · {now}

---

## 📊 综合判断

| 信号 | 评分 |
|------|------|
| {signals_str} | **{score:+d}** → {verdict} |

## 费城半导体 (SOX)

| 现价 | MA20 | 方向 | 52周分位 | YTD |
|------|------|------|------|------|
| {f(sox_val,'.0f')} | {f(sox_ma,'.0f')} | {f(sox_pos)} | {f(sox_52w)} | {f(sox_ytd)} |

---

## 🔬 核心芯片股

| 代码 | 现价 | MA20方向 | 距MA20 | YTD |
|------|------|------|------|------|
{chip_rows_str}

## 📦 半导体 ETF

| 代码 | 现价 | YTD |
|------|------|------|
{etf_rows_str}

## 🌐 宏观环境

| 指标 | 现价 | MA20 | 方向 |
|------|------|------|------|
{macro_rows_str}

---

> 数据源：Yahoo Finance · 自动生成 · 非投资建议
"""

# ── 主流程 ──
def main():
    print("[1/3] 抓取数据...")
    data = fetch_all()
    sox = fetch_sector()
    print(f"      股票:{sum(1 for k in data if k in CHIP_STOCKS)} ETF:{sum(1 for k in data if k in ETF_TICKERS)} 宏观:{sum(1 for k in data if k in MACRO)}")
    print("[2/3] 生成报告...")
    report = build_report(data, sox)
    (OUTPUT_DIR / "半导体看板.md").write_text(report, encoding="utf-8")
    print("✅ 已保存: 半导体看板.md")
    print("[3/3] 完成")

if __name__ == "__main__":
    main()
