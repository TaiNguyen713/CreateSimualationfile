"""
vin_builder.py — Per-vehicle OBD2 + system .sim file and JSON metadata generator.

Output layout (one folder per vehicle):
    output/
        {VIN}_{YEAR}_{MAKE}_{MODEL}_{ENGINE}/
            {VIN}_{YEAR}_{MAKE}_{MODEL}_{ENGINE}.sim
            {VIN}_{YEAR}_{MAKE}_{MODEL}_{ENGINE}.json

.sim structure:
    1. Protocol config header
    2. OBD2 section (verbatim template — only Mode 9 PID 02 VIN bytes change per vehicle)
    3. Per-system sections  — NWS init → DTC read → Live Data, separated by banners
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

import pandas as pd
import polars as pl

logger = logging.getLogger(__name__)

_SYSTEM_BANNER_WIDTH = 60


def _system_banner(system: str) -> str:
    inner = f' {system} '
    dashes = '-' * ((_SYSTEM_BANNER_WIDTH - len(inner)) // 2)
    return f'//{dashes}{inner}{dashes}'


# ── Protocol config header ────────────────────────────────────────────────────

_HEADER_LINES = [
    '###########################################',
    '#         Auto Generated                  #',
    '###########################################',
    '<config sw> Protocol = 29',
    '<config sw> PIN_KRX_CANH = 6',
    '<config sw> TYPE_KRX_CANH = 0',
    '<config sw> VOLT_KRX_CANH = 3',
    '<config sw> PIN_KTX_CANH = 14',
    '<config sw> TYPE_KTX_CANH = 0',
    '<config sw> VOLT_KTX_CANH = 3',
    '<config sw> PIN_LRX_CANH =  6',
    '<config sw> TYPE_LTX_CANH = 0',
    '<config sw> VOLT_LTX_CANH = 3',
    '<config sw> VREF = 0',
    '<config sw> BAUDRATE = 500000',
    '<config sw> DATABIT = 0',
    '<config sw> PARITY = 0',
    '<config sw> TBYTE = 3',
    '<config sw> TFRAME = 5',
    '<config sw> F CAN NUMBER FRAME = 1',
    '<config sw> RANGE =  500,7FF;',
    '###########################################',
    '#         End of config                   #',
    '###########################################',
]

# ── OBD2 template — everything BEFORE the 4 VIN frames ───────────────────────
# __VIN__ is replaced with the actual VIN string (e.g. KMHD35LHXHU384425)

_OBD2_PRE_VIN: list[str] = [
    '',
    '//---------------------------------OBD 2',
    '//---------------------------------Mode 3: ',
    '//P1613\tStored\tN/A\tManifold Differential Pressure Sensor - Abnormal (Severity 1)',
    '//P1529\tStored)\tN/A\tTCU Request For MIL On',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 01 03 00 00 00 00 00 00\tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 07 43 02 16 13 15 29 00\tNONE\t0\t0',
    '',
    '//---------------------------------Mode 7: ',
    '//P1110\tPending\tN/A\tElectronic Throttle System Malfunction',
    '//P1123\tPending\tN/A\tLong Term Fuel Trim Additive, Air System Too Rich',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 01 07 00 00 00 00 00 00\tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 07 47 02 11 10 11 23 00\tNONE\t0\t0',
    '',
    '//---------------------------------Mode 0A: ',
    '//P1614\tPermanent\tN/A\tMIL Request Signal Circuit Low Input',
    '//P1623\tPermanent\tN/A\tDiagnostic Lamp Powerstage Malfunction',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 01 0A 00 00 00 00 00 00\tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 07 4A 02 16 14 16 23 00\tNONE\t0\t0',
    '',
    '//---------------------------------Mode 2:',
    '//Check supported',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 00 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007e8 08 07 42 00 00 DF FB 20 01\tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 20 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007e8 08 07 42 20 00 00 00 00 01\tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 40 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007e8 08 07 42 40 00 00 00 0F 00\tNONE\t0\t0',
    '',
    '//FF-DTC: P1613',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 02 00 00 00 00 00\tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 05 42 02 00 16 13 00 00\tNONE\t0\t0',
    '',
    '',
    '//Calculated LOAD Value\tCalc Load\t100 (%)\t100 (%)',
    '//Engine Coolant Temp\tECT\t176 (°F)\t80 (°C)',
    '//Fuel Rail Pressure\tFuel Pres\t37 (psi)\t258 (kPa)',
    '//Intake Manifold Absolute Pressure\tMAP\t35 (inHg)\t120 (kPa)',
    '//Engine RPM\tEng RPM\t5534\t5534',
    '//Vehicle Speed Sensor\tVeh Speed\t53 (mph)\t86 (km/h)',
    '//Intake Air Temperature\tIAT\t32 (°F)\t0 (°C)',
    '//Air Flow Rate from Mass Air Flow Sensor\tMAF\t86.69 (lb/min)\t655.35 (g/s)',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 04 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 05 42 04 00 FF 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 05 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 05 42 05 00 78 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 0A 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 05 42 0A 00 56 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 0B 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 05 42 0B 00 78 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 0C 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 05 42 0C 00 56 78 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 0D 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 05 42 0D 00 56 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 0F 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 05 42 0F 00 28 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 10 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 05 42 10 00 FF FF 00 00 \tNONE\t0\t0',
    '',
    '',
    '//PID 13 is Supported',
    '//STFT B1\tShort Term Fuel Trim - Bank 1\t-100 %',
    '//LTFT B1\tLong Term Fuel Trim - Bank 1\t99.22 %',
    '//STFT B2\tShort Term Fuel Trim - Bank 2\t-100 %',
    '//LTFT B2\tLong Term Fuel Trim - Bank 2\t99.22 %',
    '',
    '//STSO2FT1\tShort Term Secondary O2 Sensor Fuel Trim – Bank 1\t-100 %',
    '//LGSO2FT1\tLong Term Secondary O2 Sensor Fuel Trim – Bank 1 \t99.22 %',
    '//STSO2FT2\tShort Term Secondary O2 Sensor Fuel Trim - Bank 2\t-100 %',
    '//LGSO2FT2\tLong Term Secondary O2 Sensor Fuel Trim – Bank 2 \t99.22 %',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 13 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 04 42 13 00 FF 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 06 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 04 42 06 00 00 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 07 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 04 42 07 00 FF 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 08 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 04 42 08 00 00 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 09 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 04 42 09 00 FF 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 55 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 04 42 55 00 00 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 56 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 04 42 56 00 FF 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 57 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 04 42 57 00 00 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 03 02 58 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 04 42 58 00 FF 00 00 00 \tNONE\t0\t0',
    '',
    '//---------------------------------Mode 8:',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 08 00 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007e8 08 02 48 ff 00 58 18 80 03\tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 08 01 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007e8 08 03 48 01 ff 58 18 80 03\tNONE\t0\t0',
    '',
    '//----------------------------------Mode 4:',
    'INFO_DATABASE = Req>1\t\t\t00 00 07 DF 08 01 04 00 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t00 00 07 E8 08 01 44 AA AA AA AA AA AA \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 01 04 01 00 00 00 00 00\tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007e8 08 02 44 01 00 0 04 00 80\tNONE\t0\t0',
    '',
    '//----------------------------------Mode 1:',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 00 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 00 FE 3B 20 01 FF \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 20 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 20 00 00 00 01 FF \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 40 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 40 80 00 00 01 FF \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 80 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 80 00 00 00 01 FF \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 A0 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 A0 04 00 00 00 FF \tNONE\t0\t0',
    '',
    '',
    '//Fuel System 1 Status\tFuel Sys 1\tCL\tCL',
    '//Fuel System 2 Status\tFuel Sys 2\tOL\tOL',
    '//Calculated LOAD Value\tCalc Load\t20 (%)\t20 (%)',
    '//Engine Coolant Temp\tECT\t302 (°F)\t150 (°C)',
    '//Short Term Fuel Trim - Bank 1\tSTFT B1\t-50 (%)\t-50 (%)',
    '//Long Term Fuel Trim - Bank 1\tLTFT B1\t0 (%)\t0 (%)',
    '//Intake Manifold Absolute Pressure\tMAP\t56 (inHg)\t190 (kPa)',
    '//Engine RPM\tEng RPM\t5534\t5534',
    '//Vehicle Speed Sensor\tVeh Speed\t0 (mph)\t0 (km/h)',
    '//Intake Air Temperature\tIAT\t86 (°F)\t30 (°C)',
    '//Air Flow Rate from Mass Air Flow Sensor\tMAF\t3.39 (lb/min)\t25.60 (g/s)',
    '//Location of oxygen sensors\tO2SLoc\tO2S11 O2S12\tO2S11 O2S12',
    '//Odometer OBD2 \tOdometer \t159.1 miles \t256 km',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 03 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 03 02 01 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 04 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 04 33 00 00 01 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 05 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 05 BE 3F 00 01 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 06 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 06 40 3F 00 01 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 07 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 07 80 3F 00 01 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 0B 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 0B BE 3F 00 01 00 \tNONE\t0\t0',
    '',
    '//INFO_DATABASE = Req>2\t\t\t 000007DF 08 02 01 0C 00 00 00 00 00 \tNONE\t0\t0',
    '//INFO_DATABASE = Res<2\t\t\t 000007E8 08 04 41 0C 00 00 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>2\t\t\t 000007DF 08 02 01 0C 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<2\t\t\t 000007E8 08 04 41 0C 56 78 00 00 37 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>2\t\t\t 000007DF 08 02 01 0D 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<2\t\t\t 000007E8 08 03 41 0D 00 00 00 00 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>2\t\t\t000007DF 08 02 01 0F 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<2\t\t\t000007E8 08 03 41 0F 46 00 00 00 37 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 10 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 10 0A 00 00 01 00 \tNONE\t0\t0',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 13 00 00 00 00 00\tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 08 07 41 13 03 FF FF FF FF \tNONE\t0\t0',
    '',
    '//INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 A6 00 00 00 00 00\tNONE\t0\t0',
    '//INFO_DATABASE = Res<1\t\t\t000007E8 08 07 41 A6 00 00 0A 00 FF \tNONE\t0\t0',
    '',
    '',
    '//------Monitor status this drive cycle',
    '//Complete: Components, Fuel System, Misfire',
    '//Incomplete: others',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 01 41 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 41 41 00 77 FF 00 00 \tNONE\t0\t0',
    '',
    '//------Monitor status since DTCs cleared',
    '//MIL On',
    '//Complete: Components, Fuel System, Misfire',
    '//Incomplete: others',
    'INFO_DATABASE = Req>1\t\t\t00 00 07 DF  08  02 01 01 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t00 00 07 E8  08  06 41 01 86 07 FF FF 00 \tNONE\t0\t0',
    '',
    '/////////////////////////////Mode 9: __VIN__',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF 08 02 09 00 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8 07 06 49 00 55 00 00 00 00 \tNONE\t0\t0',
    '//VIN:__VIN__',
]

# ── OBD2 template — everything AFTER the 4 VIN frames ────────────────────────

_OBD2_POST_VIN: list[str] = [
    '//Calibration ID: 0123456789ABCDEF',
    '',
    'INFO_DATABASE = Req>1\t\t\t000007DF  08  02 09 04 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8  08  10 13 49 04 01 30 31 32 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t 000007E8  08  21 33 34 35 36 37 38 39 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t 000007E8  08  22 41 42 43 44 45 46 00 \tNONE\t0\t0',
    '',
    '//CVN: 29 03 19 99',
    'INFO_DATABASE = Req>1\t\t\t000007DF  08  02 09 06 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8  08  10 07 49 06 00 99 91 30 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t 000007E8  08  21 92 00 00 00 00 00 00 \tNONE\t0\t0',
    '',
    '//IPT: 1,2,3,4,...20',
    'INFO_DATABASE = Req>1\t\t\t000007DF  08  02 09 08 00 00 00 00 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t000007E8  08  10 2B 49 08 14 00 01 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t 000007E8  08  21 02 00 03 00 04 00 05 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t 000007E8  08  22 00 06 00 07 00 08 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t 000007E8  08  23 09 00 0a 00 0b 00 0c \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t 000007E8  08  24 00 0d 00 0e 00 0f 00 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t 000007E8  08  25 10 00 11 00 12 00 13 \tNONE\t0\t0',
    'INFO_DATABASE = Res<1\t\t\t 000007E8  08  26 00 14 00 00 00 00 00 \tNONE\t0\t0',
]


# ── VIN helpers ───────────────────────────────────────────────────────────────

def vin_to_ascii_hex(vin: str) -> list[str]:
    """Convert a 17-character VIN to a list of uppercase ASCII hex byte strings."""
    vin = vin.strip().upper()
    if len(vin) != 17:
        logger.warning('VIN %r is not 17 characters (got %d) — padding/truncating', vin, len(vin))
        vin = vin[:17].ljust(17, '0')
    return [format(ord(c), '02X') for c in vin]


def _build_vin_frames(vin: str) -> list[str]:
    """Return the 4 lines for Mode 9 PID 02 with actual VIN bytes.

    Frame structure (20-byte payload: 49 02 01 + 17 VIN bytes):
        FF  : 10 14 49 02 01  VIN[0..2]
        CF21: 21              VIN[3..9]
        CF22: 22              VIN[10..16]
    No flow-control Req line — matches the template format.
    """
    v = vin_to_ascii_hex(vin)
    req = '000007DF'
    res = '000007E8'
    return [
        f'INFO_DATABASE = Req>1\t\t\t{req} 08 02 09 02 00 00 00 00 00 \tNONE\t0\t0',
        f'INFO_DATABASE = Res<1\t\t\t{res} 08 10 14 49 02 01 {v[0]} {v[1]} {v[2]} \tNONE\t0\t0',
        f'INFO_DATABASE = Res<1\t\t\t{res} 08 21 {" ".join(v[3:10])} \tNONE\t0\t0',
        f'INFO_DATABASE = Res<1\t\t\t{res} 08 22 {" ".join(v[10:17])} \tNONE\t0\t0',
    ]


# ── OBD2 section builder ──────────────────────────────────────────────────────

def _build_obd2_section(vin: str) -> list[str]:
    """Return all OBD2 lines with the actual VIN substituted into Mode 9 PID 02."""
    pre   = [line.replace('__VIN__', vin) for line in _OBD2_PRE_VIN]
    frames = _build_vin_frames(vin)
    return pre + frames + _OBD2_POST_VIN


# ── File helpers ──────────────────────────────────────────────────────────────

def _safe_name(text: str) -> str:
    return re.sub(r'[<>:"/\\|?*()]+', '', text).replace(' ', '_')


def _field(row: dict, *keys: str, default: str = 'N/A') -> str:
    for key in keys:
        val = str(row.get(key, '') or '').strip()
        if val and val.lower() != 'nan':
            return val
    return default


# ── Main generator ────────────────────────────────────────────────────────────

def generate_vin_sim_files(
    config_path: Path,
    output_dir: Path,
    loader=None,
) -> int:
    """Generate one folder per vehicle row in the VIN_YMME sheet.

    Each folder contains a .sim (OBD2 + system sections) and a .json metadata file.
    Only the Mode 9 PID 02 VIN bytes differ between vehicles; all other OBD2 data
    is kept verbatim from the template above.

    Parameters
    ----------
    config_path:
        Excel config file with VIN_YMME sheet (required) and optionally NWS / PIDs sheets.
    output_dir:
        Root output directory; one sub-folder is created per vehicle.
    loader:
        Optional pre-built DataLoader instance.  Pass one from the caller when
        batch-processing many configs so the master databases are loaded only once.
        A fresh DataLoader is created internally when None is passed.
    """
    from first_idea import DataLoader, build_all_system_content

    if loader is None:
        loader = DataLoader()

    # PIDs sheet is optional — auto-generated configs contain only VIN_YMME + NWS
    xf = pd.ExcelFile(str(config_path))
    if 'PIDs' in xf.sheet_names:
        pids = pd.read_excel(xf, sheet_name='PIDs').astype(str)
    else:
        pids = pd.DataFrame(columns=['ItemID', 'System', 'Value', 'MsgID/ECUID/ProfileID'])

    system_content: dict[str, list[str]] = {}
    try:
        system_content = build_all_system_content(pids, loader, config_path)
    except Exception as exc:
        logger.warning('Could not load system content: %s — only OBD2 section written', exc)

    df = pl.read_excel(str(config_path), sheet_name='VIN_YMME')
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0

    for row in df.iter_rows(named=True):
        vin          = _field(row, 'VIN')
        year         = _field(row, 'Year')
        make         = _field(row, 'Make')
        model        = _field(row, 'Model')
        engine       = _field(row, 'Engine')
        manufacturer = _field(row, 'Manufacturer', 'Make')

        if vin == 'N/A':
            logger.warning('Skipping row with empty VIN')
            continue

        folder_tag  = f'{vin}_{year}_{make}_{model}_{engine}'
        safe_tag    = _safe_name(folder_tag)
        vehicle_dir = output_dir / safe_tag
        vehicle_dir.mkdir(parents=True, exist_ok=True)

        # ── .sim ──────────────────────────────────────────────────────────
        lines: list[str] = [f'//Note: {folder_tag}', *_HEADER_LINES]
        lines.extend(_build_obd2_section(vin))
        for system, sys_lines in system_content.items():
            lines.append('')
            lines.append(_system_banner(system))
            lines.extend(sys_lines)

        sim_path = vehicle_dir / (safe_tag + '.sim')
        sim_path.write_text('\n'.join(lines), encoding='utf-8')
        logger.info('Written  %s  (%d lines)', sim_path, len(lines))

        # ── .json ─────────────────────────────────────────────────────────
        metadata = {
            'year':            year,
            'manufacturer':    manufacturer,
            'make':            make,
            'model':           model,
            'engine':          engine,
            'SmogCheckLoc':    'N/A',
            'SmogCheckCounty': 'N/A',
        }
        json_path = vehicle_dir / (safe_tag + '.json')
        json_path.write_text(json.dumps(metadata, indent=4), encoding='utf-8')
        logger.info('Written  %s', json_path)
        written += 1

    return written


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout)])
    n = generate_vin_sim_files(Path('config/Vehicle_infor.xlsx'), Path('output'))
    print(f'Done — {n} vehicle folder(s) written to output/')
