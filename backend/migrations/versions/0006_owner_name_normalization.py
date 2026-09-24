"""Normalizzazione del nome del proprietario esistente (B-03.2-30).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-21

Gli owner creati prima di B-03.2-30 possono avere margini di whitespace
nel nome (form e paste accidentali). I casi d'uso normalizzano ora il
nome in bootstrap e login (margini rimossi, spazi interni e case
preservati): un record con margini non sarebbe più raggiungibile dal
login, che confronta il nome normalizzato con quello persistito.

La migrazione applica la stessa regola ai soli record ``role = 'owner'``
(casella personale: un solo owner per installazione, NewRay.md §20.3),
senza cambiare ID, senza toccare gli altri campi e senza mai produrre un
nome vuoto: se il trim darebbe vuoto, il record resta com'era (il caso
d'uso lo rifiuterebbe comunque a ogni nuova scrittura). Nessun dato è
perso: si rimuove solo il whitespace di contorno.

La tabella ``users`` ha RLS con FORCE (0001): anche il ruolo delle
migrazioni non vede le righe senza scope applicativo. L'aggiornamento è
manutenzione dello schema dentro questa transazione, non un caso d'uso
utente: la RLS viene disabilitata solo attorno all'``UPDATE`` e subito
ripristinata con ENABLE + FORCE (stesso pattern del backfill di 0004).

Il downgrade non può riportare i margini originali (informazione non
conservata): è dichiarato no-op, come ogni migrazione di normalizzazione
di dati.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users ha RLS con FORCE: senza scope il ruolo delle migrazioni non
    # vede le righe. Si disabilita solo attorno all'UPDATE e si
    # ripristina subito ENABLE + FORCE (pattern 0004).
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
    # \s in regex PostgreSQL = [ \t\n\r\f\v]: stessi margini che il
    # caso d'uso rimuove per i nomi realistici. La condizione DISTINCT
    # rende l'aggiornamento un no-op sui record già puliti (idempotenza
    # e nessun tocco inutile delle righe).
    op.execute(
        """
        UPDATE users
        SET display_name = regexp_replace(display_name, '^\\s+|\\s+$', '', 'g')
        WHERE role = 'owner'
          AND display_name IS DISTINCT FROM regexp_replace(display_name, '^\\s+|\\s+$', '', 'g')
          AND regexp_replace(display_name, '^\\s+|\\s+$', '', 'g') <> ''
        """
    )
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # I margini originali non sono conservati: il downgrade è no-op
    # (documentato nel modulo). I nomi normalizzati restano validi:
    # bootstrap e login continuano a funzionare con il nome pulito.
    pass
