use openprose_rig_embedded_lab::{run_embedded_tool_demo, run_streaming_demo};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let output = serde_json::json!({
        "embedded": run_embedded_tool_demo().await?,
        "streaming": run_streaming_demo().await?,
    });
    println!("{}", serde_json::to_string_pretty(&output)?);
    Ok(())
}
