"""Feature tiers — which features load at launch.

One mechanism behind the three ideas we want for ORCA Workbench:

  * ``--simple`` / ``--gateway_mode``  -> load **CORE** only (fewest widgets;
    best over a high-latency X-forwarded link, e.g. a VPN connection).
  * default                            -> **CORE + EXTENDED**.
  * (future) an add-on tab             -> **OPTIONAL**.

A tab/plotter/calc-type is tagged with a tier; the launch mode sets how far down
the list to load. Today this gates the heavy Workflow "tools" tab and
defers the non-active pipeline tabs in simple mode — and it's the seed of the
planned add-on system, so promoting a feature between tiers is just a tag change.
"""

CORE = "core"
EXTENDED = "extended"
OPTIONAL = "optional"

_ORDER = {CORE: 0, EXTENDED: 1, OPTIONAL: 2}
_max_tier = EXTENDED   # default loads core + extended


def set_max_tier(tier):
    # type: (str) -> None
    global _max_tier
    if tier in _ORDER:
        _max_tier = tier


def max_tier():
    # type: () -> str
    return _max_tier


def includes(tier):
    # type: (str) -> bool
    """True if `tier` is loaded under the current launch mode."""
    return _ORDER.get(tier, 99) <= _ORDER[_max_tier]


def is_simple():
    # type: () -> bool
    return _max_tier == CORE
