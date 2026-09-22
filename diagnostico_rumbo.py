"""¿Está el rumbo invertido? Tres comprobaciones independientes.

    python diagnostico_rumbo.py

Álvaro, 22 de septiembre de 2026, con una tormenta organizada encima:

  «con tormentas bastantes organizadas con rayos viniendo desde el este
   y sigue diciendo que vienen del oeste»

**Ciento ochenta grados exactos no es ruido: es un signo.** El ruido reparte el
error por todos lados —eso ya se midió, 91° de media— pero una inversión
consistente solo sale de una convención equivocada en alguna parte de la
cadena. Y una convención equivocada se encuentra mirando, no razonando.

## Las tres comprobaciones, y por qué son independientes

**1. Los rayos, en latitud y longitud puras.** El archivo guarda las descargas
en coordenadas geográficas, sin pasar por ningún píxel. Si el centroide se
mueve hacia el oeste, la tormenta va al oeste y viene del este. Punto. Esta
medida no puede tener el error que se busca, porque no usa la rejilla.

**2. La orientación real de la rejilla de GOES.** El motor da por hecho que la
columna crece hacia el ESTE y la fila hacia el SUR. Si en el archivo de verdad
el eje `x` va al revés, el este y el oeste se intercambian y la latitud queda
bien — que es exactamente el síntoma descrito. Aquí se lee del archivo en vez
de suponerlo.

**3. Lo que el sistema está publicando ahora mismo**, para tener las tres
cosas en la misma pantalla y a la misma hora.

Si (1) y (3) salen opuestos y (2) revela un eje descendente, la causa está
encontrada. Si (2) sale como se esperaba, el error está en otro sitio y esto
lo acota igual: descarta la mitad del mapa.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

from nowcast import config, store

RAIZ = os.path.join(os.path.dirname(config.LATEST_JSON), "hist")


def _rumbo(norte_km: float, este_km: float) -> float | None:
    """Rumbo de un desplazamiento: hacia dónde va, en grados desde el norte."""
    if math.hypot(norte_km, este_km) < 0.5:
        return None
    return (math.degrees(math.atan2(este_km, norte_km)) + 360.0) % 360.0


def _nombre(bearing: float | None, desde: bool = True) -> str:
    if bearing is None:
        return "—"
    g = (bearing + 180.0) % 360.0 if desde else bearing
    puntos = ["norte", "noreste", "este", "sureste",
              "sur", "suroeste", "oeste", "noroeste"]
    return puntos[int((g + 22.5) % 360 // 45)]


def rayos_recientes(horas: float = 2.0):
    """Centroides de descargas de las últimas horas, en lat/lon."""
    salida = []
    if not os.path.isdir(RAIZ):
        return salida
    corte = datetime.now(timezone.utc) - timedelta(hours=horas)
    for dia in sorted(os.listdir(RAIZ))[-2:]:
        carpeta = os.path.join(RAIZ, dia)
        if not os.path.isdir(carpeta):
            continue
        for nombre in sorted(os.listdir(carpeta)):
            if not nombre.endswith(".r.json"):
                continue
            try:
                local = datetime.strptime(f"{dia} {nombre[:4]}", "%Y-%m-%d %H%M")
                t = local.replace(tzinfo=config.TZ).astimezone(timezone.utc)
                if t < corte:
                    continue
                with open(os.path.join(carpeta, nombre), encoding="utf-8") as fh:
                    puntos = json.load(fh)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            if not puntos:
                continue
            tot = wlat = wlon = 0.0
            for p in puntos:
                n = float(p[2]) if len(p) > 2 else 1.0
                wlat += float(p[0]) * n
                wlon += float(p[1]) * n
                tot += n
            if tot > 0:
                salida.append((t, wlat / tot, wlon / tot, int(tot)))
    return salida


def paso_1(horas: float = 2.0) -> float | None:
    print("1. LOS RAYOS, EN LATITUD Y LONGITUD PURAS")
    print("   (sin tocar ningún píxel: esta medida no puede tener el error)\n")
    c = rayos_recientes(horas)
    if len(c) < 2:
        print(f"   Solo {len(c)} cuadros con descargas en las últimas "
              f"{horas:.0f} h.")
        # Que no haya nada puede ser que no hubo rayos, o que el archivo no
        # este donde se busca. No son lo mismo y conviene distinguirlo aqui
        # mismo en vez de suponer.
        if not os.path.isdir(RAIZ):
            print(f"   Y el archivo no existe: {RAIZ}")
        else:
            dias = sorted(d for d in os.listdir(RAIZ)
                          if os.path.isdir(os.path.join(RAIZ, d)))
            print(f"   Días archivados: {', '.join(dias[-4:]) or 'ninguno'}")
            for dia in dias[-1:]:
                arch = sorted(n for n in os.listdir(os.path.join(RAIZ, dia))
                              if n.endswith(".r.json"))
                print(f"   Cuadros con rayos en {dia}: {len(arch)}"
                      + (f" (últimos: {', '.join(a[:4] for a in arch[-6:])})"
                         if arch else ""))
            print(f"   Hora local ahora: "
                  f"{datetime.now(timezone.utc).astimezone(config.TZ):%Y-%m-%d %H:%M}")
        print("   Si hay cuadros pero no salen, prueba con más horas:")
        print("     python diagnostico_rumbo.py 12")
        return None

    print(f"   {'hora':>8} {'lat':>9} {'lon':>10} {'destellos':>10} {'movió':>16}")
    previo = None
    rumbos = []
    for t, lat, lon, n in c:
        mov = ""
        if previo:
            t0, lat0, lon0 = previo
            dt = (t - t0).total_seconds() / 60.0
            if 5 <= dt <= 40:
                nk = (lat - lat0) * 111.0
                ek = (lon - lon0) * 111.0 * math.cos(math.radians(lat0))
                r = _rumbo(nk, ek)
                if r is not None:
                    rumbos.append(r)
                    kmh = math.hypot(nk, ek) / dt * 60
                    mov = f"hacia {_nombre(r, False):<9} {kmh:>3.0f} km/h"
        print(f"   {t.astimezone(config.TZ):%H:%M} {lat:>9.3f} {lon:>10.3f} "
              f"{n:>10} {mov:>16}")
        previo = (t, lat, lon)

    if not rumbos:
        print("\n   Las descargas no se movieron lo suficiente para dar rumbo.")
        return None
    # Media circular: promediar 350 y 10 grados tiene que dar 0, no 180.
    sy = sum(math.sin(math.radians(r)) for r in rumbos)
    sx = sum(math.cos(math.radians(r)) for r in rumbos)
    medio = (math.degrees(math.atan2(sy, sx)) + 360.0) % 360.0
    coherencia = math.hypot(sy, sx) / len(rumbos)
    print(f"\n   Rumbo medio de los rayos: va hacia el {_nombre(medio, False)} "
          f"({medio:.0f}°)")
    print(f"   O sea: VIENE DEL {_nombre(medio).upper()}")
    print(f"   Coherencia entre pasos: {coherencia:.2f} "
          f"({'firme' if coherencia > 0.7 else 'flojo, tomar con pinzas'})")
    return medio


def paso_2() -> None:
    print("\n2. LA ORIENTACIÓN REAL DE LA REJILLA DE GOES")
    print("   El motor SUPONE que la columna crece al este y la fila al sur.")
    print("   Si el eje x del archivo va al revés, el este y el oeste se")
    print("   intercambian y la latitud queda bien: el síntoma exacto.\n")
    try:
        import io

        import h5py
        import numpy as np  # noqa: F401

        from nowcast import goes, http
    except Exception as exc:
        print(f"   No se pudo preparar la lectura: {exc}")
        return

    claves = []
    for prefix, _tag, step in goes.SECTORS:
        claves = goes.recent_keys(1, prefix, step)
        if claves:
            break
    if not claves:
        print("   No hay imágenes recientes de GOES para comprobar.")
        return

    t, clave = claves[-1]
    print(f"   Archivo: {clave.split('/')[-1][:48]}…")
    raw = http.get_bytes(f"{goes.BUCKET}/{clave}", timeout=90)
    if not raw:
        print("   No se pudo descargar.")
        return

    with h5py.File(io.BytesIO(raw), "r") as fh:
        xs, ys = fh["x"], fh["y"]
        # `unpack` y no `float(xs[0])`. GOES guarda x, y y CMI como enteros
        # empaquetados con scale_factor y add_offset; h5py devuelve el entero
        # crudo y la escala de `y` es NEGATIVA. Leyendo en crudo las dos
        # diferencias salen +1 -enteros consecutivos- y el signo se pierde
        # justo cuando el signo es lo unico que se estaba buscando.
        #
        # La primera version de este diagnostico tenia ese fallo e imprimio
        # "fila -> NORTE" con toda confianza. El motor nunca lo tuvo: usa
        # `unpack` desde el principio, y ahi estaba escrito el aviso.
        x0, x1 = goes.unpack(xs, slice(0, 2))
        y0, y1 = goes.unpack(ys, slice(0, 2))
        print(f"   crudo sin escalar: x[0]={float(xs[0]):.0f} y[0]={float(ys[0]):.0f}"
              f"   escalado: x0={x0:+.6f} rad  y0={y0:+.6f} rad")
        proj = fh["goes_imager_projection"]
        grid = goes.FixedGrid({k: goes._attr(proj, k)
                               for k in ("semi_major_axis", "semi_minor_axis",
                                         "perspective_point_height",
                                         "longitude_of_projection_origin")})

    dx, dy = x1 - x0, y1 - y0
    print(f"   dx = {dx:+.3e}  (columna → {'ESTE' if dx > 0 else 'OESTE'})")
    print(f"   dy = {dy:+.3e}  (fila    → {'SUR'  if dy < 0 else 'NORTE'})")

    # Y la comprobación que no depende de interpretar los signos: convertir
    # dos puntos de la rejilla a coordenadas y ver hacia dónde caen.
    a = grid.scan_to_lonlat(x0, y0)
    b = grid.scan_to_lonlat(x0 + 50 * dx, y0)
    c = grid.scan_to_lonlat(x0, y0 + 50 * dy)
    if a and b and c:
        # scan_to_lonlat devuelve (lat, lon) o (lon, lat) segun la convencion
        # del modulo; se imprime crudo y se razona con la diferencia.
        print(f"\n   50 columnas a la derecha: la longitud cambia "
              f"{b[1] - a[1]:+.3f}°  "
              f"({'al este' if b[1] > a[1] else 'al OESTE'})")
        print(f"   50 filas hacia abajo:     la latitud cambia "
              f"{c[0] - a[0]:+.3f}°  "
              f"({'al sur' if c[0] < a[0] else 'al NORTE'})")
        mal_x = b[1] < a[1]
        mal_y = c[0] > a[0]
        if mal_x or mal_y:
            print("\n   *** AQUÍ ESTÁ EL PROBLEMA ***")
            if mal_x:
                print("   La columna crece hacia el OESTE, y el motor supone")
                print("   que crece hacia el este: el rumbo sale reflejado en")
                print("   el eje este-oeste.")
            if mal_y:
                print("   La fila crece hacia el NORTE, y el motor supone sur.")
        else:
            print("\n   La rejilla está como el motor supone. El error, si lo")
            print("   hay, no está aquí: queda descartada la mitad del mapa.")


def paso_3(rumbo_rayos: float | None) -> None:
    print("\n3. LO QUE EL SISTEMA ESTÁ PUBLICANDO AHORA")
    d = store.load_json(config.LATEST_JSON, {}) or {}
    if not d:
        print("   No hay latest.json.")
        return
    print(f"   emitido:          {d.get('issued_local')}")
    print(f"   vienen del:       {d.get('motion_from')}")
    print(f"   rumbo (hacia):    {d.get('motion_bearing')}°")
    print(f"   velocidad:        {d.get('motion_speed_kmh')} km/h")
    print(f"   fuente del rumbo: {d.get('motion_fuente')}")
    der = d.get("deriva")
    if der:
        print(f"   deriva por rayos: del {der.get('desde')} a "
              f"{der.get('kmh')} km/h, confianza {der.get('confianza')}, "
              f"{'USADA' if der.get('usada') else 'descartada'}")

    b = d.get("motion_bearing")
    if rumbo_rayos is None or b is None:
        return
    dif = abs((float(b) - rumbo_rayos + 180.0) % 360.0 - 180.0)
    print(f"\n   Diferencia con el rumbo de los rayos: {dif:.0f}°")
    if dif > 135:
        print("   *** INVERTIDO. No es ruido: es un signo en alguna parte.")
    elif dif > 60:
        print("   Discrepancia grande, pero no una inversión limpia.")
    else:
        print("   Coinciden. En ESTE instante el rumbo publicado es correcto,")
        print("   así que lo que viste tuvo otra causa o fue otro momento.")


def main() -> int:
    horas = 2.0
    if len(sys.argv) > 1:
        try:
            horas = float(sys.argv[1])
        except ValueError:
            pass
    print("=" * 66)
    print("¿ESTÁ INVERTIDO EL RUMBO?")
    print("=" * 66 + "\n")
    r = paso_1(horas)
    paso_2()
    paso_3(r)
    print("\n" + "=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
