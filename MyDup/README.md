# MyDup

Simple folder scanner inspired by [dupeGuru](../README.md). It collects basic file metadata, saves each scan, and compares same-size files across selected scans.

MyDup groups files by **same size + partial MD5** before showing or moving them.

### Partial hash (fast check)

- Algorithm: **MD5**
- Data read: **first 10 KiB + last 10 KiB** (files ≤ 20 KiB use the full file)
- Only computed for files that already share the same size (keeps it fast)
- Same approach as dupeGuru's sampling idea in `core/fs.py`, but fixed at 10 KiB

**Trade-off:** Very fast, but two files can have the same size and partial hash yet differ in the middle. For duplicate hunting this is usually acceptable as a first filter.

## Features

1. **Scan & Save** — pick a folder, scan recursively, auto-save as a session
2. **Saved Scans** — list on the left; each scan shows label, path, file count, time
3. **Delete** — remove a saved scan (**Del** button)
4. **Compare** — tick one or more scans (see above)
5. **All Files** — every file from selected scans
6. **Same Size** — same size + partial MD5 match (summary hides MD5; detail table shows it)
7. **Dup** — grouped by partial MD5 only; **Move to DUP** moves matched files to `<scan_root>/DUP/...`
8. **Move to SAME_SIZE** — on Same Size tab, move matched files into `<scan_root>/SAME_SIZE/...`

### Move examples

| Scan root | Original | Destination |
|-----------|----------|-------------|
| `c:\aaa` | `c:\aaa\pics\a.jpg` | `c:\aaa\SAME_SIZE\pics\a.jpg` or `c:\aaa\DUP\pics\a.jpg` |
| `c:\bbb` | `c:\bbb\b.bin` | `c:\bbb\DUP\b.bin` |

Each scan uses its **own** `SAME_SIZE` or `DUP` folder under its scan root.

## Example

| Scan | Path |
|------|------|
| 1 | `c:\aaa` |
| 2 | `c:\bbb` |
| 3 | `c:\ccc` |

- Tick **aaa** + **bbb** → same-size results use files from `aaa` and `bbb` only
- Tick all three → results use `aaa`, `bbb`, and `ccc`

## Architecture

```
MyDup/
  scanner.py      # os.scandir, FileInfo, group_by_size()
  storage.py      # save/load/delete JSON scan sessions under data/scans/
  file_ops.py     # move same-size files to <scan_root>/SAME_SIZE/
  main.py         # tkinter GUI
  test_scanner.py
  data/scans/     # saved scan JSON files (created at runtime)
```

### Scan flow

```mermaid
flowchart LR
    A[Scan folder] --> B[scan_folder]
    B --> C[save_scan JSON]
    C --> D[Saved Scans list]
    D --> E[User ticks scans]
    E --> F[merge + compare_scans]
    F --> G[All Files / Same Size views]
```

### Storage format

Each scan is one JSON file in `data/scans/{id}.json`:

- `id`, `path`, `label` (folder name), `created_at`
- `files[]`: name, path, size, created, modified, scan_id, scan_label

No SQL database.

## Requirements

- Python 3.10+
- **tkinter** only (stdlib)

## Run

```powershell
cd MyDup
python main.py
```

## Test

```powershell
cd MyDup
python -m unittest test_scanner.py
```
