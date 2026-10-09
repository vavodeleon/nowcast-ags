package io.github.vavodeleon.nowcast.widget

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.LocalSize
import androidx.glance.action.actionStartActivity
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
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

/**
 * Se adapta al tamaño real. La primera versión dibujaba con medidas fijas
 * y, estirado a 4x3, dejaba media tarjeta vacía con barras de 7 dp (Álvaro,
 * 9/10/2026). Ahora mide el espacio y las barras se quedan con lo que sobra.
 */
class WidgetMediano : GlanceAppWidget() {
    override val sizeMode: SizeMode = SizeMode.Exact

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

// Alto que ocupa todo lo que no son barras: márgenes, encabezado, las dos
// filas de etiquetas y el pie. Medido por encima a propósito: si sobra, las
// barras quedan un poco más bajas; si faltara, Android cortaría el pie.
private const val FIJO_DP = 150f
private const val FIJO_NUBE_DP = 18f

@Composable
private fun Mediano(r: Reloj?) {
    val alto = LocalSize.current.height.value
    val holgado = alto >= 190f
    val nube = holgado && r?.nubeBaja == true
    val barra = (alto - FIJO_DP - (if (nube) FIJO_NUBE_DP else 0f)).coerceIn(24f, 170f)

    Column(
        modifier = fondo.padding(14.dp).clickable(actionStartActivity<MainActivity>()),
    ) {
        Row(modifier = GlanceModifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically) {
            Text(r?.icono ?: "🛰️", style = TextStyle(fontSize = 24.sp))
            Spacer(GlanceModifier.width(8.dp))
            Column(modifier = GlanceModifier.defaultWeight()) {
                Text(
                    r?.titular ?: "Sin datos todavía",
                    style = TextStyle(color = c(Paleta.de(r?.color ?: "muted")),
                                      fontSize = 18.sp, fontWeight = FontWeight.Bold),
                    maxLines = 1,
                )
                Text(
                    r?.apoyo ?: "Abre la app para bajar la primera lectura.",
                    style = TextStyle(color = c(Paleta.TENUE), fontSize = 12.sp),
                    maxLines = if (holgado) 2 else 1,
                )
            }
            Spacer(GlanceModifier.width(8.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    Textos.pct(r?.p60),
                    style = TextStyle(color = c(Paleta.prob(r?.p60)),
                                      fontSize = 26.sp, fontWeight = FontWeight.Bold),
                )
                Text("en 1 h", style = TextStyle(color = c(Paleta.TENUE), fontSize = 11.sp))
            }
        }
        Spacer(GlanceModifier.height(10.dp))
        Barras(r, barra)
        Spacer(GlanceModifier.height(8.dp))
        if (nube) {
            Text(
                "☁ Nubes bajas encima: pueden lloviznar aunque diga que no",
                style = TextStyle(color = c(Paleta.TINTA), fontSize = 12.sp),
                maxLines = 1,
            )
        }
        Text(
            Textos.celda(r) + " · " + Textos.sello(r),
            style = TextStyle(color = c(Paleta.TENUE), fontSize = 12.sp),
            maxLines = 1,
        )
    }
}

/**
 * Las barras de la página, de 15 min a 3 h, sobre un carril que marca el
 * 100%: así una barra de 30% se lee como "un tercio", no como "chiquita".
 * Sin dato: carril vacío y "—", nunca una barra de 0 que diga "no llueve".
 */
@Composable
private fun Barras(r: Reloj?, alto: Float) {
    Row(modifier = GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.Bottom) {
        for (pl in r?.p ?: emptyList()) {
            Column(
                modifier = GlanceModifier.defaultWeight(),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    Textos.pct(pl.p),
                    style = TextStyle(
                        color = c(if (pl.p == null) Paleta.TENUE else Paleta.prob(pl.p)),
                        fontSize = 10.sp, fontWeight = FontWeight.Bold),
                )
                Spacer(GlanceModifier.height(2.dp))
                Box(
                    modifier = GlanceModifier.width(18.dp).height(alto.dp)
                        .background(c(Paleta.CARRIL)).cornerRadius(4.dp),
                    contentAlignment = Alignment.BottomCenter,
                ) {
                    val p = pl.p
                    if (p != null) {
                        val h = maxOf(3f, (p * alto).toFloat())
                        Box(
                            modifier = GlanceModifier.width(18.dp).height(h.dp)
                                .background(c(Paleta.prob(p))).cornerRadius(4.dp),
                        ) {}
                    }
                }
                Spacer(GlanceModifier.height(2.dp))
                Text(
                    Textos.plazo(pl.min),
                    style = TextStyle(color = c(Paleta.TENUE), fontSize = 10.sp),
                )
            }
        }
    }
}
