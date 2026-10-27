#pragma once

#include <stddef.h>
#include <stdint.h>

int codec_start(void);

// Блок PCM от микрофона. Можно звать из прерывания: только копирует в кольцо.
void codec_push_pcm(const int16_t *pcm, size_t samples);
