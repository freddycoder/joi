// Joi, côté navigateur : micro -> serveur, audio du serveur -> haut-parleur, et l'hologramme.

const $ = (id) => document.getElementById(id);
const toile = $("hologramme");
const pinceau = toile.getContext("2d");

const LIBELLES = {
  veille: "en veille",
  eveil: "réveil…",
  ecoute: "écoute",
  reflechit: "réfléchit",
  parle: "parle",
};
const LIBELLE_MODE_ECOUTE = "écoute seule";

// Apparence de l'hologramme selon l'état : couleur, vitesse de rotation, luminosité, agitation.
const APPARENCES = {
  veille: { couleur: [95, 216, 255], vitesse: 0.12, lumiere: 0.35, agitation: 0.0 },
  eveil: { couleur: [155, 123, 255], vitesse: 0.6, lumiere: 0.6, agitation: 0.25 },
  ecoute: { couleur: [95, 216, 255], vitesse: 0.22, lumiere: 0.85, agitation: 0.04 },
  reflechit: { couleur: [155, 123, 255], vitesse: 1.1, lumiere: 0.9, agitation: 0.3 },
  parle: { couleur: [255, 111, 200], vitesse: 0.35, lumiere: 1.0, agitation: 0.08 },
};
// En mode écoute, l'hologramme est atténué, quel que soit l'état.
const APPARENCE_MODE_ECOUTE = { couleur: [95, 216, 255], vitesse: 0.06, lumiere: 0.22, agitation: 0.0 };

let etat = "veille";
let mode = "conversation"; // conversation | ecoute (confirmé par le serveur)
let ws = null;
let reponseFinie = true;
let texteJoi = "";

// ---------- États et sous-titres ----------

function changerEtat(nouvel) {
  etat = nouvel;
  afficherEtat();
}

function afficherEtat() {
  const el = $("etat");
  el.textContent = mode === "ecoute" && etat === "ecoute" ? LIBELLE_MODE_ECOUTE : LIBELLES[etat] ?? etat;
  el.dataset.etat = etat;
}

function afficherErreur(message) {
  const el = $("erreur");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(afficherErreur.minuterie);
  afficherErreur.minuterie = setTimeout(() => (el.hidden = true), 10000);
}

function afficherJoi(phrase) {
  texteJoi = (texteJoi + " " + phrase).trim();
  // On garde la fin du texte pour que les sous-titres restent lisibles.
  if (texteJoi.length > 280) texteJoi = "…" + texteJoi.slice(-280).replace(/^\S*\s/, "");
  $("ligne-joi").textContent = texteJoi;
}

// ---------- Lecture de la voix de Joi ----------

let ctxSortie, analyseurSortie;
let prochainDebut = 0;
let generation = 0; // incrémentée à chaque interruption, pour ignorer l'audio périmé
let chaineDecodage = Promise.resolve();
const sources = new Set();

function preparerSortie() {
  ctxSortie = new AudioContext();
  analyseurSortie = ctxSortie.createAnalyser();
  analyseurSortie.fftSize = 512;
  analyseurSortie.connect(ctxSortie.destination);
}

function jouerPhrase(mp3) {
  const gen = generation;
  // Le décodage est asynchrone : on enchaîne les promesses pour garder l'ordre des phrases.
  chaineDecodage = chaineDecodage.then(async () => {
    if (gen !== generation) return;
    let audio;
    try {
      audio = await ctxSortie.decodeAudioData(mp3);
    } catch (e) {
      console.warn("Audio illisible", e);
      return;
    }
    if (gen !== generation) return;
    const source = ctxSortie.createBufferSource();
    source.buffer = audio;
    source.connect(analyseurSortie);
    const debut = Math.max(ctxSortie.currentTime + 0.03, prochainDebut);
    source.start(debut);
    prochainDebut = debut + audio.duration;
    sources.add(source);
    source.onended = () => {
      sources.delete(source);
      verifierFinParole();
    };
  });
}

function couperAudio() {
  generation++;
  for (const s of sources) {
    try { s.stop(); } catch { /* déjà arrêtée */ }
  }
  sources.clear();
  prochainDebut = 0;
}

