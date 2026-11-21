"""Konfiguration: Paket-Defaults + optionale ./config.toml (deep merge)."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULT_PATH = Path(__file__).with_name("default_config.toml")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> dict:
    with open(DEFAULT_PATH, "rb") as f:
        cfg = tomllib.load(f)
    user = Path(path) if path else Path("config.toml")
    if user.exists():
        with open(user, "rb") as f:
            cfg = _merge(cfg, tomllib.load(f))
    cfg["_config_path"] = str(user.resolve())
    return cfg


def set_value_in_toml(path: str | Path, section: str, key: str, value) -> None:
    """Setzt `key = value` in `[section]` einer TOML-Datei (einfacher Zeilen-Editor,
    erhält Kommentare). Legt Sektion/Key an, falls nicht vorhanden."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    rendered = f"{key} = {value!r}" if not isinstance(value, str) else f'{key} = "{value}"'
    if isinstance(value, bool):
        rendered = f"{key} = {'true' if value else 'false'}"
    header = f"[{section}]"
    in_sec, sec_idx = False, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("["):
            if in_sec:  # Sektion endet, Key nicht gefunden
                lines.insert(i, rendered)
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return
            in_sec = s.split("#")[0].strip() == header
            if in_sec:
                sec_idx = i
            continue
        if in_sec and s.split("=")[0].strip() == key and "=" in s:
            comment = ""
            if "#" in line.split("=", 1)[1]:
                comment = "  #" + line.split("#", 1)[1]
            lines[i] = rendered + comment
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    if sec_idx is None:
        lines += ["", header]
    lines.append(rendered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if v is None:
        return '""'
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_toml(cfg: dict) -> str:
    """Serialisiert die (flache bzw. zweistufige) Konfiguration als TOML."""
    lines = ["# swinglab – von der App gespeichert", ""]

    def section(name: str, d: dict):
        scalars = {k: v for k, v in d.items() if not isinstance(v, dict) and not k.startswith("_")}
        if scalars:
            lines.append(f"[{name}]")
            lines.extend(f"{k} = {_toml_value(v)}" for k, v in scalars.items())
            lines.append("")
        for k, v in d.items():
            if isinstance(v, dict):
                section(f"{name}.{k}", v)

    for k, v in cfg.items():
        if isinstance(v, dict) and not k.startswith("_"):
            section(k, v)
    return "\n".join(lines)


def save_config(cfg: dict, path: str | Path | None = None) -> Path:
    path = Path(path or cfg.get("_config_path") or "config.toml")
    path.write_text(dump_toml(cfg), encoding="utf-8")
    return path
