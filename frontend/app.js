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

// Apparence de l'hologramme selon l'état : couleur, vitesse de rotation, luminosité, agitation.
const APPARENCES = {
  veille: { couleur: [95, 216, 255], vitesse: 0.12, lumiere: 0.35, agitation: 0.0 },
  eveil: { couleur: [155, 123, 255], vitesse: 0.6, lumiere: 0.6, agitation: 0.25 },
  ecoute: { couleur: [95, 216, 255], vitesse: 0.22, lumiere: 0.85, agitation: 0.04 },
  reflechit: { couleur: [155, 123, 255], vitesse: 1.1, lumiere: 0.9, agitation: 0.3 },
  parle: { couleur: [255, 111, 200], vitesse: 0.35, lumiere: 1.0, agitation: 0.08 },
};

let etat = "veille";
let ws = null;
let reponseFinie = true;
let texteJoi = "";

// ---------- États et sous-titres ----------

function changerEtat(nouvel) {
  etat = nouvel;
  const el = $("etat");
  el.textContent = LIBELLES[nouvel] ?? nouvel;
  el.dataset.etat = nouvel;
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

async function demarrerMicro() {
  const flux = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
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

// ---------- Connexion au serveur ----------

function connecter() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) {
      jouerPhrase(e.data);
      return;
    }
    const msg = JSON.parse(e.data);
    switch (msg.type) {
      case "etat":
        if (msg.etat === "reflechit") {
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
      case "erreur":
        afficherErreur(msg.message);
        break;
    }
  };

  ws.onclose = () => {
    changerEtat("veille");
    couperAudio();
    $("reveiller").hidden = false;
    $("clavier").hidden = true;
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
  const cible = APPARENCES[etat] ?? APPARENCES.veille;
  const k = 1 - Math.exp(-dt * 3);
  for (const cle of ["vitesse", "lumiere", "agitation"]) apparence[cle] += (cible[cle] - apparence[cle]) * k;
  for (let i = 0; i < 3; i++) apparence.couleur[i] += (cible.couleur[i] - apparence.couleur[i]) * k;

  const brut = etat === "parle" ? niveauSonore(analyseurSortie) : etat === "ecoute" ? niveauSonore(analyseurMicro) * 0.6 : 0;
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
