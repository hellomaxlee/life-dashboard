"""GET /pixoo: the display as a Pixoo-64 on a desk, running the rotation live in the browser.

Rendering stays server-side. The page fetches the rotation as JSON (`/pixoo/rotation.json`:
screens in order, one PNG URL per frame with its duration, the dwell rule, the as-of time)
and each frame as raw 64x64 PNG bytes (`/pixoo/frame/<name>/<index>.png`), then draws the
pixels onto a canvas as round LEDs with inline JavaScript. The browser never computes a frame.

Gamma and brightness are client-side lookups through tables the server computes with
`app.render.gamma.led_lut`, so the emulator stays the single truth: brightness scales the PWM
level before the panel curve, exactly as `led_gamma(frame, brightness=b)` would, and a value
that falls below one PWM step goes dark. Toggling either redraws from the cached raw pixels;
nothing is fetched or rendered again. `gamma=1` on the frame endpoint exists for parity with
/preview and so a test can prove the shipped tables equal the emulator pixel for pixel.

The sequence and its holds are `app.render.rotation`'s, the same the device job sends: Week,
Today, one sparkle per earned small win, Books; a clip loops while it is held. No external
scripts, fonts, or assets; everything is inline and the page works on the LAN only.
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.render.adapters.file import png_bytes
from app.render.celebrate import CELEBRATION_ORDER, Celebration, celebrations_for
from app.render.frame import Clip
from app.render.gamma import led_gamma, led_lut
from app.render.rotation import ROTATION_ORDER, WIN_NAMES, render_screen, rotation_sequence
from app.render.view import DayView
from app.timeutil import to_utc_iso, utc_iso_to_local_display
from app.web.nav import NAV_STYLE, nav_html
from app.web.preview import (
    PLACEHOLDER,
    PLACEHOLDER_BANNER,
    PLACEHOLDER_STYLE,
    _resolve,
    fixture_paths,
    resolve_page,
)

router = APIRouter()

SIZES = (256, 512, 768)
DEFAULT_SIZE = 512
BRIGHTNESS_STEPS = tuple(range(10, 101, 10))
DEFAULT_BRIGHTNESS = 100
NAMES = ROTATION_ORDER + CELEBRATION_ORDER


def _clip(view: DayView, now: datetime, name: str) -> tuple[Clip, bool]:
    """(clip, earned) for one screen or celebration; a rotation screen is always earned."""
    if name in ROTATION_ORDER or name in WIN_NAMES:
        return render_screen(name, view, now), True
    found: Celebration = next(c for c in celebrations_for(view) if c.name == name)
    return found.clip, found.earned


def _source(view: DayView, fixture: str | None) -> dict[str, str]:
    return {"fixture": fixture} if fixture is not None else {"date": view.day_local}


def _frames(name: str, clip: Clip, source: dict[str, str]) -> list[dict[str, object]]:
    query = urlencode(source)
    return [
        {"url": f"/pixoo/frame/{name}/{index}.png?{query}", "ms": ms}
        for index, ms in enumerate(clip.durations_ms)
    ]


def luts() -> dict[str, dict[str, list[int]]]:
    """Per brightness step, the 8-bit lookup for the emulated panel and for the raw frame."""
    gamma = {str(b): led_lut(brightness=b / 100) for b in BRIGHTNESS_STEPS}
    raw = {str(b): [int(v * b / 100) for v in range(256)] for b in BRIGHTNESS_STEPS}
    return {"gamma": gamma, "raw": raw}


def rotation_payload(
    view: DayView, now: datetime, fixture: str | None, dwell_s: int
) -> dict[str, object]:
    source = _source(view, fixture)
    screens = [
        {
            "name": name,
            "frames": _frames(name, clip, source),
            "total_ms": clip.total_ms,
            "hold_ms": hold,
        }
        for name, clip, hold in rotation_sequence(view, now, dwell_s)
    ]
    celebrations = []
    for celebration in celebrations_for(view):
        celebrations.append(
            {
                "name": celebration.name,
                "earned": celebration.earned,
                "frames": _frames(celebration.name, celebration.clip, source),
                "total_ms": celebration.clip.total_ms,
            }
        )
    as_of_local = utc_iso_to_local_display(view.as_of_utc, view.home_tz) if view.as_of_utc else None
    return {
        "source": source,
        "day_local": view.day_local,
        "now_utc": to_utc_iso(now),
        "as_of_utc": view.as_of_utc,
        "as_of_local": as_of_local,
        "dwell_ms": dwell_s * 1000,
        "screens": screens,
        "celebrations": celebrations,
        "brightness_steps": list(BRIGHTNESS_STEPS),
        "luts": luts(),
    }


@router.get("/pixoo/rotation.json")
def pixoo_rotation(
    request: Request,
    day: str | None = Query(default=None, alias="date"),
    fixture: str | None = None,
) -> JSONResponse:
    view, now = _resolve(request, day, fixture)
    dwell = request.app.state.settings.device.screen_seconds
    return JSONResponse(rotation_payload(view, now, fixture, dwell))


@router.get("/pixoo/frame/{name}/{index}.png")
def pixoo_frame(
    request: Request,
    name: str,
    index: int,
    day: str | None = Query(default=None, alias="date"),
    fixture: str | None = None,
    gamma: int = 0,
) -> Response:
    if name not in NAMES and name not in WIN_NAMES:
        raise HTTPException(status_code=404, detail="unknown screen")
    if gamma not in (0, 1):
        raise HTTPException(status_code=422, detail="gamma must be 0 or 1")
    view, now = _resolve(request, day, fixture)
    clip, _ = _clip(view, now, name)
    if not 0 <= index < len(clip.frames):
        raise HTTPException(status_code=404, detail="no such frame")
    frame = clip.frames[index]
    return Response(png_bytes(led_gamma(frame) if gamma else frame), media_type="image/png")


@router.get("/pixoo", response_class=HTMLResponse)
def pixoo_page(
    request: Request,
    day: str | None = Query(default=None, alias="date"),
    fixture: str | None = None,
    placeholder: bool = True,
) -> HTMLResponse:
    view, now, fixture, is_placeholder = resolve_page(request, day, fixture, placeholder)
    banner = (
        PLACEHOLDER_BANNER.format(stem=escape(PLACEHOLDER), path="/pixoo") if is_placeholder else ""
    )
    settings = request.app.state.settings
    config = {
        "source": _source(view, fixture),
        "query": urlencode(_source(view, fixture)),
        "day_local": view.day_local,
        "fixtures": list(fixture_paths()),
        "sizes": list(SIZES),
        "default_size": DEFAULT_SIZE,
        "brightness_steps": list(BRIGHTNESS_STEPS),
        "default_brightness": DEFAULT_BRIGHTNESS,
        "dwell_ms": settings.device.screen_seconds * 1000,
    }
    title = fixture if fixture is not None else view.day_local
    options = "".join(
        f"<option value='{escape(stem)}'{' selected' if stem == fixture else ''}>"
        f"{escape(stem)}</option>"
        for stem in fixture_paths()
    )
    config_json = json.dumps(config).replace("</", "<\\/")
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>life-dashboard device</title><style>{_STYLE}{NAV_STYLE}{PLACEHOLDER_STYLE}"
        "</style></head><body>"
        + nav_html("/pixoo")
        + f"<h1>Device: {escape(title)}</h1>"
        + banner
        + _CONTROLS.replace("{options}", options).replace("{day}", escape(view.day_local))
        + _DEVICE
        + _STRIP
        + f"<script>const CONFIG={config_json};{_SCRIPT}</script></body></html>"
    )


_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:0;padding:1.5rem 1rem 3rem;color:#cfcbc4;"
    "background:radial-gradient(ellipse at 50% 20%,#2a2521 0%,#17140f 55%,#0d0b09 100%);"
    "min-height:100vh}a{color:#9cc4ff}h1{font-size:1.1rem;font-weight:600;margin:0 0 1rem}"
    "main{max-width:60rem;margin:0 auto}"
    ".controls{display:flex;flex-wrap:wrap;gap:.6rem 1.2rem;align-items:center;font-size:.8rem;"
    "margin-bottom:1.5rem}.controls label{display:flex;gap:.4rem;align-items:center}"
    "select,input,button{font:inherit;background:#2a2724;color:#e8e3da;border:1px solid #4a443d;"
    "border-radius:4px;padding:.2rem .45rem}button{cursor:pointer}button:hover{background:#3a3631}"
    "input[type=range]{width:7rem;padding:0}"
    ".stage{display:flex;flex-direction:column;align-items:center;gap:1rem}"
    ".device{position:relative;background:linear-gradient(160deg,#34343a 0%,#1a1a1e 45%,"
    "#0e0e10 100%);border-radius:var(--bezel-radius);padding:var(--bezel);"
    "box-shadow:0 1px 0 rgba(255,255,255,.14) inset,0 -2px 0 rgba(0,0,0,.7) inset,"
    "0 40px 70px -20px rgba(0,0,0,.85),0 12px 24px -8px rgba(0,0,0,.6)}"
    ".device::before{content:'';position:absolute;inset:3px;border-radius:calc(var(--bezel-radius)"
    " - 3px);pointer-events:none;background:linear-gradient(170deg,rgba(255,255,255,.05),"
    "rgba(255,255,255,0) 40%)}"
    ".matrix{display:block;background:#000;border-radius:3px;"
    "box-shadow:0 0 0 2px #050505,0 0 18px rgba(0,0,0,.9) inset}"
    ".brand{margin-top:calc(var(--bezel) * .45);text-align:center;font-size:10px;"
    "letter-spacing:.18em;color:#56565e;text-transform:lowercase;line-height:1}"
    ".caption{font-size:.8rem;color:#a8a39b;text-align:center;min-height:2.6em;"
    "font-variant-numeric:tabular-nums}.caption b{color:#e8e3da}"
    ".strip{display:flex;gap:1.2rem;justify-content:center;align-items:center;flex-wrap:wrap;"
    "margin-top:1.5rem;padding:.8rem 1rem;border:1px solid #2e2a26;border-radius:8px;"
    "background:rgba(0,0,0,.25);font-size:.8rem}"
    ".strip figure{margin:0;display:flex;flex-direction:column;align-items:center;gap:.4rem}"
    ".strip img{image-rendering:pixelated;width:96px;height:96px;background:#000;"
    "border-radius:3px}.strip figcaption{color:#a8a39b}.strip .earned{color:#ffd166}"
    ".note{font-size:.75rem;color:#7d786f;text-align:center;margin-top:1rem}"
)

_CONTROLS = (
    "<main><form class='controls' id='controls' onsubmit='return false'>"
    "<label>date <input type='date' id='date' value='{day}'></label>"
    "<label>fixture <select id='fixture'><option value=''>(database)</option>{options}"
    "</select></label>"
    "<label>size <select id='size'></select></label>"
    "<label>brightness <input type='range' id='brightness' min='10' max='100' step='10'>"
    "<span id='brightness-value'></span></label>"
    "<label><input type='checkbox' id='gamma' checked> LED gamma</label>"
    "<button type='button' id='pause'>pause</button>"
    "<button type='button' id='next'>next screen</button>"
    "</form>"
)

_DEVICE = (
    "<div class='stage'><div class='device' id='device'>"
    "<canvas class='matrix' id='matrix' width='512' height='512'></canvas>"
    "<div class='brand'>life-dashboard</div></div>"
    "<div class='caption' id='caption'>loading the rotation…</div></div>"
)

_STRIP = (
    "<div class='strip' id='strip'>"
    "<figure><img id='thumb-sparkle' alt='sparkle'><figcaption id='label-sparkle'>sparkle"
    "</figcaption><button type='button' data-play='sparkle'>play sparkle</button></figure>"
    "<figure><img id='thumb-party' alt='party'><figcaption id='label-party'>party</figcaption>"
    "<button type='button' data-play='party'>play party</button></figure>"
    "</div>"
    "<p class='note'>Frames are rendered by the service and drawn here as LEDs; the page only "
    "maps pixels. Brightness scales the PWM level before the panel curve. Judge legibility on "
    "the LED-gamma view.</p></main>"
)

_SCRIPT = r"""
(function () {
  'use strict';
  const N = 64;
  const DOT = 0.70;
  const GLOW_ALPHA = 0.55;
  const UNLIT = 'rgb(30,30,33)';
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const canvas = document.getElementById('matrix');
  const ctx = canvas.getContext('2d');
  const device = document.getElementById('device');
  const caption = document.getElementById('caption');
  const sizeSelect = document.getElementById('size');
  const brightnessInput = document.getElementById('brightness');
  const brightnessValue = document.getElementById('brightness-value');
  const gammaInput = document.getElementById('gamma');
  const pauseButton = document.getElementById('pause');
  const nextButton = document.getElementById('next');
  const dateInput = document.getElementById('date');
  const fixtureSelect = document.getElementById('fixture');

  const prefs = (function () {
    try { return JSON.parse(localStorage.getItem('pixoo-prefs') || '{}') || {}; }
    catch (e) { return {}; }
  })();
  function savePrefs() {
    try { localStorage.setItem('pixoo-prefs', JSON.stringify({
      size: state.size, brightness: state.brightness, gamma: state.gamma })); }
    catch (e) { /* per-viewer convenience only */ }
  }

  const state = {
    size: CONFIG.sizes.includes(prefs.size) ? prefs.size : CONFIG.default_size,
    brightness: CONFIG.brightness_steps.includes(prefs.brightness)
      ? prefs.brightness : CONFIG.default_brightness,
    gamma: typeof prefs.gamma === 'boolean' ? prefs.gamma : true,
    paused: false,
    rotation: null,
    screen: 0,
    frame: 0,
    frameElapsed: 0,
    screenElapsed: 0,
    overlay: null,
    lastTime: null,
    base: null,
    small: document.createElement('canvas'),
    lastDrawn: null,
  };
  state.small.width = N;
  state.small.height = N;
  const smallCtx = state.small.getContext('2d');
  const smallImage = smallCtx.createImageData(N, N);

  CONFIG.sizes.forEach(function (s) {
    const option = document.createElement('option');
    option.value = String(s);
    option.textContent = s + ' px';
    option.selected = s === state.size;
    sizeSelect.appendChild(option);
  });
  brightnessInput.value = String(state.brightness);
  gammaInput.checked = state.gamma;

  function applySize() {
    const s = state.size;
    canvas.style.width = s + 'px';
    canvas.style.height = s + 'px';
    canvas.width = Math.round(s * dpr);
    canvas.height = Math.round(s * dpr);
    device.style.setProperty('--bezel', Math.round(s * 0.075) + 'px');
    device.style.setProperty('--bezel-radius', Math.round(s * 0.055) + 'px');
    state.base = buildBase();
    redraw();
  }

  function buildBase() {
    const off = document.createElement('canvas');
    off.width = canvas.width;
    off.height = canvas.height;
    const c = off.getContext('2d');
    c.fillStyle = '#000';
    c.fillRect(0, 0, off.width, off.height);
    const cell = off.width / N;
    const r = cell * DOT / 2;
    c.fillStyle = UNLIT;
    c.beginPath();
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        c.moveTo((x + 0.5) * cell + r, (y + 0.5) * cell);
        c.arc((x + 0.5) * cell, (y + 0.5) * cell, r, 0, Math.PI * 2);
      }
    }
    c.fill();
    return off;
  }

  function lut() {
    const table = state.rotation.luts[state.gamma ? 'gamma' : 'raw'];
    return table[String(state.brightness)];
  }

  function draw(pixels) {
    state.lastDrawn = pixels;
    if (!state.base) { return; }
    const table = lut();
    const cell = canvas.width / N;
    const r = cell * DOT / 2;
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.drawImage(state.base, 0, 0);
    const paths = new Map();
    const data = smallImage.data;
    for (let i = 0, p = 0; i < N * N; i++, p += 4) {
      const rr = table[pixels[p]], gg = table[pixels[p + 1]], bb = table[pixels[p + 2]];
      data[p] = rr; data[p + 1] = gg; data[p + 2] = bb; data[p + 3] = 255;
      if (rr === 0 && gg === 0 && bb === 0) { continue; }
      const key = (rr << 16) | (gg << 8) | bb;
      let path = paths.get(key);
      if (!path) { path = new Path2D(); paths.set(key, path); }
      const cx = ((i % N) + 0.5) * cell, cy = (Math.floor(i / N) + 0.5) * cell;
      path.moveTo(cx + r, cy);
      path.arc(cx, cy, r, 0, Math.PI * 2);
    }
    smallCtx.putImageData(smallImage, 0, 0);
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = GLOW_ALPHA;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(state.small, 0, 0, canvas.width, canvas.height);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    paths.forEach(function (path, key) {
      ctx.fillStyle = 'rgb(' + (key >> 16) + ',' + ((key >> 8) & 255) + ',' + (key & 255) + ')';
      ctx.fill(path);
    });
  }

  function redraw() {
    if (state.lastDrawn) { draw(state.lastDrawn); }
  }

  function loadPixels(url) {
    return new Promise(function (resolve, reject) {
      const img = new Image();
      img.onload = function () {
        const off = document.createElement('canvas');
        off.width = N; off.height = N;
        const c = off.getContext('2d');
        c.drawImage(img, 0, 0);
        resolve(c.getImageData(0, 0, N, N).data);
      };
      img.onerror = function () { reject(new Error('frame failed: ' + url)); };
      img.src = url;
    });
  }

  async function loadClip(clip) {
    clip.pixels = await Promise.all(clip.frames.map(function (f) { return loadPixels(f.url); }));
    return clip;
  }

  function fmtSeconds(ms) {
    return (ms / 1000).toFixed(ms >= 10000 ? 0 : 1) + ' s';
  }

  function updateCaption() {
    const rot = state.rotation;
    const asOf = rot.as_of_local ? 'data as of ' + rot.as_of_local : 'no push yet';
    if (state.overlay) {
      const o = state.overlay;
      caption.innerHTML = '<b>' + o.clip.name.toUpperCase() + '</b> · ' +
        (o.clip.earned ? 'earned' : 'sample') + ' · frame ' + (o.frame + 1) + '/' +
        o.clip.frames.length + ' · ' + o.clip.frames[o.frame].ms + ' ms · ' + asOf;
      return;
    }
    const screen = rot.screens[state.screen];
    const frame = screen.frames[state.frame];
    caption.innerHTML = '<b>' + screen.name.toUpperCase() + '</b> · frame ' +
      (state.frame + 1) + '/' + screen.frames.length + ' · ' + frame.ms + ' ms · ' +
      fmtSeconds(state.screenElapsed) + ' of ' + fmtSeconds(screen.hold_ms) +
      (state.paused ? ' · paused' : '') + '<br>' + rot.day_local + ' · ' + asOf;
  }

  function showCurrent() {
    const screen = state.rotation.screens[state.screen];
    if (screen.pixels) { draw(screen.pixels[state.frame]); }
    updateCaption();
  }

  function nextScreen() {
    state.screen = (state.screen + 1) % state.rotation.screens.length;
    state.frame = 0;
    state.frameElapsed = 0;
    state.screenElapsed = 0;
    showCurrent();
  }

  function stepRotation(dt) {
    const screen = state.rotation.screens[state.screen];
    if (!screen.pixels) { return; }
    state.screenElapsed += dt;
    state.frameElapsed += dt;
    let changed = false;
    while (state.frameElapsed >= screen.frames[state.frame].ms) {
      state.frameElapsed -= screen.frames[state.frame].ms;
      state.frame = (state.frame + 1) % screen.frames.length;
      changed = true;
    }
    if (state.screenElapsed >= screen.hold_ms) { nextScreen(); return; }
    if (changed) { showCurrent(); } else { updateCaption(); }
  }

  function stepOverlay(dt) {
    const o = state.overlay;
    o.elapsed += dt;
    while (o.frame < o.clip.frames.length && o.elapsed >= o.clip.frames[o.frame].ms) {
      o.elapsed -= o.clip.frames[o.frame].ms;
      o.frame += 1;
    }
    if (o.frame >= o.clip.frames.length) {
      state.overlay = null;
      showCurrent();
      return;
    }
    draw(o.clip.pixels[o.frame]);
    updateCaption();
  }

  function tick(time) {
    requestAnimationFrame(tick);
    if (!state.rotation) { return; }
    const dt = state.lastTime === null ? 0 : Math.min(time - state.lastTime, 250);
    state.lastTime = time;
    if (state.overlay) { stepOverlay(dt); return; }
    if (state.paused) { return; }
    stepRotation(dt);
  }

  function playCelebration(name) {
    const clip = state.rotation.celebrations.find(function (c) { return c.name === name; });
    if (!clip || !clip.pixels) { return; }
    state.overlay = { clip: clip, frame: 0, elapsed: 0 };
    draw(clip.pixels[0]);
    updateCaption();
  }

  async function start() {
    const response = await fetch('/pixoo/rotation.json?' + CONFIG.query);
    if (!response.ok) {
      caption.textContent = 'rotation unavailable (' + response.status + ')';
      return;
    }
    state.rotation = await response.json();
    state.rotation.celebrations.forEach(function (c) {
      const label = document.getElementById('label-' + c.name);
      label.textContent = c.name + (c.earned ? ' · earned today' : ' · sample');
      label.className = c.earned ? 'earned' : '';
      document.getElementById('thumb-' + c.name).src =
        '/preview/image/' + c.name + '?' + CONFIG.query + '&scale=1&gamma=1';
    });
    const wanted = startScreen();
    state.screen = Math.max(0, state.rotation.screens.findIndex(function (s) {
      return s.name === wanted;
    }));
    await loadClip(state.rotation.screens[state.screen]);
    showCurrent();
    for (const screen of state.rotation.screens) {
      if (!screen.pixels) { await loadClip(screen); }
    }
    for (const c of state.rotation.celebrations) { await loadClip(c); }
    const party = state.rotation.celebrations.find(function (c) {
      return c.earned && c.name === 'party';
    });
    if (party && wanted === null) { playCelebration(party.name); }
  }

  function startScreen() {
    const match = /screen=([a-z-]+)/.exec(location.hash);
    return match ? match[1] : null;
  }

  sizeSelect.addEventListener('change', function () {
    state.size = Number(sizeSelect.value); savePrefs(); applySize();
  });
  brightnessInput.addEventListener('input', function () {
    state.brightness = Number(brightnessInput.value);
    brightnessValue.textContent = state.brightness + ' %';
    savePrefs(); redraw();
  });
  gammaInput.addEventListener('change', function () {
    state.gamma = gammaInput.checked; savePrefs(); redraw();
  });
  pauseButton.addEventListener('click', function () {
    state.paused = !state.paused;
    pauseButton.textContent = state.paused ? 'resume' : 'pause';
    if (state.rotation) { updateCaption(); }
  });
  nextButton.addEventListener('click', function () {
    if (!state.rotation) { return; }
    state.overlay = null;
    nextScreen();
  });
  dateInput.addEventListener('change', function () {
    if (dateInput.value) { location.href = '/pixoo?date=' + encodeURIComponent(dateInput.value); }
  });
  fixtureSelect.addEventListener('change', function () {
    location.href = fixtureSelect.value
      ? '/pixoo?fixture=' + encodeURIComponent(fixtureSelect.value) : '/pixoo';
  });
  document.querySelectorAll('[data-play]').forEach(function (button) {
    button.addEventListener('click', function () { playCelebration(button.dataset.play); });
  });

  if (/paused/.test(location.hash)) { pauseButton.click(); }
  brightnessValue.textContent = state.brightness + ' %';
  applySize();
  requestAnimationFrame(tick);
  start().catch(function (err) { caption.textContent = String(err); });
})();
"""
