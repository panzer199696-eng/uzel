package ru.uzel.bridge.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ChevronLeft
import androidx.compose.material.icons.rounded.ChevronRight
import androidx.compose.material.icons.rounded.Lightbulb
import androidx.compose.material.icons.rounded.RadioButtonUnchecked
import androidx.compose.material.icons.rounded.Forum
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import ru.uzel.bridge.AppViewModel
import ru.uzel.bridge.R
import ru.uzel.bridge.core.ConversationCard
import ru.uzel.bridge.core.FactItem
import ru.uzel.bridge.core.TaskItem
import java.time.LocalDate

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun DayScreen(vm: AppViewModel) {
    val state = vm.day
    val feed = state.value
    PullToRefreshBox(
        isRefreshing = state.loading && feed != null,
        onRefresh = { vm.loadDay(force = true) },
        modifier = Modifier.fillMaxSize(),
    ) {
        LazyColumn(
            Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 96.dp),
        ) {
            item { DateBar(vm) }
            state.error?.let { err -> item { ErrorBanner(err, Modifier.padding(top = 8.dp)) } }
            if (feed == null) {
                item {
                    if (state.loading) {
                        Column(Modifier.fillMaxWidth().padding(48.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                            LoadingIndicator()
                        }
                    }
                }
                return@LazyColumn
            }
            item { Totals(feed.conversations.size, feed.speech, feed.tasks.size, feed.facts.size) }
            if (feed.conversations.isEmpty()) {
                item {
                    EmptyState(
                        Icons.Rounded.Forum,
                        stringResource(R.string.day_empty_title),
                        stringResource(R.string.day_empty_text),
                    )
                }
            }
            if (feed.tasks.isNotEmpty()) {
                item { SectionHeader(stringResource(R.string.section_tasks), trailing = feed.tasks.size.toString()) }
                items(feed.tasks, key = { "t" + it.id }) { TaskRow(it) { id -> vm.openConversation(id) } }
            }
            if (feed.conversations.isNotEmpty()) {
                item { SectionHeader(stringResource(R.string.section_conversations), trailing = feed.conversations.size.toString()) }
                items(feed.conversations, key = { "c" + it.id }) { card ->
                    ConversationRow(card) { vm.openConversation(card.id) }
                }
            }
            if (feed.facts.isNotEmpty()) {
                item { SectionHeader(stringResource(R.string.section_facts), trailing = feed.facts.size.toString()) }
                items(feed.facts, key = { "f" + it.id }) { FactRow(it) }
            }
        }
    }
}

@Composable
private fun DateBar(vm: AppViewModel) {
    val today = vm.date == LocalDate.now()
    Row(
        Modifier.fillMaxWidth().padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        FilledTonalIconButton(onClick = { vm.shiftDay(-1) }) {
            Icon(Icons.Rounded.ChevronLeft, stringResource(R.string.prev_day))
        }
        Text(
            dayTitle(LocalContext.current.resources, vm.date),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.weight(1f).padding(horizontal = 12.dp),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        if (!today) TextButton(onClick = vm::goToday) { Text(stringResource(R.string.today)) }
        FilledTonalIconButton(onClick = { vm.shiftDay(1) }, enabled = !today) {
            Icon(Icons.Rounded.ChevronRight, stringResource(R.string.next_day))
        }
    }
}

@Composable
private fun Totals(conversations: Int, speech: String, tasks: Int, facts: Int) {
    Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        TotalPill("$conversations", stringResource(R.string.total_conversations), Modifier.weight(1f))
        TotalPill(speech, stringResource(R.string.total_speech), Modifier.weight(1f))
        TotalPill("$tasks", stringResource(R.string.total_tasks), Modifier.weight(1f))
        TotalPill("$facts", stringResource(R.string.total_facts), Modifier.weight(1f))
    }
}

