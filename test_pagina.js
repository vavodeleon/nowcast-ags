/* Ejecuta el JavaScript de la página contra un DOM y una red falsos.
 *
 * Existe porque los fallos de la página se descubrían en el teléfono, que es
 * el peor sitio para depurar: sin consola, con caché de por medio y con un
 * ciclo de correción de varios minutos por intento. Aquí el ciclo es de un
 * segundo.
 *
 * No hay navegador ni dependencias: se fabrica el mínimo de DOM, Leaflet y
 * fetch que el código toca. Un simulacro no demuestra que se vea bien, pero
 * sí atrapa lo que de verdad fallaba: elementos que no existen, capas que se
 * pisan, y peticiones que no se hacen cuando deberían.
 *
 *   node test_pagina.js
 */
"use strict";
const fs = require("fs");

let ok = true;
function chk(nombre, cond, detalle) {
  console.log(`  ${cond ? "PASA" : "FALLA"}  ${nombre}` + (detalle ? `  [${detalle}]` : ""));
  if (!cond) ok = false;
}

// ---------------------------------------------------------------- DOM falso
const oyentes = {};
function nuevoElemento(id) {
  const el = {
    id, textContent: "", value: "", max: "0", checked: true,
    hidden: false, style: {}, children: [],
    // Un <select> real toma como valor el de su primera opción en cuanto se
    // le añaden. Sin imitarlo, el código pediría el día "" y la prueba
    // fallaría por el simulacro y no por la página.
    appendChild(h) {
      this.children.push(h);
      if (this.children.length === 1 && h.value != null) this.value = h.value;
    },
    addEventListener(ev, fn) { (oyentes[`${id}:${ev}`] ||= []).push(fn); },
    remove() {},
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return ""; },
    set() { this.children = []; this.value = ""; },
  });
  return el;
}
const elementos = {};
const consultados = new Set();

// Los elementos se fabrican al vuelo en vez de listarlos a mano. Así la
// prueba no hay que mantenerla cada vez que la página crece, y a cambio se
// puede comprobar algo mejor: que todo lo que el JavaScript busca existe de
// verdad en el HTML. Un `q("#loquesea")` que devuelve null revienta la
// función entera y deja media página sin dibujar.
function elemento(id) {
  consultados.add(id);
  return (elementos[id] ||= nuevoElemento(id));
}
// Estado inicial que sí importa: en el HTML real el reproductor nace oculto.
elementos["reproductor"] = nuevoElemento("reproductor");
elementos["reproductor"].hidden = true;

// La raíz del documento: el tema vive en sus data-atributos.
const raiz = { dataset: {} };

global.document = {
  addEventListener(ev, fn) { (oyentes[`doc:${ev}`] ||= []).push(fn); },
  querySelector(sel) { return elemento(sel.replace("#", "")); },
  getElementById(id) { return elemento(id); },
  createElement() { return nuevoElemento("nuevo"); },
  documentElement: raiz,
  hidden: false,
};

// Paleta falsa. No imita al CSS -eso haría falta un navegador- pero sí lo
// que de verdad importa comprobar: que las gráficas y el mapa PIDEN los
// colores en vez de llevarlos escritos, y que lo que piden cambia con el
// tema. Si alguien vuelve a poner un "#697790" a mano, aquí se ve.
const colores = { claro: {}, oscuro: {} };
const pedidos = [];
global.getComputedStyle = () => ({
  getPropertyValue(n) {
    pedidos.push(n);
    return (raiz.dataset.tema === "oscuro" ? "#111111" : "#eeeeee");
  },
});

// localStorage con interruptor de avería: Safari en modo privado lanza al
// escribir, y eso no debe tumbar la página.
let guardado = {};
let almacenRoto = false;
global.localStorage = {
  getItem(k) { if (almacenRoto) throw new Error("bloqueado"); return guardado[k] ?? null; },
  setItem(k, v) { if (almacenRoto) throw new Error("bloqueado"); guardado[k] = String(v); },
};

