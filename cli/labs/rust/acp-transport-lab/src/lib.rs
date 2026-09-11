//! Provider-free research proving what the official Rust ACP SDK supplies.
//!
//! This crate intentionally models ACP as a typed bidirectional transport. It
//! does not contain an agent loop, provider client, or `OpenProse` semantics.

use std::{
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};

use agent_client_protocol::{
    Agent, Client, ConnectionTo,
    schema::{
        ProtocolVersion,
        v1::{
            AgentCapabilities, CancelNotification, ContentBlock, ContentChunk, InitializeRequest,
            InitializeResponse, NewSessionRequest, NewSessionResponse, PermissionOption,
            PermissionOptionKind, PromptRequest, PromptResponse, RequestPermissionOutcome,
            RequestPermissionRequest, RequestPermissionResponse, SelectedPermissionOutcome,
            SessionId, SessionNotification, SessionUpdate, StopReason, TextContent, ToolCallUpdate,
            ToolCallUpdateFields,
        },
    },
};
use serde::{Deserialize, Serialize};
use tokio::sync::Notify;

/// This identity is lab-only and must not be exposed by either stable product.
pub const RESEARCH_ADAPTER_ID: &str = "rust-acp-transport-lab";

/// Observable results from the in-memory transport demonstration.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AcpEvidence {
    /// Negotiated protocol version.
    pub protocol_version: String,
    /// Text assembled from streamed session updates.
    pub streamed_text: String,
    /// Number of permission requests handled by the client.
    pub permission_requests: usize,
    /// Terminal result of the ordinary prompt.
    pub ordinary_stop_reason: String,
    /// Terminal result emitted after a session cancellation notification.
    pub cancelled_stop_reason: String,
    /// Number of typed cancellation notifications observed by the fake agent.
    pub cancellation_notifications: usize,
}

