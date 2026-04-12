# CLAUDE.md — Solem BT Watering Controller

Custom component HA per centralina irrigazione **Solem BL-IP** via BLE.
Domain: `solem_bt_controller`. Versione attuale: `manifest.json`.

---

## Architettura

```
custom_components/solem_bt_controller/
├── __init__.py      setup/unload, piattaforme: button + number + sensor
├── api.py           comunicazione BLE (bleak + bleak-retry-connector + tenacity)
├── base.py          SolemBaseEntity (CoordinatorEntity)
├── button.py        Start Station 1..N, Stop, Turn On, Turn Off, Refresh
├── config_flow.py   Step 1: MAC + stazioni  Step 2: durate per stazione
│                    OptionsFlow: bluetooth timeout
├── const.py         UUID write/notify, config keys, default values
├── coordinator.py   DataUpdateCoordinator, comandi BLE, stato ottimistico
├── models.py        IrrigationController, IrrigationStation
├── number.py        durata per stazione (RestoreEntity, persiste al riavvio)
└── sensor.py        stato stazione, controller, batteria, RSSI
```

Nessun polling (`update_interval=None`). Le entity si aggiornano solo quando il coordinator chiama `async_set_updated_data({})` dopo un comando BLE.

---

## Protocollo BLE

### Caratteristiche GATT (servizio `108b0001-eab5-bc09-d0ea-0b8f467ce8ee`)

| UUID | Direzione | Uso |
|------|-----------|-----|
| `108b0002-eab5-bc09-d0ea-0b8f467ce8ee` | write | comandi HA → device |
| `108b0003-eab5-bc09-d0ea-0b8f467ce8ee` | notify | risposte device → HA |

**NON sottoscriversi a `0x2A05`** (Service Changed) — causa "Insufficient authorization" e fa cadere la connessione.

### Formato comandi (scritti su `108b0002`, `response=False`)

Tutti i comandi confermati da BLE sniff dell'app ufficiale il 2026-04-12.

```python
# Turn On persistente (porta il device fuori da OFF permanente)
struct.pack(">HBBBH", 0x3105, 0xA0, 0x00, 0x00, 0x0000)

# Avvia stazione X per Y minuti — singolo comando, durata in secondi
struct.pack(">HBBBH", 0x3105, 0x12, station, 0x00, minutes * 60)
# es. stazione 1, 5 min = 300s = 0x012c → payload: 3105120100012c

# Ferma irrigazione senza mettere device in OFF permanente
struct.pack(">HBBBH", 0x3105, 0x15, 0x00, 0xFF, 0x0000)

# Spegni controller permanentemente (emergency only — richiede app per riattivare)
struct.pack(">HBBBH", 0x3105, 0xC0, 0x00, 0x00, 0x0000)

# Commit frame (obbligatorio dopo ogni comando)
struct.pack(">BB", 0x3B, 0x00)
```

**`response=False`**: la caratteristica `108b0002` supporta solo Write Command (senza response). Usare `response=True` causa comportamento indefinito — non tornare a True.

### Risposte (notifiche su `108b0003`)

Ogni comando genera 6 pacchetti da 18 byte ciascuno:
- **Gruppo 1** (`byte[0] = 0x32`): stato PRIMA del comando
- **Gruppo 2** (`byte[0] = 0x3C`): stato DOPO il comando

Ogni gruppo ha 3 frammenti identificati da `byte[2]`: `0x02` (principale), `0x01`, `0x00`.

**Layout frammento principale (`byte[2] = 0x02`):**

| Byte | Significato | Note |
|------|-------------|------|
| 3  | Tipo frame | `0x42` = sessione irrigazione attiva; `0x02` = idle/off; `0x40` = sessione appena terminata (dopo `0x15`) |
| 5–7 | Session ID | `0xAAAAAA` durante irrigazione e dopo `0x15`; `0x000000` dopo `0xC0` (OFF permanente) |
| 10 | Batteria % | es. `0x51` = 81% |
| 13 | Station byte | numero stazione (1-based) con nuovo comando `0x12`; `0xFF` con vecchio approccio; valori `0xFC/0xFD/0xFE` durante sessioni app |
| 14 | Countdown | `0xFF` subito dopo Sprinkle (timer non ancora inizializzato); poi scende ~1/sec; `0xFF` in idle; `0x00` dopo `0x15` stop |

`is_irrigating = True` se `byte[3] == 0x42` OR (`countdown != 0xFF` AND `countdown > 0`).  
`byte[3] == 0x40` = sessione terminata pulitamente (non irrigating).  
**Il solo countdown NON è sufficiente**: immediatamente dopo un comando Sprinkle accettato, byte[3]=0x42 ma countdown=0xFF. Verificato fisicamente 2026-04-12.

