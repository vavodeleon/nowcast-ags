"""Pruebas de las piezas nuevas: presion, reproyeccion e imagen del mapa."""
import io, sys, math
import numpy as np, h5py
sys.path.insert(0, ".")

ok = True
def chk(name, cond, detail=""):
    global ok
    print(f"  {'PASA' if cond else 'FALLA'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not cond: ok = False

# ---------------- niveles de presion
print("A. Niveles de riesgo por presion")
from nowcast import pressure, config
for caida, esperado in [(0.5,"tranquilo"),(3.5,"vigilancia"),(6.0,"alto"),(9.0,"muy alto")]:
    got = pressure._level_for(caida)
    chk(f"caida de {caida} hPa -> {esperado}", got == esperado, got)
chk("sin datos", pressure._level_for(None) == "sin datos")

print("\nB. Deteccion sobre serie sintetica")
from datetime import datetime, timedelta, timezone
from nowcast import http
now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
# 30 h pasadas + 48 futuras; caida de 9 hPa concentrada en el futuro
times, vals = [], []
for k in range(-30, 49):
    t = now + timedelta(hours=k)
    times.append(t.strftime("%Y-%m-%dT%H:%M"))
    vals.append(1015.0 if k <= 6 else 1015.0 - 9.0 * min(1.0, (k-6)/18.0))
http.get_json = lambda *a, **kw: {"hourly": {"time": times, "pressure_msl": vals,
                                             "surface_pressure": [v-208 for v in vals]}}
st = pressure.fetch()
chk("lee la presion actual", abs(st.now_msl - 1015.0) < 0.6, f"{st.now_msl}")
chk("detecta la caida futura", st.forecast_drop is not None and st.forecast_drop >= 8,
    f"{st.forecast_drop}")
chk("nivel muy alto", st.level == "muy alto", st.level)
chk("marca riesgo proximo", st.is_risky_soon)
chk("serie para la grafica", len(st.series) > 40, f"{len(st.series)} puntos")
chk("distingue futuro de pasado", any(s["futuro"] for s in st.series)
    and any(not s["futuro"] for s in st.series))
d = pressure.to_dict(st)
chk("serializa a dict", d["level"] == "muy alto" and "series" in d)

# presion estable no debe alarmar
http.get_json = lambda *a, **kw: {"hourly": {"time": times,
    "pressure_msl": [1015.0]*len(times), "surface_pressure":[807.0]*len(times)}}
st2 = pressure.fetch()
chk("presion estable = tranquilo", st2.level == "tranquilo", st2.level)
chk("no marca riesgo", not st2.is_risky_soon)
chk("no marca caida en curso", not st2.is_falling_now)

print("\nC. Colores del satelite")
from nowcast import render
bt = np.array([[300.0, 250.0], [235.0, 200.0]])
rgba = render.colorize(bt)
chk("calido = transparente", rgba[0,0,3] == 0, str(rgba[0,0]))
chk("umbral 250 K aun transparente", rgba[0,1,3] == 0, str(rgba[0,1,3]))
chk("235 K ya visible", rgba[1,0,3] > 100, str(rgba[1,0,3]))
chk("200 K casi opaco", rgba[1,1,3] > 230, str(rgba[1,1,3]))
chk("mas frio = mas opaco", rgba[1,1,3] > rgba[1,0,3])
nan = render.colorize(np.array([[np.nan]]))
chk("NaN invisible", nan[0,0,3] == 0)

print("\nD. Reproyeccion a lat/lon")
from nowcast.goes import FixedGrid, scan_grid_vectorized
grid = FixedGrid({"semi_major_axis":6378137.0,"semi_minor_axis":6356752.31414,
                  "perspective_point_height":35786023.0,
                  "longitude_of_projection_origin":-75.2})
lats = np.array([[21.84, 25.0],[15.0, 21.84]])
lons = np.array([[-102.28, -100.0],[-99.0, -102.28]])
vx, vy = scan_grid_vectorized(grid, lats, lons)
for i in range(2):
    for j in range(2):
        sx, sy = grid.lonlat_to_scan(lats[i,j], lons[i,j])
        chk(f"vectorizado == escalar ({i},{j})",
            abs(vx[i,j]-sx) < 1e-12 and abs(vy[i,j]-sy) < 1e-12)
# lado oculto del planeta -> NaN
hx, hy = scan_grid_vectorized(grid, np.array([[0.0]]), np.array([[104.8]]))
chk("lado oculto -> NaN", np.isnan(hx[0,0]))

print("\nE. Imagen completa del satelite")
from nowcast.sources import Frame
NX, NY = 400, 400
xs = -0.101332 + np.arange(NX)*5.6e-5
ys = 0.128212 - np.arange(NY)*5.6e-5
scan = grid.lonlat_to_scan(config.LAT, config.LON)
i0 = int(round((scan[0]-xs[0])/5.6e-5)) - 100
j0 = int(round((scan[1]-ys[0])/(-5.6e-5))) - 100
bt = np.full((200,200), 290.0)
yy, xx = np.mgrid[0:200,0:200]
bt -= 100*np.exp(-((yy-100)**2+(xx-100)**2)/(2*15.0**2))
f = Frame(time=datetime.now(timezone.utc), data=bt.astype(np.float32),
          km_per_px=2.45, center_lat=config.LAT, center_lon=config.LON, kind="ir")
f.grid_meta = {"proj":{"semi_major_axis":6378137.0,"semi_minor_axis":6356752.31414,
    "perspective_point_height":35786023.0,"longitude_of_projection_origin":-75.2},
    "x0":xs[0],"dx":5.6e-5,"i0":i0,"y0":ys[0],"dy":-5.6e-5,"j0":j0}
import tempfile, os
path = os.path.join(tempfile.gettempdir(), "sat.png")
bounds = render.render(f, path, size=300)
chk("genera la imagen", bounds is not None and os.path.exists(path))
if bounds:
    (s,w),(n,e) = bounds
    chk("la ciudad cae dentro", s < config.LAT < n and w < config.LON < e,
        f"lat {s:.2f}..{n:.2f}  lon {w:.2f}..{e:.2f}")
    chk("extension razonable (~5 grados)", 3 < (n-s) < 8, f"{n-s:.2f} grados")
    from PIL import Image
    im = np.asarray(Image.open(path))
    chk("PNG con transparencia", im.shape[2] == 4, str(im.shape))
    chk("hay pixeles opacos (la celda)", im[...,3].max() > 200, str(im[...,3].max()))
    chk("hay pixeles transparentes (fondo)", im[...,3].min() == 0)
    centro = im[im.shape[0]//2, im.shape[1]//2, 3]
    chk("la celda quedo centrada donde va", centro > 200, f"alfa {centro}")

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
