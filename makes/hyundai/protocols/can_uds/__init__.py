"""
Importing this package registers HyundaiCanUdsProtocol in the factory.
main.py does `import makes.hyundai.protocols.can_uds` before calling
get_protocol(), which triggers this import and fires the @register decorator.
"""
from makes.hyundai.protocols.can_uds.protocol import HyundaiCanUdsProtocol

__all__ = ['HyundaiCanUdsProtocol']
