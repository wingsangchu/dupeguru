"""Scan folders and collect basic file metadata."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional

# First and last 10 KiB for partial MD5 (same idea as dupeGuru sampling).
PARTIAL_HASH_CHUNK = 10 * 1024


@dataclass(frozen=True)
class FileInfo:
    name: str
    path: str
    size: int
    created: datetime
    modified: datetime
    scan_id: str = ""
    scan_label: str = ""
    partial_hash: str = ""


# (size, partial_md5_hex, files)
MatchGroup = tuple[int, str, list[FileInfo]]

# (partial_md5_hex, files)
HashGroup = tuple[str, list[FileInfo]]


ProgressCallback = Callable[[int], None]


def _creation_time(stat: os.stat_result) -> float:
    if hasattr(stat, "st_birthtime"):
        return stat.st_birthtime
    return stat.st_ctime


def _entry_to_file_info(entry: os.DirEntry) -> Optional[FileInfo]:
    try:
        stat = entry.stat(follow_symlinks=False)
    except OSError:
        return None

    if not entry.is_file(follow_symlinks=False):
        return None

    created_ts = _creation_time(stat)
    modified_ts = stat.st_mtime
    path = Path(entry.path)

    return FileInfo(
        name=entry.name,
        path=str(path),
        size=stat.st_size,
        created=datetime.fromtimestamp(created_ts),
        modified=datetime.fromtimestamp(modified_ts),
    )


def scan_folder(
    root: str | Path,
    on_progress: Optional[ProgressCallback] = None,
) -> list[FileInfo]:
    """Recursively collect file metadata under *root* using os.scandir."""
    root_path = Path(root)
    if not root_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {root_path}")

    files: list[FileInfo] = []
    scanned = 0

    def walk(directory: Path) -> Iterator[FileInfo]:
        nonlocal scanned
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            yield from walk(Path(entry.path))
                        else:
                            info = _entry_to_file_info(entry)
                            if info is not None:
                                scanned += 1
                                if on_progress and scanned % 100 == 0:
                                    on_progress(scanned)
                                yield info
                    except OSError:
                        continue
        except OSError:
            return

    for info in walk(root_path):
        files.append(info)

    if on_progress:
        on_progress(len(files))

    files.sort(key=lambda item: (item.size, item.path.lower()))
    return files


def partial_md5(path: str | Path, size: int) -> str:
    """MD5 of file head + tail (10 KiB each). Files <= 20 KiB use the full content."""
    file_path = Path(path)
    hasher = hashlib.md5()
    if size <= 0:
        return hasher.hexdigest()

    try:
        with file_path.open("rb") as fp:
            if size <= PARTIAL_HASH_CHUNK * 2:
                hasher.update(fp.read())
            else:
                hasher.update(fp.read(PARTIAL_HASH_CHUNK))
                fp.seek(-PARTIAL_HASH_CHUNK, os.SEEK_END)
                hasher.update(fp.read(PARTIAL_HASH_CHUNK))
    except OSError:
        return ""

    return hasher.hexdigest()


def _tag_with_hash(info: FileInfo) -> FileInfo:
    digest = partial_md5(info.path, info.size)
    return FileInfo(
        name=info.name,
        path=info.path,
        size=info.size,
        created=info.created,
        modified=info.modified,
        scan_id=info.scan_id,
        scan_label=info.scan_label,
        partial_hash=digest,
    )


def group_by_size(files: list[FileInfo]) -> list[tuple[int, list[FileInfo]]]:
    """Return groups of files that share the same size (only groups with 2+ files)."""
    size_map: dict[int, list[FileInfo]] = defaultdict(list)
    for info in files:
        size_map[info.size].append(info)

    groups = [(size, group) for size, group in size_map.items() if len(group) > 1]
    groups.sort(key=lambda item: (-len(item[1]), -item[0]))
    return groups


def group_by_size_and_hash(
    files: list[FileInfo],
    on_progress: Optional[ProgressCallback] = None,
) -> list[MatchGroup]:
    """Group by size, then by partial MD5 (head+tail 10 KiB). Only groups with 2+ files."""
    size_candidates = group_by_size(files)
    groups: list[MatchGroup] = []
    hashed = 0

    for size, candidates in size_candidates:
        hash_map: dict[str, list[FileInfo]] = defaultdict(list)
        for info in candidates:
            tagged = _tag_with_hash(info)
            hash_map[tagged.partial_hash].append(tagged)
            hashed += 1
            if on_progress and hashed % 50 == 0:
                on_progress(hashed)

        for digest, group in hash_map.items():
            if len(group) >= 2 and digest:
                groups.append((size, digest, group))

    if on_progress:
        on_progress(hashed)

    groups.sort(key=lambda item: (-len(item[2]), -item[0]))
    return groups


def group_by_hash(
    files: list[FileInfo],
    on_progress: Optional[ProgressCallback] = None,
) -> list[HashGroup]:
    """Group files by partial MD5 only (2+ files per hash)."""
    hash_map: dict[str, list[FileInfo]] = defaultdict(list)
    for index, info in enumerate(files, start=1):
        tagged = _tag_with_hash(info)
        if tagged.partial_hash:
            hash_map[tagged.partial_hash].append(tagged)
        if on_progress and index % 50 == 0:
            on_progress(index)

    if on_progress:
        on_progress(len(files))

    groups = [(digest, group) for digest, group in hash_map.items() if len(group) >= 2]
    groups.sort(key=lambda item: (-len(item[1]), item[0]))
    return groups


def merge_scan_files(records: list) -> list[FileInfo]:
    """Combine file lists from multiple scan records."""
    merged: list[FileInfo] = []
    for record in records:
        merged.extend(record.files)
    merged.sort(key=lambda item: (item.size, item.scan_label.lower(), item.path.lower()))
    return merged


def compare_scans(records: list, *, cross_scan_only: bool = True) -> list[tuple[int, list[FileInfo]]]:
    """Group same-size files from selected scans.

    When *cross_scan_only* is True and more than one scan is selected, only groups
    that contain files from at least two different scans are returned.
    """
    if not records:
        return []

    files = merge_scan_files(records)
    groups = group_by_size(files)
    if not cross_scan_only or len(records) < 2:
        return groups

    filtered: list[tuple[int, list[FileInfo]]] = []
    for size, group in groups:
        labels = {item.scan_label for item in group if item.scan_label}
        if len(labels) >= 2:
            filtered.append((size, group))
    return filtered
