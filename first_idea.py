import logging
from pathlib import Path

import pandas as pd

from src.db_schema import DB_REGISTRY, DataLoader

# ── Paths ─────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path('config/Vehicle_infor.xlsx')
OUTPUT_PATH = Path('demo.sim')

# ── Protocol config written into the .sim header ──────────────────────────────

PROTOCOL_CONFIG: dict[str, str] = {
    'Protocol'     : '29',
    'BAUDRATE'     : '500000',
    'PIN_KRX_CANH' : '6',
    'PIN_KTX_CANH' : '14',
    'VOLT_KRX_CANH': '3',
    'VREF'         : '0',
    'TBYTE'        : '8',
    'TFRAME'       : '5',
    'RANGE'        : '0,0;',
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

def _build_profile_map(loader: DataLoader) -> dict[str, int]:
    """
    Return {ItemID: ecu_id_int} from the ABS_LD Profile ID sheet.

    Only rows whose Option1 is a valid hex string (CAN protocols) are included;
    K-Line / ISO9141 profiles carry a non-hex Option1 and are skipped.
    """
    profiles = loader.get('ABS_LD', 'Profile ID')
    mapping: dict[str, int] = {}
    for _, row in profiles.iterrows():
        try:
            mapping[row['ItemID']] = int(row['Option1'], 16)
        except (ValueError, TypeError):
            pass   # non-CAN protocol — Option1 is not a hex CAN ID
    return mapping


def resolve_ecu_id(item_id: str, profile_map: dict[str, int]) -> int:
    """Look up the ECU CAN ID for *item_id*; fall back to DEFAULT_ECU_ID."""
    ecu_id = profile_map.get(item_id)
    if ecu_id is None:
        logger.warning('No CAN profile for %s — using default 0x%X', item_id, DEFAULT_ECU_ID)
        return DEFAULT_ECU_ID
    return ecu_id


# ── Value encoding ────────────────────────────────────────────────────────────

def resolve_raw_value(row: pd.Series, target_val: float) -> int:
    """Algebraically invert y = a·x + b  →  x = (y − b) / a, clamped to byte range."""
    try:
        byte_size = int(row['Bytesize'])
        range_max = 1 << (byte_size * 8)

        if row['Formula'] == 'f(x)= x&a':
            return int(target_val)

        a = float(row['a'])
        b = float(row['b'])
        if a == 0:
            logger.warning('a=0 for %s — returning 0', row['ItemID'])
            return 0

        raw = round((target_val - b) / a)

        if row['Sign'] == 'Signed' and raw < 0:
            raw = range_max + raw   # two's complement

        return max(0, min(int(raw), range_max - 1))
    except Exception as exc:
        logger.warning('Value encode failed for %s: %s', row.get('ItemID', '?'), exc)
        return 0


def to_hex_bytes(raw_val: int, byte_size: int, endian: str) -> list[str]:
    """Convert an integer to a list of 2-char uppercase hex strings."""
    hex_str = format(raw_val, f'0{byte_size * 2}X')
    chunks  = [hex_str[i:i + 2] for i in range(0, len(hex_str), 2)]
    if endian == 'Low-High':
        chunks.reverse()
    return chunks


# ── ISO-TP encoding ───────────────────────────────────────────────────────────

def encode_isotp(ecu_id: int, payload: list[str]) -> list[str]:
    """Wrap a payload in ISO 15765-2 CAN frames (single-frame or multi-frame)."""
    ecu_id_str = format(ecu_id, '08X')
    total      = len(payload)
    prefix     = 'INFO_DATABASE = Res<1'
    suffix     = 'NONE\t0'
    frames: list[str] = []

    if total <= 7:
        data = [f'{total:02X}'] + payload + ['00'] * (7 - total)
        frames.append(f'{prefix}\t\t{ecu_id_str} {CAN_DLC} {" ".join(data)} \t{suffix}')
    else:
        ff_len = format(total, '03X')                          # 12-bit length
        ff = [f'1{ff_len[0]}', ff_len[1:]] + payload[:6]
        frames.append(f'{prefix}\t\t{ecu_id_str} {CAN_DLC} {" ".join(ff)} \t{suffix}')
        remaining = payload[6:]
        for i in range(0, len(remaining), 7):
            chunk = remaining[i:i + 7]
            sn    = (i // 7 + 1) % 16
            cf    = [f'2{sn:X}'] + chunk + ['00'] * (7 - len(chunk))
            frames.append(f'{prefix}\t\t{ecu_id_str} {CAN_DLC} {" ".join(cf)} \t{suffix}')

    return frames


# ── SIM file construction ─────────────────────────────────────────────────────

def build_header() -> list[str]:
    lines = [
        '#' * 45,
        '#          Auto Generated SIM File         #',
        '#' * 45,
    ]
    for key, val in PROTOCOL_CONFIG.items():
        lines.append(f'<config sw> {key} = {val}')
    lines.append('#' * 45)
    return lines


def build_sim(pids: pd.DataFrame, loader: DataLoader) -> list[str]:
    item_db     = loader.get('ABS_LD', 'Item ID')
    profile_map = _build_profile_map(loader)

    merged = pids.merge(item_db, on='ItemID', how='inner')
    if merged.empty:
        logger.error('No matching PIDs in ABS_LD database. Check ItemIDs in %s.', CONFIG_PATH)
        return build_header()

    # Attach the ECU CAN ID for each row — drives both grouping and frame encoding
    merged['ecu_id'] = merged['ItemID'].map(
        lambda iid: resolve_ecu_id(iid, profile_map)
    )

    lines = build_header()

    # Group so that all PIDs sharing the same request command AND the same ECU
    # are packed into one combined ISO-TP response
    for (cmd, ecu_id), group in merged.groupby(['GetValueCmd', 'ecu_id']):
        if cmd in ('nan', '', 'NaN'):
            logger.warning('Skipping %d rows with no GetValueCmd', len(group))
            continue

        try:
            total_bytes = int(group.iloc[0]['TotalDataSize'])
        except ValueError:
            logger.warning('Invalid TotalDataSize for cmd %s — skipping', cmd)
            continue

        payload_buffer: list[str] = ['00'] * total_bytes
        note_lines:     list[str] = []

        for _, row in group.iterrows():
            try:
                target_val = float(row['Value'])
                raw_val    = resolve_raw_value(row, target_val)
                byte_size  = int(row['Bytesize'])
                endian     = row['Endian'] if row['Endian'] not in ('nan', '', 'NaN') else 'High-Low'
                hex_bytes  = to_hex_bytes(raw_val, byte_size, endian)
                pos        = int(row['BytePosition'])

                for j, byte_val in enumerate(hex_bytes):
                    if pos + j < total_bytes:
                        payload_buffer[pos + j] = byte_val
                    else:
                        logger.warning('BytePos %d out of range for %s', pos + j, row['ItemID'])

                note_lines.append(
                    f'//Note: {row["ItemName"]} | PID: {row["ItemID"]} | Value: {row["Value"]}'
                )
                logger.info(
                    'Encoded  %-70s = %6.2f → raw %5d @ byte %3d  ECU 0x%X',
                    row['ItemID'], target_val, raw_val, pos, ecu_id,
                )
            except Exception as exc:
                logger.warning('Skipping %s: %s', row.get('ItemID', '?'), exc)

        ecu_id_str = format(int(ecu_id), '08X')
        lines.append('')
        lines.extend(note_lines)
        lines.append(f'INFO_DATABASE = Req>1\t\t{ecu_id_str} {CAN_DLC} {cmd} \t4\t0')
        lines.extend(encode_isotp(int(ecu_id), payload_buffer))

    return lines


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    logger.info('Config  : %s', CONFIG_PATH)
    logger.info('Sources : %s', DB_REGISTRY.keys())

    pids   = pd.read_excel(CONFIG_PATH, sheet_name='PIDs').astype(str)
    VIN_YMME   = pd.read_excel(CONFIG_PATH, sheet_name='VIN_YMME').astype(str)
    NWS   = pd.read_excel(CONFIG_PATH, sheet_name='NWS').astype(str)
    loader = DataLoader()

    logger.info('Loaded %d PID(s) from config', len(pids))

    lines = build_sim(pids, loader)
    OUTPUT_PATH.write_text('\n'.join(lines), encoding='utf-8')
    logger.info('Written %d lines → %s', len(lines), OUTPUT_PATH)


if __name__ == '__main__':
    main()