/// Exercise a client and a fake agent over the SDK's in-memory transport.
///
/// No executable is spawned, no credential is read, and no network request is
/// made. The fake peer exists only to prove protocol framing, streaming,
/// permission callbacks, and session cancellation carriage.
///
/// # Errors
///
/// Returns an ACP protocol error if either in-memory peer rejects a message,
/// closes early, or fails a callback.
#[allow(clippy::too_many_lines)]
pub async fn run_provider_free_demo() -> agent_client_protocol::Result<AcpEvidence> {
    let updates = Arc::new(Mutex::new(String::new()));
    let update_sink = updates.clone();
    let permission_requests = Arc::new(AtomicUsize::new(0));
    let permission_sink = permission_requests.clone();
    let cancellation_notifications = Arc::new(AtomicUsize::new(0));
    let cancellation_sink = cancellation_notifications.clone();
    let cancellation_started = Arc::new(Notify::new());
    let cancellation_started_for_agent = cancellation_started.clone();
    let cancellation_received = Arc::new(Notify::new());
    let cancellation_received_for_agent = cancellation_received.clone();
    let prompt_count = Arc::new(AtomicUsize::new(0));
    let prompt_count_for_agent = prompt_count.clone();

    let session_id = SessionId::new("provider-free-session");
    let new_session_id = session_id.clone();

    let fake_agent = Agent
        .builder()
        .name("openprose-provider-free-fake-agent")
        .on_receive_request(
            async |request: InitializeRequest, responder, _connection| {
                responder.respond(
                    InitializeResponse::new(request.protocol_version)
                        .agent_capabilities(AgentCapabilities::new()),
                )
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            async move |_request: NewSessionRequest, responder, _connection| {
                responder.respond(NewSessionResponse::new(new_session_id.clone()))
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_request(
            async move |request: PromptRequest, responder, connection: ConnectionTo<Client>| {
                let prompt_index = prompt_count_for_agent.fetch_add(1, Ordering::SeqCst);
                let cancellation_started = cancellation_started_for_agent.clone();
                let cancellation_received = cancellation_received_for_agent.clone();
                connection.spawn({
                    let connection = connection.clone();
                    async move {
                        if prompt_index == 0 {
                            let permission = connection
                                .send_request(RequestPermissionRequest::new(
                                    request.session_id.clone(),
                                    ToolCallUpdate::new(
                                        "tool-call-1",
                                        ToolCallUpdateFields::new()
                                            .title("provider-free tool boundary"),
                                    ),
                                    vec![PermissionOption::new(
                                        "allow-once",
                                        "Allow once",
                                        PermissionOptionKind::AllowOnce,
                                    )],
                                ))
                                .block_task()
                                .await?;
                            if !matches!(permission.outcome, RequestPermissionOutcome::Selected(_))
                            {
                                return responder.respond_with_internal_error(
                                    "provider-free client did not select the permission option",
                                );
                            }

                            for chunk in ["transport ", "stream"] {
                                connection.send_notification(SessionNotification::new(
                                    request.session_id.clone(),
                                    SessionUpdate::AgentMessageChunk(ContentChunk::new(
                                        ContentBlock::Text(TextContent::new(chunk)),
                                    )),
                                ))?;
                            }
                            responder.respond(PromptResponse::new(StopReason::EndTurn))
                        } else {
                            cancellation_started.notify_one();
                            cancellation_received.notified().await;
                            responder.respond(PromptResponse::new(StopReason::Cancelled))
                        }
                    }
                })?;
                Ok(())
            },
            agent_client_protocol::on_receive_request!(),
        )
        .on_receive_notification(
            async move |_notification: CancelNotification, _connection| {
                cancellation_sink.fetch_add(1, Ordering::SeqCst);
                cancellation_received.notify_one();
                Ok(())
            },
            agent_client_protocol::on_receive_notification!(),
        );

    let (protocol_version, ordinary_stop_reason, cancelled_stop_reason) = Client
        .builder()
        .name("openprose-acp-transport-lab")
        .on_receive_notification(
            async move |notification: SessionNotification, _connection| {
                if let SessionUpdate::AgentMessageChunk(chunk) = notification.update
                    && let ContentBlock::Text(text) = chunk.content
                {
                    update_sink
                        .lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner)
                        .push_str(&text.text);
                }
                Ok(())
            },
            agent_client_protocol::on_receive_notification!(),
        )
        .on_receive_request(
            async move |request: RequestPermissionRequest, responder, _connection| {
                permission_sink.fetch_add(1, Ordering::SeqCst);
                let selected = request
                    .options
                    .first()
                    .map(|option| option.option_id.clone())
                    .ok_or_else(agent_client_protocol::Error::invalid_request)?;
                responder.respond(RequestPermissionResponse::new(
                    RequestPermissionOutcome::Selected(SelectedPermissionOutcome::new(selected)),
                ))
            },
            agent_client_protocol::on_receive_request!(),
        )
        .connect_with(fake_agent, async move |connection: ConnectionTo<Agent>| {
            let initialized = connection
                .send_request(InitializeRequest::new(ProtocolVersion::V1))
                .block_task()
                .await?;
            let created = connection
                .send_request(NewSessionRequest::new(absolute_demo_cwd()))
                .block_task()
                .await?;

            let ordinary = connection
                .send_request(PromptRequest::new(
                    created.session_id.clone(),
                    vec![ContentBlock::Text(TextContent::new("ordinary prompt"))],
                ))
                .block_task()
                .await?;

            let cancelled = connection.send_request(PromptRequest::new(
                created.session_id.clone(),
                vec![ContentBlock::Text(TextContent::new("cancellation probe"))],
            ));
            cancellation_started.notified().await;
            connection.send_notification(CancelNotification::new(created.session_id))?;
            let cancelled = cancelled.block_task().await?;

            Ok((
                initialized.protocol_version.as_u16().to_string(),
                format!("{:?}", ordinary.stop_reason),
                format!("{:?}", cancelled.stop_reason),
            ))
        })
        .await?;

    let streamed_text = updates
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .clone();
    Ok(AcpEvidence {
        protocol_version,
        streamed_text,
        permission_requests: permission_requests.load(Ordering::SeqCst),
        ordinary_stop_reason,
        cancelled_stop_reason,
        cancellation_notifications: cancellation_notifications.load(Ordering::SeqCst),
    })
}

fn absolute_demo_cwd() -> PathBuf {
    if cfg!(windows) {
        PathBuf::from(r"C:\openprose-acp-lab")
    } else {
        PathBuf::from("/openprose-acp-lab")
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    #[tokio::test]
    async fn typed_in_memory_transport_carries_stream_permission_and_cancellation() {
        let evidence = tokio::time::timeout(Duration::from_secs(5), run_provider_free_demo())
            .await
            .expect("ACP demo timed out")
            .expect("ACP demo failed");

        assert_eq!(evidence.protocol_version, "1");
        assert_eq!(evidence.streamed_text, "transport stream");
        assert_eq!(evidence.permission_requests, 1);
        assert_eq!(evidence.ordinary_stop_reason, "EndTurn");
        assert_eq!(evidence.cancelled_stop_reason, "Cancelled");
        assert_eq!(evidence.cancellation_notifications, 1);
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
            .expect("ACP lab entry missing");
        assert_eq!(entry["substrate"]["version"], "2.0.0");
        assert_eq!(entry["substrate"]["isEmbeddedAgentRuntime"], false);
        assert_eq!(entry["transport"]["strictStatus"], "not_earned");
        assert_eq!(entry["semantics"]["status"], "unknown");
        assert_eq!(entry["benchmarkEligibility"]["eligible"], false);
        assert_eq!(entry["stableAdmission"]["eligible"], false);
    }
}
