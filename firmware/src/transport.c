#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/services/bas.h>
#include <zephyr/settings/settings.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/util.h>
#include "audio_packet.h"
#include "config.h"
#include "frame_queue.h"
#include "power.h"
#include "stats.h"
#include "transport.h"
#include "lib/battery/battery.h"

LOG_MODULE_REGISTER(transport, CONFIG_LOG_DEFAULT_LEVEL);

uzel_stats_t uzel_stats = {.version = 1};

//
// Сервисы
//
// Аудио — протокол Omi, его понимают оба моста:
//   19b10000-… сервис, 19b10001-… кадры (notify), 19b10002-… код кодека (read).
// Подписка на кадры требует шифрования: без сопряжения звук не получить.
//
static struct bt_uuid_128 audio_service_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x19B10000, 0xE8F2, 0x537E, 0x4F6C, 0xD104768A1214));
static struct bt_uuid_128 audio_data_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x19B10001, 0xE8F2, 0x537E, 0x4F6C, 0xD104768A1214));
static struct bt_uuid_128 audio_codec_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x19B10002, 0xE8F2, 0x537E, 0x4F6C, 0xD104768A1214));

static ssize_t audio_codec_read(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf, uint16_t len, uint16_t offset)
{
    const uint8_t codec_id = CODEC_ID;
    return bt_gatt_attr_read(conn, attr, buf, len, offset, &codec_id, sizeof(codec_id));
}

static void audio_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    LOG_INF("подписка на звук: %s", value == BT_GATT_CCC_NOTIFY ? "да" : "нет");
}

BT_GATT_SERVICE_DEFINE(audio_service,
    BT_GATT_PRIMARY_SERVICE(&audio_service_uuid),
    BT_GATT_CHARACTERISTIC(&audio_data_uuid.uuid, BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE, NULL, NULL, NULL),
    BT_GATT_CCC(audio_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE_ENCRYPT),
    BT_GATT_CHARACTERISTIC(&audio_codec_uuid.uuid, BT_GATT_CHRC_READ, BT_GATT_PERM_READ, audio_codec_read, NULL, NULL),
);
#define AUDIO_DATA_ATTR (&audio_service.attrs[2])

//
// Вход в загрузчик для обновления по воздуху (Nordic Legacy DFU, как у Omi):
//   00001530-… сервис, 00001531-… точка управления.
// Запись 0x01 <тип> (START_DFU) или 0x06 перезагружает кулон в загрузчик
// Adafruit в режиме BLE DFU (GPREGRET = 0xA8). Раньше это мог сделать любой
// телефон рядом, а загрузчик в этом режиме ждёт без таймаута — запись вставала
// до ручного сброса. Теперь только по зашифрованной связи с сопряжённым устройством.
//
static struct bt_uuid_128 dfu_service_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x00001530, 0x1212, 0xEFDE, 0x1523, 0x785FEABCD123));
static struct bt_uuid_128 dfu_control_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x00001531, 0x1212, 0xEFDE, 0x1523, 0x785FEABCD123));

#define DFU_MAGIC_OTA_RESET 0xA8

static void dfu_reset_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    NRF_POWER->GPREGRET = DFU_MAGIC_OTA_RESET;
    NVIC_SystemReset();
}
static K_WORK_DELAYABLE_DEFINE(dfu_reset_work, dfu_reset_handler);

static ssize_t dfu_control_write(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf, uint16_t len, uint16_t offset, uint8_t flags)
{
    const uint8_t *cmd = buf;
    const bool start_dfu = (len == 2 && cmd[0] == 0x01) || (len == 1 && cmd[0] == 0x06);
    if (offset != 0 || !start_dfu)
    {
        return BT_GATT_ERR(BT_ATT_ERR_VALUE_NOT_ALLOWED);
    }
    LOG_WRN("команда DFU: перезагрузка в загрузчик");
    if (cmd[0] == 0x01)
    {
        const uint8_t ack = 0x10; // как в прошивке Omi: клиент ждёт ответ до разрыва
        bt_gatt_notify(conn, attr, &ack, sizeof(ack));
    }
    // Не из обработчика ATT: ответ на запись должен успеть уйти в эфир.
    k_work_schedule(&dfu_reset_work, K_MSEC(300));
    return len;
}

static void dfu_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    ARG_UNUSED(attr);
    ARG_UNUSED(value);
}

