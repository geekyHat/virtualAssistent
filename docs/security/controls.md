# Matrice dei controlli — pilot NewRay (P-17)

Ogni riga è **un controllo eseguibile**: la colonna Test rimanda al
file+funzione che lo esercita. Un controllo rimandato non compare come
"coperto" finché la sua slice di prodotto non è completata.

## Legenda

- **Superficie:** area del pilot su cui il controllo si applica.
- **Proprietà:** l'invariante che il controllo verifica.
- **Controllo:** il meccanismo che difende la proprietà.
- **Test:** il file:funzione che lo esercita.
- **Negativo:** ✔ se esiste un controllo negativo che dimostra la
  reattività del test (bypass simulato → test fallisce).

## Identità e sessione

| Superficie | Proprietà | Controllo | Test | Negativo |
| --- | --- | --- | --- | --- |
| `require_principal` | Route protetta senza cookie → 401 | Cookie di sessione richiesto | `test_identity_boundaries.py::test_anonimo_401_su_route_protette` | — |
| `require_principal` | Cookie di un'altra sessione revocata → 401 | Sessione risolta dal DB al momento della chiamata | `test_identity_boundaries.py::test_cookie_revocato_non_riautentica` | — |
| Payload API | ID nel body non concede identità | Principal risolto lato server dal cookie | `test_identity_boundaries.py::test_id_nel_body_non_bypassa_principal` | ✔ `test_negative_id_body_bypass_attivo` |

## Isolamento per scope (RLS FORCE)

| Superficie | Proprietà | Controllo | Test | Negativo |
| --- | --- | --- | --- | --- |
| `runs` / `run_events` | Scope estraneo non vede né aggiorna | RLS FORCE + policy `app.user_id`+`app.organization_id` | `test_run_authorization.py::test_run_di_altra_org_non_visibile` | ✔ `test_negative_scope_setter_bypass_attivo` |
| `conversations` | Cross-user su GET/PATCH/DELETE → 404 uniforme | RLS + repository scoped | `test_conversation_isolation.py::test_conversation_cross_scope_404` | — |
| `run_events` | Lettura eventi di un altro utente → 404 prima dello stream | Autorizzazione risolta prima dell'apertura | `test_run_authorization.py::test_get_events_di_altro_scope_404` | — |
| `POST /runs` | Idempotency-key di un altro utente non viene riusata | UNIQUE `(org, owner, conversation, key)` | `test_run_authorization.py::test_idempotency_key_non_condivisa_tra_scope` | — |

## Run durevoli, cancel, worker

| Superficie | Proprietà | Controllo | Test | Negativo |
| --- | --- | --- | --- | --- |
| `POST /runs/{id}/cancel` | Cancel di un run di un altro utente → 404 | Servizio guarda per scope | `test_run_authorization.py::test_cancel_di_run_altrui_404` | — |
| Worker | Vecchio worker non finalizza dopo reclaim | Fence + WHERE lease_owner+fence | `test_run_worker.py::test_lease_scaduto_riclaimabile_e_vecchio_worker_non_finalizza` | — |
| `run_events` | Immutabilità della outbox | Grant SELECT/INSERT (no UPDATE/DELETE) | `test_run_authorization.py::test_run_events_sono_immutabili` | — |

## Egress di rete

| Superficie | Proprietà | Controllo | Test | Negativo |
| --- | --- | --- | --- | --- |
| `HttpClient` | Nessun redirect verso host esterni | `httpx.AsyncClient(follow_redirects=False)` default | `test_egress_policy.py::test_httpclient_non_segue_redirect` | — |
| Settings | `NEWRAY_OLLAMA_BASE_URL` non loopback in HTTP → rifiutato | Validator settings | `test_egress_policy.py::test_settings_rifiuta_ollama_non_locale` | — |
| Middleware | Origine estranea rifiutata | `SameOriginMiddleware` | `test_egress_policy.py::test_same_origin_middleware_rifiuta` | — |

## Qualifica del modello (P-19)

| Superficie | Proprietà | Controllo | Test | Negativo |
| --- | --- | --- | --- | --- |
| Store file | Path traversal sul `model_name` | Regex safe `[A-Za-z0-9._:@-]+` | `test_qualification.py::test_file_store_rifiuta_model_name_pericoloso` | — |
| Store file | File corrotto | Lettura JSON → `ValueError` esplicito | `test_qualification.py::test_file_store_get_su_file_corrotto_solleva` | — |
| Store file | Nome del file ≠ `model_name` nel contenuto | Verifica in decode | `test_qualification.py::test_get_su_file_con_model_name_mismatch_solleva` | — |
| Report | Interruzione non trasforma in successo | `attempt_completed_at` distinto | `test_qualification_report.py::test_interruzione_conserva_ultimo_checkpoint` | — |

## Superfici rimandate

Le seguenti superfici entreranno con la propria slice (§P-08 → P-16,
P-18). Nessun controllo attivo qui: **non promettiamo copertura**.

- Allegati e archivio file: parser injection, path/link injection,
  MIME sniffing, EXIF, quotas.
- Estrazione documenti e visione: prompt injection via testo/immagine,
  memory pollution.
- Retrieval e contesto: indexing di documenti revocati, ranking
  malicious.
- Memoria esplicita: cancellazione a cascata su cache/export/indici.
- Skill proposte: revisione umana obbligatoria prima della adozione.
- Workflow: capabilities minime, isolamento del processo.
- Distribuzione locale: supply chain, backup/restore, secret rotation.
