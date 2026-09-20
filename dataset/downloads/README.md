# Raw source files go here

This folder is the drop point for the original archives. It ships empty —
the files total about **3.4 GB**, too much for a repository.

You only need this if you want to rebuild the dataset from scratch. The built
CSVs are already in `dataset/final/`, so for most uses you can ignore this
folder entirely.

```
dataset/downloads/
├── omie/                                          ← stage 1 fills this for you
│   └── marginalpdbc_YYYYMMDD.1                       8,490 files, ~33 MB
└── entsoe/                                        ← you fill these by hand
    ├── DayAheadTotalLoadForecast_6.1.B_r3/           143 files, ~1.0 GB
    │   └── YYYY_MM_DayAheadTotalLoadForecast_6.1.B_r3.csv
    └── GenerationForecastsForWindAndSolar_14.1.D_r3/ 140 files, ~2.3 GB
        └── YYYY_MM_GenerationForecastsForWindAndSolar_14.1.D_r3.csv
```

**Do not rename the folders.** Stage 3 looks for those two names exactly.

---

## OMIE — scripted, no account needed

```bash
python dataset/scripts/download_omie.py
```

Fetches one file per day from 2002-01-01 to 2025-09-30 into `omie/`, skipping
anything already there. It takes a while — roughly 8,500 requests with a
deliberate pause between them, so expect a couple of hours. Safe to interrupt
and re-run; it resumes.

184 days will not download because OMIE's archive genuinely has no file for
them (153 days in 2002, all of October 2006). That is expected, not a failure.

## ENTSO-E — manual, free account needed

There is no download script for this half. Get the files from the
[ENTSO-E Transparency Platform](https://transparency.entsoe.eu/).

**Use the bulk download area, not the web CSV export.** This matters: the two
give different file formats, and the scripts here read the bulk format. Look for
SFTP / "Data extraction" access rather than the "Export CSV" button on a chart
page. Registration is free.

Download two datasets, monthly files, **2015-01 onward**:

| Dataset | Folder it goes in |
| --- | --- |
| Day-ahead Total Load Forecast (Article 6.1.B) | `DayAheadTotalLoadForecast_6.1.B_r3/` |
| Generation Forecasts for Wind and Solar (Article 14.1.D) | `GenerationForecastsForWindAndSolar_14.1.D_r3/` |

Take **all areas** — the files are pan-European and stage 3 filters them to
Spain itself. There is nothing to configure per country.

Earlier months exist but are not useful: both Spanish series begin 2014-12-11
and cover only three weeks before the platform started publishing properly in
January 2015 under Regulation (EU) 543/2013. Stage 3 discards anything before
2015-01-01.

### Checking you got the right thing

The files are **tab-separated despite the `.csv` extension**. Open one and
confirm the header matches:

*Load* — must contain `DateTime(UTC)`, `AreaMapCode`, `ResolutionCode`,
`TotalLoad[MW]`.

*Generation* — must contain `DateTime(UTC)`, `AreaMapCode`, `ResolutionCode`,
`ProductionType`, `DayAheadGenerationForecast[MW]`. For Spain the
`ProductionType` values are `Solar` and `Wind Onshore`.

If your files are comma-separated, or the columns are named differently, you
have the web export rather than the bulk download.

---

## Then build

From the repository root:

```bash
python dataset/scripts/parse_omie.py          # stage 2
python dataset/scripts/merge_entsoe.py        # stage 3
python dataset/scripts/build_model_dataset.py # stage 4
python dataset/scripts/export_es_dataset.py   # stage 5
```

The result should match the CSVs already in `dataset/working/` and
`dataset/final/` byte for byte. If it does not, something differs in your
source files — compare against the row counts in `DATASET.md`.
