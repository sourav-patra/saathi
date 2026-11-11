"""The JSON-lines file sink (M18): one file per UTC day, one line per span, old files pruned.

Files are `trace-<YYYY-MM-DD>.jsonl`, private to the service user (directory 0700, files 0600). The sink holds no
state beyond the day it last pruned for, so a restart loses nothing. Errors propagate to the exporter, which counts
them and never lets one reach a request.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

PREFIX, SUFFIX = "trace-", ".jsonl"
_NAME = re.compile(r"^trace-(\d{4}-\d{2}-\d{2})\.jsonl$")


def file_for(directory: Path, day: date) -> Path:
    return directory / f"{PREFIX}{day.isoformat()}{SUFFIX}"


def write_all(fd: int, data: bytes) -> None:
    """Write every byte or none: a short write is continued, and on a failure the partial line is cut off again."""
    start = os.lseek(fd, 0, os.SEEK_END)
    done = 0
    try:
        while done < len(data):
            n = os.write(fd, data[done:])
            if n <= 0:
                raise OSError("trace write made no progress")
            done += n
    except BaseException:
        if done:
            try:
                os.ftruncate(fd, start)
            except OSError:
                pass
        raise


class JsonlSink:
    def __init__(self, directory: Path, retention_days: int) -> None:
        self.directory = directory
        self.retention_days = retention_days
        self._pruned_for: date | None = None

    def write(self, when: datetime, record: dict[str, object]) -> None:
        day = when.date()
        line = json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=False) + "\n"
        self._prepare_dir()
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
        fd = os.open(file_for(self.directory, day), flags, 0o600)
        try:
            write_all(fd, line.encode("ascii"))
        finally:
            os.close(fd)
        if self._pruned_for != day:
            self._pruned_for = day
            self.prune(day)

    def _prepare_dir(self) -> None:
        """Create the directory 0700; refuse a symlink in its place and take an existing one back to 0700."""
        if self.directory.is_symlink():
            raise OSError(f"trace dir is a symlink: {self.directory.name}")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise OSError(f"trace dir is not a plain directory: {self.directory.name}")
        os.chmod(self.directory, 0o700)

    def prune(self, today: date) -> int:
        """Delete trace files older than the retention. Returns how many went. Other files are never touched."""
        oldest = today - timedelta(days=self.retention_days - 1)  # today counts as one of the days
        gone = 0
        for path in self.directory.iterdir():
            m = _NAME.match(path.name)
            if m is None:
                continue
            try:
                if date.fromisoformat(m.group(1)) < oldest:
                    path.unlink()
                    gone += 1
            except (ValueError, OSError):
                continue
        return gone
