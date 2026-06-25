#!/usr/bin/env ruby
# frozen_string_literal: true

require "optparse"
require "pathname"
require "time"
require "yaml"

Cluster = Struct.new(:id, :theme, :decision_class, :description, :query, :matcher, keyword_init: true)

CLUSTERS = [
  Cluster.new(
    id: "NQR-001",
    theme: "docs-governance-status-and-agent-operating-model",
    decision_class: "governance",
    description: "Documentation acceptance, MANIFEST/STATUS generation, AGENTS/agent operating model, issue draft policy, and docs preview.",
    query: "docs as code CI generated documentation status manifest agent instructions repository governance",
    matcher: ->(q) { q["file"].match?(/^(README|STATUS|agentic-development)\.md$/) || q["domain"] == "delivery-agents" || q["file"].include?("12-delivery-and-agents/") }
  ),
  Cluster.new(
    id: "NQR-002",
    theme: "product-scope-mvp-personas-metrics-release",
    decision_class: "product",
    description: "Product horizon, MVP boundaries, personas, user journeys, metrics, release criteria, and vNext/full scope.",
    query: "product requirements MVP personas user journeys release criteria product metrics documentation best practices",
    matcher: ->(q) { q["domain"] == "product" || q["file"].include?("00-product/") }
  ),
  Cluster.new(
    id: "NQR-003",
    theme: "architecture-module-boundaries-and-runtime-topology",
    decision_class: "architecture",
    description: "System context, module boundaries, runtime/deployment views, dependency map, principles, risks, and topology.",
    query: "software architecture module boundaries runtime topology dependency map architecture decision records",
    matcher: ->(q) { q["domain"] == "architecture" || q["file"].include?("01-architecture/") }
  ),
  Cluster.new(
    id: "NQR-004",
    theme: "api-mcp-event-contracts-and-compatibility",
    decision_class: "schema",
    description: "OpenAPI, AsyncAPI, MCP tools, event conventions, identifiers, errors, idempotency, compatibility, and authorization context.",
    query: "OpenAPI AsyncAPI MCP tool contracts event envelope idempotency error model API compatibility",
    matcher: ->(q) { q["domain"] == "contracts" || q["file"].include?("02-contracts/") }
  ),
  Cluster.new(
    id: "NQR-005",
    theme: "data-identity-storage-retention-and-vault-schema",
    decision_class: "storage",
    description: "Content identity, source instances, object storage, control-plane data, migrations, Obsidian vault, retention, deletion, legal hold, and schemas.",
    query: "content-addressed storage SHA-256 object storage retention deletion legal hold metadata schema",
    matcher: ->(q) { q["domain"] == "data" || q["file"].include?("03-data/") }
  ),
  Cluster.new(
    id: "NQR-006",
    theme: "pipeline-orchestration-extraction-enrichment-and-quality-gates",
    decision_class: "architecture",
    description: "Pipeline state machine, discovery/acquisition/inspection/document understanding/manifest/indexing stages, processing profiles, quality gates, failure recovery, and reprocessing.",
    query: "document processing pipeline orchestration extraction enrichment quality gates reprocessing state machine",
    matcher: ->(q) { q["domain"] == "pipelines" || q["file"].include?("04-pipelines/") }
  ),
  Cluster.new(
    id: "NQR-007",
    theme: "module-contracts-and-component-ownership",
    decision_class: "architecture",
    description: "Connector, acquisition, control-plane, converter, planner, manifest, indexer, retrieval, knowledge, and web UI module contracts.",
    query: "software module contract ownership boundaries connector acquisition indexer retrieval knowledge module",
    matcher: ->(q) { q["domain"] == "modules" || q["file"].include?("05-modules/") }
  ),
  Cluster.new(
    id: "NQR-008",
    theme: "app-ux-frontend-backend-and-telemetry",
    decision_class: "product",
    description: "Frontend/backend architecture, design system, accessibility, route catalog, UX flows, UI state, read models, and product telemetry.",
    query: "SaaS operator UI frontend architecture accessibility design system telemetry route catalog",
    matcher: ->(q) { q["domain"] == "apps-ux" || q["file"].include?("06-apps-and-ux/") }
  ),
  Cluster.new(
    id: "NQR-009",
    theme: "external-integrations-connectors-providers-and-adapters",
    decision_class: "platform",
    description: "Source connector SDK, provider abstraction, OpenAI-compatible endpoints, storage/search adapters, Obsidian, MCP integrations, and provider conformance.",
    query: "connector SDK provider abstraction OpenAI compatible API storage search adapter conformance tests",
    matcher: ->(q) { q["domain"] == "integrations" || q["file"].include?("07-integrations/") }
  ),
  Cluster.new(
    id: "NQR-010",
    theme: "platform-kubernetes-ci-cd-observability-and-capacity",
    decision_class: "platform",
    description: "Kubernetes, deployment topology, environments, CI/CD, secrets configuration, networking, observability, reliability, capacity, GPU workloads, and supply chain.",
    query: "Kubernetes CI/CD observability reliability capacity GPU workloads software supply chain deployment topology",
    matcher: ->(q) { q["domain"] == "platform" || q["file"].include?("08-platform/") }
  ),
  Cluster.new(
    id: "NQR-011",
    theme: "security-authz-acl-privacy-sandbox-and-threat-model",
    decision_class: "security",
    description: "Authentication, authorization, ACL propagation, audit/evidence, data classification, model privacy, multitenancy, threat model, sandboxing, and incident response.",
    query: "authorization ACL propagation model data privacy sandboxing threat model audit evidence multitenancy",
    matcher: ->(q) { q["domain"] == "security" || q["file"].include?("09-security/") }
  ),
  Cluster.new(
    id: "NQR-012",
    theme: "quality-tests-evaluations-and-release-gates",
    decision_class: "delivery",
    description: "Test strategy, contract/security/accessibility/performance testing, golden corpus, extraction/retrieval/entity evaluation, resiliency, release gates, and traceability.",
    query: "software quality gates golden corpus retrieval evaluation extraction evaluation release gates traceability",
    matcher: ->(q) { q["domain"] == "quality" || q["file"].include?("10-quality/") }
  ),
  Cluster.new(
    id: "NQR-013",
    theme: "operations-runbooks-backup-dr-and-support",
    decision_class: "operations",
    description: "Operations overview, runbooks, on-call, escalation, backups, restore, reprocessing, provider outage, connector failure, schema migration, and pipeline backlog.",
    query: "operations runbook on-call escalation backup restore disaster recovery provider outage schema migration",
    matcher: ->(q) { q["domain"] == "operations" || q["file"].include?("11-operations/") }
  ),
  Cluster.new(
    id: "NQR-014",
    theme: "adr-rfc-decision-records-and-architecture-choices",
    decision_class: "architecture",
    description: "ADR/RFC templates and existing ADR questions covering source identity, storage, database, processing, Docling, vault, chunking, ACL, orchestration, quality, entities, privacy, reprocessing, layout, API, auth, frontend, Kubernetes, and providers.",
    query: "architecture decision record ADR template RFC decision log architecture choices software",
    matcher: ->(q) { q["domain"] == "decisions" || q["file"].include?("13-decisions/") || q["id"].start_with?("ADR", "RFC") }
  ),
  Cluster.new(
    id: "NQR-015",
    theme: "research-benchmarks-pilots-and-format-census",
    decision_class: "research",
    description: "Research project questions, benchmark plans, format census, OCR/VLM/ASR/table/image/video experiments, cost policies, and pilot sequencing.",
    query: "document intelligence benchmark format census OCR VLM ASR table extraction evaluation research plan",
    matcher: ->(q) { q["domain"] == "research" || q["file"].include?("14-research/") }
  ),
  Cluster.new(
    id: "NQR-016",
    theme: "reference-catalogs-glossary-naming-and-technology-choices",
    decision_class: "governance",
    description: "Configuration key catalog, document templates, glossary, naming conventions, dependency/ports catalog, technology catalog, and research-source curation.",
    query: "configuration key catalog glossary naming conventions technology catalog documentation governance",
    matcher: ->(q) { q["domain"] == "reference" || q["file"].include?("99-reference/") }
  )
].freeze

