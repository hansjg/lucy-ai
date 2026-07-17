// ─── Lucy Puppet Animation Engine (relative positioning) ─────
// Each part is individually sized. We place them relative to a
// virtual body-center coordinate using manually tuned offsets.

(function () {

  // ── Part definitions ──────────────────────────────────────
  // offset: where to draw the part's top-left, as fraction of canvas size
  // pivot:  rotation origin as fraction of THAT part's own width/height
  // scale:  how large to draw it relative to canvas width
  const LAYOUT = {
    lucy_body:       { ox: 0.18, oy: 0.38, scale: 0.64, pivot: null },
    lucy_arm_l_upper:{ ox: 0.06, oy: 0.40, scale: 0.32, pivot: { x: 0.70, y: 0.08 } },
    lucy_arm_r_upper:{ ox: 0.62, oy: 0.40, scale: 0.32, pivot: { x: 0.30, y: 0.08 } },
    lucy_face_idle:      { ox: 0.18, oy: 0.04, scale: 0.62, pivot: { x: 0.50, y: 0.92 } },
    lucy_face_happy:     { ox: 0.18, oy: 0.00, scale: 0.62, pivot: { x: 0.50, y: 0.92 } },
    lucy_face_thinking:  { ox: 0.18, oy: 0.04, scale: 0.62, pivot: { x: 0.50, y: 0.92 } },
    lucy_face_surprised: { ox: 0.14, oy: 0.00, scale: 0.68, pivot: { x: 0.50, y: 0.92 } },
    lucy_head:       { ox: 0.13, oy: 0.02, scale: 0.72, pivot: { x: 0.50, y: 0.92 } },
    lucy_hair:       { ox: 0.12, oy: 0.00, scale: 0.74, pivot: { x: 0.50, y: 0.20 } },
    lucy_hand_l:     { ox: 0.06, oy: 0.65, scale: 0.28, pivot: null },
    lucy_hand_r:     { ox: 0.66, oy: 0.65, scale: 0.28, pivot: null },
  };

  // Draw order (back to front)
  const DRAW_ORDER = [
    'lucy_arm_l_upper',  // character's left = viewer's right (behind body)
    'lucy_hand_l',
    'lucy_body',
    'lucy_arm_r_upper',  // character's right = viewer's left (in front)
    'lucy_hand_r',
    'FACE',              // head + face + hair
  ];

  const FACE_PARTS = ['lucy_face_idle','lucy_face_happy','lucy_face_thinking','lucy_face_surprised'];
  const ALL_PARTS  = [...Object.keys(LAYOUT)];

  // ── Animation state ───────────────────────────────────────
  const anim = {
    emotion: 'idle',
    talking: false,
    t: 0,
    // smoothed values
    headAngle: 0, headNod: 0,
    hairAngle: 0,
    armLAngle: 0, armRAngle: 0,
    handLOY: 0,   handROY: 0,
  };

  // Emotion → target pose
  const POSES = {
    idle:      { headAngle: 0,     headNod: 0,     armL:  0.05, armR: -0.05 },
    happy:     { headAngle: 0.08,  headNod:-0.02,  armL: -0.45, armR:  0.45 },
    thinking:  { headAngle: 0.18,  headNod: 0.04,  armL:  0.05, armR:  0.55 },
    surprised: { headAngle:-0.06,  headNod:-0.08,  armL: -0.65, armR:  0.65 },
    listening: { headAngle: 0.05,  headNod: 0,     armL:  0.05, armR: -0.05 },
  };

  function lerp(a, b, t) { return a + (b - a) * t; }

  // ── Assets ────────────────────────────────────────────────
  const imgs = {};
  let loadedCount = 0;

  function loadAll() {
    return new Promise(resolve => {
      const names = ALL_PARTS;
      for (const name of names) {
        const img = new Image();
        img.src = '/static/' + name + '.png';
        img.onload  = () => { imgs[name] = img; check(); };
        img.onerror = () => { imgs[name] = null; console.warn('Missing:', name); check(); };
      }
      function check() { if (++loadedCount >= names.length) resolve(); }
    });
  }

  // ── Canvas ────────────────────────────────────────────────
  let canvas, ctx, CW;

  function resize() {
    CW = canvas.offsetWidth || 280;
    canvas.width  = CW;
    canvas.height = CW;
  }

  // ── Draw one part with optional rotation around its pivot ─
  function drawPart(name, extraOY = 0, extraAngle = 0) {
    const img = imgs[name];
    if (!img) return;
    const cfg = LAYOUT[name];
    if (!cfg) return;

    const dw = CW * cfg.scale;
    const dh = dw * (img.naturalHeight / img.naturalWidth);
    const dx = CW * cfg.ox;
    const dy = CW * cfg.oy + extraOY;

    if (cfg.pivot && extraAngle !== 0) {
      const px = dx + dw * cfg.pivot.x;
      const py = dy + dh * cfg.pivot.y;
      ctx.save();
      ctx.translate(px, py);
      ctx.rotate(extraAngle);
      ctx.translate(-px, -py);
      ctx.drawImage(img, dx, dy, dw, dh);
      ctx.restore();
    } else {
      ctx.drawImage(img, dx, dy, dw, dh);
    }
  }

  function getFaceName() {
    const map = {
      idle:      'lucy_face_idle',
      happy:     'lucy_face_happy',
      thinking:  'lucy_face_thinking',
      surprised: 'lucy_face_surprised',
      listening: 'lucy_face_idle',
    };
    return map[anim.emotion] || 'lucy_face_idle';
  }

  // ── Main render loop ──────────────────────────────────────
  function tick() {
    anim.t++;
    const t = anim.t;
    const pose = POSES[anim.emotion] || POSES.idle;

    const breathe    = Math.sin(t * 0.025) * 0.012;
    const hairSway   = Math.sin(t * 0.018) * 0.035;
    const talkJiggle = anim.talking ? Math.sin(t * 0.4) * 0.025 : 0;
    const nodOff     = (breathe + talkJiggle) * CW * 0.04;

    const spd = 0.07;
    anim.headAngle = lerp(anim.headAngle, pose.headAngle + breathe * 0.4, spd);
    anim.headNod   = lerp(anim.headNod,   pose.headNod,                   spd);
    anim.hairAngle = lerp(anim.hairAngle, hairSway,                       0.04);
    anim.armLAngle = lerp(anim.armLAngle, pose.armL,                      spd);
    anim.armRAngle = lerp(anim.armRAngle, pose.armR,                      spd);

    ctx.clearRect(0, 0, CW, CW);

    for (const key of DRAW_ORDER) {
      if (key === 'FACE') {
        // Head group: head + face + hair all rotate together
        const faceName = getFaceName();
        const hcfg = LAYOUT['lucy_head'];
        const img  = imgs['lucy_head'];
        if (img) {
          const dw = CW * hcfg.scale;
          const dh = dw * (img.naturalHeight / img.naturalWidth);
          const px = CW * hcfg.ox + dw * hcfg.pivot.x;
          const py = CW * hcfg.oy + dh * hcfg.pivot.y;
          ctx.save();
          ctx.translate(px, py);
          ctx.rotate(anim.headAngle);
          ctx.translate(0, nodOff);
          ctx.translate(-px, -py);
          // head base
          ctx.drawImage(img, CW * hcfg.ox, CW * hcfg.oy, dw, dh);
          // face expression
          const fcfg = LAYOUT[faceName];
          const fimg = imgs[faceName];
          if (fimg && fcfg) {
            const fw = CW * fcfg.scale;
            const fh = fw * (fimg.naturalHeight / fimg.naturalWidth);
            ctx.drawImage(fimg, CW * fcfg.ox, CW * fcfg.oy, fw, fh);
          }
          // hair (extra sway on top of head tilt)
          const hrcfg = LAYOUT['lucy_hair'];
          const hrimg = imgs['lucy_hair'];
          if (hrimg && hrcfg) {
            const hrw = CW * hrcfg.scale;
            const hrh = hrw * (hrimg.naturalHeight / hrimg.naturalWidth);
            const hrpx = CW * hrcfg.ox + hrw * hrcfg.pivot.x;
            const hrpy = CW * hrcfg.oy + hrh * hrcfg.pivot.y;
            ctx.save();
            ctx.translate(hrpx, hrpy);
            ctx.rotate(anim.hairAngle);
            ctx.translate(-hrpx, -hrpy);
            ctx.drawImage(hrimg, CW * hrcfg.ox, CW * hrcfg.oy, hrw, hrh);
            ctx.restore();
          }
          ctx.restore();
        }
        continue;
      }

      if (key === 'lucy_hair' || key === 'lucy_head') continue; // drawn in FACE block

      if (key === 'lucy_arm_l_upper') { drawPart(key, 0, anim.armLAngle); continue; }
      if (key === 'lucy_arm_r_upper') { drawPart(key, 0, anim.armRAngle); continue; }
      if (key === 'lucy_hand_l')      { drawPart(key, anim.armLAngle * CW * 0.15); continue; }
      if (key === 'lucy_hand_r')      { drawPart(key, anim.armRAngle * -CW * 0.15); continue; }

      drawPart(key);
    }

    requestAnimationFrame(tick);
  }

  // ── Public API ────────────────────────────────────────────
  window.LucyPuppet = {
    setEmotion(e) { anim.emotion = e; },
    setTalking(on) { anim.talking = on; },
  };

  // ── Boot ──────────────────────────────────────────────────
  async function init() {
    canvas = document.getElementById('lucy-puppet-canvas');
    if (!canvas) { console.warn('lucy-puppet-canvas not found'); return; }
    ctx = canvas.getContext('2d');
    resize();
    window.addEventListener('resize', resize);
    await loadAll();
    const ok = Object.values(imgs).filter(Boolean).length;
    console.log(`Lucy puppet: ${ok}/${ALL_PARTS.length} parts loaded`);
    tick();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
