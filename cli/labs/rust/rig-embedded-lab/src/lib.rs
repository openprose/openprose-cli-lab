//! Provider-free research proving which agent-loop responsibilities Rig owns.
//!
//! The models below are deterministic in-memory fixtures. They exist to test
//! the substrate without provider credentials, billing, or network access.

use std::{
    convert::Infallible,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
};

use futures::{StreamExt as _, stream};
use rig::{
    AgentBuilder,
    completion::{
        CompletionError, CompletionModel, CompletionRequest, CompletionResponse, Prompt, Usage,
    },
    message::{AssistantContent, ToolCall, ToolFunction},
    prelude::MultiTurnStreamItem,
    streaming::{RawStreamingChoice, StreamFinal, StreamingCompletionResponse, StreamingPrompt},
    tool::{Tool, ToolContext},
};
use serde::{Deserialize, Serialize};
use tokio::sync::Notify;

/// This identity is lab-only and must not be exposed by either stable product.
pub const RESEARCH_ADAPTER_ID: &str = "rust-rig-embedded-lab";

const OPAQUE_INSTRUCTION: &str = "opaque-instruction-payload-α";
const OPAQUE_TASK: &str = "opaque-task-payload-β";

/// Evidence from a two-turn embedded agent/tool loop.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EmbeddedEvidence {
    /// Final answer emitted after the tool result was returned to the model.
    pub final_answer: String,
    /// Number of model calls made by the embedded loop.
    pub model_calls: usize,
    /// Number of in-process tool invocations.
    pub tool_calls: usize,
    /// Whether the arbitrary instruction marker reached the model unchanged.
    pub opaque_instruction_observed: bool,
    /// Whether the arbitrary task marker reached the model unchanged.
    pub opaque_task_observed: bool,
}

/// Evidence from Rig's streaming agent surface.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StreamingEvidence {
    /// Number of assistant stream items surfaced before completion.
    pub assistant_items: usize,
    /// Aggregated final response.
    pub final_answer: String,
}

#[derive(Clone)]
struct ToolLoopModel {
    model_calls: Arc<AtomicUsize>,
    opaque_instruction_observed: Arc<AtomicBool>,
    opaque_task_observed: Arc<AtomicBool>,
}

impl CompletionModel for ToolLoopModel {
    async fn completion(
        &self,
        request: CompletionRequest,
    ) -> Result<CompletionResponse, CompletionError> {
        let serialized = serde_json::to_string(&request)
            .map_err(|error| CompletionError::ResponseError(error.to_string()))?;
        self.opaque_instruction_observed
            .fetch_or(serialized.contains(OPAQUE_INSTRUCTION), Ordering::SeqCst);
        self.opaque_task_observed
            .fetch_or(serialized.contains(OPAQUE_TASK), Ordering::SeqCst);

        let call = self.model_calls.fetch_add(1, Ordering::SeqCst);
        let choice = if call == 0 {
            AssistantContent::ToolCall(ToolCall::from_wire(
                "echo-call-1",
                ToolFunction::new(
                    "evidence_echo".to_owned(),
                    serde_json::json!({"value": "tool boundary crossed"}),
                ),
            ))
        } else {
            AssistantContent::text("embedded tool result committed")
        };
        Ok(CompletionResponse::new(
            vec![choice],
            Usage::new(),
            "provider-free-script",
        ))
    }

    async fn stream(
        &self,
        _request: CompletionRequest,
    ) -> Result<StreamingCompletionResponse, CompletionError> {
        Ok(StreamingCompletionResponse::stream(
            "provider-free-script",
            Box::pin(stream::empty()),
        ))
    }
}

#[derive(Clone)]
struct EvidenceEcho {
    calls: Arc<AtomicUsize>,
}

#[derive(Deserialize)]
struct EvidenceEchoArgs {
    value: String,
}

