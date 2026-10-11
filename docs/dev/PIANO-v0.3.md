# Piano v0.3: chat, progetti e Attività affidabili

Basato su `docs/ANALISI-APP-CLAUDE.md`. **Stato: APPROVATO dall'utente** (istruzione esplicita «approvalo, eseguilo passo passo»).

## Decisioni prese al posto delle risposte mancanti (correggibili)
| Domanda aperta | Default scelto |
|---|---|
| Cosa significa «5 più recenti» | **5 chat più recenti per progetto**, con «Mostra tutte (N)». |
| Terza cartella | Non determinabile dai dati: compare appena ha una chat o un evento. La diagnostica dirà cosa ccmail vede. |
| UI «poco chiara» | Gerarchia esplicita: **Progetto → Chat → (ultima sessione, in secondo piano)**; niente id in primo piano; stato in una frase; una sola azione per riga («Eventi»). |

## Passi (ognuno con test e criterio di accettazione)

| # | Passo | Criterio di accettazione | Chi |
|---|---|---|---|
| 1 | **Scoperta metadati multi-percorso**: pacchetto `Claude_*\LocalCache\Roaming\Claude` + `%APPDATA%\Claude`, unione per `sessionId`, override da env. | Con Python del Store trova le 6 chat reali. Test con due radici fittizie. | io |
| 2 | **Titolo di riserva dal transcript** (`customTitle`, lettura dalla coda del file, cache per mtime). | Una sessione senza chat nell'app ottiene un titolo; nessuna lettura completa di file grandi. | io |
| 3 | **Progetto stabile**: chat → `CLAUDE_PROJECT_DIR` → radice dedotta dalla cartella dei transcript (percorso più corto tra le `cwd` della stessa cartella) → `cwd`. Colonna `tdir`. | «uscite» non esiste più come progetto; sotto-agenti esclusi. | io |
| 4 | **Riparazione dati** all'avvio: riallinea sessioni esistenti, ricostruisce `projects` (conserva gli interruttori), elimina progetti senza sessioni né chat. | DB reale simulato (copia): 2 progetti, 6 chat. | io |
| 5 | **API**: `GET /v1/projects?chats=5`, `chats_total`, `chats_hidden`; `GET /v1/diagnostics`; comando `ccmail diagnose`. | Test API; `diagnose` su una home temporanea mostra le 6 chat reali. | io |
| 6 | **UI Attività + pannello Diagnostica** (Connessione). | Verifica a video su demone isolato con dati finti che riproducono la struttura reale. | **Opus** (UI) |
| 7 | **Verifica end-to-end sui dati reali** in sola lettura (home temporanea, mai il DB vivo). | Report con numeri prima/dopo. | io |
| 8 | **Documentazione**: README, report aggiornato, CHANGELOG. | Testi coerenti con il comportamento. | **Haiku** (economico) |

## Delega e risparmio token
- Lavoro di logica/DB: lo faccio io (contesto già pieno, costo minimo).
- UI (passo 6): agente **Opus** con contratto API preciso.
- Testi/doc (passo 8): agente **Haiku**.
- Se un passo si blocca due volte: consulto un agente Opus con il problema isolato.

## Rischi
- Formato interno dell'app: tutto «soft-fail», mai bloccante; test con file malformati.
- Virtualizzazione MSIX: ogni percorso esistente è provato; nessuna scrittura fuori dalla cartella di ccmail.
- Migrazione DB: v3 → v4 con colonne nuove, nessuna perdita di dati.
