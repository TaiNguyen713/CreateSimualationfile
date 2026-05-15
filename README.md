# Sim File Generator

Reads vehicle diagnostic configuration from an Excel file and Hyundai diagnostic databases, then generates per-vehicle `.sim` files (ECU CAN-bus simulation) and `.json` metadata files for use with a vehicle diagnostic tool.

---

## Requirements

- Python >= 3.11
- Dependencies listed in `pyproject.toml`

```bash
pip install -e .
# or with uv:
uv sync
```

---

## How to Run

### Desktop UI (recommended)

```bash
python ui_app.py
```

![UI layout]

```
┌─ Left panel ────────────────┐  ┌─ Right panel ─────────────────────────────────┐
│ Config File          [Browse]│  │ Vehicles                                       │
│                             │  │  VIN           Year  Make   Model       Engine  │
│ DB Folders                  │  │  KMHD35...     2017  Hyund  Elantra GT  G 2.0   │
│  Make_LD            [Browse]│  │                                                  │
│  NWS                [Browse]│  ├─ Log ───────────────────────────────────────────┤
│  DTC                [Browse]│  │ 14:30:00  INFO     Scanning 1 file(s)...        │
│                             │  │ 14:30:09  INFO     Loaded Make_LD / Item ID     │
│ Output Dir          [Browse]│  │ 14:31:00  DONE     Done — 1 folder(s) written   │
│                             │  │                                   [ Clear Log ] │
│ Make     [hyundai ▾]        │  └───────────────────────────────────────────────  ┘
│ Protocol [can_uds ▾]        │
│                             │
│ [ Load Vehicles ]           │
│ [ ▶ Generate .sim Files ]   │
│ ════════ progress ══════════│
└─────────────────────────────┘
```

### CLI

```bash
# Hyundai CAN-UDS (default)
python main.py

# Explicit make + protocol
python main.py --make hyundai --protocol can_uds

# List all registered protocols
python main.py --list
```

### Legacy single-file output

```bash
python first_idea.py
```

Generates `demo.sim` (flat, all systems combined) plus the per-vehicle folders.

---

## Input Files

| File | Purpose |
|------|---------|
| `config/Vehicle_infor.xlsx` | Main config — sheets: **PIDs**, **VIN\_YMME**, **NWS** |
| `data/Make_LD/` | Live-data database (Item ID + Profile ID sheets) |
| `data/NWS/` | Network-scan database (Ymme + Profile sheets) |
| `data/DTC/` | DTC database |

Each `data/<source>/` folder is scanned **recursively** for `.xlsx` files.
Files that do not match the expected schema trigger a `Skip? (y/n)` prompt (dialog in the UI, terminal prompt in CLI).

---

## Output

One folder per vehicle row in the `VIN_YMME` sheet:

```
output/
  KMHD35LHXHU384425_2017_Hyundai_Elantra_GTGD_G_2.0_GDI/
    KMHD35LHXHU384425_2017_Hyundai_Elantra_GTGD_G_2.0_GDI.sim
    KMHD35LHXHU384425_2017_Hyundai_Elantra_GTGD_G_2.0_GDI.json
```

### `.sim` structure

```
<protocol config header>
//----- OBD2 -----          ← verbatim template; only VIN bytes change
//--- ENGINE ---             ← per-system banner
  NWS init frames
  DTC read frames
  Live data frames
//--- ABS ---
  ...
```

### `.json` structure

```json
{
    "year": "2017",
    "manufacturer": "Hyundai",
    "make": "Hyundai",
    "model": "Elantra GT(GD)",
    "engine": "G 2.0 GDI",
    "SmogCheckLoc": "N/A",
    "SmogCheckCounty": "N/A"
}
```

---

## Project Structure

```
.
├── ui_app.py                    ← Desktop UI (tkinter)
├── main.py                      ← CLI entry point
├── first_idea.py                ← Core builders (NWS init, DTC, Live Data)
│
├── core/
│   ├── base_protocol.py         ← BaseProtocol ABC
│   └── protocol_factory.py      ← @register + get_protocol(make, protocol)
│
├── makes/
│   └── hyundai/
│       └── protocols/
│           └── can_uds/
│               └── protocol.py  ← HyundaiCanUdsProtocol (delegates to first_idea.py)
│
├── src/
│   ├── db_schema.py             ← DataLoader + DB_REGISTRY (folder-based)
│   └── vin_builder.py           ← OBD2 template + per-vehicle file writer
│
├── config/
│   └── Vehicle_infor.xlsx
│
└── data/
    ├── Make_LD/                 ← drop Hyundai LD xlsx files here
    ├── NWS/                     ← drop Hyundai NWS xlsx files here
    └── DTC/                     ← drop Hyundai DTC xlsx files here
```

---

## Adding a New Vehicle Make

1. Create the package skeleton:

```
makes/
  toyota/
    __init__.py
    protocols/
      __init__.py
      can_uds/
        __init__.py
        protocol.py
```

2. Implement `protocol.py`:

```python
from core.base_protocol import BaseProtocol
from core.protocol_factory import register

@register('toyota', 'can_uds')
class ToyotaCanUdsProtocol(BaseProtocol):
    def generate_sim_files(self, config_path, output_dir):
        ...   # Toyota-specific logic

    # implement the other 6 abstract methods
```

3. Register it in `main.py` (one line):

```python
import makes.toyota.protocols.can_uds   # noqa: F401
```

4. Add Toyota DB folders and update `DB_REGISTRY` in `src/db_schema.py` (or create a separate registry for Toyota).

No existing Hyundai code is modified.

---

## Adding a New DB Source

1. Add column-list constants in `src/db_schema.py`.
2. Add a `DataSource` entry in `DB_REGISTRY`:

```python
'NewSource': DataSource(
    folder=Path('data/NewSource'),
    sheets={
        'Sheet Name': SheetSpec(cols=_NEW_COLS, skiprows=[0, 2, 3]),
    },
),
```

3. Create `data/NewSource/` and place the matching Excel file there.
4. Call `loader.get('NewSource', 'Sheet Name')` in your builder.

---

## Logs

All log output is written to `logs/create_sim.log` and shown in the UI log panel / terminal.

| Code | Meaning |
|------|---------|
| E101 | No CAN profile for a ProfileID — using default 0x7D9 |
| E102 | Formula coefficient `a=0`, division skipped |
| E103 | General value encode failure |
| E104 | NWS row has blank Profile column |
| E105 | No matching profile in NWS DB |
| E106 | Profile found but has no CMD Query or CMD KeepAlive |
| E107 | PID group has no GetValueCmd |
| E108 | TotalDataSize missing — derived from BytePosition+Bytesize |
| E109 | Byte position exceeds payload buffer size |
| E110 | General PID encode failure |
| E111 | No PIDs matched between config and Make_LD Item ID sheet |
| E112 | Cannot compute support check buffer size |
| E113 | `MsgID/ECUID/ProfileID` column missing from PIDs config sheet |
