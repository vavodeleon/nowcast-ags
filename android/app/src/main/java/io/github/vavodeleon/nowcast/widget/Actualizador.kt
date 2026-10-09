package io.github.vavodeleon.nowcast.widget

import android.content.Context
import androidx.glance.appwidget.updateAll
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
 * Baja reloj.json y redibuja los widgets. Cada 15 minutos, como el Pi.
 *
 * Aunque la descarga falle se redibuja igual: así el "⚠" de dato viejo
 * aparece aunque no haya red, que es justo cuando hace falta.
 */
class Actualizador(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val texto = runCatching { Fuente.bajar() }.getOrNull()
        Cache.guardar(applicationContext, texto)
        WidgetChico().updateAll(applicationContext)
        WidgetMediano().updateAll(applicationContext)
        return Result.success()
    }

    companion object {
        private val conRed = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED).build()

        fun programar(ctx: Context) {
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
                "nowcast-periodico", ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<Actualizador>(15, TimeUnit.MINUTES)
                    .setConstraints(conRed).build())
        }

        fun ahora(ctx: Context) {
            WorkManager.getInstance(ctx).enqueueUniqueWork(
                "nowcast-ahora", ExistingWorkPolicy.REPLACE,
                OneTimeWorkRequestBuilder<Actualizador>().setConstraints(conRed).build())
        }
    }
}
