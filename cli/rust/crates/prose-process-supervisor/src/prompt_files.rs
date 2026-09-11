use std::fs::OpenOptions;
use std::io;
use std::io::Write as _;
use std::path::Path;
use std::path::PathBuf;
use tempfile::TempDir;

/// Exact OMP 18.0.9 highest-precedence retry and discovery control overlay.
pub const OMP_CONTROL_OVERLAY_BYTES: &[u8] = b"retry:\n  enabled: false\ndisabledProviders:\n  - native\n  - omp-plugins\n  - claude\n  - agent-plugins\n  - claude-plugins\n  - codex\n  - gemini\n  - opencode\n  - cursor\n  - windsurf\n  - vscode\n  - mcp-json\n";

#[derive(Debug)]
pub enum PrivatePromptFilesCreateError {
    Creation(io::Error),
    Cleanup(io::Error),
}

impl PrivatePromptFilesCreateError {
    #[must_use]
    pub const fn cleanup_failed(&self) -> bool {
        matches!(self, Self::Cleanup(_))
    }
}

impl std::fmt::Display for PrivatePromptFilesCreateError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Creation(_) => formatter.write_str("private transport file creation failed"),
            Self::Cleanup(_) => formatter.write_str("private transport cleanup failed"),
        }
    }
}

impl std::error::Error for PrivatePromptFilesCreateError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(match self {
            Self::Creation(error) | Self::Cleanup(error) => error,
        })
    }
}

/// Private, short-lived prompt inputs for adapters that require file paths.
#[derive(Debug)]
pub struct PrivatePromptFiles {
    directory: Option<TempDir>,
    directory_path: PathBuf,
    image: PathBuf,
    task: PathBuf,
}

impl PrivatePromptFiles {
    /// Writes both opaque inputs before spawn. The directory is private and,
    /// on Unix, each file is created with mode 0600.
    ///
    /// # Errors
    ///
    /// Returns the first private-directory, file creation, write, permission,
    /// or synchronization error.
    pub fn create(image: &[u8], task: &[u8]) -> Result<Self, PrivatePromptFilesCreateError> {
        Self::create_with_prefix("openprose-prompt-", image, task)
    }

    /// Writes private Prime transport inputs under the common recovery-aware
    /// directory prefix shared by both CLI implementations.
    ///
    /// # Errors
    ///
    /// Returns the first private-directory, file creation, write, permission,
    /// or synchronization error.
    pub fn create_prime(image: &[u8], task: &[u8]) -> Result<Self, PrivatePromptFilesCreateError> {
        Self::create_with_prefix("openprose-prime-", image, task)
    }

    fn create_with_prefix(
        prefix: &str,
        image: &[u8],
        task: &[u8],
    ) -> Result<Self, PrivatePromptFilesCreateError> {
        Self::create_with_prefix_and_hooks(prefix, image, task, |_| Ok(()), TempDir::close)
    }

    fn create_with_prefix_and_hooks(
        prefix: &str,
        image: &[u8],
        task: &[u8],
        after_directory_created: impl FnOnce(&Path) -> io::Result<()>,
        cleanup: impl FnOnce(TempDir) -> io::Result<()>,
    ) -> Result<Self, PrivatePromptFilesCreateError> {
        let directory = tempfile::Builder::new()
            .prefix(prefix)
            .tempdir()
            .map_err(PrivatePromptFilesCreateError::Creation)?;
        let image_path = directory.path().join("language-image.bin");
        let task_path = directory.path().join("task-envelope.json");
        let setup = after_directory_created(directory.path())
            .and_then(|()| set_private_directory_permissions(directory.path()))
            .and_then(|()| write_private(&image_path, image))
            .and_then(|()| write_private(&task_path, task));
        if let Err(error) = setup {
            return match cleanup(directory) {
                Ok(()) => Err(PrivatePromptFilesCreateError::Creation(error)),
                Err(cleanup_error) => Err(PrivatePromptFilesCreateError::Cleanup(cleanup_error)),
            };
        }
        Ok(Self {
            directory_path: directory.path().to_owned(),
            directory: Some(directory),
            image: image_path,
            task: task_path,
        })
    }

    #[must_use]
    pub fn image_path(&self) -> &Path {
        &self.image
    }

    #[must_use]
    pub fn task_path(&self) -> &Path {
        &self.task
    }

    #[must_use]
    pub fn directory(&self) -> &Path {
        &self.directory_path
    }

    /// Creates the fixed private credential-store subdirectory whose lifetime
    /// is owned by this guard.
    ///
    /// # Errors
    ///
    /// Returns a directory creation or permission error.
    pub fn create_credential_config_directory(&self) -> io::Result<PathBuf> {
        let directory = self.directory_path.join("credential-config");
        std::fs::create_dir(&directory)?;
        set_private_directory_permissions(&directory)?;
        Ok(directory)
    }

    /// Writes the exact wrapper-owned OMP control overlay into this guard.
    ///
    /// # Errors
    ///
    /// Returns a file creation, write, permission, or synchronization error.
    pub fn create_omp_control_overlay(&self) -> io::Result<PathBuf> {
        let path = self.directory_path.join("omp-control-overlay.yml");
        write_private(&path, OMP_CONTROL_OVERLAY_BYTES)?;
        Ok(path)
    }

