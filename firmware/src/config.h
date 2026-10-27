#pragma once

// Настройки прошивки УЗЛА. Версия — CONFIG_BT_DIS_FW_REV_STR в prj_uzel.conf
// (один источник: оттуда же её берёт build.sh для имени файла).

#include <haly/nrfy_gpio.h>

//
// Микрофон (PDM, MSM261D3526H1CPM на XIAO nRF52840 Sense)
//
#define MIC_SAMPLE_RATE 16000
#define MIC_BUFFER_SAMPLES 1600 // 100 мс на буфер DMA
// 0x28 = 0 дБ (шаг 0,5 дБ). Было 0x40 = +12 дБ: удары по корпусу клиппировали
// ещё в PDM, до любой обработки. Усиление теперь цифровое, после ФВЧ (DSP_GAIN_DB).
#define MIC_PDM_GAIN 0x28
// Приоритет прерывания PDM задаёт только IRQ_CONNECT: в Zephyr NRFX_IRQ_PRIORITY_SET
// пустой, поле interrupt_priority у nrfx игнорируется. Фактически было 5 — так и оставлено.
#define MIC_IRQ_PRIORITY 5

// https://github.com/Seeed-Studio/Adafruit_nRF52_Arduino/blob/5aa3573913449410fd60f76b75673c53855ff2ec/variants/Seeed_XIAO_nRF52840_Sense/variant.cpp#L34
#define PDM_DIN_PIN NRF_GPIO_PIN_MAP(0, 16)
#define PDM_CLK_PIN NRF_GPIO_PIN_MAP(1, 0)
#define PDM_PWR_PIN NRF_GPIO_PIN_MAP(1, 10)

//
// Обработка до кодека (src/dsp.c). Обоснование цифр — в шапке dsp.h.
//
#define DSP_HPF_HZ 120.0f
// 0.1.3: 24 → 18 дБ. Замер 27.10 после носки (3 ч): с +24 в 14 % фрагментов звук
// доходил до полной шкалы через лимитер; модель на тех же записях даёт при +18 ~0 %,
// речь −35 dBFS (старая прошивка — −43).
#define DSP_GAIN_DB 18.0f // итого +6 дБ к прежнему уровню (аппаратные +12 сняты)
#define DSP_LIMIT 0.89f   // −1 dBFS: выше — мягкое колено

//
// Кодек Opus (CELT, 16 кГц моно)
//
#define CODEC_FRAME_SAMPLES 320 // 20 мс: при том же битрейте качество выше, чем у 10 мс
#define CODEC_OPUS_BITRATE 32000
#define CODEC_OPUS_COMPLEXITY 3
#define CODEC_PCM_RING_SAMPLES 16000 // 1 с запаса между микрофоном и кодеком
#define CONFIG_OPUS_MODE CONFIG_OPUS_MODE_CELT
// Значение характеристики кодека 19b10002: 20 = Opus 16 кГц (протокол Omi)
#define CODEC_ID 20

//
// Передача по BLE
//
#define TX_QUEUE_FRAMES 100      // 2 с звука переживают обрыв связи
#define TX_RETRY_TOTAL_MS 200    // сколько ждать освобождения буферов BLE на один кадр
#define UNSECURED_CONN_TIMEOUT_S 30 // чужак без шифрования не держит единственный слот
#define BATTERY_REFRESH_S 15
