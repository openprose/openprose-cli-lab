# Reviewed signed CLI publication plans

No qualified plan exists yet. Do not use a test fixture to trigger publishing.
The main-branch plan is a reviewed inventory, not an automatic qualification
claim. `cli/ci/publication.py` validates its exact fields and every downloaded
byte. It requires the existing protected release preflight, kernel qualification,
all four platforms for both implementations, the five existing npm package
names, and notarization records binding the final macOS binaries.

See [publication setup](../../../docs/cli-publication.md). Adding a plan requires
review of its source, immutable evidence and signed final artifacts. Historical
echo-image or sentinel packages do not qualify. Existing alpha authorities have
not been replaced or declared satisfied by this directory.