function verifierFinParole() {
  if (reponseFinie && sources.size === 0 && (etat === "parle" || etat === "reflechit")) {
    changerEtat("ecoute");
  }
}

// ---------- Micro ----------

let analyseurMicro;
let pisteMicro;

function contraintesMicro(annulerEcho) {
  return { channelCount: 1, echoCancellation: annulerEcho, noiseSuppression: true, autoGainControl: true };
}

async function demarrerMicro() {
  const flux = await navigator.mediaDevices.getUserMedia({ audio: contraintesMicro(true) });
  pisteMicro = flux.getAudioTracks()[0];
  // Contexte à 16 kHz : le navigateur rééchantillonne le micro au format attendu par AssemblyAI.
  const ctxMicro = new AudioContext({ sampleRate: 16000 });
  await ctxMicro.audioWorklet.addModule("/static/micro-worklet.js");
  const source = ctxMicro.createMediaStreamSource(flux);
  const noeud = new AudioWorkletNode(ctxMicro, "micro-processor");
  noeud.port.onmessage = (e) => {
    if (ws && ws.readyState === WebSocket.OPEN && etat !== "eveil") ws.send(e.data);
  };
  source.connect(noeud);
  noeud.connect(ctxMicro.destination); // silencieux, mais garde le nœud actif
  analyseurMicro = ctxMicro.createAnalyser();
  analyseurMicro.fftSize = 512;
  source.connect(analyseurMicro);
}

// ---------- Mode écoute ----------

let verrouEcran = null;
let debutEnregistrement = 0;
let minuterieEnregistrement = null;

// Wake Lock : empêche l'écran (et donc le PC) de se mettre en veille pendant l'écoute.
// Le navigateur relâche le verrou quand l'onglet est caché : on le reprend quand il revient.
async function garderEcranAllume() {
  if (!("wakeLock" in navigator) || verrouEcran || document.visibilityState !== "visible") return;
  try {
    verrouEcran = await navigator.wakeLock.request("screen");
    verrouEcran.addEventListener("release", () => (verrouEcran = null));
    if (mode !== "ecoute") relacherEcran(); // revenu en conversation pendant la demande
  } catch (e) {
    console.warn("Wake Lock refusé :", e);
  }
}

function relacherEcran() {
  verrouEcran?.release().catch(() => {});
  verrouEcran = null;
}

document.addEventListener("visibilitychange", () => {
  if (mode === "ecoute") garderEcranAllume();
});

// Joi ne parle pas en mode écoute : pas d'écho à annuler, et sans l'annulation d'écho
// le micro capte mieux les voix éloignées. À valider dans l'atelier (voir la console).
// applyConstraints remplace toutes les contraintes : on les redonne toutes.
async function reglerMicro(annulerEcho) {
  if (!pisteMicro) return;
  try {
    await pisteMicro.applyConstraints(contraintesMicro(annulerEcho));
  } catch (e) {
    console.warn("Réglage du micro impossible :", e);
  }
  console.info("Micro :", pisteMicro.getSettings());
}

function afficherDureeEnregistrement() {
  const s = Math.floor((Date.now() - debutEnregistrement) / 1000);
  const deux = (n) => String(n).padStart(2, "0");
  $("duree-enregistrement").textContent =
    `${deux(Math.floor(s / 3600))}:${deux(Math.floor(s / 60) % 60)}:${deux(s % 60)}`;
}

function appliquerMode(nouveau) {
  const avant = mode;
  mode = nouveau;
  document.body.dataset.mode = mode;
  const bouton = $("mode");
  bouton.disabled = false;
  bouton.textContent = mode === "ecoute" ? "revenir" : LIBELLE_MODE_ECOUTE;
  bouton.title = mode === "ecoute"
    ? "Revenir en conversation avec Joi (ou dis « Joi, reviens »)"
    : "Joi se tait et enregistre ce qu'elle entend (ou dis « Joi, écoute seulement »)";
  $("clavier").hidden = mode === "ecoute" || !ws || ws.readyState !== WebSocket.OPEN;
  afficherEtat();
  if (mode === avant) return;

  if (mode === "ecoute") {
    $("ligne-humain").textContent = "";
    $("ligne-joi").textContent = "";
    debutEnregistrement = Date.now();
    afficherDureeEnregistrement();
    minuterieEnregistrement = setInterval(afficherDureeEnregistrement, 1000);
    $("enregistrement").hidden = false;
    garderEcranAllume();
    reglerMicro(false);
  } else {
    clearInterval(minuterieEnregistrement);
    $("enregistrement").hidden = true;
    relacherEcran();
    reglerMicro(true);
  }
}

