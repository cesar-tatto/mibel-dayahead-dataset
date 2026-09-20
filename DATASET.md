# Dataset reference

Two CSVs are produced by the pipeline. `model_dataset.csv` is the full record of
what the sources published, gaps included. `ES.csv` is a continuous extract cut
from it for modelling, with no missing values.

Both are UTF-8, comma-separated, with a header row.

---

## `dataset/final/model_dataset.csv`

The master record. **203,760 rows**, covering **2002-01-01 00:00 to 2025-09-30
23:00**, one row per hour.

| Column | Unit | Description |
| --- | --- | --- |
| `datetime` | — | Hour beginning, Spanish local clock time, timezone-naive (see *Time convention*) |
| `price_es` | EUR/MWh | Day-ahead marginal price, Spanish bidding zone |
| `price_pt` | EUR/MWh | Day-ahead marginal price, Portuguese bidding zone |
| `load_forecast_mw` | MW | Day-ahead total load forecast, Spain |
| `solar_forecast_mw` | MW | Day-ahead solar generation forecast, Spain |
| `wind_onshore_forecast_mw` | MW | Day-ahead onshore wind generation forecast, Spain |
| `wind_solar_total_forecast_mw` | MW | Sum of the solar and onshore wind forecasts |

**Empty cells are real and deliberate.** They mark hours the source never
published, and are left empty rather than filled:

| Column | Empty rows | Why |
| --- | --- | --- |
| `price_es` | 0 | — |
| `price_pt` | 43,752 | Portugal has a separate zonal price only from the MIBEL spot launch in July 2007 |
| `load_forecast_mw` | 109,537 | The ENTSO-E record begins 2015-01-01 |
| `solar_forecast_mw` | 109,657 | As above, plus one five-day gap |
| `wind_onshore_forecast_mw` | 109,609 | As above, plus three single-day gaps |
| `wind_solar_total_forecast_mw` | 109,729 | Empty wherever either component is empty |

Note that **184 calendar days carry no row at all**, because the OMIE archive
has no file for them: 153 days between August and December 2002, and the whole
of October 2006. The series is not a gapless 2002–2025 grid. Every day that is
present has exactly 24 rows.

## `dataset/final/ES.csv`

The modelling extract. **94,080 rows**, covering **2015-01-07 00:00 to
2025-09-30 23:00** — 3,920 days, exactly 560 weeks — with **no missing values**
in any column.

| Column | Unit |
| --- | --- |
| `datetime` | Hour beginning, Spanish local clock time, naive |
| `price_es` | EUR/MWh |
| `load_forecast_mw` | MW |
| `solar_forecast_mw` | MW |
| `wind_onshore_forecast_mw` | MW |

It differs from the master record in four ways:

1. It starts on a Sunday, 2015-01-07, so the span is a whole number of weeks.
   Day-ahead prices carry a strong weekly cycle, and whole weeks keep each
   weekday in the same relative position throughout.
2. It drops `price_pt` and `wind_solar_total_forecast_mw`.
3. The four multi-day exogenous gaps are **bridged**, affecting 192 hours —
   120 in the solar series, 72 in the wind series. See *Gaps and imputation*.
