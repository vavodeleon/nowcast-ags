"""Autoprueba con datos sinteticos.

No toca la red. Construye tormentas de laboratorio con movimiento conocido
y comprueba que el motor recupera ese movimiento, acierta el momento del
impacto y que la calibracion realmente mejora las probabilidades.

    python selftest.py
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

from nowcast import calibrate, config, engine
from nowcast.sources import Frame

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASA' if ok else 'FALLA'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(name)


def make_storm(size: int, cy: float, cx: float, radius: float,
               peak_dbz: float = 50.0) -> np.ndarray:
    """Una celda gaussiana en un campo de dBZ (NaN = sin eco)."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    d2 = (yy - cy) ** 2 + (xx - cx) ** 2
    field = peak_dbz * np.exp(-d2 / (2 * radius ** 2))
    field[field < 5.0] = np.nan
    return field.astype(np.float32)


def synthetic_sequence(vy: float, vx: float, n: int = 5, size: int = 256,
                       step_min: int = 10, start_offset: tuple = (0, -80),
                       km_per_px: float = 0.57):
    """Secuencia de frames con una celda que se mueve a (vy, vx) px/min."""
    t0 = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    home = size / 2.0
    frames = []
    for i in range(n):
        # el ultimo frame es t=0; los anteriores estan atras en el tiempo
        k = i - (n - 1)
        cy = home + start_offset[0] + vy * step_min * k
        cx = home + start_offset[1] + vx * step_min * k
        frames.append(Frame(
            time=t0 + timedelta(minutes=step_min * k),
            data=make_storm(size, cy, cx, radius=14.0),
            km_per_px=km_per_px, center_lat=config.LAT, center_lon=config.LON,
            kind="radar"))
    return frames


# ---------------------------------------------------------------- pruebas

def test_motion_recovery() -> None:
    print("\n1. Recuperacion del movimiento (correlacion de fase)")
    for label, vy, vx in [("hacia el este", 0.0, 0.9),
                          ("hacia el noreste", -0.6, 0.6),
                          ("hacia el sur", 0.8, 0.0)]:
        frames = synthetic_sequence(vy, vx, start_offset=(0, -80))
        m = engine.estimate_motion(frames)
        err = math.hypot(m.vy_px_min - vy, m.vx_px_min - vx)
        check(f"velocidad {label}", err < 0.18,
              f"esperado ({vy:.2f},{vx:.2f}) obtenido ({m.vy_px_min:.2f},{m.vx_px_min:.2f})")

    # direccion de origen en lenguaje humano: celda que viaja al este viene del oeste
    frames = synthetic_sequence(0.0, 0.9, start_offset=(0, -80))
    m = engine.estimate_motion(frames)
    check("nombra bien el origen", m.from_direction == "oeste", m.from_direction)
    check("velocidad en km/h razonable", 20 < m.speed_kmh < 45,
          f"{m.speed_kmh:.1f} km/h")


def test_impact_timing() -> None:
    print("\n2. Momento del impacto")
    # celda 80 px al oeste, viajando al este a 0.8 px/min -> impacto en ~100 min
    frames = synthetic_sequence(0.0, 0.8, start_offset=(0, -80))
    nc = engine.run_nowcast(frames)
    check("produce nowcast", nc is not None)
    if nc is None:
        return

    scores = {lead.lead_min: lead.score for lead in nc.leads}
    print("     scores:", {k: round(v, 3) for k, v in scores.items()})

    check("ahora mismo no llueve", nc.current_score < 0.15,
          f"{nc.current_score:.3f}")
    check("a +15 min sigue sin llover", scores[15] < 0.25, f"{scores[15]:.3f}")
    check("a +120 min hay senal fuerte", scores[120] > 0.35, f"{scores[120]:.3f}")
    check("la senal crece con el plazo", scores[120] > scores[30],
          f"{scores[30]:.3f} -> {scores[120]:.3f}")

    check("detecta celda entrante", nc.nearest_cell_eta_min is not None)
    if nc.nearest_cell_eta_min is not None:
        eta = nc.nearest_cell_eta_min
        check("ETA cercano a 100 min", 80 < eta < 125, f"{eta:.0f} min")


