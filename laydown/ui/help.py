"""
In-app help, generated from the schema.

The 0.2.0 tutorial documented Signatures and Page Creep as working features while
the engine ignored both — a hand-written page describing a hand-written UI, with
nothing keeping the two honest. The control reference is now derived from
``ui/schema.SCHEMA``, so it cannot describe a control that doesn't exist, and the
capability test already guarantees no control describes a setting the engine ignores.
"""

from html import escape

from laydown import __version__
from laydown.ui import theme as t
from laydown.ui.schema import SCHEMA, ControlType


def _control_rows(section) -> str:
    rows = []
    for control in section.controls:
        if control.type is ControlType.COLLECTION:
            what = "Aggiungi, rimuovi e configura gli elementi in questo elenco."
        else:
            what = control.description or ""
            if control.options:
                choices = ", ".join(escape(str(text)) for _, text in control.options)
                what = f"{what} <i>Opzioni: {choices}.</i>" if what else f"Opzioni: {choices}."
        rows.append(
            f"<tr><td><b>{escape(control.label)}</b></td><td>{what}</td></tr>"
        )
    return "".join(rows)


def _schema_reference() -> str:
    blocks = []
    for tab in SCHEMA:
        blocks.append(f"<h2>{escape(tab.name)}</h2>")
        for section in tab.sections:
            blocks.append(
                f"<h3>{escape(section.title)}</h3>"
                f"<p class='entity'>Controlla {escape(section.entity)}.</p>"
                f"<table><tr><th>Impostazione</th><th>Descrizione</th></tr>"
                f"{_control_rows(section)}</table>"
            )
    return "".join(blocks)


def tutorials_html() -> str:
    return f"""<html><head><style>
body {{ font-family:'{t.FONT_FAMILY}',sans-serif; padding:16px; color:{t.FG}; background:{t.BG}; }}
h1 {{ color:{t.ACCENT}; border-bottom:2px solid {t.ACCENT}; padding-bottom:6px; }}
h2 {{ color:{t.ACCENT}; margin-top:26px; }}
h3 {{ color:{t.FG}; margin-top:18px; margin-bottom:2px; }}
p.entity {{ color:{t.FG_FAINT}; margin-top:0; font-size:12px; }}
code {{ background:{t.INPUT_BG}; padding:2px 6px; border-radius:3px; color:{t.FG}; }}
table {{ border-collapse:collapse; margin:8px 0 16px 0; width:100%; }}
td, th {{ border:1px solid {t.BORDER}; padding:6px 10px; text-align:left;
          vertical-align:top; }}
th {{ background:{t.RAISED}; color:{t.ACCENT}; }}
i {{ color:{t.FG_MUTED}; }}
.tip {{ background:{t.RAISED}; border-left:3px solid {t.ACCENT}; padding:8px 12px; margin:12px 0; }}
</style></head><body>

<h1>Laydown {escape(__version__)}</h1>
<p>Disponi le pagine di origine sui fogli macchina per la stampa. L'imposizione è vettoriale — le pagine sono incorporate,
mai rasterizzate, garantendo che la qualità di stampa corrisponda sempre all'originale.</p>

<h2>Primi passi</h2>
<p><b>File → Apri PDF</b> (Ctrl+O), oppure trascina un PDF nella finestra. L'area di disegno mostra ogni
foglio impaginato esattamente come verrà stampato. Fai clic su <b style="color:{t.ACCENT}">Genera PDF</b>
(Ctrl+G) per esportare.</p>
<div class="tip"><b>L'anteprima corrisponde all'output reale.</b> I fogli vengono visualizzati a partire da una vera
imposizione, e i contorni magenta sono le linee di taglio effettivamente utilizzate dal motore — non un
disegno separato o approssimato.</div>

<h2>Abbondanza e riquadri pagina</h2>
<p>Un PDF pronto per la stampa specifica la pagina finita con il <b>Trim Box</b> (riquadro di rifilo) e prosegue la grafica fino
al <b>Bleed Box</b> (riquadro di abbondanza). Laydown impagina il Trim Box per impostazione predefinita, in modo che nella
cella finisca la pagina tagliata corretta. I file senza riquadri non hanno problemi: le specifiche PDF prevedono che il Trim Box corrisponda
all'intera pagina. Imposta un valore di <b>abbondanza</b> nella scheda Origine per estendere la grafica oltre la linea di taglio,
evitando bordi bianchi in caso di imprecisione nel taglio.</p>

{_schema_reference()}

<h2>Sovrimpressioni di visualizzazione</h2>
<table>
<tr><th>Opzione</th><th>Scelta rapida</th><th>Descrizione</th></tr>
<tr><td>Numeri di pagina</td><td>Alt+1</td><td>Quale pagina di origine si trova in ciascuna cella</td></tr>
<tr><td>Linee di taglio</td><td>Alt+2</td><td>Dove verrà rifilata ciascuna pagina</td></tr>
<tr><td>Parte superiore pagine</td><td>Alt+3</td><td>Indica il bordo superiore — per verificare l'orientamento</td></tr>
<tr><td>Anteprime pagine</td><td>Alt+4</td><td>Il contenuto effettivo della pagina</td></tr>
</table>

<h2>Predefiniti</h2>
<p><b>File → Salva predefinito</b> salva tutte le impostazioni in un file JSON;
<b>Carica predefinito</b> le ripristina. Molto utile per commesse ricorrenti.</p>

<h2>Scorciatoie da tastiera</h2>
<table>
<tr><td>Ctrl+O</td><td>Apri PDF</td></tr>
<tr><td>Ctrl+G</td><td>Genera PDF</td></tr>
<tr><td>Ctrl+Z / Ctrl+Y</td><td>Annulla / Ripeti una modifica</td></tr>
<tr><td>Ctrl++ / Ctrl+-</td><td>Ingrandisci / Riduci zoom</td></tr>
<tr><td>Ctrl+0</td><td>Adatta alla larghezza</td></tr>
<tr><td>Ctrl+F4</td><td>Chiudi PDF</td></tr>
<tr><td>F1</td><td>Questa guida</td></tr>
</table>
</body></html>
"""
