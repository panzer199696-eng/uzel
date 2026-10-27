#pragma once

// Счётчики работы прошивки. Читаются с телефона/ПК через характеристику
// статистики (transport.c) — так проверяется звук и нагрузка на живом кулоне
// без отладочной консоли.

#include <stdint.h>

typedef struct __attribute__((packed))
{
    uint8_t version;            // формат структуры, сейчас 1
    uint32_t uptime_s;
    uint32_t frames_encoded;
    uint32_t pcm_dropped_blocks; // блоки по 100 мс, не влезшие в кольцо кодека
    uint32_t frames_dropped_tx;  // кадры, не влезшие в очередь BLE
    uint32_t frames_sent;
    uint32_t notify_failures;    // кадры, выброшенные после исчерпания повторов
    uint32_t limited_samples;    // отсчёты, прошедшие через лимитер
    uint16_t encode_us_avg;      // время кодирования кадра 20 мс
    uint16_t encode_us_max;
    uint16_t att_mtu;
    uint8_t tx_queue_fill;       // кадров в очереди сейчас
    uint8_t bonds;               // сопряжённых устройств
} uzel_stats_t;

extern uzel_stats_t uzel_stats;
