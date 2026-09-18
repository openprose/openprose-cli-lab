use serde::Deserialize;
use std::io::{Read, Write};
use weave_file_binding_experiment::{Config, FileBinding};
#[derive(Deserialize)]
struct Request { #[serde(flatten)] config: Config, #[serde(default, deserialize_with = "optional_now")] now: Option<u64> }
fn optional_now<'de,D:serde::Deserializer<'de>>(d:D)->Result<Option<u64>,D::Error> {
    let n=f64::deserialize(d)?;
    if !n.is_finite() || n < 0.0 || n > weave_file_binding_experiment::MAX_SAFE_INTEGER as f64 || n.fract()!=0.0 { return Err(serde::de::Error::custom("invalid time")); }
    Ok(Some(n as u64))
}
fn run() -> Result<(), String> {
    let mut input = Vec::new();
    std::io::stdin().take(1_048_577).read_to_end(&mut input).map_err(|e| e.to_string())?;
    if input.len() > 1_048_576 { return Err("observer request exceeds 1 MiB".into()); }
    let request: Request = serde_json::from_slice(&input).map_err(|e| e.to_string())?;
    let binding = FileBinding::new(request.config)?;
    let now = match request.now { Some(n) => n, None => std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|e| e.to_string())?.as_millis().try_into().map_err(|_| "invalid time")? };
    let evidence = binding.observe(now)?;
    let output = serde_json::json!({"binding": binding.binding(), "evidence": evidence});
    writeln!(std::io::stdout(), "{output}").map_err(|e| e.to_string())
}
fn main() { if let Err(error) = run() { eprintln!("{error}"); std::process::exit(1); } }
