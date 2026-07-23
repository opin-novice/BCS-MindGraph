from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent

DATA_DIR = str(ROOT_DIR / "data")
RUNTIME_DIR = str(ROOT_DIR / "runtime")
MEMORY_DB_PATH = str(ROOT_DIR / "runtime" / "memory.db")
SEEN_QUESTIONS_PATH = str(ROOT_DIR / "runtime" / "seen_questions.json")
