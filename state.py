"""What survives a restart: where polling left off, posts already handled, spend per day, and a
log of every summons (what was asked, what went out, what it cost)."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# X pay-per-use prices in US dollars, from docs.x.com (Oct 2026). Estimates: X bills by its own count.
PRICE = {
    "read": 0.005,  # a post read (search or lookup)
    "reply": 0.01,  # a reply under a post that tags the bot
}

_KEEP_HANDLED = 500
_KEEP_DAYS = 14


class State:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "state.json"
        self.log_path = self.folder / "summons.jsonl"
        self.data: dict[str, Any] = {"since_id": None, "handled": [], "spend": {}}
        try:
            self.data.update(json.loads(self.path.read_text()))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            logger.warning("x: state file unreadable (%s); starting fresh", e)

    def save(self) -> None:
        handled = self.data.get("handled") or []
        self.data["handled"] = handled[-_KEEP_HANDLED:]
        days = sorted(self.data.get("spend") or {})
        self.data["spend"] = {d: self.data["spend"][d] for d in days[-_KEEP_DAYS:]}
        fd, tmp = tempfile.mkstemp(dir=self.folder, prefix=".state-")
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    @property
    def post_limit(self) -> int:
        """Characters per post the bot learned (280 once X refused a long one), 0 if never learned."""
        try:
            return int(self.data.get("post_limit") or 0)
        except (TypeError, ValueError):
            return 0

    @post_limit.setter
    def post_limit(self, value: int) -> None:
        self.data["post_limit"] = int(value)
        self.save()

    @property
    def since_id(self) -> Optional[str]:
        return self.data.get("since_id")

    @since_id.setter
    def since_id(self, value: Optional[str]) -> None:
        if value and (not self.since_id or int(value) > int(self.since_id)):
            self.data["since_id"] = value

    def handled(self, post_id: str) -> bool:
        return post_id in (self.data.get("handled") or [])

    def mark(self, post_id: str) -> None:
        self.data.setdefault("handled", []).append(post_id)

    @staticmethod
    def today() -> str:
        return dt.datetime.now(dt.timezone.utc).date().isoformat()

    def add_spend(self, kind: str, count: int = 1) -> float:
        cost = PRICE.get(kind, 0.0) * count
        if cost:
            spend = self.data.setdefault("spend", {})
            spend[self.today()] = round(spend.get(self.today(), 0.0) + cost, 4)
        return cost

    def spent_today(self) -> float:
        return float((self.data.get("spend") or {}).get(self.today(), 0.0))

    def log(self, entry: dict) -> None:
        entry = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **entry}
        try:
            with self.log_path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.warning("x: could not write the summons log: %s", e)

    def recent(self, n: int = 50) -> list[dict]:
        try:
            lines = self.log_path.read_text().splitlines()[-n:]
        except FileNotFoundError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
