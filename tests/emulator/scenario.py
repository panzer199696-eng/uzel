"""Сценарий потока: что именно кулон «проживает» за прогон (P10).

Один ``seed`` — один и тот же поток: блоки речи/тишины, потерянные пакеты
и расписание разрывов повторяются побайтово. Гроверы случайности разведены
по ролям (материал, потери), чтобы порядок вызовов одной роли не менял другую.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "речь-ru-16k.wav"

# Настоящая тишина — не повтор речи и не идеальный ноль, а очень тихий шум
# (около −60 дБFS): кулон стримит всегда, просто слушать нечего.
SILENCE_RMS = 30.0

# Блок потока равен длине речевой фикстуры (4 с = 200 кадров по 20 мс):
# удобная гранула для чередования речи и тишины.
SPEECH_BLOCK_SAMPLES = 64000


@dataclass(frozen=True)
class Scenario:
    """Параметры жизни кулона на прогон. Все времена — условные секунды."""

    speech_ratio: float = 0.25  # доля речи в потоке, остальное — тишина
    drop_rate: float = 0.0  # доля пакетов, теряемых радио
    disconnect_every_s: float = 1800.0  # разрыв связи каждые … секунд (0 — никогда)
    disconnect_for_s: float = 10.0  # длительность разрыва
    battery_hours: float = 24.0  # за сколько часов батарея садится со 100 % до 0
    seed: int = 42
    # Долгое молчание: без него пауза длиннее ~90 с не выпадает никогда, и
    # весь прогон склеивается в один разговор (conversation_gap_s = 300).
    pause_every_s: float = 0.0  # каждые … секунд потока — долгая тишина (0 — никогда)
    pause_for_s: float = 0.0  # сколько длится долгая тишина

    def __post_init__(self) -> None:
        if not 0.0 <= self.speech_ratio <= 1.0:
            raise ValueError(
                f"speech_ratio должен быть в [0, 1], а не {self.speech_ratio}"
            )
        if not 0.0 <= self.drop_rate < 1.0:
            raise ValueError(f"drop_rate должен быть в [0, 1), а не {self.drop_rate}")
        if self.disconnect_every_s < 0 or self.disconnect_for_s < 0:
            raise ValueError("времена разрывов не могут быть отрицательными")
        if self.pause_every_s < 0 or self.pause_for_s < 0:
            raise ValueError("времена пауз не могут быть отрицательными")
        if self.battery_hours <= 0:
            raise ValueError(
                f"battery_hours должен быть > 0, а не {self.battery_hours}"
            )
        object.__setattr__(self, "_drop_rng", random.Random(f"{self.seed}:потери"))

    # --- потери радио ---

    def should_drop(self) -> bool:
        """Решает, теряется ли очередной пакет. Вызывать по разу на пакет."""
        if self.drop_rate <= 0.0:
            return False
        return self._drop_rng.random() < self.drop_rate

    # --- разрывы связи ---

    def is_connected(self, elapsed_s: float) -> bool:
        """Связь есть ли в момент ``elapsed_s`` от старта прогона."""
        if self.disconnect_every_s <= 0:
            return True
        period = self.disconnect_every_s + self.disconnect_for_s
        return (elapsed_s % period) < self.disconnect_every_s

    # --- долгие паузы ---

    def is_paused(self, stream_s: float) -> bool:
        """Идёт ли долгое молчание в момент ``stream_s`` секунд потока."""
        if self.pause_every_s <= 0 or self.pause_for_s <= 0:
            return False
        period = self.pause_every_s + self.pause_for_s
        return (stream_s % period) >= self.pause_every_s

    # --- батарея ---

    def battery_percent(self, elapsed_s: float) -> int:
        """Заряд: линейно падает с 100 % до 0 за ``battery_hours`` часов."""
        elapsed_h = max(0.0, elapsed_s) / 3600.0
        remaining = 1.0 - elapsed_h / self.battery_hours
        return int(round(max(0.0, min(1.0, remaining)) * 100.0))


class SpeechMaterial:
    """Бесконечный поток PCM: блоки речи из фикстуры и настоящей тишины.

    Единственный доступный материал — 4 секунды русской речи в
    ``tests/fixtures/речь-ru-16k.wav``; длинный поток собирается блоками той же
    длины, где по Бернулли с вероятностью ``speech_ratio`` очередной блок —
    речь, а иначе тишина (нули плюс очень тихий шум).
    """

    def __init__(
        self,
        scenario: Scenario,
        fixture: Path | str = FIXTURE_PATH,
        *,
        silence_rms: float = SILENCE_RMS,
    ) -> None:
        from uzel.audio import read_wav

        self.scenario = scenario
        self.silence_rms = silence_rms
        samples, sr = read_wav(fixture, expected_sr=16000)
        speech = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        # Блок обязан быть кратен кадру 20 мс (320 сэмплов): подрезаем хвост,
        # если фикстура вдруг окажется некратной.
        usable = (len(speech) // 320) * 320
        self.speech = speech[:usable]
        self._block_rng = random.Random(f"{scenario.seed}:материал")
        self._silence_rng = np.random.default_rng((scenario.seed, 0))
        self._emitted_blocks = 0

    def block(self) -> tuple[np.ndarray, bool]:
        """Очередной блок потока: (PCM int16 моно 16 кГц, это речь?)."""
        stream_s = self._emitted_blocks * len(self.speech) / 16000.0
        self._emitted_blocks += 1
        # В паузе генератор речи не трогаем: без пауз поток тот же побайтово.
        paused = self.scenario.is_paused(stream_s)
        is_speech = not paused and self._block_rng.random() < self.scenario.speech_ratio
        if is_speech:
            return self.speech.copy(), True
        noise = self._silence_rng.standard_normal(len(self.speech))
        silence = np.clip(noise * self.silence_rms, -32768, 32767)
        return silence.astype(np.int16), False
