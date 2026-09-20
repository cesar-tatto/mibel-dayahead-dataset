"""
Step 2 of the data pipeline: parse raw OMIE marginal-price files in
dataset/downloads/omie/ (old cent/kWh format and new EUR/MWh format), normalize
Spanish decimal notation, apply DST handling to keep every day at 24
hourly values, and write the combined hourly series to
dataset/working/prices_spain.csv.

Two bidding-zone prices are extracted from each file:

  price_es  the Spanish system marginal price, present in every file from
            2002-01-01 onward.
  price_pt  the Portuguese system marginal price, present only from
            2007-07-01 onward (MIBEL's operational launch). Files before
            that date carry a single, undifferentiated price row and
            price_pt is left empty for them.

Both columns get identical treatment: the same cent/kWh -> EUR/MWh
conversion for pre-2010 files and the same DST normalisation. price_pt is
carried as a permanent column so that market-splitting between the two
zones can be quantified without re-parsing the raw archive; the models
themselves consume only price_es.
"""
import os
import pandas as pd

RAW_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'dataset', 'downloads', 'omie')
OUT_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'dataset', 'working', 'prices_spain.csv')

def parse_value(s):
    # handles Spanish decimal format: "41,88" or "4.520" or "  4,520"
    s = s.strip().replace('.', '').replace(',', '.')
    return float(s)

def parse_price_row(line, scale):
    """Split a `label;v1;v2;...` price row into floats, scaled to EUR/MWh."""
    parts = line.split(';')[1:]
    parts = [p for p in parts if p.strip()]
    try:
        return [parse_value(p) * scale for p in parts]
    except:
        return None

def normalize_dst(prices):
    """Force one calendar day's price list to exactly 24 hourly values.

    Spain and Portugal share the same DST transition instant (both are on
    Western/Central European time with a common transition calendar), so the
    identical rule applies to either zone's row:
      Spring-forward: 23 values, the 02:00 hour is missing -> interpolate it in.
      Autumn fall-back: 25 values, the 02:00 hour is repeated -> average the pair.
    This matches the convention used by the epftoolbox benchmark datasets.
    """
    n = len(prices)
    if n == 23:
        interp = (prices[1] + prices[2]) / 2.0
        return prices[:2] + [interp] + prices[2:]
    if n == 25:
        merged = (prices[2] + prices[3]) / 2.0
        return prices[:2] + [merged] + prices[4:]
    return prices

def parse_file(filepath, fname):
    # extract date from filename: marginalpdbc_YYYYMMDD.1
    date_str = fname.split('_')[1].split('.')[0]
    year, month, day = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])

    with open(filepath, 'r', encoding='latin-1') as f:
        lines = f.readlines()

    if not lines:
        return []

    first_line = lines[0]
    is_old_format = 'OMEL' in first_line or 'cent/kWh' in first_line.lower() or 'Cent/kWh' in first_line

    # old files quote cent/kWh, new files quote EUR/MWh directly.
    # 1 cent/kWh = 10 EUR/MWh exactly (0.01 EUR / 0.001 MWh).
    scale = 10 if is_old_format else 1

    prices = None
    prices_pt = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Spain. Three label variants across the archive:
        #   'Precio marginal (Cent/kWh)'                        pre-MIBEL, 1,823 files
        #   'Precio marginal en el sistema español (Cent/kWh)'  old format,  915 files
        #   'Precio marginal en el sistema español (EUR/MWh)'   new format, 5,752 files
        if prices is None:
            if is_old_format and ('Precio marginal (Cent/kWh)' in line
                                  or 'sistema español (Cent/kWh)' in line):
                prices = parse_price_row(line, scale)
                continue
            if not is_old_format and 'sistema español (EUR/MWh)' in line:
                prices = parse_price_row(line, scale)
                continue

        # Portugal. Only present from 2007-07-01 (MIBEL launch), 6,667 files:
        #   'Precio marginal en el sistema portugués (Cent/kWh)'  old format,   915 files
        #   'Precio marginal en el sistema portugués (EUR/MWh)'   new format, 5,752 files
        if prices_pt is None:
            if is_old_format and 'sistema portugués (Cent/kWh)' in line:
                prices_pt = parse_price_row(line, scale)
                continue
            if not is_old_format and 'sistema portugués (EUR/MWh)' in line:
                prices_pt = parse_price_row(line, scale)
                continue

        if prices is not None and prices_pt is not None:
            break

    if not prices:
        return []

    # --- DST handling: force every day to 24 hourly values ---
    prices = normalize_dst(prices)
    if prices_pt:
        prices_pt = normalize_dst(prices_pt)

    rows = []
    for hour_idx, price in enumerate(prices[:24]):  # cap at 24 hours
        rows.append({
            'datetime': pd.Timestamp(year=year, month=month, day=day, hour=hour_idx),
            'price_es': price,
            'price_pt': prices_pt[hour_idx] if prices_pt and hour_idx < len(prices_pt) else None,
        })
    return rows

def main():
    all_rows = []
    files = sorted(os.listdir(RAW_DIR))
    failed = []

    for i, fname in enumerate(files):
        if not fname.startswith('marginalpdbc'):
            continue
        filepath = os.path.join(RAW_DIR, fname)
        rows = parse_file(filepath, fname)
        if rows:
            all_rows.extend(rows)
        else:
            failed.append(fname)
        if (i + 1) % 500 == 0:
            print(f"Parsed {i + 1}/{len(files)} files")

    df = pd.DataFrame(all_rows)
    df = df.sort_values('datetime').reset_index(drop=True)
    df.to_csv(OUT_FILE, index=False)

    print(f"\nDone. {len(df)} rows saved.")
    print(f"Failed to parse: {len(failed)} files")
    if failed:
        print("First 10 failed:", failed[:10])
    print("\nFirst rows:")
    print(df.head())
    print("\nLast rows:")
    print(df.tail())
    print("\nPrice stats:")
    print(df[['price_es', 'price_pt']].describe())
    n_pt = int(df['price_pt'].notna().sum())
    print(f"\nprice_pt present in {n_pt} of {len(df)} rows "
          f"({df.loc[df['price_pt'].notna(), 'datetime'].min()} onward)")

if __name__ == '__main__':
    main()
