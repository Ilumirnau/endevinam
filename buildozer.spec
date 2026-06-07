[app]

# (str) Title of your application
title = Endevina’m

# (str) Package name
package.name = endevinam

# (str) Package domain (needed for android/ios packaging)
package.domain = com.arnaulira

# (str) Source code where the main.py lives
source.dir = .
source.exclude_dirs = dist,bin,.buildozer

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,kv,atlas,ico

# (str) Application versioning
version = 1.0

# (list) Application requirements
# spotipy pulls in requests/urllib3; certifi is needed for HTTPS on Android.
requirements = python3,kivy,pillow,spotipy,requests,urllib3,certifi,charset-normalizer,idna

# (str) Presplash / icon of the application
icon.filename = %(source.dir)s/media/endevinam_icon.png
# presplash.png is a padded portrait canvas (logo centred at ~70% width on the
# #191414 background) so the splash logo doesn't span the full screen width.
presplash.filename = %(source.dir)s/media/presplash.png

# (str) Background colour behind the (scaled) presplash logo. Matches the app's
# near-black theme so there is no white flash while the logo is shown.
android.presplash_color = #191414

# (str) Supported orientation (portrait suits the phone-style layout)
orientation = portrait

# (list) Permissions - the game talks to the Spotify Web API over the network.
android.permissions = INTERNET,ACCESS_NETWORK_STATE

# (int) Target / minimum Android API
android.api = 36
android.minapi = 24

# (list) The Android archs to build for
android.archs = arm64-v8a

# (bool) Indicate whether the screen should stay on
android.wakelock = True

# (bool) Auto-accept the Android SDK licenses so the build doesn't block waiting
# for interactive confirmation.
android.accept_sdk_license = True

# (str) Filename to the hook for p4a (not needed by default)
# p4a.hook =

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1

# --------------------------------------------------------------------------- #
# Build notes
# --------------------------------------------------------------------------- #
# The entrypoint is main.py, which imports EndevinamApp from endevinam.py.
#
# Android build MUST run on Linux / WSL / macOS (buildozer does not run on
# native Windows). From this folder:
#     pip install buildozer cython
#     buildozer -v android debug
# Output: bin/endevinam-1.0-debug.apk  ->  copy to a phone and install.
#
# Auth uses PKCE, so no client secret is shipped. On first launch the user
# pastes their own Spotify Client ID, taps "Connect to Spotify", authorises in
# the browser, and pastes the redirected URL back into the app.
