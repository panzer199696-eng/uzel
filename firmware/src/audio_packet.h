#pragma once

// BLE-пакет аудио в протоколе Omi, который понимают оба моста УЗЛА:
//
//   [0:2]  номер пакета, uint16 LE — по нему мост считает потери
//   [2]    внутренний индекс — у нас всегда 0: кадр не дробится
//   [3:]   один цельный кадр Opus
//
// Мосты собирать кадр из кусков не умеют (bridge/uzel_bridge/protocol.py),
// поэтому кадр, не влезающий в MTU, не режется, а ограничивается ещё в кодере
// (audio_packet_max_frame → max_data_bytes у opus_encode).

#include <stddef.h>
#include <stdint.h>

#define AUDIO_PACKET_HEADER 3
#define ATT_NOTIFY_OVERHEAD 3 // opcode + handle в уведомлении ATT

// Сколько байт кадра влезает в одно уведомление при данном ATT MTU.
size_t audio_packet_max_frame(uint16_t att_mtu);

// Собирает пакет в out. Возвращает его длину или 0, если не влезает в out_cap.
size_t audio_packet_build(uint8_t *out, size_t out_cap, uint16_t index,
                          const uint8_t *frame, size_t frame_len);
