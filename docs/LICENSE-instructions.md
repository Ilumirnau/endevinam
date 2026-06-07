# How the GPLv3 licence is applied

Endevina'm is licensed under the **GNU General Public License v3.0**.

## 1. The LICENSE file

The repo root contains `LICENSE` — the canonical, verbatim GPLv3 text from
<https://www.gnu.org/licenses/gpl-3.0.txt>. It is the authoritative licence and must not be
retyped or paraphrased.

## 2. Per-source-file header

Each source file (`endevinam.py`, `main.py`) carries the FSF's standard header:

```
Endevina'm — a personal-use, Hitster-style music guessing game for Spotify.
Copyright (C) 2026  Ilumirnau

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
```

## 3. About-screen notice

A short notice for the app's About screen (see `docs/in-app-copy.md`):

```
Endevina'm  Copyright (C) 2026  Ilumirnau
This program comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it
under the terms of the GNU GPL v3. See the LICENSE file.
```

## Note on dependencies

GPLv3 is compatible with Kivy (MIT) and the other Python libraries used here (spotipy,
Pillow, requests). If you add a dependency under an incompatible licence, check
compatibility before bundling it into distributed binaries. GPLv3's copyleft means anyone
distributing a modified Endevina'm must also release their source under GPLv3 — which is the
point: it keeps the project free.
