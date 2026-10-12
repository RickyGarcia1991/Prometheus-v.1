# Prometheus: 814-package hardware sourcing and staged build plan

As of October 11, 2026. **Dell is primary; Ubuntu Surface is portable secondary.**

This guide contains **35 sourced hardware/lab categories** and the accompanying report maps **all 814 pinned Kali package references** to potential hardware dependencies. It does NOT claim 814 working executables, 814 individually verified hardware pairings, or a requirement to buy every device.

Detailed machine-readable per-package source map: `docs/KALI_HARDWARE_MATRIX_2026-10-11.json`.
Sourcing registry: `tools/ubuntu/security_hardware_sources.json`.
Official removed-tools reference subset: `tools/ubuntu/kali_removed_reference.json`.

## Hardware currently verified on Ubuntu

- Intel AX201 Wi-Fi 6, with `monitor` mode advertised by `iw list`. Capture, concurrency and injection **not tested**; changing the built-in adapter mode might disconnect remote access.
- Intel AX201 Bluetooth/BlueZ 5.85: powered, local status read-only actions verified. No external Bluetooth dongle needed for those actions.
- USB 3-capable host controller, ~6.8 GiB RAM, approximately 90 GiB free storage.
- Dedicated external Prometheus SSD **not connected**, not read, never auto-imported or formatted.

## Design principle

Software-only operations run on the existing laptop. An external reader, probe, radio, GPU, phone or test device is needed **only if a specific permitted workflow requires it**. Kali metapackage membership alone is not hardware proof. Counts can overlap between categories; **do not total them as purchase quantities**.

## Suggested procurement phases

### Phase 0: Use existing equipment first (no purchase)

Existing Dell and Ubuntu laptops, Intel Bluetooth and Wi-Fi adapters, desktop USB 3 controller, isolated software VMs, Prometheus virtualenv, passive host diagnostics and offline PCAP/file analysis. Keep the Dell primary and back up the SSD before physical migration.

### Phase 1: Controlled learning/test lab — buy only as needed

A dedicated test Wi-Fi access point, powered USB 3.x hub, a supported USB Wi-Fi adapter if simultaneous connectivity is needed, Raspberry Pi 5 **optional** as portable Linux lab controller, Arduino UNO R4 WiFi **optional** as a programmable target, Raspberry Pi Debug Probe/Bus Pirate for low-voltage firmware work, Saleae or comparable logic analyzer where waveform analysis is needed, and multimeter/ESD-safe bench equipment.

For a Pi, buy the official power supply and cooling. For Arduino UNO R4 WiFi, the board has a CAN controller, but **connecting to a vehicle CAN bus requires a compatible transceiver/isolation and controlled bench setup**. Neither SBC nor microcontroller replaces a spectrum-capable SDR, RFID reader or licensed-frequency RF frontend.

### Phase 2: Specific radio, RFID, Bluetooth and forensic workloads

Receive-only SDR: RTL-SDR Blog V4 with its updated Linux drivers. Dedicated BLE/802.15.4 test targets: Nordic nRF52840 USB development dongle. LF/HF RFID: supported Proxmark3 RDV4 and authorized test cards. UHF RFID needs a separate regional UHF reader. Advanced SDR: HackRF One, with matching antennas, filters, attenuators and dummy load; any transmission must meet local law. Physical digital forensics: matched hardware write blocker plus separate evidence disk, not the Prometheus SSD.

### Phase 3: Advanced/professional tools — defer until a real need

UHD-compatible Ettus USRP and workstation-class GPU, specialty RF gear, calibrated multi-protocol test instruments, automotive SocketCAN bench hardware, managed Ethernet TAPs, owned mobile-device targets and platform-specific Apple/Windows lab hosts. Avoid retired Ubertooth One unless a legacy compatibility project specifically requires it; its manufacturer says it is retired.

## Complete sourcing-class matrix

Vendor links below are manufacturer/project technical pages when available. They do **not** confirm live price, availability, exact Ubuntu 26.04 compatibility, or interoperability with all Kali tools.

