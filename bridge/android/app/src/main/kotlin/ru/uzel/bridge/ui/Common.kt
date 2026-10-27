package ru.uzel.bridge.ui

import android.content.res.Resources
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import ru.uzel.bridge.R
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

// Строки и шаблоны дат — из ресурсов (values/ — английский, values-ru/ — русский),
// язык — системный или выбранный для приложения (Android 13+).
private val HM = DateTimeFormatter.ofPattern("HH:mm")

/** Язык ресурсов приложения: при выборе языка для УЗЛА он может не совпадать с системным. */
fun Resources.locale(): Locale = configuration.locales[0] ?: Locale.getDefault()

private fun Resources.dateFormat(id: Int) = DateTimeFormatter.ofPattern(getString(id), locale())

fun dayTitle(res: Resources, d: LocalDate): String {
    val today = LocalDate.now()
    val day = d.format(res.dateFormat(R.string.fmt_day))
    return when (d) {
        today -> res.getString(R.string.day_today, day)
        today.minusDays(1) -> res.getString(R.string.day_yesterday, day)
        else -> d.format(res.dateFormat(R.string.fmt_weekday)).replaceFirstChar { it.titlecase(res.locale()) }
    }
}

fun hm(ms: Long): String = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).format(HM)

/** «сегодня 14:32», «вчера 09:10», «21 октября 18:00». */
fun whenText(res: Resources, ms: Long): String {
    val zdt = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault())
    val d = zdt.toLocalDate()
    val today = LocalDate.now()
    val time = zdt.format(HM)
    return when (d) {
        today -> res.getString(R.string.when_today, time)
        today.minusDays(1) -> res.getString(R.string.when_yesterday, time)
        else -> d.format(res.dateFormat(R.string.fmt_day)) + " " + time
    }
}

/** «12 с назад», «5 мин назад». */
fun agoText(res: Resources, ms: Long, now: Long = System.currentTimeMillis()): String {
    val s = ((now - ms) / 1000).coerceAtLeast(0)
    return when {
        s < 5 -> res.getString(R.string.ago_now)
        s < 60 -> res.getString(R.string.ago_seconds, s.toInt())
        s < 3600 -> res.getString(R.string.ago_minutes, (s / 60).toInt())
        else -> res.getString(R.string.ago_hours, (s / 3600).toInt())
    }
}

/** 1 разговор, 2 разговора, 5 разговоров — по правилам языка ресурсов. */
fun Resources.count(id: Int, n: Int): String = getQuantityString(id, n, n)

/** Статус разговора в ядре → по-человечески. */
fun statusLabel(res: Resources, status: String): String = when (status) {
    "open" -> res.getString(R.string.status_open)
    "closed" -> res.getString(R.string.status_closed)
    "thin" -> res.getString(R.string.status_thin)
    "noise" -> res.getString(R.string.status_noise)
    "nlu_failed" -> res.getString(R.string.status_nlu_failed)
    else -> status
}

@Composable
fun SectionHeader(text: String, modifier: Modifier = Modifier, trailing: String? = null) {
    Row(
        modifier.fillMaxWidth().padding(start = 4.dp, end = 4.dp, top = 20.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text,
            style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.primary,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.weight(1f),
        )
        if (trailing != null) {
            Text(trailing, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Плитка с цифрой: батарея кулона, потери, отправлено, очередь. */
@Composable
fun StatTile(
    icon: ImageVector,
    value: String,
    label: String,
    modifier: Modifier = Modifier,
    accent: Color = MaterialTheme.colorScheme.primary,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(24.dp),
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(icon, null, tint = accent, modifier = Modifier.size(22.dp))
            Text(value, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
fun EmptyState(icon: ImageVector, title: String, text: String, modifier: Modifier = Modifier) {
    Column(
        modifier.fillMaxWidth().padding(horizontal = 32.dp, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Surface(shape = RoundedCornerShape(28.dp), color = MaterialTheme.colorScheme.secondaryContainer) {
            Icon(
                icon, null,
                tint = MaterialTheme.colorScheme.onSecondaryContainer,
                modifier = Modifier.padding(20.dp).size(36.dp),
            )
        }
        Text(title, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
        Text(
            text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}

/** Плашка-ошибка («нет связи с ядром») поверх устаревших данных. */
@Composable
fun ErrorBanner(text: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        color = MaterialTheme.colorScheme.errorContainer,
    ) {
        Text(
            text,
            color = MaterialTheme.colorScheme.onErrorContainer,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
        )
    }
}