let sistemaOscuro = false;
const oyentesMQ = [];
global.window = {
  addEventListener(ev, fn) { (oyentes[`win:${ev}`] ||= []).push(fn); },
  matchMedia(q) {
    return {
      get matches() { return sistemaOscuro; },
      addEventListener(_ev, fn) { oyentesMQ.push(fn); },
    };
  },
};
// Node ya define `navigator` como solo-lectura desde la v21; hay que
// redefinir la propiedad en vez de asignarla.
Object.defineProperty(global, "navigator", {
  value: { onLine: true, connection: { effectiveType: "4g" } },
  configurable: true, writable: true,
});
global.location = { origin: "https://vavodeleon.github.io" };
global.Image = class { set src(v) { imagenesPedidas.push(v); } };
// Reloj controlado. La primera version ejecutaba los setTimeout al
// instante, lo cual habria hecho que el nuevo limite de tiempo abortara
// TODAS las descargas nada mas empezar: la prueba habria "detectado" un
// fallo inexistente. Aqui los temporizadores se guardan y solo corren
// cuando la prueba avanza el reloj a proposito.
const temporizadores = [];
let idTemporizador = 0;
global.setTimeout = (fn, ms = 0) => {
  temporizadores.push({ id: ++idTemporizador, fn, ms });
  return idTemporizador;
};
global.clearTimeout = (id) => {
  const t = temporizadores.find((t) => t.id === id);
  if (t) t.cancelado = true;
};
global.setInterval = () => 1;
global.clearInterval = () => {};

async function avanzar(ms) {
  for (const t of temporizadores) {
    if (!t.cancelado && !t.hecho && t.ms <= ms) { t.hecho = true; t.fn(); }
  }
  await new Promise((r) => process.nextTick(r));
  await new Promise((r) => process.nextTick(r));
}

// ------------------------------------------------------------ Leaflet falso
const capasEnMapa = new Set();

// Leaflet encadena: casi todos sus métodos devuelven la propia capa. En vez
// de ir implementando uno a uno conforme la página los use (bindTooltip,
// setStyle, setLatLng...), cualquier método desconocido se resuelve solo y
// devuelve `this`. Lo que interesa vigilar es qué capas están en el mapa.
function capaFalsa(tipo, extra = {}) {
  const base = {
    tipo, ...extra, hijos: extra.hijos,
    // Una capa puede añadirse al mapa o a un grupo. La distinción importa:
    // quitar el grupo tiene que llevarse a sus hijos, y con `addTo` tratando
    // todo como "al mapa" el cono seguía contando como dibujado después de
    // borrarlo. Era un fallo del simulacro, pero escondía justo lo que la
    // prueba quería vigilar.
    addTo(destino) {
      if (destino && Array.isArray(destino.hijos)) destino.hijos.push(this);
      else capasEnMapa.add(this);
      return this;
    },
    remove() {
      capasEnMapa.delete(this);
      (this.hijos || []).forEach((h) => h.remove());
      return this;
    },
  };
  return new Proxy(base, {
    get(obj, prop) {
      if (prop in obj) return obj[prop];
      if (typeof prop === "symbol") return undefined;
      return function () { return this; };
    },
  });
}
global.L = {
  map: () => ({
    setView() { return this; }, remove() {}, invalidateSize() {},
    hasLayer: (c) => capasEnMapa.has(c),
  }),
  tileLayer: (url, o) => capaFalsa("tile", { url, opciones: o }),
  imageOverlay: (url, b, o) => capaFalsa("imagen", { url, bounds: b, opciones: o }),
  layerGroup: () => capaFalsa("grupo", { hijos: [] }),
  circleMarker: () => capaFalsa("punto"),
  marker: () => capaFalsa("marca"),
  polyline: () => capaFalsa("linea"),
  divIcon: () => ({}),
  circle: () => capaFalsa("circulo"),
  // El cono. Se guardan los vértices porque su forma es lo que hay que
  // comprobar: que se ensancha hacia la ciudad y no al revés.
  polygon: (pts, o) => capaFalsa("poligono", { puntos: pts, opciones: o }),
};
global.Chart = class { constructor() {} destroy() {} update() {} };

// --------------------------------------------------------------- red falsa
const pedidas = [];
const imagenesPedidas = [];
let redCaida = false;
let redColgada = false;