BT_GATT_SERVICE_DEFINE(dfu_service,
    BT_GATT_PRIMARY_SERVICE(&dfu_service_uuid),
    BT_GATT_CHARACTERISTIC(&dfu_control_uuid.uuid, BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_WRITE_ENCRYPT, NULL, dfu_control_write, NULL),
    BT_GATT_CCC(dfu_ccc_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE_ENCRYPT),
);

//
// Статистика УЗЛА: 5a0e1000-… сервис, 5a0e1001-… счётчики (stats.h), чтение по шифрованию.
//
static struct bt_uuid_128 uzel_service_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x5A0E1000, 0x7A3E, 0x4B8E, 0x9F2A, 0x0D1E2F3A4B5C));
static struct bt_uuid_128 uzel_stats_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0x5A0E1001, 0x7A3E, 0x4B8E, 0x9F2A, 0x0D1E2F3A4B5C));

static frame_queue_t tx_queue;
static fq_slot_t tx_slots[TX_QUEUE_FRAMES];

static void count_bond(const struct bt_bond_info *info, void *user_data)
{
    ARG_UNUSED(info);
    (*(uint8_t *)user_data)++;
}

static uint8_t bond_count(void)
{
    uint8_t n = 0;
    bt_foreach_bond(BT_ID_DEFAULT, count_bond, &n);
    return n;
}

static ssize_t uzel_stats_read(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf, uint16_t len, uint16_t offset)
{
    uzel_stats.uptime_s = (uint32_t)(k_uptime_get() / 1000);
    uzel_stats.tx_queue_fill = (uint8_t)MIN(fq_count(&tx_queue), UINT8_MAX);
    uzel_stats.bonds = bond_count();
    return bt_gatt_attr_read(conn, attr, buf, len, offset, &uzel_stats, sizeof(uzel_stats));
}

BT_GATT_SERVICE_DEFINE(uzel_service,
    BT_GATT_PRIMARY_SERVICE(&uzel_service_uuid),
    BT_GATT_CHARACTERISTIC(&uzel_stats_uuid.uuid, BT_GATT_CHRC_READ, BT_GATT_PERM_READ_ENCRYPT, uzel_stats_read, NULL, NULL),
);

//
// Реклама
//
static const struct bt_data ad[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
    BT_DATA(BT_DATA_UUID128_ALL, audio_service_uuid.val, sizeof(audio_service_uuid.val)),
    BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
};

static const struct bt_data sd[] = {
    BT_DATA_BYTES(BT_DATA_UUID16_ALL, BT_UUID_16_ENCODE(BT_UUID_DIS_VAL)),
    BT_DATA(BT_DATA_UUID128_ALL, dfu_service_uuid.val, sizeof(dfu_service_uuid.val)),
};

//
// Соединение
//
static struct bt_conn *current_conn;
static struct k_spinlock conn_lock;
// MTU, под который кодер ограничивает кадр. 0 — пока неизвестен (связи нет или
// обмен MTU ещё не прошёл): тогда кадр кодируется под максимум, а неподходящий
// выбросится при отправке.
static atomic_t frame_mtu = ATOMIC_INIT(0);

// Ссылка на текущее соединение (bt_conn_unref после использования) или NULL.
static struct bt_conn *conn_get(void)
{
    k_spinlock_key_t key = k_spin_lock(&conn_lock);
    struct bt_conn *conn = current_conn ? bt_conn_ref(current_conn) : NULL;
    k_spin_unlock(&conn_lock, key);
    return conn;
}

static bool conn_is_secure(struct bt_conn *conn)
{
    return bt_conn_get_security(conn) >= BT_SECURITY_L2;
}

bool transport_is_connected(void)
{
    struct bt_conn *conn = conn_get();
    if (!conn)
    {
        return false;
    }
    const bool ok = conn_is_secure(conn);
    bt_conn_unref(conn);
    return ok;
}

// Чужое устройство без шифрования держит единственный слот соединения —
// через UNSECURED_CONN_TIMEOUT_S его отключаем, чтобы свой телефон смог зайти.
static void unsecured_timeout_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    struct bt_conn *conn = conn_get();
    if (conn && !conn_is_secure(conn))
    {
        LOG_WRN("соединение без шифрования: отключаю");
        bt_conn_disconnect(conn, BT_HCI_ERR_AUTH_FAIL);
    }
    if (conn)
    {
        bt_conn_unref(conn);
    }
}
static K_WORK_DELAYABLE_DEFINE(unsecured_timeout_work, unsecured_timeout_handler);

