// ─── Cyberpunk City Background ───────────────────────────
(function () {
  const canvas = document.getElementById('bg-canvas');
  const ctx    = canvas.getContext('2d');
  let W, H, rain = [], neonSigns = [], buildings = [];

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
    buildCity();
  }

  function buildCity() {
    // Buildings
    buildings = [];
    const cols = Math.ceil(W / 38);
    for (let i = 0; i < cols; i++) {
      const bw = 28 + Math.random() * 40;
      const bh = H * (0.25 + Math.random() * 0.45);
      buildings.push({
        x: i * 38 - 10,
        y: H - bh,
        w: bw,
        h: bh,
        windows: buildWindows(i * 38 - 10, H - bh, bw, bh),
        color: `hsl(${220 + Math.random()*40},${10 + Math.random()*15}%,${5 + Math.random()*8}%)`
      });
    }

    // Neon signs
    neonSigns = [
      { x: W*0.18, y: H*0.38, text: 'ナイトシティ', color: '#ff2233', size: 13, flicker: Math.random() },
      { x: W*0.55, y: H*0.32, text: 'NETRUNNER', color: '#00e5ff', size: 11, flicker: Math.random() },
      { x: W*0.72, y: H*0.44, text: '危険区域', color: '#ff9900', size: 10, flicker: Math.random() },
      { x: W*0.35, y: H*0.5,  text: 'KIROSHI', color: '#ff2233', size: 14, flicker: Math.random() },
      { x: W*0.82, y: H*0.36, text: 'ARASAKA', color: '#cc0022', size: 12, flicker: Math.random() },
    ];

    // Rain
    rain = Array.from({ length: 220 }, () => makeRaindrop());
  }

  function buildWindows(bx, by, bw, bh) {
    const wins = [];
    const cols = Math.floor(bw / 7);
    const rows = Math.floor(bh / 9);
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        if (Math.random() > 0.45) continue;
        wins.push({
          x: bx + 3 + c * 7,
          y: by + 4 + r * 9,
          on: Math.random() > 0.3,
          color: Math.random() > 0.7 ? '#00e5ff' : Math.random() > 0.5 ? '#ff9940' : '#ffffcc',
          flickerTimer: Math.random() * 300
        });
      }
    }
    return wins;
  }

  function makeRaindrop() {
    return {
      x: Math.random() * (W + 200) - 100,
      y: Math.random() * H,
      len: 6 + Math.random() * 14,
      speed: 10 + Math.random() * 16,
      alpha: 0.1 + Math.random() * 0.35
    };
  }

  let frame = 0;
  function draw() {
    frame++;
    // Sky gradient
    const sky = ctx.createLinearGradient(0, 0, 0, H);
    sky.addColorStop(0,   '#03010a');
    sky.addColorStop(0.4, '#07030f');
    sky.addColorStop(0.7, '#0a0515');
    sky.addColorStop(1,   '#130a08');
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, W, H);

    // Distant glow blobs
    const glows = [
      { x: W*0.2, y: H*0.6, r: W*0.22, c: 'rgba(255,34,51,0.07)' },
      { x: W*0.75, y: H*0.55, r: W*0.18, c: 'rgba(0,229,255,0.05)' },
      { x: W*0.5, y: H*0.7, r: W*0.25, c: 'rgba(120,0,180,0.06)' },
    ];
    for (const g of glows) {
      const gr = ctx.createRadialGradient(g.x, g.y, 0, g.x, g.y, g.r);
      gr.addColorStop(0, g.c); gr.addColorStop(1, 'transparent');
      ctx.fillStyle = gr; ctx.fillRect(0, 0, W, H);
    }

    // Buildings
    for (const b of buildings) {
      ctx.fillStyle = b.color;
      ctx.fillRect(b.x, b.y, b.w, b.h);
      // Window flicker
      for (const w of b.windows) {
        w.flickerTimer--;
        if (w.flickerTimer <= 0) {
          if (Math.random() > 0.6) w.on = !w.on;
          w.flickerTimer = 80 + Math.random() * 400;
        }
        if (!w.on) continue;
        ctx.fillStyle = w.color;
        ctx.globalAlpha = 0.55 + Math.sin(frame * 0.02 + w.flickerTimer) * 0.1;
        ctx.fillRect(w.x, w.y, 4, 3);
        ctx.globalAlpha = 1;
      }
    }

    // Wet ground reflection
    const reflH = H * 0.08;
    const ref = ctx.createLinearGradient(0, H - reflH, 0, H);
    ref.addColorStop(0, 'rgba(255,34,51,0.0)');
    ref.addColorStop(0.5, 'rgba(255,34,51,0.06)');
    ref.addColorStop(1, 'rgba(0,229,255,0.04)');
    ctx.fillStyle = ref; ctx.fillRect(0, H - reflH, W, reflH);

    // Ground line
    ctx.strokeStyle = 'rgba(255,34,51,0.18)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, H * 0.82); ctx.lineTo(W, H * 0.82); ctx.stroke();

    // Neon signs
    for (const s of neonSigns) {
      const flicker = Math.sin(frame * 0.07 + s.flicker * 10);
      const alpha = flicker > -0.8 ? (0.75 + flicker * 0.15) : 0.1;
      ctx.globalAlpha = alpha;
      ctx.font = `bold ${s.size}px 'Share Tech Mono', monospace`;
      ctx.shadowColor = s.color;
      ctx.shadowBlur  = 12;
      ctx.fillStyle   = s.color;
      ctx.fillText(s.text, s.x, s.y);
      ctx.shadowBlur = 0;
      ctx.globalAlpha = 1;
    }

    // Rain
    ctx.strokeStyle = 'rgba(160,200,255,0.18)';
    ctx.lineWidth = 0.8;
    for (const r of rain) {
      ctx.globalAlpha = r.alpha;
      ctx.beginPath();
      ctx.moveTo(r.x, r.y);
      ctx.lineTo(r.x - 1, r.y + r.len);
      ctx.stroke();
      r.y += r.speed;
      r.x -= 0.5;
      if (r.y > H) { r.y = -r.len; r.x = Math.random() * (W + 200) - 100; }
    }
    ctx.globalAlpha = 1;

    requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize);
  resize();
  draw();
})();

