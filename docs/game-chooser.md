# Visual game chooser

The shared portal includes a grid/list gallery, name/description search, player-support filters, and accessible native card buttons. Clicking a game card or search result opens that game's detail view in a new tab; the description and existing online/local match controls appear there, not below the gallery. The detail view reuses the current match-creation flow and can return to the catalog. The service-worker shell version is bumped with the changed assets.

## Image integration pending

The current game model and API do not have an artwork field or an image generation job. Gallery artwork currently uses CSS monogram placeholders; these are not AI-generated images. Do not describe the image-at-creation requirement as complete.

Confirm the owner of image generation with the class before changing the REST API. The builder/backend owner needs to define how a generated cover is persisted and returned with game metadata (including failure/pending behavior), then the portal can render it with placeholder fallback and offline caching. Credentials and provider calls belong on the server. Existing games will also need a backfill or a documented fallback.

## Verification

Run `node --test tests/web/*.test.cjs`. The gallery checks cover player filters, description search, empty results, selection through existing solo controls, and preserving card nodes during polling. Browser checks covered grid/list switching and choosing a game. Physical phone validation remains pending.
