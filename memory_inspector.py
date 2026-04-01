#!/usr/bin/env python3
"""
memory_inspector.py — GUI tool for inspecting the agent's memory systems.

Browse sessions, conversation history, long-term preferences, task blobs,
the flat memory file, and search across everything.

Usage:
    python memory_inspector.py

Requires: tkinter (included with Python on most systems)
"""

import os
import sys
import sqlite3
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from pathlib import Path
from datetime import datetime

# ── Resolve paths from config ─────────────────────────────────────────────────

SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = SCRIPT_DIR / "memory" / "agent.db"
MEMORY_FILE = SCRIPT_DIR / "memory" / "memory.txt"
LOGS_DIR = SCRIPT_DIR / "memory" / "logs"


def get_db() -> sqlite3.Connection | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── Main App ──────────────────────────────────────────────────────────────────

class MemoryInspector:
    def __init__(self):
        self._root = tk.Tk()
        self._root.title("Memory Inspector — Agent")
        self._root.geometry("1100x700")
        self._root.configure(bg="#1e1e1e")

        self._style = ttk.Style()
        self._style.theme_use("clam")
        self._configure_theme()

        # Top bar
        top = ttk.Frame(self._root)
        top.pack(fill="x", padx=8, pady=(8, 0))

        ttk.Label(top, text="Memory Inspector", font=("Helvetica", 14, "bold")).pack(side="left")

        ttk.Button(top, text="Refresh", command=self._refresh_current).pack(side="right", padx=4)

        db_label = f"DB: {DB_PATH}" if DB_PATH.exists() else "DB: not found"
        ttk.Label(top, text=db_label, foreground="#888").pack(side="right", padx=12)

        # Tabs
        self._notebook = ttk.Notebook(self._root)
        self._notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self._tabs = {}
        self._build_sessions_tab()
        self._build_conversation_tab()
        self._build_preferences_tab()
        self._build_blobs_tab()
        self._build_memory_file_tab()
        self._build_search_tab()
        self._build_logs_tab()

        self._notebook.bind("<<NotebookTabChanged>>", lambda e: self._refresh_current())
        self._refresh_current()

    def run(self):
        self._root.mainloop()

    def _configure_theme(self):
        s = self._style
        bg = "#1e1e1e"
        fg = "#d4d4d4"
        bg2 = "#252526"
        bg3 = "#2d2d2d"
        accent = "#3c8dbc"

        s.configure(".", background=bg, foreground=fg, fieldbackground=bg2)
        s.configure("TNotebook", background=bg)
        s.configure("TNotebook.Tab", background=bg3, foreground=fg, padding=[12, 4])
        s.map("TNotebook.Tab", background=[("selected", accent)], foreground=[("selected", "#fff")])
        s.configure("TFrame", background=bg)
        s.configure("TLabel", background=bg, foreground=fg)
        s.configure("TButton", background=bg3, foreground=fg)
        s.configure("Treeview", background=bg2, foreground=fg, fieldbackground=bg2, rowheight=24)
        s.configure("Treeview.Heading", background=bg3, foreground="#aaa")
        s.map("Treeview", background=[("selected", accent)], foreground=[("selected", "#fff")])

    # ── Sessions tab ──────────────────────────────────────────────────────

    def _build_sessions_tab(self):
        frame = ttk.Frame(self._notebook)
        self._notebook.add(frame, text="Sessions")
        self._tabs["sessions"] = frame

        # Tree
        cols = ("id", "started", "ended", "summary")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        tree.heading("id", text="Session ID")
        tree.heading("started", text="Started")
        tree.heading("ended", text="Ended")
        tree.heading("summary", text="Summary")
        tree.column("id", width=280, minwidth=200)
        tree.column("started", width=160)
        tree.column("ended", width=160)
        tree.column("summary", width=400)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._sessions_tree = tree

        # Detail pane
        detail = scrolledtext.ScrolledText(frame, height=6, bg="#252526", fg="#d4d4d4",
                                           insertbackground="#d4d4d4", wrap="word",
                                           font=("Consolas", 10))
        detail.pack(fill="x", padx=4, pady=4)
        self._sessions_detail = detail

        tree.bind("<<TreeviewSelect>>", self._on_session_select)

    def _load_sessions(self):
        tree = self._sessions_tree
        tree.delete(*tree.get_children())
        conn = get_db()
        if not conn:
            return
        try:
            rows = conn.execute(
                "SELECT id, started_at, ended_at, summary FROM sessions ORDER BY started_at DESC"
            ).fetchall()
            for r in rows:
                summary = (r["summary"] or "")[:120]
                tree.insert("", "end", values=(
                    r["id"], r["started_at"] or "", r["ended_at"] or "", summary
                ))
        finally:
            conn.close()

    def _on_session_select(self, event):
        tree = self._sessions_tree
        sel = tree.selection()
        if not sel:
            return
        sid = tree.item(sel[0])["values"][0]
        conn = get_db()
        if not conn:
            return
        try:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
            if row:
                text = f"Session: {row['id']}\n"
                text += f"Started: {row['started_at']}\n"
                text += f"Ended:   {row['ended_at'] or '(active)'}\n\n"
                text += f"Summary:\n{row['summary'] or '(none)'}"
                self._sessions_detail.delete("1.0", "end")
                self._sessions_detail.insert("1.0", text)
        finally:
            conn.close()

    # ── Conversation tab ──────────────────────────────────────────────────

    def _build_conversation_tab(self):
        frame = ttk.Frame(self._notebook)
        self._notebook.add(frame, text="Conversation")
        self._tabs["conversation"] = frame

        # Filter bar
        fbar = ttk.Frame(frame)
        fbar.pack(fill="x", padx=4, pady=4)

        ttk.Label(fbar, text="Type:").pack(side="left")
        self._conv_type_var = tk.StringVar(value="all")
        type_combo = ttk.Combobox(fbar, textvariable=self._conv_type_var, width=16,
                                  values=["all", "turn", "trivial_summary", "plan_record",
                                          "task_summary", "compression", "mod_action"])
        type_combo.pack(side="left", padx=4)
        type_combo.bind("<<ComboboxSelected>>", lambda e: self._load_conversation())

        ttk.Label(fbar, text="Limit:").pack(side="left", padx=(12, 0))
        self._conv_limit_var = tk.StringVar(value="100")
        ttk.Entry(fbar, textvariable=self._conv_limit_var, width=6).pack(side="left", padx=4)

        ttk.Button(fbar, text="Load", command=self._load_conversation).pack(side="left", padx=4)

        # Tree
        cols = ("id", "type", "role", "date", "content")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        tree.heading("id", text="ID")
        tree.heading("type", text="Type")
        tree.heading("role", text="Role")
        tree.heading("date", text="Date")
        tree.heading("content", text="Content")
        tree.column("id", width=50)
        tree.column("type", width=120)
        tree.column("role", width=70)
        tree.column("date", width=100)
        tree.column("content", width=700)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._conv_tree = tree

        # Detail
        detail = scrolledtext.ScrolledText(frame, height=8, bg="#252526", fg="#d4d4d4",
                                           insertbackground="#d4d4d4", wrap="word",
                                           font=("Consolas", 10))
        detail.pack(fill="x", padx=4, pady=4)
        self._conv_detail = detail

        tree.bind("<<TreeviewSelect>>", self._on_conv_select)

    def _load_conversation(self):
        tree = self._conv_tree
        tree.delete(*tree.get_children())
        conn = get_db()
        if not conn:
            return
        try:
            etype = self._conv_type_var.get()
            limit = int(self._conv_limit_var.get() or "100")

            if etype == "all":
                rows = conn.execute(
                    "SELECT id, entry_type, role, date, content FROM conversation "
                    "ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, entry_type, role, date, content FROM conversation "
                    "WHERE entry_type = ? ORDER BY created_at DESC LIMIT ?", (etype, limit)
                ).fetchall()

            for r in rows:
                content = (r["content"] or "").replace("\n", " ")[:150]
                tree.insert("", "end", values=(
                    r["id"], r["entry_type"], r["role"] or "", r["date"] or "", content
                ))
        finally:
            conn.close()

    def _on_conv_select(self, event):
        tree = self._conv_tree
        sel = tree.selection()
        if not sel:
            return
        row_id = tree.item(sel[0])["values"][0]
        conn = get_db()
        if not conn:
            return
        try:
            row = conn.execute("SELECT * FROM conversation WHERE id = ?", (row_id,)).fetchone()
            if row:
                text = f"ID: {row['id']}  |  Type: {row['entry_type']}  |  Role: {row['role'] or '-'}\n"
                text += f"Session: {row['session_id']}\n"
                text += f"Date: {row['date'] or '-'}  |  Created: {row['created_at']}\n"
                text += f"Summarized: {'yes' if row['summarized'] else 'no'}\n\n"
                text += row["content"]
                self._conv_detail.delete("1.0", "end")
                self._conv_detail.insert("1.0", text)
        finally:
            conn.close()

    # ── Preferences tab ───────────────────────────────────────────────────

    def _build_preferences_tab(self):
        frame = ttk.Frame(self._notebook)
        self._notebook.add(frame, text="Preferences")
        self._tabs["preferences"] = frame

        cols = ("key", "value", "updated")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        tree.heading("key", text="Key")
        tree.heading("value", text="Value")
        tree.heading("updated", text="Updated")
        tree.column("key", width=200)
        tree.column("value", width=600)
        tree.column("updated", width=200)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._prefs_tree = tree

    def _load_preferences(self):
        tree = self._prefs_tree
        tree.delete(*tree.get_children())
        conn = get_db()
        if not conn:
            return
        try:
            rows = conn.execute("SELECT key, value, updated_at FROM long_term ORDER BY key").fetchall()
            for r in rows:
                tree.insert("", "end", values=(r["key"], r["value"], r["updated_at"] or ""))
        finally:
            conn.close()

    # ── Blobs tab ─────────────────────────────────────────────────────────

    def _build_blobs_tab(self):
        frame = ttk.Frame(self._notebook)
        self._notebook.add(frame, text="Task Blobs")
        self._tabs["blobs"] = frame

        # Index tree
        cols = ("name", "summary", "tags", "date")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        tree.heading("name", text="Name")
        tree.heading("summary", text="Summary")
        tree.heading("tags", text="Tags")
        tree.heading("date", text="Date")
        tree.column("name", width=200)
        tree.column("summary", width=450)
        tree.column("tags", width=150)
        tree.column("date", width=100)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._blobs_tree = tree

        # Content viewer
        detail = scrolledtext.ScrolledText(frame, height=12, bg="#252526", fg="#d4d4d4",
                                           insertbackground="#d4d4d4", wrap="word",
                                           font=("Consolas", 10))
        detail.pack(fill="x", padx=4, pady=4)
        self._blobs_detail = detail

        tree.bind("<<TreeviewSelect>>", self._on_blob_select)

    def _load_blobs(self):
        tree = self._blobs_tree
        tree.delete(*tree.get_children())
        conn = get_db()
        if not conn:
            return
        try:
            rows = conn.execute(
                "SELECT blob_name, blob_summary, tags, date FROM blob_index "
                "ORDER BY date DESC, created_at DESC"
            ).fetchall()
            for r in rows:
                tree.insert("", "end", values=(
                    r["blob_name"], r["blob_summary"], r["tags"] or "", r["date"]
                ))
        finally:
            conn.close()

    def _on_blob_select(self, event):
        tree = self._blobs_tree
        sel = tree.selection()
        if not sel:
            return
        name = tree.item(sel[0])["values"][0]
        conn = get_db()
        if not conn:
            return
        try:
            row = conn.execute(
                "SELECT * FROM task_blobs WHERE name = ? LIMIT 1", (name,)
            ).fetchone()
            if row:
                text = f"Name: {row['name']}\n"
                text += f"Summary: {row['summary']}\n"
                text += f"Tags: {row['tags'] or '-'}\n"
                text += f"Date: {row['date']}  |  Task: {row['task_id']}\n"
                text += f"Session: {row['session_id']}\n"
                text += f"{'─' * 60}\n\n"
                text += row["content"]
                self._blobs_detail.delete("1.0", "end")
                self._blobs_detail.insert("1.0", text)
        finally:
            conn.close()

    # ── Memory file tab ───────────────────────────────────────────────────

    def _build_memory_file_tab(self):
        frame = ttk.Frame(self._notebook)
        self._notebook.add(frame, text="memory.txt")
        self._tabs["memory_file"] = frame

        info = ttk.Label(frame, text=f"File: {MEMORY_FILE}", foreground="#888")
        info.pack(anchor="w", padx=8, pady=(4, 0))

        text = scrolledtext.ScrolledText(frame, bg="#252526", fg="#d4d4d4",
                                         insertbackground="#d4d4d4", wrap="word",
                                         font=("Consolas", 10))
        text.pack(fill="both", expand=True, padx=4, pady=4)
        self._memory_file_text = text

    def _load_memory_file(self):
        self._memory_file_text.delete("1.0", "end")
        if MEMORY_FILE.exists():
            content = MEMORY_FILE.read_text(encoding="utf-8")
            self._memory_file_text.insert("1.0", content or "(empty)")

            lines = len(content.splitlines())
            size = MEMORY_FILE.stat().st_size
            self._memory_file_text.insert("end", f"\n\n{'─' * 40}\n{lines} lines  |  {size:,} bytes")
        else:
            self._memory_file_text.insert("1.0", "(file does not exist)")

    # ── Search tab ────────────────────────────────────────────────────────

    def _build_search_tab(self):
        frame = ttk.Frame(self._notebook)
        self._notebook.add(frame, text="Search")
        self._tabs["search"] = frame

        # Search bar
        sbar = ttk.Frame(frame)
        sbar.pack(fill="x", padx=4, pady=4)

        ttk.Label(sbar, text="Query:").pack(side="left")
        self._search_var = tk.StringVar()
        entry = ttk.Entry(sbar, textvariable=self._search_var, width=50)
        entry.pack(side="left", padx=4)
        entry.bind("<Return>", lambda e: self._run_search())

        ttk.Button(sbar, text="Search", command=self._run_search).pack(side="left", padx=4)

        # Results
        results = scrolledtext.ScrolledText(frame, bg="#252526", fg="#d4d4d4",
                                             insertbackground="#d4d4d4", wrap="word",
                                             font=("Consolas", 10))
        results.pack(fill="both", expand=True, padx=4, pady=4)
        self._search_results = results

    def _run_search(self):
        query = self._search_var.get().strip()
        if not query:
            return

        self._search_results.delete("1.0", "end")
        results: list[str] = []
        keyword = f"%{query}%"

        conn = get_db()
        if conn:
            try:
                # Conversation
                rows = conn.execute(
                    "SELECT id, entry_type, role, content, date FROM conversation "
                    "WHERE content LIKE ? ORDER BY created_at DESC LIMIT 20",
                    (keyword,)
                ).fetchall()
                if rows:
                    results.append(f"── Conversation ({len(rows)} matches) ──")
                    for r in rows:
                        prefix = f"[{r['entry_type']}"
                        if r["role"]:
                            prefix += f"/{r['role']}"
                        prefix += "]"
                        content = r["content"].replace("\n", " ")[:200]
                        results.append(f"  {prefix} {content}")
                    results.append("")

                # Preferences
                rows = conn.execute(
                    "SELECT key, value FROM long_term WHERE key LIKE ? OR value LIKE ?",
                    (keyword, keyword)
                ).fetchall()
                if rows:
                    results.append(f"── Preferences ({len(rows)} matches) ──")
                    for r in rows:
                        results.append(f"  {r['key']}: {r['value']}")
                    results.append("")

                # Blob index
                rows = conn.execute(
                    "SELECT blob_name, blob_summary, tags, date FROM blob_index "
                    "WHERE blob_name LIKE ? OR blob_summary LIKE ? OR tags LIKE ? "
                    "ORDER BY date DESC LIMIT 20",
                    (keyword, keyword, keyword)
                ).fetchall()
                if rows:
                    results.append(f"── Task Blobs ({len(rows)} matches) ──")
                    for r in rows:
                        results.append(f"  {r['blob_name']} — {r['blob_summary']} [{r['date']}]")
                    results.append("")

                # Sessions
                rows = conn.execute(
                    "SELECT id, started_at, summary FROM sessions "
                    "WHERE summary LIKE ? ORDER BY started_at DESC LIMIT 10",
                    (keyword,)
                ).fetchall()
                if rows:
                    results.append(f"── Sessions ({len(rows)} matches) ──")
                    for r in rows:
                        summary = (r["summary"] or "")[:150]
                        results.append(f"  [{r['started_at']}] {summary}")
                    results.append("")

            finally:
                conn.close()

        # Memory file
        if MEMORY_FILE.exists():
            content = MEMORY_FILE.read_text(encoding="utf-8")
            matched = [
                line.strip() for line in content.splitlines()
                if query.lower() in line.lower()
            ]
            if matched:
                results.append(f"── memory.txt ({len(matched)} matches) ──")
                for line in matched[:20]:
                    results.append(f"  {line}")
                results.append("")

        if results:
            self._search_results.insert("1.0", "\n".join(results))
        else:
            self._search_results.insert("1.0", f"No results for '{query}'")

    # ── Logs tab ──────────────────────────────────────────────────────────

    def _build_logs_tab(self):
        frame = ttk.Frame(self._notebook)
        self._notebook.add(frame, text="Session Logs")
        self._tabs["logs"] = frame

        # Log file list
        list_frame = ttk.Frame(frame)
        list_frame.pack(side="left", fill="y", padx=(4, 0), pady=4)

        ttk.Label(list_frame, text="Log files:").pack(anchor="w")
        self._logs_listbox = tk.Listbox(list_frame, width=32, bg="#252526", fg="#d4d4d4",
                                         selectbackground="#3c8dbc", font=("Consolas", 9))
        self._logs_listbox.pack(fill="y", expand=True)
        self._logs_listbox.bind("<<ListboxSelect>>", self._on_log_select)

        # Log content
        text = scrolledtext.ScrolledText(frame, bg="#252526", fg="#d4d4d4",
                                         insertbackground="#d4d4d4", wrap="word",
                                         font=("Consolas", 10))
        text.pack(fill="both", expand=True, padx=4, pady=4)
        self._logs_text = text

    def _load_logs(self):
        self._logs_listbox.delete(0, "end")
        if LOGS_DIR.exists():
            files = sorted(LOGS_DIR.glob("*.log"), reverse=True)
            for f in files:
                self._logs_listbox.insert("end", f.name)

    def _on_log_select(self, event):
        sel = self._logs_listbox.curselection()
        if not sel:
            return
        name = self._logs_listbox.get(sel[0])
        path = LOGS_DIR / name
        self._logs_text.delete("1.0", "end")
        if path.exists():
            content = path.read_text(encoding="utf-8")
            self._logs_text.insert("1.0", content)

    # ── Refresh logic ─────────────────────────────────────────────────────

    def _refresh_current(self):
        tab_idx = self._notebook.index("current")
        tab_name = self._notebook.tab(tab_idx, "text")

        if tab_name == "Sessions":
            self._load_sessions()
        elif tab_name == "Conversation":
            self._load_conversation()
        elif tab_name == "Preferences":
            self._load_preferences()
        elif tab_name == "Task Blobs":
            self._load_blobs()
        elif tab_name == "memory.txt":
            self._load_memory_file()
        elif tab_name == "Session Logs":
            self._load_logs()


def main():
    if not DB_PATH.exists() and not MEMORY_FILE.exists():
        print(f"No memory data found at {DB_PATH.parent}")
        print("Run the agent first to create the database.")

    app = MemoryInspector()
    app.run()


if __name__ == "__main__":
    main()