const LATEST = {
  issued_utc: new Date().toISOString(),
  issued_local: "2026-08-30 12:45", confidence: "buena",
  probabilities: { "30": 0.1, "60": 0.2, "90": 0.3, "120": 0.4, "180": 0.5 },
  lat: 21.84, lon: -102.28, map_bounds: [[19, -105], [24, -99]],
  motion_from: "SO", motion_speed_kmh: 30, motion_bearing: 225,
  ahora: { estado: "Despejado", lloviendo: false },
  rayos: { total_hora: 3, fase: "acercandose", dist_km: 44 },
  pressure: { msl: 1014, change_1h: -1.8, change_3h: -2.6, change_24h: -4 },
  temperatura: { ahora: 27 },
};
const RESPUESTAS = {
  "latest.json": LATEST,
  "rayos.json": { total_hora: 3, bloques: [{ t: "x", edad_min: 5, puntos: [[21.9, -102.3, 2]] }] },
  "hist/dias.json": ["2026-08-28", "2026-08-29", "2026-08-30"],
  "hist/2026-08-30.json": {
    bounds: [[19, -105], [24, -99]],
    cuadros: [{ t: "1200" }, { t: "1215", r: 5 }, { t: "1230", r: 12 }, { t: "1245" }],
  },
  "hist/2026-08-30/1215.r.json": [[21.9, -102.3, 5]],
  "hist/2026-08-30/1230.r.json": [[21.8, -102.2, 12]],
};
global.fetch = async (url, opciones = {}) => {
  pedidas.push({ url, opciones });
  // Colgarse NO es lo mismo que fallar: la promesa se queda pendiente para
  // siempre y solo la aborta el AbortController. Esto es lo que hacia la
  // red movil, y por eso la pagina se quedaba en "Cargando..." sin dar
  // ningun mensaje de error.
  if (redColgada) {
    return new Promise((_, rechazar) => {
      if (!opciones.signal) return;         // sin límite, se cuelga de verdad
      opciones.signal.addEventListener("abort", () => {
        const e = new Error("The operation was aborted");
        e.name = "AbortError";
        rechazar(e);
      });
    });
  }
  if (redCaida) throw new Error("sin red");
  const clave = Object.keys(RESPUESTAS).find((k) => url.startsWith(k));
  if (!clave) return { ok: false, status: 404, json: async () => ({}) };
  return { ok: true, status: 200, json: async () => RESPUESTAS[clave] };
};

// -------------------------------------------------- cargar el script real
const html = fs.readFileSync(`${__dirname}/docs/index.html`, "utf8");

// Los bloques se buscan por id, no por posición ni con un comodín goloso.
// Antes era /<script>\n([\s\S]*)\n<\/script>/ y al añadir un segundo bloque
// -el del tema, que va en el <head>- la captura se tragó desde el primero
// hasta el último y la prueba entera dejó de arrancar.
function bloque(id) {
  const m = html.match(new RegExp(`<script id="${id}">\\n([\\s\\S]*?)\\n<\\/script>`));
  if (!m) throw new Error(`no encontré el bloque de script id="${id}"`);
  return m[1];
}
const js = bloque("pagina");

// El del tema corre ANTES que nada en el navegador, así que aquí también:
// decide si la página arranca en claro u oscuro.
eval(bloque("tema-temprano"));

// Las variables declaradas con `let` dentro de un eval quedan encerradas en
// el ámbito del eval. Se añade un puente al final del mismo eval para poder
// inspeccionarlas desde aquí sin tocar el código de la página.
eval(js + `
global.puente = {
  get hist(){ return hist },
  get capaHistSat(){ return capaHistSat },
  get capaHistRayos(){ return capaHistRayos },
  set ultimoIntento(v){ ultimoIntento = v },
  // Las declaraciones de función son ligables: se puede sustituir render
  // para provocar un fallo de dibujado de verdad.
  get render(){ return render },
  get pintarCono(){ return pintarCono },
  set render(f){ render = f },
  get bitacora(){ return bitacora },
  get conectarCapas(){ return conectarCapas },
  set conectarCapas(f){ conectarCapas = f },
};`);
const puente = global.puente;