impl Tool for EvidenceEcho {
    const NAME: &'static str = "evidence_echo";
    type Args = EvidenceEchoArgs;
    type Output = String;
    type Error = Infallible;

    fn description(&self) -> String {
        "Return deterministic provider-free evidence".to_owned()
    }

    fn parameters(&self) -> serde_json::Value {
        serde_json::json!({
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"]
        })
    }

    async fn call(
        &self,
        _context: &mut ToolContext,
        args: Self::Args,
    ) -> Result<Self::Output, Self::Error> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Ok(args.value)
    }
}

/// Run a complete provider-free model → tool → model loop in this process.
///
/// # Errors
///
/// Returns an error if Rig rejects the scripted completion or tool turn.
pub async fn run_embedded_tool_demo()
-> Result<EmbeddedEvidence, Box<dyn std::error::Error + Send + Sync>> {
    let model_calls = Arc::new(AtomicUsize::new(0));
    let tool_calls = Arc::new(AtomicUsize::new(0));
    let opaque_instruction_observed = Arc::new(AtomicBool::new(false));
    let opaque_task_observed = Arc::new(AtomicBool::new(false));
    let model = ToolLoopModel {
        model_calls: model_calls.clone(),
        opaque_instruction_observed: opaque_instruction_observed.clone(),
        opaque_task_observed: opaque_task_observed.clone(),
    };
    let agent = AgentBuilder::new(model)
        .preamble(OPAQUE_INSTRUCTION)
        .tool(EvidenceEcho {
            calls: tool_calls.clone(),
        })
        .build();

    let final_answer = agent.prompt(OPAQUE_TASK).max_turns(2).await?;
    Ok(EmbeddedEvidence {
        final_answer,
        model_calls: model_calls.load(Ordering::SeqCst),
        tool_calls: tool_calls.load(Ordering::SeqCst),
        opaque_instruction_observed: opaque_instruction_observed.load(Ordering::SeqCst),
        opaque_task_observed: opaque_task_observed.load(Ordering::SeqCst),
    })
}

#[derive(Clone)]
struct StreamingModel;

impl CompletionModel for StreamingModel {
    async fn completion(
        &self,
        _request: CompletionRequest,
    ) -> Result<CompletionResponse, CompletionError> {
        Ok(CompletionResponse::new(
            vec![AssistantContent::text("stream works")],
            Usage::new(),
            "provider-free-stream",
        ))
    }

    async fn stream(
        &self,
        _request: CompletionRequest,
    ) -> Result<StreamingCompletionResponse, CompletionError> {
        Ok(StreamingCompletionResponse::stream(
            "provider-free-stream",
            Box::pin(stream::iter([
                Ok(RawStreamingChoice::Message("stream ".to_owned())),
                Ok(RawStreamingChoice::Message("works".to_owned())),
                Ok(RawStreamingChoice::FinalResponse(StreamFinal::new(
                    "provider-free-stream",
                    Usage::new(),
                ))),
            ])),
        ))
    }
}

/// Run Rig's streaming agent surface and retain both deltas and terminal data.
///
/// # Errors
///
/// Returns an error if Rig rejects a stream item or the stream ends without a
/// terminal response.
pub async fn run_streaming_demo()
-> Result<StreamingEvidence, Box<dyn std::error::Error + Send + Sync>> {
    let agent = AgentBuilder::new(StreamingModel).build();
    let mut stream = agent.stream_prompt(OPAQUE_TASK).await;
    let mut assistant_items = 0;
    let mut final_answer = None;
    while let Some(item) = stream.next().await {
        match item? {
            MultiTurnStreamItem::StreamAssistantItem(_) => assistant_items += 1,
            MultiTurnStreamItem::FinalResponse(response) => {
                final_answer = Some(response.output);
            }
            _ => {}
        }
    }
    Ok(StreamingEvidence {
        assistant_items,
        final_answer: final_answer.ok_or("stream ended without a terminal response")?,
    })
}

