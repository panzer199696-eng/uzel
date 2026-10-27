package ru.uzel.bridge.ui

import android.content.Intent
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.EditNote
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Sensors
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.ViewDay
import androidx.compose.material.icons.rounded.EditNote
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Sensors
import androidx.compose.material.icons.rounded.ViewDay
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.ShortNavigationBar
import androidx.compose.material3.ShortNavigationBarItem
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import ru.uzel.bridge.AppViewModel
import ru.uzel.bridge.Tab

private data class TabSpec(val tab: Tab, val title: String, val icon: ImageVector, val selectedIcon: ImageVector)

private val TABS = listOf(
    TabSpec(Tab.HOME, "Кулон", Icons.Outlined.Sensors, Icons.Rounded.Sensors),
    TabSpec(Tab.DAY, "День", Icons.Outlined.ViewDay, Icons.Rounded.ViewDay),
    TabSpec(Tab.NOTES, "Заметки", Icons.Outlined.EditNote, Icons.Rounded.EditNote),
    TabSpec(Tab.SEARCH, "Поиск", Icons.Outlined.Search, Icons.Rounded.Search),
)

/** Широкий экран — раскрытый Find N5 и вообще всё от 600 dp. */
@Composable
fun isWide(): Boolean = LocalConfiguration.current.screenWidthDp >= 600

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun UzelApp(vm: AppViewModel, onStart: () -> Unit, openIntent: (Intent) -> Unit) {
    val snackbar = remember { SnackbarHostState() }
    val wide = isWide()

    LaunchedEffect(vm.message) {
        val text = vm.message ?: return@LaunchedEffect
        vm.message = null
        snackbar.showSnackbar(text, duration = SnackbarDuration.Short)
    }
    LaunchedEffect(vm.lastDeleted) {
        if (vm.lastDeleted == null) return@LaunchedEffect
        val r = snackbar.showSnackbar("Заметка удалена", actionLabel = "Вернуть", duration = SnackbarDuration.Short)
        if (r == SnackbarResult.ActionPerformed) vm.undoDelete()
    }

    if (vm.showSettings) {
        BackHandler { vm.showSettings = false }
        SettingsScreen(vm, openIntent, onBack = { vm.showSettings = false })
        return
    }
    BackHandler(enabled = vm.tab != Tab.HOME) { vm.tab = Tab.HOME }

    Row(Modifier.fillMaxSize()) {
        if (wide) {
            NavigationRail(
                modifier = Modifier.windowInsetsPadding(WindowInsets.safeDrawing),
                containerColor = MaterialTheme.colorScheme.surfaceContainer,
            ) {
                Box(Modifier.padding(vertical = 12.dp)) {}
                TABS.forEach { spec ->
                    NavigationRailItem(
                        selected = vm.tab == spec.tab,
                        onClick = { vm.tab = spec.tab },
                        icon = { Icon(if (vm.tab == spec.tab) spec.selectedIcon else spec.icon, null) },
                        label = { Text(spec.title) },
                    )
                }
            }
        }

        val scroll = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
        Scaffold(
            modifier = Modifier.weight(1f).nestedScroll(scroll.nestedScrollConnection),
            containerColor = MaterialTheme.colorScheme.surface,
            snackbarHost = { SnackbarHost(snackbar) },
            topBar = {
                LargeFlexibleTopAppBar(
                    title = { Text(TABS.first { it.tab == vm.tab }.title) },
                    subtitle = { Text(subtitleFor(vm)) },
                    actions = {
                        IconButton(onClick = { vm.showSettings = true }) {
                            Icon(Icons.Outlined.Settings, contentDescription = "Настройки")
                        }
                    },
                    scrollBehavior = scroll,
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface,
                        scrolledContainerColor = MaterialTheme.colorScheme.surfaceContainer,
                    ),
                )
            },
            bottomBar = {
                if (!wide) {
                    ShortNavigationBar {
                        TABS.forEach { spec ->
                            ShortNavigationBarItem(
                                selected = vm.tab == spec.tab,
                                onClick = { vm.tab = spec.tab },
                                icon = { Icon(if (vm.tab == spec.tab) spec.selectedIcon else spec.icon, null) },
                                label = { Text(spec.title) },
                            )
                        }
                    }
                }
            },
        ) { inner ->
            Box(
                Modifier.fillMaxSize().padding(inner),
                contentAlignment = Alignment.TopCenter,
            ) {
                AnimatedContent(
                    targetState = vm.tab,
                    transitionSpec = { fadeIn() togetherWith fadeOut() },
                    modifier = Modifier.widthIn(max = if (wide) 960.dp else 640.dp),
                    label = "tab",
                ) { tab ->
                    when (tab) {
                        Tab.HOME -> HomeScreen(vm, onStart, openIntent)
                        Tab.DAY -> DayScreen(vm)
                        Tab.NOTES -> NotesScreen(vm)
                        Tab.SEARCH -> SearchScreen(vm)
                    }
                }
            }
        }
    }

    if (vm.openedId != null) ConversationSheet(vm)
}

private fun subtitleFor(vm: AppViewModel): String = when (vm.tab) {
    Tab.HOME -> when {
        !vm.bridgeWanted -> "мост выключен"
        vm.snapshot.connected -> "кулон на связи"
        else -> "ищу кулон"
    }
    Tab.DAY -> vm.day.value?.let {
        plural(it.conversations.size, "разговор", "разговора", "разговоров") + " · " + it.speech + " речи"
    } ?: "загрузка…"
    Tab.NOTES -> notesCount(vm.notes.size)
    Tab.SEARCH -> "по всей памяти ядра"
}

private fun notesCount(n: Int): String =
    if (n == 0) "пока пусто" else plural(n, "заметка", "заметки", "заметок")