// Leer un elemento que el código aún no consultó no debe reventar la prueba:
// se quiere ver el FALLA con su detalle, no una excepción.
const el = (id) => elementos[id] || { textContent: "(nunca se consultó)", max: "?", hidden: "?" };

const disparar = async (clave, arg) => {
  for (const fn of oyentes[clave] || []) await fn(arg || { target: { checked: true } });
};
const espera = () => new Promise((r) => process.nextTick(r));

(async () => {
  console.log("A. Arranque");
  await disparar("doc:DOMContentLoaded");
  await espera(); await espera();
  chk("pide latest.json", pedidas.some((p) => p.url.startsWith("latest.json")));
  chk("lo pide sin caché",
    pedidas.find((p) => p.url.startsWith("latest.json"))?.opciones?.cache === "no-store");
  chk("no recarga la página entera",
    !pedidas.some((p) => p.url.endsWith(".js") || p.url.endsWith(".css")));

  console.log("\nB. Reacciona a volver a la pestaña y a recuperar la red");
  chk("hay oyente de visibilitychange", (oyentes["doc:visibilitychange"] || []).length > 0);
  chk("hay oyente de online", (oyentes["win:online"] || []).length > 0);

  const antes = pedidas.length;
  puente.ultimoIntento = 0;              // saltarse el antirráfagas para la prueba
  await disparar("win:online");
  await espera(); await espera();
  chk("al volver la red vuelve a consultar", pedidas.length > antes,
    `${pedidas.length - antes} peticiones`);

  console.log("\nC. Sin red no se borra lo que ya se veía");
  const titularPrevio = el("titular").textContent;
  redCaida = true;
  puente.ultimoIntento = 0;
  await disparar("win:online");
  await espera(); await espera();
  chk("el titular no se vacía", el("titular").textContent === titularPrevio,
    el("titular").textContent);
  chk("el sello dice que no se pudo descargar",
    /no se pudo descargar/i.test(el("sello").textContent), el("sello").textContent);
  chk("y no culpa al dibujado", !/dibujarlo/i.test(el("sello").textContent));
  redCaida = false;

  console.log("\nD. El reproductor");
  await disparar("abrir-hist:click");
  await espera(); await espera(); await espera();
  chk("pide la lista de días", pedidas.some((p) => p.url.startsWith("hist/dias.json")));
  chk("pide el índice del día", pedidas.some((p) => p.url.startsWith("hist/2026-08-30.json")));
  chk("el reproductor queda visible", el("reproductor").hidden === false);
  chk("la línea de tiempo cubre los cuadros", el("linea").max === 3,
    String(el("linea").max));
  chk("arranca en el último cuadro", el("marca-hora").textContent === "12:45",
    el("marca-hora").textContent);
  chk("dice cuántos cuadros hay",
    /4 cuadros/.test(el("aviso-hist").textContent), el("aviso-hist").textContent);

  console.log("\nE. Moverse por la línea de tiempo");
  await disparar("linea:input", { target: { value: "2" } });
  await espera(); await espera();
  chk("cambia la hora mostrada", el("marca-hora").textContent === "12:30",
    el("marca-hora").textContent);
  chk("pide los rayos de ese instante",
    pedidas.some((p) => p.url.startsWith("hist/2026-08-30/1230.r.json")));
  chk("precarga los siguientes cuadros", imagenesPedidas.length > 0,
    `${imagenesPedidas.length} precargados`);
  chk("no precarga el día entero de golpe", imagenesPedidas.length <= 6,
    `${imagenesPedidas.length}`);

  console.log("\nF. Un cuadro sin rayos no pide el archivo de rayos");
  const antesR = pedidas.filter((p) => p.url.includes(".r.json")).length;
  await disparar("linea:input", { target: { value: "0" } });
  await espera(); await espera();
  chk("no hay petición extra",
    pedidas.filter((p) => p.url.includes(".r.json")).length === antesR);

  console.log("\nG. Lo que llega en vivo no pisa el historial");
  const capaAntes = puente.capaHistSat;
  const marcaAntes = el("marca-hora").textContent;
  puente.ultimoIntento = 0;
  await disparar("win:online");
  await espera(); await espera();
  chk("la capa del historial sigue siendo la misma", puente.capaHistSat === capaAntes);
  chk("no se movió el cuadro que se estaba viendo",
    el("marca-hora").textContent === marcaAntes, el("marca-hora").textContent);

  console.log("\nH. Volver a ahora");
  await disparar("cerrar-hist:click");
  await espera();
  chk("se cierra el reproductor", el("reproductor").hidden === true);
  chk("se sueltan las capas del historial",
    puente.capaHistSat === null && puente.capaHistRayos === null);
  chk("el historial deja de estar activo", puente.hist === null);

  console.log("\nI. Un fallo al dibujar no se confunde con falta de red");
  // Esto es lo que estaba mal: render() vivía dentro del mismo try que el
  // fetch, así que cualquier error al dibujar hacía que la página dijera
  // "sin conexión" con la conexión perfecta, y se buscaba el problema en la
  // red. Aquí se provoca un fallo de dibujado con la red funcionando.
  const renderBueno = puente.render;
  puente.render = () => { throw new Error("Chart no está definido"); };
  puente.bitacora.length = 0;
  puente.ultimoIntento = 0;
  await disparar("win:online");
  await espera(); await espera(); await espera();

  const sello = el("sello").textContent;
  chk("no culpa a la conexión", !/no se pudo descargar/i.test(sello), sello);
  chk("dice que el fallo fue al dibujar", /dibujarlo/i.test(sello), sello);
  chk("la bitácora guarda el error concreto",
    /Chart no está definido/.test(el("diag-texto").textContent),
    el("diag-texto").textContent.split("\n")[0] || "(vacía)");
  puente.render = renderBueno;

  // Y al revés: sin red, el mensaje sí debe ser el de la descarga.
  redCaida = true;
  puente.ultimoIntento = 0;
  await disparar("win:online");
  await espera(); await espera(); await espera();
  chk("sin red sí culpa a la descarga",
    /no se pudo descargar/i.test(el("sello").textContent), el("sello").textContent);
  chk("y prueba los dos intentos antes de rendirse",
    /no-store/.test(el("diag-texto").textContent)
    && /paciente/.test(el("diag-texto").textContent));
  redCaida = false;

  console.log("\nJ. Una red que se CUELGA (el fallo real en datos móviles)");
  // Distinto de una red caída: la petición ni responde ni falla. Sin límite
  // de tiempo, `fetch` espera para siempre, la página se queda en
  // "Cargando..." y el manejo de errores nunca se ejecuta, así que ni
  // siquiera aparece un mensaje. Es exactamente lo que se veía en el
  // teléfono: todo el HTML dibujado y ningún dato ni ningún error.
  redColgada = true;
  puente.bitacora.length = 0;
  el("titular").textContent = "Cargando...";
  puente.ultimoIntento = 0;

  const enMarcha = disparar("win:online");
  await espera();
  chk("mientras se cuelga, aún no hay veredicto",
    /cargando/i.test(el("titular").textContent), el("titular").textContent);

  await avanzar(8000);          // vence el límite del primer intento (impaciente)
  await avanzar(25000);         // y el del segundo (paciente)
  await enMarcha;
  await espera(); await espera();

  chk("la espera se corta sola", /abort/i.test(el("diag-texto").textContent),
    el("diag-texto").textContent.split("\n")[0] || "(bitácora vacía)");
  chk("la bitácora dice que se colgó",
    /se colgó/.test(el("diag-texto").textContent),
    el("diag-texto").textContent.split("\n")[0] || "(vacía)");
  chk("se prueban los dos intentos antes de rendirse",
    (el("diag-texto").textContent.match(/se colgó/g) || []).length === 2,
    (el("diag-texto").textContent.match(/se colgó/g) || []).length + " intentos");
  chk("y el usuario ve un mensaje, no un 'Cargando' eterno",
    !/cargando/i.test(el("titular").textContent)
    || /no se pudo descargar/i.test(el("sello").textContent),
    `titular "${el("titular").textContent}", sello "${el("sello").textContent}"`);

  console.log("\n   Bitácora tal como la vería Álvaro:");
  for (const linea of el("diag-texto").textContent.split("\n"))
    console.log("     " + linea);
  redColgada = false;

  console.log("\nJ-bis. La red de seguridad se deriva de los dos intentos");
  // No puede avisar ANTES de que el segundo intento haya tenido su
  // oportunidad: estaba fijada en 12 s y al alargar el intento paciente a
  // 25 quedó anunciando un fallo con la petición todavía viva.
  const alarma = temporizadores.find(t => t.ms >= 20000 && t.ms < 60000);
  chk("hay una red de seguridad", !!alarma,
    alarma ? `a los ${alarma.ms / 1000} s` : "no encontrada");
  chk("y espera más que los dos intentos juntos",
    alarma && alarma.ms > 8000 + 25000,
    alarma ? `${alarma.ms} ms vs 33000 ms de intentos` : "");
  el("titular").textContent = "Cargando...";
  if (alarma) { alarma.hecho = false; }
  await avanzar(alarma ? alarma.ms : 40000);
  chk("un 'Cargando' que no cambia acaba avisando",
    !/cargando/i.test(el("titular").textContent), el("titular").textContent);

  console.log("\nK. El diagnóstico dice si las librerías cargaron");
  chk("informa de Leaflet y Chart",
    /Leaflet:/.test(el("diag-entorno").textContent)
    && /Chart\.js:/.test(el("diag-entorno").textContent),
    el("diag-entorno").textContent.split("\n")[0]);

  console.log("\nL. Un accesorio roto no impide traer el dato");
  // Antes, si conectarCapas() o conectarReproductor() lanzaban, el arranque
  // moria ahi y nunca se pedia latest.json: "Cargando..." eterno y ningun
  // mensaje. Los accesorios no pueden bloquear lo esencial.
  const capasBueno = puente.conectarCapas;
  puente.conectarCapas = () => { throw new Error("#cap-satelite no existe"); };
  const antesL = pedidas.length;
  el("titular").textContent = "Cargando...";
  await disparar("doc:DOMContentLoaded");
  await espera(); await espera();
  chk("se pide el dato igualmente",
    pedidas.length > antesL, `${pedidas.length - antesL} peticiones`);
  chk("y la bitácora dice qué accesorio falló",
    /fallo al preparar las capas/.test(el("diag-texto").textContent),
    el("diag-texto").textContent.split("\n")[0] || "(vacía)");
  puente.conectarCapas = capasBueno;

  console.log("\nM. Todo lo que el JavaScript busca existe en el HTML");
  const faltantes = [...consultados].filter(
    (id) => !new RegExp(`id=["']${id}["']`).test(html));
  chk(`${consultados.size} elementos consultados, ninguno inexistente`,
    faltantes.length === 0, faltantes.join(", ") || "—");

  console.log("\nN. El cono de incertidumbre");
  // La celda va al noroeste de la ciudad y se mueve hacia ella.
  const conDato = JSON.parse(JSON.stringify(RESPUESTAS["latest.json"]));
  // Celda al noroeste, viajando hacia el sureste (135°): pasa por la ciudad.
  conDato.celda = {lat: conDato.lat + 0.55, lon: conDato.lon - 0.55,
                   km: 80, eta_min: 95, intensidad: .7, radio_km: 34};
  conDato.motion_bearing = 135;
  conDato.celda.rumbo = 135;
  puente.pintarCono(conDato);
  // El cono vive dentro de un grupo, así que hay que mirar también ahí.
  const dibujadas = () => [...capasEnMapa].flatMap(
    (c) => [c, ...(c.hijos || [])]);
  const conos = dibujadas().filter((c) => c.tipo === "poligono");
  chk("se dibuja el cono", conos.length === 1, `${conos.length}`);
  chk("aparece la nota que lo explica", el("nota-cono").hidden === false);

  if (conos.length) {
    // El polígono se construye recorriendo un lado y volviendo por el otro,
    // así que el primer y el último punto son los dos bordes junto a la
    // CELDA, y los de en medio los del extremo lejano.
    const p = conos[0].puntos;
    const distKm = (a, b) => {
      const dy = (a[0] - b[0]) * 111;
      const dx = (a[1] - b[1]) * 111 * Math.cos(a[0] * Math.PI / 180);
      return Math.hypot(dy, dx);
    };
    const anchoCelda = distKm(p[0], p[p.length - 1]);
    const medio = p.length / 2;
    const anchoLejos = distKm(p[medio - 1], p[medio]);
    chk("es estrecho junto a la celda", anchoCelda < 15,
        `${anchoCelda.toFixed(0)} km`);
    chk("y ancho al otro extremo", anchoLejos > anchoCelda * 2,
        `${anchoLejos.toFixed(0)} km`);
    chk("el ancho lejano sale del radio que publica el motor",
        Math.abs(anchoLejos - 2 * conDato.celda.radio_km) < 4,
        `${anchoLejos.toFixed(0)} vs ${2 * conDato.celda.radio_km}`);

    // Lo que hace que el dibujo signifique algo: el eje es el RUMBO de la
    // celda, no una línea hacia la ciudad. Con el eje viejo esta prueba era
    // imposible de escribir, porque el cono apuntaba aquí por construcción.
    // Por el EJE CENTRAL, no por un borde. Un borde lleva el ensanchamiento
    // dentro y sale inclinado unos grados respecto al rumbo: la primera
    // versión de esta comprobación fallaba por eso, y era la prueba la que
    // estaba mal, no el dibujo.
    const medioDe = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const cerca = medioDe(p[0], p[p.length - 1]);
    const lejos = medioDe(p[medio - 1], p[medio]);
    const ejeY = lejos[0] - cerca[0], ejeX = lejos[1] - cerca[1];
    const rumboDibujado = (Math.atan2(
      ejeX * Math.cos(conDato.lat * Math.PI / 180), ejeY) * 180 / Math.PI
      + 360) % 360;
    const desvio = Math.abs(((rumboDibujado - conDato.motion_bearing + 180)
                             % 360) - 180);
    chk("el eje sigue el rumbo de la celda", desvio < 12,
        `dibujado ${rumboDibujado.toFixed(0)}° vs rumbo ${conDato.motion_bearing}°`);
  }

  console.log("\nN-ter. Una tormenta que pasa de largo se dibuja pasando de largo");
  // El caso que el dibujo viejo no podía representar. Misma celda al
  // noroeste, pero viajando al noreste (45°): se va, no viene. El cono tiene
  // que irse con ella y dejar la ciudad fuera.
  const deLargo = JSON.parse(JSON.stringify(conDato));
  deLargo.motion_bearing = 45;
  deLargo.celda.rumbo = 45;
  puente.pintarCono(deLargo);
  const cono2 = dibujadas().filter((c) => c.tipo === "poligono");
  chk("se dibuja", cono2.length === 1, `${cono2.length}`);
  if (cono2.length) {
    const p2 = cono2[0].puntos;
    // Distancia mínima de la ciudad a cualquier vértice del polígono: si el
    // cono se fuera hacia la ciudad, alguno caería encima.
    const dCiudad = Math.min(...p2.map((v) => {
      const dy = (v[0] - deLargo.lat) * 111;
      const dx = (v[1] - deLargo.lon) * 111 * Math.cos(deLargo.lat * Math.PI / 180);
      return Math.hypot(dy, dx);
    }));
    chk("y la ciudad queda lejos del cono", dCiudad > 40,
        `${dCiudad.toFixed(0)} km del borde más cercano`);
  }

  console.log("\nN-quinquies. La celda manda sobre el promedio del dominio");
  // El caso del 21/09/2026: el dominio dice una cosa y la celda otra. Se
  // dibuja la de la celda, que es la que va a pasar por aquí.
  const discrepa = JSON.parse(JSON.stringify(conDato));
  discrepa.motion_bearing = 90;     // el dominio: hacia el este
  discrepa.celda.rumbo = 270;       // esta celda: hacia el oeste
  puente.pintarCono(discrepa);
  const cono3 = dibujadas().filter((c) => c.tipo === "poligono");
  if (cono3.length) {
    const p3 = cono3[0].puntos, m3 = p3.length / 2;
    const medioDe3 = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const c1 = medioDe3(p3[0], p3[p3.length - 1]);
    const c2 = medioDe3(p3[m3 - 1], p3[m3]);
    const r3 = (Math.atan2((c2[1] - c1[1]) * Math.cos(discrepa.lat * Math.PI / 180),
                           c2[0] - c1[0]) * 180 / Math.PI + 360) % 360;
    chk("el cono sigue a la celda, no al dominio",
        Math.abs(((r3 - 270 + 180) % 360) - 180) < 12,
        `dibujado ${r3.toFixed(0)}° (celda 270°, dominio 90°)`);
  } else {
    chk("se dibuja el cono con rumbo de celda", false, "no se dibujó");
  }

  console.log("\nN-quater. Sin rumbo no hay cono, porque no hay hacia dónde");
  const sinRumbo = JSON.parse(JSON.stringify(conDato));
  delete sinRumbo.motion_bearing;
  delete sinRumbo.celda.rumbo;
  puente.pintarCono(sinRumbo);
  chk("no se dibuja",
      dibujadas().filter((c) => c.tipo === "poligono").length === 0);

  console.log("\nN-bis. Sin celda no se dibuja nada, y sin datos tampoco");
  puente.pintarCono(RESPUESTAS["latest.json"]);
  chk("desaparece el cono",
      dibujadas().filter((c) => c.tipo === "poligono").length === 0);
  chk("y la nota se esconde", el("nota-cono").hidden === true);
  // Una celda a medio publicar -sin radio- no debe reventar el dibujado.
  const aMedias = JSON.parse(JSON.stringify(conDato));
  delete aMedias.celda.radio_km;
  let reventó = false;
  try { puente.pintarCono(aMedias); } catch (e) { reventó = true; }
  chk("una celda sin radio se ignora sin lanzar", !reventó);

  console.log("\nO. Tema claro y oscuro");
  chk("sin elección guardada manda el sistema",
      raiz.dataset.temaFijado === "0", raiz.dataset.temaFijado);
  const eraOscuro = raiz.dataset.tema === "oscuro";
  (oyentes["btn-tema:click"] || []).forEach((f) => f());
  chk("el botón cambia el tema",
      (raiz.dataset.tema === "oscuro") !== eraOscuro,
      raiz.dataset.tema || "claro");
  chk("y lo recuerda", guardado.tema === (eraOscuro ? "claro" : "oscuro"),
      guardado.tema);
  chk("queda marcado como elegido", raiz.dataset.temaFijado === "1");
  chk("el icono del botón acompaña",
      el("btn-tema").textContent === (raiz.dataset.tema === "oscuro" ? "☀️" : "🌙"),
      el("btn-tema").textContent);

  console.log("\nO-bis. Una elección explícita no la pisa el sistema");
  const temaElegido = raiz.dataset.tema;
  sistemaOscuro = !(temaElegido === "oscuro");
  oyentesMQ.forEach((f) => f({ matches: sistemaOscuro }));
  chk("el cambio del sistema se ignora", raiz.dataset.tema === temaElegido,
      raiz.dataset.tema || "claro");

  console.log("\nO-ter. Los colores se piden al CSS, no van escritos a mano");
  // Si alguien vuelve a poner un "#697790" en una gráfica, aquí se nota:
  // dejarían de pedirse las variables al repintar.
  pedidos.length = 0;
  puente.render(RESPUESTAS["latest.json"]);
  const pedidasUnicas = new Set(pedidos);
  chk("se consultan variables de color al dibujar", pedidasUnicas.size >= 3,
      `${pedidasUnicas.size} variables`);
  chk("y todas son variables CSS, no literales",
      [...pedidasUnicas].every((n) => n.startsWith("--")),
      [...pedidasUnicas].join(" "));

  console.log("\nO-quater. Si el navegador bloquea el almacenamiento, no pasa nada");
  almacenRoto = true;
  let murió = false, fallo = "";
  try { (oyentes["btn-tema:click"] || []).forEach((f) => f()); }
  catch (e) { murió = true; fallo = e.message; }
  chk("el botón sigue funcionando", !murió, fallo);
  chk("y queda anotado en la bitácora",
      puente.bitacora.some((l) => /no se pudo recordar el tema/.test(l)),
      puente.bitacora.slice(-1)[0] || "(vacía)");
  almacenRoto = false;

  console.log("\n" + (ok ? "TODO EN ORDEN" : "HAY FALLOS"));
  process.exit(ok ? 0 : 1);
})();
