package ru.uzel.bridge.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.Subject
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import ru.uzel.bridge.AppViewModel
import ru.uzel.bridge.R

/** Разговор целиком: сводка, задачи, факты и — по кнопке — расшифровка. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class, ExperimentalLayoutApi::class)
@Composable
fun ConversationSheet(vm: AppViewModel) {
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = false)
    val state = vm.detail
    val d = state.value
    val res = LocalContext.current.resources
    ModalBottomSheet(onDismissRequest = vm::closeConversation, sheetState = sheet) {
        LazyColumn(
            contentPadding = PaddingValues(start = 24.dp, end = 24.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            if (d == null) {
                item {
                    Column(Modifier.fillMaxWidth().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        if (state.loading) LoadingIndicator() else Text(state.error ?: stringResource(R.string.not_loaded))
                    }
                }
                return@LazyColumn
            }
            val c = d.card
            item {
                Text(
                    "${c.started}–${c.ended}",
                    style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    stringResource(R.string.conv_meta, c.duration, c.utterances, statusLabel(res, c.status)),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (c.tags.isNotEmpty() || c.participants.isNotEmpty()) {
                    Spacer(Modifier.height(10.dp))
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        (c.participants + c.tags).forEach { Tag(it) }
                    }
                }
            }
            c.summary?.let { summary ->
                item {
                    SectionHeader(stringResource(R.string.section_summary))
                    Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh) {
                        Text(summary, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(16.dp))
                    }
                }
            }
            if (d.tasks.isNotEmpty()) {
                item { SectionHeader(stringResource(R.string.section_tasks), trailing = d.tasks.size.toString()) }
                items(d.tasks, key = { "t" + it.id }) { TaskRow(it) {} }
            }
            if (d.facts.isNotEmpty()) {
                item { SectionHeader(stringResource(R.string.section_facts), trailing = d.facts.size.toString()) }
                items(d.facts, key = { "f" + it.id }) { FactRow(it) }
            }
            if (!vm.transcriptShown) {
                item {
                    Spacer(Modifier.height(16.dp))
                    FilledTonalButton(onClick = vm::showTranscript, shapes = ButtonDefaults.shapes(), modifier = Modifier.fillMaxWidth()) {
                        Icon(Icons.Rounded.Subject, null)
                        Spacer(Modifier.size(8.dp))
                        Text(stringResource(R.string.show_transcript))
                    }
                }
            } else {
                item {
                    SectionHeader(
                        stringResource(R.string.section_transcript),
                        trailing = if (state.loading) stringResource(R.string.loading) else null,
                    )
                }
                items(d.transcript, key = { "u" + it.id }) { u ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                        Text(
                            u.started.take(5),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.width(48.dp).padding(top = 2.dp),
                        )
                        Column(Modifier.weight(1f)) {
                            u.speaker?.let {
                                Text(it, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                            }
                            Text(u.text, style = MaterialTheme.typography.bodyMedium)
                        }
                        if (u.isPrivate) {
                            Icon(Icons.Rounded.Lock, stringResource(R.string.private_mark), tint = MaterialTheme.colorScheme.tertiary, modifier = Modifier.size(16.dp))
                        }
                    }
                }
            }
        }
    }
}
