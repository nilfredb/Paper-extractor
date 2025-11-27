from enum import Enum, auto
from typing import Protocol, Tuple, Optional

class Phase(Enum):
    DISCOVERY = auto()
    PREPARATION = auto()
    ACQUISITION = auto()

class Cost(Enum):
    CHEAP = 0
    NORMAL = 1
    EXPENSIVE = 2

class Strategy(Protocol):
    phase: Phase
    cost: Cost
    name: str
    def run(self, browser, sniffer) -> Tuple[Optional[str], bool]:
        ...
