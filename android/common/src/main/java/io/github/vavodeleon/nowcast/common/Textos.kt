package io.github.vavodeleon.nowcast.common

import java.time.Instant
import kotlin.math.roundToInt

/** Colores del modo oscuro de la página (negro puro, para OLED). */
object Paleta {
    val FONDO = 0xFF000000.toInt()
    val TINTA = 0xFFE8ECF2.toInt()
    val TENUE = 0xFF95A0B3.toInt()
    val LINEA = 0xFF1D222B.toInt()
    /** Carril de las barras: se ve dónde estaría el 100%. */
    val CARRIL = 0xFF14181F.toInt()

    fun de(nombre: String): Int = when (nombre) {
        "rojo" -> 0xFFFF6B5E
        "naranja" -> 0xFFFF9147
        "amarillo" -> 0xFFF0C04A
        "azul" -> 0xFF5B9BF5
        "verde" -> 0xFF3ED88C
        "morado" -> 0xFFB57CE0
        else -> 0xFF95A0B3
    }.toInt()

    /** colorProb() de la página. Sin dato, una barra apagada. */
    fun prob(p: Double?): Int = when {
        p == null -> LINEA
        p >= .70 -> de("rojo")
        p >= .45 -> de("naranja")
        p >= .25 -> de("amarillo")
        else -> de("azul")
    }
}

object Textos {
    fun pct(p: Double?): String = p?.let { "${(it * 100).roundToInt()}%" } ?: "—"

    fun plazo(min: Int): String = when {
        min < 60 -> "$min'"
        min % 60 == 0 -> "${min / 60}h"
        else -> "${min / 60}½h"
    }

    /** "13:30", o "13:30 ⚠" si lleva más de 45 min sin publicarse. */
    fun sello(r: Reloj?, ahora: Instant = Instant.now()): String {
        if (r == null) return "sin datos"
        val h = r.hora ?: return "sin datos"
        return if (r.viejo(ahora)) "$h ⚠" else h
    }

    fun celda(r: Reloj?): String {
        val c = r?.celda
        val km = c?.km
        if (c == null || km == null) {
            return if (r?.giro == true) "Sistema girando, sin celda cerca"
                   else "Sin celdas acercándose"
        }
        val desde = c.desde?.let { ", del $it" } ?: ""
        val eta = c.etaMin?.let { " · ~$it min" } ?: ""
        return "Celda a $km km$desde$eta"
    }
}
