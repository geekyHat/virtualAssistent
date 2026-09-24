# Confini dei componenti

Fonte autorevole: NewRay.md §§4–8, 18–20. I percorsi applicativi elencati
qui sono destinazioni da implementare con i rispettivi casi d'uso.

Il backend separa `kernel` (principal, ID, errori e clock), `modules`
(dominio/casi d'uso/porte/adapter), `interfaces/http` (DTO e trasporto),
`infrastructure` (risorse tecniche) e `bootstrap` (composizione).

Un modulo espone `public.py`; i consumatori non importano i suoi adapter né
interrogano tabelle private. Dominio e applicazione non importano framework,
SQL o provider. Le implementazioni concrete vengono collegate nel bootstrap.
La tabella completa di ownership è in [NewRay.md §6](../../NewRay.md#moduli).

| Area | Proprietà principali |
| --- | --- |
| identity / access | Identità, sessioni, ACL, grant e approvazioni |
| conversations / runs / jobs | Messaggi, avanzamento, coda, lease e risorse |
| profiles / models | Istruzioni e binding versionati; catalogo e inference |
| tools / extensions / skills | Gateway, lifecycle pacchetti e metodi versionati |
| knowledge / memory / artifacts | Fonti e indici, ricordi, bozze ed export |
| web_research / mail / calendar | Ricerca, connessioni e operazioni esterne |
| audit / editions | Tracce minimizzate e entitlement separato dagli ACL |

Nel frontend `app` compone router e provider, `pages` compone feature,
`features` possiede i workflow e `shared` contiene componenti riusabili.
`shared` non importa feature; dati server, reducer del run e stato effimero
rimangono distinti. Le autorizzazioni sono sempre verificate dal backend.

I controlli automatici di questi confini sono lavoro di A-06. La loro
descrizione non equivale a controlli già implementati.
