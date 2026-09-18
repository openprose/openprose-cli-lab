use weave_local_host_experiment::{decode, encode, LocalHost};
fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().collect();
    let root = args.get(2).ok_or("usage: weave-local-host-experiment load ROOT | save-fixture ROOT FILE")?;
    let host = LocalHost::new(root);
    match args.get(1).map(String::as_str) {
        Some("load") if args.len() == 3 => host.with_lock(|store| { println!("{}", encode(&store.load()?)?); Ok(()) }),
        Some("save-fixture") if args.len() == 4 => {
            let text = std::fs::read_to_string(&args[3]).map_err(|e| e.to_string())?;
            let cp = decode(&text)?;
            host.with_lock(|store| store.save(&cp))
        }
        _ => Err("usage: weave-local-host-experiment load ROOT | save-fixture ROOT FILE".into()),
    }
}
fn main() { if let Err(error) = run() { eprintln!("{error}"); std::process::exit(1); } }
