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
automaticas = {"GITHUB_REPOSITORY", "NTFY_SERVER"}   # las pone Actions o tienen valor por omision
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

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
