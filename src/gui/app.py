"""Tkinter GUI. Non-freezing: heavy work runs in background threads.

The UI never re-parses the world when the raw/clean toggle changes — the
same ExtractionResult drives both formats.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from ..containers.reader import ContainerError
from ..pipeline import (
    ExtractionResult,
    extract_all,
    open_world,
    render_books,
    render_mobs,
    render_players,
    save_report,
    downloads_dir,
)


APP_TITLE = "Minecraft World Data Extractor"


class ExtractorGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("820x620")

        self.source_path = tk.StringVar()
        self.raw_mode = tk.BooleanVar(value=False)
        self._msg_queue: queue.Queue = queue.Queue()
        self._busy = False
        self._current_result: Optional[ExtractionResult] = None
        self._world_source = None

        self._build_ui()
        self.root.after(100, self._drain_queue)

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Input file/folder:").pack(side="left")
        ttk.Entry(top, textvariable=self.source_path, width=70).pack(side="left", padx=4)
        ttk.Button(top, text="Select .EPK / .ZIP / .MCWORLD", command=self._pick_file).pack(side="left", padx=2)
        ttk.Button(top, text="Select Folder", command=self._pick_folder).pack(side="left", padx=2)

        mid = ttk.Frame(self.root)
        mid.pack(fill="x", **pad)
        ttk.Checkbutton(mid, text="Raw data export mode", variable=self.raw_mode).pack(side="left")
        ttk.Button(mid, text="Open Downloads", command=self._open_downloads).pack(side="right")
        ttk.Button(mid, text="Clear log", command=self._clear_log).pack(side="right", padx=6)

        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.btn_players = ttk.Button(btns, text="Retrieve player data",
                                      command=lambda: self._start("players"))
        self.btn_players.pack(side="left", padx=4)
        self.btn_books = ttk.Button(btns, text="Retrieve book data",
                                    command=lambda: self._start("books"))
        self.btn_books.pack(side="left", padx=4)
        self.btn_mobs = ttk.Button(btns, text="Retrieve named mobs data",
                                   command=lambda: self._start("mobs"))
        self.btn_mobs.pack(side="left", padx=4)
        self.btn_all = ttk.Button(btns, text="Retrieve ALL",
                                  command=lambda: self._start("all"))
        self.btn_all.pack(side="left", padx=4)

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", **pad)

        self.log = tk.Text(self.root, height=25, wrap="word")
        self.log.pack(fill="both", expand=True, **pad)
        self.log.configure(state="disabled")

    # ---------- helpers ----------

    def _pick_file(self):
        p = filedialog.askopenfilename(
            title="Select world archive",
            filetypes=[
                ("Minecraft archives", "*.epk *.zip *.mcworld"),
                ("EPK", "*.epk"),
                ("MCWORLD", "*.mcworld"),
                ("ZIP", "*.zip"),
                ("All files", "*.*"),
            ],
        )
        if p:
            self.source_path.set(p)

    def _pick_folder(self):
        p = filedialog.askdirectory(title="Select world folder")
        if p:
            self.source_path.set(p)

    def _open_downloads(self):
        d = downloads_dir()
        try:
            if os.name == "nt":
                os.startfile(str(d))  # type: ignore
            elif os.uname().sysname == "Darwin":  # type: ignore
                os.system(f'open "{d}"')
            else:
                os.system(f'xdg-open "{d}"')
        except Exception as e:
            messagebox.showinfo("Downloads", f"Downloads folder: {d}\n({e})")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = ("disabled" if busy else "normal")
        for b in (self.btn_players, self.btn_books, self.btn_mobs, self.btn_all):
            b.configure(state=state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _drain_queue(self):
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                if msg[0] == "log":
                    self._log(msg[1])
                elif msg[0] == "done":
                    self._set_busy(False)
                elif msg[0] == "error":
                    self._set_busy(False)
                    messagebox.showerror("Error", msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    # ---------- extraction ----------

    def _start(self, mode: str):
        if self._busy:
            return
        path = self.source_path.get().strip()
        if not path:
            messagebox.showwarning("Missing input", "Please select a .EPK / .ZIP / .MCWORLD file or a world folder.")
            return
        if not (os.path.isfile(path) or os.path.isdir(path)):
            messagebox.showwarning("Missing input", f"Not found: {path}")
            return
        self._set_busy(True)
        threading.Thread(target=self._run, args=(path, mode), daemon=True).start()

    def _run(self, path: str, mode: str):
        def prog(msg: str):
            self._msg_queue.put(("log", msg))

        prog(f"Reading input: {path}")
        try:
            src = open_world(path, progress=prog)
        except ContainerError as e:
            self._msg_queue.put(("error", str(e)))
            return
        except Exception as e:
            self._msg_queue.put(("error", f"Unexpected error opening world: {e}"))
            return

        prog(f"World kind: {src.kind} | root: {src.root}")
        prog("Parsing data...")
        try:
            result = extract_all(src)
            self._current_result = result
        except Exception as e:
            src.cleanup()
            self._msg_queue.put(("error", f"Extraction failed: {e}"))
            return

        raw = bool(self.raw_mode.get())
        prog(f"Extracted -> players: {result.counters.get('players_parsed', 0)} / "
             f"{result.counters.get('players_discovered', 0)}, "
             f"books: {result.counters.get('books_parsed', 0)} / "
             f"{result.counters.get('books_discovered', 0)}, "
             f"mobs: {result.counters.get('mobs_parsed', 0)} / "
             f"{result.counters.get('mobs_discovered', 0)}")
        if result.warnings:
            prog(f"Warnings: {len(result.warnings)} (see log below)")
            for w in result.warnings[:10]:
                prog(f"  - {w}")
            if len(result.warnings) > 10:
                prog(f"  ... and {len(result.warnings) - 10} more")

        outputs = []
        if mode in ("players", "all"):
            text = render_players(result, raw)
            path_out, issues = save_report(text, "player_data.txt")
            outputs.append(("player_data.txt", path_out, issues))
        if mode in ("books", "all"):
            text = render_books(result, raw)
            path_out, issues = save_report(text, "book_data.txt")
            outputs.append(("book_data.txt", path_out, issues))
        if mode in ("mobs", "all"):
            text = render_mobs(result, raw)
            path_out, issues = save_report(text, "named_mobs_data.txt")
            outputs.append(("named_mobs_data.txt", path_out, issues))

        for name, out_path, issues in outputs:
            prog(f"Saved: {out_path}")
            for iss in issues:
                prog(f"  UTF-8 validation warning: {iss}")

        src.cleanup()
        prog("Complete.")
        self._msg_queue.put(("done", None))


def run():
    root = tk.Tk()
    ExtractorGUI(root)
    root.mainloop()
