#!/usr/bin/env python3
"""MyDup - same-size file scanner with saved scans and cross-scan compare."""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from file_ops import DUP_DIR, SAME_SIZE_DIR, move_dup_files, move_same_size_files, rescan_records
from scanner import FileInfo, group_by_hash, group_by_size_and_hash, merge_scan_files, scan_folder
from storage import ScanRecord, delete_scan, list_scans, save_scan

FILE_COLUMNS = ("scan", "name", "path", "size", "hash", "created", "modified")
FILE_HEADERS = {
    "scan": "Scan",
    "name": "Name",
    "path": "Path",
    "size": "Size",
    "hash": "Partial MD5",
    "created": "Created",
    "modified": "Modified",
}

GROUP_COLUMNS = ("size", "count", "scans", "sample")
GROUP_HEADERS = {
    "size": "Size",
    "count": "Count",
    "scans": "Scans",
    "sample": "Sample Path",
}

DUP_GROUP_COLUMNS = ("hash", "count", "scans", "size", "sample")
DUP_GROUP_HEADERS = {
    "hash": "Partial MD5",
    "count": "Count",
    "scans": "Scans",
    "size": "Size",
    "sample": "Sample Path",
}


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def format_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def file_row(info: FileInfo) -> tuple[str, ...]:
    return (
        info.scan_label,
        info.name,
        info.path,
        format_size(info.size),
        info.partial_hash[:12] if info.partial_hash else "",
        format_datetime(info.created),
        format_datetime(info.modified),
    )


class TreeTable(ttk.Frame):
    """Treeview table with vertical and horizontal scrollbars."""

    def __init__(
        self,
        master: tk.Misc,
        columns: tuple[str, ...],
        headers: dict[str, str],
        *,
        on_select: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master)
        self._columns = columns
        self._sort_column: Optional[str] = None
        self._sort_reverse = False
        self._rows: list[tuple] = []
        self._on_select = on_select

        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        for col in columns:
            self.tree.heading(col, text=headers[col], command=lambda c=col: self._sort_by(c))
            if col in ("path", "sample"):
                width = 280
            elif col in ("name", "created", "modified"):
                width = 130
            elif col == "hash":
                width = 110
            else:
                width = 90
            self.tree.column(col, width=width, minwidth=60, stretch=(col in ("path", "sample")))

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        if on_select is not None:
            self.tree.bind("<<TreeviewSelect>>", lambda _event: on_select())

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rows = []

    def set_rows(self, rows: list[tuple]) -> None:
        self.clear()
        self._rows = list(rows)
        for index, row in enumerate(rows):
            self.tree.insert("", "end", iid=str(index), values=row)

    def selected_index(self) -> Optional[int]:
        selected = self.tree.selection()
        if not selected:
            return None
        return int(selected[0])

    def _sort_by(self, column: str) -> None:
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False

        col_index = self._columns.index(column)

        def sort_key(row: tuple) -> object:
            value = row[col_index]
            if column == "size":
                text = str(value)
                if text.endswith(" GB"):
                    return float(text[:-3]) * 1024**3
                if text.endswith(" MB"):
                    return float(text[:-3]) * 1024**2
                if text.endswith(" KB"):
                    return float(text[:-4]) * 1024
                if text.endswith(" B"):
                    return float(text[:-2])
            if column in ("created", "modified"):
                return value
            if column == "count":
                return int(value)
            return str(value).lower()

        self._rows.sort(key=sort_key, reverse=self._sort_reverse)
        self.set_rows(self._rows)