$("mode").addEventListener("click", () => {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  $("mode").disabled = true; // jusqu'à la confirmation du serveur
  ws.send(JSON.stringify({ type: "mode", mode: mode === "ecoute" ? "conversation" : "ecoute" }));
});

// ---------- Connexion au serveur ----------

function connecter() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    mesures.connexion = Date.now();
  };

  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) {
      noterPremiereParole();
      jouerPhrase(e.data);
      return;
    }
    const msg = JSON.parse(e.data);
    switch (msg.type) {
      case "etat":
        if (msg.etat === "reflechit") {
          mesures.echanges++;
          mesures.debutReflexion = performance.now();
          reponseFinie = false;
          texteJoi = "";
          $("ligne-joi").textContent = "";
        }
        changerEtat(msg.etat);
        break;
      case "transcription":
        $("ligne-humain").textContent = msg.texte;
        break;
      case "reponse":
        afficherJoi(msg.texte);
        break;
      case "fin_reponse":
        reponseFinie = true;
        chaineDecodage.then(verifierFinParole);
        break;
      case "stop_audio":
        couperAudio();
        if (etat === "parle") changerEtat("ecoute");
        break;
      case "mode":
        appliquerMode(msg.mode);
        $("mode").hidden = false;
        break;
      case "erreur":
        afficherErreur(msg.message);
        break;
    }
  };

  ws.onclose = () => {
    mesures.connexion = null;
    appliquerMode("conversation");
    changerEtat("veille");
    couperAudio();
    $("reveiller").hidden = false;
    $("clavier").hidden = true;
    $("mode").hidden = true;
  };
}

$("reveiller").addEventListener("click", async () => {
  $("reveiller").hidden = true;
  changerEtat("eveil");
  try {
    if (!ctxSortie) preparerSortie();
    if (!analyseurMicro) await demarrerMicro();
  } catch (e) {
    afficherErreur("Impossible d'accéder au micro : " + e.message);
    $("reveiller").hidden = false;
    changerEtat("veille");
    return;
  }
  connecter();
  $("clavier").hidden = false;
});

$("clavier").addEventListener("submit", (e) => {
  e.preventDefault();
  const texte = $("saisie").value.trim();
  if (!texte || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "texte", texte }));
  $("saisie").value = "";
});

// ---------- Hologramme ----------

const NB_PARTICULES = 900;
const particules = [];
const angleOr = Math.PI * (3 - Math.sqrt(5));
for (let i = 0; i < NB_PARTICULES; i++) {
  // Points répartis uniformément sur une sphère (spirale de Fibonacci).
  const y = 1 - (i / (NB_PARTICULES - 1)) * 2;
  const r = Math.sqrt(1 - y * y);
  const a = angleOr * i;
  particules.push({ x: Math.cos(a) * r, y, z: Math.sin(a) * r, phase: Math.random() * Math.PI * 2 });
}

const apparence = { ...APPARENCES.veille, couleur: [...APPARENCES.veille.couleur] };
let rotation = 0;
let niveauLisse = 0;
let dernierTemps = performance.now();

function niveauSonore(analyseur) {
  if (!analyseur) return 0;
  const donnees = new Uint8Array(analyseur.fftSize);
  analyseur.getByteTimeDomainData(donnees);
  let somme = 0;
  for (const v of donnees) {
    const s = (v - 128) / 128;
    somme += s * s;
  }
  return Math.min(1, Math.sqrt(somme / donnees.length) * 4);
}

function redimensionner() {
  const ratio = window.devicePixelRatio || 1;
  toile.width = innerWidth * ratio;
  toile.height = innerHeight * ratio;
  pinceau.setTransform(ratio, 0, 0, ratio, 0, 0);
}
addEventListener("resize", redimensionner);
redimensionner();

