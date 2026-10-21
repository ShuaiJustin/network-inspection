import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from app.services.security import redact_runtime_text


LOG_FILE_PREFIX = "inspection_"


def _log_file_for(directory: Path, created_at: datetime) -> Path:
    return directory / f"{LOG_FILE_PREFIX}{created_at:%Y-%m-%d}.log"


def _prune_expired_log_files(directory: Path, retention_days: int, today: datetime) -> None:
    cutoff = today.date() - timedelta(days=max(1, retention_days))
    for path in directory.glob(f"{LOG_FILE_PREFIX}*.log"):
        try:
            date_part = path.stem.removeprefix(LOG_FILE_PREFIX)
            if datetime.strptime(date_part, "%Y-%m-%d").date() < cutoff:
                path.unlink(missing_ok=True)
        except (OSError, ValueError):
            continue


def persist_activity_logs(records: Iterable[dict], directory: Path, retention_days: int) -> None:
    records = list(records)
    if not records:
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for record in records:
            created_at = record["created_at"]
            payload = {
                "time": created_at.isoformat(timespec="seconds"),
                "level": record["level"],
                "event": record["event"],
                "device_ip": record["device_ip"],
                "task_id": record["task_id"],
                "message": redact_runtime_text(record["message"] or ""),
            }
            with _log_file_for(directory, created_at).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        _prune_expired_log_files(directory, retention_days, datetime.now())
    except OSError:
        # File logging is supplementary. A bad disk must not abort a completed operation.
        return
