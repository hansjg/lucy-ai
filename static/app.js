// ═══════════════════════════════════════════════════════════
// LUCY — light prism UI
// Pose-driven character, floating droplet chat, camera mode.
// ═══════════════════════════════════════════════════════════

// ─── State ───────────────────────────────────────────────
let ws, audioCtx, workletNode, sourceNode, micStream, chunks = [], isRecording = false;
let micReady = false;
let cameraStream = null, cameraActive = false;
let heldSnapshot = null;
let detectActive = false, lastDetections = [];

// ─── Conversation modality ───────────────────────────────
// Answering Lucy's follow-up shouldn't need another "hey lucy" — but only
// when you're actually talking. If you typed, opening the mic would be rude
// (and would record the room while you're reading her question).
let lastInputWasVoice = false;   // how the last message reached her
let expectingAnswer = false;     // her last reply was a question
let autoListenTimer = null;      // bail-out when you say nothing
const AUTO_LISTEN_SILENCE_MS = 7000;

// ─── DOM refs ────────────────────────────────────────────
const statusDot  = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const messages   = document.getElementById('messages');
const cameraFeed = document.getElementById('camera-feed');
const snapOverlay = document.getElementById('snap-overlay');
const snapImg    = document.getElementById('snap-img');
const snapClear  = document.getElementById('snap-clear');
const textInput  = document.getElementById('text-input');
const sendBtn    = document.getElementById('send-btn');
const pttBtn     = document.getElementById('ptt-btn');
const camBtn     = document.getElementById('cam-btn');
const camDroplet = document.getElementById('cam-droplet');
const camCaption = document.getElementById('cam-caption');
const detectOverlay = document.getElementById('detect-overlay');
const detectQuery   = document.getElementById('detect-query');
const detectBtn     = document.getElementById('detect-btn');
const detectStats   = document.getElementById('detect-stats');
const lucySprite = document.getElementById('lucy-sprite');
const lucyBubble = document.getElementById('lucy-bubble');
const lucyBubbleText = document.getElementById('lucy-bubble-text');
const poseA = document.getElementById('pose-a');
const poseB = document.getElementById('pose-b');

// ═══ Pose engine ═════════════════════════════════════════
// static poses: base | wave | point | excited | shush | thumbs
// animated clips (transparent webm) override: wave | point | shush
const POSE_SRC = p => `/static/poses/${p}.png`;
const poseVideo = document.getElementById('pose-video');
const CLIP_URLS = {
  wave:  '/static/clips/wave.webm',   // waving → smiling idle (play once)
  point: '/static/clips/point.webm',  // idle → pointing while talking → idle (loops while speaking)
  shush: '/static/clips/shush.webm',  // idle → shh → idle (play once, on wake word)
};
const clipBlobs = {};   // pose → blob URL once preloaded
let activeLayer = poseA;   // the visible png layer
let hiddenLayer = poseB;
let currentPose = 'wave';

// Preload clips into blob URLs so pose swaps never hit the network
(async () => {
  for (const [pose, url] of Object.entries(CLIP_URLS)) {
    try {
      const res = await fetch(url);
      if (res.ok) clipBlobs[pose] = URL.createObjectURL(await res.blob());
    } catch (_) { /* clip stays png-only */ }
  }
})();

function showPng(pose) {
  lucySprite.classList.remove('video-active');
  poseVideo.pause();
  hiddenLayer.src = POSE_SRC(pose);
  const swap = () => {
    hiddenLayer.style.opacity = 1;
    activeLayer.style.opacity = 0;
    [activeLayer, hiddenLayer] = [hiddenLayer, activeLayer];
  };
  if (hiddenLayer.complete) swap();
  else hiddenLayer.onload = swap;
}

function setPose(pose, { mirror = false } = {}) {
  lucySprite.classList.toggle('mirror', mirror);
  if (pose === currentPose) return;
  currentPose = pose;

  const clip = clipBlobs[pose];
  if (clip) {
    // park the idle png underneath so the fade-out lands on something sane
    activeLayer.src = POSE_SRC('base');
    poseVideo.src = clip;
    poseVideo.loop = pose === 'point';          // only the talking clip loops
    poseVideo.onended = pose === 'point' ? null : () => {
      if (currentPose !== pose) return;         // state already moved on
      if (pose === 'shush') setPose(isRecording ? 'excited' : 'base');
      else setPose('base');                     // wave settles back to idle
    };
    poseVideo.currentTime = 0;
    poseVideo.play().then(() => {
      lucySprite.classList.add('video-active');
    }).catch(() => showPng(pose));              // autoplay refused → png fallback
  } else {
    showPng(pose);
  }
}

