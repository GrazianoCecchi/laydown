# Changelog

All notable changes to Laydown (formerly PressReady) are documented here.

---

## [Unreleased]

- **Risoluzione driver PostScript & HP UPD per stampa A3 reale (1:1) e layout anteprima:**
  - **Sincronizzazione in-memory dei token proprietari HP UPD (`laydown/ui/win_print.py`):** risolto il problema critico per cui le stampanti con driver HP Universal Print Driver (PostScript) forzavano l'output su A4 riducendo l'A3. L'analisi dello spooler raw (`.prn`) ha evidenziato che la chiamata Winspool `DocumentPropertiesW` senza GUI cancellava i dati privati reimpostando il token binario `PageSize\x00A4\x00`. Il nuovo merger sincronizza direttamente la memoria `DEVMODEW` pubblica e allinea i token proprietari `PageSize` e `HPOrientation` nel buffer privato `dmDriverExtra` mantenendo rigorosamente coerente l'header di lunghezza (`dmDriverExtra`).
  - **Rimozione di qualsiasi passthrough grezzo a livello di pagina:** eliminata la chiamata a basso livello `POSTSCRIPT_PASSTHROUGH` (4115) che, iniettando comandi di selezione formato carta durante il ciclo attivo della pagina, mandava in errore irreversibile l'interprete PostScript firmware della stampante provocandone il riavvio hardware. L'emissione PostScript DSC è ora delegata in modo pulito e nativo al driver Windows GDI.
  - **Sblocco del ridimensionamento della colonna di sinistra (`laydown/ui/print_dialog.py`):** rimosso il vincolo fisso `fixedWidth(340)` che bloccava il divisore dell'anteprima; introdotto `minimumWidth(330)` con proporzioni iniziali dello splitter `[380, 740]`, permettendo all'utente di allargare a piacimento la colonna delle impostazioni.
  - **Correzione overflow del badge verde informativo:** abilitato il word wrap automatico sui testi del riquadro di stato e sintetizzata la dicitura (*"Driver pronto: Carta A3 (1:1)"* / *"Stampa reale 1:1 a grandezza naturale (100% senza riduzioni) su carta A3."*) per eliminare qualsiasi sbordamento orizzontale.

- **Integrazione diretta con il driver di stampa nativo e spooler GDI Windows:**
  - **Bypass completo della finestra generica di Windows (`laydown/ui/win_print.py`):** il pulsante *"Proprietà driver…"* chiama direttamente l'API Winspool `DocumentPropertiesW` con flag `DM_IN_PROMPT`, aprendo istantaneamente la finestra nativa delle preferenze proprietarie del costruttore (HP, Canon, Lexmark, Xerox, Epson, ecc.), eliminando del tutto la finestra di dialogo generica di Windows con l'anteprima vuota.
  - **Sincronizzazione forzata del formato A3 e orientamento nel DEVMODE del driver:** il formato carta scelto (A3 `dmPaperSize = 8`, A4, SRA3, Tabloid, ecc.), l'orientamento (orizzontale/verticale), il fronte/retro (duplex) e le copie configurati in Laydown vengono forzati direttamente nella struttura binaria `DEVMODE` del driver prima di aprire le preferenze e prima dell'invio allo spooler.
  - **Spooling diretto Windows GDI (`CreateDCW` / `StretchDIBits`):** l'invio della stampa su Windows crea direttamente il Device Context (`WINSPOOL`) con il `DEVMODE` attivo configurato a 420 × 297 mm A3, bloccando alla radice il comportamento errato dei driver di stampa che forzavano la carta predefinita A4 con autoridimensionamento. I fogli vengono rasterizzati tramite PyMuPDF a risoluzione nativa (150 DPI per bozza rapida, 600/1200 DPI per produzione) e inviati con `StretchDIBits` compensando al millimetro i margini fisici non stampabili dei rulli.
  - **Sincronizzazione bidirezionale tra driver e interfaccia:** le eventuali modifiche apportate dall'utente all'interno della finestra di configurazione nativa del driver (selezione cassetto, carta, orientamento, duplex, copie) vengono rilette al click su OK e riallineate istantaneamente sui controlli dell'anteprima di Laydown.
  - **Badge visivo di conferma configurazione ottimale:** l'interfaccia mostra un avviso verde di conferma (*"Driver configurato: A3 — Stampa 1:1 a grandezza naturale (nessuna riduzione)"*) quando foglio imposto e carta della stampante combaciano, dando assoluta certezza all'operatore.
  - Suite di test ampliata a 27 test specifici in `tests/test_print_dialog.py`.

