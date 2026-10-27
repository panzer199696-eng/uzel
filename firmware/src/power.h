#pragma once

#include <stdbool.h>

int power_init(void);

// Кулон подключён к USB (зарядка). Это и знак «можно сопрягать новое устройство».
bool power_vbus_present(void);
