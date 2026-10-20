# Jellyfin Rename — panel

Utility panel for a NAS. Not a marketing site.

## Color

- `--bg`: `#111111`
- `--surface`: `#1c1c1c`
- `--text`: `#e8e8e8`
- `--muted`: `#8a8a8a`
- `--line`: `#2a2a2a`
- `--accent`: `#00a4dc` (Jellyfin)
- `--ok`: `#5aa36a`
- `--warn`: `#c9a227`
- `--danger`: `#c45c5c`

No gradients. No purple. Accent only on the primary action and the live status dot.

## Type

- `ui-sans-serif, system-ui, sans-serif`
- Body 14px / 1.45
- Title 18px / 600
- Mono for paths and reports: `ui-monospace, monospace` 12px

## Space

- Page padding 24px
- Stack gap 16px
- Button padding 8px 14px
- Radius 4px everywhere

## Components

- Header stack max-width 720px. File table max-width 1100px
- Brand: 48px logo + 18px title, 12px gap
- Buttons: flat, 1px `--line` border, `--surface` fill. Primary = `--accent` fill, `#041018` text
- Report: `--surface` box, 1px `--line`, pre-wrap mono
- Table: native checkbox, 12px sans headers in `--muted`, 12px mono paths, row border `--line`
- Status OK = `--ok`, Rename = `--warn`. Proposed name in the last column
- Schedule gate: `--surface` fill, 1px `--warn` border, 16px padding. Primary button stays disabled until the checkbox is on
- No cards-in-cards, no icons-as-decoration, no footer brand
