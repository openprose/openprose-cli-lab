use openprose_acp_transport_lab::run_provider_free_demo;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let evidence = run_provider_free_demo().await?;
    println!("{}", serde_json::to_string_pretty(&evidence)?);
    Ok(())
}
