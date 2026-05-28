"""
iso_builder.py — PROTOCOL_ISO9141 (ISO 9141-2 / 5-baud) sim file builder.

Frame format
------------
  Request : Req>1  68 {TGT} {SRC} {SID} {params}         0 0
  Response: Res<1  48 {TGT+1} {ECU_ADDR} {resp_SID} {data}  0 0
  VIN/NWS : same but with suffix CS 0 0

  Init (5-baud):
    Req>1  >>addr         4 0
    Res<1  t20  55        4 0
    Res<1  t50  KB1 KB2   4 0
    Req>2  q00  ~KB2      4 0
    Res<2  t20  ~addr     4 0
"""

from __future__ import annotations

import logging

import pandas as pd

from src._serial_common import (
    _NAN, _DEFAULT_SRC, _BROADCAST_TGT, _ISO_DEFAULT_ECU,
    _positive_resp, _split_cmds, _dtc_to_bytes,
    _fmt_line, _resolve_profile, _header_block, _profile_pin_baudrate,
)

logger = logging.getLogger(__name__)

_PROTOCOL_NUM  = '14'
_INIT_SUFFIX   = '4\t0'       # 5-baud handshake lines
_DATA_SUFFIX   = '0\t0'       # regular OBD2 data lines
_CS_SUFFIX     = 'CS\t0\t0'  # VIN and NWS proprietary lines


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------

def make_iso_req(
    tgt: str,
    src: str,
    data_bytes: list[str],
    suffix: str = _DATA_SUFFIX,
) -> str:
    """Build an ISO9141 request line: Req>1  68 TGT SRC SID params."""
    return _fmt_line('Req>1', ['68', tgt.strip(), src.strip()] + data_bytes, suffix)


def make_iso_res(
    req_tgt: str,
    data_bytes: list[str],
    ecu: str = _ISO_DEFAULT_ECU,
    suffix: str = _DATA_SUFFIX,
) -> str:
    """
    Build an ISO9141 response line: Res<1  48 (req_tgt+1) ecu resp_SID data.

    req_tgt → original request target (TagAddr); response target = req_tgt + 1.
    ecu     → ECU physical source address in the response frame.
    """
    try:
        resp_tgt = f'{int(req_tgt.strip(), 16) + 1:02X}'
    except ValueError:
        resp_tgt = '6B'
    return _fmt_line('Res<1', ['48', resp_tgt, ecu] + data_bytes, suffix)


# ---------------------------------------------------------------------------
# Config header
# ---------------------------------------------------------------------------

def build_iso_header(profile_row: pd.Series) -> list[str]:
    """Build <config sw> header for PROTOCOL_ISO9141 from a NWS Profile row."""
    rx_pin, tx_pin, rx_volt, tx_volt, baudrate = _profile_pin_baudrate(profile_row)
    return _header_block(
        proto_num=_PROTOCOL_NUM,
        rx_pin=rx_pin, tx_pin=tx_pin,
        rx_volt=rx_volt, tx_volt=tx_volt,
        baudrate=baudrate,
        tbyte='3', tframe='5', n_frames='2',
        range_val='              7df,7df;0,0;',
    )


# ---------------------------------------------------------------------------
# 5-baud init sequence
# ---------------------------------------------------------------------------

def build_iso_init(
    five_baud_addr: str,
    kb1: str = '08',
    kb2: str = '08',
) -> list[str]:
    """
    Generate the ISO 9141-2 5-baud initialization handshake.

    Parameters
    ----------
    five_baud_addr : hex string from NWS profile FiveBaud field (e.g. '33').
    kb1, kb2       : keyword bytes (Hyundai/Kia default '08' '08').
    """
    addr      = five_baud_addr.strip().upper().zfill(2)
    compl_kb2 = f'{(~int(kb2, 16)) & 0xFF:02X}'
    compl_adr = f'{(~int(addr, 16)) & 0xFF:02X}'
    s         = _INIT_SUFFIX

    return [
        f'INFO_DATABASE = Req>1\t\t\t>>{addr}\t{s}',
        f'INFO_DATABASE = Res<1  t20\t\t\t55\t{s}',
        f'INFO_DATABASE = Res<1  t50\t\t\t{kb1.upper()} {kb2.upper()}\t{s}',
        f'INFO_DATABASE = Req>2\t\t\tq00 {compl_kb2}\t{s}',
        f'INFO_DATABASE = Res<2  t20\t\t\t{compl_adr}\t{s}',
    ]


# ---------------------------------------------------------------------------
# OBD2 section
# ---------------------------------------------------------------------------

def _vin_frames(vin: str, tgt: str, src: str, ecu: str) -> list[str]:
    vin_b = [f'{ord(c):02X}' for c in vin[:17].ljust(17)]
    lines = [
        f'//VIN: {vin} //',
        make_iso_req(tgt, src, ['09', '00'], suffix=_CS_SUFFIX),
        make_iso_res(tgt, ['49', '00', '01', 'FC', '00', '00', '00'],
                     ecu=ecu, suffix=_CS_SUFFIX),
        '',
        make_iso_req(tgt, src, ['09', '02'], suffix=_CS_SUFFIX),
        make_iso_res(tgt, ['49', '02', '01', '00', '00', '00', vin_b[0]],
                     ecu=ecu, suffix=_CS_SUFFIX),
    ]
    for seq in range(2, 6):
        start = (seq - 2) * 4 + 1
        chunk = vin_b[start:start + 4]
        if not any(b != '00' for b in chunk):
            break
        lines.append(make_iso_res(tgt, ['49', '02', f'{seq:02X}'] + chunk,
                                   ecu=ecu, suffix=_CS_SUFFIX))
    return lines