static void battery_handler(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(battery_work, battery_handler);

static void mtu_fallback_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    struct bt_conn *conn = conn_get();
    if (conn)
    {
        atomic_cas(&frame_mtu, 0, bt_gatt_get_mtu(conn));
        bt_conn_unref(conn);
    }
}
static K_WORK_DELAYABLE_DEFINE(mtu_fallback_work, mtu_fallback_handler);

static void battery_handler(struct k_work *work)
{
    uint16_t mv;
    uint8_t percent;
    if (battery_get_millivolt(&mv) == 0 && battery_get_percentage(&percent, mv) == 0)
    {
        bt_bas_set_battery_level(percent);
    }
    else
    {
        LOG_ERR("батарея не читается");
    }
    // Только пока есть связь: отмена при разрыве не останавливает обработчик,
    // который уже стоит внутри adc_read, — без проверки он перепланировал бы себя.
    struct bt_conn *conn = conn_get();
    if (conn)
    {
        bt_conn_unref(conn);
        k_work_reschedule(k_work_delayable_from_work(work), K_SECONDS(BATTERY_REFRESH_S));
    }
}

// Места под ключи кончились (CONFIG_BT_MAX_PAIRED), а новое устройство подключилось,
// пока кулон на USB: освобождаем одно место заранее. Место под ключи выделяется
// в SMP раньше, чем спрашивается pairing_accept, поэтому решать там поздно, а
// BT_KEYS_OVERWRITE_OLDEST нельзя — он вытеснял бы ключ владельца по запросу чужака.
struct bond_pick
{
    const bt_addr_le_t *peer;
    bool peer_bonded;
    bool found;
    bt_addr_le_t victim;
};

static void pick_bond(const struct bt_bond_info *info, void *user_data)
{
    struct bond_pick *pick = user_data;
    if (bt_addr_le_eq(&info->addr, pick->peer))
    {
        pick->peer_bonded = true;
    }
    else if (!pick->found)
    {
        pick->victim = info->addr;
        pick->found = true;
    }
}

static void free_bond_slot_handler(struct k_work *work)
{
    ARG_UNUSED(work);
    if (!power_vbus_present() || bond_count() < CONFIG_BT_MAX_PAIRED)
    {
        return;
    }
    struct bt_conn *conn = conn_get();
    if (!conn)
    {
        return;
    }
    struct bond_pick pick = {.peer = bt_conn_get_dst(conn)};
    bt_foreach_bond(BT_ID_DEFAULT, pick_bond, &pick);
    bt_conn_unref(conn);
    if (!pick.peer_bonded && pick.found)
    {
        LOG_WRN("места под ключи кончились: удаляю одно сопряжение ради нового устройства");
        bt_unpair(BT_ID_DEFAULT, &pick.victim);
    }
}
static K_WORK_DEFINE(free_bond_slot_work, free_bond_slot_handler);

static void on_connected(struct bt_conn *conn, uint8_t err)
{
    if (err)
    {
        // Неудачное подключение: соединения нет, ссылку брать не на что.
        LOG_WRN("подключение не удалось (0x%02x)", err);
        return;
    }

    // unref — вне спинлока: при обнулении счётчика стек может делать больше,
    // чем допустимо под блокировкой с запретом прерываний.
    struct bt_conn *new_ref = bt_conn_ref(conn);
    k_spinlock_key_t key = k_spin_lock(&conn_lock);
    struct bt_conn *old = current_conn;
    current_conn = new_ref;
    k_spin_unlock(&conn_lock, key);
    if (old)
    {
        bt_conn_unref(old);
    }

    uzel_stats.att_mtu = bt_gatt_get_mtu(conn);
    LOG_INF("подключено");
    // Обмен MTU запускает сама прошивка (BT_GATT_AUTO_UPDATE_MTU). Если клиент
    // так и не ответил — работаем с тем MTU, что есть.
    k_work_reschedule(&mtu_fallback_work, K_SECONDS(2));

    k_work_reschedule(&unsecured_timeout_work, K_SECONDS(UNSECURED_CONN_TIMEOUT_S));
    k_work_reschedule(&battery_work, K_MSEC(500));
    k_work_submit(&free_bond_slot_work);
}

static void on_disconnected(struct bt_conn *conn, uint8_t reason)
{
    LOG_INF("отключено (0x%02x)", reason);

    k_spinlock_key_t key = k_spin_lock(&conn_lock);
    struct bt_conn *old = (current_conn == conn) ? current_conn : NULL;
    if (old)
    {
        current_conn = NULL;
    }
    k_spin_unlock(&conn_lock, key);
    if (old)
    {
        bt_conn_unref(old);
    }

    atomic_set(&frame_mtu, 0);
    k_work_cancel_delayable(&mtu_fallback_work);
    k_work_cancel_delayable(&unsecured_timeout_work);
    k_work_cancel_delayable(&battery_work);
}