// ─── CP2077 Circuit Panel Background ─────────────────────
(function () {
  const canvas = document.getElementById('hack-canvas');
  const ctx    = canvas.getContext('2d');
  let W, H, nodes = [], blips = [];

  function resize() {
    W = canvas.width  = canvas.offsetWidth;
    H = canvas.height = canvas.offsetHeight;
    buildCircuit();
  }

  function buildCircuit() {
    nodes = [];
    const GRID = 32;
    const cols = Math.floor(W / GRID);
    const rows = Math.floor(H / GRID);
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        if (Math.random() > 0.38) continue;
        nodes.push({
          x: c * GRID + GRID / 2,
          y: r * GRID + GRID / 2,
          // connect right or down randomly
          right: Math.random() > 0.5 && c < cols - 1,
          down:  Math.random() > 0.5 && r < rows - 1,
        });
      }
    }
    blips = [];
  }

  function draw() {
    // Dark base with very slow fade (near-static)
    ctx.fillStyle = 'rgba(5, 1, 3, 0.06)';
    ctx.fillRect(0, 0, W, H);

    const t = Date.now();

    // Draw circuit traces
    ctx.lineWidth = 0.6;
    for (const n of nodes) {
      const pulse = 0.04 + 0.02 * Math.sin(t * 0.0008 + n.x * 0.04 + n.y * 0.03);
      ctx.strokeStyle = `rgba(180, 20, 30, ${pulse})`;
      ctx.shadowColor = 'rgba(255,30,40,0.3)';
      ctx.shadowBlur  = 2;
      if (n.right) {
        ctx.beginPath();
        ctx.moveTo(n.x, n.y);
        ctx.lineTo(n.x + 32, n.y);
        ctx.stroke();
      }
      if (n.down) {
        ctx.beginPath();
        ctx.moveTo(n.x, n.y);
        ctx.lineTo(n.x, n.y + 32);
        ctx.stroke();
      }
      // Node dot
      ctx.fillStyle = `rgba(220, 30, 40, ${pulse * 2})`;
      ctx.shadowBlur = 4;
      ctx.beginPath();
      ctx.arc(n.x, n.y, 1.2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.shadowBlur = 0;

    // Spawn blips occasionally
    if (Math.random() > 0.985 && blips.length < 6) {
      const n = nodes[Math.floor(Math.random() * nodes.length)];
      if (n) blips.push({ x: n.x, y: n.y, life: 1.0 });
    }

    // Draw & age blips
    blips = blips.filter(b => b.life > 0);
    for (const b of blips) {
      const r = (1 - b.life) * 14;
      ctx.beginPath();
      ctx.arc(b.x, b.y, r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(255, 34, 51, ${b.life * 0.5})`;
      ctx.lineWidth = 1;
      ctx.shadowColor = '#ff2233';
      ctx.shadowBlur  = 6;
      ctx.stroke();
      ctx.shadowBlur = 0;
      b.life -= 0.018;
    }

    // Slow scan line
    const scanY = ((t / 40) % (H + 60)) - 30;
    const sg = ctx.createLinearGradient(0, scanY, 0, scanY + 30);
    sg.addColorStop(0,   'rgba(255,34,51,0)');
    sg.addColorStop(0.5, 'rgba(255,34,51,0.04)');
    sg.addColorStop(1,   'rgba(255,34,51,0)');
    ctx.fillStyle = sg;
    ctx.fillRect(0, scanY, W, 30);

    requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize);
  resize();
  draw();
})();

// ─── Live2D model path ────────────────────────────────────
// Place your .model3.json file here and update this path:
const LIVE2D_MODEL = '/static/models/model.model3.json';

// ─── State ───────────────────────────────────────────────
let ws, audioCtx, workletNode, sourceNode, chunks = [], isRecording = false;
let cameraStream = null, cameraActive = false, live2dModel = null;

// ─── DOM refs ────────────────────────────────────────────
const pttBtn      = document.getElementById('ptt-btn');
const camBtn      = document.getElementById('cam-btn');
const statusDot   = document.getElementById('status-dot');
const statusText  = document.getElementById('status-text');
const messages    = document.getElementById('messages');
const liveBar     = document.getElementById('live-transcript');
const transcriptEl = document.getElementById('transcript-text');
const cameraFeed  = document.getElementById('camera-feed');
const lucyWrap    = document.getElementById('lucy-wrap');
const lucyCanvas  = document.getElementById('lucy-canvas');
const mouthAnim   = document.getElementById('mouth-open-anim');

// ─── WebSocket ───────────────────────────────────────────
function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen  = () => setStatus('idle', 'READY_');
  ws.onclose = () => { setStatus('idle', 'Disconnected'); setTimeout(connectWS, 2000); };
  ws.onerror = () => ws.close();

  ws.onmessage = async ({ data }) => {
    const msg = JSON.parse(data);

    if (msg.type === 'status') {
      const labels = { idle: 'READY_', thinking: 'PROCESSING_', speaking: 'SPEAKING_', listening: 'LISTENING_' };
      setStatus(msg.state, labels[msg.state] ?? msg.state);
      if (msg.state !== 'speaking') animateCharacter(msg.state); // audio queue drives speaking state
    }
    else if (msg.type === 'transcript') {
      showLiveBar(msg.text); // shows dots inside
    }
    else if (msg.type === 'reply_start') {
      hideLiveBar();
      beginStreamBubble(); // morphs dots → text bubble
    }
    else if (msg.type === 'token') {
      appendToken(msg.text);
    }
    else if (msg.type === 'reply_end') {
      finalizeStreamBubble();
    }
    else if (msg.type === 'audio_chunk') {
      enqueueAudio(msg.data);
    }
    else if (msg.type === 'error') {
      finalizeStreamBubble();
      removeThinkingDots();
      addMessage('ai', `Something went wrong: ${msg.message}`);
    }
  };
}

// ─── Status ──────────────────────────────────────────────
function setStatus(state, label) {
  statusDot.className = state === 'idle' ? '' : state;
  statusText.textContent = label;
}

// ─── Character animation ─────────────────────────────────
function animateCharacter(state) {
  if (live2dModel) {
    const motionMap = { idle: 'Idle', thinking: 'FlickHead', speaking: 'TapBody' };
    try { live2dModel.motion(motionMap[state] ?? 'Idle'); } catch (_) {}
    return;
  }

  const isTalking = state === 'speaking';
  lucyWrap.className = isTalking ? 'talking' : state === 'thinking' ? 'thinking' : '';

  // Flap mouth open/close while speaking
  clearInterval(window._mouthTimer);
  if (isTalking) {
    let open = false;
    window._mouthTimer = setInterval(() => {
      open = !open;
      mouthAnim.style.transform = open ? 'scaleY(1)' : 'scaleY(0.15)';
    }, 150);
  } else {
    mouthAnim.style.transform = 'scaleY(0)';
  }
}

// ─── Audio recording (PTT) ───────────────────────────────
async function startRecording() {
  if (isRecording) return;
  isRecording = true;
  chunks = [];
  pttBtn.classList.add('active');
  document.getElementById('ptt-label').textContent = 'Recording…';
  setStatus('listening', 'Listening…');
  animateCharacter('listening');

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 16000, channelCount: 1 } });
    audioCtx = new AudioContext({ sampleRate: 16000 });
    await audioCtx.audioWorklet.addModule('/static/recorder-worklet.js');
    sourceNode  = audioCtx.createMediaStreamSource(stream);
    workletNode = new AudioWorkletNode(audioCtx, 'recorder-processor');
    workletNode.port.onmessage = ({ data }) => chunks.push(data);
    sourceNode.connect(workletNode);
  } catch (err) {
    console.error('Mic error:', err);
    stopRecording();
  }
}

async function stopRecording() {
  if (!isRecording) return;
  isRecording = false;
  pttBtn.classList.remove('active');
  document.getElementById('ptt-label').textContent = 'HOLD TO SPEAK';

  sourceNode?.disconnect();
  workletNode?.disconnect();
  audioCtx?.close();

  if (chunks.length < 4) { setStatus('idle', 'READY_'); animateCharacter('idle'); return; }

  const wav = encodeWAV(chunks, 16000);
  const b64 = arrayBufferToBase64(wav);

  // Use held snapshot if available, otherwise live frame
  let imageB64 = null;
  if (heldSnapshot) {
    imageB64 = heldSnapshot;
    clearSnapshot();
  } else if (cameraActive) {
    imageB64 = captureFrame();
  }

  const payload = { type: 'audio', data: b64 };
  if (imageB64) payload.image = imageB64;
  ws?.send(JSON.stringify(payload));
}

// ─── WAV encoder (Float32 PCM → 16-bit WAV) ──────────────
function encodeWAV(chunkList, sr) {
  const total = chunkList.reduce((n, c) => n + c.length, 0);
  const buf = new ArrayBuffer(44 + total * 2);
  const view = new DataView(buf);
  const writeStr = (off, s) => [...s].forEach((c, i) => view.setUint8(off + i, c.charCodeAt(0)));

  writeStr(0, 'RIFF');
  view.setUint32(4,  36 + total * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1,  true);   // PCM
  view.setUint16(22, 1,  true);   // mono
  view.setUint32(24, sr, true);
  view.setUint32(28, sr * 2, true);
  view.setUint16(32, 2,  true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, total * 2, true);

  let off = 44;
  for (const chunk of chunkList) {
    for (const s of chunk) {
      const v = Math.max(-1, Math.min(1, s));
      view.setInt16(off, v < 0 ? v * 0x8000 : v * 0x7FFF, true);
      off += 2;
    }
  }
  return buf;
}

function arrayBufferToBase64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

// ─── Audio queue (plays chunks in order as they arrive) ──
const audioQueue = [];
let audioPlaying = false;

function enqueueAudio(b64) {
  audioQueue.push(b64);
  if (!audioPlaying) drainQueue();
}

async function drainQueue() {
  audioPlaying = true;
  animateCharacter('speaking');
  while (audioQueue.length > 0) {
    await playAudio(audioQueue.shift());
  }
  audioPlaying = false;
  animateCharacter('idle');
  setStatus('idle', 'READY_');
}

async function playAudio(b64mp3) {
  const bytes = Uint8Array.from(atob(b64mp3), c => c.charCodeAt(0));
  const blob  = new Blob([bytes], { type: 'audio/mp3' });
  const url   = URL.createObjectURL(blob);
  const audio = new Audio(url);
  return new Promise(resolve => {
    audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
    audio.onerror = resolve;
    audio.play().catch(resolve);
  });
}

// ─── Chat messages ────────────────────────────────────────
function makeRow(role) {
  const row = document.createElement('div');
  row.className = `msg-row ${role}`;
  const av = document.createElement('div');
  av.className = 'msg-avatar';
  av.textContent = role === 'ai' ? 'AI' : 'YOU';
  row.appendChild(av);
  return row;
}

function addMessage(role, text) {
  const row = makeRow(role);
  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = text;
  row.appendChild(bubble);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;
}

// ── Thinking dots ──────────────────────────────────────────
let thinkingRow = null;

function showThinkingDots() {
  if (thinkingRow) return;
  thinkingRow = document.createElement('div');
  thinkingRow.className = 'msg-row ai';
  thinkingRow.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="msg-bubble bubble-dots">
      <span></span><span></span><span></span>
    </div>`;
  messages.appendChild(thinkingRow);
  messages.scrollTop = messages.scrollHeight;
}

function removeThinkingDots() {
  if (!thinkingRow) return;
  thinkingRow.style.transition = 'opacity 0.15s ease';
  thinkingRow.style.opacity = '0';
  setTimeout(() => { thinkingRow?.remove(); thinkingRow = null; }, 150);
}

// ── Streaming bubble ───────────────────────────────────────
let streamBubble = null, streamText = '';
let tokenBuffer = '', flushTimer = null;

function beginStreamBubble() {
  removeThinkingDots();

  streamText = '';
  tokenBuffer = '';

  const row = document.createElement('div');
  row.className = 'msg-row ai';
  row.innerHTML = '<div class="msg-avatar">AI</div>';

  streamBubble = document.createElement('div');
  streamBubble.className = 'msg-bubble stream-cursor';
  row.appendChild(streamBubble);
  messages.appendChild(row);
  messages.scrollTop = messages.scrollHeight;

  flushTimer = setInterval(() => {
    if (!tokenBuffer) return;
    streamText += tokenBuffer;
    tokenBuffer = '';
    streamBubble.textContent = streamText;
    messages.scrollTop = messages.scrollHeight;
  }, 40);
}

function appendToken(token) {
  tokenBuffer += token;
}

function finalizeStreamBubble() {
  clearInterval(flushTimer);
  flushTimer = null;
  if (streamBubble) {
    streamText += tokenBuffer;
    tokenBuffer = '';
    streamBubble.textContent = streamText;
    streamBubble.classList.remove('stream-cursor');
    streamBubble = null;
    streamText = '';
  }
}

function showLiveBar(text) {
  liveBar.hidden = false;
  transcriptEl.textContent = text;
  addMessage('user', text);
  showThinkingDots();
}

function hideLiveBar() {
  liveBar.hidden = true;
  transcriptEl.textContent = '';
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ─── Camera ───────────────────────────────────────────────
async function toggleCamera() {
  if (!cameraActive) {
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } });
      cameraFeed.srcObject = cameraStream;
      document.getElementById('camera-wrap').classList.add('visible');
      camBtn.classList.add('active');
      cameraActive = true;
    } catch (err) { console.error('Camera error:', err); }
  } else {
    cameraStream?.getTracks().forEach(t => t.stop());
    document.getElementById('camera-wrap').classList.remove('visible');
    camBtn.classList.remove('active');
    cameraActive = false;
  }
}

function captureFrame() {
  const W = 320, H = 240;
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  canvas.getContext('2d').drawImage(cameraFeed, 0, 0, W, H);
  return canvas.toDataURL('image/jpeg', 0.55).split(',')[1];
}

// ─── Live2D loader ────────────────────────────────────────
async function tryLoadLive2D() {
  try {
    const res = await fetch(LIVE2D_MODEL, { method: 'HEAD' });
    if (!res.ok) return;

    const app2 = new PIXI.Application({
      view: document.getElementById('live2d-canvas'),
      width: 280, height: 340,
      backgroundAlpha: 0,
      antialias: true,
    });
    live2dModel = await PIXI.live2d.Live2DModel.from(LIVE2D_MODEL);
    app2.stage.addChild(live2dModel);
    live2dModel.scale.set(0.25);
    live2dModel.anchor.set(0.5, 0);
    live2dModel.position.set(140, 0);

    document.getElementById('live2d-canvas').style.display = 'block';
    document.getElementById('svg-char').style.display = 'none';
    console.log('✅ Live2D model loaded');
  } catch (err) {
    console.log('No Live2D model found, using SVG character.');
  }
}

// ─── PTT event binding ────────────────────────────────────
pttBtn.addEventListener('mousedown',  startRecording);
pttBtn.addEventListener('mouseup',    stopRecording);
pttBtn.addEventListener('mouseleave', stopRecording);
pttBtn.addEventListener('touchstart', e => { e.preventDefault(); startRecording(); }, { passive: false });
pttBtn.addEventListener('touchend',   stopRecording);

document.addEventListener('keydown', e => { if (e.code === 'Space' && !e.repeat) { e.preventDefault(); startRecording(); } });
document.addEventListener('keyup',   e => { if (e.code === 'Space') stopRecording(); });

camBtn.addEventListener('click', toggleCamera);

// ─── Text input ───────────────────────────────────────────
const textInput   = document.getElementById('text-input');
const sendBtn     = document.getElementById('send-btn');
const snapOverlay = document.getElementById('snap-overlay');
const snapImg     = document.getElementById('snap-img');
let heldSnapshot  = null;

function takeSnapshot() {
  if (!cameraActive) return;
  heldSnapshot = captureFrame();
  snapImg.src = 'data:image/jpeg;base64,' + heldSnapshot;
  snapOverlay.classList.add('active');
  textInput.focus();
}

function clearSnapshot() {
  heldSnapshot = null;
  snapOverlay.classList.remove('active');
  snapImg.src = '';
}

// Click live feed or frozen frame to snap
cameraFeed.addEventListener('click', takeSnapshot);
snapImg.addEventListener('click', takeSnapshot);

function sendTextMessage() {
  const text = textInput.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  textInput.value = '';
  addMessage('user', text);
  showThinkingDots();
  const payload = { type: 'text', text };
  // Use held snapshot first, fall back to live frame if camera active
  if (heldSnapshot) {
    payload.image = heldSnapshot;
    clearSnapshot();
  } else if (cameraActive) {
    payload.image = captureFrame();
  }
  ws.send(JSON.stringify(payload));
}

sendBtn.addEventListener('click', sendTextMessage);
textInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendTextMessage(); }
});

