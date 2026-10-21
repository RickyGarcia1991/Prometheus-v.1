# Prometheus Maps & Studio

A local companion workspace for Prometheus. It has its own SSD launcher and does not modify or replace the signed Prometheus core. Version 1.0.1, assembled October 9, 2026.

## Start

After installation, open `D:\START-PROMETHEUS-MAPS-STUDIO.cmd`. The application opens your default browser on a random loopback port. **Close workspace** ends its server. The SSD folder and its sibling `Prometheus-Resources\Python` must stay together; a different drive letter is supported. No install, administrator access, account or API key is required for the local tools.

To preview this source package before SSD installation, run PowerShell:

```powershell
& .\Start-Studio.ps1 -RuntimeRoot 'D:\Prometheus-Resources\Python'
```

The launcher pins the six existing interpreter/core-runtime binaries and fails when they change. An intentional Python upgrade requires reviewing and updating those pins. It does not authenticate every standard-library file or protect against an attacker who can modify the installation itself.

## Included

| Area | Working functionality | Access and limits |
|---|---|---|
| Maps | Interactive OpenStreetMap; publisher region list; current Geofabrik file date and size | Online, no API key. 554 regions returned during testing. PBF extracts are raw data, not ready-to-view offline street maps. File modification is not a road survey date. |
| Images | Browser-local crop, resize, rotation, brightness, contrast, saturation; PNG/JPEG export | Offline editing; no paid service, upload or overwrite. AI generation remains a separate ComfyUI application. |
| NASA | Direct public media search and Earthdata collection search; source dates and citation export | No paid key. Some dataset downloads require a free Earthdata login. |
| Research | 29 sources covering NASA archives, technical reports, science data, patents and public/institutional research | Legitimate specialized and nonindexed sources. Account-restricted archives retain their own access requirements. |
| Automotive | 91 manufacturer/group/owner entries, 14 directories and registries, search and export | Sources for manuals, wiring, engine specifications, diagnostics, parts, recalls and training. A worldwide starting directory, not every manufacturer or a downloaded archive of all manuals. |
| Code checks | Local Python AST / JavaScript-family risky-pattern triage and report export | No execution or source persistence. Heuristic findings can be wrong or incomplete; full security review remains in Codex. |
| Connections | Nine service/tool entries with observed access status and official links | Website handoffs; Codex plugin credentials and tools are not transferred into this application. |

The adjacent reference library contains six public PDF documents with publisher URLs, retrieval dates, file sizes and SHA-256 digests. These include NASA references, Ford/Lucid public guidance and Mitchell 1's wiring-diagram feature guide. They are not a substitute for current vehicle-specific workshop instructions.

## ProDemand: safest available access