FALLBACK = Cluster.new(
  id: "NQR-999",
  theme: "unclassified-follow-up",
  decision_class: "other",
  description: "Questions that need manual cluster assignment.",
  query: "software planning open questions decision backlog",
  matcher: ->(_q) { true }
)

options = { inventory: nil, output: nil, run_id: nil, target_repository: "unknown" }

OptionParser.new do |parser|
  parser.banner = "Usage: cluster_questions.rb --inventory PATH [--output PATH] [--run-id ID] [--target-repository OWNER/REPO]"
  parser.on("--inventory PATH", "Question inventory YAML") { |value| options[:inventory] = value }
  parser.on("--output PATH", "Write cluster map YAML") { |value| options[:output] = value }
  parser.on("--run-id ID", "Question-resolution run ID") { |value| options[:run_id] = value }
  parser.on("--target-repository OWNER/REPO", "Repository identity") { |value| options[:target_repository] = value }
  parser.on("-h", "--help") { puts parser; exit 0 }
end.parse!

abort "--inventory is required" unless options[:inventory]

inventory_path = Pathname.new(options[:inventory]).expand_path
inventory = YAML.safe_load(inventory_path.read, permitted_classes: [], aliases: false)
questions = Array(inventory["questions"])
abort "inventory has no questions: #{inventory_path}" if questions.empty?

