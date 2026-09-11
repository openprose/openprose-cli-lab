#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowsHostBuildIdentity {
    pub admitted: bool,
    pub sha256: Option<String>,
}

impl WindowsHostBuildIdentity {
    /// Resolves the two public build inputs without silently weakening malformed values.
    ///
    /// # Errors
    ///
    /// Returns a stable explanation for malformed admission/digest values or
    /// an admitted build without an integrity digest.
    pub fn resolve(admission: Option<&str>, sha256: Option<&str>) -> Result<Self, &'static str> {
        let admission = admission.filter(|value| !value.is_empty());
        let sha256 = sha256.filter(|value| !value.is_empty());
        let admitted = match admission {
            None | Some("0") => false,
            Some("1") => true,
            Some(_) => return Err("OPENPROSE_WINDOWS_HOST_ADMISSION must be exactly 0 or 1"),
        };
        if sha256.is_some_and(|value| {
            value.len() != 64
                || !value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        }) {
            return Err("OPENPROSE_WINDOWS_HOST_SHA256 must be exactly 64 lowercase hex bytes");
        }
        if admitted && sha256.is_none() {
            return Err("admitted Windows host builds require OPENPROSE_WINDOWS_HOST_SHA256");
        }
        Ok(Self {
            admitted,
            sha256: sha256.map(ToOwned::to_owned),
        })
    }
}
