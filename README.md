# Nowcast Aguascalientes

Predicción de lluvia a 0–3 horas siguiendo celdas de tormenta por satélite
infrarrojo, en lugar de confiar en modelos numéricos.

Corre cada 15 minutos en una máquina propia —un Raspberry Pi o una instancia
gratuita de Oracle Cloud— y publica en GitHub Pages. Ver
[`deploy/README.md`](deploy/README.md).

> Empezó en GitHub Actions y no funcionó, con datos medidos: descarta la
> mayoría de los disparos programados y los que acepta esperan hasta **50
> minutos en cola** antes de arrancar, para un trabajo de un minuto. Además,
> mantener un runner ocupado para compensarlo va contra sus términos de
> servicio. Un nowcast que se actualiza cada hora no es un nowcast.

## Por qué el infrarrojo y no el radar

Aguascalientes **cae en un hueco de la red de radar mexicana**. Está
verificado contra la máscara oficial de cobertura de RainViewer: el radar más
cercano alcanza desde el suroeste y hay otro al este, pero la ciudad queda
justo en medio, sin cobertura.

Esto tiene dos consecuencias que definen todo el diseño:

1. **El satélite es la fuente principal**, no el respaldo. GOES-19 banda 13
   (infrarrojo limpio, 10.3 µm) es lo único que ve la ciudad de forma
   continua.
2. **El radar no puede verificar si llovió.** Preguntarle "¿llovió?" a un
   sensor que no ve el lugar daría siempre que no, y el sistema aprendería
   una mentira. Por eso la verdad sale de la precipitación observada de
   Open-Meteo, no del radar.

Una advertencia honesta que viene con esto: un tope nuboso frío no es lluvia.
El infrarrojo ve convección, que correlaciona con lluvia pero no es lo mismo
—un cirrus denso pasando por encima se ve frío y no moja. Por eso el IR
alimenta la *predicción* pero nunca dicta la *verificación*.

---

## Puesta en marcha (10 minutos)

### 1. Instala ntfy en tu teléfono