// Lucy points TOWARD the chat/camera: mirrored on the left, natural on the right
function speakingMirror() { return !document.body.classList.contains('camera-mode'); }

// ═══ Lucy quip bubble ════════════════════════════════════
let quipTimer = null;
function quip(text, ms = 2600) {
  lucyBubbleText.textContent = text;
  lucyBubble.classList.add('show');
  clearTimeout(quipTimer);
  if (ms) quipTimer = setTimeout(() => lucyBubble.classList.remove('show'), ms);
}
function hideQuip() { clearTimeout(quipTimer); lucyBubble.classList.remove('show'); }

// ═══ Character state machine ═════════════════════════════
function setState(state) {
  const labels = { idle: 'online', listening: 'listening…', thinking: 'thinking…', speaking: 'speaking', off: 'offline' };
  statusDot.className = state === 'idle' ? '' : state;
  statusText.textContent = labels[state] ?? state;

  lucySprite.classList.toggle('talking', state === 'speaking');

  switch (state) {
    case 'listening': setPose('excited'); break;
    case 'thinking':  setPose('base');    break;  // calm idle — dots + chip say she's thinking
    case 'speaking':  setPose('point', { mirror: speakingMirror() }); break;
    default:          setPose('base');
  }
}

// ─── WebSocket ───────────────────────────────────────────
function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen  = () => setState('idle');
  ws.onclose = () => { setState('off'); setTimeout(connectWS, 2000); };
  ws.onerror = () => ws.close();

  ws.onmessage = async ({ data }) => {
    const msg = JSON.parse(data);

    if (msg.type === 'status') {
      if (msg.state === 'thinking') { setState('thinking'); }
      else if (msg.state !== 'speaking') setState(msg.state === 'idle' && audioPlaying ? 'speaking' : msg.state);
    }
    else if (msg.type === 'transcript') {
      addMessage('user', msg.text, msg.speaker ? `you · ${msg.speaker}` : null);
      showThinkingDots();
    }
    else if (msg.type === 'reply_start') {
      speechAborted = false;   // new reply — allow its audio to play
      beginStreamBubble();
    }
    else if (msg.type === 'token') {
      appendToken(msg.text);
    }
    else if (msg.type === 'reply_end') {
      finalizeStreamBubble();
      // Questions get answered by mic if the user is speaking; drainQueue
      // acts on this once she's actually finished saying it.
      expectingAnswer = !!msg.expect_answer;
      if (expectingAnswer && lastInputWasVoice && !audioPlaying && !isRecording) {
        expectingAnswer = false;
        startRecording({ auto: true });   // no TTS queued (muted) — listen now
      }
    }
    else if (msg.type === 'audio_chunk') {
      enqueueAudio(msg.data);
    }
    else if (msg.type === 'task_note') {
      upsertTaskNote(msg.task_id, msg.text);
    }
    else if (msg.type === 'task_progress') {
      const pct = msg.total ? Math.round(msg.done * 100 / msg.total) : 0;
      const bar = '#'.repeat(Math.round(pct / 5)).padEnd(20, '-');
      upsertTaskNote(msg.task_id, `receiving on ${msg.node}… [${bar}] ${pct}%`);
    }
    else if (msg.type === 'wake') {
      console.log('wake received — micReady:', micReady);
      stopSpeaking(true);                 // cut off speech AND any turn still thinking
      if (!isRecording) {
        quip("shh — i'm listening!", 2200);
        startRecording();                 // sets listening state…
        setPose('shush');                 // …then the shh clip takes the stage
        // generous window; VAD still stops early the moment you finish talking
        setTimeout(() => { if (isRecording) stopRecording(); }, 8000);
      }
    }
    else if (msg.type === 'error') {
      finalizeStreamBubble();
      removeThinkingDots();
      addMessage('ai', `something glitched — ${msg.message}`);
      setState('idle');
    }
  };
}

// ─── Audio recording + VAD ───────────────────────────────
async function initMic() {
  if (micReady) return true;
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 16000, channelCount: 1 } });
    micReady = true;
    return true;
  } catch (err) {
    console.error('Mic init error:', err);
    return false;
  }
}

