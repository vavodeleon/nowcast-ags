package io.github.vavodeleon.nowcast

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.text.Html
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import io.github.vavodeleon.nowcast.common.Cache
import io.github.vavodeleon.nowcast.common.Fuente
import io.github.vavodeleon.nowcast.common.Textos
import io.github.vavodeleon.nowcast.widget.Actualizador

/**
 * La app ES la página. Así cada mejora de docs/index.html llega sola, sin
 * volver a compilar ni instalar nada. Lo único propio es qué hacer con los
 * enlaces de fuera y qué mostrar sin red.
 */
class MainActivity : Activity() {
    private lateinit var web: WebView

    override fun onCreate(estado: Bundle?) {
        super.onCreate(estado)
        web = WebView(this).apply {
            setBackgroundColor(Color.BLACK)
            settings.javaScriptEnabled = true
            // La página guarda el tema elegido en localStorage.
            settings.domStorageEnabled = true
            webViewClient = Cliente()
        }
        setContentView(web)
        if (estado == null || web.restoreState(estado) == null) web.loadUrl(Fuente.PAGINA)
    }

    override fun onSaveInstanceState(salida: Bundle) {
        super.onSaveInstanceState(salida)
        web.saveState(salida)
    }

    override fun onResume() {
        super.onResume()
        // Abrir la app es buen momento para refrescar también los widgets.
        Actualizador.ahora(this)
    }

    @Deprecated("Basta para una sola pantalla")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }

    private inner class Cliente : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
            val u = req.url
            if (u.host == Fuente.HOST && (u.path ?: "").startsWith("/nowcast-ags")) return false
            // "¿Le atinó?" abre un issue en GitHub: mejor en el navegador,
            // donde ya tienes la sesión iniciada.
            runCatching { startActivity(Intent(Intent.ACTION_VIEW, u)) }
            return true
        }

        override fun onReceivedError(view: WebView, req: WebResourceRequest, err: WebResourceError) {
            if (req.isForMainFrame) {
                view.loadDataWithBaseURL(null, sinRed(), "text/html", "utf-8", null)
            }
        }
    }

    /** Sin internet: la última lectura guardada, dicha como lo que es. */
    private fun sinRed(): String {
        val r = Cache.leer(this)
        fun e(s: String) = Html.escapeHtml(s)
        val cuerpo = if (r == null) {
            "<p>Sin conexión y todavía sin ninguna lectura guardada.</p>"
        } else {
            """<div style="font-size:56px">${e(r.icono)}</div>
               <h1>${e(r.titular)}</h1>
               <p>${e(r.apoyo)}</p>
               <p>Lluvia en 1 h: <b>${e(Textos.pct(r.p60))}</b><br>${e(Textos.celda(r))}</p>
               <p class="t">Última lectura guardada: ${e(Textos.sello(r))}.<br>
               Sin conexión: puede haber cambiado. Si no hay internet, la malla
               sigue dando el pronóstico por radio.</p>"""
        }
        return """<html><head><meta name="viewport" content="width=device-width">
            <style>body{background:#000;color:#e8ecf2;font-family:sans-serif;
            text-align:center;padding:40px 20px}.t{color:#95a0b3;font-size:14px}</style>
            </head><body>$cuerpo</body></html>"""
    }
}
