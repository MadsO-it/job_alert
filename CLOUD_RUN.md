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

   **Kontaktpersoner.** Mens du læser, så notér navngivne personer i opslag inden for Mads'
   felt: IT, digitalisering, proces, data, AI, governance, risiko, compliance, transformation,
   projekt- og porteføljestyring. Farven er ligegyldig; et rødt opslag til en senior-AI-rolle
   kan stadig have en leder, der er værd at følge. `--show` viser linjen `KONTAKT:` med
   annoncens kontaktafsnit, også når teksten er afkortet. Har et opslag en relevant person, og
   var teksten afkortet, så læs det helt med `--start N --count 1 --chars 3000`.
   Skriv `contacts.json` som en liste, ét objekt pr. person pr. opslag:
   ```json
   {"name": "Peter Petersen", "title": "Chef for Digitalisering og Udvikling",
    "company": "Danske Bank", "department": "AI Center of Excellence",
    "role": "Leder", "focus": "…", "why": "…", "job_title": "<opslagets titel>",
    "job_link": "<opslagets link>", "email": "", "phone": ""}
   ```
   - Kun personer der står med navn i annoncen. Ingen opslag, ingen gæt, ingen søgning andre steder.
   - `role`: `Leder` (kontaktperson eller kommende leder i afdelingen), `HR og rekruttering`
     (virksomhedens egen HR/talent acquisition), `Rekrutteringsbureau` eller `Andet`.
   - `focus`: én til to sætninger med det, annoncen fortæller om afdelingens initiativer,
     prioriteter eller udfordringer (fx en AI-enhed under opbygning, et ERP-skifte, nye
     DORA-krav). Konkrete ting Mads kan nævne i en henvendelse. Tom streng, hvis annoncen
     ikke siger noget konkret. Ingen generiske vendinger om "spændende rejse" o.l.
   - `why`: én sætning om, hvorfor personen er relevant for Mads, koblet til hans profil i
     `CANDIDATE_PROFILE` (fx "Leder porteføljestyring i IT; svarer til hans governance-arbejde").
   - `email` og `phone` kun hvis de står i annoncen.
   Er der ingen relevante personer, så skriv `[]`.

5. Byg siden:
   ```bash
   python render_page.py --new new.json --ratings ratings.json --contacts contacts.json \
       --history <arbejdsmappe>/jobs.json --out-dir site
   ```

6. Publicér til den eksisterende side (samme URL, ingen ny side):
   Artifact `action: "publish"`, `url: <resultatside>`, `file_path: site/index.html`,
   `files: {"jobs.json": "site/jobs.json"}`. Udelad `icon` og `capabilities`
   (siden har database-capability; udeladt bevares den, og Mads' gemte opslag med den).

7. Tjek Mads' gemte opslag. Siden gemmer markeringer i sin database (samlingen `marks`).
   - Hent dem: ArtifactData `action: "list"`, `url: <resultatside>`, `collection: "marks"`,
     `out_dir: marks`. Er samlingen tom, så spring resten af trinnet over.
   - Gem list-svarets tekst (linjerne med `"<doc_id>" … version N`) ordret i
     `marks/versions.txt` med Write; versionerne står kun i svaret, ikke i filerne.
   - Kør `python check_saved.py marks --versions marks/versions.txt --out mark_updates.json`.
     Scriptet besøger hvert gemt opslag og afgør, om det stadig er slået op.
   - Er `mark_updates.json` ikke tom, så send dens liste som `writes` i ArtifactData
     `action: "batch"` (højst 50 pr. kald). Afvises batchen pga. version (Mads har ændret en
     markering imens), så hent listen igen og kør scriptet én gang til. Ændr kun `check`.

8. Opdatér kontaktarket (Google Sheet "Kontaktpersoner fra jobopslag", id `1K9E-a0IZpDEv5fE4SX75pGPYBm0APTbCAnDBhMv31_k`,
   fanen `Kontakter`). Kræver Google Sheets-forbindelsen.
   - Første gang: læs `get_spreadsheet` med `fields: ["sheets.properties"]`. Hedder første fane
     ikke `Kontakter`, så omdøb den (`updateSheetProperties`, `fields: "title"`) og formatér i
     samme `update_spreadsheet`-kald: fast række 1, fed overskrift, tekstombrydning i kolonne
     F, G og H, og bredder A–E 160, F–G 320, H 240, I 120, J–L 90, M–O 160, P–Q 140 pixels.
   - Læs arket: Sheets `get_values`, `range: "'Kontakter'!A:Q"`. Gem svaret i `sheet.json`.
   - Flet: `python merge_contacts.py --new site/jobs.json --existing sheet.json --out sheet_values.json`.
     Scriptet tager kontakter fra alle kørsler i historikken, så en dag hvor arket ikke kunne
     skrives, bliver indhentet. Samme person samles i én række; Status og Noter røres ikke.
   - Skriv: Sheets `update_values` med `range` og `values` fra `sheet_values.json`.
   - Læs de første rækker igen med `get_values` og tjek, at overskriften står i række 1.
   Er Google Sheets-værktøjerne ikke tilgængelige, så spring trinnet over og skriv det i
   opsummeringen.

9. Afslut med én linje: antal nye opslag og fordelingen grøn/gul/rød, eventuelle
   kilder der gav 0 opslag, antal gemte opslag tjekket (heraf taget ned), og antal nye
   kontaktpersoner i arket.

Ændr ikke koden i repoet under en planlagt kørsel.
