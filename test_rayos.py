"""Rayos (GLM) y temperatura: filtrado espacial, agrupado y ventana de 1 hora."""
import io, os, sys, json
from datetime import datetime, timedelta, timezone
import numpy as np, h5py
sys.path.insert(0, ".")
ok = True
def chk(n, c, d=""):
    global ok
    print(f"  {'PASA' if c else 'FALLA'}  {n}" + (f"  [{d}]" if d else ""))
    if not c: ok = False

from nowcast import lightning, config, store, sources, http

def glm(coords):
    """Archivo GLM sintetico con destellos en (lat, lon)."""
    buf = io.BytesIO()
    with h5py.File(buf, "w") as fh:
        fh.create_dataset("flash_lat", data=np.array([c[0] for c in coords], dtype=np.float32))
        fh.create_dataset("flash_lon", data=np.array([c[1] for c in coords], dtype=np.float32))
    return buf.getvalue()

print("A. Filtrado por cercania")
cerca = (config.LAT + 0.5, config.LON - 0.5)
lejos = (config.LAT + 20.0, config.LON + 20.0)
got = lightning._flashes(glm([cerca, lejos, cerca]))
chk("conserva los cercanos", len(got) == 2, f"{len(got)} de 3")
chk("descarta los lejanos", all(abs(a-config.LAT) < 5 for a,_ in got))
chk("archivo vacio no truena", lightning._flashes(glm([])) == [])
chk("archivo corrupto no truena", lightning._flashes(b"no soy netcdf") == [])

print("\nB. Agrupado en rejilla")
muchos = [(21.840, -102.280)] * 50 + [(21.842, -102.281)] * 30 + [(22.5, -101.9)] * 5
ag = lightning._agrupar(muchos)
chk("agrupa en pocos puntos", len(ag) <= 3, f"{len(ag)} celdas de 85 destellos")
chk("conserva el total", sum(p[2] for p in ag) == 85, str(sum(p[2] for p in ag)))
chk("ordena por intensidad", ag[0][2] >= ag[-1][2], f"{ag[0][2]} >= {ag[-1][2]}")
chk("sin destellos devuelve vacio", lightning._agrupar([]) == [])

grande = [(21.0 + i*0.001, -102.0 + i*0.001) for i in range(5000)]
chk("limita el tamano del JSON",
    len(lightning._agrupar(grande)) <= lightning.MAX_PUNTOS_POR_BLOQUE,
    f"{len(lightning._agrupar(grande))} puntos")

print("\nC. Ventana de una hora")
config.LIGHTNING_JSON = "/tmp/rayos_test.json"
if os.path.exists(config.LIGHTNING_JSON): os.remove(config.LIGHTNING_JSON)
ahora = datetime.now(timezone.utc)
viejos = {"bloques": [
    {"t": store.round_slot(ahora - timedelta(minutes=m)), "puntos": [[21.8,-102.2,3]], "total": 3}
    for m in (90, 75, 50, 30, 15)]}
store.save_json(config.LIGHTNING_JSON, viejos)
lightning.fetch_recent = lambda minutos=15: [(21.85, -102.29)]
d = lightning.update()
edades = [b["edad_min"] for b in d["bloques"]]
chk("descarta lo de mas de una hora", all(e <= 60 for e in edades), str(edades))
chk("conserva los recientes", len(d["bloques"]) >= 3, f"{len(d['bloques'])} bloques")
chk("cada bloque trae su edad", all("edad_min" in b for b in d["bloques"]))
chk("ordenados por tiempo", edades == sorted(edades, reverse=True), str(edades))
chk("escribe el total", "total_hora" in d)

antes = len(d["bloques"])
d2 = lightning.update()
chk("no duplica el bloque actual", len(d2["bloques"]) == antes,
    f"{antes} -> {len(d2['bloques'])}")

print("\nD. Si el GLM falla, no rompe nada")
lightning.fetch_recent = lambda minutos=15: (_ for _ in ()).throw(RuntimeError("S3 caido"))
d3 = lightning.update()
chk("sobrevive al fallo", isinstance(d3, dict) and "bloques" in d3)

print("\nE. Temperatura")
http.get_json = lambda *a, **kw: {
  "current": {"temperature_2m": 24.7, "apparent_temperature": 26.9,
              "relative_humidity_2m": 55},
  "hourly": {"time": [f"2026-08-06T{h:02d}:00" for h in range(24)],
             "temperature_2m": [20+h*0.3 for h in range(24)],
             "apparent_temperature": [21+h*0.3 for h in range(24)]},
  "daily": {"temperature_2m_max": [29.4], "temperature_2m_min": [15.2]}}
t = sources.fetch_temperature()
chk("lee la actual", t["ahora"] == 24.7, str(t["ahora"]))
chk("lee la sensacion", t["sensacion"] == 26.9, str(t["sensacion"]))
chk("lee maxima y minima", t["maxima"] == 29.4 and t["minima"] == 15.2)
chk("serie de 24 h", len(t["serie"]) >= 24, f"{len(t['serie'])} puntos")
chk("cada punto trae ambas curvas",
    all("temp" in p and "sensacion" in p for p in t["serie"]))
chk("hora en formato corto", t["serie"][0]["t"] == "00:00", t["serie"][0]["t"])

http.get_json = lambda *a, **kw: None
chk("sin respuesta devuelve vacio", sources.fetch_temperature() == {})

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
