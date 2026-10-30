# Translating games' texts

The platform's own screens are translated from catalogs we write (`docs/i18n.md`). The text
*inside a game* is written by the game's builder (usually the AI), so it is not a fixed list of
sentences we can translate ahead of time. This is how it is translated anyway, in the language of
each player, on demand, and remembered.

```
AI writes the game  ->  the game lists every text it shows in one JSON block (in English)
player opens it     ->  the portal asks  GET /games/{id}/versions/{v}/strings?lang=es
server              ->  reads the block, translates the texts it has never translated (Google
                        Translate), remembers each one, returns them
portal              ->  puts them in the game's state_changed message (strings, locale, direction)
game                ->  shows t("wins", {name}) in Spanish; players in one match can each use their own language
```

The key idea is that the AI **declares the game's whole vocabulary** when it writes the game: the
list is finite and known, so it can be translated one batch at a time and checked. A new version
of the game only pays for the texts that changed, and a text that two games share (or that was
already translated for another player) is translated once.

## For game builders and the AI

A translatable game has, in its client HTML, a block with every text it shows, in English, with
`{placeholders}` for the parts that change, and the helper from `game_i18n_helper.js`:

```html
<script type="application/json" id="i18n-strings">
{"your_turn": "Your turn", "wins": "{name} wins!", "moves": "Moves: {count}"}
</script>
<script>
/* the contents of game_i18n_helper.js: t(key, params) and applyLocale(message) */
window.addEventListener("message", (event) => {
  const m = event.data;
  if (!m || m.type !== "state_changed") return;
  applyLocale(m);                       // first, before drawing anything
  status.textContent = m.status === "over" ? t("wins", {name: winnerName}) : t("your_turn");
});
</script>
```

Rules (the game-building agent is told the same ones, `game_text.PROMPT_RULES`):

- **Every text the player reads is in the block**: buttons, labels, hints, errors, results,
  `aria-label`s, placeholders, and text drawn on a canvas. Shown only through `t()`.
- **One string per sentence, with placeholders.** Never build a sentence from pieces
  (`name + " wins"`) and never pick a word inside a sentence with a ternary: word order differs
  between languages. Use two keys and pick between whole texts.
- Keys are letters, digits, `_ . -` (at most 64); a text is plain text (no HTML), at most 1000
  characters; at most 400 texts. No plurals: write `Moves: {count}`.
- **Keep display text out of the match state** and out of `make_move`: store codes (`"X"`, `"draw"`) and
  turn them into text when drawing. Players in one match may use different languages.
- Player names come from `players` and are not translated.
- Layout: no fixed widths for text, let it wrap, use CSS logical properties (`margin-inline-start`,
  `text-align: start`) so a right-to-left language (Arabic) works.

`game_text.lint(code)` finds the common mistakes (no block, a key used but missing or never used,
text written in the code, sentences joined from pieces, markup, text on a canvas); the endpoint below
returns them as `warnings`. A game with no block still has its **name and description** translated.

## The API (additive: nothing existing changes)

| Endpoint | Description |
|---|---|
| `GET /games/{id}/versions/{v}/strings` | The version's name, description and string table in the viewer's language (`?lang=`, else the user's saved language, else `Accept-Language`). Same visibility as `GET /games/{id}/versions/{v}` |
| `GET /i18n/games/{locale}` | The names and descriptions of every published game, for a portal's game cards |

```json
{"game_id": "…", "game_version": 3, "locale": "es", "direction": "ltr",
 "status": "translated", "name": "Tres en raya", "description": "…", "translatable": true,
 "strings": {"your_turn": "Tu turno", "wins": "¡{name} gana!"}, "warnings": []}
```

`status`: `source` (English, or a language with no translation: nothing to do), `translated`,
`partial` (some texts could not be translated and are as the builder wrote them), `unavailable`
(no translator is set up, or it is failing). The request never fails because of the translator: what
cannot be translated comes back as written.

**The game's message** (web portal: `static/portal.js` and `I18n.gameText`; every frontend that hosts
games does the same) gains three optional fields, which a game that does not know them ignores:

```json
{"type": "state_changed", "...": "...", "locale": "es", "direction": "ltr",
 "strings": {"your_turn": "Tu turno", "wins": "¡{name} gana!"}}
```

`strings` is only sent when the game has a table and the language is not English. Fetch the strings
once per game version and language, send them with every message, and send a new message when the
player changes language. **iOS, Android and React Native portals must do this to translate games;
until they do, games there show their English.**

## Turn it on

Translation is off until configured (`"translation": {"provider": "off"}`).

1. In the Google Cloud project: **enable the Cloud Translation API**, then Credentials, **create an API
   key and restrict it to the Cloud Translation API** (and, if you can, to the VM's address).
2. On the VM, give the server the key as an environment variable, not in the config file:

   ```bash
   sudo systemctl edit game-platform      # add:  [Service]  Environment=GAME_PLATFORM_TRANSLATE_API_KEY=THE_KEY
   ```

3. In `config.json`: `"translation": {"provider": "google", "daily_character_limit": 200000}`, then
   `sudo systemctl restart game-platform`.

Instead of a key, the VM's own service account can sign in (leave the key empty): it needs the Cloud
Translation API User role **and** the VM's access scopes must include `cloud-platform` (set when the VM is
created; changing it needs the VM stopped). The key is the easier route.

**Cost.** Google charges per character translated. The server sends only texts it has never translated
into that language, and never more than `daily_character_limit` characters per (UTC) day (default
200,000; the day's count restarts with the server); beyond that, new texts stay in English until
tomorrow and a `translation_budget_exceeded` alert is raised. Check Google's current price per million
characters and its monthly free allowance before raising the limit. For trying it without a key,
`"provider": "fake"` prefixes each text with its language (`[es] Your turn`); never use it on a real server.

## Checking and correcting translations

Machine translations are remembered in the data (the `translations` collection) with their source text.
An administrator (master password) can review and fix them:

```bash
curl -u :MASTER 'https://HOST/admin/translations?locale=es'
curl -u :MASTER -X PUT https://HOST/admin/translations -H 'Content-Type: application/json' \
     -d '{"locale": "es", "source": "{name} wins!", "text": "¡{name} gana!"}'     # reviewed, never replaced
curl -u :MASTER -X DELETE https://HOST/admin/translations/es/SOURCE_HASH         # translate it again
```

A translation must keep the same `{placeholders}` as the text; the server refuses one that loses them (a
machine translation that does is discarded and the original is shown; `translation_rejected` is logged).

## What can go wrong

- **Google is down, or the key is refused:** games show their own texts; one `translation_failed` alert
  is raised, and the server leaves Google alone for a minute so players are not made to wait for it.
- **The AI forgets the block:** the game still works (and its name and description are translated); the
  `warnings` say why. Games written before this have no block.
- **A translation reads badly:** correct it as above. It is the same text for every game that uses it.

## Not done yet

- **The game builder's preview** does not send translations: a creator tests in English. A language picker
  for the preview (translating the unsaved code) is the next step.
- **Games that exist today have no block.** Options: ask the agent to "make this game translatable" as a new
  version, or translate the page's text from outside the game (which cannot work for text built from pieces).
- **The Google calls were written against Google's documentation and tested with a fake HTTP layer; they have
  not been run against Google from here.** The first real request is the test: check
  `jsonPayload.event="translation_done"` (or `translation_failed`) in the log.
- Whether the AI follows the rules reliably is untested: it needs real generations, checked with `game_text.lint`.
