package io.github.vavodeleon.nowcast.common

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime

/**
 * docs/reloj.json, tal como lo escribe nowcast/reloj.py en el Pi.
 *
 * Todo trae valor por omisión: si el Pi añade una clave, se ignora; si quita
 * una, la app sigue. "No sé" llega como null y se muestra como "—", nunca
 * como 0%, que se leería "no va a llover".
 */
@Serializable
data class Plazo(val min: Int, val p: Double? = null)

@Serializable
data class Celda(
    @SerialName("eta_min") val etaMin: Int? = null,
    val km: Int? = null,
    val desde: String? = null,
    val intensidad: Double? = null,
)

@Serializable
data class Reloj(
    val v: Int = 0,
    @SerialName("emitido_utc") val emitidoUtc: String? = null,
    val hora: String? = null,
    val icono: String = "🛰️",
    val titular: String = "Sin datos",
    val apoyo: String = "",
    val color: String = "muted",
    val p: List<Plazo> = emptyList(),
    val p60: Double? = null,
    val celda: Celda? = null,
    val giro: Boolean = false,
    @SerialName("nube_baja") val nubeBaja: Boolean? = null,
    val degradado: Boolean = false,
) {
    fun instante(): Instant? =
        runCatching { OffsetDateTime.parse(emitidoUtc).toInstant() }.getOrNull()

    fun edadMin(ahora: Instant = Instant.now()): Long? =
        instante()?.let { Duration.between(it, ahora).toMinutes() }

    /**
     * Mismo criterio que la página: más de 45 min sin publicar es que algo
     * se paró. El Pi corre cada 15, así que son dos corridas perdidas.
     */
    fun viejo(ahora: Instant = Instant.now()): Boolean =
        (edadMin(ahora) ?: Long.MAX_VALUE) > MINUTOS_VIEJO

    companion object {
        const val MINUTOS_VIEJO = 45L

        private val json = Json {
            ignoreUnknownKeys = true
            // Un null donde se esperaba texto toma el valor por omisión en
            // vez de tirar toda la lectura.
            coerceInputValues = true
        }

        fun de(texto: String?): Reloj? =
            texto?.let { runCatching { json.decodeFromString<Reloj>(it) }.getOrNull() }
    }
}
