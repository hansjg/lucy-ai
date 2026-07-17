// Lucy mobile PWA — subscribe flow + small dashboard.
// Person's name/slug comes from the URL (/mobile/<slug>), never typed —
// one link per person is the whole identity model here.

const slug = location.pathname.split('/').filter(Boolean).pop();

const enableSection = document.getElementById('m-enable');
const enableBtn      = document.getElementById('m-enable-btn');
const enableHint     = document.getElementById('m-enable-hint');
const pendingList     = document.getElementById('m-pending');
const nodesList       = document.getElementById('m-nodes');

let ownerName = null;   // resolved from /api/push/pending/<slug> the first time it loads

// Required, well-known Push API boilerplate: the VAPID key comes back as a
// base64url string, but pushManager.subscribe() needs the raw bytes. Do not
// hand-roll a different decode — browsers are strict about the exact length.
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

async function enableNotifications() {
  if (!ownerName) {
    enableHint.textContent = "couldn't identify this profile yet — try reloading";
    return;
  }
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    enableHint.textContent = 'this browser doesn\'t support push notifications';
    return;
  }
  try {
    const reg = await navigator.serviceWorker.register('/static/mobile/sw.js');
    const { key } = await fetch('/api/push/vapid-key').then(r => r.json());
    if (!key) {
      enableHint.textContent = 'Lucy has no VAPID key yet — generate one in settings first';
      return;
    }
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    });
    const res = await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: ownerName, subscription: sub.toJSON() }),
    }).then(r => r.json());
    if (res.ok) {
      enableBtn.textContent = 'Notifications on';
      enableBtn.disabled = true;
      enableHint.textContent = "you're all set — this phone will get consent requests instantly";
    } else {
      enableHint.textContent = res.error || 'subscribe failed';
    }
  } catch (e) {
    enableHint.textContent = `couldn't enable notifications: ${e.message || e}`;
  }
}

function renderPending(pending) {
  pendingList.innerHTML = pending.length
    ? pending.map(r => `<div class="m-row"><b>${r.requester}</b> wants '${r.resource}'
        <span class="m-faint">respond from the notification</span></div>`).join('')
    : '<div class="m-row m-faint">nothing waiting on you right now</div>';
}

function renderNodes(nodes) {
  nodesList.innerHTML = nodes.map(n => {
    const state = n.alive ? '<span class="m-ok">online</span>' : '<span class="m-faint">away</span>';
    return `<div class="m-row"><b>${n.label || n.name}</b> ${state}</div>`;
  }).join('');
}

async function refreshDashboard() {
  try {
    const [pendingRes, nodesRes] = await Promise.all([
      fetch(`/api/push/pending/${slug}`).then(r => r.json()),
      fetch('/api/nodes').then(r => r.json()),
    ]);
    if (pendingRes.name) ownerName = pendingRes.name;
    renderPending(pendingRes.pending || []);
    renderNodes(nodesRes.nodes || []);
  } catch (_) { /* server restarting */ }
}

enableBtn.addEventListener('click', enableNotifications);

if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
  enableSection.hidden = true;
}

refreshDashboard();
setInterval(refreshDashboard, 5000);
