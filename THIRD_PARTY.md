# Third-party runtime components

Original Tablet ONVIF application code is MIT licensed. No implementation code has been copied from the ONVIF bridge repositories explored during planning.

Runtime dependencies retain their own licenses:

- Python (PSF license)
- aiohttp (Apache-2.0 and BSD portions; see its distribution metadata)
- defusedxml (PSF license)
- PyYAML (MIT)
- MediaMTX (MIT), pinned to 1.21.2 in the container
- FFmpeg from Debian, built with its packaged codecs including libx264; GPL/LGPL and codec-specific notices apply. Review the package copyright/source obligations when redistributing images. The container uses subprocesses, and the MIT application license does not relicense these binaries.

Keep dependency notices and required source offers accessible when publishing binary images. Pin/review the final image digest and dependency lock before a stable release.
