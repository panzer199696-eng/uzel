#include <string.h>
#include "frame_queue.h"

// Индексы растут монотонно и переполняются по модулю 2^32: разность head - tail
// остаётся верной. Слот = индекс % cap. Acquire/release нужны, чтобы читатель
// не увидел индекс раньше данных слота (и наоборот).

void fq_init(frame_queue_t *q, fq_slot_t *slots, uint32_t cap)
{
    q->slots = slots;
    q->cap = cap;
    q->head = 0;
    q->tail = 0;
}

uint32_t fq_count(const frame_queue_t *q)
{
    const uint32_t head = __atomic_load_n(&q->head, __ATOMIC_ACQUIRE);
    const uint32_t tail = __atomic_load_n(&q->tail, __ATOMIC_ACQUIRE);
    return head - tail;
}

bool fq_full(const frame_queue_t *q)
{
    return fq_count(q) >= q->cap;
}

bool fq_push(frame_queue_t *q, const uint8_t *data, size_t len)
{
    if (len == 0 || len > FQ_MAX_FRAME || fq_full(q))
    {
        return false;
    }
    const uint32_t head = q->head;
    fq_slot_t *slot = &q->slots[head % q->cap];
    memcpy(slot->data, data, len);
    slot->len = (uint16_t)len;
    __atomic_store_n(&q->head, head + 1, __ATOMIC_RELEASE);
    return true;
}

const fq_slot_t *fq_peek(frame_queue_t *q)
{
    const uint32_t head = __atomic_load_n(&q->head, __ATOMIC_ACQUIRE);
    if (head == q->tail)
    {
        return NULL;
    }
    return &q->slots[q->tail % q->cap];
}

void fq_pop(frame_queue_t *q)
{
    const uint32_t head = __atomic_load_n(&q->head, __ATOMIC_ACQUIRE);
    if (head == q->tail)
    {
        return;
    }
    __atomic_store_n(&q->tail, q->tail + 1, __ATOMIC_RELEASE);
}
