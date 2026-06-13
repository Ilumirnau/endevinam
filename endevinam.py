# Endevina'm - a personal-use, Hitster-style music guessing game for Spotify.
# Copyright (C) 2026  Ilumirnau
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
Endevina'm - a cross-platform (PC + Android) Spotify playlist trivia game.

A single self-contained Kivy file: the same code runs on Windows/macOS/Linux
and packages to Android with buildozer (see buildozer.spec next to this file)
and to iOS with kivy-ios (see the README for the steps).

Game flow (a Hitster-style guessing game):
    1. Paste one or more public Spotify playlist links.
    2. "See details" shows the per-decade distribution of the playlist.
    3. "Add" pools the playlist's tracks into the sampling set.
    4. "Play" streams a random track on your active Spotify device.
    5. "Reveal" pauses playback and shows artist / year / title + album art,
       recolouring the whole UI from the album art's palette.
    6. "Next" drops that track and plays another.

Playback uses Spotify Connect (the Web API), so it controls whatever device
currently has Spotify open - identical behaviour on desktop and phone.

Requirements (desktop): ``pip install kivy spotipy pillow``
"""

import io
import locale
import os
import re
import random
import sys
import threading
import time
import webbrowser

from kivy.config import Config

# Disable the right/middle-click multitouch emulation that leaves red dots on
# screen. Must run before the Window is imported/created below.
Config.set("input", "mouse", "mouse,disable_multitouch")

from kivy import kivy_data_dir
from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.clipboard import Clipboard
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.graphics.texture import Texture
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image as KivyImage
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen, ScreenManager, FadeTransition
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

import requests
import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.oauth2 import SpotifyPKCE

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
# Authorization uses the PKCE flow, which needs NO client secret - only a
# Spotify "Client ID". Each player supplies their own free Client ID on first
# launch (created in the Spotify Developer Dashboard, see README), so every
# install runs under its own Spotify app and quota. The entered value is saved
# locally in the per-user data directory; setting the SPOTIFY_CLIENT_ID
# environment variable overrides it (handy for development). The Client ID is
# read in EndevinamApp.load_client_id().
# Spotify rejects http://localhost as "insecure"; the loopback IP literal
# 127.0.0.1 is allowed for native apps. This exact URI must also be listed in
# the Redirect URIs of the user's own Spotify app in the developer dashboard.
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:1603/callback")
SCOPE = "user-modify-playback-state user-read-playback-state"

DARK = (0x19 / 255, 0x14 / 255, 0x14 / 255)  # Spotify near-black
SPOTIFY_GREEN = (0x1D / 255, 0xB9 / 255, 0x54 / 255)

# Directory holding bundled assets (logos). When frozen by PyInstaller the data
# files live in the temporary extraction dir exposed as sys._MEIPASS.
BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
# All images/icons live in a single media/ folder to keep the repo root clean.
# The PyInstaller spec extracts them into _MEIPASS/media/, so this resolves both
# when running from source and when frozen.
MEDIA_DIR = os.path.join(BASE_DIR, "media")
LOGO_GAME = os.path.join(MEDIA_DIR, "endevinam.png")
LOGO_SPOTIFY = os.path.join(MEDIA_DIR, "Spotify_Full_Logo_RGB_White.png")
# Standalone Spotify icon, shown in place of the full logo when the top bar is
# too narrow to fit the wordmark with its required clear space. Spotify's
# branding rules only allow the icon alone when there isn't room for the full
# logo. This must be the official icon asset from Spotify's brand resources; if
# it's missing the top bar simply keeps the full logo.
LOGO_SPOTIFY_ICON = os.path.join(MEDIA_DIR, "Spotify_Primary_Logo_RGB_White.png")
# Window / taskbar icon shown while the app runs. A PNG is used because Kivy's
# image loader handles it on every platform (unlike .ico). Without this, the
# running window falls back to Kivy's default logo even though the packaged exe
# carries the brand .ico.
ICON_APP = os.path.join(MEDIA_DIR, "endevinam_icon.png")
# DejaVuSans ships with Kivy on every platform and has the ♫ / ✕ / power glyphs
# that the default Roboto font lacks.
SYMBOL_FONT = os.path.join(kivy_data_dir, "fonts", "DejaVuSans.ttf")

# Default theme colours, shown until album art recolours the UI.
DEFAULT_BG = "#1b1538"
DEFAULT_FG = "#5fe6d0"

PLAYLIST_RE = re.compile(r"playlist[/:]([a-zA-Z0-9]+)")
# A Spotify Client ID is 32 hexadecimal characters. Validating against this
# rejects doubled/garbled pastes (e.g. the same id concatenated twice) before
# they reach Spotify, which would otherwise report "client_id: Invalid".
CLIENT_ID_RE = re.compile(r"\A[0-9A-Fa-f]{32}\Z")

# --------------------------------------------------------------------------- #
# Internationalisation (i18n)
# --------------------------------------------------------------------------- #
# All user-facing strings live here, keyed by a short identifier. tr() looks up
# the active language and falls back to English per key, so a missing
# translation can never blank out the UI. Strings with {placeholders} are
# .format()-ed by tr(**kwargs). Brand names ("Endevina'm", "Spotify"), the
# redirect URI, and the artist/year/title reveal data are intentionally not
# translated.
TRANSLATIONS = {
    "en": {
        "lang_name": "English",
        # ----- Auth screen -----
        "auth_info": "Connect your own Spotify Client ID to start (one-time, free).\n\n"
        "1. Create a free app in the Spotify Developer Dashboard and add the "
        "Redirect URI shown below to it.\n"
        "2. Paste your [b]Client ID[/b] and tap [b]Connect to Spotify[/b].\n"
        "3. Authorize in the browser. It will land on a '127.0.0.1' page that "
        "may fail to load - that is fine.\n"
        "4. Copy the FULL address from the browser's address bar, paste it "
        "below, and tap [b]Finish[/b].\n\n"
        "Note: due to Spotify's 2026 API changes, a newly created Client ID can "
        "only load playlists your own account owns or collaborates on. To use "
        "someone else's playlist, copy it into your account first.",
        "redirect_uri_caption": "Redirect URI:",
        "client_id_hint": "Paste your Spotify Client ID here",
        "connect_btn": "Connect to Spotify",
        "redirect_hint": "Paste the redirected URL here",
        "finish_btn": "Finish",
        "need_client_id": "Enter your Spotify Client ID first.",
        "bad_client_id": "That doesn't look like a valid Client ID.\n"
        "It should be the 32-character ID from your Spotify app's Settings.",
        "need_connect_first": "Tap Connect to Spotify first.",
        "need_redirect": "Paste the redirected URL first.",
        "no_code": "That URL has no login code. Copy the full address you were "
        "redirected to.",
        "connecting": "Connecting...",
        "authorize_then_paste": "Authorize in your browser, then paste the "
        "redirected URL below.",
        "connect_error": "Could not connect:\n{message}\n\nTry again.",
        # ----- Game setup / buttons -----
        "how_to_play": "[b]How to play[/b]\n\n"
        "Paste a public Spotify playlist link above.\n"
        "Tap [b]See Details[/b] for the playlist's decade breakdown.\n"
        "Tap [b]Add[/b] to pool its songs.\n\n"
        "You need Spotify open and playing-capable on a device.",
        "playlist_hint": "Paste a Spotify playlist link",
        "see_details_btn": "See Details",
        "add_btn": "Add",
        "keep_spotify_open": "Keep Spotify open on a device before starting",
        "set_device_btn": "Set device",
        "retry_btn": "Retry",
        "start_game_btn": "Start game",
        "reveal_btn": "Reveal song",
        "next_btn": "Next ♫",
        # ----- Game flow / status -----
        "loading_playlist": "Loading playlist...",
        "invalid_link": "Invalid playlist link",
        "playlist_forbidden": "Spotify won't share this playlist's songs.\n\n"
        "Since Spotify's 2026 changes, a newly created Client ID can only read "
        "playlists your OWN account owns or collaborates on.\n\n"
        "To use someone else's playlist, open it in Spotify, make your own copy "
        "(Add to playlist -> New playlist), then paste YOUR copy's link here.",
        "playlist_name": "Playlist: {name}",
        "details_block": "Oldies: {old}%    1960's: {six}%\n"
        "1970's: {sev}%    1980's: {eig}%\n"
        "1990's: {nin}%    2000's: {two}%\n"
        "2010's: {ten}%    2020's: {twe}%\n\n"
        "Total songs in playlist: {total}\n\n"
        "Tap [b]Add[/b] to use this playlist.",
        "adding_tracks": "Adding tracks...",
        "added_tracks": "Added {n} new tracks (pool: {pool}).",
        "no_tracks_left": "No more tracks left in the playlist.",
        "sampling_from": "Sampling from playlists:\n{names}",
        "song_playing": "A song is playing...",
        "no_device_play": "No Spotify device found.\n"
        "Open Spotify on your phone or PC, then tap Play again.",
        "no_device_retry": "No Spotify device found.\n"
        "Open Spotify on your phone or PC, then tap Retry.",
        "playback_error": "Could not start playback:\n{exc}",
        "retry_failed": "Retry failed:\n{exc}",
        "generic_error": "Error: {exc}",
        "previous_song": "Previous song: {year}",
        # ----- Device popup -----
        "select_device_title": "Select Spotify Device",
        "no_devices_found": "No Spotify devices found.\n"
        "Open Spotify on a device first.",
        "wifi_hint": "WiFi/Cast speakers only show here after you start playing "
        "on them in the Spotify app.",
        "device_playing_suffix": "  - playing",
        "device_selected": "Device: {name}",
    },
    "ca": {
        "lang_name": "Català",
        # ----- Pantalla de connexió -----
        "auth_info": "Connecta el teu propi Client ID de Spotify per començar "
        "(un sol cop, gratuït).\n\n"
        "1. Crea una aplicació gratuïta al Spotify Developer Dashboard i "
        "afegeix-hi la Redirect URI que es mostra a sota.\n"
        "2. Enganxa el teu [b]Client ID[/b] i prem [b]Connecta amb Spotify[/b].\n"
        "3. Autoritza-ho al navegador. Anirà a una pàgina '127.0.0.1' que "
        "potser no carrega; és normal.\n"
        "4. Copia l'adreça SENCERA de la barra d'adreces del navegador, "
        "enganxa-la a sota i prem [b]Finalitza[/b].\n\n"
        "Nota: pels canvis de l'API de Spotify del 2026, un Client ID nou només "
        "pot carregar llistes que el teu compte tingui o on siguis "
        "col·laborador. Per fer servir la llista d'algú altre, copia-la abans "
        "al teu compte.",
        "redirect_uri_caption": "Redirect URI:",
        "client_id_hint": "Enganxa aquí el teu Client ID de Spotify",
        "connect_btn": "Connecta amb Spotify",
        "redirect_hint": "Enganxa aquí l'adreça redirigida",
        "finish_btn": "Finalitza",
        "need_client_id": "Primer introdueix el teu Client ID de Spotify.",
        "bad_client_id": "Aquest Client ID no sembla vàlid.\n"
        "Ha de ser l'identificador de 32 caràcters de la configuració de la "
        "teva app de Spotify.",
        "need_connect_first": "Primer prem Connecta amb Spotify.",
        "need_redirect": "Primer enganxa l'adreça redirigida.",
        "no_code": "Aquesta adreça no té cap codi d'inici de sessió. Copia "
        "l'adreça completa on t'han redirigit.",
        "connecting": "Connectant...",
        "authorize_then_paste": "Autoritza-ho al navegador i després enganxa "
        "l'adreça redirigida a sota.",
        "connect_error": "No s'ha pogut connectar:\n{message}\n\nTorna-ho a provar.",
        # ----- Configuració / botons -----
        "how_to_play": "[b]Com es juga[/b]\n\n"
        "Enganxa a dalt l'enllaç d'una llista pública de Spotify.\n"
        "Prem [b]Mostra els detalls[/b] per veure la distribució per dècades.\n"
        "Prem [b]Afegeix[/b] per agrupar-ne les cançons.\n\n"
        "Necessites Spotify obert i a punt de reproduir en un dispositiu.",
        "playlist_hint": "Enganxa l'enllaç d'una llista de Spotify",
        "see_details_btn": "Mostra els detalls",
        "add_btn": "Afegeix",
        "keep_spotify_open": "Mantén Spotify obert en un dispositiu abans de començar",
        "set_device_btn": "Tria el dispositiu",
        "retry_btn": "Reintenta",
        "start_game_btn": "Comença",
        "reveal_btn": "Mostra la cançó",
        "next_btn": "Següent ♫",
        # ----- Flux del joc / estat -----
        "loading_playlist": "Carregant la llista...",
        "invalid_link": "Enllaç de llista no vàlid",
        "playlist_forbidden": "Spotify no comparteix les cançons d'aquesta llista.\n\n"
        "Des dels canvis de Spotify del 2026, un Client ID nou només pot llegir "
        "llistes que el TEU compte tingui o on siguis col·laborador.\n\n"
        "Per fer servir la llista d'algú altre, obre-la a Spotify, fes-ne una "
        "còpia teva (Afegeix a una llista -> Nova llista) i enganxa aquí "
        "l'enllaç de la TEVA còpia.",
        "playlist_name": "Llista: {name}",
        "details_block": "Antigues: {old}%    1960: {six}%\n"
        "1970: {sev}%    1980: {eig}%\n"
        "1990: {nin}%    2000: {two}%\n"
        "2010: {ten}%    2020: {twe}%\n\n"
        "Total de cançons a la llista: {total}\n\n"
        "Prem [b]Afegeix[/b] per fer servir aquesta llista.",
        "adding_tracks": "Afegint cançons...",
        "added_tracks": "Afegides {n} cançons noves (total: {pool}).",
        "no_tracks_left": "No queden més cançons a la llista.",
        "sampling_from": "Triant de les llistes:\n{names}",
        "song_playing": "S'està reproduint una cançó...",
        "no_device_play": "No s'ha trobat cap dispositiu de Spotify.\n"
        "Obre Spotify al mòbil o a l'ordinador i torna a prémer Reprodueix.",
        "no_device_retry": "No s'ha trobat cap dispositiu de Spotify.\n"
        "Obre Spotify al mòbil o a l'ordinador i prem Reintenta.",
        "playback_error": "No s'ha pogut iniciar la reproducció:\n{exc}",
        "retry_failed": "El reintent ha fallat:\n{exc}",
        "generic_error": "Error: {exc}",
        "previous_song": "Cançó anterior: {year}",
        # ----- Finestra de dispositius -----
        "select_device_title": "Tria un dispositiu de Spotify",
        "no_devices_found": "No s'ha trobat cap dispositiu de Spotify.\n"
        "Obre Spotify en un dispositiu primer.",
        "wifi_hint": "Els altaveus WiFi/Cast només apareixen aquí després "
        "de començar a reproduir-hi des de l'aplicació de Spotify.",
        "device_playing_suffix": "  - reproduint",
        "device_selected": "Dispositiu: {name}",
    },
}

# Active language code (a key of TRANSLATIONS). Set at startup by EndevinamApp.
_LANG = "en"


def set_language(code):
    """Set the active UI language to a supported code (falls back to English)."""
    global _LANG
    _LANG = code if code in TRANSLATIONS else "en"


def tr(key, **kwargs):
    """Translate a string key for the active language, formatting placeholders.

    Falls back to the English value for any key missing in the active language.
    """
    table = TRANSLATIONS.get(_LANG, TRANSLATIONS["en"])
    text = table.get(key) or TRANSLATIONS["en"][key]
    return text.format(**kwargs) if kwargs else text


def detect_system_language():
    """Best-effort system language as a supported code, else 'en'.

    Each platform exposes the locale differently, so the source is chosen per
    platform: the JVM on Android, the Win32 UI-language API on Windows (whose
    locale.getlocale() returns English names like "Catalan_Spain" rather than
    ISO codes), and the LANG/LC_* environment variables elsewhere.
    """
    code = ""
    if platform == "android":
        try:
            from jnius import autoclass

            code = autoclass("java.util.Locale").getDefault().getLanguage() or ""
        except Exception as exc:  # noqa: BLE001 - fall through to desktop logic
            print("android locale detection failed:", exc)
    if not code and platform == "win":
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            code = locale.windows_locale.get(lcid, "")  # e.g. "ca_ES"
        except Exception as exc:  # noqa: BLE001
            print("windows locale detection failed:", exc)
    if not code:
        # POSIX / macOS: environment variables are the reliable source; the C
        # locale is a last resort (often unset for GUI apps -> English).
        code = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
                or os.environ.get("LANG") or "")
        if not code:
            try:
                code = locale.getlocale()[0] or ""
            except Exception:  # noqa: BLE001
                code = ""
    code = re.split(r"[_\-.]", code)[0].lower() if code else ""
    return code if code in TRANSLATIONS else "en"

# --------------------------------------------------------------------------- #
# Dance animation data
# --------------------------------------------------------------------------- #
# Each style is a list of keyframes; each keyframe is a list of 7 (dx, dy)
# tuples in dp, relative to the widget centre (y-up convention):
#   [head, shoulder, lhand, rhand, waist, lfoot, rfoot]
# Connections drawn: head↔shoulder (neck), shoulder↔lhand, shoulder↔rhand,
#                    shoulder↔waist (torso), waist↔lfoot, waist↔rfoot.

DANCE_POSES = {
    "default": [
        [(0, 43), (0, 24), (-22, 6), (22, 6), (0, -8), (-13, -42), (13, -42)],
        [(0, 45), (0, 26), (-22, 8), (22, 8), (0, -6), (-13, -42), (13, -42)],
        [(0, 47), (0, 28), (-22, 10), (22, 10), (0, -4), (-13, -42), (13, -42)],
        [(0, 45), (0, 26), (-22, 8), (22, 8), (0, -6), (-13, -42), (13, -42)],
        [(0, 43), (0, 24), (-22, 6), (22, 6), (0, -8), (-13, -42), (13, -42)],
        [(0, 41), (0, 22), (-22, 4), (22, 4), (0, -10), (-13, -42), (13, -42)],
    ],
    "pop": [
        [(-6, 42), (-5, 23), (-26, 10), (18, 14), (-4, -9), (-18, -42), (8, -42)],
        [(-3, 44), (-2, 25), (-24, 14), (22, 10), (-1, -7), (-15, -42), (13, -42)],
        [(3, 44), (2, 25), (-22, 10), (24, 14), (1, -7), (-13, -42), (15, -42)],
        [(6, 42), (5, 23), (-18, 14), (26, 10), (4, -9), (-8, -42), (18, -42)],
        [(3, 44), (2, 25), (-22, 10), (24, 14), (1, -7), (-13, -42), (15, -42)],
        [(-3, 44), (-2, 25), (-24, 14), (22, 10), (-1, -7), (-15, -42), (13, -42)],
    ],
    "hiphop": [
        [(0, 36), (0, 17), (-20, -2), (20, -2), (0, -14), (-18, -42), (18, -42)],
        [(-2, 32), (-1, 13), (-24, 22), (16, -10), (0, -18), (-20, -42), (16, -42)],
        [(0, 30), (0, 11), (-18, -12), (18, -12), (0, -20), (-20, -42), (20, -42)],
        [(2, 32), (1, 13), (-16, -10), (24, 22), (0, -18), (-16, -42), (20, -42)],
        [(0, 34), (0, 15), (-20, -4), (20, -4), (0, -16), (-18, -42), (18, -42)],
    ],
    "rock": [
        [(-2, 42), (-2, 23), (-30, 18), (20, -4), (-2, -10), (-22, -42), (18, -42)],
        [(-2, 42), (-2, 23), (-28, 14), (28, 22), (-2, -10), (-22, -42), (18, -42)],
        [(-2, 42), (-2, 23), (-32, 22), (14, 28), (-2, -10), (-22, -42), (18, -42)],
        [(-2, 42), (-2, 23), (-26, 10), (-4, 30), (-2, -10), (-22, -42), (18, -42)],
        [(-2, 42), (-2, 23), (-30, 18), (10, -10), (-2, -10), (-22, -42), (18, -42)],
        [(-2, 42), (-2, 23), (-28, 22), (28, -8), (-2, -10), (-22, -42), (18, -42)],
    ],
    "acoustic": [
        [(-3, 43), (-2, 24), (-24, 4), (18, 10), (-2, -8), (-15, -42), (11, -42)],
        [(-1, 43), (-1, 24), (-22, 8), (22, 6), (-1, -8), (-14, -42), (12, -42)],
        [(2, 43), (1, 24), (-20, 12), (24, 2), (1, -8), (-12, -42), (14, -42)],
        [(3, 43), (2, 24), (-22, 8), (22, 6), (2, -8), (-11, -42), (15, -42)],
        [(1, 43), (1, 24), (-24, 4), (18, 10), (1, -8), (-12, -42), (14, -42)],
        [(-1, 43), (-1, 24), (-22, 8), (22, 6), (-1, -8), (-14, -42), (12, -42)],
    ],
    "electronic": [
        [(0, 43), (0, 24), (-26, 24), (26, -10), (0, -8), (-13, -42), (13, -42)],
        [(0, 43), (0, 24), (-26, 24), (26, -10), (0, -8), (-13, -42), (13, -42)],
        [(-8, 43), (-6, 24), (-30, 6), (10, 24), (-4, -8), (-18, -42), (10, -42)],
        [(-8, 43), (-6, 24), (-30, 6), (10, 24), (-4, -8), (-18, -42), (10, -42)],
        [(8, 43), (6, 24), (-10, 24), (30, 6), (4, -8), (-10, -42), (18, -42)],
        [(8, 43), (6, 24), (-10, 24), (30, 6), (4, -8), (-10, -42), (18, -42)],
        [(0, 40), (0, 21), (-20, -6), (20, -6), (0, -10), (-16, -42), (16, -42)],
        [(0, 40), (0, 21), (-20, -6), (20, -6), (0, -10), (-16, -42), (16, -42)],
    ],
    "disco": [
        [(4, 43), (3, 24), (-18, -8), (26, 30), (2, -8), (-16, -42), (14, -42)],
        [(2, 43), (1, 24), (-20, 0), (24, 18), (1, -8), (-15, -42), (13, -42)],
        [(0, 43), (0, 24), (-22, 6), (22, 6), (0, -8), (-13, -42), (13, -42)],
        [(-2, 43), (-1, 24), (-24, 18), (20, 0), (-1, -8), (-13, -42), (15, -42)],
        [(-4, 43), (-3, 24), (-26, 30), (18, -8), (-2, -8), (-14, -42), (16, -42)],
        [(-2, 43), (-1, 24), (-24, 18), (20, 0), (-1, -8), (-13, -42), (15, -42)],
        [(0, 43), (0, 24), (-22, 6), (22, 6), (0, -8), (-13, -42), (13, -42)],
        [(2, 43), (1, 24), (-20, 0), (24, 18), (1, -8), (-15, -42), (13, -42)],
    ],
}


# The dance style is derived from the artist's genre tags via the /artists/{id}
# endpoint. Rules are checked in order; the first style whose keyword appears in
# any genre wins (specific before generic, so e.g. "dance pop" resolves to pop
# rather than electronic).
GENRE_RULES = [
    ("hiphop",     ("hip hop", "rap", "trap", "drill", "grime")),
    ("rock",       ("rock", "metal", "punk", "grunge", "emo")),
    ("disco",      ("disco", "funk", "soul", "motown")),
    ("electronic", ("edm", "house", "techno", "trance", "dubstep",
                    "electro", "electronic")),
    ("acoustic",   ("acoustic", "folk", "singer-songwriter")),
    ("pop",        ("pop",)),
]

# Each style gets a characteristic animation speed so the dances feel
# genre-appropriate.
STYLE_TEMPO = {
    "electronic": 128, "hiphop": 92, "rock": 124, "acoustic": 78,
    "disco": 116, "pop": 112, "default": 100,
}


def classify_genres(genres):
    """Map a list of Spotify genre strings → one of the DANCE_POSES style keys."""
    lowered = [g.lower() for g in (genres or [])]
    for style, keywords in GENRE_RULES:
        if any(kw in g for g in lowered for kw in keywords):
            return style
    return "default"


# --------------------------------------------------------------------------- #
# Pure helpers (platform independent)
# --------------------------------------------------------------------------- #
class PlaylistForbidden(Exception):
    """Spotify returned HTTP 403 for a playlist's items.

    Since the Feb 2026 Web API changes, apps created after 11 Feb 2026 (i.e.
    every player's freshly-made Client ID) can only read the items of playlists
    their own account owns or collaborates on. Reading anyone else's playlist
    returns 403. Older "grandfathered" apps are unaffected.
    """


def get_playlist_id(playlist_url):
    """Extract a Spotify playlist id from a URL or URI, or return None."""
    match = PLAYLIST_RE.search(playlist_url or "")
    return match.group(1) if match else None


def year_counter(tracks):
    """Return per-decade percentages: oldies, 1960s..2020s (8 buckets)."""
    decades = [1960 + 10 * i for i in range(7)]  # 1960..2020
    buckets = [0] * (len(decades) + 1)
    years = []
    for item in tracks or []:
        track = item.get("track") or {}
        date = (track.get("album") or {}).get("release_date") or ""
        if date[:4].isdigit():
            years.append(int(date[:4]))
    if not years:
        return buckets
    for y in years:
        idx = 0
        while idx < len(decades) and y >= decades[idx]:
            idx += 1
        buckets[idx] += 1
    return [int(100 * c / len(years)) for c in buckets]


def hex_from_rgb(rgb):
    return "#%02x%02x%02x" % rgb


def luminance(rgb):
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def readable_text_color(bg_rgb):
    """Pick black or white text for good contrast against a background."""
    return (0, 0, 0) if luminance(bg_rgb) > 0.55 else (255, 255, 255)


def pick_contrast_color(bg_rgb, candidates, min_diff=0.30):
    """Choose the palette colour with the most contrast against the background.

    Returns a distinct, readable secondary colour, falling back to black/white
    if no palette colour is contrasting enough.
    """
    best, best_diff = None, -1.0
    for c in candidates:
        diff = abs(luminance(c) - luminance(bg_rgb))
        if diff > best_diff:
            best, best_diff = c, diff
    if best is None or best_diff < min_diff:
        return readable_text_color(bg_rgb)
    return best


def extract_palette(image_bytes, count=3):
    """Return [dominant, c2, c3] RGB tuples from album art using Pillow.

    Pillow is bundled by buildozer's recipes, so this works on Android too.
    Falls back to None if Pillow or decoding is unavailable.
    """
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        im.thumbnail((120, 120))
        quant = im.quantize(colors=max(count, 3))
        palette = quant.getpalette()
        color_counts = sorted(quant.getcolors(), reverse=True)
        result = []
        for _, index in color_counts[:count]:
            base = index * 3
            result.append(tuple(palette[base : base + 3]))
        while len(result) < count:
            result.append(result[-1] if result else (38, 7, 65))
        return result
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, keep playing
        print("palette extraction failed:", exc)
        return None


def open_url(url):
    """Open a URL in the system browser on desktop or via an intent on Android."""
    if platform == "android":
        try:
            from jnius import autoclass

            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            PythonActivity.mActivity.startActivity(intent)
            return
        except Exception as exc:  # noqa: BLE001
            print("android browser intent failed:", exc)
    webbrowser.open(url)


def make_gradient_texture(bottom_rgb, top_rgb, height=256):
    """Vertical gradient texture: bottom_rgb at screen bottom -> top_rgb on top."""
    tex = Texture.create(size=(1, height), colorfmt="rgb")
    br, bg, bb = bottom_rgb
    tr, tg, tb = top_rgb
    buf = bytearray()
    for i in range(height):
        f = i / (height - 1)
        buf += bytes(
            (
                int(br + (tr - br) * f),
                int(bg + (tg - bg) * f),
                int(bb + (tb - bb) * f),
            )
        )
    tex.blit_buffer(bytes(buf), colorfmt="rgb", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    return tex


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #
class GradientBackground(BoxLayout):
    """A box whose canvas is filled with a vertical gradient texture."""

    def __init__(self, bottom_rgb=(38, 7, 65), top_rgb=None, **kwargs):
        super().__init__(**kwargs)
        self._bottom = tuple(bottom_rgb)
        self._top = tuple(top_rgb) if top_rgb else tuple(int(c * 255) for c in DARK)
        with self.canvas.before:
            self._tex = make_gradient_texture(self._bottom, self._top)
            self._color = Color(1, 1, 1, 1)
            self._rect = Rectangle(pos=self.pos, size=self.size, texture=self._tex)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_colors(self, bottom_rgb, top_rgb=None):
        self._bottom = tuple(bottom_rgb)
        if top_rgb is not None:
            self._top = tuple(top_rgb)
        self._tex = make_gradient_texture(self._bottom, self._top)
        self._rect.texture = self._tex


class SolidBackground(BoxLayout):
    """A box filled with a single, updatable solid colour."""

    def __init__(self, rgb=(38, 7, 65), **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            self._color = Color(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, 1)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_color(self, rgb):
        self._color.rgb = (rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)


class GradientStrip(Widget):
    """A thin band that blends the black top bar into the body colour."""

    def __init__(self, body_rgb=(38, 7, 65), **kwargs):
        super().__init__(**kwargs)
        self._dark = tuple(int(c * 255) for c in DARK)
        with self.canvas:
            self._tex = make_gradient_texture(body_rgb, self._dark)
            self._color = Color(1, 1, 1, 1)
            self._rect = Rectangle(pos=self.pos, size=self.size, texture=self._tex)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_body_color(self, body_rgb):
        self._tex = make_gradient_texture(tuple(body_rgb), self._dark)
        self._rect.texture = self._tex


class AlbumArt(Widget):
    """Album cover drawn with rounded corners, per Spotify's art guidelines.

    Spotify asks that artwork stay in its original form (no crop, distort, blur,
    or overlay) with only rounded corners added - 4 px on small/medium screens,
    8 px on large ones. Covers are square, so the texture is drawn into a square
    rounded rectangle and nothing is layered on top of it.
    """

    def __init__(self, radius=dp(6), **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            self._color = Color(1, 1, 1, 0)  # transparent until a texture is set
            self._rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[radius]
            )
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *_):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_texture(self, texture):
        self._rect.texture = texture
        self._color.rgba = (1, 1, 1, 1) if texture else (1, 1, 1, 0)

    def clear(self):
        self._rect.texture = None
        self._color.rgba = (1, 1, 1, 0)


class RoundedButton(Button):
    """A flat, rounded button filled with the accent colour, with text drawn in
    a contrasting colour (the body colour) so it stays readable."""

    def __init__(self, fill=(0.89, 0.76, 1, 1), text_color=(0.15, 0.03, 0.25, 1),
                 **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.markup = True
        self._fill = fill
        self._text = text_color
        with self.canvas.before:
            self._fill_color = Color(rgba=fill)
            self._bg = RoundedRectangle(radius=[dp(18)], pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync, disabled=self._on_disabled)
        self.color = text_color
        if self.disabled:
            self._on_disabled(self, True)

    def _sync(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size

    def _on_disabled(self, _w, value):
        if value:
            self._fill_color.rgba = (0.5, 0.5, 0.5, 0.55)
            self.color = (0.85, 0.85, 0.85, 0.7)
        else:
            self._fill_color.rgba = self._fill
            self.color = self._text

    def set_colors(self, fill, text_color):
        """Recolour: button filled with `fill`, label drawn in `text_color`."""
        self._fill = fill
        self._text = text_color
        if not self.disabled:
            self._fill_color.rgba = fill
            self.color = text_color


class SafeTextInput(TextInput):
    """A TextInput whose Paste works exactly once and never crashes on Android.

    Kivy's TextInput.paste() does ``Clipboard.paste().replace(...)``, which
    raises when the Android clipboard provider returns None; and the Android IME
    re-commits the just-pasted text through insert_text() a second time, so the
    text appears doubled. This overrides both paths: paste() is None-safe and
    debounced, and a duplicate re-commit of the same string within a short
    window after a paste is dropped.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # (text, timestamp) of the most recent paste, used to drop the IME's
        # duplicate re-commit and to debounce a repeated paste() call.
        self._paste_marker = (None, 0.0)

    def paste(self):
        self._ensure_clipboard()
        data = Clipboard.paste() or ""
        if not data:
            return
        if not self.multiline:
            data = data.replace("\n", " ")
        last_data, last_when = self._paste_marker
        now = time.time()
        if data == last_data and (now - last_when) < 0.6:
            return  # ignore a rapid duplicate paste() of the same text
        self.delete_selection()
        self._paste_marker = (data, now)
        # Insert via the base method so this legitimate insert bypasses the
        # duplicate-dropping guard in our insert_text() override below.
        TextInput.insert_text(self, data)

    def insert_text(self, substring, from_undo=False):
        data, when = self._paste_marker
        if (not from_undo and substring and substring == data
                and 0 < (time.time() - when) < 0.6):
            # The Android IME re-committed the pasted text; drop the duplicate.
            self._paste_marker = (None, 0.0)
            return
        return super().insert_text(substring, from_undo=from_undo)


class LanguageToggle(BoxLayout):
    """A compact row of buttons, one per available language.

    The active language is highlighted; tapping another switches the whole UI
    via the app's set_language_and_refresh(). One instance is placed on the auth
    screen and another in the game's setup area.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(34))
        kwargs.setdefault("spacing", dp(8))
        super().__init__(**kwargs)
        self._buttons = {}
        for code in TRANSLATIONS:
            btn = RoundedButton(
                text=TRANSLATIONS[code]["lang_name"],
                font_size=sp(13),
            )
            btn.bind(on_release=lambda _w, c=code: self._on_pick(c))
            self._buttons[code] = btn
            self.add_widget(btn)
        self.refresh()

    def _on_pick(self, code):
        app = App.get_running_app()
        if app:
            app.set_language_and_refresh(code)

    def refresh(self):
        """Highlight the active language; dim the others.

        The active button uses the same fill as the other active buttons
        (DEFAULT_FG with body-coloured text); inactive ones stay dimmed.
        """
        for code, btn in self._buttons.items():
            if code == _LANG:
                btn.set_colors(get_rgba(DEFAULT_FG), get_rgba(DEFAULT_BG))
            else:
                btn.set_colors((1, 1, 1, 0.18), get_rgba(DEFAULT_FG))


import math
from kivy.uix.widget import Widget
from kivy.graphics import Color, Ellipse, Line, PushMatrix, PopMatrix, Rotate
from kivy.metrics import dp
from kivy.clock import Clock


# Optional per-style headphone look. Anything missing falls back to "default".
# Set a style's value to None to hide headphones for that style only.
HEADPHONE_STYLES = {
    "default":    {"color": (0.12, 0.12, 0.14, 1), "head_r": dp(9),
                   "cup_w": dp(6), "cup_h": dp(10),
                   "band_w": dp(2.2), "band_pad": dp(1.8)},
    "hiphop":     {"color": (0.95, 0.20, 0.20, 1), "head_r": dp(9),
                   "cup_w": dp(7), "cup_h": dp(11),
                   "band_w": dp(2.6), "band_pad": dp(2.0)},
    "electronic": {"color": (0.20, 0.90, 0.95, 1), "head_r": dp(9),
                   "cup_w": dp(6), "cup_h": dp(10),
                   "band_w": dp(2.2), "band_pad": dp(1.8)},
    "disco":      {"color": (0.95, 0.75, 0.20, 1), "head_r": dp(9),
                   "cup_w": dp(6), "cup_h": dp(10),
                   "band_w": dp(2.2), "band_pad": dp(1.8)},
    # rock / acoustic / pop fall through to "default"
}


class DancingFigure(Widget):
    """Stick figure with optional, tilting headphones."""

    def __init__(self, show_headphones=True, headphone_styles=None, **kwargs):
        super().__init__(**kwargs)
        self._style = "default"
        self._step = 0
        self._sub = 4
        self._clock = None
        self._current_frame = list(DANCE_POSES["default"][0])
        self._fg_rgba = (1, 1, 1, 1)  # current figure colour (shared by headphones)

        self._show_headphones = show_headphones
        self._hp_styles = headphone_styles if headphone_styles is not None \
            else HEADPHONE_STYLES

        with self.canvas:
            # --- skeleton ---
            self._color_inst = Color(1, 1, 1, 1)
            self._head  = Ellipse(pos=(0, 0), size=(dp(18), dp(18)))
            self._neck  = Line(points=[0] * 4, width=dp(1.8))
            self._larm  = Line(points=[0] * 4, width=dp(1.8))
            self._rarm  = Line(points=[0] * 4, width=dp(1.8))
            self._torso = Line(points=[0] * 4, width=dp(1.8))
            self._lleg  = Line(points=[0] * 4, width=dp(1.8))
            self._rleg  = Line(points=[0] * 4, width=dp(1.8))

            # --- headphones (rotated as a group around the head) ---
            PushMatrix()
            self._hp_rot   = Rotate(angle=0, origin=(0, 0))
            self._hp_color = Color(0.12, 0.12, 0.14, 1)
            self._hp_lcup  = Ellipse(pos=(0, 0), size=(dp(6), dp(10)))
            self._hp_rcup  = Ellipse(pos=(0, 0), size=(dp(6), dp(10)))
            self._hp_band  = Line(circle=(0, 0, dp(10), 270, 90),
                                  width=dp(2.2))
            PopMatrix()

        self.bind(pos=self._redraw, size=self._redraw)

    # ---- public API ---- #

    def set_color(self, rgba):
        # The whole figure — skeleton AND headphones — shares one colour.
        self._fg_rgba = tuple(rgba)
        self._color_inst.rgba = rgba
        if self._show_headphones and self._current_hp_spec() is not None:
            self._hp_color.rgba = rgba

    def set_style(self, style, tempo=90):
        self._style = style if style in DANCE_POSES else "default"
        self._step = 0
        self._restart_clock(tempo)

    def start(self, tempo=90):
        self._step = 0
        self._restart_clock(tempo)

    def stop(self):
        if self._clock:
            self._clock.cancel()
            self._clock = None

    def set_headphones(self, enabled):
        """Toggle headphones on/off at runtime."""
        self._show_headphones = bool(enabled)
        self._redraw()

    def set_headphone_style(self, style_name, spec):
        """Override / add a per-style headphone spec at runtime.
        spec is a dict (see HEADPHONE_STYLES) or None to hide for that style."""
        self._hp_styles[style_name] = spec
        self._redraw()

    # ---- internals ---- #

    def _restart_clock(self, tempo):
        self.stop()
        interval = 60.0 / max(float(tempo), 40) / 2 / self._sub
        self._clock = Clock.schedule_interval(self._tick, interval)

    def _tick(self, _dt):
        poses = DANCE_POSES[self._style]
        n = len(poses)
        self._step = (self._step + 1) % (n * self._sub)
        ki = self._step // self._sub
        kf = (ki + 1) % n
        t = (self._step % self._sub) / self._sub
        f0, f1 = poses[ki], poses[kf]
        self._current_frame = [
            (a[0] * (1.0 - t) + b[0] * t, a[1] * (1.0 - t) + b[1] * t)
            for a, b in zip(f0, f1)
        ]
        self._redraw()

    def _current_hp_spec(self):
        """Resolve the headphone spec for the current style, with fallback."""
        spec = self._hp_styles.get(self._style, None)
        if spec is None and self._style in self._hp_styles:
            # Explicit None => hide for this style
            return None
        if spec is None:
            spec = self._hp_styles.get("default")
        return spec

    def _redraw(self, *_):
        frame = self._current_frame
        cx, cy = self.center_x, self.center_y

        def wp(dx, dy):
            return cx + dp(dx), cy + dp(dy)

        hx, hy = wp(*frame[0])
        hr = dp(9)
        self._head.pos  = (hx - hr, hy - hr)
        self._head.size = (hr * 2, hr * 2)

        sx,  sy  = wp(*frame[1])
        lhx, lhy = wp(*frame[2])
        rhx, rhy = wp(*frame[3])
        wx,  wy  = wp(*frame[4])
        lfx, lfy = wp(*frame[5])
        rfx, rfy = wp(*frame[6])

        self._neck.points  = [sx, sy, hx, hy]
        self._larm.points  = [sx, sy, lhx, lhy]
        self._rarm.points  = [sx, sy, rhx, rhy]
        self._torso.points = [sx, sy, wx, wy]
        self._lleg.points  = [wx, wy, lfx, lfy]
        self._rleg.points  = [wx, wy, rfx, rfy]

        self._update_headphones(frame, hx, hy)

    def _update_headphones(self, frame, hx, hy):
        spec = self._current_hp_spec() if self._show_headphones else None

        if spec is None:
            # Hide by zeroing geometry and going fully transparent.
            self._hp_color.rgba = (0, 0, 0, 0)
            self._hp_lcup.size = (0, 0)
            self._hp_rcup.size = (0, 0)
            self._hp_band.width = 0.0001
            self._hp_band.circle = (hx, hy, 0.0001, 270, 90)
            self._hp_rot.angle = 0
            self._hp_rot.origin = (hx, hy)
            return

        head_r   = spec["head_r"]
        cup_w    = spec["cup_w"]
        cup_h    = spec["cup_h"]
        band_w   = spec["band_w"]
        band_pad = spec["band_pad"]

        # Tilt = neck angle relative to vertical (head - shoulder).
        nx = frame[0][0] - frame[1][0]
        ny = frame[0][1] - frame[1][1]
        angle_deg = math.degrees(math.atan2(nx, ny)) if (nx or ny) else 0.0

        self._hp_rot.origin = (hx, hy)
        self._hp_rot.angle = angle_deg

        # Headphones match the figure colour (not the per-style spec colour).
        self._hp_color.rgba = self._fg_rgba

        # Ear cups — flank the head, pushed outward so they read as separate from
        # it (mostly outside the head circle, only a slight overlap).
        self._hp_lcup.pos  = (hx - head_r - cup_w * 0.8, hy - cup_h / 2)
        self._hp_lcup.size = (cup_w, cup_h)
        self._hp_rcup.pos  = (hx + head_r - cup_w * 0.2, hy - cup_h / 2)
        self._hp_rcup.size = (cup_w, cup_h)

        # Headband — top semicircle, raised a little above the head so it stays
        # clear of the skull outline. Kivy circle angles start at the top (0°) and
        # increase clockwise, so 270°→450° arcs OVER the head (passing through
        # 360°/top); 270→90 would trace the bottom instead.
        arch_r = head_r + band_pad + dp(3)
        self._hp_band.width  = band_w
        self._hp_band.circle = (hx, hy, arch_r, 270, 450)

# --------------------------------------------------------------------------- #
# Auth screen
# --------------------------------------------------------------------------- #
class AuthScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        # True once Connect has opened the browser and we're waiting for the user
        # to paste the redirect URL. Guards against a duplicate browser open: on
        # Android the button's on_release is re-dispatched when the app regains
        # focus after the browser Intent, which would otherwise rebuild the auth
        # manager (new PKCE verifier) and open a second tab.
        self._auth_pending = False
        root = GradientBackground(
            bottom_rgb=(38, 7, 65),
            orientation="vertical",
            padding=dp(24),
            spacing=dp(16),
        )
        root.add_widget(Label(text="", size_hint_y=0.2))

        # Language toggle (English | Català | ...) at the top.
        self.lang_toggle = LanguageToggle()
        root.add_widget(self.lang_toggle)

        root.add_widget(
            Label(
                text="[b]Endevina'm[/b]",
                markup=True,
                font_size=sp(34),
                color=get_rgba(DEFAULT_FG),
                size_hint_y=None,
                height=dp(60),
            )
        )
        self.info = Label(
            text=tr("auth_info"),
            markup=True,
            halign="center",
            valign="middle",
            color=get_rgba(DEFAULT_FG),
            font_size=sp(14),
        )
        self.info.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        root.add_widget(self.info)

        # Redirect URI the user must register in their Spotify app. Shown in a
        # read-only (but selectable) field with a copy icon inside its right
        # edge, so a phone-only user can grab it without the README open.
        self.redirect_uri_caption = Label(
            text=tr("redirect_uri_caption"),
            color=(get_rgba(DEFAULT_FG)[0], get_rgba(DEFAULT_FG)[1],
                   get_rgba(DEFAULT_FG)[2], 0.8),
            font_size=sp(13),
            size_hint_y=None,
            height=dp(20),
            halign="left",
            valign="middle",
        )
        self.redirect_uri_caption.bind(
            size=lambda w, *_: setattr(w, "text_size", w.size)
        )
        root.add_widget(self.redirect_uri_caption)
        # Read-only but selectable, so a phone-only user can long-press to
        # select the URI and copy it via the native bubble.
        self.redirect_uri_field = SafeTextInput(
            text=REDIRECT_URI,
            readonly=True,
            multiline=False,
            use_bubble=True,
            use_handles=True,
            size_hint_y=None,
            height=dp(48),
            font_size=sp(13),
            foreground_color=get_rgba(DEFAULT_FG),
            background_color=(1, 1, 1, 0.08),
            cursor_color=get_rgba(DEFAULT_FG),
            padding=[dp(10), dp(12)],
        )
        root.add_widget(self.redirect_uri_field)

        self.client_id_input = SafeTextInput(
            text=self.app.client_id or "",
            hint_text=tr("client_id_hint"),
            multiline=False,
            use_bubble=True,
            use_handles=True,
            size_hint_y=None,
            height=dp(48),
            font_size=sp(15),
            foreground_color=get_rgba(DEFAULT_FG),
            background_color=(1, 1, 1, 0.08),
            cursor_color=get_rgba(DEFAULT_FG),
            padding=[dp(12), dp(12)],
        )
        # Select the whole field on focus so a paste replaces the pre-filled
        # value instead of appending to it (which would double the Client ID).
        self.client_id_input.bind(
            focus=lambda inst, val: Clock.schedule_once(lambda _dt: inst.select_all())
            if val else None
        )
        # Changing the Client ID starts a fresh authorization: drop the pending
        # guard so the next Connect rebuilds the auth manager and re-opens.
        self.client_id_input.bind(
            text=lambda *_: setattr(self, "_auth_pending", False)
        )
        root.add_widget(self.client_id_input)

        self.connect_button = RoundedButton(
            text=tr("connect_btn"),
            fill=(*SPOTIFY_GREEN, 1),
            text_color=(0, 0, 0, 1),
            size_hint_y=None,
            height=dp(56),
            font_size=sp(18),
        )
        self.connect_button.bind(on_release=lambda *_: self.start_auth())
        root.add_widget(self.connect_button)

        self.redirect_input = SafeTextInput(
            hint_text=tr("redirect_hint"),
            multiline=False,
            use_bubble=True,
            use_handles=True,
            size_hint_y=None,
            height=dp(48),
            font_size=sp(15),
            foreground_color=get_rgba(DEFAULT_FG),
            background_color=(1, 1, 1, 0.08),
            cursor_color=get_rgba(DEFAULT_FG),
            padding=[dp(12), dp(12)],
        )
        root.add_widget(self.redirect_input)

        self.finish_button = RoundedButton(
            text=tr("finish_btn"),
            fill=get_rgba(DEFAULT_FG),
            text_color=get_rgba(DEFAULT_BG),
            size_hint_y=None,
            height=dp(56),
            font_size=sp(18),
        )
        self.finish_button.bind(on_release=lambda *_: self.finish_auth())
        root.add_widget(self.finish_button)
        root.add_widget(Label(text="", size_hint_y=0.2))
        self.add_widget(root)

    def apply_language(self):
        """Re-set this screen's static text from the active language."""
        self.lang_toggle.refresh()
        self.info.text = tr("auth_info")
        self.redirect_uri_caption.text = tr("redirect_uri_caption")
        self.client_id_input.hint_text = tr("client_id_hint")
        self.connect_button.text = tr("connect_btn")
        self.redirect_input.hint_text = tr("redirect_hint")
        self.finish_button.text = tr("finish_btn")

    def start_auth(self):
        # Remove any whitespace a mobile paste may add, then validate the format
        # before doing anything (a doubled/garbled id would fail at Spotify).
        client_id = "".join(self.client_id_input.text.split())
        if not client_id:
            self.info.text = tr("need_client_id")
            return
        if not CLIENT_ID_RE.match(client_id):
            self.info.text = tr("bad_client_id")
            return
        # Already waiting on a redirect from a previous tap? Don't rebuild or
        # re-open - just remind the user. This swallows the duplicate on_release
        # Android re-dispatches when returning from the browser, which would
        # otherwise rotate the PKCE verifier and strand the first authorization.
        if self._auth_pending:
            self.info.text = tr("authorize_then_paste")
            return
        # Persist the Client ID and build the auth manager once per Client ID.
        # Reusing the existing manager keeps a single, stable PKCE verifier so the
        # code the user authorizes always matches at exchange time.
        self.app.save_client_id(client_id)
        if self.app.auth_manager is None or self.app._auth_client_id != client_id:
            self.app.build_auth_manager(client_id)
        url = self.app.auth_manager.get_authorize_url()
        self._auth_pending = True
        open_url(url)
        self.info.text = tr("authorize_then_paste")

    def finish_auth(self):
        if not self.app.auth_manager:
            self.info.text = tr("need_connect_first")
            return
        url = self.redirect_input.text.strip()
        if not url:
            self.info.text = tr("need_redirect")
            return
        self.info.text = tr("connecting")
        threading.Thread(target=self._exchange, args=(url,), daemon=True).start()

    def _exchange(self, url):
        try:
            code = self.app.auth_manager.parse_response_code(url)
            # parse_response_code returns the URL unchanged when it finds no
            # ?code= param. Passing that on would either fail at Spotify or, if
            # falsy, send spotipy into its blocking interactive prompt - so bail
            # out with a clear message instead.
            if not code or code == url:
                self._on_auth_error(tr("no_code"))
                return
            # check_cache=False: we have a fresh code, so don't let a stale
            # cached token short-circuit this explicit login.
            self.app.auth_manager.get_access_token(code, check_cache=False)
            self._on_connected()
        except Exception as exc:  # noqa: BLE001
            self._on_auth_error(str(exc))

    @mainthread
    def _on_connected(self):
        self._auth_pending = False
        self.app.build_spotify_client()
        self.app.go_to_game()

    @mainthread
    def _on_auth_error(self, message):
        # Clear the guard so the user can retry (re-open the browser) after a
        # failed exchange.
        self._auth_pending = False
        self.info.text = tr("connect_error", message=message)


# --------------------------------------------------------------------------- #
# Game screen
# --------------------------------------------------------------------------- #
class GameScreen(Screen):
    def __init__(self, app, **kwargs):
        super().__init__(**kwargs)
        self.app = app

        # Game state
        self.total_playlists = []
        self.total_tracks = []
        self.track_ids = set()
        self.current_track = None
        self.first_song = True
        self.busy = False
        self.awaiting_reveal = False       # True while a song plays, before reveal
        self.selected_device_id = None    # None → fall back to first active device
        self._last_action_time = 0.0      # debounce play/reveal/next double-fires
        self.previous_years = []          # years of all songs revealed this session

        # Current revealed song details
        self.song_name = ""
        self.artists = ""
        self.release_year = ""
        self.spotify_uri = ""
        self.album_art_url = ""

        self.fg = get_rgba(DEFAULT_FG)

        # Solid body of the dominant colour (recoloured from the album art).
        self.bg = SolidBackground(
            rgb=get_color255(DEFAULT_BG), orientation="vertical"
        )

        # Top bar: a black band spanning the full width, with the game logo on
        # the left and the Spotify mark on the right. The game logo takes the
        # flexible space; the Spotify mark sits in a fixed-width slot that, as
        # the bar resizes, switches between the full logo (when there's room for
        # it plus its clear space) and the standalone icon (when the bar is too
        # narrow). Spotify's branding rules only permit the icon alone when the
        # full logo doesn't fit - see _sync_spotify_mark().
        top = BoxLayout(
            size_hint_y=None, height=dp(52), padding=[dp(14), dp(8)], spacing=dp(14)
        )
        with top.canvas.before:
            Color(*DARK, 1)
            self._top_rect = Rectangle(pos=top.pos, size=top.size)
        top.bind(
            pos=lambda w, *_: setattr(self._top_rect, "pos", w.pos),
            size=lambda w, *_: setattr(self._top_rect, "size", w.size),
        )
        top.add_widget(self._make_logo(LOGO_GAME, "[b]Endevina'm[/b]", (1, 1, 1, 1)))

        # Read the full logo's aspect ratio once (off disk, not the live texture,
        # so it's known before any layout) to decide when it fits.
        self._has_full_logo = os.path.exists(LOGO_SPOTIFY)
        self._has_spotify_icon = os.path.exists(LOGO_SPOTIFY_ICON)
        self._spotify_full_aspect = 3.3  # sensible default until measured
        if self._has_full_logo:
            try:
                ci = CoreImage(LOGO_SPOTIFY)
                if ci.height:
                    self._spotify_full_aspect = ci.width / ci.height
            except Exception as exc:  # noqa: BLE001 - keep the default aspect
                print("could not measure Spotify logo:", exc)

        self._spotify_mark = self._make_spotify_mark()
        # Right-aligned slot; its width (and the mark's source/size) is set by
        # _sync_spotify_mark whenever the bar resizes.
        self._spotify_slot = AnchorLayout(
            anchor_x="right", anchor_y="center", size_hint_x=None, width=dp(36)
        )
        self._spotify_slot.add_widget(self._spotify_mark)
        top.add_widget(self._spotify_slot)
        top.bind(width=self._sync_spotify_mark)
        Clock.schedule_once(lambda _dt: self._sync_spotify_mark(), 0)
        self.bg.add_widget(top)

        # Thin gradient strip blending the black bar into the body colour.
        self.strip = GradientStrip(
            body_rgb=get_color255(DEFAULT_BG), size_hint_y=None, height=dp(36)
        )
        self.bg.add_widget(self.strip)

        # Everything below the strip lives in a padded inner column.
        inner = BoxLayout(
            orientation="vertical", padding=[dp(16), dp(8)], spacing=dp(8)
        )

        # Language toggle — setup-only, removed once the game starts.
        self.lang_row = LanguageToggle()
        inner.add_widget(self.lang_row)

        # Playlist entry + entry buttons (hidden once the game starts).
        self.entry = TextInput(
            hint_text=tr("playlist_hint"),
            multiline=False,
            use_bubble=True,
            use_handles=True,
            size_hint_y=None,
            height=dp(46),
            font_size=sp(15),
            foreground_color=self.fg,
            background_color=(1, 1, 1, 0.10),
            cursor_color=self.fg,
            padding=[dp(12), dp(12)],
        )
        inner.add_widget(self.entry)

        self.entry_buttons = BoxLayout(
            size_hint_y=None, height=dp(50), spacing=dp(10)
        )
        self.details_button = RoundedButton(
            text=tr("see_details_btn"),
            fill=self.fg,
            text_color=get_rgba(DEFAULT_BG),
            font_size=sp(16),
        )
        self.details_button.bind(on_release=lambda *_: self.load_playlist_details())
        self.add_button = RoundedButton(
            text=tr("add_btn"),
            fill=self.fg,
            text_color=get_rgba(DEFAULT_BG),
            font_size=sp(16),
        )
        self.add_button.bind(on_release=lambda *_: self.add_to_list())
        self.entry_buttons.add_widget(self.details_button)
        self.entry_buttons.add_widget(self.add_button)
        inner.add_widget(self.entry_buttons)

        # Playlist name label.
        self.name_label = Label(
            text="",
            markup=True,
            color=self.fg,
            font_size=sp(16),
            size_hint_y=None,
            height=dp(44),
            halign="center",
            valign="middle",
        )
        self.name_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        inner.add_widget(self.name_label)

        # Centred content: album art on top of the song/info text, with flexible
        # spacers above and below so the block stays vertically centred (no big
        # blank gap, and nothing shows where the art is when none is loaded).
        content = BoxLayout(orientation="vertical", spacing=dp(12))
        content.add_widget(Widget())  # top spacer

        # Running list of years revealed this session, so a fast game doesn't lose
        # the previous-song info when the next dancer takes over. Hidden until the
        # first reveal happens (height=0 → no layout space stolen).
        self.history_label = Label(
            text="",
            color=(self.fg[0], self.fg[1], self.fg[2], 0.55),
            font_size=sp(18),
            size_hint_y=None,
            height=0,
            halign="center",
        )
        self.history_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        content.add_widget(self.history_label)

        # Rounded corners scale with screen size (8 px on large screens, 4 px on
        # small ones) per Spotify's artwork guidelines.
        art_radius = dp(8) if min(Window.width, Window.height) >= dp(600) else dp(4)
        self.album_image = AlbumArt(
            radius=art_radius,
            size_hint=(None, None),
            size=(0, 0),
            opacity=0,
            pos_hint={"center_x": 0.5},
        )
        content.add_widget(self.album_image)

        # Dancing figure (hidden until a song plays; shown in place of album art).
        self.dancing_figure = DancingFigure(
            show_headphones=True, headphone_styles=HEADPHONE_STYLES,
            size_hint=(None, None),
            size=(0, 0),
            opacity=0,
            pos_hint={"center_x": 0.5},
        )
        content.add_widget(self.dancing_figure)

        self.info_label = Label(
            text=tr("how_to_play"),
            markup=True,
            color=self.fg,
            font_size=sp(16),
            size_hint_y=None,
            halign="center",
            valign="middle",
        )
        self.info_label.bind(
            width=lambda w, val: setattr(w, "text_size", (val, None)),
            texture_size=lambda w, val: setattr(w, "height", val[1]),
        )
        content.add_widget(self.info_label)
        content.add_widget(Widget())  # bottom spacer
        inner.add_widget(content)

        # Spotify-open hint — always visible, reminds users to have Spotify running.
        self.hint_label = Label(
            text=tr("keep_spotify_open"),
            color=(1, 1, 1, 0.40),
            font_size=sp(11),
            size_hint_y=None,
            height=dp(18),
            halign="center",
        )
        self.hint_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        inner.add_widget(self.hint_label)

        body = get_rgba(DEFAULT_BG)

        # Device picker — its own full-width row above the main buttons, so a
        # selected device name has the whole row to fit (esp. on phone screens).
        # Removed once the game starts (setup-only, like the hint label).
        self.device_button = RoundedButton(
            text=tr("set_device_btn"),
            fill=self.fg,
            text_color=body,
            font_name=SYMBOL_FONT,
            font_size=sp(15),
            disabled=True,
            size_hint_y=None,
            height=dp(44),
        )
        self.device_button.bind(on_release=lambda *_: self.open_device_popup())
        inner.add_widget(self.device_button)

        # Bottom buttons. Symbols come from DejaVuSans (Roboto lacks ♫ / power).
        self.bottom_bar = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(8))
        bottom = self.bottom_bar
        # Retry re-fires playback of whatever track was last picked. Useful when
        # Next succeeded in the API but no audio reached the device (Spotify
        # Connect can drop a beat). Enabled once a track exists.
        self.retry_button = RoundedButton(
            text=tr("retry_btn"),
            fill=self.fg,
            text_color=body,
            font_name=SYMBOL_FONT,
            font_size=sp(15),
            disabled=True,
        )
        self.retry_button.bind(on_release=lambda *_: self.retry_song())
        self.play_button = RoundedButton(
            text=tr("start_game_btn"),
            fill=self.fg,
            text_color=body,
            font_size=sp(17),
            disabled=True,
            size_hint_x=1.4,
        )
        self.play_button.bind(on_release=lambda *_: self.on_play_pressed())
        self.next_button = RoundedButton(
            text=tr("next_btn"),
            fill=self.fg,
            text_color=body,
            font_name=SYMBOL_FONT,
            font_size=sp(15),
            disabled=True,
        )
        self.next_button.bind(on_release=lambda *_: self.next_song())
        bottom.add_widget(self.retry_button)
        bottom.add_widget(self.play_button)
        bottom.add_widget(self.next_button)
        inner.add_widget(bottom)

        self.bg.add_widget(inner)
        self.add_widget(self.bg)

    # ----- text helpers ----------------------------------------------------- #
    @staticmethod
    def _make_logo(source, fallback_text, fallback_color):
        """The game logo scaled to fit the top bar and pinned to the left.

        The logo sits in a left-anchored slot that spans the flexible space the
        Spotify mark's fixed-width slot leaves, so it stays flush with the bar's
        left padding instead of floating in the centre. It's given a fixed
        height (the bar's content height) and a width derived from its aspect
        ratio; fit_mode="contain" then keeps it crisp without overflowing.
        Falls back to left-aligned text if the image is missing.
        """
        content_h = dp(52) - dp(8) * 2  # top bar height minus vertical padding
        if os.path.exists(source):
            aspect = 3.0  # sensible default until measured off disk
            try:
                ci = CoreImage(source)
                if ci.height:
                    aspect = ci.width / ci.height
            except Exception as exc:  # noqa: BLE001 - keep the default aspect
                print("could not measure game logo:", exc)
            logo = KivyImage(
                source=source, fit_mode="contain", mipmap=True,
                size_hint=(None, None),
                height=content_h, width=content_h * aspect,
            )
        else:
            logo = Label(
                text=fallback_text, markup=True, font_size=sp(20),
                color=fallback_color, size_hint=(None, None),
                halign="left", valign="middle",
            )
            logo.bind(texture_size=lambda w, s: setattr(w, "size", s))
        wrap = AnchorLayout(anchor_x="left", anchor_y="center")
        wrap.add_widget(logo)
        return wrap

    def _make_spotify_mark(self):
        """The Spotify mark widget: the full logo if present, else the icon,
        else a text fallback. _sync_spotify_mark() swaps its source/size as the
        bar resizes; size_hint is fixed so we can size it ourselves."""
        source = LOGO_SPOTIFY if self._has_full_logo else LOGO_SPOTIFY_ICON
        if self._has_full_logo or self._has_spotify_icon:
            return KivyImage(
                source=source, fit_mode="contain", mipmap=True,
                size_hint=(None, None), height=dp(36), width=dp(36),
            )
        return Label(
            text="[b]Spotify[/b]", markup=True, font_size=sp(20),
            color=(*SPOTIFY_GREEN, 1),
        )

    def _sync_spotify_mark(self, *_):
        """Switch the Spotify mark between full logo and icon to suit the bar
        width, keeping Spotify's required clear space (half the icon height).

        The full logo is used only when the bar is wide enough to fit it plus
        clear space while still leaving room for the game logo; otherwise the
        standalone icon is shown (which is the only case Spotify allows it).
        """
        mark = getattr(self, "_spotify_mark", None)
        slot = getattr(self, "_spotify_slot", None)
        if not isinstance(mark, KivyImage) or slot is None:
            return  # text fallback - nothing to size
        content_h = dp(36)
        clear = content_h / 2  # Spotify clear space = half the icon's height
        bar = slot.parent
        bar_w = bar.width if bar else 0
        full_w = content_h * self._spotify_full_aspect
        # Use the full logo only on a comfortably wide bar (tablets / desktop);
        # phone-width bars get the icon so the wordmark isn't crowded against the
        # game logo. The second test is a safety check that it actually fits.
        room_for_full = bar_w >= dp(500) and bar_w >= full_w + clear + dp(160)
        use_full = self._has_full_logo and room_for_full
        if use_full:
            source, width = LOGO_SPOTIFY, full_w
        elif self._has_spotify_icon:
            source, width = LOGO_SPOTIFY_ICON, content_h  # square icon
        else:
            # No icon asset yet: keep the full logo at its natural width rather
            # than squashing it into the icon's square slot.
            source, width = LOGO_SPOTIFY, full_w
        if mark.source != source:
            mark.source = source
        mark.height = content_h
        mark.width = width
        slot.width = mark.width + clear

    def apply_language(self):
        """Re-set the setup-phase text from the active language.

        Only the setup screen exposes the language toggle, so this never needs to
        touch mid-game captions (e.g. the play button reading "Reveal song").
        """
        self.lang_row.refresh()
        self.entry.hint_text = tr("playlist_hint")
        self.details_button.text = tr("see_details_btn")
        self.add_button.text = tr("add_btn")
        self.hint_label.text = tr("keep_spotify_open")
        self.retry_button.text = tr("retry_btn")
        self.next_button.text = tr("next_btn")
        if not self.selected_device_id:
            self.device_button.text = tr("set_device_btn")
        if self.first_song:
            self.play_button.text = tr("start_game_btn")
            self.info_label.text = tr("how_to_play")

    # ----- networking wrappers (run off the UI thread) ---------------------- #
    def _run(self, target, *args):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._wrap, args=(target, args), daemon=True).start()

    def _wrap(self, target, args):
        try:
            target(*args)
        except PlaylistForbidden:
            self._set_info(tr("playlist_forbidden"))
        except Exception as exc:  # noqa: BLE001
            self._set_info(tr("generic_error", exc=exc))
        finally:
            self.busy = False

    @mainthread
    def _set_info(self, text, font_size=None):
        self.info_label.text = text
        if font_size:
            self.info_label.font_size = font_size

    @mainthread
    def _set_name(self, text):
        self.name_label.text = text

    # ----- playlist details ------------------------------------------------- #
    def load_playlist_details(self):
        self._set_info(tr("loading_playlist"))
        self._run(self._load_playlist_details)

    def _load_playlist_details(self):
        sp = self.app.sp
        playlist_id = get_playlist_id(self.entry.text)
        if not playlist_id:
            self._set_info(tr("invalid_link"))
            return
        name = sp.user_playlist(user=None, playlist_id=playlist_id, fields="name")[
            "name"
        ]
        self._set_name(tr("playlist_name", name=name))
        tracks = self._fetch_all_tracks(playlist_id)
        old, six, sev, eig, nin, two, ten, twe = year_counter(tracks)
        self._set_info(
            tr("details_block", old=old, six=six, sev=sev, eig=eig,
               nin=nin, two=two, ten=ten, twe=twe, total=len(tracks))
        )

    def _fetch_all_tracks(self, playlist_id):
        # The Feb 2026 Web API changes removed /playlists/{id}/tracks for newly
        # created apps; the current endpoint is /playlists/{id}/items. spotipy
        # still targets the old path, so request /items directly via sp._get
        # (which reuses spotipy's bearer-token handling and raises
        # SpotifyException on errors). sp.next() follows the paging "next" URL.
        sp = self.app.sp
        try:
            results = sp._get(
                f"playlists/{playlist_id}/items",
                limit=100, offset=0, additional_types="track",
            )
            tracks = list(results["items"])
            while results.get("next"):
                results = sp.next(results)
                tracks.extend(results["items"])
        except spotipy.exceptions.SpotifyException as exc:
            if getattr(exc, "http_status", None) == 403:
                raise PlaylistForbidden() from exc
            raise
        # Feb 2026 also renamed each entry's nested object from "track" to
        # "item". Normalize back to "track" so the rest of the code is unchanged
        # and the old (grandfathered) response shape keeps working too.
        for entry in tracks:
            if "track" not in entry and "item" in entry:
                entry["track"] = entry["item"]
        return tracks

    # ----- add to pool ------------------------------------------------------ #
    def add_to_list(self):
        self._set_info(tr("adding_tracks"))
        self._run(self._add_to_list)

    def _add_to_list(self):
        sp = self.app.sp
        playlist_id = get_playlist_id(self.entry.text.strip())
        if not playlist_id:
            self._set_info(tr("invalid_link"))
            return
        name = sp.user_playlist(user=None, playlist_id=playlist_id, fields="name")[
            "name"
        ]
        self.total_playlists.append(name)
        tracks = self._fetch_all_tracks(playlist_id)
        added = 0
        for track in tracks:
            tid = (track.get("track") or {}).get("id")
            if tid and tid not in self.track_ids:
                self.total_tracks.append(track)
                self.track_ids.add(tid)
                added += 1
        self._set_name(tr("playlist_name", name=name))
        self._set_info(tr("added_tracks", n=added, pool=len(self.total_tracks)))
        self._enable_play()

    @mainthread
    def _enable_play(self):
        self.play_button.disabled = False
        self.device_button.disabled = False
        self.entry.text = ""

    # ----- play / reveal / next -------------------------------------------- #
    def _debounce(self, gap=0.5):
        """Reject a game-state action that arrives too soon after the previous one.

        On Android the `mouse` input provider (enabled to suppress desktop
        multitouch dots) emits a ghost event alongside the real touch, so one tap
        can fire an action twice — which would, e.g., start a song and instantly
        reveal it. A short cooldown collapses those duplicates while never
        interfering with real play (you listen for seconds before revealing).
        """
        now = time.monotonic()
        if now - self._last_action_time < gap:
            return False
        self._last_action_time = now
        return True

    def on_play_pressed(self):
        # The same button doubles as "Play" and then "Reveal".
        if not self._debounce():
            return
        if self.awaiting_reveal:
            self.reveal_song()
        else:
            self.play_random_song()

    def trigger_active_action(self):
        """Run whatever the main bottom button would do right now (used by the
        right-click / long-press gesture). Quit stays button-only on purpose."""
        if not self.play_button.disabled:
            self.on_play_pressed()
        elif not self.next_button.disabled:
            self.next_song()

    def play_random_song(self):
        if not self.total_tracks:
            self._set_info(tr("no_tracks_left"))
            return
        if self.first_song:
            self.first_song = False
            # Hide the pre-game widgets once the game starts: the playlist entry,
            # the Spotify-open hint, the device picker, and the language toggle
            # are all setup-only.
            for widget in (self.entry, self.entry_buttons, self.hint_label,
                           self.device_button, self.lang_row):
                if widget.parent:
                    widget.parent.remove_widget(widget)
            names = ", ".join(self.total_playlists)
            self._set_name(tr("sampling_from", names=names))
        self._hide_album()
        self._show_dancer()
        self._set_info(tr("song_playing"))
        self.awaiting_reveal = True
        self.play_button.text = tr("reveal_btn")
        self.play_button.disabled = True
        self._run(self._play_random_song)

    def _play_random_song(self):
        sp = self.app.sp
        self.current_track = random.choice(self.total_tracks)
        track = self.current_track["track"]
        self.song_name = track["name"]
        self.artists = ", ".join(a["name"] for a in track["artists"])
        self.release_year = (track["album"].get("release_date") or "")[:4]
        self.spotify_uri = track["uri"]
        images = track["album"].get("images") or []
        self.album_art_url = images[0]["url"] if images else ""

        # Recolour UI from album art while the song plays.
        if self.album_art_url:
            try:
                art_bytes = requests.get(self.album_art_url, timeout=15).content
                palette = extract_palette(art_bytes, count=6)
                if palette:
                    self._apply_palette(palette)
            except Exception as exc:  # noqa: BLE001
                print("art fetch failed:", exc)

        # Find the target device: user-selected (sticky) or first active.
        devices = sp.devices().get("devices", [])
        device_id = self.selected_device_id
        if device_id:
            # Verify the chosen device is still visible; fall back if not.
            known_ids = {d["id"] for d in devices}
            if device_id not in known_ids:
                device_id = None
        if not device_id:
            if not devices:
                self._set_error(tr("no_device_play"))
                return
            device_id = devices[0]["id"]
        try:
            sp.start_playback(device_id=device_id, uris=[self.spotify_uri])
        except spotipy.exceptions.SpotifyException as exc:
            self._set_error(tr("playback_error", exc=exc))
            return
        self._reenable_play()
        # Pick the dance style from the artist's genres (background thread).
        threading.Thread(
            target=self._fetch_style, args=(track,), daemon=True
        ).start()

    @mainthread
    def _reenable_play(self):
        self.play_button.disabled = False
        # Once a track has been picked, retry is meaningful for the rest of the
        # session (re-fires whatever the latest spotify_uri is).
        self.retry_button.disabled = False

    @mainthread
    def _apply_palette(self, palette):
        # Body = dominant colour; text/buttons = the most contrasting palette
        # colour, which reads as a clear secondary against the body.
        dominant = palette[0]
        secondary = pick_contrast_color(dominant, palette[1:])
        self.fg = (secondary[0] / 255, secondary[1] / 255, secondary[2] / 255, 1)
        body = (dominant[0] / 255, dominant[1] / 255, dominant[2] / 255, 1)
        self.bg.set_color(dominant)
        self.strip.set_body_color(dominant)
        for label in (self.name_label, self.info_label):
            label.color = self.fg
        # History label sits behind the action, so keep it dimmer than fg.
        self.history_label.color = (self.fg[0], self.fg[1], self.fg[2], 0.55)
        # Buttons: filled with the secondary colour, text in the body colour.
        for btn in (self.play_button, self.next_button, self.retry_button,
                    self.device_button):
            btn.set_colors(self.fg, body)
        # Dancing figure tracks the foreground color live.
        self.dancing_figure.set_color(self.fg)

    def reveal_song(self):
        self.awaiting_reveal = False
        self._hide_dancer()
        self._set_info(
            f"[b]{self.artists}[/b]\n{self.release_year}\n{self.song_name}",
            font_size=sp(22),
        )
        if self.album_art_url:
            self._run(self._load_album_art)
        self.play_button.disabled = True
        self.next_button.disabled = False
        # Retry only makes sense while a song is playing; once revealed, the
        # user moves on with Next (which re-enables Retry on the next play).
        self.retry_button.disabled = True
        # Record the year now, but only surface it on the next dancer screen —
        # while the album cover and full song info are shown it would be redundant.
        if self.release_year:
            self.previous_years.append(self.release_year)
        threading.Thread(target=self._pause_playback, daemon=True).start()

    def _pause_playback(self):
        try:
            self.app.sp.pause_playback()
        except spotipy.exceptions.SpotifyException as exc:
            print("pause failed:", exc)

    def _load_album_art(self):
        data = requests.get(self.album_art_url, timeout=15).content
        self._set_album_texture(data)

    @mainthread
    def _hide_album(self):
        self.album_image.clear()
        self.album_image.size = (0, 0)
        self.album_image.opacity = 0

    @mainthread
    def _set_album_texture(self, data):
        try:
            texture = CoreImage(io.BytesIO(data), ext="jpg").texture
            side = min(Window.width * 0.7, Window.height * 0.42)
            self.album_image.size = (side, side)
            self.album_image.set_texture(texture)
            self.album_image.opacity = 1
        except Exception as exc:  # noqa: BLE001
            print("album art display failed:", exc)

    # ----- dancer helpers --------------------------------------------------- #

    @mainthread
    def _show_dancer(self):
        fig = self.dancing_figure
        side = min(Window.width * 0.35, dp(140))
        fig.size = (side, side * 1.2)
        fig.opacity = 1
        fig.set_color(self.fg)
        fig.start(tempo=90)  # default tempo; updated once audio features arrive
        # Surface the previous song's year only on this guessing screen.
        self._update_history_label()

    @mainthread
    def _hide_dancer(self):
        fig = self.dancing_figure
        fig.stop()
        fig.opacity = 0
        fig.size = (0, 0)
        # Hide the year hint while the album cover / full song info are shown.
        self.history_label.text = ""
        self.history_label.height = 0
        # Reset font size for the info label (may have been changed to sp(22) on reveal)
        self.info_label.font_size = sp(16)

    # ----- dance style from artist genres (runs on background thread) ------- #

    def _fetch_style(self, track):
        try:
            artist_ids = [a["id"] for a in track.get("artists", []) if a.get("id")]
            genres = []
            for aid in artist_ids[:2]:  # union genres from the first 1-2 artists
                try:
                    genres += self.app.sp.artist(aid).get("genres", [])
                except Exception:  # noqa: BLE001 - skip a bad artist lookup
                    pass
            style = classify_genres(genres)
            self._apply_dance_style(style, STYLE_TEMPO.get(style, 100))
        except Exception as exc:  # noqa: BLE001
            print("dance style classification failed:", exc)

    @mainthread
    def _apply_dance_style(self, style, tempo):
        # Only update if we're still in "playing" state (figure is visible).
        if self.dancing_figure.opacity:
            self.dancing_figure.set_style(style, tempo)

    # ----- error display ---------------------------------------------------- #

    @mainthread
    def _set_error(self, text):
        """Show an error with a temporary red highlight on the info label."""
        self.info_label.text = text
        self.info_label.color = (1.0, 0.45, 0.35, 1.0)
        self.awaiting_reveal = False
        self.play_button.text = tr("start_game_btn")
        self.play_button.disabled = False
        # If we already had a track loaded, leave retry available so the user can
        # try again after a transient Spotify Connect glitch.
        if self.spotify_uri:
            self.retry_button.disabled = False
        self._hide_dancer()
        # Restore the theme color after 2.5 s
        Clock.schedule_once(
            lambda _: setattr(self.info_label, "color", self.fg), 2.5
        )

    # ----- retry / history -------------------------------------------------- #

    def retry_song(self):
        """Re-fire playback of the most recently picked track.

        Useful when Next succeeded in the API but no audio arrived at the device
        (Spotify Connect occasionally drops the start_playback call silently).
        Doesn't pick a new track and doesn't touch the rest of the UI state.
        """
        if not self._debounce():
            return
        if not self.spotify_uri:
            return
        threading.Thread(target=self._retry_song, daemon=True).start()

    def _retry_song(self):
        sp = self.app.sp
        try:
            devices = sp.devices().get("devices", [])
            device_id = self.selected_device_id
            if device_id and device_id not in {d["id"] for d in devices}:
                device_id = None
            if not device_id:
                if not devices:
                    self._set_error(tr("no_device_retry"))
                    return
                device_id = devices[0]["id"]
            sp.start_playback(device_id=device_id, uris=[self.spotify_uri])
        except spotipy.exceptions.SpotifyException as exc:
            self._set_error(tr("retry_failed", exc=exc))

    @mainthread
    def _update_history_label(self):
        """Show the year of the most recently revealed song, nothing more."""
        last = next((y for y in reversed(self.previous_years) if y), "")
        if not last:
            self.history_label.text = ""
            self.history_label.height = 0
            return
        self.history_label.text = tr("previous_song", year=last)
        self.history_label.height = dp(28)

    # ----- device selector -------------------------------------------------- #

    def open_device_popup(self):
        """Fetch available Spotify devices and show a picker popup."""
        self.device_button.disabled = True
        threading.Thread(
            target=self._load_devices_for_popup, daemon=True
        ).start()

    def _load_devices_for_popup(self):
        try:
            devices = self.app.sp.devices().get("devices", [])
        except Exception as exc:  # noqa: BLE001
            print("device list failed:", exc)
            devices = []
        self._show_device_popup(devices)

    @mainthread
    def _show_device_popup(self, devices):
        self.device_button.disabled = False

        body_color = get_rgba(DEFAULT_BG)
        _popup_ref = [None]  # mutable container so handlers can dismiss it

        content = BoxLayout(
            orientation="vertical", spacing=dp(8), padding=dp(10)
        )

        if not devices:
            content.add_widget(
                Label(
                    text=tr("no_devices_found"),
                    halign="center",
                    color=(1, 1, 1, 0.85),
                    markup=True,
                )
            )
        else:
            sv = ScrollView()
            btn_col = BoxLayout(
                orientation="vertical",
                spacing=dp(6),
                size_hint_y=None,
            )
            btn_col.bind(minimum_height=btn_col.setter("height"))

            def _make_handler(dev_id, dev_name):
                def _handler(*_):
                    self.selected_device_id = dev_id
                    short = (dev_name[:24] + "...") if len(dev_name) > 24 else dev_name
                    self.device_button.text = tr("device_selected", name=short)
                    if _popup_ref[0]:
                        _popup_ref[0].dismiss()
                return _handler

            for dev in devices:
                dev_type = dev.get("type", "")
                label_text = f"{dev['name']}  ({dev_type})" if dev_type else dev["name"]
                if dev.get("is_active", False):
                    label_text += tr("device_playing_suffix")
                btn = RoundedButton(
                    text=label_text,
                    fill=self.fg,
                    text_color=body_color,
                    size_hint_y=None,
                    height=dp(50),
                    font_size=sp(14),
                    font_name=SYMBOL_FONT,
                )
                btn.bind(on_release=_make_handler(dev["id"], dev["name"]))
                btn_col.add_widget(btn)

            sv.add_widget(btn_col)
            content.add_widget(sv)

        # WiFi/Cast speakers can't be listed by the Spotify Web API until they're
        # active, so explain the workaround at the bottom of the popup.
        content.add_widget(
            Label(
                text=tr("wifi_hint"),
                halign="center",
                valign="middle",
                color=(1, 1, 1, 0.55),
                font_size=sp(11),
                size_hint_y=None,
                height=dp(40),
            )
        )
        content.children[0].bind(
            size=lambda w, *_: setattr(w, "text_size", w.size)
        )

        popup = Popup(
            title=tr("select_device_title"),
            content=content,
            size_hint=(0.88, min(0.75, 0.32 + 0.11 * max(len(devices), 1))),
            background_color=(*get_rgba(DEFAULT_BG)[:3], 1),
        )
        _popup_ref[0] = popup
        popup.open()

    # ----- next song -------------------------------------------------------- #

    def next_song(self):
        if not self._debounce():
            return
        if self.current_track in self.total_tracks:
            self.total_tracks.remove(self.current_track)
        self.next_button.disabled = True
        self._hide_album()
        self.play_random_song()


