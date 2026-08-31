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

global.document = {
  addEventListener(ev, fn) { (oyentes[`doc:${ev}`] ||= []).push(fn); },
  querySelector(sel) { return elemento(sel.replace("#", "")); },
  getElementById(id) { return elemento(id); },
  createElement() { return nuevoElemento("nuevo"); },
  hidden: false,
};
global.window = { addEventListener(ev, fn) { (oyentes[`win:${ev}`] ||= []).push(fn); } };
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
    addTo() { capasEnMapa.add(this); return this; },
    remove() { capasEnMapa.delete(this); return this; },
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
const js = html.match(/<script>\n([\s\S]*)\n<\/script>/)[1];

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
  chk("y prueba los dos modos de caché antes de rendirse",
    /no-store/.test(el("diag-texto").textContent)
    && /normal/.test(el("diag-texto").textContent));
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

  await avanzar(8000);          // vence el límite del primer intento
  await avanzar(8000);          // y el del segundo
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

  console.log("\nJ-bis. La red de seguridad de los 12 segundos");
  el("titular").textContent = "Cargando...";
  await avanzar(12000);
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

  console.log("\n" + (ok ? "TODO EN ORDEN" : "HAY FALLOS"));
  process.exit(ok ? 0 : 1);
})();
