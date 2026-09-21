"""¿El infrarrojo está siguiendo el yunque en vez de la tormenta?

    python medir_deriva.py

## La hipótesis

Álvaro, 21 de septiembre de 2026, mirando el cono contra las tormentas reales:

  «está apuntando completamente a otro lado, parece que solo estamos
   calculando el movimiento de la parte superior que se lleva el wind shear,
   los rayos son los que nos dan la verdadera trayectoria no?»

Tiene fundamento físico y es un problema conocido del nowcasting por satélite.
El infrarrojo mide el **techo** de la nube, a 10–14 km de altura. Ese techo lo
arrastra el viento en altura, que con cizalladura va bastante más rápido y a
menudo en otra dirección que la celda de abajo. El yunque se estira hacia donde
sopla arriba mientras la celda que llueve va a su aire.

Los rayos salen del **núcleo convectivo**. Donde hay descargas está la celda de
verdad, no su sombrero.

## Cómo se mide sin esperar

No hace falta instrumentar nada nuevo: los dos datos ya están guardados.

- `data/predictions.csv` tiene `motion_from` y `motion_speed_kmh` de cada
  corrida: el movimiento según el infrarrojo.
- `docs/hist/<día>/<HHMM>.r.json` tiene los rayos de cada instante, siete días
  hacia atrás. Dos archivos consecutivos dan el desplazamiento del centroide,
  o sea el movimiento según el núcleo.

Se comparan en los instantes en que existen los dos. Si la diferencia angular
es pequeña, la hipótesis es falsa y el problema está en otra parte. Si es
grande y **sistemática** -siempre hacia el mismo lado- es cizalladura.

Lo que hay que mirar no es solo el promedio del ángulo, sino si el sesgo tiene
dirección: un error que apunta siempre al mismo lado se corrige; uno que va
para todos lados es ruido y se trata distinto.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

from nowcast import config, lightning, store

RAIZ = os.path.join(os.path.dirname(config.LATEST_JSON), "hist")


def _rumbo_a_grados(nombre: str) -> float | None:
    """'noroeste' -> 315. Es de DONDE viene, no hacia dónde va."""
    puntos = {"norte": 0, "noreste": 45, "este": 90, "sureste": 135,
              "sur": 180, "suroeste": 225, "oeste": 270, "noroeste": 315}
    return puntos.get((nombre or "").strip().lower())


def _diferencia(a: float, b: float) -> float:
    """Diferencia angular con signo, en [-180, 180]. Positivo = b a la derecha."""
    return (b - a + 180.0) % 360.0 - 180.0


def cuadros_con_rayos() -> list[tuple[datetime, list]]:
    """Todos los instantes archivados que tienen descargas."""
    salida = []
    if not os.path.isdir(RAIZ):
        return salida
    for dia in sorted(os.listdir(RAIZ)):
        carpeta = os.path.join(RAIZ, dia)
        if not os.path.isdir(carpeta):
            continue
        for nombre in sorted(os.listdir(carpeta)):
            if not nombre.endswith(".r.json"):
                continue
            hhmm = nombre[:4]
            try:
                local = datetime.strptime(f"{dia} {hhmm}", "%Y-%m-%d %H%M")
                t = local.replace(tzinfo=config.TZ).astimezone(timezone.utc)
                with open(os.path.join(carpeta, nombre), encoding="utf-8") as fh:
                    puntos = json.load(fh)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            if puntos:
                salida.append((t, puntos))
    return salida


def deriva_en(cuadros: list, i: int) -> lightning.Deriva:
    """Deriva usando el cuadro i y los anteriores dentro de una hora."""
    t0 = cuadros[i][0]
    ventana = [(t, p) for t, p in cuadros[max(0, i - 4):i + 1]
               if timedelta(0) <= t0 - t <= timedelta(minutes=60)]
    if len(ventana) < 2:
        return lightning.Deriva(None, 0.0, 0.0, 0, 0)
    return lightning.deriva({"bloques": [
        {"t": t.isoformat(), "puntos": p, "total": len(p)} for t, p in ventana]})


def motion_ir() -> dict[str, tuple[float, float]]:
    """{instante redondeado: (rumbo_desde_grados, km/h)} según el infrarrojo."""
    salida = {}
    for fila in store.read_predictions():
        # Todas las filas de una corrida comparten el movimiento; basta una.
        if (fila.get("lead_min") or "") != "15":
            continue
        g = _rumbo_a_grados(fila.get("motion_from"))
        try:
            v = float(fila.get("motion_speed_kmh") or 0)
        except ValueError:
            continue
        if g is None:
            continue
        try:
            t = datetime.fromisoformat(fila["issued_utc"])
        except (KeyError, ValueError):
            continue
        salida[store.round_slot(t)] = (g, v)
    return salida


def main() -> int:
    print("=" * 66)
    print("¿EL INFRARROJO SIGUE EL YUNQUE EN VEZ DE LA TORMENTA?")
    print("=" * 66)

    cuadros = cuadros_con_rayos()
    if len(cuadros) < 3:
        print(f"\nSolo {len(cuadros)} cuadros archivados con rayos.")
        print("Hace falta al menos una tormenta eléctrica en los últimos")
        print("7 días para poder comparar. Vuelve a correrlo después de una.")
        return 0

    ir = motion_ir()
    print(f"\nCuadros con descargas archivados: {len(cuadros)}")
    print(f"Corridas con movimiento del infrarrojo: {len(ir)}")

    casos = []
    for i in range(len(cuadros)):
        d = deriva_en(cuadros, i)
        if d.bearing_deg is None or d.confianza < 0.25:
            continue
        clave = store.round_slot(cuadros[i][0])
        if clave not in ir:
            continue
        g_ir, v_ir = ir[clave]
        # El infrarrojo reporta de DÓNDE viene; la deriva, hacia dónde va.
        # Se comparan en la misma convención o el resultado sale a 180°.
        desde_rayos = (d.bearing_deg + 180.0) % 360.0
        casos.append({
            "t": cuadros[i][0], "ir_desde": g_ir, "ir_kmh": v_ir,
            "rayos_desde": desde_rayos, "rayos_kmh": d.speed_kmh,
            "dif": _diferencia(g_ir, desde_rayos),
            "conf": d.confianza, "destellos": d.destellos,
        })

    if len(casos) < 5:
        print(f"\nSolo {len(casos)} instantes con las dos medidas a la vez.")
        print("No alcanza para decir nada. La deriva por rayos necesita")
        print("varios bloques seguidos con descargas, o sea una tormenta")
        print("de verdad, no un par de rayos sueltos.")
        for c in casos:
            print(f"   {c['t']:%m-%d %H:%M}  IR del {c['ir_desde']:.0f}°  "
                  f"rayos del {c['rayos_desde']:.0f}°  "
                  f"dif {c['dif']:+.0f}°")
        return 0

    print(f"\n1. LOS CASOS ({len(casos)} instantes con tormenta eléctrica)\n")
    print(f"   {'cuando':>12} {'IR desde':>9} {'rayos desde':>12} "
          f"{'dif':>6} {'IR km/h':>8} {'rayos km/h':>11} {'dest.':>6}")
    for c in casos[:25]:
        print(f"   {c['t']:%m-%d %H:%M} {c['ir_desde']:>8.0f}° "
              f"{c['rayos_desde']:>11.0f}° {c['dif']:>+5.0f}° "
              f"{c['ir_kmh']:>8.0f} {c['rayos_kmh']:>11.0f} "
              f"{c['destellos']:>6}")
    if len(casos) > 25:
        print(f"   ... y {len(casos) - 25} más")

    difs = [c["dif"] for c in casos]
    n = len(difs)
    medio = sum(difs) / n
    absol = sum(abs(d) for d in difs) / n
    grandes = sum(1 for d in difs if abs(d) > 45)
    v_ir = sum(c["ir_kmh"] for c in casos) / n
    v_ra = sum(c["rayos_kmh"] for c in casos) / n

    print("\n2. EL VEREDICTO\n")
    print(f"   diferencia media (con signo):  {medio:+.0f}°")
    print(f"   diferencia media (absoluta):   {absol:.0f}°")
    print(f"   casos con más de 45° de error: {grandes} de {n}")
    print(f"   velocidad media: infrarrojo {v_ir:.0f} km/h, "
          f"rayos {v_ra:.0f} km/h")

    print()
    if absol < 25:
        print("   Las dos fuentes coinciden. La hipótesis de la cizalladura")
        print("   NO se sostiene con estos datos, y si el cono apunta mal el")
        print("   motivo está en otra parte. Conviene mirar la selección de")
        print("   la celda antes que el movimiento.")
    else:
        print("   Hay discrepancia grande y real entre lo que ve el techo de")
        print("   la nube y lo que hace el núcleo.")
        if abs(medio) > absol * 0.5:
            lado = "la derecha" if medio > 0 else "la izquierda"
            print(f"   Y es SISTEMÁTICA: el infrarrojo se desvía hacia {lado}")
            print(f"   en promedio {abs(medio):.0f}°. Un sesgo con dirección")
            print("   se corrige; el ruido sin dirección, no.")
        else:
            print("   Pero NO es sistemática: el error va para todos lados.")
            print("   Eso apunta más a ruido de la correlación de fase que a")
            print("   cizalladura, y la corrección tendría que ser otra.")
        if v_ir > v_ra * 1.3:
            print(f"\n   Además el infrarrojo va {v_ir / max(v_ra, 1):.1f} "
                  "veces más rápido que el núcleo,")
            print("   que es exactamente lo que hace el viento en altura con")
            print("   un yunque. Es la firma de la cizalladura.")

    print("\n2-bis. ¿CUÁL DE LAS DOS ES LA QUE NO MIDE?\n")
    print("   Noventa grados de diferencia media es exactamente lo que dan dos")
    print("   ángulos independientes al azar. Así que la pregunta deja de ser")
    print("   'cuánto discrepan' y pasa a ser 'cuál de las dos es ruido'.")
    print()
    print("   El desempate es la PERSISTENCIA. Una tormenta real no gira 90°")
    print("   en quince minutos: su rumbo cambia despacio. El ruido, no.")
    print("   Se mide cuánto cambia cada serie entre estimaciones seguidas.\n")

    def persistencia(pares):
        """Cambio angular medio entre estimaciones consecutivas."""
        cambios = []
        for (t0, a0), (t1, a1) in zip(pares, pares[1:]):
            dt = (t1 - t0).total_seconds() / 60.0
            if 10 <= dt <= 20:
                cambios.append(abs(_diferencia(a0, a1)))
        return (sum(cambios) / len(cambios), len(cambios)) if cambios else (None, 0)

    p_ir, n_ir = persistencia([(c["t"], c["ir_desde"]) for c in casos])
    p_ra, n_ra = persistencia([(c["t"], c["rayos_desde"]) for c in casos])
    print(f"   {'serie':>14} {'cambio medio entre cuadros':>28} {'casos':>7}")
    if p_ir is not None:
        print(f"   {'infrarrojo':>14} {p_ir:>27.0f}° {n_ir:>7}")
    if p_ra is not None:
        print(f"   {'rayos':>14} {p_ra:>27.0f}° {n_ra:>7}")
    print()
    print("   Referencia: una celda real cambia menos de ~25° en 15 minutos.")
    print("   Una serie al azar cambia ~90°.")
    print()
    # Los umbrales de antes -60 grados- contradecian la referencia impresa dos
    # lineas mas arriba: aprobaban como "persistente" una serie que cambia 59
    # grados entre cuadros, o sea mas cerca del azar (90) que de una celda
    # real (menos de 25). Corregido el 22/09/2026 porque la salida real lo
    # dejo en evidencia: infrarrojo 8 grados, rayos 59, y el veredicto decia
    # que las dos median algo.
    FIABLE, RUIDO = 25.0, 50.0
    if p_ir is not None and p_ra is not None:
        if p_ir > RUIDO and p_ra > RUIDO:
            print("   LAS DOS SON RUIDO. No hay de dónde sacar una trayectoria")
            print("   fiable con lo que hay: ni el techo de la nube a esta")
            print("   resolución, ni el centroide de descargas de esta forma.")
            print("   Lo honesto es no dibujar cono cuando no se puede medir,")
            print("   que es lo que hace ahora el umbral de resolución.")
        elif p_ir > RUIDO:
            print("   El INFRARROJO es el que no mide. Los rayos son")
            print("   persistentes, así que su deriva sí describe algo.")
        elif p_ra > RUIDO:
            print("   Los RAYOS son los que no miden así. El centroide salta")
            print("   entre celdas de un mismo complejo. El infrarrojo es más")
            print("   estable de lo que parecía: hay que mejorar el seguimiento")
            print("   de descargas antes de dejar que mande sobre nada.")
        elif p_ir <= FIABLE and p_ra <= FIABLE:
            print("   Las dos son persistentes, así que las dos miden algo real")
            print("   y distinto. Entonces la cizalladura vuelve a la mesa.")
        else:
            cual = "el infrarrojo" if p_ir > FIABLE else "los rayos"
            print(f"   Zona intermedia: {cual} está entre lo fiable y el ruido.")
            print("   Mide algo, pero no lo suficiente para dirigir un cono.")

    print("\n2-ter. ¿HACE FALTA EL UMBRAL DE RESOLUCIÓN? — el infrarrojo por velocidad")
    print("\n   Si el rumbo del satélite fuera ruido subpíxel, los casos lentos")
    print("   tendrían que saltar mucho más que los rápidos. Si son igual de")
    print("   estables, el umbral que puse el 21/09 sobra y está tirando")
    print("   información buena los días tranquilos.")
    print()
    print("   (La mediana ponderada promedia hasta 20 estimaciones por corrida")
    print("   -cinco regiones por cuatro pares de cuadros-, así que puede")
    print("   resolver mejor que un solo desplazamiento de 0.4 px.)\n")
    tramos = [(0, 1.0), (1.0, 2.0), (2.0, 4.0), (4.0, 99.0)]
    print(f"   {'desplazamiento':>18} {'casos':>7} {'cambio medio':>14}")
    for lo, hi in tramos:
        sel = [c for c in casos
               if lo <= c["ir_kmh"] * 15 / 60 / 2.44 < hi]
        p, nn = persistencia([(c["t"], c["ir_desde"]) for c in sel])
        etiq = f"{lo:.0f}-{hi:.0f} px" if hi < 99 else f">{lo:.0f} px"
        if p is None:
            print(f"   {etiq:>18} {len(sel):>7} {'sin pares seguidos':>14}")
        else:
            print(f"   {etiq:>18} {len(sel):>7} {p:>13.0f}°")
    print()
    print("   OJO con esta tabla: `motion_from` viene cuantizado en sectores de")
    print("   45°, así que dos rumbos parecidos salen como cambio de 0°. Eso")
    print("   hace que TODAS las filas parezcan más estables de lo que son.")
    print("   Desde el 21/09 se guarda `motion_bearing` en grados; con unos")
    print("   días de esa columna, esta tabla se podrá leer sin el descuento.")

    print("\n3. QUÉ SIGNIFICA PARA EL CONO\n")
    lentos = sum(1 for c in casos if c["ir_kmh"] * 15 / 60 / 2.44 < 2.0)
    print(f"   {lentos} de {n} casos tienen el satélite por debajo de 2 px de")
    print("   desplazamiento entre cuadros, o sea por debajo de su propia")
    print("   resolución. Ahí el rumbo no se puede medir, y desde el")
    print("   21/09/2026 ya no se publica: sin rumbo no hay cono.")
    print()
    print("   El rumbo decide dos cosas: qué celda se considera 'que viene")
    print("   hacia acá' y por dónde se dibuja la franja. Un error de 60°")
    print("   a 80 km de distancia son ~80 km de desvío en el punto de")
    print("   llegada: la diferencia entre mojarse y no.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
