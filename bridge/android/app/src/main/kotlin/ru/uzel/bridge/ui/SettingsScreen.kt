package ru.uzel.bridge.ui

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
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
import androidx.compose.material.icons.rounded.Language
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
import androidx.compose.ui.res.stringResource
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
import ru.uzel.bridge.R
import ru.uzel.bridge.core.CoreClient

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun SettingsScreen(vm: AppViewModel, openIntent: (Intent) -> Unit, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val res = ctx.resources
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(vm.prefs.coreUrl) }
    var uid by remember { mutableStateOf(vm.prefs.uid) }
    var token by remember { mutableStateOf(vm.prefs.apiToken) }
    var showToken by remember { mutableStateOf(false) }
    var check by remember { mutableStateOf<String?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.settings)) },
                navigationIcon = {
                    IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, stringResource(R.string.back)) }
                },
            )
        },
    ) { inner ->
        Box(Modifier.fillMaxSize().padding(inner), contentAlignment = Alignment.TopCenter) {
            Column(
                Modifier.widthIn(max = 640.dp).fillMaxWidth().verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp).padding(bottom = 32.dp),
            ) {
                SectionHeader(stringResource(R.string.section_connection))
                Group {
                    OutlinedTextField(
                        url, { url = it }, label = { Text(stringResource(R.string.core_address)) },
                        singleLine = true, modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    )
                    OutlinedTextField(uid, { uid = it }, label = { Text(stringResource(R.string.phone_uid)) }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(
                        token, { token = it }, label = { Text(stringResource(R.string.core_token)) },
                        singleLine = true, modifier = Modifier.fillMaxWidth(),
                        visualTransformation = if (showToken) VisualTransformation.None else PasswordVisualTransformation(),
                        trailingIcon = {
                            IconButton(onClick = { showToken = !showToken }) {
                                Icon(if (showToken) Icons.Rounded.VisibilityOff else Icons.Rounded.Visibility, stringResource(R.string.show))
                            }
                        },
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Button(onClick = {
                            vm.saveConnection(url, uid, token)
                            check = res.getString(R.string.saved)
                        }, shapes = ButtonDefaults.shapes()) { Text(stringResource(R.string.save)) }
                        FilledTonalButton(onClick = {
                            check = res.getString(R.string.checking)
                            scope.launch {
                                val ok = withContext(Dispatchers.IO) { CoreClient(url.trim(), uid).health() }
                                check = res.getString(if (ok) R.string.core_answers else R.string.core_unavailable)
                            }
                        }, shapes = ButtonDefaults.shapes()) { Text(stringResource(R.string.check)) }
                        check?.let { Text(it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    }
                }

                SectionHeader(stringResource(R.string.section_background))
                Group {
                    // Из модели: она перечитывает это при возврате на экран.
                    val battery = vm.batteryUnrestricted
                    CheckRow(
                        icon = Icons.Rounded.BatteryChargingFull,
                        ok = battery,
                        title = stringResource(if (battery) R.string.battery_ok else R.string.battery_limited),
                        text = stringResource(R.string.battery_text),
                        action = if (battery) null else stringResource(R.string.action_allow) to { openIntent(Keepalive.batteryIntent(ctx)) },
                    )
                    CheckRow(
                        icon = Icons.Rounded.RocketLaunch,
                        ok = null,
                        title = stringResource(R.string.coloros_title),
                        text = stringResource(R.string.coloros_text),
                        action = stringResource(R.string.open) to { openIntent(Keepalive.colorOsBatteryIntent(ctx)) },
                    )
                    CheckRow(
                        icon = Icons.Rounded.Lock,
                        ok = true,
                        title = stringResource(R.string.swipe_title),
                        text = stringResource(R.string.swipe_text),
                        action = null,
                    )
                    val notif = Keepalive.canPostNotifications(ctx)
                    CheckRow(
                        icon = Icons.Rounded.NotificationsOff,
                        ok = !notif,
                        title = stringResource(if (notif) R.string.notif_allowed else R.string.notif_off),
                        text = stringResource(if (notif) R.string.notif_allowed_text else R.string.notif_off_text),
                        action = stringResource(R.string.notifications) to { openIntent(Keepalive.notificationSettingsIntent(ctx)) },
                    )
                }

                SectionHeader(stringResource(R.string.section_firmware))
                Group {
                    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
                        if (uri != null) vm.updateFirmware(uri)
                    }
                    CheckRow(
                        icon = Icons.Rounded.SystemUpdate,
                        ok = null,
                        title = stringResource(R.string.ota_title),
                        text = stringResource(
                            R.string.ota_text,
                            vm.prefs.pendantAddress ?: stringResource(R.string.pendant_never),
                        ),
                        action = if (vm.firmwareBusy) null else stringResource(R.string.choose_firmware) to {
                            picker.launch(arrayOf("application/zip", "application/octet-stream", "*/*"))
                        },
                    )
                    vm.firmwareState?.let {
                        Text(it, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }

                SectionHeader(stringResource(R.string.section_look))
                Group {
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                        listOf(
                            stringResource(R.string.theme_system),
                            stringResource(R.string.theme_light),
                            stringResource(R.string.theme_dark),
                        ).forEachIndexed { i, label ->
                            SegmentedButton(
                                selected = vm.themeMode == i,
                                onClick = { vm.setTheme(i) },
                                shape = SegmentedButtonDefaults.itemShape(i, 3),
                            ) { Text(label, maxLines = 1) }
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(stringResource(R.string.dynamic_title), style = MaterialTheme.typography.titleSmall)
                            Text(stringResource(R.string.dynamic_text), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        Switch(checked = vm.dynamicColor, onCheckedChange = vm::setDynamic)
                    }
                }

                // Язык: по системе; на Android 13+ — свой язык для УЗЛА в системных настройках.
                SectionHeader(stringResource(R.string.section_language))
                Group {
                    CheckRow(
                        icon = Icons.Rounded.Language,
                        ok = null,
                        title = res.locale().getDisplayLanguage(res.locale()).replaceFirstChar { it.titlecase(res.locale()) },
                        text = stringResource(R.string.language_text),
                        action = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                            stringResource(R.string.language_action) to {
                                openIntent(
                                    Intent(Settings.ACTION_APP_LOCALE_SETTINGS)
                                        .setData(Uri.fromParts("package", ctx.packageName, null)),
                                )
                            }
                        } else {
                            null
                        },
                    )
                }

                Spacer(Modifier.height(24.dp))
                Text(
                    stringResource(R.string.about, BuildConfig.VERSION_NAME),
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
