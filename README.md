# Calcione POS

Sistema **POS + Kitchen Display + Customer Display** per la Sagra di Calcione.  
Backend **FastAPI + SQLModel**, frontend HTML/JS leggero. Progettato per più cucine (food/drink), numerazioni personalizzate, regole di stampa ricevute e advertising su display clienti.

> **Nota**  
> Il progetto è stato sviluppato con forte supporto di AI. Tutte le decisioni finali, l’assemblaggio e l’uso in produzione restano responsabilità dei maintainer umani.

---

## Sommario
- [A cosa serve](#a-cosa-serve)
- [Requisiti](#requisiti)
- [Configurazione](#configurazione)
- [Avvio rapido (sviluppo)](#avvio-rapido-sviluppo)

---

## A cosa serve
- **Punto cassa (POS)**: gestione ordini con split automatico per cucina/reparto.
- **Kitchen Display (KDS)**: monitor preparazione con riepiloghi e stati (in attesa / in preparazione / pronto).
- **Stampa ricevute**: regole flessibili per stampanti **ESC/POS** e template personalizzati.
- **Customer Display / Advertising**: immagini e video promozionali in rotazione sul display lato cliente.  
  *(I file multimediali locali non sono versionati nel repository.)*

---

## Requisiti
- **Python 3.13** (consigliato: virtualenv)
- Sistema **Linux** (testato su Debian)
- **bash** in `/bin/bash`
- (Opzionale) Stampante **ESC/POS** in rete
- (Opzionale) `systemd` per l’avvio come servizio

---

## Configurazione
Variabili d’ambiente principali (vedi anche `.env.example`):
- `CALCIONE_DB_URL` (default: `sqlite:///app.db`)
- altre variabili specifiche del progetto (crea un file `.env` locale **non** versionato)

> La cartella `app/static/uploads/` è **ignorata** dal repository.  
> Manteniamo solo un file segnaposto `.gitkeep` per conservare la directory vuota.

---

## Avvio rapido (sviluppo)

Avvia direttamente con lo script di comodo:

```bash
bash run.sh
```

Tipicamente `run.sh` esegue:
- creazione/attivazione virtualenv
- installazione `requirements.txt`
- avvio del dev server:
  ```bash
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
  ```

L’app sarà disponibile su: `http://<host>:8000`

---

