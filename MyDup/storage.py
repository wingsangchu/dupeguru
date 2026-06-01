"""Persist scan sessions to JSON files."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from scanner import FileInfo

DATA_DIR = Path(__file__).resolve().parent / "data" / "scans"


@dataclass
class ScanRecord:
    id: str
    path: str
    label: str
    created_at: datetime
    files: list[FileInfo]

    @classmethod
    def create(cls, path: str, files: list[FileInfo]) -> ScanRecord:
        root = Path(path)
        label = root.name or str(root)
        return cls(
            id=uuid.uuid4().hex,
            path=str(root),
            label=label,
            created_at=datetime.now(),
            files=files,
        )


def _ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _file_to_dict(info: FileInfo) -> dict:
    return {
        "name": info.name,
        "path": info.path,
        "size": info.size,
        "created": info.created.isoformat(),
        "modified": info.modified.isoformat(),
        "scan_id": info.scan_id,
        "scan_label": info.scan_label,
    }


def _file_from_dict(data: dict) -> FileInfo:
    return FileInfo(
        name=data["name"],
        path=data["path"],
        size=data["size"],
        created=datetime.fromisoformat(data["created"]),
        modified=datetime.fromisoformat(data["modified"]),
        scan_id=data.get("scan_id", ""),
        scan_label=data.get("scan_label", ""),
    )


def _record_to_dict(record: ScanRecord) -> dict:
    return {
        "id": record.id,
        "path": record.path,
        "label": record.label,
        "created_at": record.created_at.isoformat(),
        "files": [_file_to_dict(item) for item in record.files],
    }


def _record_from_dict(data: dict) -> ScanRecord:
    return ScanRecord(
        id=data["id"],
        path=data["path"],
        label=data["label"],
        created_at=datetime.fromisoformat(data["created_at"]),
        files=[_file_from_dict(item) for item in data["files"]],
    )


def save_scan(record: ScanRecord) -> Path:
    """Write a scan record to disk."""
    folder = _ensure_data_dir()
    target = folder / f"{record.id}.json"
    tagged_files = [
        FileInfo(
            name=item.name,
            path=item.path,
            size=item.size,
            created=item.created,
            modified=item.modified,
            scan_id=record.id,
            scan_label=record.label,
        )
        for item in record.files
    ]
    record.files = tagged_files
    target.write_text(json.dumps(_record_to_dict(record), indent=2), encoding="utf-8")
    return target


def list_scans() -> list[ScanRecord]:
    """Load all saved scans, newest first."""
    folder = _ensure_data_dir()
    records: list[ScanRecord] = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records.append(_record_from_dict(data))
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            continue
    records.sort(key=lambda item: item.created_at, reverse=True)
    return records


def load_scan(scan_id: str) -> Optional[ScanRecord]:
    target = _ensure_data_dir() / f"{scan_id}.json"
    if not target.exists():
        return None
    data = json.loads(target.read_text(encoding="utf-8"))
    return _record_from_dict(data)


def delete_scan(scan_id: str) -> bool:
    target = _ensure_data_dir() / f"{scan_id}.json"
    if not target.exists():
        return False
    target.unlink()
    return True
