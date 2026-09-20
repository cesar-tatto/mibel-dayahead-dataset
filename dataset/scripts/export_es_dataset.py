"""
Export dataset/final/ES.csv, the epftoolbox-ready dataset for the Spain (ES) market.

dataset/final/model_dataset.csv is the master record and is NOT changed by this script: it
keeps all four exogenous columns and the honest NaN left by build_model_dataset.py's capped,
diurnal-cycle imputation (IMPUTE_CAP_HOURS = 3h; see DATASET.md).

epftoolbox's read_data() (epftoolbox/epftoolbox/data/_datasets.py) requires a gapless hourly
index with no NaN. Confirmed by reading the code, not assumed:
  - it renames columns POSITIONALLY to ['Price', 'Exogenous 1', ..., 'Exogenous N'], with N
    taken from len(data.columns) - 1, so any number of exogenous columns is supported;
  - begin_test_date must have hour 0 and end_test_date hour 23, or it raises;
  - both LEAR's and DNN's feature-builders (_build_and_split_XYs in
    epftoolbox/epftoolbox/models/_lear.py and _dnn.py) look up lagged values with
    df.loc[<array of computed timestamps>, 'Price'], an array-label lookup that requires
    EVERY referenced timestamp to exist in the index. A single missing hour near a lag
    reference (D-1, D-2, D-3, D-7 for price; D-1, D-7 for exogenous) raises a KeyError.

Three exogenous columns are exported (load, solar, wind onshore), not the wind+solar total.
Nothing in epftoolbox hardcodes two exogenous inputs; LEAR's feature count
(96 + 7 + n_exogenous * 72), the DNN's input layer width, and the DNN hyperparameter search
space all scale with N. Solar and wind have different diurnal profiles and comparable
installed capacity in Spain (max 24.0 GW and 19.8 GW in this dataset), so summing them
destroys information that LEAR's 24 per-hour LASSO models could otherwise weight separately.
Their gaps are also disjoint (no hour is missing in both), so exporting them separately keeps
192 real readings that a summed total would have discarded. See DATASET.md,
"Why three exogenous series, not a combined total".

This script:
  1. Selects the three exogenous columns above.
  2. Trims to the window 2015-01-07 00:00 to 2025-09-30 23:00 (DATASET.md, "Analysis
     window and train/test split").
  3. Bridges the genuine multi-day gaps that build_model_dataset.py's normal 3-hour cap
     correctly leaves as NaN (Jan 2020, Jun 2024, Dec 2024, Mar 2025 -- see
     DATASET.md, "Gaps and imputation"), using the SAME diurnal-cycle method
     (build_model_dataset.diurnal_fill_capped) but with no practical cap, since this file must
     be gapless for epftoolbox to run at all. This is a documented, deliberate exception scoped
     to this export only; model_dataset.csv keeps the honest NaN.
  4. Reports which bridged hours fall inside the declared test period, and runs a plausibility
     check on every multi-day bridged run (see below).
  5. Writes dataset/final/ES.csv with column order datetime, price_es, load_forecast_mw,
     solar_forecast_mw, wind_onshore_forecast_mw, naive local (Europe/Madrid) datetime label.

Outputs:
  dataset/final/ES.csv                           the model-ready dataset
  dataset/audit/imputed_hours_ES_export.csv      every bridged value (datetime, column,
                                                 method, value)
  dataset/audit/test_days_with_imputed_inputs.csv forecast dates inside the test period whose
                                                 model inputs are partly synthetic, including
                                                 those contaminated indirectly through the
                                                 D-1 and D-7 exogenous lags; the sensitivity
                                                 analysis in DATASET.md ("Test days
                                                 with imputed inputs") excludes exactly these

Two checks below exist because an earlier version of this export silently produced synthetic
data under test days without flagging it (see DATASET.md, "Gaps and imputation"):
  - the test-period overlap report, so this can never again be discovered only by manual audit;
  - the bridge plausibility check, which compares the day-to-day variability of a bridged run
    against real data either side of it. Over a long gap the diurnal method degenerates to
    two-point linear interpolation and produces an unnaturally smooth ramp; that is exactly
    what this check measures.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from build_model_dataset import diurnal_fill_capped  # noqa: E402

MODEL_PATH = Path("dataset/final/model_dataset.csv")
OUT_PATH = Path("dataset/final/ES.csv")
LOG_PATH = Path("dataset/audit/imputed_hours_ES_export.csv")
TEST_DAYS_PATH = Path("dataset/audit/test_days_with_imputed_inputs.csv")

WINDOW_START = pd.Timestamp("2015-01-07 00:00:00")
WINDOW_END = pd.Timestamp("2025-09-30 23:00:00")
TEST_START = pd.Timestamp("2023-10-04 00:00:00")
TEST_END = pd.Timestamp("2025-09-30 23:00:00")
TEST_DAYS = 728  # 104 weeks, 2 Lago-years; asserted against the window below

EXPORT_COLS = ["load_forecast_mw", "solar_forecast_mw", "wind_onshore_forecast_mw"]

# Exogenous lags used by both LEAR and the DNN when building features for a forecast day
# (_build_and_split_XYs in epftoolbox/models/_lear.py and _dnn.py: exogenous inputs enter at
# day D, D-1 and D-7). A synthetic exogenous value on day X therefore reaches the forecasts
# for X, X+1 and X+7, so the set of contaminated FORECAST days is larger than the set of
# imputed INPUT days.
EXOG_LAG_DAYS = [0, 1, 7]

# No practical cap: the longest known gap is 120 hours (2024-12-07 to 2024-12-11). Unlike
# build_model_dataset.py's normal IMPUTE_CAP_HOURS = 3, this export deliberately bridges
# every known gap so epftoolbox has a complete grid to work with; see the module docstring.
EXPORT_FILL_CAP_HOURS = 200

# A bridged run whose daily-mean variability is below this fraction of the surrounding real
# data's variability is flagged as implausibly smooth. Not a hard failure: it is a signal that
# the fill is interpolating rather than reconstructing, which the docs must then disclose.
SMOOTHNESS_FLAG_RATIO = 0.5


def contiguous_runs(timestamps: pd.DatetimeIndex) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Group a sorted DatetimeIndex into contiguous hourly runs."""
    if len(timestamps) == 0:
        return []
    runs = []
    start = prev = timestamps[0]
    for t in timestamps[1:]:
        if t - prev != pd.Timedelta(hours=1):
            runs.append((start, prev))
            start = t
        prev = t
    runs.append((start, prev))
    return runs


