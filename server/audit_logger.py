"""
audit_logger.py — Rotating Security & Audit Logger.

Logs security events (logins, failures, logouts, system events) to an append-only
rotating file storage. Automatically rotates and caps total stored entries so storage never fills up.
"""
import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Maximum log entries to retain before rotating (dropping oldest)
MAX_LOG_ENTRIES = 10000
LOG_FILE_PATH = Path(__file__).parent.parent / "audit_logs.jsonl"


class AuditLogger:
    def __init__(self, filepath: Path = LOG_FILE_PATH, max_entries: int = MAX_LOG_ENTRIES):
        self.filepath = filepath
        self.max_entries = max_entries

    def _read_all(self) -> List[Dict[str, Any]]:
        """Read all log entries from JSONL file."""
        if not self.filepath.exists():
            return []
        entries = []
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except Exception:
                            continue
        except Exception as e:
            logger.error(f"Error reading audit log: {e}")
        return entries

    def _write_all(self, entries: List[Dict[str, Any]]) -> None:
        """Write trimmed log entries atomically."""
        try:
            temp_path = self.filepath.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")
            temp_path.replace(self.filepath)
        except Exception as e:
            logger.error(f"Error writing audit log: {e}")

    def log_event(
        self,
        event_type: str,
        ip: str,
        status: str,
        details: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record an immutable audit log event.
        Automatically rotates when exceeding max_entries.
        """
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "time_epoch": int(time.time()),
            "event": event_type,  # e.g., "LOGIN_SUCCESS", "TAP", "SWIPE", "KEY", "TEXT"
            "ip": ip,
            "status": status,    # "SUCCESS", "FAILED", "BLOCKED"
            "details": details or "",
            "user_agent": (user_agent[:120] if user_agent else "Unknown"),
        }

        entries = self._read_all()
        entries.append(entry)

        # Log Rotation: Keep only the most recent N entries (10,000)
        if len(entries) > self.max_entries:
            entries = entries[-self.max_entries:]

        self._write_all(entries)
        return entry

    def get_logs(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Return the most recent audit logs in reverse chronological order."""
        entries = self._read_all()
        entries.reverse()  # Newest first
        return entries[:limit]


# Singleton instance
audit_logger = AuditLogger()
