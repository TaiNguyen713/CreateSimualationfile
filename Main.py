"""
main.py — Multi-protocol entry point.

Usage
-----
    # default: Hyundai CAN-UDS
    python main.py

    # explicit make + protocol
    python main.py --make hyundai --protocol can_uds

    # list all registered protocols
    python main.py --list

How to add a new Make (e.g. Toyota)
-------------------------------------
1. Create makes/toyota/protocols/can_uds/__init__.py  (same pattern as Hyundai)
2. Add the import line below:
       import makes.toyota.protocols.can_uds   # noqa: F401
3. Run:  python main.py --make toyota --protocol can_uds
No existing code is modified.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# ── Register all known protocols before calling the factory ──────────────────
# Each import fires the @register decorator in the protocol module.
# Add one line here for every new Make/Protocol you create.
import makes.hyundai.protocols.can_uds  # noqa: F401

from core.protocol_factory import get_protocol, list_protocols

# ── Logging ───────────────────────────────────────────────────────────────────

LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / 'create_sim.log', encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)

CONFIG_PATH = Path('config/Vehicle_infor.xlsx')
OUTPUT_DIR  = Path('output')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate vehicle .sim files using the registered protocol.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--make', default='hyundai',
        help='Vehicle manufacturer (default: hyundai)',
    )
    parser.add_argument(
        '--protocol', default='can_uds',
        help='Communication protocol (default: can_uds)',
    )
    parser.add_argument(
        '--list', action='store_true',
        help='Print all registered (make, protocol) pairs and exit',
    )
    args = parser.parse_args()

    if args.list:
        registered = list_protocols()
        print('Registered protocols:')
        for make, proto in registered:
            print(f'  make={make!r}  protocol={proto!r}')
        return

    logger.info('Registered protocols : %s', list_protocols())
    logger.info('Selected             : make=%s  protocol=%s', args.make, args.protocol)
    logger.info('Config               : %s', CONFIG_PATH)
    logger.info('Output               : %s', OUTPUT_DIR)

    protocol = get_protocol(args.make, args.protocol)
    n = protocol.generate_sim_files(CONFIG_PATH, OUTPUT_DIR)
    logger.info('Done — %d vehicle folder(s) written to %s/', n, OUTPUT_DIR)


if __name__ == '__main__':
    main()
