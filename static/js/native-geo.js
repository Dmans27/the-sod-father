/*
 * Inside the iOS app, location comes from iOS itself (the Capacitor Geolocation plugin) instead of the
 * web view. That means one native "Allow LocalLane to use your location?" prompt -- no extra website
 * prompt naming the-sod-father.onrender.com -- and steadier GPS while walking.
 *
 * It swaps in a navigator.geolocation that behaves like the browser's, so walk-planner.js, pulses.js,
 * citybar.js and the welcome screen need no changes. Loaded in <head> without defer so it's in place
 * before any of them run. On the website (no Capacitor) it does nothing.
 */
(function () {
  'use strict';
  var C = window.Capacitor, G;
  try {
    if (!(C && C.isNativePlatform && C.isNativePlatform())) return;
    G = C.Plugins && C.Plugins.Geolocation;
  } catch (e) { return; }
  if (!G) return;

  function num(v) { return (v === undefined || v === null || isNaN(v)) ? null : Number(v); }
  function toPos(p) {
    var c = (p && p.coords) || {};
    var h = num(c.heading);
    return {
      coords: {
        latitude: c.latitude, longitude: c.longitude, accuracy: num(c.accuracy),
        altitude: num(c.altitude), altitudeAccuracy: num(c.altitudeAccuracy),
        heading: (h === null || h < 0) ? null : h, speed: num(c.speed)
      },
      timestamp: (p && p.timestamp) || Date.now()
    };
  }
  function toErr(e) {
    var m = String((e && (e.message || e.errorMessage)) || e || 'Location unavailable');
    var code = /denied|permission|not authori[sz]ed|restricted|disabled/i.test(m) ? 1 : (/time ?out/i.test(m) ? 3 : 2);
    return { code: code, message: m, PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 };
  }
  function opts(o) {
    o = o || {};
    return { enableHighAccuracy: !!o.enableHighAccuracy, timeout: o.timeout > 0 ? o.timeout : 20000,
             maximumAge: o.maximumAge > 0 ? o.maximumAge : 0 };
  }

  var watches = {}, next = 0;
  var geo = {
    getCurrentPosition: function (ok, bad, o) {
      G.getCurrentPosition(opts(o)).then(function (p) { if (ok) ok(toPos(p)); },
                                         function (e) { if (bad) bad(toErr(e)); });
    },
    watchPosition: function (ok, bad, o) {
      var id = ++next;
      watches[id] = '';
      Promise.resolve(G.watchPosition(opts(o), function (p, e) {
        if (!(id in watches)) return;
        if (e) { if (bad) bad(toErr(e)); return; }
        if (p && ok) ok(toPos(p));
      })).then(function (cid) {
        if (id in watches) watches[id] = cid;
        else if (cid) G.clearWatch({ id: cid });   // cleared before iOS answered
      }, function (e) { if (bad && (id in watches)) bad(toErr(e)); });
      return id;
    },
    clearWatch: function (id) {
      if (!(id in watches)) return;
      var cid = watches[id];
      delete watches[id];
      if (cid) G.clearWatch({ id: cid });
    }
  };
  try { Object.defineProperty(navigator, 'geolocation', { value: geo, configurable: true }); }
  catch (e) { try { navigator.geolocation = geo; } catch (e2) {} }
  window.LocalLaneNativeGeo = true;
})();