async function startRecording({ auto = false } = {}) {
  if (isRecording) return;
  // An auto-listen must never interrupt her — it only ever runs once she has
  // finished the question. A human press still takes priority over anything.
  if (!auto) stopSpeaking(true);
  if (!micReady) {
    const ok = await initMic();
    if (!ok) {
      if (!auto) quip("i can't reach the mic — click anywhere once, then try again!", 3200);
      return;
    }
  }
  isRecording = true;
  chunks = [];
  pttBtn.classList.add('active');
  textInput.placeholder = 'listening…';
  setState('listening');

  try {
    audioCtx = new AudioContext({ sampleRate: 16000 });
    // Wake-word starts arrive with no user gesture — the browser may create
    // the context suspended, which silently records nothing. Resume it
    // (allowed while mic capture is active) and bail loudly if it stays dead.
    if (audioCtx.state === 'suspended') {
      try { await audioCtx.resume(); } catch (_) {}
    }
    if (audioCtx.state !== 'running') {
      console.warn('AudioContext state:', audioCtx.state, '— cannot record');
      quip('click anywhere once so the browser lets me hear you!', 3200);
      stopRecording();
      return;
    }
    await audioCtx.audioWorklet.addModule('/static/recorder-worklet.js');
    sourceNode  = audioCtx.createMediaStreamSource(micStream);
    workletNode = new AudioWorkletNode(audioCtx, 'recorder-processor');

    let silenceStart = null;
    const SILENCE_THRESHOLD = 0.01;
    const SILENCE_DURATION  = 1500;
    const MIN_SPEECH_MS     = 400;
    let speechStarted = false;
    let speechTimer = Date.now();

    // The VAD below only closes a recording once speech has STARTED. On an
    // auto-listen nobody may answer at all, so without this the mic would
    // sit open on the room forever — exactly what "if no answer, off the
    // mic" asks us not to do.
    if (auto) {
      clearTimeout(autoListenTimer);
      autoListenTimer = setTimeout(() => {
        if (isRecording && !speechStarted) {
          chunks = [];            // nothing said — send nothing
          stopRecording();
        }
      }, AUTO_LISTEN_SILENCE_MS);
    }

    workletNode.port.onmessage = ({ data }) => {
      if (data && typeof data === 'object' && 'rms' in data) {
        const silent = data.rms < SILENCE_THRESHOLD;
        if (!silent) { speechStarted = true; silenceStart = null; }
        else if (speechStarted) {
          if (!silenceStart) silenceStart = Date.now();
          if (Date.now() - silenceStart > SILENCE_DURATION &&
              Date.now() - speechTimer > MIN_SPEECH_MS) {
            stopRecording();
          }
        }
      } else if (data instanceof Float32Array) {
        chunks.push(data);
      }
    };
    sourceNode.connect(workletNode);
  } catch (err) {
    console.error('Recording error:', err);
    stopRecording();
  }
}

async function stopRecording() {
  if (!isRecording) return;
  isRecording = false;
  clearTimeout(autoListenTimer);
  pttBtn.classList.remove('active');
  textInput.placeholder = 'say something to lucy…';

  sourceNode?.disconnect();
  workletNode?.disconnect();
  audioCtx?.close().catch(() => {});

  if (chunks.length < 4) { setState('idle'); return; }

  const wav = encodeWAV(chunks, 16000);
  const b64 = arrayBufferToBase64(wav);

  let imageB64 = null;
  if (heldSnapshot) {
    imageB64 = heldSnapshot;
    clearSnapshot();
  } else if (cameraActive) {
    imageB64 = captureFrame();
  }

  lastInputWasVoice = true;   // she may reopen the mic for her follow-up
  const payload = { type: 'audio', data: b64 };
  if (imageB64) payload.image = imageB64;
  ws?.send(JSON.stringify(attachContextFile(payload)));
}

// ─── WAV encoder ─────────────────────────────────────────
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
  view.setUint16(20, 1,  true);
  view.setUint16(22, 1,  true);
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

// ─── Audio queue (interruptible) ─────────────────────────
const audioQueue = [];
let audioPlaying = false;
let currentAudio = null;
let speechAborted = false;
let lastFullReply = '';          // last completed spoken reply
let lastInterruptedReply = null; // captured when cut off mid-speech, for resume offer

function enqueueAudio(b64) {
  if (speechAborted) return;     // drop leftover chunks from an interrupted reply
  audioQueue.push(b64);
  if (!audioPlaying) drainQueue();
}

