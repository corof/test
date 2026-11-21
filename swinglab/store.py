"""Datenablage: Sessions und Swings auf der Platte lesen."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


@dataclass
class Swing:
    dir: Path
    meta: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    mevo: dict = field(default_factory=dict)

    @classmethod
    def load(cls, d: Path) -> "Swing":
        d = Path(d)
        return cls(d, _load(d / "meta.json"), _load(d / "analysis.json"), _load(d / "mevo.json"))

    @property
    def name(self) -> str:
        return self.dir.name

    @property
    def session(self) -> str:
        return self.dir.parent.name

    @property
    def label(self) -> str:
        return f"{self.session} · #{self.name[-3:]}"

    @property
    def metrics(self) -> dict:
        return self.analysis.get("metrics", {})

    @property
    def findings(self) -> list[dict]:
        return self.analysis.get("findings", [])

    @property
    def analyzed(self) -> bool:
        return bool(self.analysis)

    @property
    def failed(self) -> bool:
        return self.analyzed and not self.analysis.get("phases")

    def summary(self) -> str:
        m = self.metrics
        parts = []
        if m.get("tempo_ratio") is not None:
            parts.append(f"Tempo {m['tempo_ratio']:.1f}:1")
        if self.mevo.get("carry") is not None:
            parts.append(f"Carry {self.mevo['carry']:.0f}")
        if self.mevo.get("club_path") is not None:
            parts.append(f"Path {self.mevo['club_path']:+.1f}°")
        if not self.analyzed:
            parts.append("Analyse läuft …")
        elif self.failed:
            parts.append("⚠ Golfer nicht erkannt")
        elif self.findings:
            parts.append(self.findings[0]["title"])
        else:
            parts.append("✓ unauffällig")
        return " · ".join(parts)


def sessions(data_dir: str | Path) -> list[Path]:
    root = Path(data_dir) / "sessions"
    return sorted((p for p in root.glob("*") if p.is_dir()), reverse=True)


def swings_of(session: Path) -> list[Swing]:
    return [Swing.load(d) for d in sorted(session.glob("swing_*")) if d.is_dir()]


def all_swings(data_dir: str | Path) -> list[Swing]:
    out = []
    for s in reversed(sessions(data_dir)):
        out += [w for w in swings_of(s) if w.analyzed and not w.failed]
    return out
