"""Bootstrap: composizione dell'applicazione (NewRay.md §5).

``wiring`` è l'unico punto di composizione: collega le implementazioni
concrete (adapter PostgreSQL, clock, settings) ai casi d'uso. Le altre
layer non importano adapter né dettagli di avvio.
"""
