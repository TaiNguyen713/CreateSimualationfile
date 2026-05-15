"""
makes/hyundai/protocols/can_uds/protocol.py

HyundaiCanUdsProtocol — Hyundai Diagnostic CAN (ISO 15765 / UDS) implementation.

Design principle: every method delegates to the existing builders in
first_idea.py and src/vin_builder.py.  Those files are NOT modified.
This class is purely an adapter that slots the existing logic into the
BaseProtocol interface so the factory can dispatch to it.

Adding Toyota CAN-UDS
---------------------
Create makes/toyota/protocols/can_uds/protocol.py, subclass BaseProtocol,
decorate with @register('toyota', 'can_uds'), and implement each method
using Toyota-specific data and builders.  Nothing here changes.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.base_protocol import BaseProtocol
from core.protocol_factory import register


@register('hyundai', 'can_uds')
class HyundaiCanUdsProtocol(BaseProtocol):
    """Hyundai Diagnostic CAN-UDS protocol.

    All business logic lives in first_idea.py (section builders) and
    src/vin_builder.py (OBD2 template + per-vehicle file generation).
    Those modules are imported lazily to avoid circular imports.
    """

    # ── lazy module accessors (avoid top-level circular imports) ──────────────

    @staticmethod
    def _b():
        """Return the first_idea module (builder functions)."""
        import first_idea
        return first_idea

    @staticmethod
    def _v():
        """Return the src.vin_builder module (OBD2 template + file writer)."""
        from src import vin_builder
        return vin_builder

    # ── BaseProtocol interface ─────────────────────────────────────────────────

    def build_header(self) -> list[str]:
        return self._b().build_header()

    def build_obd2_section(self, vin: str) -> list[str]:
        return self._v()._build_obd2_section(vin)

    def build_nws_init_lines(
        self,
        nws_config: pd.DataFrame,
        profile_df: pd.DataFrame,
    ) -> list[str]:
        return self._b().build_nws_init_lines(nws_config, profile_df)

    def build_dtc_lines(
        self,
        nws_config: pd.DataFrame,
        nws_profile: pd.DataFrame,
        system_id_map: dict[str, tuple[str, str]],
    ) -> list[str]:
        return self._b().build_dtc_lines(nws_config, nws_profile, system_id_map)

    def build_live_data_lines(
        self,
        merged: pd.DataFrame,
        system_id_map: dict[str, tuple[str, str]],
    ) -> list[str]:
        return self._b().build_live_data_lines(merged, system_id_map)

    def build_all_system_content(
        self,
        pids: pd.DataFrame,
        loader: object,
        config_path: Path,
    ) -> dict[str, list[str]]:
        return self._b().build_all_system_content(pids, loader, config_path)

    def generate_sim_files(self, config_path: Path, output_dir: Path) -> int:
        return self._v().generate_vin_sim_files(config_path, output_dir)