Descarga **ntfy** ([iOS](https://apps.apple.com/app/ntfy/id1625396347) ·
[Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
Abre la app, toca **+** y suscríbete a un nombre de canal inventado y difícil
de adivinar. Por ejemplo:

```
lluvia-ags-8f3k2p9x
```

Cualquiera que sepa ese nombre puede leer tus alertas, así que no uses algo
como `lluvia-ags`. No hay registro ni contraseña: el nombre *es* la llave.

### 2. Crea el repositorio

En GitHub, crea un repo **público** llamado `nowcast-ags`, y sube el contenido
de esta carpeta.

Público no es un descuido, es necesario: GitHub Actions da **2,000 minutos al
mes en repos privados**, y corriendo cada 15 minutos son ~2,880 corridas
mensuales. La cuota se agota en dos semanas y después se cobra. En repos
públicos, Actions es gratis e ilimitado.

Para que eso no exponga un domicilio, `config.py` usa 21.84 N, 102.28 W:
la zona sur de la ciudad, redondeada a dos decimales. Eso deja el punto sobre
una rejilla de ~1 km, señalando un área y no una dirección.

El redondeo no cuesta precisión. El píxel de GOES a esta latitud mide 2.44 km
y el cono de incertidumbre del nowcast arranca en 5 km, así que más decimales
solo fingirían una exactitud que el satélite no tiene.

Aquí no se guarda nada sensible: el canal de ntfy vive en los secretos de
GitHub, no en el código.

```bash
cd nowcast-ags
git init
git add .
git commit -m "sistema de nowcasting"
git branch -M main
git remote add origin https://github.com/TU-USUARIO/nowcast-ags.git
git push -u origin main
```

### 3. Guarda el canal como secreto

En el repo: **Settings → Secrets and variables → Actions → New repository
secret**

- Nombre: `NTFY_TOPIC`
- Valor: el nombre de canal del paso 1

### 4. Activa Actions

En la pestaña **Actions**, dale a *I understand my workflows, go ahead and
enable them*. Luego abre el workflow **nowcast** y presiona **Run workflow**
para la primera corrida manual.

Si todo salió bien, en un par de minutos aparecen `data/predictions.csv` y
`docs/latest.json` en el repo.

### 5. Enciende el tablero (opcional)

**Settings → Pages → Source: Deploy from a branch → main / `/docs`**

Tu tablero queda en `https://TU-USUARIO.github.io/nowcast-ags/`. Si el repo es
privado necesitas GitHub Pro para publicarlo; si no, puedes abrir
`docs/index.html` localmente o hacer el repo público (no contiene nada
sensible: el secreto vive aparte).

---

## Qué hace exactamente

### El pronóstico

Cada 15 minutos:

1. **Descarga** los últimos 5 cuadros de GOES-19 banda 13 desde el bucket
   público de NOAA en S3 (sector CONUS, cada 5 minutos, ~4 min de retraso) y
   recorta una ventana de 200×200 píxeles alrededor de la ciudad. Son
   temperaturas de brillo calibradas en Kelvin, no colores de los que haya que
   adivinar la temperatura. El radar solo entra si la máscara de cobertura
   dice que sirve, cosa que aquí no ocurre.
2. **Estima el movimiento** de las celdas con correlación de fase por FFT sobre
   cuatro cuadrantes más el cuadro completo, quedándose con la mediana
   ponderada. Es lo que haces al ojo cuando comparas dos cuadros del loop.
3. **Extrapola hacia atrás**: para saber qué caerá aquí en +90 min, mira qué
   hay ahora a 90 minutos de viaje en contra del viento.
4. **Abre un cono de incertidumbre** que crece con la distancia recorrida y se
   ensancha cuando la estimación de movimiento es dudosa. Dentro del cono toma
   el percentil 90, no el máximo: un píxel de ruido no debe despertarte.
5. **Corrige por crecimiento o disipación** comparando la intensidad media del
   dominio entre cuadros, amortiguado con el horizonte.
6. **Combina** con el consenso de cinco modelos numéricos (ECMWF, GFS, ICON,
   GEM, Météo-France) vía Open-Meteo.
7. **Calibra** la probabilidad con lo aprendido y, si amerita, te manda un push.

### El aprendizaje

Aquí está lo que pediste y lo que ninguna app hace: **el sistema se califica a
sí mismo**.

Cada predicción queda registrada en `data/predictions.csv` con su horizonte y
la contribución de cada fuente. Horas después, `verify.py` determina qué pasó
en realidad usando dos testigos independientes —el propio radar sobre tu píxel
y la precipitación observada de Open-Meteo— y lo escribe en
`data/observations.csv`.

Con esos pares, `calibrate.py` ajusta dos cosas por separado para cada
horizonte:

**Pesos por fuente.** Cada fuente recibe un peso inversamente proporcional a su
Brier score reciente. Si en Aguascalientes el infrarrojo le gana a los modelos
—que es lo que tu experiencia sugiere— el sistema lo descubre y le sube el peso
sin que tú intervengas. Los pesos arrancan en IR 60%, modelos 25%, radar 15%
(reflejando el hueco de cobertura) y se mueven hacia lo aprendido conforme se
acumulan casos.

**Curva de calibración.** Una regresión isotónica (algoritmo PAVA) mapea la
probabilidad cruda a una honesta. Es la diferencia entre "el sistema dice 70%"
y "de las veces que dijo 70%, llovió el 70%". Si el sistema es optimista de
más, la curva lo baja.

Ambas usan un decaimiento exponencial con vida media de 45 días, para que la
temporada de lluvias no quede contaminada por lo aprendido en secas.

El tablero muestra el Brier score y la comparación contra climatología. Un
**skill score positivo** significa que el sistema le gana a simplemente decir
"llueve el X% de los días". Ese es el número que importa.

### El barómetro de la malla

Si el servicio de Meshtastic que corre en la misma máquina está alimentando
`~/clima/clima.db`, ese sensor pasa a ser la **fuente primaria** de presión
para el presente y los cambios recientes. Open-Meteo se queda con el
pronóstico, porque el sensor sabe qué está pasando pero no qué va a pasar.

Vale la pena por dos motivos: mide **cada 60 segundos** en vez de cada hora
—un frente que baja 2 hPa en cuarenta minutos es un salto ilegible en la serie
horaria y una pendiente clara en la del sensor—, y **sigue midiendo sin
internet**, que es justo lo que se cae en una tormenta.

Dos trampas que el código resuelve y conviene conocer:

**Unidades.** El sensor marca ~815 hPa (presión de estación a 1880 m) y
Open-Meteo entrega ~1015 hPa (reducida a nivel del mar). Mezclarlas sin
convertir daría un salto de 200 hPa que el detector de frentes leería como una
catástrofe. La conversión **no** usa la fórmula barométrica con una altitud
supuesta: el factor se estima comparando ambas series donde se solapan, lo que
absorbe también cualquier desviación de calibración del sensor. Sale ~1.25.

**Ruido.** Un BMP280 tiene ruido de décimas entre muestras. Se usa la mediana
de una ventana de 10 minutos, no la última lectura.

Si el sensor lleva más de 30 minutos sin reportar, o hay menos de hora y media
de historia, o la base no existe o está corrupta, el sistema vuelve a
Open-Meteo sin ruido. La página dice de dónde salió el dato.

### Animación e historial

El motor archiva un cuadro reproyectado cada 15 minutos, con los rayos de ese
mismo instante, y conserva **7 días**. En la página, el botón *Animación* abre
un reproductor con línea de tiempo y selector de día: sirve para ver cómo se
desarrolló una celda, o para comprobar si la tormenta puntual del martes quedó
registrada.

Los cuadros se piden **bajo demanda**, no de golpe. Un día son ~96 PNG y unos
8 MB; bajarlos al abrir el reproductor sería inaceptable con datos móviles, que
es justo donde más se usa. Se precargan tres por delante del que se ve.

Esto **no acelera el crecimiento del repositorio**: `satelite.png` ya entraba a
git 96 veces al día como blobs distintos, y guardarlos con nombre propio cuesta
exactamente lo mismo. La poda a 7 días saca los viejos del sitio publicado —lo
que se descarga al abrir la página— pero no del historial de git, porque git no
olvida. El crecimiento de ~3 GB al año sigue siendo un pendiente aparte.

### Avisos de tormenta eléctrica

Además de la lluvia, el sistema vigila los rayos del GLM y avisa por
transiciones, no por estado: mientras la tormenta siga encima no repite.

| Cuándo | Qué se manda |
|---|---|
| Actividad a ~60 km acercándose | aviso anticipado, ~1 h de margen |
| Actividad a ~25 km | los truenos ya se oyen |
| 30 min sin nada cerca | ya pasó |

Los 25 km no son arbitrarios: es el alcance típico del trueno audible. Más
lejos se ve el relámpago pero rara vez se oye, y para un animal que se asusta
lo relevante es el ruido.

Hay histéresis deliberada —se entra en "encima" a 25 km pero no se sale hasta
los 35— porque una celda rondando justo el umbral mandaría un aviso cada
quince minutos, y un canal que bombardea acaba silenciado.

El "ya pasó" existe porque es la mitad que suele faltar: saber cuándo se puede
bajar la guardia es tan útil como saber cuándo subirla.

### Cuándo te avisa

Push solo si la probabilidad calibrada supera 55% dentro de los próximos
90 minutos, con 45 minutos de silencio entre alertas para no bombardearte.
Todo eso se ajusta en `nowcast/config.py`.

---

## Expectativas honestas

**Las primeras 2–3 semanas no aprende nada.** Necesita al menos 40 pares
predicción/observación por horizonte para que la calibración tenga sentido
estadístico; con menos, usa los valores por defecto. En temporada de lluvias
eso son unas dos semanas.

**El infrarrojo confunde nube alta con lluvia.** Ve topes fríos, que
correlacionan con convección pero no son lluvia medida. Por eso su curva de
probabilidad es más conservadora que la del radar, y por eso la verificación
nunca se apoya en él. Es la limitación de fondo de este sistema y no
desaparece con más código: desaparecería con un radar sobre la ciudad.

**Más allá de 3 horas la advección no sirve.** Es física, no software: las
celdas nacen y mueren en ese plazo. Para el día siguiente los modelos
numéricos siguen siendo lo mejor que hay, con toda su imprecisión.

**El cron de GitHub no es puntual, y por eso no se usa cada 15 min.**
Medido en este repo: con `cron: "*/15"`, de cuatro corridas esperadas en una
hora ocurrió **una**. GitHub retrasa o se salta los workflows programados
cuando hay carga, y castiga especialmente los intervalos cortos.

Intenté compensarlo manteniendo el runner ocupado con esperas de 15 minutos
dentro de un solo job. **Fue un error mío:** eso va contra los términos de
Actions, que prohíben usar los runners para "cualquier actividad ajena a la
producción, prueba, despliegue o publicación" del proyecto. GitHub lo hizo
cumplir cancelando corridas a mitad. Está revertido.

La solución de verdad es no depender de Actions: una máquina propia con un
temporizador de systemd, que sí cumple horarios. Ver
[`deploy/README.md`](deploy/README.md).

---

## Uso local

```bash
pip install -r requirements.txt

bash pruebas.sh                     # todas las pruebas, sin red
python selftest.py                  # solo el motor
python -m nowcast.run --no-alert    # una corrida sin notificar
python -m nowcast.daily             # reporte matutino
python -m nowcast.run --verify-only # solo verificar y recalibrar
```

## Estructura

```
nowcast/
  config.py      coordenadas, umbrales, horizontes
  goes.py        GOES-19 banda 13 desde S3 + proyección geoestacionaria
  sources.py     RainViewer (radar), Open-Meteo, cobertura
  engine.py      correlación de fase, advección, detección de celdas
  calibrate.py   PAVA + pesos por Brier score
  verify.py      qué pasó en realidad
  notify.py      push vía ntfy
  run.py         orquestador (cada 15 min)
  daily.py       reporte matutino
data/            historial: predicciones, observaciones, calibración
docs/            tablero web
selftest.py      pruebas con tormentas sintéticas
test_tormenta.py avisos de rayos: distancias, histéresis, transiciones
test_presion.py  la ventana de 1 h contra la marea atmosférica
test_barometro.py el sensor de la malla: unidades, ruido y respaldo
test_archivo.py  el historial: guardado, índice y poda
test_pagina.js   la página, con DOM y red falsos (necesita node)
```

## Ajustes comunes

Todo en `nowcast/config.py`:

| Quiero… | Cambia |
|---|---|
| Menos alertas | `ALERT_PROB_THRESHOLD` a 0.65 |
| Más aviso anticipado | `ALERT_MAX_ETA_MIN` a 120 |
| Mover la ubicación | `LAT`, `LON` |
| Alertar solo con tormenta fuerte | `DBZ_STORM` y sube el umbral |
| Correr cada 10 min | `OnCalendar` en `deploy/nowcast.timer` |

## Fuentes de datos

- [NOAA GOES-19 en AWS Open Data](https://registry.opendata.aws/noaa-goes/) — banda 13, sector CONUS, cada 5 min
- [RainViewer Weather Maps API](https://www.rainviewer.com/api/weather-maps-api.html) — radar y máscara de cobertura
- [Open-Meteo](https://open-meteo.com/en/docs) — modelos numéricos y precipitación observada
- [ntfy.sh](https://ntfy.sh) — notificaciones push

NASA GIBS quedó descartado: reporta que la capa de GOES-East banda 13 existe y
tiene datos, pero no entrega un solo tile por WMS ni por WMTS (probados tres
endpoints y varios niveles de zoom; hasta el tile 0/0/0 responde 404).

El método está descrito en la literatura de nowcasting; la referencia abierta
es [pySTEPS](https://pysteps.github.io/), y la validación de flujo óptico sobre
imágenes satelitales para convección está en
[Muñoz et al., NHESS 2024](https://nhess.copernicus.org/articles/24/567/2024/).

Todas las fuentes son públicas y gratuitas. Ninguna requiere API key.