function dessiner(maintenant) {
  const dt = Math.min(0.05, (maintenant - dernierTemps) / 1000);
  dernierTemps = maintenant;
  const t = maintenant / 1000;

  // Transition douce vers l'apparence de l'état courant.
  const cible = mode === "ecoute" ? APPARENCE_MODE_ECOUTE : APPARENCES[etat] ?? APPARENCES.veille;
  const k = 1 - Math.exp(-dt * 3);
  for (const cle of ["vitesse", "lumiere", "agitation"]) apparence[cle] += (cible[cle] - apparence[cle]) * k;
  for (let i = 0; i < 3; i++) apparence.couleur[i] += (cible.couleur[i] - apparence.couleur[i]) * k;

  const brut = etat === "parle" ? niveauSonore(analyseurSortie) : etat === "ecoute" ? niveauSonore(analyseurMicro) * (mode === "ecoute" ? 0.3 : 0.6) : 0;
  niveauLisse += (brut - niveauLisse) * (brut > niveauLisse ? 0.5 : 0.12);

  rotation += apparence.vitesse * dt;
  const largeur = innerWidth;
  const hauteur = innerHeight;
  const cx = largeur / 2;
  const cy = hauteur * 0.42;
  const rayon = Math.min(largeur, hauteur) * 0.2 * (1 + niveauLisse * 0.25);
  const [rouge, vert, bleu] = apparence.couleur.map(Math.round);
  // Léger scintillement d'hologramme.
  const scintillement = 0.92 + 0.08 * Math.sin(t * 47) * Math.sin(t * 13);
  const lumiere = apparence.lumiere * scintillement;

  pinceau.globalCompositeOperation = "source-over";
  pinceau.fillStyle = "rgba(5, 4, 10, 0.35)"; // traînée de mouvement
  pinceau.fillRect(0, 0, largeur, hauteur);

  // Halo central.
  pinceau.globalCompositeOperation = "lighter";
  const halo = pinceau.createRadialGradient(cx, cy, 0, cx, cy, rayon * (1.6 + niveauLisse));
  halo.addColorStop(0, `rgba(${rouge}, ${vert}, ${bleu}, ${0.22 * lumiere + niveauLisse * 0.25})`);
  halo.addColorStop(1, "rgba(0, 0, 0, 0)");
  pinceau.fillStyle = halo;
  pinceau.fillRect(0, 0, largeur, hauteur);

  const cosR = Math.cos(rotation);
  const sinR = Math.sin(rotation);
  const inclinaison = 0.35;
  const cosI = Math.cos(inclinaison);
  const sinI = Math.sin(inclinaison);

  for (const p of particules) {
    // Pulsation : chaque particule respire avec la voix, plus une agitation propre à l'état.
    const vague = Math.sin(p.phase + t * 2.2 + p.y * 3);
    const deformation = 1 + niveauLisse * 0.35 * vague + apparence.agitation * 0.25 * Math.sin(p.phase * 3 + t * 5);

    let x = p.x * deformation;
    let y = p.y * deformation;
    let z = p.z * deformation;
    // Rotation autour de l'axe vertical, puis inclinaison.
    [x, z] = [x * cosR - z * sinR, x * sinR + z * cosR];
    [y, z] = [y * cosI - z * sinI, y * sinI + z * cosI];

    const perspective = 1 / (1.6 - z * 0.6);
    const px = cx + x * rayon * perspective * 1.6;
    const py = cy + y * rayon * perspective * 1.6;
    const profondeur = (z + 1) / 2; // 0 = derrière, 1 = devant
    const alpha = (0.15 + profondeur * 0.75) * lumiere;
    const taille = 0.6 + profondeur * 1.6 + niveauLisse;

    pinceau.fillStyle = `rgba(${rouge}, ${vert}, ${bleu}, ${alpha})`;
    pinceau.fillRect(px - taille / 2, py - taille / 2, taille, taille);
  }

  requestAnimationFrame(dessiner);
}
requestAnimationFrame(dessiner);

// ---------- Panneaux de diagnostic : Alt+P, Alt+M, Alt+I, Échap ----------

const mesures = {
  connexion: null, // Date.now() à l'ouverture du WebSocket
  echanges: 0,
  debutReflexion: null, // performance.now() quand Joi commence à réfléchir
  latences: [], // ms entre « réfléchit » et la première phrase reçue
};

