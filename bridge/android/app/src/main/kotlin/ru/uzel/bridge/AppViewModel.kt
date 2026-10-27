package ru.uzel.bridge

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.uzel.bridge.core.ApiResult
import ru.uzel.bridge.core.BridgeSnapshot
import ru.uzel.bridge.core.ConversationCard
import ru.uzel.bridge.core.ConversationDetail
import ru.uzel.bridge.core.CoreApi
import ru.uzel.bridge.core.CoreClient
import ru.uzel.bridge.core.CoreStatus
import ru.uzel.bridge.core.DayFeed
import ru.uzel.bridge.core.FailReason
import ru.uzel.bridge.core.Note
import ru.uzel.bridge.core.PrivacyState
import ru.uzel.bridge.core.SearchResult
import ru.uzel.bridge.core.conversationAt
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

enum class Tab { HOME, DAY, NOTES, SEARCH }

/** Загружаемое значение: пока грузится, что пришло, почему не пришло. */
data class Loadable<T>(val value: T? = null, val loading: Boolean = false, val error: String? = null)

/** Что держит экран. Сеть — только на Dispatchers.IO. */
class AppViewModel(app: Application) : AndroidViewModel(app) {

    private val ctx get() = getApplication<Application>()
    val prefs = Prefs(app)

    var tab by mutableStateOf(Tab.HOME)
    var showSettings by mutableStateOf(false)

    // ---- мост ----
    var snapshot by mutableStateOf(BridgeSnapshot())
        private set
    var bridgeWanted by mutableStateOf(prefs.autoStart)
        private set
    var batteryUnrestricted by mutableStateOf(true)
        private set

    // ---- ядро ----
    var coreOnline by mutableStateOf<Boolean?>(null)
        private set
    var coreStatus by mutableStateOf<CoreStatus?>(null)
        private set
    var privacy by mutableStateOf<PrivacyState?>(null)
        private set
    var privacyBusy by mutableStateOf(false)
        private set

    // ---- день ----
    var date by mutableStateOf(LocalDate.now())
        private set
    var day by mutableStateOf(Loadable<DayFeed>())
        private set

    // ---- разговор ----
    var openedId by mutableStateOf<Long?>(null)
        private set
    var detail by mutableStateOf(Loadable<ConversationDetail>())
        private set
    var transcriptShown by mutableStateOf(false)
        private set

    // ---- заметки ----
    var notes by mutableStateOf<List<Note>>(emptyList())
        private set
    /** id заметки-метки → разговор, записанный в ту минуту. */
    val momentLinks = mutableStateMapOf<Long, ConversationCard>()

    // ---- поиск ----
    var query by mutableStateOf("")
    var search by mutableStateOf(Loadable<SearchResult>())
        private set

    // ---- настройки ----
    var themeMode by mutableStateOf(prefs.themeMode)
        private set
    var dynamicColor by mutableStateOf(prefs.dynamicColor)
        private set

    /** Сообщение для снэкбара (одноразовое). */
    var message by mutableStateOf<String?>(null)

    private var pollJob: Job? = null
    private val dayCache = HashMap<LocalDate, DayFeed>()

    private fun api() = CoreApi(prefs.coreUrl, prefs.apiToken)

    /** Причина отказа ядра — на языке телефона. */
    private fun ApiResult.Fail.text(): String = when (reason) {
        FailReason.UNAUTHORIZED -> ctx.getString(R.string.api_unauthorized)
        FailReason.NOT_FOUND -> ctx.getString(R.string.api_not_found)
        FailReason.HTTP_ERROR -> ctx.getString(R.string.api_http, code ?: 0)
        FailReason.OFFLINE -> ctx.getString(R.string.api_offline)
        FailReason.BAD_RESPONSE -> ctx.getString(R.string.api_bad_response)
    }

