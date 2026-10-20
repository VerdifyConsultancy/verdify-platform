import { QuartzComponentConstructor } from "./types"

// Public sitekey only. The matching secret is owned by central KSOPS.
export default (() => {
  function ContactVerification() { return null }
  ContactVerification.afterDOMLoaded = `
(function () {
  var sitekey = "0x4AAAAAAFeJgBaaUz7WKksT";
  function setup() {
    var form = document.getElementById("verdify-contact-form");
    if (!form || form.dataset.verificationReady) return;
    if (new URL(form.action, location.href).href !== "https://api.verdify.ai/api/v1/public/contact") return;
    form.dataset.verificationReady = "true";
    var submit = form.querySelector('button[type="submit"]');
    var status = document.getElementById("verdify-contact-status");
    var slot = document.createElement("div");
    slot.setAttribute("aria-label", "Contact verification");
    form.insertBefore(slot, submit);
    var verified = false;
    var widget;
    function pending() { verified = false; if (submit) submit.disabled = true; }
    pending();
    function guard(event) {
      if (!verified) {
        event.preventDefault(); event.stopImmediatePropagation();
        if (status) status.textContent = "Complete the verification before sending.";
      }
    }
    form.addEventListener("submit", guard, true);
    function render() {
      if (!form.isConnected || widget !== undefined) return;
      widget = window.turnstile.render(slot, {
        sitekey: sitekey, action: "contact", "response-field-name": "turnstile_token",
        callback: function () { verified = true; if (submit) submit.disabled = false; },
        "expired-callback": pending,
        "error-callback": function () {
          pending();
          if (status) status.textContent = "Verification could not load. Please reload and try again.";
        }
      });
    }
    if (window.turnstile) render();
    else {
      var script = document.getElementById("verdify-turnstile-script");
      if (!script) {
        script = document.createElement("script");
        script.id = "verdify-turnstile-script";
        script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
        script.async = true; script.defer = true;
        document.head.appendChild(script);
      }
      script.addEventListener("load", render, { once: true });
    }
    form.addEventListener("reset", function () {
      pending();
      queueMicrotask(function () {
        if (widget !== undefined && form.isConnected) window.turnstile.reset(widget);
      });
    });
    window.addCleanup(function () {
      form.removeEventListener("submit", guard, true);
      if (widget !== undefined && window.turnstile) window.turnstile.remove(widget);
    });
  }
  document.addEventListener("nav", setup);
  if (document.readyState !== "loading") setup();
  else document.addEventListener("DOMContentLoaded", setup, { once: true });
})();
`
  return ContactVerification
}) satisfies QuartzComponentConstructor
