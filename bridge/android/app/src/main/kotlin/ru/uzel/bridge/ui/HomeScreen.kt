package ru.uzel.bridge.ui

import android.content.Intent
import android.content.res.Resources
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.BatteryAlert
import androidx.compose.material.icons.rounded.Battery5Bar
import androidx.compose.material.icons.rounded.BatteryFull
import androidx.compose.material.icons.rounded.BatteryUnknown
import androidx.compose.material.icons.rounded.BluetoothSearching
import androidx.compose.material.icons.rounded.BookmarkAdd
import androidx.compose.material.icons.rounded.CloudUpload
import androidx.compose.material.icons.rounded.GraphicEq
import androidx.compose.material.icons.rounded.Inventory2
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.LockOpen
import androidx.compose.material.icons.rounded.PowerSettingsNew
import androidx.compose.material.icons.rounded.Shield
import androidx.compose.material.icons.rounded.SignalCellularAlt
import androidx.compose.material.icons.rounded.Timer
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialShapes
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.toShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import ru.uzel.bridge.AppViewModel
import ru.uzel.bridge.Keepalive
import ru.uzel.bridge.R

/** Во что сейчас складывается состояние моста — для героя экрана. */
private enum class Mood(val title: Int, val icon: ImageVector) {
    OFF(R.string.mood_off, Icons.Rounded.PowerSettingsNew),
    STARTING(R.string.mood_starting, Icons.Rounded.Timer),
    SEARCHING(R.string.mood_searching, Icons.Rounded.BluetoothSearching),
    LISTENING(R.string.mood_listening, Icons.Rounded.GraphicEq),
    QUIET(R.string.mood_quiet, Icons.Rounded.SignalCellularAlt),
}

