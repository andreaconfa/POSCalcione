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
- [Esecuzione come servizio (systemd)](#esecuzione-come-servizio-systemd)
- [Struttura del progetto](#struttura-del-progetto)
- [Line endings & Git](#line-endings--git)
- [Media & Git LFS](#media--git-lfs)
- [Sicurezza e segreti](#sicurezza-e-segreti)
- [Sviluppo](#sviluppo)
- [Crediti](#crediti)
- [Licenza](#licenza)

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

## Esecuzione come servizio (systemd)

Esempio di unit file (adattare i percorsi in base al sistema):

```ini
[Unit]
Description=Calcione POS (FastAPI)
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/calcione/calcione-pos
ExecStart=/bin/bash /home/calcione/calcione-pos/run.sh
Restart=on-failure
User=root

# Hardening
ProtectSystem=full
PrivateTmp=true
NoNewPrivileges=true
ReadWritePaths=/home/calcione/calcione-pos

[Install]
WantedBy=multi-user.target
```

Comandi utili:
```bash
sudo systemctl daemon-reload
sudo systemctl enable calcione-pos.service
sudo systemctl start  calcione-pos.service
sudo systemctl status calcione-pos.service
```

---

## Struttura del progetto

```
app/
  main.py
  routes_*.py
  receipts/
    models_receipts.py
    printing_service.py
    rules_engine.py
  static/
    js/
    uploads/        # contenuti locali (ignorati dal repo)
  templates/
run.sh
requirements.txt
```

---

## Line endings & Git
Il progetto usa **LF** come fine-riga (configurato via `.gitattributes`).  
Suggerimenti:
```bash
git config --global init.defaultBranch main
git config --global pull.rebase true
git config --global core.autocrlf input
git config --global core.eol lf
```

---

## Media & Git LFS
I file multimediali locali (immagini/video per advertising) **non** sono versionati (`app/static/uploads/**`).  
Se vuoi versionare media di grandi dimensioni, abilita **Git LFS** e usa una cartella non ignorata (es. `media/`):
```bash
git lfs install
git lfs track "*.mp4"
git add .gitattributes
git commit -m "chore: enable Git LFS for mp4"
```

---

## Sicurezza e segreti
- Non committare file `.env`, chiavi, credenziali, o database locali (`app.db`).
- Versiona **.env.example** con i soli nomi delle variabili e valori fittizi.
- Mantieni separati i file di configurazione di produzione.

---

## Sviluppo
- Stile Python: PEP8
- Ambienti isolati con `venv`
- Pull Request ben descritte; messaggi di commit chiari (es. `feat:`, `fix:`, `chore:`)
- Issue/roadmap per le modifiche più corpose

---

## Crediti
- **Autori**: team Calcione  
- **AI assist**: il codice, i template e i file di configurazione sono stati realizzati/ottimizzati con l’aiuto di un assistente AI.

---

## Licenza
Scegli e aggiorna questa sezione (es. **MIT**).
