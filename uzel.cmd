@echo off
rem uzel.cmd <command> [args] -- CLI launcher. Install guide: docs/07-*.md
rem
rem Since P12 (24.09.2026) the package is installed editable into .venv, so
rem .venv\Scripts\uzel.exe works on its own. PYTHONPATH below stays as a
rem fallback for a venv built without `pip install -e . --no-deps`.
rem Paths are resolved from this file, so any current directory works.
rem
rem ASCII only on purpose: cmd.exe parses .cmd files in the OEM codepage,
rem and Cyrillic saved as UTF-8 breaks the parser line by line.

setlocal
set "UZEL_ROOT=%~dp0"
rem "bridge" is not optional: the worker imports uzel_bridge.opus_decoder to
rem decode Opus frames from the phone. Without it every /ingest/opus chunk was
rem dropped into "done" while the phone kept getting 202 -- found by the long
rem run on 23.09.2026, not by tests: acceptance scripts added the path by hand.
set "PYTHONPATH=%UZEL_ROOT%core;%UZEL_ROOT%bridge;%UZEL_ROOT%integrations\mcp"
set "PYTHONUTF8=1"

rem No parentheses in the message on purpose: an unescaped ")" inside an
rem if-block closes the block early, and `exit /b 2` then runs every time.
rem Cost one red acceptance run on 20.09.2026.
if not exist "%UZEL_ROOT%.venv\Scripts\python.exe" (
    echo [uzel] no .venv in %UZEL_ROOT% -- see the install guide in docs\ 1>&2
    exit /b 2
)

"%UZEL_ROOT%.venv\Scripts\python.exe" -m uzel.cli %*
exit /b %ERRORLEVEL%