def test_departing_cell() -> None:
    print("\n3. Celda que se aleja (no debe alarmar)")
    # celda 80 px al ESTE viajando al este: se va
    frames = synthetic_sequence(0.0, 0.8, start_offset=(0, 80))
    nc = engine.run_nowcast(frames)
    check("ignora celda que se aleja", nc.nearest_cell_eta_min is None,
          str(nc.nearest_cell_eta_min))
    check("probabilidad baja a +60", nc.score_at(60) < 0.25,
          f"{nc.score_at(60):.3f}")


def test_glancing_cell() -> None:
    print("\n4. Celda que pasa de largo")
    # celda muy al norte moviendose al este: no cruza por casa
    frames = synthetic_sequence(0.0, 0.8, start_offset=(-95, -60))
    nc = engine.run_nowcast(frames)
    check("descarta celda fuera del corredor", nc.nearest_cell_eta_min is None,
          str(nc.nearest_cell_eta_min))


def test_direct_hit_now() -> None:
    print("\n5. Celda encima en este momento")
    frames = synthetic_sequence(0.0, 0.5, start_offset=(0, 0))
    nc = engine.run_nowcast(frames)
    check("detecta lluvia actual", nc.current_score > 0.6, f"{nc.current_score:.3f}")


def test_clear_sky() -> None:
    print("\n6. Cielo despejado")
    size = 256
    t0 = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)
    frames = [Frame(time=t0 + timedelta(minutes=10 * i),
                    data=np.full((size, size), np.nan, dtype=np.float32),
                    km_per_px=0.57, center_lat=config.LAT, center_lon=config.LON,
                    kind="radar") for i in range(-4, 1)]
    nc = engine.run_nowcast(frames)
    check("no inventa lluvia", nc is not None and max(
        l.score for l in nc.leads) < 0.01)
    check("no inventa movimiento", nc.motion.speed_kmh < 1.0,
          f"{nc.motion.speed_kmh:.2f}")


def test_growth_detection() -> None:
    print("\n7. Crecimiento y disipacion")
    t0 = datetime(2026, 8, 4, 18, 0, tzinfo=timezone.utc)

    def seq(peaks):
        return [Frame(time=t0 + timedelta(minutes=10 * i),
                      data=make_storm(256, 128, 60 + 8 * i, 14.0, peak_dbz=p),
                      km_per_px=0.57, center_lat=config.LAT,
                      center_lon=config.LON, kind="radar")
                for i, p in enumerate(peaks)]

    growing = engine.growth_rate(seq([30, 38, 46, 54]))
    decaying = engine.growth_rate(seq([54, 46, 38, 30]))
    check("detecta crecimiento", growing > 1.05, f"{growing:.3f}")
    check("detecta disipacion", decaying < 0.95, f"{decaying:.3f}")


