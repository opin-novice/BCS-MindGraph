from bcs.config import ROOT_DIR, DATA_DIR, RUNTIME_DIR, MEMORY_DB_PATH, SEEN_QUESTIONS_PATH, get_config


def test_paths_exist():
    assert "data" in DATA_DIR
    assert "runtime" in RUNTIME_DIR
    assert "memory.db" in MEMORY_DB_PATH
    assert "seen_questions" in SEEN_QUESTIONS_PATH


def test_get_config_default():
    val = get_config("nonexistent.key", default=42)
    assert val == 42


def test_get_config_empty():
    cfg = get_config()
    assert isinstance(cfg, dict)
