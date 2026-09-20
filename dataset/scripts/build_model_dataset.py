"""
Step 4 of the data pipeline: merge hourly OMIE prices (prices_spain.csv,
Spanish local clock time CET/CEST) with the ENTSO-E exogenous variables
(exogenous_spain.csv, column datetime_utc, genuinely UTC) into the final
model-ready dataset, dataset/final/model_dataset.csv.

An earlier version of this script joined the two files on the raw timestamp
label directly, with no timezone conversion. Because one side was local time
and the other UTC, price and exogenous values in the same row referred to
real-world hours 1 h apart in winter and 2 h apart in summer -- see
DATASET.md for the conventions this implements.
That earlier version and its output are not kept; there is no interest in
preserving reproducibility of a result that was simply wrong. This script is
the only merge step now.

Convention followed (Lago et al. 2021, Applied Energy 293:116983, sec. 3.1):
"All available time series are saved using the local time, and the daylight
savings are treated by either arithmetically averaging two values from the
extra hour or interpolating the neighboring values for the missing
observation." prices_spain.csv already follows this. This script makes
exogenous_spain.csv follow it too, so both sides of the merge share the same
convention: naive Europe/Madrid wall-clock labels, 24 hours every day.

An earlier version of this step also forward-filled the generation columns
with an unbounded ffill(), applied to the whole column, not to any specific
gap. Combined with a bug in merge_entsoe.py's pivot (fixed there; see
DATASET.md), this silently fabricated data for gaps of well over 100
hours, including through daytime for solar. Removed. Genuinely missing
values (step 6 below) are either filled with a capped, diurnal-cycle-aware
method or left as NaN and reported if the gap exceeds the cap, and every
fill is logged to dataset/audit/imputed_hours.csv.

Pipeline, in order (the UTC->local conversion happens BEFORE the DST
collapse, never after):
  1. Read exogenous_spain.csv, explicitly localize datetime_utc as UTC.
  2. Resample 15-min -> hourly by mean, still in UTC (equivalent to
     resampling after conversion on every non-transition day, since the
     Europe/Madrid offset from UTC is always a whole number of hours;
     doing it in UTC first avoids pandas ambiguous/nonexistent-time
     handling entirely).
  3. Convert the hourly UTC index to Europe/Madrid (tz_convert). This
     naturally produces 23-hour days in spring and 25-hour days in autumn.
  4. Drop tz info -> naive local wall-clock label, matching prices_spain.csv.
     This is a pure relabeling of the already-converted instants (not a new
     transformation): local 02:00 CEST and local 02:00 CET both collapse to
     the naive label "02:00" (the 25-hour autumn day), and the skipped
     spring-forward hour leaves a true gap in the naive label sequence (the
     23-hour spring day). Doing this immediately after conversion, rather
     than after the DST collapse, keeps every remaining step in plain
     wall-clock terms and avoids tz-aware date arithmetic (which has a real
     pitfall: a tz-aware date_range with freq="1h" steps by absolute
     60-minute increments, not wall-clock hours, and silently overshoots
     into the next calendar day on a 23-hour spring day).
  5. Collapse each day to exactly 24 hours using the SAME index arithmetic
     as parse_omie.py's DST handling (parse_omie.py itself is not imported
     or modified, per instruction; the rule is mirrored exactly below,
     generalized from one price column to all exogenous columns):
       n=23 (spring, local 02:00 missing): interpolate it as the average of
            the neighbouring hours 01:00 and 03:00.
       n=25 (autumn, local 02:00 duplicated): average the two 02:00 readings.
     Days with an irregular row count that are NOT a genuine DST transition
     (verified against the real Europe/Madrid tz calendar, not inferred from
     the row count) are left uncollapsed and reported separately -- e.g. the
     series' first calendar day, 2015-01-01, has only 23 hours because the
     raw file starts at 2015-01-01 00:00 UTC (01:00 local), not because of a
     spring-forward transition; collapsing it would mislabel every hour.
  6. Fill remaining NaN gaps in load_forecast_mw, solar_forecast_mw and
     wind_onshore_forecast_mw with a capped, diurnal-cycle rule: for each
     missing hour, linearly interpolate between the same hour-of-day on the
     nearest available day before and the nearest available day after. Never
     applied to a run of more than IMPUTE_CAP_HOURS consecutive missing
     hours; longer gaps are left as NaN and reported, not silently filled.
     wind_solar_total_forecast_mw is not independently filled; it is
     recomputed from its own (now possibly-filled) components wherever it is
     NaN, so it never disagrees with them. Every value actually filled is
     logged to dataset/audit/imputed_hours.csv (datetime, column, method, value).
  7. Trim to the price date range and merge on the local datetime label.

Outputs:
  dataset/working/exogenous_spain_local.csv (intermediate: localized,
      24h-collapsed exogenous series, naive local datetime label)
  dataset/final/model_dataset.csv           (final: merged with prices --
      the dataset used for modeling)
  dataset/audit/imputed_hours.csv           (audit log of every value filled
      by step 6)

Does NOT touch dataset/working/exogenous_spain.csv or dataset/working/prices_spain.csv.
"""
import numpy as np
import pandas as pd
from pathlib import Path
from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError

