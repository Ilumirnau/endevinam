# Endevina'm - a personal-use, Hitster-style music guessing game for Spotify.
# Copyright (C) 2026  Ilumirnau
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. It is distributed WITHOUT ANY WARRANTY; see the GNU General Public
# License for details: <https://www.gnu.org/licenses/>.
"""Entrypoint for buildozer/Android (expects a module named main).

Desktop users can run either ``python main.py`` or ``python endevinam.py``.
"""
from endevinam import EndevinamApp

if __name__ == "__main__":
    EndevinamApp().run()
