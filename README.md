# Job Alert

Et personligt jobsøgnings-værktøj. Scraper IT Jobbank og StepStone, lader Claude vurdere relevansen af hvert opslag op mod min profil, og sender et ugentligt mail-digest med kun de relevante jobs.

Bygget for at slippe for at scrolle jobsider hver dag. Ren personlig automation. Det er min kandidat-profil i koden: (nyuddannet cand.it i IT-ledelse fra AAU, juni 2026). Hvis du vil bruge det selv, skal `CANDIDATE_PROFILE`-strengen i `job_alert.py` tilpasses din egen baggrund.

## Hvad det gør

1. Henter aktuelle jobopslag fra IT Jobbank og StepStone (begge har offentlige søgeresultater)
2. Filtrerer groft på geografi (Storkøbenhavn) og kategori
3. Sender hvert opslag til Claude (claude-sonnet) i batches af 10 og beder om en ja/nej-vurdering ud fra min profil
4. Husker hvilke opslag der allerede er sendt (`seen_jobs.json`) så du ikke får dubletter
5. Sender en HTML-mail med de godkendte opslag

Køres typisk én gang om ugen via `launchd` på macOS eller `cron` på Linux.

## Hvorfor

Job-aggregatorer er fyldt med støj. AI-filtrering på en individuel kandidat-profil er klart bedre end keyword-matching, og 50–100 opslag om ugen koster under 10 cent i Claude-kald. Det her løser et konkret personligt problem og undgår at jeg overser noget vigtigt fordi jeg ikke gad åbne IT Jobbank den dag.

## Tech

Python 3.10+, kun to dependencies:

- `requests` til scraping
- `python-dotenv` til at læse `.env`

Mail sendes via `smtplib` (stdlib) som fungerer med en hvilken som helst SMTP-server. Default-konfigurationen er One.com.

## Opsætning

```bash
# 1. Klon og installer
git clone https://github.com/MadsO-it/job_alert.git
cd job_alert
pip install -r requirements.txt

# 2. Konfigurér miljø
cp .env.example .env
# rediger .env med din mail, SMTP og Anthropic-nøgle

# 3. Kør første gang manuelt
python job_alert.py
```

Første kørsel beder om SMTP-password hvis det ikke er sat i `.env` (og gemmer det base64-obfusceret i `.credentials` så du ikke skal indtaste hver gang). Tilsvarende for Anthropic-nøglen.

## Ugentlig kørsel

På macOS, eksempel `launchd` plist (`~/Library/LaunchAgents/com.mads.joballert.plist`):

```xml
<dict>
    <key>Label</key>
    <string>com.mads.joballert</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/sti/til/job_alert/job_alert.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>1</integer>
        <key>Hour</key><integer>9</integer>
    </dict>
</dict>
```

Indlæs med `launchctl load ~/Library/LaunchAgents/com.mads.joballert.plist`.

## Tilpasning

Det meste står i toppen af `job_alert.py`:

| Hvad | Hvor i koden |
|---|---|
| Søgeord til IT Jobbank/StepStone | `IT_JOBBANK_QUERIES`, `STEPSTONE_QUERIES` |
| Geografisk filtrering | `CPH_LOCATIONS`, `EXCLUDED_LOCATIONS` |
| Profil til AI-vurdering | `CANDIDATE_PROFILE` |
| Mail-skabelon | `build_html_email`, `build_plain_text_email` |

## Begrænsninger

- Jobnet og LinkedIn er ikke understøttet da de ikke synes at kunne scrapes
- Scraping bryder formentlig hver service's TOS, så brug det i moderate mængder og til personlig brug
- AI-vurderinger er ikke perfekte, så nogle opslag er måske mindre relevante. 

## Bygget med Claude

Dele af koden er skrevet i samarbejde med Claude.