function noterPremiereParole() {
  if (mesures.debutReflexion === null) return;
  mesures.latences.push(performance.now() - mesures.debutReflexion);
  if (mesures.latences.length > 20) mesures.latences.shift();
  mesures.debutReflexion = null;
}

const panneaux = { gauche: $("panneau-parametres"), droite: $("panneau-monitoring") };
let minuterieMonitoring = null;
const estOuvert = (cote) => panneaux[cote].classList.contains("ouvert");

function basculerPanneau(cote, ouvrir = !estOuvert(cote)) {
  const panneau = panneaux[cote];
  panneau.classList.toggle("ouvert", ouvrir);
  panneau.inert = !ouvrir;
  document.body.classList.toggle(`panneau-${cote}`, ouvrir);
  $("astuce").classList.add("cachee");
  if (cote === "gauche" && ouvrir) chargerParametres();
  if (cote === "droite") {
    clearInterval(minuterieMonitoring);
    minuterieMonitoring = null;
    if (ouvrir) {
      rafraichirMonitoring();
      minuterieMonitoring = setInterval(rafraichirMonitoring, 2000);
      requestAnimationFrame(dessinerOscilloscope);
    }
  }
}

addEventListener("keydown", (e) => {
  if (e.key === "Escape" && (estOuvert("gauche") || estOuvert("droite"))) {
    basculerPanneau("gauche", false);
    basculerPanneau("droite", false);
    return;
  }
  // Alt (ou Ctrl+Alt) + lettre ; e.code ne dépend pas de la disposition du clavier.
  if (!e.altKey || e.shiftKey || e.metaKey || e.repeat) return;
  if (e.code === "KeyP") basculerPanneau("gauche");
  else if (e.code === "KeyM") basculerPanneau("droite");
  else if (e.code === "KeyI") {
    const ouvrir = !(estOuvert("gauche") && estOuvert("droite"));
    basculerPanneau("gauche", ouvrir);
    basculerPanneau("droite", ouvrir);
  } else return;
  e.preventDefault();
});

setTimeout(() => $("astuce").classList.add("cachee"), 12000);

// --- Affichage ---

function remplir(dl, lignes) {
  if (typeof dl === "string") dl = $(dl);
  dl.replaceChildren();
  for (const [nom, valeur, classe] of lignes) {
    const dt = document.createElement("dt");
    dt.textContent = nom;
    const dd = document.createElement("dd");
    dd.textContent = valeur ?? "—";
    if (classe) dd.className = classe;
    dl.append(dt, dd);
  }
}

