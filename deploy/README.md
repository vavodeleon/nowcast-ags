# Mudarse a un servidor propio (Oracle Cloud, gratis)

GitHub Actions no sirve para esto y ya lo comprobamos con datos: descarta la
mayoría de los disparos programados, y los que acepta se quedan hasta **50
minutos en cola** antes de arrancar. El trabajo dura un minuto. Un nowcast
que se actualiza cada hora no es un nowcast.

Un servidor propio con `systemd` sí cumple horarios. La capa gratuita de
Oracle es permanente —no una prueba— y basta de sobra.

---

## 1. Crear la cuenta

En [oracle.com/cloud/free](https://www.oracle.com/cloud/free/). Piden tarjeta
para verificar identidad, **no cobran** mientras te quedes en los recursos
"Always Free". Tarda unos 15 minutos entre verificación y correos.

Elige bien la región al registrarte: **no se puede cambiar después**. Escoge
la más cercana a México.

## 2. Crear la máquina

**Compute → Instances → Create instance**

- **Image:** Ubuntu 22.04 o 24.04
- **Shape:** `VM.Standard.A1.Flex` (ARM), 1 OCPU y 6 GB de RAM

> **El escollo:** es muy común que Oracle responda *"Out of host capacity"*
> con las máquinas ARM. No es un error tuyo, es que están saturadas. Dos
> salidas:
>
> - Reintentar en otro momento o en otro dominio de disponibilidad.
> - Usar `VM.Standard.E2.1.Micro` (AMD, 1 GB de RAM), que siempre hay. Es
>   pequeña pero suficiente: el instalador detecta la poca memoria y añade
>   swap automáticamente.

En **Add SSH keys**, deja que genere el par y **descarga la clave privada**.
Sin ella no podrás entrar.

## 3. Entrar por SSH

Desde la Terminal del Mac, con la clave que descargaste:

```bash
chmod 600 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@LA_IP_PUBLICA
```

La IP aparece en la página de la instancia.

## 4. Instalar

Ya dentro del servidor:

```bash
git clone https://github.com/vavodeleon/nowcast-ags.git
bash nowcast-ags/deploy/instalar.sh
```

El script instala dependencias, crea el entorno de Python, **corre las
pruebas del motor** —si fallan, se detiene antes de dejar nada a medias— y
programa la ejecución cada 15 minutos con systemd.

## 5. Las credenciales

El instalador crea `~/.nowcast.env` vacío. Rellénalo:

```bash
nano ~/.nowcast.env
```

Necesitas un **token fine-grained** de GitHub
([crear aquí](https://github.com/settings/personal-access-tokens/new)) con
acceso solo a `nowcast-ags` y permisos:

- **Contents:** Read and write — para publicar los datos
- **Issues:** Read and write — para leer y cerrar tus correcciones

Guarda con `Ctrl+O`, `Enter`, `Ctrl+X`. Y arranca:

```bash
sudo systemctl start nowcast
journalctl -u nowcast -n 40 --no-pager
```

Si ves las líneas del pronóstico y un commit al final, ya está.

## 6. Apagar el cron de GitHub

Con el servidor corriendo, los disparos de Actions solo estorban: dos
autores escribiendo en el mismo repositorio cada quince minutos es pedir
conflictos. En tu Mac:

```bash
cd ~/Downloads/nowcast-ags
git pull
```

Edita `.github/workflows/nowcast.yml` y **borra las dos líneas del
`schedule`** (`- cron: ...` y la línea `schedule:`), dejando solo
`workflow_dispatch`. Así conservas el botón manual por si algún día el
servidor se cae.

```bash
git commit -am "el servidor toma el relevo: sin cron en Actions"
git push
```

---

## Vida diaria

```bash
systemctl list-timers nowcast.timer      # cuándo toca la próxima
journalctl -u nowcast -n 50 --no-pager   # qué hizo la última
journalctl -u nowcast -f                 # verlo en vivo
sudo systemctl start nowcast             # forzar una ahora
```

Para actualizar el código cuando cambies algo desde el Mac:

```bash
bash ~/nowcast-ags/deploy/instalar.sh
```

Es idempotente: hace `git pull`, reinstala lo que haga falta y vuelve a
programar el temporizador. Se puede correr las veces que quieras.

## Qué cambia y qué no

**Cambia:** la cadencia pasa a ser real. Cada 15 minutos, sin colas. Las
alertas de una celda que llega en 40 minutos por fin llegan a tiempo.

**No cambia:** el tablero sigue en GitHub Pages con la misma dirección, los
datos siguen guardándose en el mismo repositorio, y las notificaciones
siguen llegando por ntfy. Desde tu celular no notarás más diferencia que
que los números estén frescos.
