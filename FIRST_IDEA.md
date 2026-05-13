# CAN Simulation File Generator (`first_idea.py`)

Generates `.sim` files for Hyundai ABS diagnostic simulation using UDS over CAN
(ISO 14229 / ISO 15765-2 ISO-TP).

---

## Project Structure

```
CreateSimulationfile/
├── first_idea.py            # main entry point
├── src/
│   ├── __init__.py
│   ├── db_schema.py         # database registry & DataLoader library
│   └── Protocol_CAN_CANUSD.py
├── config/
│   └── Vehicle_infor.xlsx   # user input: desired PID values
├── data/
│   ├── Hyundai_ABS_LD_*.xlsx
│   ├── Hyundai_NWScan_*.xlsx
│   └── Hyundai_DTCDatabase_*.xlsx
├── logs/
│   └── create_sim.log
└── demo.sim                 # generated output
```

---

## Requirements

| Package  | Version |
|----------|---------|
| Python   | ≥ 3.11  |
| pandas   | ≥ 3.0   |
| openpyxl | ≥ 3.1   |

```bash
pip install pandas openpyxl
```

---

## Running

```bash
python first_idea.py
```

Logs go to both the console and `logs/create_sim.log`.

---

## Input Files

### `config/Vehicle_infor.xlsx` — sheet `PIDs`

| Column | Type   | Description                          |
|--------|--------|--------------------------------------|
| ItemID | string | PID key matching the ABS DB          |
| Value  | number | Desired physical value to simulate   |

### `data/Hyundai_ABS_LD_*.xlsx`

**Sheet `Item ID`** (used columns):

| Column        | Description                                            |
|---------------|--------------------------------------------------------|
| ItemID        | Unique PID key                                         |
| ItemName      | Human-readable name                                    |
| GetValueCmd   | 8-byte CAN request payload (hex, space-separated)      |
| TotalDataSize | Total response payload length in bytes                 |
| BytePosition  | Byte offset of this parameter within the response      |
| Bytesize      | Number of bytes this parameter occupies                |
| Endian        | `High-Low` (big-endian) or `Low-High` (little-endian)  |
| Formula       | `f(x)= a*x+b` or `f(x)= x&a`                          |
| Sign          | `Unsigned` or `Signed`                                 |
| a, b          | Linear formula coefficients                            |
| Floating      | Decimal places for rounding                            |

**Sheet `Profile ID`** (used columns):

| Column                | Description                          |
|-----------------------|--------------------------------------|
| MsgID/ECUID/ProfileID | Profile identifier                   |
| Protocol              | e.g. `PROTOCOL_CAN_UDS`              |
| Option1               | **CAN arbitration ID** (hex string)  |
| ItemID                | Links items to this profile          |

The ECU CAN ID is resolved per-PID from this sheet at runtime — it is **not hardcoded**.

---

## Database Library — `src/db_schema.py`

The library defines every database file and sheet the project can read.
It provides two data-model classes and a `DataLoader` that caches results.

### `SheetSpec`

```python
@dataclass
class SheetSpec:
    cols: list[str] | None   # column whitelist; None = load all
    skiprows: list[int]      # rows to skip (merged headers, etc.)
```

### `DataSource`

```python
@dataclass
class DataSource:
    path: Path
    sheets: dict[str, SheetSpec]
```

### `DB_REGISTRY`

```python
DB_REGISTRY: dict[str, DataSource] = {
    'ABS_LD': DataSource(path=..., sheets={'Item ID': ..., 'Profile ID': ...}),
    'NWS':    DataSource(path=..., sheets={'Ymme': ...,    'Profile': ...}),
    'DTC':    DataSource(path=..., sheets={'DTC': ...}),
}
```

### `DataLoader`

```python
loader = DataLoader()                        # uses DB_REGISTRY by default
df     = loader.get('ABS_LD', 'Item ID')    # loads & caches on first call
df2    = loader.get('NWS', 'Profile')        # loads a different source
```

| Method                         | Description                          |
|--------------------------------|--------------------------------------|
| `get(source, sheet)`           | Return (cached) DataFrame            |
| `sources()`                    | List all registered source keys      |
| `sheets(source)`               | List all sheet names for a source    |
| `clear_cache()`                | Force reload on next `get()` call    |

### Adding a new database

