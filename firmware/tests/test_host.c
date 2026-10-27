// Тесты переносимых модулей прошивки на ПК (gcc в WSL): dsp, frame_queue,
// audio_packet. Запуск: wsl -- bash firmware/tests/run.sh

#include <math.h>
#include <stdio.h>
#include <string.h>
#include "../src/dsp.h"
#include "../src/frame_queue.h"
#include "../src/audio_packet.h"

static int failures = 0;
static int checks = 0;

#define CHECK(cond, ...)                                  \
    do                                                    \
    {                                                     \
        checks++;                                         \
        if (!(cond))                                      \
        {                                                 \
            failures++;                                   \
            printf("ПРОВАЛ %s:%d: ", __FILE__, __LINE__); \
            printf(__VA_ARGS__);                          \
            printf("\n");                                 \
        }                                                 \
    } while (0)

#define FS 16000.0f
#define N 32000

static int16_t buf[(int)N];

static void sine(float hz, float amp)
{
    for (int i = 0; i < (int)N; i++)
    {
        buf[i] = (int16_t)lrintf(amp * 32767.0f * sinf(2.0f * 3.14159265f * hz * i / FS));
    }
}

// RMS второй половины буфера (переходный процесс фильтра отброшен), dBFS
static float rms_db_tail(void)
{
    double s = 0;
    int n = 0;
    for (int i = (int)N / 2; i < (int)N; i++, n++)
    {
        s += (double)buf[i] * buf[i];
    }
    return 20.0f * log10f((float)sqrt(s / n) / 32767.0f);
}

static float response_db(float hz)
{
    dsp_state_t st;
    dsp_init(&st, FS, 120.0f, 0.0f, 1.0f);
    sine(hz, 0.01f);
    const float in = rms_db_tail();
    dsp_process(&st, buf, (size_t)N);
    return rms_db_tail() - in;
}

static void test_dsp(void)
{
    // АЧХ ФВЧ 120 Гц, 2-й порядок
    float r20 = response_db(20), r60 = response_db(60), r120 = response_db(120);
    float r1k = response_db(1000), r4k = response_db(4000);
    printf("АЧХ: 20 Гц %.1f дБ, 60 Гц %.1f, 120 Гц %.1f, 1 кГц %.2f, 4 кГц %.2f\n", r20, r60, r120, r1k, r4k);
    CHECK(r20 < -25.0f, "20 Гц должен давиться сильнее 25 дБ: %.1f", r20);
    CHECK(r60 < -10.0f, "60 Гц должен давиться сильнее 10 дБ: %.1f", r60);
    CHECK(fabsf(r120 + 3.0f) < 0.6f, "на срезе −3 дБ: %.1f", r120);
    CHECK(fabsf(r1k) < 0.3f, "1 кГц проходит без изменений: %.2f", r1k);
    CHECK(fabsf(r4k) < 0.3f, "4 кГц проходит без изменений: %.2f", r4k);

    // Постоянная составляющая уходит
    dsp_state_t st;
    dsp_init(&st, FS, 120.0f, 0.0f, 1.0f);
    for (int i = 0; i < (int)N; i++)
    {
        buf[i] = 3000;
    }
    dsp_process(&st, buf, (size_t)N);
    CHECK(buf[(int)N - 1] > -5 && buf[(int)N - 1] < 5, "DC не убран: %d", buf[(int)N - 1]);

    // Усиление: −40 dBFS + 24 дБ = −16 dBFS, лимитер не трогает
    dsp_init(&st, FS, 120.0f, 24.0f, 0.89f);
    sine(1000, 0.01f);
    const float in = rms_db_tail();
    dsp_process(&st, buf, (size_t)N);
    const float g = rms_db_tail() - in;
    CHECK(fabsf(g - 24.0f) < 0.3f, "усиление 24 дБ: получено %.2f", g);
    CHECK(st.limited == 0, "лимитер сработал на тихом сигнале: %u", st.limited);

    // Громкий сигнал: лимитер работает, переполнения int16 нет
    dsp_init(&st, FS, 120.0f, 24.0f, 0.89f);
    sine(1000, 0.08f); // +24 дБ → ~1,26 полной шкалы: лимитер работает, форма ещё синусоидальная
    dsp_process(&st, buf, (size_t)N);
    int peak = 0, wraps = 0;
    for (int i = 1; i < (int)N; i++)
    {
        int a = buf[i] < 0 ? -buf[i] : buf[i];
        peak = a > peak ? a : peak;
        // переполнение дало бы скачок через всю шкалу между соседними отсчётами
        if ((buf[i] > 20000 && buf[i - 1] < -20000) || (buf[i] < -20000 && buf[i - 1] > 20000))
        {
            wraps++;
        }
    }
    CHECK(st.limited > 0, "лимитер не сработал на громком сигнале");
    CHECK(peak <= 32767 && peak > 30000, "пик после лимитера: %d", peak);
    CHECK(wraps == 0, "переполнение int16: %d скачков", wraps);

    // Обработка блоками по 20 мс даёт тот же результат, что одним куском
    static int16_t a[16000], b[16000];
    for (int i = 0; i < 16000; i++)
    {
        a[i] = b[i] = (int16_t)((i * 7919) % 20000 - 10000);
    }
    dsp_state_t s1, s2;
    dsp_init(&s1, FS, 120.0f, 12.0f, 0.89f);
    dsp_init(&s2, FS, 120.0f, 12.0f, 0.89f);
    dsp_process(&s1, a, 16000);
    for (int i = 0; i < 16000; i += 320)
    {
        dsp_process(&s2, b + i, 320);
    }
    CHECK(memcmp(a, b, sizeof(a)) == 0, "блочная обработка расходится с цельной");
}

