# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Reads vehicle diagnostic configuration (PIDs, NWS profiles, VINs) from an Excel config file and Hyundai diagnostic databases, then generates per-vehicle folders containing a `.sim` file (ECU CAN-bus simulation) and a `.json` metadata file for use with a vehicle diagnostic tool.

## Commands

```bash
# Run (generates per-vehicle folders under output/ from config/Vehicle_infor.xlsx)
python first_idea.py

# Install dependencies (Python >= 3.11 required)
pip install -e .
# or with uv:
uv sync
```

Logs are written to `logs/create_sim.log` alongside stdout.

## Architecture

### Data Flow

```
config/Vehicle_infor.xlsx                    data/<source>/*.xlsx  (one folder per DB source)
                                               ├── data/Make_LD/  → Item ID, Profile ID sheets
                                               ├── data/NWS/      → Ymme, Profile sheets
                                               └── data/DTC/      → DTC sheet
config/Vehicle_infor.xlsx                    data/*.xlsx (Hyundai DBs)
  ├── sheet: PIDs  (ItemID, System,            ├── Make_LD → Item ID sheet
  │         Value, MsgID/ECUID/ProfileID)      │     (GetValueCmd, BytePosition, formula…)
  ├── sheet: VIN_YMME                          ├── Make_LD → Profile ID sheet
  │         (VIN, Year, Manufacturer,          │     (Option1=res CAN ID, Option2=req CAN ID,
  │          Make, Model, Engine)              │      SupReQ1/2, SupBytePos1/2, xxBitMask1/2…)
  └── sheet: NWS                               └── NWS → Profile sheet
                                                     (TagAddr/CanReq1, CanStart1,
                                                      CMD Query, CMD KeepAlive…)

Step 1: pids  INNER JOIN item_db     ON ItemID
Step 2: merged LEFT JOIN  profile_db ON MsgID/ECUID/ProfileID
                    ↓
              build_all_system_content() → {system: [lines]}
                    ↓
              generate_vin_sim_files()
                    ↓
        output/{VIN}_{YEAR}_{MAKE}_{MODEL}_{ENGINE}/
            ├── {tag}.sim   (OBD2 section + per-system sections)
            └── {tag}.json  (year, manufacturer, make, model, engine, SmogCheckLoc, SmogCheckCounty)
```

### Three Source Files

**`src/db_schema.py`** — pure registry/loader, no business logic:
- `SheetSpec` / `DataSource` dataclasses describe how to read each Excel sheet
- `DataSource.folder` — each DB source is a **folder** (not a single file); all `.xlsx` files in the folder and its sub-folders are discovered automatically
- `DB_REGISTRY` maps source keys (`'Make_LD'`, `'NWS'`, `'DTC'`) to `data/<source>/` folders and per-sheet column lists
- `DataLoader.get(source, sheet)` — scans the folder, validates each file against the schema, concatenates data from all matching files; prompts `(y/n)` on the terminal for any file that does not match
- `_prompt_skip(file, reason)` — prints `[SCHEMA MISMATCH]` + reason, reads `y` (skip) or `n` (sys.exit(1))
- To add a new DB source: add column list constants, add a `DataSource(folder=Path('data/new/'), sheets={...})` entry in `DB_REGISTRY`, and place the matching Excel file(s) in that folder

**`first_idea.py`** — section builders and entry point:
- Entry point is `main()` → `build_sim()` → section builders → writes `demo.sim` (single-vehicle legacy path)
- Also exports `build_all_system_content(pids, loader, config_path)` used by `vin_builder.py` to build per-system content for all vehicles

**`src/vin_builder.py`** — per-vehicle output generator:
- `generate_vin_sim_files(config_path, output_dir)` — main entry; iterates VIN_YMME rows, calls `build_all_system_content()`, writes folder/`.sim`/`.json` per vehicle
- `_build_obd2_section(vin)` — assembles OBD2 lines from `_OBD2_PRE_VIN` + dynamic VIN frames + `_OBD2_POST_VIN`
- `_build_vin_frames(vin)` — generates 4 lines (1 Req + 3 Res) for Mode 9 PID 02; CAN IDs `000007DF`/`000007E8`
- `_system_banner(system)` — renders `//--- SYSTEM ---` section separator (60-char wide)
- `_safe_name(text)` — strips Windows-invalid characters for folder/file naming
- `_OBD2_PRE_VIN` / `_OBD2_POST_VIN` — verbatim OBD2 template lists; `__VIN__` placeholder in pre-VIN is the only substitution point

### Section Build Order (inside `build_sim` / `build_all_system_content`)

1. `build_header()` — `.sim` file protocol config block
2. `_build_system_id_map()` — resolves `{system: (req_can_id, res_can_id)}` from NWS Profile sheet; used as **fallback** CAN IDs for NWS init and Live Data
3. `build_nws_init_lines()` — NWS initialization Req/Res pairs per system; deduplicates by profile (same profile shared across DTCs emits frames only once)
4. `build_dtc_lines()` — DTC read frames per system
5. `build_live_data_lines()` — per system, in order:
   - **Phase 3-1**: support check blocks (`SupReQ1`, then `SupReQ2`) — one block per unique support command, CAN IDs from profile `Option1`/`Option2`
   - **Phase 3-2**: live data Req/Res blocks grouped by `(GetValueCmd, Option1, Option2)`

