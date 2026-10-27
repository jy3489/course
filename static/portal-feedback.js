/* Cosmetic feedback never changes match state or consumes ctx.random. */
(function (root) {
  'use strict';
  function createTracker() {
    let previous = null;
    return (match, user, options = {}) => {
      const next = match && {id: match.id, user, count: match.move_count, status: match.status, turn: options.yourTurn, suppressed: options.suppressed, at: Date.now()};
      const old = previous;
      previous = next;
      if (!old || !next || old.id !== next.id || old.user !== user || options.suppressed || old.suppressed || next.count < old.count || next.at - old.at > 30000) return null;
      if (old.status === 'waiting_for_players' && next.status === 'ongoing') return 'start';
      if (old.status !== 'over' && next.status === 'over') return ['win', 'draw', 'loss'].includes(options.outcome) ? options.outcome : null;
      if (next.status === 'ongoing' && !old.turn && next.turn && next.count > old.count) return 'turn';
      return null;
    };
  }
  if (typeof module !== 'undefined') module.exports = {createTracker};
  if (!root.document) return;
  let track = createTracker();
  for (const event of ["offline", "online"]) root.addEventListener(event, () => { track = createTracker(); });
  document.addEventListener("visibilitychange", () => { track = createTracker(); });
  let enabled = false, context;
  try { enabled = localStorage.getItem('portal-sounds') === 'on'; } catch {}
  const tones = {start: [392,523], turn: [523,659], win: [523,659,784], draw: [440,440], loss: [392,330]};
  function unlock() {
    if (!enabled) return;
    const Audio = root.AudioContext || root.webkitAudioContext;
    if (!Audio) return;
    try { context ||= new Audio(); context.resume().catch(() => {}); } catch {}
  }
  document.addEventListener('pointerdown', unlock, {passive:true});
  document.addEventListener('keydown', unlock);
  document.addEventListener('DOMContentLoaded', () => {
    const button = document.getElementById('match-sounds');
    if (!button) return;
    const label = () => {button.textContent = enabled ? 'Sound on' : 'Sound off'; button.setAttribute('aria-pressed', String(enabled));};
    label();
    button.addEventListener('click', () => {
      enabled = !enabled;
      try {localStorage.setItem('portal-sounds', enabled ? 'on' : 'off');} catch {}
      label(); unlock();
    });
  });
  root.PortalFeedback = {reset() { track = createTracker(); }, update(match, user, options) {
    const event = track(match, user, {...options, suppressed: options.suppressed || document.hidden});
    if (!event) return;
    const node = document.getElementById(event === 'turn' || event === 'start' ? 'seats' : 'match-result');
    if (node && !root.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      node.animate([{opacity:.55, transform:'translateY(3px)'},{opacity:1,transform:'translateY(0)'}], {duration:240});
    }
    if (!enabled || context?.state !== 'running') return;
    tones[event].forEach((frequency, i) => {
      const oscillator = context.createOscillator(), gain = context.createGain();
      const time = context.currentTime + i * .12;
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0, time);
      gain.gain.linearRampToValueAtTime(.035, time + .015);
      gain.gain.exponentialRampToValueAtTime(.001, time + .15);
      oscillator.connect(gain); gain.connect(context.destination);
      oscillator.start(time); oscillator.stop(time + .16);
      oscillator.onended = () => {oscillator.disconnect(); gain.disconnect();};
    });
  }};
})(typeof window !== 'undefined' ? window : globalThis);