1. Add column list(s) in `src/db_schema.py`:
   ```python
   _MY_COLS = ['ColA', 'ColB', ...]
   ```
2. Add an entry to `DB_REGISTRY`:
   ```python
   'MY_DB': DataSource(
       path=Path('data/my_new_database.xlsx'),
       sheets={
           'Sheet1': SheetSpec(cols=_MY_COLS, skiprows=[0, 2, 3]),
       },
   ),
   ```
3. Consume it anywhere:
   ```python
   loader.get('MY_DB', 'Sheet1')
   ```

---

## How `first_idea.py` Works

### 1. Load inputs

```python
pids   = pd.read_excel('config/Vehicle_infor.xlsx', sheet_name='PIDs')
loader = DataLoader()
```

### 2. Resolve ECU IDs (dynamic)

```python
profile_map = _build_profile_map(loader)
# → {ItemID: ecu_id_int, ...}  sourced from ABS_LD / Profile ID
```

Each `ItemID` is looked up in the `Profile ID` sheet.  
`Option1` (e.g. `'7D9'`) is parsed as a hex integer → `0x7D9`.  
Non-CAN protocols (K-Line, ISO9141) have a non-hex `Option1` and are skipped automatically.

### 3. Merge and attach ECU ID

```python
merged = pids.merge(item_db, on='ItemID', how='inner')
merged['ecu_id'] = merged['ItemID'].map(lambda iid: resolve_ecu_id(iid, profile_map))
```

### 4. Group by `(GetValueCmd, ecu_id)`

PIDs that share the same diagnostic request **and the same ECU** are packed into one combined ISO-TP response. PIDs targeting different ECUs produce separate request/response pairs.

### 5. Encode values

```
physical value  ──►  resolve_raw_value()  ──►  raw integer  ──►  to_hex_bytes()  ──►  hex list
```

Formula inversion (`f(x)= a*x+b`):

```
raw = round((target − b) / a)
```

Signed two's complement (negative values):

```
raw = 2^(byte_size × 8) + raw
```

Result is clamped to `[0, 2^(byte_size × 8) − 1]`.

### 6. Build ISO-TP response frames

`encode_isotp()` wraps the payload following ISO 15765-2:

| Payload | Output                                    |
|---------|-------------------------------------------|
| ≤ 7 B   | 1 Single Frame (SF)                       |
| > 7 B   | 1 First Frame + N Consecutive Frames (CF) |

First Frame: `1x LL d0 d1 d2 d3 d4 d5`  
Consecutive Frame: `2N d0 d1 d2 d3 d4 d5 d6` (SN wraps 1 → F)

---

## Output Format (`demo.sim`)

```
#############################################
#          Auto Generated SIM File         #
#############################################
<config sw> Protocol = 29
<config sw> BAUDRATE = 500000
...
#############################################

//Note: Wheel Speed Sensor-Left Front | PID: eLDID_0001_... | Value: 20
//Note: Wheel Speed Sensor-Right Front | PID: eLDID_0002_... | Value: 21
//Note: Wheel Speed Sensor-Left Rear | PID: eLDID_0003_... | Value: 22
//Note: Wheel Speed Sensor-Right Rear | PID: eLDID_0004_... | Value: 23
INFO_DATABASE = Req>1		000007D9 08 03 22 01 04 00 00 00 00 	4	0
INFO_DATABASE = Res<1		000007D9 08 10 2A 00 00 00 00 00 00 	NONE	0
INFO_DATABASE = Res<1		000007D9 08 21 00 00 00 00 00 00 00 	NONE	0
INFO_DATABASE = Res<1		000007D9 08 22 00 14 15 16 17 00 00 	NONE	0
...
```

`0x14 0x15 0x16 0x17` at bytes 14–17 in CF2 = wheel speeds 20, 21, 22, 23 km/h.  
`000007D9` was resolved dynamically from the Profile ID sheet, not hardcoded.

---

## Limitations

- Only `f(x)= a*x+b` and `f(x)= x&a` formulas are supported. Lookup-table (`TableID`) parameters are not yet handled.
- `NWS` and `DTC` sources are registered in `DB_REGISTRY` but their sim-generation logic is not yet implemented in `first_idea.py` — the schema is ready for expansion.
- The UDS service response header bytes (`0x62`, DID bytes) are left as `00` in the payload buffer; the simulation tool is expected to interpret the payload in context.
