from collections import defaultdict
import re
from uuid import uuid4


class IdGenerator:
    def __init__(self) -> None:
        self._counters: defaultdict[str, int] = defaultdict(int)

    def next(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"

    def observe(self, value: str | None) -> None:
        if not value:
            return
        match = re.match(r"^([a-z_]+)_(\d+)$", value)
        if not match:
            return
        prefix, number = match.groups()
        self._counters[prefix] = max(self._counters[prefix], int(number))


id_generator = IdGenerator()
