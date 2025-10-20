# app/printing.py
# --- DEPRECATO: shim di compatibilità temporaneo ---
# Le vecchie funzioni non fanno nulla: servono solo ad evitare ImportError
# mentre migriamo la stampa alle Regole.

import logging
log = logging.getLogger("printing-shim")

def print_kitchen_receipt(*args, **kwargs):
    log.warning("print_kitchen_receipt() è deprecata e ignorata (shim). Usa le Receipt Rules.")

def print_category_receipt(*args, **kwargs):
    log.warning("print_category_receipt() è deprecata e ignorata (shim). Usa le Receipt Rules.")
