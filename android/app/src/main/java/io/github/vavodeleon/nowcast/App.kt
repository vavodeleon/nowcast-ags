package io.github.vavodeleon.nowcast

import android.app.Application
import io.github.vavodeleon.nowcast.widget.Actualizador

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        // KEEP: si ya estaba programado, no se reinicia el reloj de 15 min.
        Actualizador.programar(this)
    }
}