def build_iso_obd2_section(
    vin: str,
    tgt: str = '6A',
    src: str = _DEFAULT_SRC,
    ecu: str = _ISO_DEFAULT_ECU,
    five_baud_addr: str = '33',
) -> list[str]:
    """
    ISO9141 OBD2 section: 5-baud init → Mode 01/03/07/04 → Mode 09 VIN.

    Parameters
    ----------
    tgt            : request target (TagAddr from profile, e.g. '6A').
    five_baud_addr : 5-baud init address from profile FiveBaud field.
    ecu            : ECU source address in responses (default '0D').
    """
    def req(d):
        return make_iso_req(tgt, src, d)
    def res(d):
        return make_iso_res(tgt, d, ecu=ecu)

    lines = ['', '//---------------------------------OBD 2', '']

    lines.append('//5-baud init')
    lines.extend(build_iso_init(five_baud_addr))
    lines.append('')

    lines += [
        '//Mode 01 - supported PIDs',
        req(['01', '00']),
        res(['41', '00', 'BE', '3E', 'B8', '10']),
        '',
        '//---------------------------------Mode 03: stored DTCs',
        req(['03']),
        res(['43', '00', '00', '00', '00', '00', '00']),
        '',
        '//---------------------------------Mode 07: pending DTCs',
        req(['07']),
        res(['47', '00', '00', '00', '00', '00', '00']),
        '',
        '//---------------------------------Mode 04: erase DTCs',
        req(['04']),
        res(['44']),
        '',
        '//---------------------------------Mode 09 PID 02: VIN',
    ]
    lines.extend(_vin_frames(vin, tgt, src, ecu))
    return lines


# ---------------------------------------------------------------------------
# NWS init section
# ---------------------------------------------------------------------------

def build_iso_nws_init_lines(
    nws_config: pd.DataFrame,
    profile_df: pd.DataFrame,
    ecu: str = _ISO_DEFAULT_ECU,
) -> list[str]:
    """NWS init Req/Res for every unique PROTOCOL_ISO9141 profile in nws_config."""
    lines:         list[str] = []
    seen_profiles: set[str]  = set()

    for system, group in nws_config.groupby(
            nws_config['System'].fillna('Unknown'), sort=False):
        lines.append('')
        lines.append(f'//System: {system} - NWS init (ISO9141)')

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
                logger.warning('[E105] No NWS profile match for %s (ISO init)', profile)
                continue

            tgt = str(prow.get('TagAddr/CanReq1', '')).strip()
            src = str(prow.get('SourceAddr', _DEFAULT_SRC)).strip()
            if src in _NAN:
                src = _DEFAULT_SRC
            if not tgt or tgt in _NAN:
                tgt = '6A'

            for data in _split_cmds(prow.get('CMD Query', '')):
                lines.append(make_iso_req(tgt, src, data))
                lines.append(make_iso_res(tgt, _positive_resp(data),
                                          ecu=ecu, suffix=_CS_SUFFIX))

            for data in _split_cmds(prow.get('CMD KeepAlive', '')):
                lines.append(make_iso_req(tgt, src, data))
                lines.append(make_iso_res(tgt, _positive_resp(data),
                                          ecu=ecu, suffix=_CS_SUFFIX))

    return lines


# ---------------------------------------------------------------------------
# DTC section
# ---------------------------------------------------------------------------

def build_iso_dtc_lines(
    nws_config: pd.DataFrame,
    profile_df: pd.DataFrame,
    ecu: str = _ISO_DEFAULT_ECU,
) -> list[str]:
    """DTC Req/Res for every (System, Profile) group with PROTOCOL_ISO9141."""
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
            logger.warning('[E105] No NWS profile match for %s (ISO DTC)', profile)
            continue

        tgt = str(prow.get('TagAddr/CanReq1', '')).strip()
        src = str(prow.get('SourceAddr', _DEFAULT_SRC)).strip()
        if src in _NAN:
            src = _DEFAULT_SRC
        if not tgt or tgt in _NAN:
            tgt = '6A'

        dtc_cmds = _split_cmds(prow.get('CMD Read DTC', ''))
        if not dtc_cmds:
            continue

        lines.append('')
        lines.append(f'//System: {system} - DTC read (Profile={profile}, ISO9141)')
        for dtc_val, _ in dtcs:
            lines.append(f'//DTC: {dtc_val}')

        for cmd_bytes in dtc_cmds:
            lines.append(make_iso_req(tgt, src, cmd_bytes))
            try:
                svc = int(cmd_bytes[0], 16)
            except (ValueError, IndexError):
                svc = 0x03
            payload = [f'{svc | 0x40:02X}']
            if svc in (0x18, 0x13):
                payload.append(f'{len(dtcs):02X}')
            for dtc_val, status in dtcs:
                payload += _dtc_to_bytes(dtc_val)
                try:
                    payload.append(f'{int(status, 16):02X}')
                except (ValueError, TypeError):
                    payload.append('00')
            lines.append(make_iso_res(tgt, payload, ecu=ecu, suffix=_CS_SUFFIX))

    return lines


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def build_iso_system_content(
    config_path,
    profile_df: pd.DataFrame,
    ecu: str = _ISO_DEFAULT_ECU,
) -> dict[str, list[str]]:
    """Return {system: [lines]} for all ISO9141 systems in *config_path*'s NWS sheet."""
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
            build_iso_nws_init_lines(group, profile_df, ecu)
            + build_iso_dtc_lines(group, profile_df, ecu)
        )
    return content
