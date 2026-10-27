package ru.uzel.bridge

import android.content.Context

/** Настройки моста: адрес ядра, идентификатор телефона, автозапуск, вид. */
class Prefs(context: Context) {

    private val sp = context.getSharedPreferences("uzel_bridge", Context.MODE_PRIVATE)

    /** Базовый URL ядра, например http://192.168.1.10:8770. */
    var coreUrl: String
        get() = sp.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL
        set(value) = sp.edit().putString(KEY_URL, value).apply()

    /** Идентификатор, под которым ядро видит этот телефон. */
    var uid: String
        get() = sp.getString(KEY_UID, DEFAULT_UID) ?: DEFAULT_UID
        set(value) = sp.edit().putString(KEY_UID, value).apply()

    /**
     * Токен ядра (P11): ядро, слушающее не только localhost, без него
     * отвечает 401. Пустая строка — локальное ядро без авторизации.
     */
    var apiToken: String
        get() = sp.getString(KEY_TOKEN, "") ?: ""
        set(value) = sp.edit().putString(KEY_TOKEN, value).apply()

    /**
     * Мост должен работать: включён кнопкой и не выключен ею же.
     * По нему служба поднимается после перезагрузки и после того, как
     * система её убила (сторож в [Watchdog]).
     */
    var autoStart: Boolean
        get() = sp.getBoolean(KEY_AUTO, false)
        // commit, а не apply: сразу за записью службу могут убить.
        set(value) {
            sp.edit().putBoolean(KEY_AUTO, value).commit()
        }

    /** Тема: 0 — как в системе, 1 — светлая, 2 — тёмная. */
    var themeMode: Int
        get() = sp.getInt(KEY_THEME, 0)
        set(value) = sp.edit().putInt(KEY_THEME, value).apply()

    /** Цвета Material You из обоев. */
    var dynamicColor: Boolean
        get() = sp.getBoolean(KEY_DYNAMIC, true)
        set(value) = sp.edit().putBoolean(KEY_DYNAMIC, value).apply()

    /** MAC последнего подключённого кулона — по нему идёт обновление прошивки. */
    var pendantAddress: String?
        get() = sp.getString(KEY_PENDANT, null)
        set(value) = sp.edit().putString(KEY_PENDANT, value).apply()

    companion object {
        const val DEFAULT_URL = "http://192.168.1.10:8770"
        private const val KEY_PENDANT = "pendant_address"
        const val DEFAULT_UID = "phone-1"
        private const val KEY_URL = "core_url"
        private const val KEY_UID = "uid"
        private const val KEY_TOKEN = "api_token"
        private const val KEY_AUTO = "auto_start"
        private const val KEY_THEME = "theme_mode"
        private const val KEY_DYNAMIC = "dynamic_color"
    }
}
