use crate::error::{ErrorCode, RunnerError};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs;
use std::path::{Component, Path, PathBuf};

const MAX_MANIFEST_BYTES: usize = 1024 * 1024;
const MAX_ENTRY_BYTES: usize = 16 * 1024 * 1024;
const MAX_IMAGE_BYTES: usize = 64 * 1024 * 1024;

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ImageManifest {
    pub schema: String,
    pub image_format_version: String,
    pub image_version: String,
    pub language_version: String,
    pub skill_version: String,
    pub runtime_contract_version: String,
    pub semantic_source_revision: String,
    pub purpose: String,
    pub release_eligible: bool,
    pub normalization: Normalization,
    pub payload: Vec<PayloadManifestEntry>,
    pub aggregate_sha256: AggregateDigest,
    pub model_visible_bytes: ModelVisibleBytes,
    pub instruction_placements: Vec<InstructionPlacement>,
    pub task_envelope: ExternalArtifact,
    pub one_field_framing: FramingArtifact,
    pub terminal_envelope: ExternalArtifact,
    pub minimum_transport_requirements: MinimumTransportRequirements,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Normalization {
    pub encoding: String,
    pub newlines: String,
    pub byte_order_mark: String,
    pub path_separator: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PayloadManifestEntry {
    pub path: String,
    pub media_type: String,
    pub byte_length: usize,
    pub sha256: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AggregateDigest {
    pub algorithm: String,
    pub sha256: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ModelVisibleBytes {
    pub serialization: String,
    pub byte_length: usize,
    pub sha256: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct InstructionPlacement {
    pub id: String,
    pub strictness: String,
    pub preserves_harness_base_prompt: bool,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ExternalArtifact {
    pub schema_id: String,
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FramingArtifact {
    pub id: String,
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MinimumTransportRequirements {
    pub non_interactive: String,
    pub structured_output: String,
    pub ambient_isolation: String,
    pub terminal_envelope: String,
    pub bounded_streaming: String,
}

#[derive(Debug, Clone)]
pub struct ImageEntry {
    pub path: String,
    pub bytes: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct RuntimeImage {
    pub manifest: ImageManifest,
    pub manifest_bytes: Vec<u8>,
    pub payload: Vec<ImageEntry>,
    pub task_envelope_schema: Vec<u8>,
    pub one_field_framing: Vec<u8>,
    pub terminal_envelope_schema: Vec<u8>,
}

impl RuntimeImage {
    /// Loads and cryptographically verifies an image directory. Payload bodies
    /// remain opaque bytes: validation does not branch on Markdown content.
    ///
    /// # Errors
    ///
    /// Returns `IMAGE_INVALID` or `IMAGE_TOO_LARGE` when any closed manifest,
    /// path, length, normalization, or digest invariant fails.
    pub fn load(root: &Path) -> Result<Self, RunnerError> {
        let root = fs::canonicalize(root).map_err(|error| {
            image_error(format!(
                "cannot resolve image root {}: {error}",
                root.display()
            ))
        })?;
        let manifest_path = root.join("manifest.json");
        let manifest_bytes = read_bounded(&root, &manifest_path, MAX_MANIFEST_BYTES)?;
        let manifest: ImageManifest = serde_json::from_slice(&manifest_bytes).map_err(|error| {
            image_error(format!(
                "invalid image manifest {}: {error}",
                manifest_path.display()
            ))
        })?;
        validate_manifest_metadata(&manifest)?;

        let mut aggregate = Sha256::new();
        let mut total_bytes = 0usize;
        let mut seen = BTreeSet::new();
        let mut payload = Vec::with_capacity(manifest.payload.len());
        for declared in &manifest.payload {
            validate_relative_path(&declared.path, "payload/")?;
            if !seen.insert(declared.path.clone()) {
                return Err(image_error(format!(
                    "duplicate image payload path {:?}",
                    declared.path
                )));
            }
            if declared.byte_length > MAX_ENTRY_BYTES {
                return Err(image_too_large(format!(
                    "image entry {:?} declares {} bytes; maximum is {MAX_ENTRY_BYTES}",
                    declared.path, declared.byte_length
                )));
            }
            let path = root.join(&declared.path);
            let bytes = read_bounded(&root, &path, MAX_ENTRY_BYTES)?;
            total_bytes = total_bytes
                .checked_add(bytes.len())
                .ok_or_else(|| image_too_large("image aggregate length overflow"))?;
            if total_bytes > MAX_IMAGE_BYTES {
                return Err(image_too_large(format!(
                    "image payload exceeds the {MAX_IMAGE_BYTES}-byte aggregate limit"
                )));
            }
            if bytes.len() != declared.byte_length {
                return Err(image_error(format!(
                    "image entry {:?} length mismatch: declared {}, observed {}",
                    declared.path,
                    declared.byte_length,
                    bytes.len()
                )));
            }
            let digest = sha256_hex(&bytes);
            if digest != declared.sha256 {
                return Err(image_error(format!(
                    "image entry {:?} digest mismatch: expected {}, observed {digest}",
                    declared.path, declared.sha256
                )));
            }
            validate_text_bytes(&declared.path, &bytes)?;
            aggregate.update(declared.path.as_bytes());
            aggregate.update([0]);
            aggregate.update(bytes.len().to_string().as_bytes());
            aggregate.update([0]);
            aggregate.update(&bytes);
            aggregate.update([0]);
            payload.push(ImageEntry {
                path: declared.path.clone(),
                bytes,
            });
        }
        let aggregate_digest = digest_to_hex(aggregate.finalize().as_slice());
        if aggregate_digest != manifest.aggregate_sha256.sha256 {
            return Err(image_error(format!(
                "image aggregate digest mismatch: expected {}, observed {aggregate_digest}",
                manifest.aggregate_sha256.sha256
            )));
        }
        validate_model_visible_bytes(&manifest, &payload)?;

        let task_envelope_schema = load_external(
            &root,
            &manifest.task_envelope.path,
            &manifest.task_envelope.sha256,
        )?;
        let one_field_framing = load_external(
            &root,
            &manifest.one_field_framing.path,
            &manifest.one_field_framing.sha256,
        )?;
        let terminal_envelope_schema = load_external(
            &root,
            &manifest.terminal_envelope.path,
            &manifest.terminal_envelope.sha256,
        )?;

        Ok(Self {
            manifest,
            manifest_bytes,
            payload,
            task_envelope_schema,
            one_field_framing,
            terminal_envelope_schema,
        })
    }

    /// Verifies an image compiled into a local development runner. The caller
    /// supplies exact path/byte pairs, which makes replacement of the image a
    /// data-only change rather than an adapter change.
    ///
    /// # Errors
    ///
    /// Returns `IMAGE_INVALID` or `IMAGE_TOO_LARGE` when any closed manifest,
    /// length, normalization, or digest invariant fails.
    pub fn from_embedded(
        manifest_bytes: &[u8],
        files: &[(&str, &[u8])],
    ) -> Result<Self, RunnerError> {
        if manifest_bytes.len() > MAX_MANIFEST_BYTES {
            return Err(image_too_large(
                "embedded image manifest exceeds its size limit",
            ));
        }
        let manifest: ImageManifest = serde_json::from_slice(manifest_bytes)
            .map_err(|error| image_error(format!("invalid embedded image manifest: {error}")))?;
        validate_manifest_metadata(&manifest)?;
        let lookup = |path: &str| {
            files
                .iter()
                .find_map(|(candidate, bytes)| (*candidate == path).then_some(*bytes))
                .ok_or_else(|| image_error(format!("embedded image file {path:?} is missing")))
        };

        let mut aggregate = Sha256::new();
        let mut total_bytes = 0usize;
        let mut seen = BTreeSet::new();
        let mut payload = Vec::with_capacity(manifest.payload.len());
        for declared in &manifest.payload {
            validate_relative_path(&declared.path, "payload/")?;
            if !seen.insert(declared.path.clone()) {
                return Err(image_error(format!(
                    "duplicate image payload path {:?}",
                    declared.path
                )));
            }
            let bytes = lookup(&declared.path)?;
            if bytes.len() > MAX_ENTRY_BYTES {
                return Err(image_too_large(format!(
                    "embedded image entry {:?} exceeds its size limit",
                    declared.path
                )));
            }
            total_bytes = total_bytes
                .checked_add(bytes.len())
                .ok_or_else(|| image_too_large("image aggregate length overflow"))?;
            if total_bytes > MAX_IMAGE_BYTES {
                return Err(image_too_large(
                    "embedded image exceeds its aggregate size limit",
                ));
            }
            if bytes.len() != declared.byte_length {
                return Err(image_error(format!(
                    "image entry {:?} length mismatch: declared {}, observed {}",
                    declared.path,
                    declared.byte_length,
                    bytes.len()
                )));
            }
            let observed = sha256_hex(bytes);
            if observed != declared.sha256 {
                return Err(image_error(format!(
                    "image entry {:?} digest mismatch: expected {}, observed {observed}",
                    declared.path, declared.sha256
                )));
            }
            validate_text_bytes(&declared.path, bytes)?;
            aggregate.update(declared.path.as_bytes());
            aggregate.update([0]);
            aggregate.update(bytes.len().to_string().as_bytes());
            aggregate.update([0]);
            aggregate.update(bytes);
            aggregate.update([0]);
            payload.push(ImageEntry {
                path: declared.path.clone(),
                bytes: bytes.to_vec(),
            });
        }
        let aggregate_digest = digest_to_hex(aggregate.finalize().as_slice());
        if aggregate_digest != manifest.aggregate_sha256.sha256 {
            return Err(image_error(format!(
                "image aggregate digest mismatch: expected {}, observed {aggregate_digest}",
                manifest.aggregate_sha256.sha256
            )));
        }
        validate_model_visible_bytes(&manifest, &payload)?;

        let external = |path: &str, expected: &str| {
            validate_relative_path(path, "contracts/")?;
            let bytes = lookup(path)?;
            let observed = sha256_hex(bytes);
            if observed != expected {
                return Err(image_error(format!(
                    "image artifact {path:?} digest mismatch: expected {expected}, observed {observed}"
                )));
            }
            Ok(bytes.to_vec())
        };
        let task_envelope_schema =
            external(&manifest.task_envelope.path, &manifest.task_envelope.sha256)?;
        let one_field_framing = external(
            &manifest.one_field_framing.path,
            &manifest.one_field_framing.sha256,
        )?;
        let terminal_envelope_schema = external(
            &manifest.terminal_envelope.path,
            &manifest.terminal_envelope.sha256,
        )?;

        Ok(Self {
            manifest,
            manifest_bytes: manifest_bytes.to_vec(),
            payload,
            task_envelope_schema,
            one_field_framing,
            terminal_envelope_schema,
        })
    }

    #[must_use]
    pub fn aggregate_sha256(&self) -> &str {
        &self.manifest.aggregate_sha256.sha256
    }

    /// Returns the exact model-visible image serialization declared by the
    /// manifest: ordered raw concatenation with no runner-owned separator.
    #[must_use]
    pub fn model_visible_bytes(&self) -> Vec<u8> {
        self.payload
            .iter()
            .flat_map(|entry| entry.bytes.iter().copied())
            .collect()
    }
}

fn validate_manifest_metadata(manifest: &ImageManifest) -> Result<(), RunnerError> {
    if manifest.schema != "openprose.skill-runtime-image-manifest/1"
        || manifest.image_format_version != "openprose.skill-runtime-image/1"
    {
        return Err(image_error("unsupported Skill Runtime Image format"));
    }
    if manifest.payload.is_empty() {
        return Err(image_error("image payload must contain at least one entry"));
    }
    if manifest.aggregate_sha256.algorithm != "sha256-path-length-nul-v1" {
        return Err(image_error(format!(
            "unsupported aggregate algorithm {:?}",
            manifest.aggregate_sha256.algorithm
        )));
    }
    if manifest.model_visible_bytes.serialization != "ordered-raw-concatenation-v1" {
        return Err(image_error("unsupported model-visible image serialization"));
    }
    if manifest.normalization.encoding != "utf-8"
        || manifest.normalization.newlines != "lf"
        || manifest.normalization.byte_order_mark != "forbidden"
        || manifest.normalization.path_separator != "/"
    {
        return Err(image_error("unsupported image normalization profile"));
    }
    for (name, value) in [
        (
            "nonInteractive",
            &manifest.minimum_transport_requirements.non_interactive,
        ),
        (
            "structuredOutput",
            &manifest.minimum_transport_requirements.structured_output,
        ),
        (
            "ambientIsolation",
            &manifest.minimum_transport_requirements.ambient_isolation,
        ),
        (
            "terminalEnvelope",
            &manifest.minimum_transport_requirements.terminal_envelope,
        ),
        (
            "boundedStreaming",
            &manifest.minimum_transport_requirements.bounded_streaming,
        ),
    ] {
        if value != "required" {
            return Err(image_error(format!(
                "minimum transport requirement {name} must be `required`"
            )));
        }
    }
    if manifest.instruction_placements.is_empty() {
        return Err(image_error("image declares no instruction placements"));
    }
    Ok(())
}

fn validate_model_visible_bytes(
    manifest: &ImageManifest,
    payload: &[ImageEntry],
) -> Result<(), RunnerError> {
    let bytes: Vec<u8> = payload
        .iter()
        .flat_map(|entry| entry.bytes.iter().copied())
        .collect();
    if bytes.len() != manifest.model_visible_bytes.byte_length {
        return Err(image_error(format!(
            "model-visible image length mismatch: expected {}, observed {}",
            manifest.model_visible_bytes.byte_length,
            bytes.len()
        )));
    }
    let digest = sha256_hex(&bytes);
    if digest != manifest.model_visible_bytes.sha256 {
        return Err(image_error(format!(
            "model-visible image digest mismatch: expected {}, observed {digest}",
            manifest.model_visible_bytes.sha256
        )));
    }
    Ok(())
}

fn load_external(root: &Path, relative: &str, expected: &str) -> Result<Vec<u8>, RunnerError> {
    validate_relative_path(relative, "contracts/")?;
    let bytes = read_bounded(root, &root.join(relative), MAX_ENTRY_BYTES)?;
    let observed = sha256_hex(&bytes);
    if observed != expected {
        return Err(image_error(format!(
            "image artifact {relative:?} digest mismatch: expected {expected}, observed {observed}"
        )));
    }
    Ok(bytes)
}

fn read_bounded(root: &Path, path: &Path, maximum: usize) -> Result<Vec<u8>, RunnerError> {
    let canonical = fs::canonicalize(path).map_err(|error| {
        image_error(format!(
            "cannot resolve image file {}: {error}",
            path.display()
        ))
    })?;
    if !canonical.starts_with(root) {
        return Err(image_error(format!(
            "image path {} escapes its image root",
            path.display()
        )));
    }
    let metadata = fs::metadata(&canonical).map_err(|error| {
        image_error(format!(
            "cannot inspect image file {}: {error}",
            path.display()
        ))
    })?;
    let length = usize::try_from(metadata.len()).unwrap_or(usize::MAX);
    if length > maximum {
        return Err(image_too_large(format!(
            "image file {} is {length} bytes; maximum is {maximum}",
            path.display()
        )));
    }
    fs::read(&canonical).map_err(|error| {
        image_error(format!(
            "cannot read image file {}: {error}",
            path.display()
        ))
    })
}

fn validate_relative_path(path: &str, prefix: &str) -> Result<(), RunnerError> {
    if !path.starts_with(prefix) || path.contains('\\') {
        return Err(image_error(format!("invalid image-relative path {path:?}")));
    }
    let parsed = PathBuf::from(path);
    if parsed.is_absolute()
        || parsed
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(image_error(format!("invalid image-relative path {path:?}")));
    }
    Ok(())
}

fn validate_text_bytes(path: &str, bytes: &[u8]) -> Result<(), RunnerError> {
    if bytes.starts_with(&[0xef, 0xbb, 0xbf]) {
        return Err(image_error(format!(
            "image entry {path:?} contains a byte-order mark"
        )));
    }
    std::str::from_utf8(bytes)
        .map_err(|error| image_error(format!("image entry {path:?} is not UTF-8: {error}")))?;
    if bytes.windows(2).any(|pair| pair == b"\r\n") || bytes.contains(&b'\r') {
        return Err(image_error(format!(
            "image entry {path:?} does not use LF newlines"
        )));
    }
    Ok(())
}

#[must_use]
pub fn sha256_hex(bytes: &[u8]) -> String {
    digest_to_hex(Sha256::digest(bytes).as_slice())
}

fn digest_to_hex(bytes: &[u8]) -> String {
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        let _ = write!(&mut result, "{byte:02x}");
    }
    result
}

fn image_error(message: impl Into<String>) -> RunnerError {
    RunnerError::catalog(ErrorCode::ImageInvalid).with_detail("reason", message.into())
}

fn image_too_large(message: impl Into<String>) -> RunnerError {
    RunnerError::catalog(ErrorCode::ImageTooLarge).with_detail("reason", message.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    fn shared_sentinel() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../shared/image/sentinel-v1")
    }

    #[test]
    fn verifies_shared_sentinel_without_interpreting_payload() {
        let image = RuntimeImage::load(&shared_sentinel()).unwrap();
        assert_eq!(image.manifest.image_version, "sentinel-v1");
        assert!(!image.manifest.release_eligible);
        assert_eq!(
            image.aggregate_sha256(),
            "ee13d1cbba24d1623523f4fe8747b4a3387cd6880f5d6fb4a360d2a7949ddf00"
        );
        assert_eq!(image.payload.len(), 2);
        assert_eq!(image.model_visible_bytes().len(), 346);
        assert_eq!(
            sha256_hex(&image.model_visible_bytes()),
            "893b6aa34556ce0a1b647caff68605e2801035a0e9f28bb3a12a948a5d3c3486"
        );
    }

    #[test]
    fn tampered_payload_fails_closed() {
        let source = shared_sentinel();
        let temp = TempDir::new().unwrap();
        copy_directory(&source, temp.path());
        fs::write(temp.path().join("payload/00-sentinel.md"), b"tampered\n").unwrap();
        let error = RuntimeImage::load(temp.path()).unwrap_err();
        assert_eq!(error.code, ErrorCode::ImageInvalid);
        let reason = error.details.unwrap()["reason"]
            .as_str()
            .unwrap()
            .to_owned();
        assert!(reason.contains("length mismatch") || reason.contains("digest mismatch"));
    }

    fn copy_directory(source: &Path, destination: &Path) {
        fs::create_dir_all(destination.join("payload")).unwrap();
        fs::create_dir_all(destination.join("contracts")).unwrap();
        for relative in [
            "manifest.json",
            "payload/00-sentinel.md",
            "payload/10-byte-canary.md",
            "contracts/task-envelope.schema.json",
            "contracts/one-field-framing.txt",
            "contracts/terminal-envelope.schema.json",
        ] {
            fs::copy(source.join(relative), destination.join(relative)).unwrap();
        }
    }
}
