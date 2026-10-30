// Translation helper (docs/game-translation.md). Needs the i18n-strings block before it.
const SOURCE_STRINGS = JSON.parse(document.getElementById("i18n-strings").textContent);
let strings = SOURCE_STRINGS;

// The text for a key in the viewer's language, with {name}-style placeholders filled in.
function t(key, params) {
  const text = strings[key] ?? SOURCE_STRINGS[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (match, name) => (params && name in params ? params[name] : match));
}

// Call this first in the state_changed handler, before drawing anything: the platform sends
// the viewer's translations (message.strings), language and writing direction with it.
function applyLocale(message) {
  if (message.strings) strings = { ...SOURCE_STRINGS, ...message.strings };
  if (message.locale) document.documentElement.lang = message.locale;
  if (message.direction) document.documentElement.dir = message.direction;
}
