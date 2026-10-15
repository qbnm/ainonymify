"""Add local detectors by implementing this small, text-only interface."""
from typing import Protocol
from .rules import Match


class EntityDetector(Protocol):
    def detect(self, text: str) -> list[Match]: ...
