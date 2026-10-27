#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/reboot.h>
#include "codec.h"
#include "led.h"
#include "mic.h"
#include "power.h"
#include "transport.h"
#include "lib/battery/battery.h"

LOG_MODULE_REGISTER(main, CONFIG_LOG_DEFAULT_LEVEL);

#define BOOT_BLINK_MS 150
#define STATUS_TICK_MS 100
#define STATUS_PERIOD_TICKS 30 // вспышка статуса раз в 3 с
#define FATAL_REBOOT_S 10

// Индикация: зелёный горит — на зарядке; вспышка синим — идёт связь с мостом;
// вспышка красным — связи нет, кадры копятся (до 2 с) и теряются.
// Раньше синий/красный горели постоянно — это заметный расход батареи.
static void status_tick(uint32_t tick)
{
    set_led_green(power_vbus_present());

    const bool flash = (tick % STATUS_PERIOD_TICKS) == 0;
    const bool connected = transport_is_connected();
    set_led_blue(flash && connected);
    set_led_red(flash && !connected);
}

static void boot_led_sequence(void)
{
    set_led_red(true);
    k_msleep(BOOT_BLINK_MS);
    set_led_red(false);
    set_led_green(true);
    k_msleep(BOOT_BLINK_MS);
    set_led_green(false);
    set_led_blue(true);
    k_msleep(BOOT_BLINK_MS);
    set_led_blue(false);
}

// Узел не поднялся. Раньше main просто завершался: кулон «живой», но без звука
// или без BLE до ручного сброса. Теперь — код ошибки вспышками и перезагрузка.
static void fatal(int stage, int err)
{
    LOG_ERR("сбой запуска на шаге %d: %d, перезагрузка через %d с", stage, err, FATAL_REBOOT_S);
    for (int i = 0; i < FATAL_REBOOT_S; i++)
    {
        for (int n = 0; n < stage; n++)
        {
            set_led_red(true);
            k_msleep(150);
            set_led_red(false);
            k_msleep(150);
        }
        k_msleep(1000 - 300 * MIN(stage, 3));
    }
    sys_reboot(SYS_REBOOT_COLD);
}

int main(void)
{
    // RESETREAS сбрасывается записью единиц в те же биты. Раньше писали 1 —
    // очищался только бит RESETPIN, остальные причины копились.
    const uint32_t reset_reason = NRF_POWER->RESETREAS;
    NRF_POWER->RESETREAS = reset_reason;

    LOG_INF("УЗЕЛ %s, причина сброса 0x%08x", CONFIG_BT_DIS_FW_REV_STR, reset_reason);

    int err = led_start();
    if (err)
    {
        // Без светодиодов работать можно, а сообщить об ошибке нечем.
        LOG_ERR("светодиоды: %d", err);
    }
    boot_led_sequence();

    // Ошибка батареи не мешает записи: работаем без уровня заряда.
    err = battery_init();
    if (!err)
    {
        err = battery_charge_start();
    }
    if (err)
    {
        LOG_ERR("батарея: %d", err);
    }

    power_init();

    err = transport_start();
    if (err)
    {
        fatal(1, err);
    }
    err = codec_start();
    if (err)
    {
        fatal(2, err);
    }
    mic_set_callback(codec_push_pcm);
    err = mic_start();
    if (err)
    {
        fatal(3, err);
    }

    LOG_INF("запущен");
    for (uint32_t tick = 0;; tick++)
    {
        status_tick(tick);
        k_msleep(STATUS_TICK_MS);
    }
    return 0;
}