---

## Setup hardware dell'utente

- HA su macchina **senza Bluetooth nativo**
- **ESP32 come BLE proxy** via ESPHome: `esp32-bluetooth-proxy-09ccac` @ `192.168.1.225`
- Centralina Solem: MAC `C8:B9:61:F0:15:30`, nome `quarnaro 14 / 2N`
- RSSI tipico: −40/−50 dBm (antenna esterna aggiunta). Sotto −85 dBm la connessione diventa inaffidabile.

---

## Flusso d'uso

L'utente usa **Node-RED** per la logica di scheduling. HA espone solo i pulsanti. La sequenza tipica in Node-RED:

```
Start Station 1 → timer N min → Stop → Start Station 2 → ...
```

---

## Decisioni architetturali — NON rimettere in discussione

- Niente meteo, scheduling, contabilità acqua, sensore umidità, slider portata
- Tutta la logica di processo in Node-RED
- Singolo progetto, nessuna dipendenza esterna (no `solem_toolkit`)
- Durate per stazione: gestite da `number` entity con `RestoreEntity` (persistono al riavvio)
- Stato reale da notifiche BLE come sorgente primaria; ottimistico come fallback
- Nessun polling: `update_interval=None`

---

## Comportamenti noti e gotcha

**Comandi confermati da BLE sniff app ufficiale (2026-04-12)**  
Vedere sezione "Formato comandi" per la lista completa. I comandi `0x12` (sessione arm), `0x22` (sprinkle), `0x24` (stop manual) sono stati sostituiti/rimossi sulla base dello sniff.

**`0x12` è sia Turn On (arm) che Start Irrigation**  
Con `station=0xFF, duration=0xFFFF`: arm della sessione BLE (vecchio approccio, non più usato).  
Con `station=N, duration=seconds`: avvia irrigazione su stazione N per N secondi — questo è il formato che usa l'app, confermato dallo sniff.

**`0xA0` = Turn On persistente**  
Confermato dallo sniff: l'app invia `0xA0` quando l'utente preme "Turn ON". Porta il device fuori da stato OFF permanente (a differenza del vecchio `0x12` che era solo un arm di sessione). Usare `0xA0` per il pulsante Turn On in HA.

**`0x15` = Stop senza OFF permanente**  
Confermato dallo sniff: l'app invia `31051500ff0000` per fermare l'irrigazione. Dopo `0x15`: `byte[3]=0x40`, `session_id=0xaaaaaa`, `countdown=0x00`. Il device **non** va in OFF permanente, quindi il comando successivo `0x12` funziona senza intervento dell'app.  
Confronto con `0xC0`: dopo `0xC0` il device ha `byte[3]=0x02`, `session_id=0x000000` (OFF permanente — richiede app/pulsante fisico per riattivare).

**Stop (`0x24`) NON ferma mai la valvola — rimosso**  
Confermato in tutti i test fisici: `0x24` non ferma l'irrigazione in nessuna condizione. Rimosso dal codice.

**Refresh State durante irrigazione la interrompe**  
Qualsiasi comando BLE successivo a un'irrigazione in corso la ferma. Non inviare Refresh durante un ciclo attivo.

**`station_byte` ha valori anomali durante sessioni avviata dall'app**  
Durante irrigazione avviata dalla app (non da HA), il byte 13 assume valori come `0xFD`, `0xFC`, `0xFE`. La formula `station_byte & 0x0F` produce 13, 12, 14 — fuori dal range delle stazioni configurate. Il codice ignora questi valori senza azzerare lo stato delle stazioni.

**RSSI via `async_last_service_info`**  
L'RSSI si legge dall'API bluetooth di HA (non dall'oggetto `BLEDevice` che in bleak moderno non espone `.rssi`). Si aggiorna ad ogni comando in `coordinator._update_rssi()`.

**`response=False` obbligatorio**  
La caratteristica `108b0002` è write-without-response. Non usare `response=True`.

---

## Debug

Abilita logging completo in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.solem_bt_controller: debug
```

I log includono:
- Payload hex di ogni write
- Raw hex dei pacchetti di notifica (per analisi byte layout)
- RSSI aggiornato dopo ogni comando
- Stato parsed (batteria, stazione, countdown, is_irrigating)

Per analizzare il layout raw del pacchetto di risposta, cerca la riga:
```
Parsed state: ... | raw: <18 byte hex>
```
