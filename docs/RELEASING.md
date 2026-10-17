# Source and license distribution

The runtime Docker target includes LICENSE, THIRD_PARTY_NOTICES.md, application
source, dependency source archives and original license notices. Its footer
links to `/legal/`; `/legal/source` packages the currently installed application
source, including local modifications, using a narrow source-file allowlist.
Never replace this with an archive of the working directory: it may contain
`.env`, PDFs, accounts and personal rules.

`python scripts/prepare_legal.py` collects the installed Python distributions'
sources and notices, the exact MuPDF source release, PDF.js source, and Debian
package copyright files. It writes `legal/`, which is ignored by Git. Downloads
happen during the image build, never during document processing. A missing
source archive or hash mismatch fails the build. PyPI downloads are verified
against release SHA-256 digests; MuPDF and PDF.js use the reviewed hashes in
`scripts/source-lock.json`. Update that file when upgrading those engines.
All downloaded artifacts have a recorded hash.

For native development, run that command after installing/updating Python and
Composer dependencies to make dependency source downloads available locally.
Re-run it after changing dependency versions. Application source is always
packaged on demand, so a modified deployment does not point to an older Git tag.

## Publishing a container image

1. Build the `runtime` target from the intended source tree. Keep the image
   digest, dependency manifest and source bundle together for that release.
2. Export the material with `docker cp CONTAINER:/app/legal ./release-legal`
   and obtain `/legal/source` from that same image. Publish these alongside the
   image, at no extra charge, with a clear source link in the registry/release
   description. Keep the exact source material accessible while distributing
   the image. A link to the moving main branch is not an exact source release.
3. To retain all Debian sources automatically, build the optional release target:

   ```sh
   docker build --target distribution-sources -t ainonymify:distribution .
   ```

   This runs `scripts/collect_system_sources.py`, downloads the exact Debian
   source packages (including Debian patches), and records their SHA-256 hashes.
   Publish its `legal/` directory alongside the image as above. This larger
   target is intended for redistribution; normal local Compose builds use the
   smaller `runtime` target.

   The manifest lists Debian binary/source package versions. When distributing
   the complete image, also retain corresponding Debian source packages for
   any redistributed GPL/LGPL components that are not covered by the System
   Libraries exception, including downstream patches/build scripts as applicable.
   Debian's copyright files identify upstream terms. Source packages can be
   obtained using `apt-get source SOURCE_PACKAGE=SOURCE_VERSION` with matching
   `deb-src` repositories; archive them beside the release. Do not assume that
   an upstream repository or a Debian mirror will retain your exact version.
4. If you replace wheels with custom binaries, modify dependencies, add packages
   or change the base image, update the source/notices bundle to match them.
   Keep applicable license notices and offer the modified corresponding source.

The default source collection includes upstream sdists and the complete MuPDF
release. It is not a claim that arbitrary custom images meet every component's
redistribution obligations. OS package inventory is provided for release review;
system source packages are not downloaded by ordinary local builds.
