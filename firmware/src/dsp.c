#include <math.h>
#include "dsp.h"

#define DSP_PI 3.14159265358979f

void dsp_init(dsp_state_t *st, float fs, float hpf_hz, float gain_db, float limit)
{
    // ФВЧ по RBJ Audio EQ Cookbook, Q = 1/sqrt(2) (Баттерворт)
    const float w0 = 2.0f * DSP_PI * hpf_hz / fs;
    const float cw = cosf(w0);
    const float alpha = sinf(w0) / (2.0f * 0.70710678f);
    const float a0 = 1.0f + alpha;

    st->b0 = (1.0f + cw) / 2.0f / a0;
    st->b1 = -(1.0f + cw) / a0;
    st->b2 = (1.0f + cw) / 2.0f / a0;
    st->a1 = -2.0f * cw / a0;
    st->a2 = (1.0f - alpha) / a0;
    st->z1 = 0.0f;
    st->z2 = 0.0f;
    st->gain = powf(10.0f, gain_db / 20.0f);
    st->limit = limit;
    st->limited = 0;
}

// Ниже порога сигнал не трогается, выше — плавно прижимается к полной шкале
// (tanh-колено). Жёсткого среза нет, поэтому нет и гармоник клиппинга.
static inline float soft_limit(float x, float t, uint32_t *limited)
{
    const float ax = fabsf(x);
    if (ax <= t)
    {
        return x;
    }
    (*limited)++;
    const float knee = 1.0f - t;
    const float y = t + knee * tanhf((ax - t) / knee);
    return x < 0.0f ? -y : y;
}

void dsp_process(dsp_state_t *st, int16_t *pcm, size_t n)
{
    float z1 = st->z1;
    float z2 = st->z2;

    for (size_t i = 0; i < n; i++)
    {
        const float x = (float)pcm[i] * (1.0f / 32768.0f);
        const float y = st->b0 * x + z1;
        z1 = st->b1 * x - st->a1 * y + z2;
        z2 = st->b2 * x - st->a2 * y;

        const float out = soft_limit(y * st->gain, st->limit, &st->limited) * 32767.0f;
        // lrintf медленнее на M4F, а точность округления здесь не важна
        pcm[i] = (int16_t)(out >= 0.0f ? out + 0.5f : out - 0.5f);
    }

    st->z1 = z1;
    st->z2 = z2;
}