// Cut Lucy off immediately (wake word or user starts talking). Remembers
// what she was saying so she can offer to resume it afterward, kills local
// playback, AND tells the server to cancel the in-flight reply so it stops
// generating and synthesizing the rest.
function stopSpeaking(force = false) {
  // Only a reply cut off MID-GENERATION is resume-worthy. A finished reply
  // whose audio tail gets cut is already fully readable in the chat —
  // capturing it caused hallucinated "want me to continue…" offers.
  const partial = streamBubble ? streamBubble.textContent : null;
  const wasBusy = audioPlaying || !!streamBubble;
  if (partial && partial.trim().length > 20) {
    lastInterruptedReply = partial.trim();
  }
  speechAborted = true;
  audioQueue.length = 0;
  if (currentAudio) {
    try { currentAudio.pause(); currentAudio.src = ''; } catch (_) {}
    currentAudio = null;
  }
  audioPlaying = false;
  finalizeStreamBubble();   // close the half-written bubble
  // force=true (wake word / user starts talking): cancel the server turn even
  // if she hasn't started speaking yet — she may still be in the thinking phase
  if ((wasBusy || force) && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'interrupt' }));   // cancel server-side turn
  }
}

async function drainQueue() {
  audioPlaying = true;
  setState('speaking');
  while (audioQueue.length > 0 && !speechAborted) {
    await playAudio(audioQueue.shift());
  }
  audioPlaying = false;
  if (speechAborted) { setState('idle'); return; }

  // She just finished asking something and you're in a spoken conversation:
  // open the mic yourself rather than making them say "hey lucy" again.
  if (expectingAnswer && lastInputWasVoice && !isRecording) {
    expectingAnswer = false;
    setPose('base');
    startRecording({ auto: true });
    return;
  }
  // occasional thumbs-up flourish when she finishes talking
  if (Math.random() < 0.25) {
    setPose('thumbs');
    setTimeout(() => setState('idle'), 1100);
    statusDot.className = '';
    statusText.textContent = 'online';
    lucySprite.classList.remove('talking');
  } else {
    setState('idle');
  }
}

async function playAudio(b64mp3) {
  if (speechAborted) return;
  const bytes = Uint8Array.from(atob(b64mp3), c => c.charCodeAt(0));
  const blob  = new Blob([bytes], { type: 'audio/mp3' });
  const url   = URL.createObjectURL(blob);
  const audio = new Audio(url);
  currentAudio = audio;
  return new Promise(resolve => {
    audio.onended = () => { URL.revokeObjectURL(url); if (currentAudio === audio) currentAudio = null; resolve(); };
    audio.onerror = resolve;
    audio.onpause = resolve;   // interrupt calls pause() — don't leave drainQueue hanging
    audio.play().catch(resolve);
  });
}

