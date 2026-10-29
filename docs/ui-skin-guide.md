# UI skin contributor guide

This guide explains how to design, preview, contribute, publish, and access a UI
skin in the experiment framework. A skin is a CSS theme selected for a signed-in
user by the UI-skin experiment; there is no image-upload or skin-upload form.
Contributors submit the CSS change to the repository, and an administrator
registers the skin variant after its CSS is deployed.

## What makes a skin

Skins are CSS custom-property overrides in `static/experiments.css`. Add one
`:root[data-skin="<key>"]` rule. The key must start with a lowercase letter or
digit and may contain lowercase letters, digits, and hyphens (maximum 50
characters). Use the same key in the CSS selector and the admin registration
form. For example, a variant named "Ocean" could use the key `ocean`.

The shared base theme is defined in `static/common.css`. The primary tokens
available to override are:

| Token | Purpose |
|---|---|
| `--color-bg-0` … `--color-bg-4` | Page, panel, and elevated-surface colors |
| `--color-text-primary` … `--color-text-quaternary` | Text hierarchy |
| `--color-border-1` … `--color-border-4` | Border hierarchy |
| `--color-accent`, `--color-accent-hover` | Interactive accent colors |
| `--color-focus-ring` | Keyboard-focus indicator |
| `--color-ok`, `--color-warn`, `--color-error` | Status colors |
| `--font-sans`, `--font-mono` | Main and code fonts |
| `--font-size-xs`, `--font-size-sm`, `--font-size-md` | Shared type sizes |
| `--space-xs` … `--space-xl` | Shared spacing |
| `--radius-sm`, `--radius-md` | Component corner radii |
| `--shadow-1`, `--shadow-3` | Shared elevation shadows |
| `color-scheme` | Native browser controls; use `light` or `dark` |

Use the existing Aurora and Sunset rules in `static/experiments.css` as
examples. A new skin inherits any tokens it does not override from
`static/common.css`. Prefer changing tokens over adding selectors for individual
components: token-based themes remain consistent across the portal, builder,
browse, invites, console, admin, and statistics pages.

Example:

```css
:root[data-skin="ocean"] {
  color-scheme: dark;
  --color-bg-0: #071824;
  --color-bg-1: #0c2635;
  --color-bg-2: #123447;
  --color-bg-3: #1b465b;
  --color-bg-4: #285d72;
  --color-text-primary: #f0fbff;
  --color-text-secondary: #c4e3ed;
  --color-text-tertiary: #91bdcc;
  --color-text-quaternary: #668f9f;
  --color-border-1: #183746;
  --color-border-2: #285064;
  --color-border-3: #3a6a7f;
  --color-border-4: #56899d;
  --color-accent: #38c6bd;
  --color-accent-hover: #69e2d7;
  --color-focus-ring: #69e2d7;
}
```

Keep text readable on every surface, make controls distinguishable, and retain a
visible focus ring for keyboard users. Check both foreground/background contrast
and hover/focus states; do not rely on color alone to communicate status.

## Create and preview locally

1. Create a branch from the current project branch.
2. Add the theme rule to `static/experiments.css`. Pick a unique key and keep it
   lowercase with hyphens if needed.
3. Run the app using the local setup described in the repository
   [README](../README.md#running). Open the portal or builder while signed in.
4. To preview your unregistered skin, open the browser developer console on the
   page and run `document.documentElement.dataset.skin = "ocean"` (replace
   `ocean` with your key). This applies the rule to the current page only; repeat
   on each page you want to inspect. Reloading or refreshing the user's server
   assignment may restore the assigned skin.
5. Inspect the portal, builder, and other listed app surfaces at desktop and
   mobile widths. Check dialogs, navigation, disabled states, errors, focus
   indicators, and any color combinations affected by your palette.

The preview command is only for local visual checking. Do not persist a forced
skin in browser storage or change the assignment endpoint; assignment is
controlled by the experiment service.

## Contribute and publish

There is no direct UI upload. The theme CSS is the deliverable:

1. Commit the `static/experiments.css` change on your branch.
2. Open a pull request and include the proposed display name and exact skin key
   in its description. Include screenshots if practical.
3. Once the CSS is merged and deployed, an administrator opens **Admin →
   Statistics** and uses **Add a UI skin variant**. Enter the display name and
   the exact key from the CSS selector.
4. Confirm the new variant appears in the UI-skin experiment and that an account
   assigned to it sees the new theme.

Deploy the matching CSS before registering the variant: registration makes it
eligible for new assignments immediately, so users must not be assigned to a
key that has no deployed CSS rule. Registering a key does not upload or deploy
CSS.

When editing `static/experiments.css`, update its cache-busting version from
`v=1` in each HTML page that loads it and in `static/portal-sw.js`. Keep those
references in sync so browsers and the portal service worker receive the new
theme. Also increment the `CACHE` version in `static/portal-sw.js` so an updated
portal shell replaces the previous cached shell. The pages currently linking
the stylesheet are `admin.html`, `browse.html`, `builder.html`, `console.html`,
`invites.html`, `portal.html`, and `stats.html`.

## Access the assigned skin and experiment results

- **As a user:** sign in normally. The browser requests the user's assignment
  from `GET /users/me/experiments/skin` and applies its `key` as the root
  `data-skin` attribute. The assignment is persisted server-side, so it remains
  stable across visits and deployments. Anonymous visitors use the base theme.
- **As an administrator:** open **Admin → Statistics** to compare variants and
  see assignments, signups, match starts, and return activity. The dashboard
  requires the master password.
- **In the API:** `GET /users/me/experiments/skin` requires the normal user
  authentication headers. `GET /admin/experiments` returns experiment metrics
  and requires the master password.

Assignments are deterministic for a user's ID when first selected and then
persisted. Adding a variant does not move users who already have assignments,
but it can change which variant is selected for users who are assigned later.
On account merge, the surviving account keeps its assignment; if it had none,
it inherits the merged account's assignment. Do not rename or remove a
registered key during an active test without coordinating with the experiment
administrator.

## Before requesting review

- [ ] The CSS selector key matches the proposed admin key exactly.
- [ ] The theme uses shared tokens where possible and leaves unspecified tokens
      to the base theme.
- [ ] Text, controls, status states, and keyboard focus remain clear and
      accessible.
- [ ] The portal and builder have been previewed at desktop and mobile sizes.
- [ ] Any cache-busting references are updated consistently.
- [ ] The pull request description includes the display name, key, and preview
      screenshots or testing notes.
