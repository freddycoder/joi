// Tourne dans le fil audio du navigateur : convertit le son du micro (flottants -1..1)
// en PCM 16 bits et l'envoie par paquets de 50 ms, le format attendu par AssemblyAI.
// Le contexte audio est créé à 16 kHz, donc 50 ms = 800 échantillons.

const TAILLE_PAQUET = 800;

class MicroProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.tampon = new Int16Array(TAILLE_PAQUET);
    this.position = 0;
  }

  process(entrees) {
    const canal = entrees[0] && entrees[0][0];
    if (!canal) return true;

    for (let i = 0; i < canal.length; i++) {
      const s = Math.max(-1, Math.min(1, canal[i]));
      this.tampon[this.position++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.position === TAILLE_PAQUET) {
        this.port.postMessage(this.tampon.buffer, [this.tampon.buffer]);
        this.tampon = new Int16Array(TAILLE_PAQUET);
        this.position = 0;
      }
    }
    return true;
  }
}

registerProcessor("micro-processor", MicroProcessor);
