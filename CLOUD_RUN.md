# Ugentlig sky-kørsel

Instruktioner til den planlagte Claude-session, der kører Job Alert uden Mads' computer.
Der sendes ingen mail; resultatet publiceres på resultatsiden, og Claude laver selv
grøn/gul/rød-vurderingen (ingen Anthropic API-nøgle nødvendig).

Resultatside: https://claude.ai/artifact/CqRmLzWfJJTaSDkE3brsbi

## Trin

1. Klon repoet og installér afhængigheder:
   ```bash
   git clone --depth 1 https://github.com/MadsO-it/job_alert.git && cd job_alert
   pip install --break-system-packages -q -r requirements.txt
   ```

2. Hent historikken fra resultatsiden med Artifact-værktøjet:
   `action: "read"`, `url: <resultatside>`, `path: "jobs.json"`, `out_dir: <arbejdsmappe>`.
   Hvis filen ikke kan hentes, så stop og rapportér det. Kør ikke uden historik,
   ellers kommer alle gamle opslag igen.

3. Scrape og filtrér (titelfilter og geografi sker i scriptet):
   ```bash
   python job_alert.py --export new.json --seen <arbejdsmappe>/jobs.json
   ```
   Hvis en kilde giver 0 opslag på alle søgninger, er sidens struktur sandsynligvis
   ændret. Notér det i opsummeringen.
   Antal opslag pr. kilde gemmes i `new.json` under `sources` og vises som
   kildestatus nederst på resultatsiden; kilder med 0 eller fejl markeres med rødt.

4. Vurdér hvert opslag i `new.json` selv. Brug `CANDIDATE_PROFILE` og `RATING_RULES`
   fra `job_alert.py` ordret som kriterier. `new.json` indeholder annoncens fulde tekst;
   læs den i portioner med
   ```bash
   python job_alert.py --show new.json --start 1 --count 25
   ```
   (derefter `--start 26` osv., til alle id'er er set). Er der mere end 60 nye opslag,
   så brug `--chars 400` og `--count 50`, så alt kan nås i én session; titel, virksomhed
   og annoncens indledning er nok til farven. Vurdér ud fra titel, virksomhed og
   annoncetekst. Skriv resultatet til `ratings.json` som `{"<id>": "green"|"yellow"|"red"}`
   med én nøgle per opslag. Giv ingen begrundelser; kun farven.

5. Byg siden:
   ```bash
   python render_page.py --new new.json --ratings ratings.json \
       --history <arbejdsmappe>/jobs.json --out-dir site
   ```

6. Publicér til den eksisterende side (samme URL, ingen ny side):
   Artifact `action: "publish"`, `url: <resultatside>`, `file_path: site/index.html`,
   `files: {"jobs.json": "site/jobs.json"}`. Udelad `icon`.

7. Afslut med én linje: antal nye opslag og fordelingen grøn/gul/rød, samt eventuelle
   kilder der gav 0 opslag.

Ændr ikke koden i repoet under en planlagt kørsel.
