"""Where a finished briefing goes.

One method, one direction. M3's approval button will need a reply path, but
running locally it can poll for the callback inside the same process while
the user is still at the keyboard — so there is nothing to generalise yet.
"""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def send(self, text: str) -> None: ...