def test_geostationary_projection() -> None:
    print("\n7b. Proyeccion geoestacionaria de GOES-19")
    from nowcast.goes import FixedGrid, _haversine_km

    # parametros reales del ABI de GOES (GOES-East a 75.2 W)
    grid = FixedGrid({
        "semi_major_axis": 6378137.0,
        "semi_minor_axis": 6356752.31414,
        "perspective_point_height": 35786023.0,
        "longitude_of_projection_origin": -75.2,
    })

    # ida y vuelta: (lat,lon) -> barrido -> (lat,lon)
    for lat, lon in [(21.8853, -102.2916), (19.43, -99.13), (0.0, -75.2),
                     (40.0, -80.0)]:
        scan = grid.lonlat_to_scan(lat, lon)
        check(f"visible {lat},{lon}", scan is not None)
        if scan is None:
            continue
        back = grid.scan_to_lonlat(*scan)
        err = _haversine_km((lat, lon), back)
        check(f"ida y vuelta {lat},{lon}", err < 0.5, f"{err * 1000:.0f} m")

    # el subpunto satelital debe caer en (0,0) de barrido
    sub = grid.lonlat_to_scan(0.0, -75.2)
    check("subpunto en el origen", abs(sub[0]) < 1e-9 and abs(sub[1]) < 1e-9)

    # el lado opuesto del planeta no es visible
    check("descarta el lado oculto", grid.lonlat_to_scan(0.0, 104.8) is None)

    # tamano real del pixel sobre Aguascalientes: 2 km en el nadir se estira
    x, y = grid.lonlat_to_scan(21.8853, -102.2916)
    step = 5.6e-5  # resolucion angular de 2 km del ABI, en radianes
    p0 = grid.scan_to_lonlat(x, y)
    p1 = grid.scan_to_lonlat(x + step, y)
    km = _haversine_km(p0, p1)
    check("pixel de ~2-4 km sobre la ciudad", 2.0 < km < 4.5, f"{km:.2f} km")

    # el pixel debe ser mas grande aqui que en el nadir (angulo de vision)
    n0 = grid.scan_to_lonlat(0.0, 0.0)
    n1 = grid.scan_to_lonlat(step, 0.0)
    km_nadir = _haversine_km(n0, n1)
    check("mayor que en el nadir", km > km_nadir,
          f"{km:.2f} vs {km_nadir:.2f} km")


def test_goes_unpacking() -> None:
    """Un archivo GOES sintetico, empaquetado igual que los reales.

    Los datos vienen como enteros con scale_factor/add_offset. h5py NO los
    desempaqueta solo. Esta prueba existe porque olvidarlo hizo que el
    sistema devolviera cero cuadros de infrarrojo en produccion, sin error
    visible: simplemente se caia de vuelta a los modelos numericos.
    """
    print("\n7c. Desempaquetado de archivos GOES")
    import io as _io
    import h5py

    from nowcast import config, goes

    NX, NY = 500, 300
    x_scale, x_off = 5.6e-5, -0.101332
    y_scale, y_off = -5.6e-5, 0.128212
    # escala coherente con uint16: 89.62 K + 65535 pasos llega a ~352 K,
    # que cubre todo el rango fisico de la banda 13
    bt_scale, bt_off = 0.004, 89.62

    buf = _io.BytesIO()
    with h5py.File(buf, "w") as fh:
        proj = fh.create_dataset("goes_imager_projection", data=0)
        proj.attrs["semi_major_axis"] = np.float64(6378137.0)
        proj.attrs["semi_minor_axis"] = np.float64(6356752.31414)
        proj.attrs["perspective_point_height"] = np.float64(35786023.0)
        proj.attrs["longitude_of_projection_origin"] = np.float64(-75.2)

        dx = fh.create_dataset("x", data=np.arange(NX, dtype=np.int16))
        dx.attrs["scale_factor"] = np.float32(x_scale)
        dx.attrs["add_offset"] = np.float32(x_off)
        dy = fh.create_dataset("y", data=np.arange(NY, dtype=np.int16))
        dy.attrs["scale_factor"] = np.float32(y_scale)
        dy.attrs["add_offset"] = np.float32(y_off)

        # 280 K de fondo con una celda fria de 200 K, en enteros empaquetados
        bt = np.full((NY, NX), 280.0)
        yy, xx = np.mgrid[0:NY, 0:NX]
        bt -= 80.0 * np.exp(-((yy - 150) ** 2 + (xx - 250) ** 2) / (2 * 20.0 ** 2))
        packed = np.round((bt - bt_off) / bt_scale).astype(np.uint16)
        cmi = fh.create_dataset("CMI", data=packed)
        cmi.attrs["scale_factor"] = np.float32(bt_scale)
        cmi.attrs["add_offset"] = np.float32(bt_off)
        cmi.attrs["_FillValue"] = np.uint16(65535)

    payload = buf.getvalue()

    # el desempaquetado debe devolver Kelvin, no enteros crudos
    with h5py.File(_io.BytesIO(payload), "r") as fh:
        vals = goes.unpack(fh["CMI"], np.s_[150, 250])
        crudo = float(np.asarray(fh["CMI"][150, 250]))
    check("recupera Kelvin del tope frio", abs(float(vals) - 200.0) < 1.0,
          f"{float(vals):.1f} K (crudo: {crudo:.0f})")
    check("el crudo NO son Kelvin", crudo > 1000, f"{crudo:.0f}")

    # la ventana debe centrarse donde toca y traer temperaturas plausibles
    saved = (config.LAT, config.LON)
    with h5py.File(_io.BytesIO(payload), "r") as fh:
        gx = goes.FixedGrid({"semi_major_axis": 6378137.0,
                             "semi_minor_axis": 6356752.31414,
                             "perspective_point_height": 35786023.0,
                             "longitude_of_projection_origin": -75.2})
        target = gx.scan_to_lonlat(x_off + 250 * x_scale, y_off + 150 * y_scale)
    config.LAT, config.LON = target

    got = goes._read_window(payload, 60)
    check("lee la ventana", got is not None)
    if got is not None:
        window, km_per_px = got
        centro = float(window[60, 60])
        check("centrada en la celda fria", abs(centro - 200.0) < 2.0,
              f"{centro:.1f} K")
        check("bordes calidos", abs(float(window[0, 0]) - 280.0) < 2.0,
              f"{float(window[0, 0]):.1f} K")
        # el punto sintetico cae cerca del borde norte del sector, donde el
        # angulo de vision estira mucho el pixel; el rango es mas ancho que
        # el 2.44 km real sobre Aguascalientes
        check("km/pixel plausible", 2.0 < km_per_px < 8.0, f"{km_per_px:.2f} km")

    # fuera del sector debe rendirse, no devolver basura
    config.LAT, config.LON = -40.0, -70.0
    check("descarta si cae fuera del sector",
          goes._read_window(payload, 60) is None)
    config.LAT, config.LON = saved


