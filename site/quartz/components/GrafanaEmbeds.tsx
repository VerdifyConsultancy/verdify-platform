import { QuartzComponentConstructor } from "./types"

// GrafanaEmbeds — progressive-enhancement upgrader for placeholder
// <div class="grafana-embed"> nodes emitted by the GrafanaDefer
// transformer. Desktop browsers get live Grafana iframes by default.
// iOS, narrow touch/mobile devices, and timed-out iframes get stable
// cached PNGs from Grafana's `/render/d-solo/...` endpoint with an
// explicit "Open in Grafana" escape hatch.
//
// The component itself emits no DOM (returns null) — the render is
// done client-side by the afterDOMLoaded script below, which scans
// for `.grafana-embed` placeholder divs and upgrades them in place.
// The placeholder div carries data-iframe-src, data-image-src,
// data-height, data-title.
//
// Lifecycle: re-runs on every Quartz `nav` event so SPA navigations
// re-bind. Timers and IntersectionObserver are tracked by
// window.addCleanup so they don't leak across navigations.

export default (() => {
  function GrafanaEmbeds() {
    return null
  }

  GrafanaEmbeds.css = `
.grafana-embed {
  width: 100%;
  margin: 0.5rem 0;
  border-radius: 0;
  overflow: hidden;
  background: transparent;
  border: 0;
  position: relative;
  box-shadow: none;
}
.grafana-embed__frame,
.grafana-embed__img {
  width: 100%;
  display: block;
  border: 0;
}
.grafana-embed__placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--canopy-green, #0E5A43);
  font-size: 0.85rem;
  min-height: 60px;
}
.grafana-embed__actions {
  padding: 0.4rem 0.6rem;
  font-size: 0.8rem;
  color: var(--gray, #6B7280);
  border-top: 1px solid var(--panel-border, #DCEDE7);
  background: color-mix(in srgb, var(--fog-white, #F4F7F4) 92%, white);
}
.grafana-embed__actions a {
  color: var(--canopy-green, #0E5A43);
  text-decoration: underline;
}
.grafana-embed__actions button {
  appearance: none;
  margin: 0 0.75rem 0 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--canopy-green, #0E5A43);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
:root[saved-theme="dark"] .grafana-embed__actions {
  background: color-mix(in srgb, var(--lightgray, #17352D) 48%, #111217);
  border-top-color: color-mix(in srgb, var(--secondary, #7DE2B8) 24%, transparent);
}
`

  GrafanaEmbeds.afterDOMLoaded = `
(function () {
  function shouldUseStaticImages() {
    if (typeof navigator === 'undefined') return false;
    var ua = navigator.userAgent || '';
    var ios = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    var mobile = /Android|Mobile/.test(ua);
    var coarse = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;
    var narrow = typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 820px)').matches;
    return ios || mobile || (coarse && narrow);
  }

  function sizedRenderUrl(url, cssWidth, cssHeight) {
    try {
      var u = new URL(url, window.location.href);
      var width = Math.max(320, Math.round(cssWidth || 0));
      var height = Math.max(80, Math.round(cssHeight || 0));
      var bucket;
      if (width <= 430) bucket = 800;
      else if (width <= 620) bucket = 1000;
      else if (width <= 860) bucket = 1200;
      else bucket = 1440;
      var assumedWidth = bucket === 800 ? 390 : bucket === 1000 ? 540 : bucket === 1200 ? 740 : 1000;
      var renderHeight = Math.max(160, Math.round(height * (bucket / assumedWidth)));
      u.searchParams.set('width', String(bucket));
      u.searchParams.set('height', String(renderHeight));
      return u.toString();
    } catch (_) {
      return url;
    }
  }

  function currentTheme() {
    return document.documentElement.getAttribute('saved-theme') === 'dark' ? 'dark' : 'light';
  }

  function themedUrl(url) {
    if (!url) return url;
    try {
      var u = new URL(url, window.location.href);
      u.searchParams.set('theme', currentTheme());
      return u.toString();
    } catch (_) {
      return url;
    }
  }

  function appendActions(el, liveSrc, loadInteractive) {
    if (!liveSrc) return;
    var actions = document.createElement('div');
    actions.className = 'grafana-embed__actions';
    if (loadInteractive) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = 'Load interactive panel';
      b.addEventListener('click', function () {
        loadInteractive();
      });
      actions.appendChild(b);
    }
    var a = document.createElement('a');
    a.href = themedUrl(liveSrc);
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = 'Open in Grafana';
    actions.appendChild(a);
    el.appendChild(actions);
  }

  function setup() {
    var embeds = Array.from(document.querySelectorAll('.grafana-embed:not([data-grafana-enhanced])[data-iframe-src], .grafana-embed:not([data-grafana-enhanced])[data-image-src]'));
    if (!embeds.length) return;

    var staticImages = shouldUseStaticImages();
    var loaded = new WeakMap();
    var timers = [];
    var elementTimers = new WeakMap();
    var observer = null;

    // Client-side concurrency cap on PNG fetches. The grafana-image-
    // renderer + nginx cache can comfortably keep ~2 renders in
    // flight at once; bursting all 8 panels at once during initial
    // page load saturates the host and causes 20s+ per-image times.
    // Queue surplus requests; pop on completion.
    var IMG_MAX_INFLIGHT = 2;
    var imgInflight = 0;
    var imgQueue = [];

    function dequeueImg() {
      while (imgInflight < IMG_MAX_INFLIGHT && imgQueue.length) {
        var fn = imgQueue.shift();
        imgInflight++;
        fn();
      }
    }

    function loadImg(img, src) {
      var done = false;
      var settle = function () {
        if (done) return;
        done = true;
        imgInflight = Math.max(0, imgInflight - 1);
        dequeueImg();
      };
      img.addEventListener('load', settle, { once: true });
      img.addEventListener('error', settle, { once: true });
      // Belt-and-suspenders: if neither fires within 35s (renders
      // queue can occasionally take that long), release the slot
      // anyway so we don't deadlock the queue.
      window.setTimeout(settle, 35000);
      img.src = src;
    }

    function enqueueImg(img, src) {
      imgQueue.push(function () { loadImg(img, src); });
      dequeueImg();
    }

    function trackTimer(el, timer) {
      timers.push(timer);
      var elementTimerList = elementTimers.get(el) || [];
      elementTimerList.push(timer);
      elementTimers.set(el, elementTimerList);
      return timer;
    }

    function clearElementTimers(el) {
      var elementTimerList = elementTimers.get(el) || [];
      for (var i = 0; i < elementTimerList.length; i++) window.clearInterval(elementTimerList[i]);
      elementTimers.delete(el);
    }

    function renderImage(el, imgSrc, iframeSrc, liveSrc, title, height, refreshMs) {
      clearElementTimers(el);
      el.innerHTML = '';
      el.style.minHeight = height + 'px';

      var img = document.createElement('img');
      img.className = 'grafana-embed__img';
      img.alt = title;
      img.loading = 'eager';
      img.decoding = 'async';
      img.width = el.clientWidth || Math.round(el.getBoundingClientRect().width) || window.innerWidth || 800;
      img.height = height;
      img.style.height = height + 'px';
      var cssWidth = img.width;
      var sizedSrc = sizedRenderUrl(themedUrl(imgSrc), cssWidth, height);

      // The renderer can still 429/timeout under load even with the
      // nginx cache and concurrency cap. Retry up to 3 times with
      // exponential backoff (3s, 6s, 12s) before falling back to a
      // placeholder with the "Open live panel" escape hatch. The
      // retries themselves go through enqueueImg so they respect
      // the IMG_MAX_INFLIGHT cap and don't dogpile.
      var attempts = 0;
      var maxAttempts = 3;
      img.addEventListener('error', function () {
        attempts++;
        if (attempts <= maxAttempts) {
          var backoff = 3000 * Math.pow(2, attempts - 1); // 3s, 6s, 12s
          window.setTimeout(function () {
            if (document.body.contains(el)) {
              enqueueImg(img, sizedRenderUrl(themedUrl(imgSrc), el.clientWidth || cssWidth, height));
            }
          }, backoff);
        } else {
          var ph = document.createElement('div');
          ph.className = 'grafana-embed__placeholder';
          ph.style.height = height + 'px';
          ph.textContent = 'Image render unavailable. Tap below to open live panel.';
          if (img.parentNode) img.parentNode.replaceChild(ph, img);
        }
      });
      el.appendChild(img);
      enqueueImg(img, sizedSrc);

      appendActions(el, liveSrc, iframeSrc ? function () {
        renderIframe(el, iframeSrc, liveSrc, title, height, imgSrc, refreshMs);
      } : null);

      if (refreshMs > 0) {
        var t = window.setInterval(function () {
          if (document.body.contains(el)) {
            enqueueImg(img, sizedRenderUrl(themedUrl(imgSrc), el.clientWidth || cssWidth, height));
          }
        }, refreshMs);
        trackTimer(el, t);
      }
    }

    function renderIframe(el, iframeSrc, liveSrc, title, height, imageSrc, refreshMs) {
      clearElementTimers(el);
      el.innerHTML = '';
      el.style.minHeight = height + 'px';

      var f = document.createElement('iframe');
      f.className = 'grafana-embed__frame';
      f.title = title;
      f.height = String(height);
      f.style.height = height + 'px';
      f.frameBorder = '0';
      f.loading = 'lazy';
      f.referrerPolicy = 'no-referrer-when-downgrade';

      var settled = false;
      var fallbackTimer = null;
      function fallBackToStatic() {
        if (settled || !imageSrc || !document.body.contains(el)) return;
        settled = true;
        if (fallbackTimer) window.clearTimeout(fallbackTimer);
        renderImage(el, imageSrc, iframeSrc, liveSrc, title, height, refreshMs);
      }

      if (imageSrc) {
        fallbackTimer = window.setTimeout(fallBackToStatic, 12000);
        trackTimer(el, fallbackTimer);
        f.addEventListener('load', function () {
          settled = true;
          if (fallbackTimer) window.clearTimeout(fallbackTimer);
        }, { once: true });
        f.addEventListener('error', fallBackToStatic, { once: true });
      }

      f.src = themedUrl(iframeSrc);
      el.appendChild(f);

      appendActions(el, liveSrc, null);
    }

    function load(el) {
      var theme = currentTheme();
      if (loaded.get(el) === theme) return;
      loaded.set(el, theme);

      var iframeSrc = el.getAttribute('data-iframe-src') || '';
      var imageSrc = el.getAttribute('data-image-src') || '';
      var liveSrc = el.getAttribute('data-live-src') || iframeSrc;
      var title = el.getAttribute('data-title') || 'Grafana panel';
      var height = parseInt(el.getAttribute('data-height') || '300', 10);
      var refreshMs = parseInt(el.getAttribute('data-refresh-ms') || '60000', 10);

      if (!iframeSrc && !imageSrc) {
        el.innerHTML = '<div class="grafana-embed__placeholder">Missing Grafana source.</div>';
        return;
      }

      if (staticImages && imageSrc) {
        renderImage(el, imageSrc, iframeSrc, liveSrc, title, height, refreshMs);
      } else if (iframeSrc) {
        renderIframe(el, iframeSrc, liveSrc, title, height, imageSrc, refreshMs);
      } else {
        renderImage(el, imageSrc, iframeSrc, liveSrc, title, height, refreshMs);
      }
    }

    // Pre-fill placeholders with a "Loading…" state so layout is
    // stable before each panel is upgraded.
    embeds.forEach(function (el) {
      el.setAttribute('data-grafana-enhanced', 'true');
      var height = parseInt(el.getAttribute('data-height') || '300', 10);
      el.style.minHeight = height + 'px';
      el.innerHTML = '<div class="grafana-embed__placeholder" style="height:' + height + 'px">Loading...</div>';
    });

    if ('IntersectionObserver' in window) {
      observer = new IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].isIntersecting) {
            load(entries[i].target);
            observer.unobserve(entries[i].target);
          }
        }
      }, { rootMargin: '300px 0px', threshold: 0 });
      embeds.forEach(function (el) { observer.observe(el); });
    } else {
      embeds.forEach(load);
    }

    function reloadForThemeChange() {
      Array.from(document.querySelectorAll('.grafana-embed[data-grafana-enhanced]')).forEach(function (el) {
        loaded.delete(el);
        load(el);
      });
    }
    document.addEventListener('themechange', reloadForThemeChange);

    if (window.addCleanup) {
      window.addCleanup(function () {
        if (observer) observer.disconnect();
        for (var i = 0; i < timers.length; i++) window.clearInterval(timers[i]);
        document.removeEventListener('themechange', reloadForThemeChange);
      });
    }
  }

  document.addEventListener('nav', setup);
  if (document.readyState !== 'loading') setup();
})();
`

  return GrafanaEmbeds
}) satisfies QuartzComponentConstructor
