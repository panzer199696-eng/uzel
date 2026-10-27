# UZEL — Android client

*Русский → [README.md](README.md)*

Our own phone client: it receives audio from the pendant over BLE and sends it
to the UZEL core over HTTP. It works with a pendant running UZEL firmware and
with Omi pendants. It replaces the Omi app without a cloud: `:core` contains
nothing but the Kotlin stdlib, `:app` uses system APIs, Compose and the Nordic
DFU library for updating the pendant firmware.

The app is in English and Russian and follows the phone language; on
Android 13+ you can choose a language just for UZEL (Settings → "Language").

Change history: [CHANGELOG.md](CHANGELOG.md) (Russian).

## Install a ready APK

1. Download `uzel-android-<version>.apk` from the repository **Releases**.
2. Allow installs from this source and install the APK.
3. Grant the permissions: Bluetooth (scan and connect), notifications and, on
   Android 17, local network access. Without the last one the core on your
   home network is unreachable.
4. Under "Settings", enter the core address (`http://<PC-IP>:8770`) and the
   token if the core listens beyond localhost (see
   [docs/en/07-install.md](../../docs/en/07-install.md), section "Settings").
5. Turn the bridge on. The first pairing with a pendant running UZEL firmware
   works only while the pendant is connected to USB (or not paired with anyone
   yet).

⚠️ **The build in Releases is signed with a debug key.** It is not an app
store key and says nothing about where the file came from. Install the APK
only from this repository's Releases. If you need your own chain of trust,
build the APK yourself. A debug APK will not update over an APK signed with a
different key: uninstall the old one first (the address, token and unsent
audio are erased).

## Build it yourself

You need JDK 21 and the Android SDK (compileSdk/targetSdk 37). AGP 9.4.1 and
Gradle 9.8.2 come through the project wrapper.

1. Create `bridge/android/local.properties` with the SDK path:
   `sdk.dir=D\:\\Android\\Sdk` (Windows) or `sdk.dir=/home/<name>/Android/Sdk`.
2. Build and run the tests:

```
cd bridge/android
gradlew.bat :core:test :app:assembleDebug        # Windows (JAVA_HOME → JDK 21)
./gradlew :core:test :app:assembleDebug          # Linux / macOS
```

The APK appears in `app/build/outputs/apk/debug/app-debug.apk`. If the project
path on Windows contains non-ASCII characters, the build folders move to a
temporary folder by themselves (`<TEMP>\uzel-android-build\{core,app}`),
otherwise the test classpath breaks. Gradle prints the final APK path.

3. Install it on a phone with USB debugging enabled:
   `adb install -r <path to app-debug.apk>`.

## Languages

Strings live in resources: `app/src/main/res/values/strings.xml` is English
(the Android default), `values-ru/strings.xml` is Russian. To add a language,
copy `values/strings.xml` to `values-<code>/strings.xml`, translate the values
(keep the names and the `%1$s`/`%1$d` arguments) and add the code to
`res/xml/locales_config.xml`. `StringsParityTest` in `:core:test` checks that
every translation has the same names and arguments as English.

## What is inside

| Module | Contents |
|---|---|
| `:core` | Pure Kotlin/JVM, not a single `android.*` import: packet parsing, loss counter, frame buffer, disk spool, HTTP client, uploader |
| `:app` | BLE client, foreground service, Compose UI, quick settings tiles, pendant firmware update, autostart after reboot |

All logic that can be checked without a phone lives in `:core` and is covered
by unit tests. `:app` holds only what does not work without a phone.

## What is verified and what is not

**Verified:**
- `:core:test`: unit tests on a plain JVM (protocol, buffer, spool, uploading
  to a real HTTP server from the JDK, string resource parity);
- on a live pendant and an OPPO Find N5 phone (ColorOS): BLE audio without
  losses, pairing with UZEL firmware, over-the-air pendant firmware update,
  service survival on ColorOS.

**Not verified:** other manufacturers and their background service "killers",
other Android versions (minSdk 26), phone battery life over a full day.

## Screens and survival on ColorOS

Sections: **Pendant** (live state, battery, losses, core privacy), **Day**
(conversations, tasks, facts from `/ui/api/day`), **Notes** (your own notes
and *moment marks* — a timestamped note that gets linked to the conversation
recorded at that minute), **Search** across the core memory. Quick settings
tiles: "UZEL" (bridge on/off) and "Mark"; a "Mark the moment" shortcut on the
icon.

**Why the bridge "died on close".** ColorOS (Athena, `SwipeUpClearAction`)
kills the whole package with signal 9 when its card is swiped away from
Recents, wipes its alarms and forbids the system from restarting the service
(`OplusAppStartupManager: prevent restart service`). Neither START_STICKY nor
the battery whitelist nor "Allow background activity" helps. The fix: the
window leaves no card in Recents (`excludeFromRecents` +
`finishAndRemoveTask()` in `onStop`), there is nothing to swipe away; the
service lives on — verified by swiping and "Close". The watchdog (`Watchdog`,
an alarm every 5 min) restarts the service after other kills, `BootReceiver`
after a reboot or an update.

**The notification** is mandatory for the service, but its channel has
minimal importance: ColorOS does not show it in the shade or the status bar.

**Android 17 (targetSdk 37)** requires `ACCESS_LOCAL_NETWORK`, otherwise
requests to the core on the home network silently fail and audio piles up in
the spool. The permission is in the same group as Bluetooth and is granted
together with it.

## Why Opus, not PCM

The pendant encodes audio itself; the Bluetooth channel is narrow, and Opus
16 kHz at 24 kbit/s takes 2.8 KB/s versus 32 KB/s of raw PCM — eleven times
less. The phone receives ready Opus frames and forwards them to the core as is
(the request body is length-prefixed frames, format `opus-frames/1`); the core
worker decodes them, not the phone. This saves both battery and traffic.

## If the core answers 204

This is **not an error**. It is how the core says "recording is muted by
privacy": the data is not needed, the chunk is dropped and does not go into
the buffer. Once the mute is lifted, new chunks are accepted again. Only an
unreachable core is an error — then chunks pile up on disk (limit 256 MB,
after that the oldest are removed) and are sent once the connection is back,
in their original order.
