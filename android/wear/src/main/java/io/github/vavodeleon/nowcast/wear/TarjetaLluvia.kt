package io.github.vavodeleon.nowcast.wear

import androidx.concurrent.futures.SuspendToFutureAdapter
import androidx.wear.protolayout.ActionBuilders
import androidx.wear.protolayout.ColorBuilders.argb
import androidx.wear.protolayout.DimensionBuilders.dp
import androidx.wear.protolayout.DimensionBuilders.expand
import androidx.wear.protolayout.DimensionBuilders.sp
import androidx.wear.protolayout.LayoutElementBuilders as L
import androidx.wear.protolayout.ModifiersBuilders as M
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.protolayout.TimelineBuilders
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders
import androidx.wear.tiles.TileService
import com.google.common.util.concurrent.ListenableFuture
import io.github.vavodeleon.nowcast.common.Cache
import io.github.vavodeleon.nowcast.common.Fuente
import io.github.vavodeleon.nowcast.common.Paleta
import io.github.vavodeleon.nowcast.common.Reloj
import io.github.vavodeleon.nowcast.common.Textos
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withTimeoutOrNull

/**
 * La tarjeta del reloj: veredicto, probabilidad a 1 h, barras y la celda.
 *
 * El sistema la vuelve a pedir cada 15 minutos (freshness). Tocarla la
 * refresca en el momento. Si no hay red o tarda, se dibuja la última lectura
 * guardada con su hora, y el "⚠" si ya es vieja: nunca un dato viejo
 * haciéndose pasar por actual.
 */
class TarjetaLluvia : TileService() {

    override fun onTileRequest(
        requestParams: RequestBuilders.TileRequest,
    ): ListenableFuture<TileBuilders.Tile> =
        SuspendToFutureAdapter.launchFuture(Dispatchers.IO) {
            // El sistema no espera para siempre a una tarjeta: tope de 6 s.
            val texto = withTimeoutOrNull(6_000) { Fuente.bajar() }
            Cache.guardar(this@TarjetaLluvia, texto)
            tarjeta(Cache.leer(this@TarjetaLluvia))
        }

    override fun onTileResourcesRequest(
        requestParams: RequestBuilders.ResourcesRequest,
    ): ListenableFuture<ResourceBuilders.Resources> =
        SuspendToFutureAdapter.launchFuture(Dispatchers.Default) {
            ResourceBuilders.Resources.Builder().setVersion(RECURSOS).build()
        }

    private fun tarjeta(r: Reloj?): TileBuilders.Tile =
        TileBuilders.Tile.Builder()
            .setResourcesVersion(RECURSOS)
            .setFreshnessIntervalMillis(15 * 60 * 1000L)
            .setTileTimeline(TimelineBuilders.Timeline.fromLayoutElement(contenido(r)))
            .build()

    private fun contenido(r: Reloj?): L.LayoutElement {
        val columna = L.Column.Builder()
            .setHorizontalAlignment(L.HORIZONTAL_ALIGN_CENTER)
            .addContent(texto("${r?.icono ?: "🛰️"} ${r?.titular ?: "Sin datos"}",
                              14f, Paleta.de(r?.color ?: "muted"), negrita = true, lineas = 2))
            .addContent(texto(Textos.pct(r?.p60), 30f, Paleta.prob(r?.p60), negrita = true))
            .addContent(texto("lluvia en 1 h", 11f, Paleta.TENUE))
            .addContent(espacio(6f))
            .addContent(barras(r))
            .addContent(espacio(6f))
            .addContent(texto(Textos.celda(r), 11f, Paleta.TINTA, lineas = 2))
            .addContent(texto(Textos.sello(r), 10f, Paleta.TENUE))
            .build()

        // Tocar en cualquier parte vuelve a pedir la tarjeta: refresco a mano.
        val tocar = M.Clickable.Builder()
            .setId("refrescar")
            .setOnClick(ActionBuilders.LoadAction.Builder().build())
            .build()

        return L.Box.Builder()
            .setWidth(expand())
            .setHeight(expand())
            .setHorizontalAlignment(L.HORIZONTAL_ALIGN_CENTER)
            .setVerticalAlignment(L.VERTICAL_ALIGN_CENTER)
            .setModifiers(M.Modifiers.Builder().setClickable(tocar).build())
            .addContent(columna)
            .build()
    }

    private fun barras(r: Reloj?): L.LayoutElement {
        val fila = L.Row.Builder().setVerticalAlignment(L.VERTICAL_ALIGN_BOTTOM)
        val plazos = r?.p ?: emptyList()
        plazos.forEachIndexed { i, pl ->
            if (i > 0) fila.addContent(L.Spacer.Builder().setWidth(dp(3f)).build())
            val alto = maxOf(2f, ((pl.p ?: 0.0) * 24).toFloat())
            fila.addContent(
                L.Box.Builder()
                    .setWidth(dp(9f))
                    .setHeight(dp(alto))
                    .setModifiers(
                        M.Modifiers.Builder().setBackground(
                            M.Background.Builder()
                                .setColor(argb(Paleta.prob(pl.p)))
                                .setCorner(M.Corner.Builder().setRadius(dp(2f)).build())
                                .build()
                        ).build()
                    )
                    .build()
            )
        }
        return fila.build()
    }

    private fun texto(s: String, tam: Float, color: Int,
                      negrita: Boolean = false, lineas: Int = 1): L.LayoutElement =
        L.Text.Builder()
            .setText(s)
            .setMaxLines(lineas)
            .setMultilineAlignment(L.TEXT_ALIGN_CENTER)
            .setFontStyle(
                L.FontStyle.Builder()
                    .setSize(sp(tam))
                    .setColor(argb(color))
                    .setWeight(if (negrita) L.FONT_WEIGHT_BOLD else L.FONT_WEIGHT_NORMAL)
                    .build()
            )
            .build()

    private fun espacio(alto: Float): L.LayoutElement =
        L.Spacer.Builder().setHeight(dp(alto)).build()

    private companion object {
        const val RECURSOS = "1"
    }
}