static void test_frame_queue(void)
{
    static fq_slot_t slots[4];
    frame_queue_t q;
    fq_init(&q, slots, 4);
    uint8_t d[FQ_MAX_FRAME + 1];
    memset(d, 0xAB, sizeof(d));

    CHECK(fq_peek(&q) == NULL, "пустая очередь отдала кадр");
    CHECK(!fq_push(&q, d, 0), "принят пустой кадр");
    CHECK(!fq_push(&q, d, FQ_MAX_FRAME + 1), "принят слишком длинный кадр");
    for (int i = 0; i < 4; i++)
    {
        d[0] = (uint8_t)i;
        CHECK(fq_push(&q, d, (size_t)(10 + i)), "кадр %d не влез", i);
    }
    CHECK(fq_full(&q), "очередь на 4 не полна после 4 кадров");
    CHECK(!fq_push(&q, d, 5), "принят кадр в полную очередь");
    for (int i = 0; i < 4; i++)
    {
        const fq_slot_t *s = fq_peek(&q);
        CHECK(s && s->data[0] == i && s->len == 10 + i, "порядок нарушен на %d", i);
        CHECK(fq_peek(&q) == s, "peek сдвинул очередь");
        fq_pop(&q);
    }
    CHECK(fq_count(&q) == 0, "очередь не опустела");
    fq_pop(&q); // pop пустой очереди безопасен
    CHECK(fq_count(&q) == 0, "pop пустой очереди сломал счётчик");

    // Переполнение индексов uint32
    q.head = q.tail = 0xFFFFFFFEu;
    for (int round = 0; round < 10; round++)
    {
        d[0] = (uint8_t)round;
        CHECK(fq_push(&q, d, 1), "push на переполнении индекса");
        const fq_slot_t *s = fq_peek(&q);
        CHECK(s && s->data[0] == round, "кадр потерян на переполнении индекса");
        fq_pop(&q);
    }
    CHECK(fq_count(&q) == 0, "счётчик после переполнения индекса: %u", fq_count(&q));
}

static void test_audio_packet(void)
{
    CHECK(audio_packet_max_frame(23) == 17, "MTU 23: %zu", audio_packet_max_frame(23));
    CHECK(audio_packet_max_frame(247) == 241, "MTU 247: %zu", audio_packet_max_frame(247));
    CHECK(audio_packet_max_frame(6) == 0 && audio_packet_max_frame(0) == 0, "крошечный MTU");

    uint8_t frame[5] = {1, 2, 3, 4, 5};
    uint8_t out[8];
    size_t n = audio_packet_build(out, sizeof(out), 0x1234, frame, 5);
    CHECK(n == 8, "длина пакета %zu", n);
    CHECK(out[0] == 0x34 && out[1] == 0x12 && out[2] == 0, "заголовок %02x %02x %02x", out[0], out[1], out[2]);
    CHECK(memcmp(out + 3, frame, 5) == 0, "кадр испорчен");
    CHECK(audio_packet_build(out, 7, 1, frame, 5) == 0, "пакет не влезает, а собран");
    CHECK(audio_packet_build(out, 8, 1, frame, 0) == 0, "собран пакет без кадра");
}

int main(void)
{
    test_dsp();
    test_frame_queue();
    test_audio_packet();
    printf("%s: проверок %d, провалов %d\n", failures ? "ПРОВАЛ" : "ОК", checks, failures);
    return failures ? 1 : 0;
}
