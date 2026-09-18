use std::{io::Write, sync::atomic::{AtomicBool,Ordering}};
static CANCELLED:AtomicBool=AtomicBool::new(false);
extern "C" fn stop(_:libc::c_int) { CANCELLED.store(true,Ordering::Relaxed); }
fn emit(value:&serde_json::Value)->Result<(),String> { let mut out=std::io::stdout().lock();writeln!(out,"{value}").and_then(|_|out.flush()).map_err(|e|e.to_string()) }
fn run()->Result<(),String> {
    let args:Vec<String>=std::env::args().skip(1).collect();
    if args.len()==1 && ["--help","-h"].contains(&args[0].as_str()) {
        println!("usage: weave-rust-local check CONFIG | step CONFIG | status CONFIG | serve CONFIG --poll-ms N --max-steps N\nNative experimental Unix coordinator. No provider is selected implicitly.");
        return Ok(());
    }
    match args.as_slice() {
        [command,path] if command=="check"=>{
            let result=weave_rust_local::check_config(path);emit(&result)?;
            if result["status"]!="configured" {std::process::exit(2);}
            Ok(())
        },
        [command,path] if command=="status"=>emit(&weave_rust_local::status_config(path)?),
        [command,path] if command=="step"=>emit(&weave_rust_local::step_config(path)?.projection()),
        [command,path,poll,ms,max,steps] if command=="serve" && poll=="--poll-ms" && max=="--max-steps"=>{
            if [ms,steps].iter().any(|s|s.starts_with('0') || s.is_empty() || !s.bytes().all(|b|b.is_ascii_digit())) { return Err("explicit positive serve bounds required".into()); }
            let ms=ms.parse().map_err(|_|"invalid poll bound")?;let steps=steps.parse().map_err(|_|"invalid step bound")?;
            unsafe {libc::signal(libc::SIGINT,stop as *const () as libc::sighandler_t);libc::signal(libc::SIGTERM,stop as *const () as libc::sighandler_t);}
            let result=weave_rust_local::serve_config(path,ms,steps,&CANCELLED,|r,_|emit(&r.projection()))?;
            emit(&serde_json::json!({"stopped":result.stopped,"steps":result.steps}))
        },
        _=>Err("usage: weave-rust-local check CONFIG | step CONFIG | status CONFIG | serve CONFIG --poll-ms N --max-steps N".into()),
    }
}
fn main(){if let Err(error)=run(){eprintln!("{error}");std::process::exit(1);}}
