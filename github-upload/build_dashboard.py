# /// script
# requires-python = ">=3.12"
# dependencies = ["pandas>=2.2,<3"]
# ///
"""Build the Studio North month-by-month replay dashboard.

Reads four tables saved by a Customer Economics Lab run and writes a
self-contained HTML dashboard (no internet needed) that steps through
January 2024 - December 2025 one month at a time.

For every as-of month M the script builds a separate snapshot using only
records Studio North could have known by the end of M:
  * company months with calendar_month <= M
  * cohort cells (cohort x age) with calendar_month <= M
  * holdout tests whose follow-up ended by the first day of month M+1

It then proves there is no look-ahead: each snapshot is rebuilt from copies
of the tables that have every later record deleted, and the two must match.

Usage:
    uv run build_dashboard.py                      # reads run_outputs/, writes dashboard.html
    uv run build_dashboard.py --tables DIR --out FILE
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

TABLES = {
    "company": "company_totals.csv",
    "cohort_totals": "cohort_totals.csv",
    "cohort_cash": "cohort_cash_economics.csv",
    "channel_cac": "channel_cac.csv",
}
REQUIRED = {
    "company": ["calendar_month", "n_retained", "net_revenue", "contribution", "acquisition_spend"],
    "cohort_totals": ["cohort_month", "age_month", "calendar_month", "n_acquired", "n_retained", "retained_share"],
    "cohort_cash": ["cohort_month", "age_month", "calendar_month", "n_acquired", "cac",
                    "cumulative_contribution", "balance_per_customer"],
    "channel_cac": ["channel", "primary_need", "test_month", "followup_end_at", "buyers_treatment",
                    "buyers_control", "incremental_buyers", "credited_cac", "icac",
                    "icac_ci_lower", "icac_ci_upper"],
}
SERIES = ["search/build_skill", "partner/build_skill", "search/save_time", "partner/save_time"]


# --------------------------------------------------------------------------- load

def load_tables(folder: Path) -> dict[str, pd.DataFrame]:
    tables = {}
    for key, name in TABLES.items():
        path = folder / name
        if not path.exists():
            raise SystemExit(f"Missing {path}. Copy it from your run's tables/ folder.")
        df = pd.read_csv(path)
        missing = [c for c in REQUIRED[key] if c not in df.columns]
        if missing:
            raise SystemExit(f"{name} is missing columns: {missing}")
        for col in ("calendar_month", "cohort_month", "test_month"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col].astype(str).str[:10])
        if "followup_end_at" in df.columns:
            df["followup_end_at"] = pd.to_datetime(df["followup_end_at"])
        tables[key] = df
    return tables


def cut_to(tables: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Delete every record dated after the end of `as_of` (what was knowable then)."""
    month_end = as_of + pd.offsets.MonthBegin(1)  # first day of the next month, exclusive
    return {
        "company": tables["company"][tables["company"].calendar_month <= as_of],
        "cohort_totals": tables["cohort_totals"][tables["cohort_totals"].calendar_month <= as_of],
        "cohort_cash": tables["cohort_cash"][tables["cohort_cash"].calendar_month <= as_of],
        "channel_cac": tables["channel_cac"][tables["channel_cac"].followup_end_at <= month_end],
    }


# --------------------------------------------------------------------------- snapshot

def r2(x) -> float | None:
    return None if pd.isna(x) else round(float(x), 2)


