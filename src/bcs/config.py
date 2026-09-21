import os
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent

DATA_DIR = str(ROOT_DIR / "data")
RUNTIME_DIR = str(ROOT_DIR / "runtime")
MEMORY_DB_PATH = str(ROOT_DIR / "runtime" / "memory.db")
SEEN_QUESTIONS_PATH = str(ROOT_DIR / "runtime" / "seen_questions.json")


def load_config(path: str = None) -> Dict[str, Any]:
    if path is None:
        path = str(ROOT_DIR / "config" / "config.yaml")
    cfg_path = Path(path)
    if not cfg_path.exists():
        return {}

    import yaml
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_CONFIG_CACHE: Dict[str, Any] = {}


def get_config(key: str = None, default: Any = None) -> Any:
    if not _CONFIG_CACHE:
        _CONFIG_CACHE.update(load_config())
    if key is None:
        return _CONFIG_CACHE
    parts = key.split(".")
    val = _CONFIG_CACHE
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            return default
    return val if val is not None else default