class ScanListPanel(ttk.LabelFrame):
    def __init__(self, master: tk.Misc, app: MyDupApp) -> None:
        super().__init__(master, text="Saved Scans", padding=4)
        self.app = app
        self._records: list[ScanRecord] = []
        self._checks: dict[str, tk.BooleanVar] = {}

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 4))
        ttk.Button(toolbar, text="Select All", command=self._select_all).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="Clear", command=self._clear_selection).pack(side="left")

        canvas = tk.Canvas(self, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.list_frame = ttk.Frame(canvas)
        self.list_frame.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._canvas = canvas

    def refresh(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self._checks.clear()
        self._records = list_scans()

        if not self._records:
            ttk.Label(self.list_frame, text="No saved scans yet.\nScan a folder to save one.").pack(
                anchor="w", padx=4, pady=4
            )
            return

        for record in self._records:
            self._add_row(record)

    def _add_row(self, record: ScanRecord) -> None:
        row = ttk.Frame(self.list_frame)
        row.pack(fill="x", pady=2)

        var = tk.BooleanVar(value=False)
        var.trace_add("write", lambda *_args: self.app.update_compare_view())
        self._checks[record.id] = var

        ttk.Checkbutton(row, variable=var).pack(side="left")
        text = (
            f"{record.label}\n"
            f"{record.path}\n"
            f"{len(record.files)} files | {format_datetime(record.created_at)}"
        )
        ttk.Label(row, text=text, wraplength=220, justify="left").pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Del", width=4, command=lambda rid=record.id: self.app.delete_saved_scan(rid)).pack(
            side="right"
        )

    def selected_records(self) -> list[ScanRecord]:
        return [record for record in self._records if self._checks.get(record.id, tk.BooleanVar()).get()]

    def _select_all(self) -> None:
        for var in self._checks.values():
            var.set(True)

    def _clear_selection(self) -> None:
        for var in self._checks.values():
            var.set(False)


class MyDupApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MyDup - Same Size File Finder")
        self.geometry("1180x700")
        self.minsize(900, 520)

        self._scan_thread: Optional[threading.Thread] = None
        self._compare_thread: Optional[threading.Thread] = None
        self._same_size_groups: list = []
        self._dup_groups: list = []
        self._pending_folder = ""

        self._build_ui()
        self.scan_panel.refresh()
        self._set_status("Ready")

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Folder:").pack(side="left")
        self.folder_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.folder_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(6, 6)
        )
        ttk.Button(top, text="Browse...", command=self._browse_folder).pack(side="left", padx=(0, 4))
        self.scan_btn = ttk.Button(top, text="Scan & Save", command=self._start_scan)
        self.scan_btn.pack(side="left")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 4))

        self.scan_panel = ScanListPanel(body, self)
        body.add(self.scan_panel, weight=1)

        right = ttk.Frame(body)
        body.add(right, weight=4)

        compare_bar = ttk.Frame(right, padding=(0, 0, 0, 4))
        compare_bar.pack(fill="x")
        ttk.Label(
            compare_bar,
            text="Tick scans to compare. Same-size groups also require matching partial MD5 (first+last 10 KiB).",
        ).pack(side="left")
        ttk.Button(compare_bar, text="Refresh Compare", command=self.update_compare_view).pack(side="right")

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)

        all_files_frame = ttk.Frame(self.notebook)
        self.all_files_table = TreeTable(all_files_frame, FILE_COLUMNS, FILE_HEADERS)
        self.all_files_table.pack(fill="both", expand=True, padx=4, pady=4)
        self.notebook.add(all_files_frame, text="All Files")

        same_size_frame = ttk.Frame(self.notebook)
        same_size_toolbar = ttk.Frame(same_size_frame)
        same_size_toolbar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(
            same_size_toolbar,
            text="Same-size groups from selected scans (pick a row for details):",
        ).pack(side="left")
        self.move_btn = ttk.Button(
            same_size_toolbar,
            text="Move to SAME_SIZE",
            command=self._move_same_size_files,
        )
        self.move_btn.pack(side="right")

        self.groups_table = TreeTable(
            same_size_frame,
            GROUP_COLUMNS,
            GROUP_HEADERS,
            on_select=self._on_group_selected,
        )
        self.groups_table.pack(fill="both", expand=True, padx=4, pady=4)

        self.group_detail_table = TreeTable(same_size_frame, FILE_COLUMNS, FILE_HEADERS)
        self.group_detail_table.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.notebook.add(same_size_frame, text="Same Size")

        dup_frame = ttk.Frame(self.notebook)
        dup_toolbar = ttk.Frame(dup_frame)
        dup_toolbar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(
            dup_toolbar,
            text="Duplicate groups by partial MD5 (pick a row for details):",
        ).pack(side="left")
        self.dup_move_btn = ttk.Button(
            dup_toolbar,
            text="Move to DUP",
            command=self._move_dup_files,
        )
        self.dup_move_btn.pack(side="right")

        self.dup_groups_table = TreeTable(
            dup_frame,
            DUP_GROUP_COLUMNS,
            DUP_GROUP_HEADERS,
            on_select=self._on_dup_group_selected,
        )
        self.dup_groups_table.pack(fill="both", expand=True, padx=4, pady=4)

        self.dup_detail_table = TreeTable(dup_frame, FILE_COLUMNS, FILE_HEADERS)
        self.dup_detail_table.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.notebook.add(dup_frame, text="Dup")

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(6, 2)).pack(
            fill="x", side="bottom"
        )

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select Folder to Scan")
        if folder:
            self.folder_var.set(folder)

    def _start_scan(self) -> None:
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showwarning("MyDup", "Please select a folder first.")
            return
        if self._scan_thread and self._scan_thread.is_alive():
            return

        self._pending_folder = folder
        self.scan_btn.configure(state="disabled")
        self._set_status("Scanning...")

        self._scan_thread = threading.Thread(target=self._run_scan, args=(folder,), daemon=True)
        self._scan_thread.start()

    def _run_scan(self, folder: str) -> None:
        try:
            files = scan_folder(folder, on_progress=lambda count: self.after(0, self._on_progress, count))
        except Exception as exc:
            self.after(0, self._on_scan_error, str(exc))
            return
        self.after(0, self._on_scan_finished, files)

    def _on_progress(self, count: int) -> None:
        self._set_status(f"Scanning... {count} files found")

    def _on_scan_error(self, message: str) -> None:
        self.scan_btn.configure(state="normal")
        self._set_status("Scan failed")
        messagebox.showerror("Scan Error", message)

    def _on_scan_finished(self, files: list[FileInfo]) -> None:
        self.scan_btn.configure(state="normal")
        try:
            record = ScanRecord.create(self._pending_folder, files)
            save_scan(record)
        except OSError as exc:
            messagebox.showerror("Save Error", f"Could not save scan:\n{exc}")
            return

        self.scan_panel.refresh()
        for scan_id, var in self.scan_panel._checks.items():
            var.set(scan_id == record.id)

        self.update_compare_view()
        self._set_status(f"Saved scan '{record.label}' ({len(files)} files)")

    def delete_saved_scan(self, scan_id: str) -> None:
        record = next((item for item in self.scan_panel._records if item.id == scan_id), None)
        label = record.label if record else scan_id
        if not messagebox.askyesno("Delete Scan", f"Delete saved scan '{label}'?"):
            return
        if delete_scan(scan_id):
            self.scan_panel.refresh()
            self.update_compare_view()
            self._set_status(f"Deleted scan '{label}'")
        else:
            messagebox.showerror("Delete Error", "Scan file not found.")

    def update_compare_view(self) -> None:
        selected = self.scan_panel.selected_records()
        if not selected:
            self.all_files_table.clear()
            self.groups_table.clear()
            self.group_detail_table.clear()
            self.dup_groups_table.clear()
            self.dup_detail_table.clear()
            self._same_size_groups = []
            self._dup_groups = []
            self._set_status("Select one or more saved scans to compare.")
            return

        files = merge_scan_files(selected)
        self.all_files_table.set_rows([file_row(info) for info in files])
        self.groups_table.clear()
        self.group_detail_table.clear()
        self.dup_groups_table.clear()
        self.dup_detail_table.clear()
        self._same_size_groups = []
        self._dup_groups = []

        if self._compare_thread and self._compare_thread.is_alive():
            return

        self._set_status("Hashing files (10 KiB head + tail)...")

        def run() -> None:
            same_size_groups = group_by_size_and_hash(files)
            dup_groups = group_by_hash(files)
            self.after(0, self._apply_compare_groups, selected, files, same_size_groups, dup_groups)

        self._compare_thread = threading.Thread(target=run, daemon=True)
        self._compare_thread.start()

    def _apply_compare_groups(self, selected, files, same_size_groups, dup_groups) -> None:
        self._same_size_groups = same_size_groups
        self._dup_groups = dup_groups

        group_rows = []
        for size, _digest, group in self._same_size_groups:
            labels = sorted({item.scan_label for item in group if item.scan_label})
            group_rows.append(
                (format_size(size), str(len(group)), ", ".join(labels), group[0].path)
            )
        self.groups_table.set_rows(group_rows)
        self.group_detail_table.clear()

        dup_rows = []
        for digest, group in self._dup_groups:
            labels = sorted({item.scan_label for item in group if item.scan_label})
            sizes = {item.size for item in group}
            size_text = format_size(next(iter(sizes))) if len(sizes) == 1 else "mixed"
            dup_rows.append(
                (digest[:12], str(len(group)), ", ".join(labels), size_text, group[0].path)
            )
        self.dup_groups_table.set_rows(dup_rows)
        self.dup_detail_table.clear()

        labels = ", ".join(record.label for record in selected)
        same_size_file_count = sum(len(group) for _, _, group in self._same_size_groups)
        dup_file_count = sum(len(group) for _, group in self._dup_groups)
        self._set_status(
            f"Compare [{labels}]: {len(files)} files, "
            f"{len(self._same_size_groups)} same-size groups ({same_size_file_count} files), "
            f"{len(self._dup_groups)} dup groups ({dup_file_count} files)"
        )

    def _on_group_selected(self) -> None:
        index = self.groups_table.selected_index()
        if index is None or index >= len(self._same_size_groups):
            self.group_detail_table.clear()
            return
        _, _, files = self._same_size_groups[index]
        self.group_detail_table.set_rows([file_row(info) for info in files])

    def _on_dup_group_selected(self) -> None:
        index = self.dup_groups_table.selected_index()
        if index is None or index >= len(self._dup_groups):
            self.dup_detail_table.clear()
            return
        _, files = self._dup_groups[index]
        self.dup_detail_table.set_rows([file_row(info) for info in files])

    def _move_same_size_files(self) -> None:
        selected = self.scan_panel.selected_records()
        if not selected:
            messagebox.showwarning("MyDup", "Select one or more saved scans first.")
            return
        if not self._same_size_groups:
            messagebox.showinfo("MyDup", "No same-size file groups to move.")
            return

        file_count = len(
            {info.path.lower() for _, _, group in self._same_size_groups for info in group}
        )
        roots = "\n".join(f"  {record.path}\\{SAME_SIZE_DIR}\\..." for record in selected)
        if not messagebox.askyesno(
            "Move Same-Size Files",
            f"Move {file_count} file(s) into SAME_SIZE under each scan root?\n\n{roots}\n\n"
            "Original subfolder layout is kept under SAME_SIZE.",
        ):
            return

        self.move_btn.configure(state="disabled")
        self._set_status("Moving same-size files...")

        def run() -> None:
            try:
                result, affected_ids = move_same_size_files(selected, self._same_size_groups)
                if affected_ids:
                    rescan_records(list_scans(), affected_ids)
            except Exception as exc:
                self.after(0, self._on_move_error, str(exc))
                return
            self.after(0, self._on_move_finished, result)

        threading.Thread(target=run, daemon=True).start()

    def _on_move_error(self, message: str) -> None:
        self.move_btn.configure(state="normal")
        self._set_status("Move failed")
        messagebox.showerror("Move Error", message)

    def _on_move_finished(self, result) -> None:
        self.move_btn.configure(state="normal")
        self.scan_panel.refresh()

        selected_ids = {record.id for record in self.scan_panel.selected_records()}
        for scan_id, var in self.scan_panel._checks.items():
            if scan_id in selected_ids:
                var.set(True)

        self.update_compare_view()

        summary = (
            f"Moved {result.moved_count} file(s). "
            f"Skipped {len(result.skipped)}. Errors {len(result.errors)}."
        )
        self._set_status(summary)

        details = summary
        if result.errors:
            details += "\n\nErrors:\n" + "\n".join(f"{path}\n  {msg}" for path, msg in result.errors[:10])
            if len(result.errors) > 10:
                details += f"\n... and {len(result.errors) - 10} more"

        messagebox.showinfo("Move Complete", details)

    def _move_dup_files(self) -> None:
        selected = self.scan_panel.selected_records()
        if not selected:
            messagebox.showwarning("MyDup", "Select one or more saved scans first.")
            return
        if not self._dup_groups:
            messagebox.showinfo("MyDup", "No duplicate (MD5) groups to move.")
            return

        file_count = len({info.path.lower() for _, group in self._dup_groups for info in group})
        roots = "\n".join(f"  {record.path}\\{DUP_DIR}\\..." for record in selected)
        if not messagebox.askyesno(
            "Move Duplicate Files",
            f"Move {file_count} file(s) into DUP under each scan root?\n\n{roots}\n\n"
            "Original subfolder layout is kept under DUP.",
        ):
            return

        self.dup_move_btn.configure(state="disabled")
        self._set_status("Moving duplicate files to DUP...")

        def run() -> None:
            try:
                result, affected_ids = move_dup_files(selected, self._dup_groups)
                if affected_ids:
                    rescan_records(list_scans(), affected_ids)
            except Exception as exc:
                self.after(0, lambda: self._on_dup_move_error(str(exc)))
                return
            self.after(0, lambda: self._on_dup_move_finished(result))

        threading.Thread(target=run, daemon=True).start()

    def _on_dup_move_error(self, message: str) -> None:
        self.dup_move_btn.configure(state="normal")
        self._set_status("Move to DUP failed")
        messagebox.showerror("Move Error", message)

    def _on_dup_move_finished(self, result) -> None:
        self.dup_move_btn.configure(state="normal")
        self.scan_panel.refresh()

        selected_ids = {record.id for record in self.scan_panel.selected_records()}
        for scan_id, var in self.scan_panel._checks.items():
            if scan_id in selected_ids:
                var.set(True)

        self.update_compare_view()

        summary = (
            f"Moved {result.moved_count} file(s) to DUP. "
            f"Skipped {len(result.skipped)}. Errors {len(result.errors)}."
        )
        self._set_status(summary)

        details = summary
        if result.errors:
            details += "\n\nErrors:\n" + "\n".join(f"{path}\n  {msg}" for path, msg in result.errors[:10])
            if len(result.errors) > 10:
                details += f"\n... and {len(result.errors) - 10} more"

        messagebox.showinfo("Move to DUP Complete", details)


def main() -> None:
    app = MyDupApp()
    app.mainloop()


if __name__ == "__main__":
    main()
