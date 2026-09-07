"""D5 前 N 檔集中度與蒙地卡羅（重抽訊號月）。讀 backtest/signals.csv，不打網路。
用法：python backtest/concentration_mc.py [--n 10] [--draws 100]"""
import argparse, numpy as np, pandas as pd

def run(n, draws, seed=20260907):
    d = pd.read_csv("backtest/signals.csv")
    rng = np.random.default_rng(seed)
    out = {}
    for hold in ("3m", "6m"):
        d5 = d[(d.D5 == True) & d[f"ret_{hold}"].notna()]
        rows = []
        for m, g in d5.groupby("signal_date"):
            g = g.sort_values("rel_mom_rank", ascending=False)
            if n and len(g) < n:
                continue
            p = g.head(n) if n else g
            r = p[f"ret_{hold}"].mean(); b = p[f"bench_{hold}"].iloc[0]
            rows.append(dict(m=m, ret=r, ex=r - b))
        h = pd.DataFrame(rows)
        s = h.iloc[rng.integers(0, len(h), draws)]
        q = lambda x, p: float(np.percentile(x, p))
        out[hold] = dict(months=len(h), distinct=int(s.m.nunique()), mean=s.ret.mean(), median=s.ret.median(),
                         p5=q(s.ret, 5), p25=q(s.ret, 25), p75=q(s.ret, 75), p95=q(s.ret, 95),
                         loss_prob=(s.ret < 0).mean(), ex_median=s.ex.median(), lose_bench_prob=(s.ex < 0).mean(),
                         hist_median=h.ret.median(), hist_win=(h.ret > 0).mean(), hist_win_vs_bench=(h.ex > 0).mean(),
                         worst=h.ret.min(), worst_month=h.loc[h.ret.idxmin(), "m"])
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=10); ap.add_argument("--draws", type=int, default=100)
    a = ap.parse_args()
    for hold, v in run(a.n, a.draws).items():
        print(hold, {k: (round(x, 4) if isinstance(x, float) else x) for k, x in v.items()})
