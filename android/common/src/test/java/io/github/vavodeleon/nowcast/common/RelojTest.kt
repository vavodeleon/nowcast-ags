package io.github.vavodeleon.nowcast.common

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * La otra mitad del contrato. test_reloj.py (en el Pi) genera estas muestras
 * con el mismo código que publica reloj.json y comprueba que tienen las
 * claves de verdad; aquí se comprueba que la app las entiende.
 */
class RelojTest {
    private fun muestra(nombre: String) =
        javaClass.getResource("/$nombre")!!.readText()

    @Test fun leeLaMuestraDelPi() {
        val r = Reloj.de(muestra("reloj.json"))
        assertNotNull(r)
        r!!
        assertEquals(1, r.v)
        assertEquals("Lluvia en ~40 min", r.titular)
        assertEquals(0.5, r.p60!!, 1e-9)
        assertEquals(7, r.p.size)
        assertNull("un plazo sin dato es null, no 0", r.p[2].p)
        assertEquals("oeste", r.celda?.desde)
        assertEquals("13:30", r.hora)
    }

    @Test fun sinDatosNoEsCero() {
        val r = Reloj.de(muestra("reloj_vacio.json"))!!
        assertNull(r.p60)
        assertEquals("—", Textos.pct(r.p60))
        assertEquals("sin datos", Textos.sello(r))
    }

    @Test fun clavesNuevasOMalasNoRompen() {
        val r = Reloj.de("""{"v":2,"titular":null,"algo_nuevo":[1,2],"p60":0.3}""")
        assertNotNull(r)
        assertEquals("Sin datos", r!!.titular)
        assertNull(Reloj.de("esto no es json"))
        assertNull(Reloj.de(null))
    }

    @Test fun viejoDespuesDe45Min() {
        val r = Reloj(emitidoUtc = "2026-10-05T19:30:00+00:00", hora = "13:30")
        assertFalse(r.viejo(Instant.parse("2026-10-05T20:15:00Z")))
        assertTrue(r.viejo(Instant.parse("2026-10-05T20:16:00Z")))
        assertEquals("13:30 ⚠", Textos.sello(r, Instant.parse("2026-10-05T21:00:00Z")))
    }

    @Test fun eligeElMasReciente() {
        val viejo = """{"emitido_utc":"2026-10-05T19:30:00+00:00"}"""
        val nuevo = """{"emitido_utc":"2026-10-05T20:30:00+00:00"}"""
        assertEquals(nuevo, Fuente.elegir(viejo, nuevo))
        assertEquals(nuevo, Fuente.elegir(nuevo, viejo))
        assertEquals(viejo, Fuente.elegir(viejo, null))
        assertEquals(viejo, Fuente.elegir(viejo, "basura"))
        assertNull(Fuente.elegir(null, null))
    }

    @Test fun textos() {
        assertEquals("15'", Textos.plazo(15))
        assertEquals("1h", Textos.plazo(60))
        assertEquals("1½h", Textos.plazo(90))
        assertEquals("24%", Textos.pct(0.244))
        val r = Reloj.de(muestra("reloj.json"))
        assertEquals("Celda a 35 km, del oeste · ~40 min", Textos.celda(r))
        assertEquals("Sin celdas acercándose", Textos.celda(Reloj()))
        assertEquals(Paleta.de("rojo"), Paleta.prob(0.70))
        assertEquals(Paleta.de("naranja"), Paleta.prob(0.699))
        assertEquals(Paleta.LINEA, Paleta.prob(null))
    }
}
