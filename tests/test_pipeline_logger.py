import os
import tempfile
import json
from bcs.pipeline_logger import PipelineLogger


def test_start_run():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        run_id = pl.start_run("History", "easy", 1, max_facts=5)
        assert run_id.startswith("run_")
        logs = pl.get_recent_logs()
        assert len(logs) == 1
        assert logs[0]["stage"] == "start"
        assert logs[0]["input"]["topic"] == "History"
        os.unlink(tmp.name)


def test_log_stage():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        pl.start_run("Geography", "medium", 2)
        pl.log_stage("kg_retrieval", output_data={"fact_count": 10})
        logs = pl.get_recent_logs()
        stages = [e["stage"] for e in logs]
        assert "start" in stages
        assert "kg_retrieval" in stages
        os.unlink(tmp.name)


def test_log_complete():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        pl.start_run("History", "hard", 1)
        pl.log_complete(output_data={"mcq_count": 3}, metrics={"avg_quality_score": 0.85})
        logs = pl.get_recent_logs()
        assert logs[-1]["stage"] == "complete"
        assert logs[-1]["output"]["mcq_count"] == 3
        os.unlink(tmp.name)


def test_execution_time():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        pl.start_run("History", "easy", 1)
        import time
        time.sleep(0.01)
        pl.log_stage("test_stage")
        logs = pl.get_recent_logs()
        assert logs[-1]["execution_time_ms"] >= 10
        os.unlink(tmp.name)


def test_failure_mode():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        pl.start_run("History", "easy", 1)
        pl.log_complete(failure_mode="no_facts")
        logs = pl.get_recent_logs()
        assert logs[-1]["failure_mode"] == "no_facts"
        os.unlink(tmp.name)


def test_get_run_logs():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        run_id = pl.start_run("History", "easy", 1)
        pl.log_stage("test")
        logs = pl.get_run_logs(run_id)
        assert len(logs) == 2
        assert all(e["pipeline_run_id"] == run_id for e in logs)
        os.unlink(tmp.name)


def test_get_recent_logs_limit():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        for i in range(10):
            pl.start_run(f"Topic{i}", "easy", 1)
        logs = pl.get_recent_logs(limit=3)
        assert len(logs) == 3
        os.unlink(tmp.name)


def test_clear():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        pl.start_run("History", "easy", 1)
        assert len(pl.get_recent_logs()) > 0
        pl.clear()
        assert pl.get_recent_logs() == []
        os.unlink(tmp.name)


def test_empty_log_file():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        assert pl.get_recent_logs() == []
        os.unlink(tmp.name)


def test_json_lines_format():
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as tmp:
        tmp.close()
        pl = PipelineLogger(log_path=tmp.name)
        pl.start_run("History", "easy", 1)
        with open(tmp.name, "r", encoding="utf-8") as f:
            line = f.readline().strip()
        parsed = json.loads(line)
        assert "pipeline_run_id" in parsed
        assert "timestamp" in parsed
        assert "stage" in parsed
        assert "execution_time_ms" in parsed
        os.unlink(tmp.name)
