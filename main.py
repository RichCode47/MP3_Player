import os
import tkinter as tk
from tkinter import filedialog, messagebox

import pygame


class MP3Player:
    def __init__(self, root):
        self.root = root
        self.root.title("MP3 Player")
        self.root.geometry("560x430")
        self.root.minsize(480, 350)
        self.root.configure(bg="#181818")

        self.songs = []
        self.current_song = None
        self.paused = False

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
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)

    def build_interface(self):
        tk.Label(
            self.root,
            text="MP3 Player",
            font=("Arial", 20, "bold"),
            bg="#181818",
            fg="white"
        ).pack(pady=(15, 5))

        self.status = tk.StringVar(value="Load a folder to get started.")

        tk.Label(
            self.root,
            textvariable=self.status,
            bg="#181818",
            fg="#cccccc",
            wraplength=450
        ).pack(pady=5)

        playlist_frame = tk.Frame(self.root, bg="#181818")
        playlist_frame.pack(
            padx=15, pady=10, fill=tk.BOTH, expand=True
        )

        scrollbar = tk.Scrollbar(playlist_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.playlist = tk.Listbox(
            playlist_frame,
            bg="#252525",
            fg="white",
            selectbackground="#356859",
            selectforeground="white",
            exportselection=False,
            activestyle="none",
            font=("Arial", 11),
            yscrollcommand=scrollbar.set
        )
        self.playlist.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar.config(command=self.playlist.yview)
        self.playlist.bind("<Double-Button-1>", self.play_music)

        controls = tk.Frame(self.root, bg="#181818")
        controls.pack(pady=10, fill=tk.X, padx=15)

        buttons = [
            ("Play", self.play_music, "#238636"),
            ("Pause", self.pause_music, "#805500"),
            ("Resume", self.resume_music, "#2459a6"),
            ("Stop", self.stop_music, "#b62324")
        ]

        for column, (text, command, color) in enumerate(buttons):
            controls.columnconfigure(column, weight=1)

            tk.Button(
                controls,
                text=text,
                command=command,
                bg=color,
                fg="white",
                padx=5,
                pady=6
            ).grid(row=0, column=column, padx=4, sticky="ew")

        tk.Button(
            self.root,
            text="Load Music Folder",
            command=self.load_music,
            bg="#444444",
            fg="white",
            padx=15,
            pady=6
        ).pack(pady=(0, 15))

    def load_music(self):
        directory = filedialog.askdirectory(parent=self.root)

        if not directory:
            return

        try:
            # Store full paths so playback never depends on the
            # application's current working directory.
            songs = sorted(
                [
                    os.path.join(directory, name)
                    for name in os.listdir(directory)
                    if name.lower().endswith(".mp3")
                    and os.path.isfile(os.path.join(directory, name))
                ],
                key=lambda path: os.path.basename(path).casefold()
            )
        except OSError as error:
            messagebox.showerror(
                "Folder Error",
                f"Could not read this folder:\n{error}",
                parent=self.root
            )
            return

        self.stop_music()
        self.songs = songs
        self.playlist.delete(0, tk.END)

        for song in self.songs:
            self.playlist.insert(tk.END, os.path.basename(song))

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

    def play_music(self, event=None):
        selection = self.playlist.curselection()

        if not selection:
            messagebox.showinfo(
                "MP3 Player",
                "Load a folder and select a song first.",
                parent=self.root
            )
            return

        song = self.songs[selection[0]]

        try:
            pygame.mixer.music.stop()
            self.current_song = None
            self.paused = False

            pygame.mixer.music.load(song)
            pygame.mixer.music.play()

            self.current_song = song
            self.status.set(f"Playing: {os.path.basename(song)}")
        except (pygame.error, OSError) as error:
            self.status.set("Playback failed.")
            messagebox.showerror(
                "Playback Error",
                f"Could not play this file:\n{error}",
                parent=self.root
            )

    def pause_music(self):
        if self.current_song and not self.paused:
            if pygame.mixer.music.get_busy():
                pygame.mixer.music.pause()
                self.paused = True
                self.status.set(
                    f"Paused: {os.path.basename(self.current_song)}"
                )

    def resume_music(self):
        if self.current_song and self.paused:
            pygame.mixer.music.unpause()
            self.paused = False
            self.status.set(
                f"Playing: {os.path.basename(self.current_song)}"
            )

    def stop_music(self):
        pygame.mixer.music.stop()
        self.current_song = None
        self.paused = False
        self.status.set("Stopped.")

    def close_app(self):
        pygame.mixer.quit()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = MP3Player(root)

    if root.winfo_exists():
        root.mainloop()