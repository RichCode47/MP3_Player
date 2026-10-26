import ctypes
import json
import math
import os
import sys
import tkinter as tk
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pygame
from PIL import Image, ImageTk

from library import read_track_metadata, scan_music_folder, track_ids
from persistence import read_playlist, validate_playlist, write_json_atomic
from playback import PlaybackEngine, PlaybackState
from playlist_queue import PlaylistQueue, RepeatMode


class MP3Player:
    def __init__(self, root):
        self.root = root
        self.ready = False
        self.closing = False
        self.app_menu = None
        self.executor = None
        self.background_jobs = {}
        self.background_after_id = None
        self.pending_track_id = None
        self.playback = PlaybackEngine()
        self.root.title("MP3 Player")
        self.root.geometry("1180x740")
        self.root.minsize(900, 600)
        self.root.configure(bg="#000000")

        self.songs = []
        self.song_ids = []
        self.song_index_by_id = {}
        self.folder_order = []
        self.displayed_song_indices = []
        self.displayed_songs = []
        self.library_category = tk.StringVar(value="Tracks")
        self.library_items = []
        self.library_folder_filter = None
        self.library_expanded = False
        self.current_playlist_name = "Current playlist"
        self.sort_order = "name_asc"
        self.current_song = None
        self.current_song_index = None
        self.playlist_queue = PlaylistQueue()
        self.settings_path = self.get_settings_path()
        self.volume_save_after_id = None
        self.volume_level = self.load_volume_setting()
        self.cover_thumbnail = None
        self.cover_image = None
        self.large_cover_image = None
        self.seeking = False
        self.progress_after_id = None
        self.search_placeholder_visible = False

        try:
            self.playback.initialize(self.volume_level)
        except pygame.error as error:
            messagebox.showerror(
                "Audio Error",
                f"Could not initialize the audio device:\n{error}",
                parent=self.root
            )
            self.playback.shutdown()
            self.root.destroy()
            return

        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mp3-files")
        self.build_interface()
        self.refresh_library()
        try:
            self.configure_custom_title_bar()
        except OSError as error:
            self.window_controls.grid_remove()
            self.status.set(f"Using the system title bar: {error}")
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.background_after_id = self.root.after(50, self.process_background_tasks)
        self.ready = True

    @property
    def paused(self):
        return self.playback.state == PlaybackState.PAUSED

    @property
    def track_duration(self):
        return self.playback.duration

    def cancel_file_job(self, kind):
        job = self.background_jobs.pop(kind, None)
        if job is not None:
            job[0].cancel()

    def submit_file_job(self, kind, operation, callback, description):
        self.cancel_file_job(kind)
        future = self.executor.submit(operation)
        self.background_jobs[kind] = (future, callback, description)

    def process_background_tasks(self):
        self.background_after_id = None
        if self.closing:
            return
        for kind, (future, callback, description) in list(self.background_jobs.items()):
            current = self.background_jobs.get(kind)
            if current is None or current[0] is not future:
                continue
            if not future.done():
                continue
            self.background_jobs.pop(kind)
            try:
                result = future.result()
            except Exception as error:
                # Worker failures are reported on Tk's thread, never in a worker.
                if kind == "track":
                    self.pending_track_id = None
                self.status.set(f"Could not {description}: {error}")
                messagebox.showerror("File Error", f"Could not {description}:\n{error}", parent=self.root)
            else:
                callback(result)
        self.background_after_id = self.root.after(50, self.process_background_tasks)

    def install_library(self, songs, folder_order, sort_order, name):
        self.stop_music()
        self.songs = list(songs)
        self.folder_order = list(folder_order)
        self.current_playlist_name = name
        self.library_folder_filter = None
        self.displayed_song_indices = []
        self.displayed_songs = []
        self.playlist.delete(0, tk.END)
        self.library_category.set("Tracks")
        self.sort_order = sort_order
        self.sort_var.set({
            "name_asc": "Name (A-Z)", "name_desc": "Name (Z-A)", "folder": "Folder order"
        }[sort_order])
        self.set_search_query("")
        self.apply_sort_order()
        self.playlist_queue.set_tracks(self.song_ids)
        if self.songs:
            self.playlist.selection_set(0)
            self.playlist.activate(0)
            self.status.set(f"Loaded {len(self.songs)} MP3 file(s).")
        else:
            self.status.set("No available music files found.")

    def playlist_loaded(self, path, result):
        songs, order, sort, missing = result
        self.install_library(songs, order, sort, Path(path).stem)
        if missing:
            preview = "\n".join(os.path.basename(song) for song in missing[:5])
            remainder = f"\n...and {len(missing) - 5} more." if len(missing) > 5 else ""
            messagebox.showwarning(
                "Missing Music Files",
                f"{len(missing)} file(s) could not be found and were skipped:\n{preview}{remainder}",
                parent=self.root
            )

    def play_prepared_track(self, identity, metadata):
        index = self.song_index_by_id.get(identity)
        if identity != self.pending_track_id or index is None:
            return
        self.pending_track_id = None
        self.playlist_queue.select(identity)
        song = self.songs[index]
        try:
            self.playback.play(song, metadata.duration, self.volume_level)
        except (pygame.error, OSError) as error:
            self.status.set("Playback failed.")
            messagebox.showerror("Playback Error", f"Could not play this file:\n{error}", parent=self.root)
            self.update_track_display()
            return
        self.current_song = song
        self.current_song_index = index
        self.cover_thumbnail = metadata.artwork
        self.refresh_playlist(preferred_index=index)
        self.update_track_display()
        status = f"Playing: {os.path.basename(song)}"
        if metadata.duration is None:
            status += " (duration unavailable; seeking disabled)"
        self.status.set(status)

    @staticmethod
    def get_settings_path():
        if sys.platform == "win32":
            config_directory = Path(
                os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
            )
        elif sys.platform == "darwin":
            config_directory = Path.home() / "Library" / "Application Support"
        else:
            config_directory = Path(
                os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            )
        return config_directory / "MP3_Player" / "settings.json"

    def load_volume_setting(self):
        try:
            with self.settings_path.open("r", encoding="utf-8") as settings_file:
                settings = json.load(settings_file)
        except FileNotFoundError:
            return 100.0
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            messagebox.showwarning(
                "Volume Settings",
                f"Could not read saved volume settings; using 100% volume.\n"
                f"{error}",
                parent=self.root
            )
            return 100.0

        volume = settings.get("volume") if isinstance(settings, dict) else None
        if (
            isinstance(volume, bool)
            or not isinstance(volume, (int, float))
            or not 0 <= volume <= 100
            or not math.isfinite(volume)
        ):
            messagebox.showwarning(
                "Volume Settings",
                "Saved volume setting is invalid; using 100% volume.",
                parent=self.root
            )
            return 100.0
        return float(volume)

    def save_volume_setting(self):
        write_json_atomic(self.settings_path, {"volume": self.volume_level})

    def schedule_volume_save(self):
        if self.volume_save_after_id is not None:
            self.root.after_cancel(self.volume_save_after_id)
        self.volume_save_after_id = self.root.after(
            350,
            self.persist_volume_setting
        )

    def persist_volume_setting(self):
        self.volume_save_after_id = None
        try:
            self.save_volume_setting()
        except OSError as error:
            self.status.set(f"Volume changed but could not be saved: {error}")

    def build_interface(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            "Dark.TCombobox",
            fieldbackground="#242424",
            background="#242424",
            foreground="#ffffff",
            arrowcolor="#ffffff",
            bordercolor="#242424"
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", "#242424")],
            foreground=[("readonly", "#ffffff")]
        )

        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="Load a folder to get started.")
        self.shuffle_var = tk.BooleanVar(value=self.playlist_queue.shuffle)
        self.repeat_var = tk.StringVar(value=self.playlist_queue.repeat_mode.value)

        top_bar = tk.Frame(self.root, bg="#050505", height=58)
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.grid_propagate(False)
        top_bar.columnconfigure(1, weight=1)
        self.top_bar = top_bar

        navigation = tk.Frame(top_bar, bg="#050505")
        navigation.grid(row=0, column=0, padx=(14, 8), sticky="w")
        navigation.bind("<ButtonPress-1>", self.start_window_drag)
        menu_button = self._make_button(
            navigation, "•••", self.show_app_menu
        )
        menu_button.configure(width=3, font=("Arial", 12, "bold"))
        menu_button.pack(side=tk.LEFT, padx=(0, 9))
        for label, command in (
            ("‹", self.previous_music),
            ("›", self.next_music)
        ):
            button = self._make_button(navigation, label, command)
            button.configure(
                width=2,
                font=("Arial", 20),
                padx=2,
                pady=0,
                fg="#aaaaaa"
            )
            button.pack(side=tk.LEFT, padx=2)

        search_box = tk.Frame(top_bar, bg="#242424")
        search_box.grid(row=0, column=1, padx=18, pady=8, sticky="ew")
        search_box.bind("<ButtonPress-1>", self.start_window_drag)
        search_box.columnconfigure(2, weight=1)
        home_button = self._make_button(
            search_box, "⌂", self.go_home, accent=True
        )
        home_button.configure(
            font=("Segoe UI Symbol", 19, "bold"),
            width=3,
            padx=4,
            pady=2
        )
        home_button.grid(row=0, column=0, padx=(0, 10))
        tk.Label(
            search_box,
            text="⌕",
            font=("Arial", 20),
            bg="#242424",
            fg="#aaaaaa",
            padx=4
        ).grid(row=0, column=1, sticky="w")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.filter_playlist)
        self.search_entry = tk.Entry(
            search_box,
            textvariable=self.search_var,
            bg="#242424",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            font=("Arial", 11)
        )
        self.search_entry.grid(
            row=0, column=2, sticky="ew", ipady=8, padx=(2, 14)
        )
        self.search_placeholder = "What do you want to play?"
        self.search_entry.bind("<FocusIn>", self.clear_search_placeholder)
        self.search_entry.bind("<FocusOut>", self.restore_search_placeholder)
        self.search_entry.bind("<Escape>", self.clear_search)
        self.restore_search_placeholder()

        right_controls = tk.Frame(top_bar, bg="#050505")
        right_controls.grid(row=0, column=2, padx=(4, 0), sticky="e")
        help_button = self._make_button(right_controls, "?", self.show_help)
        help_button.configure(width=3, font=("Arial", 11, "bold"))
        help_button.pack(side=tk.LEFT, padx=3)
        profile_button = self._make_button(
            right_controls, "M", self.show_about, accent=True
        )
        profile_button.configure(
            width=3,
            font=("Arial", 11, "bold"),
            relief=tk.FLAT,
            highlightthickness=2,
            highlightbackground="#1ed760"
        )
        profile_button.pack(side=tk.LEFT, padx=(7, 12))

        self.window_controls = tk.Frame(top_bar, bg="#050505")
        self.window_controls.grid(row=0, column=3, sticky="e")
        for label, command in (
            ("_", self.minimize_window),
            ("□", self.toggle_maximize_window),
            ("X", self.close_app)
        ):
            button = tk.Button(
                self.window_controls,
                text=label,
                command=command,
                bg="#050505",
                fg="#dddddd",
                activebackground="#333333",
                activeforeground="#ffffff",
                font=("Arial", 10),
                relief=tk.FLAT,
                width=5,
                height=2,
                bd=0,
                cursor="hand2"
            )
            if label == "X":
                button.configure(
                    activebackground="#c42b1c",
                    activeforeground="#ffffff"
                )
            button.pack(side=tk.LEFT, fill=tk.Y)
        top_bar.columnconfigure(3, weight=0)
        top_bar.bind("<ButtonPress-1>", self.start_window_drag)

        workspace = tk.Frame(self.root, bg="#000000")
        workspace.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 8))
        workspace.rowconfigure(0, weight=1)
        workspace.columnconfigure(0, minsize=260, weight=0)
        workspace.columnconfigure(1, weight=1)
        workspace.columnconfigure(2, minsize=225, weight=0)
        self.workspace = workspace

        library = tk.Frame(workspace, bg="#121212")
        library.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        library.columnconfigure(0, weight=1)
        library.rowconfigure(3, weight=1)

        library_header = tk.Frame(library, bg="#121212")
        library_header.grid(
            row=0, column=0, padx=16, pady=(17, 12), sticky="ew"
        )
        library_header.columnconfigure(0, weight=1)
        tk.Label(
            library_header,
            text="Your Library",
            font=("Arial", 15, "bold"),
            bg="#121212",
            fg="#ffffff"
        ).grid(row=0, column=0, sticky="w")
        add_button = self._make_button(
            library_header, "+", self.load_music
        )
        add_button.configure(font=("Arial", 18), width=2, padx=3, pady=1)
        add_button.grid(row=0, column=1, padx=(8, 2))
        expand_button = self._make_button(
            library_header, "↗", self.toggle_library_expansion
        )
        expand_button.configure(font=("Arial", 12), width=2, padx=3, pady=5)
        expand_button.grid(row=0, column=2, padx=(2, 0))
        self.library_expand_button = expand_button

        category_bar = tk.Frame(library, bg="#121212")
        category_bar.grid(
            row=1, column=0, padx=10, pady=(0, 9), sticky="ew"
        )
        self.library_category_buttons = {}
        for category in ("Tracks", "Folders", "Playlists"):
            button = tk.Button(
                category_bar,
                text=category,
                command=lambda value=category: self.set_library_category(
                    value
                ),
                bg="#292929",
                fg="#ffffff",
                activebackground="#3a3a3a",
                activeforeground="#ffffff",
                font=("Arial", 9, "bold"),
                relief=tk.FLAT,
                padx=8,
                pady=7,
                cursor="hand2"
            )
            button.pack(side=tk.LEFT, padx=2)
            self.library_category_buttons[category] = button

        library_actions = tk.Frame(library, bg="#121212")
        library_actions.grid(
            row=2, column=0, padx=12, pady=(0, 8), sticky="ew"
        )
        library_actions.columnconfigure(0, weight=1)
        library_actions.columnconfigure(1, weight=1)
        self._make_button(
            library_actions, "Open playlist", self.open_playlist
        ).grid(row=0, column=0, padx=2, sticky="ew")
        self._make_button(
            library_actions, "Save playlist", self.save_playlist
        ).grid(row=0, column=1, padx=2, sticky="ew")

        library_list_frame = tk.Frame(library, bg="#121212")
        library_list_frame.grid(
            row=3, column=0, padx=(12, 6), pady=(0, 8), sticky="nsew"
        )
        library_list_frame.rowconfigure(0, weight=1)
        library_list_frame.columnconfigure(0, weight=1)
        self.library_list = tk.Listbox(
            library_list_frame,
            bg="#121212",
            fg="#eeeeee",
            selectbackground="#292929",
            selectforeground="#ffffff",
            exportselection=False,
            activestyle="none",
            font=("Arial", 10),
            relief=tk.FLAT,
            highlightthickness=0
        )
        self.library_list.grid(row=0, column=0, sticky="nsew")
        library_scrollbar = tk.Scrollbar(
            library_list_frame,
            command=self.library_list.yview
        )
        library_scrollbar.grid(row=0, column=1, sticky="ns")
        self.library_list.configure(yscrollcommand=library_scrollbar.set)
        self.library_list.bind(
            "<<ListboxSelect>>",
            self.select_library_item
        )
        self.library_list.bind(
            "<Double-Button-1>",
            self.activate_library_item
        )
        self.library_count = tk.Label(
            library,
            text="0 tracks",
            font=("Arial", 9),
            bg="#121212",
            fg="#777777"
        )
        self.library_count.grid(
            row=4, column=0, padx=16, pady=(0, 14), sticky="w"
        )

        center = tk.Frame(workspace, bg="#151515")
        center.grid(row=0, column=1, sticky="nsew", padx=0)
        center.columnconfigure(0, weight=1)
        center.rowconfigure(2, weight=1)

        tk.Label(
            center,
            text="Your music",
            font=("Arial", 24, "bold"),
            bg="#151515",
            fg="#ffffff"
        ).grid(row=0, column=0, padx=26, pady=(24, 4), sticky="w")
        self.status_label = tk.Label(
            center,
            textvariable=self.status,
            font=("Arial", 10),
            bg="#151515",
            fg="#b3b3b3"
        )
        self.status_label.grid(
            row=1, column=0, padx=28, pady=(0, 17), sticky="w"
        )

        playlist_frame = tk.Frame(center, bg="#151515")
        playlist_frame.grid(
            row=2, column=0, padx=18, pady=(0, 12), sticky="nsew"
        )
        playlist_frame.columnconfigure(0, weight=1)
        playlist_frame.rowconfigure(1, weight=1)

        playlist_header = tk.Frame(playlist_frame, bg="#151515")
        playlist_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        playlist_header.columnconfigure(0, weight=1)
        tk.Label(
            playlist_header,
            text="TRACKS",
            font=("Arial", 9, "bold"),
            bg="#151515",
            fg="#b3b3b3"
        ).grid(row=0, column=0, padx=10, sticky="w")

        self.sort_var = tk.StringVar(value="Name (A-Z)")
        sort_control = ttk.Combobox(
            playlist_header,
            textvariable=self.sort_var,
            values=("Name (A-Z)", "Name (Z-A)", "Folder order"),
            state="readonly",
            width=16,
            style="Dark.TCombobox"
        )
        sort_control.grid(row=0, column=1, padx=6, sticky="e")
        sort_control.bind("<<ComboboxSelected>>", self.change_sort_order)

        list_container = tk.Frame(playlist_frame, bg="#151515")
        list_container.grid(row=1, column=0, sticky="nsew")
        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)
        scrollbar = tk.Scrollbar(list_container)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.playlist = tk.Listbox(
            list_container,
            bg="#151515",
            fg="#eeeeee",
            selectbackground="#333333",
            selectforeground="#ffffff",
            exportselection=False,
            activestyle="none",
            font=("Arial", 11),
            relief=tk.FLAT,
            highlightthickness=0,
            yscrollcommand=scrollbar.set
        )
        self.playlist.grid(row=0, column=0, sticky="nsew")
        scrollbar.config(command=self.playlist.yview)
        self.playlist.bind("<Double-Button-1>", self.play_music)
        self.playlist.bind("<Return>", self.play_music)

        activity = tk.Frame(workspace, bg="#121212")
        activity.grid(row=0, column=2, sticky="nsew", padx=(7, 0))
        activity.columnconfigure(0, weight=1)

        tk.Label(
            activity,
            text="Now playing",
            font=("Arial", 15, "bold"),
            bg="#121212",
            fg="#ffffff"
        ).grid(row=0, column=0, padx=18, pady=(20, 18), sticky="w")

        self.large_cover_art = tk.Label(
            activity,
            text="MP3",
            font=("Arial", 28, "bold"),
            bg="#263b35",
            fg="#d9eee4",
            width=8,
            height=5
        )
        self.large_cover_art.grid(row=1, column=0, padx=18, pady=(0, 17), sticky="ew")

        self.now_playing_title = tk.Label(
            activity,
            text="Nothing playing",
            font=("Arial", 15, "bold"),
            bg="#121212",
            fg="#ffffff",
            wraplength=190,
            justify=tk.LEFT,
            anchor="w"
        )
        self.now_playing_title.grid(
            row=2, column=0, padx=18, pady=(0, 5), sticky="ew"
        )
        self.now_playing_location = tk.Label(
            activity,
            text="Choose a track from your library.",
            font=("Arial", 10),
            bg="#121212",
            fg="#b3b3b3",
            wraplength=190,
            justify=tk.LEFT,
            anchor="w"
        )
        self.now_playing_location.grid(
            row=3, column=0, padx=18, pady=(0, 17), sticky="ew"
        )

        tk.Frame(activity, bg="#282828", height=1).grid(
            row=4, column=0, padx=18, pady=(0, 16), sticky="ew"
        )
        tk.Label(
            activity,
            text="YOUR PLAYLIST",
            font=("Arial", 9, "bold"),
            bg="#121212",
            fg="#b3b3b3"
        ).grid(row=5, column=0, padx=18, pady=(0, 8), sticky="w")
        tk.Label(
            activity,
            text="Search tracks, choose a sort order, and use the playback controls below to move through your local music.",
            font=("Arial", 10),
            bg="#121212",
            fg="#a7a7a7",
            justify=tk.LEFT,
            wraplength=190
        ).grid(row=6, column=0, padx=18, pady=(0, 18), sticky="nw")

        player_bar = tk.Frame(self.root, bg="#050505", height=100)
        player_bar.grid(row=2, column=0, sticky="ew")
        player_bar.grid_propagate(False)
        player_bar.columnconfigure(0, weight=1)
        player_bar.columnconfigure(1, weight=2)
        player_bar.columnconfigure(2, weight=1)

        track_info = tk.Frame(player_bar, bg="#050505")
        track_info.grid(row=0, column=0, padx=14, sticky="w")
        self.cover_art = tk.Label(
            track_info,
            text="♫",
            font=("Segoe UI Symbol", 23),
            bg="#263b35",
            fg="#d9eee4",
            width=3,
            height=2
        )
        self.cover_art.pack(side=tk.LEFT, padx=(0, 10))
        track_text = tk.Frame(track_info, bg="#050505", width=210)
        track_text.pack(side=tk.LEFT, fill=tk.Y)
        track_text.pack_propagate(False)
        self.bottom_track_title = tk.Label(
            track_text,
            text="Nothing playing",
            font=("Arial", 10, "bold"),
            bg="#050505",
            fg="#ffffff",
            anchor="w",
            wraplength=205
        )
        self.bottom_track_title.pack(anchor="w", fill=tk.X, pady=(13, 0))
        self.bottom_track_location = tk.Label(
            track_text,
            text="Load a music folder to begin",
            font=("Arial", 9),
            bg="#050505",
            fg="#a7a7a7",
            anchor="w",
            wraplength=205
        )
        self.bottom_track_location.pack(anchor="w", fill=tk.X, pady=(3, 0))

        player_center = tk.Frame(player_bar, bg="#050505")
        player_center.grid(row=0, column=1, pady=(8, 4), sticky="nsew")
        player_center.columnconfigure(0, weight=1)
        transport = tk.Frame(player_center, bg="#050505")
        transport.grid(row=0, column=0, pady=(0, 3))
        self.shuffle_button = self._make_button(
            transport, "SH", self.toggle_shuffle
        )
        self.shuffle_button.grid(row=0, column=0, padx=5)
        for column, (label, command, symbol) in enumerate((
            ("Previous", self.previous_music, "|◀"),
            ("Play or pause", self.toggle_playback, "▶"),
            ("Stop", self.stop_music, "■"),
            ("Next", self.next_music, "▶|")
        ), start=1):
            self._make_button(
                transport,
                symbol,
                command,
                accent=(label == "Play or pause")
            ).grid(row=0, column=column, padx=7)
        self.playback_button = transport.grid_slaves(row=0, column=2)[0]
        self.playback_button.configure(
            font=("Segoe UI Symbol", 12, "bold"),
            width=3,
            height=1,
            padx=2,
            pady=5
        )
        self.repeat_button = self._make_button(
            transport, "RPT", self.cycle_repeat_mode
        )
        self.repeat_button.grid(row=0, column=5, padx=5)
        self.update_playback_mode_controls()

        progress = tk.Frame(player_center, bg="#050505")
        progress.grid(row=1, column=0, sticky="ew")
        progress.columnconfigure(1, weight=1)
        self.elapsed_label = tk.Label(
            progress,
            text="0:00",
            font=("Arial", 8),
            bg="#050505",
            fg="#b3b3b3",
            width=5
        )
        self.elapsed_label.grid(row=0, column=0, padx=(0, 5))
        self.seek_var = tk.DoubleVar(value=0)
        self.seek_scale = tk.Scale(
            progress,
            variable=self.seek_var,
            from_=0,
            to=1,
            orient=tk.HORIZONTAL,
            showvalue=False,
            resolution=0.1,
            command=self.on_seek_drag,
            bg="#050505",
            fg="#ffffff",
            troughcolor="#535353",
            activebackground="#1ed760",
            highlightthickness=0,
            bd=0,
            sliderlength=10,
            width=5,
            state=tk.DISABLED
        )
        self.seek_scale.grid(row=0, column=1, sticky="ew")
        self.seek_scale.bind("<ButtonPress-1>", self.begin_seek)
        self.seek_scale.bind("<ButtonRelease-1>", self.seek_music)
        for key in ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next"):
            self.seek_scale.bind(f"<KeyPress-{key}>", self.seek_with_keyboard)
        self.duration_label = tk.Label(
            progress,
            text="--:--",
            font=("Arial", 8),
            bg="#050505",
            fg="#b3b3b3",
            width=5
        )
        self.duration_label.grid(row=0, column=2, padx=(5, 0))

        volume_frame = tk.Frame(player_bar, bg="#050505")
        volume_frame.grid(row=0, column=2, padx=16, sticky="e")
        tk.Label(
            volume_frame,
            text="VOL",
            font=("Arial", 8, "bold"),
            bg="#050505",
            fg="#a7a7a7"
        ).pack(side=tk.LEFT, padx=(0, 7))
        self.volume_scale = tk.Scale(
            volume_frame,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            showvalue=False,
            command=self.set_volume,
            bg="#050505",
            fg="#ffffff",
            troughcolor="#535353",
            activebackground="#1ed760",
            highlightthickness=0,
            bd=0,
            sliderlength=10,
            width=5,
            length=100
        )
        self.volume_scale.set(self.volume_level)
        self.volume_scale.pack(side=tk.LEFT)
        self.volume_scale.bind("<ButtonPress-1>", self.on_volume_pointer)
        self.volume_scale.bind("<B1-Motion>", self.on_volume_pointer)
        self.progress_after_id = self.root.after(250, self.update_progress)

    @staticmethod
    def _make_button(parent, text, command, accent=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg="#1ed760" if accent else "#282828",
            fg="#101010" if accent else "#eeeeee",
            activebackground="#1ed760" if accent else "#3a3a3a",
            activeforeground="#101010" if accent else "#ffffff",
            font=("Arial", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=8,
            cursor="hand2"
        )

    def set_library_category(self, category):
        if category not in self.library_category_buttons:
            return
        self.library_category.set(category)
        if category in ("Tracks", "Playlists"):
            self.library_folder_filter = None
            self.set_search_query("")
        self.refresh_library()
        self.refresh_playlist()

    def toggle_library_expansion(self):
        self.library_expanded = not self.library_expanded
        self.workspace.columnconfigure(
            0,
            minsize=340 if self.library_expanded else 260
        )
        self.library_expand_button.configure(
            text="↙" if self.library_expanded else "↗"
        )

    def refresh_library(self):
        category = self.library_category.get()
        selection = self.library_list.curselection()
        selected = (
            self.library_items[selection[0]]
            if selection and selection[0] < len(self.library_items) else None
        )
        for name, button in self.library_category_buttons.items():
            active = name == category
            button.configure(
                bg="#ffffff" if active else "#292929",
                fg="#111111" if active else "#ffffff",
                activebackground="#dddddd" if active else "#3a3a3a",
                activeforeground="#111111" if active else "#ffffff"
            )
        items, labels = [], []
        if category == "Tracks":
            for identity, song in zip(self.song_ids, self.songs):
                folder = os.path.basename(os.path.dirname(song)) or song
                items.append(("track", identity))
                labels.append(f"♪  {os.path.basename(song)}  ·  {folder}")
            empty_label = "Load a folder to see tracks."
        elif category == "Folders":
            counts = Counter(os.path.dirname(song) for song in self.songs)
            for folder, count in sorted(counts.items(), key=lambda item: item[0].casefold()):
                items.append(("folder", folder))
                name = os.path.basename(folder) or folder
                labels.append(f"▸  {name}  ·  {count} track{'s' if count != 1 else ''}")
            empty_label = "No folders in your library."
        else:
            if self.songs:
                items.append(("playlist", self.current_playlist_name))
                count = len(self.songs)
                labels.append(f"♫  {self.current_playlist_name}  ·  {count} track{'s' if count != 1 else ''}")
            empty_label = "Open or load a playlist to see it here."
        self.library_items = items
        self.library_list.delete(0, tk.END)
        self.library_list.insert(tk.END, *(labels or [empty_label]))
        if selected in items:
            self.library_list.selection_set(items.index(selected))
        count = len(self.songs)
        text = f"{count} track{'s' if count != 1 else ''}"
        if self.library_folder_filter is not None:
            text += " · folder filtered"
        self.library_count.config(text=text)

    def select_library_item(self, _event=None):
        selection = self.library_list.curselection()
        if not selection or selection[0] >= len(self.library_items):
            return
        item_type, value = self.library_items[selection[0]]
        if item_type == "track":
            index = self.song_index_by_id.get(value)
            if index is None:
                return
            self.library_folder_filter = None
            self.set_search_query("")
            self.refresh_playlist(preferred_index=index)
            if index in self.displayed_song_indices:
                row = self.displayed_song_indices.index(index)
                self.playlist.selection_clear(0, tk.END)
                self.playlist.selection_set(row)
                self.playlist.activate(row)
                self.playlist.see(row)
        elif item_type == "folder":
            self.library_folder_filter = value
            self.refresh_playlist()
            self.status.set(f"Showing tracks from {os.path.basename(value)}.")
            count = len(self.displayed_song_indices)
            self.library_count.config(text=f"{count} track{'s' if count != 1 else ''} in folder")
        elif item_type == "playlist":
            self.library_folder_filter = None
            self.refresh_playlist()

    def activate_library_item(self, _event=None):
        selection = self.library_list.curselection()
        if not selection or selection[0] >= len(self.library_items):
            return
        item_type, value = self.library_items[selection[0]]
        if item_type == "track":
            index = self.song_index_by_id.get(value)
            if index is not None:
                self.play_song_index(index)
        elif item_type == "folder":
            self.set_library_category("Tracks")
            self.library_folder_filter = value
            self.refresh_playlist()
        elif item_type == "playlist":
            self.set_library_category("Tracks")

    def configure_custom_title_bar(self):
        if sys.platform != "win32":
            self.window_controls.grid_remove()
            return

        self.root.update_idletasks()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = ctypes.c_void_p(self.get_window_handle())
        pointer_suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
        get_window_long = getattr(
            user32, f"GetWindowLong{pointer_suffix}"
        )
        get_window_long.argtypes = (ctypes.c_void_p, ctypes.c_int)
        get_window_long.restype = ctypes.c_ssize_t
        set_window_long = getattr(
            user32, f"SetWindowLong{pointer_suffix}"
        )
        set_window_long.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_ssize_t
        )
        set_window_long.restype = ctypes.c_ssize_t

        ctypes.set_last_error(0)
        original_style = get_window_long(hwnd, -16)
        if not original_style and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        style = original_style
        style &= ~0x00C00000
        style |= 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
        ctypes.set_last_error(0)
        if not set_window_long(hwnd, -16, style) and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())

        set_window_pos = user32.SetWindowPos
        set_window_pos.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint
        )
        set_window_pos.restype = ctypes.c_int
        if not set_window_pos(hwnd, None, 0, 0, 0, 0, 0x0027):
            error = ctypes.get_last_error()
            set_window_long(hwnd, -16, original_style)
            raise ctypes.WinError(error)

    def get_window_handle(self):
        user32 = ctypes.windll.user32
        ancestor = user32.GetAncestor
        ancestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        ancestor.restype = ctypes.c_void_p
        # Tk's winfo_id is its client window; GA_ROOT returns the WM wrapper.
        hwnd = ancestor(self.root.winfo_id(), 2)
        if not hwnd:
            raise ctypes.WinError()
        return hwnd

    def start_window_drag(self, event=None):
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            hwnd = ctypes.c_void_p(self.get_window_handle())
            user32.ReleaseCapture.restype = ctypes.c_int
            send_message = user32.SendMessageW
            send_message.argtypes = (
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_size_t,
                ctypes.c_ssize_t
            )
            send_message.restype = ctypes.c_ssize_t
            send_message(hwnd, 0x00A1, 0x0002, 0)
            return "break"

        if event is not None:
            self.drag_start_x = event.x_root
            self.drag_start_y = event.y_root
            self.window_start_x = self.root.winfo_x()
            self.window_start_y = self.root.winfo_y()
            self.top_bar.bind("<B1-Motion>", self.drag_window)

    def drag_window(self, event):
        x = self.window_start_x + event.x_root - self.drag_start_x
        y = self.window_start_y + event.y_root - self.drag_start_y
        self.root.geometry(f"+{x}+{y}")

    def minimize_window(self):
        self.root.iconify()

    def toggle_maximize_window(self):
        try:
            maximized = self.root.state() != "zoomed"
            self.root.state("zoomed" if maximized else "normal")
        except tk.TclError:
            # X11 exposes maximization as an attribute instead of a WM state.
            try:
                maximized = not bool(self.root.attributes("-zoomed"))
                self.root.attributes("-zoomed", maximized)
            except tk.TclError:
                self.status.set("Window maximization is unavailable on this system.")
                return
        self.window_controls.winfo_children()[1].configure(text="❐" if maximized else "□")

    def go_home(self):
        self.set_search_query("")
        self.search_entry.focus_set()
        self.search_entry.icursor(tk.END)

    def clear_search(self, _event=None):
        self.set_search_query("")
        self.search_entry.focus_set()
        return "break"

    def set_search_query(self, query):
        self.search_placeholder_visible = False
        self.search_var.set(query)
        if not query and self.root.focus_get() is not self.search_entry:
            self.restore_search_placeholder()

    def clear_search_placeholder(self, _event=None):
        if self.search_placeholder_visible:
            self.search_placeholder_visible = False
            self.search_var.set("")
            self.search_entry.configure(fg="#ffffff")

    def restore_search_placeholder(self, _event=None):
        if not self.search_var.get():
            self.search_placeholder_visible = True
            self.search_var.set(self.search_placeholder)
            self.search_entry.configure(fg="#aaaaaa")

    def show_app_menu(self):
        menu = self.build_app_menu()
        try:
            menu.tk_popup(
                self.top_bar.winfo_rootx() + 10,
                self.top_bar.winfo_rooty() + self.top_bar.winfo_height()
            )
        finally:
            menu.grab_release()

    def build_app_menu(self):
        if self.app_menu is not None:
            return self.app_menu
        menu_style = {
            "tearoff": False,
            "bg": "#181818",
            "fg": "#eeeeee",
            "activebackground": "#333333",
            "activeforeground": "#ffffff",
            "disabledforeground": "#777777",
            "borderwidth": 0,
            "activeborderwidth": 0,
            "font": ("Arial", 10),
            "relief": tk.FLAT
        }
        menu = tk.Menu(self.root, **menu_style)

        file_menu = tk.Menu(menu, **menu_style)
        file_menu.add_command(
            label="Load music folder...",
            command=self.load_music
        )
        file_menu.add_command(
            label="Open playlist...",
            command=self.open_playlist
        )
        file_menu.add_command(
            label="Save playlist...",
            command=self.save_playlist
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close_app)
        menu.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menu, **menu_style)
        edit_menu.add_command(
            label="Clear search",
            command=self.clear_search
        )
        edit_menu.add_command(
            label="Search tracks",
            command=lambda: self.search_entry.focus_set()
        )
        menu.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menu, **menu_style)
        for category in ("Tracks", "Folders", "Playlists"):
            view_menu.add_command(
                label=f"Library: {category}",
                command=lambda value=category: self.set_library_category(
                    value
                )
            )
        view_menu.add_separator()
        view_menu.add_command(
            label="Expand library",
            command=self.toggle_library_expansion
        )
        view_menu.add_command(
            label="Maximize window",
            command=self.toggle_maximize_window
        )
        menu.add_cascade(label="View", menu=view_menu)

        playback_menu = tk.Menu(menu, **menu_style)
        playback_menu.add_command(
            label="Previous track",
            command=self.previous_music
        )
        playback_menu.add_command(
            label="Play / pause",
            command=self.toggle_playback
        )
        playback_menu.add_command(
            label="Stop",
            command=self.stop_music
        )
        playback_menu.add_command(
            label="Next track",
            command=self.next_music
        )
        playback_menu.add_separator()
        playback_menu.add_checkbutton(
            label="Shuffle",
            variable=self.shuffle_var,
            command=lambda: self.set_shuffle(self.shuffle_var.get())
        )
        repeat_menu = tk.Menu(playback_menu, **menu_style)
        for mode, label in (
            (RepeatMode.OFF, "Off"),
            (RepeatMode.ALL, "Repeat all"),
            (RepeatMode.ONE, "Repeat one")
        ):
            repeat_menu.add_radiobutton(
                label=label,
                variable=self.repeat_var,
                value=mode.value,
                command=lambda selected=mode: self.set_repeat_mode(selected)
            )
        playback_menu.add_cascade(label="Repeat", menu=repeat_menu)
        playback_menu.add_separator()
        playback_menu.add_command(
            label="Volume up",
            command=lambda: self.adjust_volume(10)
        )
        playback_menu.add_command(
            label="Volume down",
            command=lambda: self.adjust_volume(-10)
        )
        menu.add_cascade(label="Playback", menu=playback_menu)

        help_menu = tk.Menu(menu, **menu_style)
        help_menu.add_command(label="Help", command=self.show_help)
        help_menu.add_command(label="About MP3 Player", command=self.show_about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.app_menu = menu
        return menu

    def adjust_volume(self, delta):
        self.set_volume(self.volume_level + delta)
        self.volume_scale.set(self.volume_level)

    def show_help(self):
        messagebox.showinfo(
            "MP3 Player Help",
            "Load a folder or open a saved playlist. Search by track name, "
            "double-click a track to play it, and use the bottom controls "
            "to seek, adjust volume, or move between tracks.",
            parent=self.root
        )

    def show_about(self):
        messagebox.showinfo(
            "About MP3 Player",
            "MP3 Player\nLocal music playback with Tkinter and Pygame.",
            parent=self.root
        )

    @staticmethod
    def format_time(seconds):
        total_seconds = max(0, int(seconds))
        minutes, remaining_seconds = divmod(total_seconds, 60)
        return f"{minutes}:{remaining_seconds:02d}"

    @staticmethod
    def read_track_metadata(path):
        return read_track_metadata(path)

    def update_cover_art(self, path):
        key = (path, id(self.cover_thumbnail))
        if key == getattr(self, "displayed_cover_key", None):
            return
        self.displayed_cover_key = key
        self.cover_image = None
        self.large_cover_image = None
        if path and self.cover_thumbnail is not None:
            small = self.cover_thumbnail.copy()
            small.thumbnail((58, 58), Image.Resampling.LANCZOS)
            self.cover_image = ImageTk.PhotoImage(small, master=self.root)
            self.large_cover_image = ImageTk.PhotoImage(self.cover_thumbnail, master=self.root)
            self.cover_art.config(image=self.cover_image, text="", width=58, height=58)
            self.large_cover_art.config(image=self.large_cover_image, text="", width=180, height=180)
        else:
            self.cover_art.config(image="", text="♫", width=3, height=2)
            self.large_cover_art.config(image="", text="MP3", width=8, height=5)

    def current_position(self):
        return self.playback.position()

    def update_progress(self):
        self.progress_after_id = None
        if self.closing:
            return
        if self.current_song:
            if self.playback.poll_finished():
                next_identity = self.playlist_queue.next_track(automatic=True)
                next_index = self.song_index_by_id.get(next_identity)
                if next_index is not None:
                    self.refresh_playlist(preferred_index=next_index)
                    self.play_song_index(next_index)
                else:
                    self.status.set("Playlist finished.")
            position = self.current_position()
            if not self.seeking:
                self.seek_var.set(position)
                self.elapsed_label.config(text=self.format_time(position))
            self.playback_button.config(
                text="Ⅱ" if self.playback.state == PlaybackState.PLAYING else "▶"
            )
        self.progress_after_id = self.root.after(250, self.update_progress)

    def begin_seek(self, _event=None):
        self.seeking = self.current_song is not None and self.track_duration is not None

    def on_seek_drag(self, value):
        self.elapsed_label.config(text=self.format_time(float(value)))

    def seek_with_keyboard(self, event):
        if self.track_duration is None or self.current_song is None:
            return "break"
        offsets = {"Left": -5, "Down": -5, "Right": 5, "Up": 5, "Prior": 10, "Next": -10}
        if event.keysym == "Home":
            position = 0
        elif event.keysym == "End":
            position = self.track_duration
        else:
            position = self.current_position() + offsets[event.keysym]
        self.seek_var.set(max(0, min(position, self.track_duration)))
        self.seek_music()
        return "break"

    def seek_music(self, _event=None):
        self.seeking = False
        if self.track_duration is None or self.current_song is None:
            return
        try:
            self.playback.seek(self.seek_var.get())
        except (pygame.error, NotImplementedError) as error:
            self.status.set(f"Could not seek within this track: {error}")
            messagebox.showerror("Seeking Error", f"Could not seek within this track:\n{error}", parent=self.root)
        else:
            state = "Paused" if self.paused else "Playing"
            self.status.set(f"{state}: {os.path.basename(self.current_song)}")
        self.update_track_display()

    def on_volume_pointer(self, event):
        minimum_x = self.volume_scale.coords(0)[0]
        maximum_x = self.volume_scale.coords(100)[0]
        if maximum_x <= minimum_x:
            return "break"

        fraction = (event.x - minimum_x) / (maximum_x - minimum_x)
        self.volume_level = max(0.0, min(100.0, fraction * 100))
        self.volume_scale.set(self.volume_level)
        self.set_volume(self.volume_level)
        return "break"

    def set_volume(self, value):
        volume = max(0.0, min(100.0, float(value)))
        try:
            self.playback.set_volume(volume)
        except pygame.error as error:
            self.status.set(f"Could not change playback volume: {error}")
            return
        self.volume_level = volume
        self.schedule_volume_save()

    def toggle_playback(self):
        if self.pending_track_id is not None:
            self.stop_music()
        elif self.current_song:
            if self.paused:
                self.resume_music()
            elif self.playback.state == PlaybackState.PLAYING:
                self.pause_music()
            elif self.current_song_index is not None:
                self.play_song_index(self.current_song_index)
        else:
            self.play_music()

    def update_track_display(self):
        if self.current_song:
            title = os.path.basename(self.current_song)
            location = os.path.dirname(self.current_song)
            self.update_cover_art(self.current_song)
            self.now_playing_title.config(text=title)
            self.now_playing_location.config(text=location)
            self.bottom_track_title.config(text=title)
            self.bottom_track_location.config(text=location)
            self.playback_button.config(text="Ⅱ" if self.playback.state == PlaybackState.PLAYING else "▶")
        else:
            self.update_cover_art(None)
            self.now_playing_title.config(text="Nothing playing")
            self.now_playing_location.config(
                text="Choose a track from your library."
            )
            self.bottom_track_title.config(text="No track selected")
            self.bottom_track_location.config(
                text="Load a music folder to begin"
            )
            self.playback_button.config(text="▶")

        if self.track_duration is not None:
            self.seek_scale.configure(
                to=max(self.track_duration, 0.1),
                state=tk.NORMAL
            )
            self.duration_label.config(
                text=self.format_time(self.track_duration)
            )
        else:
            self.seek_scale.configure(to=1, state=tk.DISABLED)
            self.duration_label.config(text="--:--")
        self.seek_var.set(self.current_position())
        self.elapsed_label.config(text=self.format_time(self.current_position()))


    def load_music(self):
        directory = filedialog.askdirectory(parent=self.root)
        if not directory:
            return
        self.status.set("Loading music folder...")
        self.submit_file_job(
            "library", lambda: scan_music_folder(directory),
            lambda songs: self.install_library(
                songs, songs, "name_asc", os.path.basename(os.path.normpath(directory))
            ), "load this music folder"
        )

    def filter_playlist(self, *_):
        if self.search_placeholder_visible:
            return
        self.refresh_playlist()

    def change_sort_order(self, _event=None):
        sort_orders = {
            "Name (A-Z)": "name_asc",
            "Name (Z-A)": "name_desc",
            "Folder order": "folder"
        }
        selected_order = sort_orders[self.sort_var.get()]
        if selected_order == self.sort_order:
            return

        self.sort_order = selected_order
        self.apply_sort_order()
        self.status.set(f"Playlist sorted by {self.sort_var.get()}.")

    def apply_sort_order(self):
        original_ids = track_ids(self.songs)
        def identity_at(index):
            return original_ids[index] if index is not None and 0 <= index < len(original_ids) else None
        current_id = identity_at(self.current_song_index)
        selection = self.playlist.curselection()
        selected_id = identity_at(
            self.displayed_song_indices[selection[0]]
            if selection and selection[0] < len(self.displayed_song_indices) else None
        )
        source = self.folder_order or self.songs
        self.songs = list(source)
        self.folder_order = list(source)
        if self.sort_order in ("name_asc", "name_desc"):
            self.songs.sort(
                key=lambda song: os.path.basename(song).casefold(),
                reverse=self.sort_order == "name_desc"
            )
        self.song_ids = track_ids(self.songs)
        self.song_index_by_id = {identity: index for index, identity in enumerate(self.song_ids)}
        self.current_song_index = self.song_index_by_id.get(current_id)
        selected_index = self.song_index_by_id.get(selected_id)
        self.playlist.selection_clear(0, tk.END)
        self.refresh_playlist(preferred_index=(
            self.current_song_index if self.current_song_index is not None else selected_index
        ))
        self.playlist_queue.update_tracks(self.song_ids)
        self.refresh_library()

    def refresh_playlist(self, preferred_song=None, preferred_index=None):
        selection = self.playlist.curselection()
        selected_index = (
            self.displayed_song_indices[selection[0]]
            if selection and selection[0] < len(self.displayed_song_indices) else None
        )
        target_index = preferred_index
        if target_index is None:
            target_index = selected_index
            if preferred_song is not None:
                target_index = next((i for i, song in enumerate(self.songs) if song == preferred_song), None)
        query = "" if self.search_placeholder_visible else self.search_var.get().strip().casefold()
        folder = (
            os.path.normcase(os.path.abspath(self.library_folder_filter))
            if self.library_folder_filter is not None else None
        )
        self.displayed_song_indices = [
            index for index, song in enumerate(self.songs)
            if query in os.path.basename(song).casefold()
            and (folder is None or os.path.normcase(os.path.abspath(os.path.dirname(song))) == folder)
        ]
        self.displayed_songs = [self.songs[index] for index in self.displayed_song_indices]
        counts = Counter(os.path.basename(song).casefold() for song in self.displayed_songs)
        labels = [
            song if counts[os.path.basename(song).casefold()] > 1 else os.path.basename(song)
            for song in self.displayed_songs
        ]
        self.playlist.delete(0, tk.END)
        if labels:
            self.playlist.insert(tk.END, *labels)
        if target_index not in self.displayed_song_indices:
            target_index = self.current_song_index
        if target_index in self.displayed_song_indices:
            row = self.displayed_song_indices.index(target_index)
            self.playlist.selection_set(row)
            self.playlist.activate(row)

    def save_playlist(self):
        if not self.songs:
            messagebox.showinfo("MP3 Player", "Load or open a playlist before saving.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save Playlist", defaultextension=".json",
            filetypes=(("JSON playlist", "*.json"), ("All files", "*.*"))
        )
        if not path:
            return
        data = {"version": 1, "songs": self.songs, "folder_order": self.folder_order, "sort_order": self.sort_order}
        try:
            write_json_atomic(path, data)
        except (OSError, ValueError, TypeError) as error:
            messagebox.showerror("Playlist Error", f"Could not save this playlist:\n{error}", parent=self.root)
            return
        self.current_playlist_name = Path(path).stem
        self.refresh_library()
        self.status.set(f"Saved playlist: {os.path.basename(path)}")

    def open_playlist(self):
        path = filedialog.askopenfilename(
            parent=self.root, title="Open Playlist",
            filetypes=(("JSON playlist", "*.json"), ("All files", "*.*"))
        )
        if not path:
            return
        self.status.set("Opening playlist...")
        self.submit_file_job(
            "library", lambda: read_playlist(path),
            lambda result: self.playlist_loaded(path, result), "open this playlist"
        )

    @staticmethod
    def validate_playlist(data, playlist_directory):
        return validate_playlist(data, playlist_directory)

    def play_music(self, event=None):
        selection = self.playlist.curselection()

        if not selection:
            messagebox.showinfo(
                "MP3 Player",
                "Load a folder and select a song first.",
                parent=self.root
            )
            return

        song_index = self.displayed_song_indices[selection[0]]
        self.play_song_index(song_index)

    def play_song_index(self, song_index):
        if not 0 <= song_index < len(self.songs):
            return
        identity = self.song_ids[song_index]
        self.playlist_queue.select(identity)
        song = self.songs[song_index]
        self.stop_music()
        self.pending_track_id = identity
        self.status.set(f"Loading: {os.path.basename(song)}")
        self.submit_file_job(
            "track", lambda: self.read_track_metadata(song),
            lambda metadata: self.play_prepared_track(identity, metadata), "read this track"
        )

    def previous_music(self):
        self.play_adjacent_song(-1)

    def next_music(self):
        self.play_adjacent_song(1)

    def play_adjacent_song(self, offset):
        if not self.songs:
            self.status.set("Load a folder to get started.")
            return
        current_identity = self.pending_track_id
        if current_identity is None and self.current_song_index is not None:
            current_identity = self.song_ids[self.current_song_index]
        if current_identity is None:
            selection = self.playlist.curselection()
            selected_index = (
                self.displayed_song_indices[selection[0]]
                if selection and selection[0] < len(self.displayed_song_indices)
                else 0
            )
            self.playlist_queue.select(self.song_ids[selected_index])
        next_identity = (
            self.playlist_queue.previous_track()
            if offset < 0 else self.playlist_queue.next_track()
        )
        next_index = self.song_index_by_id.get(next_identity)
        if next_index is None:
            return
        self.refresh_playlist(preferred_index=next_index)
        if next_index in self.displayed_song_indices:
            self.playlist.see(self.displayed_song_indices.index(next_index))
        self.play_song_index(next_index)

    def toggle_shuffle(self):
        self.set_shuffle(not self.playlist_queue.shuffle)

    def set_shuffle(self, enabled):
        self.playlist_queue.set_shuffle(enabled)
        self.shuffle_var.set(self.playlist_queue.shuffle)
        self.update_playback_mode_controls()

    def cycle_repeat_mode(self):
        modes = (RepeatMode.OFF, RepeatMode.ALL, RepeatMode.ONE)
        current_index = modes.index(self.playlist_queue.repeat_mode)
        self.set_repeat_mode(modes[(current_index + 1) % len(modes)])

    def set_repeat_mode(self, mode):
        self.playlist_queue.set_repeat_mode(mode)
        self.repeat_var.set(mode.value)
        self.update_playback_mode_controls()

    def update_playback_mode_controls(self):
        if not hasattr(self, "shuffle_button"):
            return
        self.shuffle_button.configure(
            bg="#1ed760" if self.playlist_queue.shuffle else "#282828",
            fg="#101010" if self.playlist_queue.shuffle else "#eeeeee",
            activebackground="#1ed760" if self.playlist_queue.shuffle else "#3a3a3a"
        )
        repeat_label = {
            RepeatMode.OFF: "RPT",
            RepeatMode.ALL: "RPT ALL",
            RepeatMode.ONE: "RPT 1"
        }[self.playlist_queue.repeat_mode]
        active = self.playlist_queue.repeat_mode != RepeatMode.OFF
        self.repeat_button.configure(
            text=repeat_label,
            bg="#1ed760" if active else "#282828",
            fg="#101010" if active else "#eeeeee",
            activebackground="#1ed760" if active else "#3a3a3a"
        )

    def pause_music(self):
        try:
            changed = self.playback.pause()
        except pygame.error as error:
            self.status.set(f"Could not pause playback: {error}")
            return
        if changed:
            self.update_track_display()
            self.status.set(f"Paused: {os.path.basename(self.current_song)}")

    def resume_music(self):
        try:
            changed = self.playback.resume()
        except pygame.error as error:
            self.status.set(f"Could not resume playback: {error}")
            return
        if changed:
            self.update_track_display()
            self.status.set(f"Playing: {os.path.basename(self.current_song)}")

    def stop_music(self):
        self.cancel_file_job("track")
        self.pending_track_id = None
        self.playback.stop()
        self.current_song = None
        self.current_song_index = None
        self.cover_thumbnail = None
        self.seeking = False
        self.update_track_display()
        self.status.set("Stopped.")

    def close_app(self):
        if self.closing:
            return
        self.closing = True
        self.ready = False
        for timer in (self.progress_after_id, self.background_after_id, self.volume_save_after_id):
            if timer is not None:
                self.root.after_cancel(timer)
        if self.volume_save_after_id is not None:
            self.volume_save_after_id = None
            try:
                self.save_volume_setting()
            except OSError as error:
                messagebox.showwarning("Volume Settings", f"Could not save volume settings:\n{error}", parent=self.root)
        for kind in list(self.background_jobs):
            self.cancel_file_job(kind)
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
        self.playback.shutdown()
        self.root.destroy()


def run():
    root = tk.Tk()
    app = MP3Player(root)

    if app.ready:
        root.mainloop()


if __name__ == "__main__":
    run()
