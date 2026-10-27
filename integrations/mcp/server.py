"""Запускатель MCP-сервера УЗЛА — то, что прописывается в ~/.claude.json.

Скрипт вне пакета: Claude Code запускает его с любым текущим каталогом,
поэтому пути до ``core`` и ``integrations/mcp`` считаются от ``__file__``.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "core"))
sys.path.insert(0, str(_ROOT / "integrations" / "mcp"))

from uzel_mcp.server import main

raise SystemExit(main())
