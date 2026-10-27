#include <zephyr/logging/log.h>
#include <zephyr/kernel.h>
#include <haly/nrfy_gpio.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <zephyr/sys/onoff.h>
#include "nrfx_pdm.h"
#include "config.h"
#include "mic.h"

LOG_MODULE_REGISTER(mic, CONFIG_LOG_DEFAULT_LEVEL);

// Порт https://github.com/Seeed-Studio/Seeed_Arduino_Mic/blob/master/src/hardware/nrf52840_adc.cpp
// Два буфера DMA по 100 мс: пока PDM пишет в один, второй отдаётся наверх.

static int16_t buffer_0[MIC_BUFFER_SAMPLES];
static int16_t buffer_1[MIC_BUFFER_SAMPLES];
static volatile uint8_t next_buffer_index;
static volatile mic_handler_t mic_callback;

static void pdm_irq_handler(nrfx_pdm_evt_t const *event)
{
    // Ошибка у nrfx_pdm приходит отдельным событием без запроса буфера
    // (переполнение: буфер не подали вовремя) — драйвер сам продолжит.
    if (event->error)
    {
        LOG_ERR("PDM: ошибка %d", event->error);
        return;
    }

    if (event->buffer_requested)
    {
        nrfx_pdm_buffer_set(next_buffer_index ? buffer_1 : buffer_0, MIC_BUFFER_SAMPLES);
        next_buffer_index ^= 1;
    }

    if (event->buffer_released && mic_callback)
    {
        mic_callback(event->buffer_released, MIC_BUFFER_SAMPLES);
    }
}

void mic_set_callback(mic_handler_t callback)
{
    mic_callback = callback;
}

// Кварц HFXO держим запросом через диспетчер часов. Раньше его включали прямой
// записью в регистр — MPSL об этом не знал и гасил кварц, когда радио молчало:
// PDM переходил на RC-генератор (±1,5 %), частота дискретизации плыла.
static struct onoff_client hfclk_client;

static int hfclk_request(void)
{
    struct onoff_manager *mgr = z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF);
    sys_notify_init_spinwait(&hfclk_client.notify);
    int err = onoff_request(mgr, &hfclk_client);
    if (err < 0)
    {
        return err;
    }
    int res = 0;
    for (int i = 0; i < 100; i++) // кварц стартует за ~0,4 мс; ждём до 100 мс
    {
        if (sys_notify_fetch_result(&hfclk_client.notify, &res) == 0)
        {
            return res;
        }
        k_msleep(1);
    }
    return -ETIMEDOUT;
}

int mic_start(void)
{
    int err = hfclk_request();
    if (err)
    {
        LOG_ERR("кварц HFXO не запустился: %d", err);
        return err;
    }

    // 1,28 МГц / 80 = 16 кГц
    nrfx_pdm_config_t pdm_config = NRFX_PDM_DEFAULT_CONFIG(PDM_CLK_PIN, PDM_DIN_PIN);
    pdm_config.gain_l = MIC_PDM_GAIN;
    pdm_config.gain_r = MIC_PDM_GAIN;
    pdm_config.clock_freq = NRF_PDM_FREQ_1280K;
    pdm_config.mode = NRF_PDM_MODE_MONO;
    pdm_config.edge = NRF_PDM_EDGE_LEFTFALLING;
    pdm_config.ratio = NRF_PDM_RATIO_80X;
    // Через nrfx_isr, а не IRQ_DIRECT_CONNECT: прямое подключение без ISR_DIRECT_DECLARE
    // не выходит из прерывания через планировщик, и поток кодека, разбуженный
    // семафором, ждал следующего чужого прерывания.
    IRQ_CONNECT(PDM_IRQn, MIC_IRQ_PRIORITY, nrfx_isr, nrfx_pdm_irq_handler, 0);

    if (nrfx_pdm_init(&pdm_config, pdm_irq_handler) != NRFX_SUCCESS)
    {
        LOG_ERR("PDM не инициализирован");
        return -EIO;
    }

    nrfy_gpio_cfg_output(PDM_PWR_PIN);
    nrfy_gpio_pin_set(PDM_PWR_PIN);

    if (nrfx_pdm_start() != NRFX_SUCCESS)
    {
        LOG_ERR("PDM не запустился");
        return -EIO;
    }

    LOG_INF("микрофон запущен");
    return 0;
}
