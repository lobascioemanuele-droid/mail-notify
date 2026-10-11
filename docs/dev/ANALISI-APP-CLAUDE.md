# Analisi dell'app desktop di Claude: dove sta cosa e perché la webapp ccmail mostrava dati sbagliati

Data: 2026-10-10 · Autore: Claude (sessione ccmail) · Stato: **solo osservazioni, nessuna modifica al codice**.

## 0. Metodo e limiti

- Ho ispezionato file reali sul tuo PC in **sola lettura**: elenchi di file, nomi di campo, un sottoinsieme di valori
  non sensibili (id, date, cartelle, titoli troncati a ~38 caratteri). Non ho letto contenuti di conversazione né credenziali.
- Ogni affermazione è marcata: **[VERIFICATO]** (visto sui tuoi dati), **[DEDOTTO]** (conseguenza logica, non provata),
  **[IGNOTO]** (non so).
- Il formato dei file dell'app è interno e non documentato: potrebbe cambiare a ogni aggiornamento.

## 1. Come è fatta l'app desktop

| Fatto | Stato |
|---|---|
| L'app è un pacchetto Windows (MSIX/Store) `Claude_pzs8sxrjxfjjc`, basata su Electron (cartelle `Cache`, `GPUCache`, `IndexedDB`, `Local Storage`, `Crashpad`…). | VERIFICATO |
| Il suo profilo è in `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\`. Un processo non "pacchettizzato" (PowerShell) lo vede anche in `%APPDATA%\Claude\` con gli stessi file e le stesse date. | VERIFICATO |
| **Python del Microsoft Store è anch'esso pacchettizzato**: per lui `%APPDATA%` è un'altra cartella virtuale (la sua `LocalCache\Roaming`). Da lì `…\Roaming\Claude\claude-code-sessions` **non esiste** (0 file), mentre nel percorso del pacchetto Claude ci sono tutti i 12 file. | VERIFICATO (confronto diretto, stessi nomi e dimensioni) |
| Il Claude Code incluso nell'app sta in `claude-code\` (6 file, ~500 MB: eseguibili versionati). | VERIFICATO |
| `local-agent-mode-sessions\` (822 file) contiene cache di *skills/plugin* (`.xsd`, `.py`, `.md`, `.ttf`) e `manifest.json`: **non** sono chat di Claude Code. | VERIFICATO |
| `git-shadow\`, `spaces-present\`: pochi file minuscoli, non rilevanti. | VERIFICATO |

## 2. Dove sta ciò che serve a ccmail

### 2.1 Le chat dell'app (nome, progetto, archiviata)

Percorso: `<profilo Claude>\claude-code-sessions\<idAccount>\<idOrg>\local_<uuid>.json` — un file per chat. **[VERIFICATO]**

Campi utili (gli altri, es. `promptAppendSnapshot`, non vengono letti): 

| Campo | Significato | Stato |
|---|---|---|
| `sessionId` (`local_<uuid>`) | id della **chat** nell'app | VERIFICATO |
| `cliSessionId` | id della sessione di Claude Code = il `session_id` che ricevono gli hook | VERIFICATO (corrisponde ai transcript e ai nostri eventi) |
| `title`, `titleSource` (`auto` / `user`) | **nome della chat come lo vedi nell'app** | VERIFICATO |
| `originCwd`, `cwd` | cartella del progetto; nelle tue 6 chat coincidono | VERIFICATO |
| `isArchived` | chat archiviata | VERIFICATO (tutte `False` oggi) |
| `createdAt`, `lastActivityAt` | millisecondi epoch | VERIFICATO |
| `completedTurns` | turni completati | VERIFICATO |

Altri file nella stessa cartella: `deleted_<uuid>` (13 byte, contiene un timestamp: marcatore di chat cancellata, 2 nel tuo caso) e
`scheduled-tasks.json` (125 byte, per organizzazione). Ci sono **due** cartelle account (`3bbebaa6…` con le chat, `6827c66a…` solo con
task pianificati). **[VERIFICATO]**

### 2.2 I transcript di Claude Code

Percorso: `~/.claude/projects/<percorso codificato>/<session_id>.jsonl` (+ `…/<session>/subagents/agent-*.jsonl`). **[VERIFICATO]**

- Accessibile normalmente anche da Python del Store (la home non è virtualizzata). **[VERIFICATO]**
- Contiene record `custom-title` (campo `customTitle`) e `agent-name`: **il titolo della chat è presente anche nel transcript**,
  ripetuto molte volte (86 volte nel più recente). **[VERIFICATO l'esistenza; DEDOTTO che coincida con `title` dell'app: la lunghezza è
  uguale (36), il contenuto non l'ho confrontato]**
- I transcript dei sotto-agenti (`agent-*`) **non** sono sessioni dell'utente e vanno esclusi. **[VERIFICATO]**

### 2.3 Gli eventi degli hook

Già noti dalla documentazione e dalla traccia: `session_id`, `cwd`, `transcript_path`, ecc. Il progetto stabile è
`CLAUDE_PROJECT_DIR` (variabile d'ambiente dell'hook, non nel JSON). **[IGNOTO]** se l'app desktop la imposta per gli hook.

## 3. I tuoi dati reali

Chat nell'app (da metadati): **6**, in **2 progetti**.

| Progetto (`originCwd`) | Chat | Titoli (troncati) | Ultima attività |
|---|---|---|---|
| mail-notify | 1 | «ccmail: daemon notifiche Claude Co…» (auto) | 10-10 16:22 |
| acme - Copy | 5 | «T3b WP1 implementation» (user), «T3 WP1 Test Ottimizzazione Prophet» (user), «Acme DSS onboarding» (auto), «T3 WP1 implementation» (auto), «Verifica plan T3 Wp1» (auto) | 10-10 16:05 … 10-07 |

- **Chat cancellate**: 2 (`deleted_*`). **[VERIFICATO]**
- **Transcript senza chat nell'app** (esclusi i sotto-agenti): 2 sessioni del 10-05, probabilmente aperte da terminale o in
  chat poi cancellate. **[DEDOTTO]**
- Relazione chat↔sessione: in tutte e 6 le chat c'è **un solo** `cliSessionId` (1:1). Non ho mai visto una chat che
  cambia id di sessione. **[VERIFICATO sui dati; IGNOTO se accade con ripresa/compattazione]**

## 4. Perché la webapp era sbagliata: le tre cause (con prove)

### 4.1 «uscite» non è un progetto
Nel database di ccmail la sessione `e47891a4` aveva `cwd = …\uscite`. Nell'app la stessa chat («T3 WP1 Test Ottimizzazione Prophet»,
`cliSessionId = e47891a4`) ha `originCwd = acme - Copy`. Quindi **«uscite» è una sottocartella in cui Claude si è spostato**, non una
cartella di progetto. Il vecchio codice usava la cartella corrente come "progetto". **[VERIFICATO]**

### 4.2 I nomi delle chat non comparivano
La lettura dei metadati cercava `%APPDATA%\Claude\claude-code-sessions`. Dal processo del demone (Python del Store) quel
percorso non esiste, quindi `chats` nel database ha **0 righe** e nessuna sessione è stata collegata a una chat. La correzione
non è nella webapp ma nella ricerca del percorso. **[VERIFICATO: stesso script, percorso A = 0 file, percorso pacchetto = 12 file]**

### 4.3 Chat «mancanti»
Conseguenza di 4.2: senza metadati la vista mostrava solo le sessioni viste dagli hook (4 righe, non 6 chat). Non c'era alcun limite
di visualizzazione: il tetto «5 più recenti» che indichi **non è ancora implementato** (neanche lo avevo nel contratto).
**[VERIFICATO sul database; limite da implementare]**

## 5. Cosa NON so (e come lo verificherei)

1. **La tua terza cartella.** Nei metadati ci sono 2 progetti; nei transcript 2 cartelle; nello storage dell'app (Local Storage,
   IndexedDB) una ricerca di percorsi non ha trovato nulla (dati binari/compressi). Se ne vedi 3 nell'app, probabilmente la terza non ha
   ancora chat. → Dimmi i nomi che vedi nella barra laterale dell'app.
2. `CLAUDE_PROJECT_DIR` negli hook dell'app desktop → registrarlo nella traccia diagnostica.
3. Titolo dei transcript = titolo dell'app? → confronto diretto in un test.
4. Se una chat cambia `cliSessionId` dopo ripresa/compattazione → osservazione nel tempo (il demone già conserva lo storico).
5. A cosa si riferisce esattamente «5 più recenti»: per progetto (lo suppongo) o in totale?

## 6. Correzioni proposte (non ancora eseguite)

| # | Intervento | Motivo |
|---|---|---|
| 1 | Cercare i metadati in **tutti** i percorsi candidati: `…\Packages\Claude_*\LocalCache\Roaming\Claude\…` e `%APPDATA%\Claude\…`, unendo i risultati per `sessionId`. | 4.2 |
| 2 | Titolo di riserva dal transcript (`customTitle`, lettura dalla coda del file, con cache) per sessioni fuori dall'app o se i metadati mancano. | 2.2 |
| 3 | Il progetto di una sessione = progetto della chat (`originCwd`), poi `CLAUDE_PROJECT_DIR`, **mai** la sottocartella; le righe già salvate vengono riallineate. | 4.1 |
| 4 | Mostrare al massimo **5 chat più recenti per progetto**, con «mostra tutte (N)». | richiesta |
| 5 | Nascondere sessioni di sotto-agenti e ripulire i progetti fantasma già nel database (`uscite`). | 4.1 |
| 6 | Una **diagnostica** nella webapp (Connessione): dove ho trovato i metadati, quante chat, quante sessioni collegate/non collegate. | evitare di andare alla cieca |
| 7 | Rivedere la UI di Attività con te su uno schizzo, prima di scrivere codice (vedi domande). | «poco chiaro come iterazione UI» |

## 7. Esito (v0.3)

Stato: implementato e coperto da test (107 test). Ogni sintomo della sezione 4 è mappato alla correzione.

| Sintomo (sezione 4) | Causa | Correzione in v0.3 | Stato |
|---|---|---|---|
| 4.1 «uscite» comparisce come progetto | Il progetto era la cartella corrente, cioè una sottocartella in cui Claude si era spostato | Il progetto è la cartella della chat (`originCwd`), poi `CLAUDE_PROJECT_DIR`, poi la radice dedotta dalla cartella dei transcript; le sottocartelle non sono mai progetti. All'avvio le righe salvate vengono riallineate e i progetti fantasma rimossi (conservando gli interruttori). Le sessioni dei sotto-agenti sono escluse. | Implementato, con test |
| 4.2 I nomi delle chat non compaiono | Il percorso `%APPDATA%\Claude` non è visibile al Python del Microsoft Store (pacchetto MSIX): 0 chat collegate | Ricerca dei metadati in `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code-sessions` e in `%APPDATA%\Claude\claude-code-sessions`, unione per id di sessione; override con `CCMAIL_CLAUDE_SESSIONS`. Se i metadati mancano, titolo di riserva dal `custom-title` in coda al transcript. Le mail mostrano `Chat: <nome>`. | Implementato, con test |
| 4.3 Chat «mancanti» e nessun limite di visualizzazione | Conseguenza di 4.2 (solo 4 sessioni dagli hook); il tetto di 5 chat non esisteva | `GET /v1/projects?chats=5` mostra le 5 chat più recenti per progetto (non archiviate), con `chats_total` e `chats_hidden` per «mostra tutte (N)»; `GET /v1/diagnostics` e `ccmail diagnose` mostrano dove sono stati trovati i metadati e quante chat e sessioni sono collegate. | Implementato, con test |

Limiti ancora aperti (sezione 5): il formato dei file dell'app resta interno e non documentato, quindi ogni lettura è «soft-fail» (se il formato cambia, ccmail usa i dati degli hook); una chat potrebbe in teoria cambiare `cliSessionId` dopo ripresa o compattazione (non osservato). Dopo l'aggiornamento: `ccmail install-hooks` e riavvio di Claude Code (nuovo hook `PostToolBatch`).
