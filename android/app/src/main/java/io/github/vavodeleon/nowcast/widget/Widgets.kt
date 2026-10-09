package io.github.vavodeleon.nowcast.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.cornerRadius
import androidx.glance.appwidget.provideContent
import androidx.glance.background
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.width
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import io.github.vavodeleon.nowcast.MainActivity
import io.github.vavodeleon.nowcast.common.Cache
import io.github.vavodeleon.nowcast.common.Paleta
import io.github.vavodeleon.nowcast.common.Reloj
import io.github.vavodeleon.nowcast.common.Textos

private fun c(argb: Int) = ColorProvider(Color(argb))

private val fondo = GlanceModifier
    .fillMaxSize()
    .background(c(Paleta.FONDO))
    .cornerRadius(16.dp)

/* ------------------------------------------------------------ chico 2x1 */

class WidgetChico : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val r = Cache.leer(context)
        provideContent { Chico(r) }
    }
}

class ReceptorChico : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = WidgetChico()
    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        Actualizador.programar(context)
        Actualizador.ahora(context)
    }
}

@Composable
private fun Chico(r: Reloj?) {
    Row(
        modifier = fondo.padding(horizontal = 12.dp)
            .clickable(actionStartActivity<MainActivity>()),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(r?.icono ?: "🛰️", style = TextStyle(fontSize = 26.sp))
        Spacer(GlanceModifier.width(10.dp))
        Column {
            Text(
                Textos.pct(r?.p60),
                style = TextStyle(color = c(Paleta.prob(r?.p60)),
                                  fontSize = 22.sp, fontWeight = FontWeight.Bold),
            )
            Text(
                "lluvia en 1 h · " + Textos.sello(r),
                style = TextStyle(color = c(Paleta.TENUE), fontSize = 12.sp),
                maxLines = 1,
            )
        }
    }
}

/* ---------------------------------------------------------- mediano 4x2 */

class WidgetMediano : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val r = Cache.leer(context)
        provideContent { Mediano(r) }
    }
}

class ReceptorMediano : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = WidgetMediano()
    override fun onEnabled(context: Context) {
        super.onEnabled(context)
        Actualizador.programar(context)
        Actualizador.ahora(context)
    }
}

@Composable
private fun Mediano(r: Reloj?) {
    Column(
        modifier = fondo.padding(12.dp).clickable(actionStartActivity<MainActivity>()),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(r?.icono ?: "🛰️", style = TextStyle(fontSize = 22.sp))
            Spacer(GlanceModifier.width(8.dp))
            Text(
                r?.titular ?: "Sin datos todavía",
                style = TextStyle(color = c(Paleta.de(r?.color ?: "muted")),
                                  fontSize = 17.sp, fontWeight = FontWeight.Bold),
                maxLines = 1,
            )
        }
        Text(
            r?.apoyo ?: "Abre la app para bajar la primera lectura.",
            style = TextStyle(color = c(Paleta.TENUE), fontSize = 12.sp),
            maxLines = 1,
        )
        Spacer(GlanceModifier.height(6.dp))
        Barras(r)
        Spacer(GlanceModifier.height(4.dp))
        Text(
            Textos.celda(r) + " · " + Textos.sello(r),
            style = TextStyle(color = c(Paleta.TENUE), fontSize = 11.sp),
            maxLines = 1,
        )
    }
}

/** Las barras de la página, de 15 min a 3 h. Sin dato: una raya apagada. */
@Composable
private fun Barras(r: Reloj?) {
    val alto = 34.0
    Row(modifier = GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.Bottom) {
        for (pl in r?.p ?: emptyList()) {
            Column(
                modifier = GlanceModifier.defaultWeight(),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                val h = maxOf(2.0, (pl.p ?: 0.0) * alto)
                Box(
                    modifier = GlanceModifier.width(16.dp).height(h.dp)
                        .background(c(Paleta.prob(pl.p))).cornerRadius(3.dp),
                ) {}
                Text(
                    Textos.plazo(pl.min),
                    style = TextStyle(color = c(Paleta.TENUE), fontSize = 10.sp),
                )
            }
        }
    }
}
