"""
Merge ENTSO-E load forecast + wind/solar generation forecast for Spain
into a single exogenous variables file, aligned by DateTime(UTC).

Trims to 2015 onward. Very little pre-2015 data exists: for Spain both
series start 2014-12-11 and cover the same 21 days to year end (481 load
rows, 983 generation rows, no missing values in either), because the
ENTSO-E Transparency Platform began publishing in January 2015 under
Regulation (EU) 543/2013. Keeping that fragment would start the exogenous
series with three weeks of data and nothing before it. The two series are
NOT inconsistent with each other; an earlier version of this docstring and
of the project documentation said so, which was wrong.

load_generation_forecast() pivots raw rows (one per timestamp per
production type) into wide columns and sums wind+solar into a total. Both
steps use pandas reductions that default to skipna=True, which treats a
raw row with an empty forecast value as contributing 0, not NaN. This
previously fabricated thousands of false zero readings and silently
understated wind_solar_total_forecast_mw whenever one component was
missing -- see the project history for the investigation. Fixed here by
using aggfunc="mean" (equivalent to sum when at most one row exists per
cell, which is the normal case, but correctly returns NaN for an
all-missing cell) and skipna=False for the total.
"""
import pandas as pd
from pathlib import Path

BASE = Path("dataset/downloads/entsoe")
OUT_PATH = Path("dataset/working/exogenous_spain.csv")

LOAD_FOLDER = BASE / "DayAheadTotalLoadForecast_6.1.B_r3"
GEN_FOLDER = BASE / "GenerationForecastsForWindAndSolar_14.1.D_r3"

CUTOFF = pd.Timestamp("2015-01-01")


def require_source_files():
    """Fail early and legibly when the raw ENTSO-E files are not in place.

    Without this, an empty folder surfaces much later as pandas'
    "No objects to concatenate", which says nothing about what is missing
    or where to put it.
    """
    missing = []
    for folder, label in ((LOAD_FOLDER, "day-ahead total load forecast (Article 6.1.B)"),
                          (GEN_FOLDER, "wind and solar generation forecast (Article 14.1.D)")):
        n = len(list(folder.glob("*.csv"))) if folder.is_dir() else 0
        if n == 0:
            missing.append((folder, label))

    if not missing:
        return

    print("Cannot build the exogenous series: the raw ENTSO-E files are missing.\n")
    for folder, label in missing:
        state = "empty" if folder.is_dir() else "does not exist"
        print(f"  {folder}  ({state})")
        print(f"      expected: monthly .csv files of the {label}\n")
    print("These are not included in the repository (~3.3 GB in total).")
    print("dataset/downloads/README.md explains where to get them and what")
    print("the files must look like.\n")
    print("If you only want to use the dataset, it is already built:")
    print("  dataset/final/model_dataset.csv")
    print("  dataset/final/ES.csv")
    raise SystemExit(1)


def load_load_forecast():
    frames = []
    resolutions_seen = set()
    for f in sorted(LOAD_FOLDER.glob("*.csv")):
        df = pd.read_csv(f, sep="\t")
        df = df[df["AreaMapCode"] == "ES"]
        resolutions_seen.update(df["ResolutionCode"].unique())
        frames.append(df[["DateTime(UTC)", "TotalLoad[MW]"]])
    out = pd.concat(frames, ignore_index=True)
    out = out.rename(columns={"DateTime(UTC)": "datetime_utc", "TotalLoad[MW]": "load_forecast_mw"})
    print(f"Load forecast resolutions seen across all files: {resolutions_seen}")
    return out

