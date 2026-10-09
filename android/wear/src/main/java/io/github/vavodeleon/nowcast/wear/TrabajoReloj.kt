package io.github.vavodeleon.nowcast.wear

import android.content.Context
import android.util.Log
import androidx.wear.tiles.TileService
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import io.github.vavodeleon.nowcast.common.Cache
import io.github.vavodeleon.nowcast.common.Fuente
import java.util.concurrent.TimeUnit

/**
 * La descarga del reloj, fuera de la tarjeta.
 *
 * La primera versión bajaba el dato dentro de la propia petición de la
 * tarjeta. En el Galaxy Watch8 nunca llegó nada: Wear OS restringe la red a
 * los servicios en segundo plano de apps que no se usan, y la tarjeta se
 * quedaba en "Sin datos" sin un solo error en el registro. WorkManager con
 * restricción de red es el camino que el sistema sí respeta. Al terminar
 * pide que se redibuje la tarjeta.
 */
class TrabajoReloj(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        Estado.marcarIntento(applicationContext)
        val texto = Fuente.bajar()
        Log.i(Fuente.TAG, "trabajo del reloj: ${if (texto == null) "sin respuesta" else "dato nuevo"}")
        Cache.guardar(applicationContext, texto)
        TileService.getUpdater(applicationContext).requestUpdate(TarjetaLluvia::class.java)
        return Result.success()
    }

    companion object {
        private val conRed = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED).build()

        fun programar(ctx: Context) {
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
                "reloj-periodico", ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<TrabajoReloj>(15, TimeUnit.MINUTES)
                    .setConstraints(conRed).build())
        }

        fun ahora(ctx: Context) {
            WorkManager.getInstance(ctx).enqueueUniqueWork(
                "reloj-ahora", ExistingWorkPolicy.KEEP,
                OneTimeWorkRequestBuilder<TrabajoReloj>().setConstraints(conRed).build())
        }
    }
}

/**
 * Cuándo se intentó bajar por última vez. Se mide el intento, no la edad del
 * dato: el dato del Pi puede tener 15 minutos recién bajado, y usar su edad
 * para decidir haría que cada redibujo pidiera otra descarga sin fin.
 */
object Estado {
    private const val PREFS = "reloj_estado"
    private const val CLAVE = "ultimo_intento"

    fun marcarIntento(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putLong(CLAVE, System.currentTimeMillis()).apply()
    }

    fun minutosDesdeIntento(ctx: Context): Long {
        val t = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(CLAVE, 0L)
        return (System.currentTimeMillis() - t) / 60_000
    }
}
