"""El workflow le pasa al codigo TODO lo que el codigo espera del entorno.

Este archivo existe por un fallo real: feedback.py leia GITHUB_TOKEN de las
variables de entorno, pero el workflow nunca se lo pasaba. En Actions ese
token no se expone solo. El resultado fue silencioso: las correcciones no se
leian ni se cerraban, y nada indicaba por que. Es un desajuste entre dos
archivos que nadie comparaba; aqui se comparan.

Sin dependencias externas a proposito. La primera version usaba pyyaml y
fallaba en cualquier maquina limpia -lo descubrio un Raspberry Pi recien
instalado- porque los runners de GitHub ya lo traen y ocultaban el problema.
Una prueba que solo corre en CI no sirve para validar un despliegue.
"""
from __future__ import annotations

import re
import sys

ok = True


def chk(nombre: str, condicion: bool, detalle: str = "") -> None:
    global ok
    print(f"  {'PASA' if condicion else 'FALLA'}  {nombre}"
          + (f"  [{detalle}]" if detalle else ""))
    if not condicion:
        ok = False


WF = ".github/workflows/nowcast.yml"
texto = open(WF, encoding="utf-8").read()


def bloque(nombre: str) -> str:
    """Devuelve las lineas indentadas que siguen a 'nombre:'."""
    m = re.search(rf"^(\s*){nombre}:\s*$", texto, re.M)
    if not m:
        return ""
    sangria = len(m.group(1))
    fuera = []
    for linea in texto[m.end():].splitlines():
        if not linea.strip() or linea.lstrip().startswith("#"):
            continue
        if len(linea) - len(linea.lstrip()) <= sangria:
            break
        fuera.append(linea)
    return "\n".join(fuera)


def claves(bl: str) -> set[str]:
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_-]*):", bl, re.M))


print("A. Variables que el codigo lee del entorno")
pedidas: set[str] = set()
for mod in ("config", "feedback"):
    src = open(f"nowcast/{mod}.py", encoding="utf-8").read()
    pedidas |= set(re.findall(r"os\.environ\.get\(\s*[\"']([A-Z_]+)[\"']", src))

entorno = claves(bloque("env"))
# Variables que el workflow NO tiene que pasar, cada una por su motivo.
# Esta lista se amplia a mano y a proposito: cuando el codigo empieza a leer
# una variable nueva, la prueba falla y obliga a decidir si hay que pasarla
# o si pertenece aqui. Ese empujon es justo lo que faltaba el dia que
# GITHUB_TOKEN no llegaba y nadie se entero.
automaticas = {
    "GITHUB_REPOSITORY",   # la pone Actions sola
    "NTFY_SERVER",         # tiene valor por omision: ntfy.sh
    # El barometro de la malla LoRa vive en el Raspberry, no en un runner.
    # Su ausencia es el caso normal aqui y el codigo cae al modelo solo.
    "CLIMA_DB",
    # El canal de respuestas de salud solo tiene sentido en la maquina que
    # manda los avisos; en un runner no hay a quien preguntarle si le dolio.
    "NTFY_TOPIC_RESPUESTAS",
}
faltantes = pedidas - entorno - automaticas
print(f"     el codigo pide: {sorted(pedidas)}")
print(f"     el workflow da: {sorted(entorno)}")
chk("no falta ninguna variable", not faltantes, f"faltan {sorted(faltantes)}")

print("\nB. Lo que necesita el feedback")
permisos = bloque("permissions")
chk("GITHUB_TOKEN se pasa al script", "GITHUB_TOKEN" in entorno)
chk("permiso de escritura en issues",
    re.search(r"issues:\s*write", permisos) is not None, permisos.strip() or "vacio")
chk("permiso de escritura en el repo",
    re.search(r"contents:\s*write", permisos) is not None)

print("\nC. Los canales de notificacion")
chk("canal de lluvia", "NTFY_TOPIC" in entorno)
chk("canal de presion", "NTFY_TOPIC_SALUD" in entorno)

print("\nD. Sin bucles que ocupen el runner")
# Mantener un runner ocupado con sleep va contra los terminos de Actions,
# y GitHub lo hizo cumplir cancelando corridas a mitad.
chk("nada de sleep largo", "sleep 900" not in texto)
m = re.search(r"timeout-minutes:\s*(\d+)", texto)
chk("timeout acotado", m is not None and int(m.group(1)) <= 20,
    m.group(1) + " min" if m else "sin timeout")

print("\nE. Esta prueba no necesita nada instalado")
chk("sin dependencias externas",
    not re.search(r"^\s*import\s+(yaml|requests|numpy)", open(__file__).read(), re.M))

print("\nF. Coherencia del despliegue")
# Otro desajuste entre archivos que nadie comparaba: correr.sh asumia
# $HOME/nowcast-ags mientras el instalador acepta DESTINO en otro disco.
# systemd arrancaba el servicio y el script moria en el primer cd.
def sin_comentarios(texto: str) -> str:
    """Los comentarios explican fallos pasados y citan el codigo malo."""
    return "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))

correr = sin_comentarios(open("deploy/correr.sh", encoding="utf-8").read())
servicio = sin_comentarios(open("deploy/nowcast.service", encoding="utf-8").read())
instalador = open("deploy/instalar.sh", encoding="utf-8").read()

chk("correr.sh no da por hecho que el proyecto esta en el home",
    "$HOME/nowcast-ags" not in correr)
chk("el script se protege de reescribirse a si mismo",
    "NOWCAST_REEJECUTADO" in correr,
    "git pull cambia correr.sh mientras bash lo lee")
chk("el push va autenticado",
    "credential.helper" in correr, "git push sin credenciales falla en systemd")
chk("el token no acaba en .git/config",
    "credential.helper store" not in correr and "remote set-url" not in correr)
chk("un fallo no se reporta como exito",
    not re.search(r"^SuccessExitStatus", servicio, re.M))

# Todo marcador __ASI__ del .service tiene que sustituirlo el instalador.
marcadores = set(re.findall(r"__([A-Z]+)__", servicio))
sustituidos = set(re.findall(r"s\|__([A-Z]+)__\|", instalador))
chk("el instalador sustituye todos los marcadores",
    marcadores <= sustituidos, f"sin sustituir: {sorted(marcadores - sustituidos)}")

print("\nG. Las pruebas no leen nada de la maquina")
# La suite empezo a depender del entorno sin que nadie lo notara: al añadir
# el barometro de la malla, una prueba de presion sintetica pasaba a leer la
# presion REAL de la casa si el archivo existia. Pasaba en un portatil y
# fallaba en el Raspberry.
import glob as _glob
sueltas = []
for ruta in sorted(_glob.glob("test_*.py")):
    if ruta == "test_barometro.py":
        continue          # esa prueba SI es sobre el sensor; apunta a un temporal
    cuerpo = open(ruta, encoding="utf-8").read()
    usa_presion = "pressure.fetch" in cuerpo or "_pr.fetch" in cuerpo
    if usa_presion and 'CLIMA_DB = ""' not in cuerpo:
        sueltas.append(ruta)
chk("ninguna prueba de presion depende del sensor real",
    not sueltas, ", ".join(sueltas) or "—")

corredor = open("pruebas.sh", encoding="utf-8").read()
chk("el corredor tambien lo neutraliza", 'export CLIMA_DB=""' in corredor)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