def test_field_of_view() -> None:
    print("\n8. Limite del campo de vision")
    # a 0.8 px/min, +180 min = 144 px: el origen queda fuera de la imagen
    frames = synthetic_sequence(0.0, 0.8, start_offset=(0, -80))
    nc = engine.run_nowcast(frames)
    vis = {l.lead_min: (l.in_domain, l.visible_fraction) for l in nc.leads}
    print("     visibilidad:", vis)
    check("horizontes cortos son visibles", nc.sees(15) and nc.sees(60))
    check("+180 min queda fuera de vista", not nc.sees(180),
          f"visible {vis[180][1]:.2f}")

    # celda quieta: todo el rango es visible
    frames = synthetic_sequence(0.0, 0.0, start_offset=(0, 0))
    nc = engine.run_nowcast(frames)
    check("sin movimiento todo es visible", all(l.in_domain for l in nc.leads))


def test_isotonic() -> None:
    print("\n8. Regresion isotonica (calibracion)")
    rng = np.random.default_rng(7)
    n = 800
    # el sistema esta sobreconfiado: dice p, la realidad es p^2
    raw = rng.uniform(0, 1, n)
    truth = raw ** 2
    y = (rng.uniform(0, 1, n) < truth).astype(float)
    w = np.ones(n)

    xs, ys = calibrate._pava(raw, y, w)
    check("salida monotona", bool(np.all(np.diff(ys) >= -1e-9)))

    before = float(np.mean((raw - y) ** 2))
    corrected = np.interp(raw, xs, ys)
    after = float(np.mean((corrected - y) ** 2))
    check("mejora el Brier score", after < before,
          f"{before:.4f} -> {after:.4f}")

    # el ajuste debe parecerse a la funcion verdadera p^2
    err = float(np.mean(np.abs(np.interp(raw, xs, ys) - truth)))
    check("recupera la curva real", err < 0.08, f"error medio {err:.4f}")


