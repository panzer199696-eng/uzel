#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

int transport_start(void);

// Есть ли зашифрованное соединение с сопряжённым устройством.
bool transport_is_connected(void);

// Кадр Opus в очередь на отправку. false — очередь полна.
bool transport_queue_frame(const uint8_t *frame, size_t len);
bool transport_queue_full(void);

// Предел размера кадра для кодера: чтобы кадр целиком влез в уведомление.
size_t transport_max_frame_bytes(void);
