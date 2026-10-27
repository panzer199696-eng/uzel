#include <zephyr/logging/log.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/ring_buffer.h>
#include "codec.h"
#include "config.h"
#include "dsp.h"
#include "frame_queue.h"
#include "stats.h"
#include "transport.h"
#include "utils.h"
#include "lib/opus-1.2.1/opus.h"

LOG_MODULE_REGISTER(codec, CONFIG_LOG_DEFAULT_LEVEL);

// Микрофон (прерывание) → кольцо PCM → поток кодека: ФВЧ/усиление/лимитер →
// Opus 20 мс → очередь BLE. Поток спит на семафоре, пока нет полного кадра;
// раньше он опрашивал кольцо каждые 10 мс.

static int16_t pcm_ring_data[CODEC_PCM_RING_SAMPLES];
static struct ring_buf pcm_ring;
static K_SEM_DEFINE(pcm_ready, 0, 1);

static dsp_state_t dsp;
static int16_t frame_pcm[CODEC_FRAME_SAMPLES];
static uint8_t frame_opus[FQ_MAX_FRAME];

// Размер состояния CELT-кодера в сборке libopus из src/lib (только CELT,
// FIXED_POINT): 2200 + 4980 байт, проверено 26.10.2026 на arm-zephyr-eabi.
#define OPUS_ENCODER_SIZE 7180
static uint8_t opus_state_mem[OPUS_ENCODER_SIZE] __aligned(4);
static OpusEncoder *const opus_state = (OpusEncoder *)opus_state_mem;

#define CODEC_STACK_SIZE 32000 // libopus собран с USE_ALLOCA: рабочие буферы на стеке
K_THREAD_STACK_DEFINE(codec_stack, CODEC_STACK_SIZE);
static struct k_thread codec_thread;

void codec_push_pcm(const int16_t *pcm, size_t samples)
{
    const uint32_t bytes = samples * sizeof(int16_t);
    if (ring_buf_space_get(&pcm_ring) < bytes)
    {
        uzel_stats.pcm_dropped_blocks++;
        return;
    }
    ring_buf_put(&pcm_ring, (const uint8_t *)pcm, bytes);
    k_sem_give(&pcm_ready);
}

static void encode_frame(void)
{
    dsp_process(&dsp, frame_pcm, CODEC_FRAME_SAMPLES);
    uzel_stats.limited_samples = dsp.limited;

    // Очередь полна (связи давно нет) — не тратим процессор на кодирование.
    // ФВЧ при этом прогнан: его состояние должно идти без разрывов.
    if (transport_queue_full())
    {
        uzel_stats.frames_dropped_tx++;
        return;
    }

    const uint32_t t0 = k_cycle_get_32();
    const opus_int32 len = opus_encode(opus_state, frame_pcm, CODEC_FRAME_SAMPLES,
                                       frame_opus, transport_max_frame_bytes());
    const uint32_t us = k_cyc_to_us_floor32(k_cycle_get_32() - t0);

    if (len <= 0)
    {
        LOG_WRN("Opus: ошибка кодирования %d", len);
        return;
    }

    uzel_stats.frames_encoded++;
    if (us > uzel_stats.encode_us_max)
    {
        uzel_stats.encode_us_max = (uint16_t)MIN(us, UINT16_MAX);
    }
    // Скользящее среднее с окном ~32 кадра
    uzel_stats.encode_us_avg = (uint16_t)((uzel_stats.encode_us_avg * 31u + us) / 32u);

    if (!transport_queue_frame(frame_opus, (size_t)len))
    {
        uzel_stats.frames_dropped_tx++;
    }
}

static void codec_thread_fn(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    for (;;)
    {
        k_sem_take(&pcm_ready, K_FOREVER);
        while (ring_buf_size_get(&pcm_ring) >= sizeof(frame_pcm))
        {
            ring_buf_get(&pcm_ring, (uint8_t *)frame_pcm, sizeof(frame_pcm));
            encode_frame();
        }
    }
}

int codec_start(void)
{
    ASSERT_TRUE(opus_encoder_get_size(1) <= (int)sizeof(opus_state_mem));
    ASSERT_TRUE(opus_encoder_init(opus_state, MIC_SAMPLE_RATE, 1, OPUS_APPLICATION_RESTRICTED_LOWDELAY) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(opus_state, OPUS_SET_BITRATE(CODEC_OPUS_BITRATE)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(opus_state, OPUS_SET_VBR(1)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(opus_state, OPUS_SET_VBR_CONSTRAINT(0)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(opus_state, OPUS_SET_COMPLEXITY(CODEC_OPUS_COMPLEXITY)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(opus_state, OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(opus_state, OPUS_SET_LSB_DEPTH(16)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(opus_state, OPUS_SET_DTX(0)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(opus_state, OPUS_SET_INBAND_FEC(0)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(opus_state, OPUS_SET_PACKET_LOSS_PERC(0)) == OPUS_OK);

    dsp_init(&dsp, (float)MIC_SAMPLE_RATE, DSP_HPF_HZ, DSP_GAIN_DB, DSP_LIMIT);
    ring_buf_init(&pcm_ring, sizeof(pcm_ring_data), (uint8_t *)pcm_ring_data);

    k_thread_create(&codec_thread, codec_stack, K_THREAD_STACK_SIZEOF(codec_stack),
                    codec_thread_fn, NULL, NULL, NULL, K_PRIO_PREEMPT(4), 0, K_NO_WAIT);
    k_thread_name_set(&codec_thread, "codec");
    return 0;
}
