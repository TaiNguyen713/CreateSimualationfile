"""
db_schema.py — Database registry and loader library.

Defines every database/sheet the project can read.  To add a new source:
  1. Add its column list (e.g. _NEW_COLS)
  2. Add a DataSource entry in DB_REGISTRY
  3. Call loader.get('NEW_KEY', 'Sheet Name') wherever needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


# ── Data-model ────────────────────────────────────────────────────────────────

@dataclass
class SheetSpec:
    """Describes how to read one sheet from an Excel file."""
    cols: list[str] | None       # None → load every column
    skiprows: list[int] = field(default_factory=list)


@dataclass
class DataSource:
    """One Excel file with one or more named sheets."""
    path: Path
    sheets: dict[str, SheetSpec]


# ── Column lists ──────────────────────────────────────────────────────────────

# ABS Live-Data database  (Hyundai_ABS_LD_*.xlsx)
_LD_ITEM_COLS: list[str] = [
    'ItemID', 'ItemName', 'GetValueCmd', 'TotalDataSize',
    'BytePosition', 'Bytesize', 'Endian', 'Formula', 'Sign', 'a', 'b', 'Floating',
]

_LD_PROFILE_COLS: list[str] = [
    'MsgID/ECUID/ProfileID', 'Protocol', 'Option1', 'ItemID',
]

# NWS (Network Scan) database  (Hyundai_NWScan_*.xlsx)
_NWS_YMME_COLS: list[str] = [
    'Year', 'Manufacturer', 'Make', 'Model', 'Engine',
    'System', 'MsgID/ECUID',
]

_NWS_PROFILE_COLS: list[str] = [
    'MsgID/ECUID', 'Protocol', 'TagAddr/CanReq1', 'Baudrate',
    'CMD Read DTC', 'CMD Erase',
]

# DTC database  (Hyundai_DTCDatabase_*.xlsx)
# The DTC sheet has a merged multi-row header; load all columns and
# re-index manually when consuming this sheet.
_DTC_ALL_COLS: None = None


# ── Registry ──────────────────────────────────────────────────────────────────

DB_REGISTRY: dict[str, DataSource] = {

    'ABS_LD': DataSource(
        path=Path('data/Hyundai_ABS_LD_V20.00.02_Mar172021.xlsx'),
        sheets={
            'Item ID':    SheetSpec(cols=_LD_ITEM_COLS,    skiprows=[0, 2, 3]),
            'Profile ID': SheetSpec(cols=_LD_PROFILE_COLS, skiprows=[0, 2, 3]),
        },
    ),

    'NWS': DataSource(
        path=Path('data/Hyundai_NWScan_v20.00.04_Apr172021.xlsx'),
        sheets={
            'Ymme':    SheetSpec(cols=_NWS_YMME_COLS,    skiprows=[0, 2, 3]),
            'Profile': SheetSpec(cols=_NWS_PROFILE_COLS, skiprows=[0, 2, 3]),
        },
    ),

    'DTC': DataSource(
        path=Path('data/Hyundai_DTCDatabase_PCMABSSRS_V24.00.04_Nov032025.xlsx'),
        sheets={
            'DTC': SheetSpec(cols=_DTC_ALL_COLS, skiprows=[]),
        },
    ),

}


# ── Loader ────────────────────────────────────────────────────────────────────

class DataLoader:
    """
    Loads and caches DataFrames from the registry on demand.

    Usage::

        loader = DataLoader()                          # uses DB_REGISTRY
        item_db  = loader.get('ABS_LD', 'Item ID')
        profiles = loader.get('ABS_LD', 'Profile ID')

    All cells are cast to ``str`` so callers never deal with mixed dtypes
    from Excel's number/string coercion.
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
        """All registered source keys."""
        return list(self._registry)

    def sheets(self, source: str) -> list[str]:
        """All registered sheet names for *source*."""
        if source not in self._registry:
            raise KeyError(f'Unknown source {source!r}. Available: {self.sources()}')
        return list(self._registry[source].sheets)

    def clear_cache(self) -> None:
        """Force a reload on the next ``get()`` call."""
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
        if not ds.path.exists():
            raise FileNotFoundError(
                f'Database file not found for {source!r}: {ds.path}'
            )
        spec = ds.sheets[sheet]
        logger.info('Loading  %-10s / %-20s  from %s', source, sheet, ds.path)
        return pd.read_excel(
            ds.path,
            sheet_name=sheet,
            skiprows=spec.skiprows,
            usecols=spec.cols,
        ).astype(str)