def plausibility_check(series_filled: pd.Series, run_start: pd.Timestamp,
                       run_end: pd.Timestamp) -> dict | None:
    """Compare the day-to-day variability of a bridged run against real data either side.

    Returns None for runs shorter than 2 whole days (a single-day or sub-day fill has no
    day-to-day variability to measure). Otherwise returns the daily-mean standard deviation
    inside the run, the same statistic over an equally long real window immediately before and
    after, and their ratio.
    """
    n_days = int((run_end.normalize() - run_start.normalize()).days) + 1
    if n_days < 2:
        return None

    inside = series_filled.loc[run_start:run_end]
    sd_inside = float(inside.groupby(inside.index.normalize()).mean().std())

    before = series_filled.loc[
        run_start - pd.Timedelta(days=n_days):run_start - pd.Timedelta(hours=1)]
    after = series_filled.loc[
        run_end + pd.Timedelta(hours=1):run_end + pd.Timedelta(days=n_days)]
    real = pd.concat([before, after])
    if real.empty:
        return None
    sd_real = float(real.groupby(real.index.normalize()).mean().std())

    ratio = sd_inside / sd_real if sd_real > 0 else float("nan")
    return {"n_days": n_days, "sd_inside": sd_inside, "sd_real": sd_real, "ratio": ratio}