4. Column order is fixed: datetime first, target second, then the exogenous
   series. This is deliberate — some forecasting libraries assign column roles
   positionally rather than by name. The
   [epftoolbox](https://github.com/jeslago/epftoolbox) benchmark library, which
   several comments in the build scripts refer to, is one of them: its
   `read_data()` renames whatever it finds to `Price`, `Exogenous 1`,
   `Exogenous 2` and so on, in order, and requires a gapless hourly grid. That
   requirement is the reason the gaps are bridged in this file at all.

---

## Time convention

Both files use **Spanish local clock time** (Europe/Madrid: UTC+1 in winter,
UTC+2 in summer), written as a naive timestamp with no offset or zone suffix.

This matters because the two sources disagree. OMIE publishes in local time;
ENTSO-E publishes in UTC. The ENTSO-E series is converted to Europe/Madrid, then
stripped of its zone, so both sit on one clock.

Local time was chosen over UTC because a day-ahead auction is defined over a
local calendar day — gate closure, the auction and delivery are all specified in
Spanish local time.

**Daylight-saving days are normalised to 24 hours.** The clock change otherwise
produces a 23-hour day in spring and a 25-hour day in autumn. On the short day
the missing hour is interpolated; on the long day the duplicated hour is
averaged. Every date in both files therefore has exactly 24 rows, which is what
makes a fixed daily 24-vector possible.

The conversion is done **before** the daylight-saving normalisation, because it
is the conversion that produces the 23- and 25-hour days in the first place.

## Resolution

Both files are hourly throughout.

ENTSO-E moved Spanish publication from hourly to 15-minute resolution on
**2022-05-23**. From that date each hour arrives as four quarter-hour values,
which are averaged to a single hourly value. Aggregation runs from 15 minutes up
to the hour, never the reverse, so the series stays hourly across the whole span
and matches the price series.

## Gaps and imputation

Two different rules apply, and which file you are reading determines which one
you get.

**In `model_dataset.csv`**, short gaps of up to three consecutive hours are
filled by interpolating between the same hour of day on the nearest available
days either side — following the daily cycle rather than cutting across it.
Gaps longer than three hours are **not** filled and remain empty.

**In `ES.csv`**, four multi-day gaps that ENTSO-E never published are bridged as
well, using the same method without the three-hour cap:

| Interval | Series | Hours |
| --- | --- | --- |
| 2020-01-05 | onshore wind | 24 |
| 2024-06-02 | onshore wind | 24 |
| 2024-12-07 to 2024-12-11 | solar | 120 |
| 2025-03-31 | onshore wind | 24 |

192 hours in total. Over an interval of several days the method reduces to a
straight line between the boundary days, applied separately for each hour of the
day — it preserves the daily shape but replaces day-to-day variation with a
smooth ramp. **These values are reconstructed, not observed.** If that matters
for your use, the intervals above let you exclude them.

They are bridged only because many forecasting feature builders retrieve lagged
values by timestamp and fail outright when one is absent. The master record
keeps them empty so the distinction between observed and reconstructed is never
lost.

Every filled value is logged. Running the pipeline writes
`dataset/audit/imputed_hours.csv` and `dataset/audit/imputed_hours_ES_export.csv`
listing each one by timestamp, column, method and value.

## Why three exogenous series, not a combined total

Solar and onshore wind are kept as separate columns rather than summed. The two
technologies deliver zero-marginal-cost generation at different times of day —
solar is a single midday peak, while onshore wind dips at midday and peaks
overnight — so an identical volume of renewable output acts on the price
differently depending on when it arrives. A model estimating hour-specific
relationships can weight them separately only if they arrive separately.

`wind_solar_total_forecast_mw` is provided in the master record for convenience
and is simply the sum of the two.

## Known characteristics

Worth knowing before modelling:

- **Zero and negative prices.** The series is frequently zero and increasingly
  often negative from 2024 onward. Error measures that divide by the realised
  price break down on these hours.
- **A dispersion break.** The annual standard deviation of `price_es` rose about
  sixfold in 2021 and has not returned to its earlier level. A model calibrated
  on the earlier part of the record faces a target that changes character
  partway through.
- **A bid-limit regime.** Until May 2021 the Iberian market applied bid limits of
  0 and 180.3 EUR/MWh, so prices in the earlier part of the record could neither
  go negative nor exceed that ceiling. Later years are unconstrained.
- **A market time unit change.** The day-ahead market moved to a 15-minute market
  time unit for delivery from 2025-10-01. This dataset ends 2025-09-30 and is
  hourly throughout, so it is unaffected — but anything extending it past that
  date is not.

## Sources and terms

- **Prices** — OMIE, daily `marginalpdbc_YYYYMMDD.1` files from the public
  archive. Files before June 2010 are denominated in cent/kWh and are converted
  to EUR/MWh during parsing.
- **Load forecast** — ENTSO-E Transparency Platform, Article 6.1.B, day-ahead
  total load forecast. Distinct from Article 6.1.A, which is realised load.
- **Wind and solar forecasts** — ENTSO-E Transparency Platform, Article 14.1.D,
  day-ahead wind and solar generation forecast. Only the day-ahead forecast
  column is used.

All three are forecasts published ahead of delivery, not realised outturns.

ENTSO-E reports Spanish solar under a single production type, so
`solar_forecast_mw` combines photovoltaic output with Spain's concentrated solar
plants, which keep generating after sunset on stored heat. The series therefore
does not fall to zero at night and the two technologies cannot be separated.

The data is redistributed here under the terms of its original publishers. Check
OMIE's and ENTSO-E's own conditions before republishing it further.
