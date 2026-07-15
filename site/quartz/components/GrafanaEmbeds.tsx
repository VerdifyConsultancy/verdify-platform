import { QuartzComponentConstructor } from "./types"

// GrafanaEmbeds — progressive-enhancement upgrader for placeholder
// <div class="grafana-embed"> nodes emitted by the GrafanaDefer
// transformer. Every browser gets a stable cached PNG from Grafana's
// `/render/d-solo/...` endpoint by default. Interactive Grafana is an
// explicit action: cross-origin iframes are considerably heavier and their
// load event does not prove that the panel actually rendered in Chrome or
// Safari.
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
  background: color-mix(in srgb, var(--lightgray, #17352D) 48%, #071512);
  border-top-color: color-mix(in srgb, var(--secondary, #7DE2B8) 24%, transparent);
}
`

  GrafanaEmbeds.afterDOMLoaded = `
(function () {
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

  var graphRefreshSequence = 0;
  function refreshedRenderUrl(url, cssWidth, cssHeight) {
    try {
      var u = new URL(sizedRenderUrl(url, cssWidth, cssHeight), window.location.href);
      graphRefreshSequence++;
      // Safari can retain a failed image request for an identical URL even
      // when the response says no-store. Give browser retries and periodic
      // refreshes a unique identity. The render proxy strips the _qts marker
      // from its shared cache key, so this does not fragment the server cache.
      // Keep _qts first: the proxy removes that prefix and retains the exact
      // stable query string produced by sizedRenderUrl.
      var stableQuery = u.search ? u.search.slice(1) : '';
      var marker = String(Date.now()) + '-' + String(graphRefreshSequence);
      u.search = '_qts=' + encodeURIComponent(marker) + (stableQuery ? '&' + stableQuery : '');
      return u.toString();
    } catch (_) {
      return sizedRenderUrl(url, cssWidth, cssHeight);
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
    var timers = [];
    var disposed = false;
    var EMPTY_IMAGE_SRC = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
    var cameraSnapshots = Array.from(document.querySelectorAll('img.camera-snapshot[data-camera-src]'));
    var cameraStates = [];

    function restoreCameraSources() {
      cameraSnapshots.forEach(function (img) {
        var base = img.getAttribute('data-camera-autoload-src');
        if (base) img.setAttribute('data-camera-src', base);
        img.removeAttribute('data-camera-autoload-src');
      });
    }

    function cameraRefreshUrl(base) {
      try {
        var u = new URL(base, window.location.href);
        u.searchParams.set('_', String(Date.now()));
        return u.toString();
      } catch (_) {
        return base + (base.indexOf('?') === -1 ? '?' : '&') + '_=' + Date.now();
      }
    }

    function refreshCameraAfterLoad(img, base, state) {
      if (disposed || state.disposed || state.inFlight || !document.body.contains(img)) return;
      state.inFlight = true;
      state.sequence++;
      var sequence = state.sequence;
      var probe = new Image();
      state.probe = probe;
      probe.decoding = 'async';

      function releaseProbe() {
        if (state.sequence !== sequence) return;
        state.inFlight = false;
        state.probe = null;
      }

      function commitProbe() {
        if (
          !disposed
          && !state.disposed
          && state.sequence === sequence
          && document.body.contains(img)
          && probe.naturalWidth > 0
        ) {
          img.src = probe.src;
        }
        releaseProbe();
      }

      probe.addEventListener('load', function () {
        // Decode before swapping so Safari and Chrome keep the visible
        // last-known-good frame until its replacement is display-ready.
        if (typeof probe.decode === 'function') {
          probe.decode().then(commitProbe, releaseProbe);
        } else {
          commitProbe();
        }
      }, { once: true });
      probe.addEventListener('error', releaseProbe, { once: true });
      probe.src = cameraRefreshUrl(base);
    }

    function cleanupCameras() {
      cameraStates.forEach(function (state) {
        state.disposed = true;
        state.sequence++;
        state.inFlight = false;
        if (state.probe) {
          state.probe.src = EMPTY_IMAGE_SRC;
          state.probe = null;
        }
      });
      restoreCameraSources();
    }

    // The public homepage has only two camera snapshots. Ask every browser to
    // fetch them immediately instead of relying on divergent native lazy-load
    // heuristics, especially Safari's treatment of below-the-fold images. The
    // legacy static refresher sees no data-camera-src after this enhancement;
    // replacements are preloaded here and swapped atomically instead.
    cameraSnapshots.forEach(function (img) {
      var cameraSrc = img.getAttribute('data-camera-src');
      if (!cameraSrc) return;
      var state = { disposed: false, inFlight: false, sequence: 0, probe: null, initialRetryScheduled: false };
      cameraStates.push(state);
      img.loading = 'eager';
      img.decoding = 'async';
      img.setAttribute('data-camera-autoload-src', cameraSrc);
      img.removeAttribute('data-camera-src');
      if (!img.getAttribute('src')) img.src = cameraSrc;

      function scheduleInitialCameraRetry() {
        if (state.initialRetryScheduled || state.disposed || disposed) return;
        state.initialRetryScheduled = true;
        var retryTimer = window.setTimeout(function () {
          state.initialRetryScheduled = false;
          refreshCameraAfterLoad(img, cameraSrc, state);
        }, 5000);
        timers.push(retryTimer);
      }

      img.addEventListener('error', scheduleInitialCameraRetry, { once: true });
      // afterDOMLoaded can run after a parser-started image has already failed.
      if (img.complete && img.naturalWidth === 0) scheduleInitialCameraRetry();
      timers.push(window.setInterval(function () {
        refreshCameraAfterLoad(img, cameraSrc, state);
      }, 30000));
    });

    var embeds = Array.from(document.querySelectorAll('.grafana-embed:not([data-grafana-enhanced])[data-iframe-src], .grafana-embed:not([data-grafana-enhanced])[data-image-src]'));
    if (!embeds.length) {
      if (window.addCleanup) {
        window.addCleanup(function () {
          disposed = true;
          for (var cameraTimerIndex = 0; cameraTimerIndex < timers.length; cameraTimerIndex++) window.clearInterval(timers[cameraTimerIndex]);
          cleanupCameras();
        });
      }
      return;
    }

    var loaded = new WeakMap();
    var elementTimers = new WeakMap();
    var renderGenerations = new WeakMap();
    var observer = null;

    // Client-side concurrency cap on PNG fetches. The grafana-image-
    // renderer + nginx cache can comfortably keep ~2 renders in
    // flight at once; bursting all 8 panels at once during initial
    // page load saturates the host and causes 20s+ per-image times.
    // Queue surplus requests; pop on completion.
    var IMG_MAX_INFLIGHT = 2;
    var imgInflight = 0;
    var imgQueue = [];
    var queuedImages = new WeakSet();
    var activeImageLoads = new Set();

    function isCurrentRender(el, generation) {
      return !disposed && document.body.contains(el) && renderGenerations.get(el) === generation;
    }

    function beginRender(el) {
      var generation = (renderGenerations.get(el) || 0) + 1;
      renderGenerations.set(el, generation);
      activeImageLoads.forEach(function (job) {
        if (job.el === el && job.generation !== generation) job.cancel(false);
      });
      imgQueue = imgQueue.filter(function (job) {
        if (job.el !== el || job.generation === generation) return true;
        queuedImages.delete(job.img);
        return false;
      });
      return generation;
    }

    function dequeueImg() {
      while (imgInflight < IMG_MAX_INFLIGHT && imgQueue.length) {
        var job = imgQueue.shift();
        if (!job.isCurrent()) {
          queuedImages.delete(job.img);
          continue;
        }
        imgInflight++;
        loadImg(job);
      }
    }

    function loadImg(job) {
      var done = false;
      var watchdog = null;
      var onLoad = function () { settle(true, true); };
      var onError = function () { settle(false, true); };

      function settle(success, notify) {
        if (done) return;
        done = true;
        job.img.removeEventListener('load', onLoad);
        job.img.removeEventListener('error', onError);
        if (watchdog) window.clearTimeout(watchdog);
        activeImageLoads.delete(job);
        queuedImages.delete(job.img);
        imgInflight = Math.max(0, imgInflight - 1);
        if (notify && job.isCurrent()) job.onResult(success);
        dequeueImg();
      }

      job.cancel = function (notify) {
        if (done) return;
        job.img.removeEventListener('load', onLoad);
        job.img.removeEventListener('error', onError);
        // Replacing src aborts a hung/obsolete browser request before its queue
        // slot is released, preserving the two-request cap.
        job.img.src = EMPTY_IMAGE_SRC;
        settle(false, notify);
      };
      activeImageLoads.add(job);
      job.img.addEventListener('load', onLoad, { once: true });
      job.img.addEventListener('error', onError, { once: true });
      watchdog = window.setTimeout(function () { job.cancel(true); }, 35000);
      job.img.src = job.src;
    }

    function enqueueImg(img, src, el, generation, onResult) {
      if (!isCurrentRender(el, generation) || queuedImages.has(img)) return;
      queuedImages.add(img);
      imgQueue.push({
        img: img,
        src: src,
        el: el,
        generation: generation,
        onResult: onResult,
        isCurrent: function () { return isCurrentRender(el, generation); },
        cancel: function () {},
      });
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
      var generation = beginRender(el);
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
      var unavailable = null;

      function current() {
        return isCurrentRender(el, generation);
      }

      function requestImage(src) {
        enqueueImg(img, src, el, generation, handleImageResult);
      }

      function handleImageResult(success) {
        if (!current()) return;
        if (success && img.naturalWidth > 0) {
          attempts = 0;
          // The periodic refresh remains active after a cold-render failure.
          // Restore a recovered image without requiring navigation.
          if (unavailable && unavailable.parentNode) {
            unavailable.parentNode.replaceChild(img, unavailable);
            unavailable = null;
          }
          return;
        }

        attempts++;
        if (attempts <= maxAttempts) {
          var backoff = 3000 * Math.pow(2, attempts - 1); // 3s, 6s, 12s
          trackTimer(el, window.setTimeout(function () {
            if (current()) {
              requestImage(refreshedRenderUrl(themedUrl(imgSrc), el.clientWidth || cssWidth, height));
            }
          }, backoff));
        } else if (!unavailable) {
          var ph = document.createElement('div');
          ph.className = 'grafana-embed__placeholder';
          ph.style.height = height + 'px';
          ph.textContent = 'Image render unavailable. Tap below to open live panel.';
          if (current() && img.parentNode) {
            img.parentNode.replaceChild(ph, img);
            unavailable = ph;
          }
        }
      }

      el.appendChild(img);
      requestImage(sizedSrc);

      appendActions(el, liveSrc, iframeSrc ? function () {
        if (current()) renderIframe(el, iframeSrc, liveSrc, title, height, imgSrc, refreshMs);
      } : null);

      if (refreshMs > 0) {
        var t = window.setInterval(function () {
          if (current()) {
            requestImage(refreshedRenderUrl(themedUrl(imgSrc), el.clientWidth || cssWidth, height));
          }
        }, refreshMs);
        trackTimer(el, t);
      }
    }

    function renderIframe(el, iframeSrc, liveSrc, title, height, imageSrc, refreshMs) {
      clearElementTimers(el);
      var generation = beginRender(el);
      el.innerHTML = '';
      el.style.minHeight = height + 'px';

      var f = document.createElement('iframe');
      f.className = 'grafana-embed__frame';
      f.title = title;
      f.height = String(height);
      f.style.height = height + 'px';
      f.frameBorder = '0';
      // Interactive panels are created only after an explicit user action, so
      // there is no reason to defer that requested navigation again.
      f.loading = 'eager';
      f.referrerPolicy = 'no-referrer-when-downgrade';

      var settled = false;
      var fallbackTimer = null;
      function current() {
        return isCurrentRender(el, generation);
      }
      function fallBackToStatic() {
        if (settled || !imageSrc || !current()) return;
        settled = true;
        if (fallbackTimer) window.clearTimeout(fallbackTimer);
        renderImage(el, imageSrc, iframeSrc, liveSrc, title, height, refreshMs);
      }

      if (imageSrc) {
        fallbackTimer = window.setTimeout(fallBackToStatic, 12000);
        trackTimer(el, fallbackTimer);
        f.addEventListener('load', function () {
          if (!current()) return;
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

      if (imageSrc) {
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
      // Start the bounded image queue before a panel reaches the viewport so a
      // normal scroll does not expose an avoidable blank/loading interval.
      }, { rootMargin: '1200px 0px', threshold: 0 });
      embeds.forEach(function (el) { observer.observe(el); });
    } else {
      embeds.forEach(load);
    }

    function reloadForThemeChange() {
      Array.from(document.querySelectorAll('.grafana-embed[data-grafana-enhanced]')).forEach(function (el) {
        // The initial theme event fires before IntersectionObserver has brought
        // distant panels into scope. Do not let it fan out every graph request.
        if (!loaded.has(el)) return;
        loaded.delete(el);
        load(el);
      });
    }
    document.addEventListener('themechange', reloadForThemeChange);

    if (window.addCleanup) {
      window.addCleanup(function () {
        disposed = true;
        if (observer) observer.disconnect();
        for (var i = 0; i < timers.length; i++) window.clearInterval(timers[i]);
        imgQueue.forEach(function (job) { queuedImages.delete(job.img); });
        imgQueue = [];
        activeImageLoads.forEach(function (job) { job.cancel(false); });
        cleanupCameras();
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