PRICES_PATH = Path("dataset/working/prices_spain.csv")
EXOG_PATH = Path("dataset/working/exogenous_spain.csv")
EXOG_LOCAL_OUT = Path("dataset/working/exogenous_spain_local.csv")
MODEL_OUT = Path("dataset/final/model_dataset.csv")
IMPUTED_LOG_PATH = Path("dataset/audit/imputed_hours.csv")

LOCAL_TZ = "Europe/Madrid"
IMPUTE_CAP_HOURS = 3  # never fill a run of more than this many consecutive hours, by any method


def collapse_dst_day(group: pd.DataFrame) -> pd.DataFrame:
    """Collapse one local calendar day to exactly 24 hourly rows.

    Mirrors parse_omie.py's DST rule (lines 63-74 of that file) index-for-index,
    generalized from a single price column to an arbitrary set of numeric
    exogenous columns. Not imported from parse_omie.py (which stays untouched)
    but must stay arithmetically identical to it:
      n=23: prices[:2] + [avg(prices[1], prices[2])] + prices[2:]
      n=25: prices[:2] + [avg(prices[2], prices[3])] + prices[4:]
    """
    n = len(group)
    if n == 24:
        return group
    values = group.to_numpy()
    if n == 23:
        interp = (values[1] + values[2]) / 2.0
        out = np.vstack([values[:2], interp[None, :], values[2:]])
    elif n == 25:
        merged = (values[2] + values[3]) / 2.0
        out = np.vstack([values[:2], merged[None, :], values[4:]])
    else:
        raise ValueError(
            f"Day starting {group.index[0]} has {n} local hours; "
            f"expected 23 (spring), 24 (normal), or 25 (autumn)."
        )
    # Build the output index from naive wall-clock hour positions (00:00..23:00),
    # not tz-aware arithmetic: date_range with a tz-aware anchor and freq="1h"
    # steps by absolute 60-minute increments, which overshoots into the next
    # calendar day on a 23-hour spring-forward day. parse_omie.py never touches
    # tz-aware timestamps at all -- it works purely on hour positions within a
    # day -- and this mirrors that.
    local_midnight = pd.Timestamp(group.index[0].date())
    new_index = pd.date_range(local_midnight, periods=24, freq="1h")
    return pd.DataFrame(out, index=new_index, columns=group.columns)


def true_dst_kind(date) -> str | None:
    """Ground truth for whether `date` is a real Europe/Madrid DST transition
    day, using the actual tz database rather than trusting the observed row
    count. A day can have fewer than 24 raw hours for reasons that have
    nothing to do with DST -- e.g. 2015-01-01 has only 23 hours in this data
    because the series starts exactly at 2015-01-01 00:00 UTC (01:00 local),
    not because of a spring-forward transition. Probes local 02:30, which
    always falls inside the nonexistent/ambiguous window on a transition day
    and is always ordinary otherwise."""
    probe = pd.Timestamp(f"{date} 02:30:00")
    try:
        probe.tz_localize(LOCAL_TZ, nonexistent="raise", ambiguous="raise")
        return None
    except NonExistentTimeError:
        return "spring_forward"
    except AmbiguousTimeError:
        return "autumn_fallback"


