# Contributing

Use the development commands in README. Add meaningful regression tests for authentication, event ordering or client interoperability changes. Keep source credentials out of fixtures, logs and example configuration.

For NVR reports include product/version, bridge version, authentication method, failing ONVIF operation if known and the redacted UI diagnostics. Distinguish field validation, authentication, stream startup and event subscription failures. Add client quirks behind documented compatibility behavior rather than disabling authentication globally.

A successful local test does not justify claiming ONVIF certification or universal NVR compatibility. Complete the acceptance matrix before release. Publish a changelog, tagged source and reproducible container build, and retain dependency notices.
