/* Documents de l'etude (lot L7) — bloc du panneau Ressources du bureau.
 *
 * Liste, ajout (bouton et glisser-deposer), statut d'indexation et retrait
 * (avec confirmation) des documents que l'assistant peut consulter.
 * Balisage : hub/templates/_documents_etude.html. API : /studies/{sid}/documents.
 *
 * Autonome : ne s'appuie que sur qsConfirmer / qsToast (produit.js), avec
 * repli sur confirm() s'ils manquent. Le texte venu du serveur est toujours
 * pose par textContent, jamais interprete comme du balisage.
 */
(function () {
  "use strict";

  var FORMATS = [".pdf", ".docx", ".odt", ".txt", ".md", ".markdown", ".csv", ".xlsx"];
  var LIBELLES = {
    en_attente: "En attente",
    extraction: "Indexation…",
    indexe: "Consultable",
    erreur: "Erreur",
  };
  var CLE_OUVERT = "qs-documents-etude-ouvert";
  var SUIVI_MS = 3000;
  var SUIVI_MAX = 100;

  var suivi = null;
  var essaisSuivi = 0;

  function el(id) { return document.getElementById(id); }

  function section() { return el("src-section-documents"); }

  function estOuvert() {
    var b = el("docs-etude-toggle");
    return !!b && b.getAttribute("aria-expanded") === "true";
  }

  async function sidActif() {
    var s = section();
    if (s && s.dataset.sid) return s.dataset.sid;
    try {
      var r = await fetch("/studies/active", { credentials: "include" });
      if (!r.ok) return null;
      var d = await r.json();
      return (d && d.id) || null;
    } catch (e) {
      return null;
    }
  }

  function taille(octets) {
    var n = Number(octets) || 0;
    if (n < 1024) return n + " o";
    if (n < 1024 * 1024) return Math.round(n / 1024) + " Ko";
    return (n / (1024 * 1024)).toFixed(1).replace(".", ",") + " Mo";
  }

  function annoncer(message, genre) {
    var st = el("docs-etude-statut");
    if (!st) return;
    st.textContent = message || "";
    st.classList.remove("ok", "err");
    if (genre) st.classList.add(genre);
  }

  function toast(message, erreur) {
    if (typeof window.qsToast === "function") {
      window.qsToast(message, erreur ? { type: "erreur" } : undefined);
    } else {
      annoncer(message, erreur ? "err" : "ok");
    }
  }

  async function confirmer(options) {
    if (typeof window.qsConfirmer === "function") return window.qsConfirmer(options);
    return window.confirm(options.titre + "\n" + (options.message || ""));
  }

  function resumer(docs) {
    var meta = el("docs-etude-resume");
    if (!meta) return;
    if (!docs.length) { meta.textContent = "aucun"; return; }
    var prets = docs.filter(function (d) { return d.statut === "indexe"; }).length;
    var enCours = docs.filter(function (d) {
      return d.statut === "en_attente" || d.statut === "extraction";
    }).length;
    var txt = prets + " consultable" + (prets > 1 ? "s" : "");
    if (enCours) txt += ", " + enCours + " en cours";
    meta.textContent = txt;
  }

  function bouton(libelle, classe, etiquette, action) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = classe;
    b.textContent = libelle;
    b.setAttribute("aria-label", etiquette);
    b.addEventListener("click", action);
    return b;
  }

  function ligne(sid, d) {
    var li = document.createElement("li");
    li.className = "docs-etude-doc";
    li.dataset.statut = d.statut;

    var nom = document.createElement("span");
    nom.className = "docs-etude-nom";
    nom.textContent = d.titre || d.nom_fichier || "Document";
    nom.title = d.nom_fichier || "";
    li.appendChild(nom);

    var meta = document.createElement("span");
    meta.className = "docs-etude-meta";
    var morceaux = [String(d.format || "").toUpperCase(), taille(d.taille)];
    if (d.n_pages) morceaux.push(d.n_pages + " p.");
    meta.textContent = morceaux.join(" · ");
    li.appendChild(meta);

    var badge = document.createElement("span");
    badge.className = "docs-etude-badge";
    badge.textContent = LIBELLES[d.statut] || d.statut;
    li.appendChild(badge);

    var actions = document.createElement("span");
    actions.className = "docs-etude-actions";
    var lien = document.createElement("a");
    lien.href = "/studies/" + encodeURIComponent(sid) + "/documents/"
      + encodeURIComponent(d.id) + "/fichier";
    lien.setAttribute("download", d.nom_fichier || "");
    lien.textContent = "Télécharger";
    lien.setAttribute("aria-label", "Télécharger " + (d.titre || "le document"));
    actions.appendChild(lien);
    if (d.statut === "erreur") {
      actions.appendChild(bouton("Relancer", "docs-etude-relancer",
        "Relancer l'indexation de " + (d.titre || "ce document"),
        function () { reindexer(sid, d); }));
    }
    actions.appendChild(bouton("Retirer", "docs-etude-retirer",
      "Retirer " + (d.titre || "ce document") + " de l'étude",
      function () { retirer(sid, d); }));
    li.appendChild(actions);

    var message = d.message || (d.avertissements || [])[0];
    if (message) {
      var p = document.createElement("p");
      p.className = "docs-etude-message";
      p.textContent = message;
      li.appendChild(p);
    }
    return li;
  }

  function rendre(sid, docs) {
    var liste = el("docs-etude-liste");
    resumer(docs);
    if (!liste) return;
    liste.textContent = "";
    if (!docs.length) {
      var vide = document.createElement("li");
      vide.className = "src-empty";
      vide.textContent = "Aucun document pour l'instant.";
      liste.appendChild(vide);
      return;
    }
    docs.forEach(function (d) { liste.appendChild(ligne(sid, d)); });
  }

  function planifierSuivi(docs) {
    if (suivi) { clearTimeout(suivi); suivi = null; }
    var enCours = docs.some(function (d) {
      return d.statut === "en_attente" || d.statut === "extraction";
    });
    if (!enCours || essaisSuivi >= SUIVI_MAX) return;
    essaisSuivi += 1;
    suivi = setTimeout(charger, SUIVI_MS);
  }

  async function charger() {
    var sid = await sidActif();
    if (!sid) { resumer([]); return; }
    try {
      var r = await fetch("/studies/" + encodeURIComponent(sid) + "/documents",
        { credentials: "include" });
      if (!r.ok) {
        var meta = el("docs-etude-resume");
        if (meta) meta.textContent = "indisponible";
        return;
      }
      var corps = await r.json();
      var docs = corps.documents || [];
      rendre(sid, docs);
      planifierSuivi(docs);
    } catch (e) {
      var m = el("docs-etude-resume");
      if (m) m.textContent = "indisponible";
    }
  }

  function extension(nom) {
    var i = (nom || "").lastIndexOf(".");
    return i >= 0 ? nom.slice(i).toLowerCase() : "";
  }

  async function deposer(fichiers) {
    var liste = Array.prototype.slice.call(fichiers || []);
    if (!liste.length) return;
    var sid = await sidActif();
    if (!sid) { annoncer("Aucune étude active.", "err"); return; }
    var reussis = 0;
    var erreurs = [];
    for (var i = 0; i < liste.length; i += 1) {
      var f = liste[i];
      if (FORMATS.indexOf(extension(f.name)) < 0) {
        erreurs.push(f.name + " : format non pris en charge");
        continue;
      }
      annoncer("Envoi de « " + f.name + " » (" + (i + 1) + "/" + liste.length + ")…");
      var fd = new FormData();
      fd.append("file", f);
      try {
        var r = await fetch("/studies/" + encodeURIComponent(sid) + "/documents",
          { method: "POST", body: fd, credentials: "include" });
        if (r.ok) {
          reussis += 1;
        } else {
          var err = await r.json().catch(function () { return {}; });
          erreurs.push(f.name + " : " + (err.detail || ("erreur " + r.status)));
        }
      } catch (e) {
        erreurs.push(f.name + " : connexion perdue");
      }
    }
    if (reussis && !erreurs.length) {
      annoncer(reussis + " document(s) ajouté(s), indexation en cours.", "ok");
    } else if (reussis) {
      annoncer(reussis + "/" + liste.length + " ajouté(s). " + erreurs.join(" ; "), "err");
    } else {
      annoncer(erreurs.join(" ; ") || "Échec de l'envoi.", "err");
    }
    essaisSuivi = 0;
    if (!estOuvert()) basculer(true);
    await charger();
  }

  async function retirer(sid, d) {
    var ok = await confirmer({
      titre: "Retirer « " + (d.titre || "ce document") + " » ?",
      message: "Le fichier est supprimé de l'étude et l'assistant ne pourra plus le consulter.",
      confirmer: "Retirer",
      danger: true,
    });
    if (!ok) return;
    try {
      var r = await fetch("/studies/" + encodeURIComponent(sid) + "/documents/"
        + encodeURIComponent(d.id), { method: "DELETE", credentials: "include" });
      if (r.ok || r.status === 204) {
        toast("Document « " + (d.titre || "") + " » retiré.");
      } else {
        toast("Le retrait a échoué (erreur " + r.status + ").", true);
      }
    } catch (e) {
      toast("Connexion perdue : le document n'a pas été retiré.", true);
    }
    await charger();
  }

  async function reindexer(sid, d) {
    try {
      var r = await fetch("/studies/" + encodeURIComponent(sid) + "/documents/"
        + encodeURIComponent(d.id) + "/reindexer", { method: "POST", credentials: "include" });
      if (!r.ok) toast("Relance impossible (erreur " + r.status + ").", true);
    } catch (e) {
      toast("Connexion perdue : relance impossible.", true);
    }
    essaisSuivi = 0;
    await charger();
  }

  function basculer(ouvrir) {
    var b = el("docs-etude-toggle");
    var corps = el("docs-etude-contenu");
    if (!b || !corps) return;
    var ouvert = typeof ouvrir === "boolean" ? ouvrir : !estOuvert();
    b.setAttribute("aria-expanded", ouvert ? "true" : "false");
    var chevron = b.querySelector(".src-chevron");
    if (chevron) chevron.textContent = ouvert ? "▾" : "▸";
    corps.classList.toggle("collapsed", !ouvert);
    try { localStorage.setItem(CLE_OUVERT, ouvert ? "1" : "0"); } catch (e) { /* prive */ }
    if (ouvert) { essaisSuivi = 0; charger(); }
  }

  function brancherDepot(zone) {
    var s = section();
    if (!s) return;
    // Sur toute la section : le panneau Sources a son propre depot (donnees
    // QGIS) ; on arrete la propagation pour qu'un document ne parte pas
    // aussi dans data/.
    s.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (zone) zone.classList.add("docs-etude-survol");
    });
    s.addEventListener("dragleave", function (e) {
      e.stopPropagation();
      if (s.contains(e.relatedTarget)) return;
      if (zone) zone.classList.remove("docs-etude-survol");
    });
    s.addEventListener("drop", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (zone) zone.classList.remove("docs-etude-survol");
      if (!zone) return;  // pas d'etude active
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        deposer(e.dataTransfer.files);
      }
    });
  }

  function init() {
    if (!section()) return;
    var bascule = el("docs-etude-toggle");
    if (bascule) bascule.addEventListener("click", function () { basculer(); });
    var entree = el("docs-etude-input");
    var choisir = el("docs-etude-choisir");
    if (entree) {
      entree.addEventListener("change", function () {
        deposer(entree.files);
        entree.value = "";
      });
    }
    if (choisir && entree) {
      choisir.addEventListener("click", function (e) {
        e.stopPropagation();
        entree.click();
      });
    }
    brancherDepot(el("docs-etude-depot"));
    var ouvert = false;
    try { ouvert = localStorage.getItem(CLE_OUVERT) === "1"; } catch (e) { /* prive */ }
    if (ouvert) basculer(true); else charger();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
