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

```python
# Accendi controller
struct.pack(">HBBBH", 0x3105, 0x12, 0xFF, 0x00, 0xFFFF)

# Avvia stazione X per Y minuti
struct.pack(">HBBBBH", 0x3105, 0x22, station, 0x00, minutes, 0xFFFF)

# Ferma irrigazione manuale
struct.pack(">HBBBH", 0x3105, 0x24, 0x00, 0x00, 0xFFFF)

# Spegni controller permanentemente
struct.pack(">HBBBH", 0x3105, 0xC0, 0x00, 0x00, 0x0000)

# Commit frame (obbligatorio dopo ogni comando)
struct.pack(">BB", 0x3B, 0x00)
```

**CRITICO — Turn On prima di Sprinkle**: il device ignora i comandi Sprinkle se nella stessa sessione BLE non ha ricevuto prima un Turn On. `sprinkle_station()` in `api.py` invia automaticamente `[turn_on, sprinkle]` nella stessa connessione con 1 secondo di pausa (`INTER_COMMAND_DELAY`) tra i due.

**`response=False`**: la caratteristica `108b0002` supporta solo Write Command (senza response). Usare `response=True` causa comportamento indefinito — non tornare a True.

### Risposte (notifiche su `108b0003`)

Ogni comando genera 6 pacchetti da 18 byte ciascuno:
- **Gruppo 1** (`byte[0] = 0x32`): stato PRIMA del comando
- **Gruppo 2** (`byte[0] = 0x3C`): stato DOPO il comando

Ogni gruppo ha 3 frammenti identificati da `byte[2]`: `0x02` (principale), `0x01`, `0x00`.

**Layout frammento principale (`byte[2] = 0x02`):**

| Byte | Significato | Note |
|------|-------------|------|
| 3  | Tipo frame | `0x42` = sessione irrigazione attiva; `0x02` = idle/off |
| 5–7 | Session ID | `0xAAAAAA` durante irrigazione; `0x000000` altrimenti |
| 10 | Batteria % | es. `0x51` = 81% |
| 13 | Station byte | `0xFF` durante irrigazione via BLE; valori `0xFC/0xFD/0xFE` durante sessioni app |
| 14 | Countdown | `0xFF` subito dopo Sprinkle (timer non ancora inizializzato); poi scende ~1/sec; `0xFF` anche in idle/off |

`is_irrigating = True` se `byte[3] == 0x42` OR (`countdown != 0xFF` AND `countdown > 0`).  
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

**Turn On obbligatorio prima di Sprinkle**  
Il device risponde "Write OK" ma non apre la valvola se non ha ricevuto Turn On nella stessa sessione BLE. Già gestito in `api.sprinkle_station()`.

**Turn On BLE NON sveglia il device da stato "permanently off"**  
Il comando `0x12` (Turn On) è un "arm" della sessione BLE corrente, non un cambio di stato persistente. Se il device è in stato OFF permanente (impostato da `0xC0` / Turn Off da HA o dall'app), il comando `0x12` viene accettato dal device (Write OK + 12 notification packets) ma il device rimane spento (`countdown=0xFF`). Quando si è in questo stato, il device potrebbe accodare il comando Sprinkle e eseguirlo quando l'utente riaccende dalla app. Solo l'app o il pulsante fisico possono portare il device fuori da OFF permanente. Il codice ora rileva questa condizione e NON avvia il safety timer.

**Stop (`0x24`) non ferma la valvola — da investigare**  
Il comando `0x24` (Stop manual sprinkle) non ferma fisicamente l'irrigazione in nessuno dei test condotti. Il device risponde con `Write OK` e notifiche, ma il countdown continua. Solo `0xC0` (Turn Off permanently) ferma la valvola in modo affidabile. Ipotesi: `0x24` ferma solo sessioni BLE-iniziate con `0x22` nella stessa sessione firmware, ma non sessioni avviate via app o che sono già transizionate allo stato "programma". Da investigare con varianti del comando (es. stazione specifica: `3105 24 01 00 ffff`, oppure trailing `0x0000`).

**Turn Off (`0xC0`) ferma sempre la valvola, ma mette il device in OFF permanente**  
Dopo `0xC0`, il BLE Turn On (`0x12`) NON porta il device in ON. Solo app o pulsante fisico. Usare Turn Off solo come emergency stop.

**Refresh State e Turn On invocati durante irrigazione la interrompono**  
Qualsiasi comando BLE successivo a uno Sprinkle in corso (incluso Turn On standalone e Refresh State) ferma l'irrigazione. Non inviare Refresh durante un ciclo.

**Countdown vs minuti richiesti**  
Il countdown nella risposta BLE non corrisponde ai minuti passati nel comando (es. 10 minuti richiesti → countdown ~235 sec). Potrebbe essere un cap firmware del device. Da investigare.

**`station_byte` sempre `0xFF` durante irrigazione avviata da HA**  
Durante irrigazione avviata via BLE, `station_byte` (byte 13) rimane `0xFF`. Il codice usa solo il `countdown` (byte 14) per rilevare irrigazione attiva.

**`station_byte` ha valori anomali durante irrigazione avviata dall'app**  
Durante irrigazione avviata dalla app (non da HA), il byte 13 assume valori come `0xFD`, `0xFC`, `0xFE`. La formula `station_byte & 0x0F` produce 13, 12, 14 — fuori dal range delle stazioni configurate. Il codice ora ignora questi valori senza azzerare lo stato delle stazioni.

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
