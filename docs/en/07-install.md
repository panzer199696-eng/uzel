# Installing from scratch

*Русский → [../07-установка.md](../07-установка.md)*

The guide is checked by an automated acceptance run (the author's helper
script): empty folder → clone → environment → all tests green → live core →
audio ingest → utterance in the database → MCP answers. Everything on
Windows 11, Python 3.12.

It goes step by step. **Steps 1–5 give you a working core** and are enough to
check the installation. Steps 6–11 switch on what it is all for:
transcription on the graphics card, local analysis, the pendant, the phone,
the PC window.

---

## 0. What you need

| | Need | Why | Without it |
|---|---|---|---|
| required | Python **3.12** | the whole core | no way |
| required | git | cloning | download an archive instead |
| required | ~1 GB of disk | environment and dependencies | no way |
| for speed | NVIDIA graphics card + driver | transcription on the GPU | the CPU does it, many times slower |
| for analysis | [Ollama](https://ollama.com) | facts and tasks from conversations | utterances are there, conclusions are not |
| for the pendant | Bluetooth LE on the PC | the `bridge/` | audio only from the Omi app or the phone |
| for notes | Obsidian | day notes export | notes are plain files, readable with anything |

Disk space: environment ~450 MB, CUDA wheels +2.0 GB, whisper `large-v3`
~3 GB, analysis model 4.7–6.6 GB, search model 255 MB. **The full set is
about 10.5–12 GB.**

---

## 1. Clone

```powershell
git clone https://github.com/panzer199696-eng/uzel.git uzel
cd uzel
```

The repository is small (about 12 MB): the VAD model
(`core/uzel/models/silero_vad.onnx`) is inside, the other models are
downloaded separately and are not stored in git.

## 2. Environment

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install -e . --no-deps
```

About a minute with a warm pip cache, up to ten with an empty one.

The last line installs UZEL itself in development (editable) mode: after it
`uzel`, `uzel-bridge` and `uzel-mcp` are in `.venv\Scripts` and work without
`PYTHONPATH`. Install only with `-e`: the core looks for the VAD model next to
the sources, a regular install would move the code into `site-packages`
without it.

## 3. Settings

```powershell
mkdir %USERPROFILE%\uzel-data
copy .env.example %USERPROFILE%\uzel-data\.env
notepad %USERPROFILE%\uzel-data\.env
```

> ⚠️ **`.env` lives next to the data, not in the project folder.** It holds
> keys, and `.gitignore` hides the file only from git, not from a cloud backup.
> Another location can be set with the `UZEL_ENV_FILE` variable. An old `.env`
> in the project folder is still read for compatibility, but it is weaker than
> the new one and logs a warning.

Defaults: data in `%USERPROFILE%\uzel-data`, notes in the Obsidian notes folder
`%USERPROFILE%\Documents\Obsidian`. If yours differ, uncomment and adjust:

```ini
UZEL_DATA_DIR=D:\uzel-data
UZEL_VAULT_DIR=D:\Notes
UZEL_USER_ROLE=site engineer   # who wears the pendant — a hint for the analysis model
```

**Language.** `UZEL_LANG=auto` (the default) uses the system language if it is
translated (English and Russian are shipped), otherwise English. It covers the
window, tray, CLI, Obsidian notes and the language of summaries, facts and
tasks. The language picked in the window (`/ui`, top right) overrides it.
`UZEL_STT_LANGUAGE=auto` lets whisper detect the spoken language; if you
always speak one language, set it explicitly (`UZEL_STT_LANGUAGE=en`): whisper
often mistakes the language of 1–3 second clips. In `auto` mode UZEL lends
short utterances the language of confidently detected long ones from the same
batch.

> ⚠️ Keep `UZEL_DATA_DIR` **outside the project folder and outside any cloud
> backup**. Raw audio, the database of every utterance and the search index
> are written there. A project on a Desktop that syncs to a cloud drive is a
> ready-made leak.

> ⚠️ **An environment variable beats `.env`.** If the core insists on using a
> foreign folder while `.env` is correct, check `echo %UZEL_DATA_DIR%`: an
> inherited variable once silently pointed a fresh installation at a
> production database.

> ⚠️ **Does the phone need access from outside?** Then set `UZEL_HOST=0.0.0.0`
> and **always** `UZEL_API_TOKEN=secret` in the same `.env`: without a token the
> core refuses to start on a non-loopback address, and with a token everything
> except `/health` answers 401 without it. The token is passed as a
> `?token=secret` parameter or an `Authorization: Bearer secret` header.

## 4. Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests green, a few skipped. No network needed. Skipped are the
integration tests (enabled with `UZEL_RUN_INTEGRATION=1`) and live semantic
search checks — until the embedding model is downloaded into
`UZEL_FASTEMBED_CACHE` (default `%USERPROFILE%\models-fastembed`).

## 5. First run

```powershell
uzel.cmd serve
```

`uzel.cmd` is the CLI launcher: it takes python from `.venv` and works from
any folder without activating the environment. It is the same as
`.venv\Scripts\uzel.exe serve` or `.venv\Scripts\python.exe -m uzel.cli serve`
— after step 2 the package is installed and `PYTHONPATH` is not needed.

Check it from another window:

```powershell
curl http://127.0.0.1:8770/health
start http://127.0.0.1:8770/ui
```

`/health` returns only liveness and the version — it is open without a token,
the bridge and the phone poll it. The version, queue size and privacy state
are in the `/ui` window: a page with the status, the day feed, search, the
"Pause for 30 minutes" and "Turn recording off" buttons and a language
switcher. All assets are local, the page never goes to the internet.

**At this point the installation is done.** Next comes what makes it useful.

---

## 6. Transcription on the graphics card

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-cuda.txt
```

Two gigabytes of `nvidia-*` wheels. `ctranslate2` does not declare them as a
dependency, but with `device="cuda"` it looks for `cublas64_12.dll` and
`cudnn*.dll` **on the process PATH** — `os.add_dll_directory()` does not help,
this was checked. The code adds `site-packages/nvidia/*/bin` to PATH itself
(`core/uzel/stt/local_backend.py`), but there is nothing to add without the
wheels. Without them everything works — on the CPU.

The speech model downloads itself on first use (~3 GB). To put it in place in
advance and not depend on the network, download it into
`UZEL_LOCAL_MODEL_DIR\large-v3` — the existence of that folder cancels the
download.

Measured on a GTX 1660 Ti (6 GB): `large-v3` with `int8_float16` takes
**1963 MiB**. `float16` is twice as slow and loses text, `int8` differs by 3 %.

## 7. Local conversation analysis

Install [Ollama](https://ollama.com), then:

```powershell
ollama pull qwen3:8b
uzel.cmd llm-check
```

`llm-check` says plainly which model analyses conversations and whether
anything goes to the cloud. Expected:

```
analysis model: ollama (local), qwen3:8b — ready
cloud on the default path: forbidden (allow_cloud=False, no cloud backends in settings)
```

The line is built from the actual state: enable Groq or the agent gateway
(`gateway`) in the settings and it lists them; allow the cloud for the
pipeline and it says "ALLOWED". A smaller model also works:
`ollama pull qwen2.5:7b-instruct-q4_K_M` and
`UZEL_OLLAMA_MODEL=qwen2.5:7b-instruct-q4_K_M`.

> ⚠️ **6 GB of video memory cannot hold whisper and the analysis model at the
> same time**: 1963 + 4140 = 6103 MiB out of 6144. That is why the night batch
> runs in two passes — all transcription, unload whisper, all analysis. This
> is measured, not overcaution.

> ⚠️ Ollama keeps the model in video memory for `keep_alive` minutes after an
> answer. While it is there, whisper does not load onto the card and the
> process silently waits.

## 8. Pipeline

Two passes, separated on purpose.

```powershell
uzel.cmd worker ingest --loop     # cheap: queue -> VAD -> utterances, no GPU
uzel.cmd worker batch             # night: transcription -> conversations -> analysis -> export
uzel.cmd worker recover           # return to the queue what broke off halfway
```

The night batch stops on the `<UZEL_DATA_DIR>\worker.stop` file and on a time
limit (`UZEL_WORKER_TIME_LIMIT_S`, one hour by default) — both are checked
between batches, not after all the work.

## 9. Audio sources

**Pendant via the PC** (Bluetooth LE needed):

```powershell
.venv\Scripts\uzel-bridge.exe --scan
.venv\Scripts\uzel-bridge.exe --address <MAC> --uid pendant-1
```

While the pendant is connected to a phone, the PC **cannot see it at all**: an
active BLE connection stops advertising. Disconnect it from the phone first.

**The Omi app without our own APK** — Developer Mode sends webhooks to our
core; addresses and caveats are in
[06-совместимость-с-приложением-omi.md](../06-совместимость-с-приложением-omi.md)
(Russian).

**Our own Android client** — `bridge/android/`, built with Gradle, see
[bridge/android/README.en.md](../../bridge/android/README.en.md). A ready APK
is in Releases. The core token is entered under "Settings": if the core
listens beyond localhost, everything except `/health` answers 401 without it.

## 10. PC window and autostart

```powershell
uzel.cmd app                      # core + window + tray icon
uzel.cmd autostart install        # a shortcut in shell:startup, runs via pythonw
uzel.cmd autostart status
```

## 11. MCP server for Claude Code

```powershell
claude mcp add uzel --scope local -- <path>\.venv\Scripts\python.exe -m uzel_mcp
claude mcp list
```

`PYTHONPATH` is not needed (the package was installed in step 2);
`UZEL_DATA_DIR` must be visible to the server process if the data folder is
not the default one. Seven tools, all read-only: `search`, `conversation`,
`day`, `facts`, `tasks`, `stats`, `privacy`. `conversation` returns verbatim
utterances only with an explicit `with_utterances=true`. Tool descriptions and
answers follow the interface language.

---

## If it does not work

| Symptom | Cause | What to do |
|---|---|---|
| `ModuleNotFoundError: No module named 'uzel'` | the package is not installed in `.venv` | `.venv\Scripts\python.exe -m pip install -e . --no-deps` |
| The core writes to a foreign data folder | an environment variable beats `.env` | `echo %UZEL_DATA_DIR%`, remove it |
| Transcription takes minutes | no CUDA wheels, the CPU is working | step 6 |
| The batch silently waits for whisper to load | Ollama still holds the card | wait for `keep_alive` or unload the model |
| `uzel.cmd` complains with garbled characters | the batch file was re-saved as UTF-8 | keep it ASCII: cmd.exe reads `.cmd` in the OEM codepage |
| A scan does not find the pendant | it is connected to a phone | disconnect it from the phone |
| Non-ASCII output is garbled | console encoding | `PYTHONUTF8=1` (already set in `uzel.cmd`) |
| The first search "hangs" | the embedding model (255 MB) is downloading | do not kill the process, wait |
| Wrong language in the window or notes | `UZEL_LANG=auto` picked the system language | pick a language in the window or set `UZEL_LANG=en` |

## What the installation does NOT give you

- **A WER measurement** — you need a recording of your own voice and its
  verbatim transcript, see `tests/wer/README.md`, then
  `uzel.cmd wer <audio> <reference>`. The reading text and the acceptance
  tooling are Russian for now.
- **A pendant** — it has to be built and flashed:
  [09-build-the-pendant.md](09-build-the-pendant.md).
- **A calibrated quality filter** — `UZEL_QUALITY_ENFORCE=false` is set on
  purpose: the thresholds in `config.py` are still hypotheses. Live with
  `uzel.cmd quality-report` for a week, then switch it on.
