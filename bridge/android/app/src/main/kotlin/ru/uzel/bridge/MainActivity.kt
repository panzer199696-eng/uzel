package ru.uzel.bridge

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.PowerManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.DisposableEffect
import ru.uzel.bridge.ui.UzelApp
import ru.uzel.bridge.ui.UzelTheme
import ru.uzel.bridge.ui.isDark

/** Экран моста: Compose, Material 3 Expressive. */
class MainActivity : ComponentActivity() {

    private val vm: AppViewModel by viewModels()

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
            if (result.values.all { it }) {
                vm.startBridge()
                askBatteryOnce()
            } else {
                Toast.makeText(this, R.string.status_no_permission, Toast.LENGTH_LONG).show()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        handleIntent(intent)
        setContent {
            val dark = isDark(vm.themeMode)
            // Иконки системных панелей — под тему приложения, а не системную.
            DisposableEffect(dark) {
                val style = if (dark) SystemBarStyle.dark(Color.TRANSPARENT)
                else SystemBarStyle.light(Color.TRANSPARENT, Color.TRANSPARENT)
                enableEdgeToEdge(statusBarStyle = style, navigationBarStyle = style)
                onDispose {}
            }
            UzelTheme(vm.themeMode, vm.dynamicColor) {
                UzelApp(
                    vm = vm,
                    onStart = ::requestStart,
                    openIntent = ::openSafely,
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** Ярлык «Метка» с иконки приложения. */
    private fun handleIntent(intent: Intent?) {
        if (intent?.action == ACTION_MOMENT) {
            vm.addMoment()
            vm.tab = Tab.NOTES
        }
    }

    override fun onResume() {
        super.onResume()
        vm.onResume()
        // Мост включён раньше, а новой версии нужно ещё разрешение (локальная
        // сеть на Android 17) — спросить сразу, но не больше раза за открытие.
        val missing = missingPermissions()
        if (vm.bridgeWanted && missing.isNotEmpty() && !askedThisLaunch) {
            askedThisLaunch = true
            permissionLauncher.launch(missing.toTypedArray())
        }
    }

    private var askedThisLaunch = false

    override fun onPause() {
        vm.onPause()
        super.onPause()
    }

    /** Окно само открыло чужой экран (настройки, «Поделиться») — задачу не трогать. */
    private var launchedOwn = false

    override fun startActivity(intent: Intent?, options: Bundle?) {
        launchedOwn = true
        super.startActivity(intent, options)
    }

    @Deprecated("Activity API")
    override fun startActivityForResult(intent: Intent, requestCode: Int, options: Bundle?) {
        launchedOwn = true
        @Suppress("DEPRECATION")
        super.startActivityForResult(intent, requestCode, options)
    }

    override fun onStart() {
        super.onStart()
        launchedOwn = false
    }

    /**
     * Окно ушло с экрана: «Домой», «Недавние», другое приложение. Убираем свою
     * задачу целиком: на ColorOS смахивание карточки из «Недавних» убивает весь
     * пакет вместе со службой и запрещает её перезапуск (поймано 23.10.2026) —
     * нет карточки, нечего смахнуть. Служба при этом живёт, экран открывается
     * заново с иконки или плиткой. `onUserLeaveHint` для этого не годится:
     * ColorOS не зовёт его при переходе в «Недавние» (проверено).
     */
    override fun onStop() {
        super.onStop()
        val screenOn = getSystemService(PowerManager::class.java)?.isInteractive != false
        if (!isChangingConfigurations && !launchedOwn && screenOn && !isFinishing) {
            finishAndRemoveTask()
        }
    }

    private fun requestStart() {
        val missing = missingPermissions()
        if (missing.isEmpty()) {
            vm.startBridge()
            askBatteryOnce()
        } else {
            permissionLauncher.launch(missing.toTypedArray())
        }
    }

    /** Без снятия ограничений батареи сторож не сможет поднять мост из фона. */
    private fun askBatteryOnce() {
        if (!Keepalive.ignoresBatteryOptimizations(this)) openSafely(Keepalive.batteryIntent(this))
    }

    private fun openSafely(intent: Intent) {
        try {
            startActivity(intent)
        } catch (e: Exception) {
            try {
                startActivity(Keepalive.appDetailsIntent(this))
            } catch (_: Exception) {
                Toast.makeText(this, R.string.settings_not_found, Toast.LENGTH_LONG).show()
            }
        }
    }

    companion object {
        const val ACTION_MOMENT = "ru.uzel.bridge.action.MOMENT"
    }
}
