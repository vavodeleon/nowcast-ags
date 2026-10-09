package io.github.vavodeleon.nowcast.wear

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.ScrollView
import android.widget.TextView
import androidx.wear.tiles.TileService
import io.github.vavodeleon.nowcast.common.Cache
import io.github.vavodeleon.nowcast.common.Fuente
import io.github.vavodeleon.nowcast.common.Reloj
import io.github.vavodeleon.nowcast.common.Textos
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * La app del reloj al abrirla desde la lista de apps: lo mismo que la
 * tarjeta, en texto. Descarga en primer plano; si aquí llega el dato y en la
 * tarjeta no, el problema es la red en segundo plano y no la red en sí.
 */
class Principal : Activity() {
    private val alcance = MainScope()
    private lateinit var texto: TextView

    override fun onCreate(estado: Bundle?) {
        super.onCreate(estado)
        texto = TextView(this).apply {
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.BLACK)
            textSize = 15f
            gravity = Gravity.CENTER
            setPadding(36, 48, 36, 48)
        }
        setContentView(ScrollView(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(texto)
        })
        TrabajoReloj.programar(this)
    }

    override fun onResume() {
        super.onResume()
        mostrar(Cache.leer(this), "Bajando…")
        alcance.launch {
            val nuevo = Fuente.bajar()
            Cache.guardar(this@Principal, nuevo)
            mostrar(Cache.leer(this@Principal),
                    if (nuevo == null) "No se pudo bajar el dato." else "")
            TileService.getUpdater(this@Principal).requestUpdate(TarjetaLluvia::class.java)
        }
    }

    override fun onDestroy() {
        alcance.cancel()
        super.onDestroy()
    }

    private fun mostrar(r: Reloj?, nota: String) {
        texto.text = if (r == null) {
            "🛰️\nSin datos todavía\n\n$nota"
        } else {
            "${r.icono}\n${r.titular}\n\n${Textos.pct(r.p60)} en 1 h\n\n" +
                "${Textos.celda(r)}\n\n${Textos.sello(r)}" +
                (if (nota.isNotEmpty()) "\n\n$nota" else "")
        }
    }
}
