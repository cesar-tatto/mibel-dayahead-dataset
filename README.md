# MIBEL day-ahead dataset

An hourly dataset of Spanish day-ahead electricity prices and the day-ahead
forecasts that drive them, covering **2002 to 2025**, together with the scripts
that build it from the original public archives.

The prices come from OMIE, the operator of the Iberian day-ahead market. The
load, solar and onshore wind forecasts come from the ENTSO-E Transparency
Platform. Everything here is rebuilt from those two sources by a fixed sequence
of five scripts.

**The built dataset is already in this repository.** You do not need to download
anything or run anything to use it:

- [`dataset/final/model_dataset.csv`](dataset/final/model_dataset.csv) — the full
  record, 2002–2025, 203,760 hourly rows, gaps preserved as empty cells.
- [`dataset/final/ES.csv`](dataset/final/ES.csv) — a continuous modelling
  extract, 2015–2025, 94,080 hourly rows, no missing values.

[`DATASET.md`](DATASET.md) documents every column, the time convention, how gaps
are handled, and what to watch out for before modelling. **Read it before using
the data** sincethe time handling and the imputation rules are not obvious from the
files alone.

## Quickstart

```bash
git clone <this repository>
cd mibel-dayahead-dataset
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

```python
import pandas as pd

df = pd.read_csv("dataset/final/ES.csv", parse_dates=["datetime"])
print(df.shape)     # (94080, 5)
print(df.head())
```

Four packages, no build step. The install is quick because nothing here needs a
machine-learning stack.

## Repository layout

```
dataset/
  scripts/     the five pipeline stages, run in order
  downloads/   drop point for the raw archives — ships empty, see below
  working/     intermediate outputs of stages 2-4
  final/       the two datasets you probably want
DATASET.md     column reference, conventions, caveats
```

## How the dataset is built

Five stages, run in order. Each reads what the previous one wrote.

| # | Script | Reads | Writes |
| --- | --- | --- | --- |
| 1 | `download_omie.py` | omie.es | `downloads/omie/` (8,490 daily files) |
| 2 | `parse_omie.py` | `downloads/omie/` | `working/prices_spain.csv` |
| 3 | `merge_entsoe.py` | `downloads/entsoe/` | `working/exogenous_spain.csv` |
| 4 | `build_model_dataset.py` | the two above | `working/exogenous_spain_local.csv`, `final/model_dataset.csv` |
| 5 | `export_es_dataset.py` | `final/model_dataset.csv` | `final/ES.csv` |

Stage 4 does the work that matters most: OMIE publishes in Spanish local time
and ENTSO-E in UTC, so it converts one onto the other, normalises the
daylight-saving days to 24 hours, fills short gaps and joins the two series.
`DATASET.md` explains the rules it applies.

Stages 4 and 5 also write imputation logs to `dataset/audit/`, recording every
value they filled. That directory is created on demand; it is not in the
repository because it is an output, not an input.

> **Run every script from the repository root**, not from inside
> `dataset/scripts/`. Most of them resolve paths against the current working
> directory, and stage 5 imports stage 4 as a module.

```bash
python dataset/scripts/build_model_dataset.py
python dataset/scripts/export_es_dataset.py
```

Those two stages reproduce both final CSVs from the intermediates already in the
repository, which is the quickest way to confirm the pipeline works on your
machine.

## Rebuilding from the raw sources

Stages 1 to 3 need the original archives, about 3.4 GB in total. They are not in
this repository, but the folders they belong in are:

```
dataset/downloads/
├── omie/                                            ← stage 1 fills this
└── entsoe/
    ├── DayAheadTotalLoadForecast_6.1.B_r3/          ← you fill these
    └── GenerationForecastsForWindAndSolar_14.1.D_r3/
```

Drop the files in and the scripts will find them. **The folder names matter** —
stage 3 looks for those two exactly.

The OMIE half is scripted and needs no account:

```bash
python dataset/scripts/download_omie.py
```

The ENTSO-E half (about 3.3 GB) has to be fetched by hand from the
[Transparency Platform](https://transparency.entsoe.eu/), using its **bulk
download** area rather than the web CSV export — the two produce different file
formats and these scripts read the bulk one.

**[`dataset/downloads/README.md`](dataset/downloads/README.md) has the full
instructions**: which two datasets to download, what the files should look like,
and how to tell whether you got the right format. Stage 3 checks before it
starts and tells you what is missing if anything is.

With the raw files in place, run all five stages from the repository root:

```bash
python dataset/scripts/download_omie.py        # stage 1
python dataset/scripts/parse_omie.py           # stage 2
python dataset/scripts/merge_entsoe.py         # stage 3
python dataset/scripts/build_model_dataset.py  # stage 4
python dataset/scripts/export_es_dataset.py    # stage 5
```

The output should match the CSVs already here, byte for byte.

## Requirements

Python 3.10 or later, and four packages pinned in `requirements.txt`: `pandas`,
`numpy`, `requests`, `pytz`.