assigned = Hash.new { |hash, key| hash[key] = [] }
questions.each do |question|
  cluster = CLUSTERS.find { |candidate| candidate.matcher.call(question) } || FALLBACK
  assigned[cluster.id] << question
end

clusters = (CLUSTERS + [FALLBACK]).map do |cluster|
  items = assigned[cluster.id]
  next if items.empty?

  files = items.map { |item| item["file"] }.uniq.sort
  {
    "cluster_id" => cluster.id,
    "theme" => cluster.theme,
    "decision_class" => cluster.decision_class,
    "description" => cluster.description,
    "status" => "research-needed",
    "protected_decision" => items.any? { |item| item["protected_candidate"] || item["priority"] == "P0" },
    "question_count" => items.length,
    "priority_counts" => items.group_by { |item| item["priority"] }.transform_values(&:length).sort.to_h,
    "domains" => items.group_by { |item| item["domain"] }.transform_values(&:length).sort.to_h,
    "question_ids" => items.map { |item| item["id"] },
    "affected_artifacts" => files,
    "research_queries" => [cluster.query],
    "evidence_refs" => [],
    "options" => [],
    "selected_answer" => {
      "summary" => nil,
      "rationale" => nil,
      "confidence" => "low",
      "delegated_authority_rationale" => nil,
      "human_escalation" => true,
      "escalation_reason" => "Cluster has not been researched."
    },
    "planning_handoff" => {
      "northstar_question_ids" => [],
      "proposed_artifact_updates" => [],
      "issue_recommendations" => []
    }
  }
end.compact

cluster_map = {
  "schema_ref" => "northstar-question-cluster-map.v1",
  "kind" => "NorthStarQuestionClusterMap",
  "schema_version" => "1.0",
  "run_id" => options[:run_id] || File.basename(inventory_path.dirname.to_s),
  "target_repository" => options[:target_repository],
  "inventory_path" => inventory_path.to_s,
  "generated_at" => Time.now.utc.iso8601,
  "summary" => {
    "total_questions" => questions.length,
    "clusters" => clusters.length,
    "unclassified_questions" => assigned[FALLBACK.id].length,
    "protected_candidates" => questions.count { |item| item["protected_candidate"] || item["priority"] == "P0" }
  },
  "clusters" => clusters
}

rendered = YAML.dump(cluster_map)
if options[:output]
  output = Pathname.new(options[:output]).expand_path
  output.dirname.mkpath
  output.write(rendered)
else
  puts rendered
end