# --------------------------------------------------------------------------- #
# Small colour utilities (kept after class defs for readability)
# --------------------------------------------------------------------------- #
def get_color255(hex_str):
    hex_str = hex_str.lstrip("#")
    return tuple(int(hex_str[i : i + 2], 16) for i in (0, 2, 4))


def get_rgba(hex_str):
    r, g, b = get_color255(hex_str)
    return (r / 255, g / 255, b / 255, 1)


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
class EndevinamApp(App):
    def build(self):
        self.title = "Endevina'm"
        # Brand window/taskbar icon (Kivy shows its own logo otherwise).
        if os.path.exists(ICON_APP):
            self.icon = ICON_APP
        self.sp = None
        # Pick the UI language before building any screen so initial text is
        # correct: a saved preference wins, else the detected system language,
        # else English.
        set_language(self.load_language() or detect_system_language())
        # The auth manager is built lazily once a Client ID is available, either
        # from a previous run / the environment, or from the auth screen.
        # _auth_client_id tracks which Client ID the current manager was built
        # for, so start_auth can reuse it (stable PKCE verifier) instead of
        # rebuilding on every tap.
        self.auth_manager = None
        self._auth_client_id = None
        self.client_id = self.load_client_id()
        if self.client_id:
            self.build_auth_manager(self.client_id)

        # When the soft keyboard opens (Android), pan the window so the focused
        # field stays just above it - otherwise the bottom text inputs sit
        # hidden behind the keyboard and can't be seen, edited, or pasted into.
        Window.softinput_mode = "below_target"

        self.sm = ScreenManager(transition=FadeTransition())
        self.game_screen = GameScreen(self, name="game")
        self.auth_screen = AuthScreen(self, name="auth")
        self.sm.add_widget(self.auth_screen)
        self.sm.add_widget(self.game_screen)

        # Right-click (desktop) / long-press (touch) advances the game.
        self._lp_event = None
        self._lp_touch = None
        self._lp_start = (0, 0)
        Window.bind(
            on_touch_down=self._on_touch_down,
            on_touch_move=self._on_touch_move,
            on_touch_up=self._on_touch_up,
        )

        # Skip auth only if we have a Client ID and a valid cached token.
        if self.auth_manager and self.auth_manager.validate_token(
            self.auth_manager.get_cached_token()
        ):
            self.build_spotify_client()
            self.sm.current = "game"
        else:
            self.sm.current = "auth"
        return self.sm

    # ----- right-click / long-press gesture -------------------------------- #
    @staticmethod
    def _over(touch, widget):
        """True if the window-space touch falls inside an on-screen widget."""
        if not widget or not widget.parent:
            return False
        lx, ly = widget.to_widget(touch.x, touch.y)
        return 0 <= lx <= widget.width and 0 <= ly <= widget.height

    def _interactive_hit(self, touch):
        gs = self.game_screen
        return any(
            self._over(touch, w)
            for w in (gs.entry, gs.entry_buttons, gs.device_button, gs.bottom_bar)
        )

    def _on_touch_down(self, _window, touch):
        if self.sm.current != "game":
            return False
        is_mouse = "button" in touch.profile
        # Right mouse button -> active action, unless over the text box / buttons
        # (so the text box keeps its copy/paste menu and buttons work normally).
        if is_mouse and touch.button == "right":
            if self._interactive_hit(touch):
                return False
            self.game_screen.trigger_active_action()
            return True
        # Finger / left button: arm a long-press timer over empty areas only.
        if not is_mouse or touch.button == "left":
            if self._interactive_hit(touch):
                return False
            self._cancel_long_press()
            self._lp_touch = touch
            self._lp_start = (touch.x, touch.y)
            self._lp_event = Clock.schedule_once(
                lambda _dt: self._fire_long_press(touch), 0.5
            )
        return False

    def _on_touch_move(self, _window, touch):
        if self._lp_touch is touch:
            dx = abs(touch.x - self._lp_start[0])
            dy = abs(touch.y - self._lp_start[1])
            if dx + dy > dp(20):  # finger dragged -> not a long press
                self._cancel_long_press()
        return False

    def _on_touch_up(self, _window, _touch):
        self._cancel_long_press()
        return False

    def _cancel_long_press(self):
        if self._lp_event is not None:
            self._lp_event.cancel()
            self._lp_event = None
        self._lp_touch = None

    def _fire_long_press(self, touch):
        if self._lp_touch is touch and self.sm.current == "game":
            self.game_screen.trigger_active_action()
        self._cancel_long_press()

    def _cache_path(self):
        # Per-user writable dir on every platform. Never bundle this with the
        # app: each user authenticates themselves, so the token must not ship.
        return os.path.join(self.user_data_dir, ".spotify_cache")

    def _client_id_path(self):
        # The Client ID is not secret, but it is per-user, so it lives next to
        # the token cache in the per-user data directory rather than in the app.
        return os.path.join(self.user_data_dir, "client_id")

    def load_client_id(self):
        """Return a valid saved/env Client ID, or None.

        A stored value that fails validation (e.g. a previously saved doubled id)
        is ignored, so the app self-heals to a clean auth screen instead of
        re-using a broken id.
        """
        candidate = os.environ.get("SPOTIFY_CLIENT_ID")
        if not candidate:
            try:
                with open(self._client_id_path(), encoding="utf-8") as fh:
                    candidate = fh.read()
            except OSError:
                candidate = None
        candidate = "".join(candidate.split()) if candidate else ""
        return candidate if CLIENT_ID_RE.match(candidate) else None

    def save_client_id(self, client_id):
        """Persist the Client ID to the per-user data directory."""
        self.client_id = client_id.strip()
        try:
            with open(self._client_id_path(), "w", encoding="utf-8") as fh:
                fh.write(self.client_id)
        except OSError as exc:  # noqa: BLE001 - non-fatal, just won't be remembered
            print("could not save client id:", exc)

    def _lang_path(self):
        return os.path.join(self.user_data_dir, "language")

    def load_language(self):
        """Return the saved language code, or None if the user hasn't chosen one."""
        try:
            with open(self._lang_path(), encoding="utf-8") as fh:
                code = fh.read().strip()
                return code if code in TRANSLATIONS else None
        except OSError:
            return None

    def save_language(self, code):
        try:
            with open(self._lang_path(), "w", encoding="utf-8") as fh:
                fh.write(code)
        except OSError as exc:  # noqa: BLE001 - non-fatal, just won't be remembered
            print("could not save language:", exc)

    def set_language_and_refresh(self, code):
        """Switch language, persist the choice, and retranslate both screens."""
        set_language(code)
        self.save_language(_LANG)
        self.auth_screen.apply_language()
        self.game_screen.apply_language()

    def build_auth_manager(self, client_id):
        """Build the PKCE auth manager for a given Client ID (no secret needed)."""
        self.auth_manager = SpotifyPKCE(
            client_id=client_id,
            redirect_uri=REDIRECT_URI,
            scope=SCOPE,
            cache_handler=CacheFileHandler(cache_path=self._cache_path()),
            open_browser=False,
        )
        self._auth_client_id = client_id

    def build_spotify_client(self):
        self.sp = spotipy.Spotify(auth_manager=self.auth_manager)

    def go_to_game(self):
        self.sm.current = "game"


if __name__ == "__main__":
    if platform != "android":
        Window.size = (420, 700)  # phone-like aspect for desktop preview
    EndevinamApp().run()
