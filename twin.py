#!/usr/bin/env python3
"""Twin desktop interface for the Sift File Tools engine."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading

import dearpygui.dearpygui as dpg

from sift import find_engine, human

DUPLICATES = "Exact duplicates"
SEARCH = "Filename search"
results = queue.Queue()


def set_mode(sender=None, mode=DUPLICATES, user_data=None):
    searching = mode == SEARCH
    dpg.configure_item("search_options", show=searching)
    dpg.configure_item("scan_options", show=not searching)
    dpg.configure_item("run", label="Search filenames" if searching else "Scan for duplicates")
    dpg.delete_item("results", children_only=True)
    dpg.set_value("status", "Choose a folder and enter a search." if searching
                  else "Choose a folder to find files with identical contents.")


def browse():
    import tkinter as tk
    from tkinter import filedialog

    picker = tk.Tk()
    picker.withdraw()
    try:
        picker.attributes("-topmost", True)
        current = dpg.get_value("folder")
        folder = filedialog.askdirectory(parent=picker, initialdir=current or os.getcwd())
    finally:
        picker.destroy()
    if folder:
        dpg.set_value("folder", os.path.normpath(folder))


def run_job(mode, args):
    try:
        command = [find_engine(), *args]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        proc = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            **options,
        )
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or f"Sift exited with code {proc.returncode}.")
        results.put((mode, json.loads(proc.stdout), None))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, SystemExit) as exc:
        results.put((mode, None, str(exc)))


def run():
    if not dpg.is_item_enabled("run"):
        return

    folder = dpg.get_value("folder").strip().strip('"')
    if not folder or not os.path.isdir(os.path.expanduser(folder)):
        dpg.set_value("status", "Choose an existing folder.")
        return
    folder = os.path.abspath(os.path.expanduser(folder))

    mode = dpg.get_value("mode")
    if mode == SEARCH:
        query = dpg.get_value("query").strip()
        if not query:
            dpg.set_value("status", "Enter part of a filename to search for.")
            return
        setting, minimum = "top", 1
    else:
        setting, minimum = "min-size", 0

    try:
        raw = dpg.get_value(setting).strip()
        value = int(raw)
        if not raw.isascii() or not raw.isdecimal() or not minimum <= value < 2**64:
            raise ValueError
    except ValueError:
        label = "Maximum results" if mode == SEARCH else "Minimum file size"
        dpg.set_value("status", f"{label} must be a whole number of at least {minimum}.")
        return

    flags = [f"--{setting}", str(value)]
    if dpg.get_value("hidden"):
        flags.append("--hidden")
    if dpg.get_value("follow_links"):
        flags.append("--follow-links")

    if mode == SEARCH:
        args = ["find", *flags, "--", query, folder]
        status = "Searching filenames..."
    else:
        args = ["scan", *flags, "--", folder]
        status = "Scanning files and checking for exact duplicates..."

    dpg.set_value("status", status)
    dpg.delete_item("results", children_only=True)
    dpg.disable_item("run")
    dpg.disable_item("mode")
    threading.Thread(target=run_job, args=(mode, args), daemon=True).start()


def open_folder(sender, app_data, path):
    folder = os.path.dirname(path)
    try:
        if os.name == "nt":
            os.startfile(folder)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
    except OSError as exc:
        dpg.set_value("status", f"Could not open the folder. {exc}")


def copy_path(sender, app_data, path):
    dpg.set_clipboard_text(path)


def add_files(parent, entries, show_score=False):
    with dpg.table(parent=parent, header_row=True, row_background=True,
                   resizable=True, borders_innerV=True):
        dpg.add_table_column(label="Path", init_width_or_weight=1)
        if show_score:
            dpg.add_table_column(label="Score", width_fixed=True, init_width_or_weight=60)
        dpg.add_table_column(label="Actions", width_fixed=True, init_width_or_weight=175)

        for path, score in entries:
            with dpg.table_row():
                label = dpg.add_text(path)
                with dpg.tooltip(parent=label):
                    dpg.add_text(path, wrap=700)
                if show_score:
                    dpg.add_text(str(score))
                with dpg.group(horizontal=True):
                    dpg.add_button(label="Open folder", callback=open_folder, user_data=path)
                    dpg.add_button(label="Copy path", callback=copy_path, user_data=path)


def show_group(sender, app_data, group):
    dpg.delete_item("group_detail", children_only=True)
    dpg.add_text(f"{group['copies']:,} copies · {human(group['size'])} each",
                 parent="group_detail")
    add_files("group_detail", ((path, None) for path in group["files"]))


def show_results(mode, data):
    if mode == SEARCH:
        hits = data["results"]
        dpg.set_value("status", f"Showing {len(hits):,} of {data['files_scanned']:,} files searched")
        if hits:
            add_files("results", ((hit["path"], hit["score"]) for hit in hits), show_score=True)
        else:
            dpg.add_text("No matching filenames.", parent="results")
        return

    groups = data["groups"]
    group_label = "duplicate group" if len(groups) == 1 else "duplicate groups"
    dpg.set_value(
        "status",
        f"{len(groups):,} {group_label} · {human(data['total_wasted'])} reclaimable · "
        f"{data['files_scanned']:,} files scanned · {data['files_hashed']:,} hashed",
    )
    if not groups:
        dpg.add_text("No exact duplicates found.", parent="results")
        return

    with dpg.group(horizontal=True, parent="results"):
        with dpg.child_window(width=300, height=-1):
            for index, group in enumerate(groups, 1):
                dpg.add_button(
                    label=f"#{index:,}   {group['copies']:,} copies   ·   {human(group['wasted'])}",
                    width=-1, callback=show_group, user_data=group,
                )
        dpg.add_child_window(tag="group_detail", width=-1, height=-1)
    show_group(None, None, groups[0])


def process_result():
    try:
        mode, data, error = results.get_nowait()
    except queue.Empty:
        return
    dpg.enable_item("run")
    dpg.enable_item("mode")
    if error is not None:
        dpg.set_value("status", f"Sift could not finish. {error}")
    else:
        show_results(mode, data)


def build_ui():
    with dpg.window(tag="main"):
        dpg.add_text("SIFT FILE TOOLS", color=(141, 197, 253))
        with dpg.group(horizontal=True):
            dpg.add_text("Folder")
            dpg.add_button(label="Browse...", callback=browse)
            dpg.add_input_text(tag="folder", default_value=os.getcwd(), width=-1)
        dpg.add_radio_button((DUPLICATES, SEARCH), tag="mode", horizontal=True,
                             default_value=DUPLICATES, callback=set_mode)
        with dpg.group(horizontal=True, tag="scan_options"):
            dpg.add_text("Minimum file size (bytes)")
            dpg.add_input_text(tag="min-size", default_value="1", width=140, decimal=True)
        with dpg.group(horizontal=True, tag="search_options", show=False):
            dpg.add_text("Filename")
            dpg.add_input_text(tag="query", hint="e.g. rpt2024", width=270,
                               on_enter=True, callback=run)
            dpg.add_text("Maximum results")
            dpg.add_input_text(tag="top", default_value="50", width=90, decimal=True)
        with dpg.group(horizontal=True):
            dpg.add_checkbox(label="Include dot-prefixed files and folders", tag="hidden")
            dpg.add_checkbox(label="Follow symbolic links", tag="follow_links")
        dpg.add_button(tag="run", callback=run)
        dpg.add_text(tag="status", wrap=0)
        dpg.add_separator()
        dpg.add_child_window(tag="results", width=-1, height=-1)
    set_mode()


def main():
    dpg.create_context()
    try:
        dpg.configure_app(manual_callback_management=True)
        build_ui()
        dpg.create_viewport(title="Sift File Tools | Twin", width=1180, height=740,
                            min_width=740, min_height=430)
        dpg.setup_dearpygui()
        dpg.set_primary_window("main", True)
        dpg.show_viewport()
        while dpg.is_dearpygui_running():
            dpg.run_callbacks(dpg.get_callback_queue())
            process_result()
            dpg.render_dearpygui_frame()
    finally:
        dpg.destroy_context()


if __name__ == "__main__":
    main()