| Hardware class / example | Priority | Potential package references* | Source |
|---|---|---:|---|
| **Ubuntu Surface Laptop Go / Dell workstation** — Existing computer; no purchase needed. | already_owned | 0 | [Technical documentation](https://www.kali.org/docs/general-use/metapackages/) |
| **Isolated local VMs or lab target devices** — Separate VM or controlled test host; build with existing Dell first. | recommended | 145 | [Technical documentation](https://www.kali.org/docs/general-use/metapackages/) |
| **Powered USB 3.x hub and data cables** — Anker/StarTech/other powered hub; validate per-port current and USB data bandwidth. | optional | 0 | [Technical documentation](https://www.startech.com/) |
| **Raspberry Pi 5 SBC with official supply and cooling** — Raspberry Pi 5 8 GB or suitable alternative; Pi not a substitute for specialized RF hardware. | optional | 0 | [Technical documentation](https://www.raspberrypi.com/products/raspberry-pi-5/) |
| **Arduino UNO R4 WiFi or compatible microcontroller board** — Arduino UNO R4 WiFi (RA4M1 + ESP32-S3); CAN transceiver required to connect bus. | optional | 0 | [Technical documentation](https://store.arduino.cc/products/uno-r4-wifi) |
| **Dedicated lab router, switch, Ethernet NICs** — Spare router and wired NICs; use isolated VLAN or physically separate switch. | optional | 25 | [Technical documentation](https://www.kali.org/docs/general-use/metapackages/) |
| **Authorized Ethernet network TAP / SPAN-capable switch** — Garland Technology copper/fiber TAP suited to link speed and medium. | conditional | 24 | [Technical documentation](https://www.garlandtechnology.com/products/network-taps) |
| **Dedicated Wi-Fi access point under user control** — Spare Wi-Fi router on isolated network; do not test others' networks. | conditional | 19 | [Technical documentation](https://www.kali.org/tools/kali-meta/) |
| **USB Wi-Fi adapter with verified Linux chipset/monitor-mode support** — ALFA AWUS036ACM MT7612U mainline driver; verify monitor capability under current Ubuntu kernel before purchase. | conditional | 19 | [Technical documentation](https://docs.alfa.com.tw/Support/Linux/MT7612U/) |
| **Local Bluetooth controller / BlueZ USB adapter** — Surface internal Bluetooth radio already detected; add dongle only if separate controller needed. | already_owned | 11 | [Technical documentation](https://www.kali.org/tools/kali-meta/) |
| **Bluetooth Low Energy / 802.15.4 development USB board** — Nordic nRF52840 Dongle supports BLE and 802.15.4; it is not a turnkey wideband Bluetooth sniffer. | conditional | 10 | [Technical documentation](https://www.nordicsemi.com/Products/Development-hardware/nrf52840-dongle) |
| **Ubertooth One specialized Bluetooth adapter** — Manufacturer marks Ubertooth One retired; source only for compatibility research and verify support before purchase. | avoid_default | 1 | [Technical documentation](https://greatscottgadgets.com/ubertoothone/) |
| **RTL-SDR Blog V4 USB receive-only software-defined radio** — RTL-SDR Blog V4; requires V4-compatible Linux drivers; cannot transmit. | conditional | 12 | [Technical documentation](https://www.rtl-sdr.com/v4/) |
| **HackRF One wideband half-duplex USB SDR** — Great Scott Gadgets HackRF One 1 MHz–6 GHz; use correct filters and dummy load; comply with RF regulations. | conditional | 3 | [Technical documentation](https://greatscottgadgets.com/hackrf/one/) |
| **YARD Stick One sub-1-GHz transceiver** — Great Scott Gadgets YARD Stick One; correct sub-GHz band and antenna/dummy load. | conditional | 1 | [Technical documentation](https://greatscottgadgets.com/yardstickone/) |
| **Ettus USRP SDR hardware with UHD driver** — Ettus USRP series; costly and compute-intensive, check particular supported generation. | specialized | 3 | [Technical documentation](https://www.ettus.com/all-products/usrp-b200mini/) |
| **Proxmark3 RDV4 / current compatible RFID lab reader** — Proxmark3 RDV4 supported branch; firmware and host-client must match; avoid retired clones. | conditional | 3 | [Technical documentation](https://proxmark.com/proxmark-3-hardware/) |
| **Dedicated UHF RFID reader and test tags** — Choose region-compatible, documented reader only for UHF-specific projects. | specialized | 0 | [Technical documentation](https://www.gs1.org/standards/epc-rfid) |
| **CCID/PCSC smart-card reader and owned test card** — Vendor-supported USB CCID reader; check card standards and Ubuntu pcsc-lite. | conditional | 0 | [Technical documentation](https://pcsclite.apdu.fr/) |
| **Digital/analog USB logic analyzer with safe probes** — Saleae Logic 8 / Logic Pro 8; recording RAM and USB bandwidth requirements vary. | conditional | 11 | [Technical documentation](https://www.saleae.com/logic) |
| **Bus Pirate 5/6 serial-protocol probe** — Dangerous Prototypes Bus Pirate; verify target voltage and signal level. | conditional | 0 | [Technical documentation](https://docs.buspirate.com/docs/hardware/bp5rev10/hardware/) |
| **CMSIS-DAP SWD probe / JTAG debug adapter** — Raspberry Pi Debug Probe for SWD+UART; JTAG needs compatible alternate probe. | conditional | 1 | [Technical documentation](https://www.raspberrypi.com/documentation/microcontrollers/debug-probe.html) |
| **USB-to-UART level-safe serial adapter** — Choose FTDI/CP2102 USB interface; match target voltage 1.8/3.3/5V and isolate high-voltage circuits. | conditional | 3 | [Technical documentation](https://www.raspberrypi.com/documentation/microcontrollers/debug-probe.html) |
| **Compatible SPI/EEPROM flash programmer** — Select programmer by exact flash chip/voltage; back up before any write; Bus Pirate may suffice. | conditional | 1 | [Technical documentation](https://docs.buspirate.com/docs/hardware/bp5rev10/hardware/) |
| **Hardware forensic write blocker and evidence drive** — OpenText forensic bridge or equivalent USB/SATA/NVMe solution matching source media. | conditional | 101 | [Technical documentation](https://www.opentext.com/products/digital-forensic-hardware) |
| **Dedicated evidence/image SSD and backup space** — Separate SSD with capacity >= source media, encrypted if necessary. | conditional | 101 | [Technical documentation](https://www.opentext.com/products/digital-forensic-hardware) |
| **GPU workstation with suitable vendor drivers** — Prefer Dell or separate workstation, not Surface integrated Intel GPU; verify ROCm/CUDA/OpenCL support. | optional | 49 | [Technical documentation](https://www.kali.org/docs/general-use/metapackages/) |
| **Owned Android handset with cable** — Spare test phone; supported Android version and USB data cable; do not use primary personal phone. | conditional | 1 | [Technical documentation](https://developer.android.com/studio/command-line/adb) |
| **Mac/iPhone development/test hardware** — A Mac plus owned iPhone if Apple workflows are actually needed; Linux alone cannot replace Xcode. | specialized | 0 | [Technical documentation](https://developer.apple.com/xcode/) |
| **Isolated SIP PBX and test endpoint/softphone** — Software PBX and softphones can run in VMs; dedicated physical VoIP phones are optional. | conditional | 18 | [Technical documentation](https://www.asterisk.org/) |
| **USB-CAN adapter (SocketCAN) and isolated CAN test bench** — CANable with supported gs_usb/candleLight firmware, termination and isolated test harness; use receive-only first. | conditional | 0 | [Technical documentation](https://canable.io/getting-started.html) |
| **RF antennas, attenuators, filters, shielding, dummy loads** — Band-matched SMA vs RP-SMA; never transmit out-of-band or use disconnected RF loads. | conditional | 13 | [Technical documentation](https://greatscottgadgets.com/hackrf/one/) |
| **ESD-safe bench, multimeter, current-limited power and isolation** — Insulated probes, bench PSU, ESD mat, voltage measurement and appropriate electrical protection. | recommended | 11 | [Technical documentation](https://www.raspberrypi.com/documentation/microcontrollers/debug-probe.html) |
| **USB GNSS/GPS receiver** — Generic USB GNSS receiver with gpsd compatibility; unnecessary for ordinary Wi-Fi analysis. | conditional | 0 | [Technical documentation](https://gpsd.io/) |
| **Cellular modem or shielded private LTE/5G test setup** — Specialized SDR, shielded enclosure/dummy loads, private test SIMs; avoid live carrier access. | specialized | 0 | [Technical documentation](https://www.ettus.com/all-products/usrp-b200mini/) |

*Potential references* indicates the number of package rows linked by exact-name hints or **category-level inference**; it is not a count of tools that require buying that hardware. A package may appear in several groups. Zero does not mean the class is unnecessary in all possible use cases.

## Manufacturer-verified considerations

- **Wireless:** ALFA's MT7612U documentation covers AWUS036ACM with in-kernel Linux drivers, but confirm your precise kernel and monitor-mode needs before purchase. The Surface Intel AX201 already advertises monitor mode; separate USB hardware mainly helps retain the network connection.
- **Bluetooth:** Nordic nRF52840 is for programmable BLE, Zigbee/Thread and 802.15.4 development. It is not a drop-in replacement for every Ubertooth Bluetooth experiment. Ubertooth One is retired.
- **SDR:** RTL-SDR V4 is receive-only and requires updated drivers; HackRF One can transmit and receive half-duplex (1 MHz–6 GHz). Ettus USRP/UHD is for higher-end SDR tasks; check model and host requirements.
- **RFID:** Proxmark3 RDV4 is a documented LF/HF device family; an EPC UHF test requires different reader hardware. Check exact host client and firmware pairing.
- **PCB/firmware:** Bus Pirate supports SPI/I2C/UART/1-Wire protocols; Raspberry Pi Debug Probe supports SWD and UART. Check voltage levels and target permissions, and back up firmware before any write.
- **Forensics:** OpenText hardware write blockers help preserve original storage evidence. Software permissions, a read-only mount request, or a SHA-256 calculation alone are not equivalent to hardware write blocking.
- **Automotive:** CANable-style USB-CAN can expose SocketCAN after the correct firmware; bench-harness isolation, termination, voltage compatibility and permission are necessary. No live-vehicle transmit operations are enabled in Prometheus.

## What can be built with Raspberry Pi or Arduino?

| Build | Host platform | Additional modules | Limitations |
|---|---|---|---|
| Offline network diagnostics / source fetch proxy for Prometheus | Raspberry Pi 5 | USB storage, Ethernet/Wi-Fi, power/cooling | Pi is a *separate host*, not a universal replacement for RF frontends or USB debug probes |
| BLE/Thread test node | Nordic nRF52840 | Own test firmware and USB host | Dedicated development radio; not all Bluetooth sniff/capture features |
| USB serial and SWD microcontroller test rig | Pi Debug Probe + optional Raspberry Pi/Pico | Target board, 3.3-V-safe wiring | Debug Probe SWD does not cover every JTAG target |
| Sensor, relay, or CAN demonstration fixture | Arduino UNO R4 WiFi | Correct transceiver, 5V/3.3V isolation, optional power stage | Never connect bare GPIO directly to automotive or mains voltage |
| Passive packet/file analysis workstation | Dell or Ubuntu | Owned PCAPs, storage, optional NIC | No special RF device required for offline PCAP analysis |
| GNURadio/Spectrum experiment | Ubuntu/Pi/Linux host | RTL-SDR V4 or HackRF or USRP as appropriate | Compute/bandwidth/antenna/driver constraints vary; transmit only lawfully |
| Read-only evidence imaging | Dell/Ubuntu | Dedicated write blocker and image storage | Not the same as SSD migration; no original-media writes permitted |

## Procurement gates before buying

1. Select a named task and the exact package/command. Verify that it is maintained, legal for the intended authorized target, installable on the host and has a tested reviewed adapter.
2. Identify the matching chipset/band/voltage/firmware and use the manufacturer's specifications. Test on known-good equipment before buying additional add-ons.
3. Budget for power, USB cables, ports, antenna connector types (SMA vs RP-SMA), heatsinks, ESD protection, known-good storage, test fixtures, drivers and replacement accessories.
4. Separate portable use (small/light and low power) from the Dell bench (GPU, USRP, large forensic images, full VM lab).
5. Do not buy every class from the matrix. Most of the 814 package references are software, libraries or multi-command packages that run without specialized external equipment.
6. No dark-web purchasing is necessary. Use authorized manufacturers/distributors and official project docs; verify stock, warranty, licensing and provenance.
7. The Dell and SSD remain unchanged; transfer only after verified release, backup, compatible filesystem, one-way staged import and rollback testing.

## Evidence and limitations

Pinned Kali catalog: https://www.kali.org/tools/kali-meta/
Removed/replaced Kali utilities: https://www.kali.org/docs/tools/removed-tools/
Kali category scope: https://www.kali.org/docs/general-use/metapackages/

Inventory status: **814 package references mapped**, **35 classes sourced**. **Zero of the 814 packages were individually certified against all possible hardware combinations.** The report includes manufacturer sources and explicit evidence grades. Hardware compatibility must be verified at purchase/integration time.
