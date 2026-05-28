"""
can_builder.py — PROTOCOL_CAN / PROTOCOL_CAN_UDS sim file builder.

Wraps first_idea.py for use in the unified protocol-dispatch flow of
vin_builder.py, exposing a consistent interface alongside kw_builder and
iso_builder.

CAN frame format (handled internally by first_idea.py):
  Req>1  {CAN_ID} 08 {8-byte-payload}  NONE 0 0
  Res<1  {CAN_ID} 08 {8-byte-payload}  NONE 0 0
"""

from __future__ import annotations

import pandas as pd

# first_idea.py exports used here
from first_idea import (
    PROTOCOL_CONFIG,
    build_header,
    build_all_system_content,
)


def build_can_header() -> list[str]:
    """Return the <config sw> header block for CAN/CAN_UDS."""
    return build_header()


def build_can_system_content(
    pids: pd.DataFrame,
    loader,
    config_path,
) -> dict[str, list[str]]:
    """
    Build CAN sim content per system.

    Parameters
    ----------
    pids        : PIDs DataFrame (from config Excel 'PIDs' sheet).
    loader      : DataLoader instance (from first_idea.DataLoader).
    config_path : Path to the vehicle config Excel file.

    Returns
    -------
    {system_name: [lines]} — same contract as kw/iso builders.
    """
    return build_all_system_content(pids, loader, config_path)
