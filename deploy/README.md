# Mudarse a una máquina propia

Dos caminos, el mismo instalador para ambos:

- **[Raspberry Pi](#raspberry-pi)** — si ya tienes uno encendido en casa.
- **[Oracle Cloud](#oracle-cloud-gratis-para-siempre)** — gratis y siempre
  encendido, pero hay que crear cuenta.

---

## Raspberry Pi

Un Pi 3 basta. El trabajo pesado son una FFT sobre matrices de 200×200 y una
reproyección de 700×700: unos 10–15 segundos en un Pi 3. La memoria pico
ronda los 400 MB.

**Lo que sí hay que tener en cuenta:**

| | por corrida | al mes |
|---|---|---|
| GOES banda 13 (5 × 13 MB) | 65 MB | |
| GLM, rayos (45 × 360 KB) | 16 MB | |
| **Total descargado** | **~81 MB** | **~230 GB** |

Sin límite de datos eso da igual, pero conviene saberlo antes.

**La tarjeta SD.** El sistema hace unos 96 commits de git al día. Las SD
mueren por desgaste de escritura; si el Pi va a estar años con esto, arranca
desde un SSD por USB.

**Sistema de 64 bits.** En Raspberry Pi OS de 32 bits, `scipy` y `h5py` se
compilan desde cero y tardan una eternidad. En 64 bits llegan precompilados.
El instalador lo detecta y avisa.

Verificado sobre un Pi 3 Model B con Raspberry Pi OS Lite 64-bit (Debian 13
trixie, Python 3.13): las dependencias se instalan precompiladas en un par
de minutos y la suite del motor tarda **7.4 segundos**. La corrida real está
dominada por la descarga, no por el procesador.

**Convivencia con LoRa.** El servicio se instala con prioridad baja de CPU
(`Nice=10`), disco en modo `idle` y un tope duro de 700 MB de memoria. Un
nowcast puede tardar dos minutos más sin que nadie lo note; un gateway LoRa
que pierde su ventana de recepción pierde el paquete para siempre. Por eso
el nowcast siempre cede el paso.

### Arrancar de la SD, escribir en un disco externo

Es lo recomendable, y **no hace falta arrancar desde USB**. La SD se
desgasta por escrituras, y el sistema operativo casi solo lee una vez
arrancado. Quien escribe 96 veces al día es el repositorio. Moviendo solo
eso al disco externo, la SD queda prácticamente en modo lectura.

El disco externo solo necesita unos **8 GB**; cualquier SSD o memoria
servirá de sobra por tamaño. Para durabilidad, un SSD SATA con adaptador USB
aguanta mucho mejor las escrituras diarias que una memoria USB.

**El disco tiene que estar en ext4.** Una memoria de fábrica viene en exFAT
o FAT32, que no guardan permisos ni enlaces simbólicos: git se corrompe de
formas raras y difíciles de diagnosticar. El instalador se detiene si
detecta un sistema de archivos inadecuado.

Con el disco conectado, identifica cuál es:

```bash
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT
```

Suponiendo que sea `/dev/sda1` — **verifica bien, esto borra el disco**:

```bash
sudo umount /dev/sda1 2>/dev/null
sudo mkfs.ext4 -L datos /dev/sda1
sudo mkdir -p /mnt/datos
```

Para que se monte solo al arrancar, por UUID (los nombres tipo `/dev/sda1`
cambian de orden entre reinicios):

```bash
sudo blkid /dev/sda1        # copia el UUID que aparece
sudo nano /etc/fstab
```

Añade al final, con tu UUID:

```
UUID=EL-UUID-QUE-COPIASTE  /mnt/datos  ext4  defaults,noatime,nofail  0  2
```

`noatime` evita escrituras cada vez que se lee un archivo. `nofail` hace que
el Pi arranque igual aunque el disco no esté conectado.

```bash
sudo mount -a
sudo chown -R $USER:$USER /mnt/datos
```

### Instalación

```bash
git clone https://github.com/vavodeleon/nowcast-ags.git
DESTINO=/mnt/datos/nowcast-ags bash nowcast-ags/deploy/instalar.sh
```

Esa variable `DESTINO` es la que manda todo al disco externo: el
repositorio, el entorno de Python y el archivo de swap. El servicio queda
configurado para **no arrancar si el disco no está montado**, en vez de
escribir en el punto de montaje vacío —que estaría en la SD— y acabar con
dos copias divergentes.

Y sigue desde el [paso 5](#5-las-credenciales) de abajo, que es igual para
las dos opciones.

---

## Oracle Cloud, gratis para siempre

La capa gratuita de Oracle es permanente —no una prueba— y basta de sobra.

### 1. Crear la cuenta

En [oracle.com/cloud/free](https://www.oracle.com/cloud/free/). Piden tarjeta
para verificar identidad, **no cobran** mientras te quedes en los recursos
"Always Free". Tarda unos 15 minutos entre verificación y correos.

Elige bien la región al registrarte: **no se puede cambiar después**. Escoge
la más cercana a México.

### 2. Crear la máquina

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

### 3. Entrar por SSH

Desde la Terminal del Mac, con la clave que descargaste:

```bash
chmod 600 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@LA_IP_PUBLICA
```

La IP aparece en la página de la instancia.

### 4. Instalar

Ya dentro del servidor:

```bash
git clone https://github.com/vavodeleon/nowcast-ags.git
bash nowcast-ags/deploy/instalar.sh
```

El script instala dependencias, crea el entorno de Python, **corre las
pruebas del motor** —si fallan, se detiene antes de dejar nada a medias— y
programa la ejecución cada 15 minutos con systemd.

---

## 5. Las credenciales

*(igual para Raspberry Pi y para Oracle)*

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
