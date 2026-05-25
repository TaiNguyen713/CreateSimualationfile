import logging
import re
from pathlib import Path
import pandas as pd
from src.db_schema import DataLoader

# ── Paths ─────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path('config/Vehicle_infor.xlsx')   # legacy single-config fallback
CONFIG_DIR  = Path('config/auto_configs')          # folder of batch-generated config files
OUTPUT_DIR  = Path('output')
OUTPUT_PATH = Path('demo.sim')

# ── Protocol config written into the .sim header ──────────────────────────────

PROTOCOL_CONFIG: dict[str, str] = {
    'Protocol'           : '29',
    'PIN_KRX_CANH'       : '6',
    'TYPE_KRX_CANH'      : '0',
    'VOLT_KRX_CANH'      : '3',
    'PIN_KTX_CANH'       : '14',
    'TYPE_KTX_CANH'      : '0',
    'VOLT_KTX_CANH'      : '3',
    'PIN_LRX_CANH'       : ' 6',
    'TYPE_LTX_CANH'      : '0',
    'VOLT_LTX_CANH'      : '3',
    'VREF'               : '0',
    'BAUDRATE'           : '500000',
    'DATABIT'            : '0',
    'PARITY'             : '0',
    'TBYTE'              : '3',
    'TFRAME'             : '5',
    'F CAN NUMBER FRAME' : '1',
    'RANGE'              : ' 500,7FF;',
}

CAN_DLC         = '08'
DEFAULT_ECU_ID  = 0x7D9   # fallback when a PID has no profile entry

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / 'create_sim.log', encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)


# ── ECU ID resolution ─────────────────────────────────────────────────────────


def _build_system_id_map(nws_config: pd.DataFrame, profile_df: pd.DataFrame) -> dict[str, tuple[str, str]]:
    """Return {system: (req_can_id, res_can_id)} by resolving the first NWS profile per system.

    Prefers OBD connector rows when multiple profile rows match the same MsgID/ECUID.
    Used to look up CAN request/response IDs for both NWS init and Live Data sections.
    """
    mapping: dict[str, tuple[str, str]] = {}
    for _, row in nws_config.iterrows():
        system = clean_system(row.get('System', 'Unknown'))
        if system in mapping:
            continue
        profile = str(row.get('Profile', '')).strip()
        if not profile or profile in ('nan', 'NaN'):
            continue
        matches = profile_df[profile_df['MsgID/ECUID'] == profile]
        if len(matches) > 1:
            filtered = matches[matches['Connector'].str.contains('OBD', case=False, na=False)]
            if not filtered.empty:
                matches = filtered
        if matches.empty:
            continue
        profile_row = matches.iloc[0]
        mapping[system] = (
            format_can_id(profile_row['TagAddr/CanReq1']),
            format_can_id(profile_row['CanStart1']),
        )
    return mapping


def resolve_ecu_id(profile_id: str, profile_map: dict[str, int]) -> int:
    """Return the CAN request address for *profile_id* from profile_map, or DEFAULT_ECU_ID."""
    ecu_id = profile_map.get(profile_id)
    if ecu_id is None:
        logger.warning('[E101] No CAN profile for %s — using default 0x%X', profile_id, DEFAULT_ECU_ID)
        return DEFAULT_ECU_ID
    return ecu_id


# ── Value encoding ────────────────────────────────────────────────────────────

