package ru.uzel.bridge.ui

import android.content.Intent
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.Send
import androidx.compose.material.icons.rounded.Bookmark
import androidx.compose.material.icons.rounded.BookmarkAdd
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.Delete
import androidx.compose.material.icons.rounded.EditNote
import androidx.compose.material.icons.rounded.Forum
import androidx.compose.material.icons.rounded.MoreVert
import androidx.compose.material.icons.rounded.PushPin
import androidx.compose.material.icons.rounded.RadioButtonUnchecked
import androidx.compose.material.icons.rounded.Share
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import ru.uzel.bridge.AppViewModel
import ru.uzel.bridge.core.ConversationCard
import ru.uzel.bridge.core.Note

@Composable
fun NotesScreen(vm: AppViewModel) {
    var draft by rememberSaveable { mutableStateOf("") }
    var editing by remember { mutableStateOf<Note?>(null) }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 96.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = draft,
                    onValueChange = { draft = it },
                    placeholder = { Text("Новая заметка…") },
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(24.dp),
                    maxLines = 5,
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.Sentences,
                        imeAction = ImeAction.Done,
                    ),
                    keyboardActions = KeyboardActions(onDone = {
                        vm.addNote(draft)
                        draft = ""
                    }),
                )
                Spacer(Modifier.size(8.dp))
                FilledIconButton(
                    onClick = {
                        if (draft.isBlank()) vm.addMoment() else vm.addNote(draft)
                        draft = ""
                    },
                    modifier = Modifier.size(56.dp),
                ) {
                    Icon(
                        if (draft.isBlank()) Icons.Rounded.BookmarkAdd else Icons.AutoMirrored.Rounded.Send,
                        contentDescription = if (draft.isBlank()) "Метка момента" else "Сохранить",
                    )
                }
            }
            Text(
                "Пустая — ставит метку момента: потом здесь появится разговор, который кулон записал в эту минуту.",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 8.dp, top = 6.dp, end = 64.dp),
            )
        }
        if (vm.notes.isEmpty()) {
            item {
                EmptyState(
                    Icons.Rounded.EditNote,
                    "Заметок пока нет",
                    "Пишите мысли на ходу или ставьте метки — плитка «Метка» есть и в быстрых настройках",
                )
            }
        }
        items(vm.notes, key = { it.id }) { note ->
            SwipeNote(
                note = note,
                link = vm.momentLinks[note.id],
                onDelete = { vm.deleteNote(note) },
                onToggleDone = { vm.toggleDone(note) },
                onTogglePin = { vm.togglePin(note) },
                onEdit = { editing = note },
                onOpenConversation = { vm.openConversation(it) },
            )
        }
    }

    editing?.let { note ->
        EditDialog(
            note = note,
            onDismiss = { editing = null },
            onSave = { text ->
                vm.editNote(note.id, text)
                editing = null
            },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SwipeNote(
    note: Note,
    link: ConversationCard?,
    onDelete: () -> Unit,
    onToggleDone: () -> Unit,
    onTogglePin: () -> Unit,
    onEdit: () -> Unit,
    onOpenConversation: (Long) -> Unit,
) {
    val state = rememberSwipeToDismissBoxState()
    SwipeToDismissBox(
        state = state,
        enableDismissFromStartToEnd = false,
        onDismiss = { if (it == SwipeToDismissBoxValue.EndToStart) onDelete() },
        backgroundContent = {
            if (state.dismissDirection != SwipeToDismissBoxValue.EndToStart) return@SwipeToDismissBox
            val color by animateColorAsState(
                if (state.targetValue == SwipeToDismissBoxValue.EndToStart) MaterialTheme.colorScheme.errorContainer
                else Color.Transparent,
                label = "swipe",
            )
            Surface(shape = RoundedCornerShape(24.dp), color = color, modifier = Modifier.fillMaxSize()) {
                Box(Modifier.fillMaxSize().padding(horizontal = 24.dp), contentAlignment = Alignment.CenterEnd) {
                    Icon(Icons.Rounded.Delete, "Удалить", tint = MaterialTheme.colorScheme.onErrorContainer)
                }
            }
        },
    ) {
        NoteCard(note, link, onDelete, onToggleDone, onTogglePin, onEdit, onOpenConversation)
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun NoteCard(
    note: Note,
    link: ConversationCard?,
    onDelete: () -> Unit,
    onToggleDone: () -> Unit,
    onTogglePin: () -> Unit,
    onEdit: () -> Unit,
    onOpenConversation: (Long) -> Unit,
) {
    val scheme = MaterialTheme.colorScheme
    val ctx = LocalContext.current
    var menu by remember { mutableStateOf(false) }
    // Подпись метки — в тон её фону: на закреплённой одно, на обычной другое.
    val momentInk = if (note.pinned) scheme.onSecondaryContainer else scheme.onTertiaryContainer
    Surface(
        shape = RoundedCornerShape(24.dp),
        color = when {
            note.pinned -> scheme.secondaryContainer
            note.moment -> scheme.tertiaryContainer
            else -> scheme.surfaceContainerHigh
        },
        modifier = Modifier.fillMaxWidth().combinedClickable(onClick = onEdit, onLongClick = { menu = true }),
    ) {
        Column(Modifier.padding(start = 8.dp, end = 4.dp, top = 8.dp, bottom = 12.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                IconButton(onClick = onToggleDone) {
                    Icon(
                        if (note.done) Icons.Rounded.CheckCircle else Icons.Rounded.RadioButtonUnchecked,
                        contentDescription = "Готово",
                        tint = if (note.done) scheme.primary else scheme.outline,
                    )
                }
                Column(Modifier.weight(1f).padding(top = 10.dp)) {
                    if (note.moment) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Rounded.Bookmark, null, tint = momentInk, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.size(4.dp))
                            Text(
                                "Метка · " + whenText(note.createdAt),
                                style = MaterialTheme.typography.labelLarge,
                                color = momentInk,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                        if (note.text.isNotBlank()) Spacer(Modifier.height(4.dp))
                    }
                    if (note.text.isNotBlank()) {
                        Text(
                            note.text,
                            style = MaterialTheme.typography.bodyLarge,
                            textDecoration = if (note.done) TextDecoration.LineThrough else null,
                            color = if (note.done) scheme.onSurfaceVariant else scheme.onSurface,
                        )
                    }
                    if (!note.moment) {
                        Spacer(Modifier.height(4.dp))
                        Text(whenText(note.createdAt), style = MaterialTheme.typography.labelSmall, color = scheme.onSurfaceVariant)
                    }
                }
                if (note.pinned) {
                    Icon(Icons.Rounded.PushPin, "Закреплена", tint = scheme.primary, modifier = Modifier.padding(top = 12.dp).size(18.dp))
                }
                Box {
                    IconButton(onClick = { menu = true }) { Icon(Icons.Rounded.MoreVert, "Ещё") }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(
                            text = { Text(if (note.pinned) "Открепить" else "Закрепить") },
                            leadingIcon = { Icon(Icons.Rounded.PushPin, null) },
                            onClick = { menu = false; onTogglePin() },
                        )
                        DropdownMenuItem(
                            text = { Text("Поделиться") },
                            leadingIcon = { Icon(Icons.Rounded.Share, null) },
                            onClick = {
                                menu = false
                                val text = buildString {
                                    if (note.moment) append("Метка ").append(whenText(note.createdAt)).append('\n')
                                    append(note.text)
                                    link?.summary?.let { append("\n\nРазговор ").append(link.started).append(": ").append(it) }
                                }.trim()
                                val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
                                ctx.startActivity(Intent.createChooser(send, "Поделиться заметкой"))
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("Удалить") },
                            leadingIcon = { Icon(Icons.Rounded.Delete, null) },
                            onClick = { menu = false; onDelete() },
                        )
                    }
                }
            }
            if (note.moment && link != null) {
                Surface(
                    onClick = { onOpenConversation(link.id) },
                    shape = RoundedCornerShape(16.dp),
                    color = scheme.surface.copy(alpha = 0.7f),
                    modifier = Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 8.dp),
                ) {
                    Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Surface(shape = CircleShape, color = scheme.primaryContainer) {
                            Icon(
                                Icons.Rounded.Forum, null,
                                tint = scheme.onPrimaryContainer,
                                modifier = Modifier.padding(8.dp).size(18.dp),
                            )
                        }
                        Spacer(Modifier.size(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(
                                "Разговор ${link.started}–${link.ended} · ${link.duration}",
                                style = MaterialTheme.typography.labelLarge,
                            )
                            Text(
                                link.summary ?: statusLabel(link.status),
                                style = MaterialTheme.typography.bodySmall,
                                color = scheme.onSurfaceVariant,
                                maxLines = 2,
                            )
                        }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun EditDialog(note: Note, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by remember { mutableStateOf(note.text) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (note.moment) "Метка · " + whenText(note.createdAt) else "Заметка") },
        text = {
            OutlinedTextField(
                value = text,
                onValueChange = { text = it },
                placeholder = { Text("Что было важно в этот момент?") },
                modifier = Modifier.fillMaxWidth(),
                minLines = 3,
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            )
        },
        confirmButton = { TextButton(onClick = { onSave(text) }) { Text("Сохранить") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } },
    )
}
