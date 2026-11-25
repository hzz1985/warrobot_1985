# -*- coding: utf-8 -*-
"""Tiny atomic JSON state store (peaks, lock flags, per-day task marks)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict


class JsonState:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: Dict[str, Any] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def update(self, mapping: Dict[str, Any]) -> None:
        self.data.update(mapping)
        self.save()

    def bump(self, key: str, amount: float = 1) -> float:
        cur = float(self.data.get(key, 0) or 0) + amount
        self.set(key, cur)
        return cur

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A sibling .tmp written with a normal open() inherits the folder ACL;
        # tempfile.mkstemp would create a 0600 DACL that a locked-down host can
        # refuse, which is a real failure mode on this machine.
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
