import ctypes
import json
import os
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame
from PIL import Image

import main
from library import TrackMetadata
from playback import PlaybackEngine, PlaybackState


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Path(self.temp.name) / "settings.json"
        self.settings.write_text(json.dumps({"volume": 37}), encoding="utf-8")
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        if sys.platform == "win32":
            self.root.attributes("-alpha", 0)
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args[1])
        self.mixer = MagicMock()
        self.mixer.music.get_pos.return_value = 0
        self.mixer.music.get_busy.return_value = True
        self.engine = PlaybackEngine(self.mixer)
        for name in ("showerror", "showwarning", "showinfo"):
            mocked = patch.object(main.messagebox, name)
            setattr(self, name, mocked.start())
            self.addCleanup(mocked.stop)
        with patch.object(main.MP3Player, "get_settings_path", return_value=self.settings), \
                patch.object(main.MP3Player, "configure_custom_title_bar"), \
                patch("main.PlaybackEngine", return_value=self.engine):
            self.player = main.MP3Player(self.root)
        self.addCleanup(self.finish)

    def finish(self):
        self.player.close_app()
        self.assertEqual(self.callback_errors, [])

    def install(self, names=("A.mp3", "B.mp3", "C.mp3")):
        songs = [str(Path(self.temp.name) / name) for name in names]
        self.player.install_library(songs, songs, "name_asc", "Test library")
        return songs

    def prepare(self, index=0):
        identity = self.player.song_ids[index]
        self.player.pending_track_id = identity
        self.player.play_prepared_track(identity, TrackMetadata(120))

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for background work")

    def tick_progress(self):
        self.root.after_cancel(self.player.progress_after_id)
        self.player.update_progress()

    def test_saved_volume_survives_window_mapping_and_restart(self):
        self.root.deiconify()
        self.root.update()
        self.assertEqual(self.player.volume_level, 37)
        self.assertEqual(self.player.volume_scale.get(), 37)
        self.mixer.music.set_volume.assert_called_with(0.37)
        self.player.close_app()
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["volume"], 37)

    def test_sorted_library_preserves_selected_track_and_plays_its_label(self):
        songs = self.install()
        self.player.library_list.selection_set(0)
        selected_id = self.player.library_items[0]
        self.player.sort_order = "name_desc"
        self.player.apply_sort_order()
        self.assertIn("C.mp3", self.player.library_list.get(0))
        row = self.player.library_list.curselection()[0]
        self.assertEqual(self.player.library_items[row], selected_id)
        with patch.object(self.player, "play_song_index") as play:
            self.player.activate_library_item()
        self.assertEqual(self.player.songs[play.call_args.args[0]], songs[0])

    def test_duplicate_occurrence_survives_sort_and_filter(self):
        self.install(("A.mp3", "B.mp3", "A.mp3"))
        self.player.current_song_index = 1  # second A in ascending order
        identity = self.player.song_ids[1]
        self.player.sort_order = "name_desc"
        self.player.apply_sort_order()
        self.assertEqual(self.player.song_ids[self.player.current_song_index], identity)
        self.player.set_search_query("A.mp3")
        selected = self.player.playlist.curselection()[0]
        self.assertEqual(self.player.displayed_song_indices[selected], self.player.current_song_index)

    def test_both_cover_widgets_show_images_at_usable_sizes(self):
        self.install()
        self.prepare()
        self.player.cover_thumbnail = Image.new("RGB", (180, 180), "green")
        self.player.update_track_display()
        self.root.update_idletasks()
        self.assertIsNot(self.player.cover_art, self.player.large_cover_art)
        self.assertTrue(self.player.cover_art.cget("image"))
        self.assertTrue(self.player.large_cover_art.cget("image"))
        self.assertGreaterEqual(self.player.cover_art.winfo_reqwidth(), 58)
        self.assertGreaterEqual(self.player.cover_art.winfo_reqheight(), 58)
        self.assertEqual(self.player.large_cover_image.width(), 180)
        self.player.stop_music()
        self.assertFalse(self.player.cover_art.cget("image"))
        self.assertFalse(self.player.large_cover_art.cget("image"))

    def test_menu_is_reused(self):
        first = self.player.build_app_menu()
        child_count = len(self.root.winfo_children())
        for _ in range(10):
            self.assertIs(self.player.build_app_menu(), first)
        self.assertEqual(len(self.root.winfo_children()), child_count)

    def test_first_progress_timer_is_tracked_and_close_cancels_callbacks(self):
        self.assertIsNotNone(self.player.progress_after_id)
        self.assertIn(self.player.progress_after_id, self.root.tk.call("after", "info"))
        self.player.close_app()
        self.assertFalse(self.root.tk.call("after", "info"))

    def test_eof_advances_once_and_last_track_finishes(self):
        self.install()
        self.prepare(0)
        self.mixer.music.get_busy.return_value = False
        with patch.object(self.player, "play_song_index") as play:
            self.tick_progress()
            self.tick_progress()
        play.assert_called_once_with(1)
        self.prepare(2)
        with patch.object(self.player, "play_song_index") as play:
            self.tick_progress()
        play.assert_not_called()
        self.assertEqual(self.engine.state, PlaybackState.FINISHED)
        self.assertEqual(self.player.status.get(), "Playlist finished.")

    def test_repeat_one_repeats_at_eof_and_manual_next_advances(self):
        self.install()
        self.prepare(1)
        self.player.set_repeat_mode(main.RepeatMode.ONE)
        self.mixer.music.get_busy.return_value = False
        with patch.object(self.player, "play_song_index") as play:
            self.tick_progress()
        play.assert_called_once_with(1)

        with patch.object(self.player, "play_song_index") as play:
            self.player.next_music()
        play.assert_called_once_with(2)

    def test_repeat_all_wraps_at_eof(self):
        self.install()
        self.prepare(2)
        self.player.set_repeat_mode(main.RepeatMode.ALL)
        self.mixer.music.get_busy.return_value = False
        with patch.object(self.player, "play_song_index") as play:
            self.tick_progress()
        play.assert_called_once_with(0)

    def test_paused_track_does_not_advance(self):
        self.install()
        self.prepare()
        self.player.pause_music()
        self.mixer.music.get_busy.return_value = False
        with patch.object(self.player, "play_song_index") as play:
            self.tick_progress()
        play.assert_not_called()
        self.assertTrue(self.player.paused)

    def test_keyboard_seek_updates_audio_and_preserves_pause(self):
        self.install()
        self.prepare()
        self.player.pause_music()
        result = self.player.seek_with_keyboard(SimpleNamespace(keysym="Right"))
        self.assertEqual(result, "break")
        self.mixer.music.play.assert_called_with(start=5.0)
        self.assertEqual(self.player.current_position(), 5)
        self.assertTrue(self.player.paused)

    def test_stop_discards_metadata_that_finishes_later(self):
        self.install()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow_metadata(path):
            started.set()
            release.wait(timeout=2)
            return TrackMetadata(120)

        with patch.object(self.player, "read_track_metadata", side_effect=slow_metadata):
            self.player.play_song_index(0)
            self.assertTrue(started.wait(timeout=1))
            self.player.stop_music()
            release.set()
            self.root.update()
        self.assertIsNone(self.player.current_song)
        self.assertIsNone(self.player.pending_track_id)
        self.mixer.music.play.assert_not_called()

    def test_background_work_keeps_ui_responsive_and_discards_stale_results(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        completed, heartbeat = [], []

        def slow_work():
            started.set()
            release.wait(timeout=2)
            return "old"

        self.player.submit_file_job("library", slow_work, completed.append, "load old folder")
        self.assertTrue(started.wait(timeout=1))
        self.root.after(0, lambda: heartbeat.append(True))
        self.root.update()
        self.assertEqual(heartbeat, [True])
        self.player.submit_file_job("library", lambda: "new", completed.append, "load new folder")
        self.wait_until(lambda: completed == ["new"])
        release.set()
        self.root.update()
        self.assertEqual(completed, ["new"])

    def test_library_result_can_cancel_another_completed_job(self):
        self.install()
        library_future, track_future = Future(), Future()
        library_future.set_result(self.player.songs.copy())
        track_future.set_result(TrackMetadata(120))
        self.player.background_jobs = {
            "library": (library_future, lambda songs: self.player.install_library(songs, songs, "folder", "New"), "load"),
            "track": (track_future, MagicMock(), "read track")
        }
        self.root.after_cancel(self.player.background_after_id)
        self.player.process_background_tasks()
        self.assertEqual(self.player.background_jobs, {})

    def test_save_failure_keeps_original_playlist(self):
        self.install()
        path = Path(self.temp.name) / "existing.json"
        path.write_text("original playlist", encoding="utf-8")
        with patch.object(main.filedialog, "asksaveasfilename", return_value=str(path)), \
                patch("persistence.os.replace", side_effect=OSError("file locked")):
            self.player.save_playlist()
        self.assertEqual(path.read_text(encoding="utf-8"), "original playlist")
        self.showerror.assert_called_once()

    def test_invalid_playlist_keeps_current_library(self):
        songs = self.install()
        path = Path(self.temp.name) / "invalid.json"
        path.write_text("{invalid", encoding="utf-8")
        with patch.object(main.filedialog, "askopenfilename", return_value=str(path)):
            self.player.open_playlist()
        self.wait_until(lambda: "library" not in self.player.background_jobs)
        self.assertEqual(self.player.songs, songs)
        self.showerror.assert_called_once()

    def test_real_mp3_seek_pause_and_automatic_advance(self):
        fixture = str(Path(__file__).parent / "fixtures" / "tone.mp3")
        real_engine = PlaybackEngine()
        real_engine.initialize(self.player.volume_level)
        self.player.playback = real_engine
        self.player.install_library([fixture, fixture], [fixture, fixture], "folder", "Test MP3s")
        self.player.play_song_index(0)
        self.wait_until(lambda: self.player.current_song_index == 0)
        self.assertEqual(real_engine.state, PlaybackState.PLAYING)
        self.assertGreater(self.player.track_duration, 3)
        self.player.pause_music()
        self.player.seek_var.set(self.player.track_duration - 0.2)
        self.player.seek_music()
        self.assertEqual(real_engine.state, PlaybackState.PAUSED)
        self.assertFalse(pygame.mixer.music.get_busy())
        self.player.resume_music()
        self.wait_until(lambda: self.player.current_song_index == 1)
        self.assertEqual(real_engine.state, PlaybackState.PLAYING)
        self.player.seek_var.set(self.player.track_duration - 0.2)
        self.player.seek_music()
        self.wait_until(lambda: real_engine.state == PlaybackState.FINISHED)
        self.assertEqual(self.player.current_song_index, 1)
        self.assertEqual(self.player.status.get(), "Playlist finished.")
        self.showerror.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows title bar")
    def test_custom_title_bar_changes_outer_window_and_maximizes(self):
        self.root.deiconify()
        self.root.update_idletasks()
        hwnd = self.player.get_window_handle()
        self.assertNotEqual(hwnd, self.root.winfo_id())
        suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
        get_style = getattr(ctypes.windll.user32, f"GetWindowLong{suffix}")
        get_style.argtypes = (ctypes.c_void_p, ctypes.c_int)
        get_style.restype = ctypes.c_ssize_t
        self.player.configure_custom_title_bar()
        self.assertFalse(get_style(hwnd, -16) & 0x00C00000)
        self.player.toggle_maximize_window()
        self.assertEqual(self.root.state(), "zoomed")
        self.player.toggle_maximize_window()
        self.assertEqual(self.root.state(), "normal")
        self.assertFalse(get_style(hwnd, -16) & 0x00C00000)
        self.player.minimize_window()
        self.assertEqual(self.root.state(), "iconic")
        self.root.deiconify()
        self.assertEqual(self.root.state(), "normal")

    @unittest.skipUnless(sys.platform == "win32", "Windows title bar")
    def test_title_bar_failure_restores_native_style(self):
        user32 = MagicMock()
        suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
        get_style = getattr(user32, f"GetWindowLong{suffix}")
        set_style = getattr(user32, f"SetWindowLong{suffix}")
        get_style.return_value = 0x00C00000
        set_style.return_value = 0x00C00000
        user32.SetWindowPos.return_value = 0
        with patch("main.ctypes.WinDLL", return_value=user32), \
                patch.object(self.player, "get_window_handle", return_value=123):
            with self.assertRaises(OSError):
                self.player.configure_custom_title_bar()
        self.assertEqual(set_style.call_args.args[2], 0x00C00000)


class StartupTests(unittest.TestCase):
    def test_audio_failure_exits_without_querying_destroyed_root(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        root.withdraw()
        engine = MagicMock()
        engine.initialize.side_effect = pygame.error("no audio device")
        with patch("main.tk.Tk", return_value=root), \
                patch("main.PlaybackEngine", return_value=engine), \
                patch.object(main.MP3Player, "load_volume_setting", return_value=37), \
                patch.object(main.messagebox, "showerror") as showerror:
            main.run()
        showerror.assert_called_once()
        engine.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
