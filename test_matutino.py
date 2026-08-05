"""El reporte matutino: ventana horaria y no repetir en el mismo dia."""
import sys, json, os
from datetime import datetime, timedelta
sys.path.insert(0, ".")
ok = True
def chk(n, c, d=""):
    global ok
    print(f"  {'PASA' if c else 'FALLA'}  {n}" + (f"  [{d}]" if d else ""))
    if not c: ok = False

from nowcast import config, daily, store
import nowcast.daily as D

enviados = []
D.send_all = lambda: enviados.append(datetime.now(config.TZ))
config.STATE_JSON = "/tmp/state_test.json"
if os.path.exists(config.STATE_JSON): os.remove(config.STATE_JSON)

class FakeDT(datetime):
    _now = None
    @classmethod
    def now(cls, tz=None): return cls._now

print("A. Ventana horaria")
real = D.datetime
D.datetime = FakeDT
base = datetime.now(config.TZ).replace(year=2026, month=8, day=6)

casos = [
    (5, 30, False, "5:30 es demasiado temprano"),
    (6, 15, False, "6:15 aun no toca"),
    (6, 30, True,  "6:30 justo en punto SI envia"),
    (7, 45, True,  "7:45 dentro de la ventana"),
    (10, 0, True,  "10:00 ultimo momento util"),
    (11, 0, False, "11:00 ya paso la ventana"),
    (23, 0, False, "23:00 no envia"),
]
for h, m, esperado, etiqueta in casos:
    if os.path.exists(config.STATE_JSON): os.remove(config.STATE_JSON)
    enviados.clear()
    FakeDT._now = base.replace(hour=h, minute=m)
    got = D.maybe_send_morning()
    chk(etiqueta, got == esperado, f"envio={got}")

print("\nB. No repetir el mismo dia")
if os.path.exists(config.STATE_JSON): os.remove(config.STATE_JSON)
enviados.clear()
FakeDT._now = base.replace(hour=6, minute=30)
chk("primera vez envia", D.maybe_send_morning() is True)
FakeDT._now = base.replace(hour=6, minute=45)
chk("15 min despues NO repite", D.maybe_send_morning() is False)
FakeDT._now = base.replace(hour=9, minute=0)
chk("tres horas despues tampoco", D.maybe_send_morning() is False)
chk("solo se envio una vez", len(enviados) == 1, f"{len(enviados)} envios")

print("\nC. Al dia siguiente vuelve a enviar")
FakeDT._now = (base + timedelta(days=1)).replace(hour=6, minute=30)
chk("nuevo dia SI envia", D.maybe_send_morning() is True)
chk("van dos envios en total", len(enviados) == 2, f"{len(enviados)}")

print("\nD. Si el sistema estuvo caido, lo manda tarde")
if os.path.exists(config.STATE_JSON): os.remove(config.STATE_JSON)
enviados.clear()
FakeDT._now = base.replace(hour=9, minute=20)   # se perdio la hora exacta
chk("recupera el envio a las 9:20", D.maybe_send_morning() is True)

D.datetime = real
print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