    /// Prevents automatic removal when an owned service could not be settled.
    /// The caller deliberately leaves the private directory for diagnosis.
    pub fn preserve_directory(&mut self) {
        if let Some(directory) = self.directory.take() {
            let _ = directory.keep();
        }
    }

    /// Explicitly removes the complete private-file tree and verifies that its
    /// root no longer exists. Callers must settle this result before emitting a
    /// terminal runner result; `TempDir`'s best-effort `Drop` is not cleanup
    /// authority.
    ///
    /// # Errors
    ///
    /// Returns the removal error, or `AlreadyExists` when the owned root is
    /// still present after a nominally successful removal.
    pub fn close(&mut self) -> io::Result<()> {
        if let Some(directory) = self.directory.take() {
            directory.close()?;
        }
        match std::fs::symlink_metadata(&self.directory_path) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Ok(_) => Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "private transport directory remains after cleanup",
            )),
            Err(error) => Err(error),
        }
    }
}

fn write_private(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt as _;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}

fn set_private_directory_permissions(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn files_are_exact_and_removed_with_the_guard() {
        let directory;
        {
            let files = PrivatePromptFiles::create(b"image\0bytes", b"{\"task\":true}\n").unwrap();
            directory = files.directory().to_owned();
            assert_eq!(std::fs::read(files.image_path()).unwrap(), b"image\0bytes");
            assert_eq!(
                std::fs::read(files.task_path()).unwrap(),
                b"{\"task\":true}\n"
            );
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt as _;
                assert_eq!(
                    std::fs::metadata(files.image_path())
                        .unwrap()
                        .permissions()
                        .mode()
                        & 0o777,
                    0o600
                );
            }
        }
        assert!(!directory.exists());
    }

    #[test]
    fn failed_owned_service_settlement_can_preserve_the_private_directory() {
        let mut files = PrivatePromptFiles::create(b"image", b"task").unwrap();
        let directory = files.directory().to_owned();
        files.preserve_directory();
        drop(files);
        assert!(directory.exists());
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn credential_config_directory_is_private_and_guard_owned() {
        let directory;
        {
            let files = PrivatePromptFiles::create(b"image", b"task").unwrap();
            directory = files.create_credential_config_directory().unwrap();
            assert_eq!(directory.parent(), Some(files.directory()));
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt as _;
                assert_eq!(
                    std::fs::metadata(&directory).unwrap().permissions().mode() & 0o777,
                    0o700
                );
            }
        }
        assert!(!directory.exists());
    }

    #[test]
    fn omp_control_overlay_is_exact_private_and_guard_owned() {
        let directory;
        let overlay;
        {
            let files = PrivatePromptFiles::create(b"image", b"task").unwrap();
            directory = files.directory().to_owned();
            overlay = files.create_omp_control_overlay().unwrap();
            assert_eq!(overlay.parent(), Some(files.directory()));
            assert_eq!(std::fs::read(&overlay).unwrap(), OMP_CONTROL_OVERLAY_BYTES);
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt as _;
                assert_eq!(
                    std::fs::metadata(&overlay).unwrap().permissions().mode() & 0o777,
                    0o600
                );
            }
        }
        assert!(!overlay.exists());
        assert!(!directory.exists());
    }

    #[test]
    fn explicit_close_removes_and_verifies_the_complete_private_tree() {
        let mut files = PrivatePromptFiles::create(b"image", b"task").unwrap();
        let root = files.directory().to_owned();
        let credential = files.create_credential_config_directory().unwrap();
        std::fs::write(credential.join("credential.json"), b"secret").unwrap();
        files.create_omp_control_overlay().unwrap();

        files.close().unwrap();
        assert!(!root.exists());
        files.close().unwrap();
    }

    #[test]
    fn partial_creation_explicitly_settles_or_reports_cleanup_as_dominant() {
        use std::sync::{Arc, Mutex};

        let removed_path = Arc::new(Mutex::new(None));
        let observed = Arc::clone(&removed_path);
        let error = PrivatePromptFiles::create_with_prefix_and_hooks(
            "openprose-partial-",
            b"image",
            b"task",
            move |path| {
                *observed.lock().unwrap() = Some(path.to_owned());
                Err(io::Error::other("injected creation failure"))
            },
            TempDir::close,
        )
        .unwrap_err();
        assert!(matches!(error, PrivatePromptFilesCreateError::Creation(_)));
        assert!(!removed_path.lock().unwrap().as_ref().unwrap().exists());

        let retained_path = Arc::new(Mutex::new(None));
        let observed = Arc::clone(&retained_path);
        let error = PrivatePromptFiles::create_with_prefix_and_hooks(
            "openprose-partial-",
            b"image",
            b"task",
            |_| Err(io::Error::other("injected creation failure")),
            move |directory| {
                let path = directory.keep();
                *observed.lock().unwrap() = Some(path);
                Err(io::Error::other("injected cleanup failure"))
            },
        )
        .unwrap_err();
        assert!(error.cleanup_failed());
        let retained = retained_path.lock().unwrap().take().unwrap();
        assert!(retained.exists());
        std::fs::remove_dir_all(retained).unwrap();
    }
}
