// Job polling and Copy buttons. No inline script is allowed, so pages carry data-* hooks.
(function () {
  "use strict";
  var sheet = document.querySelector('link[rel="stylesheet"]');
  var base = sheet.getAttribute("href").replace(/\/static\/app\.css$/, "");

  document.querySelectorAll("[data-job]").forEach(function (el) {
    var id = el.getAttribute("data-job");
    var failures = 0;
    var timer = setInterval(function () {
      fetch(base + "/jobs/" + encodeURIComponent(id), { headers: { Accept: "application/json" } })
        .then(function (r) {
          if (r.status === 404) { clearInterval(timer); return null; }  // another process's job
          failures = 0;
          return r.ok ? r.json() : null;
        })
        .then(function (job) {
          if (job && (job.state === "done" || job.state === "failed")) {
            clearInterval(timer);
            window.location.reload();
          }
        })
        .catch(function () {
          failures += 1;
          if (failures >= 5) { clearInterval(timer); }
        });
    }, 3000);
  });

  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var id = button.getAttribute("data-copy");
      var target = document.getElementById(id);
      var note = document.querySelector('[data-copy-note="' + id + '"]');
      var say = function (text) { if (note) { note.textContent = text; } };
      var select = function () {
        var range = document.createRange();
        range.selectNodeContents(target);
        var selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        say("selected — copy with your phone's menu");
      };
      if (window.isSecureContext && navigator.clipboard) {
        navigator.clipboard.writeText(target.textContent).then(function () { say("copied"); }, select);
        return;
      }
      select();
    });
  });
})();
