use std::{fs, path::PathBuf, sync::atomic::{AtomicU64, Ordering}};
use weave_file_binding_experiment::{Config, FileBinding, MAX_SAFE_INTEGER};
static ID: AtomicU64 = AtomicU64::new(0);
struct Temp(PathBuf);
impl Temp {
    fn new() -> Self { let p=std::env::temp_dir().join(format!("weave-binding-{}-{}",std::process::id(), ID.fetch_add(1,Ordering::Relaxed))); fs::create_dir(&p).unwrap(); Self(p) }
    fn config(&self)->Config { Config { root:self.0.to_str().unwrap().into(),kernel:"k".into(), contracts:vec!["c".into()],evidence:vec!["e".into()],policy:"v1".into(),ttl_ms:60,limit:1000 } }
    fn seed(&self) { for name in ["k","c","e"] { fs::write(self.0.join(name),name).unwrap(); } }
}
impl Drop for Temp { fn drop(&mut self){ let _=fs::remove_dir_all(&self.0); } }
#[test] fn fixtures_and_repeated_observation() {
    let t=Temp::new(); let fixture:serde_json::Value=serde_json::from_str(include_str!("../fixture-v1.json")).unwrap();
    for (name,content) in fixture["files"].as_object().unwrap() {fs::write(t.0.join(name),content.as_str().unwrap()).unwrap();}
    for row in fixture["cases"].as_array().unwrap() {
        let mut value=row.clone();value["root"]=t.0.to_str().unwrap().into();
        let binding=FileBinding::new(serde_json::from_value(value).unwrap()).unwrap();let now=row["now"].as_u64().unwrap();
        let a=binding.observe(now).unwrap();assert_eq!(a.gap,row["gap"].as_bool().unwrap(),"{}",row["name"]);
        assert_eq!(a,binding.observe(now).unwrap());assert_eq!(a.identity.len(),64);
    }
}
#[test] fn content_changes_identity_not_binding_and_missing_restores() {
    let t=Temp::new();t.seed(); let b=FileBinding::new(t.config()).unwrap();let first=b.observe(0).unwrap();let id=b.binding().to_owned();
    fs::write(t.0.join("e"),"changed").unwrap();assert_ne!(first.identity,b.observe(0).unwrap().identity);assert_eq!(id,b.binding());
    fs::remove_file(t.0.join("e")).unwrap();assert!(b.observe(0).unwrap().gap);t.seed();assert_eq!(first,b.observe(0).unwrap());
}
#[test] fn aggregate_exact_and_growth_boundary() {
    let t=Temp::new();t.seed();let mut c=t.config();c.limit=3;let b=FileBinding::new(c).unwrap();assert!(!b.observe(0).unwrap().gap);
    fs::write(t.0.join("e"),"ee").unwrap();assert!(b.observe(0).unwrap().gap);
}
#[test] fn invalid_utf8_and_bom_hash() {
    let t=Temp::new();t.seed();let b=FileBinding::new(t.config()).unwrap();fs::write(t.0.join("e"),[0xff]).unwrap();assert!(b.observe(0).unwrap().gap);
    fs::write(t.0.join("e"),"\u{feff}x").unwrap();let with=b.observe(0).unwrap();let p:serde_json::Value=serde_json::from_str(&with.payload).unwrap();assert_eq!(p["files"][2]["content"],"x");
    fs::write(t.0.join("e"),"x").unwrap();assert_ne!(with.identity,b.observe(0).unwrap().identity);
}
#[test] fn containment_symlinks_hardlinks_and_nonfiles() {
    use std::os::unix::fs::symlink;
    let t=Temp::new();let outside=Temp::new();t.seed();outside.seed();
    symlink(outside.0.join("e"),t.0.join("escape")).unwrap();let mut c=t.config();c.evidence=vec!["escape".into()];assert!(FileBinding::new(c).unwrap().observe(0).unwrap().gap);
    symlink(t.0.join("e"),t.0.join("inside")).unwrap();let mut c=t.config();c.evidence=vec!["inside".into()];assert!(!FileBinding::new(c).unwrap().observe(0).unwrap().gap);
    fs::hard_link(outside.0.join("e"),t.0.join("hardlink")).unwrap();let mut c=t.config();c.evidence=vec!["hardlink".into()];assert!(!FileBinding::new(c).unwrap().observe(0).unwrap().gap);
    let mut c=t.config();c.evidence=vec![".".into()];assert!(FileBinding::new(c).unwrap().observe(0).unwrap().gap);
    let path=std::ffi::CString::new(t.0.join("fifo").to_str().unwrap()).unwrap();assert_eq!(unsafe {libc::mkfifo(path.as_ptr(),0o600)},0);
    let mut c=t.config();c.evidence=vec!["fifo".into()];assert!(FileBinding::new(c).unwrap().observe(0).unwrap().gap);
}
#[test] fn declarations_bounds_and_safe_times() {
    let t=Temp::new();t.seed();let mut c=t.config();c.policy="\u{feff}\u{2000}".into();assert!(FileBinding::new(c).is_err());
    let mut c=t.config();c.policy="\u{0085}".into();assert!(FileBinding::new(c).is_ok());
    for n in [0,MAX_SAFE_INTEGER+1] {let mut c=t.config();c.limit=n;assert!(FileBinding::new(c).is_err());}
    let mut c=t.config();c.contracts.clear();assert!(FileBinding::new(c).is_err());
    let mut c=t.config();c.kernel.clear();assert!(FileBinding::new(c).is_err());
    let b=FileBinding::new(t.config()).unwrap();assert!(b.observe(MAX_SAFE_INTEGER).is_err());assert!(b.observe(u64::MAX).is_err());assert!(b.observe(MAX_SAFE_INTEGER-60).is_ok());
}
#[test] fn json_numeric_values_match_javascript() {
    let t=Temp::new(); let text=format!(r#"{{"root":{},"kernel":"k","contracts":["c"],"evidence":["e"],"policy":"v1","ttlMs":1e0,"limit":3.0}}"#,serde_json::to_string(t.0.to_str().unwrap()).unwrap());
    let c:Config=serde_json::from_str(&text).unwrap();assert_eq!(c.ttl_ms,1);assert_eq!(c.limit,3);
    assert!(serde_json::from_str::<Config>(&text.replace("3.0","3.5")).is_err());
}
