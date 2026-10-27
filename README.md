# UZEL — a local AI pendant

[![Русский](https://img.shields.io/badge/язык-Русский-red)](README.ru.md) · **Русский → [README.ru.md](README.ru.md)**

> *Uzel* is Russian for "knot": a knot to remember by · a network node · a structural joint.

A wearable voice-recorder companion. The pendant on your chest records audio,
your PC transcribes it, pulls facts and tasks out of conversations, puts notes
into Obsidian and gives the memory to any agent over MCP. **Audio and its
analysis never leave your computer.** The code does contain cloud backends,
but they are off by default and each one is explicitly marked as cloud.

It is an answer to pendants like Omi: the same idea, but without a
subscription and without someone else's server, in English and Russian (any
other language is added with a translation file). The project's principles
are in **[MANIFESTO.en.md](MANIFESTO.en.md)** ([Русский](MANIFESTO.md)).

## Status (27.10.2026)

| Part | Version | State |
|---|---|---|
| Pendant firmware | 0.1.2 | Our own, grown from Omi DK2 2.0.10. Clean audio without hum, encrypted link, over-the-air updates verified on the pendant from the PC and from the phone — [firmware/](firmware/README.en.md) |
| Case | v2.4 | Computed and checked on the PC (closed meshes, fits, clip travel), exported for printing. **Not printed yet** — [hardware/case-v2/](hardware/case-v2/HANDOFF.md) |
| Android client | 0.3.1 | Our own, in Kotlin: audio from the pendant, spool, day screen, notes, pendant firmware update — [bridge/android/](bridge/android/README.en.md) |
| PC core | 0.9.0 | Python: ingest, VAD, transcription, analysis, storage, export, tray window, MCP. Over 900 pytest tests, no network needed |

## What it does

| | |
|---|---|
| **Audio ingest** | the pendant over BLE via the phone or directly via the PC, `POST /ingest` (PCM) and `/ingest/opus`, Omi app webhooks |
| **Segmentation** | Silero VAD → utterances → conversations by pauses |
| **Transcription** | faster-whisper `large-v3` on your own graphics card (`int8_float16`, ~2 GB of video memory), language detected automatically or fixed |
| **Analysis** | Ollama on the same PC (`qwen3:8b` by default): summary, facts with timestamps, tasks with a supporting quote — in the interface language |
| **Quality filter** | eight rules against junk and hallucinations, `uzel.cmd quality-report` |
| **Storage** | SQLite + Qdrant (embedded mode) for semantic search |
| **Export** | a day note in Obsidian, tasks into an inbox file, a daily report |
| **Privacy** | manual mute, pause, quiet windows; muted audio is discarded **before** it is written to disk |
| **PC window** | `/ui` + window + tray icon + autostart, English or Russian |
| **MCP** | seven read-only tools: search, conversations, day, facts, tasks |

The pipeline is split into two passes: a cheap permanent one (queue → VAD →
utterances, the GPU is untouched) and a night batch (transcription →
conversations → analysis → export). The reason is simple: 6 GB of video
memory cannot hold whisper and the analysis model at the same time.

## What is not verified

Read this section before relying on the project.

- **Recognition quality (WER) is not measured.** The tooling is ready
  (`uzel.cmd wer`, [tests/wer/](tests/wer/README.md)), but there is no
  reference recording with a verbatim transcript. English recognition has not
  been tried on real conversations at all.
- **Transcription speed is uncertain.** Two measurements on the same hardware
  differed eightfold (RTF 0.091 and 0.78), the cause was not found. Consider
  the speed margin unknown.
- **Pendant battery life is not measured** with an ammeter. Goals like "twice
  as long as Omi" are not confirmed yet.
- **Case v2.4 is not printed** and not fitted on a board.
- **Android is verified on one phone** (OPPO Find N5, ColorOS). On other
  firmwares the system may kill the background service.
- **A full day of wearing as a formal test with a report has not been done.**
  The author wears the pendant in real conversations since October 2026, but
  that is not a measurement.
- **The quality filter is not calibrated**: `UZEL_QUALITY_ENFORCE=false`, the
  thresholds are still hypotheses.
- **The PC part is verified only on Windows 11.** There is no iOS client.
- **Over-the-air firmware updates are not signed** (Legacy DFU, only the CRC is
  checked). The only protection is that the bootloader can be entered only
  over an encrypted link from a paired device.
- From the [architecture](docs/01-архитектура.md) plan, the following are
  **not done yet**: IMU gestures, an hours-long flash buffer (the pendant now
  keeps 2 s of audio when the link drops), diarization, webhooks, Docker.
- Most design documents in `docs/` are in Russian; the install guide, the
  pendant build guide, the Android and firmware READMEs have English versions.

## Recording conversations and the law

The pendant hears more than just you. When you record, you are responsible
for other people's words too.

- **Tell the people you talk to** that you are recording. The LED on the
  pendant does not replace words.
- **Follow the laws of your country** on recording conversations, privacy and
  personal data. Many countries require the consent of all participants.
  Other people's conversations and voices are their personal data.
- Turn recording off where nobody expects it: at the doctor's, in
  negotiations, at someone's home. That is what the "Private" button, pause and
  scheduled quiet windows are for.
- If you enable cloud backends, you are sending other people's conversations
  to a third party.

The project is provided "as is" (see [LICENSE](LICENSE)). How you use the
device is up to you.

## Installing the PC part

Step by step, from an empty folder to a working core:
**[docs/en/07-install.md](docs/en/07-install.md)**. In short (Windows,
Python 3.12):

```powershell
git clone https://github.com/panzer199696-eng/uzel.git uzel
cd uzel
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -e . --no-deps
mkdir %USERPROFILE%\uzel-data
copy .env.example %USERPROFILE%\uzel-data\.env
.venv\Scripts\python.exe -m pytest -q
uzel.cmd serve              # http://127.0.0.1:8770/ui
```

## Building the pendant

The parts list, wiring, soldering, case printing, bootloader and firmware are
described in **[docs/en/09-build-the-pendant.md](docs/en/09-build-the-pendant.md)**.
An Omi pendant you already own works too: the bridge works with it as is.

## Ready builds

They are in the repository **Releases**:
- firmware: `uzel-fw-<version>.uf2` (by cable), `.hex` (over the air from the
  PC), `-dfu.zip` (over the air from the phone);
- Android client: an APK **signed with a debug key**. Details in
  [bridge/android/README.en.md](bridge/android/README.en.md);
- case: `uzel_case_v<version>_plate.3mf`, if published. Otherwise the script in
  the repository builds it.

## Languages

The interface and output are in English or Russian: the `/ui` window, tray,
CLI, MCP answers, Obsidian notes — and **summaries, facts and tasks are
written in the interface language** even if the conversation was in another
one. The transcript stays verbatim in the original language.

| What | How to switch |
|---|---|
| interface language | the switcher in the `/ui` window header (stored in `UZEL_DATA_DIR\language.txt`) or `UZEL_LANG=en` / `ru` / `auto` in `.env`; `auto` is the system language if translated, otherwise English |
| recognition language | `UZEL_STT_LANGUAGE=auto` (whisper detects it) or a whisper code: `en`, `ru`, … If you speak one language, set it explicitly |
| Android client | follows the phone language; on Android 13+ also Settings → Language in the app |

In `auto` recognition mode short utterances (under 4 s) borrow the language of
confidently detected long ones from the same batch: on 1–3 s clips whisper
often mistakes the language.

**Adding a language** (German, code `de`, as an example):
1. Copy `core/uzel/locales/en.json` to `core/uzel/locales/de.json` and
   translate the values. Do not touch the keys or the `{…}` parameters. The
   same file holds the analysis prompts (`nlu.prompt.*`: keep the JSON schema,
   "Write in German"), weekday names, "empty" words and deadline cues for the
   analysis, whisper hallucination stop phrases (`stt.hallucination.*`) and the
   Obsidian folder names. `pytest tests/test_i18n.py` checks that keys and
   parameters match.
2. Android: copy `bridge/android/app/src/main/res/values/strings.xml` to
   `values-de/strings.xml`, translate it, add `de` to
   `res/xml/locales_config.xml`. `gradlew :core:test` checks the parity.
3. Plural forms: an object with `one/few/many/other` in the catalogue; the
   rules for `ru`, `uk`, `be` and "one/other" for the rest are in
   `core/uzel/i18n.py`.

## How it differs from Omi

An honest comparison: on the left what Omi does today, on the right what
**works** in UZEL, not what is planned.

| | Omi | UZEL |
|---|---|---|
| Where audio is processed | cloud | your PC, entirely |
| Where conversations are analysed | cloud | Ollama on your PC |
| Price | subscription | 0, your own hardware |
| Mute | in the app | the core discards audio before writing it to disk |
| Where knowledge goes | their app | Obsidian + SQLite + MCP for any agent |
| Other hardware | — | works with an Omi pendant you already own |
| Recognition quality | not stated | whisper `large-v3`, **WER not measured** |
| Maturity | a product | a working prototype by one author |

Omi is more mature, and thousands of people wear it. UZEL's advantage is that
the recording stays at home.

## Layout

```
core/uzel/           core: ingest → VAD → segmentation → STT → NLU → storage → export
  stt/  nlu.py       transcription and analysis, backend router with a fuse
  worker.py          the two pipeline passes
  quality.py         filter B1–B8
  i18n.py locales/   localisation: catalogues ru.json, en.json
  ui/  desktop.py    page, window, tray
  omi/               Omi app webhooks
bridge/uzel_bridge/  BLE bridge on the PC (bleak): Omi protocol, pairing, DFU
bridge/android/      Android client: :core (pure Kotlin) + :app (BLE, service, UI)
firmware/            pendant firmware (Zephyr / nRF Connect SDK 2.7)
hardware/case-v2/    case: 3MF build scripts, original Omi Triangle v1 models
integrations/mcp/    MCP server, stdio, no third-party dependencies
tests/               pytest; no network needed
tools/               helper tools (public repository export)
```

Data (database, audio, `.env`) is kept **outside the repository**, in
`UZEL_DATA_DIR`.

## Documents

- [Manifesto](MANIFESTO.en.md)
- [Install from scratch](docs/en/07-install.md)
- [Build the pendant](docs/en/09-build-the-pendant.md)
- [Android client](bridge/android/README.en.md) · [Firmware](firmware/README.en.md)
- In Russian only for now: [first day with the pendant](docs/08-первый-боевой-день.md),
  [concept](docs/00-концепция.md), [architecture](docs/01-архитектура.md),
  [roadmap](docs/02-дорожная-карта.md), [risks](docs/03-риски-и-решения.md),
  [Omi teardown](docs/04-разбор-omi.md),
  [Omi app compatibility](docs/06-совместимость-с-приложением-omi.md)

## License

MIT — [LICENSE](LICENSE). Third-party components (Omi firmware, Opus, battery
driver, Silero VAD, Omi Triangle v1 case) and their licenses are listed in
[NOTICE](NOTICE).