// Prevent Space from triggering PTT when typing in input
textInput.addEventListener('keydown', e => e.stopPropagation(), true);

// ─── Lucy canvas setup — auto-detects green mouth marker ─
function setupLucy() {
  const img = new Image();
  img.src = '/static/lucy.png';
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    lucyCanvas.width  = img.naturalWidth;
    lucyCanvas.height = img.naturalHeight;
    const ctx = lucyCanvas.getContext('2d');
    ctx.drawImage(img, 0, 0);

    const imageData = ctx.getImageData(0, 0, lucyCanvas.width, lucyCanvas.height);
    const d = imageData.data;
    let minX = Infinity, maxX = 0, minY = Infinity, maxY = 0, found = false;

    // Find green pixels (green channel dominant, low red & blue)
    for (let i = 0; i < d.length; i += 4) {
      const r = d[i], g = d[i+1], b = d[i+2], a = d[i+3];
      if (a > 100 && g > 120 && g > r * 1.5 && g > b * 1.5) {
        const idx = i / 4;
        const x = idx % lucyCanvas.width;
        const y = Math.floor(idx / lucyCanvas.width);
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minY = Math.min(minY, y); maxY = Math.max(maxY, y);
        // Replace green with skin colour
        d[i]   = 248; // R
        d[i+1] = 210; // G
        d[i+2] = 200; // B
        found = true;
      }
    }

    ctx.putImageData(imageData, 0, 0);

    if (found) {
      // Position mouth overlay using detected bounds (% of canvas size)
      const W = lucyCanvas.width, H = lucyCanvas.height;
      const cx  = ((minX + maxX) / 2) / W * 100;
      const cy  = minY / H * 100;
      const mw  = ((maxX - minX) * 0.9) / W * 100;
      const mh  = Math.max((maxY - minY) * 1.5, H * 0.018) / H * 100;

      mouthAnim.style.left   = `${cx - mw / 2}%`;
      mouthAnim.style.top    = `${cy}%`;
      mouthAnim.style.width  = `${mw}%`;
      mouthAnim.style.height = `${mh}%`;
      console.log(`Mouth detected at cx=${cx.toFixed(1)}% cy=${cy.toFixed(1)}% w=${mw.toFixed(1)}% h=${mh.toFixed(1)}%`);
    } else {
      console.warn('No green mouth marker found in lucy.png');
    }
  };
}

// ─── Boot ────────────────────────────────────────────────
connectWS();
tryLoadLive2D();
setupLucy();

// Startup greeting
setTimeout(() => {
  addMessage('ai', 'Neural link established. I\'m Lucy — hold the button or press Space to speak, or type below.');
}, 500);