def load_generation_forecast():
    frames = []
    resolutions_seen = set()
    for f in sorted(GEN_FOLDER.glob("*.csv")):
        df = pd.read_csv(f, sep="\t")
        df = df[df["AreaMapCode"] == "ES"]
        resolutions_seen.update(df["ResolutionCode"].unique())
        frames.append(df[["DateTime(UTC)", "ProductionType", "DayAheadGenerationForecast[MW]"]])
    long_df = pd.concat(frames, ignore_index=True)
    print(f"Generation forecast resolutions seen across all files: {resolutions_seen}")
    print(f"Production types found: {long_df['ProductionType'].unique()}")

    # aggfunc="mean" not "sum": there is normally at most one raw row per
    # (timestamp, ProductionType) cell, so mean and sum agree whenever a
    # value exists. They disagree when ENTSO-E publishes a row with an
    # empty forecast: pandas' sum(skipna=True) treats "sum of one NaN" as
    # 0.0, silently fabricating a zero, while mean() correctly returns NaN.
    # Confirmed via inspection of the raw ENTSO-E files (DATASET.md) that
    # aggfunc="sum" was doing exactly this for hundreds of raw rows.
    #
    # dropna=False: pivot_table's default (dropna=True) drops any row that
    # ends up entirely NaN across all columns. With aggfunc="sum" this never
    # triggered, because the sum-of-NaN=0 bug above meant a fully-missing
    # timestamp still had a numeric (fabricated) 0.0 in every column, so
    # nothing looked all-NaN. Now that mean() correctly returns NaN, a
    # timestamp with no raw data for solar OR wind would be silently dropped
    # from the index entirely (confirmed: 192 such timestamps, all in
    # 2026-06, outside the modelling window but real) unless dropna=False
    # keeps the row so it flows through as an explicit NaN.
    wide = long_df.pivot_table(
        index="DateTime(UTC)",
        columns="ProductionType",
        values="DayAheadGenerationForecast[MW]",
        aggfunc="mean",
        dropna=False,
    ).reset_index()
    wide = wide.rename(columns={"DateTime(UTC)": "datetime_utc"})
    wide.columns = [c.lower().replace(" ", "_") + "_forecast_mw" if c != "datetime_utc" else c for c in wide.columns]

    wind_solar_cols = [c for c in wide.columns if c != "datetime_utc"]
    # skipna=False: if any component is missing, the total is unknown, not
    # "the sum of whatever wasn't missing". The default skipna=True silently
    # dropped a missing wind value and reported total == solar alone.
    wide["wind_solar_total_forecast_mw"] = wide[wind_solar_cols].sum(axis=1, skipna=False)
    return wide

require_source_files()

print("Loading day-ahead load forecast...")
load_df = load_load_forecast()
print(f"  {len(load_df)} rows")

print("\nLoading wind/solar generation forecast...")
gen_df = load_generation_forecast()
print(f"  {len(gen_df)} rows")
gen_cols_report = [c for c in gen_df.columns if c != "datetime_utc"]
print(f"  NaN counts (genuinely missing, not fabricated zeros):\n"
      f"{gen_df[gen_cols_report].isna().sum().to_string()}")

print("\nMerging on datetime_utc...")
merged = pd.merge(load_df, gen_df, on="datetime_utc", how="outer", indicator=True)

mismatch = merged[merged["_merge"] != "both"]
if len(mismatch) > 0:
    print(f"  >>> {len(mismatch)} timestamps did not match between load and generation files:")
    print(mismatch["_merge"].value_counts())
else:
    print("  All timestamps matched cleanly.")

merged = merged.drop(columns="_merge")
merged["datetime_utc"] = pd.to_datetime(merged["datetime_utc"])
merged = merged.sort_values("datetime_utc").reset_index(drop=True)

# --- trim pre-2015 data ---
before_trim = len(merged)
merged = merged[merged["datetime_utc"] >= CUTOFF].reset_index(drop=True)
print(f"\nTrimmed {before_trim - len(merged)} rows before {CUTOFF.date()}")

dupes = merged["datetime_utc"].duplicated().sum()
if dupes:
    print(f"  >>> {dupes} duplicate timestamps found -- investigate before proceeding")

print(f"\nFinal shape: {merged.shape}")
print(f"Range: {merged['datetime_utc'].min()} to {merged['datetime_utc'].max()}")
print(f"Columns: {list(merged.columns)}")

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
merged.to_csv(OUT_PATH, index=False)
print(f"\nSaved to {OUT_PATH}")