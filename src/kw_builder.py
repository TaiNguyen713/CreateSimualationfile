"""
kw_builder.py — PROTOCOL_KW (KWP2000 FastBaud) sim file builder.

Frame format
------------
  Request : Req>1  {0x80|n} {TGT} {SRC} {SID} {params}   CS 0 0
  Response: Res>1  {0x80|m} {SRC(tester)} {TGT(ECU)} {resp_SID} {data}  CS 0 0

  Functional broadcast (OBD2):
  Request : Req>1  {0xC0|n} 33 {SRC} {SID} {params}  CS 0 0
"""

from __future__ import annotations

import logging

import pandas as pd

from src._serial_common import (
    _NAN, _DEFAULT_SRC, _BROADCAST_TGT,
    _positive_resp, _split_cmds, _dtc_to_bytes,
    _fmt_line, _resolve_profile, _header_block, _profile_pin_baudrate,
)

logger = logging.getLogger(__name__)

_PROTOCOL_NUM = '15'
_SUFFIX       = 'CS\t0\t0'


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------

def make_kw_req(
    tgt: str,
    src: str,
    data_bytes: list[str],
    functional: bool = False,
) -> str:
    """Build a KWP2000 request line."""
    n      = len(data_bytes)
    prefix = 0xC0 if functional else 0x80
    fmt    = f'{prefix | n:02X}'
    return _fmt_line('Req>1', [fmt, tgt.strip(), src.strip()] + data_bytes, _SUFFIX)


def make_kw_res(
    req_tgt: str,
    req_src: str,
    data_bytes: list[str],
) -> str:
    """
    Build a KWP2000 response line.

    req_tgt → ECU physical address (becomes response source).
    req_src → tester address (becomes response target).
    """
    fmt = f'{0x80 | len(data_bytes):02X}'
    return _fmt_line('Res>1', [fmt, req_src.strip(), req_tgt.strip()] + data_bytes, _SUFFIX)


# ---------------------------------------------------------------------------
# Config header
# ---------------------------------------------------------------------------

def build_kw_header(profile_row: pd.Series) -> list[str]:
    """Build <config sw> header for PROTOCOL_KW from a NWS Profile row."""
    rx_pin, tx_pin, rx_volt, tx_volt, baudrate = _profile_pin_baudrate(profile_row)
    return _header_block(
        proto_num=_PROTOCOL_NUM,
        rx_pin=rx_pin, tx_pin=tx_pin,
        rx_volt=rx_volt, tx_volt=tx_volt,
        baudrate=baudrate,
        tbyte='5', tframe='15', n_frames='1', range_val='   0,0;',
    )


# ---------------------------------------------------------------------------
# OBD2 section
# ---------------------------------------------------------------------------

def _vin_frames(vin: str, src: str, obd2_ecu: str) -> list[str]:
    vin_b = [f'{ord(c):02X}' for c in vin[:17].ljust(17)]
    lines = [
        f'//VIN: {vin} //',
        make_kw_req(_BROADCAST_TGT, src, ['09', '02'], functional=True),
        make_kw_res(obd2_ecu, src, ['49', '02', '01', '00', '00', '00', vin_b[0]]),
    ]
    for seq in range(2, 6):
        start = (seq - 2) * 4 + 1
        chunk = vin_b[start:start + 4]
        if not any(b != '00' for b in chunk):
            break
        lines.append(make_kw_res(obd2_ecu, src,
                                  ['49', '02', f'{seq:02X}'] + chunk))
    return lines


def build_kw_obd2_section(
    vin: str,
    src: str = _DEFAULT_SRC,
    obd2_ecu: str = '11',
) -> list[str]:
    """KW OBD2 section — functional broadcast (TGT=0x33, Res>1, CS suffix)."""
    def req(d):
        return make_kw_req(_BROADCAST_TGT, src, d, functional=True)
    def res(d):
        return make_kw_res(obd2_ecu, src, d)

    lines = [
        '', '//---------------------------------OBD 2', '',
        '//Init',
        req(['81']), res(['C1', 'E9', '8F']),
        '',
        '//Mode 01 - supported PIDs',
        req(['01', '00']), res(['41', '00', 'BE', '3F', 'B8', '11']),
        '',
        '//---------------------------------Mode 03: stored DTCs',
        req(['03']), res(['43', '00', '00', '00', '00', '00', '00']),
        '',
        '//---------------------------------Mode 07: pending DTCs',
        req(['07']), res(['47', '00', '00', '00', '00', '00', '00']),
        '',
        '//---------------------------------Mode 04: erase DTCs',
        req(['04']), res(['44']),
        '',
        '//---------------------------------Mode 09 PID 02: VIN',
    ]
    lines.extend(_vin_frames(vin, src, obd2_ecu))
    return lines


# ---------------------------------------------------------------------------
# NWS init section
# ---------------------------------------------------------------------------

