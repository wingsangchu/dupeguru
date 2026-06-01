import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from file_ops import DUP_DIR, SAME_SIZE_DIR, move_dup_files, move_same_size_files, same_size_destination
from scanner import (
    FileInfo,
    PARTIAL_HASH_CHUNK,
    group_by_hash,
    group_by_size,
    group_by_size_and_hash,
    merge_scan_files,
    partial_md5,
    scan_folder,
)
from storage import ScanRecord, delete_scan, list_scans, save_scan

_NOW = datetime(2026, 1, 1, 12, 0, 0)


def _record(label: str, path: str, files: list[FileInfo]) -> ScanRecord:
    return ScanRecord(
        id=f"id-{label}",
        path=path,
        label=label,
        created_at=_NOW,
        files=files,
    )


class ScannerTest(unittest.TestCase):
    def test_scan_folder_collects_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("hello", encoding="utf-8")
            (root / "b.txt").write_text("hello", encoding="utf-8")
            sub = root / "sub"
            sub.mkdir()
            (sub / "c.txt").write_text("x", encoding="utf-8")

            files = scan_folder(root)
            self.assertEqual(len(files), 3)

    def test_group_by_size_returns_only_duplicates(self) -> None:
        files = [
            FileInfo("a", "/a", 10, _NOW, _NOW),
            FileInfo("b", "/b", 10, _NOW, _NOW),
            FileInfo("c", "/c", 20, _NOW, _NOW),
        ]
        groups = group_by_size(files)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], 10)

    def test_merge_and_group_multiple_scans(self) -> None:
        aaa = _record(
            "aaa",
            r"c:\aaa",
            [
                FileInfo("a1", r"c:\aaa\a1.txt", 100, _NOW, _NOW),
                FileInfo("a2", r"c:\aaa\a2.txt", 100, _NOW, _NOW),
            ],
        )
        bbb = _record(
            "bbb",
            r"c:\bbb",
            [FileInfo("b1", r"c:\bbb\b1.txt", 100, _NOW, _NOW)],
        )
        groups = group_by_size(merge_scan_files([aaa, bbb]))
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0][1]), 3)

    def test_partial_md5_same_head_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.bin"
            b = root / "b.bin"
            payload = b"A" * 100 + b"B" * 100
            a.write_bytes(payload)
            b.write_bytes(payload)
            self.assertEqual(partial_md5(a, len(payload)), partial_md5(b, len(payload)))

    def test_partial_md5_differs_for_same_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.bin"
            b = root / "b.bin"
            size = PARTIAL_HASH_CHUNK * 2 + 100
            a.write_bytes(b"A" * size)
            data = bytearray(b"A" * size)
            data[size - 100] = ord("X")
            b.write_bytes(data)
            self.assertNotEqual(partial_md5(a, size), partial_md5(b, size))

    def test_group_by_size_and_hash_splits_same_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.bin"
            b = root / "b.bin"
            c = root / "c.bin"
            size = PARTIAL_HASH_CHUNK * 2 + 100
            a.write_bytes(b"A" * size)
            b.write_bytes(b"A" * size)
            data = bytearray(b"A" * size)
            data[size - 100] = ord("X")
            c.write_bytes(data)

            files = scan_folder(root)
            groups = group_by_size_and_hash(files)
            self.assertEqual(len(groups), 1)
            self.assertEqual(len(groups[0][2]), 2)

    def test_group_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "a.bin"
            b = root / "b.bin"
            payload = b"same-content"
            a.write_bytes(payload)
            b.write_bytes(payload)

            files = scan_folder(root)
            groups = group_by_hash(files)
            self.assertEqual(len(groups), 1)
            self.assertEqual(len(groups[0][1]), 2)

    def test_merge_scan_files(self) -> None:
        aaa = _record("aaa", r"c:\aaa", [FileInfo("a", "/a", 1, _NOW, _NOW)])
        bbb = _record("bbb", r"c:\bbb", [FileInfo("b", "/b", 2, _NOW, _NOW)])
        merged = merge_scan_files([aaa, bbb])
        self.assertEqual(len(merged), 2)


class FileOpsTest(unittest.TestCase):
    def test_same_size_destination_keeps_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sub" / "photo.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"x")

            dest = same_size_destination(root, source)
            self.assertEqual(dest, root / SAME_SIZE_DIR / "sub" / "photo.jpg")

    def test_move_same_size_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "sub"
            sub.mkdir()
            file_a = sub / "a.txt"
            file_b = root / "b.txt"
            file_a.write_text("same", encoding="utf-8")
            file_b.write_text("same", encoding="utf-8")

            scan_id = "scan-1"
            files = [
                FileInfo("a.txt", str(file_a), 4, _NOW, _NOW, scan_id, "root"),
                FileInfo("b.txt", str(file_b), 4, _NOW, _NOW, scan_id, "root"),
            ]
            record = ScanRecord(id=scan_id, path=str(root), label="root", created_at=_NOW, files=files)
            groups = group_by_size_and_hash(files)

            result, affected = move_same_size_files([record], groups)
            self.assertEqual(result.moved_count, 2)
            self.assertEqual(affected, [scan_id])
            self.assertTrue((root / SAME_SIZE_DIR / "sub" / "a.txt").is_file())
            self.assertTrue((root / SAME_SIZE_DIR / "b.txt").is_file())
            self.assertFalse(file_a.exists())
            self.assertFalse(file_b.exists())

    def test_move_dup_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_a = root / "a.txt"
            file_b = root / "b.txt"
            file_a.write_text("dup-content", encoding="utf-8")
            file_b.write_text("dup-content", encoding="utf-8")

            scan_id = "scan-dup"
            files = [
                FileInfo("a.txt", str(file_a), 11, _NOW, _NOW, scan_id, "root"),
                FileInfo("b.txt", str(file_b), 11, _NOW, _NOW, scan_id, "root"),
            ]
            record = ScanRecord(id=scan_id, path=str(root), label="root", created_at=_NOW, files=files)
            groups = [("abc123", files)]

            result, affected = move_dup_files([record], groups)
            self.assertEqual(result.moved_count, 2)
            self.assertEqual(affected, [scan_id])
            self.assertTrue((root / DUP_DIR / "a.txt").is_file())
            self.assertTrue((root / DUP_DIR / "b.txt").is_file())


class StorageTest(unittest.TestCase):
    def test_save_list_delete_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch("storage.DATA_DIR", data_dir):
                record = ScanRecord.create(
                    str(data_dir / "sample"),
                    [FileInfo("x.txt", str(data_dir / "x.txt"), 5, _NOW, _NOW)],
                )
                save_scan(record)
                self.assertEqual(len(list_scans()), 1)
                loaded = list_scans()[0]
                self.assertEqual(loaded.label, "sample")
                self.assertEqual(loaded.files[0].scan_id, record.id)
                self.assertTrue(delete_scan(record.id))
                self.assertEqual(list_scans(), [])


if __name__ == "__main__":
    unittest.main()
