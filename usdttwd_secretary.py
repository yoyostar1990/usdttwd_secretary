#!/usr/bin/env python3
"""
📋 USDT/TWD 刷發票秘書（新手版）—— MAX 交易所
沒有帳號可以申請：https://max.maicoin.com/signup?r=1834cca6

【核心策略】
  帳上的 USDT（數位美元）和台幣維持各一半（1:1），每天自動成交一筆小單，
  讓 MAX 每天開一張電子發票給你。目的是「每天一張發票拿去對獎」，不是賺價差。

【成本與期望值】
  - 策略成本：每年約 365 元（保守估計；2025-10 ~ 2026-10 回測實際約 200~250 元，
    主要是手續費——發票規則是「當天手續費合計四捨五入 ≥ 1 元」，所以每天至少要付約 0.5 元）
  - 發票期望值：目前約 2 元／張，一年 365 張 ≈ 730 元
  - 策略成本 APR ≈ 100%：(發票期望值 730 − 成本 365) ÷ 成本 365。分母是「每年花掉的成本」，不是本金。
    ⚠️ 這是「對獎的期望值」，不是保證報酬：期望值大部分來自很少出現的大獎，
    多數人實際拿到的會比 730 元少很多，少數人會多很多——取決於自身運氣。
  - 另外：一半資金是 USDT，會跟著美金匯率漲跌（近一年最大回撤約 1.5%）。

【要準備什麼】
  1. 本金：建議 1,500 元台幣存進 MAX（程式第一天會自動換一半成 USDT）
     本金會被成本慢慢吃掉，餘額掉到 800 元以下時再補 500 元（低於約 700 元就會開始漏發票）
  2. MAX 帳戶設好「手機條碼載具」→ 中獎才會自動對獎、自動匯款
  3. MAX API 金鑰：MAX 網站 → 個人中心 → API 管理 → 新增，勾「交易」權限（不要勾提領！）
  4. 一台一直開著的電腦，裝 Python 3.9 以上，然後：  pip install requests

【怎麼用】
  1. 把下面的 API_KEY / API_SECRET 換成你自己的
  2. 執行：  python3 usdttwd_secretary.py      （Windows 打 python usdttwd_secretary.py）
     視窗不要關，它會自己在每天 08:01 掛單、每 10 分鐘檢查、23:55 補單。關掉那天就沒有發票。
  其他指令：
     python3 usdttwd_secretary.py status       看餘額跟今天狀態
     python3 usdttwd_secretary.py open --dry   試算今天會怎麼掛單，不會真的下單

【注意】
  ⚠️ 帳上「所有」的 USDT 和台幣都會被拿來做 1:1。如果你平常也在這個帳戶放大筆台幣或 USDT，
     那些錢也會被平衡掉——最好開一個子帳戶，或只放刷發票用的錢。其他幣（BTC、ETH…）不會動。
  ⚠️ 每天 08:01 會撤掉你在 USDT/TWD 市場上的所有掛單（包括你自己手動掛的）。

【運作細節（看不懂可跳過）】
  08:01  看 00:00–08:00 的最高/最低價，在現價下方掛買、上方掛賣（離現價 2.5 倍的夜間波動）。
         數量 = 成交後回到 1:1 的量，但至少要「這一筆的手續費就夠開發票」。
  每10分 一邊成交了 → 撤掉另一邊，依新的持倉重新算量再掛。
  23:55  今天手續費合計還不到 0.5 元 → 直接吃單補一筆（方向朝 1:1），23:58 再檢查一次。
  手續費率會自動偵測你的 VIP 等級，以及有沒有用 MAX 幣折抵。

【免責聲明】
 Copyright (c) 2026 yoyostar1990
 MIT License — 可自由使用、修改、散布，但須保留本段。
 本程式不提供任何保證；使用者自行承擔交易風險與損失，作者不負任何責任。

"""

# ============================================================================
# ⬇️ 只要改這兩行 ⬇️
API_KEY = "貼上你的 API KEY"
API_SECRET = "貼上你的 API SECRET"
# ============================================================================

import base64
import hashlib
import hmac
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

