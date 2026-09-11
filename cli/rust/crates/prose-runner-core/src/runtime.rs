use chrono::{SecondsFormat, Utc};
use uuid::Uuid;

/// Observable wall time. Tests inject a fixed implementation.
pub trait Clock: Send + Sync {
    fn now_rfc3339(&self) -> String;
}

#[derive(Debug, Clone, Copy, Default)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now_rfc3339(&self) -> String {
        Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true)
    }
}

/// Observable invocation IDs. Tests inject a deterministic implementation.
pub trait IdSource: Send + Sync {
    fn next_invocation_id(&self) -> String;
}

#[derive(Debug, Clone, Copy, Default)]
pub struct SystemIdSource;

impl IdSource for SystemIdSource {
    fn next_invocation_id(&self) -> String {
        Uuid::now_v7().to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn production_ids_are_uuid_v7() {
        let value = SystemIdSource.next_invocation_id();
        let parsed = Uuid::parse_str(&value).unwrap();
        assert_eq!(parsed.get_version_num(), 7);
    }

    #[test]
    fn production_clock_is_utc_rfc3339() {
        let value = SystemClock.now_rfc3339();
        assert!(value.ends_with('Z'));
        assert!(chrono::DateTime::parse_from_rfc3339(&value).is_ok());
    }
}
