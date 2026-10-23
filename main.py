import ctypes
import json
import os
import sys
import tkinter as tk
import time
from io import BytesIO
from collections import Counter
from tkinter import filedialog, messagebox, ttk

import pygame
from PIL import Image, ImageTk, UnidentifiedImageError
from mutagen import MutagenError
from mutagen.mp3 import MP3


class MP3Player:
    def __init__(self, root):
        self.root = root
        self.root.title("MP3 Player")
        self.root.geometry("1180x740")
        self.root.minsize(900, 600)
        self.root.configure(bg="#000000")

        self.songs = []
        self.folder_order = []
        self.displayed_song_indices = []
        self.displayed_songs = []
        self.sort_order = "name_asc"
        self.current_song = None
        self.current_song_index = None
        self.paused = False
        self.track_duration = None
        self.cover_data = None
        self.cover_image = None
        self.cover_path = None
        self.playback_position = 0.0
        self.playback_started_at = None
        self.seeking = False
        self.progress_after_id = None
        self.search_placeholder_visible = False

        try:
            pygame.mixer.init()
        except pygame.error as error:
            messagebox.showerror(
                "Audio Error",
                f"Could not initialize the audio device:\n{error}",
                parent=self.root
            )
            self.root.destroy()
            return

        self.build_interface()
        self.configure_custom_title_bar()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)

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
        workspace.columnconfigure(0, minsize=220, weight=0)
        workspace.columnconfigure(1, weight=1)
        workspace.columnconfigure(2, minsize=225, weight=0)

        library = tk.Frame(workspace, bg="#121212")
        library.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        library.columnconfigure(0, weight=1)
        library.rowconfigure(5, weight=1)

        tk.Label(
            library,
            text="Your Library",
            font=("Arial", 15, "bold"),
            bg="#121212",
            fg="#ffffff"
        ).grid(row=0, column=0, padx=18, pady=(20, 15), sticky="w")

        self._make_button(
            library, "Load music folder", self.load_music, accent=True
        ).grid(row=1, column=0, padx=14, pady=4, sticky="ew")
        self._make_button(
            library, "Open saved playlist", self.open_playlist
        ).grid(row=2, column=0, padx=14, pady=4, sticky="ew")
        self._make_button(
            library, "Save current playlist", self.save_playlist
        ).grid(row=3, column=0, padx=14, pady=4, sticky="ew")

        tk.Frame(library, bg="#282828", height=1).grid(
            row=4, column=0, padx=14, pady=15, sticky="ew"
        )
        self.library_summary = tk.Label(
            library,
            text="LOCAL FILES\n\nLoad a folder of MP3 files to\nbuild your library.",
            justify=tk.LEFT,
            font=("Arial", 10),
            bg="#121212",
            fg="#b3b3b3",
            wraplength=180
        )
        self.library_summary.grid(
            row=5, column=0, padx=18, pady=5, sticky="nw"
        )
        self.library_count = tk.Label(
            library,
            text="0 tracks",
            font=("Arial", 9),
            bg="#121212",
            fg="#777777"
        )
        self.library_count.grid(
            row=6, column=0, padx=18, pady=(0, 18), sticky="w"
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

        self.cover_art = tk.Label(
            activity,
            text="MP3",
            font=("Arial", 28, "bold"),
            bg="#263b35",
            fg="#d9eee4",
            width=8,
            height=5
        )
        self.cover_art.grid(row=1, column=0, padx=18, pady=(0, 17), sticky="ew")

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
        for column, (label, command, symbol) in enumerate((
            ("Previous", self.previous_music, "|◀"),
            ("Play or pause", self.toggle_playback, "▶"),
            ("Stop", self.stop_music, "■"),
            ("Next", self.next_music, "▶|")
        )):
            self._make_button(
                transport,
                symbol,
                command,
                accent=(label == "Play or pause")
            ).grid(row=0, column=column, padx=7)
        self.playback_button = transport.grid_slaves(row=0, column=1)[0]
        self.playback_button.configure(
            font=("Segoe UI Symbol", 12, "bold"),
            width=3,
            height=1,
            padx=2,
            pady=5
        )

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
        self.volume_scale.set(100)
        self.volume_scale.pack(side=tk.LEFT)
        self.root.after(250, self.update_progress)

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

    def configure_custom_title_bar(self):
        if sys.platform != "win32":
            self.window_controls.grid_remove()
            return

        self.root.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = ctypes.c_void_p(self.root.winfo_id())
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

        style = get_window_long(hwnd, -16)
        style &= ~0x00C00000
        style |= 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
        set_window_long(hwnd, -16, style)

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
            raise ctypes.WinError()

    def start_window_drag(self, event=None):
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            hwnd = ctypes.c_void_p(self.root.winfo_id())
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
        if self.root.state() == "zoomed":
            self.root.state("normal")
            self.window_controls.winfo_children()[1].configure(text="□")
        else:
            self.root.state("zoomed")
            self.window_controls.winfo_children()[1].configure(text="❐")

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
        menu = tk.Menu(
            self.root,
            tearoff=False,
            bg="#242424",
            fg="#ffffff",
            activebackground="#333333",
            activeforeground="#ffffff"
        )
        menu.add_command(label="Load music folder...", command=self.load_music)
        menu.add_command(label="Open playlist...", command=self.open_playlist)
        menu.add_command(label="Save playlist...", command=self.save_playlist)
        menu.add_separator()
        menu.add_command(label="About MP3 Player", command=self.show_about)
        try:
            menu.tk_popup(
                self.top_bar.winfo_rootx() + 10,
                self.top_bar.winfo_rooty() + self.top_bar.winfo_height()
            )
        finally:
            menu.grab_release()

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

    def read_track_metadata(self, path):
        try:
            audio = MP3(path)
        except (MutagenError, OSError):
            return None, None

        duration = audio.info.length
        cover_data = None
        if audio.tags:
            artwork = audio.tags.getall("APIC")
            if artwork:
                front_cover = next(
                    (image for image in artwork if image.type == 3),
                    artwork[0]
                )
                cover_data = front_cover.data
        return (duration if duration > 0 else None), cover_data

    def update_cover_art(self, path):
        if path == self.cover_path:
            return

        self.cover_path = path
        self.cover_image = None
        if path and self.cover_data:
            try:
                with Image.open(BytesIO(self.cover_data)) as image:
                    cover = image.convert("RGB")
                    cover.thumbnail((58, 58), Image.Resampling.LANCZOS)
                    self.cover_image = ImageTk.PhotoImage(cover)
            except (UnidentifiedImageError, OSError):
                self.cover_image = None

        if self.cover_image:
            self.cover_art.config(image=self.cover_image, text="")
        else:
            self.cover_art.config(image="", text="♫")

    def current_position(self):
        if (
            self.playback_started_at is not None
            and not self.paused
        ):
            position = self.playback_position + (
                time.monotonic() - self.playback_started_at
            )
        else:
            position = self.playback_position
        if self.track_duration is not None:
            position = min(position, self.track_duration)
        return max(0.0, position)

    def update_progress(self):
        if self.current_song:
            is_busy = pygame.mixer.music.get_busy()
            if not self.paused and not is_busy:
                self.playback_position = (
                    self.track_duration
                    if self.track_duration is not None
                    else self.current_position()
                )
                self.playback_started_at = None
                position = self.playback_position
            else:
                position = self.current_position()

            if not self.seeking:
                self.seek_var.set(position)
            self.elapsed_label.config(
                text=self.format_time(position)
            )
            self.playback_button.config(
                text="Ⅱ" if is_busy and not self.paused else "▶"
            )
        self.progress_after_id = self.root.after(250, self.update_progress)

    def begin_seek(self, _event=None):
        self.seeking = True

    def on_seek_drag(self, value):
        self.elapsed_label.config(text=self.format_time(float(value)))

    def seek_music(self, _event=None):
        self.seeking = False
        if self.track_duration is None or self.current_song is None:
            return

        position = min(float(self.seek_var.get()), self.track_duration)
        try:
            pygame.mixer.music.set_pos(position)
        except pygame.error as error:
            self.playback_position = self.current_position()
            self.seek_var.set(self.playback_position)
            self.elapsed_label.config(
                text=self.format_time(self.playback_position)
            )
            messagebox.showerror(
                "Seeking Error",
                f"Could not seek within this track:\n{error}",
                parent=self.root
            )
            return

        self.playback_position = position
        self.playback_started_at = (
            None if self.paused else time.monotonic()
        )

    def set_volume(self, value):
        pygame.mixer.music.set_volume(float(value) / 100)

    def toggle_playback(self):
        if self.current_song:
            if self.paused:
                self.resume_music()
            elif pygame.mixer.music.get_busy():
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
            self.playback_button.config(text="Ⅱ" if not self.paused else "▶")
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
        self.seek_var.set(self.playback_position)
        self.elapsed_label.config(text=self.format_time(self.playback_position))

        self.library_count.config(
            text=f"{len(self.songs)} track"
            f"{'s' if len(self.songs) != 1 else ''}"
        )
        if self.songs:
            folders = {os.path.dirname(song) for song in self.songs}
            folder_label = (
                next(iter(folders))
                if len(folders) == 1
                else f"{len(folders)} folders"
            )
            self.library_summary.config(
                text=f"LOCAL FILES\n\n{folder_label}"
            )
        else:
            self.library_summary.config(
                text="LOCAL FILES\n\nLoad a folder of MP3 files to\nbuild your library."
            )

    def load_music(self):
        directory = filedialog.askdirectory(parent=self.root)

        if not directory:
            return

        try:
            # Store full paths so playback never depends on the
            # application's current working directory.
            songs = [
                os.path.join(directory, name)
                for name in os.listdir(directory)
                if name.lower().endswith(".mp3")
                and os.path.isfile(os.path.join(directory, name))
            ]
        except OSError as error:
            messagebox.showerror(
                "Folder Error",
                f"Could not read this folder:\n{error}",
                parent=self.root
            )
            return

        self.stop_music()
        self.songs = songs.copy()
        self.folder_order = songs.copy()
        self.displayed_song_indices = []
        self.displayed_songs = []
        self.playlist.delete(0, tk.END)
        self.sort_order = "name_asc"
        self.sort_var.set("Name (A-Z)")
        self.set_search_query("")
        self.apply_sort_order()
        self.update_track_display()

        if self.songs:
            self.playlist.selection_set(0)
            self.playlist.activate(0)
            self.status.set(f"Loaded {len(self.songs)} MP3 file(s).")
        else:
            self.status.set("No MP3 files found in this folder.")
            messagebox.showinfo(
                "MP3 Player",
                "No MP3 files found in the selected folder.",
                parent=self.root
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
        original_songs = self.songs
        original_current_index = self.current_song_index
        selection = self.playlist.curselection()
        original_selected_index = (
            self.displayed_song_indices[selection[0]]
            if selection and selection[0] < len(self.displayed_song_indices)
            else None
        )
        source = self.folder_order or original_songs
        indexed_songs = list(enumerate(source))

        if self.sort_order == "name_asc":
            indexed_songs.sort(
                key=lambda item: os.path.basename(item[1]).casefold()
            )
        elif self.sort_order == "name_desc":
            indexed_songs.sort(
                key=lambda item: os.path.basename(item[1]).casefold(),
                reverse=True
            )

        self.songs = [song for _, song in indexed_songs]
        self.folder_order = list(source)
        def remap_index(index):
            if index is None or index >= len(original_songs):
                return None
            song = original_songs[index]
            occurrence = original_songs[:index + 1].count(song) - 1
            matching_indexes = [
                new_index for new_index, candidate in enumerate(self.songs)
                if candidate == song
            ]
            return (
                matching_indexes[occurrence]
                if occurrence < len(matching_indexes) else None
            )

        self.current_song_index = remap_index(original_current_index)
        selected_index = remap_index(original_selected_index)
        self.refresh_playlist(
            preferred_index=(
                self.current_song_index
                if self.current_song_index is not None
                else selected_index
            )
        )

    def refresh_playlist(self, preferred_song=None, preferred_index=None):
        selection = self.playlist.curselection()
        selected_index = (
            self.displayed_song_indices[selection[0]]
            if selection and selection[0] < len(self.displayed_song_indices)
            else None
        )
        target_index = preferred_index
        if target_index is None:
            target_song = preferred_song
            if target_song is None and selected_index is not None:
                target_song = self.songs[selected_index]
            if target_song is not None:
                target_index = next(
                    (
                        index for index, song in enumerate(self.songs)
                        if song == target_song
                    ),
                    None
                )
        query = (
            ""
            if self.search_placeholder_visible
            else self.search_var.get().strip().casefold()
        )
        self.displayed_song_indices = [
            index for index, song in enumerate(self.songs)
            if query in os.path.basename(song).casefold()
        ]
        self.displayed_songs = [
            self.songs[index] for index in self.displayed_song_indices
        ]

        basenames = Counter(
            os.path.basename(song).casefold()
            for song in self.displayed_songs
        )
        duplicate_basenames = {
            name for name, count in basenames.items() if count > 1
        }

        self.playlist.delete(0, tk.END)
        for song in self.displayed_songs:
            basename = os.path.basename(song)
            label = (
                song if basename.casefold() in duplicate_basenames
                else basename
            )
            self.playlist.insert(tk.END, label)

        if target_index not in self.displayed_song_indices:
            target_index = (
                self.current_song_index
                if self.current_song_index in self.displayed_song_indices
                else None
            )
        if target_index in self.displayed_song_indices:
            row = self.displayed_song_indices.index(target_index)
            self.playlist.selection_set(row)
            self.playlist.activate(row)

    def save_playlist(self):
        if not self.songs:
            messagebox.showinfo(
                "MP3 Player",
                "Load or open a playlist before saving.",
                parent=self.root
            )
            return

        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save Playlist",
            defaultextension=".json",
            filetypes=(("JSON playlist", "*.json"), ("All files", "*.*"))
        )
        if not path:
            return

        playlist_data = {
            "version": 1,
            "songs": self.songs,
            "folder_order": self.folder_order,
            "sort_order": self.sort_order
        }
        try:
            with open(path, "w", encoding="utf-8") as playlist_file:
                json.dump(playlist_data, playlist_file, indent=2)
                playlist_file.write("\n")
        except OSError as error:
            messagebox.showerror(
                "Playlist Error",
                f"Could not save this playlist:\n{error}",
                parent=self.root
            )
            return

        self.status.set(f"Saved playlist: {os.path.basename(path)}")

    def open_playlist(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Open Playlist",
            filetypes=(("JSON playlist", "*.json"), ("All files", "*.*"))
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as playlist_file:
                playlist_data = json.load(playlist_file)
            songs, folder_order, sort_order = self.validate_playlist(
                playlist_data,
                os.path.dirname(os.path.abspath(path))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            messagebox.showerror(
                "Playlist Error",
                f"Could not open this playlist:\n{error}",
                parent=self.root
            )
            return

        missing_songs = [song for song in songs if not os.path.isfile(song)]
        available_songs = [song for song in songs if os.path.isfile(song)]
        available_folder_order = [
            song for song in folder_order if os.path.isfile(song)
        ]

        self.stop_music()
        self.songs = available_songs
        self.folder_order = available_folder_order
        self.displayed_song_indices = []
        self.displayed_songs = []
        self.playlist.delete(0, tk.END)
        self.sort_order = sort_order
        self.sort_var.set({
            "name_asc": "Name (A-Z)",
            "name_desc": "Name (Z-A)",
            "folder": "Folder order"
        }[sort_order])
        self.current_song_index = None
        self.set_search_query("")
        self.apply_sort_order()
        if self.songs:
            self.playlist.selection_set(0)
            self.playlist.activate(0)
        self.update_track_display()
        self.status.set(f"Loaded {len(self.songs)} song(s) from playlist.")

        if missing_songs:
            preview = "\n".join(os.path.basename(song) for song in missing_songs[:5])
            remainder = (
                f"\n...and {len(missing_songs) - 5} more."
                if len(missing_songs) > 5 else ""
            )
            messagebox.showwarning(
                "Missing Music Files",
                f"{len(missing_songs)} file(s) could not be found and were "
                f"skipped:\n{preview}{remainder}",
                parent=self.root
            )

    @staticmethod
    def validate_playlist(data, playlist_directory):
        if not isinstance(data, dict) or type(data.get("version")) is not int:
            raise ValueError("The playlist must be a versioned JSON object.")
        if data["version"] != 1:
            raise ValueError(
                f"Unsupported playlist version: {data['version']}."
            )

        songs = data.get("songs")
        folder_order = data.get("folder_order")
        sort_order = data.get("sort_order")
        valid_orders = {"name_asc", "name_desc", "folder"}
        if (
            not isinstance(songs, list)
            or not all(isinstance(song, str) and song for song in songs)
            or not isinstance(folder_order, list)
            or not all(
                isinstance(song, str) and song for song in folder_order
            )
            or sorted(songs) != sorted(folder_order)
            or not isinstance(sort_order, str)
            or sort_order not in valid_orders
        ):
            raise ValueError("The playlist contains invalid data.")

        def resolve_paths(paths):
            return [
                os.path.abspath(
                    song if os.path.isabs(song)
                    else os.path.join(playlist_directory, song)
                )
                for song in paths
            ]

        return resolve_paths(songs), resolve_paths(folder_order), sort_order

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
        song = self.songs[song_index]
        duration, cover_data = self.read_track_metadata(song)

        try:
            pygame.mixer.music.stop()
            self.current_song = None
            self.current_song_index = None
            self.paused = False
            self.playback_position = 0.0
            self.playback_started_at = None

            pygame.mixer.music.load(song)
            pygame.mixer.music.play()

            self.current_song = song
            self.current_song_index = song_index
            self.track_duration = duration
            self.cover_data = cover_data
            self.playback_started_at = time.monotonic()
            self.update_track_display()
            status = f"Playing: {os.path.basename(song)}"
            if duration is None:
                status += " (duration unavailable; seeking disabled)"
            self.status.set(status)
        except (pygame.error, OSError) as error:
            self.current_song = None
            self.current_song_index = None
            self.track_duration = None
            self.cover_data = None
            self.playback_position = 0.0
            self.playback_started_at = None
            self.paused = False
            self.update_track_display()
            self.status.set("Playback failed.")
            messagebox.showerror(
                "Playback Error",
                f"Could not play this file:\n{error}",
                parent=self.root
            )

    def previous_music(self):
        self.play_adjacent_song(-1)

    def next_music(self):
        self.play_adjacent_song(1)

    def play_adjacent_song(self, offset):
        if not self.songs:
            self.status.set("Load a folder to get started.")
            return

        if self.current_song_index is not None:
            current_index = self.current_song_index
        else:
            selection = self.playlist.curselection()
            current_index = (
                self.displayed_song_indices[selection[0]]
                if selection and selection[0] < len(self.displayed_song_indices)
                else (-1 if offset > 0 else 0)
            )

        next_index = (current_index + offset) % len(self.songs)
        self.refresh_playlist(preferred_index=next_index)
        if next_index in self.displayed_song_indices:
            self.playlist.see(self.displayed_song_indices.index(next_index))
        self.play_song_index(next_index)

    def pause_music(self):
        if self.current_song and not self.paused:
            if pygame.mixer.music.get_busy():
                self.playback_position = self.current_position()
                pygame.mixer.music.pause()
                self.paused = True
                self.playback_started_at = None
                self.update_track_display()
                self.status.set(
                    f"Paused: {os.path.basename(self.current_song)}"
                )

    def resume_music(self):
        if self.current_song and self.paused:
            pygame.mixer.music.unpause()
            self.paused = False
            self.playback_started_at = time.monotonic()
            self.update_track_display()
            self.status.set(
                f"Playing: {os.path.basename(self.current_song)}"
            )

    def stop_music(self):
        pygame.mixer.music.stop()
        self.current_song = None
        self.current_song_index = None
        self.paused = False
        self.track_duration = None
        self.cover_data = None
        self.playback_position = 0.0
        self.playback_started_at = None
        self.update_track_display()
        self.status.set("Stopped.")

    def close_app(self):
        if self.progress_after_id is not None:
            self.root.after_cancel(self.progress_after_id)
        pygame.mixer.quit()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = MP3Player(root)

    if root.winfo_exists():
        root.mainloop()