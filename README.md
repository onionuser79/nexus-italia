# NEXUS-ITALIA Gateway Installer

Installer automatico per gateway **NEXUS-ITALIA** basato su Raspberry Pi0 2W e Companion USB MeshCore.

Questo repository installa e configura in automatico:

- dipendenze di sistema
- ambiente Python dedicato
- `meshcore-cli` dentro il virtualenv del gateway
- configurazione `config.yaml`
- servizio `systemd` `nexus-gateway`
- avvio automatico al boot

## Requisiti

- Raspberry Pi OS / Debian / Ubuntu (NO desktop)
- accesso Internet
- Companion USB MeshCore collegato
- credenziali MQTT da richiedere all'indirizzo email info@meshcoreitalia.it


## Creazione canale NEXUS con relativa Secret Key

<img width="302" height="399" alt="nexus" src="https://github.com/user-attachments/assets/8b4a8b6f-4050-4015-a9d1-3f626b3de48f" />

Nome Canale: Nexus

Secret Key: a45768ab48e203498edbc11b35cdfbd7



## Installazione rapida

Clona il repository e lancia lo script come root:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/xpinguinx/nexus-italia.git
cd nexus-italia
sudo bash install_gateway.sh
```

Lo script chiede passo passo:

- utente Linux del servizio
- porta seriale del Companion
- `gateway_id`
- dati radio locali
- host/porta/credenziali MQTT
- nome e numero canale MeshCore

## Valori verificati in test

Configurazione funzionante già verificata:

- `gateway_id`: `NEXUS-ITALIA-RM`
- seriale: `/dev/ttyUSB0`
- canale MeshCore: `NEXUS`
- numero canale: `1`
- broker MQTT con autenticazione utente/password
- servizio avviato via `systemd`

## Comandi utili

Stato servizio:

```bash
sudo systemctl status nexus-gateway --no-pager
```

Log live:

```bash
journalctl -u nexus-gateway -f
```

Riavvio:

```bash
sudo systemctl restart nexus-gateway
```

## Percorsi installati

- applicazione: `/opt/nexus-gateway`
- configurazione: `/opt/nexus-gateway/config.yaml`
- servizio: `/etc/systemd/system/nexus-gateway.service`

## Configurazione scope e beacon RF (branch `feature/scope-and-beacon`)

Questa versione introduce tre nuove funzionalità:

### 1. Configurazione automatica dello scope del canale

All'avvio del servizio gateway, viene impostato automaticamente lo scope sul canale Nexus tramite il comando:

```bash
meshcli -j -s /dev/ttyUSB0 -b 115200 scope "#it-lo"
```

Lo scope è configurabile nel file `config.yaml`:

```yaml
channel_scope: "#it-lo"
```

Se il campo `channel_scope` non è presente, il valore di default è `#it-lo`.

### 2. Beacon periodico via RF sul canale Nexus

Il gateway trasmette periodicamente un messaggio beacon via RF sul canale Nexus, utilizzando il comando:

```bash
meshcli -j -s /dev/ttyUSB0 -b 115200 chan 2 "testo del beacon"
```

Parametri configurabili in `config.yaml` sotto la sezione `runtime`:

```yaml
runtime:
  beacon_interval_sec: 10800    # intervallo in secondi (default: 3 ore)
  beacon_channel: 2             # ID canale Nexus come visto dal Companion
  beacon_text: "NEXUS-ITALIA Gateway XX - meshcoreitalia.it"
```

- `beacon_interval_sec` — intervallo tra i beacon (default 10800 = 3 ore)
- `beacon_channel` — numero del canale sul quale trasmettere il beacon (default `2`, corrispondente al canale Nexus sul Companion)
- `beacon_text` — testo del beacon; se vuoto, il beacon è disabilitato

### 3. Beacon iniziale 10 secondi dopo l'avvio

Oltre al beacon periodico, il gateway invia un primo beacon **10 secondi dopo lo startup**, in modo da annunciarsi immediatamente sulla rete RF dopo un riavvio o un'accensione.

### File modificati

| File | Modifica |
|------|----------|
| `nexus_gateway/config.py` | Aggiunti campi `channel_scope`, `beacon_channel`, `beacon_interval_sec`, `beacon_text` |
| `nexus_gateway/meshcli_adapter.py` | Aggiunti metodi `set_scope()` e `send_beacon()` |
| `nexus_gateway/service.py` | Chiamata `set_scope()` allo startup, beacon iniziale a +10 s, loop beacon ricorrente in thread |
| `config.example.yaml` | Documentati i nuovi parametri con valori di default |

### Esempio completo di configurazione beacon

```yaml
channel_scope: "#it-lo"

runtime:
  dedupe_ttl_sec: 180
  heartbeat_interval_sec: 30
  poll_interval_sec: 5
  log_level: INFO
  beacon_interval_sec: 10800
  beacon_channel: 2
  beacon_text: "NEXUS-ITALIA Gateway RM - meshcoreitalia.it"
```

---

## Note operative

Lo script aggiunge l'utente del servizio al gruppo `dialout` per l'accesso alla seriale.
Dopo l'installazione, se il Companion non viene visto subito dal servizio, può essere utile un riavvio del Raspberry.

## Test manuali MeshCore

```bash
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/meshcli -j -s /dev/ttyUSB0 -b 115200 get_channels
sudo -u <utente-servizio> /opt/nexus-gateway/.venv/bin/meshcli -j -s /dev/ttyUSB0 -b 115200 sync_msgs
```

