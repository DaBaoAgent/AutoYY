# Security policy

## Reporting a vulnerability

Please report security issues privately to the repository maintainers instead of opening a public issue with exploit details, credentials, or private source URLs. Include the affected version, reproduction steps, impact, and the smallest safe proof of concept.

## Security boundaries

AutoYY treats manifest paths and external-tool arguments as untrusted input. Topic directories must remain below the configured output root; absolute paths and `..` escapes are rejected before file creation.

Browser cookies, access tokens, proxy credentials, and account session data must never be written to manifests, state files, logs, fixtures, or the repository. Cookie use requires explicit user authorization.

AutoYY does not bypass DRM, paywalls, regional access controls, private-video authorization, or platform security. Download support is intended for owned, licensed, public-domain, Creative Commons, or otherwise authorized material.

Generated state under `.autoyy/` contains workflow metadata and fingerprints only. It must not contain secrets.

## Supported versions

Security fixes target the current main branch and the latest tagged release. Older unmaintained releases may not receive patches.
