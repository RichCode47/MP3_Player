"""Playlist ordering rules for automatic and manual track navigation."""

import random
from enum import Enum


class RepeatMode(Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"


class PlaylistQueue:
    def __init__(self, rng=None):
        self.rng = rng if rng is not None else random.Random()
        self.tracks = []
        self.current = None
        self.history = []
        self.upcoming = []
        self.shuffle = False
        self.repeat_mode = RepeatMode.OFF

    def set_tracks(self, tracks):
        self.tracks = list(tracks)
        self.current = None
        self.history.clear()
        self.upcoming.clear()
        if self.shuffle:
            self._refill_upcoming()

    def update_tracks(self, tracks):
        tracks = list(tracks)
        available = set(tracks)
        self.tracks = tracks
        if self.current not in available:
            self.current = None
        self.history = [track for track in self.history if track in available]
        self.upcoming = [
            track for track in self.upcoming if track in available
        ]
        if self.shuffle:
            new_tracks = [
                track for track in tracks
                if track != self.current and track not in self.upcoming
            ]
            self.rng.shuffle(new_tracks)
            self.upcoming.extend(new_tracks)

    def set_shuffle(self, enabled):
        enabled = bool(enabled)
        if enabled == self.shuffle:
            return
        self.shuffle = enabled
        self.history.clear()
        self.upcoming.clear()
        if self.shuffle:
            self._refill_upcoming()

    def set_repeat_mode(self, mode):
        if not isinstance(mode, RepeatMode):
            raise ValueError(f"Unsupported repeat mode: {mode!r}")
        self.repeat_mode = mode

    def select(self, track):
        if track not in self.tracks:
            raise ValueError(f"Track is not in this playlist: {track!r}")
        if track == self.current:
            return
        if self.current is not None:
            self.history.append(self.current)
        self.current = track
        self.upcoming = [item for item in self.upcoming if item != track]

    def next_track(self, automatic=False):
        if not self.tracks:
            return None

        if automatic and self.repeat_mode == RepeatMode.ONE and self.current is not None:
            return self.current

        if self.shuffle:
            if not self.upcoming:
                if automatic and self.repeat_mode == RepeatMode.OFF:
                    return None
                self._refill_upcoming()
            if not self.upcoming:
                return None
            return self._select(self.upcoming.pop(0))

        if self.current is None:
            return self._select(self.tracks[0])

        current_index = self.tracks.index(self.current)
        next_index = current_index + 1
        if next_index == len(self.tracks):
            if automatic and self.repeat_mode != RepeatMode.ALL:
                return None
            next_index = 0
        return self._select(self.tracks[next_index])

    def previous_track(self):
        if not self.tracks:
            return None

        if self.shuffle:
            if self.history:
                previous = self.history.pop()
                if self.current is not None:
                    self.upcoming.insert(0, self.current)
                self.upcoming = [
                    item for item in self.upcoming if item != previous
                ]
                self.current = previous
                return previous
            if len(self.tracks) == 1:
                return self.current or self.tracks[0]
            choices = [track for track in self.tracks if track != self.current]
            previous = self.rng.choice(choices)
            if self.current is not None:
                self.upcoming.insert(0, self.current)
            return self._select(previous)

        if self.current is None:
            return self._select(self.tracks[-1])
        current_index = self.tracks.index(self.current)
        previous_index = (current_index - 1) % len(self.tracks)
        return self._select(self.tracks[previous_index])

    def _select(self, track):
        if track != self.current and self.current is not None:
            self.history.append(self.current)
        self.current = track
        self.upcoming = [item for item in self.upcoming if item != track]
        return track

    def _refill_upcoming(self):
        self.upcoming = [
            track for track in self.tracks if track != self.current
        ]
        if not self.upcoming and self.tracks and self.current is not None:
            self.upcoming = [self.current]
        self.rng.shuffle(self.upcoming)
