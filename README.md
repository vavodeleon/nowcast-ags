# Nowcast Aguascalientes

Predicción de lluvia a 0–3 horas siguiendo celdas de tormenta por satélite
infrarrojo, en lugar de confiar en modelos numéricos.

Corre solo en GitHub Actions, cada 15 minutos, gratis. No necesitas servidor
ni dejar la computadora encendida.

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

La solución está en `nowcast.yml`: se pide **un disparo por hora** —los cron
horarios sí se cumplen— y dentro del mismo job se hacen las cuatro pasadas
durmiendo 15 minutos entre cada una. El runner queda ocupado ~46 min por hora,
lo cual en un repo público es gratis.

---

## Uso local

```bash
pip install -r requirements.txt

python selftest.py                  # pruebas del motor, sin red
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
```

## Ajustes comunes

Todo en `nowcast/config.py`:

| Quiero… | Cambia |
|---|---|
| Menos alertas | `ALERT_PROB_THRESHOLD` a 0.65 |
| Más aviso anticipado | `ALERT_MAX_ETA_MIN` a 120 |
| Mover la ubicación | `LAT`, `LON` |
| Alertar solo con tormenta fuerte | `DBZ_STORM` y sube el umbral |
| Correr cada 10 min | el cron en `.github/workflows/nowcast.yml` |

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