def build_kw_nws_init_lines(
    nws_config: pd.DataFrame,
    profile_df: pd.DataFrame,
) -> list[str]:
    """NWS init Req/Res for every unique PROTOCOL_KW profile in nws_config."""
    lines:         list[str] = []
    seen_profiles: set[str]  = set()

    for system, group in nws_config.groupby(
            nws_config['System'].fillna('Unknown'), sort=False):
        lines.append('')
        lines.append(f'//System: {system} - NWS init (KW)')

        for _, row in group.iterrows():
            profile = str(row.get('Profile', '')).strip()
            if not profile or profile in _NAN:
                continue
            lines.append(
                f'//NOTE: System={system} Profile={profile} '
                f'DTC={row.get("DTC","")} Status={row.get("Status","")}'
            )
            if profile in seen_profiles:
                continue
            seen_profiles.add(profile)

            prow = _resolve_profile(profile, profile_df)
            if prow is None:
                logger.warning('[E105] No NWS profile match for %s (KW init)', profile)
                continue

            tgt = str(prow.get('TagAddr/CanReq1', '')).strip()
            src = str(prow.get('SourceAddr', _DEFAULT_SRC)).strip()
            if src in _NAN:
                src = _DEFAULT_SRC
            if not tgt or tgt in _NAN:
                tgt = _BROADCAST_TGT

            for data in _split_cmds(prow.get('CMD Query', '')):
                lines.append(make_kw_req(tgt, src, data))
                lines.append(make_kw_res(tgt, src, _positive_resp(data)))

            for data in _split_cmds(prow.get('CMD KeepAlive', '')):
                lines.append(make_kw_req(tgt, src, data))
                lines.append(make_kw_res(tgt, src, _positive_resp(data)))

    return lines


# ---------------------------------------------------------------------------
# DTC section
# ---------------------------------------------------------------------------

def build_kw_dtc_lines(
    nws_config: pd.DataFrame,
    profile_df: pd.DataFrame,
) -> list[str]:
    """DTC Req/Res for every (System, Profile) group with PROTOCOL_KW."""
    lines: list[str] = []

    for (system, profile), grp in nws_config.groupby(
            [nws_config['System'].fillna('Unknown'),
             nws_config['Profile'].fillna('nan')],
            sort=False):
        profile = str(profile).strip()
        if not profile or profile in _NAN:
            continue

        dtcs: list[tuple[str, str]] = [
            (str(r.get('DTC', '')).strip(), str(r.get('Status', '00')).strip())
            for _, r in grp.iterrows()
            if str(r.get('DTC', '')).strip() not in _NAN
            and str(r.get('DTC', '')).strip() != '0'
        ]
        if not dtcs:
            continue

        prow = _resolve_profile(profile, profile_df)
        if prow is None:
            logger.warning('[E105] No NWS profile match for %s (KW DTC)', profile)
            continue

        tgt = str(prow.get('TagAddr/CanReq1', '')).strip()
        src = str(prow.get('SourceAddr', _DEFAULT_SRC)).strip()
        if src in _NAN:
            src = _DEFAULT_SRC
        if not tgt or tgt in _NAN:
            tgt = _BROADCAST_TGT

        dtc_cmds = _split_cmds(prow.get('CMD Read DTC', ''))
        if not dtc_cmds:
            continue

        lines.append('')
        lines.append(f'//System: {system} - DTC read (Profile={profile}, KW)')
        for dtc_val, _ in dtcs:
            lines.append(f'//DTC: {dtc_val}')

        for cmd_bytes in dtc_cmds:
            lines.append(make_kw_req(tgt, src, cmd_bytes))
            try:
                svc = int(cmd_bytes[0], 16)
            except (ValueError, IndexError):
                svc = 0x18
            payload = [f'{svc | 0x40:02X}']
            if svc in (0x18, 0x13):
                payload.append(f'{len(dtcs):02X}')
            for dtc_val, status in dtcs:
                payload += _dtc_to_bytes(dtc_val)
                try:
                    payload.append(f'{int(status, 16):02X}')
                except (ValueError, TypeError):
                    payload.append('00')
            lines.append(make_kw_res(tgt, src, payload))

    return lines


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def build_kw_system_content(
    config_path,
    profile_df: pd.DataFrame,
) -> dict[str, list[str]]:
    """Return {system: [lines]} for all KW systems in *config_path*'s NWS sheet."""
    import pandas as _pd

    xf = _pd.ExcelFile(str(config_path))
    if 'NWS' not in xf.sheet_names:
        return {}
    nws_config = _pd.read_excel(xf, sheet_name='NWS').astype(str)
    content: dict[str, list[str]] = {}
    for system, group in nws_config.groupby(
            nws_config['System'].fillna('Unknown'), sort=False):
        system = str(system).strip()
        content[system] = (
            build_kw_nws_init_lines(group, profile_df)
            + build_kw_dtc_lines(group, profile_df)
        )
    return content
