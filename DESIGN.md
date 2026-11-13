# Design: the connect window

## Brand rule (read first)
Decided by the publisher on 9 November 2026. Two colour families, never mixed:

1. **Paid / enterprise products** (the eBot product and ETLYON services) use the **ETLYON corporate palette**: green `#1F5E55`, warm white, ink (the brand pack).
2. **Open-source projects** (this one) use the bright **"flying colours"**: purple, green, honey, a blue main button, orange. Each project or app gets **one accent** of its own.

The small label **"by ETLYON · open source"** links the two families.

## What a person sees
One small dark card per step, centred on the page. Fewest words; nothing a person has to decode.

```
┌──────────────────────────────────────┐  ← 3 px accent line (the app's accent)
│ (C) Claude · Odoo                    │  ← circle: assistant's initial on the app accent
│                                      │
│ Connect Odoo to Claude               │  ← title (bold)
│ Claude will use Odoo as you, with    │  ← one lead line
│ your own rights — nothing more.      │
│                                      │
│ Odoo web address                     │  ← label
│ [ erp.example.com                  ] │  ← example INSIDE the box (placeholder)
│ [            Continue             ]  │  ← blue main button, full width
│               Not now                │  ← quiet text button
│                                      │
│      English  Français  Deutsch      │  ← language switch (current one bold)
│  Odoo connector · by ETLYON · open source │  ← small foot, links to the source code
└──────────────────────────────────────┘
```

