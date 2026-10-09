# Smart home in Prometheus

Choose **Smart home** in the main interface, then type a command or use the existing Record button and review the transcription before Send. This release includes a Home Assistant connector in standby. No hub, vendor account or physical device has been paired or tested. It does not install Home Assistant, discover your network, create subscriptions, or queue commands for later.

Examples after pairing:

- `turn off bedroom lights`
- `turn on front porch lights`
- `dim bedroom lights to 40%`
- `set bedroom lights color to blue`
- `set living room speaker volume to 25%`
- `pause living room speaker`
- `status bedroom lights`
- `turn off bedroom lights; turn on front porch lights`

Use `help` for guidance and `devices` for your configured names. Names match exactly; Prometheus does not guess which room to control. One to four semicolon-separated commands are supported. The entire batch is validated before sending. A hub error stops the remaining actions; completed actions are not rolled back or retried. A reported hub state is not independent physical verification.

## Pair once on each Windows account

1. Run Home Assistant on a supported hub/host and pair devices inside its own interface. Keep Amazon, Hue, Apple, Google and other passwords there. Follow the relevant integration's account and hardware requirements.
2. Copy `config/smart-home.example.json` to `Prometheus-Data/SmartHome/config.json` on the SSD (or `SmartHome/config.json` beside a custom memory database). Replace the example endpoint and entity IDs. Choose only the actions you intend to allow. Keep `enabled` false during setup.
3. Prefer a correctly verified HTTPS endpoint. A hostname requires `allow_remote_https: true`. For a trusted private LAN IP using HTTP, explicitly set `allow_insecure_http: true`; HTTP transmits the bearer token unencrypted on that network. No certificate-validation bypass is provided.
4. Create a Home Assistant long-lived access token for a dedicated user with only the access you intend. Its effective permissions are those of that user; this is not a claim that Home Assistant tokens have individual action scopes.
5. Run `tools/Set-HomeAssistant-Credential.ps1 -ConfigPath '<your config.json path>'` interactively. Enter the token in its secure prompt. The script stores it in Windows Credential Manager, creates a separate random browser control key, and writes only that key's SHA-256 hash to the configuration. It preserves a configuration backup and leaves controls disabled.
6. Review devices and set `enabled` to true. Paste the private control key into the Smart home field in Prometheus. It is kept only in that page's memory and cleared on close/restart. Retrieve it later with the same script's `-ShowControlKey` option. Do not paste either secret in chat, prompts, source code or GitHub.
7. Start with a status query and one lamp. Confirm the actual device before adding others. Set `enabled` false to return to standby; revoke the HA token in Home Assistant to revoke access. A new laptop/Windows account requires pairing again. The SSD and flash key carry no HA token.

## Supported action mappings

| Home Assistant entity | Explicitly allowed actions |
|---|---|
| light | on, off, brightness 0–100%, seven named RGB colors, status |
| switch / fan | on, off, status |
| media_player | play/resume, pause, stop, volume 0–100%, status |
| climate | temperature within per-device minimum/maximum; C/F is checked against hub metadata; status |
| scene | activate, status |
| button | run, status |
| notify | `announce on exact name: plain text message` (up to 300 characters) |
| sensor / binary_sensor | status |

Temperature entries require `minimum`, `maximum` and `unit` (`C` or `F`). Actions unsupported by the specific integration still fail at the hub. A scene, routine button, or grouped light can control multiple devices; inspect its downstream effects before allowing it, and review again when editing that routine. There is no arbitrary service call, global all-device target, model-generated action, Alexa free-text passthrough, lock/alarm control, camera stream or microphone-opening action.

The additional private key prevents callers with only the general loopback bootstrap token from using physical controls. This remains a local application for a trusted Windows account: software running as that same user can access that user's credentials; administrators and writable installation/configuration files are part of the trust boundary. Research results and model output cannot invoke this connector. Commands and replies follow the main application's local conversation history; secrets are excluded.

## Platform routes and limits

Use the [Home Assistant integration directory](https://www.home-assistant.io/integrations/) to enroll supported models. Philips [Hue](https://www.home-assistant.io/integrations/hue/), [Matter](https://www.home-assistant.io/integrations/matter/), [HomeKit Device](https://www.home-assistant.io/integrations/homekit_controller/), [Google Cast](https://www.home-assistant.io/integrations/cast/), [Nest](https://www.home-assistant.io/integrations/nest/), [SmartThings](https://www.home-assistant.io/integrations/smartthings/), Sonos, Shelly, TP-Link, Zigbee and Z-Wave devices can use the supported entity mappings when their integration exposes them. This is one bridge architecture, not a direct native SDK for every manufacturer.

[Alexa Devices](https://www.home-assistant.io/integrations/alexa_devices/) is maintained by Home Assistant community contributors. It exposes supported Echo media, notification and routine entities; availability depends on account/model. Alexa Smart Home and Google Assistant integrations primarily expose HA devices to those assistants, which is a different direction from importing all their linked devices. Apple/HomeKit and Matter commissioning also have per-device limits.

**Drop In:** this release hands off to the Alexa app or an authorized Echo. A Drop In status selector does not initiate two-way audio. No supported general household API for initiating Drop In was verified; [Amazon's documented Communications API](https://developer.amazon.com/en-US/docs/alexa/alexa-smart-properties/communications-api.html) is for Smart Properties with specific deployment restrictions. One-way announcements are separate from intercom calls.

Home Assistant itself can run locally without a cloud subscription. Optional HA Cloud is paid; Nest Device Access and some cloud APIs have fees or changing terms. SmartThings announced paid API tiers for October 2026. No paid service was activated. Check current provider terms at pairing time.

Implementation uses the documented [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/): real controls call `/api/services/{domain}/{service}`, followed where possible by a state read. Updating `/api/states` is not used as a substitute for controlling hardware. Documentation checked October 9, 2026.
