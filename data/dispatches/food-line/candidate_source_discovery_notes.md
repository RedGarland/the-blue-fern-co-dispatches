# Food Line Candidate Source Discovery Plan

Use this plan to discover, verify, and queue candidate sources before they are tested or promoted into the Food Line pressure registry.

## A. National recurring sources

Focus on outlets that regularly publish source-backed food insecurity, SNAP, WIC, hunger, food bank, and emergency food reporting.

- AP
- Reuters
- NPR
- Marketplace
- Stateline
- Route Fifty
- KFF Health News
- Civil Eats
- The Conversation
- FRAC updates
- CBPP updates
- Urban Institute
- Feeding America news/research

Search seeds:

- `site:apnews.com food insecurity RSS`
- `site:reuters.com SNAP delay RSS`
- `site:npr.org food bank demand RSS`
- `site:marketplace.org hunger RSS`
- `site:stateline.org SNAP RSS`
- `site:routefifty.com emergency food RSS`
- `site:kffhealthnews.org food insecurity RSS`
- `site:civileats.com hunger RSS`
- `site:theconversation.com food insecurity RSS`
- `site:frac.org SNAP updates`
- `site:cbpp.org food assistance updates`
- `site:urban.org food insecurity`
- `site:feedingamerica.org news food insecurity`

## B. State and local public media targets

For each starter state, look for:
- statewide public radio
- city public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- state capital bureau

### WA
- statewide public radio
- Seattle/Tacoma city public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Olympia or Spokane capital bureau

### OR
- statewide public radio
- Portland city public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Salem capital bureau

### ID
- statewide public radio
- Boise city public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Boise capital bureau

### CA
- statewide public radio
- Los Angeles or San Diego public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Sacramento capital bureau

### TX
- statewide public radio
- Austin/Houston/Dallas public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Austin capital bureau

### FL
- statewide public radio
- Miami/Tampa/Orlando public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Tallahassee capital bureau

### NY
- statewide public radio
- New York City public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Albany capital bureau

### PA
- statewide public radio
- Philadelphia/Pittsburgh public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Harrisburg capital bureau

### OH
- statewide public radio
- Columbus/Cleveland public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Columbus capital bureau

### MS
- statewide public radio
- Jackson public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Jackson capital bureau

### KY
- statewide public radio
- Louisville/Lexington public radio
- nonprofit newsroom
- local TV RSS
- rural/local newspaper RSS
- Frankfort capital bureau

Search seeds:

- `[state] food insecurity local news RSS`
- `[city] SNAP delay RSS`
- `[state] food bank demand RSS`
- `[state] food assistance RSS`

## C. Food bank and provider targets

For each starter state, look for:
- major Feeding America affiliates
- regional food banks
- Meals on Wheels or senior nutrition sources
- school or summer meal advocacy sources

### WA
- Food Lifeline
- Northwest Harvest
- Meals on Wheels-related senior nutrition sources
- summer meal advocates

### OR
- Oregon Food Bank
- Meals on Wheels-related senior nutrition sources
- school meal advocates

### ID
- Idaho food bank affiliates
- Meals on Wheels-related senior nutrition sources
- summer meal advocates

### CA
- California food bank affiliates
- Meals on Wheels state or regional chapters
- school/summer meal advocates

### TX
- North Texas Food Bank
- regional food bank affiliates
- senior nutrition providers
- school/summer meal advocates

### FL
- Feeding South Florida
- regional food bank affiliates
- Meals on Wheels Florida chapters
- school/summer meal advocates

### NY
- Food Bank For New York City
- Regional Food Bank of Northeastern New York
- Meals on Wheels chapters
- school/summer meal advocates

### PA
- Philabundance
- Greater Pittsburgh Community Food Bank
- Meals on Wheels chapters
- school/summer meal advocates

### OH
- local Feeding America affiliates
- Meals on Wheels chapters
- school/summer meal advocates

### MS
- Mississippi food bank affiliates
- Meals on Wheels chapters
- school/summer meal advocates

### KY
- Dare to Care Food Bank
- God’s Pantry Food Bank
- Meals on Wheels chapters
- school/summer meal advocates

Search seeds:

- `[food bank name] news RSS`
- `[food bank name] press RSS`
- `[state] Meals on Wheels news`
- `[state] summer meals news`

## D. Official pressure targets

For each starter state, look for:
- SNAP agency notices
- WIC agency notices
- EBT outage or status source if available
- D-SNAP or disaster food assistance source
- state emergency management alerts
- child nutrition or summer meals notices

### WA
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### OR
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### ID
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### CA
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### TX
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### FL
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### NY
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### PA
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### OH
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### MS
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

### KY
- SNAP agency notices
- WIC agency notices
- EBT outage/status page
- disaster food assistance notices
- emergency management alerts
- child nutrition notices

## Manual validation checklist

- URL resolves in browser
- RSS or Atom feed has item titles
- Feed has summaries or page links
- Content is not mostly recipes, restaurants, or lifestyle posts
- Source has occasional food insecurity, SNAP, WIC, food bank, or hunger pressure stories
- Source has stable public access
- No paywall bypass is required

## PowerShell helper commands

```powershell
Invoke-WebRequest -Uri "https://example.com/feed.rss" -Method Head | Select-Object StatusCode, StatusDescription
```

```powershell
Invoke-WebRequest -Uri "https://example.com/feed.rss" | Select-String -Pattern "<rss|<feed|<item|<entry"
```

```powershell
Invoke-WebRequest -Uri "https://example.com/feed.rss" | Select-String -Pattern "food bank|SNAP|WIC|hunger|food insecurity"
```

```powershell
python scripts\test_food_line_candidate_sources.py --date 2026-06-08
```

## Candidate intake template

Copy and paste this JSON structure when adding a new candidate source:

```json
{
  "source_id": "",
  "source_name": "",
  "publisher": "",
  "candidate_url": "",
  "source_family": "",
  "source_type": "rss",
  "state": "",
  "location_name": "",
  "location_scope": "",
  "candidate_reason": "",
  "expected_text_basis": "",
  "extraction_quality_guess": "",
  "pressure_topics_expected": [],
  "status": "candidate",
  "notes": ""
}
```

## Importing verified candidates

- Paste verified rows into `data/dispatches/food-line/candidate_source_intake_template.csv`.
- Use `|` inside `pressure_topics_expected` to separate multiple topics in one cell.
- Run the importer:

```powershell
python scripts\test_food_line_candidate_sources.py --import-intake data/dispatches/food-line/candidate_source_intake_template.csv
```

- Run the candidate tester after import:

```powershell
python scripts\test_food_line_candidate_sources.py --date 2026-06-08
```

- Promote verified enabled candidates:

```powershell
python scripts\test_food_line_candidate_sources.py --date 2026-06-08 --promote-enabled
```

## Screening notes

- Keep candidates out of production until they have been reviewed.
- Reject feeds that are mostly recipes, restaurant posts, fundraising blurbs, or lifestyle content.
- Keep candidate pages only when they expose usable source text and recurring pressure evidence.
- Do not infer pressure from source names, categories, or registry labels alone.
