# A/B experiments

The portal and builder use the persisted experiment framework in `models.py`,
`store.py`, and `service.py`. Experiment data is included in JSON snapshots and
Firestore snapshots. The first pilot creates two active experiments:

- **UI skin pilot**: Classic, Aurora, and Sunset.
- **Advertisement campaign pilot**: Student campaign A, B, and C, each with a
  $100 budget.

## Admin workflow

Open **Admin → Statistics** to see skin and campaign results separately. The
dashboard reports assignments, campaign link clicks, attributed signups, match
starts, users who started a match, and 7-day returning users. A returning user is
an attributed signup that starts a match during days 7–14 after signup. Rates use
unique users for conversions; match starts are shown as total started matches.

Create campaign links from the campaign form. The key becomes the stable short URL
`/c/<key>`; use lowercase letters, digits, and hyphens. The budget defaults to $100
and cannot exceed that amount. Update **Actual spend** as the campaign runs. The
dashboard calculates cost per signup, match-starting user, and returning user from
recorded spend. Clicks are counted per campaign and day; multiple clicks by the
same visitor are not deduplicated.

Campaign links use first-touch attribution in a browser. The campaign code is
remembered locally until a new account is created. Signups and later match starts
are attributed only when the user has a recorded campaign signup. Existing
accounts that click a campaign link do not become campaign signups.

The related admin APIs, all protected by the master password, are:

- `GET /admin/experiments` — dashboard metrics.
- `POST /admin/experiments/campaign-links` — create a campaign link and set its
  budget (up to $100).
- `PATCH /admin/experiments/campaign-links/{variant_id}` — record actual spend.
- `POST /admin/experiments/skin-variants` — register a skin variant.
- `PATCH /admin/experiments/{experiment_id}` — pause or resume an experiment.

## Student skin workflow

Follow the [UI skin contributor guide](ui-skin-guide.md) to create and preview a
theme, submit its CSS change, and register it after deployment using **Add a UI
skin variant** on the statistics page. A skin key must have a matching theme
rule before users assigned to it will see a distinct design.

Skin assignment is selected with SHA-256 of the account's user ID modulo the current
variant list, then stored so it survives restarts and later changes to the variant
list. On account merge, the survivor's existing assignment wins; if it has none, it
inherits the merged account's assignment. Anonymous visitors use the default theme.

## Metric and data notes

Skin and campaign results are independent; campaign-by-skin comparisons are not
currently reported. New signups and match starts are recorded against the assigned
skin. The browser sends the remembered campaign code when creating an account; the
server accepts only active campaign keys. Click totals are stored as per-day
aggregates. User-level assignment and conversion events are removed when an account
is deleted.
