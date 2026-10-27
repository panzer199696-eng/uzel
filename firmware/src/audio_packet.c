#include <string.h>
#include "audio_packet.h"

size_t audio_packet_max_frame(uint16_t att_mtu)
{
    const size_t overhead = ATT_NOTIFY_OVERHEAD + AUDIO_PACKET_HEADER;
    return att_mtu > overhead ? (size_t)att_mtu - overhead : 0;
}

size_t audio_packet_build(uint8_t *out, size_t out_cap, uint16_t index,
                          const uint8_t *frame, size_t frame_len)
{
    if (frame_len == 0 || frame_len + AUDIO_PACKET_HEADER > out_cap)
    {
        return 0;
    }
    out[0] = index & 0xFF;
    out[1] = (index >> 8) & 0xFF;
    out[2] = 0;
    memcpy(out + AUDIO_PACKET_HEADER, frame, frame_len);
    return frame_len + AUDIO_PACKET_HEADER;
}