def resolve_raw_value(row: pd.Series, target_val: float) -> int:
    """Algebraically invert y = a·x + b  →  x = (y − b) / a, clamped to byte range.

    Sign='Signed'  → clip to two's-complement range, then encode negative as range_max+x.
    Sign='Unsigned' / missing → clip to [0, range_max-1].
    Endian is handled downstream by to_hex_bytes().
    """
    try:
        byte_size  = int(float(row['Bytesize']))
        range_max  = 1 << (byte_size * 8)
        sign       = str(row.get('Sign', 'nan')).strip()
        is_signed  = sign.lower() == 'signed'

        if str(row.get('Formula', '')).strip() == 'f(x)= x&a':
            raw = int(target_val)
            if is_signed:
                raw = max(-(range_max // 2), min(raw, range_max // 2 - 1))
                if raw < 0:
                    raw = range_max + raw
            else:
                raw = max(0, min(raw, range_max - 1))
            return raw

        a = float(row['a'])
        b = float(row['b'])
        if a == 0:
            logger.warning('[E102] a=0 for %s — returning 0', row['ItemID'])
            return 0

        raw = round((target_val - b) / a)

        if is_signed:
            # Clip to signed range first, then two's-complement encode negatives.
            raw = max(-(range_max // 2), min(raw, range_max // 2 - 1))
            if raw < 0:
                raw = range_max + raw
        else:
            raw = max(0, min(raw, range_max - 1))

        return int(raw)
    except Exception as exc:
        logger.warning('[E103] Value encode failed for %s: %s', row.get('ItemID', '?'), exc)
        return 0


def to_hex_bytes(raw_val: int, byte_size: int, endian: str) -> list[str]:
    """Convert an integer to a list of 2-char uppercase hex strings."""
    hex_str = format(raw_val, f'0{byte_size * 2}X')
    chunks  = [hex_str[i:i + 2] for i in range(0, len(hex_str), 2)]
    if endian == 'Low-High':
        chunks.reverse()
    return chunks


def encode_isotp(res_id: str, payload: list[str], req_id: str | None = None) -> list[str]:
    """Encode a payload list into ISO-TP CAN frames (Res<1 lines).

    For multi-frame responses, inserts a flow-control Req>1 (Q--) after the first frame.
    """
    total_len = len(payload)
    suffix    = 'NONE\t0'
    frames: list[str] = []

    if total_len <= 7:
        frame = [f'{total_len:02X}'] + payload + ['00'] * (7 - total_len)
        frames.append(f'INFO_DATABASE = Res<1\t\t\t{res_id} 08 {" ".join(frame)} \t{suffix}')
    else:
        ff_len = f'{total_len:03X}'
        ff = ['1' + ff_len[0], ff_len[1:]] + payload[:6]
        frames.append(f'INFO_DATABASE = Res<1\t\t\t{res_id} 08 {" ".join(ff)} \t{suffix}')
        if req_id is not None:
            frames.append(f'INFO_DATABASE = Req>1\t\t\tQ-- {req_id} 08 30 xx xx 00 00 00 00 00 \t{suffix}')
        remaining = payload[6:]
        for i in range(0, len(remaining), 7):
            chunk = remaining[i:i + 7]
            idx   = (i // 7 + 1) % 16
            cf    = [f'2{idx:X}'] + chunk + ['00'] * (7 - len(chunk))
            frames.append(f'INFO_DATABASE = Res<1\t\t\t{res_id} 08 {" ".join(cf)} \t{suffix}')

    return frames


# ── DTC helpers ───────────────────────────────────────────────────────────────

def dtc_to_bytes(dtc: str) -> list[str]:
    """Convert a DTC string to hex bytes (ISO 15031-6).

    5-digit (P0115)   → 2 bytes [DTC_hi, DTC_lo]
    7-digit (P011511) → 3 bytes [DTC_hi, DTC_lo, symptom_byte]

    Encoding:
      Byte1 bits[7:6] = system letter  P=00 C=01 B=10 U=11
      Byte1 bits[5:4] = 2nd digit (0-3)
      Byte1 bits[3:0] = 3rd digit
      Byte2           = 4th+5th digits (one hex byte)
      Byte3 (optional)= symptom digits 6-7 as hex byte
    """
    dtc = dtc.strip().upper()
    if len(dtc) < 5:
        return ['00', '00']
    prefix = {'P': 0x00, 'C': 0x40, 'B': 0x80, 'U': 0xC0}.get(dtc[0], 0x00)
    try:
        code  = int(dtc[1:5], 16)           # digits 2-5 as 4-char hex
        byte1 = prefix | ((code >> 8) & 0x3F)
        byte2 = code & 0xFF
        result = [format(byte1, '02X'), format(byte2, '02X')]
        if len(dtc) >= 7:                    # 7-digit extended DTC
            symptom = int(dtc[5:7], 16)
            result.append(format(symptom, '02X'))
        return result
    except ValueError:
        logger.warning('Cannot parse DTC %r', dtc)
        return ['00', '00']


def _classify_dtc_cmd(parts: list[str]) -> str:
    """Return 'active' (mask=89), 'history' (mask=08), or 'other' from a normalized cmd."""
    if len(parts) >= 4:
        mask = parts[3].upper()
        if mask == '89':
            return 'active'
        if mask == '08':
            return 'history'
    return 'other'


def _dtc_status_byte(status_col: str, cmd_class: str) -> str:
    """Return the 2-char hex status byte for a DTC entry.

    active/history overrides come from the command mask; 'other' uses the Status column (hex).
    """
    if cmd_class == 'active':
        return '89'
    if cmd_class == 'history':
        return '08'
    try:
        return format(int(status_col, 16), '02X')
    except ValueError:
        try:
            return format(int(float(status_col)), '02X')
        except Exception:
            return '00'


def build_dtc_lines(nws_config: pd.DataFrame, nws_profile: pd.DataFrame,
                    system_id_map: dict[str, tuple[str, str]]) -> list[str]:
    """Generate simulated DTC read Req/Res frames for every (System, Profile) group.

    Response format determined by service byte in CMD Read DTC:
      0x18 (KWP2000): [58, numDTCs, {DTC_hi, DTC_lo, status} × n]
      0x19 (UDS):     [59, subfunc, FF, {DTC_hi, DTC_lo, status} × n]
    Status byte: 0x89 if mask=89 (active), 0x08 if mask=08 (history), else Status column (hex).
    """
    lines: list[str] = []
    _nan = {'nan', 'NaN', ''}

    for (system, profile), grp in nws_config.groupby(
            [nws_config['System'].fillna('Unknown'), nws_config['Profile'].fillna('nan')],
            sort=False):
        system  = clean_system(system)
        profile = str(profile).strip()
        if not profile or profile in _nan:
            continue

        matches = nws_profile[nws_profile['MsgID/ECUID'] == profile]
        if len(matches) > 1:
            filtered = matches[matches['Connector'].str.contains('OBD', case=False, na=False)]
            if not filtered.empty:
                matches = filtered
        if matches.empty:
            logger.warning('[E105] No NWS profile match for %s (DTC section)', profile)
            continue
        prow = matches.iloc[0]

        req_id = format_can_id(prow['TagAddr/CanReq1'])
        res_id = format_can_id(prow['CanStart1'])

        cmd_raw = str(prow.get('CMD Read DTC', 'nan')).strip()
        if cmd_raw in _nan:
            continue
        dtc_cmds = split_command_list(cmd_raw)
        if not dtc_cmds:
            continue

        # Offset = extra header bytes between service response byte and DTC entries
        try:
            offset = int(float(str(prow.get('Offset', '0')).strip()))
        except (ValueError, TypeError):
            offset = 0

        # Collect DTCs from config rows
        dtcs: list[tuple[str, str]] = []
        for _, row in grp.iterrows():
            dtc_val    = str(row.get('DTC',    'nan')).strip()
            status_val = str(row.get('Status', 'nan')).strip()
            if dtc_val not in _nan:
                dtcs.append((dtc_val, status_val))
        if not dtcs:
            continue

        lines.append('')
        lines.append(f'//System: {system} — DTC Read (Profile={profile})')
        for dtc_val, status_val in dtcs:
            lines.append(f'//DTC: {dtc_val}  Status: {status_val}')

        for cmd in dtc_cmds:
            parts     = cmd.split()
            cmd_class = _classify_dtc_cmd(parts)
            try:
                service = int(parts[1], 16) if len(parts) > 1 else 0x18
            except ValueError:
                service = 0x18

            resp_svc = format(service + 0x40, '02X')
            payload: list[str] = [resp_svc]

            if service == 0x19:
                # UDS ReadDTCInformation: [59, subfunc, availMask, {DTC entries}]
                subfunc = parts[2] if len(parts) > 2 else '02'
                payload += [subfunc, 'FF']
            elif service == 0x18:
                # KWP2000 ReadDTCByStatus: [58, numDTCs, {DTC entries}]
                payload.append(format(len(dtcs), '02X'))
            else:
                # Generic: fill Offset extra bytes as 00
                payload += ['00'] * offset

            for dtc_val, status_val in dtcs:
                payload += dtc_to_bytes(dtc_val)
                payload.append(_dtc_status_byte(status_val, cmd_class))

            lines.append(make_fixed_frame('Req>1', req_id, cmd, 'NONE\t0'))
            lines.extend(encode_isotp(res_id, payload, req_id))

    return lines


def positive_response_service(cmd: str) -> str | None:
    parts = cmd.split()
    if len(parts) < 2:
        return None
    try:
        service = int(parts[1], 16)
    except ValueError:
        return None

    if 0x00 <= service <= 0x3F:
        return format(service + 0x40, '02X')
    return format(service, '02X')


def clean_system(value: object) -> str:
    system = str(value).strip()
    if system in ('nan', 'NaN', ''):
        return 'Unknown'
    return system


def normalize_payload(cmd: str) -> str:
    parts = [part for part in cmd.strip().split() if part]
    if not parts:
        return '00 00 00 00 00 00 00 00'
    if len(parts) > 8:
        parts = parts[:8]
    return ' '.join(parts + ['00'] * (8 - len(parts)))


def split_command_list(value: object) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text or text in ('nan', 'NaN'):
        return []

    commands: list[str] = []
    for item in re.split(r'[\r\n]+', text):
        clean = item.split(':', 1)[0].strip()
        if clean:
            commands.append(normalize_payload(clean))
    return commands


def format_can_id(raw_id: object) -> str:
    raw = str(raw_id).strip()
    if raw in ('nan', 'NaN', '', 'None'):
        return '00000000'
    try:
        return format(int(raw, 16), '08X')
    except ValueError:
        try:
            return format(int(float(raw)), '08X')
        except Exception:
            return '00000000'


def positive_response_payload(cmd: str) -> str:
    parts = cmd.split()
    if len(parts) < 2:
        return normalize_payload(cmd)
    try:
        service = int(parts[1], 16)
    except ValueError:
        return normalize_payload(cmd)

    if 0x00 <= service <= 0x3F:
        parts[1] = format(service + 0x40, '02X')
    return normalize_payload(' '.join(parts))


def make_fixed_frame(direction: str, can_id: str, payload: str, suffix: str = 'NONE\t0\t0') -> str:
    return f'INFO_DATABASE = {direction}\t\t\t{can_id} {CAN_DLC} {payload} \t{suffix}'


def build_header() -> list[str]:
    lines = [
        '###########################################',
        '#         Auto Generated                  #',
        '###########################################',
    ]
    for key, val in PROTOCOL_CONFIG.items():
        lines.append(f'<config sw> {key} = {val}')
    lines += [
        '###########################################',
        '#         End of config                   #',
        '###########################################',
    ]
    return lines


def build_nws_init_lines(nws_config: pd.DataFrame, profile_df: pd.DataFrame) -> list[str]:
    lines: list[str] = []
    if nws_config.empty:
        return lines

    for system, group in nws_config.groupby(nws_config['System'].fillna('Unknown')):
        system = clean_system(system)
        lines.append('')
        lines.append(f'//System: {system} — NWS initialization')

        seen_profiles: set[str] = set()

        for _, row in group.iterrows():
            profile = str(row.get('Profile', '')).strip()
            if not profile or profile in ('nan', 'NaN'):
                logger.warning('[E104] Skipping NWS row with missing Profile for system %s', system)
                continue

            lines.append(f'//NOTE: System={system} Profile={profile} DTC={row.get("DTC", "")} Status={row.get("Status", "")}')

            if profile in seen_profiles:
                continue
            seen_profiles.add(profile)

            matches = profile_df[profile_df['MsgID/ECUID'] == profile]
            if len(matches) > 1:
                filtered = matches[matches['Connector'].str.contains('OBD', case=False, na=False)]
                if not filtered.empty:
                    matches = filtered
            if matches.empty:
                logger.warning('[E105] No DB NWS Profile match for %s (system=%s)', profile, system)
                continue

            profile_row = matches.iloc[0]
            req_id = format_can_id(profile_row['TagAddr/CanReq1'])
            res_id = format_can_id(profile_row['CanStart1'])

            query_cmds = split_command_list(profile_row.get('CMD Query', ''))
            keepalive_cmds = split_command_list(profile_row.get('CMD KeepAlive', ''))
            if not query_cmds and not keepalive_cmds:
                logger.warning('[E106] No CMD Query or CMD KeepAlive found for %s', profile)
                continue

            for cmd in query_cmds:
                lines.append(make_fixed_frame('Req>1', req_id, cmd))
                lines.append(make_fixed_frame('Res<1', res_id, positive_response_payload(cmd)))

            for cmd in keepalive_cmds:
                lines.append(make_fixed_frame('Req>1', req_id, cmd))
                lines.append(make_fixed_frame('Res<1', res_id, positive_response_payload(cmd)))

    return lines


_SUP_SPECS: list[tuple[str, str, str, str, str]] = [
    ('SupReQ1', 'SupBytePos1', 'SupBitPos1', 'SupByteSize1', 'SupStatus1'),
    ('SupReQ2', 'SupBytePos2', 'SupBitPos2', 'SupByteSize2', 'SupStatus2'),
]


def build_live_data_lines(merged: pd.DataFrame, system_id_map: dict[str, tuple[str, str]]) -> list[str]:
    """Emit one combined Req/Res block per unique command per system.

    Support bits (SupBytePos/SupBitPos) and live data values (BytePosition) that
    share the same request command are merged into a single response buffer so the
    tool sees exactly one request/response exchange per command.
    SupBytePos is 1-indexed (byte 1 = service byte); BytePosition is 0-indexed.
    """
    lines: list[str] = []
    merged['System'] = merged['System'].fillna('Unknown').replace({'nan': 'Unknown', 'NaN': 'Unknown'})
    _nan = {'nan', 'NaN', ''}

    def _resolve_can_ids(row: pd.Series, system: str) -> tuple[str, str]:
        """Return (req_id, res_id) from Option1/Option2 with system_id_map fallback.

        In the Make_LD Profile ID sheet: Option1 = response CAN ID, Option2 = request CAN ID.
        """
        opt1 = str(row.get('Option1', 'nan')).strip()
        opt2 = str(row.get('Option2', 'nan')).strip()
        fallback = system_id_map.get(system, (format(DEFAULT_ECU_ID, '08X'), format(DEFAULT_ECU_ID, '08X')))
        req = format_can_id(opt2) if opt2 not in _nan else fallback[0]
        res = format_can_id(opt1) if opt1 not in _nan else fallback[1]
        return req, res

    for system, sys_group in merged.groupby('System'):

        # cmd_map: normalized_cmd → {req_id, res_id, ld_rows, sup_items}
        # One entry per unique request command; both LD and support data accumulate here.
        cmd_map: dict[str, dict] = {}

        def _ensure_cmd(cmd: str, row: pd.Series) -> None:
            if cmd not in cmd_map:
                req_id, res_id = _resolve_can_ids(row, system)
                cmd_map[cmd] = {'req_id': req_id, 'res_id': res_id,
                                'ld_rows': [], 'sup_items': []}

        # ── Collect live data rows ─────────────────────────────────────────────
        for _, row in sys_group.iterrows():
            cmd_raw = str(row.get('GetValueCmd', '')).strip()
            if cmd_raw in _nan:
                continue
            cmd = normalize_payload(cmd_raw)
            _ensure_cmd(cmd, row)
            cmd_map[cmd]['ld_rows'].append(row)

        # ── Collect support check items from SupReQ1 + SupReQ2 ────────────────
        for req_col, bp_col, bit_col, bs_col, status_col in _SUP_SPECS:
            if req_col not in sys_group.columns:
                continue
            for _, row in sys_group.iterrows():
                cmd_raw = str(row.get(req_col, '')).strip()
                if cmd_raw in _nan:
                    continue
                bp = str(row.get(bp_col, '')).strip()
                if bp in _nan:
                    continue
                cmd = normalize_payload(cmd_raw)
                _ensure_cmd(cmd, row)
                cmd_map[cmd]['sup_items'].append({
                    'byte_pos':  str(row.get(bp_col,     'nan')),
                    'bit_pos':   str(row.get(bit_col,    'nan')),
                    'byte_size': str(row.get(bs_col,     'nan')),
                    'status':    str(row.get(status_col, 'nan')),
                })

        # ── Build and emit one block per command ───────────────────────────────
        for cmd, info in cmd_map.items():
            ld_rows   = info['ld_rows']
            sup_items = info['sup_items']
            req_id    = info['req_id']
            res_id    = info['res_id']

            # Buffer size from live data
            ld_size = 0
            if ld_rows:
                ld_df = pd.DataFrame(ld_rows)
                valid = [
                    int(float(r['TotalDataSize']))
                    for _, r in ld_df.iterrows()
                    if str(r['TotalDataSize']).strip() not in _nan and float(r['TotalDataSize']) > 0
                ]
                if valid:
                    ld_size = max(valid)
                else:
                    try:
                        ld_size = max(
                            int(float(r['BytePosition'])) + int(float(r['Bytesize']))
                            for _, r in ld_df.iterrows()
                        )
                        logger.warning('[E108] TotalDataSize missing for cmd %s — derived %d bytes', cmd, ld_size)
                    except (ValueError, TypeError):
                        logger.warning('[E108] Invalid TotalDataSize for cmd %s — skipping', cmd)

            # Buffer size from support check.
            # SupBytePos is 0-indexed from the service byte (buffer[0]=service,
            # buffer[1]=sub-function echo, buffer[2+]=data/support bytes).
            sup_size = 0
            if sup_items:
                try:
                    sup_size = max(
                        int(float(item['byte_pos'])) + max(1, int(float(item['byte_size'])))
                        for item in sup_items
                        if str(item['byte_pos']).strip() not in ('nan', 'NaN', '')
                    )
                except (ValueError, TypeError):
                    logger.warning('[E112] Cannot determine support buffer size for %s in system %s', cmd, system)

            total_bytes = max(ld_size, sup_size)
            if total_bytes == 0:
                continue

            buf = ['00'] * total_bytes

            # buffer[0] = positive-response service byte (request_service | 0x40)
            svc = positive_response_service(cmd)
            if svc:
                buf[0] = svc

            # buffer[1] = sub-function echo (byte 2 of the request command, same in
            # both request and response per Hyundai 0x21 service convention).
            cmd_parts = cmd.split()
            if len(cmd_parts) >= 3 and len(buf) > 1:
                buf[1] = cmd_parts[2].upper()

            # Place support bits at SupBytePos (0-indexed).
            # SupStatus "1: Support\n0: Not Support": leading digit = supported bit value;
            # 1 → set the bit, 0 → buffer already 0, no action.
            for item in sup_items:
                try:
                    pos = int(float(item['byte_pos']))
                    m = re.match(r'(\d+)', str(item['status']).strip())
                    if not m or not int(m.group(1)) or pos < 0 or pos >= total_bytes:
                        continue
                    bit = int(float(item['bit_pos']))
                    buf[pos] = format(int(buf[pos], 16) | (1 << bit), '02X')
                except (ValueError, TypeError):
                    continue

            # Place live data values (BytePosition 0-indexed from service byte)
            notes: list[str] = []
            if ld_rows:
                ld_df = pd.DataFrame(ld_rows)
                for _, row in ld_df.iterrows():
                    try:
                        target_val = float(row['Value'])
                        raw_val    = resolve_raw_value(row, target_val)
                        byte_size  = int(float(row['Bytesize']))
                        endian_raw = str(row.get('Endian', 'nan')).strip()
                        endian     = endian_raw if endian_raw not in _nan else 'High-Low'
                        sign       = str(row.get('Sign', 'nan')).strip()
                        hex_bytes  = to_hex_bytes(raw_val, byte_size, endian)
                        pos        = int(float(row['BytePosition']))
                        for j, bv in enumerate(hex_bytes):
                            if pos + j < total_bytes:
                                buf[pos + j] = bv
                            else:
                                logger.warning('[E109] BytePos %d out of range for %s', pos + j, row['ItemID'])
                        notes.append(f'//NOTE: {row["ItemName"]} | PID: {row["ItemID"]} | Value: {row["Value"]}')
                        logger.info('Encoded  %-30s = %8.4f -> raw %5d  sign=%-8s endian=%-9s @ byte %3d  Profile %s',
                                    row['ItemID'], target_val, raw_val, sign, endian, pos,
                                    row.get('MsgID/ECUID/ProfileID', '?'))
                    except Exception as exc:
                        logger.warning('[E110] Skipping %s: %s', row.get('ItemID', '?'), exc)

            lines.append('')
            lines.append(f'//System: {system} — {cmd}')
            for note in notes:
                lines.append(note)
            lines.append(make_fixed_frame('Req>1', req_id, cmd, 'NONE\t0'))
            lines.extend(encode_isotp(res_id, buf, req_id))

    return lines


def build_all_system_content(
    pids: pd.DataFrame,
    loader: DataLoader,
    config_path: Path,
) -> dict[str, list[str]]:
    """Return {system_name: [lines]} for all systems in config.

    Content per system in order: NWS init → DTC read → Live Data.
    Systems with no usable output are omitted from the result.
    """
    item_db    = loader.get('Make_LD', 'Item ID')
    profile_db = loader.get('Make_LD', 'Profile ID')

    xf = pd.ExcelFile(config_path)
    nws_config = (
        pd.read_excel(config_path, sheet_name='NWS').astype(str)
        if 'NWS' in xf.sheet_names
        else pd.DataFrame(columns=['System', 'Profile', 'DTC', 'Status'])
    )
    nws_profile = loader.get('NWS', 'Profile')

    merged = pids.merge(item_db, on='ItemID', how='inner')
    if not merged.empty and 'MsgID/ECUID/ProfileID' in merged.columns:
        merged = merged.merge(
            profile_db, on=['ItemID', 'MsgID/ECUID/ProfileID'], how='left'
        )
        for col in ('Option1', 'Option2'):
            if col in merged.columns:
                merged[col] = merged[col].fillna('nan')
    merged['System'] = (
        merged['System'].fillna('Unknown').replace({'nan': 'Unknown', 'NaN': 'Unknown'})
    )

    system_id_map = _build_system_id_map(nws_config, nws_profile)

    nws_systems = {clean_system(s) for s in nws_config['System'].fillna('Unknown').unique()}
    pid_systems = {str(s) for s in merged['System'].unique()}
    all_systems = sorted(nws_systems | pid_systems)

    result: dict[str, list[str]] = {}
    for system in all_systems:
        sys_nws    = nws_config[nws_config['System'].apply(clean_system) == system].copy()
        sys_merged = merged[merged['System'] == system].copy()

        lines: list[str] = []
        if not sys_nws.empty:
            lines.extend(build_nws_init_lines(sys_nws, nws_profile))
            lines.extend(build_dtc_lines(sys_nws, nws_profile, system_id_map))
        if not sys_merged.empty:
            lines.extend(build_live_data_lines(sys_merged, system_id_map))

        if any(ln.startswith('INFO_DATABASE') for ln in lines):
            result[system] = lines

    return result


def build_sim(pids: pd.DataFrame, loader: DataLoader) -> list[str]:
    """Orchestrate the full .sim build: header → NWS init → support checks → live data."""
    item_db     = loader.get('Make_LD', 'Item ID')
    profile_db  = loader.get('Make_LD', 'Profile ID')
    nws_config  = pd.read_excel(CONFIG_PATH, sheet_name='NWS').astype(str) if 'NWS' in pd.ExcelFile(CONFIG_PATH).sheet_names else pd.DataFrame(columns=['System', 'Profile', 'DTC', 'Status'])
    nws_profile = loader.get('NWS', 'Profile')

    if 'System' not in pids.columns:
        pids['System'] = 'Unknown'

    # Step 1: item details (GetValueCmd, BytePosition, formula, …)
    merged = pids.merge(item_db, on='ItemID', how='inner')
    if merged.empty:
        logger.error('[E111] No matching PIDs in Make_LD database. Check ItemIDs in %s.', CONFIG_PATH)
        return build_header()

    # Step 2: profile details (Option1=req CAN ID, Option2=res CAN ID, SupReQ1/2, …)
    # Join on both ItemID + MsgID/ECUID/ProfileID — the Profile ID sheet has one row
    # per (item, profile) pair, so joining on ProfileID alone creates a cartesian product.
    if 'MsgID/ECUID/ProfileID' in merged.columns:
        join_keys = ['ItemID', 'MsgID/ECUID/ProfileID']
        merged = merged.merge(profile_db, on=join_keys, how='left')
        # LEFT JOIN leaves float NaN in profile columns for unmatched rows.
        # Convert to string 'nan' so groupby does not silently drop those rows.
        for col in ('Option1', 'Option2'):
            if col in merged.columns:
                merged[col] = merged[col].fillna('nan')
    else:
        logger.warning('[E113] MsgID/ECUID/ProfileID missing from PIDs config — profile CAN IDs and support check unavailable')

    merged['System'] = merged['System'].fillna('Unknown').replace({'nan': 'Unknown', 'NaN': 'Unknown'})
    system_id_map = _build_system_id_map(nws_config, nws_profile)

    lines = build_header()
    lines.extend(build_nws_init_lines(nws_config, nws_profile))
    lines.extend(build_dtc_lines(nws_config, nws_profile, system_id_map))
    lines.extend(build_live_data_lines(merged, system_id_map))
    return lines


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    from src.vin_builder import generate_vin_sim_files

    # ── Discover config files ─────────────────────────────────────────────────
    # Primary  : all .xlsx files in CONFIG_DIR (batch-generated by auto_create_config.py)
    # Fallback : single legacy CONFIG_PATH when the batch folder is empty / absent
    config_files: list[Path] = (
        sorted(CONFIG_DIR.glob('*.xlsx')) if CONFIG_DIR.exists() else []
    )
    if not config_files:
        logger.info('No configs in %s — using legacy config %s', CONFIG_DIR, CONFIG_PATH)
        config_files = [CONFIG_PATH]

    total = len(config_files)
    logger.info('Found %d config file(s) -> output dir: %s', total, OUTPUT_DIR)

    # ── Create DataLoader once; all configs share the same master databases ───
    loader = DataLoader()

    ok = failed = sim_count = 0
    for i, config_path in enumerate(config_files, 1):
        logger.info('[%d/%d] %s', i, total, config_path.name)
        try:
            n = generate_vin_sim_files(
                config_path=config_path,
                output_dir=OUTPUT_DIR,
                loader=loader,
            )
            sim_count += n
            ok += 1
        except Exception as exc:
            logger.error('FAILED %s: %s', config_path.name, exc)
            failed += 1

        if i % 100 == 0 or i == total:
            logger.info('Progress: %d/%d  (ok=%d  failed=%d  sims=%d)',
                        i, total, ok, failed, sim_count)

    logger.info('Done. %d config(s) processed — %d .sim folder(s) written, %d error(s)',
                total, sim_count, failed)


if __name__ == '__main__':
    main()
