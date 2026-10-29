"""deploy/reparar-git.sh: reproduce el apagón del 28/10/2026 y lo repara.

Se monta un "GitHub" (repositorio desnudo) y un "Pi" que publica en él. El
Pi hace un commit nuevo y se le vacían los objetos de ese commit, como dejó
el reinicio de las 23:46. El reparador tiene que dejar git sano, de vuelta a
lo que tiene GitHub, SIN tocar los datos que el Pi calculó después.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time

ok = True
RAIZ = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(RAIZ, "deploy", "reparar-git.sh")


def chk(nombre, cond, detalle=""):
    global ok
    print(f"  {'PASA' if cond else 'FALLA'}  {nombre}" + (f"  [{detalle}]" if detalle else ""))
    if not cond:
        ok = False


def sh(*args, cwd, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                                GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1"))
    if check and r.returncode:
        raise RuntimeError(f"{args}: {r.stderr}")
    return r


def montar():
    base = tempfile.mkdtemp()
    remoto = os.path.join(base, "github.git")
    pi = os.path.join(base, "pi")
    sh("git", "init", "-q", "--bare", "-b", "main", remoto, cwd=base)
    sh("git", "clone", "-q", remoto, pi, cwd=base)
    sh("git", "checkout", "-q", "-b", "main", cwd=pi)
    os.makedirs(os.path.join(pi, "data"))
    with open(os.path.join(pi, "data", "pred.csv"), "w") as f:
        f.write("t,p\n1,0.1\n")
    sh("git", "add", "data", cwd=pi)
    sh("git", "commit", "-q", "-m", "23:31", cwd=pi)
    sh("git", "push", "-q", "origin", "main", cwd=pi)
    sh("git", "fetch", "-q", "origin", cwd=pi)
    return base, pi


def reparar(pi):
    return subprocess.run(["bash", SCRIPT, pi], capture_output=True, text=True,
                          env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull,
                                   GIT_CONFIG_NOSYSTEM="1"))


print("A. Un repositorio sano no se toca")
base, pi = montar()
antes = sh("git", "rev-parse", "HEAD", cwd=pi).stdout
r = reparar(pi)
chk("sale bien y en silencio", r.returncode == 0 and "reparado" not in r.stdout,
    r.stdout + r.stderr)
chk("y HEAD sigue igual", sh("git", "rev-parse", "HEAD", cwd=pi).stdout == antes)
shutil.rmtree(base)

print("\nB. El apagón del 28/10: el último commit, con sus objetos vacíos")
base, pi = montar()
with open(os.path.join(pi, "data", "pred.csv"), "a") as f:
    f.write("2,0.2\n")
sh("git", "add", "data", cwd=pi)
sh("git", "commit", "-q", "-m", "23:46", cwd=pi)
# Vaciar los objetos de ese commit: el commit, su árbol y el blob nuevo.
nuevos = sh("git", "rev-list", "--objects", "origin/main..HEAD", cwd=pi).stdout.split()
nuevos = [x for x in nuevos if len(x) == 40]
for h in nuevos:
    p = os.path.join(pi, ".git", "objects", h[:2], h[2:])
    os.chmod(p, 0o644)
    open(p, "w").close()
# Y las corridas de la noche siguieron escribiendo datos.
with open(os.path.join(pi, "data", "pred.csv"), "a") as f:
    f.write("3,0.3\n4,0.4\n")
chk("está roto como en el Pi", sh("git", "status", cwd=pi, check=False).returncode != 0)
r = reparar(pi)
chk("el reparador termina bien", r.returncode == 0, r.stderr[-300:])
chk("y dice que reparó", "reparado" in r.stdout)
chk("git vuelve a funcionar", sh("git", "status", cwd=pi, check=False).returncode == 0)
chk("parado donde está GitHub",
    sh("git", "rev-parse", "HEAD", cwd=pi).stdout == sh("git", "rev-parse", "origin/main", cwd=pi).stdout)
datos = open(os.path.join(pi, "data", "pred.csv")).read()
chk("los datos de la noche siguen en disco, completos",
    datos == "t,p\n1,0.1\n2,0.2\n3,0.3\n4,0.4\n", repr(datos))
sh("git", "add", "data", cwd=pi)
sh("git", "commit", "-q", "-m", "11:21", cwd=pi)
p = sh("git", "push", "-q", "origin", "HEAD:main", cwd=pi, check=False)
chk("y la siguiente corrida publica todo", p.returncode == 0, p.stderr)
shutil.rmtree(base)

print("\nC. Un candado abandonado por una corrida que murió")
base, pi = montar()
lock = os.path.join(pi, ".git", "index.lock")
open(lock, "w").close()
viejo = time.time() - 20 * 60
os.utime(lock, (viejo, viejo))
r = reparar(pi)
chk("lo quita", not os.path.exists(lock) and r.returncode == 0, r.stderr)
open(lock, "w").close()          # uno recién creado: alguien lo está usando
reparar(pi)
chk("pero no uno reciente", os.path.exists(lock))
os.remove(lock)
shutil.rmtree(base)

print("\nD. El aviso de 'lleva una hora sin publicar', con el bloque real")
# Se extrae de correr.sh el bloque 4 tal cual y se corre con un reloj falso
# y un avisar() que solo anota. Así se prueba el código que corre en el Pi,
# no una copia.
texto = open(os.path.join(RAIZ, "deploy", "correr.sh"), encoding="utf-8").read()
ini = texto.index("ahora_s=$(date +%s)")
fin = texto.index("exit $codigo", ini)
bloque = texto[ini:fin]
vig = tempfile.mkdtemp()
anotados = os.path.join(vig, "avisos.txt")


def corrida(minuto, fallo):
    prog = (f'VIG="{vig}"; codigo=0; fallo="{fallo}"\n'
            f'date() {{ echo $(( 1700000000 + {minuto} * 60 )); }}\n'
            f'avisar() {{ echo "$1" >> "{anotados}"; }}\n' + bloque + "\nexit 0\n")
    return subprocess.run(["bash", "-c", prog], capture_output=True, text=True)


def avisos():
    return open(anotados).read().splitlines() if os.path.exists(anotados) else []


corrida(0, "git no pudo subir tras 3 intentos")
corrida(15, "git no pudo subir tras 3 intentos")
chk("un fallo de un rato no avisa", avisos() == [])
r = corrida(60, "git no pudo subir tras 3 intentos")
chk("a la hora avisa", len(avisos()) == 1 and "60 min" in avisos()[0], str(avisos()))
chk("y la corrida sale como fallo", r.returncode == 5)
corrida(75, "git no pudo subir tras 3 intentos")
corrida(500, "git no pudo subir tras 3 intentos")
chk("una sola vez, no cada 15 minutos", len(avisos()) == 1)
corrida(515, "")
chk("al recuperarse avisa cuánto duró",
    len(avisos()) == 2 and "515 min" in avisos()[1], str(avisos()))
corrida(530, "")
chk("y después ya no dice nada", len(avisos()) == 2)
corrida(600, "x"); corrida(615, "")
chk("un corte corto que se arregla solo no molesta", len(avisos()) == 2, str(avisos()))
shutil.rmtree(vig)

print("\n" + ("TODO EN ORDEN" if ok else "HAY FALLOS"))
sys.exit(0 if ok else 1)
