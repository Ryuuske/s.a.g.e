"""Agent-keyed drawer metadata — serialization helpers.

ChromaDB metadata values must be scalars (str / int / float / bool), so a
``list[str]`` of agent names is stored as a JSON-encoded string under the
``"agents"`` metadata key. These helpers wrap that encoding so callers do
not have to repeat the json.loads / json.dumps + error handling.

Invariants:
- ``serialize_agents`` always returns a JSON-encoded list string (never None).
  An empty list serialises to ``"[]"``, not ``""`` — that way the read path
  can distinguish "no agents recorded" (empty list) from "agents field
  absent entirely" (None metadata value).
- ``deserialize_agents`` returns ``[]`` for None / empty string / malformed
  JSON / JSON that decodes to a non-list. It never raises on bad input,
  because read-path failures should degrade to "no agents on this drawer"
  rather than crashing the whole search.
"""

from __future__ import annotations

import json
from typing import Optional


def serialize_agents(agents: list[str]) -> str:
    """Encode an agent name list as a JSON string for ChromaDB metadata."""
    return json.dumps(list(agents))


def deserialize_agents(blob: Optional[str]) -> list[str]:
    """Decode the ``agents`` metadata back to a list[str], lenient on errors."""
    if not blob:
        return []
    try:
        parsed = json.loads(blob)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]
