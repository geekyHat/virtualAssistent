---
name: newray-module-boundaries
description: Progetta o modifica moduli, porte e dipendenze NewRay. Usa per cambi strutturali e nuovi adapter, non per semplici correzioni di testo.
---

Leggi le sezioni 4–8 e 19 di NewRay.md e l'ADR pertinente. Identifica
proprietario dei dati, caso d'uso, API pubblica e direzione degli import.
Riusa porte equivalenti; aggiungine una solo per un confine concreto.
Non esporre l'intero SDK vendor attraverso un adapter generico.

Traccia principal → caso d'uso → policy → adapter → risultato. Controlla
transazioni, errori, stop e recupero. Mantieni il binding del modello nel run.

Consegna una vertical slice con test del contratto e verifica degli import.
Registra un ADR se cambia un'invariante; non alterare la visione per facilitare
l'implementazione. Indica le prove eseguite e i punti ancora aperti.
