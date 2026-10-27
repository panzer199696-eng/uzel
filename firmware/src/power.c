#include <zephyr/logging/log.h>
#include <zephyr/usb/usb_device.h>
#include <hal/nrf_power.h>
#include "power.h"

LOG_MODULE_REGISTER(power, CONFIG_LOG_DEFAULT_LEVEL);

bool power_vbus_present(void)
{
    // Аппаратный детектор VBUS. Раньше «на зарядке» выводилось из событий USB-стека,
    // и любое событие, кроме CONNECTED/DISCONNECTED, считалось подключённым USB.
    return nrf_power_usbregstatus_vbusdet_get(NRF_POWER);
}

int power_init(void)
{
    // USB нужен только для диагностики: живая прошивка видна в системе как
    // VID_2FE3/PID_0100 (CDC). Если стек уже поднят консолью — не ошибка.
    int err = usb_enable(NULL);
    if (err && err != -EALREADY)
    {
        LOG_WRN("USB не включился: %d", err);
    }
    return 0;
}
