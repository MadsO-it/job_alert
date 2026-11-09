# Job alert via GitHub Actions (mail)

Workflowet `.github/workflows/job-alert-gurlin.yml` kører hver mandag morgen, scraper
Jobindex og psykologjob.dk, lader Claude inddele opslagene i grøn/gul/rød og sender en mail.
Det kører på GitHubs servere, så ingen computer skal være tændt.

## Engangsopsætning

Repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Indhold |
|---|---|
| `GURLIN_PROFILE` | Hele profil-JSON'en (indsættes som den er) |
| `GURLIN_EMAIL` | Modtageradressen |
| `SENDER_EMAIL` | Afsenderadressen (One.com-kontoen) |
| `SMTP_PASSWORD` | Kodeordet til afsenderkontoen |
| `ANTHROPIC_API_KEY` | API-nøgle fra console.anthropic.com |

SMTP-server er som standard `send.one.com:587`. Bruges en anden, så sæt *variables*
`SMTP_HOST` og `SMTP_PORT` samme sted (fanen Variables).

## Test

Actions → **Job Alert (Gurlin)** → **Run workflow**. Med "Byg mailen uden at sende den"
slået til køres alt undtagen afsendelsen, og loggen viser antal opslag pr. kilde og farve.
Uden den sendes en rigtig mail. Første mail bør markeres som "ikke spam" hos modtageren.

## Hvordan det hænger sammen

- **Profilen** ligger kun som secret, aldrig i repoet. Formatet er beskrevet ved
  `apply_profile` i `job_alert.py`. Den kan ændres når som helst ved at opdatere secret'en.
- **Dublethukommelsen** gemmes i Actions-cachen (`state/`). GitHub sletter cache, der ikke er
  brugt i 7 dage, så den kan forsvinde mellem to ugentlige kørsler. Derfor køres med
  `--since 8`: kun opslag fra de sidste 8 dage medtages. psykologjob.dk har ingen
  opslagsdato, så forsvinder cachen, kan opslag derfra blive sendt igen.
- **Logs er offentlige**, fordi repoet er offentligt. Scriptet skriver søgeord og antal
  opslag i loggen, men ikke profilteksten, og secrets maskeres af GitHub.
- **Fejl**: hvis mailen ikke kan sendes, fejler kørslen, og GitHub sender en notifikation
  til den, der sidst har ændret workflowets tidsplan.
- **60 dage**: GitHub slår planlagte workflows fra i offentlige repoer, hvis der ikke har været
  aktivitet i repoet i 60 dage. Så skal workflowet slås til igen under Actions.

## Flere personer

Kopiér workflow-filen, ret navn, secret-navne og cache-nøgle (`seen-gurlin-`), og giv
profilen sin egen `seen_file`.
