"""Moduli applicativi (NewRay.md §§5–6).

Ogni modulo espone ``public.py``: i consumatori importano da lì, mai dagli
adapter né dalle tabelle private altrui. Un modulo compare quando esiste un
caso d'uso reale; niente pacchetti vuoti (ADR 0004).
"""
