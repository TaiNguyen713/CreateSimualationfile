"""
db_schema.py — Database registry and folder-based loader.

Each DB source is now a *folder* rather than a single file:
  - All .xlsx files in the folder (and any sub-folders) are discovered automatically.
  - Each discovered file is validated against the source's expected sheet names
    and column lists (the "db schema").
  - A file that does not match prompts the user on the terminal:
        [SCHEMA MISMATCH] data/Make_LD/some_other_file.xlsx
          Reason : ...
          Skip this file? (y/n):
    y → skip silently, n → abort the process.
  - All valid files that contain the requested sheet are loaded and concatenated.

To add a new DB source:
  1. Add its column-list constants below.
  2. Add a DataSource entry in DB_REGISTRY pointing to its folder.
  3. Place matching Excel files in that folder.
  4. Call loader.get('NEW_KEY', 'Sheet Name') wherever needed.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


# ── Data-model ────────────────────────────────────────────────────────────────

@dataclass
class SheetSpec:
    """Describes how to read one sheet from an Excel file."""
    cols: list[str] | None           # None → load every column
    skiprows: list[int] = field(default_factory=list)


@dataclass
class DataSource:
    """One folder (possibly with sub-folders) containing Excel files for one DB source."""
    folder: Path                     # root folder to scan; sub-folders are included
    sheets: dict[str, SheetSpec]


# ── Column lists ──────────────────────────────────────────────────────────────

# Make Live-Data database  (Hyundai_Make_LD_*.xlsx or equivalent)
_LD_ITEM_COLS: list[str] = [
    'ItemID', 'ItemName', 'GetValueCmd', 'TotalDataSize',
    'BytePosition', 'Bytesize', 'Endian', 'Formula', 'Sign', 'a', 'b', 'Floating',
]

_LD_PROFILE_COLS: list[str] = [
    'MsgID/ECUID/ProfileID', 'ItemID', 'Protocol', 'Option1', 'Option2',
    'SupReQ1', 'SupByteSize1', 'SupBitSize1', 'SupBytePos1', 'SupBitPos1', 'SupStatus1',
    'SupReQ2', 'SupByteSize2', 'SupBitSize2', 'SupBytePos2', 'SupBitPos2', 'SupStatus2',
]

# NWS (Network Scan) database  (Hyundai_NWScan_*.xlsx or equivalent)
_NWS_YMME_COLS: list[str] = [
    'Year', 'Manufacturer', 'Make', 'Model', 'Engine',
    'System', 'MsgID/ECUID',
]

_NWS_PROFILE_COLS: list[str] = [
    'MsgID/ECUID', 'Connector', 'Protocol', 'InitPin', 'InitPinVolt',
    'CanH/Rx', 'CanH/RxVolt', 'CanL/Tx', 'CanL/TxVolt', 'Resitor',
    'Vref', 'Baudrate', 'SerialDataFormat', 'Checksum', 'TimingWup',
    'TimingP', 'TimingW', 'xxTiming1281', 'Header', 'xxAutoFormat',
    'TagAddr/CanReq1', 'SourceAddr', 'CanStart1', 'CanEnd1', 'xxCanStart2',
    'xxCanEnd2', 'FiveBaud', 'InitType', 'CMD Query', 'xxCMD ECUInfo',
    'xxECUInfoType', 'CMD KeepAlive', 'CMD Read DTC', 'DtcReadType',
    'Offset', 'DTC Frame', 'DTC Format', 'LookupTable', 'DtcDisplayType',
    'CMD Erase', 'xxEraseType', 'SID Exit', 'Note',
]

# DTC database — merged multi-row header; load all columns
_DTC_ALL_COLS: None = None


# ── Registry ──────────────────────────────────────────────────────────────────

DB_REGISTRY: dict[str, DataSource] = {

    'Make_LD': DataSource(
        folder=Path('data/Make_LD'),
        sheets={
            'Item ID':    SheetSpec(cols=_LD_ITEM_COLS,    skiprows=[0, 2, 3]),
            'Profile ID': SheetSpec(cols=_LD_PROFILE_COLS, skiprows=[0, 2, 3]),
        },
    ),

    'NWS': DataSource(
        folder=Path('data/NWS'),
        sheets={
            'Ymme':    SheetSpec(cols=_NWS_YMME_COLS,    skiprows=[0, 2, 3]),
            'Profile': SheetSpec(cols=_NWS_PROFILE_COLS, skiprows=[0, 2, 3]),
        },
    ),

    'DTC': DataSource(
        folder=Path('data/DTC'),
        sheets={
            'DTC': SheetSpec(cols=_DTC_ALL_COLS, skiprows=[]),
        },
    ),

}


# ── File-discovery helpers ────────────────────────────────────────────────────

def _discover_xlsx(folder: Path) -> list[Path]:
    """Return all .xlsx files under *folder* (recursive), sorted by path."""
    return sorted(folder.rglob('*.xlsx'))


def _file_matches_source(file: Path, xf: pd.ExcelFile, sheets: dict[str, SheetSpec]) -> str | None:
    """Check whether an already-opened Excel file matches a source schema.

    Returns None  → file is valid for this source (has at least one sheet+columns match).
    Returns str   → human-readable mismatch reason; file should be offered for skipping.
    """
    available = xf.sheet_names
    source_sheets = list(sheets.keys())

    matched_any_sheet = False
    for sheet_name, spec in sheets.items():
        if sheet_name not in available:
            continue
        matched_any_sheet = True

        if spec.cols is None:
            return None  # sheet present, no column check needed — valid

        try:
            df_hdr = pd.read_excel(xf, sheet_name=sheet_name,
                                   skiprows=spec.skiprows, nrows=0)
        except Exception:
            continue  # can't read this sheet — try next

        missing = [c for c in spec.cols if c not in df_hdr.columns]
        if not missing:
            return None  # all expected columns present — valid

    if not matched_any_sheet:
        return (
            f'None of the expected sheets {source_sheets} found. '
            f'File has: {available}'
        )

    # At least one sheet name matched but columns were wrong / unreadable
    return (
        f'Sheet(s) {[s for s in source_sheets if s in available]} found '
        f'but required columns are missing or unreadable.'
    )


def _prompt_skip(file: Path, reason: str) -> None:
    """Print a schema-mismatch warning and ask the user whether to skip the file.

    y (or Enter in a non-interactive shell) → returns normally; file is skipped.
    n → prints an abort message and calls sys.exit(1).
    """
    sep = '-' * 60
    print(f'\n{sep}')
    print(f'[SCHEMA MISMATCH]  {file}')
    print(f'  Reason : {reason}')
    print(sep)
    while True:
        try:
            ans = input('  Skip this file? (y/n): ').strip().lower()
        except EOFError:
            ans = 'y'   # non-interactive — skip automatically
        if ans == 'y':
            logger.info('[SKIP] %s', file)
            return
        if ans == 'n':
            print('\nProcess aborted by user.')
            sys.exit(1)
        print('  Please enter y or n.')


# ── Loader ────────────────────────────────────────────────────────────────────

class DataLoader:
    """
    Loads and caches DataFrames from the DB_REGISTRY on demand.

    For each (source, sheet) request the loader:
      1. Recursively discovers all .xlsx files in the source folder.
      2. Opens each file and validates it against the source schema.
      3. Prompts the user on the terminal for any file that does not match.
      4. Reads the requested sheet from every valid file and concatenates.

    Usage::

        loader = DataLoader()
        item_db  = loader.get('Make_LD', 'Item ID')
        profiles = loader.get('Make_LD', 'Profile ID')

    All cells are cast to ``str`` so callers never deal with mixed dtypes.
    """

    def __init__(self, registry: dict[str, DataSource] = DB_REGISTRY) -> None:
        self._registry = registry
        self._cache: dict[tuple[str, str], pd.DataFrame] = {}

    # ── public API ────────────────────────────────────────────────────────────

    def get(self, source: str, sheet: str) -> pd.DataFrame:
        """Return the DataFrame for *(source, sheet)*, loading on first access."""
        key = (source, sheet)
        if key not in self._cache:
            self._cache[key] = self._load(source, sheet)
        return self._cache[key]

    def sources(self) -> list[str]:
        return list(self._registry)

    def sheets(self, source: str) -> list[str]:
        if source not in self._registry:
            raise KeyError(f'Unknown source {source!r}. Available: {self.sources()}')
        return list(self._registry[source].sheets)

    def clear_cache(self) -> None:
        """Force a reload on the next get() call."""
        self._cache.clear()

    # ── internal ──────────────────────────────────────────────────────────────

    def _load(self, source: str, sheet: str) -> pd.DataFrame:
        if source not in self._registry:
            raise KeyError(f'Unknown source {source!r}. Available: {self.sources()}')
        ds = self._registry[source]
        if sheet not in ds.sheets:
            raise KeyError(
                f'Unknown sheet {sheet!r} in {source!r}. '
                f'Available: {self.sheets(source)}'
            )
        if not ds.folder.exists():
            raise FileNotFoundError(
                f'DB folder not found for {source!r}: {ds.folder}'
            )

        spec       = ds.sheets[sheet]
        xlsx_files = _discover_xlsx(ds.folder)

        if not xlsx_files:
            logger.warning('No .xlsx files found in %s for source %r', ds.folder, source)
            return pd.DataFrame()

        logger.info(
            'Scanning %d file(s) in %s for source %r / sheet %r',
            len(xlsx_files), ds.folder, source, sheet,
        )

        frames: list[pd.DataFrame] = []

        for file in xlsx_files:
            # ── open once, reuse for both validation and loading ──────────────
            try:
                xf = pd.ExcelFile(file)
            except Exception as exc:
                _prompt_skip(file, f'Cannot open file: {exc}')
                continue

            # ── validate against source schema ────────────────────────────────
            mismatch = _file_matches_source(file, xf, ds.sheets)
            if mismatch:
                _prompt_skip(file, mismatch)
                continue  # user chose 'y' (skip); 'n' already called sys.exit

            # ── file is valid for this source — does it have the sheet? ───────
            if sheet not in xf.sheet_names:
                logger.debug(
                    'File %s is a valid %r source but has no sheet %r — skipping',
                    file.name, source, sheet,
                )
                continue

            # ── load the sheet ────────────────────────────────────────────────
            try:
                df = pd.read_excel(
                    xf,
                    sheet_name=sheet,
                    skiprows=spec.skiprows,
                    usecols=spec.cols,
                ).astype(str)
                frames.append(df)
                logger.info(
                    'Loaded   %-10s / %-20s  from %s  (%d rows)',
                    source, sheet, file.name, len(df),
                )
            except Exception as exc:
                logger.warning(
                    'Error reading %r / %r from %s: %s', source, sheet, file, exc,
                )

        if not frames:
            logger.warning(
                'No usable files for %r / %r in %s', source, sheet, ds.folder,
            )
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        logger.info(
            'Merged   %-10s / %-20s  -> %d total rows from %d file(s)',
            source, sheet, len(result), len(frames),
        )
        return result
