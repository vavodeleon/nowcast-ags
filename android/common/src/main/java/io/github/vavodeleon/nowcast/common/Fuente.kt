package io.github.vavodeleon.nowcast.common

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * De dónde sale el dato, y qué hacer cuando la página se atasca.
 *
 * El 5/10/2026 GitHub tuvo una falla de Actions: el Pi siguió subiendo cada
 * 15 minutos, pero la página pública se quedó en las 13:30 más de una hora,
 * porque su despliegue no arrancaba. El repositorio sí tenía lo nuevo. Así
 * que si la copia de la página está vieja, se pide la del repositorio (raw),
 * que no pasa por ese despliegue, y se queda la más reciente de las dos.
 */
object Fuente {
    const val HOST = "vavodeleon.github.io"
    const val PAGINA = "https://$HOST/nowcast-ags/"
    private const val PAGES = "https://$HOST/nowcast-ags/reloj.json"
    private const val RAW =
        "https://raw.githubusercontent.com/vavodeleon/nowcast-ags/main/docs/reloj.json"

    suspend fun bajar(): String? = withContext(Dispatchers.IO) {
        // El parámetro esquiva las cachés intermedias: sin él, el CDN puede
        // servir la copia de hace diez minutos.
        val t = System.currentTimeMillis()
        val a = runCatching { leerUrl("$PAGES?t=$t") }.getOrNull()
        val ra = Reloj.de(a)
        if (ra != null && !ra.viejo()) return@withContext a
        val b = runCatching { leerUrl("$RAW?t=$t") }.getOrNull()
        elegir(a, b)
    }

    /** El más reciente de dos textos; el que no se pueda leer no cuenta. */
    fun elegir(a: String?, b: String?): String? {
        val ia = Reloj.de(a)?.instante()
        val ib = Reloj.de(b)?.instante()
        return when {
            ia == null && ib == null -> null
            ia == null -> b
            ib == null -> a
            ib.isAfter(ia) -> b
            else -> a
        }
    }

    private fun leerUrl(url: String): String {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 8_000
        c.readTimeout = 8_000
        c.useCaches = false
        c.setRequestProperty("Cache-Control", "no-cache")
        try {
            if (c.responseCode != 200) throw IOException("HTTP ${c.responseCode}")
            return c.inputStream.bufferedReader().use { it.readText() }
        } finally {
            c.disconnect()
        }
    }
}

/** Última lectura buena, para mostrar algo cuando no hay red. */
object Cache {
    private const val PREFS = "nowcast"
    private const val CLAVE = "reloj"

    fun texto(ctx: Context): String? =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(CLAVE, null)

    fun leer(ctx: Context): Reloj? = Reloj.de(texto(ctx))

    /** Guarda solo si es más nuevo: una respuesta atrasada no pisa la buena. */
    fun guardar(ctx: Context, nuevo: String?) {
        val mejor = Fuente.elegir(texto(ctx), nuevo) ?: return
        if (mejor === nuevo) {
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().putString(CLAVE, nuevo).apply()
        }
    }
}