static void on_security_changed(struct bt_conn *conn, bt_security_t level, enum bt_security_err err)
{
    if (err)
    {
        LOG_WRN("шифрование не включилось: %d", err);
        return;
    }
    LOG_INF("уровень защиты %d", level);
    if (level >= BT_SECURITY_L2)
    {
        k_work_cancel_delayable(&unsecured_timeout_work);
    }
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
    .connected = on_connected,
    .disconnected = on_disconnected,
    .security_changed = on_security_changed,
};

static void on_mtu_updated(struct bt_conn *conn, uint16_t tx, uint16_t rx)
{
    ARG_UNUSED(conn);
    uzel_stats.att_mtu = MIN(tx, rx);
    atomic_set(&frame_mtu, uzel_stats.att_mtu);
    LOG_INF("ATT MTU %u", uzel_stats.att_mtu);
}

static struct bt_gatt_cb gatt_callbacks = {
    .att_mtu_updated = on_mtu_updated,
};

//
// Сопряжение: Just Works (кнопок и экрана нет), но новое устройство принимается,
// только пока кулон ни с кем не сопряжён или лежит на зарядке (есть VBUS).
// Так чужой телефон не может сопрячься и слушать звук, пока кулон на человеке.
//
static enum bt_security_err pairing_accept(struct bt_conn *conn, const struct bt_conn_pairing_feat *const feat)
{
    ARG_UNUSED(conn);
    ARG_UNUSED(feat);
    if (bond_count() == 0 || power_vbus_present())
    {
        return BT_SECURITY_ERR_SUCCESS;
    }
    LOG_WRN("сопряжение отклонено: кулон не на зарядке");
    return BT_SECURITY_ERR_PAIR_NOT_ALLOWED;
}

static void pairing_cancel(struct bt_conn *conn)
{
    ARG_UNUSED(conn);
}

static struct bt_conn_auth_cb auth_callbacks = {
    .pairing_accept = pairing_accept,
    .cancel = pairing_cancel,
};

static void pairing_complete(struct bt_conn *conn, bool bonded)
{
    ARG_UNUSED(conn);
    LOG_INF("сопряжено%s", bonded ? ", ключи сохранены" : "");
}

static void pairing_failed(struct bt_conn *conn, enum bt_security_err reason)
{
    ARG_UNUSED(conn);
    LOG_WRN("сопряжение не удалось: %d", reason);
}

static struct bt_conn_auth_info_cb auth_info_callbacks = {
    .pairing_complete = pairing_complete,
    .pairing_failed = pairing_failed,
};

//
// Отправка звука
//
static K_SEM_DEFINE(tx_kick, 0, 1);
static uint16_t packet_index;
static uint8_t packet_buf[FQ_MAX_FRAME + AUDIO_PACKET_HEADER];

size_t transport_max_frame_bytes(void)
{
    const atomic_val_t mtu = atomic_get(&frame_mtu);
    return mtu ? MIN((size_t)FQ_MAX_FRAME, audio_packet_max_frame((uint16_t)mtu)) : FQ_MAX_FRAME;
}

bool transport_queue_full(void)
{
    return fq_full(&tx_queue);
}

bool transport_queue_frame(const uint8_t *frame, size_t len)
{
    if (!fq_push(&tx_queue, frame, len))
    {
        return false;
    }
    k_sem_give(&tx_kick);
    return true;
}

