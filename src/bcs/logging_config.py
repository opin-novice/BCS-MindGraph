import logging
import sys
import io
from pathlib import Path

LOGGING_CONFIGURED = False


def setup_logging(level: int = logging.INFO):
    global LOGGING_CONFIGURED
    if LOGGING_CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stdout_handler = logging.StreamHandler(sys.stdout)
    try:
        stdout_handler.stream = io.TextIOWrapper(
            stdout_handler.stream.buffer, encoding="utf-8", errors="replace"
        )
    except AttributeError:
        pass
    stdout_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(stdout_handler)

    log_dir = Path(__file__).resolve().parent.parent.parent.parent / "runtime"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        str(log_dir / "bcs.log"), encoding="utf-8", mode="a"
    )
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)

    LOGGING_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
