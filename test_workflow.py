"""El workflow le pasa al codigo TODO lo que el codigo espera del entorno.

Este archivo existe por un fallo real: feedback.py leia GITHUB_TOKEN de las
variables de entorno, pero el workflow nunca se lo pasaba. En Actions ese
token no se expone solo. El resultado fue silencioso: las correcciones no
se leian ni se cerraban, y nada indicaba por que.

Es un fallo de contrato entre dos archivos que nadie comparaba. Aqui se
comparan.
"""
import re, sys, yaml
sys.path.insert(0, ".")
ok = True
def chk(n, c, d=""):
    global ok
    print(f"  {'PASA' if c else 'FALLA'}  {n}" + (f"  [{d}]" if d else ""))
    if not c: ok = False

WF = ".github/workflows/nowcast.yml"
d = yaml.safe_load(open(WF))
paso = [s for s in d["jobs"]["run"]["steps"] if "env" in s][0]
entorno = set(paso["env"])

print("A. Variables que el codigo lee del entorno")
# lo que el codigo pide de verdad, extraido del propio codigo
pedidas = set()
for mod in ("config", "feedback"):
    src = open(f"nowcast/{mod}.py").read()
    pedidas |= set(re.findall(r"os\.environ\.get\(\s*[\"']([A-Z_]+)[\"']", src))
# estas las pone Actions por su cuenta
automaticas = {"GITHUB_REPOSITORY", "NTFY_SERVER"}
faltantes = pedidas - entorno - automaticas
print(f"     el codigo pide: {sorted(pedidas)}")
print(f"     el workflow da: {sorted(entorno)}")
chk("no falta ninguna variable", not faltantes, f"faltan {sorted(faltantes)}")

print("\nB. Lo que necesita el feedback")
chk("GITHUB_TOKEN se pasa al script", "GITHUB_TOKEN" in entorno)
chk("permiso de escritura en issues",
    d.get("permissions", {}).get("issues") == "write", str(d.get("permissions")))
chk("permiso de escritura en el repo",
    d.get("permissions", {}).get("contents") == "write")

print("\nC. Los canales de notificacion")
chk("canal de lluvia", "NTFY_TOPIC" in entorno)
chk("canal de presion", "NTFY_TOPIC_SALUD" in entorno)

print("\nD. Sin bucles que ocupen el runner")
texto = open(WF).read()
chk("nada de sleep largo", "sleep 900" not in texto)
chk("timeout acotado", d["jobs"]["run"]["timeout-minutes"] <= 20,
    str(d["jobs"]["run"]["timeout-minutes"]))

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