MARKET, BASE_CCY = "usdttwd", "usdt"
MIN_BASE, QTY_DP, PX_DP = 8.0, 2, 3     # 交易所規則：最小 8 USDT、數量 2 位小數、價格 3 位小數
MIN_TWD = 250.0                          # 交易所最小成交額
N_MULT = 2.5                             # 掛價 = 夜間波動 × 2.5
FEE_TARGET = 0.5                         # 當天手續費合計 > 0.5 元 → 四捨五入 1 元 → 有發票
FEE_MARGIN = 1.1                         # 23:55 補單補到 0.55 元，留一點換算誤差的空間
MAX_FEE_MIN = 0.3                        # MAX 幣少於這個數量就不算折抵
TZ = timezone(timedelta(hours=8))        # 台北時間（發票按台北日曆日開）
BASE = "https://max-api.maicoin.com"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "usdttwd_secretary_state.json")
LOG = os.path.join(HERE, "usdttwd_secretary.log")
DRY = "--dry" in sys.argv


def log(msg):
    line = f"{datetime.now(TZ):%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------- MAX API
def api(path, method="GET", params=None, signed=True):
    params = dict(params or {})
    headers = {"Content-Type": "application/json"}
    if signed:
        key = os.environ.get("MAXEX_API_KEY", API_KEY)
        secret = os.environ.get("MAXEX_API_SECRET", API_SECRET)
        params["nonce"] = int(time.time() * 1000)
        payload = base64.b64encode(json.dumps({**params, "path": path}, separators=(",", ":")).encode()).decode()
        headers.update({"X-MAX-ACCESSKEY": key, "X-MAX-PAYLOAD": payload,
                        "X-MAX-SIGNATURE": hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()})
    if method == "GET":
        r = requests.get(BASE + path, params=params, headers=headers, timeout=20)
    else:
        r = requests.request(method, BASE + path, json=params, headers=headers, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} {r.status_code}: {r.text[:300]}")
    return r.json()


def accounts():
    return {a["currency"]: (float(a["balance"]), float(a["locked"])) for a in api("/api/v2/members/accounts")}


def holdings():
    """(USDT 可用, USDT 總共, 台幣可用, 台幣總共)"""
    acc = accounts()
    u_free, u_lock = acc.get(BASE_CCY, (0.0, 0.0))
    t_free, t_lock = acc.get("twd", (0.0, 0.0))
    return u_free, u_free + u_lock, t_free, t_free + t_lock


def last_price():
    t = api(f"/api/v2/tickers/{MARKET}", signed=False)
    return float(t["last"]), float(t["buy"]), float(t["sell"])


def twd_rate(ccy):
    return 1.0 if ccy == "twd" else float(api(f"/api/v2/tickers/{ccy}twd", signed=False)["last"])


_RATES = None


