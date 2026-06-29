# frozen_string_literal: true

module Verdify
  class SemanticValidator
    SHA_PATTERN = /\A[0-9a-f]{40}\z/i
    APPROVAL_REQUIRED_STATUSES = %w[approved active complete dispatched changes_requested].freeze
    PROJECT_COVERAGE_AREAS = %w[
      product_outcome
      users_stakeholders_relationships
      domain_data_model
      scope_non_goals
      design_surfaces
      security_privacy_compliance
      infrastructure_hosting
      environments_configuration
      integrations_dependencies
      deployment_release_rollback
      operations_observability_support
      quality_testing_evidence
      governance_ownership_approvals
      documentation_enablement
      cost_procurement_risk
      migration_legacy
      accessibility_localization
    ].freeze

    def self.validate(document)
      new.validate(document)
    end

    def validate(document)
      return [] unless document.is_a?(Hash)

      errors = []
      case document["kind"]
      when "ProjectDefinition"
        validate_project_definition(document, errors)
      when "StateOfUnion"
        validate_state_of_union(document, errors)
      when "ArchitectureDefinition", "ModuleContract"
        validate_approval(document, errors) if document["status"] == "approved"
      when "SprintPlan"
        validate_sprint_plan(document, errors)
      when "LaneContract"
        validate_lane_contract(document, errors)
      when "LaneCloseout"
        validate_lane_closeout(document, errors)
      when "CriticReport"
        validate_critic_report(document, errors)
      when "ReleaseVerification"
        validate_release(document, errors)
      when "OutcomeReview"
        validate_outcome(document, errors)
      when "HumanGate"
        validate_gate(document, errors)
      when "LaneLease"
        validate_lease(document, errors)
      when "EvidenceManifest"
        validate_evidence(document, errors)
      end
      errors
    end

    private

    def validate_project_definition(document, errors)
      return unless document["status"] == "approved"

      validate_approval(document, errors)
      stage_status = document["stage_status"] || {}
      incomplete = %w[discovery requirements product design_surface].reject { |stage| stage_status[stage] == "approved" }
      errors << "$.stage_status: approved project definition has incomplete stages: #{incomplete.join(', ')}" unless incomplete.empty?
      validate_project_lifecycle(document, errors)
    end

    def validate_project_lifecycle(document, errors)
      lifecycle = document["lifecycle"] || {}
      coverage = Array(lifecycle["coverage"])
      areas = coverage.map { |item| item["area"] }.compact
      duplicates = areas.select { |area| areas.count(area) > 1 }.uniq
      errors << "$.lifecycle.coverage: duplicate coverage areas: #{duplicates.join(', ')}" unless duplicates.empty?
      missing = PROJECT_COVERAGE_AREAS - areas
      errors << "$.lifecycle.coverage: approved project definition is missing coverage areas: #{missing.join(', ')}" unless missing.empty?

      unknown = coverage.select { |item| item["status"] == "unknown" }.map { |item| item["area"] }
      errors << "$.lifecycle.coverage: approved project definition cannot contain unknown coverage: #{unknown.join(', ')}" unless unknown.empty?

      open_blocking = Array(lifecycle["open_gaps"]).select { |gap| gap["blocking"] == true && gap["status"] == "open" }
      unless open_blocking.empty?
        ids = open_blocking.map { |gap| gap["id"] }.join(", ")
        errors << "$.lifecycle.open_gaps: approved project definition has open blocking gaps: #{ids}"
      end
    end

    def validate_state_of_union(document, errors)
      return unless document["status"] == "approved"

      validate_approval(document, errors)
      errors << "$.source_freshness: approved state of union requires at least one source freshness record" if Array(document["source_freshness"]).empty?
      errors << "$.planning_inventory: approved state of union requires at least one planning inventory record" if Array(document["planning_inventory"]).empty?
      errors << "$.delivery_health: approved state of union requires delivery health status" if document.dig("delivery_health", "status").to_s.empty?

      open_blocking = Array(document["gaps"]).select { |gap| gap["blocking"] == true }
      unless open_blocking.empty?
        ids = open_blocking.map { |gap| gap["id"] }.join(", ")
        errors << "$.gaps: approved state of union has blocking gaps: #{ids}"
      end

      blocked_candidates = Array(document["next_sprint_candidates"]).select { |candidate| candidate["readiness"] == "blocked" }
      errors << "$.next_sprint_candidates: approved state of union cannot hand off blocked sprint candidates" unless blocked_candidates.empty?

      if document.dig("handoff", "next_skill") == "sprint-planning" && Array(document["next_sprint_candidates"]).empty?
        errors << "$.handoff.next_skill: sprint-planning handoff requires next_sprint_candidates"
      end
    end

    def validate_sprint_plan(document, errors)
      status = document["status"].to_s
      return unless %w[approved active complete].include?(status)

      validate_approval(document, errors)
      %w[issue_ids scope acceptance_criteria lanes].each do |field|
        errors << "$.#{field}: approved/active/complete sprint requires at least one item" if Array(document[field]).empty?
      end

      planned = Array(document["issue_ids"]).map(&:to_i).sort
      assignments = Hash.new { |hash, key| hash[key] = [] }
      Array(document["lanes"]).each do |lane|
        Array(lane["issue_ids"]).each { |issue| assignments[issue.to_i] << lane["lane_id"] }
      end
      assigned = assignments.keys.sort
      errors << "$.lanes: assigned issue IDs #{assigned.inspect} do not match sprint issue IDs #{planned.inspect}" unless assigned == planned
      assignments.each do |issue, lanes|
        errors << "$.lanes: issue ##{issue} is assigned more than once (#{lanes.join(', ')})" if lanes.length != 1
      end

      lane_ids = Array(document["lanes"]).map { |lane| lane["lane_id"] }
      Array(document["lanes"]).each_with_index do |lane, index|
        errors << "$.lanes[#{index}].owner: approved sprint lane requires an owner" if lane["owner"].to_s.strip.empty?
        errors << "$.lanes[#{index}].reviewer: approved sprint lane requires a reviewer" if lane["reviewer"].to_s.strip.empty?
      end
      Array(document["acceptance_criteria"]).each_with_index do |criterion, index|
        unknown = Array(criterion["lane_ids"]) - lane_ids
        errors << "$.acceptance_criteria[#{index}].lane_ids: unknown lanes #{unknown.join(', ')}" unless unknown.empty?
      end

      review_plan = document["review_plan"] || {}
      if Array(review_plan["user_stories_for_review"]).empty?
        errors << "$.review_plan.user_stories_for_review: approved/active/complete sprint requires at least one reviewable user story"
      end
      if Array(review_plan["human_review_milestones"]).empty?
        errors << "$.review_plan.human_review_milestones: approved/active/complete sprint requires a human review milestone"
      end
    end

    def validate_lane_contract(document, errors)
      if APPROVAL_REQUIRED_STATUSES.include?(document["status"].to_s)
        validate_approval(document, errors)
      end
      issue_ids = Array(document["issue_ids"])
      if issue_ids.length > 1 && document["coupling_justification"].to_s.strip.empty?
        errors << "$.coupling_justification: multi-issue lane requires an explicit justification"
      end
      unless document.dig("worktree_policy", "one_coding_session_per_worktree") == true
        errors << "$.worktree_policy.one_coding_session_per_worktree: must be true"
      end
      errors << "$.worktree_policy.lock_required: must be true" unless document.dig("worktree_policy", "lock_required") == true
      validate_sha(document["baseline_sha"], "$.baseline_sha", errors)

      owned = Array(document.dig("ownership", "owned_paths"))
      prohibited = Array(document.dig("ownership", "prohibited_paths"))
      overlap = owned & prohibited
      errors << "$.ownership: paths cannot be both owned and prohibited: #{overlap.join(', ')}" unless overlap.empty?
    end

    def validate_lane_closeout(document, errors)
      validate_sha(document["baseline_sha"], "$.baseline_sha", errors)
      validate_sha(document["head_sha"], "$.head_sha", errors)
      return unless document["status"] == "ready_for_critic"

      errors << "$.pull_request: ready_for_critic requires a pull request" if document["pull_request"].nil?
      errors << "$.worktree_clean: ready_for_critic requires a clean worktree" unless document["worktree_clean"] == true
      Array(document["validation_results"]).each_with_index do |result, index|
        errors << "$.validation_results[#{index}]: ready_for_critic requires passed validation" unless result["result"] == "passed" && result["exit_status"].to_i.zero?
      end
      Array(document["acceptance_evidence"]).each_with_index do |assessment, index|
        errors << "$.acceptance_evidence[#{index}]: ready_for_critic requires satisfied criteria" unless assessment["assessment"] == "satisfied"
      end
    end

    def validate_critic_report(document, errors)
      validate_sha(document["reviewed_head_sha"], "$.reviewed_head_sha", errors)
      return unless %w[approve approve_with_risks].include?(document["outcome"])

      Array(document["acceptance_assessment"]).each_with_index do |assessment, index|
        errors << "$.acceptance_assessment[#{index}]: approval requires satisfied criteria" unless assessment["assessment"] == "satisfied"
      end
      blocking = Array(document["findings"]).select { |finding| %w[critical high].include?(finding["severity"]) }
      errors << "$.findings: approval cannot contain critical/high findings" unless blocking.empty?
      if document["outcome"] == "approve_with_risks" && Array(document["residual_risks"]).empty?
        errors << "$.residual_risks: approve_with_risks requires at least one recorded risk"
      end
    end

    def validate_release(document, errors)
      validate_sha(document["integrated_sha"], "$.integrated_sha", errors)
      return unless document["status"] == "verified"

      errors << "$.deployment.observed_revision: must equal integrated_sha" unless document.dig("deployment", "observed_revision") == document["integrated_sha"]
      errors << "$.deployment.deployed_at: verified release requires deployment time" if document.dig("deployment", "deployed_at").to_s.empty?
      errors << "$.verified_at: verified release requires timestamp" if document["verified_at"].to_s.empty?
      errors << "$.verifier: verified release requires independent verifier" if document["verifier"].to_s.empty?
      Array(document["integration_results"]).each_with_index do |result, index|
        errors << "$.integration_results[#{index}]: verified release requires passed integration" unless result["result"] == "passed"
      end
      Array(document["runtime_checks"]).each_with_index do |result, index|
        errors << "$.runtime_checks[#{index}]: verified release requires passed runtime checks" unless result["result"] == "passed"
      end
      errors << "$.runtime_checks: verified release requires runtime evidence" if Array(document["runtime_checks"]).empty?
    end

    def validate_outcome(document, errors)
      if %w[accepted accepted_with_risks].include?(document["decision"])
        errors << "$.delivered_outcomes: accepted outcome requires delivered outcomes" if Array(document["delivered_outcomes"]).empty?
        errors << "$.evidence_links: accepted outcome requires evidence" if Array(document["evidence_links"]).empty?
        errors << "$.incomplete_items: accepted outcome cannot contain incomplete items" unless Array(document["incomplete_items"]).empty?
      end
      if document["decision"] == "accepted_with_risks" && Array(document["residual_risks"]).empty?
        errors << "$.residual_risks: accepted_with_risks requires at least one risk"
      end
    end

    def validate_gate(document, errors)
      if document["status"] == "open"
        errors << "$.decision: open gate cannot already have a decision" unless document["decision"].nil?
        errors << "$.resolved_at: open gate cannot have resolved_at" unless document["resolved_at"].nil?
      else
        errors << "$.decision: resolved gate requires a decision" if document["decision"].to_s.empty?
        errors << "$.resolved_at: resolved gate requires a timestamp" if document["resolved_at"].to_s.empty?
      end
    end

    def validate_lease(document, errors)
      if document["status"] == "active"
        errors << "$.released_at: active lease cannot have released_at" unless document["released_at"].nil?
      elsif document["status"] == "released"
        errors << "$.released_at: released lease requires timestamp" if document["released_at"].to_s.empty?
      end
    end

    def validate_evidence(document, errors)
      ids = Array(document["items"]).map { |item| item["id"] }
      errors << "$.items: evidence IDs must be unique" unless ids.uniq.length == ids.length
    end

    def validate_approval(document, errors)
      approval = document["approval"] || {}
      errors << "$.approval.status: must be approved when artifact status is approved/active/complete" unless approval["status"] == "approved"
      errors << "$.approval.approver: approved artifact requires approver" if approval["approver"].to_s.empty?
      errors << "$.approval.approved_at: approved artifact requires timestamp" if approval["approved_at"].to_s.empty?
    end

    def validate_sha(value, path, errors)
      errors << "#{path}: expected a full 40-character commit SHA" unless value.to_s.match?(SHA_PATTERN)
    end
  end
end
