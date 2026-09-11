pub fn validate_semver(version: &str) -> Result<(), &'static str> {
    if !version.is_ascii() {
        return Err("only ASCII SemVer identifiers are allowed");
    }
    let (without_build, build) = split_optional(version, '+', true)?;
    if let Some(build) = build {
        validate_identifiers(build, false)?;
    }
    let (core, prerelease) = split_optional(without_build, '-', false)?;
    if let Some(prerelease) = prerelease {
        validate_identifiers(prerelease, true)?;
    }

    let mut core_parts = core.split('.');
    for _ in 0..3 {
        validate_numeric(
            core_parts
                .next()
                .ok_or("core must contain major.minor.patch")?,
        )?;
    }
    if core_parts.next().is_some() {
        return Err("core must contain exactly major.minor.patch");
    }
    Ok(())
}

fn split_optional(
    value: &str,
    separator: char,
    require_unique_separator: bool,
) -> Result<(&str, Option<&str>), &'static str> {
    let Some((left, right)) = value.split_once(separator) else {
        return Ok((value, None));
    };
    if left.is_empty()
        || right.is_empty()
        || (require_unique_separator && right.contains(separator))
    {
        return Err("metadata separators and components must be unambiguous and non-empty");
    }
    Ok((left, Some(right)))
}

fn validate_numeric(identifier: &str) -> Result<(), &'static str> {
    if identifier.is_empty() || !identifier.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err("core identifiers must contain only decimal digits");
    }
    if identifier.len() > 1 && identifier.starts_with('0') {
        return Err("numeric identifiers must not contain leading zeroes");
    }
    Ok(())
}

fn validate_identifiers(
    value: &str,
    reject_numeric_leading_zero: bool,
) -> Result<(), &'static str> {
    for identifier in value.split('.') {
        if identifier.is_empty()
            || !identifier
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        {
            return Err("metadata identifiers must use ASCII alphanumerics or hyphen");
        }
        if reject_numeric_leading_zero
            && identifier.bytes().all(|byte| byte.is_ascii_digit())
            && identifier.len() > 1
            && identifier.starts_with('0')
        {
            return Err("numeric prerelease identifiers must not contain leading zeroes");
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::validate_semver;

    #[test]
    fn accepts_semver_two_versions_used_by_release_workflows() {
        for version in [
            "0.1.0",
            "0.1.0-alpha.1",
            "1.2.3-rc.0+build.7",
            "1.2.3-alpha-beta+build-sha",
            "10.20.30+20260828.sha-abcdef",
        ] {
            assert_eq!(validate_semver(version), Ok(()), "{version}");
        }
    }

    #[test]
    fn rejects_ambiguous_or_non_semver_versions() {
        for version in [
            "",
            "v0.1.0",
            "0.1",
            "0.1.0.1",
            "01.0.0",
            "0.01.0",
            "0.1.00",
            "0.1.0-",
            "0.1.0-alpha..1",
            "0.1.0-01",
            "0.1.0+",
            "0.1.0+build+again",
            "0.1.0 alpha",
            "0.1.0-α",
        ] {
            assert!(validate_semver(version).is_err(), "{version}");
        }
    }
}
