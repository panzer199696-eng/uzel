package ru.uzel.bridge.ui

import android.content.Intent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.BatteryChargingFull
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.NotificationsOff
import androidx.compose.material.icons.rounded.RocketLaunch
import androidx.compose.material.icons.rounded.SystemUpdate
import androidx.compose.material.icons.rounded.Visibility
import androidx.compose.material.icons.rounded.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.uzel.bridge.AppViewModel
import ru.uzel.bridge.BuildConfig
import ru.uzel.bridge.Keepalive
import ru.uzel.bridge.core.CoreClient

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun SettingsScreen(vm: AppViewModel, openIntent: (Intent) -> Unit, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(vm.prefs.coreUrl) }
    var uid by remember { mutableStateOf(vm.prefs.uid) }
    var token by remember { mutableStateOf(vm.prefs.apiToken) }
    var showToken by remember { mutableStateOf(false) }
    var check by remember { mutableStateOf<String?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Настройки") },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Назад") }
                },
            )
        },
    ) { inner ->
        Box(Modifier.fillMaxSize().padding(inner), contentAlignment = Alignment.TopCenter) {
            Column(
                Modifier.widthIn(max = 640.dp).fillMaxWidth().verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp).padding(bottom = 32.dp),
            ) {
                SectionHeader("Подключение к ядру")
                Group {
                    OutlinedTextField(
                        url, { url = it }, label = { Text("Адрес ядра") },
                        singleLine = true, modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    )
                    OutlinedTextField(uid, { uid = it }, label = { Text("Имя телефона для ядра (uid)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(
                        token, { token = it }, label = { Text("Токен ядра") },
                        singleLine = true, modifier = Modifier.fillMaxWidth(),
                        visualTransformation = if (showToken) VisualTransformation.None else PasswordVisualTransformation(),
                        trailingIcon = {
                            IconButton(onClick = { showToken = !showToken }) {
                                Icon(if (showToken) Icons.Rounded.VisibilityOff else Icons.Rounded.Visibility, "Показать")
                            }
                        },
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Button(onClick = {
                            vm.saveConnection(url, uid, token)
                            check = "Сохранено"
                        }, shapes = ButtonDefaults.shapes()) { Text("Сохранить") }
                        FilledTonalButton(onClick = {
                            check = "Проверяю…"
                            scope.launch {
                                val ok = withContext(Dispatchers.IO) { CoreClient(url.trim(), uid).health() }
                                check = if (ok) "Ядро отвечает" else "Ядро недоступно"
                            }
                        }, shapes = ButtonDefaults.shapes()) { Text("Проверить") }
                        check?.let { Text(it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    }
                }

                SectionHeader("Работа в фоне")
                Group {
                    // Из модели: она перечитывает это при возврате на экран.
                    val battery = vm.batteryUnrestricted
                    CheckRow(
                        icon = Icons.Rounded.BatteryChargingFull,
                        ok = battery,
                        title = if (battery) "Батарея без ограничений" else "Батарея ограничивает мост",
                        text = "Нужно, чтобы сторож мог поднять мост, если система его закроет.",
                        action = if (battery) null else "Разрешить" to { openIntent(Keepalive.batteryIntent(ctx)) },
                    )
                    CheckRow(
                        icon = Icons.Rounded.RocketLaunch,
                        ok = null,
                        title = "Фоновая активность ColorOS",
                        text = "В «Использовании батареи» выберите «Разрешить работу в фоновом режиме» — " +
                            "тогда ColorOS не усыпит мост при долгой работе.",
                        action = "Открыть" to { openIntent(Keepalive.colorOsBatteryIntent(ctx)) },
                    )
                    CheckRow(
                        icon = Icons.Rounded.Lock,
                        ok = true,
                        title = "Нечего смахнуть",
                        text = "ColorOS при смахивании карточки из «Недавних» убивает приложение вместе с мостом. " +
                            "Поэтому УЗЕЛ убирает свою карточку, как только вы выходите: мост работает, " +
                            "экран открывается с иконки или плиткой «УЗЕЛ» в шторке.",
                        action = null,
                    )
                    val notif = Keepalive.canPostNotifications(ctx)
                    CheckRow(
                        icon = Icons.Rounded.NotificationsOff,
                        ok = !notif,
                        title = if (notif) "Уведомление разрешено" else "Без уведомления в шторке",
                        text = if (notif) {
                            "Служба держит тихое уведомление без значка. Чтобы убрать его совсем — выключите уведомления УЗЛА; мост работает и без них."
                        } else {
                            "Мост работает без значка в шторке. Видно его только в системном списке активных приложений."
                        },
                        action = "Уведомления" to { openIntent(Keepalive.notificationSettingsIntent(ctx)) },
                    )
                }

                SectionHeader("Прошивка кулона")
                Group {
                    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
                        if (uri != null) vm.updateFirmware(uri)
                    }
                    CheckRow(
                        icon = Icons.Rounded.SystemUpdate,
                        ok = null,
                        title = "Обновление по воздуху",
                        text = "Кулон ${vm.prefs.pendantAddress ?: "ещё не подключался"}. Выберите файл " +
                            "uzel-fw-<версия>-dfu.zip — мост на время заливки отключится и включится сам. " +
                            "Кулон должен быть сопряжён с этим телефоном.",
                        action = if (vm.firmwareBusy) null else "Выбрать прошивку" to {
                            picker.launch(arrayOf("application/zip", "application/octet-stream", "*/*"))
                        },
                    )
                    vm.firmwareState?.let {
                        Text(it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }

                SectionHeader("Вид")
                Group {
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                        listOf("Как в системе", "Светлая", "Тёмная").forEachIndexed { i, label ->
                            SegmentedButton(
                                selected = vm.themeMode == i,
                                onClick = { vm.setTheme(i) },
                                shape = SegmentedButtonDefaults.itemShape(i, 3),
                            ) { Text(label, maxLines = 1) }
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Цвета Material You", style = MaterialTheme.typography.titleSmall)
                            Text("Из обоев телефона; выключено — фирменная морская волна", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Switch(checked = vm.dynamicColor, onCheckedChange = vm::setDynamic)
                    }
                }

                Spacer(Modifier.height(24.dp))
                Text(
                    "УЗЕЛ ${BuildConfig.VERSION_NAME} · мост кулон → ядро без облака",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun Group(content: @Composable () -> Unit) {
    Surface(shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) { content() }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun CheckRow(icon: ImageVector, ok: Boolean?, title: String, text: String, action: Pair<String, () -> Unit>?) {
    val scheme = MaterialTheme.colorScheme
    Row(verticalAlignment = Alignment.Top) {
        Icon(
            when (ok) {
                true -> Icons.Rounded.CheckCircle
                false -> Icons.Rounded.ErrorOutline
                null -> icon
            },
            null,
            tint = when (ok) {
                true -> scheme.primary
                false -> scheme.error
                null -> scheme.onSurfaceVariant
            },
            modifier = Modifier.padding(top = 2.dp).size(24.dp),
        )
        Spacer(Modifier.size(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            Text(text, style = MaterialTheme.typography.bodySmall, color = scheme.onSurfaceVariant)
            if (action != null) {
                Spacer(Modifier.height(6.dp))
                FilledTonalButton(onClick = action.second, shapes = ButtonDefaults.shapes()) { Text(action.first) }
            }
        }
    }
}
