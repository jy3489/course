# Player avatars and match feedback

The web portal assigns one of 1,000 animal avatars when an account has no profile
picture. The server saves the URL in `User.picture_url`. Existing provider pictures
are preserved; accounts missing pictures are backfilled during the existing startup
account migration. Ten red animal avatars identify computers, with the word
“Computer” retained beside them. The same stable player-ID mapping is used in the
portal and game context. Game clients receive picture URLs but choose whether to
render them.

`static/avatars/manifest.json` describes all 1,010 generated PNGs (96 × 96).
User variants combine 20 animals, five palettes, five accessories and two expressions.
Assets total about 7.7 MB; the service worker caches only pictures actually requested,
not the entire catalog. New profile selection uses cosmetic randomness outside
`ctx.random`; game logic and its seeded randomness are unchanged.

In match Controls, **Sound off / Sound on** saves a browser-local preference.
Sounds default off. Five short synthesized cues cover match start, your turn,
explicit win, draw and loss. Unknown results and early endings do not imply a loss.
Browsers require a user gesture before audio can play. Initial loads, repeated polls,
history, pending offline moves, account/match switches and reconnects stay silent.
No audio assets or third-party requests are required.

Button presses, controls opening and confirmed match events have short motion;
OS reduced-motion preferences disable the new animations. No animation changes a
move, result or timing rule.

## Verification

- Python tests cover asset counts/dimensions, persisted assignment, legacy-account
  backfill, provider-picture preservation and stable computer selection.
- JavaScript tests cover event transitions, duplicate suppression and silent replay,
  offline, account/match switch cases.
- Browser tests load real avatar images, enable synthesized audio, confirm a turn
  produces exactly two tones despite repeated updates, save/reload the preference,
  and check reduced-motion behavior on desktop and mobile emulation.

For a manual check, open a match with another account, turn sound on in Controls,
and alternate turns. Check that polling stays silent. Reload, browse match history,
then disconnect/reconnect: old events should not replay. Try the same sequence with
sound off and reduced motion enabled. Real iPhone/iPad Safari audio should also be
checked before presenting; automated Chrome emulation does not replace that check.