// ─── Chat messages ────────────────────────────────────────
function addMessage(role, text, label = null) {
  const row = document.createElement('div');
  row.className = `msg-row ${role}`;
  if (role === 'ai' || label) {
    const lab = document.createElement('div');
    lab.className = 'msg-label';
    lab.textContent = role === 'ai' ? 'lucy' : label;
    row.appendChild(lab);
  }
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
    <div class="msg-label">lucy</div>
    <div class="msg-bubble bubble-dots"><span></span><span></span><span></span></div>`;
  messages.appendChild(thinkingRow);
  messages.scrollTop = messages.scrollHeight;
}

function removeThinkingDots() {
  if (!thinkingRow) return;
  thinkingRow.style.transition = 'opacity 0.15s ease';
  thinkingRow.style.opacity = '0';
  const r = thinkingRow;
  thinkingRow = null;
  setTimeout(() => r.remove(), 150);
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
  row.innerHTML = '<div class="msg-label">lucy</div>';

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
    lastFullReply = streamText;   // remember for interrupt-resume
    streamBubble = null;
    streamText = '';
  }
}

// ─── Camera ───────────────────────────────────────────────
async function toggleCamera() {
  if (!cameraActive) {
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } });
      cameraFeed.srcObject = cameraStream;
      cameraActive = true;
      camBtn.classList.add('active');
      document.body.classList.add('camera-mode');
      setPose('point'); // she's sliding right — point at the camera
      setTimeout(() => { if (!isRecording && !audioPlaying) setPose('base'); }, 2200);
      toggleDetection();   // detection IS the camera now — start scanning right away
    } catch (err) { console.error('Camera error:', err); }
  } else {
    stopDetection({ release: true });   // camera gone — free the VRAM too
    cameraStream?.getTracks().forEach(t => t.stop());
    cameraActive = false;
    camBtn.classList.remove('active');
    document.body.classList.remove('camera-mode');
    clearSnapshot();
    hideQuip();
  }
}

function captureFrame() {
  const W = 320, H = 240;
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  canvas.getContext('2d').drawImage(cameraFeed, 0, 0, W, H);
  return canvas.toDataURL('image/jpeg', 0.55).split(',')[1];
}

function takeSnapshot() {
  if (!cameraActive) return;
  heldSnapshot = captureFrame();
  snapImg.src = 'data:image/jpeg;base64,' + heldSnapshot;
  snapOverlay.classList.add('active');
  quip('got it — ask me about it!', 2400);
  textInput.focus();
}

function clearSnapshot() {
  heldSnapshot = null;
  snapOverlay.classList.remove('active');
  snapImg.src = '';
}

cameraFeed.addEventListener('click', takeSnapshot);
snapImg.addEventListener('click', takeSnapshot);
snapClear.addEventListener('click', e => { e.stopPropagation(); clearSnapshot(); });
camBtn.addEventListener('click', toggleCamera);

// ─── Live detection (LocateAnything-3B) ───────────────────
// One frame in flight at a time: the loop awaits each result before grabbing
// the next frame, so the GPU sets the pace and requests never pile up.
const detectSleep = ms => new Promise(r => setTimeout(r, ms));

// Hue per label, stable across frames so boxes don't flicker colors
function labelHue(label) {
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) % 360;
  return h;
}

// Capture exactly the 4:3 region object-fit:cover displays, so the model's
// coordinates map 1:1 onto the overlay with no letterbox math later.
function captureDetectFrame() {
  const vw = cameraFeed.videoWidth, vh = cameraFeed.videoHeight;
  if (!vw || !vh) return null;
  const AR = 4 / 3;
  let sx = 0, sy = 0, sw = vw, sh = vh;
  if (vw / vh > AR) { sw = vh * AR; sx = (vw - sw) / 2; }
  else              { sh = vw / AR; sy = (vh - sh) / 2; }
  const canvas = document.createElement('canvas');
  canvas.width = 512; canvas.height = 384;
  canvas.getContext('2d').drawImage(cameraFeed, sx, sy, sw, sh, 0, 0, 512, 384);
  return canvas.toDataURL('image/jpeg', 0.8).split(',')[1];
}

function clearDetectOverlay() {
  detectOverlay.getContext('2d').clearRect(0, 0, detectOverlay.width, detectOverlay.height);
}

function drawDetections(objects, points) {
  const dpr = window.devicePixelRatio || 1;
  const w = detectOverlay.clientWidth, h = detectOverlay.clientHeight;
  detectOverlay.width = w * dpr; detectOverlay.height = h * dpr;
  const ctx = detectOverlay.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.font = '600 11px "JetBrains Mono", monospace';
  ctx.lineWidth = 2;

  for (const o of objects) {
    const hue = labelHue(o.label);
    const [x1, y1, x2, y2] = o.box;
    const x = x1 * w, y = y1 * h, bw = (x2 - x1) * w, bh = (y2 - y1) * h;
    ctx.strokeStyle = `hsl(${hue} 90% 62%)`;
    ctx.shadowColor = `hsla(${hue}, 90%, 55%, 0.55)`;
    ctx.shadowBlur = 8;
    ctx.beginPath();
    ctx.roundRect(x, y, bw, bh, 8);
    ctx.stroke();
    ctx.shadowBlur = 0;
    // label chip — above the box, or tucked inside when at the top edge
    const tw = ctx.measureText(o.label).width + 12;
    const ty = y > 20 ? y - 19 : y + 3;
    ctx.fillStyle = `hsla(${hue}, 75%, 22%, 0.82)`;
    ctx.beginPath();
    ctx.roundRect(x, ty, tw, 16, 8);
    ctx.fill();
    ctx.fillStyle = `hsl(${hue} 95% 85%)`;
    ctx.fillText(o.label, x + 6, ty + 12);
  }
  for (const p of points || []) {
    const hue = labelHue(p.label), x = p.x * w, y = p.y * h;
    ctx.strokeStyle = `hsl(${hue} 90% 62%)`;
    ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = `hsl(${hue} 90% 62%)`;
    ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill();
  }
}

async function detectLoop() {
  let announced = false;
  while (detectActive && cameraActive) {
    const frame = captureDetectFrame();
    if (!frame) { await detectSleep(250); continue; }
    let data;
    try {
      const res = await fetch('/api/vision/detect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: frame, query: detectQuery.value.trim() }),
      });
      data = await res.json();
    } catch {
      detectStats.textContent = 'server offline';
      await detectSleep(2000);
      continue;
    }
    if (!detectActive) break;

    if (data.state === 'loading') {
      detectStats.textContent = 'warming up model…';
      await detectSleep(1500);
      continue;
    }
    if (!data.ok) {
      detectStats.textContent = 'vision error';
      console.error('vision.locate:', data.error);
      await detectSleep(2500);
      continue;
    }
    lastDetections = data.objects;
    drawDetections(data.objects, data.points);
    const n = data.objects.length + (data.points?.length || 0);
    detectStats.textContent = `${n} found · ${(data.ms / 1000).toFixed(1)}s`;
    if (!announced && n > 0) { announced = true; quip('I can see things now!', 2200); }
  }
  clearDetectOverlay();
}

function stopDetection({ release = false } = {}) {
  if (!detectActive && !release) return;
  detectActive = false;
  lastDetections = [];
  detectBtn.classList.remove('active');
  camDroplet.classList.remove('detecting');
  camCaption.textContent = 'camera · on-device';
  detectStats.textContent = '';
  clearDetectOverlay();
  if (release) fetch('/api/vision/release', { method: 'POST' }).catch(() => {});
}

async function toggleDetection() {
  if (detectActive) { stopDetection(); return; }
  if (!cameraActive) return;
  // a sticky load error stays until released — clear it so retry means retry
  try {
    const st = await (await fetch('/api/vision/status')).json();
    if (st.state === 'error') await fetch('/api/vision/release', { method: 'POST' });
  } catch { /* detect loop will surface connectivity problems */ }
  detectActive = true;
  detectBtn.classList.add('active');
  camDroplet.classList.add('detecting');
  camCaption.textContent = 'locateanything-3b · live';
  detectStats.textContent = 'warming up model…';
  quip('scanning… show me stuff!', 2400);
  detectLoop();
}

detectBtn.addEventListener('click', toggleDetection);
// typing in the query box must never trigger Space-PTT or global Enter
detectQuery.addEventListener('keydown', e => e.stopPropagation(), true);

// ─── Text input ───────────────────────────────────────────
function sendTextMessage() {
  const text = textInput.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  stopSpeaking();   // a new question supersedes whatever she's still saying
  // Typing means you're at the keyboard — she must not open the mic on her
  // follow-up. Any pending auto-listen from an earlier spoken turn is off.
  lastInputWasVoice = false;
  expectingAnswer = false;
  clearTimeout(autoListenTimer);
  if (isRecording) stopRecording();
  textInput.value = '';
  addMessage('user', text);
  showThinkingDots();
  setState('thinking');
  const payload = { type: 'text', text };
  if (heldSnapshot) {
    payload.image = heldSnapshot;
    clearSnapshot();
  } else if (cameraActive) {
    payload.image = captureFrame();
  }
  ws.send(JSON.stringify(attachContextFile(payload)));
  textInput.focus();   // keep the conversation flowing — Enter always works
}

sendBtn.addEventListener('click', sendTextMessage);
textInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendTextMessage(); }
});
// Don't let Space PTT fire while typing
textInput.addEventListener('keydown', e => e.stopPropagation(), true);

// ─── PTT bindings ─────────────────────────────────────────
document.addEventListener('click', () => { if (!micReady) initMic(); }, { once: true });

pttBtn.addEventListener('mousedown',  startRecording);
pttBtn.addEventListener('mouseup',    stopRecording);
pttBtn.addEventListener('mouseleave', stopRecording);
pttBtn.addEventListener('touchstart', e => { e.preventDefault(); startRecording(); }, { passive: false });
pttBtn.addEventListener('touchend',   stopRecording);

document.addEventListener('keydown', e => { if (e.code === 'Space' && !e.repeat) { e.preventDefault(); startRecording(); } });
document.addEventListener('keyup',   e => { if (e.code === 'Space') stopRecording(); });

// Enter works from anywhere on the page — sends if there's text,
// otherwise just focuses the input
document.addEventListener('keydown', e => {
  if (e.key !== 'Enter' || e.repeat || document.activeElement === textInput) return;
  e.preventDefault();
  if (textInput.value.trim()) sendTextMessage();
  else textInput.focus();
});

// Start typing anywhere → characters go to the input (Space stays PTT)
document.addEventListener('keydown', e => {
  if (document.activeElement === textInput) return;
  if (e.key.length === 1 && e.key !== ' ' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    textInput.focus();
  }
});

// ─── Lucy interactions ────────────────────────────────────
const CLICK_QUIPS = [
  'hehe, hi!',
  'need something?',
  "that tickles!",
  "i'm all ears ✳",
  'say "hey lucy" anytime!',
];
lucySprite.addEventListener('click', () => {
  if (isRecording || audioPlaying) return;
  setPose('wave');
  quip(CLICK_QUIPS[Math.floor(Math.random() * CLICK_QUIPS.length)], 2200);
  setTimeout(() => { if (currentPose === 'wave') setPose('base'); }, 1900);
});

// Gentle parallax on mouse move
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if (!reduceMotion) {
  const lucyFloat = document.getElementById('lucy-float');
  document.addEventListener('mousemove', e => {
    const nx = (e.clientX / window.innerWidth) - 0.5;
    const ny = (e.clientY / window.innerHeight) - 0.5;
    lucyFloat.style.rotate = `${nx * 1.6}deg`;
    lucyFloat.style.translate = `${nx * 8}px ${ny * 4}px`;
  });
}

// ─── Task notes & file sharing (drop, paste, "send this") ─
const taskNoteEls = {};   // task_id -> DOM element, so progress updates in place

function upsertTaskNote(taskId, text) {
  let el = taskId ? taskNoteEls[taskId] : null;
  if (!el) {
    el = document.createElement('div');
    el.className = 'task-note';
    messages.appendChild(el);
    if (taskId) taskNoteEls[taskId] = el;
  }
  el.textContent = '⚙ ' + text;
  messages.scrollTop = messages.scrollHeight;
}

let lastUploadedFileName = null;
let lastUploadedAt = 0;
const CONTEXT_FILE_TTL_MS = 10 * 60 * 1000;   // "send this" stays valid for 10 min

async function uploadSharedFile(f) {
  upsertTaskNote(null, `uploading ${f.name}…`);
  try {
    const res = await fetch('/api/files/upload?name=' + encodeURIComponent(f.name),
                            { method: 'POST', body: f });
    const j = await res.json();
    if (j.ok) {
      lastUploadedFileName = j.name;
      lastUploadedAt = Date.now();
      upsertTaskNote(null, `${j.name} ready (${(j.bytes / 1e6).toFixed(1)} MB) — say "send this to <device>"`);
      quip('got the file!', 2200);
    } else {
      upsertTaskNote(null, `upload failed: ${j.error}`);
    }
  } catch (err) {
    upsertTaskNote(null, 'upload failed: ' + err.message);
  }
}

// Attach the recently-uploaded file as context so "send this" resolves it
function attachContextFile(payload) {
  if (lastUploadedFileName && (Date.now() - lastUploadedAt) < CONTEXT_FILE_TTL_MS) {
    payload.contextFile = lastUploadedFileName;
  }
  if (lastInterruptedReply) {
    payload.resumeContext = lastInterruptedReply;
    lastInterruptedReply = null;   // offer to resume only once
  }
  return payload;
}

// Drag a file anywhere onto Lucy → it lands in the core shared folder
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', async e => {
  e.preventDefault();
  const f = e.dataTransfer?.files?.[0];
  if (f) await uploadSharedFile(f);
});

// Paste (Ctrl+V) a file anywhere → same as dropping it
document.addEventListener('paste', async e => {
  const f = [...(e.clipboardData?.files || [])][0];
  if (!f) return;
  e.preventDefault();
  await uploadSharedFile(f);
});

// ─── Nodes & task log panel ───────────────────────────────
const nodesBtn  = document.getElementById('nodes-btn');
const taskPanel = document.getElementById('task-panel');
const tpNodes   = document.getElementById('tp-nodes');
const tpTasks   = document.getElementById('tp-tasks');
let tpTimer = null;

async function refreshTaskPanel() {
  try {
    const [nodesRes, tasksRes, filesRes, pairRes] = await Promise.all([
      fetch('/api/nodes').then(r => r.json()),
      fetch('/api/tasks').then(r => r.json()),
      fetch('/api/files').then(r => r.json()),
      fetch('/api/pairing').then(r => r.json()),
    ]);
    const tpPairing = document.getElementById('tp-pairing');
    tpPairing.innerHTML = `<div class="tp-row tp-pairing-cmd" title="click to copy — paste into PowerShell on the new device">${pairRes.command}</div>`;
    tpNodes.innerHTML = nodesRes.nodes.map(n => {
      const state = n.alive ? '<span class="tp-ok">online</span>' : '<span class="tp-away">away</span>';
      const s = n.stats || {};
      const load = 'cpu' in s ? ` · cpu ${Math.round(s.cpu)}%` : '';
      const batt = 'battery' in s ? ` · batt ${Math.round(s.battery)}%${s.on_ac ? '⚡' : ''}` : '';
      const agent = n.agent ? ` · v${n.agent}` : '';
      return `<div class="tp-row"><b>${n.label || n.name}</b> ${state}${load}${batt}${agent}<br>
              <span class="tp-caps">${n.capabilities.join(' · ') || 'no capabilities'}</span></div>`;
    }).join('');
    const tpFiles = document.getElementById('tp-files');
    tpFiles.innerHTML = filesRes.files.length ? filesRes.files.map(f =>
      `<div class="tp-row"><b>${f.name}</b> <span class="tp-caps">${(f.bytes / 1e6).toFixed(1)} MB</span></div>`
    ).join('') : '<div class="tp-row tp-caps">drop a file anywhere to share it</div>';
    tpTasks.innerHTML = tasksRes.tasks.length ? tasksRes.tasks.map(t => {
      const st = t.state === 'done' ? '<span class="tp-ok">done</span>'
               : t.state === 'failed' ? '<span class="tp-fail">failed</span>' : t.state;
      const ms = t.duration_ms != null ? ` · ${t.duration_ms}ms` : '';
      const time = (t.created || '').slice(11, 19);
      return `<div class="tp-row"><b>${t.capability}.${t.action}</b> → ${t.node} · ${st}${ms}<br>
              <span class="tp-caps">${time}${t.error ? ' · ' + t.error : ''}</span></div>`;
    }).join('') : '<div class="tp-row tp-caps">no tasks yet — try /task monitoring.system read_stats</div>';
  } catch (_) { /* server restarting */ }
}

nodesBtn.addEventListener('click', () => {
  const open = taskPanel.hidden;
  taskPanel.hidden = !open;
  nodesBtn.classList.toggle('open', open);
  clearInterval(tpTimer);
  if (open) {
    closeSettingsPanel();   // the two panels share the corner — one at a time
    refreshTaskPanel();
    tpTimer = setInterval(refreshTaskPanel, 3000);
  }
});

document.getElementById('tp-pairing').addEventListener('click', e => {
  const row = e.target.closest('.tp-pairing-cmd');
  if (!row) return;
  navigator.clipboard?.writeText(row.textContent).then(() => quip('pairing command copied!', 2000));
});

// ─── Settings panel ───────────────────────────────────────
const settingsBtn   = document.getElementById('settings-btn');
const settingsPanel = document.getElementById('settings-panel');
const setConnect    = document.getElementById('set-connect');
const setTarget     = document.getElementById('set-target');
const setDevicesHint = document.getElementById('set-devices-hint');

function renderSettings(res) {
  setConnect.checked = !!res.settings.connect_devices;
  const devs = res.devices || [];
  setTarget.innerHTML = '<option value="">auto</option>' + devs.map(d =>
    `<option value="${d.name}">${d.label || d.name}${d.online ? ' · online' : d.wakeable ? '' : ' · re-pair to enable'}</option>`
  ).join('');
  setTarget.value = res.settings.connect_target || '';
  setDevicesHint.textContent = devs.length
    ? `${devs.length} known device${devs.length > 1 ? 's' : ''}`
    : 'no devices paired yet — see the nodes panel';
}

async function loadSettings() {
  try { renderSettings(await fetch('/api/settings').then(r => r.json())); }
  catch (_) { /* server restarting */ }
}

async function saveSettings() {
  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ connect_devices: setConnect.checked, connect_target: setTarget.value }),
    }).then(r => r.json());
    renderSettings(res);
  } catch (_) {}
}

setConnect.addEventListener('change', saveSettings);
setTarget.addEventListener('change', saveSettings);

function closeSettingsPanel() {
  settingsPanel.hidden = true;
  settingsBtn.classList.remove('open');
}

settingsBtn.addEventListener('click', () => {
  const open = settingsPanel.hidden;
  settingsPanel.hidden = !open;
  settingsBtn.classList.toggle('open', open);
  if (open) {
    loadSettings();
    taskPanel.hidden = true;
    nodesBtn.classList.remove('open');
    clearInterval(tpTimer);
  }
});

// ─── Boot ────────────────────────────────────────────────
connectWS();
setState('idle');
setPose('wave');
quip("hey! i'm lucy ✳", 3200);
setTimeout(() => { if (currentPose === 'wave') setPose('base'); }, 2600);
setTimeout(() => {
  addMessage('ai', "hey, i'm lucy! talk to me — hold the mic, hold Space, type, or just say \"hey lucy\".");
}, 600);
