"""Move matched files into SAME_SIZE or DUP folders under each scan root."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from scanner import FileInfo, scan_folder
from storage import ScanRecord, save_scan

SAME_SIZE_DIR = "SAME_SIZE"
DUP_DIR = "DUP"


@dataclass
class MoveResult:
    moved: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def moved_count(self) -> int:
        return len(self.moved)


def _scan_roots(records: list[ScanRecord]) -> dict[str, Path]:
    return {record.id: Path(record.path) for record in records}


def special_folder_destination(scan_root: Path, file_path: Path, folder_name: str) -> Path:
    """Build destination under ``scan_root/<folder_name>`` keeping relative layout."""
    target_root = scan_root / folder_name
    try:
        relative = file_path.relative_to(scan_root)
    except ValueError:
        relative = Path(file_path.name)

    if relative.parts and relative.parts[0].upper() == folder_name.upper():
        return file_path.resolve()

    return (target_root / relative).resolve()


def same_size_destination(scan_root: Path, file_path: Path) -> Path:
    return special_folder_destination(scan_root, file_path, SAME_SIZE_DIR)


def dup_destination(scan_root: Path, file_path: Path) -> Path:
    return special_folder_destination(scan_root, file_path, DUP_DIR)


def unique_files_from_groups(groups: list) -> list[FileInfo]:
    seen: set[str] = set()
    result: list[FileInfo] = []
    for item in groups:
        files = item[2] if len(item) == 3 else item[1]
        for info in files:
            key = info.path.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(info)
    return result


def _move_files_to_folder(
    records: list[ScanRecord],
    groups: list,
    folder_name: str,
) -> tuple[MoveResult, list[str]]:
    roots = _scan_roots(records)
    files = unique_files_from_groups(groups)
    result = MoveResult()
    affected_scan_ids: set[str] = set()

    for info in files:
        source = Path(info.path)
        if not info.scan_id or info.scan_id not in roots:
            result.errors.append((info.path, "Unknown scan for this file"))
            continue

        scan_root = roots[info.scan_id]
        destination = special_folder_destination(scan_root, source, folder_name)

        if destination == source.resolve():
            result.skipped.append((info.path, f"Already under {folder_name}"))
            continue

        if not source.is_file():
            result.errors.append((info.path, "Source file not found"))
            continue

        if destination.exists():
            result.errors.append((info.path, f"Destination already exists: {destination}"))
            continue

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            result.moved.append((str(source), str(destination)))
            affected_scan_ids.add(info.scan_id)
        except OSError as exc:
            result.errors.append((info.path, str(exc)))

    return result, sorted(affected_scan_ids)


def move_same_size_files(
    records: list[ScanRecord],
    groups: list,
) -> tuple[MoveResult, list[str]]:
    """Move files in *groups* into ``<scan_root>/SAME_SIZE/...`` for each scan."""
    return _move_files_to_folder(records, groups, SAME_SIZE_DIR)


def move_dup_files(
    records: list[ScanRecord],
    groups: list,
) -> tuple[MoveResult, list[str]]:
    """Move files in *groups* into ``<scan_root>/DUP/...`` for each scan."""
    return _move_files_to_folder(records, groups, DUP_DIR)


def rescan_records(records: list[ScanRecord], scan_ids: list[str]) -> list[ScanRecord]:
    """Rescan affected saved scans and persist updated file lists."""
    id_set = set(scan_ids)
    updated: list[ScanRecord] = []

    for record in records:
        if record.id not in id_set:
            continue
        files = scan_folder(record.path)
        refreshed = ScanRecord(
            id=record.id,
            path=record.path,
            label=record.label,
            created_at=record.created_at,
            files=files,
        )
        save_scan(refreshed)
        updated.append(refreshed)

    return updated