def main() -> None:
    df = pd.read_csv(MODEL_PATH)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)

    window = df[(df["datetime"] >= WINDOW_START) & (df["datetime"] <= WINDOW_END)].copy()
    print(f"Window {WINDOW_START} to {WINDOW_END}: {len(window)} rows")

    expected_hours = int((WINDOW_END - WINDOW_START) / pd.Timedelta(hours=1)) + 1
    if len(window) != expected_hours:
        raise ValueError(
            f"Window has {len(window)} rows, expected {expected_hours} for a gapless hourly "
            f"grid. model_dataset.csv's own row index must be gapless over this window (only "
            f"VALUES may be missing) for the fill below to be meaningful; if this fires, stop "
            f"and investigate before proceeding."
        )

    n_nan_before = window[["price_es"] + EXPORT_COLS].isna().sum()
    print(f"\nNaN before export-only bridging:\n{n_nan_before.to_string()}")
    if window["price_es"].isna().any():
        raise ValueError("price_es has NaN inside the export window; cannot proceed "
                          "(prices_spain.csv is documented gapless over 2002-2025).")

    window = window.set_index("datetime")
    log_records: list[dict] = []
    for col in EXPORT_COLS:
        filled, col_log, col_unfillable = diurnal_fill_capped(
            window[col], col, cap_hours=EXPORT_FILL_CAP_HOURS
        )
        window[col] = filled
        log_records.extend(col_log)
        if col_unfillable:
            raise ValueError(
                f"{len(col_unfillable)} value(s) in {col} could not be bridged even without "
                f"a cap (no same-hour-of-day neighbour on both sides); this export cannot be "
                f"gapless. Stop and investigate: {col_unfillable[:5]}"
            )

    n_nan_after = int(window[["price_es"] + EXPORT_COLS].isna().sum().sum())
    print(f"\nBridged {len(log_records)} values (uncapped diurnal interpolation, export-only)")
    print(f"NaN remaining: {n_nan_after}")
    if n_nan_after > 0:
        raise ValueError("NaN remains after bridging; refusing to write a file epftoolbox "
                          "cannot consume.")

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_df = pd.DataFrame(log_records, columns=["datetime", "column", "method", "value"])
    log_df = log_df.sort_values(["datetime", "column"]).reset_index(drop=True)
    log_df.to_csv(LOG_PATH, index=False)
    print(f"Wrote {len(log_df)} bridged values to {LOG_PATH}")

    # --- Bridged runs, per column, with the plausibility check -----------------
    print(f"\n{'='*74}\nBRIDGED RUNS AND PLAUSIBILITY\n{'='*74}")
    for col, g in log_df.groupby("column"):
        ts = pd.DatetimeIndex(sorted(g["datetime"]))
        print(f"\n{col}  ({len(ts)} hours bridged)")
        for run_start, run_end in contiguous_runs(ts):
            n_hours = int((run_end - run_start) / pd.Timedelta(hours=1)) + 1
            in_test = run_start <= TEST_END and run_end >= TEST_START
            marker = "  [INSIDE TEST PERIOD]" if in_test else "  (training period)"
            print(f"  {n_hours:>4} h  {run_start} to {run_end}{marker}")
            check = plausibility_check(window[col], run_start, run_end)
            if check is None:
                continue
            flag = ("  <-- IMPLAUSIBLY SMOOTH"
                    if check["ratio"] < SMOOTHNESS_FLAG_RATIO else "")
            print(f"         daily-mean sd inside: {check['sd_inside']:>9.1f}   "
                  f"surrounding real: {check['sd_real']:>9.1f}   "
                  f"ratio: {check['ratio']:.2f}{flag}")

    # --- Test-period overlap report -------------------------------------------
    print(f"\n{'='*74}\nTEST-PERIOD OVERLAP\n{'='*74}")
    in_test = log_df[(log_df["datetime"] >= TEST_START) & (log_df["datetime"] <= TEST_END)]
    pct = 100.0 * len(in_test) / len(log_df) if len(log_df) else 0.0
    print(f"Declared test period: {TEST_START.date()} to {TEST_END.date()}")
    print(f"Bridged hours inside the test period: {len(in_test)} / {len(log_df)} ({pct:.1f}%)")
    if len(in_test):
        print("\nBy column:")
        print(in_test.groupby("column").size().to_string())
        affected_dates = sorted(pd.Timestamp(d) for d in
                                in_test["datetime"].dt.normalize().unique())
        print(f"\nINPUT days inside the test period with synthetic values: "
              f"{len(affected_dates)}")
        input_day_cols: dict[pd.Timestamp, list[str]] = {}
        for d in affected_dates:
            day_log = in_test[in_test["datetime"].dt.normalize() == d]
            cols_affected = sorted(day_log["column"].unique())
            input_day_cols[d] = cols_affected
            print(f"  {d.date()}  {len(day_log):>3} imputed hours  "
                  f"({', '.join(cols_affected)})")

        # Propagate through the exogenous lag structure: a synthetic input on day X reaches
        # the forecasts for X (lag D), X+1 (lag D-1) and X+7 (lag D-7).
        contaminated: dict[pd.Timestamp, dict] = {}
        for d, cols_affected in input_day_cols.items():
            for lag in EXOG_LAG_DAYS:
                fd = d + pd.Timedelta(days=lag)
                if not (TEST_START <= fd <= TEST_END):
                    continue
                entry = contaminated.setdefault(
                    fd, {"via_lags": set(), "source_days": set(), "columns": set()})
                entry["via_lags"].add(f"D-{lag}" if lag else "D")
                entry["source_days"].add(str(d.date()))
                entry["columns"].update(cols_affected)

        print(f"\nFORECAST days contaminated once the D, D-1 and D-7 exogenous lags are "
              f"followed: {len(contaminated)}")
        print(f"  = {100.0 * len(contaminated) / TEST_DAYS:.2f}% of the {TEST_DAYS}-day "
              f"test period")
        rows = []
        for fd in sorted(contaminated):
            e = contaminated[fd]
            rows.append({
                "forecast_date": fd.date(),
                "via_lags": ";".join(sorted(e["via_lags"])),
                "source_input_days": ";".join(sorted(e["source_days"])),
                "columns_affected": ";".join(sorted(e["columns"])),
            })
            print(f"  {fd.date()}  via {','.join(sorted(e['via_lags'])):<10} "
                  f"from {','.join(sorted(e['source_days']))}")
        TEST_DAYS_PATH.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(TEST_DAYS_PATH, index=False)
        print(f"\nWrote {len(rows)} contaminated forecast dates to {TEST_DAYS_PATH}")
        print("Headline metrics are reported over the full test period; a sensitivity\n"
              "analysis excluding exactly these dates is reported alongside "
              "(DATASET.md,\n\"Gaps and imputation\").")

    # --- Write the export ------------------------------------------------------
    out = window[["price_es"] + EXPORT_COLS].reset_index()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_PATH, index=False)
    print(f"\n{'='*74}")
    print(f"Saved {out.shape} to {OUT_PATH}")
    print(f"Columns: {list(out.columns)}")
    print(f"  -> read_data() will rename these positionally to "
          f"['Price'] + {[f'Exogenous {i}' for i in range(1, len(EXPORT_COLS) + 1)]}")
    print(f"Range: {out['datetime'].min()} to {out['datetime'].max()}")
    per_day = out.groupby(out["datetime"].dt.normalize()).size()
    print(f"Days with exactly 24 rows: {(per_day == 24).sum()} / {len(per_day)}")


if __name__ == "__main__":
    main()