// Отправляет самый старый кадр. Возвращает false, если отправлять нечего.
static bool send_one(struct bt_conn *conn)
{
    const fq_slot_t *frame = fq_peek(&tx_queue);
    if (!frame)
    {
        return false;
    }

    // MTU 0 — соединение уже разорвано: кадр остаётся в очереди до переподключения.
    // Раньше он выбрасывался как «не влезающий», и обрыв стирал все 2 с очереди.
    const uint16_t mtu = bt_gatt_get_mtu(conn);
    if (mtu == 0)
    {
        return false;
    }

    // Кадр закодирован под больший MTU, чем у этого соединения: мост не умеет
    // собирать кадр из кусков, поэтому такой кадр не режем, а выбрасываем.
    if (frame->len > audio_packet_max_frame(mtu))
    {
        fq_pop(&tx_queue);
        uzel_stats.frames_dropped_tx++;
        return true;
    }

    const size_t len = audio_packet_build(packet_buf, sizeof(packet_buf), packet_index, frame->data, frame->len);
    const int64_t deadline = k_uptime_get() + TX_RETRY_TOTAL_MS;
    int err;
    for (;;)
    {
        err = bt_gatt_notify(conn, AUDIO_DATA_ATTR, packet_buf, len);
        if ((err != -ENOMEM && err != -EAGAIN) || k_uptime_get() >= deadline)
        {
            break;
        }
        k_sleep(K_MSEC(5)); // буферы контроллера заняты — ждём, а не долбим
    }

    if (err == 0)
    {
        fq_pop(&tx_queue);
        packet_index++;
        uzel_stats.frames_sent++;
    }
    else if (err == -ENOTCONN)
    {
        // Связь пропала: кадр остаётся в очереди и уйдёт после переподключения.
        return false;
    }
    else
    {
        fq_pop(&tx_queue);
        uzel_stats.notify_failures++;
        LOG_DBG("кадр не отправлен: %d", err);
    }
    return true;
}

static bool ready_to_send(struct bt_conn *conn)
{
    return conn_is_secure(conn) && bt_gatt_is_subscribed(conn, AUDIO_DATA_ATTR, BT_GATT_CCC_NOTIFY);
}

#define PUSHER_STACK_SIZE 3072 // с запасом под немедленные логи отладочной сборки
K_THREAD_STACK_DEFINE(pusher_stack, PUSHER_STACK_SIZE);
static struct k_thread pusher_thread;

static void pusher_fn(void *p1, void *p2, void *p3)
{
    ARG_UNUSED(p1);
    ARG_UNUSED(p2);
    ARG_UNUSED(p3);

    for (;;)
    {
        struct bt_conn *conn = conn_get();
        if (!conn || !ready_to_send(conn))
        {
            // Нет связи или подписки: кадры копятся в очереди, поток спит.
            if (conn)
            {
                bt_conn_unref(conn);
            }
            k_sleep(K_MSEC(100));
            continue;
        }

        bool sent_any = false;
        while (send_one(conn))
        {
            sent_any = true;
        }
        bt_conn_unref(conn);

        if (!sent_any)
        {
            k_sem_take(&tx_kick, K_MSEC(200));
        }
    }
}

//
// Запуск
//
int transport_start(void)
{
    fq_init(&tx_queue, tx_slots, TX_QUEUE_FRAMES);

    int err = bt_conn_auth_cb_register(&auth_callbacks);
    if (err)
    {
        LOG_ERR("auth_cb: %d", err);
        return err;
    }
    err = bt_conn_auth_info_cb_register(&auth_info_callbacks);
    if (err)
    {
        LOG_ERR("auth_info_cb: %d", err);
        return err;
    }
    bt_gatt_cb_register(&gatt_callbacks);

    // Хранилище ключей поднимаем до bt_enable: если bt_enable сам споткнётся о
    // чужие данные в разделе, повторный вызов вернёт -EALREADY, и кулон уйдёт
    // в цикл перезагрузок без рекламы. Не поднялось — раздел стираем (теряются
    // только сопряжения) и пробуем ещё раз.
    err = settings_subsys_init();
    if (err)
    {
        LOG_ERR("хранилище настроек: %d, стираю раздел", err);
        const struct flash_area *fa;
        if (flash_area_open(FIXED_PARTITION_ID(storage_partition), &fa) == 0)
        {
            flash_area_erase(fa, 0, fa->fa_size);
            flash_area_close(fa);
        }
        err = settings_subsys_init();
        if (err)
        {
            LOG_ERR("хранилище настроек не поднялось: %d", err);
            return err;
        }
    }

    err = bt_enable(NULL);
    if (err)
    {
        LOG_ERR("Bluetooth не включился: %d", err);
        return err;
    }
    // Ключи сопряжения и подписки сопряжённых устройств — из флеша.
    settings_load();
    uzel_stats.bonds = bond_count();
    LOG_INF("Bluetooth включён, сопряжённых устройств: %u", uzel_stats.bonds);

    err = bt_le_adv_start(BT_LE_ADV_CONN, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
    if (err)
    {
        LOG_ERR("реклама не запустилась: %d", err);
        return err;
    }

    k_thread_create(&pusher_thread, pusher_stack, K_THREAD_STACK_SIZEOF(pusher_stack),
                    pusher_fn, NULL, NULL, NULL, K_PRIO_PREEMPT(7), 0, K_NO_WAIT);
    k_thread_name_set(&pusher_thread, "pusher");
    return 0;
}
