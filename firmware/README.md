# Прошивка УЗЛА

*English → [README.en.md](README.en.md)*

Кулон: Seeed XIAO nRF52840 **Sense** (корпус Triangle v1), загрузчик Adafruit nRF52
Bootloader (UF2) с SoftDevice S140 7.3.0 во флеше (нужен загрузчику для обновления
по воздуху). Zephyr / nRF Connect SDK v2.7.0, приложение с адреса `0x27000`.
Как собрать сам кулон и поставить загрузчик на чистую плату, описано в
[docs/09-сборка-кулона.md](../docs/09-сборка-кулона.md).

Прошивка выросла из Omi DK2 2.0.10 (BasedHardware/omi, MIT). Сторонние
компоненты и их лицензии перечислены в [NOTICE](../NOTICE).

## Тулчейн (один раз)

Проект собирается в **WSL2** (Ubuntu 22.04/24.04) или на любом Linux x86-64.
`build.sh` ждёт тулчейн по таким путям:

| Что | Путь |
|---|---|
| nRF Connect SDK v2.7.0 (west-workspace) | `~/ncs` |
| Zephyr SDK 0.16.8 | `~/zephyr-sdk-0.16.8` |
| Python-окружение с `west` | `~/ncs-venv` |

```bash
sudo apt update
sudo apt install --no-install-recommends git cmake ninja-build gperf ccache \
  dfu-util device-tree-compiler wget xz-utils file make gcc python3-dev \
  python3-venv python3-pip rsync

# west и исходники nRF Connect SDK 2.7.0 (~4 ГБ)
python3 -m venv ~/ncs-venv
~/ncs-venv/bin/pip install west
~/ncs-venv/bin/west init -m https://github.com/nrfconnect/sdk-nrf --mr v2.7.0 ~/ncs
cd ~/ncs && ~/ncs-venv/bin/west update && ~/ncs-venv/bin/west zephyr-export
~/ncs-venv/bin/pip install -r zephyr/scripts/requirements.txt \
  -r nrf/scripts/requirements.txt -r bootloader/mcuboot/scripts/requirements.txt

# Zephyr SDK 0.16.8, только компилятор под ARM
cd ~
wget https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v0.16.8/zephyr-sdk-0.16.8_linux-x86_64_minimal.tar.xz
tar xf zephyr-sdk-0.16.8_linux-x86_64_minimal.tar.xz
~/zephyr-sdk-0.16.8/setup.sh -t arm-zephyr-eabi -c
```

Другие пути можно подставить, исправив переменные в начале `build.sh`.

## Сборка

```bash
bash firmware/build.sh release   # → firmware/out/uzel-fw-<версия>.uf2, .hex, -dfu.zip
bash firmware/build.sh debug     # то же с логами в USB CDC: uzel-fw-<версия>-debug.*
bash firmware/tests/run.sh       # тесты dsp / frame_queue / audio_packet на ПК (gcc)
```

Из Windows те же команды запускаются через `wsl -- bash firmware/build.sh release`.
`build.sh` копирует дерево в `~/uzel-fw/app` (rsync, только изменения) и собирает
там: путь к репозиторию на диске Windows может содержать кириллицу и пробелы,
а сборка через `/mnt/c` в разы медленнее.

Конфигурация:

| Файл | Что задаёт |
|---|---|
| `prj_uzel.conf` | рабочая сборка: BLE, сопряжение, сервисы, логи выключены; **версия** — `CONFIG_BT_DIS_FW_REV_STR` |
| `prj_uzel_debug.conf` | добавка для `debug`: консоль и логи в USB CDC |
| `boards/xiao_ble_sense.overlay` | дерево устройств поверх платы `xiao_ble/nrf52840/sense` |
| `boards/debug_console.overlay` | консоль на USB для `debug` |

Версия растёт с каждой сборкой, которая уходит на кулон. Изменения описаны
в `CHANGELOG.md`.

## Прошивка

- **По кабелю:** двойное нажатие RST → диск `XIAO-SENSE` → скопировать `.uf2`.
- **По воздуху с ПК:** `uzel-bridge --dfu firmware/out/uzel-fw-<версия>.hex`.
- **По воздуху с телефона:** Android-клиент → Настройки → «Прошивка кулона» →
  `uzel-fw-<версия>-dfu.zip`.

Для обновления по воздуху кулон должен быть уже сопряжён: войти в загрузчик
можно только по зашифрованной связи. ⚠️ Сам пакет DFU не подписан, загрузчик
проверяет только CRC. Подробности описаны в
[docs/09-сборка-кулона.md](../docs/09-сборка-кулона.md#5-прошивка).

## Протокол BLE

| Что | UUID | Доступ |
|---|---|---|
| Аудио, сервис | `19b10000-e8f2-537e-4f6c-d104768a1214` | |
| кадры Opus | `…0001` | notify, подписка по шифрованию |
| код кодека | `…0002` | read, 20 = Opus 16 кГц |
| DFU (Nordic Legacy) | `00001530-1212-efde-1523-785feabcd123` | |
| точка управления | `00001531-…` | write по шифрованию: `01 04` → загрузчик |
| Статистика УЗЛА | `5a0e1000-7a3e-4b8e-9f2a-0d1e2f3a4b5c` | |
| счётчики | `5a0e1001-…` | read по шифрованию, формат `src/stats.h` |
| Батарея, DIS | стандартные | |

Пакет аудио: `uint16 LE` номер + `0` + один цельный кадр Opus 20 мс (CELT, 16 кГц).

## Сопряжение

Just Works. Новое устройство принимается, только пока сопряжённых нет **или кулон
подключён к USB**. Телефон/ПК, потерявший ключи, сопрягается заново на зарядке.
Без шифрования через 30 с кулон рвёт соединение.

## Светодиоды

| Свет | Значит |
|---|---|
| красный–зелёный–синий при старте | загрузка |
| зелёный горит | на USB |
| синяя вспышка раз в 3 с | связь с мостом есть |
| красная вспышка раз в 3 с | связи нет, звук копится (до 2 с) |
| красные серии по N, 10 с, перезагрузка | сбой запуска на шаге N (1 BLE, 2 кодек, 3 микрофон) |
