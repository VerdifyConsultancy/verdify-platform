#!/usr/bin/env ruby
# frozen_string_literal: true

require "digest"
require "optparse"
require "pathname"
require "time"
require "yaml"

options = {
  repo: Dir.pwd,
  sprint: nil,
  output: nil,
  controller_session_id: nil,
  controller_owner: "sprint-orchestrator",
  mcp_server: "agents.vallery.net",
  api_ref: nil,
  executor: "agent-platform-worker",
  model: nil,
  effort: nil,
  poll_minutes: 5,
  heartbeat_minutes: 15,
  platform_readiness: ".agent-workflow/platform/platform-readiness.yaml"
}

OptionParser.new do |parser|
  parser.banner = "Usage: build_execution_runbook.rb --repo PATH --sprint ID [options]"
  parser.on("--repo PATH", "Target repository root") { |value| options[:repo] = value }
  parser.on("--sprint ID", "Sprint ID") { |value| options[:sprint] = value }
  parser.on("--output PATH", "Output runbook path") { |value| options[:output] = value }
  parser.on("--controller-session-id ID", "Controller session ID") { |value| options[:controller_session_id] = value }
  parser.on("--controller-owner NAME", "Controller owner/name") { |value| options[:controller_owner] = value }
  parser.on("--mcp-server NAME", "Agent Platform MCP server identity") { |value| options[:mcp_server] = value }
  parser.on("--api-ref REF", "Agent Platform API/MCP reference") { |value| options[:api_ref] = value }
  parser.on("--executor NAME", "Lane worker executor name") { |value| options[:executor] = value }
  parser.on("--model NAME", "Requested lane model") { |value| options[:model] = value }
  parser.on("--effort LEVEL", "Requested lane reasoning/effort level") { |value| options[:effort] = value }
  parser.on("--poll-minutes N", Integer, "Controller poll interval") { |value| options[:poll_minutes] = value }
  parser.on("--heartbeat-minutes N", Integer, "Worker heartbeat timeout") { |value| options[:heartbeat_minutes] = value }
  parser.on("--platform-readiness PATH", "Platform readiness artifact ref") { |value| options[:platform_readiness] = value }
  parser.on("-h", "--help") { puts parser; exit 0 }
end.parse!

abort "--sprint is required" if options[:sprint].to_s.empty?

repo = Pathname.new(options[:repo]).expand_path
abort "repo does not exist: #{repo}" unless repo.directory?

sprint_dir = repo.join(".agent-workflow/sprints", options[:sprint])
plan_path = sprint_dir.join("sprint-plan.yaml")
abort "sprint plan does not exist: #{plan_path}" unless plan_path.file?

def load_yaml(path)
  YAML.safe_load(path.read, permitted_classes: [], aliases: false)
end

def rel(path, root)
  Pathname.new(path).expand_path.relative_path_from(root).to_s
rescue ArgumentError
  path.to_s
end

def repo_path(root, artifact_path)
  value = artifact_path.to_s
  value = value.sub(%r{\A\.agent-workflow/}, ".agent-workflow/")
  root.join(value)
end

plan = load_yaml(plan_path)
abort "sprint plan is not a SprintPlan" unless plan["kind"] == "SprintPlan"

controller_session = options[:controller_session_id] || "controller-#{options[:sprint]}"
runbook_id = "exec-#{options[:sprint]}"
output_path = options[:output] ? Pathname.new(options[:output]).expand_path : sprint_dir.join("execution/sprint-execution-runbook.yaml")
output_path.dirname.mkpath

lane_records = Array(plan["lanes"]).map do |lane|
  contract_path = repo_path(repo, lane["contract_path"])
  contract_hash = contract_path.file? ? Digest::SHA256.file(contract_path).hexdigest : nil
  prompt_base = sprint_dir.join("prompts", "#{lane['lane_id']}.worker")
  {
    "lane_id" => lane["lane_id"],
    "issue_ids" => Array(lane["issue_ids"]),
    "contract_path" => lane["contract_path"],
    "branch" => lane["branch"],
    "owner" => lane["owner"] || "worker",
    "dependency_state" => "ready",
    "platform_session" => {
      "create_operation_ref" => ".agent-workflow/sprints/#{options[:sprint]}/execution/control-requests/#{lane['lane_id']}-session-create.yaml",
      "session_id" => nil,
      "executor" => options[:executor],
      "model" => options[:model],
      "effort" => options[:effort],
      "status" => "planned"
    },
    "prompt" => {
      "prompt_path" => rel("#{prompt_base}.md", repo),
      "manifest_path" => rel("#{prompt_base}.manifest.json", repo),
      "contract_hash" => contract_hash
    },
    "terminal" => {
      "tmux_session" => nil,
      "browser_terminal_url" => nil,
      "operator_attach_required" => true
    },
    "status" => "planned"
  }
end

contracts = lane_records.map { |lane| lane["contract_path"] }
wave_path = sprint_dir.join("release/wave-release-plan.yaml")
wave = wave_path.file? ? load_yaml(wave_path) : nil
status = wave ? "ready" : "draft"
repository = plan.dig("github", "repository") || "local/#{repo.basename}"

