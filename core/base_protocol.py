"""
base_protocol.py — Strategy interface for all Make / Protocol combinations.

Every new Make+Protocol (e.g. Toyota/CAN-UDS, GM/K-Line) must subclass
BaseProtocol and implement every abstract method.  The factory in
core/protocol_factory.py maps (make, protocol) strings to concrete subclasses.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd


class BaseProtocol(ABC):
    """Abstract interface all Make/Protocol classes must satisfy."""

    # ── .sim header ───────────────────────────────────────────────────────────

    @abstractmethod
    def build_header(self) -> list[str]:
        """Protocol config block written at the top of every .sim file."""

    # ── OBD2 layer (mandatory for every vehicle) ──────────────────────────────

    @abstractmethod
    def build_obd2_section(self, vin: str) -> list[str]:
        """OBD2 simulation lines; only the VIN bytes differ per vehicle."""

    # ── NWS / system init ─────────────────────────────────────────────────────

    @abstractmethod
    def build_nws_init_lines(
        self,
        nws_config: pd.DataFrame,
        profile_df: pd.DataFrame,
    ) -> list[str]:
        """NWS initialization Req/Res frame pairs per system."""

    # ── DTC read ──────────────────────────────────────────────────────────────

    @abstractmethod
    def build_dtc_lines(
        self,
        nws_config: pd.DataFrame,
        nws_profile: pd.DataFrame,
        system_id_map: dict[str, tuple[str, str]],
    ) -> list[str]:
        """Simulated DTC-read Req/Res frames per (system, profile) group."""

    # ── Live data ─────────────────────────────────────────────────────────────

    @abstractmethod
    def build_live_data_lines(
        self,
        merged: pd.DataFrame,
        system_id_map: dict[str, tuple[str, str]],
    ) -> list[str]:
        """Live data Req/Res blocks, one per unique command per system."""

    # ── Orchestration ─────────────────────────────────────────────────────────

    @abstractmethod
    def build_all_system_content(
        self,
        pids: pd.DataFrame,
        loader: object,
        config_path: Path,
    ) -> dict[str, list[str]]:
        """Return {system_name: [lines]} — NWS init → DTC → Live Data per system."""

    @abstractmethod
    def generate_sim_files(self, config_path: Path, output_dir: Path) -> int:
        """Write one output folder per VIN row. Returns number of folders written."""
