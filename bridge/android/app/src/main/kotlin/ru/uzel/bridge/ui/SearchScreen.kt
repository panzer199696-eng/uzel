package ru.uzel.bridge.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.ManageSearch
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import ru.uzel.bridge.AppViewModel
import ru.uzel.bridge.core.SearchHit

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun SearchScreen(vm: AppViewModel) {
    val state = vm.search
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 96.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            OutlinedTextField(
                value = vm.query,
                onValueChange = { vm.query = it },
                placeholder = { Text("Что обсуждали про…") },
                leadingIcon = { Icon(Icons.Rounded.Search, null) },
                trailingIcon = {
                    if (vm.query.isNotEmpty()) {
                        IconButton(onClick = { vm.query = "" }) { Icon(Icons.Rounded.Close, "Очистить") }
                    }
                },
                singleLine = true,
                shape = RoundedCornerShape(28.dp),
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { vm.runSearch() }),
            )
        }
        state.error?.let { item { ErrorBanner(it) } }
        if (state.loading) {
            item {
                Column(Modifier.fillMaxWidth().padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    LoadingIndicator()
                }
            }
        }
        val result = state.value
        if (result == null && !state.loading) {
            item {
                EmptyState(
                    Icons.Rounded.ManageSearch,
                    "Поиск по всему, что слышал кулон",
                    "Ищет по смыслу, а не только по словам, если ядро держит смысловой индекс",
                )
            }
        }
        if (result != null && !state.loading) {
            item {
                Text(
                    if (result.hits.isEmpty()) "Ничего не нашлось" else "Найдено ${result.hits.size} — ${result.origin}",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 4.dp, top = 8.dp),
                )
            }
            items(result.hits, key = { it.utteranceId }) { hit ->
                HitCard(hit, vm.query) { hit.conversationId?.let(vm::openConversation) }
            }
        }
    }
}

@Composable
private fun HitCard(hit: SearchHit, query: String, onClick: () -> Unit) {
    val scheme = MaterialTheme.colorScheme
    Surface(
        onClick = onClick,
        enabled = hit.conversationId != null,
        shape = RoundedCornerShape(24.dp),
        color = scheme.surfaceContainerHigh,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    hit.started + (hit.speaker?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.labelMedium,
                    color = scheme.primary,
                    modifier = Modifier.weight(1f),
                )
                if (hit.isPrivate) {
                    Icon(Icons.Rounded.Lock, "Приватное", tint = scheme.tertiary, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.size(4.dp))
                }
            }
            Spacer(Modifier.size(4.dp))
            Text(highlight(hit.text, query), style = MaterialTheme.typography.bodyMedium, maxLines = 5)
        }
    }
}

/** Подсветка слов запроса в тексте реплики. */
@Composable
private fun highlight(text: String, query: String) = buildAnnotatedString {
    val words = query.lowercase().split(' ').filter { it.length >= 3 }.map { it.take(5) }
    if (words.isEmpty()) {
        append(text)
        return@buildAnnotatedString
    }
    val lower = text.lowercase()
    var i = 0
    while (i < text.length) {
        val match = words.mapNotNull { w -> lower.indexOf(w, i).takeIf { it >= 0 }?.let { it to w } }.minByOrNull { it.first }
        if (match == null) {
            append(text.substring(i))
            break
        }
        val (at, w) = match
        append(text.substring(i, at))
        // До конца слова, чтобы подсветить «кирпича» по запросу «кирпич».
        var end = at + w.length
        while (end < text.length && text[end].isLetter()) end++
        withStyle(SpanStyle(fontWeight = FontWeight.Bold, color = MaterialTheme.colorScheme.primary)) {
            append(text.substring(at, end))
        }
        i = end
    }
}
