# Connected computers

Open Prometheus from its SSD launcher and choose **Remote**. Choose **Connect account**, finish Desktop Commander sign-in in your browser, and select an online computer by its name and device ID. Two computers can have the same name; the ID is the target.

The other computer must have the Desktop Commander agent running under its own Windows account. The SSD-root **SETUP-DELL-REMOTE.cmd** verifies and installs this release's bundled runtime and opens pairing. Sign into the same Desktop Commander account, match the displayed pairing code, and approve that computer. ChatGPT sign-in alone does not pair a computer. Leave the installed agent window open while remote work is needed.

The panel shows the tools actually supplied by the connected service. These include file operations, terminal commands, processes, searches and diagnostics, within the target Windows account's permissions. Choose a tool, enter its arguments, then select **Review action**. Check the full computer ID and arguments before **Run reviewed action**. Results are displayed as text or supported images. A returned result can contain an error or a still-running process; read it before proceeding.

Changing a target, reconnecting, editing arguments, or waiting five minutes invalidates a review. A review can run once. An interrupted request is never automatically repeated. If its outcome is unconfirmed, inspect the target before creating another action. Closing the connection does not undo a command already started on the remote computer.

This release exposes remote tools in the Remote panel. Conversation messages do not automatically execute remote commands. It does not supply mouse, keyboard, live desktop control, or unrestricted administrator rights. Services and elevated operations still require their own access on the target computer.

## Account and runtime

The endpoint is fixed to `https://mcp.desktopcommander.app/mcp`. Browser authorization and tokens stay in the current Windows profile under `%LOCALAPPDATA%\PrometheusRemote\ClientAuth`; no credentials are carried on the SSD. The device agent creates its own target-computer session. Never copy either computer's credential files.

The runtime is pinned by a SHA-256 manifest embedded in the signed core. Node and the remote connection packages are bundled, so a connect action does not download executable dependencies. MCP Remote 0.14.3 includes a documented patch that binds the session credential at its first identity check and disables tool replay after authorization, session, redirect, or network failures. Expired credentials require an explicit reconnect.

The local interface remains on loopback. Its remote endpoints require the current interface token and enforce browser origin/host checks. The local audit records the tool, computer ID and an argument digest; it does not write commands, output or account credentials. Choose **Disconnect** to stop this client's bridge; revoke account/device access in the provider dashboard when needed.

## Dell readiness

The setup bundle includes reviewed portable backup helpers, also in `tools/Prometheus.RemoteBackup`. They use the SSD's Python and discover its current drive letter. Install all four helpers together after the Dell's Host Agent installation, preserving the previous files. Task registration and a real backup/restore check on the Dell remain part of the live handoff; including these files does not establish a backup schedule.

The bundled dependency audit still reports eight transitive findings rooted in two CLI-only denial-of-service advisories, documented in the setup verification folder. The affected watch/formatting CLI paths are not called by the inspected normal agent/PDF library path. This is a reachability finding, not a claim that those dependencies are fixed.
