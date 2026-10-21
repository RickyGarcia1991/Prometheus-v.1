"""Read-only Linux host power observations."""
from pathlib import Path
from itertools import islice


def _read(path):
    try:
        with path.open('rb') as stream:
            value = stream.read(65)
        return value.decode('ascii').strip() if len(value) <= 64 else None
    except (OSError, UnicodeError):
        return None


def read_power(root=Path('/sys/class/power_supply')):
    """Return single host battery capacity and independently measured AC state.

    Kernel power-supply links are expected here. Reads are bounded, no values are
    written, and absent or ambiguous observations remain unknown. In particular,
    full/not-charging battery state does not prove an external supply is online.
    Multiple batteries need capacity-weighted accounting, not a plain average.
    """
    try:
        supplies = list(islice(Path(root).iterdir(), 33))
    except OSError:
        return None, None
    if len(supplies) > 32:
        return None, None
    batteries, external = [], []
    for supply in supplies:
        scope = _read(supply/'scope')
        if scope not in (None, 'System'):
            continue
        kind = _read(supply/'type')
        if kind == 'Battery':
            present = _read(supply/'present')
            if present == '0':
                continue
            value = _read(supply/'capacity')
            capacity = int(value) if value is not None and value.isdigit() else None
            batteries.append(capacity if present in (None, '1') and capacity is not None and 0 <= capacity <= 100 else None)
        elif kind in ('Mains', 'USB', 'Wireless'):
            external.append({'0': False, '1': True}.get(_read(supply/'online')))
    plugged = True if any(value is True for value in external) else (
        False if external and all(value is False for value in external) else None)
    return (batteries[0] if len(batteries) == 1 else None), plugged