- **Procedura di stampa integrata e finestra professionale di anteprima di stampa:**
  - **Modulo dedicato di stampa (`laydown/ui/print_dialog.py`):** creata una finestra di dialogo completa per la stampa di produzione (`PrintDialog`), stilizzata in modo coerente con il dark theme OKLCH dell'applicazione (`laydown.ui.theme`).
  - **Doppia modalità di stampa: Bozza Veloce (150 DPI) e Alta Definizione (600/1200 DPI):**
    - Aggiunto il pulsante dedicato **⚡ Bozza Veloce (150 DPI)** che rasterizza ed invia allo spooler in 1-2 secondi, permettendo all'utente di verificare rapidamente crocini, pieghe e allineamento sulla carta fisica senza attendere i tempi lunghi della stampa ad alta risoluzione.
    - Pulsante primario **🖨️ Stampa Alta Definizione** per il lancio definitivo della produzione tipografica a 600 o 1200 DPI.
  - **Finestra di stampa persistente (nessuna chiusura automatica indesiderata):**
    - Eliminata la chiusura automatica modale (`accept()`) e rimosso il popup bloccante al termine dell'invio allo spooler: la finestra rimane aperta mostrando lo stato aggiornato in tempo reale (`"✅ Stampa inviata con successo allo spooler"`), consentendo all'utente di stampare una bozza, verificare i fogli, modificare le opzioni e lanciare la stampa definitiva. La chiusura avviene manualmente tramite il pulsante *"Chiudi"* o la `[X]`.
  - **Risoluzione mismatch formato carta A3/A4 e abbinamento intelligente:**
    - Introdotto l'algoritmo [`find_best_matching_paper_size`](file:///C:/Users/a125756/Documenti-GRA/Programmazione/Progetti_python/laydown/laydown/ui/print_dialog.py) che riconosce prioritariamente il formato A3 del progetto ed evita fallback anomali su formati come "Legale" sulle stampanti desktop che caricano A4.
    - Selezionando una stampante che supporta solo l'A4 per un progetto A3, il sistema imposta automaticamente la modalità di bozza scalata (*"Adatta al foglio di stampa"*) con avviso visivo azzurro chiaro esplicativo, consentendo di visualizzare l'intero foglio A3 ridotto sulla carta A4 o di passare al 100% per un ritaglio a dimensione naturale. Su stampanti A3, il formato viene bloccato a grandezza naturale (1:1).
  - **Pannello di controllo prestampa unificato:**
    - **Selezione stampante:** rileva in tempo reale tutte le periferiche disponibili (`QPrinterInfo`), evidenziando la stampante predefinita e fornendo accesso diretto alle proprietà avanzate del driver di sistema.
    - **Fogli e intervallo:** supporto per stampa di tutti i fogli, foglio corrente o intervallo personalizzato con sintassi flessibile (es. `1-3, 5`), con validazione visiva istantanea.
    - **Copie e fascicolazione:** controllo del numero di copie e opzione di fascicolazione.
    - **Fronte / Retro (Duplex):** selezione rapida tra *Solo fronte*, *Fronte/retro - Lato lungo* e *Fronte/retro - Lato corto*, con rilevamento e preimpostazione automatica per opuscoli e documenti a più fogli.
    - **Carta e orientamento:** interrogazione dei formati supportati dalla stampante (`QPageSize`), con abbinamento automatico alle dimensioni del foglio imposto e orientamento (automatico, orizzontale o verticale).
    - **Geometria e scala di prestampa:** opzioni essenziali per la grafica professionale: *100% Dimensioni reali (Do Not Scale)* per preservare millimetricamente rifili e crocini, *Adatta all'area stampabile* e *Riduci solo se sovradimensionato*, con centratura automatica sul foglio.
    - **Avviso visivo margini non stampabili:** rilevamento e avviso automatico (badge card) se le dimensioni del foglio imposto o i crocini di taglio eccedono l'area utile o i margini fisici dei rulli della stampante selezionata.
    - **Qualità di stampa:** selezione della risoluzione di rasterizzazione allo spooler (300 DPI per bozze veloci, 600 DPI per alta definizione, 1200 DPI per massima qualità).
  - **Anteprima di stampa interattiva in tempo reale (`QPrintPreviewWidget`):**
    - Visualizzazione fluida dei fogli imposti renderizzati ad alta precisione tramite PyMuPDF.
    - Controlli completi di zoom (*Ingrandisci*, *Riduci*, *Adatta pagina*, *Adatta larghezza*, *100%*).
    - Navigazione tra i fogli con indicatore del foglio corrente.
    - Modalità di visualizzazione a pagina singola o pagine affiancate (*Facing pages* per confrontare fronte e retro).
    - Riquadro tratteggiato opzionale per mostrare a video i margini fisici non stampabili della periferica.
  - **Spooling asincrono su thread separato (`_PrintWorker`):** rasterizzazione ed invio allo spooler di stampa eseguiti in background su `QThread` con barra di progresso e possibilità di annullamento, senza mai congelare l'interfaccia grafica.
  - **Punti di accesso nell'interfaccia:**
    - Aggiunta scorciatoia da tastiera globale **Ctrl+P** e voce di menu **File → Stampa…** con icona Lucide `print`.
    - Aggiunto pulsante rapido **Stampa…** nel footer della barra laterale sinistra accanto a *Genera PDF*.
  - Aggiunta la suite di test dedicata in `tests/test_print_dialog.py`.

