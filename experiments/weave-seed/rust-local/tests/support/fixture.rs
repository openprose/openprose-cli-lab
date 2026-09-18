//! Provider-free subprocess fixture. Not an OpenProse semantic assessor.
use std::{fs, io::{Read,Write}, time::Duration};
fn main() {
    let mode=std::env::args().nth(1).unwrap();
    if mode=="hold-pipes" { fs::write("descendant.pid",std::process::id().to_string()).unwrap();std::thread::sleep(Duration::from_secs(30));return; }
    if mode=="no-read" { std::thread::sleep(Duration::from_secs(30)); return; }
    let mut input=String::new();std::io::stdin().read_to_string(&mut input).unwrap();
    let input:serde_json::Value=serde_json::from_str(&input).unwrap();assert_eq!(input["schema"],"openprose.weave-input/1");
    fs::OpenOptions::new().create(true).append(true).open("calls.log").unwrap().write_all(format!("{mode}\n").as_bytes()).unwrap();
    if mode=="fork-pipes" {
        std::process::Command::new(std::env::current_exe().unwrap()).arg("hold-pipes").spawn().unwrap();
        return;
    }
    if mode=="sleep" { fs::write("child.pid",std::process::id().to_string()).unwrap();std::thread::sleep(Duration::from_secs(30));return; }
    if mode=="flood" { for _ in 0..1000 {println!("{}","x".repeat(8192));} return; }
    if mode=="flood-err" { for _ in 0..1000 {eprintln!("{}","x".repeat(8192));} return; }
    if mode=="invalid" { println!("{{\"judgment\":\"satisfied\",\"extra\":true}}");return; }
    if mode=="unknown" {println!("{{\"judgment\":\"unknown\"}}");return;}
    if mode=="check-env" {
        assert_eq!(std::env::var("WEAVE_SELECTED").unwrap(),"present");assert!(std::env::var("WEAVE_HIDDEN").is_err());
        println!("{{\"judgment\":\"unknown\"}}");return;
    }
    if mode=="assess" {
        assert!(input["attempt"].is_null());
        let payload:serde_json::Value=serde_json::from_str(input["evidence"]["payload"].as_str().unwrap()).unwrap();
        let content=|name:&str|payload["files"].as_array().unwrap().iter().find(|f|f["path"].as_str().unwrap().ends_with(&format!("/{name}"))).unwrap()["content"].as_str().unwrap();
        let judgment=if content("source.txt")==content("report.txt") {"satisfied"} else {"work-needed"};
        println!("{{\"judgment\":\"{judgment}\"}}");
    } else {
        assert!(!input["attempt"].as_str().unwrap().is_empty());
        fs::write("report.txt",fs::read("source.txt").unwrap()).unwrap();
        if mode=="fail" {std::process::exit(1);}
    }
}
