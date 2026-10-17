/*
 * Témoin de lien Ethernet — l'écran et le rapport.
 *
 * Un seul fichier, sans dépendance ni construction. Il ne parle qu'à l'API du
 * poste (mêmes origine et port) : `/api/etat` toutes les 3 s, et pendant la
 * mesure `/api/observation` à la même cadence ; rien quand l'onglet est caché.
 * Une observation passée rouverte est lue une fois, jamais sondée.
 *
 * `rapport.html` charge ce même fichier : la tête, la bande et la liste y sont
 * dessinées par les mêmes fonctions.
 */
(function () {
  'use strict';

  const CADENCE_MS = 3000;
  const LISTE_OBS_MS = 15000;
  const FENETRES = ['tout', '1h', '24h'];
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ------------------------------------------------------------------ formats
  const nombre = (n, dec = 0) => n.toLocaleString('fr-FR', { maximumFractionDigits: dec });
  const deux = (n) => String(n).padStart(2, '0');
  // Les instants viennent en ISO 8601 avec fuseau ; on borne les décimales
  // (certains navigateurs refusent les microsecondes).
  const date = (iso) => new Date(String(iso).replace(/(\.\d{3})\d+/, '$1'));

  function duree(s) {
    s = Math.max(0, Math.round(s));
    if (s < 60) return `${s} s`;
    const m = Math.floor(s / 60);
    if (m < 60) return s % 60 ? `${m} min ${deux(s % 60)} s` : `${m} min`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} h ${deux(m % 60)}`;
    return `${nombre(Math.floor(h / 24))} j ${h % 24} h`;
  }
  // Pour le verdict : une précision qui ne change qu'à la minute (il est lu à voix haute).
  const dureeVerdict = (s) => (s < 60 ? "moins d'une minute" : s < 3600 ? `${Math.floor(s / 60)} min` : duree(s));

  const heure = (iso, sec = true) => date(iso).toLocaleTimeString('fr-FR',
    sec ? { hour: '2-digit', minute: '2-digit', second: '2-digit' } : { hour: '2-digit', minute: '2-digit' });
  const jour = (iso) => date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' });
  const jourComplet = (iso) => date(iso).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' });
  const memeJour = (a, b) => date(a).toDateString() === date(b).toDateString();
  // « 00:42 » aujourd'hui, « 16/10 23:58 » un autre jour.
  const quand = (iso) => (memeJour(iso, new Date()) ? heure(iso, false) : `${jour(iso)} ${heure(iso, false)}`);
  const instant = (iso, avecJour) => (avecJour ? `${jour(iso)} ${heure(iso)}` : heure(iso));

  const ETATS = {
    debout: 'debout', secours: 'en secours', coupe: 'coupé', desactive: 'désactivé',
    non_mesure: 'non mesuré', redemarrage: 'redémarrage du routeur',
  };

  // ------------------------------------------------------------------ verdict
  function texteVerdict(v, maintenant = new Date()) {
    const ecoule = v.depuis ? (maintenant - date(v.depuis)) / 1000 : 0;
    const depuis = v.depuis ? `depuis ${quand(v.depuis)}` : '';
    switch (v.etat) {
      case 'stable':
        return { classe: 'v-stable', titre: `Lien stable depuis ${dureeVerdict(ecoule)}`, detail: depuis };
      case 'coupe':
        return { classe: 'v-coupe', titre: `Coupé depuis ${quand(v.depuis)}`, detail: `depuis ${dureeVerdict(ecoule)}` };
      case 'injoignable':
        return { classe: 'v-gris', titre: 'Routeur injoignable', detail: depuis ? `${depuis} — non mesuré, ce n'est pas une coupure` : '' };
      case 'desactive':
        return { classe: 'v-gris', titre: 'Lien désactivé', detail: depuis ? `${depuis} — désactivé sur le routeur, ce n'est pas une coupure` : '' };
      default:
        return v.depuis
          ? { classe: 'v-gris', titre: `Aucune mesure depuis ${quand(v.depuis)}`, detail: 'non mesuré depuis (poste en veille, outil fermé ou collecte arrêtée)' }
          : { classe: 'v-gris', titre: 'Aucune mesure', detail: 'en attente du premier relevé' };
    }
  }

  // Arrondi vers le bas : 99,999 % ne doit pas s'afficher « 100 % » quand il y a eu une coupure.
  const texteDispo = (d) => (d == null ? 'non mesuré' : `${nombre(Math.floor(d * 10000 + 1e-9) / 100, 2)} %`);
  const AU_DERNIER = { ...ETATS, debout: 'stable', secours: 'stable (en secours)' };

  // Une observation close ne dit pas un état « actuel » : elle se résume.
  function resumeClos(data) {
    const n = data.chiffres.coupures;
    const dernier = data.bande.length ? data.bande[data.bande.length - 1].etat : null;
    const periode = `du ${jourComplet(data.debut)} ${heure(data.debut, false)}`
      + (data.dernier ? ` au ${jourComplet(data.dernier)} ${heure(data.dernier, false)}` : '');
    return {
      classe: 'v-gris is-close',
      titre: `Observation close — ${nombre(n)} coupure${n > 1 ? 's' : ''} · disponibilité ${texteDispo(data.chiffres.disponibilite)}`,
      detail: [periode, dernier ? `au dernier relevé : ${AU_DERNIER[dernier] || dernier}` : ''].filter(Boolean).join(' · '),
    };
  }

  // La tête : verdict (zone lue à voix haute, réécrite seulement s'il change) et trois chiffres.
  // `vivante` faux : observation close, résumée au lieu d'un verdict.
  function remplirTete(conteneur, data, vivante = true) {
    if (!conteneur.querySelector('.verdict')) {
      conteneur.innerHTML = '<div class="verdict" aria-live="polite" aria-atomic="true"><h1></h1>'
        + '<div class="detail"></div></div><div class="chiffre"></div><div class="chiffre"></div><div class="chiffre"></div>';
    }
    const v = vivante ? texteVerdict(data.verdict) : resumeClos(data);
    const bloc = conteneur.querySelector('.verdict');
    bloc.className = `verdict ${v.classe}`;
    const h1 = bloc.querySelector('h1');
    if (h1.textContent !== v.titre) h1.textContent = v.titre;
    const detail = [v.detail, `${data.lien} · ${data.modele || 'modèle inconnu'}`].filter(Boolean).join(' · ');
    if (bloc.querySelector('.detail').textContent !== detail) bloc.querySelector('.detail').textContent = detail;

    const c = data.chiffres;
    const micro = data.coupures.filter((x) => x.genre === 'micro-coupure').length;
    const dispo = texteDispo(c.disponibilite);
    const cases = [
      ['Coupures', nombre(c.coupures), micro ? `dont ${nombre(micro)} micro-coupure${micro > 1 ? 's' : ''}` : 'sur la fenêtre affichée'],
      ['Durée coupée', duree(c.coupe_s), micro ? 'cumulée, micro-coupures non chiffrées' : 'cumulée'],
      ['Disponibilité', dispo, c.mesure_s ? `sur ${duree(c.mesure_s)} mesurées` : 'aucun temps mesuré'],
    ];
    conteneur.querySelectorAll('.chiffre').forEach((el, i) => {
      const [lbl, val, sous] = cases[i];
      el.innerHTML = `<span class="lbl">${lbl}</span><span class="v">${esc(val)}</span><span class="sous">${esc(sous)}</span>`;
    });
  }

  // Les lignes qui disent une réserve sur la mesure affichée.
  function notes(data) {
    const l = [];
    if (data.horloge_decalee) l.push('Horloges du routeur et du poste en désaccord — les causes du journal ne sont pas rapprochées.');
    if (data.etat_lien === 'introuvable') l.push("Le lien choisi n'apparaît plus dans la réponse du routeur.");
    return l.map((t) => `<p class="message is-reserve">${esc(t)}</p>`).join('');
  }

  // ------------------------------------------------------------------ la bande
  function contenuBulle(data, cle) {
    const multi = data.bande.length && !memeJour(data.bande[0].debut, data.bande[data.bande.length - 1].fin);
    const i = Number(cle.slice(1));
    if (cle[0] === 's') {
      const s = data.bande[i];
      return `<b>${esc(ETATS[s.etat] || s.etat)}</b><span>${instant(s.debut, multi)} → ${instant(s.fin, multi)}</span>`
        + `<span>${duree((date(s.fin) - date(s.debut)) / 1000)}</span>`;
    }
    const c = data.coupures[i];
    const cause = `Cause : ${c.cause ? esc(c.cause) : 'non donnée par le routeur'}`;
    if (c.genre === 'micro-coupure') {
      return `<b>Micro-coupure</b><span>vers ${instant(c.debut, multi)}</span><span>durée inconnue (&lt; 3 s)</span><span>${cause}</span>`;
    }
    if (!c.fin) return `<b>Coupure en cours</b><span>depuis ${instant(c.debut, multi)}</span><span>${cause}</span>`;
    return `<b>Coupure</b><span>${instant(c.debut, multi)} → ${instant(c.fin, multi)}</span>`
      + `<span>durée ${duree(c.duree_s)}</span><span>${cause}</span>`;
  }

  function montrerBulle(html, x, y) {
    const b = $('infobulle');
    if (!b) return;
    b.innerHTML = html;
    b.hidden = false;
    const l = Math.min(x + 14, window.innerWidth - b.offsetWidth - 8);
    b.style.left = `${Math.max(8, l)}px`;
    b.style.top = `${y + 18 + b.offsetHeight > window.innerHeight ? y - b.offsetHeight - 10 : y + 18}px`;
  }
  let pointeur = null;
  const cacherBulle = () => { if ($('infobulle')) $('infobulle').hidden = true; };

  /*
   * Dessine la bande de `data` (réponse de /api/observation) dans `conteneur`.
   * Le SVG est écrit en balisage et confié à l'analyseur HTML : pas d'espace de
   * noms à écrire en dur. `opts.eclaire` : début de la coupure éclairée ;
   * `opts.surCoupure(i)` : appelé au clic sur la coupure i.
   */
  function dessinerBande(conteneur, data, opts = {}) {
    const bande = data.bande || [];
    const legende = '<div class="legende" aria-hidden="true">'
      + '<span><i class="l-debout"></i>debout</span><span><i class="l-secours"></i>en secours</span>'
      + '<span><i class="l-coupe"></i>coupé</span><span><i class="l-micro"></i>micro-coupure</span>'
      + '<span><i class="l-desactive"></i>désactivé</span><span><i class="l-non_mesure"></i>non mesuré</span>'
      + '<span><i class="l-redemarrage"></i>redémarrage du routeur</span></div>';
    // Le focus clavier survit au redessin : il revient sur la même coupure.
    const actif = document.activeElement;
    const focusDebut = actif && conteneur.contains(actif) ? actif.getAttribute('data-debut') : null;
    if (!bande.length) {
      cacherBulle();
      conteneur.innerHTML = '<div class="bande"><div class="bande-svg"></div></div>'
        + '<p class="panel-note">Aucun relevé sur cette fenêtre : non mesuré.</p>' + legende;
      return;
    }
    const t0 = +date(bande[0].debut);
    const t1 = +date(bande[bande.length - 1].fin);
    const W = Math.max(300, Math.round((conteneur.clientWidth || 1032) - 32));
    const H = 44;
    const ech = W / Math.max(1, t1 - t0);
    const x = (iso) => Math.min(W, Math.max(0, (+date(iso) - t0) * ech));
    const f = (n) => n.toFixed(2);

    let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Bande de temps de l'observation">`
      + '<defs><pattern id="temoin-hachure" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
      + '<line class="hachure-trait" x1="0" y1="0" x2="0" y2="7"/></pattern>'
      + '<pattern id="temoin-hachure-dense" width="3.5" height="3.5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
      + '<line class="hachure-trait" x1="0" y1="0" x2="0" y2="3.5"/></pattern></defs>';
    bande.forEach((s, i) => {
      const a = x(s.debut);
      const w = Math.max(x(s.fin) - a, 0.6);
      svg += `<rect class="s-${esc(s.etat)}" x="${f(a)}" y="0" width="${f(w)}" height="${H}" data-tip="s${i}"/>`;
      if (s.etat === 'secours') svg += `<rect class="s-secours-trait" x="${f(a)}" y="0" width="${f(w)}" height="3" data-tip="s${i}"/>`;
    });
    data.coupures.forEach((c, i) => {
      const a = x(c.debut);
      const ec = opts.eclaire === c.debut ? ' is-eclaire' : '';
      const etiquette = esc(`${c.genre} à ${heure(c.debut)}`);
      svg += `<g data-tip="c${i}" data-debut="${esc(c.debut)}" class="coupure${ec}" tabindex="0" role="button" aria-label="${etiquette}">`;
      if (c.genre === 'micro-coupure') {
        svg += `<rect class="m-cible" x="${f(a - 6)}" y="0" width="12" height="${H}"/>`
          + `<rect class="m-micro" x="${f(a - 1)}" y="0" width="2" height="${H}"/>`;
      } else {
        const b = c.fin ? x(c.fin) : W;
        svg += `<rect class="m-coupure" x="${f(a)}" y="0" width="${f(Math.max(b - a, 3))}" height="${H}"/>`;
      }
      svg += '</g>';
    });
    svg += '</svg>';

    const multi = !memeJour(bande[0].debut, bande[bande.length - 1].fin);
    const axe = [0, 0.25, 0.5, 0.75, 1].map((p) => {
      const iso = new Date(t0 + p * (t1 - t0)).toISOString();
      return `<span class="num" style="left:${p * 100}%">${instant(iso, multi).slice(0, multi ? 11 : 5)}</span>`;
    }).join('');

    conteneur.innerHTML = `<div class="bande"><div class="bande-svg">${svg}</div></div><div class="axe">${axe}</div>${legende}`;
    const zone = conteneur.querySelector('svg');
    const cible = (ev) => ev.target.closest && ev.target.closest('[data-tip]');
    // La bande est redessinée à chaque relevé : la bulle ouverte suit ce qui
    // se trouve maintenant sous le pointeur, ou se ferme.
    if ($('infobulle') && !$('infobulle').hidden && pointeur) {
      const sous = document.elementFromPoint(pointeur.x, pointeur.y);
      const t = sous && zone.contains(sous) ? sous.closest('[data-tip]') : null;
      if (t) montrerBulle(contenuBulle(data, t.dataset.tip), pointeur.x, pointeur.y); else cacherBulle();
    }
    if (focusDebut) {
      const g = [...zone.querySelectorAll('[data-debut]')].find((el) => el.getAttribute('data-debut') === focusDebut);
      if (g) {
        // Les écouteurs ne sont pas encore posés : la bulle se rouvre à la main.
        g.focus({ preventScroll: true });
        const r = g.getBoundingClientRect();
        montrerBulle(contenuBulle(data, g.dataset.tip), r.left, r.bottom);
      } else {
        cacherBulle();
      }
    }
    zone.addEventListener('mousemove', (ev) => {
      const t = cible(ev);
      pointeur = { x: ev.clientX, y: ev.clientY };
      if (t) montrerBulle(contenuBulle(data, t.dataset.tip), ev.clientX, ev.clientY); else cacherBulle();
    });
    zone.addEventListener('mouseleave', cacherBulle);
    zone.addEventListener('focusin', (ev) => {
      const t = cible(ev);
      if (!t) return;
      const r = t.getBoundingClientRect();
      montrerBulle(contenuBulle(data, t.dataset.tip), r.left, r.bottom);
    });
    zone.addEventListener('focusout', cacherBulle);
    const choisir = (ev) => {
      const t = cible(ev);
      if (t && t.dataset.tip[0] === 'c' && opts.surCoupure) opts.surCoupure(Number(t.dataset.tip.slice(1)));
    };
    zone.addEventListener('click', choisir);
    zone.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); choisir(ev); }
    });
  }

  // ------------------------------------------------------------------ la liste
  // `vivante` : la fin d'une coupure non close est « en cours » ; sinon elle
  // l'était encore au dernier relevé de l'observation.
  function dessinerListe(conteneur, data, opts = {}) {
    const rangs = data.coupures.map((c, i) => ({ c, i }))
      .sort((a, b) => date(b.c.debut) - date(a.c.debut));
    if (!rangs.length) {
      conteneur.innerHTML = '<table class="coupures"><tbody><tr class="vide"><td>Aucune coupure sur la fenêtre affichée.</td></tr></tbody></table>';
      return;
    }
    const multi = data.bande.length > 0 && !memeJour(data.bande[0].debut, data.bande[data.bande.length - 1].fin);
    const lignes = rangs.map(({ c, i }) => {
      let fin; let dur;
      if (c.genre === 'micro-coupure') {
        fin = '—'; dur = 'inconnue (&lt; 3 s)';
      } else if (!c.fin) {
        const borne = opts.vivante ? new Date() : date(data.dernier || c.debut);
        fin = `<span class="en-cours">${opts.vivante ? 'en cours' : 'en cours au dernier relevé'}</span>`;
        dur = `${duree((borne - date(c.debut)) / 1000)} <span class="en-cours">en cours</span>`;
      } else {
        fin = instant(c.fin, multi); dur = duree(c.duree_s);
      }
      const ec = opts.eclaire === c.debut ? ' class="is-eclaire"' : '';
      const genre = c.genre === 'micro-coupure' ? '<span class="genre-micro">micro-coupure</span>' : 'coupure';
      return `<tr data-i="${i}"${ec}><td class="num">${instant(c.debut, multi)}</td><td class="num">${fin}</td>`
        + `<td class="num">${dur}</td><td>${genre}</td><td>${c.cause ? esc(c.cause) : '<span title="non donnée par le routeur">—</span>'}</td></tr>`;
    }).join('');
    conteneur.innerHTML = '<table class="coupures"><thead><tr><th>Début</th><th>Fin</th><th>Durée</th>'
      + `<th>Type</th><th>Cause</th></tr></thead><tbody>${lignes}</tbody></table>`;
    if (opts.surCoupure) {
      conteneur.querySelector('tbody').addEventListener('click', (ev) => {
        const tr = ev.target.closest('tr[data-i]');
        if (tr) opts.surCoupure(Number(tr.dataset.i), false);
      });
    }
  }

  // ------------------------------------------------------------------ l'API
  async function api(chemin, corps) {
    let r;
    try {
      r = await fetch(chemin, corps === undefined
        ? { headers: { Accept: 'application/json' }, cache: 'no-store' }
        : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corps) });
    } catch (e) {
      const x = new Error('Le témoin ne répond plus : le programme a-t-il été fermé ? Nouvel essai dans 3 s.');
      x.reseau = true;
      throw x;
    }
    let json = null;
    try { json = await r.json(); } catch (e) { /* corps vide */ }
    if (!r.ok) {
      const x = new Error(json && typeof json.detail === 'string' ? json.detail : `Le témoin a répondu ${r.status}.`);
      x.status = r.status;
      throw x;
    }
    return json;
  }

  // ------------------------------------------------------------------ l'écran
  const etat = {
    dernier: null,      // dernière réponse de /api/etat
    vue: null,
    obsLive: null,      // l'observation mesurée en ce moment
    lecture: null,      // une observation passée, rouverte en lecture seule
    donnees: null,      // dernière réponse de /api/observation affichée
    fenetre: 'tout',
    eclaire: null,      // début de la coupure éclairée
    formHote: null,     // formulaire ouvert depuis la recherche
    identHote: null,
    compte: 'admin',
    sigRecherche: null,
    sigLiens: null,
    sigObs: null,
    sigMesure: null,
    envoi: false,
    obsLues: 0,
    erreurAction: false,
  };

  function erreur(message, action = false) {
    const el = $('erreur');
    if (!message) { if (!etat.erreurAction || action) { el.hidden = true; etat.erreurAction = false; } return; }
    el.textContent = message;
    el.hidden = false;
    etat.erreurAction = action;
  }
  const occupe = (oui) => $('vue-recherche').parentElement.setAttribute('aria-busy', oui ? 'true' : 'false');

  // ---- sondage
  let timer = null;
  let enCours = false;
  let encore = false;
  async function tour() {
    if (enCours) { encore = true; return; }
    clearTimeout(timer);
    if (document.hidden) return;
    enCours = true;
    try {
      appliquerEtat(await api('/api/etat'));
      erreur(null);
      if (etat.dernier.phase === 'mesure' && etat.lecture == null) await lireObservation();
      if (Date.now() - etat.obsLues > LISTE_OBS_MS) await lireObservations();
    } catch (err) {
      erreur(err.message);
    } finally {
      enCours = false;
    }
    if (encore) { encore = false; tour(); return; }
    if (!document.hidden) timer = setTimeout(tour, CADENCE_MS);
  }

  function appliquerEtat(e) {
    etat.dernier = e;
    etat.obsLive = e.phase === 'mesure' ? e.observation : null;
    if (e.phase === 'choix_lien' || e.phase === 'mesure') etat.formHote = null;
    let vue = e.phase === 'choix_lien' ? 'lien' : e.phase;
    if (e.phase === 'recherche' && etat.formHote) vue = 'identifiants';
    if (etat.lecture != null) vue = 'mesure';
    if (vue !== etat.vue && etat.vue === 'identifiants') viderFormulaire();
    etat.vue = vue;
    for (const v of ['recherche', 'identifiants', 'lien', 'mesure']) $(`vue-${v}`).hidden = v !== vue;

    if (e.phase === 'recherche') $('identite').textContent = 'Recherche du routeur…';
    else if (e.phase === 'identifiants') $('identite').textContent = `Routeur ${e.hote}`;
    else $('identite').textContent = [e.modele, e.lien].filter(Boolean).join(' · ') || 'Routeur connecté';

    if (vue === 'recherche') rendreRecherche(e.equipements || []);
    if (vue === 'identifiants') rendreIdentifiants(etat.formHote || e.hote, e.phase);
    if (vue === 'lien') rendreLiens(e);
    $('retour-direct').textContent = etat.obsLive != null ? 'Revenir à la mesure en cours' : 'Fermer';
    $('nouvelle').hidden = etat.lecture != null || e.phase !== 'mesure';
    if ($('nouvelle').hidden) $('confirmer').hidden = true;
  }

  // ---- 1. recherche
  function rendreRecherche(equipements) {
    const sig = JSON.stringify(equipements);
    if (sig === etat.sigRecherche) return;
    etat.sigRecherche = sig;
    const liste = $('equipements');
    liste.hidden = !equipements.length;
    $('aucun-equipement').hidden = equipements.length > 0;
    $('recherche-note').textContent = equipements.length > 1 ? `${equipements.length} routeurs trouvés` : '';
    liste.innerHTML = equipements.map((q, i) => `<div class="equipement"><span class="hote num">${esc(q.hote)}</span>`
      + `<span class="detail">${q.firmware ? `firmware ${esc(q.firmware)}` : ''}${q.retenu ? ' · identifiants retenus sur ce poste' : ''}</span>`
      + `<button type="button" data-i="${i}"${equipements.length === 1 ? ' class="is-primary"' : ''}>Se connecter</button></div>`).join('');
    liste.querySelectorAll('button').forEach((b) => {
      const q = equipements[Number(b.dataset.i)];
      b.addEventListener('click', () => (q.retenu ? connecter({ hote: q.hote, retenir: true }) : ouvrirFormulaire(q.hote)));
    });
  }

  async function connecter(corps) {
    if (etat.envoi) return;
    etat.envoi = true;
    erreur(null, true);
    occupe(true);
    try {
      appliquerEtat(await api('/api/connexion', corps));
      etat.formHote = null;
    } catch (err) {
      if (err.status === 401) {
        // Jamais de nouvel essai automatique : le formulaire attend un clic.
        ouvrirFormulaire(corps.hote);
        if (!/^identifiants requis$/i.test(err.message)) refus(err.message);
      } else {
        erreur(err.message, true);
      }
    } finally {
      etat.envoi = false;
      occupe(false);
      tour();
    }
  }

  // ---- 2. identifiants
  function ouvrirFormulaire(hote) {
    etat.formHote = hote;
    if (etat.dernier) appliquerEtat(etat.dernier);
  }
  function refus(message) {
    $('ident-refus').textContent = message;
    $('ident-refus').hidden = false;
  }
  function viderFormulaire() {
    $('form-identifiants').reset();
    $('ident-refus').hidden = true;
    etat.identHote = null;
  }
  function rendreIdentifiants(hote, phase) {
    if (etat.identHote !== hote) {
      viderFormulaire();
      etat.identHote = hote;
      $('ident-hote').textContent = hote;
    }
    $('ident-annuler').hidden = !(etat.formHote && phase === 'recherche');
  }
  function choisirCompte(compte) {
    etat.compte = compte;
    document.querySelectorAll('[data-compte]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.compte === compte)));
    document.querySelectorAll('[data-pour]').forEach((d) => { d.hidden = d.dataset.pour !== compte; });
  }
  function envoyerIdentifiants(ev) {
    ev.preventDefault();
    const f = ev.target.elements;
    const corps = { hote: etat.identHote, retenir: f.retenir.checked };
    const [a, b] = etat.compte === 'admin' ? ['username', 'password'] : ['client_id', 'client_secret'];
    if (!f[a].value.trim() || !f[b].value) {
      refus(etat.compte === 'admin' ? "Saisissez l'utilisateur et le mot de passe." : 'Saisissez le client ID et le client secret.');
      return;
    }
    corps[a] = f[a].value.trim();
    corps[b] = f[b].value;
    $('ident-refus').hidden = true;
    connecter(corps);
  }

  // ---- 3. choix du lien
  function rendreLiens(e) {
    $('lien-modele').textContent = e.modele || '';
    const sig = JSON.stringify([e.liens, e.lien_retenu]);
    if (sig === etat.sigLiens) return;
    etat.sigLiens = sig;
    const coche = $('liens').querySelector('input:checked');
    const choisi = coche ? coche.value : e.lien_retenu;
    $('aucun-lien').hidden = e.liens.length > 0;
    $('liens').hidden = !e.liens.length;
    $('liens').innerHTML = e.liens.map((l) => `<label><input type="radio" name="wan" value="${esc(l.wan_id)}"`
      + `${String(l.wan_id) === String(choisi) ? ' checked' : ''}><span class="pastille ${l.connecte ? 'is-ok' : 'is-coupe'}"></span>`
      + `<b>WAN ${esc(l.wan_id)}</b><span>${l.connecte ? 'connecté' : 'non connecté'}</span></label>`).join('');
    majMesurer();
  }
  const majMesurer = () => { $('mesurer').disabled = !$('liens').querySelector('input:checked'); };
  async function mesurer(ev) {
    ev.preventDefault();
    const coche = $('liens').querySelector('input:checked');
    if (!coche || etat.envoi) return;
    etat.envoi = true;
    erreur(null, true);
    try {
      await api('/api/lien', { wan_id: coche.value });
      etat.donnees = null;
      etat.obsLues = 0;
    } catch (err) {
      erreur(err.message, true);
    } finally {
      etat.envoi = false;
      tour();
    }
  }

  // ---- 4. la mesure
  async function lireObservation() {
    const id = etat.lecture ?? etat.obsLive;
    if (id == null) return;
    const data = await api(`/api/observation?id=${encodeURIComponent(id)}&fenetre=${etat.fenetre}`);
    // L'affichage a changé entre-temps (autre observation, autre fenêtre) : réponse périmée.
    if ((etat.lecture ?? etat.obsLive) !== id || data.fenetre !== etat.fenetre) return;
    etat.donnees = data;
    rendreMesure();
  }

  function eclairer(i, depuisBande = true) {
    const c = etat.donnees && etat.donnees.coupures[i];
    if (!c) return;
    etat.eclaire = etat.eclaire === c.debut && !depuisBande ? null : c.debut;
    rendreMesure();
    const tr = $('liste').querySelector('tr.is-eclaire');
    if (tr && depuisBande) tr.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  function rendreMesure() {
    const data = etat.donnees;
    if (!data) return;
    const vivante = etat.lecture == null;
    $('lecture').hidden = vivante;
    if (!vivante) {
      $('lecture-quoi').textContent = `${data.lien} · ${data.modele || ''} · du ${jourComplet(data.debut)} ${heure(data.debut, false)}`
        + (data.dernier ? ` au ${jourComplet(data.dernier)} ${heure(data.dernier, false)}` : '');
    }
    $('notes').innerHTML = notes(data);
    remplirTete($('tete'), data, vivante);
    const b = data.bande;
    $('bande-note').textContent = b.length ? `${quand(b[0].debut)} → ${quand(b[b.length - 1].fin)}` : '';
    const opts = { eclaire: etat.eclaire, surCoupure: eclairer, vivante };
    // Rien de neuf : ni bande ni liste ne sont redessinées (focus et bulle restent).
    // Seule la durée d'une coupure en cours avance : la liste suit, elle n'a pas de focus.
    const sig = [JSON.stringify(data), etat.eclaire, vivante, $('bande').clientWidth].join('|');
    const neuf = sig !== etat.sigMesure;
    etat.sigMesure = sig;
    if (neuf) dessinerBande($('bande'), data, opts);
    if (neuf || (vivante && data.coupures.some((c) => !c.fin && c.genre !== 'micro-coupure'))) {
      dessinerListe($('liste'), data, opts);
    }
    $('export-coupures').href = `/api/observations/${data.id}/coupures.csv`;
    $('export-releves').href = `/api/observations/${data.id}/releves.csv`;
    $('rapport').href = `rapport.html?id=${data.id}&fenetre=${etat.fenetre}`;
  }

  function choisirFenetre(f) {
    etat.fenetre = f;
    try { localStorage.setItem('temoin.fenetre', f); } catch (e) { /* commodité seulement */ }
    document.querySelectorAll('[data-fenetre]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.fenetre === f)));
    lireObservation().catch((err) => erreur(err.message));
  }

  // ---- observations passées
  async function lireObservations() {
    etat.obsLues = Date.now();
    const liste = await api('/api/observations');
    etat.observations = liste;
    const sel = $('observations');
    $('choix-observation').hidden = !liste.length;
    const actuelle = String(etat.lecture ?? etat.obsLive ?? '');
    const sig = JSON.stringify([liste, actuelle]);
    if (sig === etat.sigObs || document.activeElement === sel) return;
    etat.sigObs = sig;
    const libelle = (o) => `n° ${o.id} · ${o.lien} · ${o.modele || ''} · ${jour(o.debut)} ${heure(o.debut, false)}`
      + (o.en_cours ? ' (en cours)' : o.dernier ? ` → ${jour(o.dernier)} ${heure(o.dernier, false)}` : '');
    sel.innerHTML = (actuelle ? '' : '<option value="">Rouvrir une observation…</option>')
      + liste.map((o) => `<option value="${o.id}">${esc(libelle(o))}</option>`).join('');
    sel.value = actuelle;
  }

  function ouvrirObservation(id) {
    etat.lecture = id == null || id === etat.obsLive ? null : id;
    etat.donnees = null;
    etat.eclaire = null;
    etat.sigObs = null;
    $('observations').value = String(etat.lecture ?? etat.obsLive ?? '');
    if (etat.dernier) appliquerEtat(etat.dernier);
    if (etat.lecture != null || etat.obsLive != null) lireObservation().catch((err) => erreur(err.message));
    lireObservations().catch(() => {});
  }

  async function nouvelleObservation() {
    if (etat.envoi) return;
    etat.envoi = true;
    erreur(null, true);
    try {
      await api('/api/observations/nouvelle', {});
      $('confirmer').hidden = true;
      etat.donnees = null;
      etat.eclaire = null;
      etat.obsLues = 0;
    } catch (err) {
      erreur(err.message, true);
    } finally {
      etat.envoi = false;
      tour();
    }
  }

  function ecran() {
    try { if (FENETRES.includes(localStorage.getItem('temoin.fenetre'))) etat.fenetre = localStorage.getItem('temoin.fenetre'); } catch (e) { /* fenêtre par défaut */ }
    document.querySelectorAll('[data-fenetre]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.fenetre === etat.fenetre));
      b.addEventListener('click', () => choisirFenetre(b.dataset.fenetre));
    });
    $('form-adresse').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const hote = $('adresse').value.trim();
      if (hote) connecter({ hote, retenir: true });
    });
    $('form-autre').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const hote = $('autre-adresse').value.trim();
      if (hote) connecter({ hote, retenir: true });
    });
    $('form-identifiants').addEventListener('submit', envoyerIdentifiants);
    document.querySelectorAll('[data-compte]').forEach((b) => b.addEventListener('click', () => choisirCompte(b.dataset.compte)));
    $('ident-annuler').addEventListener('click', () => { etat.formHote = null; viderFormulaire(); appliquerEtat(etat.dernier); });
    $('liens').addEventListener('change', majMesurer);
    $('form-lien').addEventListener('submit', mesurer);
    $('nouvelle').addEventListener('click', () => { $('confirmer').hidden = false; $('nouvelle-oui').focus(); });
    $('nouvelle-non').addEventListener('click', () => { $('confirmer').hidden = true; $('nouvelle').focus(); });
    $('nouvelle-oui').addEventListener('click', nouvelleObservation);
    $('observations').addEventListener('change', (ev) => ouvrirObservation(ev.target.value ? Number(ev.target.value) : null));
    $('retour-direct').addEventListener('click', () => ouvrirObservation(null));
    let attente = null;
    window.addEventListener('resize', () => {
      clearTimeout(attente);
      attente = setTimeout(rendreMesure, 150);
    });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) clearTimeout(timer); else tour();
    });
    tour();
  }

  // ------------------------------------------------------------------ le rapport
  async function rapport() {
    const p = new URLSearchParams(window.location.search);
    const fenetre = FENETRES.includes(p.get('fenetre')) ? p.get('fenetre') : 'tout';
    $('imprimer').addEventListener('click', () => window.print());
    try {
      const data = await api(`/api/observation?id=${encodeURIComponent(p.get('id') || '')}&fenetre=${fenetre}`);
      // Sans la liste, le rapport reste lisible : il se dit alors clos.
      let vivante = false;
      try {
        vivante = (await api('/api/observations')).some((o) => o.id === data.id && o.en_cours);
      } catch (e) { /* repli : observation close */ }
      const champs = {
        'r-equipement': data.modele || 'modèle inconnu',
        'r-lien': data.lien,
        'r-periode': `du ${jourComplet(data.debut)} ${heure(data.debut)}`
          + (data.dernier ? ` au ${jourComplet(data.dernier)} ${heure(data.dernier)}` : '') + (vivante ? ' (mesure en cours)' : ''),
        'r-fenetre': { tout: "l'observation entière", '1h': 'la dernière heure', '24h': 'les dernières 24 h' }[fenetre],
        'r-etabli': `le ${jourComplet(new Date().toISOString())} à ${heure(new Date().toISOString(), false)}`,
      };
      Object.entries(champs).forEach(([id, t]) => { $(id).textContent = t; });
      document.title = `Rapport — ${data.lien} — ${jourComplet(data.debut)}`;
      $('notes').innerHTML = notes(data);
      remplirTete($('tete'), data, vivante);
      const opts = { vivante };
      dessinerBande($('bande'), data, opts);
      dessinerListe($('liste'), data, opts);
      $('contenu').hidden = false;
      window.addEventListener('resize', () => dessinerBande($('bande'), data, opts));
    } catch (err) {
      $('erreur').textContent = err.status === 404 || err.status === 422 ? 'Observation introuvable.' : err.message;
      $('erreur').hidden = false;
    }
  }

  window.Temoin = { duree, texteVerdict, remplirTete, dessinerBande, dessinerListe };
  if (document.body.dataset.page === 'rapport') rapport(); else ecran();
}());