private fun moodOf(vm: AppViewModel, now: Long): Mood {
    val s = vm.snapshot
    return when {
        !vm.bridgeWanted -> Mood.OFF
        !s.isFresh(now) -> Mood.STARTING
        !s.connected -> Mood.SEARCHING
        now - s.lastFrameAt < 4_000 -> Mood.LISTENING
        else -> Mood.QUIET
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun HomeScreen(vm: AppViewModel, onStart: () -> Unit, openIntent: (Intent) -> Unit) {
    val now = System.currentTimeMillis()
    val mood = moodOf(vm, now)
    val wide = isWide()
    val scroll = rememberScrollState()

    Column(
        Modifier.fillMaxWidth().verticalScroll(scroll).padding(horizontal = 16.dp).padding(bottom = 96.dp),
    ) {
        if (wide) {
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Column(Modifier.weight(1f)) { Hero(vm, mood, onStart) }
                Column(Modifier.weight(1f)) {
                    Stats(vm, now)
                    CoreCard(vm)
                    KeepaliveHint(vm, openIntent)
                }
            }
        } else {
            Hero(vm, mood, onStart)
            Stats(vm, now)
            CoreCard(vm)
            KeepaliveHint(vm, openIntent)
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun Hero(vm: AppViewModel, mood: Mood, onStart: () -> Unit) {
    val scheme = MaterialTheme.colorScheme
    val container by animateColorAsState(
        when (mood) {
            Mood.OFF -> scheme.surfaceContainerHigh
            Mood.STARTING, Mood.SEARCHING -> scheme.secondaryContainer
            else -> scheme.primaryContainer
        },
        label = "hero",
    )
    val orbColor by animateColorAsState(
        when (mood) {
            Mood.OFF -> scheme.outline
            Mood.STARTING, Mood.SEARCHING -> scheme.secondary
            else -> scheme.primary
        },
        label = "orb",
    )
    val onOrb = if (mood == Mood.OFF) scheme.surface else scheme.onPrimary
    val res = LocalContext.current.resources

    Surface(shape = RoundedCornerShape(36.dp), color = container, modifier = Modifier.fillMaxWidth()) {
        Column(
            Modifier.padding(horizontal = 24.dp, vertical = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Orb(mood, orbColor, onOrb)
            Spacer(Modifier.height(20.dp))
            Text(stringResource(mood.title), style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.SemiBold)
            Spacer(Modifier.height(4.dp))
            Text(
                heroSubtitle(res, vm, mood),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
            val p = vm.privacy
            if (p != null && !p.recording) {
                Spacer(Modifier.height(12.dp))
                Surface(shape = CircleShape, color = scheme.tertiaryContainer) {
                    Row(
                        Modifier.padding(horizontal = 14.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(Icons.Rounded.Lock, null, Modifier.size(16.dp), tint = scheme.onTertiaryContainer)
                        Spacer(Modifier.size(6.dp))
                        Text(
                            privacyText(res, p.until),
                            style = MaterialTheme.typography.labelLarge,
                            color = scheme.onTertiaryContainer,
                        )
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (vm.bridgeWanted) {
                    OutlinedButton(
                        onClick = vm::stopBridge,
                        modifier = Modifier.height(56.dp),
                        shapes = ButtonDefaults.shapes(),
                    ) {
                        Icon(Icons.Rounded.PowerSettingsNew, null)
                        Spacer(Modifier.size(8.dp))
                        Text(stringResource(R.string.action_turn_off))
                    }
                    Button(
                        onClick = { vm.addMoment() },
                        modifier = Modifier.height(56.dp),
                        shapes = ButtonDefaults.shapes(),
                    ) {
                        Icon(Icons.Rounded.BookmarkAdd, null)
                        Spacer(Modifier.size(8.dp))
                        Text(stringResource(R.string.action_mark))
                    }
                } else {
                    Button(
                        onClick = onStart,
                        modifier = Modifier.height(64.dp).fillMaxWidth(0.8f),
                        shapes = ButtonDefaults.shapes(),
                    ) {
                        Icon(Icons.Rounded.PowerSettingsNew, null)
                        Spacer(Modifier.size(10.dp))
                        Text(stringResource(R.string.action_turn_on_bridge), style = MaterialTheme.typography.titleMedium)
                    }
                }
            }
        }
    }
}

/** Круг-«печенька» Material 3 Expressive: крутится и дышит, пока идёт звук. */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun Orb(mood: Mood, color: Color, onColor: Color) {
    val t = rememberInfiniteTransition(label = "orb")
    val spin by t.animateFloat(
        0f, 360f,
        infiniteRepeatable(tween(if (mood == Mood.LISTENING) 9_000 else 24_000, easing = LinearEasing)),
        label = "spin",
    )
    val pulse by t.animateFloat(
        0f, 1f,
        infiniteRepeatable(tween(1_800, easing = LinearEasing), RepeatMode.Restart),
        label = "pulse",
    )
    val moving = mood != Mood.OFF
    val shape = when (mood) {
        Mood.LISTENING -> MaterialShapes.Cookie9Sided
        Mood.QUIET -> MaterialShapes.Cookie12Sided
        Mood.SEARCHING, Mood.STARTING -> MaterialShapes.SoftBurst
        Mood.OFF -> MaterialShapes.Circle
    }.toShape()

    Box(Modifier.size(196.dp), contentAlignment = Alignment.Center) {
        if (mood == Mood.LISTENING || mood == Mood.SEARCHING) {
            // Расходящиеся волны: две, со сдвигом в полпериода.
            for (offset in listOf(0f, 0.5f)) {
                val p = (pulse + offset) % 1f
                Box(
                    Modifier
                        .size(132.dp)
                        .scale(1f + 0.45f * p)
                        .alpha(0.35f * (1f - p))
                        .clip(CircleShape)
                        .background(color),
                )
            }
        }
        Box(
            Modifier
                .size(148.dp)
                .rotate(if (moving) spin else 0f)
                .clip(shape)
                .background(color),
        )
        Icon(mood.icon, null, tint = onColor, modifier = Modifier.size(56.dp))
    }
}

private fun heroSubtitle(res: Resources, vm: AppViewModel, mood: Mood): String {
    val s = vm.snapshot
    return when (mood) {
        Mood.OFF -> res.getString(R.string.hero_off)
        Mood.STARTING -> res.getString(R.string.hero_starting)
        Mood.SEARCHING -> res.getString(R.string.hero_searching)
        Mood.LISTENING, Mood.QUIET -> buildString {
            append(res.getString(if (mood == Mood.LISTENING) R.string.hero_sound else R.string.hero_silence))
            if (s.startedAt > 0) append(res.getString(R.string.hero_since, hm(s.startedAt)))
            if (s.lastSentAt > 0) append(res.getString(R.string.hero_sent, agoText(res, s.lastSentAt)))
        }
    }
}

private fun privacyText(res: Resources, until: String?): String {
    if (until == null) return res.getString(R.string.privacy_on)
    val time = until.substringAfter('T').take(5)
    return res.getString(R.string.privacy_until, time)
}

@Composable
private fun Stats(vm: AppViewModel, now: Long) {
    val s = vm.snapshot
    val fresh = s.isFresh(now)
    val battery = s.batteryPercent.takeIf { fresh && s.connected }
    val scheme = MaterialTheme.colorScheme
    val locale = LocalContext.current.resources.locale()
    SectionHeader(stringResource(R.string.section_pendant))
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        StatTile(
            icon = when {
                battery == null -> Icons.Rounded.BatteryUnknown
                battery <= 15 -> Icons.Rounded.BatteryAlert
                battery >= 90 -> Icons.Rounded.BatteryFull
                else -> Icons.Rounded.Battery5Bar
            },
            value = battery?.let { "$it %" } ?: "—",
            label = stringResource(R.string.stat_battery),
            accent = if (battery != null && battery <= 15) scheme.error else scheme.primary,
            modifier = Modifier.weight(1f),
        )
        StatTile(
            icon = Icons.Rounded.SignalCellularAlt,
            value = if (fresh && s.packets + s.lost > 0) {
                String.format(locale, "%.1f %%", s.lossRatio * 100)
            } else "—",
            label = stringResource(R.string.stat_loss),
            accent = if (s.lossRatio > 0.05) scheme.error else scheme.primary,
            modifier = Modifier.weight(1f),
        )
    }
    Spacer(Modifier.height(12.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        StatTile(
            icon = Icons.Rounded.CloudUpload,
            value = if (fresh) s.sent.toString() else "—",
            label = stringResource(R.string.stat_sent),
            modifier = Modifier.weight(1f),
        )
        StatTile(
            icon = Icons.Rounded.Inventory2,
            value = if (fresh) s.spooled.toString() else "—",
            label = stringResource(R.string.stat_spooled),
            accent = if (s.spooled > 20) scheme.tertiary else scheme.primary,
            modifier = Modifier.weight(1f),
        )
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun CoreCard(vm: AppViewModel) {
    val scheme = MaterialTheme.colorScheme
    val online = vm.coreOnline
    val st = vm.coreStatus
    val res = LocalContext.current.resources
    SectionHeader(stringResource(R.string.section_core))
    Surface(shape = RoundedCornerShape(28.dp), color = scheme.surfaceContainerHigh, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(12.dp).clip(CircleShape).background(
                        when (online) {
                            true -> Color(0xFF2E9E5B)
                            false -> scheme.error
                            null -> scheme.outline
                        }
                    )
                )
                Spacer(Modifier.size(10.dp))
                Text(
                    when (online) {
                        true -> stringResource(R.string.core_online) +
                            (st?.version?.let { stringResource(R.string.core_version, it) } ?: "")
                        false -> stringResource(R.string.core_offline)
                        null -> stringResource(R.string.core_checking)
                    },
                    style = MaterialTheme.typography.titleMedium,
                )
            }
            if (online == true && st != null) {
                Spacer(Modifier.height(6.dp))
                Text(
                    stringResource(
                        R.string.core_memory,
                        res.count(R.plurals.conversations, st.conversations),
                        res.count(R.plurals.tasks, st.tasks),
                        res.count(R.plurals.facts, st.facts),
                        st.pending,
                    ),
                    style = MaterialTheme.typography.bodyMedium,
                    color = scheme.onSurfaceVariant,
                )
            }
            if (online == true) {
                Spacer(Modifier.height(16.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Rounded.Shield, null, tint = scheme.primary, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.size(8.dp))
                    Text(stringResource(R.string.privacy_title), style = MaterialTheme.typography.labelLarge)
                }
                Spacer(Modifier.height(8.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    val busy = vm.privacyBusy
                    FilledTonalButton(onClick = { vm.mute(10) }, enabled = !busy, shapes = ButtonDefaults.shapes()) { Text(stringResource(R.string.privacy_mute10)) }
                    FilledTonalButton(onClick = { vm.mute(60) }, enabled = !busy, shapes = ButtonDefaults.shapes()) { Text(stringResource(R.string.privacy_mute60)) }
                    FilledTonalButton(onClick = vm::pausePrivacy, enabled = !busy, shapes = ButtonDefaults.shapes()) {
                        Icon(Icons.Rounded.Lock, null, Modifier.size(18.dp))
                        Spacer(Modifier.size(6.dp))
                        Text(stringResource(R.string.privacy_private))
                    }
                    if (vm.privacy?.recording == false) {
                        Button(onClick = vm::unmute, enabled = !busy, shapes = ButtonDefaults.shapes()) {
                            Icon(Icons.Rounded.LockOpen, null, Modifier.size(18.dp))
                            Spacer(Modifier.size(6.dp))
                            Text(stringResource(R.string.privacy_lift))
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun KeepaliveHint(vm: AppViewModel, openIntent: (Intent) -> Unit) {
    if (!vm.bridgeWanted || vm.batteryUnrestricted) return
    val ctx = LocalContext.current
    Spacer(Modifier.height(16.dp))
    Surface(shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.tertiaryContainer, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp)) {
            Text(stringResource(R.string.keepalive_title), style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onTertiaryContainer)
            Spacer(Modifier.height(4.dp))
            Text(
                stringResource(R.string.keepalive_text),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onTertiaryContainer,
            )
            Spacer(Modifier.height(12.dp))
            Button(onClick = { openIntent(Keepalive.batteryIntent(ctx)) }, shapes = ButtonDefaults.shapes()) {
                Text(stringResource(R.string.action_allow))
            }
        }
    }
}
