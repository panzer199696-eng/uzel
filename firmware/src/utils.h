#pragma once

#include <zephyr/logging/log.h>

// Скобки вокруг аргумента обязательны. В исходной версии было `if (!result)`,
// и ASSERT_TRUE(a == b) раскрывался в `(!a) == b` — проверка размера кодера
// Opus из-за этого не срабатывала никогда.
#define ASSERT_OK(expr)                                                \
    do                                                                 \
    {                                                                  \
        int _rc = (expr);                                              \
        if (_rc < 0)                                                   \
        {                                                              \
            LOG_ERR("%s:%d: %s = %d", __FILE__, __LINE__, #expr, _rc); \
            return _rc;                                                \
        }                                                              \
    } while (0)

#define ASSERT_TRUE(expr)                                         \
    do                                                            \
    {                                                             \
        if (!(expr))                                              \
        {                                                         \
            LOG_ERR("%s:%d: не выполнено %s", __FILE__, __LINE__, #expr); \
            return -1;                                            \
        }                                                         \
    } while (0)
