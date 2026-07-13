/*
 * recipe_stream.js — Chantier G4-b-3d
 *
 * Ecoute les events SSE `recipe_done` (yield par
 * agent/recipe_executor_mute.stream_recipe_execution) et affiche un CTA
 * bas d'ecran « Publier ce livrable ». Au clic : POST /api/livrable/publish
 * (bridge hub -> publish_assembly/component + notify agent journal).
 *
 * Zero dependance externe (vanilla ES2015+). Le desk peut consommer :
 *   - via EventSource dedie (fetch SSE + parse manuel)
 *   - via handler exporte `window.recipeStreamHandlers.onRecipeDone(data)`
 *     appele par un autre code (chat qui parse deja le stream SSE).
 */

(function () {
  "use strict";

  var CTA_CLASS = "rct-publish-cta";
  var TOAST_CLASS = "rct-publish-toast";
  var currentLivrable = null;

  function ensureStyles() {
    if (document.getElementById("rct-publish-styles")) return;
    var css =
      "." + CTA_CLASS + "{" +
      "position:fixed;bottom:24px;right:24px;z-index:9999;" +
      "padding:12px 20px;border:0;border-radius:6px;" +
      "background:#000091;color:#fff;font:600 14px/1.4 system-ui,sans-serif;" +
      "cursor:pointer;box-shadow:0 4px 12px rgba(0,0,0,.18);" +
      "max-width:400px;text-align:left;}" +
      "." + CTA_CLASS + ":hover{background:#1212a3;}" +
      "." + CTA_CLASS + "[disabled]{opacity:.6;cursor:wait;}" +
      "." + TOAST_CLASS + "{" +
      "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);" +
      "z-index:9999;padding:12px 20px;border-radius:4px;" +
      "background:#18753c;color:#fff;font:14px/1.4 system-ui,sans-serif;" +
      "max-width:80%;box-shadow:0 4px 12px rgba(0,0,0,.18);}" +
      "." + TOAST_CLASS + ".error{background:#ce0500;}" +
      "." + TOAST_CLASS + " a{color:#fff;text-decoration:underline;}";
    var style = document.createElement("style");
    style.id = "rct-publish-styles";
    style.textContent = css;
    document.head.appendChild(style);
  }

  function onRecipeDone(data) {
    if (!data || typeof data !== "object") return;
    var output = data.output || {};
    var provenance = output.provenance || {};
    var publishHint = data.publish_hint || {};
    currentLivrable = {
      livrable_id: provenance.livrable_id || output.livrable_id || null,
      publish_hint: publishHint,
      session_id: provenance.session_id || null,
    };
    var title =
      (output.scene_manifest && output.scene_manifest.title) ||
      output.title ||
      "livrable";
    // Sans livrable_id, le POST cote hub echouera (validation Pydantic) ;
    // on affiche quand meme le bouton (l'user voit un toast d'erreur clair
    // au lieu d'un CTA silencieusement inactif) mais on log.
    if (!currentLivrable.livrable_id) {
      console.warn(
        "[recipe_stream] recipe_done sans livrable_id — bouton publier " +
          "affiche mais POST echouera (verifier journal_livrable cote agent)"
      );
    }
    showPublishButton(title);
  }

  function showPublishButton(title) {
    ensureStyles();
    var existing = document.querySelector("." + CTA_CLASS);
    if (existing) existing.remove();
    var btn = document.createElement("button");
    btn.className = CTA_CLASS;
    btn.type = "button";
    btn.setAttribute("aria-label", "Publier le livrable " + title);
    btn.textContent = "Publier « " + title + " »";
    btn.addEventListener("click", publishLivrable);
    document.body.appendChild(btn);
  }

  async function publishLivrable() {
    if (!currentLivrable) return;
    var btn = document.querySelector("." + CTA_CLASS);
    if (btn) {
      btn.setAttribute("disabled", "disabled");
      btn.textContent = "Publication en cours…";
    }
    try {
      var r = await fetch("/api/livrable/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(currentLivrable),
      });
      var body;
      try {
        body = await r.json();
      } catch (_e) {
        body = { detail: "reponse non-JSON" };
      }
      if (r.ok && body.published_url) {
        showToast(
          "Publie : <a href=\"" +
            escapeAttr(body.published_url) +
            "\" target=\"_blank\" rel=\"noopener\">" +
            escapeHtml(body.published_url) +
            "</a>",
          false
        );
        if (btn) btn.remove();
        currentLivrable = null;
      } else {
        var detail =
          (body && (body.detail || body.error)) ||
          "publication echouee (HTTP " + r.status + ")";
        showToast("Erreur : " + escapeHtml(String(detail)), true);
        if (btn) {
          btn.removeAttribute("disabled");
          btn.textContent = "Reessayer la publication";
        }
      }
    } catch (exc) {
      showToast("Erreur reseau : " + escapeHtml(String(exc)), true);
      if (btn) {
        btn.removeAttribute("disabled");
        btn.textContent = "Reessayer la publication";
      }
    }
  }

  function showToast(html, isError) {
    ensureStyles();
    var t = document.createElement("div");
    t.className = TOAST_CLASS + (isError ? " error" : "");
    t.setAttribute("role", isError ? "alert" : "status");
    t.innerHTML = html;
    document.body.appendChild(t);
    setTimeout(function () {
      t.remove();
    }, 8000);
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  // Handler public pour les autres bouts de code (chat SSE parser deja en
  // place dans le desk) : ils peuvent appeler
  // window.recipeStreamHandlers.onRecipeDone(payloadJSON).
  window.recipeStreamHandlers = window.recipeStreamHandlers || {};
  window.recipeStreamHandlers.onRecipeDone = onRecipeDone;
  // Expose aussi pour retrofit et debug.
  window.recipeStreamHandlers._currentLivrable = function () {
    return currentLivrable;
  };
})();
