import logging
import sys
import io

LOGGING_CONFIGURED = False

def setup_logging(level: int = logging.INFO):
    global LOGGING_CONFIGURED
    if LOGGING_CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    try:
        handler.stream = io.TextIOWrapper(
            handler.stream.buffer, encoding="utf-8", errors="replace"
        )
    except AttributeError:
        pass

    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    ))
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    LOGGING_CONFIGURED = True

def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
