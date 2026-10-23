# MP3_Player

A Python desktop MP3 player built with Tkinter and Pygame. Its dark, three-panel interface keeps your local library, track list, and current track visible alongside playback controls. The library panel categorizes local tracks, folders, and the current saved playlist. A custom navigation bar provides menu, track navigation, home/search, help, and profile actions; on Windows, it also replaces the native title bar with draggable minimize, maximize, and close controls. The compact player bar displays embedded MP3 cover art when available, track progress and duration, seek controls, and volume. The selected volume is saved locally and restored the next time the player starts. Load music from a local folder, browse and search your playlist, sort tracks by name or folder order, and play, pause, resume, stop, or skip between tracks.

Save playlists as JSON files and reopen them later. Playlist files store music paths, so the original audio files must remain available at those locations (relative paths are resolved from the playlist file's folder).

Install the required packages with `python -m pip install -r requirements.txt`, then launch the player with `python main.py`.
