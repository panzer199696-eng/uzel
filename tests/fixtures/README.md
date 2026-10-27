# Фикстуры тестов

## `речь-ru-16k.wav`

Синтезированная речь, а не запись живого человека: голос **Microsoft Irina
Desktop** (ru-RU) встроенного синтезатора Windows (`System.Speech`), скорость +1.

- Текст: «Сегодня подписали акт скрытых работ по армированию плиты.»
- Формат: WAV, 16 кГц, моно, 16 бит, **ровно 4,000 с** (64 000 сэмплов): речь
  ≈ 3,85 с, тишина синтезатора по краям обрезана и добита нулями до 4 с поровну.
  Длина важна: эмулятор (`tests/emulator/scenario.py`) режет поток блоками
  длины фикстуры, а `test_emulator.py` считает их по 4 с.
- Проверено: Silero VAD — вероятность речи до 1,0, речь на всей длине;
  faster-whisper `small` и `large-v3` распознают текст дословно.

На ней держатся проверки, которые синтетический тон не ловит: окно Silero VAD
с контекстом (`test_vad.py`), сквозной путь «Opus → ядро → расшифровка»
(`test_worker_e2e.py`) и интеграционная расшифровка (`test_integration_stt.py`,
ищет в тексте «акт» и «скрытых работ»).

Пересобрать (PowerShell):

```powershell
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice("Microsoft Irina Desktop"); $s.Rate = 1
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
  [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
  [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile("речь-ru-16k.wav", $fmt)
$s.Speak("Сегодня подписали акт скрытых работ по армированию плиты.")
$s.Dispose()
```

После синтеза обрезать тишину по краям (порог огибающей ≈ 500 из 32767, запас
80 мс) и добить нулями до 64 000 сэмплов.
