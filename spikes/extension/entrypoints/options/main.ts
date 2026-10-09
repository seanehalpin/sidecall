const stateEl = document.getElementById('state')!;
const out = document.getElementById('out')!;

async function refresh() {
  const st = await navigator.permissions.query({ name: 'microphone' as PermissionName });
  stateEl.textContent = st.state;
  stateEl.dataset.state = st.state;
  st.onchange = () => {
    stateEl.textContent = st.state;
    stateEl.dataset.state = st.state;
  };
}

document.getElementById('grant')!.addEventListener('click', async () => {
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    const label = s.getAudioTracks()[0].label;
    s.getTracks().forEach((t) => t.stop());
    out.textContent = `Granted. Device: ${label}`;
  } catch (e) {
    const err = e as DOMException;
    out.textContent = `Failed: ${err.name}: ${err.message}`;
  }
  await refresh();
});

refresh();
