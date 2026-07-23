import datetime
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from bcs.logging_config import get_logger

log = get_logger(__name__)

DEFAULT_LOG_PATH = str(Path(__file__).resolve().parent.parent.parent.parent / "runtime" / "pipeline_log.jsonl")


class PipelineLogger:
    def __init__(self, log_path: str = DEFAULT_LOG_PATH):
        self._log_path = log_path
        self._lock = threading.Lock()
        self._run_id: Optional[str] = None
        self._start_time: Optional[float] = None
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

    def start_run(self, topic: str, difficulty: str, count: int, max_facts: Optional[int] = None) -> str:
        self._run_id = f"run_{uuid.uuid4().hex[:12]}"
        self._start_time = __import__("time").time()
        self._write({
            "pipeline_run_id": self._run_id,
            "timestamp": datetime.datetime.now().isoformat(),
            "stage": "start",
            "input": {"topic": topic, "difficulty": difficulty, "count": count, "max_facts": max_facts},
            "output": {},
            "metrics": {},
            "failure_mode": "none",
            "execution_time_ms": 0,
        })
        return self._run_id

    def log_stage(self, stage: str, input_data: Optional[Dict] = None, output_data: Optional[Dict] = None,
                  metrics: Optional[Dict] = None, failure_mode: str = "none"):
        elapsed = 0
        if self._start_time:
            elapsed = int((__import__("time").time() - self._start_time) * 1000)
        self._write({
            "pipeline_run_id": self._run_id or "",
            "timestamp": datetime.datetime.now().isoformat(),
            "stage": stage,
            "input": input_data or {},
            "output": output_data or {},
            "metrics": metrics or {},
            "failure_mode": failure_mode,
            "execution_time_ms": elapsed,
        })

    def log_complete(self, output_data: Optional[Dict] = None, metrics: Optional[Dict] = None,
                     failure_mode: str = "none"):
        elapsed = 0
        if self._start_time:
            elapsed = int((__import__("time").time() - self._start_time) * 1000)
        self._write({
            "pipeline_run_id": self._run_id or "",
            "timestamp": datetime.datetime.now().isoformat(),
            "stage": "complete",
            "input": {},
            "output": output_data or {},
            "metrics": metrics or {},
            "failure_mode": failure_mode,
            "execution_time_ms": elapsed,
        })

    def _write(self, entry: Dict[str, Any]):
        with self._lock:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as exc:
                log.warning("Failed to write pipeline log: %s", exc)

    def get_recent_logs(self, limit: int = 50) -> list:
        if not os.path.exists(self._log_path):
            return []
        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [json.loads(line) for line in lines[-limit:]]
        except Exception as exc:
            log.warning("Failed to read pipeline logs: %s", exc)
            return []

    def get_run_logs(self, run_id: str) -> list:
        return [e for e in self.get_recent_logs(10000) if e.get("pipeline_run_id") == run_id]

    def clear(self):
        with self._lock:
            try:
                open(self._log_path, "w").close()
            except Exception as exc:
                log.warning("Failed to clear pipeline logs: %s", exc)


_pipeline_logger = PipelineLogger()


def get_pipeline_logger() -> PipelineLogger:
    return _pipeline_logger
