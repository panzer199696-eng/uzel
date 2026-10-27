#pragma once

#include <stddef.h>
#include <stdint.h>

// Вызывается из прерывания PDM с готовым блоком на MIC_BUFFER_SAMPLES отсчётов.
// Блок перезапишется через 100 мс — данные надо забрать сразу.
typedef void (*mic_handler_t)(const int16_t *pcm, size_t samples);

void mic_set_callback(mic_handler_t callback);
int mic_start(void);
