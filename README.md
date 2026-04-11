# Solem BT Watering Controller

A Home Assistant custom integration to control a **Solem BL-IP** irrigation controller via Bluetooth BLE.

## Requirements

### Hardware
- Solem BL-IP irrigation controller
- A Bluetooth adapter reachable from Home Assistant. If HA runs on a machine without Bluetooth, an **ESP32 running ESPHome Bluetooth Proxy** works well.

### Software
- Home Assistant 2024.12 or newer
- Python packages (installed automatically): `bleak >= 0.22.3`, `bleak-retry-connector >= 3.6.0`, `tenacity >= 8.0.0`

## Installation

### Via HACS (recommended)
1. In HACS, go to **Integrations** → three-dot menu → **Custom repositories**
2. Add this repository URL, category **Integration**
3. Install **Solem BT Watering Controller**
4. Restart Home Assistant

### Manual
Copy the `custom_components/solem_bt_controller/` folder into your HA `config/custom_components/` directory and restart.

## Configuration

Go to **Settings → Integrations → Add Integration** and search for **Solem BT Watering Controller**.

**Step 1 — Device**
| Field | Description |
|-------|-------------|
| MAC address | BLE MAC of the Solem controller (e.g. `C8:B9:61:F0:15:30`) |
| Number of stations | How many stations the controller manages (1–16) |

**Step 2 — Safety durations**
Set a maximum irrigation time (minutes) for each station. This is the fallback duration passed to the device — it limits how long the valve stays open if HA loses contact mid-session.

**Options (post-setup)**
Go to the integration card → **Configure** to adjust the Bluetooth connection timeout.

The per-station duration can be changed at any time from the dashboard via the `Station N Duration` number entities — no reconfiguration needed.

## Entities

For a 4-station setup the integration creates:

### Buttons
| Entity | Description |
|--------|-------------|
| `button.solem_XXXXX_start_station_1` … `_4` | Start manual irrigation on that station |
| `button.solem_XXXXX_stop_irrigation` | Stop all active manual irrigation |
| `button.solem_XXXXX_turn_on` | Enable the controller |
| `button.solem_XXXXX_turn_off` | Disable the controller permanently |
| `button.solem_XXXXX_refresh` | Read device state without changing anything *(diagnostic)* |

### Sensors
| Entity | Description |
|--------|-------------|
| `sensor.solem_XXXXX_station_1` … `_4` | Station state: `Sprinkling` / `Stopped` |
| `sensor.solem_XXXXX_controller_state` | Controller state: `On` / `Off` |
| `sensor.solem_XXXXX_battery` | Battery level % *(diagnostic)* |
| `sensor.solem_XXXXX_signal_strength` | BLE RSSI in dBm *(diagnostic)* |

### Number entities
| Entity | Description |
|--------|-------------|
| `number.solem_XXXXX_station_1_duration` … `_4` | Max irrigation duration for each station (1–240 min) |

## Usage with Node-RED

All scheduling and process logic lives in Node-RED. A typical watering sequence:

```
[button: Start Station 1]
       ↓
[Node-RED timer: N minutes]
       ↓
[button: Stop Irrigation]
       ↓
[button: Start Station 2]
       ↓
...
```

The integration does not implement schedules, weather checks, or multi-station sequencing — those belong in Node-RED.

## BLE Protocol notes

The controller communicates over a custom GATT service (`108b0001-…`):

- **Write characteristic** `108b0002-…` — HA → device (commands)
- **Notify characteristic** `108b0003-…` — device → HA (state responses)

Every command must be followed by a commit frame (`0x3B 0x00`). The device replies with 6 notification packets (two groups of 3 × 18 bytes): state before and state after the command. Battery level, active station, and remaining countdown are parsed from these packets.

**Important**: the device ignores Sprinkle commands unless the controller has been explicitly turned on first in the same BLE session. The integration handles this automatically — `Start Station` always sends a Turn On before the Sprinkle command in a single connection.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Integration fails to load | Missing Python packages | Restart HA after install |
| Cannot connect at setup | Device out of BLE range or proxy offline | Move ESP32 closer; check ESPHome logs |
| Station shows `Stopped` after start | BLE write failed silently | Check RSSI sensor — below −85 dBm is unreliable |
| Signal strength always `unknown` | No command sent yet | Press **Refresh State**; RSSI updates on each command |

For detailed diagnostics enable debug logging:

```yaml
# configuration.yaml
logger:
  logs:
    custom_components.solem_bt_controller: debug
```
