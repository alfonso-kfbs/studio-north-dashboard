# Studio North, month by month

A dashboard that replays the Studio North customer-economics report one month at a time, from January 2024 to December 2025. For each month it shows only what Studio North could have known by the end of that month.

This project extends the original [Studio North Customer Economics Lab](https://github.com/longxiutian/customer-economics-lab). The original data, code and report are kept unchanged as the baseline.

**Live version (claude.ai):** https://claude.ai/artifact/Ssp8k2npzZp96dSvjBYGBK

## What it shows

Move the slider (or press **Play from January 2024**, or use the ← → keys) to step through the months. For the selected month you see:

| Element | What it shows |
|---|---|
| Headline and five tiles | Paying members, monthly contribution (and change vs the prior month), tracked acquisition spend, new members and their historical CAC, and cumulative contribution less cumulative acquisition spend since January 2024 |
| Contribution vs spend chart | Monthly contribution (bars) against tracked acquisition spend (line), up to the selected month |
| Retention triangle | Share of each joining cohort still subscribed, by months since joining |
| Cash-after-acquisition triangle | Each cohort's cumulative contribution minus its acquisition spend, per member (red = cost not yet recovered, green = recovered). This is the payback view. |
| Holdout test chart | Incremental CAC (iCAC) with 95% intervals for the channel-by-need holdout tests, shown only once each test's 31-day follow-up has ended |

## How the as-of rule works

`build_dashboard.py` precomputes a separate snapshot for each month M, using only records dated by the end of M:

- company months with `calendar_month <= M`
- cohort cells (cohort × age) with `calendar_month <= M`. Cohorts that had not joined yet are hidden.
- holdout tests whose `followup_end_at` is on or before the first day of month M+1

**Look-ahead check:** every run rebuilds each month's snapshot from copies of the tables with all later records deleted. The script stops with an error if any month's view changes.

**Observed vs forecast:** every figure is an observed result. The report's forecasts (sBG retention, LTV projections) and its final recommendation were fitted on all data through December 2025. Showing them in earlier months would leak future information, so they are not replayed. The report PDF stays the reference for forecasts.

## Definitions (same as the report)

- **Cohort**: members whose first positive payment fell in the same calendar month.
- **Contribution**: net revenue (after discounts and refunds) minus recorded variable costs. Fixed costs are not in the data.
- **Historical CAC**: tracked acquisition spend for a joining month divided by members acquired that month.
- **Cash after acquisition / payback**: a cohort's cumulative contribution minus its acquisition spend. Payback is the first age at which it turns positive.
- **iCAC**: test spend divided by (treatment buyers − control buyers).
- **Data cutoff**: 1 January 2026, exclusive. Dataset version 5.1, USD, UTC.

## How to run it

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/). Dependencies are declared inside the script and pinned in `build_dashboard.py.lock`.

```bash
git clone https://github.com/alfonso-kfbs/studio-north-dashboard.git
cd studio-north-dashboard
uv run build_dashboard.py
```

Then open `dashboard.html` in any browser. It is self-contained and works offline.

The script prints the look-ahead check result and a month-by-month table. It writes:
- `dashboard.html`: the interactive dashboard
- `monthly_snapshots.json`: the precomputed snapshot for every month

Options: `--tables DIR` (folder with the four CSVs, default `run_outputs/`) and `--out FILE`.

## Inputs

`run_outputs/` holds four tables saved by my lab run (`runs/sn-20261116/tables/`), unchanged, plus `executive_metrics.csv` from the run's `report/` folder (used only for the reconciliation check):

| File | Contents |
|---|---|
| `company_totals.csv` | Paying members, net revenue, contribution and acquisition spend per calendar month |
| `cohort_totals.csv` | Each cohort's members, retained share and contribution at each month of age |
| `cohort_cash_economics.csv` | Each cohort's acquisition spend, historical CAC and cumulative contribution less spend |
| `channel_cac.csv` | The 24 channel-by-need holdout tests (July–December 2025) with iCAC and 95% intervals |

## Reconciliation with the original report

The report's headline comparison (`executive_metrics.csv`) was computed from the same `cohort_cash_economics.csv` file used here; the SHA-256 recorded in the report matches. On every run, the script recomputes those figures from the dashboard's input and compares them:

| Cohort window (age 5) | Blended CAC | Cash after CAC per customer | Report |
|---|---|---|---|
| Jan–Jun 2024 | $107.67 | −$1.16 | match |
| Jan–Jun 2025 | $85.31 | +$45.80 | match |

At the latest month (December 2025) the dashboard shows 3,007 paying members, $99,473 monthly contribution, $21,915 acquisition spend, 293 new members at a historical CAC of $74.80, and +$609,586 cumulative contribution less spend.

## What changes as the months advance

- **June 2024**: monthly contribution first exceeds monthly acquisition spend.
- **December 2024**: cumulative contribution first covers cumulative acquisition spend.
- **Newer cohorts are cheaper and pay back faster.** Historical CAC fell from about $111 (early 2024) to about $71–75 (late 2025). The first observed payback age fell from 6–7 months (Jan–Mar 2024 cohorts) to 4 (mid/late 2024), 3 (early 2025) and 2 (Aug–Oct 2025).
- **By November 2025** (when the June 2025 cohort reaches age 5), the whole Jan–Jun 2025 window can be compared at age 5: +$45.80 cash after CAC per customer vs −$1.16 for the Jan–Jun 2024 cohorts. That is the report's headline comparison, and it is the earliest month the full comparison was knowable.
- **July 2025 onward**: holdout tests show the incremental cost of an extra buyer (iCAC, about $214–236 on average per wave) is far above historical CAC. Scaling paid acquisition would cost much more per added member than past averages suggest.

## Repository layout

```
build_dashboard.py          # builds the dashboard (this extension)
build_dashboard.py.lock     # uv lockfile for the script's dependencies
dashboard.html              # generated dashboard
monthly_snapshots.json      # generated monthly snapshots
run_outputs/                # the four input tables from my lab run
report/                     # the original report PDF (baseline)
cohort_lab/, data/, sql/,   # the original Studio North code and data (baseline, unchanged)
pyproject.toml, uv.lock
```
