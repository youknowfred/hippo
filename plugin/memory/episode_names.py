"""Which names an episode record SERVED.

The prompt hook over-fetches by the floor size plus the session's cooldown set, then renders
floor and cooldown members as collapse lines. The episode buffer's ``recalled_names`` lists
that whole pool: the right set for readers that want "the hook matched it" (the cooldown,
the floor diet, the floor trim report), the wrong one for readers that mean "injected".
``served_names`` (additive, written since v1.46.0) is the served rows alone, the same set
as the recall ledger row's ``names``. An older record has no ``served_names`` and reads its
``recalled_names``, as before.
"""

from __future__ import annotations

from typing import List


def served(episode: dict) -> List[str]:
    """The names ``episode`` served as rows (``recalled_names`` on a record that predates
    the split). Never raises on a malformed record."""
    names = episode.get("served_names") if isinstance(episode, dict) else None
    if isinstance(names, list):
        return names
    names = episode.get("recalled_names") if isinstance(episode, dict) else None
    return names if isinstance(names, list) else []
