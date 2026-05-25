"""
auto_create_config.py — Auto-generate Vehicle_infor-style config files (Excel)
by filtering and mapping data from the master NWS and DTC databases.

Two-step workflow (runs once per unique YMME)
---------------------------------------------
Step 1 – Vehicle Selection and YMME Configuration
  1a. Scan NWS Ymme sheet; keep rows where InnovaGroup is GROUP_ABS or GROUP_SRS.
  1b. Cross-reference with NWS Profile sheet; keep only PROTOCOL_CAN / PROTOCOL_CAN_UDS.
  1c. Enumerate ALL unique YMME combinations in the filtered dataset — every YMME
      that has at least one ABS or SRS CAN profile gets its own config file.
  1d. Write VIN_YMME sheet with the YMME data + default placeholder VIN.

Step 2 – DTC Mapping and Configuration
  2a. For each YMME, use its MsgID/ECUID values to filter the DTC sheet
      (DTC column "Option 3" is the MessageId key).
  2b. Map each (System, MsgID/ECUID) pair to its SAE DTC list.
  2c. Write the NWS sheet — every system always gets at least one row;
      if no DTC is found in the database, write a placeholder row with DTC="0".

Usage
-----
    python auto_create_config.py              # writes to config/auto_configs/
    python auto_create_config.py out/dir      # custom output directory
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import openpyxl
import pandas as pd
import polars as pl

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

NWS_DIR            = Path('data/NWS')
DTC_DIR            = Path('data/DTC')
DEFAULT_OUTPUT_DIR = Path('config/auto_configs')

DEFAULT_VIN    = '11111111111111111'   # placeholder written into every VIN_YMME row
DEFAULT_STATUS = '00'                  # placeholder written into every NWS Status cell

TARGET_GROUPS    = {'GROUP_ABS', 'GROUP_SRS'}
TARGET_PROTOCOLS = {'PROTOCOL_CAN_UDS', 'PROTOCOL_CAN'}

YMME_COLS = ['Year', 'Manufacturer', 'Make', 'Model', 'Engine']

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_xlsx(folder: Path) -> Path:
    """Return the first .xlsx file found recursively in *folder*."""
    files = sorted(folder.rglob('*.xlsx'))
    if not files:
        raise FileNotFoundError(f'No .xlsx files found in {folder}')
    if len(files) > 1:
        logger.warning('Multiple .xlsx files in %s — using %s', folder, files[0].name)
    return files[0]


def _pd_to_pl(df: pd.DataFrame) -> pl.DataFrame:
    """Convert a pandas DataFrame to polars without requiring pyarrow.

    Fills NaN with '' and builds polars Series from plain Python lists —
    compatible even when pyarrow is not installed.
    """
    df = df.fillna('').astype(str)
    return pl.from_dict({col: df[col].tolist() for col in df.columns})


def _safe_name(text: str) -> str:
    """Strip characters that are invalid in Windows file/folder names."""
    return re.sub(r'[\\/:*?"<>|]', '_', text).strip()


def _ymme_tag(ymme: dict[str, str]) -> str:
    """Build a filesystem-safe filename tag from a YMME dict."""
    return _safe_name(
        f"{ymme['Year']}_{ymme['Manufacturer']}_{ymme['Make']}_"
        f"{ymme['Model']}_{ymme['Engine']}"
    )


# ---------------------------------------------------------------------------
# Step 1a – Load NWS Ymme
# ---------------------------------------------------------------------------

def load_nws_ymme(nws_file: Path) -> pl.DataFrame:
    """
    Load the NWS Ymme sheet including the InnovaGroup column.

    Multi-row header layout: row 1 is the real column header; rows 0, 2, 3
    are metadata and must be skipped.
    """
    wanted_cols = YMME_COLS + ['System', 'MsgID/ECUID', 'InnovaGroup']
    df = pd.read_excel(
        nws_file,
        sheet_name='Ymme',
        skiprows=[0, 2, 3],
        usecols=wanted_cols,
    )
    logger.info('Loaded NWS Ymme: %d rows', len(df))
    return _pd_to_pl(df)


# ---------------------------------------------------------------------------
# Step 1b – Load NWS Profile
# ---------------------------------------------------------------------------

def load_nws_profile(nws_file: Path) -> pl.DataFrame:
    """Load the NWS Profile sheet (only MsgID/ECUID and Protocol columns)."""
    df = pd.read_excel(
        nws_file,
        sheet_name='Profile',
        skiprows=[0, 2, 3],
        usecols=['MsgID/ECUID', 'Protocol'],
    )
    df = df.drop_duplicates(subset='MsgID/ECUID')
    logger.info('Loaded NWS Profile: %d unique profiles', len(df))
    return _pd_to_pl(df)


# ---------------------------------------------------------------------------
# Step 1b – Filter by group and protocol
# ---------------------------------------------------------------------------

def filter_abs_srs_can(ymme: pl.DataFrame, profile: pl.DataFrame) -> pl.DataFrame:
    """
    Keep YMME rows that belong to GROUP_ABS or GROUP_SRS *and* whose
    MsgID/ECUID uses a CAN-based protocol.

    Returns columns: Year, Manufacturer, Make, Model, Engine,
                     System, MsgID/ECUID, InnovaGroup, Protocol
    """
    abs_srs = ymme.filter(pl.col('InnovaGroup').is_in(list(TARGET_GROUPS)))
    logger.info('After GROUP_ABS/GROUP_SRS filter: %d rows', len(abs_srs))

    can_profiles = profile.filter(pl.col('Protocol').is_in(list(TARGET_PROTOCOLS)))
    filtered = abs_srs.join(can_profiles, on='MsgID/ECUID', how='inner')
    logger.info('After CAN protocol filter: %d rows, %d unique YMMEs',
                len(filtered), filtered.select(YMME_COLS).n_unique())
    return filtered


# ---------------------------------------------------------------------------
# Step 1c – Get systems for one YMME
# ---------------------------------------------------------------------------

def get_systems_for_ymme(filtered: pl.DataFrame, ymme: dict[str, str]) -> pl.DataFrame:
    """
    Return unique (System, MsgID/ECUID, InnovaGroup) rows for a single YMME.
    """
    mask = (
        (pl.col('Year')         == ymme['Year'])
        & (pl.col('Manufacturer') == ymme['Manufacturer'])
        & (pl.col('Make')         == ymme['Make'])
        & (pl.col('Model')        == ymme['Model'])
        & (pl.col('Engine')       == ymme['Engine'])
    )
    return (
        filtered.filter(mask)
        .select(['System', 'MsgID/ECUID', 'InnovaGroup'])
        .unique()
    )


# ---------------------------------------------------------------------------
# Step 1d – Build VIN_YMME row
# ---------------------------------------------------------------------------

def build_vin_ymme(ymme: dict[str, str]) -> dict[str, str]:
    """Return a VIN_YMME row dict with the default placeholder VIN."""
    return {
        'VIN':          DEFAULT_VIN,
        'Year':         ymme['Year'],
        'Manufacturer': ymme['Manufacturer'],
        'Make':         ymme['Make'],
        'Model':        ymme['Model'],
        'Engine':       ymme['Engine'],
    }


# ---------------------------------------------------------------------------
# Step 2a – Load DTC sheet
# ---------------------------------------------------------------------------

def load_dtc(dtc_file: Path) -> pl.DataFrame:
    """
    Load the DTC sheet.  Column "Option 3" holds the MessageId (MsgID/ECUID
    link key); "SAE DTC" holds the DTC code.

    Multi-row header: rows 0, 2, 3 are metadata; row 1 is the real header.
    """
    df = pd.read_excel(
        dtc_file,
        sheet_name='DTC',
        skiprows=[0, 2, 3],
        usecols=['Option 3', 'SAE DTC', 'Innova Group'],
    )
    logger.info('Loaded DTC sheet: %d rows', len(df))
    return _pd_to_pl(df)


def prepare_dtc(dtc_raw: pl.DataFrame) -> pl.DataFrame:
    """
    Rename columns and drop rows with empty/nan msgid or DTC code.
    Called once before the per-YMME loop so we don't repeat the work.
    """
    return (
        dtc_raw
        .rename({'Option 3': 'MsgID/ECUID', 'SAE DTC': 'DTC'})
        .filter(
            (pl.col('MsgID/ECUID') != '') & (pl.col('MsgID/ECUID') != 'nan') &
            (pl.col('DTC')         != '') & (pl.col('DTC')          != 'nan')
        )
        .select(['MsgID/ECUID', 'DTC'])
    )


# ---------------------------------------------------------------------------
# Step 2b/c – Map one YMME's systems to NWS rows
# ---------------------------------------------------------------------------

def map_dtc_to_nws(systems: pl.DataFrame, dtc_clean: pl.DataFrame) -> pl.DataFrame:
    """
    Build NWS config rows for a single YMME.

    Every system always produces at least one row:
      - Systems with DTC matches -> one row per DTC code.
      - Systems with NO DTC match -> one placeholder row with DTC = "0".

    Parameters
    ----------
    systems:
        DataFrame with columns System, MsgID/ECUID, InnovaGroup (one row per profile).
    dtc_clean:
        Pre-cleaned DTC table (from prepare_dtc) with columns MsgID/ECUID, DTC.
    """
    # Pre-filter DTC to only the msgids that appear in this YMME's systems —
    # avoids scanning the full DTC table once per system.
    ymme_msgids = systems['MsgID/ECUID'].to_list()
    dtc_subset  = dtc_clean.filter(pl.col('MsgID/ECUID').is_in(ymme_msgids))

    rows: list[dict[str, str]] = []

    for sys_row in systems.iter_rows(named=True):
        msgid  = sys_row['MsgID/ECUID']
        system = sys_row['System']

        dtcs = dtc_subset.filter(pl.col('MsgID/ECUID') == msgid)['DTC'].to_list()

        if not dtcs:
            # No entry in DTC database — emit placeholder so the NWS sheet is complete
            rows.append({'System': system, 'Profile': msgid, 'DTC': '0', 'Status': DEFAULT_STATUS})
        else:
            for code in dtcs:
                rows.append({'System': system, 'Profile': msgid, 'DTC': code, 'Status': DEFAULT_STATUS})

    return pl.DataFrame(
        rows,
        schema={'System': pl.String, 'Profile': pl.String, 'DTC': pl.String, 'Status': pl.String},
    )


# ---------------------------------------------------------------------------
# Write one config file
# ---------------------------------------------------------------------------

def write_config(
    vin_ymme: dict[str, str],
    nws_rows: pl.DataFrame,
    output_path: Path,
) -> None:
    """Write VIN_YMME and NWS sheets to *output_path* (overwrites if exists)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    ws_vin = wb.create_sheet('VIN_YMME')
    vin_header = ['VIN', 'Year', 'Manufacturer', 'Make', 'Model', 'Engine']
    ws_vin.append(vin_header)
    ws_vin.append([vin_ymme[col] for col in vin_header])

    ws_nws = wb.create_sheet('NWS')
    nws_header = ['System', 'Profile', 'DTC', 'Status']
    ws_nws.append(nws_header)
    for row in nws_rows.iter_rows(named=True):
        ws_nws.append([row[col] for col in nws_header])

    wb.save(output_path)


