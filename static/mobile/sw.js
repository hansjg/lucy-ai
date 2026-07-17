// Lucy mobile PWA — service worker: receives pushes, renders the
// notification, and resolves consent requests right from the action
// buttons (no need to open the app).

self.addEventListener('push', event => {
  const data = event.data.json();
  event.waitUntil(self.registration.showNotification(data.title, {
    body: data.body,
    data,
    // A plain "you were auto-approved" notice has no requestId — it must
    // render with no actions, or the buttons appear with nothing to resolve.
    actions: data.requestId ? [
      { action: 'allow', title: 'Allow' },
      { action: 'deny', title: 'Deny' },
    ] : [],
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const { requestId } = event.notification.data || {};
  if (!requestId || !event.action) return;   // plain notice, or body tap
  event.waitUntil(fetch('/api/push/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request_id: requestId, allow: event.action === 'allow' }),
  }));
});