`build_all_system_content()` calls steps 3–5 once per system and returns `{system: [lines]}`. The per-vehicle `.sim` file is then assembled as: protocol header → OBD2 section → `//--- SYSTEM ---` banner + system lines, for each system.

### CAN ID Resolution (priority order)

| Priority | Source | When used |
|----------|--------|-----------|
| 1st | `Option1` / `Option2` from Make_LD Profile ID sheet (via profile join) | LD support check + live data blocks |
| 2nd | `TagAddr/CanReq1` / `CanStart1` from NWS Profile sheet (via `system_id_map`) | NWS init + fallback for unmatched profiles |
| 3rd | `DEFAULT_ECU_ID` (0x7D9) | last resort |

The `_can_ids(group, system)` helper inside `build_live_data_lines` encapsulates this logic.

### ISO-TP Encoding (`encode_isotp`)

- Payload ≤ 7 bytes → Single Frame (SF)
- Payload > 7 bytes → First Frame (FF) + flow control `Req>1 Q-- {req_id} 08 30 08 02 ...` + Consecutive Frames (CF 21, 22, …)

### Key Excel Column Relationships

| Sheet | Key column(s) | Used for |
|-------|--------------|----------|
| config / PIDs | `ItemID`, `System`, `Value`, `MsgID/ECUID/ProfileID` | merge keys; target values; profile link |
| config / VIN_YMME | `VIN`, `Year`, `Manufacturer`, `Make`, `Model`, `Engine` | one output folder per row |
| Make_LD / Item ID | `ItemID` | item details: `GetValueCmd`, `BytePosition`, `Bytesize`, formula fields |
| Make_LD / Profile ID | `MsgID/ECUID/ProfileID`, `Option1` (res CAN ID), `Option2` (req CAN ID), `SupReQ1/2`, `SupBytePos1/2`, `SupStatus1/2` | CAN IDs per profile; support check response generation |
| NWS / Profile | `MsgID/ECUID`, `TagAddr/CanReq1`, `CanStart1`, `CMD Query`, `CMD KeepAlive` | NWS init frames + fallback system CAN ID map |
| config / NWS | `System`, `Profile`, `DTC`, `Status` | which NWS profiles to initialize |

### Warning / Error Codes

| Code | Meaning |
|------|---------|
| E101 | No CAN profile for a ProfileID — using default 0x7D9 |
| E102 | Formula coefficient `a=0`, division skipped, returns 0 |
| E103 | General value encode failure for an ItemID |
| E104 | NWS row has blank Profile column |
| E105 | No matching profile in NWS DB for a Profile ID |
| E106 | Profile found but has no CMD Query or CMD KeepAlive |
| E107 | PID group has no GetValueCmd |
| E108 | TotalDataSize missing — derived from BytePosition+Bytesize, or skipped |
| E109 | Byte position exceeds payload buffer size |
| E110 | General PID encode failure (caught exception) |
| E111 | No PIDs matched between config and Make_LD Item ID sheet |
| E112 | Cannot compute support check buffer size (SupBytePos/SupByteSize invalid) |
| E113 | `MsgID/ECUID/ProfileID` column missing from PIDs config sheet |

## Important Conventions

- All DataFrames are loaded with `.astype(str)` — every value is a string. Use `float(x)` / `int(float(x))` to parse numeric fields; never `int(x)` directly on Excel-sourced values because floats come in as `'110.0'` not `'110'`.
- NaN values from Excel become the string `'nan'`. The sentinel set `_nan = {'nan', 'NaN', ''}` is used throughout `build_live_data_lines` for guards.
- The `.sim` output format uses tab-separated fields: `INFO_DATABASE = {Dir}\t\t\t{CAN_ID} 08 {8-byte-payload} \t{suffix}`. The `Q--` prefix on a Req line marks an ISO-TP flow control frame.
- `PROTOCOL_CONFIG` in `first_idea.py` controls the `.sim` header block. Field order is significant — it is written exactly as declared.
- OBD2 template (`_OBD2_PRE_VIN` / `_OBD2_POST_VIN` in `vin_builder.py`) is verbatim — **never edit individual byte values** without explicit user instruction. Only the 4 VIN-frame lines (Mode 9 PID 02) are dynamic.
- OBD2 CAN IDs: `000007DF` (functional broadcast request), `000007E8` (ECM response). These differ from the system-level CAN IDs resolved via `_can_ids()`.
- Windows console is cp1252 — avoid Unicode characters (e.g. `→`) in `logger` calls; use ASCII equivalents (`->`) instead.
- `vin_builder.py` reads VIN_YMME with `polars` (`pl.read_excel`); PIDs sheet uses `pandas`. Do not swap these without testing.