Open **ProDemand / Mitchell 1** in Connections, or [ProDemand](https://prodemand.com/), and sign in directly on its official page. Keep credentials in your own browser/password manager. No credential store, password field, cookie import, token extraction, hidden login API or background scraper exists in this companion.

Codex can assist with requested lookups through the browser session you authorize. The standalone Prometheus application does not automatically inherit that session or Codex's browser-control tools. If the session expires, sign in again. Browser sign-in, vehicle selection and search were verified on October 9, 2026. A 2020 Toyota Corolla SE 2.0L M20A-FKS was used without a VIN. The session displays **1SEARCH LIMITED**; Wiring Diagrams and Service Manual controls did not open, so full repair-information access is not yet verified. Mitchell 1's [account guidance](https://mitchell1.com/suretrack-faqs/) explains associating a SureTrack user with a paid shop subscription. The account may need that association or the separate shop-level login; no license or account setting was changed.

ProDemand is the requested primary source for **all available repair categories**, including component locations/tests, service manuals, DTCs, reset procedures, tires/lifting, fluids, specifications, bulletins, ADAS, wiring, and other subscribed resources. This preference does not establish entitlement to disabled modules.

For a lookup, supply the VIN or exact year/make/model, engine code, market and requested system. Keep the source title, vehicle match, publication/revision date, retrieval date and official link with each answer. Save documents only through the service's available export/print features and applicable subscription permissions; do not assume unlimited bulk retrieval or redistribution.

Mitchell 1's [integration page](https://mitchell1.com/home/repair-information/) describes pass-through launch and limited data integrations. Its [API eligibility page](https://mitchell1.com/resources/api-request/) restricts access to approved commercial software providers, enterprise customers and strategic partners; individual repair facilities, hobby projects and single-shop software are not eligible. Approval, documentation and potentially fees are separate from a ProDemand subscription. If eligible access is granted later, vendor-issued scoped credentials stored in Windows Credential Manager are preferable to scripted password entry. No application or vendor contact was submitted.

## Provider status on October 9, 2026

- Adobe and OpenAI Developers are installed/enabled in Codex. Adobe tooling initialized; paid feature entitlements were not tested. OpenAI documentation access is separate from billed API execution.
- Runway authentication was confirmed. The connected Free workspace exposed image models, but no video models. No credits were spent and no plan was purchased. Runway assets remain in its workspace.
- GPTs open in ChatGPT; this companion does not embed a custom GPT or turn a ChatGPT subscription into API access.
- ComfyUI is already on the SSD under `Prometheus-Resources\Media`; use the existing media launcher. Model availability, GPU compatibility and a successful generation were not verified here.
- Codex Security completed a source review of this companion. Daybreak entitlement was not granted; it is not required for these local checks and cannot be enabled by this application. The official access page is [ChatGPT Cyber](https://chatgpt.com/cyber).

## Accuracy, refresh and privacy

NASA and Geofabrik metadata refresh on explicit requests, with a ten-minute process cache and displayed retrieval times. No background polling or scheduled automation is installed. Maps load only after you request them. Search terms and coordinates go to their selected publishers; ordinary source links open external websites. Local image editing and code checks work offline.

The directory's 135 unique URLs were checked on October 9: 106 reachable, 20 unverified, nine access restricted. Reachability does not establish completeness, authenticity of each linked document, your entitlement, or continuing availability. Redirect query/fragment metadata was removed. Further official manufacturers can be discovered through the national registries; neither NHTSA nor this directory is a complete global census.

Match technical information to the specific vehicle. Cite the original document, distinguish record dates from observation dates, and check for a newer revision before relying on a repair procedure. NASA media may have third-party credit or usage restrictions.

Security controls include loopback binding, Host/Origin/session-token checks, fixed static routes, HTTPS publisher restrictions, bounded request sizes, three same-origin redirect hops, and no reviewed-code execution. The browser-origin checks do not isolate the server from other native processes on your computer. The four-megabyte limit applies to final JSON responses, not every network byte in a redirect chain.

The security report records the original launcher finding. The separate remediation note explains the fix and its validation; the historical report was not rewritten to erase the finding.

## Retest update

Version 1.0.1 clears outdated region links and citation/code exports when their inputs change, prevents late tasks from reopening a closed workspace, and adds initialization retry and Cancel online lookup. Cancellation stops browser waiting; the server may finish its one publisher worker. Server callers wait at most 25 seconds, and additional publisher requests receive a busy response while that worker remains occupied. Local tools remain independent. Launcher failures write startup-error.txt and show a message; OPEN-STUDIO.txt contains the current loopback address if the browser does not open. These runtime files contain no account credentials.

The main Prometheus interface separately includes Smart home mode with a Home Assistant bridge on standby. Pairing instructions and platform limits are in the core docs/SMART-HOME.md. No smart-home devices are connected by installing this companion.

## Money and lifecycle update (1.0.2)

Money & investing adds 26 official resources, a stock research checklist, a browser-local moving-average historical simulator with benchmark and cost comparisons, savings scenarios and income estimates. See ../../docs/MONEY-RESEARCH.md. No live price feed, trading or automated personal claims are connected. Studio now observes the shared shutdown/eject markers and closes during SSD departure. Physical removal still depends on Windows confirming safe eject.