    /** Экран на виду: опрашиваем снимок службы и ядро. */
    fun onResume() {
        bridgeWanted = prefs.autoStart
        batteryUnrestricted = Keepalive.ignoresBatteryOptimizations(ctx)
        reloadNotes()
        if (bridgeWanted) Watchdog.ensureRunning(ctx, "экран открыт")
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            var tick = 0
            while (isActive) {
                snapshot = withContext(Dispatchers.IO) {
                    BridgeSnapshot.readFrom(Watchdog.snapshotFile(ctx))
                }
                if (tick % 10 == 0) refreshCore()
                tick++
                delay(1_000)
            }
        }
        if (day.value == null) loadDay()
    }

    fun onPause() {
        pollJob?.cancel()
    }

    // ---- мост ----

    fun startBridge() {
        prefs.autoStart = true
        bridgeWanted = true
        try {
            RecorderService.start(ctx)
        } catch (e: Exception) {
            message = ctx.getString(R.string.bridge_start_failed, e.message ?: "")
        }
    }

    fun stopBridge() {
        prefs.autoStart = false
        bridgeWanted = false
        RecorderService.stop(ctx)
    }

    // ---- ядро ----

    fun refreshCore() {
        viewModelScope.launch {
            val ok = withContext(Dispatchers.IO) { CoreClient(prefs.coreUrl, prefs.uid).health() }
            coreOnline = ok
            if (!ok) return@launch
            when (val r = withContext(Dispatchers.IO) { api().status() }) {
                is ApiResult.Ok -> {
                    coreStatus = r.value
                    privacy = r.value.privacy ?: privacy
                }
                is ApiResult.Fail -> if (r.code == 401) message = r.text()
            }
        }
    }

    fun mute(minutes: Int) = privacyCall { it.mute(minutes) }

    fun pausePrivacy() = privacyCall { it.pause() }

    fun unmute() = privacyCall { it.unmute() }

    private fun privacyCall(call: (CoreApi) -> ApiResult<PrivacyState>) {
        privacyBusy = true
        viewModelScope.launch {
            when (val r = withContext(Dispatchers.IO) { call(api()) }) {
                is ApiResult.Ok -> privacy = r.value
                is ApiResult.Fail -> message = r.text()
            }
            privacyBusy = false
        }
    }

    // ---- день ----

    fun shiftDay(days: Long) {
        val next = date.plusDays(days)
        if (next.isAfter(LocalDate.now())) return
        date = next
        loadDay()
    }

    fun goToday() {
        date = LocalDate.now()
        loadDay()
    }

    fun loadDay(force: Boolean = false) {
        val target = date
        val cached = dayCache[target]
        day = Loadable(cached, loading = true)
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) {
                api().day(if (target == LocalDate.now()) null else target.toString())
            }
            if (target != date) return@launch
            day = when (r) {
                is ApiResult.Ok -> {
                    dayCache[target] = r.value
                    Loadable(r.value)
                }
                is ApiResult.Fail -> Loadable(cached, error = r.text())
            }
            if (force) linkMoments()
        }
    }

    // ---- разговор ----

    fun openConversation(id: Long) {
        openedId = id
        transcriptShown = false
        loadDetail(id, withUtterances = false)
    }

    fun showTranscript() {
        val id = openedId ?: return
        transcriptShown = true
        loadDetail(id, withUtterances = true)
    }

    fun closeConversation() {
        openedId = null
        detail = Loadable()
    }

    private fun loadDetail(id: Long, withUtterances: Boolean) {
        detail = Loadable(detail.value?.takeIf { it.card.id == id }, loading = true)
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { api().conversation(id, withUtterances) }
            if (openedId != id) return@launch
            detail = when (r) {
                is ApiResult.Ok -> Loadable(r.value)
                is ApiResult.Fail -> Loadable(error = r.text())
            }
        }
    }

    // ---- заметки ----

    fun reloadNotes() {
        viewModelScope.launch {
            notes = withContext(Dispatchers.IO) { ctx.notesStore().all() }
            linkMoments()
        }
    }

    fun addNote(text: String) {
        if (text.isBlank()) return
        notesIo { it.add(text) }
    }

    fun addMoment(text: String = "") {
        notesIo { ctx.addMoment(text) }
        message = ctx.getString(R.string.moment_saved)
    }

    fun editNote(id: Long, text: String) = notesIo { it.update(id) { n -> n.copy(text = text.trim()) } }

    fun togglePin(note: Note) = notesIo { it.update(note.id) { n -> n.copy(pinned = !n.pinned) } }

    fun toggleDone(note: Note) = notesIo { it.update(note.id) { n -> n.copy(done = !n.done) } }

    var lastDeleted by mutableStateOf<Note?>(null)
        private set

    fun deleteNote(note: Note) {
        lastDeleted = note
        notesIo { it.delete(note.id) }
    }

    fun undoDelete() {
        val note = lastDeleted ?: return
        lastDeleted = null
        notesIo { it.restore(note) }
    }

    private fun notesIo(block: (ru.uzel.bridge.core.NotesStore) -> Any?) {
        viewModelScope.launch {
            notes = withContext(Dispatchers.IO) {
                val store = ctx.notesStore()
                block(store)
                store.all()
            }
            linkMoments()
        }
    }

    /** Подбирает к каждой метке разговор из ленты её дня (ленты кэшируются). */
    private fun linkMoments() {
        val moments = notes.filter { it.moment }
        if (moments.isEmpty()) return
        viewModelScope.launch {
            val zone = ZoneId.systemDefault()
            val byDay = moments.groupBy { Instant.ofEpochMilli(it.createdAt).atZone(zone).toLocalDate() }
            for ((d, list) in byDay) {
                val feed = dayCache[d] ?: withContext(Dispatchers.IO) {
                    (api().day(d.toString()) as? ApiResult.Ok)?.value
                }?.also { dayCache[d] = it } ?: continue
                for (note in list) {
                    conversationAt(feed.conversations, note.createdAt)?.let { momentLinks[note.id] = it }
                }
            }
        }
    }

    // ---- поиск ----

    fun runSearch() {
        val q = query.trim()
        if (q.isEmpty()) return
        search = Loadable(search.value, loading = true)
        viewModelScope.launch {
            val r = withContext(Dispatchers.IO) { api().search(q) }
            search = when (r) {
                is ApiResult.Ok -> Loadable(r.value)
                is ApiResult.Fail -> Loadable(error = r.text())
            }
        }
    }

    // ---- настройки ----

    fun saveConnection(url: String, uid: String, token: String) {
        prefs.coreUrl = url.trim().ifEmpty { Prefs.DEFAULT_URL }
        prefs.uid = uid.trim().ifEmpty { Prefs.DEFAULT_UID }
        prefs.apiToken = token.trim()
        dayCache.clear()
        refreshCore()
        loadDay()
        // Служба читает адрес при старте — перезапуск, чтобы подхватила.
        if (bridgeWanted) {
            RecorderService.stop(ctx)
            viewModelScope.launch {
                delay(1_500)
                startBridge()
            }
        }
    }

    fun setTheme(mode: Int) {
        prefs.themeMode = mode
        themeMode = mode
    }

    fun setDynamic(on: Boolean) {
        prefs.dynamicColor = on
        dynamicColor = on
    }

    // ---- прошивка кулона ----

    /** Что сейчас с обновлением прошивки; null — не запускалось. */
    var firmwareState by mutableStateOf<String?>(null)
        private set
    var firmwareBusy by mutableStateOf(false)
        private set

    /**
     * Обновление по воздуху из zip-пакета прошивки УЗЛА. Мост на это время
     * выключается (кулон держит одно соединение) и включается сам по окончании.
     */
    fun updateFirmware(zip: android.net.Uri) {
        val address = prefs.pendantAddress
        if (address == null) {
            message = ctx.getString(R.string.pendant_unknown)
            return
        }
        if (firmwareBusy) return
        firmwareBusy = true
        val bridgeWasOn = prefs.autoStart
        if (bridgeWasOn) {
            prefs.autoStart = false  // иначе сторож поднимет мост посреди заливки
            RecorderService.stop(ctx)
        }
        val update = FirmwareUpdate(ctx) { state -> firmwareState = state }
        update.onFinished = {
            firmwareBusy = false
            if (bridgeWasOn) startBridge()
        }
        viewModelScope.launch {
            delay(1_500)  // мост отпускает соединение не мгновенно
            try {
                update.start(address, zip)
            } catch (e: Exception) {
                firmwareState = ctx.getString(R.string.fw_not_started, e.message ?: "")
                update.onFinished?.invoke()
            }
        }
    }
}
