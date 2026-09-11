# Decision 0005: model-visible Skill Runtime Image bytes

Status: accepted for image format v1

When one harness instruction channel must carry multiple image payload entries,
the delivered image is every verified payload body concatenated byte-for-byte
in manifest order, with no inserted separator or metadata. The manifest names
this `ordered-raw-concatenation-v1` and declares the resulting byte length and
SHA-256.

The aggregate image digest remains distinct: it binds paths, lengths, entry
boundaries, and bodies for package integrity. The model-visible digest binds
only the bytes delivered to the harness. Result evidence may therefore report
both without conflating package identity with prompt content.

This decision fixes a shared-oracle defect caught before product admission:
early adapter scenarios delivered only the first of two sentinel entries. A
single generator now derives wire and prompt-file metadata from the manifest,
ordered payloads, language-owned framing fixture, and task fixture.
