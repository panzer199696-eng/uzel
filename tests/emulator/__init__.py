"""Эмулятор кулона «УЗЕЛ» для долгих прогонов (пакет P10).

Кулон уехал с владельцем 17.09.2026, а система ни разу не работала дольше
нескольких минут. Этот пакет изображает кулон часами: речь и тишина, потери
радио, разрывы связи, разряд батареи — и всё это на настоящих пакетах
протокола, которые разбирает ``parse_packet``.

Запуск долгого прогона::

    .venv\\Scripts\\python.exe -m tests.emulator.run --hours 12 \\
        --speech-ratio 0.25 --drop-rate 0.01 --disconnect-every 30m \\
        --target http://127.0.0.1:8770 --uid эмулятор-1 \\
        --report ops\\прогон-p10\\поток.json

Чтобы пакет запускался как ``python -m tests.emulator.run`` без PYTHONPATH,
здесь добавляются ``core`` и ``bridge`` в sys.path (те же каталоги, что pytest
получает из pythonpath в pyproject.toml).
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _part in ("core", "bridge"):
    _path = str(_PROJECT_ROOT / _part)
    if _path not in sys.path:
        sys.path.insert(0, _path)