# ---------------------------------------------------------------------------
# Main entry point — generate one file per YMME
# ---------------------------------------------------------------------------

def generate_all_configs(output_dir: Path = DEFAULT_OUTPUT_DIR) -> None:
    """
    Generate one config Excel file per unique YMME that has at least one
    GROUP_ABS or GROUP_SRS profile with a CAN-based protocol.

    All data (NWS Ymme, Profile, DTC) is loaded once; per-YMME processing
    is polars filter + openpyxl write.

    Parameters
    ----------
    output_dir:
        Directory where all config files are written.
        Default: config/auto_configs/
    """
    nws_file = _find_xlsx(NWS_DIR)
    dtc_file = _find_xlsx(DTC_DIR)
    logger.info('NWS database : %s', nws_file)
    logger.info('DTC database : %s', dtc_file)

    # ── Load all source data once ─────────────────────────────────────────────
    logger.info('=== Loading source databases ===')
    ymme_df    = load_nws_ymme(nws_file)
    profile_df = load_nws_profile(nws_file)
    dtc_raw    = load_dtc(dtc_file)

    # ── Step 1: filter and enumerate YMMEs ───────────────────────────────────
    logger.info('=== Step 1: Filtering profiles ===')
    filtered   = filter_abs_srs_can(ymme_df, profile_df)
    dtc_clean  = prepare_dtc(dtc_raw)           # cleaned once, reused per YMME

    unique_ymmes = (
        filtered.select(YMME_COLS)
        .unique()
        .sort(YMME_COLS)                        # deterministic order: Year asc
    )
    total = len(unique_ymmes)
    logger.info('Total unique YMMEs to process: %d', total)

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 2 + write: one file per YMME ────────────────────────────────────
    logger.info('=== Step 2: DTC mapping + writing config files ===')
    ok = skipped = 0

    for i, ymme in enumerate(unique_ymmes.iter_rows(named=True), 1):
        try:
            systems  = get_systems_for_ymme(filtered, ymme)
            vin_ymme = build_vin_ymme(ymme)
            nws_rows = map_dtc_to_nws(systems, dtc_clean)

            tag      = _ymme_tag(ymme)
            out_path = output_dir / f'{tag}.xlsx'
            write_config(vin_ymme, nws_rows, out_path)
            ok += 1

        except Exception as exc:
            logger.error('FAILED [%d/%d] %s %s %s %s: %s',
                         i, total, ymme['Year'], ymme['Make'], ymme['Model'], ymme['Engine'], exc)
            skipped += 1

        if i % 100 == 0 or i == total:
            logger.info('Progress: %d/%d  (ok=%d  failed=%d)', i, total, ok, skipped)

    logger.info('Done. %d config files written to %s', ok, output_dir)


if __name__ == '__main__':
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT_DIR
    generate_all_configs(out_dir)