def test_weights() -> None:
    print("\n9. Aprendizaje de pesos")
    cal = {"weights": {"60": {"radar": 0.7, "ir": 0.2, "models": 0.1}},
           "curves": {"60": {"x": [0.0, 0.5, 1.0], "y": [0.0, 0.3, 0.9]}},
           "skill": {"60": {"n": 2000}}}   # muchas muestras: confianza total
    w = calibrate.weights_for(60, cal)
    check("lee pesos aprendidos", abs(w["radar"] - 0.7) < 1e-9)
    check("usa prior si no hay nada", calibrate.weights_for(999, cal)["radar"]
          == calibrate.DEFAULT_WEIGHTS["radar"])
    check("aplica la curva", abs(calibrate.apply_curve(0.5, 60, cal) - 0.3) < 1e-6,
          str(calibrate.apply_curve(0.5, 60, cal)))
    check("sin curva no altera", calibrate.apply_curve(0.42, 999, cal) == 0.42)

    # con pocas muestras debe encogerse hacia la probabilidad cruda
    pocos = dict(cal, skill={"60": {"n": 50}})
    shrunk = calibrate.apply_curve(0.5, 60, pocos)
    check("encoge con pocas muestras", 0.3 < shrunk < 0.5, f"{shrunk:.3f}")

    # nunca debe anunciar certeza absoluta
    extremo = {"curves": {"60": {"x": [0.0, 0.5, 1.0], "y": [0.0, 1.0, 1.0]}},
               "skill": {"60": {"n": 5000}}, "weights": {}}
    top = calibrate.apply_curve(0.9, 60, extremo)
    bottom = calibrate.apply_curve(0.0, 60, extremo)
    check("nunca dice 100%", top <= calibrate.P_CEIL, f"{top:.3f}")
    check("nunca dice 0%", bottom >= calibrate.P_FLOOR, f"{bottom:.3f}")


def test_signal_normalisation() -> None:
    print("\n10. Normalizacion de campos")
    radar = Frame(time=datetime.now(timezone.utc),
                  data=np.array([[np.nan, 20.0], [30.0, 45.0]], dtype=np.float32),
                  km_per_px=0.57, center_lat=0, center_lon=0, kind="radar")
    sig = engine.to_signal(radar)
    check("NaN -> 0", sig[0, 0] == 0.0)
    check("umbral de traza -> 0", abs(sig[0, 1]) < 1e-6)
    check("tormenta -> 1", abs(sig[1, 1] - 1.0) < 1e-6)
    check("valor intermedio", 0.3 < sig[1, 0] < 0.5, f"{sig[1, 0]:.3f}")

    ir = Frame(time=datetime.now(timezone.utc),
               data=np.array([[280.0, 235.0], [227.5, 210.0]], dtype=np.float32),
               km_per_px=2.0, center_lat=0, center_lon=0, kind="ir")
    sig = engine.to_signal(ir)
    check("IR calido -> 0", sig[0, 0] == 0.0)
    check("IR muy frio -> 1", abs(sig[1, 1] - 1.0) < 1e-6)
    check("IR intermedio", 0.4 < sig[1, 0] < 0.6, f"{sig[1, 0]:.3f}")


def main() -> int:
    print("Autoprueba del motor de nowcasting")
    print("=" * 55)
    for fn in (test_motion_recovery, test_impact_timing, test_departing_cell,
               test_glancing_cell, test_direct_hit_now, test_clear_sky,
               test_growth_detection, test_geostationary_projection,
               test_goes_unpacking,
               test_field_of_view, test_isotonic,
               test_weights, test_signal_normalisation):
        try:
            fn()
        except Exception as exc:  # una prueba rota no debe ocultar las demas
            import traceback
            traceback.print_exc()
            FAILURES.append(f"{fn.__name__} (excepcion: {exc})")

    print("\n" + "=" * 55)
    if FAILURES:
        print(f"{len(FAILURES)} prueba(s) fallaron:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("Todo en orden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
