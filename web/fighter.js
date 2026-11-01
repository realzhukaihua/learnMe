'use strict';

// Cartoon martial artist for a task window's header: practises a routine while the task
// runs, stands at attention otherwise. Look and routine are seeded by the task id, so a
// window keeps the same fighter across reloads.

const Fighter = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const BLEND = 380; // ms to ease between practising and attention

  // Angles in degrees. A limb at 0 hangs straight down; negative swings it forward (the
  // fighter faces right). Knees bend positive, elbows negative. Torso > 0 leans forward.
  // x/y shift the hip, s is facing (passing through 0 reads as a turn), w is the weapon
  // relative to the front forearm.
  const ATTN = { x: 0, y: 0, s: 1, t: 0, h: 0, ab1: 3, ab2: 0, af1: -3, af2: 0, lb1: 1, lb2: 0, lf1: -1, lf2: 0, w: 0 };
  const GUARD = { y: 2, t: 4, ab1: -35, ab2: -95, af1: -55, af2: -80, lb1: 25, lb2: 10, lf1: -35, lf2: 50 };

  const jab = { af1: -88, af2: -4, ab1: -28, ab2: -110, t: 10, x: 1.5 };
  const cross = { ab1: -86, ab2: -6, af1: -30, af2: -120, t: 15, x: 2.5, lb1: 32 };
  const chamber = { y: 0, t: -4, lf1: -95, lf2: 110, lb1: 3, lb2: 0 };
  const frontKick = { ...chamber, t: -12, lf1: -100, lf2: 8, af1: -40, af2: -100 };
  const highKick = { y: 0, t: -28, h: 10, lf1: -125, lf2: 8, lb1: 8, lb2: 2, af1: -20, af2: -120, ab1: 30, ab2: -40 };
  const crouch = { y: 5, t: 10, lf1: -50, lf2: 80, lb1: 5, lb2: 60, af1: -30, af2: -100, ab1: 10, ab2: -100 };
  const tuck = { y: -9, s: -1, t: 5, lf1: -80, lf2: 120, lb1: -30, lb2: 120, af1: -60, af2: -90, ab1: -40, ab2: -100 };
  const airKick = { y: -7, s: 1, t: -15, lf1: -100, lf2: 5, lb1: -10, lb2: 110, af1: -20, af2: -110, ab1: 40, ab2: -60 };
  const lunge = { y: 3, t: 14, x: 3, lf1: -45, lf2: 50, lb1: 35, lb2: -5 };
  const staffThrust = { ...lunge, af1: -90, af2: -2, w: 362 };
  const swordThrust = { ...lunge, af1: -90, af2: 0, w: 0, ab1: 115, ab2: 0 };
  const swordChop = { ...lunge, x: 2, t: 12, af1: -80, af2: 0, w: 15, ab1: 100, ab2: 0 };

  // seq: [ms to reach this pose from the previous one, pose]; the first entry's ms closes
  // the loop; wrap is added to the first pose when closing it.
  const ROUTINES = {
    fist: {
      name: tr('Boxing'), base: GUARD,
      seq: [[220, {}], [110, jab], [140, jab], [170, {}], [110, cross], [140, cross], [170, {}],
        [160, chamber], [100, frontKick], [160, frontKick], [140, chamber], [200, {}]],
    },
    kick: {
      name: tr('Kicks'), base: GUARD,
      seq: [[240, {}], [180, highKick], [200, highKick], [200, {}],
        [180, crouch], [170, tuck], [150, airKick], [150, airKick], [180, crouch], [220, {}]],
    },
    taichi: {
      name: tr('Tai chi'), base: { y: 3, ab1: -25, ab2: -60, af1: -50, af2: -50, lb1: 22, lb2: 18, lf1: -32, lf2: 45 },
      seq: [[1100, {}],
        [1300, { x: 2, t: 4, af1: -95, af2: -8, ab1: -10, ab2: -125 }],
        [1300, { x: -1, t: -3, af1: -135, af2: -35, ab1: -60, ab2: -45, lf2: 55 }],
        [1300, { y: 1, af1: -165, af2: -15, ab1: 25, ab2: -20, lf1: -12, lf2: 25, lb1: 12, lb2: 8 }]],
    },
    staff: {
      name: tr('Staff'), base: { ...GUARD, af1: -50, af2: -45, w: -60, ab1: -40, ab2: -95 },
      attn: { w: 180 }, wrap: { w: 720 }, // staff upright at rest; the loop ends one full spin on
      seq: [[260, {}], [260, { af1: -80, af2: -20, w: 120 }], [260, { af1: -55, af2: -45, w: 300 }],
        [160, staffThrust], [180, staffThrust],
        [220, { y: 0, t: -6, af1: -165, af2: -5, w: 440 }],
        [130, { ...lunge, af1: -70, af2: -5, w: 375 }], [180, { ...lunge, af1: -70, af2: -5, w: 375 }]],
    },
    sword: {
      name: tr('Sword'), base: { ...GUARD, af1: -45, af2: -55, w: -30, ab1: -30, ab2: -110 },
      attn: { w: 180 }, // reverse grip, blade along the forearm
      seq: [[260, {}], [150, swordThrust], [220, swordThrust], [240, {}],
        [200, { t: -6, y: 0, af1: -175, af2: 0, w: 0, lf1: -20, lf2: 20, lb1: 12, lb2: 6 }],
        [120, swordChop], [200, swordChop],
        [260, { s: -1, y: 1, t: 4, af1: -90, af2: 0, w: 0, ab1: 100, ab2: 0 }]],
    },
  };

  const SKIN = ['#f6d2b0', '#eec39a', '#d9a273', '#b9804f', '#8d5a36'];
  const HAIR = ['#1f1a17', '#2d221c', '#4a3123', '#7a4b2a', '#c9a15b', '#b8422e', '#e8e8e8'];
  const HAIR_STYLES = ['bald', 'short', 'spiky', 'bun', 'buns', 'ponytail', 'long'];
  const CLOTH = ['#f3f3f0', '#d94a3d', '#f2b632', '#3f6fd9', '#ef8a2a', '#3e9c5a', '#8a5cd6', '#4a505d'];
  const PANTS = ['#2a2d35', '#1f2a44', '#f3f3f0', '#5a3a26'];
  const BELTS = ['#1c1c1c', '#c0392b', '#7b4a24', '#f3f3f0', '#2f6fd6'];
  const BANDS = ['#e04a3a', '#f3f3f0', '#2f6fd6', '#f2b632'];
  const INK = '#1a1a1a';
  const lum = hex => [1, 3, 5].reduce((n, i) => n + parseInt(hex.slice(i, i + 2), 16), 0) / 765;

  function rng(str) {
    let a = 2166136261;
    for (const c of str) a = Math.imul(a ^ c.charCodeAt(0), 16777619);
    return () => { // mulberry32
      a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
    el.append(...kids.filter(Boolean));
    return el;
  }
  const bone = (x1, y1, x2, y2, stroke, width, cls) =>
    s('line', { x1, y1, x2, y2, stroke, 'stroke-width': width, 'stroke-linecap': 'round', class: cls });

  function makeLook(r) {
    const pick = a => a[Math.floor(r() * a.length)];
    const top = pick(CLOTH);
    const skin = pick(SKIN);
    const hair = pick(HAIR.filter(h => Math.abs(lum(h) - lum(skin)) > .2)); // keep the hairline readable
    const roll = r();
    return {
      skin, hair, hairStyle: pick(HAIR_STYLES),
      top, pants: r() < .5 ? top : pick(PANTS), belt: pick(BELTS.filter(b => b !== top)),
      longSleeves: r() < .5,
      band: r() < .35 ? pick(BANDS.filter(b => b !== hair)) : null,
      weapon: roll < .2 ? 'staff' : roll < .4 ? 'sword' : null,
    };
  }

  function head(L) {
    const { hair, skin } = L;
    const style = L.hairStyle;
    const behind = {
      ponytail: s('path', { d: 'M-6 -10Q-12.5 -8 -11 1Q-9 -4 -5 -5Z', fill: hair }),
      long: s('path', { d: 'M-7.2 -9Q-10 -1 -6.5 3.5L-1 0Z', fill: hair }),
    }[style];
    const above = {
      spiky: s('path', { d: 'M-7.4 -8L-10.5 -11.5L-6.6 -12.6L-7 -16.6L-3 -14.6L-1.2 -18.4L1.6 -14.8L5 -16.2L4.6 -12.4L7 -11.6L2 -11Z', fill: hair }),
      bun: s('g', {}, s('circle', { cx: -1.8, cy: -15.2, r: 2.9, fill: hair }),
        bone(-5, -16.6, 1.6, -14, '#d8b24a', .8)),
      buns: s('g', {}, s('circle', { cx: -5, cy: -12.6, r: 2.7, fill: hair }),
        s('circle', { cx: 2.6, cy: -14, r: 2.5, fill: hair })),
    }[style];
    const bald = style === 'bald';
    const old = hair === '#e8e8e8';
    const brow = old ? hair : INK;
    return s('g', {},
      behind,
      bald ? null : s('circle', { cx: 0, cy: -7.2, r: 7.7, fill: hair }),
      s('circle', bald ? { cx: 0, cy: -7.2, r: 7.4, fill: skin } : { cx: 1.3, cy: -6.2, r: 6.6, fill: skin }),
      s('circle', { cx: -1.3, cy: -6.4, r: 1.5, fill: skin }),
      above,
      L.band && s('g', {},
        bone(-7.1, -10.2, 6.6, -10.6, L.band, 2),
        s('g', { class: 'tails' }, bone(0, 0, -5.5, 1.2, L.band, 1.4), bone(0, 0, -5, 3.6, L.band, 1.4))),
      s('ellipse', { cx: 4.1, cy: -6.6, rx: .95, ry: 1.25, fill: INK }),
      s('circle', { cx: 3, cy: -4.2, r: 1.1, fill: '#ff8a8a', opacity: .35 }),
      bone(2.6, -9.6, 5.4, -8.6, brow, .9, 'on-only'),
      bone(4.6, -3.4, 5.9, -3.6, INK, .8, 'on-only'),
      bone(2.6, -9.2, 5.4, -9.4, brow, .9, 'off-only'),
      s('path', { d: 'M4.2 -3.9Q5.1 -2.7 6 -3.9', fill: 'none', stroke: INK, 'stroke-width': .8, 'stroke-linecap': 'round', class: 'off-only' }),
      old && s('path', { d: 'M2.4 -1.6Q5.4 -.6 4.6 4.4Q3 1 .8 -.4Z', fill: hair }));
  }

  function weapon(kind) {
    if (kind === 'staff') {
      return s('g', {}, bone(0, -4, 0, 24, '#9b6a3a', 1.7), // gripped near one end
        bone(0, -4, 0, -2.5, '#d9b26a', 1.8), bone(0, 22.5, 0, 24, '#d9b26a', 1.8));
    }
    return s('g', {}, // sword
      s('path', { d: 'M-.75 2L.75 2L.6 16L0 17.8L-.6 16Z', fill: '#dfe5ee' }),
      bone(-2.2, 1.8, 2.2, 1.8, '#d8b24a', 1.3),
      bone(0, -2.5, 0, 1.4, '#5a3b22', 1.5),
      bone(0, -2.5, -1.4, -6, '#e04a3a', 1.1));
  }

  const ease = f => f * f * (3 - 2 * f);

  function mix(a, b, f, shortWay) {
    const p = {};
    for (const k in a) {
      let to = b[k];
      if (k === 'w' && shortWay) to -= Math.round((to - a.w) / 360) * 360;
      p[k] = a[k] + (to - a[k]) * f;
    }
    return p;
  }

  // One animation loop for every fighter that's moving.
  const live = new Set();
  let raf = 0;
  function loop(now) {
    for (const f of live) if (!f.tick(now)) live.delete(f);
    raf = live.size ? requestAnimationFrame(loop) : 0;
  }
  function wake(f) {
    live.add(f);
    if (!raf) raf = requestAnimationFrame(loop);
  }

  return class Fighter {
    constructor(seed) {
      const r = rng(String(seed));
      const L = makeLook(r);
      const choices = L.weapon ? [L.weapon] : ['fist', 'kick', 'taichi'];
      const R = ROUTINES[choices[Math.floor(r() * choices.length)]];
      this.name = R.name;
      this.attn = { ...ATTN, ...R.attn };
      this.keys = R.seq.map(([ms, p]) => ({ ms, pose: { ...ATTN, ...R.base, ...p } }));
      this.total = this.keys.reduce((n, k) => n + k.ms, 0);
      this.offset = r() * this.total;
      this.wrap = { ...this.keys[0].pose };
      for (const [k, v] of Object.entries(R.wrap || {})) this.wrap[k] += v;

      const sleeve = L.longSleeves ? L.top : L.skin;
      const leg = (k1, k2, x) => {
        const knee = s('g', {}, bone(0, 0, 0, 7.6, L.pants, 4), bone(-.6, 8.6, 2.8, 8.6, INK, 3));
        const hip = s('g', {}, bone(0, 0, 0, 8.6, L.pants, 4.2), knee);
        return [[hip, k1, x, 0], [knee, k2, 0, 8.6]];
      };
      const arm = (k1, k2, x, held) => {
        const elbow = s('g', {}, bone(0, 0, 0, 5.6, sleeve, 3.2));
        if (held) {
          const grip = s('g', {}, weapon(held));
          elbow.append(grip);
          this.grip = [grip, 'w', 0, 6.2];
        }
        elbow.append(s('circle', { cx: 0, cy: 6.2, r: 2, fill: L.skin }));
        const shoulder = s('g', {}, bone(0, 0, 0, 6.5, L.top, 3.6), elbow);
        return [[shoulder, k1, x, -11], [elbow, k2, 0, 6.5]];
      };
      const [legB, kneeB] = leg('lb1', 'lb2', -1);
      const [legF, kneeF] = leg('lf1', 'lf2', 1);
      const [armB, elbowB] = arm('ab1', 'ab2', -.5);
      const [armF, elbowF] = arm('af1', 'af2', .5, L.weapon);
      const headG = s('g', {}, head(L));
      this.tails = headG.querySelector('.tails');
      const torso = s('g', {},
        armB[0],
        s('path', { d: 'M-4 0L-5 -11Q-5 -13 -3 -13L3 -13Q5 -13 5 -11L4.2 0Z', fill: L.top }),
        s('path', { d: 'M1.2 -13L3.9 -13L2.7 -9.8Z', fill: L.skin }),
        bone(-4.4, -2.2, 4.5, -2.2, L.belt, 2.2),
        bone(3.8, -2, 5.4, 1.6, L.belt, 1.2),
        headG,
        armF[0]);
      this.root = s('g', {}, legB[0], legF[0], torso);
      this.shadow = s('ellipse', { cx: 32, cy: 59.6, rx: 10, ry: 1.5, fill: 'rgba(255,255,255,.08)' });
      this.label = s('title');
      this.el = s('svg', { class: 'fighter', viewBox: '0 0 64 64', 'aria-hidden': 'true' },
        this.label, this.shadow, this.root);
      this.joints = [legB, kneeB, legF, kneeF, armB, elbowB, armF, elbowF,
        [torso, 't', 0, 0], [headG, 'h', .5, -13], this.grip].filter(Boolean);

      this.on = false;
      this.pose = { ...this.attn };
      this.from = null;
      this.apply(this.pose, 0);
      this.label.textContent = `${this.name} · ${tr('At attention')}`;
    }

    set(on) {
      if (on === this.on) return;
      this.on = on;
      this.from = { ...this.pose };
      this.t0 = performance.now();
      this.el.classList.toggle('on', on);
      this.label.textContent = `${this.name} · ${on ? tr('Practising') : tr('At attention')}`;
      wake(this);
    }

    routine(now) {
      let t = (now + this.offset) % this.total;
      for (let i = 0; i < this.keys.length; i++) {
        const { ms, pose } = this.keys[i];
        if (t < ms || i === this.keys.length - 1) {
          const prev = i ? this.keys[i - 1].pose : this.keys[this.keys.length - 1].pose;
          const to = i ? pose : this.wrap;
          return mix(prev, to, ease(Math.min(1, t / ms)));
        }
        t -= ms;
      }
    }

    tick(now) {
      if (!this.el.isConnected) return false;
      if (REDUCED) {
        this.apply(this.on ? this.keys[0].pose : this.attn, now);
        return false;
      }
      const target = this.on ? this.routine(now) : this.attn;
      const f = (now - this.t0) / BLEND;
      this.pose = f < 1 ? mix(this.from, target, ease(f), true) : target;
      this.apply(this.pose, now);
      return this.on || f < 1;
    }

    apply(p, now) {
      const n = v => +v.toFixed(2);
      this.root.setAttribute('transform', `translate(${n(32 + p.x)} ${n(40 + p.y)}) scale(${n(p.s)} 1)`);
      this.shadow.setAttribute('cx', n(32 + p.x));
      this.shadow.setAttribute('rx', n(10 + Math.min(0, p.y) * .5));
      for (const [el, k, x, y] of this.joints) el.setAttribute('transform', `translate(${x} ${y}) rotate(${n(p[k])})`);
      if (this.tails) {
        const a = this.on ? -4 + 14 * Math.sin(now / 90) : -35;
        this.tails.setAttribute('transform', `translate(-7 -10.3) rotate(${n(a)})`);
      }
    }
  };
})();
