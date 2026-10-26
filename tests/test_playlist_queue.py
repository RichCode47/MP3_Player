import random
import unittest

from playlist_queue import PlaylistQueue, RepeatMode


class PlaylistQueueTests(unittest.TestCase):
    def setUp(self):
        self.queue = PlaylistQueue(random.Random(7))
        self.queue.set_tracks(["a", "b", "c"])

    def test_sequential_auto_advance_stops_at_end_and_manual_next_wraps(self):
        self.assertEqual(self.queue.next_track(automatic=True), "a")
        self.assertEqual(self.queue.next_track(automatic=True), "b")
        self.assertEqual(self.queue.next_track(automatic=True), "c")
        self.assertIsNone(self.queue.next_track(automatic=True))
        self.assertEqual(self.queue.next_track(), "a")

    def test_repeat_all_wraps_automatic_advance(self):
        self.queue.set_repeat_mode(RepeatMode.ALL)
        self.queue.select("c")
        self.assertEqual(self.queue.next_track(automatic=True), "a")

    def test_repeat_one_repeats_automatically_but_manual_next_advances(self):
        self.queue.set_repeat_mode(RepeatMode.ONE)
        self.queue.select("b")
        self.assertEqual(self.queue.next_track(automatic=True), "b")
        self.assertEqual(self.queue.next_track(), "c")

    def test_shuffle_visits_each_track_once_before_repeating(self):
        self.queue.set_shuffle(True)
        first_cycle = [self.queue.next_track() for _ in range(3)]
        self.assertEqual(set(first_cycle), {"a", "b", "c"})
        self.assertEqual(len(first_cycle), len(set(first_cycle)))

    def test_shuffle_does_not_repeat_current_at_cycle_boundary(self):
        self.queue.set_shuffle(True)
        first_cycle = [self.queue.next_track() for _ in range(3)]
        self.queue.set_repeat_mode(RepeatMode.ALL)
        next_track = self.queue.next_track(automatic=True)
        self.assertNotEqual(first_cycle[-1], next_track)

    def test_shuffle_off_stops_automatic_advance_after_current_cycle(self):
        self.queue.set_shuffle(True)
        for _ in range(3):
            self.queue.next_track()
        self.assertIsNone(self.queue.next_track(automatic=True))
        self.assertEqual(self.queue.next_track(), "a")

    def test_shuffle_previous_uses_history_and_next_returns_forward(self):
        self.queue.set_shuffle(True)
        first = self.queue.next_track()
        second = self.queue.next_track()
        self.assertEqual(self.queue.previous_track(), first)
        self.assertEqual(self.queue.next_track(), second)

    def test_toggling_shuffle_rebuilds_upcoming_tracks(self):
        self.queue.select("b")
        self.queue.set_shuffle(True)
        self.assertEqual(self.queue.next_track(), "a")
        self.queue.set_shuffle(False)
        self.assertEqual(self.queue.next_track(), "b")

    def test_replacing_library_resets_position_but_preserves_modes(self):
        self.queue.set_shuffle(True)
        self.queue.set_repeat_mode(RepeatMode.ALL)
        self.queue.next_track()
        self.queue.set_tracks(["x", "y"])
        self.assertIsNone(self.queue.current)
        self.assertEqual(self.queue.repeat_mode, RepeatMode.ALL)
        self.assertTrue(self.queue.shuffle)
        self.assertEqual(set([self.queue.next_track(), self.queue.next_track()]), {"x", "y"})

    def test_reordering_tracks_preserves_current_and_shuffle_cycle(self):
        self.queue.select("b")
        self.queue.set_shuffle(True)
        upcoming = list(self.queue.upcoming)
        self.queue.update_tracks(["c", "b", "a"])
        self.assertEqual(self.queue.current, "b")
        self.assertEqual(self.queue.upcoming, upcoming)
        self.queue.set_shuffle(False)
        self.assertEqual(self.queue.next_track(), "a")

    def test_empty_and_single_track_queues(self):
        self.queue.set_tracks([])
        self.assertIsNone(self.queue.next_track())
        self.assertIsNone(self.queue.previous_track())

        self.queue.set_tracks(["only"])
        self.queue.set_shuffle(True)
        self.assertEqual(self.queue.next_track(automatic=True), "only")
        self.assertIsNone(self.queue.next_track(automatic=True))
        self.assertEqual(self.queue.previous_track(), "only")
        self.queue.set_repeat_mode(RepeatMode.ALL)
        self.assertEqual(self.queue.next_track(automatic=True), "only")


if __name__ == "__main__":
    unittest.main()
