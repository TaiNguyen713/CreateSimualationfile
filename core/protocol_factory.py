"""
protocol_factory.py — Maps (make, protocol) pairs to BaseProtocol subclasses.

Usage
-----
Registration (in each protocol module):

    from core.protocol_factory import register

    @register('hyundai', 'can_uds')
    class HyundaiCanUdsProtocol(BaseProtocol):
        ...

Retrieval (in main.py or tests):

    from core.protocol_factory import get_protocol
    protocol = get_protocol('hyundai', 'can_uds')
    protocol.generate_sim_files(config_path, output_dir)

Adding a new Make
-----------------
1. Create makes/<new_make>/protocols/<protocol>/__init__.py
2. Decorate the class with @register('<new_make>', '<protocol>')
3. Import the module before calling get_protocol() — typically in main.py.
No existing code needs to change.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.base_protocol import BaseProtocol

# {(make_lower, protocol_lower): ConcreteProtocolClass}
_REGISTRY: dict[tuple[str, str], type[BaseProtocol]] = {}


def register(make: str, protocol: str):
    """Class decorator that registers a BaseProtocol subclass."""
    def _decorator(cls: type[BaseProtocol]) -> type[BaseProtocol]:
        _REGISTRY[(make.lower(), protocol.lower())] = cls
        return cls
    return _decorator


def get_protocol(make: str, protocol: str = 'can_uds') -> BaseProtocol:
    """Return a fresh instance of the protocol for the given make.

    Raises
    ------
    ValueError
        If the (make, protocol) combination has not been registered.
    """
    key = (make.lower(), protocol.lower())
    cls = _REGISTRY.get(key)
    if cls is None:
        raise ValueError(
            f'No protocol registered for make={make!r} protocol={protocol!r}. '
            f'Available: {list(_REGISTRY)}'
        )
    return cls()


def list_protocols() -> list[tuple[str, str]]:
    """Return all registered (make, protocol) pairs."""
    return list(_REGISTRY)
