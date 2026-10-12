# Bluetooth adapter review — Ubuntu portable secondary (2026-10-11)

**Decision:** Add ONLY two local, bounded, read-only and explicitly approved Bluetooth controller metadata actions. No new hardware purchase is necessary for these two actions.

## Verified on the Surface Laptop Go

- Ubuntu BlueZ CLI: `bluetoothctl 5.85`.
- Laptop has a functioning Bluetooth controller, powered on and not discoverable or pairable.
- `/usr/bin/bluetoothctl show` and `/usr/sbin/rfkill list bluetooth` are available.
- The approved Prometheus actions are `bluetooth_controller_status` and `bluetooth_radio_blocks`.
- Both return parsed, redacted status values. Controller addresses/names, device UUIDs and nearby devices are NOT exposed or written into Prometheus chat memory. Non-Bluetooth radio block states are not attributed to Bluetooth.
- No discovery, pairing, connection, packet capture, transmission, spoofing, Bluetooth target testing, or configuration changes.
- Exact user approval remains required via Prometheus Core or the local GUI.
- Bluetooth adapter errors return generic failure codes without raw hardware identifiers.

## Hardware decisions

| Use case | Hardware | Decision |
|---|---|---|
| Basic local Bluetooth controller diagnostics | Existing laptop Bluetooth chipset | **Already present; no purchase** |
| Test BLE development firmware / Thread / Zigbee / 802.15.4 | Nordic nRF52840 Dongle | Optional; manufacturer lists protocol support, but validate exact app/firmware |
| Dedicated historical Ubertooth tool compatibility | Great Scott Gadgets Ubertooth One | **Retired by manufacturer; do not buy as default** |
| Full Bluetooth protocol testing requiring specific capture features | Specialized capture gear, selected after a specific validated application is selected | Not installed; do not assert generic Bluetooth adapters are sniffers |

Vendor/authoritative sources:
- Nordic nRF52840: https://www.nordicsemi.com/Products/Development-hardware/nrf52840-dongle
- Ubertooth retired status: https://greatscottgadgets.com/ubertoothone/
- Kali current Bluetooth metapackage names: https://www.kali.org/tools/kali-meta/
- Kali retired and broken tools: https://www.kali.org/docs/tools/removed-tools/

## Tests and command examples

```bash
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/prometheus.py security adapters
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/prometheus.py security run --tool bluetooth_controller_status --approve
~/prometheus-env/bin/python ~/Prometheus-Ubuntu/prometheus.py security run --tool bluetooth_radio_blocks --approve
cd ~/Prometheus-Ubuntu
~/prometheus-env/bin/python -m pytest -q tests/test_security_bluetooth.py
```

The adapters are device-read-only, not security scanners. They must be retested after rebasing against newer Dell code. Never run the Dell-side controller or SSD executable on Ubuntu without a separate signed and validated Linux port.
