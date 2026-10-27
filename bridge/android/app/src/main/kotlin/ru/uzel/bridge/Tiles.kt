package ru.uzel.bridge

import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import android.widget.Toast

/**
 * Плитка «Метка» в быстрых настройках: одно касание — заметка-момент
 * с текущим временем. Потом в «Заметках» видно, какой разговор кулон
 * записал в эту минуту.
 */
class MomentTileService : TileService() {

    override fun onStartListening() {
        qsTile?.apply {
            state = Tile.STATE_INACTIVE
            label = getString(R.string.tile_moment)
            updateTile()
        }
    }

    override fun onClick() {
        addMoment()
        Toast.makeText(this, R.string.moment_saved, Toast.LENGTH_SHORT).show()
        qsTile?.apply {
            state = Tile.STATE_ACTIVE
            updateTile()
        }
    }
}

/** Плитка «УЗЕЛ»: включить и выключить мост, не открывая приложение. */
class BridgeTileService : TileService() {

    override fun onStartListening() = refresh()

    override fun onClick() {
        val prefs = Prefs(this)
        if (prefs.autoStart) {
            prefs.autoStart = false
            RecorderService.stop(this)
        } else if (missingPermissions().isEmpty()) {
            prefs.autoStart = true
            try {
                RecorderService.start(this)
            } catch (e: Exception) {
                Toast.makeText(this, R.string.open_app_to_start, Toast.LENGTH_LONG).show()
            }
        } else {
            Toast.makeText(this, R.string.open_app_to_start, Toast.LENGTH_LONG).show()
        }
        refresh(forceOn = prefs.autoStart)
    }

    private fun refresh(forceOn: Boolean? = null) {
        val on = forceOn ?: Prefs(this).autoStart
        qsTile?.apply {
            state = if (on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
            label = getString(R.string.tile_bridge)
            subtitle = getString(if (on) R.string.tile_on else R.string.tile_off)
            updateTile()
        }
    }
}