- **Pagine consecutive con spaziature side-by-side e segnaposto per pagine mancanti:**
  - **Spaziature affiancate identiche a Step & Repeat:** nella modalità *"Pagine consecutive"* (`RepeatMode.OFF`), le pagine a dimensione reale (`ACTUAL_SIZE`) vengono posizionate affiancate rispettando al millimetro le impostazioni dei gutter orizzontale e verticale (`gutter_h_mm` e `gutter_v_mm`), con l'intero gruppo di celle centrato all'interno del foglio sottraendo i margini. Eliminato il distanziamento artificiale causato dalla suddivisione nominale del foglio su celle oversize.
  - **Segnaposto simulato per pagine mancanti sul foglio:** quando il documento sorgente non ha un numero sufficiente di pagine per completare tutte le pose del foglio (ad esempio un biglietto da visita da 1 sola pagina imposto su griglia 2×5 o un numero dispari di pagine sull'ultimo foglio), le celle rimanenti vengono popolate da un segnaposto (`is_placeholder=True`) che simula la grafica dell'ultima pagina inserita.
  - **Crocini di taglio completi:** la presenza del segnaposto assicura che i crocini di taglio (`draw_marks`) vengano tracciati per l'intera griglia di pose del foglio, permettendo il taglio regolare dell'intero cartoncino stampato.
  - **Evidenziazione visiva nell'anteprima:** nella preview a video (`preview_panel.py`), le pose segnaposto vengono chiaramente distinte da una cornice tratteggiata (`DashLine`) e dal numero di pagina con asterisco (es. `1*`).
  - Aggiunta la suite di test dedicata in `tests/test_layout_depth.py` (`TestSequentialPagesCompactAndPlaceholder`).

## [0.4.8] - 2026-10-26

- **Installer Windows (.exe) e supporto Inno Setup:**
  - Creato lo script di installazione Inno Setup (`packaging/windows/installer.iss`) con compressione ultra (LZMA2), wizard moderno, lingua italiana e inglese, collegamenti nel menu Start e sul Desktop, e programma di disinstallazione.
  - Aggiornato `packaging/windows/build.ps1` per rilevare automaticamente Inno Setup Compiler (`ISCC.exe`) e generare `dist/Laydown-0.4.8-windows-x64-setup.exe` accanto al pacchetto portatile `.zip`.
  - Compilato con successo il bundle e generati i pacchetti di rilascio completi per Windows x64.

- **Controlli numerici con frecce (SpinBox) e impostazione minima per Griglia personalizzata:**
  - **Frecce di incremento/decremento visibili per tutti gli SpinBox:** implementata la generazione e renderizzazione vettoriale (`_spin_arrow_png`) dei chevron `up` e `down` nel tema QSS dell'applicazione (`laydown.ui.theme`), garantendo che `QSpinBox` e `QDoubleSpinBox` mostrino chiaramente le frecce su/giù stilizzate con hover ed eliminando l'aspetto piatto da casella di testo.
  - **Inizializzazione a 1 per Griglia personalizzata:** selezionando *"Griglia personalizzata (Righe × Colonne)"*, i campi **Colonne** e **Righe** vengono automaticamente impostati a `1` sia nello store che nell'interfaccia.
  - **Blocco e controllo anti-zero:** impostato il valore minimo rigoroso a `1` (`minimum=1`) sia a livello di schema (`schema.py`), che di widget (`QSpinBox.setRange(1, 50)`), che di logica (`panel.ValueStore` e `geometry.grid_for`). La freccia giù non consente di scendere sotto 1 e non è possibile inserire valori nulli o negativi.
  - Aggiunti test di regressione in `tests/test_panel.py` e `tests/test_icons.py`.

- **Opzione Crocini di taglio solo perimetrali (esclusione crocini interni):**
  - Aggiunta l'opzione `crop_outer_only` in `MarkItem` e nell'interfaccia utente (scheda **Segni** > **Crocini di taglio** > casella di spunta *"Solo perimetrali (escludi interni)"*).
  - Quando attiva, il motore di imposizione (`laydown.engine.marks._draw_crop_marks_outer`):
    - Disabilita ed esclude completamente tutti i trattini interni compresi tra le pagine/copie adiacenti della griglia;
    - Traccia esclusivamente i crocini perimetrali rivolti verso l'esterno (nei margini del foglio) in corrispondenza di ciascuna linea di taglio verticale (in alto e in basso) e orizzontale (a sinistra e a destra);
    - Evita che eventuali imperfezioni nel taglio o tagli a filo (gutter a zero) lascino pezzetti di crocini neri visibili sugli angoli o sui bordi dei biglietti da visita o delle etichette stampate;
    - Mantiene l'allineamento perfetto della lama da bordo a bordo del foglio per l'operatore alla ghigliottina.
  - Aggiornata la matrice di capacità (`laydown.engine.capabilities`), il componente UI `MarksTab` (`marks_tab.py`) e aggiunta la suite di test dedicata in `tests/test_marks_output.py`.

- **Posizionamento affiancato e centratura Step & Repeat (Ripetizione pagina per riempire il foglio):**
  - **Posizionamento affiancato (Side-by-side):** nella modalità di ripetizione pagina (`FILL_SHEET` e `COPIES`), ciascuna copia viene posizionata esattamente con le dimensioni del box selezionato dall'utente (TrimBox, MediaBox, CropBox, ecc.) invece di espandere le celle all'intero foglio con spaziatura interna indesiderata.
  - **Centratura rispetto all'area utile del foglio:** l'intero blocco delle copie (`cols × rows`) viene centrato perfettamente all'interno del foglio sottraendo i margini impostati (`start_x = ml + (avail_w - total_w)/2`, `start_y = mt + (avail_h - total_h)/2`), garantendo margini residui simmetrici a sinistra/destra e in alto/basso.
  - **Distanze e spaziatura orizzontale e verticale esatte:** lo spazio tra copie adiacenti rispetta al millimetro le impostazioni delle caselle di spaziatura (`gutter_h_mm` e `gutter_v_mm`). Con spaziatura a zero, i bordi delle copie combaciano perfettamente a filo (taglio unico / single cut) per il taglio tipografico rapido con taglierina o ghigliottina.
  - **Riferimento dinamico al riquadro sorgente selezionato:** le dimensioni di ogni copia e le relative distanze di taglio fanno riferimento in tempo reale al riquadro scelto nel combobox dei riquadri sorgente (`SourceBox.TRIM`, `MEDIA`, `CROP`, `BLEED`).
  - **Supporto `auto_rotate` coerente:** se la rotazione automatica è attiva, l'algoritmo valuta se orientare le copie a 90° per consentire alla griglia di adattarsi all'area utile del foglio senza sbordare o minimizzando la riduzione.
  - Aggiornati `laydown/engine/geometry.py` (`step_and_repeat_grid`, `cell_grid`, `sheet_plan`, `bleed_caps`) e `laydown/engine/impose.py`, con test completi in `tests/test_layout_depth.py`.

- **Ripetizione e Duplicazione pagine (Step & Repeat / Riempi foglio):**
  - Implementata la modalità di duplicazione e ripetizione per layout N-Up (`RepeatMode`):
    - **Pagine consecutive (nessuna replica):** modalità tradizionale per documenti multipagina ordinati.
    - **Riempi foglio (Step & Repeat):** replica ciascuna pagina del PDF di origine fino a saturare completamente tutte le celle della griglia del foglio (perfetto per stampare plance di biglietti da visita, etichette o volantini partendo da un PDF a pagina singola o fronte/retro).
    - **Numero copie per pagina:** consente all'utente di definire un numero fisso di repliche da produrre per ciascuna pagina del file di origine.
  - Estesa la griglia N-Up con formati standard tipografici:
    - Aggiunti preset veloci: **10-Up (2 × 5)** (ideale per 10 biglietti da visita su foglio A4), **12-Up (3 × 4)**, **20-Up (4 × 5)** (ideale per 20 biglietti da visita su foglio A3), **24-Up (4 × 6)**, **25-Up (5 × 5)** e **32-Up (4 × 8)**.
    - Aggiunta opzione **Griglia personalizzata** per impostare liberamente qualsiasi numero di Colonne e Righe.
  - Aggiornato il modello dati (`LayoutSettings.repeat_mode`, `LayoutSettings.repeat_count`), la matrice di capacità (`capabilities.py`), la logica di pianificazione (`geometry._plan_nup`), lo schema dell'interfaccia (`schema.py`) e aggiunta la suite di test dedicata in `tests/test_layout_depth.py`.

- **Popup descrittivi ed evidenziazione interattiva per ciascun riquadro (Page Box):**
  - Aggiunti popup informativi ricchi (HTML) per tutti i riquadri dello standard PDF (MediaBox, CropBox, BleedBox, TrimBox, ArtBox) che spiegano con chiarezza la funzione tipografica, le dimensioni correnti e lo stato (Dichiarato vs Derivato).
  - I popup appaiono passando il cursore del mouse:
    - Direttamente sopra il canvas sui bordi o all'interno di ciascun riquadro (con cursore a manina ed evidenziazione visiva immediata del box);
    - Sulle caselle di spunta dei singoli riquadri;
    - Su tutte le celle e righe della tabella dei riquadri pagina.
  - La selezione di una riga nella tabella evidenzia in tempo reale il rispettivo riquadro sul canvas di anteprima.

- **Correzione allineamento geometrico dei riquadri per PDF con origine negativa/offset (prestampa):**
  - Risolto un disallineamento critico in `geometry.source_boxes` e nell'Ispettore PDF presente su file generati da Adobe InDesign / Illustrator con crocini di taglio esterni (dove `TrimBox` è a `[0, 0, W, H]` e `MediaBox`/`CropBox` si estendono nello spazio negativo `[-36, -36, ...]`).
  - La conversione dei riquadri ora calcola accuratamente il vettore di traslazione verso lo spazio normalizzato `page.rect` (`dx = page.rect.x0 - crop.x0`, `dy = page.rect.y0 - crop.y0`) preservando la perfetta centratura e le distanze corrette su tutti e 4 i margini, con e senza rotazione di pagina.
  - La visualizzazione in sovrapposizione dell'Ispettore ora disegna il TrimBox e tutti gli altri riquadri esattamente allineati con i crocini di taglio e la grafica originale.
  - Aggiunti test di regressione dedicati in `tests/test_source_boxes.py` e `tests/test_inspector.py`.

- **Strumento di Ispezione PDF (PDF Inspector) con visualizzazione grafica dei riquadri:**
  - Aggiunto motore di analisi indipendente e senza dipendenze Qt (`laydown.engine.inspector`):
    - Estrazione completa delle proprietà del documento (nome file, percorso, dimensione formattata, versione PDF, numero pagine, titolo, autore, oggetto, creatore, produttore, date di creazione e modifica, crittografia).
    - Calcolo e misurazione precisa di tutti i riquadri pagina dello standard PDF (MediaBox, CropBox, BleedBox, TrimBox, ArtBox) sia in punti tipografici che in millimetri, con coordinate assolute e visive (tenendo conto della rotazione di pagina).
    - Rilevamento dello stato di definizione del riquadro: esplicito (dichiarato nel dizionario di pagina PDF) o derivato/fallback.
  - Aggiunta finestra di dialogo dedicata `PdfInspectorDialog` (`laydown.ui.inspector_dialog`):
    - Header riassuntivo con badge per conteggio pagine, versione PDF e dimensione del file.
    - Selettore di pagina con pulsanti avanti/indietro, spinbox e indicatore di rotazione.
    - Tabella riassuntiva con dimensioni in mm, pt e stato per ciascun box.
    - Riquadro metadati dettagliati del documento.
    - Area di rendering grafico ad alta risoluzione (`_PageBoxCanvas`) che sovrappone i riquadri con colori distintivi dedicati (MediaBox grigio chiaro, CropBox verde smeraldo, BleedBox blu ciano, TrimBox rosa magenta, ArtBox ambra) e tratti personalizzati.
    - Controlli interattivi di sovrapposizione: caselle di spunta individuali per mostrare/nascondere ciascun riquadro, toggle per etichette dimensionali agli angoli, e comandi di zoom (Ingrandisci, Riduci, Reimposta).
  - Integrazione nell'interfaccia principale:
    - Nuova voce di menu "Ispeziona PDF…" nel menu File (con scorciatoia `Ctrl+I`).
    - Nuovo pulsante dedicato con icona `inspect` nella barra degli strumenti sopra l'anteprima, abilitato all'apertura del documento.
  - Test di unità ed integrazione completi (`tests/test_inspector.py` e `tests/test_inspector_ui.py`).

- **Traduzione completa in italiano dell'interfaccia utente:**
  - Tutti i menu della barra dei menu (File, Modifica, Visualizza, Aiuto), le relative azioni e le scorciatoie sono in italiano.
  - Tradotti i titoli delle schede, le sezioni, le etichette dei controlli, le descrizioni, i testi segnaposto e le opzioni dello schema (Origine, Layout, Foglio, Segni).
  - Tradotte le schede per la gestione dei segni e crocini di stampa (MarksTab) e delle trasformazioni pagina (PreprocessorsTab).
  - Tradotti i pulsanti della barra degli strumenti, la barra di preflight con le relative notifiche, i messaggi della barra di stato e le finestre di dialogo (Apri, Salva, Esporta, Predefiniti, Dettagli controllo preliminare, Informazioni e Guida/Tutorial).
  - Tradotta l'area segnaposto del visualizzatore di anteprima e tutti i messaggi di avanzamento ed esportazione.

---

## [0.4.7] — 2026-10-15

Sharper preview, safer presets and print-shop sheet sizes.

- **Sharp preview on high-resolution screens.** On displays scaled to 150% or 200% (most laptops) the
  sheet preview came out soft: sheets were rendered with the extra pixels, then scaled down to normal size
  and stretched back up by Windows. The preview now keeps the screen's full resolution. Pinned by
  `tests/test_preview_hidpi.py`.
- **Booklets keep their spreads with "Blanks in the middle".** With an odd number of blank pages, every
  later page was pushed to the wrong side of the fold. Blanks now go in the centre in pairs, and a single
  odd blank sits just before the back cover.
- **SRA press sheets.** SRA4, SRA3 and SRA2 are now in the sheet size list, next to the A sizes.
- **Custom marks on network paths are refused.** A preset pointing a custom mark at a network share
  (`\\server\share\...`) could make Windows send your login to that server. Laydown now skips such a
  mark and preflight asks you to copy the file to this computer.
- **Copyright lines** name the legal owner, Daniel Mitev, with the public attribution Daniel Mevit (@danielmevit).

## [0.4.6] — 2026-10-12

Output-correctness and robustness round from the 2026-10-12 review
(`docs/ai/reviews/2026-10-12-review.md`).

- **Pages print at their actual size by default — this changes output for existing jobs.**
  N-Up and booklet layouts used to scale every page to *fill* its cell, enlarging small pages: a
  90 x 50 mm business card on an A4 2 x 4 sheet printed at 100 x 55.6 mm, and the Scale Pages step had
  no visible effect. The new **Page size** setting (Layout ▸ Arrangement) defaults to **Actual size**:
  pages keep their real dimensions and only shrink when they do not fit (preflight reports it).
  Choose **Fit to cell** for the old enlarge-to-fill behaviour. Presets saved before this change load
  as Actual size. Auto-rotate under Actual size only turns a page when that avoids or reduces
  shrinking. On the command line: `--set layout.scaling=fit`.

- **Rotated pages keep all of their artwork.** A source page carrying `/Rotate 90` or `270` —
  including one turned by Laydown's own Rotate Pages step — was imposed with part of its
  artwork cropped away, because the page boxes (unrotated) were intersected with the rotated
  page. The working copy is now normalised in memory before any box is read or page placed,
  with Trim/Bleed/Crop/Art boxes carried across; the file on disk is never touched. Pinned by
  `tests/test_rotation.py`.
- **Preflight checks the job that will actually be imposed** — page range and preprocessors
  applied, every page inspected, boxes compared correctly on rotated pages — and now warns when
  pages are being *enlarged* to fill their cell (the old "full size unless they have to shrink"
  text was wrong), when bleed is wider than half the gutter or set on a booklet fold, when a
  custom-mark PDF is missing, and when the source has annotations or form fields; an encrypted
  source is an error instead of an exception.
- **Failures are visible and exports are safe.** A crash inside preflight now shows as a
  "Preflight could not run" error instead of a clean-looking job; a password-protected PDF gets
  a plain message (owner-password-only, print-permitted PDFs still open normally); export writes
  to a temporary file beside the target and replaces it only on success, and refuses to
  overwrite the source PDF; loading a preset starts from the defaults rather than the current
  settings.
- **The preview no longer eats memory on large jobs or freezes on a settings change.** Total
  preview raster memory is capped (512 MB; the render resolution drops for very large jobs,
  never below 36 DPI), sheets are rasterised as images off the GUI thread, and a new settings
  change cancels the previous render instead of waiting for it.
- **Bleed and creep stay on their own page.** A page's bleed used to print over the neighbouring
  page's live area whenever the gutter was narrower than the bleed, and across a booklet's fold;
  booklet creep could push a page's artwork over the facing page. Bleed facing another cell is now
  capped at half the gutter, the fold side at half the gutter and at the fold itself, and crept
  content is clipped at the fold (as it would be on press) — with or without bleed set. Trim
  rects and crop marks do not move. Pinned by pixel-sampling tests.
- **Docs match the code again.** Version and test-count drift removed (docs no longer hard-code
  a test count), the CLI and the release-pipeline changes are documented, HANDOFF records the Store
  status as unverified, the AppxManifest carries a `0.0.0.0` placeholder that the build stamps, and
  GOTCHAS gains the rotated-box, GIL, CRLF and bleed-cap traps. `.gitattributes` now pins LF line
  endings so Windows and WSL git agree.
- **Printer's marks are press-correct.** Crop, gap-crop, registration, folding, perforation and
  collating marks now print in the **Registration** colour (`/Separation /All`), so they appear on
  every plate instead of only black; the colour bar is true DeviceCMYK process solids (C, M, Y, K
  and the overprints) instead of RGB; no mark is drawn in RGB any more. The Text Label's custom
  text is now used (it was offered in the Marks tab but ignored). The capability contract now
  classifies every mark and preprocessor field per type, so a control wired to a field the engine
  ignores fails the build.
- **Printable annotations and filled form fields now print.** They used to be silently dropped
  (imposition copies a page's contents only). Print-flagged annotations and form-field values are
  flattened into the page on the working copy before imposing; screen-only and hidden annotations
  are left out, as a printer would. Preflight says how many will be flattened. The source file is
  never modified.
- **Headless command line.** `python -m laydown impose SRC.pdf -o OUT.pdf [--preset P.json]
  [--set TARGET=VALUE ...] [--force]` and `python -m laydown preflight SRC.pdf [--json]` run the
  same engine without the GUI (Qt is never loaded). Settings come from the defaults, a preset and
  `--set` overrides (typed by each setting's default); a preflight error blocks the job with exit
  code 2 unless `--force`; the output is written atomically and can never overwrite the source.
- **Release builds are reproducible and fail loudly.** CI installs an exact, pinned build
  toolchain (`packaging/constraints.txt`); the PyMuPDF floor is raised to 1.28.0 (older bundled
  MuPDF builds carry CVE-2026-3308 and CVE-2026-25556); GitHub Actions moved to their Node 24
  majors; a tag build without the stable signing certificate now stops instead of minting a
  throwaway one, and the Windows job fails if no MSIX was produced.

## [0.4.5] — 2026-07-19

- **Windows taskbar-icon hardening.** A blank-taskbar-icon report led here; the reported blank
  itself turned out to be a stale Windows *icon cache* on the machine (every layer of the build
  proved correct — see `docs/ai/GOTCHAS.md`), but the investigation surfaced two real weaknesses,
  both fixed. The unpackaged build (portable ZIP) no longer claims the *MSIX package's* identity
  (`LaydownTeam.Laydown`) as its taskbar AppUserModelID — on a machine that also has the MSIX
  installed, Windows would resolve the portable's taskbar icon through that package, which the
  portable isn't running inside, and draw an empty button. It now claims its own identity
  (`DanielMevit.Laydown`), and only when it detects it is *not* running inside a package, so a
  real MSIX run keeps the identity Windows gives it. And the icon loader now tries several bundle
  roots and falls back to the multi-size `.ico`, so the window icon can never come back empty
  (pinned by a test).
- **The four settings tabs (Source/Layout/Sheet/Marks) now fill the panel width**, each an equal
  quarter, instead of sitting as fixed-width buttons. Icons stay centred in every tab.
- **Help ▸ About says more.** It now describes what Laydown does, credits **Daniel Mevit** with a
  clickable link to [damt.xyz](https://damt.xyz), and links the AGPL licence and the GitHub source.

## [0.4.4] — 2026-07-18

- **The four settings-tab icons are centred within their buttons.** They were built with a
  `QTabBar`, which left-biases an icon-only tab by ~7px (the empty text still reserves layout),
  so the glyphs sat off-centre. The strip is now `QToolButton`s in an exclusive group — a
  QToolButton centres its icon exactly (measured <1px offset, was 7px). Pinned by a test.

## [0.4.3] — 2026-07-18

- **Security: the dropdown-chevron image is written to the per-user cache, not the shared
  temp dir.** 0.4.2 wrote it to a predictable path in the world-writable temp directory, which
  on a multi-user system invites a symlink/collision (the save would follow a planted symlink).
  Low risk for a single-user desktop app, but real; it now goes to `QStandardPaths`
  `CacheLocation` with a private `mkdtemp` (0700) fallback. Flagged by the commit security review.

## [0.4.2] — 2026-07-18

UI fixes and polish from hands-on use.

- **Fixed: zoom no longer spawns windows.** Clicking zoom in/out opened stray windows of the
  sheets and dislocated the preview. `_clear_grid` called `setParent(None)` on each sheet
  widget, which reparents a *visible* child to the desktop — turning it into a top-level
  window — and every zoom rebuilds the grid. Now `hide()` + `deleteLater()`. Reproduced in
  isolation and pinned by `tests/test_preview_zoom.py`.
- **Double-click the empty canvas to open a PDF**, alongside Ctrl+O and drag-and-drop.
- **Page numbers in the preview are bigger and regular weight** (were smaller and bold).
- **Distinct fit-page and actual-size icons.** They were both a square-with-corners and easy
  to confuse; actual-size is now a plain sheet rectangle.
- **The four settings tabs (Source/Layout/Sheet/Marks) are centred** in the panel header
  instead of clustering left.
- **The zoom/fit group is right-aligned** in the toolbar; the column-view buttons stay left.
- **Dropdowns show a chevron.** Styling the combo boxes had suppressed Fusion's native arrow,
  so they lost their "this opens a list" cue; a Lucide chevron is rendered back in.

## [0.4.1] — 2026-07-17

- **UI icons are now the [Lucide](https://lucide.dev) set.** The toolbar (column views, zoom,
  fit-to-width / fit-page / actual-size), the four settings-tab glyphs (source, layout, sheet →
  ruler, marks → crop) and the Generate button were hand-drawn with QPainter; they're now the
  verbatim Lucide SVGs, rendered through QtSvg and tinted to the theme. Consistent stroke weight,
  crisp at any DPI. Lucide is ISC-licensed; attribution is in `NOTICE`.
- `laydown/ui/icons.py` holds the icon set with a test that guards every name, so a mistyped glyph
  fails CI instead of crashing the window. QtSvg is bundled automatically by the existing
  PyInstaller collection (verified in a frozen Linux build).

## [0.4.0] — 2026-07-16

**PressReady is now Laydown.** Fujifilm ships "Revoria XMF PressReady" — a prepress workflow
product in this exact field — so the old name was a collision waiting to matter. "Laydown" is
what printers call the imposition arrangement itself. Same app, same code lineage.

- **Windows sees a different app.** The package identity changed (`PressReadyTeam.PressReady`
  → `LaydownTeam.Laydown`), so an existing PressReady install does not upgrade — remove it once:
  `Get-AppxPackage PressReadyTeam.PressReady | Remove-AppxPackage`.
- New signing certificate (its subject must match the new publisher), executable/artifact names
  (`Laydown.exe`, `Laydown-<version>-<platform>`), settings under `HKCU\Software\Laydown`, repo
  at `github.com/danielmevit/laydown`, site at <https://danielmevit.github.io/laydown/>.
- Releases 0.2.0 and 0.3.0 keep their PressReady-named files — history is not rewritten.

### MSIX signing fixed (0x800B010A)

The 0.3.0 release pipeline signed each MSIX with a certificate minted on the CI runner and
destroyed with it, so the public certificate users needed to trust never existed anywhere they
could get it — every install failed with 0x800B010A. Releases are now signed with one stable
certificate (kept in repo secrets, valid to 2031); its public half ships as
`Laydown-msix-signing.cer` beside the installer, and the notes carry the one-time trust command.
The 0.3.0 Windows assets were rebuilt and replaced in place.

## [0.3.0] — 2026-07-15

Version restarted at **0.3.0** to match how the rest of these projects are numbered. The
February release was renumbered from v2.0.0 to **v0.2.0** to suit — same commit, same binaries,
new label.

Those binaries still declare `2.0.0.0` inside the MSIX, which renaming a tag can't change, and
MSIX refuses to install an older version over a newer one. So if you have February's build
installed, remove it before putting 0.3.0 on:
`Get-AppxPackage PressReadyTeam.PressReady | Remove-AppxPackage`.

### Packaging — Windows, macOS, Linux

- **Four artifacts, built by CI on every version tag** (`.github/workflows/release.yml`):
  Windows x64 (MSIX + portable ZIP), macOS **arm64 and Intel** (.dmg), and Linux x86_64
  (portable tar.gz). PyInstaller can't cross-compile, so each runs on its own runner. Every job
  runs the tests *and* `--smoke` before packaging, and the Linux job smoke-tests the built
  bundle itself.
- **No 32-bit Windows build.** It was planned and attempted, and it can't be done: PyQt6 ships
  no `win32` wheel (only `win_amd64`/`win_arm64`), so pip falls back to the source tarball and
  tries to build Qt with qmake. A 32-bit build would mean compiling Qt from source for x86.
  The claim has been removed from the README and the site rather than left standing.
- One cross-platform `PressReady.spec` (per-OS icon, macOS `.app` bundle, PDF file
  association); `packaging/{windows,linux,macos}/` scripts replace `build_msix.ps1`.
- Trimmed unused Qt modules (WebEngine, Quick, QML, Multimedia, …) out of the bundle.
- **Windows build fix:** `build.ps1` failed to parse before running a line. Windows PowerShell
  5.1 reads a BOM-less `.ps1` as ANSI (cp1252), so a UTF-8 em-dash in a `Write-Host` message
  arrived as three bytes ending in a curly quote — which PowerShell treats as a string
  terminator, unbalancing every brace after it. The script is now pure ASCII, and
  `tests/test_packaging.py` enforces that from Linux.
- **Linux build fix:** the launcher script was named `pressready` next to the `PressReady`
  binary. On a case-insensitive filesystem — i.e. building from WSL against a Windows drive —
  those are the same file, so the script overwrote the 100 MB binary and then exec'd itself
  forever. CI would never have caught it (ext4 is case-sensitive). The launcher is gone, and
  the build now fails loudly if the binary comes out suspiciously small.

### The website

- `site/` — an Astro page published to <https://danielmevit.github.io/pressready> by
  `.github/workflows/deploy.yml`. **That workflow was replaced**: it uploaded the repository
  root, which would have served the raw repo instead of a site.

### Fixed

- **The settings panel painted itself near-white.** `QScrollArea`'s viewport is a child widget,
  so a `QScrollArea { background: transparent }` rule never reached it and it fell back to the
  platform's light palette — putting near-white section titles on a near-white background,
  unreadable. Fixed with a real dark `QPalette` plus viewport rules, so nothing depends on a
  stylesheet reaching every widget.
- **Undo, redo and preset loads only updated length fields.** Every other control — segmented,
  select, switch, text — kept showing its old value while the store, the preview and the
  exported PDF had the new one. All controls now follow the store, with a test that no control
  can be added without being resyncable.
- The mark editor's settings panel showed empty at startup and hid itself after choosing
  artwork (a stray line absorbed into the wrong method).
- Colours and fonts are read from `ui/theme.py` everywhere; the preview panel and the two
  collection editors had hardcoded leftovers.
- Bounded the preprocessor/mark lists and gave them empty states instead of a tall black box.

### Docs

- README rewritten in plain language — what it does, who it's for, what it can't do yet.

### Phase 6 — Marks, units, preflight (2026-07-15)

- **Units** — mm / cm / in / pt (View → Units). The model stays millimetres throughout; the
  unit is display-only, so a shop doing Letter work can type inches and nothing downstream
  has to care.
- **New marks** — gap crop marks (cuts that run to the sheet edge, for ganged work),
  perforation marks, a process + grey-ramp colour bar, and **custom marks: any PDF, stamped
  on the sheet**. That last one came straight out of the reference study — Imposition
  Wizard's `Placeholders/` folder holds `missing-bull-eye.pdf` and friends, which gives away
  that a custom mark is just artwork placed by rule. `show_pdf_page` already did that, so a
  shop can drop in its own bull's-eye or house colour bar. A missing or corrupt mark PDF
  never takes the job down.
- **Preflight** — problems surface while there's still time to fix them, instead of arriving
  as an exception after Generate (or at the guillotine): impossible margins, no trim box,
  bleed with no artwork behind it, mixed page sizes, page counts that don't fold, pages
  being scaled. Graded deliberately: A4 two-up on A3 lands near 97% and that is the job this
  tool is *for*, so slight scaling is a note and only a real reduction raises the strip.
  Notes stay in Help → Preflight (F7) rather than sitting on screen training people to
  ignore it.

### Phase 4 — The Toolcraft-style UI (2026-07-15)

- **`ui/theme.py`** — every colour, radius and size in one place, plus the whole stylesheet.
  Neutrals are authored in **OKLCH** and converted to hex at import (Qt has no `oklch()` or
  `color-mix()`). The converter is exact: `oklch(0.269)` → `#262626`, matching Toolcraft's
  own literal for the same token.
- **`ui/components.py`** — the control kit: a real switch instead of a checkbox, a
  full-width segmented control, 36px collapsible section headers with a per-section reset,
  label-above field rows, hairline 4px scrollbars.
- **Generate PDF moved to a sticky footer** — Toolcraft pins final delivery actions rather
  than burying them in a toolbar.
- **The accent stays orange.** It's the product's identity and is already in the app icon and
  MSIX tiles; what's borrowed from Toolcraft is the structure, density and control anatomy.
- **Preview is no longer soft.** Sheets were rasterised at 72 DPI and then scaled up by the
  zoom factor, so "Actual size" on a 96-DPI screen was a 1.33× blow-up of a 72-DPI image.
  It now rasterises at a DPI matched to how large the sheet is actually drawn (96–300),
  and re-renders when zoom moves far enough to matter.

### Phase 3 — One truth for the settings panel (2026-07-15)

**The structural fix for 0.2.0's central defect.** The Layout tab collected booklet modes,
right-to-left, signatures and creep; the engine ignored all of them; nothing could see the
gap because "the model has a field" and "the engine reads it" were unrelated facts.

- **`engine/capabilities.py`** — every setting is now classified HONOURED or NOT_IMPLEMENTED,
  and three tests enforce it: no control may target an unhonoured setting, every named path
  must exist, and **no field may escape classification**. Adding a setting to the model now
  fails the build until someone decides, in public, whether the engine honours it. Verified
  by adding a dummy field and watching it fail.
- **`ui/schema.py`** — the panel is declared, not wired: sections and controls with a
  `target`, default, description and `visible_when`. Section titles must name what they edit,
  sections stay 2–7 controls, switches may not be labelled "Enable X", segmented controls
  stay ≤4 short options — all enforced by tests rather than review.
- **Hide, never disable** — a control that can't act is removed, not greyed out. A greyed
  control still claims the feature exists.
- **`ui/panel.py`** — one `ValueStore` behind every control, which is what makes the rest
  nearly free: **undo/redo** (Ctrl+Z/Y — the Edit menu's stubs are now real),
  **per-section reset**, and **presets** (File → Save/Load Preset, readable JSON).
- **In-app help is generated from the schema** (`ui/help.py`), so it can no longer document a
  feature that doesn't exist — which is exactly what the 0.2.0 tutorial did.
- Deleted `layout_tab.py` and `sheet_tab.py` and their four hand-wired `get_settings()`
  methods. The two genuinely growable lists (preprocessors, marks) keep bespoke editors.

### Phase 5 — The layout depth the UI already promised (2026-07-15)

- **Arbitrary N-Up grids** — 1/2/4/6/8/9/16-up, or explicit rows × columns. Was hardcoded to
  2×1 and 2×2.
- **Auto-rotate** — turn a page a quarter turn when it fills its cell better, so landscape
  artwork sits on a portrait sheet without pre-rotating the file.
- **Right-to-left binding**, and **blanks in the middle** (pad at the centre spread instead of
  the end, keeping a printed back cover).
- **Signatures and perfect binding** — saddle stitch nests the whole document as one
  signature; perfect binding folds fixed-size signatures separately and gathers them, which
  changes the page order. Each signature still folds into reading order (tested).
- **Creep** — fore-edge compensation, walked linearly from the outermost sheet's shift to the
  innermost. Stated as explicit endpoints the operator measures rather than derived from a
  guessed paper caliper; negatives shift the other way.
- **Not implemented: work-and-turn and work-and-tumble.** These are press-form techniques
  that put both forms on one double-size plate, not orderings — getting them subtly wrong
  wastes plates and paper. They are removed from the model rather than left as controls that
  do nothing, and are in ROADMAP.md's backlog pending a pressman's review.

### Phase 2 — Box-aware imposition (2026-07-14)

**The headline fix.** 0.2.0 called `show_pdf_page` without a clip, so every page was imposed on
its **MediaBox**. Press-ready PDFs — the files this tool exists for — describe the finished page
with a **TrimBox** and paint artwork past it to a **BleedBox**. For those, PressReady placed the
wrong area, and printed crop marks around the wrong edge, without saying a word.

- **Source box selection** — Media / Crop / Trim / Bleed, defaulting to **Trim**. PDF defines
  TrimBox to fall back to CropBox and then MediaBox, so the new default is also correct for plain
  PDFs that carry no box information (asserted by a test).
- **Bleed** — `bleed_mm` pulls artwork beyond the chosen box and lets it spill outside the cell, so
  a slightly-off cut still lands on ink. The page does not move when bleed is added.
- **Crop marks now follow the trim rect, not the cell.** A page whose proportions differ from its
  cell is letterboxed inside it; marks drawn on the cell sat off the page edge entirely. Found while
  reasoning about boxes and fixed here.
- **Anything outside the chosen box is clipped**, so a slug mark in the media margin no longer
  reaches the press sheet.

**Architecture**
- New `engine/geometry.py` — one home for sheet/cell/box/fit maths and for `sheet_plan()`, which
  decides which page goes in which cell of which sheet. `impose.py` now only renders that plan.
- **The preview can no longer drift from the output.** It used to re-derive cell geometry from the
  project while displaying a separately-imposed PDF: two opinions about one layout. `impose_to_temp`
  now returns an `ImposeResult` carrying the geometry the run actually used, and the overlay
  describes *that*. Deleted `compute_cells()` and its duplicate maths.
- `LayoutSettings` gained `rows`/`cols` (groundwork for arbitrary N-Up grids).

### Phase 1 — Ground truth (2026-07-14)

**Tests** — `tests/` went from empty to 96 tests. The centrepiece is a round-trip bench harness
(`tests/helpers.py`): build a source PDF carrying findable tokens, impose it, read the tokens back
out of the *output*, and assert every page landed where the geometry says. Cell maths is re-derived
in the tests rather than imported, so they can disagree with the engine. Also invariants for
saddle-stitch ordering, page-range parsing, margin containment, determinism, and culture-invariance.

**Fixed** (each was reproduced by a failing test first):
- `impose()` leaked the source document whenever imposition raised; output documents leaked on a
  failed save. Both now close in `finally`.
- **Reorder silently deleted pages.** `"4,3"` on a four-page document meant "keep two" — a reorder
  is not a delete. A partial, out-of-range or unparseable order now raises with a message that says
  what to type instead. Bad expressions used to be ignored entirely.
- **Scale Pages cropped instead of scaling.** It only moved the MediaBox, so reducing a page cut its
  bottom-right away rather than shrinking the artwork. It now re-places page content vectorially and
  carries trim/bleed/art boxes with it.
- Booklet marks counted **sides as sheets** — a 4-side (2-sheet) booklet labelled itself "Sheet 1 of
  4", and collating marks stepped per side, so a collated stack showed the wrong staircase.
- Removed a dead expression in `impose.py` whose branches were identical.

**Added**
- `python -m pressready --smoke` — headless end-to-end self-check (build a sample, impose it, verify
  the output, construct the real window offscreen, exit 0/1). Runs in WSL and in a frozen build.
- Proper CLI entry point (`--version`, optional PDF argument).
- Version single-sourced from `pressready/__init__.py`. `pyproject.toml` reads it dynamically, the
  About dialog no longer hardcodes it, and `build_msix.ps1` reads it and **stamps it into the
  staged manifest** — its `$appVersion` was previously computed and then never used, so the MSIX
  silently took whatever the checked-in `AppxManifest.xml` said and bumping the script did nothing.

### Planning (2026-07-14)

- **Reference study** — `docs/ai/REFERENCE_STUDY.md`: Imposition Wizard 3 (installed build:
  feature and domain model; its tab list is where PressReady's four tabs and unimplemented enums
  came from) and Toolcraft (`@pixel-point/toolcraft`: schema-driven control panel, layout laws,
  dark token system). No code from either was copied.
- **`ROADMAP.md`** — six phases from a full code review: ground-truth bench harness → box-aware
  imposition → schema-driven panel with an engine capability contract → Toolcraft-style UI →
  the promised layout depth → marks/units/preflight. Absorbs the earlier `TODO.md`, which is
  removed so there is one work list.
- **DECISIONS** — keep PyQt6/PyMuPDF and port Toolcraft's design language rather than its stack;
  a control the engine doesn't honour must not exist in the UI; IW is a domain reference, not a
  feature target; brand accent stays orange.

### Project organization (2026-07-14)

- Adopted the AI-project standard (`_refs/ai-project-setup-playbook.md`): added `AGENTS.md`
  router and `docs/ai/` (START_HERE, DECISIONS, GOTCHAS, DESIGN_SYSTEM).
- **CodeGraph**: indexed the repo (`.codegraph/` gitignored, per-machine); `codegraph.json`
  excludes `_legacy/`, `framer-demo/`, `pressready-voice/`.
- **Git model**: work now happens on `dev`; `main` is releases only.
- **Hygiene**: untracked `framer-demo/node_modules` (2,196 files were committed); added
  `node_modules/` and `.codegraph/` to `.gitignore`; set `core.filemode false` for WSL.

### Licensing

- Project relicensed under **GNU Affero General Public License v3.0 only (AGPL-3.0-only)**. Added `LICENSE`, `LICENSING.md`, and `NOTICE`; updated `README.md` and `pyproject.toml`.

---

## [0.2.0] — 2026-02-19

_Published at the time as **v2.0.0**, renumbered to 0.2.0 on 2026-07-15 when the project moved
to 0.x versioning. The code and the released binaries are unchanged — only the label. Their
filenames still read `2.0.0`, and the MSIX still declares `2.0.0.0` internally, because they
are the original files._

### Installers

- **MSIX installer** — Native Windows packaging via `build_msix.ps1`. Produces `PressReady_2.0.0.msix` in `installer_output/`. Double-click to install; integrates with Start Menu, Settings → Apps, and Add/Remove Programs.
- **Portable ZIP** — Produced alongside MSIX as `PressReady_2.0.0-windows-x64.zip`. Extract and run `PressReady.exe` with no installation. Suitable for USB drives, portable use, or restricted environments.
- Removed Inno Setup and any third-party installer dependencies. MSIX uses only the free Windows 10 SDK (`makeappx.exe`, `signtool.exe`).

### Build Pipeline

- **PyInstaller** — One-dir bundle with `PressReady.spec`. Includes PyQt6, PyMuPDF, and app icons.
- **build_msix.ps1** — Single script to build both MSIX and portable ZIP. Auto-creates self-signed certificate on first run.
- **AppxManifest.xml** — MSIX manifest with app identity, icons, and runFullTrust capability.
- **assets/icons/msix/** — MSIX tile icons (Square44, Square150, StoreLogo).

### Code

- **pressready/ui/main_window.py** — `_app_icon()` now handles PyInstaller frozen mode via `sys._MEIPASS` for correct icon resolution in bundled builds.

### Project Structure

- Added: `AppxManifest.xml`, `build_msix.ps1`, `CHANGELOG.md`, `assets/icons/msix/`
- Build output: `installer_output/` (MSIX + ZIP), `dist/`, `build/`, `msix_stage/` (gitignored)
- Signing certs: `certs/` (gitignored)

---

## [1.x] — Archived

Original v1 code is in `_legacy/v1/`. See README for v2 feature list.
