// ==UserScript==
// @name         DGX Dashboard — Local models card
// @namespace    github.com/paraporoco/dgx-spark-model-card
// @version      2.2.0
// @description  Adds a model-serving card to the NVIDIA DGX Dashboard. Touches no NVIDIA file; all logic is served by the dgx-model-card sidecar on 127.0.0.1:8110.
// @homepageURL  https://github.com/paraporoco/dgx-spark-model-card
// @match        http://localhost:11000/*
// @match        http://127.0.0.1:11000/*
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * Install
 *   1. Install Violentmonkey or Tampermonkey in whichever browser you open the
 *      dashboard with.
 *   2. New script, paste this in, save.
 *   3. Open http://localhost:11000 and sign in as usual.
 *
 * Where the logic lives
 *   This script does one thing: it injects a <script> tag pointing at the
 *   sidecar's /card.js. Card markup, polling and controls all live there, so
 *   the card is changed by editing card.js on the Spark — no userscript edit,
 *   no browser round-trip.
 *
 * Remove it
 *   Toggle the script off. Nothing on disk changes. To drop the card without
 *   touching the extension, run in the console:
 *       window.__dgxModelCard.destroy()
 *
 * Remote use
 *   The sidecar binds 127.0.0.1 only. Browsing the dashboard from another
 *   machine needs BOTH ports forwarded:
 *       ssh -L 11000:127.0.0.1:11000 -L 8110:127.0.0.1:8110 you@spark
 *   On a DGX Spark reached through NVIDIA Sync, register port 8110 in Sync's
 *   own app list instead (nvsync config write <alias>) and reconnect — see the
 *   README.
 *
 * If the sidecar runs on a non-default port, change SIDECAR below and add a
 * matching @match / @connect line.
 */

(function () {
  "use strict";

  var SIDECAR = "http://127.0.0.1:8110";
  var TAG_ID = "dgx-model-card-loader";

  function inject() {
    if (document.getElementById(TAG_ID)) return;
    var s = document.createElement("script");
    s.id = TAG_ID;
    s.src = SIDECAR + "/card.js?v=" + Date.now();
    s.async = true;
    s.onerror = function () {
      console.warn("[dgx-model-card] sidecar unreachable at " + SIDECAR +
                   " — is dgx-model-card.service running, and is the port forwarded?");
    };
    (document.head || document.documentElement).appendChild(s);
  }

  // The dashboard is a SPA behind a sign-in modal: the card grid does not exist
  // at document-idle. card.js polls for the grid itself, so injecting once is
  // enough — but re-inject if a hard navigation drops the tag.
  inject();

  var reinject = new MutationObserver(function () {
    if (!document.getElementById(TAG_ID)) inject();
  });
  reinject.observe(document.documentElement, { childList: true, subtree: false });
})();
