"""Bounded view history: metadata only, never image buffers."""

from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class ViewState:
    cx: float
    cy: float
    scale: float
    selected: str = ""
    row: int | None = None
    query: str = ""

    def equivalent(self, other):
        return (other is not None and abs(self.cx - other.cx) < 1e-6 and abs(self.cy - other.cy) < 1e-6
                and math.isclose(self.scale, other.scale, rel_tol=1e-9)
                and (self.selected, self.row, self.query) == (other.selected, other.row, other.query))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return None
        values = [data.get(key) for key in ("cx", "cy", "scale")]
        if any(type(value) not in (int, float) for value in values):
            return None
        cx, cy, scale = values
        if abs(cx) > 1e9 or abs(cy) > 1e9 or not 1e-5 <= scale <= 16:
            return None
        if any(not math.isfinite(value) for value in values):
            return None
        selected, row, query = data.get("selected", ""), data.get("row"), data.get("query", "")
        if not isinstance(selected, str) or len(selected) > 256 or not isinstance(query, str):
            return None
        if row is not None and (type(row) is not int or not 1 <= row <= 100000):
            return None
        return cls(float(cx), float(cy), float(scale), selected, row, query[:512])


class ViewHistory:
    def __init__(self, limit=64):
        self.limit = max(2, limit)
        self.entries = []
        self.position = -1

    @property
    def can_back(self):
        return self.position > 0

    @property
    def can_forward(self):
        return 0 <= self.position < len(self.entries) - 1

    def record(self, state):
        if self.position >= 0 and state.equivalent(self.entries[self.position]):
            return
        del self.entries[self.position + 1:]
        self.entries.append(state)
        if len(self.entries) > self.limit:
            del self.entries[:len(self.entries) - self.limit]
        self.position = len(self.entries) - 1

    def step(self, direction):
        target = self.position + direction
        if not 0 <= target < len(self.entries):
            return None
        self.position = target
        return self.entries[target]

    def reset(self, state):
        self.entries = [state]
        self.position = 0


def validated_saved_view(data):
    if not isinstance(data, dict):
        return None
    state = ViewState.from_dict(data.get("state"))
    key, extent = data.get("map_key"), data.get("extent")
    if (state is None or not isinstance(key, str) or len(key) > 2048 or not isinstance(extent, list)
            or len(extent) != 2 or any(type(value) is not int or not 1 <= value <= 2000000 for value in extent)):
        return None
    return dict(state=state.to_dict(), map_key=key, extent=extent)