const deuxChiffres = (n) => String(n).padStart(2, "0");
function duree(s) {
  if (s == null) return "—";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600);
  return h ? `${h}h${deuxChiffres(Math.floor(s / 60) % 60)}` : `${Math.floor(s / 60)}:${deuxChiffres(s % 60)}`;
}
function octets(n) {
  if (n < 1024) return `${n} o`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} Ko`;
  return `${(n / 1024 ** 2).toFixed(1)} Mo`;
}
const horodatage = (iso) => (iso ? iso.replace("T", " ").slice(5, 16) : "jamais");

function messageVide(conteneur, texte) {
  const p = document.createElement("p");
  p.className = "vide";
  p.textContent = texte;
  conteneur.replaceChildren(p);
}

// --- Paramètres (panneau gauche) ---

async function chargerParametres() {
  const conteneur = $("parametres");
  try {
    const reponse = await fetch("/api/parametres");
    if (!reponse.ok) throw new Error(`HTTP ${reponse.status}`);
    const { groupes } = await reponse.json();
    conteneur.replaceChildren();
    for (const groupe of groupes) {
      const section = document.createElement("section");
      const titre = document.createElement("h3");
      titre.textContent = groupe.titre;
      const dl = document.createElement("dl");
      section.append(titre, dl);
      conteneur.append(section);
      remplir(dl, groupe.lignes.map((l) => [l.nom, l.valeur, l.valeur === "MANQUANTE" ? "alerte" : ""]));
    }
  } catch (e) {
    messageVide(conteneur, `Serveur injoignable (${e.message})`);
  }

  const reglages = pisteMicro?.getSettings?.() ?? {};
  const ouiNon = (v) => (pisteMicro ? (v ? "oui" : "non") : "—");
  remplir("parametres-navigateur", [
    ["Micro", pisteMicro ? (pisteMicro.label || "actif").slice(0, 28) : "pas encore ouvert"],
    ["Annulation d'écho", ouiNon(reglages.echoCancellation)],
    ["Réduction du bruit", ouiNon(reglages.noiseSuppression)],
    ["Envoi au serveur", "PCM 16 kHz mono"],
    ["Sortie audio", ctxSortie ? `${ctxSortie.sampleRate / 1000} kHz` : "—"],
    ["Écran allumé", "wakeLock" in navigator ? (verrouEcran ? "verrouillé" : "disponible") : "non pris en charge"],
  ]);
}

// --- Monitoring (panneau droit) ---

async function rafraichirMonitoring() {
  const relie = ws && ws.readyState === WebSocket.OPEN;
  const latences = mesures.latences;
  const moyenne = latences.length ? latences.reduce((a, b) => a + b, 0) / latences.length : null;
  remplir("mon-session", [
    ["Liaison", relie ? "connectée" : "hors ligne", relie ? "ok" : "attention"],
    ["État", `${LIBELLES[etat] ?? etat} · ${mode}`, etat === "veille" ? "" : "actif"],
    ["Éveillée depuis", mesures.connexion ? duree((Date.now() - mesures.connexion) / 1000) : "—"],
    ["Échanges", String(mesures.echanges)],
    ["Temps de réponse", latences.length ? `${(latences.at(-1) / 1000).toFixed(1)} s` : "—"],
    ["Moyenne", moyenne ? `${(moyenne / 1000).toFixed(1)} s (${latences.length})` : "—"],
  ]);

  let d;
  try {
    const reponse = await fetch("/api/monitoring");
    if (!reponse.ok) throw new Error(`HTTP ${reponse.status}`);
    d = await reponse.json();
  } catch (e) {
    remplir("mon-serveur", [["Serveur", `injoignable (${e.message})`, "alerte"]]);
    return;
  }

  const v = d.voix;
  remplir("mon-voix", [
    ["Dernier moteur", v.moteur ?? "—", v.moteur?.includes("repli") ? "attention" : ""],
    ["Dernière synthèse", v.derniere_ms != null ? `${v.derniere_ms} ms` : "—"],
    ["Synthèses", `${v.syntheses} · cache ${v.cache}`],
    ["Replis edge-tts", String(v.replis), v.replis ? "attention" : ""],
    ["Cache", `${v.cache_fichiers} phrases · ${octets(v.cache_octets)}`],
  ]);

  const seance = d.ecoute.seances[0];
  const seg = d.ecoute.segments;
  const lignesEcoute = [];
  if (seance) {
    lignesEcoute.push(
      ["Enregistrement", duree(seance.audio_s), "actif"],
      ["Segment en cours", `${duree(seance.segment_courant_s)} / ${duree(seance.duree_segment_s)}`],
      ["Pas encore appris", duree(seance.non_appris_s)],
      ["Prochaine analyse", seance.prochaine_analyse_s != null ? `dans ${duree(seance.prochaine_analyse_s)}` : "fin d'écoute"],
    );
  } else {
    lignesEcoute.push(["Enregistrement", "inactif"]);
  }
  lignesEcoute.push(
    ["Segments en attente", String(seg.enregistre ?? 0), seg.enregistre ? "attention" : ""],
    ["Transcrits / appris", `${seg.transcrit ?? 0} / ${seg.consolide ?? 0}`],
  );
  if (seg.echec) lignesEcoute.push(["En échec", String(seg.echec), "alerte"]);
  remplir("mon-ecoute", lignesEcoute);

  // Barre : progression vers la prochaine analyse de l'écoute.
  const barre = $("mon-progression");
  barre.hidden = !(seance && seance.prochaine_analyse_s != null);
  if (!barre.hidden) {
    const total = seance.non_appris_s + seance.prochaine_analyse_s;
    barre.firstElementChild.style.width = `${Math.min(100, (100 * seance.non_appris_s) / Math.max(1, total))}%`;
  }

  const t = d.transcription;
  const m = d.memoire;
  remplir("mon-memoire", [
    ["Transcription", t.en_cours ? "en cours" : "au repos", t.en_cours ? "actif" : ""],
    ["Audio transcrit", `${(t.secondes / 3600).toFixed(2)} h · ${t.segments} seg.`],
    ["Coût estimé", `${t.cout.toFixed(2)} $ US`],
    ["Dernière passe", horodatage(t.derniere)],
    ["Bibliothécaire", m.consolidation_en_cours ? "range les souvenirs" : "au repos", m.consolidation_en_cours ? "actif" : ""],
    ["Consolidé jusqu'à", horodatage(m.derniere_consolidation)],
    ["Fichiers mémoire", String(m.fichiers)],
  ]);

  remplir("mon-serveur", [
    ["En ligne depuis", duree(d.serveur.depuis_s)],
    ["Onglets connectés", String(d.serveur.conversations)],
    ["Tâches de fond", String(d.serveur.taches_de_fond), d.serveur.taches_de_fond ? "actif" : ""],
  ]);

  const liste = $("mon-evenements");
  liste.replaceChildren();
  for (const ev of [...d.evenements].reverse()) {
    const li = document.createElement("li");
    li.className = ev.niveau;
    const quand = document.createElement("time");
    quand.textContent = ev.heure;
    const source = document.createElement("span");
    source.className = "source";
    source.textContent = ev.source;
    li.append(quand, source, document.createTextNode(ev.message));
    liste.append(li);
  }
  if (!d.evenements.length) {
    const li = document.createElement("li");
    li.textContent = "rien à signaler";
    liste.append(li);
  }
}

// Oscilloscope : la forme d'onde du micro (cyan) et de la voix de Joi (rose), en direct.
const toileOscillo = $("oscilloscope");
const pinceauOscillo = toileOscillo.getContext("2d");
const ondes = new Map(); // un tampon réutilisé par analyseur

function tracerOnde(analyseur, couleur, largeur, hauteur) {
  if (!analyseur) return;
  if (!ondes.has(analyseur)) ondes.set(analyseur, new Uint8Array(analyseur.fftSize));
  const donnees = ondes.get(analyseur);
  analyseur.getByteTimeDomainData(donnees);
  pinceauOscillo.strokeStyle = couleur;
  pinceauOscillo.shadowColor = couleur;
  pinceauOscillo.shadowBlur = 6;
  pinceauOscillo.lineWidth = 1.2;
  pinceauOscillo.beginPath();
  for (let i = 0; i < donnees.length; i++) {
    const x = (i / (donnees.length - 1)) * largeur;
    const y = hauteur / 2 + ((donnees[i] - 128) / 128) * hauteur * 0.9;
    if (i) pinceauOscillo.lineTo(x, y);
    else pinceauOscillo.moveTo(x, y);
  }
  pinceauOscillo.stroke();
}

function dessinerOscilloscope() {
  if (!estOuvert("droite")) return;
  const ratio = window.devicePixelRatio || 1;
  const largeur = toileOscillo.clientWidth;
  const hauteur = toileOscillo.clientHeight;
  if (toileOscillo.width !== Math.round(largeur * ratio)) {
    toileOscillo.width = Math.round(largeur * ratio);
    toileOscillo.height = Math.round(hauteur * ratio);
  }
  pinceauOscillo.setTransform(ratio, 0, 0, ratio, 0, 0);
  pinceauOscillo.clearRect(0, 0, largeur, hauteur);
  if (!analyseurMicro && !analyseurSortie) {
    pinceauOscillo.shadowBlur = 0;
    pinceauOscillo.strokeStyle = "rgba(95, 216, 255, 0.35)";
    pinceauOscillo.beginPath();
    pinceauOscillo.moveTo(0, hauteur / 2);
    pinceauOscillo.lineTo(largeur, hauteur / 2);
    pinceauOscillo.stroke();
  }
  tracerOnde(analyseurMicro, "rgba(95, 216, 255, 0.9)", largeur, hauteur);
  tracerOnde(analyseurSortie, "rgba(255, 111, 200, 0.9)", largeur, hauteur);
  requestAnimationFrame(dessinerOscilloscope);
}
