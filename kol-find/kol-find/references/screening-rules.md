# KOL-Find Screening Rules

## Qualified Creator

A qualified creator should normally be one of:

- individual creator
- personal brand
- creator-led studio
- expert, educator, coach, reviewer, or niche operator with recurring original content
- podcast or media channel only when there is a clear host/personality and sponsorship path

They should have:

- recent content matching the configured niche
- enough followers and recent views for the configured threshold
- public signals supporting target country/region and language, when required
- public contact path or sponsorship signal when contactability is required

## Default Exclusions

Exclude:

- official accounts, product accounts, company accounts, platform accounts
- media outlets, institutions, associations, agencies without a clear creator personality
- pure repost, clip, quote, audiobook, compilation, or motivation-archive accounts
- globally famous founders, CEOs, investors, celebrities, politicians, or public figures unlikely to accept normal sponsorships
- accounts primarily in excluded regions or languages
- accounts whose content only keyword-matches one post but whose overall channel is off-niche
- risky content listed in the task configuration

## Region Logic

Use platform-provided region when available.

If no platform region exists, infer cautiously from:

- self-declared bio location
- language and accent
- repeated geo tags
- creator links or business entity
- country-code top-level domain
- local topics, pricing, laws, holidays, stores, or cities

Do not over-infer from one travel post. Use `unknown` when evidence is weak.

If target-region compliance is strict, reject unknown-region accounts unless the config explicitly allows them.

## Contactability

Preferred contact signals:

- public email
- business inquiries text
- collab, partnership, sponsor, or media kit link
- management or agency email
- Linktree, Komi, Pillar, Beacons, personal site contact page
- recurring paid partnership, ad, sponsored, or affiliate disclosure

Do not expose private contact data. Use only public profile or public webpage information.

## Recent Average Views

Default metric:

- average views from original content posted in the last 10 days

If fewer than enough recent posts exist:

- calculate from available recent original posts
- record the count if the output schema allows it
- otherwise use the average and keep uncertainty in an audit sheet if requested

Exclude from average when possible:

- reposts
- live replays
- shorts/reels copied from another account
- pinned old posts
- pure ads unrelated to the creator's normal content

## Product Fit

For multiple products/campaigns:

1. First apply global exclusions.
2. Then evaluate campaign-specific niche, region, language, follower, and view thresholds.
3. Mark every campaign the creator fits.
4. Choose one primary recommendation using this order:
   - strongest niche match
   - strongest recent content evidence
   - platform priority
   - highest recent average views
   - strongest contactability

## Manual Review Signals

Flag or sample-check:

- huge accounts with no visible sponsorship path
- founders, CEOs, investors, or public figures
- unclear country/region
- multilingual accounts where target audience is unclear
- accounts with strong follower count but weak recent views
- channels whose title suggests podcast, media, clips, archive, official, TV, news, or compilation