def collapse_dst_all_days(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """Apply collapse_dst_day to every local calendar day in df (tz-aware,
    Europe/Madrid index) that is a genuine DST transition day. Days with an
    irregular row count that are NOT a real transition (e.g. a data
    boundary or gap) are left untouched and reported separately -- collapsing
    them would silently mislabel every hour for that day.

    Returns (collapsed frame, transition log, anomaly log)."""
    day_key = df.index.normalize()
    frames = []
    transitions = []
    anomalies = []
    for day, group in df.groupby(day_key):
        n = len(group)
        kind = true_dst_kind(day.date())
        if kind is None:
            if n != 24:
                anomalies.append({"date": day.date(), "n_hours": n,
                                   "reason": "irregular row count, but not a DST transition date"})
            frames.append(group)
            continue
        expected_n = 23 if kind == "spring_forward" else 25
        if n != expected_n:
            anomalies.append({"date": day.date(), "n_hours": n,
                               "reason": f"expected {expected_n} hours for a {kind} day, got {n}; left uncollapsed"})
            frames.append(group)
            continue
        transitions.append({"date": day.date(), "kind": kind, "n_hours_before": n})
        frames.append(collapse_dst_day(group))
    out = pd.concat(frames).sort_index()
    return out, transitions, anomalies


def find_nan_runs(mask: pd.Series) -> list[tuple[int, int]]:
    """Return (start_pos, end_pos_inclusive) for every contiguous True run
    in a boolean Series with a positional (0..n-1) sense, via the standard
    diff-of-concatenated-boundary trick."""
    m = mask.to_numpy()
    if not m.any():
        return []
    d = np.diff(np.concatenate(([0], m.view(np.int8), [0])))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    return list(zip(starts.tolist(), (ends - 1).tolist()))


def diurnal_fill_capped(s: pd.Series, column: str, cap_hours: int = IMPUTE_CAP_HOURS):
    """Fill runs of at most `cap_hours` consecutive NaN using a diurnal-cycle
    rule: for each missing hour, linearly interpolate between the same
    hour-of-day on the nearest available day strictly before and the nearest
    available day strictly after. This follows the daily shape (e.g. a solar
    ramp) instead of drawing a straight line across hours of very different
    typical value, which naive time-adjacent interpolation would do.

    Never applied to a run longer than `cap_hours`, and never applied to an
    hour that lacks a same-hour-of-day neighbour on either side (e.g. right
    at the edge of the series): both cases are left as NaN and reported, not
    filled by any method.

    `s` must have a sorted, gap-free-per-present-hour DatetimeIndex (the
    naive local hourly index produced by collapse_dst_all_days).

    Returns (filled_series, log_records, unfillable_records). log_records
    entries are {datetime, column, method, value} for every hour actually
    filled; unfillable_records are {datetime, column, reason} for every hour
    left as NaN.
    """
    filled = s.copy()
    log_records: list[dict] = []
    unfillable: list[dict] = []
    mask = s.isna()

    for start, end in find_nan_runs(mask):
        run_len = end - start + 1
        run_index = s.index[start:end + 1]
        if run_len > cap_hours:
            for t in run_index:
                unfillable.append({"datetime": t, "column": column,
                                    "reason": f"gap of {run_len}h exceeds the {cap_hours}h cap"})
            continue
        for t in run_index:
            same_hour = s[s.index.hour == t.hour]
            before = same_hour.loc[:t].iloc[:-1].dropna()  # strictly before t
            after = same_hour.loc[t:].iloc[1:].dropna()    # strictly after t
            if before.empty or after.empty:
                unfillable.append({"datetime": t, "column": column,
                                    "reason": "no same-hour-of-day value available on both sides"})
                continue
            t_before, v_before = before.index[-1], float(before.iloc[-1])
            t_after, v_after = after.index[0], float(after.iloc[0])
            d_before = (t.normalize() - t_before.normalize()).days
            d_after = (t_after.normalize() - t.normalize()).days
            weight = d_before / (d_before + d_after)
            value = v_before + weight * (v_after - v_before)
            filled.loc[t] = value
            log_records.append({"datetime": t, "column": column,
                                 "method": "diurnal_interp", "value": value})
    return filled, log_records, unfillable


def main() -> None:
    # --- Load prices (for the price date range used to trim exogenous) ---
    prices = pd.read_csv(PRICES_PATH)
    prices["datetime"] = pd.to_datetime(prices["datetime"])
    prices = prices.sort_values("datetime").reset_index(drop=True)
    print(f"Prices: {len(prices)} rows, {prices['datetime'].min()} to {prices['datetime'].max()}")

    # --- Load exogenous, explicitly localize as UTC ---
    exog = pd.read_csv(EXOG_PATH)
    exog["datetime_utc"] = pd.to_datetime(exog["datetime_utc"]).dt.tz_localize("UTC")
    exog = exog.sort_values("datetime_utc").reset_index(drop=True)
    print(f"Exogenous (raw, UTC): {len(exog)} rows, {exog['datetime_utc'].min()} to {exog['datetime_utc'].max()}")

    # No forward-fill here any more. merge_entsoe.py now correctly leaves
    # genuinely missing raw values as NaN (see DATASET.md); an
    # unbounded ffill previously fabricated data for gaps of well over 100
    # hours. Remaining NaN is handled after resampling and DST collapse,
    # with a capped diurnal-cycle rule (step 6), not here.
    gen_cols = ["solar_forecast_mw", "wind_onshore_forecast_mw", "wind_solar_total_forecast_mw"]
    n_raw_nan = exog[gen_cols + ["load_forecast_mw"]].isna().sum()
    print(f"\nRaw NaN counts before resampling (genuinely missing at source):\n{n_raw_nan.to_string()}")

    # --- Resample 15-min -> hourly by mean, still in UTC ---
    exog_hourly_utc = (
        exog.set_index("datetime_utc")
        .resample("1h")
        .mean()
    )
    print(f"\nExogenous (resampled to hourly, UTC): {len(exog_hourly_utc)} rows")

    new_nans = exog_hourly_utc[gen_cols + ["load_forecast_mw"]].isna().sum()
    if new_nans.sum() > 0:
        print(f"  >>> NaNs after resampling:\n{new_nans}")
    else:
        print("  No NaNs introduced by resampling.")

    # --- Convert UTC -> Europe/Madrid (BEFORE the DST collapse) ---
    exog_hourly_local_aware = exog_hourly_utc.tz_convert(LOCAL_TZ)
    print(f"\nConverted to {LOCAL_TZ}: {len(exog_hourly_local_aware)} rows "
          f"({exog_hourly_local_aware.index.min()} to {exog_hourly_local_aware.index.max()})")

    # --- Drop tz info now: a pure relabeling of the already-converted instants,
    # not a new transformation. Local 02:00 CEST and local 02:00 CET both become
    # the naive label "02:00" -- exactly the 25-hour autumn day the collapse
    # step below expects -- and the skipped spring-forward hour leaves a true
    # gap in the naive label sequence. Doing this before collapsing keeps every
    # day's index in plain wall-clock terms, avoiding tz-aware date arithmetic
    # entirely for the rest of the pipeline, matching prices_spain.csv and
    # matching how parse_omie.py itself never touches tz-aware timestamps.
    exog_hourly_local = exog_hourly_local_aware.copy()
    exog_hourly_local.index = exog_hourly_local.index.tz_localize(None)

    # --- Collapse every local day to exactly 24 hours ---
    exog_collapsed, transitions, anomalies = collapse_dst_all_days(exog_hourly_local)
    n_spring = sum(1 for t in transitions if t["kind"] == "spring_forward")
    n_autumn = sum(1 for t in transitions if t["kind"] == "autumn_fallback")
    print(f"\nDST collapse: {len(transitions)} transition days touched "
          f"({n_spring} spring-forward interpolated, {n_autumn} autumn-fallback averaged)")
    print(f"Rows after collapse: {len(exog_collapsed)} "
          f"(was {len(exog_hourly_local)} before collapse)")
    for t in transitions:
        print(f"  {t['date']}  {t['kind']:<16} ({t['n_hours_before']} local hours -> 24)")
    if anomalies:
        print(f"\n  >>> {len(anomalies)} day(s) with an irregular row count that are NOT "
              f"real DST transitions (left uncollapsed):")
        for a in anomalies:
            print(f"    {a['date']}  {a['n_hours']} hours  -- {a['reason']}")

    exog_collapsed.index.name = "datetime"

    # --- Fill remaining NaN gaps: capped diurnal-cycle rule, never ffill ---
    fill_cols = ["load_forecast_mw", "solar_forecast_mw", "wind_onshore_forecast_mw"]
    n_nan_before = exog_collapsed[fill_cols + ["wind_solar_total_forecast_mw"]].isna().sum().sum()
    print(f"\nNaN before capped imputation: {int(n_nan_before)}")

    log_records: list[dict] = []
    unfillable: list[dict] = []
    for col in fill_cols:
        filled, col_log, col_unfillable = diurnal_fill_capped(exog_collapsed[col], col)
        exog_collapsed[col] = filled
        log_records.extend(col_log)
        unfillable.extend(col_unfillable)

    # wind_solar_total_forecast_mw is derived, not independently imputed:
    # recompute it from its own (now possibly-filled) components wherever it
    # is NaN, so it can never disagree with them (see DATASET.md, the
    # merge_entsoe.py skipna=False fix). Still NaN if either component is.
    total_col = "wind_solar_total_forecast_mw"
    total_nan_mask = exog_collapsed[total_col].isna()
    recomputed = exog_collapsed.loc[total_nan_mask, "solar_forecast_mw"] + \
        exog_collapsed.loc[total_nan_mask, "wind_onshore_forecast_mw"]
    exog_collapsed.loc[total_nan_mask, total_col] = recomputed
    for t, v in recomputed.dropna().items():
        log_records.append({"datetime": t, "column": total_col,
                             "method": "recomputed_from_components", "value": float(v)})

    n_nan_after = exog_collapsed[fill_cols + [total_col]].isna().sum().sum()
    print(f"Filled by diurnal interpolation or component recomputation: {len(log_records)}")
    print(f"NaN remaining (gap exceeds {IMPUTE_CAP_HOURS}h cap, or no same-hour "
          f"neighbour on both sides): {int(n_nan_after)}")
    if unfillable:
        print(f"\n  >>> {len(unfillable)} hour(s) left as NaN, not filled by any method:")
        for u in unfillable[:25]:
            print(f"    {u['datetime']}  {u['column']:<32} -- {u['reason']}")
        if len(unfillable) > 25:
            print(f"    ... and {len(unfillable) - 25} more")

    IMPUTED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_df = pd.DataFrame(log_records, columns=["datetime", "column", "method", "value"])
    log_df = log_df.sort_values(["datetime", "column"]).reset_index(drop=True)
    log_df.to_csv(IMPUTED_LOG_PATH, index=False)
    print(f"\nWrote {len(log_df)} imputed values to {IMPUTED_LOG_PATH}")

    exog_local = exog_collapsed.reset_index()

    # --- Save the intermediate localized exogenous file ---
    EXOG_LOCAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    exog_local.to_csv(EXOG_LOCAL_OUT, index=False)
    print(f"\nSaved localized exogenous series to {EXOG_LOCAL_OUT} ({len(exog_local)} rows)")

    # --- Trim exogenous to match price range ---
    p_start, p_end = prices["datetime"].min(), prices["datetime"].max()
    exog_trimmed = exog_local[
        (exog_local["datetime"] >= p_start) & (exog_local["datetime"] <= p_end)
    ].reset_index(drop=True)
    print(f"\nExogenous trimmed to price range: {len(exog_trimmed)} rows")

    # --- Merge on the shared local datetime label ---
    merged = pd.merge(prices, exog_trimmed, on="datetime", how="left", indicator=True)

    print(f"\n{'='*70}\nMERGE RESULT\n{'='*70}")
    print(merged["_merge"].value_counts())

    unmatched = merged[merged["_merge"] != "both"]
    if len(unmatched) > 0:
        print(f"\n>>> {len(unmatched)} price rows have no matching exogenous data:")
        print(unmatched[["datetime", "price_es"]].head(20))
        print(f"\nDate range of unmatched rows: {unmatched['datetime'].min()} to {unmatched['datetime'].max()}")
    else:
        print("\nAll price rows matched with exogenous data.")

    merged = merged.drop(columns="_merge")

    print(f"\nFinal dataset shape: {merged.shape}")
    print(f"Columns: {list(merged.columns)}")
    print(f"NaN counts per column:\n{merged.isna().sum()}")

    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(MODEL_OUT, index=False)
    print(f"\nSaved to {MODEL_OUT}")

    print(f"\n{'='*70}")
    print("DST TRANSITION DAYS TOUCHED (for verification against sample years)")
    print(f"{'='*70}")
    print(f"Total: {len(transitions)}  (spring: {n_spring}, autumn: {n_autumn})")
    for t in transitions:
        print(f"  {t['date']}  {t['kind']}")


if __name__ == "__main__":
    main()
