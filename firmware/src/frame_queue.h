#pragma once

// Очередь кадров Opus между кодеком и BLE: один писатель, один читатель.
//
// Кадр держится в очереди, пока не уйдёт в эфир (peek → отправка → pop), так
// что при коротком обрыве связи звук не теряется, а досылается после
// переподключения. Без зависимостей от Zephyr — тестируется на ПК.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FQ_MAX_FRAME 200

typedef struct
{
    uint16_t len;
    uint8_t data[FQ_MAX_FRAME];
} fq_slot_t;

typedef struct
{
    fq_slot_t *slots;
    uint32_t cap;
    uint32_t head; // пишет только производитель
    uint32_t tail; // пишет только потребитель
} frame_queue_t;

void fq_init(frame_queue_t *q, fq_slot_t *slots, uint32_t cap);

// false — очередь полна или кадр пустой/длиннее FQ_MAX_FRAME.
bool fq_push(frame_queue_t *q, const uint8_t *data, size_t len);

// Самый старый кадр или NULL, если очередь пуста. Кадр остаётся в очереди.
const fq_slot_t *fq_peek(frame_queue_t *q);

// Убрать самый старый кадр (после успешной отправки или при отказе от него).
void fq_pop(frame_queue_t *q);

uint32_t fq_count(const frame_queue_t *q);
bool fq_full(const frame_queue_t *q);
