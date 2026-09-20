"""
Step 1 of the data pipeline: download daily OMIE marginal-price files
(marginalpdbc_YYYYMMDD.1) from the OMIE website into dataset/downloads/omie/,
covering 2002-01-01 to 2025-09-30. Skips files already present.
"""
import requests
import time
import os
from datetime import date, timedelta

# --- Config ---
START_DATE = date(2002, 1, 1)
END_DATE = date(2025, 9, 30)
RAW_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'dataset', 'downloads', 'omie')
MAX_RETRIES = 3
SLEEP_BETWEEN_REQUESTS = 0.5  # seconds

def get_omie_url(d):
    filename = f"marginalpdbc_{d.strftime('%Y%m%d')}.1"
    return f"https://www.omie.es/sites/default/files/dados/AGNO_{d.year}/MES_{d.strftime('%m')}/TXT/INT_PBC_EV_H_1_{d.strftime('%d_%m_%Y')}_{d.strftime('%d_%m_%Y')}.TXT", filename

def download_file(d):
    url, filename = get_omie_url(d)
    filepath = os.path.join(RAW_DIR, filename)

    if os.path.exists(filepath):
        return 'skipped'

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                with open(filepath, 'wb') as f:
                    f.write(r.content)
                return 'ok'
            else:
                return f'error_{r.status_code}'
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2)
            else:
                return f'failed: {e}'

def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    current = START_DATE
    total = (END_DATE - START_DATE).days + 1
    done = 0

    while current <= END_DATE:
        status = download_file(current)
        done += 1
        if done % 100 == 0 or status.startswith('failed'):
            print(f"[{done}/{total}] {current} — {status}")
        current += timedelta(days=1)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    print("Download complete.")

if __name__ == '__main__':
    main()