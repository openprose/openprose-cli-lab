use std::collections::{BTreeMap, BTreeSet};
use std::ffi::{OsStr, OsString};
use std::fmt::{self, Debug, Formatter};

/// Whether a value may appear in sanitized diagnostics.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sensitivity {
    Public,
    Secret,
    /// Exact adapter-owned setting which may enter the child but is neither a
    /// credential nor a literal that human assistant output must suppress.
    Control,
}

#[derive(Clone)]
struct EnvironmentValue {
    value: OsString,
    sensitivity: Sensitivity,
}

/// A closed child environment assembled from an adapter-specific allowlist.
///
/// Ambient values are retained only so recursion can be detected before
/// spawning. They are not inherited unless the adapter explicitly allows the
/// name.
#[derive(Clone, Default)]
pub struct EnvironmentPolicy {
    ambient: BTreeMap<OsString, OsString>,
    allowed: BTreeMap<OsString, EnvironmentValue>,
    missing_allowed: BTreeSet<OsString>,
}

impl Debug for EnvironmentPolicy {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EnvironmentPolicy")
            .field("ambient_names", &self.ambient.keys().collect::<Vec<_>>())
            .field("allowed_names", &self.allowed.keys().collect::<Vec<_>>())
            .field("missing_allowed", &self.missing_allowed)
            .finish_non_exhaustive()
    }
}

impl EnvironmentPolicy {
    #[must_use]
    pub fn from_current() -> Self {
        Self::from_pairs(std::env::vars_os())
    }

    #[must_use]
    pub fn from_pairs(
        values: impl IntoIterator<Item = (impl Into<OsString>, impl Into<OsString>)>,
    ) -> Self {
        Self {
            ambient: values
                .into_iter()
                .map(|(name, value)| (name.into(), value.into()))
                .collect(),
            allowed: BTreeMap::new(),
            missing_allowed: BTreeSet::new(),
        }
    }

    #[must_use]
    pub fn allow_inherited(mut self, name: impl Into<OsString>, sensitivity: Sensitivity) -> Self {
        let name = name.into();
        if let Some(value) = self.ambient.get(&name).cloned() {
            self.allowed
                .insert(name, EnvironmentValue { value, sensitivity });
        } else {
            self.missing_allowed.insert(name);
        }
        self
    }

    #[must_use]
    pub fn set(
        mut self,
        name: impl Into<OsString>,
        value: impl Into<OsString>,
        sensitivity: Sensitivity,
    ) -> Self {
        let name = name.into();
        self.missing_allowed.remove(&name);
        self.allowed.insert(
            name,
            EnvironmentValue {
                value: value.into(),
                sensitivity,
            },
        );
        self
    }

    pub(crate) fn ambient_contains(&self, name: &str) -> bool {
        self.ambient.contains_key(OsStr::new(name))
    }

    pub(crate) fn entries(&self) -> impl Iterator<Item = (&OsStr, &OsStr)> {
        self.allowed
            .iter()
            .map(|(name, value)| (name.as_os_str(), value.value.as_os_str()))
    }

    /// Returns the exact non-empty values classified as credentials or other
    /// child-only secrets so higher layers can sanitize every public surface.
    #[must_use]
    pub fn secret_strings(&self) -> Vec<String> {
        let mut values = self
            .allowed
            .values()
            .filter(|value| value.sensitivity == Sensitivity::Secret)
            .filter_map(|value| value.value.to_str())
            .filter(|value| !value.is_empty())
            .map(ToOwned::to_owned)
            .collect::<Vec<_>>();
        values.sort_unstable_by(|left, right| {
            right.len().cmp(&left.len()).then_with(|| left.cmp(right))
        });
        values.dedup();
        values
    }

    /// Returns every non-empty value admitted to the child environment.
    ///
    /// Human output streaming uses this closed copy only as a suppression
    /// list. Keeping the names out of the result prevents callers from
    /// accidentally reconstructing or logging the child environment.
    #[must_use]
    pub fn output_protected_strings(&self) -> Vec<String> {
        let mut values = self
            .allowed
            .values()
            .filter(|value| value.sensitivity != Sensitivity::Control)
            .filter_map(|value| value.value.to_str())
            .filter(|value| !value.is_empty())
            .map(ToOwned::to_owned)
            .collect::<Vec<_>>();
        values.sort_unstable_by(|left, right| {
            right.len().cmp(&left.len()).then_with(|| left.cmp(right))
        });
        values.dedup();
        values
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_allowlisted_values_are_materialized_and_debug_hides_values() {
        let policy = EnvironmentPolicy::from_pairs([
            ("PATH", "/fixture/bin"),
            ("OPENAI_API_KEY", "very-secret"),
            ("UNRELATED_SECRET", "must-not-pass"),
        ])
        .allow_inherited("PATH", Sensitivity::Public)
        .allow_inherited("OPENAI_API_KEY", Sensitivity::Secret)
        .set("PRIME_AGENT_TELEMETRY", "0", Sensitivity::Control);
        let entries: BTreeMap<_, _> = policy
            .entries()
            .map(|(name, value)| (name.to_owned(), value.to_owned()))
            .collect();
        assert_eq!(entries.len(), 3);
        assert!(!entries.contains_key(OsStr::new("UNRELATED_SECRET")));
        assert_eq!(policy.secret_strings(), ["very-secret"]);
        assert_eq!(
            policy.output_protected_strings(),
            ["/fixture/bin", "very-secret"]
        );
        assert_eq!(entries[OsStr::new("PRIME_AGENT_TELEMETRY")], "0");
        let debug = format!("{policy:?}");
        assert!(!debug.contains("very-secret"));
        assert!(!debug.contains("must-not-pass"));
    }

    #[test]
    fn protected_values_are_deduplicated_and_longest_first() {
        let policy = EnvironmentPolicy::from_pairs([
            ("SHORT_SECRET", "/fixture/store"),
            ("LONG_SECRET", "/fixture/store/credentials"),
            ("DUPLICATE_SECRET", "/fixture/store/credentials"),
            ("PUBLIC_PATH", "/fixture/store/cache"),
        ])
        .allow_inherited("SHORT_SECRET", Sensitivity::Secret)
        .allow_inherited("LONG_SECRET", Sensitivity::Secret)
        .allow_inherited("DUPLICATE_SECRET", Sensitivity::Secret)
        .allow_inherited("PUBLIC_PATH", Sensitivity::Public);
        assert_eq!(
            policy.secret_strings(),
            ["/fixture/store/credentials", "/fixture/store"]
        );
        assert_eq!(
            policy.output_protected_strings(),
            [
                "/fixture/store/credentials",
                "/fixture/store/cache",
                "/fixture/store"
            ]
        );
    }
}
