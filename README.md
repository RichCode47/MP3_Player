# MP3_Player

A Python desktop MP3 player built with Tkinter and Pygame. Its dark, three-panel interface keeps your local library, track list, and current track visible alongside playback controls. The library panel categorizes local tracks, folders, and the current saved playlist. A custom navigation bar provides File, Edit, View, Playback, and Help menus, track navigation, home/search, and profile actions; on Windows, it also replaces the native title bar with draggable minimize, maximize, and close controls. The compact player bar displays embedded MP3 cover art when available, track progress and duration, seek controls, and volume. The selected volume is saved locally and restored the next time the player starts. Load music from a local folder, browse and search your playlist, sort tracks by name or folder order, and play, pause, resume, stop, or skip between tracks.

Save playlists as JSON files and reopen them later. Playlist files store music paths, so the original audio files must remain available at those locations (relative paths are resolved from the playlist file's folder).

Tracks advance automatically in playlist order and stop at the end by default. Use Shuffle to play each track once per cycle, or choose Repeat all to wrap at the end and Repeat one to restart the current track automatically. Manual Previous/Next controls continue to navigate even in Repeat one mode. Search and folder filters change the visible list; playback continues through the complete playlist. Folder scans, playlist loading, metadata reads, and cover decoding run in background workers. Opening another track or pressing Stop discards any superseded metadata result.

Artwork appears in both the player bar and the Now playing panel. Drag the progress slider to seek, or focus it and use the arrow keys to move five seconds, Page Up/Down to move ten seconds, or Home/End to jump to either end. Seeking preserves pause. MP3 seek accuracy can vary with the file's encoding.

Use Python 3.11–3.13 with Tkinter installed. On Windows, create and run the environment from PowerShell:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Select `.venv\Scripts\python.exe` as your IDE's interpreter. On Linux/macOS, use `.venv/bin/python` instead. Linux may require the distribution's Tkinter package and an available desktop display.

Run the regression suite with `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` (or `.venv/bin/python -m unittest discover -s tests -v` on Linux/macOS). Tests use temporary settings and playlists, a generated MP3 fixture, and SDL's dummy audio driver. Tk integration tests need a display; on headless Linux use `xvfb-run -a`. GitHub Actions runs the suite on Windows and Linux with Python 3.11 and 3.13.

The application is split into `main.py` (Tk interface and coordination), `playback.py` (audio transport and explicit playback states), `playlist_queue.py` (repeat, shuffle, and navigation order), `library.py` (file scanning, track identities, and metadata), and `persistence.py` (playlist validation and atomic JSON writes). Playlist and settings saves replace the destination only after a successful write.
