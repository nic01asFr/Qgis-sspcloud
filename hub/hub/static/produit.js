/* Comportements communs du produit — QGIS Service
 *
 * Deux primitives partagees par « Mon espace » et le bureau :
 *
 *   qsToast(message, options)   message de confirmation non bloquant,
 *                               eventuellement porteur d'une action
 *                               (« Annuler »). Annonce aux lecteurs d'ecran
 *                               par une region role=status.
 *   qsConfirmer(options)        boite de dialogue de confirmation qui
 *                               remplace window.confirm : titre, texte clair,
 *                               bouton d'action nomme (« Dépublier », pas
 *                               « OK »), focus gere, Echap pour renoncer.
 *                               Rend une promesse de booleen.
 *
 * Avant, chaque page improvisait : confirm() natif au libelle « OK »,
 * alert() pour les erreurs, et un toast maison par panneau. Les actions
 * destructives (depublier, archiver) n'offraient aucun retour en arriere.
 *
 * Aucune dependance. Les styles vivent dans produit.css (.qs-toast,
 * .qs-dialogue).
 */
(function () {
  "use strict";

  var region = null;

  function regionToasts() {
    if (region && document.body.contains(region)) return region;
    region = document.createElement("div");
    region.className = "qs-toasts";
    region.setAttribute("role", "status");
    region.setAttribute("aria-live", "polite");
    document.body.appendChild(region);
    return region;
  }

  /**
   * Affiche un message bref.
   * options :
   *   type      "succes" | "erreur" | "info" (defaut "succes")
   *   action    libelle d'un bouton (ex. « Annuler »)
   *   surAction fonction appelee au clic sur le bouton
   *   duree     millisecondes avant disparition (defaut 5000, 8000 avec action)
   *   surFin    fonction appelee quand le message disparait SANS action
   * Rend { fermer() }.
   */
  function qsToast(message, options) {
    var o = options || {};
    var hote = regionToasts();
    var el = document.createElement("div");
    el.className = "qs-toast qs-toast--" + (o.type || "succes");
    var texte = document.createElement("span");
    texte.className = "qs-toast__texte";
    texte.textContent = message;
    el.appendChild(texte);
    var fini = false;
    var minuteur = null;

    function fermer(parAction) {
      if (fini) return;
      fini = true;
      clearTimeout(minuteur);
      el.classList.add("qs-toast--sortie");
      setTimeout(function () { el.remove(); }, 200);
      if (!parAction && typeof o.surFin === "function") o.surFin();
    }

    if (o.action) {
      var bouton = document.createElement("button");
      bouton.type = "button";
      bouton.className = "qs-toast__action";
      bouton.textContent = o.action;
      bouton.addEventListener("click", function () {
        fermer(true);
        if (typeof o.surAction === "function") o.surAction();
      });
      el.appendChild(bouton);
    }
    var croix = document.createElement("button");
    croix.type = "button";
    croix.className = "qs-toast__fermer";
    croix.setAttribute("aria-label", "Fermer le message");
    croix.textContent = "×";
    croix.addEventListener("click", function () { fermer(false); });
    el.appendChild(croix);

    hote.appendChild(el);
    var duree = o.duree || (o.action ? 8000 : 5000);
    // Survol ou focus : on suspend la disparition, le temps de lire ou
    // d'atteindre le bouton au clavier (WCAG 2.2.1, delai reglable).
    function suspendre() { clearTimeout(minuteur); }
    function reprendre() {
      clearTimeout(minuteur);
      minuteur = setTimeout(function () { fermer(false); }, duree);
    }
    el.addEventListener("mouseenter", suspendre);
    el.addEventListener("mouseleave", reprendre);
    el.addEventListener("focusin", suspendre);
    el.addEventListener("focusout", reprendre);
    reprendre();
    return { fermer: function () { fermer(false); } };
  }

  /**
   * Demande une confirmation. options :
   *   titre      question courte (« Dépublier ce livrable ? »)
   *   message    consequence, en une ou deux phrases
   *   confirmer  libelle du bouton d'action (defaut « Confirmer »)
   *   annuler    libelle du bouton de renoncement (defaut « Annuler »)
   *   danger     true pour une action destructive (bouton rouge)
   * Rend une Promise<boolean>.
   */
  function qsConfirmer(options) {
    var o = options || {};
    return new Promise(function (resoudre) {
      var retourFocus = document.activeElement;
      var fond = document.createElement("div");
      fond.className = "qs-dialogue";
      var boite = document.createElement("div");
      boite.className = "qs-dialogue__boite";
      boite.setAttribute("role", "alertdialog");
      boite.setAttribute("aria-modal", "true");
      var idTitre = "qs-dlg-t-" + Date.now();
      var idTexte = "qs-dlg-m-" + Date.now();
      boite.setAttribute("aria-labelledby", idTitre);
      boite.setAttribute("aria-describedby", idTexte);

      var h = document.createElement("h2");
      h.className = "qs-dialogue__titre";
      h.id = idTitre;
      h.textContent = o.titre || "Confirmer ?";
      var p = document.createElement("p");
      p.className = "qs-dialogue__texte";
      p.id = idTexte;
      p.textContent = o.message || "";
      var pied = document.createElement("div");
      pied.className = "qs-dialogue__pied";
      var non = document.createElement("button");
      non.type = "button";
      non.className = "qs-btn qs-btn--tertiaire";
      non.textContent = o.annuler || "Annuler";
      var oui = document.createElement("button");
      oui.type = "button";
      oui.className = "qs-btn" + (o.danger ? " qs-btn--danger" : "");
      oui.textContent = o.confirmer || "Confirmer";
      pied.appendChild(non);
      pied.appendChild(oui);
      boite.appendChild(h);
      boite.appendChild(p);
      boite.appendChild(pied);
      fond.appendChild(boite);
      document.body.appendChild(fond);

      function clore(valeur) {
        document.removeEventListener("keydown", clavier, true);
        fond.remove();
        if (retourFocus && typeof retourFocus.focus === "function") {
          retourFocus.focus();
        }
        resoudre(valeur);
      }
      function clavier(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          clore(false);
        } else if (e.key === "Tab") {
          // Piege a focus : deux boutons, on boucle de l'un a l'autre.
          e.preventDefault();
          (document.activeElement === oui ? non : oui).focus();
        }
      }
      non.addEventListener("click", function () { clore(false); });
      oui.addEventListener("click", function () { clore(true); });
      fond.addEventListener("click", function (e) {
        if (e.target === fond) clore(false);
      });
      document.addEventListener("keydown", clavier, true);
      // Focus sur le renoncement : une validation reflexe par Entree ne
      // doit pas declencher l'action destructive.
      (o.danger ? non : oui).focus();
    });
  }

  /** Copie un texte, avec repli par selection quand l'API est refusee. */
  function qsCopier(texte) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(texte).then(
        function () { return true; },
        function () { return copieRepli(texte); }
      );
    }
    return Promise.resolve(copieRepli(texte));
  }
  function copieRepli(texte) {
    var zone = document.createElement("textarea");
    zone.value = texte;
    zone.setAttribute("readonly", "");
    zone.style.position = "fixed";
    zone.style.opacity = "0";
    document.body.appendChild(zone);
    zone.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    zone.remove();
    return ok;
  }

  /* Menus (role="menu") : un menu ARIA promet la navigation aux fleches.
     Le menu de compte l'annoncait sans la fournir -- seule la touche Tab
     le parcourait. Haut/Bas bouclent, Debut/Fin sautent aux extremites. */
  document.addEventListener("keydown", function (e) {
    var menu = e.target && e.target.closest && e.target.closest('[role="menu"]');
    if (!menu) return;
    var items = Array.prototype.slice.call(
      menu.querySelectorAll('[role="menuitem"]')
    ).filter(function (el) { return el.offsetParent !== null; });
    if (!items.length) return;
    var i = items.indexOf(document.activeElement);
    var cible = null;
    if (e.key === "ArrowDown") cible = items[(i + 1) % items.length];
    else if (e.key === "ArrowUp") cible = items[(i - 1 + items.length) % items.length];
    else if (e.key === "Home") cible = items[0];
    else if (e.key === "End") cible = items[items.length - 1];
    if (cible) { e.preventDefault(); cible.focus(); }
  });

  window.qsToast = qsToast;
  window.qsConfirmer = qsConfirmer;
  window.qsCopier = qsCopier;
})();