def snapshot(tables: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict:
    """Everything the dashboard shows for as-of month M, built from records <= M only."""
    t = cut_to(tables, as_of)  # the rule is applied here, whatever the input holds

    company = t["company"].sort_values("calendar_month")
    company = company.assign(
        cum_contribution=company.contribution.cumsum(),
        cum_spend=company.acquisition_spend.cumsum(),
    )
    cur = company.iloc[-1]
    prev = company.iloc[-2] if len(company) > 1 else None
    cum_balance = cur.cum_contribution - cur.cum_spend

    new = t["cohort_cash"]
    new = new[(new.cohort_month == as_of) & (new.age_month == 0)]
    new = new.iloc[0] if len(new) else None

    tiles = {
        "paying_members": int(cur.n_retained),
        "net_revenue": r2(cur.net_revenue),
        "contribution": r2(cur.contribution),
        "contribution_change": None if prev is None else r2(cur.contribution - prev.contribution),
        "acquisition_spend": r2(cur.acquisition_spend),
        "new_members": None if new is None else int(new.n_acquired),
        "historical_cac": None if new is None else r2(new.cac),
        "cum_contribution": r2(cur.cum_contribution),
        "cum_spend": r2(cur.cum_spend),
        "cum_balance": r2(cum_balance),
    }

    company_rows = [
        {"month": m.strftime("%Y-%m-%d"), "contribution": r2(c), "spend": r2(s), "paying": int(n)}
        for m, c, s, n in zip(company.calendar_month, company.contribution,
                              company.acquisition_spend, company.n_retained)
    ]
    retention = [
        {"cohort": c.strftime("%Y-%m-%d"), "age": int(a), "cal": m.strftime("%Y-%m-%d"),
         "v": round(float(v), 4), "n_ret": int(nr), "n_acq": int(na)}
        for c, a, m, v, nr, na in zip(t["cohort_totals"].cohort_month, t["cohort_totals"].age_month,
                                      t["cohort_totals"].calendar_month, t["cohort_totals"].retained_share,
                                      t["cohort_totals"].n_retained, t["cohort_totals"].n_acquired)
    ]
    cash = [
        {"cohort": c.strftime("%Y-%m-%d"), "age": int(a), "cal": m.strftime("%Y-%m-%d"),
         "v": r2(v), "cac": r2(cac), "cum": r2(cum)}
        for c, a, m, v, cac, cum in zip(t["cohort_cash"].cohort_month, t["cohort_cash"].age_month,
                                        t["cohort_cash"].calendar_month, t["cohort_cash"].balance_per_customer,
                                        t["cohort_cash"].cac, t["cohort_cash"].cumulative_contribution)
    ]
    tests = [
        {"wave": w.strftime("%Y-%m-%d"), "key": f"{ch}/{nd}", "icac": r2(i), "lo": r2(lo), "hi": r2(hi),
         "credited": r2(cr), "tb": int(tb), "cb": int(cb), "inc": r2(inc)}
        for w, ch, nd, i, lo, hi, cr, tb, cb, inc in zip(
            t["channel_cac"].test_month, t["channel_cac"].channel, t["channel_cac"].primary_need,
            t["channel_cac"].icac, t["channel_cac"].icac_ci_lower, t["channel_cac"].icac_ci_upper,
            t["channel_cac"].credited_cac, t["channel_cac"].buyers_treatment,
            t["channel_cac"].buyers_control, t["channel_cac"].incremental_buyers)
    ]
    return {"as_of": as_of.strftime("%Y-%m-%d"), "tiles": tiles, "company": company_rows,
            "retention": retention, "cash": cash, "tests": tests}


def build_snapshots(tables: dict[str, pd.DataFrame]) -> list[dict]:
    months = sorted(tables["company"].calendar_month.unique())
    return [snapshot(tables, pd.Timestamp(m)) for m in months]


def check_no_lookahead(tables: dict[str, pd.DataFrame], snaps: list[dict]) -> None:
    """Rebuild every month from tables with later records deleted; the view must not change."""
    for s in snaps:
        m = pd.Timestamp(s["as_of"])
        if snapshot(cut_to(tables, m), m) != s:
            raise SystemExit(f"Look-ahead check FAILED for {s['as_of']}")
    print(f"Look-ahead check passed: all {len(snaps)} months rebuild identically from cut-off records.")


# --------------------------------------------------------------------------- output

def print_summary(snaps: list[dict]) -> None:
    print("\nAs-of month   Paying  Contribution     Spend   New   CAC     Cum. contribution less spend")
    for s in snaps:
        t = s["tiles"]
        cac = f"{t['historical_cac']:.2f}" if t["historical_cac"] is not None else "  -"
        new = t["new_members"] if t["new_members"] is not None else "-"
        print(f"{s['as_of'][:7]}      {t['paying_members']:>6,}  {t['contribution']:>12,.0f}  "
              f"{t['acquisition_spend']:>8,.0f}  {new:>4}  {cac:>6}  {t['cum_balance']:>+14,.0f}")
    first_above = next((s["as_of"][:7] for s in snaps
                        if s["tiles"]["contribution"] >= s["tiles"]["acquisition_spend"]), None)
    first_cum = next((s["as_of"][:7] for s in snaps if s["tiles"]["cum_balance"] >= 0), None)
    first_test = next((s["as_of"][:7] for s in snaps if s["tests"]), None)
    print(f"\nFirst month contribution >= acquisition spend: {first_above}")
    print(f"First month cumulative contribution covers cumulative spend: {first_cum}")
    print(f"First month with a finished holdout test: {first_test}")


def write_html(snaps: list[dict], out: Path) -> None:
    payload = json.dumps({"snapshots": snaps, "series": SERIES}, separators=(",", ":"))
    out.write_text(HTML.replace("__DATA__", payload), encoding="utf-8")
    print(f"\nWrote {out} ({out.stat().st_size / 1024:,.0f} KB). Open it in any browser.")


def reconcile_with_report(tables: dict[str, pd.DataFrame], folder: Path) -> None:
    """Recompute the report's executive metrics from the same table and compare.

    The report's executive_metrics.csv compares two six-month cohort windows at
    age 5: blended CAC and cash after CAC per customer, weighted by acquired members.
    """
    path = folder / "executive_metrics.csv"
    if not path.exists():
        print("\nReconciliation skipped: copy report/executive_metrics.csv into the tables folder to run it.")
        return
    report = pd.read_csv(path)
    cash = tables["cohort_cash"]
    print("\nReconciliation with the report's executive metrics (cohort window, age 5):")
    ok = True
    for _, row in report.iterrows():
        start, end, age = pd.Timestamp(row.window_start), pd.Timestamp(row.window_end), int(row.age_month)
        cohorts = cash[(cash.cohort_month >= start) & (cash.cohort_month <= end) & (cash.age_month == age)]
        n = cohorts.n_acquired.sum()
        spend = (cohorts.cac * cohorts.n_acquired).sum()
        blended_cac = spend / n
        cash_after = cohorts.balance_after_acquisition.sum() / n
        match = abs(blended_cac - row.blended_cac) < 0.01 and abs(cash_after - row.cash_after_cac_per_customer) < 0.01
        ok &= match
        print(f"  {start:%b %Y}-{end:%b %Y}: blended CAC {blended_cac:.2f} (report {row.blended_cac:.2f}), "
              f"cash after CAC {cash_after:+.2f} (report {row.cash_after_cac_per_customer:+.2f}) "
              f"-> {'match' if match else 'DIFFERENT'}")
    print("  Result:", "reproduces the report." if ok else "differs from the report; see lines above.")


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tables", type=Path, default=here / "run_outputs", help="folder with the four CSVs")
    ap.add_argument("--out", type=Path, default=here / "dashboard.html", help="HTML file to write")
    ap.add_argument("--snapshots", type=Path, default=here / "monthly_snapshots.json",
                    help="also save the precomputed monthly snapshots as JSON")
    args = ap.parse_args()

    tables = load_tables(args.tables)
    snaps = build_snapshots(tables)
    check_no_lookahead(tables, snaps)
    print_summary(snaps)
    reconcile_with_report(tables, args.tables)
    args.snapshots.write_text(json.dumps(snaps, indent=1), encoding="utf-8")
    write_html(snaps, args.out)


# --------------------------------------------------------------------------- page

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Studio North, month by month</title>
<style>
  :root { --fg:#1f1e1d; --muted:#6b6a68; --line:#e3e1dc; --panel:#faf9f6; --bg:#fff;
          --c0:#3f6fb5; --c1:#d0743c; --c2:#4f9a6b; --c3:#8a63b8; --ok:#2f8a4f; --bad:#c2412d; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
  .sn { max-width:1180px; margin:0 auto; padding:28px 20px 48px; display:grid; gap:28px; }
  h1 { font:400 34px/1.15 Georgia,"Times New Roman",serif; margin:0; }
  h2 { font-size:18px; font-weight:600; margin:0; }
  .lede { margin:8px 0 0; max-width:70ch; }
  .lede b { font-weight:600; font-variant-numeric:tabular-nums; }
  .note, .cap { color:var(--muted); font-size:13px; margin:6px 0 0; max-width:78ch; }
  .player { display:flex; flex-wrap:wrap; align-items:center; gap:14px; padding:14px 18px; background:var(--panel);
            border:1px solid var(--line); border-radius:10px; }
  .month { font:400 22px Georgia,serif; min-width:6.5em; font-variant-numeric:tabular-nums; }
  .btn { font:inherit; font-size:13px; font-weight:600; padding:6px 14px; border-radius:999px; border:1px solid var(--line);
         background:var(--bg); cursor:pointer; }
  .btn:hover { border-color:var(--muted); }
  input[type=range] { flex:1 1 220px; accent-color:var(--fg); }
  .ends { display:flex; justify-content:space-between; width:100%; color:var(--muted); font-size:12px; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }
  .tile { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:16px; display:grid; gap:4px; }
  .label { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }
  .big { font:400 30px/1.1 Georgia,serif; font-variant-numeric:tabular-nums; }
  .sub { font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .good { color:var(--ok); } .bad { color:var(--bad); }
  .two { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr)); gap:28px; }
  section { display:grid; gap:8px; min-width:0; }
  .legend { display:flex; flex-wrap:wrap; gap:14px; font-size:12px; color:var(--muted); }
  .legend span { display:inline-flex; align-items:center; gap:6px; }
  .sw { width:12px; height:12px; border-radius:2px; display:inline-block; }
  .swl { width:16px; height:3px; }
  .chart, .tri { position:relative; min-width:0; }
  .tri { overflow-x:auto; }
  svg text { font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
  .axis { fill:var(--muted); font-size:11px; }
  .tip { position:absolute; pointer-events:none; background:#fff; border:1px solid var(--line); border-radius:8px;
         box-shadow:0 4px 14px rgba(0,0,0,.12); padding:8px 10px; font-size:12px; white-space:nowrap; z-index:2;
         font-variant-numeric:tabular-nums; }
  .tip .k { color:var(--muted); }
  .empty { margin:0; padding:16px; border:1px dashed var(--line); border-radius:10px; color:var(--muted); }
  details { font-size:13px; color:var(--muted); }
  summary { cursor:pointer; color:var(--fg); }
  dl { display:grid; grid-template-columns:max-content 1fr; gap:4px 16px; margin:8px 0 0; }
  dt { color:var(--fg); } dd { margin:0; }
  .badge { display:inline-block; font-size:11px; font-weight:600; padding:1px 8px; border-radius:999px;
           background:#eef3fb; color:var(--c0); margin-left:6px; vertical-align:middle; }
</style>
</head>
<body>
<div class="sn">
  <header>
    <h1>Studio North, month by month</h1>
    <p class="lede">At the end of <b id="asof">—</b>, <b id="paying">—</b> members paid, and monthly contribution of
      <b id="contrib">—</b> was <span id="cmp">—</span> tracked acquisition spend of <b id="spend">—</b>.
      Since January 2024, total contribution has <span id="cumcmp">—</span> total acquisition spend by <b id="cumgap">—</b>.</p>
    <p class="note">A replay of the observed records behind the Studio North customer-economics report, as they stood at the end
      of each month. Every figure is <b>observed</b> <span class="badge">observed</span>; nothing here is a forecast.
      Contribution is net revenue minus recorded variable costs; fixed costs are not in the data. Model forecasts and the final
      recommendation use all data through December 2025, so they are not replayed here.</p>
  </header>

  <div class="player">
    <span class="month" id="asof2">—</span>
    <button class="btn" id="play">Play from January 2024</button>
    <button class="btn" id="pause" hidden>Pause</button>
    <input type="range" id="range" min="0" step="1" aria-label="Month to replay">
    <div class="ends"><span id="end0"></span><span id="end1"></span></div>
  </div>

  <div class="tiles">
    <div class="tile"><span class="label">Paying members</span><span class="big" id="t-paying">—</span><span class="sub">in the month</span></div>
    <div class="tile"><span class="label">Monthly contribution</span><span class="big" id="t-contrib">—</span><span class="sub"><span id="t-change">—</span> vs prior month</span></div>
    <div class="tile"><span class="label">Acquisition spend</span><span class="big" id="t-spend">—</span><span class="sub">tracked, in the month</span></div>
    <div class="tile"><span class="label">New members</span><span class="big" id="t-new">—</span><span class="sub">historical CAC <span id="t-cac">—</span> each</span></div>
    <div class="tile"><span class="label">Contribution less spend</span><span class="big" id="t-cum">—</span><span class="sub">cumulative since Jan 2024</span></div>
  </div>

  <section>
    <h2>Monthly contribution overtook acquisition spend</h2>
    <div class="legend"><span><i class="sw" style="background:var(--c0)"></i>Contribution in the month</span>
      <span><i class="sw swl" style="background:var(--c1)"></i>Tracked acquisition spend</span></div>
    <div class="chart" id="company-chart"></div>
    <p class="cap">All paying members in each calendar month, from every joining cohort. The outlined bar is the month being replayed.</p>
  </section>

  <div class="two">
    <section>
      <h2>Share of each cohort still subscribed</h2>
      <div class="tri" id="retention-tri"></div>
      <p class="cap">Rows are joining months; columns are months since the first payment (age 0). Each cell is the percent of the
        original cohort still under contract. Outlined cells were observed in the month being replayed; blank cells were still in the future.</p>
    </section>
    <section>
      <h2>Cash after acquisition cost, per member</h2>
      <div class="legend"><span><i class="sw" style="background:var(--bad)"></i>Acquisition cost not yet recovered</span>
        <span><i class="sw" style="background:var(--ok)"></i>Recovered</span></div>
      <div class="tri" id="cash-tri"></div>
      <p class="cap">Cumulative contribution minus the cohort's acquisition spend, in US dollars per acquired member.</p>
    </section>
  </div>

  <section>
    <h2>Cost per additional buyer in the monthly holdout tests</h2>
    <div class="legend">
      <span><i class="sw swl" style="background:var(--c0)"></i>Search / build skill</span>
      <span><i class="sw swl" style="background:var(--c1)"></i>Partner / build skill</span>
      <span><i class="sw swl" style="background:var(--c2)"></i>Search / save time</span>
      <span><i class="sw swl" style="background:var(--c3)"></i>Partner / save time</span>
    </div>
    <p class="empty" id="icac-empty" hidden>No holdout test had finished by this month. The monthly tests began on 1 July 2025,
      and each needs a 31-day follow-up.</p>
    <div class="chart" id="icac-chart"></div>
    <p class="cap">Each wave held back 500 of 1,000 prospects and spent $11,000 on the other 500. iCAC is that spend divided by the
      extra buyers over the control group; whiskers show the 95% interval. A wave appears once its follow-up has ended. These tests
      measure acquisition only, not retention or revenue.</p>
  </section>

  <details>
    <summary>Definitions</summary>
    <dl>
      <dt>Cohort</dt><dd>Members whose first positive payment fell in the same calendar month.</dd>
      <dt>Contribution</dt><dd>Net revenue (after discounts and refunds) minus recorded variable costs.</dd>
      <dt>Historical CAC</dt><dd>Tracked acquisition spend for a joining month divided by the members acquired that month.</dd>
      <dt>iCAC</dt><dd>Incremental cost of acquisition: test spend divided by treatment buyers minus control buyers.</dd>
      <dt>As-of rule</dt><dd>Each month's view is precomputed by build_dashboard.py from records dated by that month's end only.</dd>
      <dt>Data cutoff</dt><dd>1 January 2026, exclusive. Dataset version 5.1, USD, UTC.</dd>
    </dl>
  </details>
</div>

<script>
const DATA = __DATA__;
const S = DATA.snapshots;
const NS = 'http://www.w3.org/2000/svg';
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const fmtMonth = (s) => MON[+s.slice(5,7)-1] + ' ' + s.slice(0,4);
const fmtShort = (s) => MON[+s.slice(5,7)-1] + " '" + s.slice(2,4);
const money = (v) => (v < 0 ? '−' : '') + '$' + Math.round(Math.abs(v)).toLocaleString('en-US');
const money2 = (v) => '$' + v.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
const signed = (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + '$' + Math.round(Math.abs(v)).toLocaleString('en-US');
const int = (v) => Math.round(v).toLocaleString('en-US');
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const COLORS = ['--c0','--c1','--c2','--c3'].map(css);
const ALL_MONTHS = S[S.length-1].company.map(r => r.month);
const ALL_COHORTS = ALL_MONTHS;               // one cohort per joining month
const MAX_AGE = ALL_MONTHS.length - 1;
let idx = S.length - 1, timer = null;

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function niceMax(v) { const p = Math.pow(10, Math.floor(Math.log10(v))); return Math.ceil(v / p * 2) / 2 * p; }
function tipFor(box) {
  const t = document.createElement('div'); t.className = 'tip'; t.hidden = true; box.appendChild(t);
  return {
    show(ev, lines) {
      t.replaceChildren(...lines.map(([k, v]) => { const d = document.createElement('div');
        if (k) { const s = document.createElement('span'); s.className = 'k'; s.textContent = k + ' '; d.appendChild(s); }
        d.appendChild(document.createTextNode(v)); return d; }));
      t.hidden = false;
      const b = box.getBoundingClientRect();
      let x = ev.clientX - b.left + box.scrollLeft + 12, y = ev.clientY - b.top + 12;
      if (x + t.offsetWidth > box.scrollLeft + box.clientWidth) x = ev.clientX - b.left + box.scrollLeft - t.offsetWidth - 12;
      t.style.left = Math.max(0, x) + 'px'; t.style.top = Math.max(0, y) + 'px';
    },
    hide() { t.hidden = true; },
  };
}
function setText(id, txt, cls) {
  const e = document.getElementById(id); e.textContent = txt;
  e.classList.remove('good','bad'); if (cls) e.classList.add(cls);
}

function drawText(s) {
  const t = s.tiles;
  setText('asof', fmtMonth(s.as_of)); setText('asof2', fmtMonth(s.as_of));
  setText('paying', int(t.paying_members)); setText('contrib', money(t.contribution));
  setText('spend', money(t.acquisition_spend));
  setText('cmp', t.contribution >= t.acquisition_spend ? 'above' : 'below');
  setText('cumcmp', t.cum_balance >= 0 ? 'exceeded' : 'fallen short of');
  setText('cumgap', money(Math.abs(t.cum_balance)));
  setText('t-paying', int(t.paying_members)); setText('t-contrib', money(t.contribution));
  setText('t-change', t.contribution_change == null ? '—' : signed(t.contribution_change),
          t.contribution_change == null ? null : (t.contribution_change >= 0 ? 'good' : 'bad'));
  setText('t-spend', money(t.acquisition_spend));
  setText('t-new', t.new_members == null ? '—' : int(t.new_members));
  setText('t-cac', t.historical_cac == null ? '—' : money2(t.historical_cac));
  setText('t-cum', signed(t.cum_balance), t.cum_balance >= 0 ? 'good' : 'bad');
}

function drawCompany(s) {
  const box = document.getElementById('company-chart'); box.replaceChildren();
  const all = S[S.length-1].company, m = s.as_of;
  const W = Math.max(300, box.clientWidth || 700), H = 260, mt = 12, mb = 28, ml = 52, mr = 8;
  const svg = el('svg', {width:'100%', viewBox:`0 0 ${W} ${H}`, role:'img',
    'aria-label': `Monthly contribution and acquisition spend from January 2024 to ${fmtMonth(m)}`}, box);
  const ymax = niceMax(Math.max(...all.map(r => Math.max(r.contribution, r.spend))) * 1.05);
  const y = (v) => H - mb - (v / ymax) * (H - mt - mb);
  for (let i = 0; i <= 5; i++) { const v = ymax * i / 5;
    el('line', {x1:ml, x2:W-mr, y1:y(v), y2:y(v), stroke:'#eceae5'}, svg);
    el('text', {x:ml-8, y:y(v), dy:'0.35em', 'text-anchor':'end', class:'axis'}, svg).textContent = '$' + Math.round(v/1000) + 'k'; }
  const step = (W - ml - mr) / all.length, bw = step * 0.78;
  const x = (i) => ml + i * step + (step - bw) / 2;
  const every = W < 520 ? 6 : 3;
  all.forEach((r, i) => { if (i % every === 0)
    el('text', {x:x(i)+bw/2, y:H-mb+16, 'text-anchor':'middle', class:'axis'}, svg).textContent = fmtShort(r.month); });
  const shown = s.company;
  shown.forEach((r, i) => el('rect', {x:x(i), width:bw, y:y(r.contribution), height:y(0)-y(r.contribution),
    fill:COLORS[0], 'fill-opacity': r.month === m ? 1 : 0.7, stroke: r.month === m ? '#1f1e1d' : 'none', 'stroke-width':1.5}, svg));
  el('path', {d: shown.map((r, i) => (i ? 'L' : 'M') + (x(i)+bw/2) + ',' + y(r.spend)).join(''),
    fill:'none', stroke:COLORS[1], 'stroke-width':2.5}, svg);
  shown.forEach((r, i) => el('circle', {cx:x(i)+bw/2, cy:y(r.spend), r: r.month === m ? 4 : 2.5, fill:COLORS[1]}, svg));
  const tip = tipFor(box);
  shown.forEach((r, i) => { const h = el('rect', {x:ml+i*step, width:step, y:mt, height:H-mt-mb, fill:'transparent'}, svg);
    h.addEventListener('pointermove', (ev) => tip.show(ev, [['', fmtMonth(r.month)], ['Contribution', money(r.contribution)],
      ['Acquisition spend', money(r.spend)], ['Paying members', int(r.paying)]]));
    h.addEventListener('pointerleave', tip.hide); });
}

function drawTriangle(id, cells, m, opts) {
  const box = document.getElementById(id); box.replaceChildren();
  const W = Math.max(320, box.clientWidth || 520), ml = 58, mt = 18;
  const nA = MAX_AGE + 1, cw = Math.max(12, Math.floor((W - ml - 4) / nA));
  const ch = Math.max(13, Math.min(20, Math.round(cw * 0.72)));
  const Wd = ml + cw * nA + 4, H = mt + ch * ALL_COHORTS.length + 4;
  const svg = el('svg', {width:Wd, height:H, viewBox:`0 0 ${Wd} ${H}`, role:'img', 'aria-label': opts.label(m)}, box);
  const yi = new Map(ALL_COHORTS.map((c, i) => [c, i]));
  ALL_COHORTS.filter(c => c <= m).forEach(c => el('text', {x:ml-6, y:mt+yi.get(c)*ch+ch/2, dy:'0.35em',
    'text-anchor':'end', class:'axis'}, svg).textContent = fmtShort(c));
  const st = cw < 22 ? 6 : 3;
  for (let a = 0; a < nA; a += st) el('text', {x:ml+a*cw+cw/2, y:mt-6, 'text-anchor':'middle', class:'axis'}, svg).textContent = a;
  const tip = tipFor(box), labels = cw >= 24;
  cells.forEach(r => {
    const g = el('g', {transform:`translate(${ml + r.age*cw},${mt + yi.get(r.cohort)*ch})`}, svg);
    el('rect', {width:cw-1, height:ch-1, fill:opts.fill(r.v), 'fill-opacity':opts.opacity(r.v),
      stroke: r.cal === m ? '#1f1e1d' : 'none', 'stroke-width':1.2}, g);
    if (labels) { const t = el('text', {x:(cw-1)/2, y:(ch-1)/2, dy:'0.35em', 'text-anchor':'middle',
      'font-size': Math.min(11, ch-4), fill:'#1f1e1d'}, g); t.textContent = opts.label1(r.v); }
    g.addEventListener('pointermove', (ev) => tip.show(ev, opts.tip(r)));
    g.addEventListener('pointerleave', tip.hide);
  });
}

function drawTests(s) {
  const box = document.getElementById('icac-chart'); box.replaceChildren();
  const empty = document.getElementById('icac-empty');
  const done = s.tests; empty.hidden = done.length > 0;
  if (!done.length) return;
  const allTests = S[S.length-1].tests;
  const waves = [...new Set(allTests.map(r => r.wave))].sort();
  const W = Math.max(300, box.clientWidth || 700), H = 260, mt = 12, mb = 28, ml = 52, mr = 12;
  const svg = el('svg', {width:'100%', viewBox:`0 0 ${W} ${H}`, role:'img',
    'aria-label': `Incremental cost per additional buyer for holdout waves completed by ${fmtMonth(s.as_of)}`}, box);
  const ymax = niceMax(Math.max(...allTests.map(r => r.hi)));
  const y = (v) => H - mb - (v / ymax) * (H - mt - mb);
  for (let i = 0; i <= 5; i++) { const v = ymax * i / 5;
    el('line', {x1:ml, x2:W-mr, y1:y(v), y2:y(v), stroke:'#eceae5'}, svg);
    el('text', {x:ml-8, y:y(v), dy:'0.35em', 'text-anchor':'end', class:'axis'}, svg).textContent = '$' + Math.round(v); }
  const x = (w) => ml + 24 + waves.indexOf(w) * (W - ml - mr - 48) / Math.max(1, waves.length - 1);
  waves.forEach(w => el('text', {x:x(w), y:H-mb+16, 'text-anchor':'middle', class:'axis'}, svg).textContent = fmtMonth(w));
  const tip = tipFor(box);
  DATA.series.forEach((k, i) => {
    const off = (i - 1.5) * 5;
    const pts = done.filter(r => r.key === k).sort((a, b) => a.wave < b.wave ? -1 : 1);
    pts.forEach(r => el('line', {x1:x(r.wave)+off, x2:x(r.wave)+off, y1:y(r.lo), y2:y(r.hi),
      stroke:COLORS[i], 'stroke-opacity':0.45, 'stroke-width':1.5}, svg));
    el('path', {d: pts.map((r, j) => (j ? 'L' : 'M') + (x(r.wave)+off) + ',' + y(r.icac)).join(''),
      fill:'none', stroke:COLORS[i], 'stroke-width':2.2}, svg);
    pts.forEach(r => { const c = el('circle', {cx:x(r.wave)+off, cy:y(r.icac), r:4, fill:COLORS[i]}, svg);
      c.addEventListener('pointermove', (ev) => tip.show(ev, [
        ['', `${k.replace('/', ' / ').replace('_', ' ')}, ${fmtMonth(r.wave)} wave`],
        ['iCAC', `${money(r.icac)} (95% CI ${money(r.lo)}–${money(r.hi)})`], ['Credited CAC', money(r.credited)],
        ['Buyers', `${r.tb} treated vs ${r.cb} held back = ${int(r.inc)} extra`]]));
      c.addEventListener('pointerleave', tip.hide); });
  });
}

function draw() {
  const s = S[idx];
  drawText(s); drawCompany(s);
  drawTriangle('retention-tri', s.retention, s.as_of, {
    fill: () => COLORS[0], opacity: (v) => 0.1 + 0.75 * v, label1: (v) => Math.round(v * 100),
    label: (m) => `Retention triangle as of ${fmtMonth(m)}`,
    tip: (r) => [['', `${fmtMonth(r.cohort)} cohort, age ${r.age}`], ['Observed in', fmtMonth(r.cal)],
      ['Still subscribed', (r.v * 100).toFixed(1) + '%'], ['Members', `${int(r.n_ret)} of ${int(r.n_acq)}`]] });
  drawTriangle('cash-tri', s.cash, s.as_of, {
    fill: (v) => v >= 0 ? css('--ok') : css('--bad'), opacity: (v) => 0.12 + 0.7 * Math.min(1, Math.abs(v) / 200),
    label1: (v) => Math.round(v), label: (m) => `Cash after acquisition cost per member as of ${fmtMonth(m)}`,
    tip: (r) => [['', `${fmtMonth(r.cohort)} cohort, age ${r.age}`], ['Observed in', fmtMonth(r.cal)],
      ['Per member', signed(r.v)], ['Historical CAC', money2(r.cac)], ['Cumulative contribution', money(r.cum)]] });
  drawTests(s);
}

const range = document.getElementById('range'), play = document.getElementById('play'), pause = document.getElementById('pause');
range.max = S.length - 1; range.value = idx;
document.getElementById('end0').textContent = fmtMonth(S[0].as_of);
document.getElementById('end1').textContent = fmtMonth(S[S.length-1].as_of);
const stop = () => { clearInterval(timer); timer = null; play.hidden = false; pause.hidden = true; };
range.addEventListener('input', () => { stop(); idx = +range.value; draw(); });
play.addEventListener('click', () => {
  idx = 0; range.value = 0; draw(); play.hidden = true; pause.hidden = false;
  const slow = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  timer = setInterval(() => { if (idx >= S.length - 1) return stop(); idx += 1; range.value = idx; draw(); }, slow ? 1600 : 900);
});
pause.addEventListener('click', stop);
document.addEventListener('keydown', (e) => {
  if (e.target === range) return;
  if (e.key === 'ArrowRight' && idx < S.length - 1) { stop(); idx++; range.value = idx; draw(); }
  if (e.key === 'ArrowLeft' && idx > 0) { stop(); idx--; range.value = idx; draw(); }
});
let rt; window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(draw, 150); });
draw();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
