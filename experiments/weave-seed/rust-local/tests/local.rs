use std::{fs, path::{Path,PathBuf}, process::{Command,Stdio}, sync::atomic::{AtomicBool,AtomicU64,Ordering}, time::{Duration,Instant}};
use serde_json::{Value,json};
use weave_rust_local::{step_config,status_config,acquire_owner,serve_config};
static NEXT:AtomicU64=AtomicU64::new(0);
struct Fixture {root:PathBuf,path:PathBuf,config:Value}
impl Fixture {
    fn new()->Self {
        let root=std::env::temp_dir().join(format!("weave-rust-local-{}-{}",std::process::id(),NEXT.fetch_add(1,Ordering::Relaxed)));
        fs::create_dir(&root).unwrap();
        for (name,content) in [("kernel.md","synthetic"),("program.md","synthetic equality"),("source.txt","one"),("report.txt","")] {fs::write(root.join(name),content).unwrap();}
        let exe=env!("CARGO_BIN_EXE_weave-local-test-fixture");
        let config=json!({"schema":1,"root":".","kernel":"kernel.md","contracts":["program.md"],"evidence":["source.txt","report.txt"],"capabilityVersion":"fixture/v1","assessor":[exe,"assess"],"actor":[exe,"act"],"environmentKeys":[],"checkpointDirectory":"host","maxAttempts":3,"ttlMs":60000,"timeoutMs":2000});
        let path=root.join("config.json");fs::write(&path,config.to_string()).unwrap();Self {root,path,config}
    }
    fn save(&self){fs::write(&self.path,self.config.to_string()).unwrap();}
    fn mode(&mut self,cap:&str,mode:&str){self.config[cap][1]=mode.into();self.save();}
    fn calls(&self)->String{fs::read_to_string(self.root.join("calls.log")).unwrap_or_default()}
    fn lock(&self)->PathBuf{self.root.join("host/service.lock")}
}
impl Drop for Fixture {fn drop(&mut self){let _=fs::remove_dir_all(&self.root);}}
fn cli(command:&str,path:&Path,extra:&[&str])->std::process::Output{Command::new(env!("CARGO_BIN_EXE_weave-rust-local")).arg(command).arg(path).args(extra).env_clear().output().unwrap()}
#[test] fn fresh_status_has_no_side_effect_and_corruption_is_not_reset(){
    let f=Fixture::new();let s=status_config(&f.path).unwrap();assert!(s["checkpoint"].is_null());assert_eq!(s["serviceOwned"],false);assert!(!f.root.join("host").exists());
    fs::create_dir(f.root.join("host")).unwrap();fs::write(f.root.join("host/checkpoint.json"),"corrupt").unwrap();assert!(status_config(&f.path).is_err());assert!(step_config(&f.path).is_err());assert!(!f.lock().exists());assert_eq!(fs::read_to_string(f.root.join("host/checkpoint.json")).unwrap(),"corrupt");
}
#[test] fn repair_reuse_change_gap_restart_budget(){
    let mut f=Fixture::new();f.config["maxAttempts"]=2.into();f.save();
    let a=step_config(&f.path).unwrap();assert_eq!(a.status,"satisfied");assert_eq!(a.checkpoint.attempts,1);let calls=f.calls();
    assert_eq!(step_config(&f.path).unwrap().status,"reused");assert_eq!(f.calls(),calls);
    fs::write(f.root.join("source.txt"),"two").unwrap();let c=step_config(&f.path).unwrap();assert_eq!(c.status,"satisfied");assert_eq!(c.checkpoint.attempts,2);
    fs::write(f.root.join("source.txt"),"three").unwrap();assert_eq!(step_config(&f.path).unwrap().status,"attempt-limit");assert_eq!(fs::read_to_string(f.root.join("report.txt")).unwrap(),"two");
    fs::remove_file(f.root.join("source.txt")).unwrap();let before=f.calls();assert_eq!(step_config(&f.path).unwrap().status,"evidence-gap");assert_eq!(f.calls(),before);
}
#[test] fn failed_action_pending_no_replay_and_serve_stops(){
    let mut f=Fixture::new();f.mode("actor","fail");let flag=AtomicBool::new(false);
    let result=serve_config(&f.path,1,10,&flag,|_,_|Ok(())).unwrap();assert_eq!(result.stopped,"pending");assert_eq!(result.steps,1);assert_eq!(result.last.unwrap().status,"action-outcome-unknown");
    let calls=f.calls();let again=step_config(&f.path).unwrap();assert_eq!(again.status,"recovery-needed");assert_eq!(again.checkpoint.attempts,1);assert_eq!(calls,f.calls());assert!(!f.lock().exists());
}
#[test] fn duplicate_owner_config_change_and_replaced_lock(){
    let f=Fixture::new();let mut owner=acquire_owner(&f.path).unwrap();assert!(acquire_owner(&f.path).is_err());assert!(!cli("step",&f.path,&[]).status.success());
    fs::write(&f.path,format!("{} ",f.config)).unwrap();assert!(owner.step().unwrap_err().contains("configuration changed"));
    fs::rename(f.lock(),f.root.join("host/old-lock")).unwrap();fs::create_dir(f.lock()).unwrap();assert!(owner.release().is_err());drop(owner);assert!(f.lock().is_dir());
}
#[test] fn existing_checkpoint_lock_preserved_and_service_released(){
    let f=Fixture::new();fs::create_dir_all(f.root.join("host/lock")).unwrap();assert!(step_config(&f.path).is_err());assert!(f.root.join("host/lock").exists());assert!(!f.lock().exists());
}
#[test] fn assessor_failure_unknown_and_forbidden_environment(){
    let mut f=Fixture::new();f.mode("assessor","invalid");assert!(step_config(&f.path).is_err());assert!(!f.root.join("host/checkpoint.json").exists());assert!(!f.lock().exists());
    f.mode("assessor","unknown");let r=step_config(&f.path).unwrap();assert_eq!(r.status,"unknown");assert_eq!(r.checkpoint.attempts,0);
    f.config["environment"]=json!({});f.save();assert!(step_config(&f.path).unwrap_err().contains("environmentKeys"));
}
#[test] fn process_timeout_flood_and_explicit_environment(){
    for mode in ["sleep","flood","flood-err","no-read"] {
        let mut f=Fixture::new();f.mode("assessor",mode);f.config["timeoutMs"]=40.into();f.config["maxOutputBytes"]=128.into();f.save();let start=Instant::now();assert!(step_config(&f.path).is_err(),"{mode}");assert!(start.elapsed()<Duration::from_secs(3));assert!(!f.lock().exists());
    }
    let mut f=Fixture::new();f.mode("assessor","check-env");f.config["environmentKeys"]=json!(["WEAVE_SELECTED"]);f.save();
    let result=Command::new(env!("CARGO_BIN_EXE_weave-rust-local")).arg("step").arg(&f.path).env_clear().env("WEAVE_SELECTED","present").env("WEAVE_HIDDEN","absent").output().unwrap();
    assert!(result.status.success(),"{}",String::from_utf8_lossy(&result.stderr));assert!(String::from_utf8_lossy(&result.stdout).contains("unknown"));
}
#[test] fn serve_bounded_reuse_cancel_and_bad_options(){
    let f=Fixture::new();let flag=AtomicBool::new(false);let mut statuses=Vec::new();
    let result=serve_config(&f.path,1,3,&flag,|r,_|{statuses.push(r.status);assert!(f.lock().exists());Ok(())}).unwrap();assert_eq!(result.steps,3);assert_eq!(result.stopped,"step-limit");assert_eq!(statuses,vec!["satisfied","reused","reused"]);assert!(!f.lock().exists());
    let result=serve_config(&f.path,1000,100,&flag,|_,_|{flag.store(true,Ordering::Relaxed);Ok(())}).unwrap();assert_eq!(result.steps,1);assert_eq!(result.stopped,"cancelled");assert!(!f.lock().exists());
    assert!(serve_config(&f.path,0,1,&flag,|_,_|Ok(())).is_err());assert!(serve_config(&f.path,1,0,&flag,|_,_|Ok(())).is_err());
}
#[test] fn cli_serve_sigterm_idle_releases_owner(){
    let f=Fixture::new();let mut child=Command::new(env!("CARGO_BIN_EXE_weave-rust-local")).args(["serve",f.path.to_str().unwrap(),"--poll-ms","30000","--max-steps","100"]).env_clear().stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    let start=Instant::now();while fs::read(f.root.join("host/checkpoint.json")).ok().and_then(|b|serde_json::from_slice::<Value>(&b).ok()).is_none_or(|v|v["disposition"]!="satisfied") || f.root.join("host/lock").exists() {assert!(start.elapsed()<Duration::from_secs(5));std::thread::sleep(Duration::from_millis(10));}
    unsafe{libc::kill(child.id() as i32,libc::SIGTERM);}
    let start=Instant::now();loop{if child.try_wait().unwrap().is_some(){break;}assert!(start.elapsed()<Duration::from_secs(3));std::thread::sleep(Duration::from_millis(10));}
    let result=child.wait_with_output().unwrap();assert!(result.status.success(),"{}",String::from_utf8_lossy(&result.stderr));assert!(String::from_utf8_lossy(&result.stdout).contains("cancelled"));assert!(!f.lock().exists());
}
#[test] fn literal_judgment_grammar(){
    for text in ["{\"judgment\":\"satisfied\"}"," \n { \"judgment\" : \"unknown\" }\t"]{assert!(weave_rust_local::parse_judgment(text.as_bytes()).is_ok());}
    for text in ["{\"judgment\":\"satis fied\"}","{\"judgment\":\"satisfied\",\"x\":1}","{\"judgment\":\"satisfied\",\"judgment\":\"satisfied\"}","{\"judgment\":\"\\u0073atisfied\"}"]{assert!(weave_rust_local::parse_judgment(text.as_bytes()).is_err());}
}
#[test] fn actor_timeout_and_descendant_pipe_timeout_remain_pending(){
    for mode in ["sleep","fork-pipes"] {
        let mut f=Fixture::new();f.mode("actor",mode);f.config["timeoutMs"]=500.into();f.save();
        let start=Instant::now();let result=step_config(&f.path).unwrap();assert_eq!(result.status,"action-outcome-unknown");assert!(result.checkpoint.pending.is_some());assert!(start.elapsed()<Duration::from_secs(3));
        let calls=f.calls();assert_eq!(step_config(&f.path).unwrap().status,"recovery-needed");assert_eq!(f.calls(),calls);
    }
}
#[test] fn cli_sigterm_during_actor_preserves_pending(){
    let mut f=Fixture::new();f.mode("actor","sleep");f.config["timeoutMs"]=10000.into();f.save();
    let mut child=Command::new(env!("CARGO_BIN_EXE_weave-rust-local")).args(["serve",f.path.to_str().unwrap(),"--poll-ms","1000","--max-steps","100"]).env_clear().stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    let start=Instant::now();let actor=loop {
        if let Some(pid)=fs::read_to_string(f.root.join("child.pid")).ok().and_then(|s|s.parse::<i32>().ok()) {break pid;}
        assert!(start.elapsed()<Duration::from_secs(5));std::thread::sleep(Duration::from_millis(10));
    };
    unsafe{libc::kill(child.id() as i32,libc::SIGTERM);}
    let start=Instant::now();loop{if child.try_wait().unwrap().is_some(){break;}assert!(start.elapsed()<Duration::from_secs(3));std::thread::sleep(Duration::from_millis(10));}
    let result=child.wait_with_output().unwrap();assert!(result.status.success(),"{}",String::from_utf8_lossy(&result.stderr));
    let stdout=String::from_utf8(result.stdout).unwrap();assert!(stdout.contains("action-outcome-unknown"));assert!(stdout.contains("cancelled"));assert!(!f.lock().exists());
    let state=status_config(&f.path).unwrap();assert!(state["checkpoint"]["pending"].is_string());assert_eq!(state["checkpoint"]["attempts"],1);
    assert_eq!(unsafe{libc::kill(actor,0)},-1);
}
#[test] fn changed_sources_and_expiry_during_serve(){
    let mut f=Fixture::new();f.config["ttlMs"]=1000.into();f.save();let flag=AtomicBool::new(false);let mut statuses=Vec::new();
    let result=serve_config(&f.path,1100,3,&flag,|r,n|{
        statuses.push(r.status);
        if n==1 {fs::write(f.root.join("source.txt"),"updated").unwrap();}
        Ok(())
    }).unwrap();
    assert_eq!(result.steps,3);assert_eq!(statuses,vec!["satisfied","satisfied","satisfied"]);assert_eq!(result.last.unwrap().checkpoint.attempts,2);
    assert_eq!(f.calls().lines().filter(|l|*l=="act").count(),2);
}
#[test] fn callback_error_releases_service_and_help_succeeds(){
    let f=Fixture::new();let flag=AtomicBool::new(false);assert!(serve_config(&f.path,1,1,&flag,|_,_|Err("caller failed".into())).is_err());assert!(!f.lock().exists());
    let result=Command::new(env!("CARGO_BIN_EXE_weave-rust-local")).arg("--help").env_clear().output().unwrap();assert!(result.status.success());assert!(result.stderr.is_empty());
}
#[test] fn check_is_readonly_reports_local_readiness_and_never_verifies_provider(){
    let f=Fixture::new();let before:Vec<_>=fs::read_dir(&f.root).unwrap().map(|e|e.unwrap().file_name()).collect();
    let result=weave_rust_local::check_config(&f.path);assert_eq!(result["schema"],"openprose.weave-check/1");assert_eq!(result["status"],"configured");assert_eq!(result["providerVerified"],false);assert_eq!(result["semanticAssessment"],false);assert_eq!(result["runtime"]["name"],"rust");assert_eq!(result["runtime"]["version"],"0.0.0");assert_eq!(result["checkpoint"]["status"],"absent");assert_eq!(result["sources"]["selectedCount"],4);
    let after:Vec<_>=fs::read_dir(&f.root).unwrap().map(|e|e.unwrap().file_name()).collect();assert_eq!(before,after);assert!(f.calls().is_empty());assert!(!f.root.join("host").exists());
    let cli=cli("check",&f.path,&[]);assert!(cli.status.success());assert!(cli.stderr.is_empty());assert_eq!(serde_json::from_slice::<Value>(&cli.stdout).unwrap(),result);
}
#[test] fn check_reports_missing_names_without_values_and_execution_stays_strict(){
    let mut f=Fixture::new();f.config["environmentKeys"]=json!(["WEAVE_CHECK_SELECTED"]);f.save();
    let run=|present:bool|{let mut command=Command::new(env!("CARGO_BIN_EXE_weave-rust-local"));command.args(["check",f.path.to_str().unwrap()]).env_clear();if present{command.env("WEAVE_CHECK_SELECTED","sensitive-test-value");}command.output().unwrap()};
    let missing=run(false);assert_eq!(missing.status.code(),Some(2));assert!(missing.stderr.is_empty());let parsed:Value=serde_json::from_slice(&missing.stdout).unwrap();assert_eq!(parsed["environment"]["missing"],json!(["WEAVE_CHECK_SELECTED"]));assert!(parsed["errors"].as_array().unwrap().contains(&json!("ENVIRONMENT_MISSING")));
    let present=run(true);assert!(present.status.success());assert!(!String::from_utf8_lossy(&present.stdout).contains("sensitive-test-value"));assert!(cli("step",&f.path,&[]).status.code()!=Some(0));
    assert!(f.calls().is_empty());assert!(!f.root.join("host/checkpoint.json").exists());
}
#[test] fn check_distinguishes_gaps_executables_pending_locks_corrupt_and_config(){
    use std::os::unix::fs::PermissionsExt;
    let mut f=Fixture::new();let check=|f:&Fixture,code:&str|{let r=weave_rust_local::check_config(&f.path);assert_eq!(r["status"],"blocked");assert!(r["errors"].as_array().unwrap().contains(&json!(code)),"{r}");};
    fs::remove_file(f.root.join("source.txt")).unwrap();check(&f,"SELECTED_SOURCE_GAP");fs::write(f.root.join("source.txt"),"one").unwrap();
    let exe=f.root.join("nonexe");fs::write(&exe,"must not run").unwrap();fs::set_permissions(&exe,fs::Permissions::from_mode(0o600)).unwrap();f.config["actor"]=json!([exe]);f.save();check(&f,"ACTOR_EXECUTABLE_UNAVAILABLE");
    f.config["actor"]=json!([env!("CARGO_BIN_EXE_weave-local-test-fixture"),"act"]);f.save();fs::create_dir(f.root.join("host")).unwrap();
    let cp=weave_local_host_experiment::core::Checkpoint {attempts:1,pending:Some("unsettled".into()),..Default::default()};fs::write(f.root.join("host/checkpoint.json"),weave_local_host_experiment::encode(&cp).unwrap()).unwrap();check(&f,"RECOVERY_REQUIRED");
    fs::create_dir(f.lock()).unwrap();check(&f,"EXISTING_OWNER_OR_HOST_LOCK");assert!(f.lock().exists());
    fs::write(f.root.join("host/checkpoint.json"),"corrupt").unwrap();check(&f,"CHECKPOINT_INVALID_OR_UNREADABLE");assert_eq!(fs::read_to_string(f.root.join("host/checkpoint.json")).unwrap(),"corrupt");
    f.config["timeoutMs"]=0.into();f.save();check(&f,"CONFIGURATION_INVALID_OR_UNAVAILABLE");let output=cli("check",&f.path,&[]);assert_eq!(output.status.code(),Some(2));assert!(output.stderr.is_empty());assert!(f.calls().is_empty());
}
#[test] fn configuration_fifo_invalid_utf8_and_size_fail_without_state(){
    let f=Fixture::new();let fifo=f.root.join("config-fifo");let c=std::ffi::CString::new(fifo.to_str().unwrap()).unwrap();assert_eq!(unsafe{libc::mkfifo(c.as_ptr(),0o600)},0);
    let start=Instant::now();assert_eq!(weave_rust_local::check_config(&fifo)["configuration"],"invalid");assert!(status_config(&fifo).is_err());assert!(step_config(&fifo).is_err());assert!(start.elapsed()<Duration::from_secs(1));
    for bytes in [vec![0xff],vec![b' ';1_048_577]] {fs::write(&f.path,bytes).unwrap();assert_eq!(weave_rust_local::check_config(&f.path)["configuration"],"invalid");assert!(status_config(&f.path).is_err());assert!(step_config(&f.path).is_err());}
    assert!(!f.root.join("host").exists());assert!(f.calls().is_empty());
}
#[test] fn nul_command_argument_is_configuration_error_before_effects(){
    let mut f=Fixture::new();f.config["actor"][1]="bad\0argument".into();f.save();assert_eq!(weave_rust_local::check_config(&f.path)["configuration"],"invalid");assert!(step_config(&f.path).is_err());assert!(f.calls().is_empty());assert!(!f.root.join("host/checkpoint.json").exists());
}
#[test] fn settlement_preserves_attempts_invalidates_and_reassesses_without_replay(){
    let mut f=Fixture::new();f.mode("actor","fail");let pending=step_config(&f.path).unwrap().checkpoint;
    let calls=f.calls();let attempt=pending.pending.as_deref().unwrap();
    let cp=weave_rust_local::settle_config(&f.path,&pending.binding,attempt,"completed","operator checked actual report").unwrap();
    assert_eq!(cp.attempts,pending.attempts);assert!(cp.pending.is_none());assert_eq!(cp.disposition,weave_local_host_experiment::core::Judgment::Unknown);assert_eq!(cp.valid_until,0);
    let receipt=cp.settlement.as_ref().unwrap();assert_eq!(receipt.binding,pending.binding);assert_eq!(receipt.attempt,attempt);assert_eq!(receipt.outcome,"completed");assert_eq!(receipt.receipt,"operator checked actual report");assert_eq!(f.calls(),calls);assert!(!f.lock().exists());
    let next=step_config(&f.path).unwrap();assert_eq!(next.status,"satisfied");assert_eq!(next.checkpoint.attempts,pending.attempts);assert_eq!(f.calls().lines().filter(|line|*line=="fail").count(),1);assert_eq!(f.calls().lines().filter(|line|*line=="assess").count(),2);
}
#[test] fn settlement_requires_exact_identity_valid_receipt_and_pending_preserving_bytes(){
    let mut f=Fixture::new();f.mode("actor","fail");let pending=step_config(&f.path).unwrap().checkpoint;let attempt=pending.pending.as_deref().unwrap();
    let path=f.root.join("host/checkpoint.json");let original=fs::read(&path).unwrap();let calls=f.calls();
    for (binding,id,outcome,receipt) in [
        ("wrong",attempt,"completed","receipt"),
        (pending.binding.as_str(),"wrong","completed","receipt"),
        (pending.binding.as_str(),attempt,"assumed","receipt"),
        (pending.binding.as_str(),attempt,"completed",""),
        (pending.binding.as_str(),attempt,"completed"," \t\n"),
        (pending.binding.as_str(),attempt,"completed","\u{feff}"),
    ] {
        assert!(weave_rust_local::settle_config(&f.path,binding,id,outcome,receipt).is_err());assert_eq!(fs::read(&path).unwrap(),original);assert_eq!(f.calls(),calls);assert!(!f.lock().exists());
    }
    weave_rust_local::settle_config(&f.path,&pending.binding,attempt,"not-applied","trusted reconciliation").unwrap();let settled=fs::read(&path).unwrap();
    assert!(weave_rust_local::settle_config(&f.path,&pending.binding,attempt,"not-applied","duplicate").is_err());assert_eq!(fs::read(&path).unwrap(),settled);assert_eq!(f.calls(),calls);
}
#[test] fn settlement_needs_no_environment_sources_or_current_capability_configuration(){
    let mut f=Fixture::new();f.mode("actor","fail");let pending=step_config(&f.path).unwrap().checkpoint;let calls=f.calls();
    fs::remove_file(f.root.join("kernel.md")).unwrap();fs::remove_file(f.root.join("source.txt")).unwrap();
    // Only the locator is required for explicit recovery. Credentials and adapters are irrelevant.
    fs::write(&f.path,json!({"schema":1,"checkpointDirectory":"host","environmentKeys":["UNAVAILABLE_SETTLEMENT_KEY"],"actor":null}).to_string()).unwrap();
    let output=cli("settle",&f.path,&["--binding",&pending.binding,"--attempt",pending.pending.as_deref().unwrap(),"--outcome","completed","--receipt","reviewed-local-effects"]);
    assert!(output.status.success(),"{}",String::from_utf8_lossy(&output.stderr));assert!(output.stderr.is_empty());assert_eq!(serde_json::from_slice::<Value>(&output.stdout).unwrap(),json!({"status":"settled","attempts":1,"pending":null}));assert_eq!(f.calls(),calls);
}
#[test] fn settlement_refuses_both_lock_kinds_without_deleting_or_changing_state(){
    let mut f=Fixture::new();f.mode("actor","fail");let pending=step_config(&f.path).unwrap().checkpoint;let original=fs::read(f.root.join("host/checkpoint.json")).unwrap();let calls=f.calls();
    for name in ["service.lock","lock"] {
        let path=f.root.join("host").join(name);fs::create_dir(&path).unwrap();
        assert!(weave_rust_local::settle_config(&f.path,&pending.binding,pending.pending.as_deref().unwrap(),"completed","receipt").is_err());assert!(path.is_dir());assert_eq!(fs::read(f.root.join("host/checkpoint.json")).unwrap(),original);assert_eq!(f.calls(),calls);fs::remove_dir(path).unwrap();
    }
    let wrong_order=cli("settle",&f.path,&["--attempt",pending.pending.as_deref().unwrap(),"--binding",&pending.binding,"--outcome","completed","--receipt","receipt"]);assert!(!wrong_order.status.success());assert!(wrong_order.stdout.is_empty());assert_eq!(fs::read(f.root.join("host/checkpoint.json")).unwrap(),original);
}
#[test] fn not_applied_settlement_never_replenishes_exhausted_budget(){
    let mut f=Fixture::new();f.config["maxAttempts"]=1.into();f.save();f.mode("actor","fail-before-effect");let pending=step_config(&f.path).unwrap().checkpoint;
    assert_eq!(fs::read_to_string(f.root.join("report.txt")).unwrap(),"");
    let cp=weave_rust_local::settle_config(&f.path,&pending.binding,pending.pending.as_deref().unwrap(),"not-applied","fixture failed before effect").unwrap();assert_eq!(cp.attempts,1);
    let result=step_config(&f.path).unwrap();assert_eq!(result.status,"attempt-limit");assert_eq!(result.checkpoint.attempts,1);assert_eq!(f.calls().lines().filter(|l|*l=="fail-before-effect").count(),1);
}