struct DropGuard(Arc<AtomicUsize>);

impl Drop for DropGuard {
    fn drop(&mut self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }
}

#[derive(Clone)]
struct PendingModel {
    started: Arc<Notify>,
    dropped: Arc<AtomicUsize>,
}

impl CompletionModel for PendingModel {
    async fn completion(
        &self,
        _request: CompletionRequest,
    ) -> Result<CompletionResponse, CompletionError> {
        let _guard = DropGuard(self.dropped.clone());
        self.started.notify_one();
        std::future::pending().await
    }

    async fn stream(
        &self,
        _request: CompletionRequest,
    ) -> Result<StreamingCompletionResponse, CompletionError> {
        Ok(StreamingCompletionResponse::stream(
            "provider-free-pending",
            Box::pin(stream::empty()),
        ))
    }
}

/// Prove the embedded request future is cancelled by dropping its task.
///
/// This does not prove that every real provider's HTTP request is remotely
/// cancelled. That remains a provider-specific admission requirement.
pub async fn prove_drop_cancellation() -> bool {
    let started = Arc::new(Notify::new());
    let dropped = Arc::new(AtomicUsize::new(0));
    let agent = AgentBuilder::new(PendingModel {
        started: started.clone(),
        dropped: dropped.clone(),
    })
    .build();
    let task = tokio::spawn(async move { agent.prompt("pending request").await });
    started.notified().await;
    task.abort();
    let _ = task.await;
    dropped.load(Ordering::SeqCst) == 1
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    #[tokio::test]
    async fn embedded_loop_executes_tool_and_keeps_inputs_opaque() {
        let evidence = run_embedded_tool_demo()
            .await
            .expect("embedded demo failed");
        assert_eq!(evidence.final_answer, "embedded tool result committed");
        assert_eq!(evidence.model_calls, 2);
        assert_eq!(evidence.tool_calls, 1);
        assert!(evidence.opaque_instruction_observed);
        assert!(evidence.opaque_task_observed);
    }

    #[tokio::test]
    async fn streaming_surface_preserves_deltas_and_terminal_result() {
        let evidence = run_streaming_demo().await.expect("streaming demo failed");
        // Two provider deltas plus Rig's committed assistant item.
        assert_eq!(evidence.assistant_items, 3);
        assert_eq!(evidence.final_answer, "stream works");
    }

    #[tokio::test]
    async fn dropping_run_cancels_the_in_process_model_future() {
        assert!(
            tokio::time::timeout(Duration::from_secs(5), prove_drop_cancellation())
                .await
                .expect("drop cancellation demo timed out")
        );
    }

    #[test]
    fn research_identity_cannot_alias_a_stable_harness() {
        assert!(RESEARCH_ADAPTER_ID.ends_with("-lab"));
        assert!(!["prime", "omp", "codex", "claude", "openprose"].contains(&RESEARCH_ADAPTER_ID));
    }

    #[test]
    fn scorecard_is_machine_readable_and_fail_closed() {
        let scorecard: serde_json::Value =
            serde_json::from_str(include_str!("../../scorecard.json"))
                .expect("scorecard must be valid JSON");
        assert_eq!(scorecard["releaseEligible"], false);
        let variants = scorecard["variants"]
            .as_array()
            .expect("scorecard variants must be an array");
        let entry = variants
            .iter()
            .find(|variant| variant["id"] == RESEARCH_ADAPTER_ID)
            .expect("Rig lab entry missing");
        assert_eq!(entry["substrate"]["version"], "0.42.0");
        assert_eq!(entry["substrate"]["isEmbeddedAgentRuntime"], true);
        assert_eq!(entry["transport"]["strictStatus"], "not_earned");
        assert_eq!(entry["semantics"]["status"], "unknown");
        assert_eq!(entry["benchmarkEligibility"]["eligible"], false);
        assert_eq!(entry["stableAdmission"]["eligible"], false);
    }
}
