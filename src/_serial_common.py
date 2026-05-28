"""
_serial_common.py — Shared constants and utilities for serial protocol builders
(KW and ISO9141).  Not intended for direct use by external callers.
"""

from __future__ import annotations

import re

import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PIN_RE = re.compile(r'\d+')

_VOLT_MAP: dict[str, str] = {
    'LEVEL_12V':   '3',
    'LEVEL_5V':    '1',
    'LEVEL_FLOAT': '0',
    'LEVEL_OPEN':  '0',
}

_NAN            = {'', 'nan', 'NaN'}
_DEFAULT_SRC    = 'F1'        # tester source address
_BROADCAST_TGT  = '33'       # OBD2 functional broadcast target
_ISO_DEFAULT_ECU = '0D'      # Hyundai/Kia ISO9141 ECU address default

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pin_number(pin_str: str) -> str:
    """'PIN7' → '7', 'PIN_NA' → '0'."""
    m = _PIN_RE.search(str(pin_str))
    return m.group() if m else '0'


def _volt_code(volt_str: str) -> str:
    return _VOLT_MAP.get(str(volt_str).strip(), '3')


def _positive_resp(data_bytes: list[str]) -> list[str]:
    """KWP2000: positive response SID = request SID | 0x40 (always)."""
    if not data_bytes:
        return ['00']
    try:
        return [f'{int(data_bytes[0], 16) | 0x40:02X}'] + data_bytes[1:]
    except ValueError:
        return ['00']


def _fmt_line(direction: str, frame_bytes: list[str], suffix: str) -> str:
    payload = ' '.join(b.upper() for b in frame_bytes)
    return f'INFO_DATABASE = {direction}\t\t\t{payload}\t{suffix}'


def _split_cmds(raw: object) -> list[list[str]]:
    """
    Parse a NWS profile command field into a list of byte lists.
    Strips :Label annotations (e.g. ':Active', ':Pending').

    '18 FF 00:Active\\n18 01 FF 00:History'
      → [['18','FF','00'], ['18','01','FF','00']]
    """
    if raw is None:
        return []
    text = str(raw).strip()
    if not text or text in _NAN:
        return []
    result = []
    for line in re.split(r'[\r\n]+', text):
        clean = line.split(':', 1)[0].strip()
        if clean:
            bytes_ = [b.strip() for b in clean.split() if b.strip()]
            if bytes_:
                result.append(bytes_)
    return result


def _dtc_to_bytes(dtc: str) -> list[str]:
    """Convert DTC string (e.g. 'P0123') to [hi_byte, lo_byte] hex strings."""
    dtc = dtc.strip().upper()
    if len(dtc) < 5:
        return ['00', '00']
    prefix = {'P': 0x00, 'C': 0x40, 'B': 0x80, 'U': 0xC0}.get(dtc[0], 0x00)
    try:
        code  = int(dtc[1:5], 16)
        byte1 = prefix | ((code >> 8) & 0x3F)
        byte2 = code & 0xFF
        return [f'{byte1:02X}', f'{byte2:02X}']
    except ValueError:
        return ['00', '00']


def _resolve_profile(profile: str, profile_df: pd.DataFrame) -> pd.Series | None:
    """Return the first matching NWS Profile row (OBD connector preferred)."""
    matches = profile_df[profile_df['MsgID/ECUID'] == profile]
    if len(matches) > 1:
        obd = matches[matches['Connector'].str.contains('OBD', case=False, na=False)]
        if not obd.empty:
            return obd.iloc[0]
    return matches.iloc[0] if not matches.empty else None


def _header_block(
    proto_num: str,
    rx_pin: str,
    tx_pin: str,
    rx_volt: str,
    tx_volt: str,
    baudrate: str,
    tbyte: str,
    tframe: str,
    n_frames: str,
    range_val: str,
) -> list[str]:
    """Assemble the <config sw> header block from pre-computed values."""
    return [
        '###########################################',
        '#         Auto Generated                  #',
        '###########################################',
        f'<config sw> Protocol = {proto_num}',
        f'<config sw> PIN_KRX_CANH = {rx_pin}',
        f'<config sw> TYPE_KRX_CANH = 0',
        f'<config sw> VOLT_KRX_CANH = {rx_volt}',
        f'<config sw> PIN_KTX_CANH = {tx_pin}',
        f'<config sw> TYPE_KTX_CANH = 0',
        f'<config sw> VOLT_KTX_CANH = {tx_volt}',
        f'<config sw> PIN_LRX_CANH =  {rx_pin}',
        f'<config sw> TYPE_LTX_CANH = 0',
        f'<config sw> VOLT_LTX_CANH = {tx_volt}',
        f'<config sw> VREF = 0',
        f'<config sw> BAUDRATE = {baudrate}',
        f'<config sw> DATABIT = 0',
        f'<config sw> PARITY = 0',
        f'<config sw> TBYTE = {tbyte}',
        f'<config sw> TFRAME = {tframe}',
        f'<config sw> F CAN NUMBER FRAME = {n_frames}',
        f'<config sw> RANGE ={range_val}',
        '###########################################',
        '#         End of config                   #',
        '###########################################',
    ]


def _profile_pin_baudrate(profile_row: pd.Series) -> tuple[str, str, str, str, str]:
    """Return (rx_pin, tx_pin, rx_volt, tx_volt, baudrate) from a profile row."""
    rx_pin  = _pin_number(str(profile_row.get('CanH/Rx',    '')))
    tx_pin  = _pin_number(str(profile_row.get('CanL/Tx',    '')))
    rx_volt = _volt_code(str(profile_row.get('CanH/RxVolt', 'LEVEL_12V')))
    tx_volt = _volt_code(str(profile_row.get('CanL/TxVolt', 'LEVEL_12V')))
    try:
        baudrate = str(int(float(str(profile_row.get('Baudrate', '10400')))))
    except (ValueError, TypeError):
        baudrate = '10400'
    return rx_pin, tx_pin, rx_volt, tx_volt, baudrate