### The four steps
| # | Step | Title | Content | Buttons |
|---|---|---|---|---|
| 1 | Address | **Connect {App} to {Assistant}** | lead line; label "{App} web address"; box with `erp.example.com` inside; an error turns the box's border orange and appears **under** the box in orange, regular weight, saying what to do ("Use an address that starts with https://.") | **Continue** · Not now |
| 2 | Key | **Connect {App} to {Assistant}** | "Connecting to **host** · Change" (Change on the right, link colour; absent when the address was fixed by the connector address); grey **Next:** box with numbered steps (1. Open {App} ↗ 2. the menu path, using the app's real labels 3. Paste it below); label "API key"; box with "Paste your key here" inside | **Connect** · Not now |
| 3 | Connected | **{App} is connected to {Assistant}.** (green) | "Signed in as {name} ({email})" (the signed-in account at run time); "You can go back to {Assistant} now."; small grey "Not the right account? Delete the key in {App}, then connect again." The page then returns to the assistant by itself; "Back to {Assistant}" link as well. | (Back to {Assistant}) |
| 4 | Didn't connect | **{App} didn't connect. Nothing was changed.** (orange) | one line saying why and what to do ("Odoo refused this key. Make a new one and paste it again.") | **Try again** · Not now |

**Hub (Nextcloud), step 2.** Hub signs in inside Nextcloud itself (Login Flow v2); the Odoo rows above do not change.

| # | Step | Title | Content | Buttons |
|---|---|---|---|---|
| 1 | Address | as Odoo | label "Hub web address"; box with `cloud.example.com` inside | **Continue** · Not now |
| 2 | Grant access | **Connect Hub to {Assistant}** | "Connecting to **host** · Change"; grey **Next:** box: 1. Open Hub ↗ (Nextcloud's own approval page on that host, new tab) 2. Click Log in, then Grant access 3. Come back here; small link "Paste an app password instead". The page reloads itself every 3 s (no script) and moves on once access is granted. | Not now |
| 2b | Paste (fallback) | **Connect Hub to {Assistant}** | "Connecting to **host** · Change"; grey **Next:** box: 1. Open Hub ↗ 2. Your picture › Settings › Security › Create new app password 3. Paste it below; label "User name" (box); label "App password", box with "Paste your app password here" inside. After three Grant access starts in one sign-in, step 2 opens here by itself with "Too many tries. Paste an app password instead." | **Connect** · Not now |
| 3 | Connected | as Odoo | "Signed in as {name} ({email})"; small grey "Not the right account? Delete the app password in Hub, then connect again." | (Back to {Assistant}) |
| 4 | Didn't connect | as Odoo | e.g. "Access was not granted in time. Try again." or "Hub refused this user name and app password. Make a new app password and paste it again." | **Try again** · Not now |

**kSuite (Infomaniak): no step 1.** kSuite works by account, on Infomaniak's own servers: there is no server to pick, so the window opens at step 2, and step 2 has no "Connecting to … · Change" line (nothing to change). The Odoo and Hub rows above do not change.

| # | Step | Title | Content | Buttons |
|---|---|---|---|---|
| 2 | Token | **Connect kSuite to {Assistant}** | the lead line (this is the first page); grey **Next:** box: 1. Open Infomaniak ↗ (Infomaniak's own API token page, new tab) 2. Create a token with user_info, workspace:mail and workspace:calendar 3. Paste it below; label "API token", box with "Paste your token here" inside | **Connect** · Not now |
| 3 | Connected | as Odoo | "Signed in as {name} ({email})"; small grey "Not the right account? Delete the token in Infomaniak, then connect again." | (Back to {Assistant}) |
| 4 | Didn't connect | as Odoo | e.g. "Infomaniak refused this token. Make a new one and paste it again." or, naming exactly the part the token lacks, "This token cannot open Calendar. Make a new token with workspace:calendar and paste it again." | **Try again** · Not now |

Only for an assistant we cannot verify, one more step before 3: **Allow "{name}"?**, with the line *"{name}" ({return host}) wants to use your {App}. It is not an assistant we know: continue only if you added it yourself.* and *It will read and change what you can in {App}, as {who}.*, then **Allow** · Not now. Known assistants (Claude, ChatGPT, Le Chat, and the operator's own) never see it: pressing Connect is the consent.

### Word rules
- **Fewest words.** One title, one lead line, one action. No paragraph a person must read to succeed.
- **Examples live inside the boxes** (placeholders); no hint lines under them.
- **"Connecting to {host} · Change"**: plain, one line.
- **Green title = connected; orange title = didn't connect**; the colours are for status only, never decoration.
- **No expiry wording** on these pages: no "ends on", no "3 months", no key-ending page. (Reminders, if any, live in the assistant, not here.)
- **No raw IDs**, ever: a person's name and email, or nothing.
- **No key-naming rule**: the person may name the key anything.
- People never read "MCP" or "plugin". The foot says "{App} connector · by ETLYON · open source" (the publisher's label, set in configuration).
- Menu paths use the app's real labels (Odoo 19: *Your picture › My Preferences › Security › Add API Key*; Nextcloud 28+: *Log in*, then *Grant access*, *Settings › Security › Create new app password*; an administrator's menu says *Personal settings* instead of *Settings*; Infomaniak: *Create a token* / *Créer un token* / *Token erstellen*, *API token* / *Token API* / *API-Token*, and the scope identifiers user_info, workspace:mail, workspace:calendar). **Unverified:** the identifiers are Infomaniak's own (its Mail and Calendar MCP servers' READMEs), but whether its token form shows them or display names (perhaps *User information*, *Mail*, *Calendar*), and after which product/application choice, is not checked: nobody has opened the form yet. Check it on a real account before LIVE; if it shows display names, step 2 uses those. kSuite never mentions the token's validity field (no expiry wording). In French "token" is masculine (*Collez-le*), in German neuter (*das Token*).
- English, French and German for every word; the switch keeps the person on the same step.

## Palette
Dark is the approved look and the default. Hex values and where each is used:

| Token | Dark | Light | Used for |
|---|---|---|---|
| page | `#101116` | `#F2F3F5` | page background |
| card | `#181B20` | `#FFFFFF` | the card |
| inset | `#1F242B` | `#F2F4F7` | the Next: box, input boxes |
| text | `#ECEDEF` | `#15181D` | titles and body |
| muted | `#9BA0A8` | `#5B616B` | lead line, "Not now", labels, unselected languages |
| foot | `#8F959E` | `#6B717B` | the small foot line |
| blue | `#5B97F5` | `#5B97F5` | main button (text `#101116`), links and "Change" (light mode links `#2F6FD6`) |
| green | `#6FD38A` | `#1E7B45` | "connected" title only |
| orange | `#F0A24A` | `#A85A10` | "didn't connect" title and field errors only |

**One accent per app** (top line, circle, focused box):

| App | Accent | Circle | Letter on circle | Light-mode line/focus |
|---|---|---|---|---|
| Odoo | `#5B4FD8` purple | `#5B4FD8` | white `#FFFFFF` | `#5B4FD8` |
| Hub (Nextcloud) | `#3FA080` green | `#3FA080` | dark `#101116` | `#3FA080` |
| kSuite | `#E0B84A` honey | `#C99A2E` | dark `#101116` | `#9C7A1E` |

The focused box shows a 2 px border in the app accent plus a soft outer halo (Odoo halo `#7468E3`).

## Contrast check (WCAG 2; text 4.5:1, large text and controls 3:1)
| Pair | Ratio | Verdict |
|---|---|---|
| white letter on Odoo `#5B4FD8` | 5.91 | ✓ |
| white letter on Hub `#3FA080` | 3.21 | ✗ → **dark letter** `#101116`: 5.88 ✓ |
| white letter on kSuite circle `#C99A2E` | 2.58 | ✗ → **dark letter**: 7.31 ✓ |
| dark text `#101116` on blue button `#5B97F5` | 6.46 | ✓ (white would be 2.92 ✗) |
| green title `#6FD38A` on card | 9.34 | ✓ |
| orange title `#F0A24A` on card | 8.20 | ✓ |
| body `#ECEDEF` on card | 14.74 | ✓ |
| muted `#9BA0A8` on card / on inset | 6.57 / 5.94 | ✓ |
| foot `#8F959E` on card | 5.72 | ✓ (the darker `#7A808A` was 4.34 ✗) |
| link `#5B97F5` on card | 5.91 | ✓ |
| Odoo focus `#5B4FD8` vs card | 2.92 | marginal → the `#7468E3` halo reaches 3.97 ✓ |
| Hub / kSuite focus vs card | 5.39 / 9.14 | ✓ |
| light: green `#1E7B45`, orange `#A85A10`, link `#2F6FD6` on white | 5.28 / 5.08 / 4.81 | ✓ |
| light: kSuite honey `#E0B84A` as a line on white | 1.89 | ✗ → `#9C7A1E`: 4.03 ✓ |

## Sizes
- Card: max width 26 rem (≈ 416 px), radius 14 px, padding 24–26 px; centred with 8 vh space above at 1440; at 390 the card fills the width with a 16 px gutter.
- Circle 32 px; title 1.3 rem bold; body 15–16 px; buttons full width, 44 px tall minimum (touch).
- Checked at **390** (phone) and **1440** (desktop), dark and light.

## Pictures
Frames rendered from the code with example data (no real people or addresses): `docs/design/` (address, key, connected, didn't connect; 390 and 1440).