runbook = {
  "schema_ref" => "sprint-execution-runbook.schema.yaml",
  "kind" => "SprintExecutionRunbook",
  "schema_version" => "1.0",
  "runbook_id" => runbook_id,
  "status" => status,
  "sprint_id" => options[:sprint],
  "wave_id" => wave ? wave["wave_id"] : nil,
  "repository" => repository,
  "prepared_at" => Time.now.utc.iso8601,
  "controller" => {
    "session_id" => controller_session,
    "owner" => options[:controller_owner],
    "authority" => "Coordinate lane sessions, answer delegated lane questions, reconcile CI/CD, and stop at protected gates.",
    "interfaces" => %w[agent_platform_mcp tmux_terminal browser_terminal github ci gitops telemetry],
    "poll_interval_minutes" => options[:poll_minutes],
    "delegation_policy" => "Full delegated authority inside approved lane contracts; protected decisions remain gated."
  },
  "prerequisites" => {
    "approved_sprint_plan" => rel(plan_path, repo),
    "approved_lane_contracts" => contracts,
    "wave_release_plan" => wave_path.file? ? rel(wave_path, repo) : nil,
    "platform_readiness" => options[:platform_readiness],
    "open_blockers" => []
  },
  "platform" => {
    "mcp_server" => options[:mcp_server],
    "api_ref" => options[:api_ref],
    "auth_mode" => "mcp_session",
    "required_tools" => [
      { "name" => "agent_platform.session.create", "purpose" => "Create one lane worker session per approved lane.", "required" => true },
      { "name" => "agent_platform.session.poll", "purpose" => "Read lane status, questions, closeout, and coordination requests.", "required" => true },
      { "name" => "agent_platform.terminal.attach", "purpose" => "Attach operator-visible tmux or browser terminal views.", "required" => true },
      { "name" => "agent_platform.session.send", "purpose" => "Answer worker questions and coordination requests.", "required" => true }
    ],
    "terminal_access" => {
      "mode" => "both",
      "operator_visible" => true,
      "attach_refs" => []
    },
    "fallback_policy" => "If Agent Platform MCP is unavailable, stop and route to platform-readiness or a gate instead of launching local workers ad hoc."
  },
  "cadence" => {
    "poll_interval_minutes" => options[:poll_minutes],
    "heartbeat_timeout_minutes" => options[:heartbeat_minutes],
    "loop_until" => [
      "All required lanes have closeout artifacts.",
      "Independent critics approve current heads.",
      "Review packet is complete with exact revision, CI, deployment, telemetry, rollback, and questions.",
      "Deployment verification and outcome review are ready for the next role."
    ]
  },
  "lanes" => lane_records,
  "coordination" => {
    "poll_steps" => [
      "Refresh GitHub, lease, session, PR, check, and deployment state.",
      "Poll each Agent Platform lane session through MCP.",
      "Capture questions, blockers, closeout, and coordination requests.",
      "Answer delegated questions or open gates for protected decisions.",
      "Record controller and session-ledger events, then update sprint status."
    ],
    "controller_responses" => [
      "Answer contract-scoped worker questions.",
      "Dispatch only dependency-ready lanes with no active worker session.",
      "Route scope changes to sprint-planning or architecture-contracts.",
      "Route closeout to independent-critic and approved lanes to review-inbox."
    ],
    "coordination_request_policy" => "Resolve only within approved lane contracts and delegation; route protected changes to gates.",
    "escalation_paths" => []
  },
  "ci_cd" => {
    "required_checks" => Array(wave&.dig("github", "required_checks") || []),
    "workflows" => Array(wave&.dig("ci", "workflows")).map { |item| item["name"] }.compact,
    "merge_policy" => "Merge or queue only after critic approval, review packet readiness, and required checks.",
    "deployment_trigger" => "Trigger review deployment only from approved release plan and authorized environment policy.",
    "evidence_refs" => []
  },
  "deployment" => {
    "target_environment" => Array(wave&.dig("environments")).find { |env| env["required"] }&.dig("name"),
    "namespace" => Array(wave&.dig("environments")).find { |env| env["required"] }&.dig("namespace"),
    "strategy" => wave&.dig("deployment_strategy", "type") || "Follow the approved wave release plan.",
    "approval_required" => wave&.dig("github", "environment_protection_required") || false,
    "rollout_refs" => Array(wave&.dig("gitops", "desired_state_refs") || []),
    "rollback_refs" => Array(wave&.dig("rollback", "validation_steps") || [])
  },
  "review" => {
    "review_packet_path" => ".agent-workflow/sprints/#{options[:sprint]}/review/review-inbox-packet.yaml",
    "human_review_trigger" => "Review packet complete with exact revision, checks, deployment evidence, telemetry, rollback, and open questions.",
    "reviewer_visibility" => []
  },
  "ledger" => {
    "controller_state_path" => ".agent-workflow/controller/controller-state.yaml",
    "session_ledger_path" => ".agent-workflow/controller/session-ledger.yaml",
    "required_events" => %w[lane_dispatched prompt_compiled worker_status worker_closeout critic_reviewed review_packet_created ci_observed deployment_observed]
  },
  "stop_conditions" => [
    "Platform MCP operation identity is missing or unavailable.",
    "A lane session, issue, branch, PR, lease, or contract identity conflicts.",
    "A worker asks for protected production, schema, security, migration, or destructive authority.",
    "Required CI/CD, review, deployment, telemetry, or rollback evidence cannot be obtained."
  ],
  "handoff" => {
    "next_skill" => "sprint-orchestrator",
    "next_mode" => "platform-dispatch",
    "reason" => "Dispatch dependency-ready lane sessions through Agent Platform MCP and supervise execution."
  }
}

rendered = YAML.dump(runbook).gsub(/[ \t]+$/, "")
output_path.write(rendered)
puts output_path
