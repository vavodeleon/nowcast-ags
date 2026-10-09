# App Android y tarjeta Wear OS

- **`app/`**: la app del celular. Abre la página de siempre dentro de la app
  y añade dos widgets: chico (2x1, lluvia en 1 h) y mediano (4x2, veredicto,
  barras hasta 3 h y la celda).
- **`wear/`**: una tarjeta (Tile) para el reloj, con el veredicto, el % a 1 h,
  las barras y la celda. Al tocarla se refresca.
- **`common/`**: lo que comparten. Lee `docs/reloj.json`; si la copia de
  GitHub Pages lleva más de 45 min sin cambiar, pide la del repositorio
  directamente (lo de la falla de Actions del 5/10).

El veredicto lo calcula el Pi (`nowcast/reloj.py`), no la app: así el widget,
el reloj y la página dicen siempre lo mismo. `test_reloj.py` lo comprueba
contra la función de la página.

## Quién compila

GitHub Actions (`.github/workflows/android.yml`), solo cuando cambia algo
dentro de `android/`. Publica un release con los dos APK. El enlace a la
última versión es siempre el mismo:

    https://github.com/vavodeleon/nowcast-ags/releases/latest/download/nowcast-celular.apk
    https://github.com/vavodeleon/nowcast-ags/releases/latest/download/nowcast-reloj.apk

## Una sola vez: la llave de firma

Android solo instala una actualización encima de otra si las dos están
firmadas con la misma llave. Por eso la llave es tuya, se crea una vez y se
guarda: **si se pierde, para actualizar hay que desinstalar y volver a
instalar**. Y si se filtra, alguien podría firmar una "actualización" que
tu celular aceptaría. Nunca va al repositorio (`*.jks`, `*.p12` y `*.pem` están en .gitignore).

1. En el Mac, con el `openssl` que ya trae macOS (no hace falta Java ni
   Homebrew). Crea la llave en un formato que Android acepta (PKCS12):

       umask 077 && mkdir -p ~/llave-tmp && cd ~/llave-tmp
       openssl req -x509 -newkey rsa:4096 -nodes -keyout k.pem -out c.pem \
         -days 10000 -subj "/CN=Alvaro/O=nowcast-ags/C=MX"
       openssl pkcs12 -export -in c.pem -inkey k.pem -name nowcast \
         -out ~/nowcast.p12 -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1
       rm k.pem c.pem && cd ~ && rmdir ~/llave-tmp

   El segundo `openssl` pide una contraseña dos veces ("Export Password"):
   guárdala en tu gestor de contraseñas. Los `.pem` intermedios llevan la
   llave sin cifrar: por eso se borran al final.

   (Con Java instalado, `keytool -genkeypair -keystore ~/nowcast.jks -alias
   nowcast -keyalg RSA -keysize 4096 -validity 10000` hace lo mismo.)

2. Copia la llave en base64 al portapapeles:

       base64 -i ~/nowcast.p12 | pbcopy

3. En GitHub: *Settings → Secrets and variables → Actions → New repository
   secret*. Cuatro secretos:

   | nombre | valor |
   |---|---|
   | `ANDROID_KEYSTORE_B64` | lo del portapapeles |
   | `ANDROID_KEYSTORE_PASS` | la contraseña del paso 1 |
   | `ANDROID_KEY_ALIAS` | `nowcast` |
   | `ANDROID_KEY_PASS` | la misma contraseña |

4. Respalda `~/nowcast.p12` (con la contraseña aparte), por ejemplo en el
   gestor de contraseñas como archivo adjunto. Después puedes borrarlo del Mac.

Sin estos secretos el workflow igual compila, pero con una llave de
depuración y sin publicar release: deja los APK como artefacto descargable
para probar, y avisa.

## Instalar en el celular

Abre el enlace de `nowcast-celular.apk` en el navegador del celular. La
primera vez Android pide permiso para instalar apps desde ese navegador.
Para actualizar, el mismo enlace: se instala encima y conserva los widgets.

Widgets: mantén presionada la pantalla de inicio → Widgets → *Lluvia Ags*.

## Instalar en el reloj

Los relojes no instalan APK desde un navegador; se hace una vez con `adb`
desde el Mac, por WiFi (reloj y Mac en la misma red).

1. En el Mac: `brew install android-platform-tools`
2. En el reloj: *Ajustes → Sistema → Información → Versión de software*
   (o *Número de compilación*): tócalo 7 veces. Aparece *Opciones de
   desarrollador*: activa *Depuración ADB* y *Depurar por Wi-Fi*.
3. Dentro de *Depurar por Wi-Fi → Vincular dispositivo nuevo* sale una IP,
   un puerto y un código. En el Mac:

       adb pair IP:PUERTO_DE_VINCULACION     # pide el código
       adb connect IP:PUERTO                 # el que sale en "Depurar por Wi-Fi"
       curl -LO https://github.com/vavodeleon/nowcast-ags/releases/latest/download/nowcast-reloj.apk
       adb install -r nowcast-reloj.apk

4. En el reloj: desliza hasta el final de las tarjetas → *+* → *Lluvia Ags*.

Para actualizar, repite `adb connect` e `adb install -r`. Al terminar puedes
apagar la depuración por Wi-Fi: gasta batería.

La app del reloj no tiene ícono en la lista de apps: es solo la tarjeta.

## Cada cuánto se actualiza

- Widgets: cada 15 min con WorkManager, y al abrir la app. Android puede
  atrasarlo si el celular está en reposo profundo; la hora del widget dice
  de cuándo es el dato, y sale con ⚠ si pasa de 45 min.
- Tarjeta: el sistema la pide cada 15 min, y al tocarla.
- Sin internet ninguna de las dos sabe nada nuevo: muestran la última
  lectura con su hora. Para eso está la malla.
