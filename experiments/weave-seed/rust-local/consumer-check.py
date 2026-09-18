#!/usr/bin/env python3
"""Offline private SDK qualification in fresh copies, without source checkout fallback."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

CONSUMER = r'''
use std::{fs, path::PathBuf};
use weave_file_binding_experiment::{Config,FileBinding};
use weave_local_host_experiment::{core::{Checkpoint,Evidence,Judgment},Capabilities,LocalHost};
struct World { root:PathBuf, binding:FileBinding, actions:usize }
impl Capabilities for World {
    fn observe(&mut self)->Result<Evidence,String> {
        let e=self.binding.observe(10)?;
        Ok(Evidence{identity:e.identity,payload:e.payload,observed_at:e.observed_at,valid_until:e.valid_until,gap:e.gap})
    }
    fn assess(&mut self,_:&Evidence)->Result<Judgment,String> {
        Ok(if fs::read(self.root.join("source.txt")).unwrap()==fs::read(self.root.join("report.txt")).unwrap(){Judgment::Satisfied}else{Judgment::WorkNeeded})
    }
    fn act(&mut self,_:&Evidence,_:&str)->Result<(),String>{
        self.actions+=1;fs::copy(self.root.join("source.txt"),self.root.join("report.txt")).map_err(|e|e.to_string())?;Ok(())
    }
    fn clock(&mut self)->u64{10}
    fn new_id(&mut self)->String{"consumer-attempt-1".into()}
}
fn main(){
    let root=PathBuf::from(std::env::args().nth(1).expect("fresh caller-selected root"));fs::create_dir(&root).unwrap();
    for (name,content) in [("kernel.md","opaque"),("program.md","opaque"),("source.txt","ready"),("report.txt","")]{fs::write(root.join(name),content).unwrap();}
    let binding=FileBinding::new(Config{root:root.to_str().unwrap().into(),kernel:"kernel.md".into(),contracts:vec!["program.md".into()],evidence:vec!["source.txt".into(),"report.txt".into()],policy:"consumer/v1".into(),ttl_ms:60000,limit:262144}).unwrap();
    let identity=binding.binding().to_owned();let mut world=World{root:root.clone(),binding,actions:0};
    let host=LocalHost::new(root.join("host"));let (checkpoint,status)=host.step(&identity,&mut world,2).unwrap();assert_eq!(status,"satisfied");assert_eq!(checkpoint.attempts,1);
    // Compile-time proof that host's public core reexport is the ordinary dependency type.
    let typed:openprose_weave_experimental::Checkpoint=checkpoint;let _:Checkpoint=typed;
    let restarted=LocalHost::new(root.join("host"));let (_,status)=restarted.step(&identity,&mut world,2).unwrap();assert_eq!(status,"reused");assert_eq!(world.actions,1);
    let config=root.join("config.json");let executable=std::env::current_exe().unwrap();
    fs::write(&config,serde_json::json!({"schema":1,"root":".","checkpointDirectory":"host","kernel":"kernel.md","contracts":["program.md"],"evidence":["source.txt","report.txt"],"capabilityVersion":"consumer/v1","assessor":[executable],"actor":[executable],"environmentKeys":[],"maxAttempts":2}).to_string()).unwrap();
    let diagnostic=weave_rust_local::check_config(&config);assert_eq!(diagnostic["status"],"configured");assert_eq!(diagnostic["providerVerified"],false);assert_eq!(diagnostic["checkpoint"]["status"],"present");
    let status=weave_rust_local::status_config(&config).unwrap();assert_eq!(status["checkpoint"]["attempts"],1);assert_eq!(status["serviceOwned"],false);
    println!("copied Rust SDK consumer PASS: shared core types, native binding, durable repair/restart reuse, readonly local readiness/status; no capabilities launched");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cargo', default=shutil.which('cargo'), help='Cargo executable (required if absent from PATH)')
    args = parser.parse_args()
    if not args.cargo:
        parser.error('supply --cargo /absolute/path/to/cargo')
    cargo = Path(args.cargo).absolute()
    seed = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix='weave-copied-sdk-') as name:
        root = Path(name).resolve()
        sdk = root / 'sdk'
        for package in ('rust', 'rust-binding', 'rust-host', 'rust-local'):
            shutil.copytree(seed / package, sdk / package, ignore=shutil.ignore_patterns('target', '.git', '__pycache__', '.DS_Store'))
        consumer = root / 'consumer'
        (consumer / 'src').mkdir(parents=True)
        (consumer / 'Cargo.toml').write_text('''[package]
name = "private-weave-copied-consumer"
version = "0.0.0"
edition = "2021"
publish = false
[dependencies]
openprose-weave-experimental = { path = "../sdk/rust", version = "0.0.0" }
weave-file-binding-experiment = { path = "../sdk/rust-binding", version = "0.0.0" }
weave-local-host-experiment = { path = "../sdk/rust-host", version = "0.0.0" }
weave-rust-local = { path = "../sdk/rust-local", version = "0.0.0" }
serde_json = "1"
''')
        (consumer / 'src/main.rs').write_text(CONSUMER)
        environment = {key: os.environ[key] for key in ('HOME', 'CARGO_HOME', 'RUSTUP_HOME', 'TMPDIR') if key in os.environ}
        environment['PATH'] = str(cargo.parent) + os.pathsep + os.defpath
        environment['CARGO_TARGET_DIR'] = str(root / 'target')
        def call(*arguments, capture=False):
            return subprocess.run([str(cargo), *arguments], cwd=consumer, env=environment, check=True, text=True, capture_output=capture, timeout=180)
        # Resolve only the fresh consumer's lock offline, then prove its locked graph.
        call('generate-lockfile', '--offline')
        metadata = json.loads(call('metadata', '--offline', '--locked', '--format-version', '1', capture=True).stdout)
        local_packages = []
        for package in metadata['packages']:
            if package['source'] is None:
                manifest = Path(package['manifest_path']).resolve()
                if root not in manifest.parents:
                    raise AssertionError('path dependency escaped copied SDK: ' + str(manifest))
                local_packages.append(package['name'])
        assert set(local_packages) == {'private-weave-copied-consumer', 'openprose-weave-experimental', 'weave-file-binding-experiment', 'weave-local-host-experiment', 'weave-rust-local'}
        call('run', '--offline', '--locked', '--', str(root / 'observation'))
        print('Metadata verified: all five local packages resolve exclusively inside fresh copies; registry dependencies used offline cache.')


if __name__ == '__main__':
    main()