@Composable
private fun TotalPill(value: String, label: String, modifier: Modifier) {
    Surface(modifier, shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh) {
        Column(Modifier.padding(vertical = 12.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(value, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold, maxLines = 1)
            Text(label, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ConversationRow(card: ConversationCard, onClick: () -> Unit) {
    val scheme = MaterialTheme.colorScheme
    val res = LocalContext.current.resources
    val analysed = card.status == "closed" && card.summary != null
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        // Колонка времени — как в ленте календаря.
        Column(Modifier.width(56.dp).padding(top = 14.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(card.started, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
            Text(card.ended, style = MaterialTheme.typography.labelSmall, color = scheme.onSurfaceVariant)
        }
        Surface(
            onClick = onClick,
            shape = RoundedCornerShape(24.dp),
            color = if (analysed) scheme.surfaceContainerHigh else scheme.surfaceContainerLow,
            modifier = Modifier.weight(1f),
        ) {
            Column(Modifier.padding(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        card.duration + " · " + statusLabel(res, card.status),
                        style = MaterialTheme.typography.labelMedium,
                        color = if (analysed) scheme.primary else scheme.onSurfaceVariant,
                        modifier = Modifier.weight(1f),
                    )
                    if (card.tasks > 0) CountBadge(stringResource(R.string.badge_tasks, card.tasks))
                    if (card.facts > 0) {
                        Spacer(Modifier.size(6.dp))
                        CountBadge(stringResource(R.string.badge_facts, card.facts))
                    }
                }
                Spacer(Modifier.height(6.dp))
                Text(
                    card.summary ?: stringResource(R.string.no_summary, card.utterances),
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (card.summary != null) scheme.onSurface else scheme.onSurfaceVariant,
                    maxLines = 4,
                    overflow = TextOverflow.Ellipsis,
                )
                if (card.tags.isNotEmpty() || card.participants.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        (card.participants + card.tags).take(6).forEach { Tag(it) }
                    }
                }
            }
        }
    }
}

@Composable
private fun CountBadge(text: String) {
    Surface(shape = CircleShape, color = MaterialTheme.colorScheme.secondaryContainer) {
        Text(
            text,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSecondaryContainer,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
        )
    }
}

@Composable
fun Tag(text: String) {
    Surface(shape = RoundedCornerShape(8.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
        Text(
            text,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
        )
    }
}

@Composable
fun TaskRow(task: TaskItem, onOpen: (Long) -> Unit) {
    val scheme = MaterialTheme.colorScheme
    Surface(
        shape = RoundedCornerShape(20.dp),
        color = scheme.surfaceContainerHigh,
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp)
            .clickable(enabled = task.conversationId != null) { task.conversationId?.let(onOpen) },
    ) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.Top) {
            Icon(Icons.Rounded.RadioButtonUnchecked, null, tint = scheme.primary, modifier = Modifier.size(22.dp))
            Spacer(Modifier.size(12.dp))
            Column(Modifier.weight(1f)) {
                Text(task.text, style = MaterialTheme.typography.bodyLarge)
                val who = task.assignee?.let { stringResource(R.string.task_who, it) }
                val due = task.due?.let { stringResource(R.string.task_due, it) }
                val meta = listOfNotNull(who, due)
                if (meta.isNotEmpty()) {
                    Text(meta.joinToString(" · "), style = MaterialTheme.typography.labelMedium, color = scheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
fun FactRow(fact: FactItem) {
    val scheme = MaterialTheme.colorScheme
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp, horizontal = 4.dp), verticalAlignment = Alignment.Top) {
        Icon(Icons.Rounded.Lightbulb, null, tint = scheme.tertiary, modifier = Modifier.size(20.dp))
        Spacer(Modifier.size(12.dp))
        Text(fact.text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
        fact.said?.let {
            Spacer(Modifier.size(8.dp))
            Text(it, style = MaterialTheme.typography.labelSmall, color = scheme.onSurfaceVariant)
        }
    }
}