def fee_rates():
    """(掛單費率, 吃單費率)：VIP 等級 × MAX 幣折抵（從近 14 天用 MAX 幣扣的成交反推）。"""
    global _RATES
    if _RATES:
        return _RATES
    vip = api("/api/v2/members/vip_level")["current_vip_level"]
    maker, taker = float(vip["maker_fee"]), float(vip["taker_fee"])
    max_bal = accounts().get("max", (0.0, 0.0))[0]
    ratios = []
    if max_bal >= MAX_FEE_MIN:
        p_max = twd_rate("max")
        for t in api("/api/v2/trades/my", params={"market": MARKET, "limit": 50}):
            if t.get("fee_currency") != "max" or time.time() - t["created_at"] > 14 * 86400:
                continue
            side = "bid" if t["side"] in ("bid", "buy") else "ask"
            rate = maker if t.get("info", {}).get("maker") == side else taker
            ratios.append(float(t["fee"]) * p_max / float(t["funds"]) / rate)
    disc = sorted(ratios)[len(ratios) // 2] if ratios else 1.0
    log(f"  費率：VIP{vip['level']} 掛單 {maker:.3%} 吃單 {taker:.3%}"
        f"{f'，MAX 幣折抵 ×{disc:.2f}' if ratios else ''}")
    _RATES = (maker * disc, taker * disc)
    return _RATES


def clear_orders():
    if not DRY:
        api("/api/v2/orders/clear", "POST", {"market": MARKET})
        time.sleep(2)


def place(side, qty, price=None):
    qty = math.floor(qty * 10 ** QTY_DP) / 10 ** QTY_DP
    p = {"market": MARKET, "side": side, "volume": f"{qty:.{QTY_DP}f}", "ord_type": "limit" if price else "market"}
    if price:
        price = round(price, PX_DP)
        p["price"] = f"{price:.{PX_DP}f}"
    log(f"  {'[試算] ' if DRY else ''}{'買' if side == 'buy' else '賣'} {qty:,.2f} USDT @ {price or '市價'}"
        f"（≈{qty * (price or last_price()[0]):,.0f} 元）")
    return None if DRY else api("/api/v2/orders", "POST", p)["id"]


def order(oid):
    return api("/api/v2/order", params={"id": oid})


# ---------------------------------------------------------------- 策略
def to_one(usdt, twd, px):
    """回到 1:1 要買（+）或賣（−）多少 USDT。"""
    return ((usdt * px + twd) / 2 - usdt * px) / px


def today():
    return datetime.now(TZ).date().isoformat()


def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def save(st):
    if not DRY:
        json.dump(st, open(STATE, "w"), indent=1, ensure_ascii=False)


def min_qty(px):
    """掛單最小量：交易所最小單，且「這一筆」的手續費就 > 0.5 元。"""
    return max(MIN_BASE, MIN_TWD / px, FEE_TARGET / fee_rates()[0] / px) * 1.02


def side_qty(side, px):
    u_free, usdt, t_free, twd = holdings()
    q = to_one(usdt, twd, px)
    if side == "buy" and q > 0:
        q = max(q, min_qty(px))
        return q if t_free >= q * px * 1.001 else 0.0
    if side == "sell" and q < 0:
        q = max(-q, min_qty(px))
        return q if u_free >= q else 0.0
    return 0.0


def night_range():
    """今天 00:00–07:59 的最高/最低價，與 08:00 開盤價。"""
    anchor = datetime.now(TZ).replace(hour=8, minute=0, second=0, microsecond=0)
    start = int((anchor - timedelta(hours=8)).timestamp())
    raw = api("/api/v2/k", params={"market": MARKET, "period": 60, "limit": 9, "timestamp": start}, signed=False)
    bars = [b for b in raw if start <= b[0] < anchor.timestamp()]
    p8 = [b for b in raw if b[0] == int(anchor.timestamp())]
    if len(bars) < 8 or not p8:
        raise RuntimeError("拿不到今天 00:00–08:00 的 K 線，稍後再試")
    return max(float(b[2]) for b in bars), min(float(b[3]) for b in bars), float(p8[0][1])


def cmd_open():
    clear_orders()
    h8, l8, p8 = night_range()
    hi, lo = round(p8 + N_MULT * (h8 - p8), PX_DP), round(p8 - N_MULT * (p8 - l8), PX_DP)
    px = last_price()[0]
    _, usdt, _, twd = holdings()
    total = usdt * px + twd
    log(f"開盤 {today()}｜現價 {px}｜掛買 {lo}、掛賣 {hi}｜USDT {usdt:,.2f} + 台幣 {twd:,.0f} = {total:,.0f} 元"
        f"（USDT 佔 {usdt * px / total if total else 0:.0%}）")
    st = {"day": today(), "hi": hi, "lo": lo, "buy_id": None, "sell_id": None, "fills": []}
    for side, p in (("buy", lo), ("sell", hi)):
        q = side_qty(side, p)
        if q:
            st[f"{side}_id"] = place(side, q, p)
    save(st)


def cmd_poll():
    st = load()
    if st.get("day") != today():
        return
    changed = False
    for side, other in (("buy", "sell"), ("sell", "buy")):
        oid = st.get(f"{side}_id")
        if not oid:
            continue
        o = order(oid)
        if o["state"] not in ("done", "cancel", "fail"):
            continue
        if float(o.get("executed_volume") or 0) > 0:
            st["fills"].append(side)
            log(f"✅ {'買' if side == 'buy' else '賣'}單成交 {o['executed_volume']} USDT @ {o['price']}")
        st[f"{side}_id"] = None
        changed = True
        if o["state"] == "done" and st.get(f"{other}_id"):     # 一邊成交 → 另一邊依新持倉重掛
            api("/api/v2/order/delete", "POST", {"id": st[f"{other}_id"]})
            time.sleep(2)
            p = st["lo"] if other == "buy" else st["hi"]
            q = side_qty(other, p)
            st[f"{other}_id"] = place(other, q, p) if q else None
    for side in ("buy", "sell"):                                  # 之前掛不出去的那邊，持倉變了再試
        if st.get(f"{side}_id") or side in st["fills"]:
            continue
        p = st["lo"] if side == "buy" else st["hi"]
        q = side_qty(side, p)
        if q:
            st[f"{side}_id"] = place(side, q, p)
            changed = True
    if changed:
        save(st)


def fee_today():
    """今天（台北日曆日）USDT/TWD 的手續費合計，折台幣。"""
    total = 0.0
    for t in api("/api/v2/trades/my", params={"market": MARKET, "limit": 100}):
        if datetime.fromtimestamp(t["created_at"], TZ).date().isoformat() == today():
            total += float(t.get("fee") or 0) * twd_rate(t.get("fee_currency") or "twd")
    return total


def cmd_fallback():
    fee = fee_today()
    if fee > FEE_TARGET:
        log(f"23:5x 檢查：今天手續費 {fee:.2f} 元，夠開發票了 👍")
        return
    clear_orders()
    px, _, ask = last_price()
    u_free, usdt, t_free, twd = holdings()
    q = to_one(usdt, twd, px)
    need = max(MIN_BASE, MIN_TWD / px, (FEE_TARGET * FEE_MARGIN - fee) / fee_rates()[1] / px) * 1.02
    if abs(q) < need:
        q = need if q >= 0 else -need
    if (q > 0 and t_free < q * ask * 1.003) or (q < 0 and u_free < -q):
        log(f"❌ 餘額不夠下最小單（需要約 {abs(q) * px:,.0f} 元）—— 今天沒有發票，請補錢")
        return
    log(f"23:5x 檢查：今天手續費 {fee:.2f} 元，不夠 → 吃單補一筆")
    place("buy" if q > 0 else "sell", abs(q))


def cmd_status():
    px = last_price()[0]
    u_free, usdt, t_free, twd = holdings()
    total = usdt * px + twd
    print(f"USDT {usdt:,.2f}（可用 {u_free:,.2f}）  台幣 {twd:,.0f}（可用 {t_free:,.0f}）  現價 {px}")
    print(f"總值 {total:,.0f} 元，USDT 佔 {usdt * px / total if total else 0:.0%}")
    print(f"今天手續費 {fee_today():.2f} 元（> 0.5 就有發票）")
    if total < 800:
        print("⚠️ 總值低於 800 元，建議補 500 元，不然快要開始漏發票")


def run_forever():
    """一直跑：08:01 開盤掛單、每 10 分鐘檢查、23:55 與 23:58 補單。00:00–08:00 不動。"""
    log("📋 秘書上班了（視窗不要關）")
    ran, last_poll = set(), 0.0
    while True:
        now = datetime.now(TZ)
        day, hm = today(), now.hour * 60 + now.minute
        try:
            if 8 * 60 + 1 <= hm < 23 * 60 + 50 and load().get("day") != day:
                cmd_open()
            if hm >= 8 * 60 + 1 and time.time() - last_poll >= 600:
                last_poll = time.time()
                cmd_poll()
            for t in (23 * 60 + 55, 23 * 60 + 58):
                if hm >= t and (day, t) not in ran:
                    ran.add((day, t))
                    cmd_fallback()
        except Exception as e:      # 網路斷掉之類的：記下來，下一輪再試
            log(f"❌ 出錯了（會自動重試）：{e}")
        time.sleep(30)


if __name__ == "__main__":
    if API_KEY.startswith("貼上") and "MAXEX_API_KEY" not in os.environ:
        sys.exit("請先打開這個檔案，把 API_KEY / API_SECRET 換成你自己的 MAX API 金鑰")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cmds = {"open": cmd_open, "poll": cmd_poll, "fallback": cmd_fallback, "status": cmd_status}
    if not args:
        run_forever()
    elif args[0] in cmds:
        cmds[args[0]]()
    else:
        sys.exit(__doc__)
